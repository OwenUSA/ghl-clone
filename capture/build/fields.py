"""Create the checklist folders and custom fields on the live GHL account.

DEFAULTS TO A DRY RUN. Without `--apply` it resolves and fills every control and then
presses Escape, so the whole path is exercised and nothing is saved. Only `--apply`
resolves a submit button, and every submit goes through `guard.Guard.click`, which
enforces the ALLOW/DENY lists and screenshots before and after.

Idempotent: reads the account's existing fields and folders first (from the API, which
is exact - the DOM grid is virtualised and under-reports) and skips anything already
present. Re-running after a half-failure will not create duplicates.

Nothing here is billable. Custom fields are free; the denylist in guard.py covers the
metered controls regardless.

    uv run python build/fields.py            # dry run  (default, safe)
    uv run python build/fields.py --apply    # actually create
"""
import argparse
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import panel
import spec
from attach import attach
from guard import Guard, Refused, shot
from playwright.sync_api import sync_playwright
from settle import settle

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "captures" / "checklists"
from ghl_account import LOC  # noqa: E402  (flat script layout)

URL = ("https://app.gohighlevel.com/v2/location/" + LOC
       + "/settings/fields?tab=field")


def read_existing(page):
    """Field names and folder names already on the account, via the API the page
    fetches. Exact - unlike the virtualised DOM grid, which under-reported 47/61
    and disagreed with itself between runs."""
    seen = {"fields": set(), "folders": set()}

    def walk(node):
        if isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            name = node.get("name")
            if name and ("dataType" in node or "fieldKey" in node):
                if node.get("dataType") == "FOLDER" or node.get("isFolder"):
                    seen["folders"].add(name.strip())
                else:
                    seen["fields"].add(name.strip())
            for v in node.values():
                walk(v)

    def on_response(resp):
        if "customFields" not in resp.url and "custom-fields" not in resp.url:
            return
        try:
            if "json" not in (resp.headers.get("content-type") or ""):
                return
            walk(resp.json())
        except Exception:
            pass

    page.on("response", on_response)
    page.goto(URL, wait_until="domcontentloaded", timeout=90000)
    ok, reason = settle(page, timeout_ms=90000)
    page.wait_for_timeout(8000)
    page.remove_listener("response", on_response)
    if not ok:
        raise SystemExit("page did not settle (%s) - refusing to act blind" % reason)
    return seen


def folder_names_from_dom(page):
    """The folders tab lists folder names; used to confirm creation took."""
    return set(page.evaluate("""() => {
      const out = [];
      for (const e of document.querySelectorAll('*')) {
        if (e.children.length !== 0) continue;
        const r = e.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        const t = (e.innerText || '').trim();
        if (t && t.length < 60) out.push(t);
      }
      return [...new Set(out)];
    }"""))


def create_folder(page, g, name, dry):
    print("\n-- folder %r" % name)
    if not panel.open_by_text(page, "Create folder"):
        raise RuntimeError("'Create folder' button not found")
    page.wait_for_timeout(3500)
    panel.root(page)
    panel.select_only(page, spec.OBJECT)
    panel.fill_placeholder(page, "Enter folder name", name)
    # Never let a screenshot abort a build. Measured: Page.screenshot timed out
    # after 30s mid-run ("waiting for fonts to load"), which crashed the whole
    # pass and left the remaining fields uncreated. guard.shot() swallows it.
    shot(page, "folder-%s" % name, "filled")
    if dry:
        panel.escape(page)
        print("   [dry] filled and abandoned - nothing saved")
        return False
    g.step = "folder:%s" % name
    g.click("Create")
    page.wait_for_timeout(3500)
    return True


def create_field(page, g, folder, name, ftype, options, desc, dry):
    print("\n-- field %r (%s, folder=%s, %d options)"
          % (name, ftype, folder, len(options)))
    if not panel.open_by_text(page, "Create field"):
        raise RuntimeError("'Create field' button not found")
    page.wait_for_timeout(3500)
    panel.root(page)

    panel.select(page, "Field type", ftype)
    panel.select(page, "Add to object", spec.OBJECT)
    panel.fill(page, "Field name", name)

    # In a dry run the target folders were never created, so selecting one would
    # always fail and mask every other problem. Fall back to a folder that does
    # exist purely to exercise the rest of the form - and say so, rather than
    # letting the run look cleaner than it is.
    try:
        panel.select(page, "Folder name", folder)
    except RuntimeError:
        if not dry:
            raise
        print("   [dry] folder %r does not exist yet; using 'Opportunity Details' "
              "to exercise the rest of the form" % folder)
        panel.select(page, "Folder name", "Opportunity Details")

    if options:
        # Fill THEN add, one row at a time. Pre-creating all the rows does not work:
        # "+ Add option" is a no-op while the last row is still empty, so the row
        # count stays at 1 and the field would be created with a single option.
        for i, opt in enumerate(options):
            have = panel.option_rows(page)
            if i >= have:
                if not panel.add_option_row(page):
                    raise RuntimeError("'+ Add option' not found")
                page.wait_for_timeout(420)
                if panel.option_rows(page) <= have:
                    raise RuntimeError(
                        "'+ Add option' did not add a row (stuck at %d, needed %d)"
                        % (have, len(options)))
            panel.set_option(page, i, opt)
        got = panel.option_rows(page)
        if got != len(options):
            raise RuntimeError("built %d option rows, expected %d"
                               % (got, len(options)))

    shot(page, "field-%s" % name, "filled")

    if dry:
        panel.escape(page)
        print("   [dry] filled and abandoned - nothing saved")
        return False

    g.step = "field:%s" % name
    g.click("Create custom field")
    page.wait_for_timeout(3500)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually create. Without it everything is a dry run.")
    ap.add_argument("--only", default=None,
                    help="comma-separated substrings; build only matching fields")
    ap.add_argument("--skip-folders", action="store_true",
                    help="leave folders alone (they already exist)")
    args = ap.parse_args()
    dry = not args.apply
    only = [s.strip().lower() for s in (args.only or "").split(",") if s.strip()]

    OUT.mkdir(parents=True, exist_ok=True)
    print("MODE:", "DRY RUN (nothing will be saved)" if dry else "APPLY - WILL WRITE")

    with sync_playwright() as pw:
        b, ctx, page = attach(pw)
        page.bring_to_front()

        existing = read_existing(page)
        print("\nexisting: %d fields, %d folders"
              % (len(existing["fields"]), len(existing["folders"])))

        g = Guard(page, dry_run=dry, step="fields")
        made = {"folders": [], "fields": []}
        skipped = {"folders": [], "fields": []}
        failed = []

        dom = folder_names_from_dom(page) if not args.skip_folders else set()
        for folder in ([] if args.skip_folders else spec.FOLDERS):
            if folder in existing["folders"] or folder in dom:
                print("\n-- folder %r ALREADY EXISTS - skipping" % folder)
                skipped["folders"].append(folder)
                continue
            try:
                if create_folder(page, g, folder, dry):
                    made["folders"].append(folder)
            except (RuntimeError, Refused) as exc:
                print("   FAILED: %s" % exc)
                failed.append(("folder", folder, str(exc)))
                panel.escape(page)
            page.wait_for_timeout(2200)          # GHL 429s under rapid navigation

        for folder, name, ftype, options, desc in spec.FIELDS:
            if only and not any(s in name.lower() for s in only):
                continue
            if name in existing["fields"]:
                print("\n-- field %r ALREADY EXISTS - skipping" % name)
                skipped["fields"].append(name)
                continue
            try:
                if create_field(page, g, folder, name, ftype, options, desc, dry):
                    made["fields"].append(name)
            except (RuntimeError, Refused) as exc:
                print("   FAILED: %s" % exc)
                failed.append(("field", name, str(exc)))
                panel.escape(page)
            page.wait_for_timeout(2200)

        report = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "dry_run": dry,
                  "created": made, "skipped": skipped, "failed": failed,
                  "clicks": g.clicks}
        (OUT / ("run_%s.json" % ("dry" if dry else "apply"))).write_text(
            json.dumps(report, indent=1), encoding="utf-8")

        print("\n=== %s ===" % ("DRY RUN" if dry else "APPLIED"))
        print("  folders created: %d   fields created: %d"
              % (len(made["folders"]), len(made["fields"])))
        print("  skipped (already there): %d folders, %d fields"
              % (len(skipped["folders"]), len(skipped["fields"])))
        print("  failed: %d" % len(failed))
        for kind, name, why in failed:
            print("     %-7s %-34s %s" % (kind, name[:34], why[:70]))
        print("  guarded clicks performed: %d" % g.clicks)
        if dry:
            print("\nnothing was saved. re-run with --apply to create for real.")


if __name__ == "__main__":
    main()

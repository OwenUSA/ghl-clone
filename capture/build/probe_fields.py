"""READ-ONLY: enumerate every existing custom field on the live account.

Owen's brief was explicitly "check what is done already" - so this runs before anything
is proposed, let alone created. It matters: the page reports **61 fields across 6
folders**, where DECISIONS.md only ever recorded the four `owen_*` ones. A checklist
milestone that already exists under another name must not be duplicated.

Three things earlier attempts got wrong, all worth recording because each one failed
*silently* rather than loudly:

1. The route is `/settings/fields?tab=field`. `/settings/custom_fields` renders a blank
   126-node shell. Found by enumerating the Settings nav, not by guessing.
2. There is **no table**: `querySelectorAll('table,tr,[role=row]')` returns zero. GHL
   renders the grid as plain divs, so a row-based scrape collects nothing - which is
   indistinguishable from "this account has no fields".
3. The grid is **virtualised and paginated**. Scraping innerText yielded 47 of 61, and
   *a different 47 on each run* - the scroll races the virtualiser. Two runs that
   disagree are the only reason this was caught; a single run looks authoritative.

So the DOM is not the source of truth here. This listens to the network response the
page itself fetches, which is exact, complete, and immune to virtualisation. Falls back
to the DOM scrape only if no API response is seen, and says so loudly.

Read-only: it navigates and listens. No control is clicked at all.
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from attach import attach
from playwright.sync_api import sync_playwright
from settle import settle

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "captures" / "settings"
from ghl_account import LOC  # noqa: E402  (flat script layout)

URL = ("https://app.gohighlevel.com/v2/location/" + LOC
       + "/settings/fields?tab=field")

INTERESTING = re.compile(r"custom-?fields?|customFields|/fields", re.IGNORECASE)


def walk(node, found):
    """GHL wraps payloads inconsistently ({customFields:[...]} vs bare list vs
    {data:{...}}). Rather than pin one shape, find every dict that looks like a
    custom field anywhere in the tree."""
    if isinstance(node, list):
        for x in node:
            walk(x, found)
    elif isinstance(node, dict):
        keys = set(node)
        if {"name"} <= keys and ({"dataType", "fieldKey"} & keys):
            found.append(node)
        for v in node.values():
            walk(v, found)


def main():
    captured = []

    with sync_playwright() as pw:
        b, ctx, page = attach(pw)
        page.bring_to_front()

        def on_response(resp):
            if not INTERESTING.search(resp.url):
                return
            try:
                if "json" not in (resp.headers.get("content-type") or ""):
                    return
                body = resp.json()
            except Exception:
                return
            found = []
            walk(body, found)
            if found:
                captured.append({"url": resp.url, "fields": found})
                print("  <- %d fields from %s" % (len(found), resp.url[:95]))

        page.on("response", on_response)

        print("navigating (listening for the fields API)...")
        page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        ok, reason = settle(page, timeout_ms=90000)
        print("settle:", ok, reason)
        page.wait_for_timeout(9000)
        page.reload(wait_until="domcontentloaded", timeout=90000)
        settle(page, timeout_ms=90000)
        page.wait_for_timeout(9000)
        page.remove_listener("response", on_response)

        OUT.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(OUT / "fields_page.png"))

    if not captured:
        raise SystemExit(
            "NO API RESPONSE SEEN - refusing to fall back to the DOM scrape, which "
            "measured 47/61 and disagreed with itself between runs. Re-run; if it "
            "keeps failing the field list must be read another way.")

    # Dedupe across all responses by id (or key+name when no id).
    merged, seen = [], set()
    for blob in captured:
        for f in blob["fields"]:
            ident = f.get("id") or (f.get("fieldKey"), f.get("name"))
            if ident in seen:
                continue
            seen.add(ident)
            merged.append({
                "id": f.get("id"),
                "name": f.get("name"),
                "type": f.get("dataType"),
                "key": f.get("fieldKey"),
                "model": f.get("model") or f.get("objectKey"),
                "folder": (f.get("parentId") or ""),
            })

    by_model = {}
    for f in merged:
        by_model.setdefault(f["model"] or "unknown", []).append(f)

    (OUT / "existing_fields.json").write_text(
        json.dumps({"total": len(merged), "by_model": by_model}, indent=1),
        encoding="utf-8")

    print("\n=== %d custom fields (from the API, not the DOM) ===" % len(merged))
    for model in sorted(by_model):
        rows = by_model[model]
        print("\n--- %s (%d) ---" % (str(model).upper(), len(rows)))
        for f in sorted(rows, key=lambda r: (r["name"] or "").lower()):
            print("  %-36s %-20s %s"
                  % ((f["name"] or "")[:36], (f["type"] or "")[:20], f["key"] or ""))
    print("\nwrote captures/settings/existing_fields.json")


if __name__ == "__main__":
    main()

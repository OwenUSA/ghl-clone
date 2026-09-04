"""READ-ONLY reconnaissance of GHL Settings > Custom Fields and Automation > Workflows.

Nothing in `captures/` has ever touched Settings or Automation - every existing capture
is Contacts / Conversations / Opportunities / Calendars / Reporting / Dashboard /
Launchpad. So this runs before any write script and answers, from the live DOM rather
than from assumption:

  1. Does the Opportunity tab exist under Custom Fields, and what is already in it?
     (We know four `owen_*` opportunity fields exist - DECISIONS.md - so this doubles as
     a known-good control: if the probe cannot see those, the probe is wrong.)
  2. What field TYPES does "Add Field" actually offer? GHL's "Checkbox" is a
     multi-select group that requires options, not a single boolean, so the right type
     for a milestone is an open question that must be answered by measurement.
  3. Do "Create Task" and "Update Opportunity Field" exist as workflow actions, and does
     "Task Completed" exist as a trigger? The whole Part-5 automation depends on it.

This script CANNOT write: it never imports guard.Guard and never calls .click() on a
mutating control. It opens the Add Field panel (a read of a form, not a submit) and
closes it with Escape only - the open_menus.py rule.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from attach import attach
from playwright.sync_api import sync_playwright
from settle import settle

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "captures" / "settings"
from ghl_account import LOC  # noqa: E402  (flat script layout)

BASE = "https://app.gohighlevel.com/v2/location/" + LOC

# Every visible leaf-ish element with its text and position. Same shape as
# open_menus.py:SNAPSHOT so the two capture sets stay comparable.
SNAPSHOT = """
() => {
  const vis = e => {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = getComputedStyle(e);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const out = [];
  for (const e of document.querySelectorAll('*')) {
    if (!vis(e)) continue;
    const t = (e.innerText || '').trim();
    if (!t || t.length > 80) continue;
    if (e.children.length > 1) continue;
    const r = e.getBoundingClientRect();
    out.push({ text: t, x: Math.round(r.x), y: Math.round(r.y),
               tag: e.tagName.toLowerCase(),
               aria: (e.getAttribute('aria-label') || '').trim() });
  }
  return out;
}
"""

# Buttons only - this is the list a write script would later have to resolve against,
# so recording it now means fields.py can be written against measured labels.
BUTTONS = """
() => {
  const vis = e => {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = getComputedStyle(e);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const out = [];
  const sel = 'button,[role=button],[role=tab],[role=menuitem],a[href]';
  for (const e of document.querySelectorAll(sel)) {
    if (!vis(e)) continue;
    const r = e.getBoundingClientRect();
    out.push({ tag: e.tagName.toLowerCase(),
               aria: (e.getAttribute('aria-label') || e.getAttribute('title') || '').trim(),
               text: (e.innerText || '').trim().slice(0, 60),
               href: (e.getAttribute('href') || '').slice(0, 120),
               kids: e.querySelectorAll('*').length,
               x: Math.round(r.x), y: Math.round(r.y) });
  }
  return out;
}
"""

INPUTS = """
() => {
  const out = [];
  for (const e of document.querySelectorAll('input,select,textarea')) {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    out.push({ tag: e.tagName.toLowerCase(), type: e.getAttribute('type') || '',
               name: e.getAttribute('name') || '', id: e.getAttribute('id') || '',
               placeholder: e.getAttribute('placeholder') || '',
               aria: (e.getAttribute('aria-label') || '').trim(),
               x: Math.round(r.x), y: Math.round(r.y) });
  }
  return out;
}
"""

# url, slug, and whether to also try opening the Add Field panel.
TARGETS = [
    ("custom_fields", BASE + "/settings/custom_fields"),
    ("workflows",     BASE + "/automation/workflows"),
]


def probe(page, slug, url):
    print("\n=== %s ===" % slug)
    print("  ->", url)
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    ok, reason = settle(page, timeout_ms=90000)
    print("  settle:", ok, reason)
    if not ok:
        # DECISIONS.md: abort rather than record a loading state as ground truth.
        print("  ABORTING this target - a loading state is not a measurement")
        return {"status": "ABORTED", "reason": reason, "url": url}

    page.wait_for_timeout(2500)
    data = {
        "status": "OK",
        "url": page.url,
        "title": page.title(),
        "nodes": page.evaluate("() => document.querySelectorAll('*').length"),
        "buttons": page.evaluate(BUTTONS),
        "inputs": page.evaluate(INPUTS),
        "snapshot": page.evaluate(SNAPSHOT),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / (slug + ".json")).write_text(json.dumps(data, indent=1), encoding="utf-8")
    page.screenshot(path=str(OUT / (slug + ".png")))
    print("  nodes=%d buttons=%d inputs=%d"
          % (data["nodes"], len(data["buttons"]), len(data["inputs"])))
    print("  wrote captures/settings/%s.{json,png}" % slug)

    # Print the button labels - this is the material a write script needs.
    seen = set()
    print("  --- clickable labels ---")
    for b in sorted(data["buttons"], key=lambda b: (b["y"], b["x"])):
        lbl = b["aria"] or b["text"]
        if not lbl or lbl in seen:
            continue
        seen.add(lbl)
        print("    %-44s (%4d,%4d) kids=%d" % (lbl[:44], b["x"], b["y"], b["kids"]))
    return data


def main():
    with sync_playwright() as pw:
        b, ctx, page = attach(pw)
        page.bring_to_front()
        print("attached:", b.version)
        results = {}
        for slug, url in TARGETS:
            try:
                results[slug] = probe(page, slug, url)
            except Exception as exc:
                print("  FAILED: %s" % exc)
                results[slug] = {"status": "FAILED", "error": str(exc), "url": url}
            # GHL 429s under rapid sequential navigation and then hangs on a
            # spinner forever (DECISIONS.md). Pace deliberately.
            page.wait_for_timeout(4000)

        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "_index.json").write_text(
            json.dumps({k: v.get("status") for k, v in results.items()}, indent=1),
            encoding="utf-8")
        print("\ndone. read-only: no clicks were performed.")


if __name__ == "__main__":
    main()

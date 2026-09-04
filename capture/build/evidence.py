"""READ-ONLY: capture clean evidence of what was created, for the hand-off document.

The earlier `shots.py` grabbed whole pages, where the new items sat below the fold. This
drives the page's own search and sort so each shot actually shows the thing it claims:

  1. Folders tab, scrolled  - the three new folders
  2. Opportunity fields, sorted newest-first - the 25 new fields
  3. A search for "Photos Captured" - proving the 14-option field exists

All read-only: it types in search boxes, clicks column headers and scrolls. It resolves
no Create/Save/Delete control at all.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from attach import attach
from playwright.sync_api import sync_playwright
from settle import settle

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "captures" / "checklists"
from ghl_account import LOC  # noqa: E402  (flat script layout)

BASE = "https://app.gohighlevel.com/v2/location/" + LOC

CLICK_TEXT = """
(want) => {
  for (const e of document.querySelectorAll('*')) {
    if (e.children.length > 1) continue;
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    if ((e.innerText || '').trim() === want) { e.click(); return true; }
  }
  return false;
}
"""

TYPE_IN = """
(args) => {
  const [ph, val] = args;
  for (const e of document.querySelectorAll('input')) {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    if ((e.getAttribute('placeholder') || '') !== ph) continue;
    e.click(); e.focus();
    window.__s = e;
    return true;
  }
  return false;
}
"""

SCROLL = """
(frac) => {
  let best = null, area = 0;
  for (const e of document.querySelectorAll('*')) {
    if (e.scrollHeight > e.clientHeight + 30) {
      const r = e.getBoundingClientRect();
      if (r.width * r.height > area) { area = r.width * r.height; best = e; }
    }
  }
  if (best) { best.scrollTop = best.scrollHeight * frac; return true; }
  return false;
}
"""


def snap(page, name):
    path = OUT / ("evidence__%s.png" % name)
    try:
        page.screenshot(path=str(path), timeout=20000)
        print("   saved", path.name)
    except Exception as exc:
        print("   screenshot failed:", str(exc)[:70])


def load(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    ok, reason = settle(page, timeout_ms=90000)
    page.wait_for_timeout(6000)
    return ok


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        b, ctx, page = attach(pw)
        page.bring_to_front()

        # --- 1. folders -------------------------------------------------------
        print("\n-> folders")
        load(page, BASE + "/settings/fields?tab=folder")
        snap(page, "1_folders_top")
        page.evaluate(SCROLL, 1.0)
        page.wait_for_timeout(1500)
        snap(page, "2_folders_bottom")

        # --- 2. opportunity fields, newest first -----------------------------
        print("\n-> opportunity fields")
        load(page, BASE + "/settings/fields?tab=field")
        page.evaluate(CLICK_TEXT, "Opportunity")
        page.wait_for_timeout(4000)
        # Sort by Created so the 25 new fields group together.
        if page.evaluate(CLICK_TEXT, "Created (EDT)"):
            page.wait_for_timeout(3000)
            page.evaluate(CLICK_TEXT, "Created (EDT)")   # toggle to descending
            page.wait_for_timeout(3000)
        snap(page, "3_opportunity_fields")
        page.evaluate(SCROLL, 1.0)
        page.wait_for_timeout(1500)
        snap(page, "4_opportunity_fields_more")

        # --- 3. the 14-option photo field ------------------------------------
        print("\n-> search 'Photos Captured'")
        if page.evaluate(TYPE_IN, ["Search fields", ""]):
            page.keyboard.type("Photos", delay=60)
            page.wait_for_timeout(4000)
            snap(page, "5_photos_captured")
        else:
            print("   search box not found")

        # --- 4. the checklist fields by name ---------------------------------
        print("\n-> search 'Checklist'")
        if page.evaluate(TYPE_IN, ["Search fields", ""]):
            page.keyboard.press("Control+A")
            page.keyboard.type("Leak", delay=60)
            page.wait_for_timeout(4000)
            snap(page, "6_inspection_fields")

    print("\ndone - read-only.")


if __name__ == "__main__":
    main()

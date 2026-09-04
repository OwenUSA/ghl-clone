"""Enumerate candidate menu triggers on Conversations. CLICKS NOTHING.

The safety contract allows opening dropdowns/overflow menus, but the Conversations
header puts a call button and a delete button directly beside the menu triggers.
So: enumerate first, classify, and only ever click things that pass the allowlist
in open_menus.py. Anything ambiguous is recorded by label and left alone.
"""
import json
import pathlib
import sys

from attach import attach
from ghl_account import LOC
from playwright.sync_api import sync_playwright
from settle import settle

VIEWS = {
    "conversations": "/conversations/conversations",
    "opportunities": "/opportunities/list",
    "contacts": "/contacts/smart_list/All",
    "calendars": "/calendars/view",
}
VIEW = sys.argv[1] if len(sys.argv) > 1 else "conversations"
URL = "https://app.gohighlevel.com/v2/location/" + LOC + VIEWS[VIEW]

JS = """
() => {
  const vis = e => {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = getComputedStyle(e);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const out = [];
  const sel = 'button,[role=button],[aria-haspopup],summary,'
            + '[class*=dropdown],[class*=menu],[class*=caret],[class*=chevron]';
  for (const e of document.querySelectorAll(sel)) {
    if (!vis(e)) continue;
    const r = e.getBoundingClientRect();
    const svgs = e.querySelectorAll('svg').length;
    out.push({
      tag: e.tagName.toLowerCase(),
      text: (e.innerText || '').trim().slice(0, 50),
      aria: e.getAttribute('aria-label') || '',
      title: e.getAttribute('title') || '',
      haspopup: e.getAttribute('aria-haspopup') || '',
      expanded: e.getAttribute('aria-expanded') || '',
      cls: (e.getAttribute('class') || '').slice(0, 90),
      id: e.id || '',
      svgs: svgs,
      x: Math.round(r.x), y: Math.round(r.y),
      w: Math.round(r.width), h: Math.round(r.height)
    });
  }
  return out;
}
"""

with sync_playwright() as pw:
    b, ctx, page = attach(pw)
    page.bring_to_front()
    page.goto(URL, wait_until="domcontentloaded", timeout=90000)
    ok, reason = settle(page)
    print("settle:", ok, reason)
    if not ok:
        raise SystemExit("aborting")

    cands = page.evaluate(JS)
    print("%d candidate interactive elements\n" % len(cands))
    for c in sorted(cands, key=lambda c: (c["y"], c["x"])):
        label = c["text"] or c["aria"] or c["title"] or "(icon only)"
        print("  y=%-5s x=%-5s %-30s haspopup=%-5s svg=%s cls=%s"
              % (c["y"], c["x"], label[:30], c["haspopup"] or "-",
                 c["svgs"], c["cls"][:50]))

    out = pathlib.Path("captures/" + VIEW)
    out.mkdir(parents=True, exist_ok=True)
    (out / "menu_candidates.json").write_text(
        json.dumps(cands, indent=1), encoding="utf-8")
    print("\nwrote captures/conversations/menu_candidates.json")

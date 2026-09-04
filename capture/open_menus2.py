"""Open allowlisted menus on any view. Same safety rules as open_menus.py.

Usage: python capture/open_menus2.py opportunities "Advanced filters (1)" "Manage fields" "Menu"

Resolves by exact label, re-checks against the denylist at click time, closes with
Escape. Records NOT_FOUND rather than reporting an empty menu as "no contents".
"""
import json
import pathlib
import re
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

DENY = re.compile(
    r"\bcall\b|dial|delete|remove|archive|\bsend\b|save|create|^add\b|import|export|"
    r"merge|assign|convert|publish|duplicate|favorite|mark as|block|opt.?out",
    re.IGNORECASE)

RESOLVE = """
(label) => {
  const vis = e => {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = getComputedStyle(e);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const all = document.querySelectorAll(
    'button,[role=button],[aria-haspopup],[class*=cursor-pointer],[class*=menu-container]');
  for (const e of all) {
    if (!vis(e)) continue;
    const l = (e.getAttribute('aria-label') || e.getAttribute('title')
               || (e.innerText || '')).trim();
    if (l === label) {
      window.__target = e;
      const r = e.getBoundingClientRect();
      return { found: true, label: l, x: Math.round(r.x), y: Math.round(r.y) };
    }
  }
  return { found: false };
}
"""

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
    if (e.children.length > 1) continue;
    const t = (e.innerText || '').trim();
    if (!t || t.length > 60) continue;
    out.push(t);
  }
  return out;
}
"""

view = sys.argv[1]
labels = sys.argv[2:]
URL = "https://app.gohighlevel.com/v2/location/" + LOC + VIEWS[view]

with sync_playwright() as pw:
    b, ctx, page = attach(pw)
    page.bring_to_front()
    page.goto(URL, wait_until="domcontentloaded", timeout=90000)
    ok, reason = settle(page)
    print("settle:", ok, reason)
    if not ok:
        raise SystemExit("aborting")

    results = {}
    for label in labels:
        before = set(page.evaluate(SNAPSHOT))
        info = page.evaluate(RESOLVE, label)
        if not info.get("found"):
            print("NOT FOUND: %s" % label)
            results[label] = {"status": "NOT_FOUND"}
            continue
        if DENY.search(info["label"]):
            print("REFUSED (denylist): %r" % info["label"])
            results[label] = {"status": "REFUSED"}
            continue

        page.evaluate("() => window.__target.click()")
        page.wait_for_timeout(1600)
        after = page.evaluate(SNAPSHOT)
        seen, new = set(), []
        for t in after:
            if t not in before and t not in seen:
                seen.add(t)
                new.append(t)
        page.keyboard.press("Escape")
        page.wait_for_timeout(700)

        results[label] = {"status": "OPENED", "items": new}
        print("\n=== %s === %d new" % (label, len(new)))
        for t in new[:40]:
            print("   -", t.replace("\n", " | ")[:70])

    out = pathlib.Path("captures/" + view)
    out.mkdir(parents=True, exist_ok=True)
    (out / "menus.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    print("\nwrote %s/menus.json" % out)

"""Capture a GHL view at several desktop widths to learn how it adapts.

The build so far assumed 1440x900 only. On a 2K monitor the question is what is
FIXED (sidebar, panels) and what is FLUID (list, thread, table) — guessing that
wrong makes every wide-screen layout wrong.
"""
import json
import pathlib
import sys
import time

from attach import attach
from ghl_account import LOC
from playwright.sync_api import sync_playwright
from settle import settle

from capture import set_viewport

BASE = "https://app.gohighlevel.com/v2/location/" + LOC
SIZES = [(1440, 900), (1920, 1080), (2560, 1440)]
EXTRACT_JS = pathlib.Path(__file__).with_name("extract.js").read_text(encoding="utf-8")

VIEWS = {
    "conversations": "/conversations/conversations",
    "contacts": "/contacts/smart_list/All",
    "calendars": "/calendars/view",
    "opportunities": "/opportunities/list",
}

# Landmarks whose width tells us fixed vs fluid.
PROBE = """
() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const out = { vw: window.innerWidth };
  const aside = document.querySelector('aside');
  if (aside) out.sidebar = Math.round(aside.getBoundingClientRect().width);
  const wide = [];
  for (const e of document.querySelectorAll('div,section,main')) {
    if (!vis(e)) continue;
    const r = e.getBoundingClientRect();
    if (r.height > 400 && r.width > 150) {
      wide.push({ x: Math.round(r.x), w: Math.round(r.width),
                  h: Math.round(r.height),
                  cls: (e.getAttribute('class') || '').slice(0, 46) });
    }
  }
  wide.sort((a, b) => a.x - b.x);
  const seen = new Set(); out.cols = [];
  for (const c of wide) {
    const k = c.x + ':' + c.w;
    if (seen.has(k)) continue;
    seen.add(k); out.cols.push(c);
  }
  out.cols = out.cols.slice(0, 14);
  return out;
}
"""

view = sys.argv[1] if len(sys.argv) > 1 else "conversations"
stamp = time.strftime("%Y%m%d-%H%M%S")

with sync_playwright() as pw:
    b, ctx, page = attach(pw)
    page.bring_to_front()
    page.goto(BASE + VIEWS[view], wait_until="domcontentloaded", timeout=90000)
    ok, why = settle(page, timeout_ms=90000)
    print("settle:", ok, why)
    if not ok:
        raise SystemExit("aborting")

    for w, h in SIZES:
        set_viewport(page, w, h)
        page.wait_for_timeout(2500)
        s = page.evaluate(PROBE)
        print("\n=== %dx%d ===  sidebar=%s" % (w, h, s.get("sidebar")))
        for c in s["cols"]:
            print("   x=%-6s w=%-6s h=%-5s %s" % (c["x"], c["w"], c["h"], c["cls"]))

        page.evaluate(EXTRACT_JS)
        data = page.evaluate("() => window.__extract()")
        data["breakpoint"] = "%dx%d" % (w, h)
        data["scrollLabel"] = "top"
        out = pathlib.Path("captures") / view
        out.mkdir(parents=True, exist_ok=True)
        (out / ("%s__%dx%d__top__%s.json" % (LOC, w, h, stamp))).write_text(
            json.dumps(data, indent=1), encoding="utf-8")

    set_viewport(page, 1440, 900)

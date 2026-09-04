"""Capture Dashboard + Launchpad, and expand the Calendars filter accordion.

Throttled (GHL 429s under rapid navigation). Read-only: navigate, expand an
accordion, extract. Expanding an accordion is explicitly allowed by the safety
contract; nothing is saved or submitted.
"""
import json
import pathlib
import time

from attach import attach
from ghl_account import LOC
from playwright.sync_api import sync_playwright
from settle import settle

from capture import set_viewport

BASE = "https://app.gohighlevel.com/v2/location/" + LOC
EXTRACT_JS = pathlib.Path(__file__).with_name("extract.js").read_text(encoding="utf-8")
STAMP = time.strftime("%Y%m%d-%H%M%S")

VIEWS = {
    "reporting": BASE + "/reporting/reports",
}

EXPAND = """
() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  let hits = [];
  for (const e of document.querySelectorAll('*')) {
    if (!vis(e)) continue;
    if (e.children.length > 3) continue;
    const t = (e.innerText || '').trim();
    if (t === 'Calendars' || t === 'Groups') {
      e.click();
      hits.push(t);
    }
  }
  return hits;
}
"""

TEXTS = """
() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const out = [];
  for (const e of document.querySelectorAll('*')) {
    if (!vis(e)) continue;
    if (e.children.length > 1) continue;
    const t = (e.innerText || e.getAttribute('placeholder') || '').trim();
    if (!t || t.length > 60) continue;
    const r = e.getBoundingClientRect();
    out.push({ t, x: Math.round(r.x), y: Math.round(r.y) });
  }
  return out;
}
"""

with sync_playwright() as pw:
    b, ctx, page = attach(pw)
    page.bring_to_front()
    set_viewport(page, 1440, 900)

    for name, url in VIEWS.items():
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
        ok, why = settle(page, timeout_ms=90000)
        print("=== %s === settled=%s (%s)" % (name, ok, why))
        if not ok:
            print("  !! skipped, would record a loading state")
            continue
        page.evaluate(EXTRACT_JS)
        data = page.evaluate("() => window.__extract()")
        data["scrollers"] = page.evaluate("() => window.__scrollers()")
        data["breakpoint"] = "1440x900"
        data["scrollLabel"] = "top"
        out = pathlib.Path("captures") / name
        out.mkdir(parents=True, exist_ok=True)
        (out / ("%s__1440x900__top__%s.json" % (LOC, STAMP))).write_text(
            json.dumps(data, indent=1), encoding="utf-8")
        page.screenshot(path=str(out / ("%s__%s.png" % (LOC, STAMP))))
        print("  wrote %d elements" % data["count"])
        time.sleep(60)

    # Calendars filter panel: expand the Calendars / Groups accordions
    page.goto(BASE + "/calendars/view", wait_until="domcontentloaded", timeout=90000)
    ok, why = settle(page, timeout_ms=90000)
    print("=== calendars filter === settled=%s" % ok)
    if ok:
        before = {e["t"] for e in page.evaluate(TEXTS)}
        clicked = page.evaluate(EXPAND)
        print("  expanded:", clicked)
        page.wait_for_timeout(2500)
        after = page.evaluate(TEXTS)
        new = [e for e in after if e["t"] not in before]
        new.sort(key=lambda e: (e["y"], e["x"]))
        print("  %d new rows in filter panel:" % len(new))
        seen = set()
        for e in new:
            if e["t"] in seen:
                continue
            seen.add(e["t"])
            print("    y=%-5s x=%-5s %s" % (e["y"], e["x"], e["t"][:56]))
        pathlib.Path("captures/calendars/filter_groups.json").write_text(
            json.dumps(new, indent=1), encoding="utf-8")

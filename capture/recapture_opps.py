"""Re-capture the Opportunities kanban, stepping horizontally.

The first capture recorded only the stages visible in the viewport: 5 stages
summing to 23 opportunities, while GHL's header read 25. The board scrolls
sideways. This walks it to the right edge, accumulating every stage column, and
reconciles the total against the header count.
"""
import json
import pathlib
import re
import time

from attach import attach
from ghl_account import LOC
from playwright.sync_api import sync_playwright
from settle import settle

URL = ("https://app.gohighlevel.com/v2/location/" + LOC + "/opportunities/list")
EXTRACT_JS = pathlib.Path(__file__).with_name("extract.js").read_text(encoding="utf-8")

# "6 opportunities  $2,475.00" sits under each stage title.
STAGE_LINE = re.compile(r"^(\d+)\s+opportunit", re.IGNORECASE)

COLLECT = """
() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const out = [];
  for (const el of document.querySelectorAll('*')) {
    if (!vis(el)) continue;
    const t = (el.innerText || '').trim();
    if (!/^\\d+\\s+opportunit/i.test(t)) continue;
    if (el.children.length > 2) continue;
    const head = el.parentElement ? (el.parentElement.innerText || '').trim() : '';
    out.push({ meta: t.split('\\n')[0], head: head.split('\\n').slice(0, 3).join(' | '),
               x: Math.round(el.getBoundingClientRect().x) });
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
        raise SystemExit("aborting - would record a loading state")

    page.evaluate(EXTRACT_JS)
    hs = page.evaluate("() => window.__hscrollers()")
    print("horizontal scrollers:", json.dumps(hs[:3], indent=1))

    stages, seen = [], set()
    pos, step, max_left = 0, 400, 0
    while True:
        info = page.evaluate("(px) => window.__scrollXTo(px)", pos)
        max_left = info.get("max", 0)
        page.wait_for_timeout(1200)
        # x is viewport-relative, so it is only comparable across scroll steps
        # once the current scrollLeft is added back in.
        left = info.get("scrollLeft", 0)
        for s in page.evaluate(COLLECT):
            key = s["head"]
            if key not in seen:
                seen.add(key)
                s["absX"] = s["x"] + left
                s["scrollLeft"] = left
                stages.append(s)
        if info["target"] == "none" or info["scrollLeft"] >= max_left:
            break
        pos += step

    print("\nmax scrollLeft = %s" % max_left)
    print("stages found: %d" % len(stages))
    total = 0
    for s in stages:
        m = STAGE_LINE.match(s["meta"])
        n = int(m.group(1)) if m else 0
        total += n
        print("  %-58s %s" % (s["head"][:58], s["meta"]))
    print("\nsum of stage counts = %d" % total)

    body = page.evaluate("() => document.body.innerText || ''")
    m = re.search(r"(\d+)\s+opportunities", body)
    print("header says          = %s" % (m.group(1) if m else "?"))

    out = pathlib.Path("captures/opportunities")
    out.mkdir(parents=True, exist_ok=True)
    f = out / ("%s__stages__%s.json" % (LOC, time.strftime("%Y%m%d-%H%M%S")))
    f.write_text(json.dumps(
        {"stages": stages, "sum": total,
         "header": m.group(1) if m else None,
         "maxScrollLeft": max_left}, indent=1), encoding="utf-8")
    print("wrote", f)

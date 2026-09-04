"""Deep reference capture: HTML + CSS + computed styles + screenshots.

Screenshots alone invite approximation; computed styles alone miss icons and
shape. This grabs all of it, from BOTH sides, at every desktop width, and writes
them side by side so GHL and our app can be flipped between.

Per view and breakpoint:
  references/<side>/<view>/<bp>.html   full outerHTML
  references/<side>/<view>/<bp>.png    full screenshot
  references/<side>/<view>/<bp>.json   computed styles (shared extract.js)
  references/<side>/<view>/styles.css  every readable CSS rule
  references/compare/<view>__<bp>.html a flip/side-by-side viewer

Usage:
  python capture/deep.py ghl   [view ...]
  python capture/deep.py ours  [view ...]
"""
import json
import pathlib
import sys
import time

from attach import attach
from ghl_account import LOC
from login import ensure_logged_in
from playwright.sync_api import sync_playwright
from settle import settle

from capture import set_viewport

GHL = "https://app.gohighlevel.com/v2/location/" + LOC
SIZES = [(1440, 900), (1920, 1080), (2560, 1440)]

VIEWS = {
    "conversations": (GHL + "/conversations/conversations", "Conversations"),
    "contacts": (GHL + "/contacts/smart_list/All", "Contacts"),
    "opportunities": (GHL + "/opportunities/list", "Opportunities"),
    "calendars": (GHL + "/calendars/view", "Calendars"),
    "dashboard": (GHL + "/dashboard", "Dashboard"),
    "launchpad": (GHL + "/launchpad", "Launchpad"),
}

EXTRACT_JS = pathlib.Path(__file__).with_name("extract.js").read_text(encoding="utf-8")

CSS_DUMP = """
() => {
  const out = [];
  for (const sheet of Array.from(document.styleSheets)) {
    let rules;
    try { rules = sheet.cssRules; } catch (e) {
      out.push('/* cross-origin sheet, unreadable: ' + (sheet.href || '') + ' */');
      continue;
    }
    if (!rules) continue;
    out.push('/* ==== ' + (sheet.href || 'inline') + ' ==== */');
    for (const r of Array.from(rules)) out.push(r.cssText);
  }
  return out.join('\\n');
}
"""

VIEWER = """<!doctype html><meta charset=utf-8>
<title>%(view)s @ %(bp)s — GHL vs ours</title>
<style>
 body{margin:0;font:13px system-ui;background:#111;color:#eee}
 .bar{padding:8px 12px;display:flex;gap:12px;align-items:center;position:sticky;top:0;background:#111;z-index:2}
 button{padding:6px 12px;border-radius:6px;border:1px solid #444;background:#222;color:#eee;cursor:pointer}
 button.on{background:#004eeb;border-color:#004eeb}
 .wrap{position:relative}
 img{display:block;width:100%%;border-top:1px solid #333}
 .side{display:none}.side.on{display:block}
 .sbs{display:grid;grid-template-columns:1fr 1fr;gap:8px}
 .sbs img{width:100%%}
 h3{margin:6px 12px;font-weight:600;color:#9aa}
</style>
<div class=bar>
 <b>%(view)s @ %(bp)s</b>
 <button id=bg class=on onclick="show('ghl')">GHL</button>
 <button id=bo onclick="show('ours')">Ours</button>
 <button id=bb onclick="show('both')">Side by side</button>
 <span style="color:#888">flip between GHL and ours to spot differences</span>
</div>
<div id=ghl class="side on"><h3>GoHighLevel</h3><img src="%(ghl)s"></div>
<div id=ours class="side"><h3>Ours</h3><img src="%(ours)s"></div>
<div id=both class="side sbs"><img src="%(ghl)s"><img src="%(ours)s"></div>
<script>
function show(k){for(const s of ['ghl','ours','both']){document.getElementById(s).className='side'+(s===k?' on':'')+(s==='both'?' sbs':'')}
 for(const [b,v] of [['bg','ghl'],['bo','ours'],['bb','both']])document.getElementById(b).className=(v===k?'on':'')}
</script>
"""


def grab(page, side, view, bp, out_root):
    d = out_root / side / view
    d.mkdir(parents=True, exist_ok=True)
    page.evaluate(EXTRACT_JS)
    data = page.evaluate("() => window.__extract()")
    (d / ("%s.json" % bp)).write_text(json.dumps(data, indent=1), encoding="utf-8")
    (d / ("%s.html" % bp)).write_text(
        page.evaluate("() => document.documentElement.outerHTML"), encoding="utf-8")
    (d / "styles.css").write_text(page.evaluate(CSS_DUMP), encoding="utf-8")
    page.screenshot(path=str(d / ("%s.png" % bp)), full_page=False)
    return data["count"]


def main():
    side = sys.argv[1] if len(sys.argv) > 1 else "ours"
    wanted = sys.argv[2:] or list(VIEWS)
    out_root = pathlib.Path("references")

    with sync_playwright() as pw:
        b, ctx, page = attach(pw)
        page.bring_to_front()

        for view in wanted:
            url, nav = VIEWS[view]
            if side == "ghl":
                page.goto(url, wait_until="domcontentloaded", timeout=90000)
                ok, why = settle(page, timeout_ms=90000)
                if not ok:
                    print("%-14s SKIPPED (%s)" % (view, why))
                    continue
            else:
                page.goto("http://localhost:5173/", wait_until="networkidle", timeout=60000)
                ensure_logged_in(page)
                page.wait_for_timeout(1500)
                page.get_by_role("button", name=nav, exact=False).first.click()
                page.wait_for_timeout(2500)

            for w, h in SIZES:
                set_viewport(page, w, h)
                page.wait_for_timeout(1800)
                bp = "%dx%d" % (w, h)
                n = grab(page, side, view, bp, out_root)
                print("%-14s %-10s %s  %d elements" % (view, bp, side, n))

            set_viewport(page, 1440, 900)
            if side == "ghl":
                time.sleep(45)  # throttle: GHL 429s under rapid navigation

        # viewers (only meaningful once both sides exist)
        cmp_dir = out_root / "compare"
        cmp_dir.mkdir(parents=True, exist_ok=True)
        for view in wanted:
            for w, h in SIZES:
                bp = "%dx%d" % (w, h)
                g = out_root / "ghl" / view / ("%s.png" % bp)
                o = out_root / "ours" / view / ("%s.png" % bp)
                if g.exists() and o.exists():
                    (cmp_dir / ("%s__%s.html" % (view, bp))).write_text(
                        VIEWER % {
                            "view": view, "bp": bp,
                            "ghl": "../ghl/%s/%s.png" % (view, bp),
                            "ours": "../ours/%s/%s.png" % (view, bp),
                        }, encoding="utf-8")
        print("\nviewers in references/compare/")


if __name__ == "__main__":
    main()

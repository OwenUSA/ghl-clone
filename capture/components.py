"""Per-component capture and comparison: GHL vs ours.

For each named component in each view this saves, from BOTH sides:
  references/components/<side>/<view>/<component>.png    clipped screenshot
  references/components/<side>/<view>/<component>.html   outerHTML
  references/components/<side>/<view>/<component>.json   box + computed styles of
                                                         every visible descendant
and writes a flip viewer per component.

Components are located by a text anchor, then walking up N ancestors to the
container. That works on both sides even though the DOM structures differ.

SAFETY: this opens menus/tabs only. It never opens a customer thread, never
clicks call/text/send, and never touches anything that mutates the live account.
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

VIEW_URLS = {
    "conversations": (GHL + "/conversations/conversations", "Conversations"),
    "contacts": (GHL + "/contacts/smart_list/All", "Contacts"),
    "opportunities": (GHL + "/opportunities/list", "Opportunities"),
    "calendars": (GHL + "/calendars/view", "Calendars"),
    "dashboard": (GHL + "/dashboard", "Dashboard"),
}

# view -> [(component, anchor text, ancestor levels up)]
COMPONENTS = {
    "conversations": [
        ("inbox-header", "Team inbox", 2),
        ("inbox-tabs", "Starred", 3),
        ("conversation-row", "Call", "Jul", 4),
        ("thread-header", "Jul 25", "Filter messages", 3),
        ("composer", "Type a message", 3),
        ("panel-header", "Contact Details", 2),
        ("panel-owner", "Followers", 3),
        ("panel-tabs", "All fields", 2),
    ],
    "contacts": [
        ("title-row", "268 Contacts", 2),
        ("smartlist-row", "Add Smart List", 3),
        ("toolbar", "Manage fields", 3),
        ("table-header", "Business name", 3),
        ("pagination", "Next", 3),
    ],
    "opportunities": [
        ("pipeline-row", "opportunities", 3),
        ("savedview-row", "Open opportunities", 3),
        ("toolbar", "Advanced filters", "Search opportunities", 2),
        ("stage-header", "New Lead", 2),
        ("opportunity-card", "Value:", 3),
    ],
    "calendars": [
        ("toolbar", "Today", 3),
        ("day-header", "26 Sun", 2),
        ("manage-view", "View by type", 2),
        ("filters", "Users", 2),
    ],
    "dashboard": [
        ("header", "Dashboard", 2),
        ("status-card", "Opportunity status", 3),
        ("value-card", "Opportunity value", 3),
        ("conversion-card", "Conversion rate", 3),
    ],
}

FIND = """
([anchor, up]) => {
  const vis = e => {
    const r = e.getBoundingClientRect();
    if (r.width <= 2 || r.height <= 2) return false;
    const cs = getComputedStyle(e);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  // Pick the SMALLEST element carrying the anchor as its own text. Taking the
  // first match in document order grabbed a page-level container (every
  // component then resolved to the full 1440x900 viewport).
  let best = null, bestArea = Infinity;
  for (const e of document.querySelectorAll('*')) {
    if (!vis(e)) continue;
    let own = '';
    for (const n of e.childNodes) if (n.nodeType === 3) own += n.nodeValue;
    const t = (own || e.getAttribute('placeholder')
                   || e.getAttribute('aria-label')
                   || e.getAttribute('title') || '').trim();
    if (!t.includes(anchor)) continue;
    const r = e.getBoundingClientRect();
    const area = r.width * r.height;
    if (area < bestArea) { best = e; bestArea = area; }
  }
  if (!best) return null;

  // Walk up, but stop before swallowing the whole page.
  let n = best;
  for (let i = 0; i < up && n.parentElement; i++) {
    const p = n.parentElement;
    const pr = p.getBoundingClientRect();
    const nr = n.getBoundingClientRect();
    // A container that suddenly gets much taller is the pane, not the component.
    if (pr.height > Math.max(140, nr.height * 3)) break;
    if (pr.width > window.innerWidth * 0.96) break;
    n = p;
  }
  const r = n.getBoundingClientRect();
  window.__comp = n;
  return { x: Math.max(0, Math.round(r.x)), y: Math.max(0, Math.round(r.y)),
           width: Math.round(r.width), height: Math.round(r.height) };
}
"""

DUMP = """
() => {
  const root = window.__comp;
  if (!root) return null;
  const PROPS = ['display','position','width','height','font-family','font-size',
    'font-weight','line-height','color','background-color','padding-top',
    'padding-right','padding-bottom','padding-left','margin-top','margin-bottom',
    'gap','border-top-width','border-top-color','border-top-left-radius',
    'box-shadow','flex-direction','align-items','justify-content'];
  const vis = e => {
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = getComputedStyle(e);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const out = [];
  const walk = (e, depth) => {
    if (depth > 6) return;
    const TAG = e.tagName.toUpperCase();
    if (vis(e) && TAG !== 'PATH') {
      const cs = getComputedStyle(e);
      const st = {};
      for (const p of PROPS) {
        const v = cs.getPropertyValue(p).trim();
        if (v && !['none','normal','auto','0px','rgba(0, 0, 0, 0)'].includes(v)) st[p] = v;
      }
      const r = e.getBoundingClientRect();
      let own = '';
      for (const n of e.childNodes) if (n.nodeType === 3) own += n.nodeValue;
      out.push({ tag: e.tagName.toLowerCase(), depth,
                 text: own.trim().slice(0, 60),
                 isIcon: ['SVG','I'].includes(TAG),
                 box: { w: +r.width.toFixed(1), h: +r.height.toFixed(1) },
                 style: st });
    }
    for (const c of e.children) walk(c, depth + 1);
  };
  walk(root, 0);
  return { html: root.outerHTML.slice(0, 200000), nodes: out };
}
"""

VIEWER = """<!doctype html><meta charset=utf-8><title>%(v)s / %(c)s</title>
<style>body{margin:0;font:13px system-ui;background:#111;color:#eee}
.bar{padding:8px 12px;display:flex;gap:10px;align-items:center}
button{padding:5px 10px;border-radius:6px;border:1px solid #444;background:#222;color:#eee;cursor:pointer}
button.on{background:#004eeb;border-color:#004eeb}
img{display:block;max-width:100%%;border:1px solid #333;background:#fff}
.side{display:none;padding:12px}.side.on{display:block}
.sbs{display:flex;gap:12px;align-items:flex-start}
h4{margin:0 0 6px;color:#9aa;font-weight:600}</style>
<div class=bar><b>%(v)s / %(c)s</b>
<button id=a class=on onclick="s('g')">GHL</button>
<button id=b onclick="s('o')">Ours</button>
<button id=d onclick="s('x')">Side by side</button></div>
<div id=g class="side on"><h4>GoHighLevel</h4><img src="../ghl/%(v)s/%(c)s.png"></div>
<div id=o class=side><h4>Ours</h4><img src="../ours/%(v)s/%(c)s.png"></div>
<div id=x class="side sbs"><div><h4>GHL</h4><img src="../ghl/%(v)s/%(c)s.png"></div>
<div><h4>Ours</h4><img src="../ours/%(v)s/%(c)s.png"></div></div>
<script>function s(k){for(const i of['g','o','x'])document.getElementById(i).className='side'+(i===k?' on':'')+(i==='x'?' sbs':'');
for(const[i,v]of[['a','g'],['b','o'],['d','x']])document.getElementById(i).className=(v===k?'on':'')}</script>
"""


def capture_side(page, side, view, comps, out_root):
    d = out_root / side / view
    d.mkdir(parents=True, exist_ok=True)
    found = {}
    for entry in comps:
        if len(entry) == 3:
            name, anchor, up = entry
        else:
            name, a_ghl, a_ours, up = entry
            anchor = a_ghl if side == "ghl" else a_ours
        box = page.evaluate(FIND, [anchor, up])
        if not box or box["width"] < 8 or box["height"] < 8:
            print("    %-20s NOT FOUND (%s)" % (name, side))
            found[name] = None
            continue
        data = page.evaluate(DUMP)
        (d / (name + ".html")).write_text(data["html"], encoding="utf-8")
        (d / (name + ".json")).write_text(
            json.dumps({"box": box, "nodes": data["nodes"]}, indent=1), encoding="utf-8")
        try:
            page.screenshot(path=str(d / (name + ".png")), clip=box)
        except Exception as e:
            print("    %-20s clip failed: %s" % (name, str(e)[:50]))
        found[name] = {"box": box, "nodes": data["nodes"]}
        print("    %-20s %sx%s @%s,%s  %d nodes"
              % (name, box["width"], box["height"], box["x"], box["y"],
                 len(data["nodes"])))
    return found


def main():
    side = sys.argv[1]
    views = sys.argv[2:] or list(COMPONENTS)
    out_root = pathlib.Path("references/components")

    with sync_playwright() as pw:
        b, ctx, page = attach(pw)
        page.bring_to_front()
        set_viewport(page, 1440, 900)

        for view in views:
            url, nav = VIEW_URLS[view]
            print("== %s (%s)" % (view, side))
            if side == "ghl":
                page.goto(url, wait_until="domcontentloaded", timeout=90000)
                ok, why = settle(page, timeout_ms=90000)
                if not ok:
                    print("   SKIPPED:", why)
                    continue
            else:
                page.goto("http://localhost:5173/", wait_until="networkidle", timeout=60000)
                ensure_logged_in(page)
                page.wait_for_timeout(1400)
                page.get_by_role("button", name=nav, exact=False).first.click()
                page.wait_for_timeout(2600)
            capture_side(page, side, view, COMPONENTS[view], out_root)
            if side == "ghl":
                time.sleep(40)

        cmp_dir = out_root / "compare"
        cmp_dir.mkdir(parents=True, exist_ok=True)
        for view in views:
            for entry in COMPONENTS[view]:
                name = entry[0]
                g = out_root / "ghl" / view / (name + ".png")
                o = out_root / "ours" / view / (name + ".png")
                if g.exists() and o.exists():
                    (cmp_dir / ("%s__%s.html" % (view, name))).write_text(
                        VIEWER % {"v": view, "c": name}, encoding="utf-8")
    print("\nviewers: references/components/compare/")


if __name__ == "__main__":
    main()

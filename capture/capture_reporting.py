"""Capture GHL's Reporting tabs that run on first-party data.

DELIBERATELY NOT OPENED: "Google Ads", "Meta Ads (Facebook Ads) report" and
"Local Marketing Audit" — all third-party integrations, excluded by request.
Not opening them also avoids provisioning/consent prompts on a live account.
"""
import json
import pathlib
import time

from attach import attach
from ghl_account import LOC
from playwright.sync_api import sync_playwright
from settle import settle

from capture import set_viewport

BASE = "https://app.gohighlevel.com/v2/location/" + LOC + "/reporting/reports"
EXTRACT_JS = pathlib.Path(__file__).with_name("extract.js").read_text(encoding="utf-8")

ALLOWED = ["Call report", "Appointment report", "Attribution report"]
FORBIDDEN = ["Google Ads", "Meta Ads", "Local Marketing Audit"]

CLICK_TAB = """
(label) => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  for (const e of document.querySelectorAll('a,button,div,span')) {
    if (!vis(e)) continue;
    let own = '';
    for (const n of e.childNodes) if (n.nodeType === 3) own += n.nodeValue;
    if (own.trim() === label) { e.click(); return true; }
  }
  return false;
}
"""

TEXTS = """
() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const out = [];
  for (const e of document.querySelectorAll('*')) {
    if (!vis(e) || e.children.length > 1) continue;
    let own = '';
    for (const n of e.childNodes) if (n.nodeType === 3) own += n.nodeValue;
    const t = (own || e.getAttribute('placeholder') || '').trim();
    if (!t || t.length > 60) continue;
    const r = e.getBoundingClientRect();
    const cs = getComputedStyle(e);
    out.push({ t, x: Math.round(r.x), y: Math.round(r.y),
               fs: cs.fontSize, fw: cs.fontWeight, color: cs.color });
  }
  return out;
}
"""

with sync_playwright() as pw:
    b, ctx, page = attach(pw)
    page.bring_to_front()
    set_viewport(page, 1440, 900)
    out = pathlib.Path("captures/reporting")
    out.mkdir(parents=True, exist_ok=True)

    for label in ALLOWED:
        assert not any(f.lower() in label.lower() for f in FORBIDDEN), label
        page.goto(BASE, wait_until="domcontentloaded", timeout=90000)
        ok, why = settle(page, timeout_ms=90000)
        if not ok:
            print("%-22s SKIPPED (%s)" % (label, why))
            continue
        if not page.evaluate(CLICK_TAB, label):
            print("%-22s TAB NOT FOUND" % label)
            continue
        page.wait_for_timeout(4000)
        settle(page, timeout_ms=60000)
        page.wait_for_timeout(2000)

        slug = label.lower().replace(" ", "_")
        page.evaluate(EXTRACT_JS)
        data = page.evaluate("() => window.__extract()")
        (out / (slug + ".json")).write_text(json.dumps(data, indent=1), encoding="utf-8")
        page.screenshot(path=str(out / (slug + ".png")))

        rows = [r for r in page.evaluate(TEXTS) if r["x"] > 230]
        rows.sort(key=lambda r: (r["y"], r["x"]))
        print("\n=== %s === url=%s" % (label, page.url.split("/location/")[-1][:70]))
        seen = set()
        for r in rows[:34]:
            if r["t"] in seen:
                continue
            seen.add(r["t"])
            print("  y=%-5s x=%-5s %-38s fs=%-6s fw=%s"
                  % (r["y"], r["x"], r["t"][:38], r["fs"], r["fw"]))
        time.sleep(40)

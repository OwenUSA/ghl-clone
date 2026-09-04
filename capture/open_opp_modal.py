"""Open ONE opportunity card on GHL and record the detail modal, then Escape.

Safety contract: deliberately opening a modal is allowed; dismissing it must use
the explicit close affordance, Escape preferred. Nothing inside is clicked, no
field is focused or blurred — a blur on an editor screen can trip an autosave and
mutate real data.
"""
import json
import pathlib

from attach import attach
from ghl_account import LOC
from playwright.sync_api import sync_playwright
from settle import settle

URL = "https://app.gohighlevel.com/v2/location/" + LOC + "/opportunities/list"
EXTRACT_JS = pathlib.Path(__file__).with_name("extract.js").read_text(encoding="utf-8")

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
    const t = (e.innerText || e.getAttribute('placeholder') || '').trim();
    if (!t || t.length > 70) continue;
    const r = e.getBoundingClientRect();
    out.push({ t: t, x: Math.round(r.x), y: Math.round(r.y),
               fs: getComputedStyle(e).fontSize,
               fw: getComputedStyle(e).fontWeight,
               color: getComputedStyle(e).color });
  }
  return out;
}
"""

# Click the first card by its wrapper class - never by coordinate.
OPEN_FIRST_CARD = """
() => {
  const card = document.querySelector('.crm-opportunities-card-wrapper');
  if (!card) return { ok: false };
  const title = (card.innerText || '').split('\\n')[0];
  card.click();
  return { ok: true, title: title };
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

    before = {e["t"] for e in page.evaluate(SNAPSHOT)}
    opened = page.evaluate(OPEN_FIRST_CARD)
    print("opened card:", opened)
    if not opened.get("ok"):
        raise SystemExit("no card found")

    page.wait_for_timeout(3500)
    after = page.evaluate(SNAPSHOT)

    seen, new = set(), []
    for e in after:
        if e["t"] not in before and e["t"] not in seen:
            seen.add(e["t"])
            new.append(e)
    new.sort(key=lambda e: (e["y"], e["x"]))

    page.keyboard.press("Escape")
    page.wait_for_timeout(1200)
    still_open = any(e["t"] not in before for e in page.evaluate(SNAPSHOT))
    print("modal closed by Escape:", not still_open)

    print("\n%d new elements in modal:" % len(new))
    for e in new[:60]:
        print("  y=%-5s x=%-5s %-42s fs=%-6s fw=%-4s %s"
              % (e["y"], e["x"], e["t"].replace("\n", " | ")[:42],
                 e["fs"], e["fw"], e["color"]))

    out = pathlib.Path("captures/opportunities")
    out.mkdir(parents=True, exist_ok=True)
    (out / "detail_modal.json").write_text(
        json.dumps({"card": opened.get("title"), "elements": new}, indent=1),
        encoding="utf-8")
    print("\nwrote captures/opportunities/detail_modal.json")

import pathlib

from attach import attach
from ghl_account import LOC
from playwright.sync_api import sync_playwright
from settle import settle

BASE = "https://app.gohighlevel.com/v2/location/" + LOC
VIEWS = {
    "contacts": BASE + "/contacts/smart_list/All",
    "conversations": BASE + "/conversations/conversations",
    "opportunities": BASE + "/opportunities/list",
    "calendars": BASE + "/calendars/view",
}

JS = """
() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const t = document.body.innerText || '';
  return {
    url: location.href,
    rows: document.querySelectorAll('tr').length,
    tables: document.querySelectorAll('table').length,
    grids: document.querySelectorAll('[role=row]').length,
    iframes: Array.from(document.querySelectorAll('iframe')).filter(vis).length,
    textHead: t.slice(0, 600)
  };
}
"""

with sync_playwright() as pw:
    b, ctx, page = attach(pw)
    pathlib.Path("captures").mkdir(exist_ok=True)
    ctx.storage_state(path="captures/auth.json")

    for name, url in VIEWS.items():
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            ok = settle(page, timeout_ms=60000)
            s = page.evaluate(JS)
            print("--- %s --- settled=%s" % (name, ok))
            print("  url:", s["url"])
            print("  tables=%s tr=%s role_row=%s iframes=%s"
                  % (s["tables"], s["rows"], s["grids"], s["iframes"]))
            print("  text:", " | ".join(x for x in s["textHead"].split("\n") if x.strip())[:450])
            print()
        except Exception as e:
            print("--- %s --- FAILED: %s\n" % (name, e))

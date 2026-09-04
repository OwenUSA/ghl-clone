from attach import attach
from ghl_account import LOC
from playwright.sync_api import sync_playwright
from settle import settle

BASE = "https://app.gohighlevel.com/v2/location/" + LOC
VIEWS = {
    "contacts": BASE + "/contacts/smart_list/All",
    "conversations": BASE + "/conversations/conversations",
    "opportunities": BASE + "/opportunities/list",
}

JS = """
() => {
  const out = { len: (document.body.innerText || '').length, tail: '', scrollers: [] };
  const t = document.body.innerText || '';
  out.tail = t.slice(400, 2600);
  // which element actually scrolls
  for (const e of document.querySelectorAll('*')) {
    if (e.scrollHeight > e.clientHeight + 40 && e.clientHeight > 200) {
      const cs = getComputedStyle(e);
      if (cs.overflowY === 'auto' || cs.overflowY === 'scroll') {
        out.scrollers.push({
          tag: e.tagName.toLowerCase(),
          cls: (e.className || '').toString().slice(0, 70),
          h: e.clientHeight, sh: e.scrollHeight
        });
      }
    }
  }
  out.scrollers = out.scrollers.slice(0, 6);
  out.docScrolls = document.documentElement.scrollHeight > window.innerHeight + 20;
  return out;
}
"""

with sync_playwright() as pw:
    b, ctx, page = attach(pw)
    for name, url in VIEWS.items():
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        settle(page, timeout_ms=60000)
        page.wait_for_timeout(3000)
        s = page.evaluate(JS)
        print("=== %s === innerText chars=%s docScrolls=%s" % (name, s["len"], s["docScrolls"]))
        print("SCROLLERS:", s["scrollers"])
        print("TEXT[400:2600]:")
        print(" | ".join(x.strip() for x in s["tail"].split("\n") if x.strip())[:1400])
        print()

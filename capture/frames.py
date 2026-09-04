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

with sync_playwright() as pw:
    b, ctx, page = attach(pw)
    for name, url in VIEWS.items():
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        settle(page, timeout_ms=60000)
        page.wait_for_timeout(4000)
        print("=== %s === frames=%d" % (name, len(page.frames)))
        for f in page.frames:
            try:
                txt = f.evaluate("() => (document.body && document.body.innerText || '')")
                n = f.evaluate("() => document.querySelectorAll('*').length")
            except Exception as e:
                txt, n = "<blocked: %s>" % str(e)[:60], -1
            flat = " | ".join(x.strip() for x in txt.split("\n") if x.strip())
            print("  frame url=%s" % f.url[:110])
            print("    nodes=%s textlen=%s" % (n, len(txt)))
            if len(flat) > 0:
                print("    text:", flat[:700])
        print()

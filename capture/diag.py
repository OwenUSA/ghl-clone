from attach import attach
from ghl_account import LOC
from playwright.sync_api import sync_playwright

URL = "https://app.gohighlevel.com/v2/location/" + LOC + "/contacts/smart_list/All"

errors, failed, console = [], [], []

with sync_playwright() as pw:
    b, ctx, page = attach(pw)
    page.on("pageerror", lambda e: errors.append(str(e)[:200]))
    page.on("requestfailed", lambda r: failed.append(
        "%s %s" % (r.failure or "?", r.url[:120])))
    page.on("console", lambda m: console.append(
        "%s: %s" % (m.type, m.text[:180])) if m.type in ("error", "warning") else None)

    page.bring_to_front()
    page.goto(URL, wait_until="domcontentloaded", timeout=60000)

    for i in range(9):
        page.wait_for_timeout(5000)
        n = page.evaluate("() => document.querySelectorAll('*').length")
        tl = page.evaluate("() => (document.body.innerText||'').length")
        print("t=%2ds nodes=%4d textlen=%4d" % ((i + 1) * 5, n, tl))

    print("\nPAGE ERRORS (%d):" % len(errors))
    for e in errors[:12]:
        print("  ", e)
    print("\nFAILED REQUESTS (%d):" % len(failed))
    for f in failed[:15]:
        print("  ", f)
    print("\nCONSOLE err/warn (%d):" % len(console))
    for c in console[:20]:
        print("  ", c)

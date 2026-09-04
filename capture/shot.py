import sys

from attach import attach
from ghl_account import LOC
from playwright.sync_api import sync_playwright
from settle import settle

BASE = "https://app.gohighlevel.com/v2/location/" + LOC
url = sys.argv[1] if len(sys.argv) > 1 else BASE + "/contacts/smart_list/All"
out = sys.argv[2] if len(sys.argv) > 2 else "captures/diag.png"

with sync_playwright() as pw:
    b, ctx, page = attach(pw)
    page.bring_to_front()
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    settle(page, timeout_ms=60000)
    page.wait_for_timeout(5000)
    print("viewport:", page.viewport_size)
    print("inner:", page.evaluate("() => [innerWidth, innerHeight]"))
    print("nodes:", page.evaluate("() => document.querySelectorAll('*').length"))
    page.screenshot(path=out)
    print("saved", out)

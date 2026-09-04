"""Screenshot our app at a given nav section. Local only - never touches GHL."""
import sys

from attach import attach
from login import ensure_logged_in
from playwright.sync_api import sync_playwright

from capture import set_viewport

section = sys.argv[1] if len(sys.argv) > 1 else "conversations"
out = sys.argv[2] if len(sys.argv) > 2 else "captures/ours/%s.png" % section

with sync_playwright() as pw:
    b, ctx, page = attach(pw)
    page.bring_to_front()
    set_viewport(page, 1440, 900)
    page.goto("http://localhost:5173/", wait_until="networkidle", timeout=60000)
    ensure_logged_in(page)
    page.wait_for_timeout(1500)
    page.get_by_role("button", name=section, exact=False).first.click()
    page.wait_for_timeout(2500)
    page.screenshot(path=out)
    print("saved", out)

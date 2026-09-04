"""Screenshot the Contact Details panel in both places it is used."""
from attach import attach
from login import ensure_logged_in
from playwright.sync_api import sync_playwright

from capture import set_viewport

with sync_playwright() as pw:
    b, ctx, page = attach(pw)
    page.bring_to_front()
    set_viewport(page, 1440, 900)
    page.goto("http://localhost:5173/", wait_until="networkidle", timeout=60000)
    ensure_logged_in(page)
    page.wait_for_timeout(2000)

    # Contacts: click the first data row to open the panel
    page.get_by_role("button", name="Contacts", exact=False).first.click()
    page.wait_for_timeout(2000)
    page.locator("tbody tr").first.click()
    page.wait_for_timeout(2500)
    page.screenshot(path="captures/ours/contacts_panel.png")
    print("saved captures/ours/contacts_panel.png")

    # Conversations: panel is the permanent third pane
    page.get_by_role("button", name="Conversations", exact=False).first.click()
    page.wait_for_timeout(3000)
    page.screenshot(path="captures/ours/conversations_panel.png")
    print("saved captures/ours/conversations_panel.png")

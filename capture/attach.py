"""Attach to the long-lived capture Chrome over CDP. Never launches a browser."""
from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9222"

def attach(pw):
    b = pw.chromium.connect_over_cdp(CDP)
    ctx = b.contexts[0]
    return b, ctx, ctx.pages[0] if ctx.pages else ctx.new_page()

if __name__ == "__main__":
    with sync_playwright() as pw:
        b, ctx, page = attach(pw)
        print("attached:", b.version)
        for p in ctx.pages:
            print(" tab:", p.url)

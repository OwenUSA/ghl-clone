import json
import re

from attach import attach
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    b, ctx, page = attach(pw)
    for p in ctx.pages:
        print("TAB:", p.url)
        print("  title:", p.title())
    page = ctx.pages[0]
    url = page.url
    m = re.search(r"/location/([A-Za-z0-9]+)", url)
    print("\nlocation_id:", m.group(1) if m else "NOT IN URL")
    # sidebar labels only - read-only, no clicks
    try:
        labels = page.eval_on_selector_all(
            "#sidebar-v2 a, nav a, aside a",
            "els => [...new Set(els.map(e => (e.innerText||'').trim()).filter(Boolean))]",
        )
        print("nav labels:", json.dumps(labels[:40], indent=1))
    except Exception as e:
        print("nav read failed:", e)

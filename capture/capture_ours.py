"""Capture OUR app with the same shared extraction module used on GHL.

Two copies of the extraction logic would drift and produce fake differences, so
this imports extract.js — the identical file — and writes into captures/ours/.
"""
import json
import pathlib
import sys
import time

from attach import attach
from login import ensure_logged_in
from playwright.sync_api import sync_playwright

from capture import set_viewport

URL = "http://localhost:5173/"
NAME = sys.argv[1] if len(sys.argv) > 1 else "contacts"
NAV = sys.argv[2] if len(sys.argv) > 2 else None  # sidebar item to click first

EXTRACT_JS = pathlib.Path(__file__).with_name("extract.js").read_text(encoding="utf-8")

with sync_playwright() as pw:
    b, ctx, page = attach(pw)
    page.bring_to_front()
    set_viewport(page, 1440, 900)
    page.goto(URL, wait_until="networkidle", timeout=60000)
    ensure_logged_in(page)
    page.wait_for_timeout(2000)
    if NAV:
        page.get_by_role("button", name=NAV, exact=False).first.click()
        page.wait_for_timeout(3000)

    page.evaluate(EXTRACT_JS)
    data = page.evaluate("() => window.__extract()")
    data["scrollers"] = page.evaluate("() => window.__scrollers()")
    data["breakpoint"] = "1440x900"
    data["scrollLabel"] = "top"

    out = pathlib.Path("captures/ours") / NAME
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    f = out / ("ours__1440x900__top__%s.json" % stamp)
    f.write_text(json.dumps(data, indent=1), encoding="utf-8")
    page.screenshot(path=str(out / ("ours__1440x900__%s.png" % stamp)))
    print("wrote %s (%d elements)" % (f, data["count"]))
    print("scrollers:", [s["cls"][:50] for s in data["scrollers"]])

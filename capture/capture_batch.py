"""Capture several views with generous in-process gaps.

GHL returns 429 under rapid sequential navigation and then hangs the content
region on a spinner indefinitely. GAP_S is the throttle that avoids that.
Aborts the whole batch on a failed settle rather than writing loading states.
"""
import sys
import time

from attach import attach
from playwright.sync_api import sync_playwright

from capture import VIEWS, capture_view

GAP_S = 75

names = sys.argv[1:] or ["conversations", "opportunities", "calendars"]

with sync_playwright() as pw:
    b, ctx, page = attach(pw)
    for i, name in enumerate(names):
        if name not in VIEWS:
            print("skip unknown view:", name)
            continue
        print("=== capturing %s (%d/%d) ===" % (name, i + 1, len(names)))
        ok = capture_view(page, name, VIEWS[name])
        if not ok:
            print("!! batch stopped at %s - not writing further captures" % name)
            break
        if i < len(names) - 1:
            print("  ... throttling %ds before next view" % GAP_S)
            time.sleep(GAP_S)
print("batch done")

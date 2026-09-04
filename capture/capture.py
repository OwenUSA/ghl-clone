"""Capture one GHL view. Read-only: navigate, resize, scroll. No clicks.

Usage:  python capture/capture.py contacts
One view per invocation, deliberately — GHL rate-limits (429) under rapid
sequential navigation. Do not loop this over all views in one go.
"""
import json
import pathlib
import sys
import time

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

# Desktop only. Mobile is our own design (DECISIONS.md) — GHL has no mobile
# layout worth referencing, so we deliberately do not capture 390x844.
BREAKPOINTS = {"1440x900": (1440, 900)}

EXTRACT_JS = pathlib.Path(__file__).with_name("extract.js").read_text(encoding="utf-8")


def set_viewport(page, w, h, mobile=False):
    """Real Chrome over CDP ignores set_viewport_size; use device metrics override."""
    cdp = page.context.new_cdp_session(page)
    cdp.send("Emulation.setDeviceMetricsOverride", {
        "width": w, "height": h,
        "deviceScaleFactor": 1,
        "mobile": mobile,
    })
    return cdp


def scroll_container(page):
    """Return a JS handle expression for whatever actually scrolls."""
    scrollers = page.evaluate("() => window.__scrollers()")
    return scrollers


def capture_view(page, name, url, pause=6.0):
    out_root = pathlib.Path("captures") / name
    out_root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")

    page.bring_to_front()
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    ok, reason = settle(page, verbose=True)
    print("  settle: %s (%s)" % (ok, reason))
    if not ok:
        print("  !! ABORTING capture of %s - would record a loading state" % name)
        return False

    for bp, (w, h) in BREAKPOINTS.items():
        set_viewport(page, w, h, mobile=(w < 500))
        page.wait_for_timeout(2500)
        ok2, reason2 = settle(page, timeout_ms=45000)
        if not ok2:
            print("  !! %s @ %s did not settle after resize: %s" % (name, bp, reason2))
            continue

        page.evaluate(EXTRACT_JS)
        scrollers = page.evaluate("() => window.__scrollers()")

        for label, frac in (("top", 0.0), ("mid", 0.5), ("bottom", 1.0)):
            where = page.evaluate("(f) => window.__scrollToFrac(f)", frac)
            page.wait_for_timeout(1200)
            data = page.evaluate("() => window.__extract()")
            data["scrollers"] = scrollers
            data["scrolledVia"] = where
            data["breakpoint"] = bp
            data["scrollLabel"] = label
            data["location"] = LOC
            data["capturedAt"] = stamp

            fn = out_root / ("%s__%s__%s__%s.json" % (LOC, bp, label, stamp))
            fn.write_text(json.dumps(data, indent=1), encoding="utf-8")
            print("    wrote %s (%d elements)" % (fn, data["count"]))

        page.screenshot(path=str(out_root / ("%s__%s__%s.png" % (LOC, bp, stamp))))

    # restore desktop
    set_viewport(page, 1440, 900)
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in VIEWS:
        print("usage: capture.py <%s>" % "|".join(VIEWS))
        sys.exit(1)
    name = sys.argv[1]
    with sync_playwright() as pw:
        b, ctx, page = attach(pw)
        print("=== capturing %s ===" % name)
        capture_view(page, name, VIEWS[name])

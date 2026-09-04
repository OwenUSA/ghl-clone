"""Drive every view of OUR app: scroll it, capture it, record console errors.

Mirrors what capture.py does to GHL (same extract.js, same scroll positions) so
the two sides are comparable. Also exercises menus/dialogs and reports any
console error or failed request, which a styling diff alone would never catch.
"""
import json
import pathlib
import time

from attach import attach
from login import ensure_logged_in
from playwright.sync_api import sync_playwright

from capture import set_viewport

VIEWS = [
    ("contacts", "Contacts"),
    ("conversations", "Conversations"),
    ("opportunities", "Opportunities"),
    ("calendars", "Calendars"),
]

EXTRACT_JS = pathlib.Path(__file__).with_name("extract.js").read_text(encoding="utf-8")
STAMP = time.strftime("%Y%m%d-%H%M%S")


def main():
    errors, failed, warnings = [], [], []
    report = {}

    with sync_playwright() as pw:
        b, ctx, page = attach(pw)
        page.on("pageerror", lambda e: errors.append(str(e)[:300]))
        page.on("requestfailed", lambda r: failed.append(r.url[:160]))
        page.on("console", lambda m: (
            errors.append("console.error: " + m.text[:250]) if m.type == "error"
            else warnings.append(m.text[:160]) if m.type == "warning" else None))

        page.bring_to_front()
        set_viewport(page, 1440, 900)
        page.goto("http://localhost:5173/", wait_until="networkidle", timeout=60000)
        ensure_logged_in(page)
        page.wait_for_timeout(2000)

        for name, nav in VIEWS:
            page.get_by_role("button", name=nav, exact=False).first.click()
            page.wait_for_timeout(2500)
            page.evaluate(EXTRACT_JS)

            scrollers = page.evaluate("() => window.__scrollers()")
            out = pathlib.Path("captures/ours") / name
            out.mkdir(parents=True, exist_ok=True)

            per_scroll = {}
            for label, frac in (("top", 0.0), ("mid", 0.5), ("bottom", 1.0)):
                where = page.evaluate("(f) => window.__scrollToFrac(f)", frac)
                page.wait_for_timeout(900)
                data = page.evaluate("() => window.__extract()")
                data.update(scrollers=scrollers, scrolledVia=where,
                            breakpoint="1440x900", scrollLabel=label)
                (out / ("ours__1440x900__%s__%s.json" % (label, STAMP))).write_text(
                    json.dumps(data, indent=1), encoding="utf-8")
                per_scroll[label] = {"count": data["count"], "via": where}

            page.evaluate("() => window.__scrollToFrac(0)")
            page.wait_for_timeout(400)
            page.screenshot(path=str(out / ("audit__%s.png" % STAMP)))

            report[name] = {
                "scrollers": [s["cls"][:60] for s in scrollers],
                "scroll": per_scroll,
            }
            print("%-14s scrollers=%s" % (name, report[name]["scrollers"] or "NONE"))
            for label, v in per_scroll.items():
                print("    %-7s elements=%-5s scrolled=%s"
                      % (label, v["count"], v["via"]))

    print("\n=== console errors: %d ===" % len(errors))
    for e in dict.fromkeys(errors):
        print("  ", e)
    print("=== failed requests: %d ===" % len(failed))
    for f in dict.fromkeys(failed):
        print("  ", f)
    print("=== warnings: %d ===" % len(warnings))
    for w in list(dict.fromkeys(warnings))[:10]:
        print("  ", w)

    pathlib.Path("captures/ours/audit_summary.json").write_text(
        json.dumps({"views": report, "errors": list(dict.fromkeys(errors)),
                    "failedRequests": list(dict.fromkeys(failed))}, indent=1),
        encoding="utf-8")


if __name__ == "__main__":
    main()

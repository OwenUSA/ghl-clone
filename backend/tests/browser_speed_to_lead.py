"""Drive Reporting -> Speed to lead in a REAL headless browser.

    cd backend && uv run python -m tests.browser_speed_to_lead [--shots DIR]

Not collected by pytest (no `test_` prefix): it starts its own API and Vite dev server.
Needs node on PATH, the `capture` dependency group (Playwright) and its Chromium.

The database is a throwaway SQLite file in a fresh temp directory, created with `create_all` —
never `app.seed`, never a database this did not create. The leads are what the Dispatch reader
would have kept (dispatch_jobs, dispatch_calls, zuper_status_history) for the week of Mon
2026-10-05, with invented customers. No phone system and no Zuper is configured, so nothing can
be rung, sent or asked.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import traceback
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tests.browser_dialer import Checks, free_port, wait_http

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend"
ADMIN, TECH = "owner@speed.test", "tech@speed.test"
PASSWORD = "speed-check-password-1"
NY = ZoneInfo("America/New_York")


def ny(d, h, m=0):
    return datetime(2026, 10, d, h, m, tzinfo=NY).astimezone(UTC)


# (job #, board, customer, phone, created) and what happened, all New York time.
LEADS = [
    ("801", "AHS - Inspection", "Robin Tile", "9415550101", ny(5, 9)),
    ("802", "AHS - Inspection", "Carla Jiga", "9415550102", ny(5, 21)),      # after hours
    ("803", "AHS - Inspection", "Sam Benjamin", "9415550103", ny(6, 10)),
    ("804", "AHS - Inspection", "Jean Gilles", "9415550104", ny(7, 11)),     # nobody calls
    ("901", "Retail", "Al Varado", "9415550105", ny(8, 14)),
    ("902", "Sarasota Repairs", "Dan Fleur", "9415550106", ny(9, 9)),
]
# (uid, at, direction, status, seconds, number, staff)
CALLS = [
    ("c1", ny(5, 9, 25), "OUTGOING", "COMPLETED", 240, "9415550101", "Luis Candiales"),
    ("c2", ny(6, 8, 40), "OUTGOING", "COMPLETED", 120, "9415550102", "Luis Candiales"),
    ("c3", ny(6, 13), "OUTGOING", "NO_ANSWER", 0, "9415550103", "Owen Buzaglo"),
    ("c4", ny(6, 15), "OUTGOING", "COMPLETED", 300, "9415550103", "Owen Buzaglo"),
    ("c5", ny(8, 13, 30), "INCOMING", "NO_ANSWER", 0, "9415550105", None),   # rang first
    ("c6", ny(8, 14, 10), "OUTGOING", "COMPLETED", 180, "9415550105", "Luis Candiales"),
    ("c7", ny(9, 11, 30), "OUTGOING", "COMPLETED", 90, "9415550106", "Luis Candiales"),
    ("c8", ny(7, 10), "INCOMING", "NO_ANSWER", 0, "9415559999", None),        # a stranger
]


def serve(port: int, db_path: str) -> None:
    for key in ("OWEN_BASE_URL", "OWEN_SOFTPHONE_KEY", "CRM_LINK_BASE_URL", "CRM_LINK_API_KEY",
                "ZUPER_API_KEY", "ZUPER_SYNC_ENABLED", "DISPATCH_ENABLED"):
        os.environ.pop(key, None)
    os.environ.update({"DATABASE_URL": "sqlite:///" + db_path, "AUTO_CREATE_ALL": "1"})
    sys.path.insert(0, str(BACKEND))
    import uvicorn
    from app import auth
    from app.db import SessionLocal
    from app.main import app
    from app.models import DispatchCall, DispatchJob, Role, User, ZuperStatusHistory

    with SessionLocal() as db:
        db.add(User(email=ADMIN, name="Owner", role=Role.ADMIN,
                    password_hash=auth.hash_password(PASSWORD)))
        db.add(User(email=TECH, name="Tech", role=Role.TECH,
                    password_hash=auth.hash_password(PASSWORD)))
        for number, board, name, phone, created in LEADS:
            db.add(DispatchJob(job_uid="j" + number, job_number=number, board=board,
                               title=name + " roof", customer_name=name, phones=[phone],
                               status="Work Order Received", zuper_created_at=created))
        for uid, at, direction, status, secs, number, staff in CALLS:
            db.add(DispatchCall(call_uid=uid, occurred_at=at, direction=direction,
                                status=status, duration_seconds=secs, number=number,
                                staff_name=staff))
        db.add_all([
            ZuperStatusHistory(history_uid="h1", job_uid="j801", status_name="Work Order Received",
                               changed_at=ny(5, 9)),
            ZuperStatusHistory(history_uid="h2", job_uid="j801", status_name="Welcome Call!",
                               changed_at=ny(5, 9, 27), done_by_name="Luis Candiales")])
        db.commit()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def run(shots: Path) -> int:
    from playwright.sync_api import expect, sync_playwright

    work = Path(tempfile.mkdtemp(prefix="speed-check-"))
    db_path = str(work / "speed.db")
    api_port, vite_port = free_port(), free_port()
    procs: list[subprocess.Popen] = []
    checks = Checks()
    try:
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "tests.browser_speed_to_lead", "--serve", str(api_port),
             db_path], cwd=BACKEND, start_new_session=True, stdout=subprocess.DEVNULL))
        wait_http(f"http://127.0.0.1:{api_port}/api/health", timeout=120)
        env = {**os.environ, "GHL_API_PORT": str(api_port), "GHL_VITE_PORT": str(vite_port)}
        procs.append(subprocess.Popen(
            ["npx", "vite", "--config", "e2e/vite.sipmock.config.ts"], cwd=FRONTEND, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
            shell=sys.platform == "win32"))
        app_url = f"http://localhost:{vite_port}/"
        wait_http(app_url, timeout=120)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)

            def signed_in(email):
                ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                          timezone_id="America/New_York")
                page = ctx.new_page()
                page.goto(app_url)
                page.fill('input[type="email"]', email)
                page.fill('input[type="password"]', PASSWORD)
                page.click('button[type="submit"]')
                page.wait_for_selector("text=Sign in to continue", state="detached")
                page.get_by_role("button", name="Reporting", exact=True).first.click()
                return page

            page = signed_in(ADMIN)
            page.get_by_role("button", name="Speed to lead", exact=True).click()
            dates = page.locator('input[type="date"]')
            dates.nth(0).fill("2026-10-05")
            dates.nth(1).fill("2026-10-10")
            table = page.get_by_role("table", name="Speed to lead, every lead")
            expect(table.locator("tbody tr")).to_have_count(6, timeout=20000)
            page.mouse.move(5, 5)
            page.screenshot(path=str(shots / "01-speed-to-lead.png"), full_page=True)
            # Nothing on the page is wider than the page: wide tables scroll inside their card.
            over = page.evaluate("""() => { const m = document.querySelector('main') ||
                document.body; const right = m.getBoundingClientRect().right;
                return [...document.querySelectorAll('section, [data-tile]')]
                  .filter(e => e.getBoundingClientRect().right > right + 1).length }""")
            checks.ok(over == 0, f"no card or tile runs past the right edge ({over})")

            on_time = page.locator("text=Contacted on time").locator("..")
            # 801 25 min, 802 40 min (after hours: from 8 AM), 901 40 min from their call,
            # on time; 803 3 h and 902 2 h 30 late; 804 never contacted.
            checks.ok("50%" in on_time.inner_text() and "3 of 6" in on_time.inner_text(),
                      f"3 of 6 leads on time ({on_time.inner_text()!r})")
            row = lambda n: table.locator("tbody tr", has_text="#" + n).inner_text()  # noqa: E731
            checks.ok("25 min" in row("801") and "On time" in row("801")
                      and "Luis Candiales" in row("801"), "801: Luis called in 25 minutes")
            checks.ok("40 min" in row("802") and "11 h 40 min on the clock" in row("802"),
                      "802: arrived at 9 PM, called 8:40 AM = 40 business minutes")
            checks.ok("Late" in row("803") and "3 h" in row("803"),
                      "803: answered after 3 business hours is late")
            checks.ok("Not contacted" in row("804"), "804: nobody called")
            checks.ok("Customer called first" in row("901") and "40 min" in row("901"),
                      "901: the clock starts at the customer's own call")
            checks.ok("27 min" in row("801") and "Welcome Call!" in row("801"),
                      "801: Zuper's column moved 27 minutes after creation")

            table.scroll_into_view_if_needed()
            page.screenshot(path=str(shots / "03-every-lead.png"))
            page.get_by_role("button", name="Not contacted · 1").click()
            expect(table.locator("tbody tr")).to_have_count(1)
            checks.ok("#804" in table.inner_text(), "the Not contacted filter shows only 804")

            page.get_by_role("button", name="Retail (and regional)").click()
            page.get_by_role("button", name="All · 2").click()
            expect(table.locator("tbody tr")).to_have_count(2, timeout=20000)
            shown = table.inner_text()
            checks.ok("#901" in shown and "#902" in shown and "#801" not in shown,
                      "the Retail filter shows 901 and 902 (regional board) and no AHS lead")
            missed = page.locator("text=Missed calls — how fast we call back").locator("../..")
            checks.ok("Never called back" in missed.inner_text(),
                      "the missed-calls card is shown")
            page.screenshot(path=str(shots / "02-retail.png"), full_page=True)

            tech = signed_in(TECH)
            checks.ok(tech.get_by_role("button", name="Speed to lead").count() == 0,
                      "a technician has no Speed to lead tab")
            browser.close()
        failed = [w for ok, w in checks.results if not ok]
        print("\n%d/%d checks passed" % (len(checks.results) - len(failed), len(checks.results)),
              flush=True)
        return 1 if failed else 0
    except Exception:  # noqa: BLE001 - report and fail the run
        traceback.print_exc()
        return 1
    finally:
        for p in procs:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                               capture_output=True)
            else:
                p.kill()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", nargs=2)
    ap.add_argument("--shots", default=None)
    args = ap.parse_args()
    if args.serve:
        serve(int(args.serve[0]), args.serve[1])
        return 0
    shots = Path(args.shots or tempfile.mkdtemp(prefix="speed-shots-"))
    shots.mkdir(parents=True, exist_ok=True)
    print("screenshots in", shots, flush=True)
    return run(shots)


if __name__ == "__main__":
    sys.exit(main())

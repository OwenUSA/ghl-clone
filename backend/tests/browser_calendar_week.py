"""Drive the Calendars week view in a REAL headless browser over the measured Monday.

    cd backend && uv run python -m tests.browser_calendar_week [--shots DIR]

Not collected by pytest (no `test_` prefix): it starts its own API and Vite dev server.
Needs node on PATH, the `capture` dependency group (Playwright) and its Chromium.

The database is a throwaway SQLite file in a fresh temp directory, created with
`create_all` — never `app.seed`, never a database this did not create. The data arrives
the way production's does: fixture CSVs through `app.workiz_import --commit`, TWICE —
the first export has J2P6JO on Monday 10–12 and leaves 9CNTDD the way an earlier import
did (titled "Inspection", no import record); the second has Workiz's real times. The
names are invented; the Job #s and times are production's week of Sep 13–19, 2026.
No phone system is configured (OWEN_* / CRM_LINK_* unset), so nothing can be rung or sent.

The browser runs in America/New_York, the company's zone, at 1440x900.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

from tests.browser_dialer import Checks, free_port, wait_http

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend"
ADMIN = "owner@calweek.test"
PASSWORD = "calweek-check-password-1"

# (Job #, invented customer, phone, start, end, street, city) — Eastern wall clock.
WEEK = [
    ("03T4HE", "Robin Tile", "9415550101", "2026-09-14 07:30", "2026-09-14 10:00",
     "5790 SW 34th St", "Miami"),
    ("9CNTDD", "Carla Jiga", "9415550102", "2026-09-14 12:00", "2026-09-14 14:00",
     "925 165th Ave", "Pembroke Pines"),
    ("QNAXZ2", "Sam Benjamin", "9415550103", "2026-09-14 12:00", "2026-09-14 13:00",
     "8712 Palm Way", "Coral Springs"),
    ("IBZM14", "Jean Gilles", "9415550104", "2026-09-14 12:30", "2026-09-14 13:30",
     "7109 Oak Ln", "Tamarac"),
    ("0J2X27", "Al Varado", "9415550105", "2026-09-14 15:00", "2026-09-14 17:00",
     "3021 37th Ave", "West Hollywood"),
    ("Z6GSNN", "Dan Fleur", "9415550106", "2026-09-14 15:00", "2026-09-14 16:00",
     "477 SE 35th Ave", "Homestead"),
    ("N61PDL", "Sav Vanwyk", "9415550107", "2026-09-14 16:00", "2026-09-14 18:00",
     "6102 NW 72nd Way", "Parkland"),
    ("J2P6JO", "Sawan Estimate", "9415550108", "2026-09-15 13:00", "2026-09-15 15:00",
     "16313 28th Ct", "Miramar"),
    ("K9HW88", "Allison Stieg", "9415550109", "2026-09-15 15:00", "2026-09-15 17:00",
     "1498 NE 39th St", "Oakland Park"),
    ("RC9C3E", "Chris Mitch", "9415550110", "2026-09-15 15:00", "2026-09-15 17:00",
     "3605 82nd Ave", "Coral Springs"),
    ("WFNSXZ", "Rose Mae", "9415550111", "2026-09-16 07:30", "2026-09-16 10:00",
     "839 Montclaire Ct", "West Palm Beach"),
    # A multi-day job: Thursday morning to Friday mid-morning.
    ("ELK2KF", "Jess Weiss", "9415550112", "2026-09-17 07:30", "2026-09-18 10:00",
     "22153 Larkspur Trl", "Boca Raton"),
]
# The first export: J2P6JO still on Monday 10-12, as production holds it.
FIRST_TIMES = {"J2P6JO": ("2026-09-14 10:00", "2026-09-14 12:00")}


def wz(stamp: str) -> str:
    return datetime.strptime(stamp, "%Y-%m-%d %H:%M").strftime("%a %b %d, %Y %I:%M %p")


def write_export(work: str, first: bool) -> tuple[str, str]:
    clients, jobs = [], []
    for i, (job, name, phone, start, end, street, city) in enumerate(WEEK, start=1):
        if first and job in FIRST_TIMES:
            start, end = FIRST_TIMES[job]
        clients.append({"Client #": str(i), "Name": name, "Email": "", "Address": "",
                        "Phone": phone, "Ad Source": ""})
        jobs.append({"Job #": job, "Job name": "Inspection", "Client": name, "Tags": "",
                     "Type": "Roof Repair", "Job Created": wz("2026-09-01 09:00"),
                     "Scheduled": wz(start), "End": wz(end), "Phone": phone, "Email": "",
                     "Status": "Submitted", "Tech": "", "Created by": "Office",
                     "Address": street, "City": city, "State": "FL", "Zip code": "33000",
                     "Total": "100", "Source": "AHS", "Lead Created Date": "",
                     "Job origin": "New"})
    paths = []
    for name, rows in (("clients", clients), ("jobs", jobs)):
        path = os.path.join(work, "%s-%s.csv" % (name, "first" if first else "second"))
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        paths.append(path)
    return paths[0], paths[1]


def serve(port: int, db_path: str, work: str) -> None:
    """Seed the owner, import twice, then serve the API. Runs in a child process."""
    for key in ("OWEN_BASE_URL", "OWEN_SOFTPHONE_KEY", "CRM_LINK_BASE_URL", "CRM_LINK_API_KEY"):
        os.environ.pop(key, None)
    os.environ.update({"DATABASE_URL": "sqlite:///" + db_path, "AUTO_CREATE_ALL": "1"})
    sys.path.insert(0, str(BACKEND))
    import uvicorn
    from app import auth
    from app import workiz_import as wi
    from app.db import SessionLocal
    from app.main import app
    from app.models import Appointment, Opportunity, Role, User
    from sqlalchemy import select

    with SessionLocal() as db:
        db.add(User(email=ADMIN, name="Owner", role=Role.ADMIN,
                    password_hash=auth.hash_password(PASSWORD)))
        db.commit()

    clients, jobs = write_export(work, first=True)
    assert wi.main(["--clients", clients, "--jobs", jobs, "--commit"]) == 0
    with SessionLocal() as db:
        # 9CNTDD as an earlier import left it: titled after the job, no import record.
        card = next(o for o in db.scalars(select(Opportunity)).all()
                    if o.custom_fields.get("workiz_id") == "9CNTDD")
        blob = dict(card.custom_fields)
        blob.pop(wi.WORKIZ_APPOINTMENT)
        card.custom_fields = blob
        db.scalar(select(Appointment).where(
            Appointment.opportunity_id == card.id)).title = "Inspection"
        db.commit()
    clients, jobs = write_export(work, first=False)
    assert wi.main(["--clients", clients, "--jobs", jobs, "--commit"]) == 0

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def run(shots: Path) -> int:
    from playwright.sync_api import expect, sync_playwright

    work = Path(tempfile.mkdtemp(prefix="calweek-check-"))
    db_path = str(work / "calweek.db")
    api_port, vite_port = free_port(), free_port()
    procs: list[subprocess.Popen] = []
    checks = Checks()

    def rows(sql: str):
        with sqlite3.connect(db_path) as c:
            return c.execute(sql).fetchall()

    try:
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "tests.browser_calendar_week", "--serve", str(api_port),
             db_path, str(work)], cwd=BACKEND, start_new_session=True,
            stdout=subprocess.DEVNULL))
        wait_http(f"http://127.0.0.1:{api_port}/api/health", timeout=120)
        env = {**os.environ, "GHL_API_PORT": str(api_port), "GHL_VITE_PORT": str(vite_port)}
        procs.append(subprocess.Popen(
            ["npx", "vite", "--config", "e2e/vite.sipmock.config.ts"], cwd=FRONTEND, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        app_url = f"http://localhost:{vite_port}/"
        wait_http(app_url, timeout=120)
        print(f"API :{api_port} (pid {procs[0].pid}), Vite :{vite_port} (pid {procs[1].pid}),"
              f" db {db_path}", flush=True)

        # ---- what the two imports wrote ----------------------------------------------
        appts = rows("select o.title, a.title, a.starts_at, a.ends_at, a.status from "
                     "appointments a join opportunities o on o.id = a.opportunity_id")
        checks.ok(len(appts) == len(WEEK), f"one appointment per job ({len(appts)})")
        checks.ok(all(card == title for card, title, *_ in appts),
                  "every visit is titled like its card (9CNTDD's 'Inspection' fixed)")
        j2 = [a for a in appts if a[0] == "Sawan Estimate"][0]
        checks.ok(j2[2].startswith("2026-09-15 17:00"),
                  f"J2P6JO was MOVED to Tue 1 PM Eastern ({j2[2]})")
        checks.ok(rows("select count(*) from jobs")[0][0] == 0,
                  "the imports queued nothing (past visits included)")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                      timezone_id="America/New_York")
            page = ctx.new_page()
            page.goto(app_url)
            page.fill('input[type="email"]', ADMIN)
            page.fill('input[type="password"]', PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_selector("text=Sign in to continue", state="detached")
            page.get_by_role("button", name="Calendars", exact=True).first.click()

            label = "Sep 13 – Sep 19, 2026"
            for _ in range(120):
                shown = page.locator("text=/[A-Z][a-z]{2} \\d+ – [A-Z][a-z]{2} \\d+, \\d{4}/")
                current = shown.first.inner_text()
                if current == label:
                    break
                start = datetime.strptime(current.split(" – ")[0] + current[-6:],
                                          "%b %d, %Y")
                page.get_by_role("button", name="Previous" if start > datetime(2026, 9, 13)
                                 else "Next").click()
            expect(page.get_by_text(label, exact=True)).to_be_visible()
            mon = page.locator('[data-day-column="Mon Sep 14 2026"]')
            expect(mon.locator("[data-appointment]")).to_have_count(7, timeout=15000)
            page.mouse.move(5, 5)
            page.screenshot(path=str(shots / "01-week-sep-13-19.png"))

            def boxes(column):
                out = {}
                for el in column.locator("[data-appointment]").all():
                    b = el.bounding_box()
                    out[el.inner_text().split("\n")[0].split(",")[0]] = (el, b)
                return out

            def intersect(a, b):
                w = min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"])
                h = min(a["y"] + a["height"], b["y"] + b["height"]) - max(a["y"], b["y"])
                return max(0, w) * max(0, h)

            def column_box(column):
                return column.bounding_box()

            # ---- Monday: seven blocks, heavy overlap, nothing behind anything ----------
            m = boxes(mon)
            names = {w[1]: w[0] for w in WEEK}
            checks.ok(set(m) == {n for j, n, *_ in WEEK[:7]}, f"Monday draws all seven ({sorted(m)})")
            worst = max((intersect(a[1], b[1]) for i, a in enumerate(m.values())
                         for b in list(m.values())[i + 1:]), default=0)
            checks.ok(worst <= 1.0, f"no two Monday blocks overlap on screen (max {worst:.1f}px²)")
            hidden = []
            for name, (el, b) in m.items():
                hit = page.evaluate(
                    "([x, y]) => document.elementFromPoint(x, y)?.closest('[data-appointment]')"
                    "?.getAttribute('data-appointment')",
                    [b["x"] + b["width"] / 2, b["y"] + min(b["height"] / 2, 8)])
                if hit != el.get_attribute("data-appointment"):
                    hidden.append(name)
            checks.ok(not hidden, f"every Monday block is the topmost thing at its centre {hidden}")

            col = column_box(mon)
            three = [m[n][1] for n in ("Carla Jiga", "Sam Benjamin", "Jean Gilles")]
            checks.ok(all(abs(b["width"] - col["width"] / 3) <= 4 for b in three),
                      "12:00-2, 12:00-1 and 12:30-1:30 split the column in three "
                      f"({[round(b['width']) for b in three]} of {round(col['width'])})")
            hour_px = m["Robin Tile"][1]["height"] / 2.5
            for name, hours in (("Carla Jiga", 2), ("Sam Benjamin", 1), ("Al Varado", 2),
                                ("Sav Vanwyk", 2)):
                h = m[name][1]["height"]
                checks.ok(abs(h - hours * hour_px) <= 1.5,
                          f"{names[name]} is {hours}h tall ({h:.1f}px at {hour_px:.1f}px/h)")
            text = m["Robin Tile"][0].inner_text()
            checks.ok("7:30 – 10:00 AM" in text, f"the block shows its time RANGE ({text!r})")
            checks.ok("Job #03T4HE" in text and "5790 SW 34th St, Miami" in text,
                      "a tall block shows the Workiz Job # and street, city")
            tip = m["Sam Benjamin"][0].get_attribute("title") or ""
            checks.ok("Job #QNAXZ2" in tip and "8712 Palm Way, Coral Springs" in tip
                      and "12:00 – 1:00 PM" in tip, f"hover carries the full detail ({tip!r})")

            # ---- Tuesday: the pair at 3-5 that used to be stacked, and the move -------
            tue = page.locator('[data-day-column="Tue Sep 15 2026"]')
            t = boxes(tue)
            a, b = t["Allison Stieg"][1], t["Chris Mitch"][1]
            checks.ok(intersect(a, b) <= 1.0 and abs(a["y"] - b["y"]) <= 1
                      and abs(a["width"] - b["width"]) <= 2
                      and abs(a["width"] - column_box(tue)["width"] / 2) <= 4,
                      "K9HW88 and RC9C3E at 3-5 are side by side, half the column each")
            checks.ok("Sawan Estimate" in t and "Sawan Estimate" not in m,
                      "J2P6JO is drawn on Tuesday, not on Monday where it used to be")

            # ---- the multi-day job is on Thursday and Friday ---------------------------
            thu = boxes(page.locator('[data-day-column="Thu Sep 17 2026"]'))
            fri = boxes(page.locator('[data-day-column="Fri Sep 18 2026"]'))
            checks.ok("Jess Weiss" in thu and "Jess Weiss" in fri,
                      "a job from Thu 7:30 AM to Fri 10 AM is drawn on both days")
            pane_box = page.locator("[data-hour-pane]").bounding_box()
            words = fri["Jess Weiss"][0].locator("[data-block-range]").bounding_box()
            checks.ok(words is not None and pane_box["y"] <= words["y"]
                      <= pane_box["y"] + pane_box["height"],
                      "Friday's carried-over part shows its words where the grid opens, "
                      f"not at midnight ({words and round(words['y'])} in pane "
                      f"{round(pane_box['y'])}-{round(pane_box['y'] + pane_box['height'])})")
            checks.ok(fri["Jess Weiss"][0].locator("[data-block-range]").inner_text()
                      == "Thu 7:30 AM – Fri 10:00 AM", "...and its range names both days")

            # ---- the window opens on 7 AM-7 PM -----------------------------------------
            pane = page.evaluate("() => { const p = document.querySelector('[data-hour-pane]');"
                                 " return [p.scrollTop, p.clientHeight] }")
            checks.ok(abs(pane[0] - 7 * hour_px) <= 1 and pane[1] >= 12 * hour_px - 1,
                      f"the grid opens at 7 AM with 7 PM in view (scrollTop {pane[0]}, "
                      f"pane {pane[1]}px, {hour_px:.1f}px/h)")

            # ---- a click opens the existing detail panel -------------------------------
            m["Robin Tile"][0].click()
            dlg = page.get_by_role("dialog", name="Appointment details")
            expect(dlg).to_be_visible(timeout=10000)
            page.screenshot(path=str(shots / "02-block-click-opens-detail.png"))
            dlg.get_by_role("button", name="Close").first.click()
            expect(dlg).to_be_hidden()

            # ---- Day view ---------------------------------------------------------------
            page.locator("select").first.select_option("Day view")
            for _ in range(10):
                if page.locator('[data-day-column="Mon Sep 14 2026"]').count():
                    break
                page.get_by_role("button", name="Previous").click()
            day = page.locator('[data-day-column="Mon Sep 14 2026"]')
            expect(day.locator("[data-appointment]")).to_have_count(7)
            d = boxes(day)
            worst = max((intersect(x[1], y[1]) for i, x in enumerate(d.values())
                         for y in list(d.values())[i + 1:]), default=0)
            checks.ok(worst <= 1.0, "Day view: the same seven, none overlapping")
            page.mouse.move(5, 5)
            page.screenshot(path=str(shots / "03-day-mon-sep-14.png"))

            # ---- Month view: every booking, "+N more" ----------------------------------
            page.locator("select").first.select_option("Month view")
            cell = page.locator('[data-month-cell="Mon Sep 14 2026"]')
            more = cell.locator("[data-month-more]")
            expect(more).to_have_text("+4 more", timeout=10000)
            checks.ok(cell.locator("[data-appointment]").count() == 3,
                      "the crowded Monday shows three chips and +4 more")
            page.screenshot(path=str(shots / "04-month-september.png"))
            more.click()
            pop = page.locator('[data-day-popover="Mon Sep 14 2026"]')
            expect(pop).to_be_visible()
            checks.ok(pop.locator("[data-appointment]").count() == 7,
                      "+4 more opens a list of all seven")
            page.screenshot(path=str(shots / "05-month-plus-more-popover.png"))
            page.keyboard.press("Escape")
            expect(pop).to_be_hidden()

            # ---- the week again, with Manage view closed: the widest the blocks get ----
            page.locator("select").first.select_option("Week view")
            for _ in range(10):
                if page.get_by_text(label, exact=True).count():
                    break
                page.get_by_role("button", name="Next").click()
            page.get_by_text("Manage view", exact=True).last.locator(
                "xpath=following-sibling::button").click()
            expect(mon.locator("[data-appointment]")).to_have_count(7)
            m = boxes(mon)
            worst = max((intersect(x[1], y[1]) for i, x in enumerate(m.values())
                         for y in list(m.values())[i + 1:]), default=0)
            checks.ok(worst <= 1.0, "panel closed: still no two Monday blocks overlapping")
            page.mouse.move(5, 5)
            page.screenshot(path=str(shots / "06-week-manage-view-closed.png"))
            ctx.close()
            browser.close()
    except Exception:  # noqa: BLE001 - report and fail the run
        traceback.print_exc()
        checks.ok(False, "the run completed")
    finally:
        for p in procs:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(p.pid, signal.SIGTERM)
        time.sleep(0.5)

    failed = [w for ok, w in checks.results if not ok]
    print(f"\n{len(checks.results) - len(failed)}/{len(checks.results)} checks passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        serve(int(sys.argv[2]), sys.argv[3], sys.argv[4])
        sys.exit(0)
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", default=None)
    args = parser.parse_args()
    shots = Path(args.shots or tempfile.mkdtemp(prefix="calweek-shots-"))
    shots.mkdir(parents=True, exist_ok=True)
    print("screenshots:", shots, flush=True)
    sys.exit(run(shots))

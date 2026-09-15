"""Drive the opportunity modal's "Workiz technician(s)" field in a REAL headless browser.

    cd backend && uv run python -m tests.browser_workiz_tech [--shots DIR]

Not collected by pytest (no `test_` prefix): it starts its own API and Vite dev server.
Needs node on PATH, the `capture` dependency group (Playwright) and its Chromium.

The database is a throwaway SQLite file in a fresh temp directory, created with
`create_all` — never `app.seed`, never a database this did not create. The data arrives
the way production's does: fixture CSVs through `app.workiz_import`, with a tech map.
No phone system is configured (OWEN_* / CRM_LINK_* unset), so nothing can be rung or sent.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.browser_dialer import Checks, free_port, wait_http

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend"
ADMIN, TECH = "owner@wztech.test", "tess@wztech.test"
PASSWORD = "wztech-check-password-1"


def serve(port: int, db_path: str, work: str) -> None:
    """Seed users, run the importer, then serve the API. Runs in a child process."""
    for key in ("OWEN_BASE_URL", "OWEN_SOFTPHONE_KEY", "CRM_LINK_BASE_URL", "CRM_LINK_API_KEY"):
        os.environ.pop(key, None)
    os.environ.update({"DATABASE_URL": "sqlite:///" + db_path, "AUTO_CREATE_ALL": "1"})
    sys.path.insert(0, str(BACKEND))
    import uvicorn
    from app import auth
    from app import workiz_import as wi
    from app.db import SessionLocal
    from app.main import app
    from app.models import Role, User

    with SessionLocal() as db:
        db.add(User(email=ADMIN, name="Owner", role=Role.ADMIN,
                    password_hash=auth.hash_password(PASSWORD)))
        db.add(User(email=TECH, name="Tess Tech", role=Role.TECH, only_assigned_data=True,
                    password_hash=auth.hash_password(PASSWORD)))
        db.commit()

    def wz(moment):
        return moment.astimezone(wi.WORKIZ_TZ).strftime(wi.WORKIZ_DATETIME)

    soon = datetime.now(UTC) + timedelta(days=5)
    clients = [{"Client #": "1", "Name": "Ada Rowe", "Email": "", "Address": "",
                "Phone": "9415550111", "Ad Source": ""},
               {"Client #": "2", "Name": "Ben Vale", "Email": "", "Address": "",
                "Phone": "9415550222", "Ad Source": ""}]
    base = {"Job name": "", "Tags": "", "Type": "Roof Repair", "Job Created": wz(soon),
            "Scheduled": wz(soon), "End": wz(soon + timedelta(hours=2)), "Email": "",
            "Status": "Submitted", "Created by": "Office", "Address": "", "City": "",
            "State": "", "Zip code": "", "Total": "100", "Source": "AHS",
            "Lead Created Date": "", "Job origin": "New"}
    jobs = [{**base, "Job #": "J1", "Client": "Ada Rowe", "Phone": "9415550111",
             "Tech": "NIco,  Tess  Tech , Antonio Brown"},
            {**base, "Job #": "J2", "Client": "Ben Vale", "Phone": "9415550222", "Tech": ""}]
    paths = {}
    for name, rows in (("clients", clients), ("jobs", jobs)):
        paths[name] = os.path.join(work, name + ".csv")
        with open(paths[name], "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    tech_map = os.path.join(work, "techs.json")
    with open(tech_map, "w") as fh:
        json.dump({"NIco": None}, fh)
    assert wi.main(["--clients", paths["clients"], "--jobs", paths["jobs"], "--commit",
                    "--tech-map", tech_map]) == 0

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def run(shots: Path) -> int:
    from playwright.sync_api import expect, sync_playwright

    work = Path(tempfile.mkdtemp(prefix="wztech-check-"))
    db_path = str(work / "wztech.db")
    api_port, vite_port = free_port(), free_port()
    procs: list[subprocess.Popen] = []
    checks = Checks()

    def one(sql: str):
        with sqlite3.connect(db_path) as c:
            return c.execute(sql).fetchone()

    try:
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "tests.browser_workiz_tech", "--serve", str(api_port),
             db_path, str(work)], cwd=BACKEND, start_new_session=True,
            stdout=subprocess.DEVNULL))
        wait_http(f"http://127.0.0.1:{api_port}/api/health")
        env = {**os.environ, "GHL_API_PORT": str(api_port), "GHL_VITE_PORT": str(vite_port)}
        procs.append(subprocess.Popen(
            ["npx", "vite", "--config", "e2e/vite.sipmock.config.ts"], cwd=FRONTEND, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        app_url = f"http://localhost:{vite_port}/"
        wait_http(app_url)
        print(f"API :{api_port} (pid {procs[0].pid}), Vite :{vite_port} (pid {procs[1].pid}),"
              f" db {db_path}", flush=True)

        tess_id = one(f"select id from users where email = '{TECH}'")[0]
        assigned = one("select a.assigned_user_id from appointments a join opportunities o"
                       " on o.id = a.opportunity_id where o.title = 'Ada Rowe'")[0]
        checks.ok(assigned == tess_id,
                  "the import assigned J1's visit to the first MAPPED tech (NIco is null)")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)

            def sign_in(email):
                ctx = browser.new_context(viewport={"width": 1440, "height": 900})
                page = ctx.new_page()
                page.goto(app_url)
                page.fill('input[type="email"]', email)
                page.fill('input[type="password"]', PASSWORD)
                page.click('button[type="submit"]')
                page.wait_for_selector("text=Sign in to continue", state="detached")
                page.get_by_role("button", name="Opportunities", exact=True).first.click()
                return ctx, page

            def open_card(page, title):
                page.get_by_text(title, exact=True).first.click()
                dlg = page.get_by_role("dialog", name=f'Edit "{title}"')
                expect(dlg).to_be_visible(timeout=10000)
                expect(dlg.get_by_text("Opportunity details", exact=True).first).to_be_visible()
                return dlg

            # ---- the owner --------------------------------------------------------
            ctx, page = sign_in(ADMIN)
            dlg = open_card(page, "Ada Rowe")
            field = dlg.get_by_label("Workiz technician(s)")
            checks.ok(field.count() == 1, "the modal shows one 'Workiz technician(s)' field")
            checks.ok(field.input_value() == "NIco, Tess Tech, Antonio Brown",
                      f"names in job order, whitespace collapsed ({field.input_value()!r})")
            checks.ok(field.evaluate("el => el.readOnly") is True, "the field is read-only")
            field.click()
            page.keyboard.type("forged")
            checks.ok(field.input_value() == "NIco, Tess Tech, Antonio Brown",
                      "typing into it changes nothing")
            field.scroll_into_view_if_needed()
            page.screenshot(path=str(shots / "01-admin-modal-workiz-technicians.png"))
            dlg.get_by_label("Hide empty fields").check()
            checks.ok(dlg.get_by_label("Workiz technician(s)").count() == 1,
                      "Hide empty fields keeps a field that has names")
            dlg.get_by_role("button", name="Close").click()

            dlg = open_card(page, "Ben Vale")
            checks.ok(dlg.get_by_label("Workiz technician(s)").count() == 1,
                      "a card with no technician shows the empty field by default")
            checks.ok(dlg.get_by_label("Workiz technician(s)").input_value() == "",
                      "...empty")
            dlg.get_by_label("Hide empty fields").check()
            checks.ok(dlg.get_by_label("Workiz technician(s)").count() == 0,
                      "Hide empty fields hides it")
            dlg.get_by_role("button", name="Close").click()
            ctx.close()

            # ---- the restricted technician -----------------------------------------
            ctx, page = sign_in(TECH)
            dlg = open_card(page, "Ada Rowe")
            field = dlg.get_by_label("Workiz technician(s)")
            checks.ok(field.count() == 1 and
                      field.input_value() == "NIco, Tess Tech, Antonio Brown",
                      "the tech, whose visit it is, sees the names on their job")
            field.scroll_into_view_if_needed()
            page.screenshot(path=str(shots / "02-tech-modal-workiz-technicians.png"))
            dlg.get_by_role("button", name="Close").click()
            # Her board has loaded (she just opened a card from it): Ben's job is not on it.
            checks.ok(page.get_by_text("Ben Vale", exact=True).count() == 0,
                      "the restricted tech's board does not hold the unassigned job")
            ctx.close()
            browser.close()

        blob = json.loads(one("select custom_fields from opportunities"
                              " where title = 'Ada Rowe'")[0])
        checks.ok(blob.get("workiz_tech") == ["NIco", "Tess Tech", "Antonio Brown"],
                  "nothing the browser did changed the stored names")
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
    shots = Path(args.shots or tempfile.mkdtemp(prefix="wztech-shots-"))
    shots.mkdir(parents=True, exist_ok=True)
    print("screenshots:", shots, flush=True)
    sys.exit(run(shots))

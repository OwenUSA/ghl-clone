"""Drive AI Agents → Overview and Activity (the agent registry) in a REAL headless browser.

    cd backend && uv run python -m tests.browser_registry [--shots DIR]

Not collected by pytest (no `test_` prefix). It reuses `tests.browser_ai_agents --serve`: a
throwaway SQLite database made with create_all, the model provider and the phone system replaced,
nothing that can reach the outside. Works on Windows and Linux (no process groups).

Checks, on screen AND in the database: the Overview lists the auto-reply under role 1 at
Automatic; lowering it to Shadow needs no ceremony and writes one log row; promoting it back
opens the dialog, refuses without a reason and PROMOTE, and writes one more row when both are
given; Activity shows both changes.
"""
from __future__ import annotations

import argparse
import contextlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

from tests.browser_ai_agents import ADMIN, BACKEND, FRONTEND, PASSWORD
from tests.browser_dialer import Checks, free_port, wait_http


def run(shots: Path) -> int:
    from playwright.sync_api import expect, sync_playwright

    work = Path(tempfile.mkdtemp(prefix="registry-check-"))
    db_path = str(work / "reg.db")
    record = work / "provider.jsonl"
    record.touch()
    api_port, vite_port = free_port(), free_port()
    procs: list[subprocess.Popen] = []
    checks = Checks()

    def q(sql: str):
        with sqlite3.connect(db_path) as c:
            return c.execute(sql).fetchall()

    try:
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "tests.browser_ai_agents", "--serve", str(api_port), db_path,
             str(record)], cwd=BACKEND))
        wait_http(f"http://127.0.0.1:{api_port}/api/health")
        env = {**os.environ, "GHL_API_PORT": str(api_port), "GHL_VITE_PORT": str(vite_port)}
        procs.append(subprocess.Popen(
            "npx vite --config e2e/vite.sipmock.config.ts", cwd=FRONTEND, env=env, shell=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        app_url = f"http://localhost:{vite_port}/"
        wait_http(app_url)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(app_url)
            page.fill('input[type="email"]', ADMIN)
            page.fill('input[type="password"]', PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_selector("text=Sign in to continue", state="detached")
            nav = page.get_by_role("button", name="AI Agents", exact=True)
            nav.first.wait_for(timeout=20000)
            nav.click()

            # ---- Overview -----------------------------------------------------------------
            expect(page.get_by_role("tab", name="Overview")).to_be_visible()
            expect(page.get_by_text("Phone answering & speed to lead")).to_be_visible()
            row = page.get_by_role("button", name="Auto-reply")
            expect(row).to_be_visible()
            checks.ok("Text customers: Automatic" in row.inner_text(),
                      "the auto-reply's texts read Automatic by default")
            checks.ok(page.get_by_text("Nothing does this job yet.").count() >= 1,
                      "a role nothing covers says so")
            page.screenshot(path=str(shots / "01-overview.png"), full_page=True)
            row.click()
            expect(page.get_by_text("Prices or money")).to_be_visible()
            page.screenshot(path=str(shots / "02-scope-card.png"), full_page=True)

            sel = page.get_by_label("Text customers level")
            sel.select_option("shadow")
            expect(page.get_by_text("is now Shadow")).to_be_visible()
            checks.ok(q("select level from agent_permissions where entry_key='autoreply'")
                      == [("shadow",)], "lowering to Shadow is saved at once")
            checks.ok(q("select count(*) from agent_registry_log")[0][0] == 1,
                      "one log row for the change")

            sel.select_option("auto")
            dlg = page.get_by_role("dialog")
            expect(dlg).to_be_visible()
            promote = dlg.get_by_role("button", name="Promote")
            checks.ok(promote.is_disabled(), "Promote is disabled until reason and PROMOTE")
            dlg.locator("#registry-promote-reason").fill("Two clean weeks in shadow")
            dlg.locator("#registry-promote-word").fill("PROMOTE")
            page.screenshot(path=str(shots / "03-promote-dialog.png"))
            promote.click()
            expect(page.get_by_text("is now Automatic")).to_be_visible()
            checks.ok(q("select level, reason from agent_permissions where entry_key='autoreply'")
                      == [("auto", "Two clean weeks in shadow")], "the promotion and its reason")

            # ---- Activity -----------------------------------------------------------------
            page.get_by_role("tab", name="Activity").click()
            expect(page.get_by_text("from Shadow to Automatic. Reason: Two clean weeks in "
                                    "shadow", exact=False)).to_be_visible()
            page.screenshot(path=str(shots / "04-activity.png"), full_page=True)
            page.set_viewport_size({"width": 400, "height": 860})
            page.get_by_role("tab", name="Overview").click()
            page.screenshot(path=str(shots / "05-overview-phone.png"), full_page=True)
            checks.ok(not errors, "no page errors (%s)" % errors[:2])
            browser.close()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        checks.ok(False, "the run completed without an exception")
    finally:
        for p in procs:
            with contextlib.suppress(Exception):
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                                   capture_output=True)
                else:
                    p.terminate()
    passed = sum(1 for ok, _ in checks.results if ok)
    print(f"\n{passed}/{len(checks.results)} registry browser checks passed")
    return 0 if passed == len(checks.results) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    args = ap.parse_args()
    out = Path(args.shots or tempfile.mkdtemp(prefix="registry-shots-"))
    out.mkdir(parents=True, exist_ok=True)
    print("screenshots:", out)
    sys.exit(run(out))

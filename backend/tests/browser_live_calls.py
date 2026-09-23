"""Drive the "AI is on a call" banner, Listen and Take over in a REAL headless browser.

    cd backend && uv run python -m tests.browser_live_calls [--keep] [--shots DIR]

Not collected by pytest (no `test_` prefix): it starts its own API and Vite dev server.
Needs node on PATH, the `capture` dependency group (Playwright) and its Chromium.

NOTHING HERE CAN REACH A PHONE SYSTEM, by construction — the same arrangement as
`tests/browser_dialer.py`, whose helpers it uses:

  * the SIP layer is FAKE (`frontend/e2e/vite.sipmock.config.ts`), so "the browser phone is
    Ready" is real UI state with no WebSocket behind it;
  * the API runs in a child process (`--serve`) in which `crmlink.live_calls` answers from a
    state file this run writes, `crmlink.live_call_action` is a recorder, and `httpx.post` /
    `httpx.get` in the link module RAISE. Every owen-main URL points at 127.0.0.1:9;
  * the database is a throwaway SQLite file in a fresh temp directory, created with
    `create_all` — never `app.seed`, never a database this did not create.

Every check asserts what is on screen AND what reached the owen-main boundary.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

from tests.browser_dialer import Checks, free_port, wait_http

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend"
ADMIN = "owner@livecall.test"
TECH = "tech@livecall.test"
PASSWORD = "livecall-check-password-1"
JANE = "+19415550101"
STRANGER = "+19415550199"
LINKEDID = "1758640000.42"


def serve(port: int, db_path: str, state: str, record: str) -> None:
    """The API, with every hop to owen-main replaced. Runs in a child process."""
    os.environ.update({
        "DATABASE_URL": "sqlite:///" + db_path,
        "AUTO_CREATE_ALL": "1",
        "OWEN_BASE_URL": "http://127.0.0.1:9",
        "OWEN_SOFTPHONE_KEY": "owen_sk_fake",
        "CRM_LINK_BASE_URL": "http://127.0.0.1:9",
        "CRM_LINK_API_KEY": "owen_sk_fake",
    })
    sys.path.insert(0, str(BACKEND))
    import uvicorn
    from app import auth, crmlink, softphone
    from app import connection_status as cs
    from app.db import SessionLocal
    from app.main import app
    from app.models import Contact, Role, User

    def forbidden(*a, **k):
        raise RuntimeError("a real HTTP request to the phone system was attempted")

    crmlink.httpx.post = forbidden
    crmlink.httpx.get = forbidden
    if hasattr(cs, "httpx"):
        cs.httpx.get = forbidden
        cs.httpx.post = forbidden

    def live_calls():
        data = json.loads(Path(state).read_text() or "{}")
        return crmlink.LinkResult(True, 200, "", {"calls": data.get("calls", [])})

    def live_call_action(linkedid, action, operator_email):
        with open(record, "a") as fh:
            fh.write(json.dumps({"linkedid": linkedid, "action": action,
                                 "email": operator_email}) + "\n")
        if json.loads(Path(state).read_text() or "{}").get("ended"):
            return crmlink.LinkResult(False, 404, "That call has already ended.")
        return crmlink.LinkResult(True, 200, "", {"ok": True, "operator": "owner-livecall.test",
                                                  "operator_channel": "op-fake"})

    crmlink.live_calls = live_calls
    crmlink.live_call_action = live_call_action

    def post_json(url, *, key, payload, timeout):
        return 200, {"ok": True, "operator": "owner-livecall.test",
                     "endpoint": "operator-owner-livecall.test",
                     "sip": {"endpoint": "operator-owner-livecall.test", "username": "fake",
                             "authorization_username": "fake", "password": "fake",
                             "domain": "fake.invalid", "wss_url": "wss://fake.invalid/ws",
                             "expires_at": int(time.time()) + 3600},
                     "ice_servers": []}

    softphone.post_json = post_json

    with SessionLocal() as db:
        db.add(User(email=ADMIN, name="Owner", role=Role.ADMIN,
                    password_hash=auth.hash_password(PASSWORD)))
        db.add(User(email=TECH, name="Tess Tech", role=Role.TECH,
                    password_hash=auth.hash_password(PASSWORD)))
        db.add(Contact(first_name="Jane", last_name="Doe", phone=JANE))
        db.commit()

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def run(shots: Path, keep: bool) -> int:
    from playwright.sync_api import expect, sync_playwright

    work = Path(tempfile.mkdtemp(prefix="livecall-check-"))
    db_path = str(work / "livecall.db")
    state = work / "state.json"
    record = work / "actions.jsonl"
    state.write_text("{}")
    record.touch()
    api_port, vite_port = free_port(), free_port()
    procs: list[subprocess.Popen] = []
    checks = Checks()

    def set_live(calls, ended=False):
        state.write_text(json.dumps({"calls": calls, "ended": ended}))

    def actions() -> list[dict]:
        return [json.loads(x) for x in record.read_text().splitlines() if x.strip()]

    started_epoch = int(time.time()) - 125
    started = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(started_epoch))
    jane_call = {"linkedid": LINKEDID, "caller_number": JANE, "dialed_number": "+19547758492",
                 "agent": "Intake", "started_at": started, "duration_s": 125, "turns": 9}

    def sign_in(page, email):
        page.goto(app_url)
        page.fill('input[type="email"]', email)
        page.fill('input[type="password"]', PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_selector("text=Sign in to continue", state="detached")

    try:
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "tests.browser_live_calls", "--serve", str(api_port),
             db_path, str(state), str(record)], cwd=BACKEND, start_new_session=True))
        wait_http(f"http://127.0.0.1:{api_port}/api/health")
        env = {**os.environ, "GHL_API_PORT": str(api_port), "GHL_VITE_PORT": str(vite_port)}
        procs.append(subprocess.Popen(
            ["npx", "vite", "--config", "e2e/vite.sipmock.config.ts"], cwd=FRONTEND, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        app_url = f"http://localhost:{vite_port}/"
        wait_http(app_url)
        print(f"API :{api_port}, Vite :{vite_port}, db {db_path}", flush=True)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=[
                "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])

            # ---- a technician: nothing drawn, nothing polled ---------------------------
            tctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                       permissions=["microphone"])
            tpage = tctx.new_page()
            polled: list[str] = []
            tpage.on("request", lambda r: polled.append(r.url)
                     if "/api/live-calls" in r.url else None)
            set_live([jane_call])
            sign_in(tpage, TECH)
            tpage.wait_for_timeout(7000)
            checks.ok(polled == [], f"a TECH's browser never asks for live calls ({polled[:2]})")
            checks.ok(tpage.get_by_text("AI is on a call").count() == 0,
                      "a TECH sees no banner while a call is live")
            tctx.close()

            # ---- an admin ---------------------------------------------------------------
            set_live([])
            ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                      permissions=["microphone"])
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            sign_in(page, ADMIN)
            page.wait_for_function("() => window.__sipMock?.log.includes('register')")
            page.wait_for_timeout(6000)
            checks.ok(page.get_by_text("AI is on a call").count() == 0,
                      "nothing live: no banner")

            set_live([jane_call])
            banner = page.get_by_role("status").filter(has_text="AI is on a call with")
            expect(banner).to_be_visible(timeout=8000)
            text = banner.inner_text()
            checks.ok("AI is on a call with Jane Doe" in text,
                      f"a known caller is named from Contacts ({text!r})")
            m = re.search(r"(\d\d):(\d\d)", text)
            shown_s = int(m.group(1)) * 60 + int(m.group(2)) if m else -1
            real_s = time.time() - started_epoch
            checks.ok(abs(shown_s - real_s) <= 3 and "Intake" in text,
                      f"it shows how long, counted from started_at ({shown_s}s vs {real_s:.0f}s),"
                      " and which agent")
            page.screenshot(path=str(shots / "01-banner.png"))
            box = banner.bounding_box()
            checks.ok(box is not None and box["y"] < 60, "it sits in the top bar")

            listen = banner.get_by_role("button", name="Listen")
            take = banner.get_by_role("button", name="Take over")
            checks.ok(listen.is_enabled() and take.is_enabled(),
                      "with the browser phone Ready, both buttons are offered")

            listen.click()
            expect(banner).to_contain_text("answer it to listen", timeout=5000)
            checks.ok(actions() == [{"linkedid": LINKEDID, "action": "listen", "email": ADMIN}],
                      "Listen relayed once, with the signed-in user's own email")

            take.click()
            confirm = page.get_by_role("alertdialog", name="Take over this call?")
            expect(confirm).to_be_visible(timeout=3000)
            checks.ok("Jane Doe" in confirm.inner_text()
                      and "cannot be handed back" in confirm.inner_text(),
                      "Take over asks first, naming the customer and that it is permanent")
            checks.ok(len(actions()) == 1, "nothing is relayed before it is confirmed")
            page.screenshot(path=str(shots / "02-confirm.png"))
            confirm.get_by_role("button", name="Cancel").click()
            page.wait_for_timeout(300)
            checks.ok(len(actions()) == 1 and confirm.count() == 0,
                      "Cancel relays nothing")
            take.click()
            confirm.get_by_role("button", name="Take over").click()
            expect(banner).to_contain_text("answer it to take the call", timeout=5000)
            checks.ok(actions()[-1] == {"linkedid": LINKEDID, "action": "takeover",
                                        "email": ADMIN},
                      "confirmed: takeover relayed once, with the user's own email")

            # ---- a call that ended between drawing and clicking ---------------------------
            set_live([jane_call], ended=True)
            listen.click()
            expect(banner).to_contain_text("That call has already ended.", timeout=5000)
            checks.ok(True, "the server's sentence is shown when the call has ended")

            # ---- phone switched off: nothing that would ring it --------------------------
            set_live([dict(jane_call, caller_number=STRANGER)])
            expect(banner).to_contain_text("(941) 555-0199", timeout=8000)
            checks.ok(True, "an unknown caller is shown as the formatted number")
            page.get_by_role("button", name="Connection status", exact=False).hover()
            page.get_by_role("button", name="Switch off").click()
            page.mouse.move(700, 500)
            page.wait_for_timeout(500)
            checks.ok(listen.is_disabled() and take.is_disabled()
                      and "Switch your browser phone on" in banner.inner_text(),
                      "phone off: both disabled, with the sentence saying why")
            page.screenshot(path=str(shots / "03-phone-off.png"))

            # ---- the call ends -----------------------------------------------------------
            set_live([])
            expect(banner).to_have_count(0, timeout=8000)
            checks.ok(True, "when the call ends the banner goes")
            checks.ok(not errors, f"no uncaught page errors ({errors[:2]})")
            browser.close()
    except Exception:  # noqa: BLE001 - any failure is one failed check, then clean up
        traceback.print_exc()
        checks.ok(False, "the run completed without an exception")
    finally:
        for p in procs:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(p.pid, signal.SIGTERM)
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
        if not keep:
            for f in work.iterdir():
                f.unlink()
            work.rmdir()

    passed = sum(1 for ok, _ in checks.results if ok)
    print(f"\n{passed}/{len(checks.results)} live-call browser checks passed")
    return 0 if passed == len(checks.results) else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        serve(int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5])
        sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep the temp database")
    ap.add_argument("--shots", default=None, help="where to write screenshots")
    args = ap.parse_args()
    out = Path(args.shots or tempfile.mkdtemp(prefix="livecall-shots-"))
    out.mkdir(parents=True, exist_ok=True)
    print("screenshots:", out)
    sys.exit(run(out, args.keep))

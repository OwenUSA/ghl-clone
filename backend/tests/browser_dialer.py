"""Drive the Conversations dialer and the in-call window in a REAL headless browser.

    cd backend && uv run python -m tests.browser_dialer [--keep] [--shots DIR]

Not collected by pytest (no `test_` prefix): it starts its own API and Vite dev server.
Needs node on PATH, the `capture` dependency group (Playwright) and its Chromium.

NOTHING HERE CAN REACH A PHONE SYSTEM, by construction rather than by care:

  * the SIP layer is FAKE — the dev server is started with `frontend/e2e/vite.sipmock.
    config.ts`, which swaps `src/lib/sipUser.ts` for `e2e/sipUserMock.ts`: no WebSocket,
    no REGISTER, no INVITE leaves the page. The check plays owen-main's part by calling
    `window.__sipMock.invite(number)`;
  * the API runs in a child process (`--serve`) in which `crmlink.place_call` is replaced
    by a recorder, `softphone.post_json` returns a fake credential blob, and both
    `httpx.post` and `httpx.get` in the link modules RAISE — a real request would fail
    the run rather than go anywhere. Every owen-main URL points at 127.0.0.1:9;
  * the database is a throwaway SQLite file in a fresh temp directory, created with
    `create_all` — never `app.seed`, never a database this did not create.

Every check asserts behaviour on screen AND on the far side of it: what the recorder saw
owen-main asked for, what the fake SIP user was told to do, and what is in the database.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend"
ADMIN = "owner@dialer.test"
PASSWORD = "dialer-check-password-1"
REFUSED = "+19415550100"
STRANGER = "+19415550199"
CALLER = "+13055550123"
JANE = "+19415550101"


# --------------------------------------------------------------------------- the API

def serve(port: int, db_path: str, record: str) -> None:
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

    def place_call(to_number, operator=None):
        with open(record, "a") as fh:
            fh.write(json.dumps({"to_number": to_number, "operator": operator}) + "\n")
        if to_number.endswith(REFUSED[-10:]):
            return crmlink.LinkResult(False, 409, "This contact is blocked in the phone system.")
        return crmlink.LinkResult(True, 200, "", {"ok": True, "operator_channel": "op-fake",
                                                  "callee_channel": "c-fake",
                                                  "linkedid": "op-fake"})

    crmlink.place_call = place_call

    def post_json(url, *, key, payload, timeout):
        return 200, {"ok": True, "operator": "owner-dialer.test",
                     "endpoint": "operator-owner-dialer.test",
                     "sip": {"endpoint": "operator-owner-dialer.test", "username": "fake",
                             "authorization_username": "fake", "password": "fake",
                             "domain": "fake.invalid", "wss_url": "wss://fake.invalid/ws",
                             "expires_at": int(time.time()) + 3600},
                     "ice_servers": []}

    softphone.post_json = post_json

    with SessionLocal() as db:
        db.add(User(email=ADMIN, name="Owner", role=Role.ADMIN,
                    password_hash=auth.hash_password(PASSWORD)))
        db.add(Contact(first_name="Jane", last_name="Doe", phone=JANE))
        db.commit()

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


# --------------------------------------------------------------------------- the run

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, timeout: float = 60) -> None:
    import urllib.request
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:  # noqa: BLE001 - not up yet, whatever the reason
            time.sleep(0.3)
    raise SystemExit("did not come up: " + url)


class Checks:
    def __init__(self):
        self.results: list[tuple[bool, str]] = []

    def ok(self, cond: bool, what: str) -> None:
        self.results.append((bool(cond), what))
        print(("  PASS  " if cond else "  FAIL  ") + what, flush=True)


def run(shots: Path, keep: bool) -> int:
    from playwright.sync_api import expect, sync_playwright

    work = Path(tempfile.mkdtemp(prefix="dialer-check-"))
    db_path = str(work / "dialer.db")
    record = work / "place_call.jsonl"
    record.touch()
    api_port, vite_port = free_port(), free_port()
    procs: list[subprocess.Popen] = []
    checks = Checks()

    def db_count(sql: str) -> int:
        with sqlite3.connect(db_path) as c:
            return c.execute(sql).fetchone()[0]

    def dialled() -> list[dict]:
        return [json.loads(x) for x in record.read_text().splitlines() if x.strip()]

    try:
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "tests.browser_dialer", "--serve", str(api_port), db_path,
             str(record)], cwd=BACKEND, start_new_session=True))
        wait_http(f"http://127.0.0.1:{api_port}/api/health")
        env = {**os.environ, "GHL_API_PORT": str(api_port), "GHL_VITE_PORT": str(vite_port)}
        procs.append(subprocess.Popen(
            ["npx", "vite", "--config", "e2e/vite.sipmock.config.ts"], cwd=FRONTEND, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        app_url = f"http://localhost:{vite_port}/"
        wait_http(app_url)
        print(f"API :{api_port} (pid {procs[0].pid}), Vite :{vite_port} (pid {procs[1].pid}),"
              f" db {db_path}", flush=True)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, args=[
                "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
            ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                      permissions=["microphone"])
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(app_url)
            page.fill('input[type="email"]', ADMIN)
            page.fill('input[type="password"]', PASSWORD)
            page.click('button[type="submit"]')
            page.wait_for_selector("text=Sign in to continue", state="detached")
            page.get_by_role("button", name="Conversations", exact=True).first.click()
            page.wait_for_function("() => window.__sipMock?.log.includes('register')")
            page.wait_for_timeout(300)
            sip_log = lambda: page.evaluate("() => window.__sipMock.log.slice()")  # noqa: E731
            invite = lambda n: page.evaluate("(n) => window.__sipMock.invite(n)", n)  # noqa: E731

            # ---- placement ----------------------------------------------------------
            call_btn = page.get_by_role("button", name="Call a number")
            checks.ok(call_btn.count() == 1, "one 'Call a number' control on Conversations")
            box = call_btn.bounding_box()
            team = page.get_by_text("Team inbox", exact=True).bounding_box()
            checks.ok(box is not None and team is not None and abs(box["y"] - team["y"]) < 20
                      and box["x"] > team["x"], "it sits in the inbox header, beside Team inbox")

            # ---- the dialer: empty, typing, keypad, backspace, paste ------------------
            call_btn.click()
            dlg = page.get_by_role("dialog", name="Call a number")
            field = dlg.locator("#dial-number")
            call = dlg.get_by_role("button", name="Call", exact=True)
            hint = dlg.locator("#dial-hint")
            checks.ok(dlg.is_visible(), "dialer opens")
            checks.ok(dlg.get_by_test_id("calling-from").inner_text() == "(954) 482-9099",
                      "Calling from (954) 482-9099, read-only text")
            checks.ok(dlg.locator("select").count() == 0, "no line picker — there is one line")
            checks.ok(call.is_disabled() and "Enter a number" in hint.inner_text(),
                      "empty: Call disabled, says to enter a number")
            page.screenshot(path=str(shots / "01-dialer-empty.png"))

            field.press_sequentially("94155501")
            checks.ok(field.input_value() == "(941) 555-01", "typing formats as you type")
            checks.ok(call.is_disabled() and "too short" in hint.inner_text(),
                      f"too short: Call disabled with the reason ({hint.inner_text()!r})")
            field.fill("")
            for k in "9415550199":
                dlg.get_by_role("button", name=f"Dial {k}").click()
            checks.ok(field.input_value() == "(941) 555-0199", "keypad appends")
            checks.ok(call.is_enabled(), "a valid number with a Ready phone enables Call")
            dlg.get_by_role("button", name="Delete last digit").click()
            checks.ok(field.input_value() == "(941) 555-019", "backspace removes the last digit")
            dlg.get_by_role("button", name="Dial *").click()
            checks.ok(call.is_disabled() and "only digits" in hint.inner_text(),
                      "a star makes Call disabled with its reason")
            page.evaluate("""(text) => {
                const el = document.querySelector('#dial-number')
                const dt = new DataTransfer(); dt.setData('text', text)
                el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true,
                  cancelable: true }))
            }""", "tel:" + REFUSED)
            checks.ok(field.input_value() == "+1 (941) 555-0100",
                      f"paste replaces the field with the number ({field.input_value()!r})")

            # ---- a server refusal places nothing -------------------------------------
            before_threads = db_count("select count(*) from number_threads")
            before_events = db_count("select count(*) from number_thread_events") + db_count(
                "select count(*) from conversation_events")
            call.click()
            expect(hint).to_contain_text("blocked in the phone system", timeout=5000)
            checks.ok(True, "the server's refusal is shown as its sentence")
            checks.ok(dialled()[-1] == {"to_number": REFUSED, "operator": ADMIN},
                      "the refused call reached the CRM's call path normalised, ringing this user")
            checks.ok(db_count("select count(*) from number_threads") == before_threads
                      and db_count("select count(*) from number_thread_events")
                      + db_count("select count(*) from conversation_events") == before_events,
                      "a refused call wrote nothing")
            checks.ok(call.is_enabled() and dlg.is_visible(), "the dialer stays open to try again")
            page.screenshot(path=str(shots / "02-dialer-refused.png"))
            invite(REFUSED)
            incoming = page.get_by_role("dialog", name="Incoming call")
            expect(incoming).to_be_visible(timeout=3000)
            checks.ok("answer" not in sip_log(),
                      "after a refusal the intent is cleared: that number ringing is INBOUND")
            incoming.get_by_role("button", name="Decline").click()
            dlg.get_by_role("button", name="Close").click()

            # ---- not Ready: switched off -----------------------------------------------
            page.get_by_role("button", name="Connection status", exact=False).hover()
            page.get_by_role("button", name="Switch off").click()
            page.mouse.move(700, 500)
            page.wait_for_timeout(300)
            call_btn.click()
            field.fill("9415550199")
            checks.ok(call.is_disabled() and hint.inner_text()
                      == "Your browser phone is switched off — switch it on to call.",
                      f"phone off: Call disabled with the sentence ({hint.inner_text()!r})")
            page.screenshot(path=str(shots / "03-dialer-phone-off.png"))
            n_before = len(dialled())
            field.press("Enter")
            page.wait_for_timeout(300)
            checks.ok(len(dialled()) == n_before, "Enter with Call disabled places nothing")
            dlg.get_by_role("button", name="Close").click()
            page.get_by_role("button", name="Connection status", exact=False).hover()
            page.get_by_role("button", name="Switch on").click()
            page.mouse.move(700, 500)
            page.wait_for_function(
                "() => window.__sipMock.log.filter((x) => x === 'register').length >= 2")
            page.wait_for_timeout(300)

            checks.ok(page.locator("#ghl-status-card").count() == 0,
                      "the status card closes after Switch on once the pointer is elsewhere")

            # ---- a call to an unknown number -------------------------------------------
            contacts_before = db_count("select count(*) from contacts")
            call_btn.click()
            checks.ok(page.locator("#ghl-status-card").count() == 0,
                      "a click elsewhere closes a status card left open by focus")
            field.fill("(941) 555-0199")
            call.click()
            expect(hint).to_contain_text("ringing this browser", timeout=5000)
            checks.ok(dialled()[-1] == {"to_number": STRANGER, "operator": ADMIN},
                      "Call hit /api/calls/dial with +19415550199, ringing this user's browser")
            checks.ok(dlg.is_visible(), "the dialer waits for this browser's leg")

            # A genuine inbound call in the window is not swallowed.
            invite(CALLER)
            expect(incoming).to_be_visible(timeout=3000)
            checks.ok("answer" not in sip_log(),
                      "a different number ringing during the window shows Incoming call")
            incoming.get_by_role("button", name="Decline").click()

            # Our leg: auto-answered, no incoming card.
            invite(STRANGER)
            win = page.get_by_role("dialog", name="Call in progress")
            expect(win).to_be_visible(timeout=3000)
            checks.ok(incoming.count() == 0, "our own leg never shows the Incoming call card")
            checks.ok(sip_log()[-1] == "answer", "our own leg was answered automatically")
            checks.ok(page.get_by_role("dialog", name="Call a number").count() == 0,
                      "the dialer closes once the call connects")
            checks.ok(win.get_by_test_id("call-title").inner_text() == "(941) 555-0199",
                      "window: the formatted number for an unknown number")
            checks.ok(win.get_by_test_id("recording").is_visible(), "window: recording indicator")
            t1 = win.get_by_test_id("call-timer").inner_text()
            page.wait_for_timeout(2200)
            t2 = win.get_by_test_id("call-timer").inner_text()
            checks.ok(t1 != t2 and t2.startswith("0:0"), f"window: the timer runs ({t1} -> {t2})")
            for word in ("Hold", "Resume", "Transfer"):
                checks.ok(win.get_by_role("button", name=word).count() == 0,
                          f"window: no {word} button (not supported)")

            mute = win.get_by_role("button", name="Mute")
            mute.click()
            checks.ok(sip_log()[-1] == "mute" and win.get_by_role("button", name="Unmute")
                      .get_attribute("aria-pressed") == "true", "Mute mutes and says so")
            win.get_by_role("button", name="Unmute").click()
            checks.ok(sip_log()[-1] == "unmute", "Unmute unmutes")

            win.get_by_role("button", name="Keypad").click()
            for k in ("1", "2", "#"):
                win.get_by_role("button", name=f"Send {k}").click()
            checks.ok(sip_log()[-3:] == ["dtmf:1", "dtmf:2", "dtmf:#"],
                      f"keypad sends DTMF down the line ({sip_log()[-3:]})")
            checks.ok(win.get_by_test_id("dtmf-entry").inner_text().strip() == "12#",
                      "keypad readout shows what was pressed")
            page.screenshot(path=str(shots / "04-in-call-keypad.png"))
            win.get_by_role("button", name="Audio").click()
            checks.ok(win.get_by_role("combobox", name="Microphone").count() == 1,
                      "Audio: a microphone choice")
            mic = win.get_by_role("combobox", name="Microphone")
            expect(mic.locator("option")).to_have_count(3, timeout=5000)
            checks.ok(True, "Audio: System default plus the two fake microphones are listed")
            spk = win.get_by_role("combobox", name="Speaker").count()
            checks.ok(spk == 1, "Audio: a speaker choice (Chromium supports setSinkId)")
            speaker = win.get_by_role("combobox", name="Speaker")
            expect(speaker.locator("option")).to_have_count(3, timeout=5000)
            speaker.select_option(label="Fake Audio Output 1")
            chosen = page.evaluate("() => localStorage.getItem('ghl.audio.speaker')")
            checks.ok(bool(chosen) and speaker.input_value() == chosen,
                      "Audio: choosing a speaker is kept for this browser")
            page.screenshot(path=str(shots / "05-in-call-audio.png"))

            win.get_by_role("button", name="Hang up").click()
            ended = page.get_by_role("dialog", name="Call ended")
            expect(ended).to_be_visible(timeout=3000)
            checks.ok(sip_log()[-1] == "hangup", "Hang up hangs up")
            checks.ok("Ended · 0:0" in ended.get_by_test_id("call-duration").inner_text(),
                      f"after: the duration ({ended.get_by_test_id('call-duration').inner_text()})")
            checks.ok(ended.get_by_role("button", name="Add as contact").count() == 1
                      and ended.get_by_role("button", name="Open conversation").count() == 0,
                      "after: an unknown number offers Add as contact, not Open conversation")
            page.screenshot(path=str(shots / "06-after-call-unknown.png"))
            checks.ok(db_count("select count(*) from contacts") == contacts_before,
                      "no contact was created by the call")
            checks.ok(db_count(f"select count(*) from number_threads where phone = '{STRANGER}'")
                      == 1, "the call is logged on the number's own thread")
            ended.get_by_role("button", name="Add as contact").click()
            form = page.get_by_text("Add Contact", exact=True)
            expect(form).to_be_visible()
            phone_value = page.evaluate("""() => [...document.querySelectorAll('input')]
                .map((i) => i.value).find((v) => v.includes('9415550199')) ?? null""")
            checks.ok(phone_value == STRANGER,
                      f"Add as contact opens the form prefilled ({phone_value})")
            page.wait_for_timeout(500)
            checks.ok(db_count("select count(*) from contacts") == contacts_before,
                      "opening the form saves nothing")
            page.mouse.click(10, 890)  # the backdrop closes the form
            page.wait_for_timeout(300)
            checks.ok(db_count("select count(*) from contacts") == contacts_before,
                      "closing the form saves nothing")

            # Single-shot: the same number ringing again is a real call now.
            invite(STRANGER)
            expect(incoming).to_be_visible(timeout=3000)
            checks.ok(sip_log()[-1] == f"invite:{STRANGER}",
                      "a second INVITE from the dialled number is not swallowed")

            # ...and answering an inbound call uses the SAME window.
            incoming.get_by_role("button", name="Answer").click()
            expect(win).to_be_visible(timeout=3000)
            checks.ok("Inbound" in win.inner_text()
                      and win.get_by_test_id("call-timer").count() == 1,
                      "an inbound call answered here uses the same in-call window")
            page.evaluate("() => window.__sipMock.remoteHangup()")
            expect(ended).to_be_visible(timeout=3000)
            ended.get_by_role("button", name="Close").click()

            # ---- a known number: named, then Open conversation --------------------------
            call_btn.click()
            field.fill("9415550101")
            call.click()
            expect(hint).to_contain_text("ringing this browser", timeout=5000)
            invite(JANE)
            expect(win).to_be_visible(timeout=3000)
            checks.ok(win.get_by_test_id("call-title").inner_text() == "Jane Doe",
                      "a contact's number shows their name")
            page.evaluate("() => window.__sipMock.remoteHangup()")
            expect(ended).to_be_visible(timeout=3000)
            checks.ok(ended.get_by_role("button", name="Open conversation").count() == 1
                      and ended.get_by_role("button", name="Add as contact").count() == 0,
                      "after: a known number offers Open conversation only")
            page.screenshot(path=str(shots / "07-after-call-contact.png"))
            ended.get_by_role("button", name="Open conversation").click()
            page.wait_for_timeout(800)
            header = page.locator("div.truncate", has_text="Jane Doe").first
            checks.ok(header.is_visible() and ended.count() == 0,
                      "Open conversation opens Jane's thread and closes the window")

            # ---- the thread header's phone icon rings this browser too -------------------
            n = len(dialled())
            page.get_by_role("button", name="Call +19415550101").click()
            page.wait_for_timeout(500)
            checks.ok(len(dialled()) == n + 1 and dialled()[-1]["operator"] == ADMIN,
                      "the thread's phone icon rings this browser when it is Ready")
            invite(JANE)
            expect(win).to_be_visible(timeout=3000)
            checks.ok(incoming.count() == 0 and win.get_by_test_id("call-title").inner_text()
                      == "Jane Doe", "...and opens the same window, not the incoming card")
            win.get_by_role("button", name="Hang up").click()
            expect(ended).to_be_visible(timeout=3000)
            ended.get_by_role("button", name="Close").click()

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
    print(f"\n{passed}/{len(checks.results)} dialer browser checks passed")
    return 0 if passed == len(checks.results) else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        serve(int(sys.argv[2]), sys.argv[3], sys.argv[4])
        sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep the temp database")
    ap.add_argument("--shots", default=None, help="where to write screenshots")
    args = ap.parse_args()
    out = Path(args.shots or tempfile.mkdtemp(prefix="dialer-shots-"))
    out.mkdir(parents=True, exist_ok=True)
    print("screenshots:", out)
    sys.exit(run(out, args.keep))

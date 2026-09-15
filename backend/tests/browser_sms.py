"""Drive "New message", the composer and Settings → Automations in a REAL headless browser.

    cd backend && uv run python -m tests.browser_sms [--keep] [--shots DIR]

Not collected by pytest (no `test_` prefix): it starts its own API and Vite dev server.
Needs node on PATH, the `capture` dependency group (Playwright) and its Chromium.

NOTHING HERE CAN SEND A TEXT, by construction rather than by care:

  * the API runs in a child process (`--serve`) in which `crmlink.send_sms` is replaced by
    a recorder that answers the way owen-main does (queued / opted out / unreachable), and
    `httpx.post` and `httpx.get` in the link module RAISE — a real request fails the run
    rather than going anywhere. The link's URL is 127.0.0.1:9;
  * the SIP layer is the dialer check's fake (`frontend/e2e/vite.sipmock.config.ts`);
  * the database is a throwaway SQLite file in a fresh temp directory, created with
    `create_all` — never `app.seed`, never a database this did not create.

Every check asserts what is on screen AND what the recorder saw AND what is in the database.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import traceback
import urllib.request
from pathlib import Path

from tests.browser_dialer import BACKEND, FRONTEND, Checks, free_port, wait_http

ADMIN = "owner@sms.test"
TECH = "tess@sms.test"
PASSWORD = "sms-check-password-1"
STRANGER = "+19415550199"
JANE = "+19415550101"
BOB = "+19415550150"          # the restricted technician's own customer
OPTED_OUT = "+19415550166"
UNREACHABLE = "+19415550177"


# --------------------------------------------------------------------------- the API

def serve(port: int, db_path: str, record: str, token_file: str) -> None:
    """The API, with owen-main replaced by a recorder. Runs in a child process."""
    os.environ.update({
        "DATABASE_URL": "sqlite:///" + db_path,
        "AUTO_CREATE_ALL": "1",
        "CRM_LINK_BASE_URL": "http://127.0.0.1:9",
        "CRM_LINK_API_KEY": "owen_sk_fake",
        "CRM_LINK_FROM_NUMBER": "+19544829099",
    })
    for key in ("OWEN_BASE_URL", "OWEN_SOFTPHONE_KEY"):
        os.environ.pop(key, None)
    sys.path.insert(0, str(BACKEND))
    import uvicorn
    from app import auth, crmlink
    from app.db import SessionLocal
    from app.main import app
    from app.models import Contact, Opportunity, Pipeline, Role, Stage, User

    def forbidden(*a, **k):
        raise RuntimeError("a real HTTP request to the phone system was attempted")

    crmlink.httpx.post = forbidden
    crmlink.httpx.get = forbidden
    sent = {"n": 0}

    def send_sms(to_number, body):
        sent["n"] += 1
        with open(record, "a") as fh:
            fh.write(json.dumps({"to_number": to_number, "body": body,
                                 "from_number": crmlink.current().from_number}) + "\n")
        if to_number == OPTED_OUT:
            # owen-main's own words, through the CRM's real translation.
            return crmlink.LinkResult(False, 409, crmlink._human("this contact has opted out of SMS"))
        if to_number == UNREACHABLE:
            return crmlink.LinkResult(False, 0, "could not reach the phone system")
        return crmlink.LinkResult(True, 200, "", {"ok": True, "status": "queued",
                                                  "message_id": "m-%d" % sent["n"]})

    crmlink.send_sms = send_sms

    with SessionLocal() as db:
        admin = User(email=ADMIN, name="Owner", role=Role.ADMIN,
                     password_hash=auth.hash_password(PASSWORD))
        tech = User(email=TECH, name="Tess", role=Role.TECH, only_assigned_data=True,
                    password_hash=auth.hash_password(PASSWORD))
        db.add_all([admin, tech])
        db.flush()
        jane = Contact(first_name="Jane", last_name="Doe", phone=JANE)
        bob = Contact(first_name="Bob", last_name="Roof", phone=BOB)
        db.add_all([jane, bob])
        p = Pipeline(name="Main")
        db.add(p)
        db.flush()
        s = Stage(pipeline_id=p.id, name="New Lead", position=0)
        db.add(s)
        db.flush()
        db.add(Opportunity(title="Bob roof", contact_id=bob.id, pipeline_id=p.id,
                           stage_id=s.id, owner_id=tech.id))
        plain, tok = auth.mint_api_token(admin, name="feed", scopes="events:write")
        db.add(tok)
        db.commit()
        Path(token_file).write_text(plain)

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


# --------------------------------------------------------------------------- the run

def run(shots: Path, keep: bool) -> int:
    from playwright.sync_api import expect, sync_playwright

    work = Path(tempfile.mkdtemp(prefix="sms-check-"))
    db_path = str(work / "sms.db")
    record = work / "send_sms.jsonl"
    record.touch()
    token_file = work / "feed.token"
    api_port, vite_port = free_port(), free_port()
    procs: list[subprocess.Popen] = []
    checks = Checks()

    def db_count(sql: str) -> int:
        with sqlite3.connect(db_path) as c:
            return c.execute(sql).fetchone()[0]

    def texts() -> list[dict]:
        return [json.loads(x) for x in record.read_text().splitlines() if x.strip()]

    def feed(path: str, body: dict) -> dict:
        req = urllib.request.Request(
            f"http://127.0.0.1:{api_port}{path}", data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + token_file.read_text(),
                     "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    try:
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "tests.browser_sms", "--serve", str(api_port), db_path,
             str(record), str(token_file)], cwd=BACKEND, start_new_session=True))
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
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))

            def sign_in(p, email):
                p.goto(app_url)
                p.fill('input[type="email"]', email)
                p.fill('input[type="password"]', PASSWORD)
                p.click('button[type="submit"]')
                p.wait_for_selector("text=Sign in to continue", state="detached")
                p.get_by_role("button", name="Conversations", exact=True).first.click()
                p.wait_for_timeout(500)

            sign_in(page, ADMIN)
            contacts0 = db_count("select count(*) from contacts")

            # ---- placement ----------------------------------------------------------
            new_btn = page.get_by_role("button", name="New message", exact=True)
            call_btn = page.get_by_role("button", name="Call a number")
            checks.ok(new_btn.count() == 1, "one 'New message' control on Conversations")
            nb, cb = new_btn.bounding_box(), call_btn.bounding_box()
            checks.ok(nb and cb and abs(nb["y"] - cb["y"]) < 4 and 0 < cb["x"] - nb["x"] < 60,
                      "it sits immediately left of 'Call a number' in the inbox header")
            page.screenshot(path=str(shots / "01-inbox-header.png"))

            # ---- the dialog: empty, typing, paste, segments ---------------------------
            new_btn.click()
            dlg = page.get_by_role("dialog", name="New message")
            field = dlg.locator("#new-message-number")
            body = dlg.locator("#new-message-body")
            send = dlg.get_by_role("button", name="Send", exact=True)
            hint = dlg.locator("#new-message-hint")
            checks.ok(dlg.is_visible(), "New message opens")
            checks.ok(dlg.get_by_test_id("sending-from").inner_text() == "(954) 482-9099",
                      "Sending from (954) 482-9099, read-only text")
            checks.ok(dlg.locator("select").count() == 0 and "Quo" not in dlg.inner_text(),
                      "no sender picker, and Quo is never offered")
            checks.ok(send.is_disabled() and "Enter a number to text" in hint.inner_text(),
                      "empty: Send disabled, says to enter a number")
            field.press_sequentially("94155501")
            checks.ok(field.input_value() == "(941) 555-01", "typing formats as you type")
            body.fill("Hello")
            checks.ok(send.is_disabled() and "too short" in hint.inner_text(),
                      f"too short: Send disabled with the reason ({hint.inner_text()!r})")
            n_before = len(texts())
            send.click(force=True)
            page.wait_for_timeout(300)
            checks.ok(len(texts()) == n_before, "a disabled Send makes no request")
            page.screenshot(path=str(shots / "02-invalid-number.png"))
            page.evaluate("""(text) => {
                const el = document.querySelector('#new-message-number')
                const dt = new DataTransfer(); dt.setData('text', text)
                el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true,
                  cancelable: true }))
            }""", "tel:+1 941-555-0199")
            checks.ok(field.input_value() == "+1 (941) 555-0199",
                      f"paste replaces the field with the number ({field.input_value()!r})")
            body.fill("Hi, this is Dream Team Roofing about your roof.")
            seg = dlg.get_by_test_id("segment-count")
            checks.ok(seg.inner_text() == "47 characters · 1 segment",
                      f"character and segment count ({seg.inner_text()!r})")
            body.fill("x" * 150 + " 👍")
            checks.ok("3 segments" in seg.inner_text() and "67 per segment" in seg.inner_text(),
                      f"an emoji switches to 67/70-unit segments ({seg.inner_text()!r})")
            body.fill("Hi, this is Dream Team Roofing about your roof.")
            checks.ok(send.is_enabled(), "a valid number and a message enable Send")
            page.screenshot(path=str(shots / "03-new-message-ready.png"))

            # ---- send to an unknown number: a number-only thread, no contact ------------
            send.click()
            expect(dlg).to_be_hidden(timeout=5000)
            checks.ok(texts()[-1] == {"to_number": STRANGER, "from_number": "+19544829099",
                                      "body": "Hi, this is Dream Team Roofing about your roof."},
                      "the transport got ONE text, to the normalised number, from the DID")
            checks.ok(len(texts()) == n_before + 1, "exactly one transport call")
            checks.ok(db_count("select count(*) from contacts") == contacts0
                      and db_count("select count(*) from number_threads") == 1,
                      "a number-only thread was made and NO contact")
            header = page.locator("div.truncate", has_text="(941) 555-0199").first
            expect(header).to_be_visible(timeout=5000)
            checks.ok(True, "after Send the page opens that number's thread")
            bubble = page.get_by_text("Hi, this is Dream Team Roofing about your roof.",
                                      exact=True)
            expect(bubble).to_be_visible(timeout=5000)
            status = page.get_by_test_id("delivery-status").last
            checks.ok(status.get_attribute("data-status") == "QUEUED"
                      and "queued" in status.inner_text(), "the bubble says queued")
            row = page.get_by_text("Not a contact").first
            checks.ok(row.is_visible(), "the thread is in the inbox as a number, not a contact")
            page.screenshot(path=str(shots / "04-sent-number-thread.png"))

            # ---- the delivery receipt advances the bubble -------------------------------
            feed("/api/events/delivery", {"provider_ref": "m-1", "status": "sent"})
            feed("/api/events/delivery", {"provider_ref": "m-1", "status": "delivered"})
            expect(page.locator('[data-testid="delivery-status"][data-status="DELIVERED"]')
                   ).to_have_count(1, timeout=15000)
            checks.ok("delivered" in page.get_by_test_id("delivery-status").last.inner_text(),
                      "queued → delivered on the bubble, without a reload")
            page.screenshot(path=str(shots / "05-delivered.png"))

            # ---- a second New message reuses the thread ------------------------------
            new_btn.click()
            dlg.locator("#new-message-number").fill("941-555-0199")
            dlg.locator("#new-message-body").fill("Following up on the estimate")
            dlg.get_by_role("button", name="Send", exact=True).click()
            expect(dlg).to_be_hidden(timeout=5000)
            expect(page.get_by_text("Following up on the estimate", exact=True)
                   ).to_be_visible(timeout=5000)
            checks.ok(db_count("select count(*) from number_threads") == 1
                      and db_count("select count(*) from number_thread_events") == 2,
                      "a second text to the same number lands on the same thread")

            # ---- the composer on the number thread -------------------------------------
            composer = page.get_by_placeholder("Type a message")
            composer.fill("Are you home Tuesday?")
            composer.press("Enter")
            expect(page.get_by_text("Are you home Tuesday?", exact=True)).to_be_visible(timeout=5000)
            checks.ok(texts()[-1]["to_number"] == STRANGER and len(texts()) == n_before + 3,
                      "the composer on a number thread sends through the same transport")

            # ---- a contact's number opens the contact's conversation -----------------
            new_btn.click()
            dlg.locator("#new-message-number").fill("(941) 555-0101")
            dlg.locator("#new-message-body").fill("We can be there Tuesday")
            dlg.get_by_role("button", name="Send", exact=True).click()
            expect(dlg).to_be_hidden(timeout=5000)
            expect(page.locator("div.truncate", has_text="Jane Doe").first).to_be_visible(timeout=5000)
            checks.ok(texts()[-1]["to_number"] == JANE
                      and db_count("select count(*) from contacts") == contacts0
                      and db_count("select count(*) from number_threads") == 1,
                      "a contact's number goes on Jane's conversation, nothing new created")
            page.screenshot(path=str(shots / "06-contact-thread.png"))

            # ---- REFUSED: owen-main's sentence under the bubble, no retry ---------------
            new_btn.click()
            dlg.locator("#new-message-number").fill("9415550166")
            dlg.locator("#new-message-body").fill("Promo you did not ask for")
            dlg.get_by_role("button", name="Send", exact=True).click()
            expect(dlg).to_be_hidden(timeout=5000)
            expect(page.get_by_text("Promo you did not ask for", exact=True)).to_be_visible(timeout=5000)
            detail = page.get_by_test_id("delivery-detail").last
            checks.ok(page.get_by_test_id("delivery-status").last.get_attribute("data-status")
                      == "REFUSED" and "opted out" in detail.inner_text()
                      and "STOP" in detail.inner_text(),
                      f"REFUSED shows owen-main's sentence ({detail.inner_text()!r})")
            checks.ok(detail.get_by_role("button", name="Retry").count() == 0,
                      "a refusal offers no Retry")
            page.screenshot(path=str(shots / "07-refused-opted-out.png"))

            # ---- FAILED: distinct, and Retry sends it again ----------------------------
            new_btn.click()
            dlg.locator("#new-message-number").fill("9415550177")
            dlg.locator("#new-message-body").fill("Try me again")
            dlg.get_by_role("button", name="Send", exact=True).click()
            expect(dlg).to_be_hidden(timeout=5000)
            expect(page.get_by_text("Try me again", exact=True).first).to_be_visible(timeout=5000)
            status = page.get_by_test_id("delivery-status").last
            detail = page.get_by_test_id("delivery-detail").last
            checks.ok(status.get_attribute("data-status") == "FAILED"
                      and "failed" in status.inner_text()
                      and "retry" in detail.inner_text(),
                      f"FAILED is distinct and says it can be retried ({detail.inner_text()!r})")
            n = len(texts())
            page.screenshot(path=str(shots / "08-failed-retry.png"))
            detail.get_by_role("button", name="Retry").click()
            expect(page.get_by_text("Try me again", exact=True)).to_have_count(2, timeout=5000)
            checks.ok(len(texts()) == n + 1 and texts()[-1]["to_number"] == UNREACHABLE,
                      "Retry sends the same text once more, as a new message")

            # ---- inbound from an unknown number: no contact, unread, MMS note -----------
            feed("/api/events", {"type": "SMS", "direction": "INBOUND", "from_number": "+13055550123",
                                 "body": "Here is the leak [2 attachments — view in OWEN]",
                                 "provider_ref": "in-1", "source_system": "BulkVS",
                                 "source_number": "+19544829099"})
            unread_row = page.get_by_text("(305) 555-0123", exact=False).first
            expect(unread_row).to_be_visible(timeout=15000)
            checks.ok(db_count("select count(*) from contacts") == contacts0
                      and db_count("select unread_count from number_threads where phone_key = "
                                   "'3055550123'") == 1,
                      "an inbound text from an unknown number: no contact, unread 1")
            row = page.locator("div.cursor-pointer", has_text="+13055550123").first
            checks.ok(row.locator("span", has_text="1").count() >= 1
                      and "Not a contact" in row.inner_text(),
                      "its row shows an unread badge and 'Not a contact'")
            page.screenshot(path=str(shots / "09-inbound-unread.png"))
            page.locator("div.cursor-pointer", has_text="+13055550123").first.click()
            att = page.get_by_test_id("mms-attachments")
            expect(att).to_be_visible(timeout=5000)
            checks.ok("2 attachments" in att.inner_text()
                      and page.get_by_text("Here is the leak", exact=True).is_visible(),
                      "an MMS shows its words and its attachment count (the event carries no URLs)")
            page.wait_for_timeout(1500)
            checks.ok(db_count("select unread_count from number_threads where phone_key = "
                               "'3055550123'") == 0, "opening it marks it read")
            page.screenshot(path=str(shots / "10-mms.png"))

            # ---- Settings → Automations ----------------------------------------------
            page.get_by_role("button", name="Settings", exact=True).first.click()
            page.get_by_role("tab", name="Automations").click()
            rules = page.get_by_role("listitem")
            expect(rules).to_have_count(4, timeout=5000)
            off = page.locator('[data-enabled="false"]')
            checks.ok(off.count() == 3 and all(
                "only texts a person sends" in off.nth(i).inner_text()
                or "Nothing texts anyone back" in off.nth(i).inner_text() for i in range(3)),
                "three customer-texting rules show Off with the reason")
            checks.ok(page.locator('[role="list"] input, [role="list"] button, [role="switch"]'
                                   ).count() == 0, "no switches that silently do nothing")
            page.screenshot(path=str(shots / "11-settings-automations.png"))

            checks.ok(not errors, f"no uncaught page errors ({errors[:2]})")

            # ---- a restricted technician -----------------------------------------------
            tctx = browser.new_context(viewport={"width": 1440, "height": 900})
            tpage = tctx.new_page()
            terrors: list[str] = []
            tpage.on("pageerror", lambda e: terrors.append(str(e)))
            sign_in(tpage, TECH)
            tpage.get_by_role("button", name="New message", exact=True).click()
            tdlg = tpage.get_by_role("dialog", name="New message")
            checks.ok("Only assigned data" in tdlg.locator("#new-message-hint").inner_text(),
                      "a restricted technician is told the rule before typing")
            before = (len(texts()), db_count("select count(*) from number_threads"),
                      db_count("select count(*) from number_thread_events"))
            tdlg.locator("#new-message-number").fill("(941) 555-0199")
            tdlg.locator("#new-message-body").fill("hi")
            tdlg.get_by_role("button", name="Send", exact=True).click()
            expect(tdlg.locator("#new-message-hint")).to_have_attribute("data-tone", "red",
                                                                        timeout=5000)
            checks.ok(tdlg.is_visible() and "not one of them" in tdlg.inner_text()
                      and (len(texts()), db_count("select count(*) from number_threads"),
                           db_count("select count(*) from number_thread_events")) == before,
                      "an unknown number is refused in words, nothing sent or written")
            tpage.screenshot(path=str(shots / "12-tech-refused.png"))
            tdlg.locator("#new-message-number").fill("(941) 555-0150")
            tdlg.get_by_role("button", name="Send", exact=True).click()
            expect(tdlg).to_be_hidden(timeout=5000)
            checks.ok(texts()[-1]["to_number"] == BOB, "her own customer is texted")
            checks.ok(not terrors, f"no uncaught page errors for the technician ({terrors[:2]})")
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
    print(f"\n{passed}/{len(checks.results)} SMS browser checks passed")
    return 0 if passed == len(checks.results) else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        serve(int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5])
        sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep the temp database")
    ap.add_argument("--shots", default=None, help="where to write screenshots")
    args = ap.parse_args()
    out = Path(args.shots or tempfile.mkdtemp(prefix="sms-shots-"))
    out.mkdir(parents=True, exist_ok=True)
    print("screenshots:", out)
    sys.exit(run(out, args.keep))

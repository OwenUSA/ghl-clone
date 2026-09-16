"""Drive picture messages in a REAL headless browser (2026-09-16).

    cd backend && uv run python -m tests.browser_pictures [--keep] [--shots DIR]

Not collected by pytest (no `test_` prefix): it starts its own API and Vite dev server.
Needs node on PATH, the `capture` dependency group (Playwright) and its Chromium.

NOTHING HERE CAN SEND A TEXT OR REACH A PHONE SYSTEM, by construction rather than by care:

  * the API runs in a child process (`--serve`) in which `crmlink`'s two network calls —
    `httpx.post` and `httpx.get` — are replaced by a recorder. A real request RAISES and
    fails the run rather than going anywhere. The link's URL is 127.0.0.1:9;
  * the database is a throwaway SQLite file in a fresh temp directory, created with
    `create_all` — never `app.seed`, never a database this did not create;
  * `MEDIA_ROOT` is a directory inside that same temp directory, so no real picture is
    read, written or deleted.

The reason this exists rather than more `TestClient` assertions: `test_message_pictures.py`
proves the SERVER. What it cannot prove is that an `<img src="/api/attachments/12">` renders
— that the session cookie actually reaches a bytes route, that the thumbnail is the picture
and not a broken-image glyph, that the viewer pages, and that a photo dropped on the
composer arrives at the transport. Those are the parts that would be discovered by the owner
rather than by a test.
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
import zlib
from pathlib import Path

from tests.browser_dialer import BACKEND, FRONTEND, Checks, free_port, wait_http

ADMIN = "owner@pics.test"
TECH = "tess@pics.test"
PASSWORD = "pictures-check-password-1"
JANE = "+19415550101"
BOB = "+19415550150"          # the restricted technician's own customer


def png(tag: bytes = b"a", width: int = 48, height: int = 48) -> bytes:
    """A real PNG. The server decides what a file is by sniffing it, and a browser will
    not render a fake one, so both halves of this check need the genuine article."""
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (len(payload).to_bytes(4, "big") + kind + payload
                + zlib.crc32(kind + payload).to_bytes(4, "big"))
    raw = b"".join(b"\x00" + (tag * 3) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", width.to_bytes(4, "big") + height.to_bytes(4, "big")
                    + bytes([8, 2, 0, 0, 0]))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


NOT_AN_IMAGE = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n"


# --------------------------------------------------------------------------- the API

def serve(port: int, db_path: str, record: str, token_file: str, media_root: str) -> None:
    """The API, with owen-main replaced by a recorder. Runs in a child process."""
    os.environ.update({
        "DATABASE_URL": "sqlite:///" + db_path,
        "AUTO_CREATE_ALL": "1",
        "CRM_LINK_BASE_URL": "http://127.0.0.1:9",
        "CRM_LINK_API_KEY": "owen_sk_fake",
        "CRM_LINK_FROM_NUMBER": "+19544829099",
        "MEDIA_ROOT": media_root,
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

    # The pictures the fake owen-main will hand back for an inbound MMS, by locator.
    inbound = {"m-1/0": png(b"a"), "m-3/0": png(b"a"), "m-3/1": png(b"b"),
               "m-3/2": png(b"c")}
    uploads = {"n": 0}

    def note(kind: str, payload: dict) -> None:
        with open(record, "a") as fh:
            fh.write(json.dumps({"kind": kind, **payload}) + "\n")

    def send_sms(to_number, body, media_ids=None):
        note("send", {"to_number": to_number, "body": body,
                      "media_ids": list(media_ids or [])})
        return crmlink.LinkResult(True, 200, "", {"ok": True, "status": "queued",
                                                  "message_id": "out-1"})

    def upload_media(data, filename, content_type):
        uploads["n"] += 1
        note("upload", {"filename": filename, "content_type": content_type,
                        "byte_size": len(data)})
        return crmlink.LinkResult(True, 201, "",
                                  {"ok": True,
                                   "media_id": "owen-media-%d" % uploads["n"]})

    def fetch_message_media(message_id, index):
        key = "%s/%d" % (message_id, index)
        note("fetch", {"key": key})
        data = inbound.get(key)
        if data is None:
            # "gone" is how the thread's Retry gets something to retry.
            return crmlink.LinkResult(False, 502, "could not reach the phone system")
        return data, "image/png"

    crmlink.send_sms = send_sms
    crmlink.upload_media = upload_media
    crmlink.fetch_message_media = fetch_message_media

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

    work = Path(tempfile.mkdtemp(prefix="pictures-check-"))
    db_path = str(work / "pictures.db")
    media_root = work / "media"
    record = work / "owen.jsonl"
    record.touch()
    token_file = work / "feed.token"
    photo = work / "roof.png"
    photo.write_bytes(png(b"r"))
    second = work / "gutter.png"
    second.write_bytes(png(b"g"))
    quote = work / "quote.pdf"
    quote.write_bytes(NOT_AN_IMAGE)
    api_port, vite_port = free_port(), free_port()
    procs: list[subprocess.Popen] = []
    checks = Checks()

    def db_count(sql: str) -> int:
        with sqlite3.connect(db_path) as c:
            return c.execute(sql).fetchone()[0]

    def seen(kind: str) -> list[dict]:
        return [json.loads(x) for x in record.read_text().splitlines()
                if x.strip() and json.loads(x)["kind"] == kind]

    def feed(path: str, body: dict) -> dict:
        req = urllib.request.Request(
            f"http://127.0.0.1:{api_port}{path}", data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + token_file.read_text(),
                     "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def mms(provider_ref: str, body: str, num_media: int, number: str = JANE) -> dict:
        return feed("/api/events", {
            "type": "SMS", "direction": "INBOUND", "from_number": number, "body": body,
            "provider_ref": provider_ref, "num_media": num_media,
            "source_system": "BulkVS", "source_number": "+19544829099"})

    def drain() -> None:
        """Run the queue the way the worker does, in this process, against the same
        database the API is using."""
        env = {**os.environ, "DATABASE_URL": "sqlite:///" + db_path,
               "AUTO_CREATE_ALL": "1", "MEDIA_ROOT": str(media_root),
               "CRM_LINK_BASE_URL": "", "CRM_LINK_API_KEY": ""}
        subprocess.run([sys.executable, "-m", "tests.browser_pictures", "--drain",
                        db_path, str(media_root), str(record)],
                       cwd=BACKEND, env=env, check=True)

    try:
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "tests.browser_pictures", "--serve", str(api_port),
             db_path, str(record), str(token_file), str(media_root)],
            cwd=BACKEND, start_new_session=True))
        wait_http(f"http://127.0.0.1:{api_port}/api/health")
        env = {**os.environ, "GHL_API_PORT": str(api_port), "GHL_VITE_PORT": str(vite_port)}
        procs.append(subprocess.Popen(
            ["npx", "vite", "--config", "e2e/vite.sipmock.config.ts"], cwd=FRONTEND, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        app_url = f"http://localhost:{vite_port}/"
        wait_http(app_url)
        print(f"API :{api_port}, Vite :{vite_port}, db {db_path}, media {media_root}",
              flush=True)

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

            def conversations(p):
                """Back to the inbox. A reload lands on the Dashboard — the app's home
                since 2026-09-13 — so every reload has to navigate, not assume."""
                p.get_by_role("button", name="Conversations", exact=True).first.click()
                p.wait_for_timeout(500)

            def open_jane(p):
                p.locator("div.cursor-pointer", has_text="Jane").first.click()
                p.wait_for_timeout(400)

            def rendered(locator) -> bool:
                """Did the browser actually DECODE the bytes? `naturalWidth` is 0 for a
                broken image, which is exactly what a 404, a 401 or an HTML error page
                served as an image looks like on screen."""
                return locator.evaluate("el => el.complete && el.naturalWidth > 0")

            sign_in(page, ADMIN)
            contacts0 = db_count("select count(*) from contacts")

            # ---- one inbound picture -------------------------------------------------
            mms("m-1", "Here is the leak", 1)
            open_jane(page)
            expect(page.get_by_test_id("message-pictures").last).to_be_visible(timeout=8000)
            checks.ok(page.get_by_test_id("picture-unavailable").count() == 1,
                      "before the worker runs the picture shows as on its way")
            checks.ok(page.get_by_text("Here is the leak", exact=True).is_visible(),
                      "and the customer's words are on the thread already")
            page.screenshot(path=str(shots / "01-pending.png"))

            drain()
            page.reload()
            conversations(page)
            open_jane(page)
            thumb = page.get_by_test_id("picture-thumb").last.locator("img")
            expect(thumb).to_be_visible(timeout=8000)
            checks.ok(rendered(thumb), "the thumbnail is a real decoded picture")
            checks.ok(len(seen("fetch")) == 1, "the bytes were fetched exactly once")
            checks.ok(db_count("select count(*) from message_attachments") == 1,
                      "one attachment row")
            page.screenshot(path=str(shots / "02-thumbnail.png"))

            # ---- the viewer ------------------------------------------------------------
            mms("m-3", "three of the roof", 3)
            drain()
            page.reload()
            conversations(page)
            open_jane(page)
            group = page.get_by_test_id("message-pictures").last
            expect(group).to_have_attribute("data-count", "3", timeout=8000)
            group.get_by_test_id("picture-thumb").first.click()
            viewer = page.get_by_test_id("picture-viewer")
            expect(viewer).to_be_visible(timeout=5000)
            big = page.get_by_test_id("viewer-image")
            first_id = big.get_attribute("data-attachment")
            checks.ok(rendered(big), "the large view renders the picture")
            checks.ok(page.get_by_test_id("viewer-counter").inner_text() == "1 of 3",
                      "the viewer says which picture this is")
            checks.ok("Jane" in page.get_by_test_id("viewer-who").inner_text()
                      and bool(page.get_by_test_id("viewer-when").inner_text().strip()),
                      "and who sent it, and when")
            page.screenshot(path=str(shots / "03-viewer.png"))

            page.get_by_test_id("viewer-next").click()
            expect(page.get_by_test_id("viewer-counter")).to_have_text("2 of 3", timeout=3000)
            checks.ok(big.get_attribute("data-attachment") != first_id,
                      "next shows a different picture")
            page.keyboard.press("ArrowRight")
            expect(page.get_by_test_id("viewer-counter")).to_have_text("3 of 3", timeout=3000)
            checks.ok(page.get_by_test_id("viewer-next").is_disabled(),
                      "there is nowhere past the last one, and it says so")
            page.keyboard.press("ArrowLeft")
            expect(page.get_by_test_id("viewer-counter")).to_have_text("2 of 3", timeout=3000)
            page.keyboard.press("Escape")
            expect(viewer).to_be_hidden(timeout=3000)
            checks.ok(True, "previous pages back, and Escape closes")

            # ---- a fetch that failed, and Retry ----------------------------------------
            mms("m-gone", "storm damage", 1)
            drain()
            page.reload()
            conversations(page)
            open_jane(page)
            bad = page.get_by_test_id("picture-unavailable").last
            expect(bad).to_be_visible(timeout=8000)
            checks.ok("unavailable" in bad.inner_text().lower()
                      and page.get_by_text("storm damage", exact=True).is_visible(),
                      "a picture that could not be fetched says so and keeps the words")
            checks.ok(bad.get_by_test_id("picture-retry").count() == 1,
                      "and offers a Retry")
            page.screenshot(path=str(shots / "04-unavailable.png"))
            fetches = len(seen("fetch"))
            bad.get_by_test_id("picture-retry").click()
            page.wait_for_timeout(1500)
            checks.ok(len(seen("fetch")) == fetches + 1,
                      "Retry asks the phone system again")

            # ---- attach, preview, remove, send -----------------------------------------
            page.reload()
            conversations(page)
            open_jane(page)
            page.get_by_test_id("attach-input").set_input_files([str(photo), str(second)])
            expect(page.get_by_test_id("picture-preview").first).to_be_visible(timeout=8000)
            previews = page.get_by_test_id("picture-preview")
            checks.ok(previews.count() == 2, "two pictures are attached and previewed")
            checks.ok(rendered(previews.first),
                      "the preview is the picture the server holds, decoded")
            page.screenshot(path=str(shots / "05-composer-attached.png"))

            drafts = db_count("select count(*) from message_attachments where status='DRAFT'")
            checks.ok(drafts == 2, "each is a draft on the server, not a local blob")
            page.get_by_test_id("picture-remove").last.click()
            expect(page.get_by_test_id("picture-preview")).to_have_count(1, timeout=5000)
            checks.ok(db_count("select count(*) from message_attachments where "
                               "status='DRAFT'") == 1,
                      "removing one takes it off the composer AND off the server")

            sends = len(seen("send"))
            page.fill('input[placeholder="Type a message"]', "here is the flashing")
            page.get_by_role("button", name="Send", exact=True).click()
            expect(page.get_by_text("here is the flashing", exact=True)).to_be_visible(
                timeout=8000)
            checks.ok(len(seen("send")) == sends + 1, "the text was handed to the transport")
            last = seen("send")[-1]
            checks.ok(last["media_ids"] and len(last["media_ids"]) == 1,
                      f"with exactly the one attached picture ({last['media_ids']})")
            checks.ok(len(seen("upload")) >= 1
                      and seen("upload")[-1]["content_type"] == "image/png",
                      "the bytes went to the phone system first, as a picture")
            checks.ok(page.get_by_test_id("picture-preview").count() == 0,
                      "and the composer is empty again")
            sent_group = page.get_by_test_id("message-pictures").last
            checks.ok(rendered(sent_group.get_by_test_id("picture-thumb").first
                               .locator("img")),
                      "the sent picture is on the thread")
            page.screenshot(path=str(shots / "06-sent.png"))

            # ---- drag and paste ---------------------------------------------------------
            # The owner asked for "drag, paste or pick". The picker is exercised above;
            # these two are dispatched as real DOM events carrying a real File, because
            # they are the two nobody would notice were broken until a photo vanished into
            # a composer that ignored it.
            def drop_a_picture(selector: str, kind: str) -> None:
                page.evaluate(
                    """([sel, kind]) => {
                        const bytes = new Uint8Array([137,80,78,71,13,10,26,10]);
                        const el = document.querySelector(sel);
                        const dt = new DataTransfer();
                        dt.items.add(new File([bytes], 'dropped.png', {type: 'image/png'}));
                        const ev = kind === 'paste'
                          ? new ClipboardEvent('paste', {clipboardData: dt, bubbles: true,
                                                         cancelable: true})
                          : new DragEvent('drop', {dataTransfer: dt, bubbles: true,
                                                   cancelable: true});
                        el.dispatchEvent(ev);
                    }""", [selector, kind])

            for kind in ("paste", "drop"):
                drop_a_picture('input[placeholder="Type a message"]', kind)
                problem = page.get_by_test_id("picture-problem")
                # Eight bytes is a PNG signature and nothing else, so the server refuses it
                # by its bytes — which proves the file REACHED the server, which is the
                # thing this check is about.
                expect(problem).to_be_visible(timeout=8000)
                checks.ok("picture" in problem.inner_text().lower(),
                          f"a picture arriving by {kind} reaches the server")

            # ---- a picture with no words ----------------------------------------------
            page.get_by_test_id("attach-input").set_input_files([str(photo)])
            expect(page.get_by_test_id("picture-preview").first).to_be_visible(timeout=8000)
            sends = len(seen("send"))
            page.get_by_role("button", name="Send", exact=True).click()
            page.wait_for_timeout(2000)
            checks.ok(len(seen("send")) == sends + 1 and seen("send")[-1]["body"] == "",
                      "a picture with no words is a message and goes on its own")

            # ---- something that is not a picture ---------------------------------------
            page.get_by_test_id("attach-input").set_input_files([str(quote)])
            problem = page.get_by_test_id("picture-problem")
            expect(problem).to_be_visible(timeout=8000)
            checks.ok("picture" in problem.inner_text().lower(),
                      f"a PDF is refused in a sentence ({problem.inner_text()!r})")
            checks.ok(page.get_by_test_id("picture-preview").count() == 0
                      and db_count("select count(*) from message_attachments where "
                                   "status='DRAFT'") == 0,
                      "and nothing at all was attached or stored")
            page.screenshot(path=str(shots / "07-refused.png"))

            # ---- an unknown number: pictures, no contact --------------------------------
            out = mms("m-1", "roof", 1, number="+13055550123")
            drain()
            page.reload()
            conversations(page)
            page.locator("div.cursor-pointer", has_text="+13055550123").first.click()
            page.wait_for_timeout(600)
            checks.ok(db_count("select count(*) from contacts") == contacts0,
                      "an MMS from an unknown number still creates no contact")
            checks.ok(out["number_thread_id"] is not None
                      and rendered(page.get_by_test_id("picture-thumb").last.locator("img")),
                      "and its picture shows on the number's own thread")
            page.screenshot(path=str(shots / "08-number-thread.png"))

            checks.ok(not errors, f"no uncaught page errors ({errors[:2]})")

            # ---- a restricted technician -------------------------------------------------
            # Jane's picture is not hers to see. Asked of the BYTES route, in a real
            # browser session, which is the door that actually has to hold.
            with sqlite3.connect(db_path) as c:
                jane_att = c.execute(
                    "select a.id from message_attachments a "
                    "join conversation_events e on e.id = a.conversation_event_id "
                    "join conversations v on v.id = e.conversation_id "
                    "join contacts ct on ct.id = v.contact_id "
                    "where ct.phone = ? and a.status='STORED' limit 1", (JANE,)).fetchone()[0]
            tctx = browser.new_context(viewport={"width": 1440, "height": 900})
            tpage = tctx.new_page()
            sign_in(tpage, TECH)
            status = tpage.evaluate(
                """async (id) => (await fetch('/api/attachments/' + id,
                                              {credentials: 'include'})).status""",
                jane_att)
            checks.ok(status == 404,
                      f"a restricted technician's own browser cannot read it ({status})")
            admin_status = page.evaluate(
                """async (id) => (await fetch('/api/attachments/' + id,
                                              {credentials: 'include'})).status""",
                jane_att)
            checks.ok(admin_status == 200, "and an admin still can")
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
            for f in sorted(work.rglob("*"), reverse=True):
                f.unlink() if f.is_file() else f.rmdir()
            work.rmdir()

    passed = sum(1 for ok, _ in checks.results if ok)
    print(f"\n{passed}/{len(checks.results)} picture browser checks passed")
    return 0 if passed == len(checks.results) else 1


def drain_jobs(db_path: str, media_root: str, record: str) -> None:
    """Drain the queue in a child process, with the SAME owen-main double the API has.

    A child rather than an in-process call because `app.db` builds its engine at import
    time from `DATABASE_URL`, and this module's parent process has no database at all.
    """
    os.environ.update({"DATABASE_URL": "sqlite:///" + db_path, "AUTO_CREATE_ALL": "1",
                       "MEDIA_ROOT": media_root,
                       "CRM_LINK_BASE_URL": "http://127.0.0.1:9",
                       "CRM_LINK_API_KEY": "owen_sk_fake"})
    sys.path.insert(0, str(BACKEND))
    from app import crmlink
    from app.worker import drain_once

    def forbidden(*a, **k):
        raise RuntimeError("a real HTTP request to the phone system was attempted")

    crmlink.httpx.post = forbidden
    crmlink.httpx.get = forbidden
    inbound = {"m-1/0": png(b"a"), "m-3/0": png(b"a"), "m-3/1": png(b"b"),
               "m-3/2": png(b"c")}

    def fetch_message_media(message_id, index):
        key = "%s/%d" % (message_id, index)
        # Recorded into the SAME file the API's double writes to, so "the bytes were
        # fetched exactly once" counts every fetch from both processes rather than only
        # the ones that happened to run in the one the browser is talking to.
        with open(record, "a") as fh:
            fh.write(json.dumps({"kind": "fetch", "key": key}) + "\n")
        data = inbound.get(key)
        if data is None:
            return crmlink.LinkResult(False, 502, "could not reach the phone system")
        return data, "image/png"

    crmlink.fetch_message_media = fetch_message_media
    while drain_once():
        pass


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        serve(int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6])
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "--drain":
        drain_jobs(sys.argv[2], sys.argv[3], sys.argv[4])
        sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep the temp database")
    ap.add_argument("--shots", default=None, help="where to write screenshots")
    args = ap.parse_args()
    out = Path(args.shots or tempfile.mkdtemp(prefix="pictures-shots-"))
    out.mkdir(parents=True, exist_ok=True)
    print("screenshots:", out)
    sys.exit(run(out, args.keep))

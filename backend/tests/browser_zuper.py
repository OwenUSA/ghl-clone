"""Drive Settings → Zuper, "Quotes & invoices", the contact section and Zuper photos in a REAL
headless browser.

    cd backend && uv run python -m tests.browser_zuper [--keep] [--shots DIR]

Not collected by pytest (no `test_` prefix): it starts its own API and Vite dev server.
Needs node on PATH, the `capture` dependency group (Playwright) and its Chromium.

NOTHING HERE CAN REACH ZUPER, by construction rather than by care:

  * the API runs in a child process (`--serve`) in which `app.zuper.client.TRANSPORT` is the
    in-memory fake (`tests/zuper_fake.py`), installed BEFORE any request can be made. The fake
    answers only the key `zk_test`, and every request the client sends goes to it — there is
    no other transport in that process. `client._sleep` is a no-op;
  * the owen-main link and the softphone are unset, and the dev server is the dialer check's
    (`frontend/e2e/vite.sipmock.config.ts`), so no SIP user agent exists either;
  * the database is a throwaway SQLite file in a fresh temp directory, created with
    `create_all` — never `app.seed`, never a database this did not create.

What the child does before serving, as the runbook's operator would: seeds users, pipelines,
contacts and cards, runs the Checklist seed, creates the Zuper categories and runs the initial
load (into the fake). Then, as "a person in Zuper": adds a quote and a paid invoice to Jane's
job and an invoice to the technician's job, pulls them through `engine.pull_document`, deletes
Carl's job and mirrors that delete through `engine.mirror_zuper_delete` (a real, restorable
snapshot), attaches a photo and a PDF to Jane's job, and adds ONE hand-made conflict row with a
Checklist field (the only row the engine did not write itself). Everything else on screen was
produced by the shipped code.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import sqlite3
import struct
import subprocess
import sys
import tempfile
import traceback
import zlib
from pathlib import Path

from tests.browser_dialer import BACKEND, FRONTEND, Checks, free_port, wait_http

ADMIN = "owner@example.test"          # the fake's office user email
TECH = "tess@zuper.test"
PASSWORD = "zuper-check-password-1"


def png(width: int = 96, height: int = 96, rgb=(234, 88, 12)) -> bytes:
    """A real, solid-colour PNG, so the relayed thumbnail has pixels to draw."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    row = b"\x00" + bytes(rgb) * width
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * height)) + chunk(b"IEND", b""))


# --------------------------------------------------------------------------- the API

def serve(port: int, db_path: str, ids_file: str) -> None:
    """The API, with Zuper replaced by the in-memory fake. Runs in a child process."""
    for key in list(os.environ):
        if key.startswith(("ZUPER_", "CRM_LINK_", "OWEN_")):
            os.environ.pop(key)
    os.environ.update({
        "DATABASE_URL": "sqlite:///" + db_path,
        "AUTO_CREATE_ALL": "1",
        "ZUPER_SYNC_ENABLED": "true",
        "ZUPER_API_KEY": "zk_test",
        "ZUPER_WEBHOOK_TOKEN": "whk_test",
    })
    sys.path.insert(0, str(BACKEND))
    from app.zuper import client
    from tests.zuper_fake import FakeZuper, iso

    # FIRST, before anything that could send a request exists.
    fake = FakeZuper().configure_account().install()
    client._sleep = lambda seconds: None

    import uvicorn
    from app import auth, checklist_seed
    from app.db import SessionLocal
    from app.main import app
    from app.models import (
        Contact,
        Opportunity,
        Pipeline,
        Role,
        Stage,
        User,
        ZuperConflict,
        ZuperDocument,
    )
    from app.zuper import engine, load, mapping, setup

    ids: dict[str, int | str] = {}
    with SessionLocal() as db:
        admin = User(email=ADMIN, name="Owen Owner", role=Role.ADMIN,
                     password_hash=auth.hash_password(PASSWORD))
        tech = User(email=TECH, name="Tess Tech", role=Role.TECH, only_assigned_data=True,
                    password_hash=auth.hash_password(PASSWORD))
        db.add_all([admin, tech])
        db.flush()
        stages: dict[str, list[Stage]] = {}
        for name, names in ((mapping.AHS_PIPELINE, ["New Lead", "Inspection", "Submit Invoices"]),
                            (mapping.RETAIL_PIPELINE, ["New Lead", "Estimate Sent", "Sold"]),
                            ("Warranty", ["Open"])):
            p = Pipeline(name=name)
            db.add(p)
            db.flush()
            stages[name] = []
            for i, stage_name in enumerate(names):
                s = Stage(pipeline_id=p.id, name=stage_name, position=i)
                db.add(s)
                stages[name].append(s)
            db.flush()
        people = {}
        for key, first, last, phone in (("jane", "Jane", "Doe", "+19415550101"),
                                        ("bob", "Bob", "Roof", "+19415550150"),
                                        ("carl", "Carl", "Keys", "+19415550160"),
                                        ("dan", "Dan", "Ward", "+19415550170")):
            c = Contact(first_name=first, last_name=last, phone=phone,
                        email="%s@example.test" % key)
            db.add(c)
            people[key] = c
        db.flush()
        cards = {}
        for key, title, pipeline, owner in (("jane", "Jane roof", mapping.AHS_PIPELINE, admin),
                                            ("bob", "Bob roof", mapping.RETAIL_PIPELINE, tech),
                                            ("carl", "Carl gutter", mapping.RETAIL_PIPELINE, admin),
                                            ("dan", "Dan warranty", "Warranty", admin)):
            o = Opportunity(title=title, contact_id=people[key].id,
                            pipeline_id=stages[pipeline][0].pipeline_id,
                            stage_id=stages[pipeline][0].id, owner_id=owner.id,
                            value_cents=100_00, created_by="Browser check")
            db.add(o)
            cards[key] = o
        db.commit()
        for key in people:
            ids["contact_" + key] = people[key].id
            ids["opp_" + key] = cards[key].id

        checklist_seed.run(db, commit=True)
        code, report = load.run(db, commit=True)
        if code != 0:
            raise SystemExit("the initial load into the fake failed: %s" % json.dumps(report))

        engine.mark_quiet(db)
        job = {k: mapping.mapping_for(db, "opportunity", cards[k].id).zuper_uid
               for k in ("jane", "bob", "carl")}
        # "A person in Zuper": a quote approved and an invoice paid on Jane's job, an open
        # invoice on the technician's own job.
        quote = fake.add_estimate(job["jane"], status="APPROVED", total="9500.00",
                                  number="Q-1001")
        paid = fake.add_invoice(job["jane"], status="PAID", total="9500.00", balance="0.00",
                                number="INV-7")
        sent = fake.add_invoice(job["bob"], status="SENT", total="1200.00", balance="1200.00",
                                number="INV-9")
        ctx = engine.Ctx(db)
        engine.pull_document(ctx, ZuperDocument.QUOTE, quote)
        engine.pull_document(ctx, ZuperDocument.INVOICE, paid)
        engine.pull_document(ctx, ZuperDocument.INVOICE, sent)
        db.commit()
        # Carl's job deleted in Zuper, mirrored with a restorable snapshot.
        fake.delete_job(job["carl"])
        ctx = engine.Ctx(db)
        engine.mirror_zuper_delete(ctx, "opportunity",
                                   mapping.mapping_for(db, "opportunity", cards["carl"].id))
        db.commit()
        # The one hand-made row: a Checklist answer the CRM question could not hold.
        db.add(ZuperConflict(crm_type="opportunity", crm_id=ids["opp_jane"],
                             zuper_uid=job["jane"], field="cl:How many leaks?",
                             rule="crm_cannot_hold", winner="crm", written_to="zuper",
                             before="several", after="3"))
        db.commit()
        # Zuper's job attachments on Jane's job: a photo and a PDF, served by the fake.
        photo_url = "https://files.zuperpro.com/att/roof.png"
        pdf_url = "https://files.zuperpro.com/att/report.pdf"
        fake.files[photo_url] = (png(), "image/png")
        fake.files[pdf_url] = (b"%PDF-1.4\n% fake\n", "application/pdf")
        fake.attachments[job["jane"]] = [
            {"attachment_uid": "att-1", "file_name": "roof.png", "mime_type": "image/png",
             "url": photo_url, "created_at": iso(fake.clock)},
            {"attachment_uid": "att-2", "file_name": "report.pdf",
             "mime_type": "application/pdf", "url": pdf_url, "created_at": iso(fake.clock)},
        ]
        _ = setup   # imported so a missing module fails here, not mid-drive
    Path(ids_file).write_text(json.dumps(ids))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


# --------------------------------------------------------------------------- the run

def owner_pid(port: int) -> int | None:
    """The pid listening on 127.0.0.1:<port>, from `ss` — so a stale server on a reused port
    is never mistaken for ours."""
    out = subprocess.run(["ss", "-ltnpH", "sport = :%d" % port], capture_output=True,
                         text=True).stdout
    import re
    hit = re.search(r"pid=(\d+)", out)
    return int(hit.group(1)) if hit else None


def ours(port: int, proc: subprocess.Popen) -> bool:
    pid = owner_pid(port)
    try:
        return pid is not None and os.getpgid(pid) == proc.pid
    except ProcessLookupError:
        return False


def run(shots: Path, keep: bool) -> int:
    from playwright.sync_api import expect, sync_playwright

    work = Path(tempfile.mkdtemp(prefix="zuper-check-"))
    db_path = str(work / "zuper.db")
    ids_file = work / "ids.json"
    api_port, vite_port = free_port(), free_port()
    procs: list[subprocess.Popen] = []
    checks = Checks()

    def db_one(sql: str, *args):
        with sqlite3.connect(db_path) as c:
            row = c.execute(sql, args).fetchone()
            return row[0] if row else None

    try:
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "tests.browser_zuper", "--serve", str(api_port), db_path,
             str(ids_file)], cwd=BACKEND, start_new_session=True))
        wait_http(f"http://127.0.0.1:{api_port}/api/health", timeout=120)
        checks.ok(procs[0].poll() is None and ours(api_port, procs[0]),
                  f"the API on :{api_port} is the child this run started")
        env = {**os.environ, "GHL_API_PORT": str(api_port), "GHL_VITE_PORT": str(vite_port)}
        procs.append(subprocess.Popen(
            ["npx", "vite", "--config", "e2e/vite.sipmock.config.ts"], cwd=FRONTEND, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        app_url = f"http://localhost:{vite_port}/"
        wait_http(app_url)
        checks.ok(ours(vite_port, procs[1]), f"Vite on :{vite_port} is the one this run started")
        ids = json.loads(ids_file.read_text())
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
                p.wait_for_timeout(300)

            sign_in(page, ADMIN)

            # ---- Settings → Zuper: blocked until the setup check passes -----------------
            page.get_by_role("button", name="Settings", exact=True).first.click()
            page.get_by_role("tab", name="Zuper", exact=True).click()
            page.goto(f"{app_url}settings/zuper")
            expect(page.get_by_role("tab", name="Zuper", exact=True)).to_have_attribute(
                "aria-selected", "true", timeout=10000)
            checks.ok(True, "/settings/zuper opens the Zuper section")
            blockers = page.get_by_test_id("blockers")
            expect(blockers).to_be_visible(timeout=10000)
            checks.ok("The sync can be switched on when:" in blockers.inner_text()
                      and "Not confirmed by hand yet" in blockers.inner_text(),
                      "unconfirmed items are listed as reasons")
            checks.ok(page.get_by_role("switch").count() == 0,
                      "no switch is drawn while anything blocks it")
            checks.ok(page.get_by_test_id("sync-chip").inner_text().strip() == "Paused",
                      "the connection chip says Paused (env on, key set, switch off)")
            page.screenshot(path=str(shots / "01-zuper-blocked.png"))

            with page.expect_response(lambda r: "/api/zuper/setup/check" in r.url) as resp:
                page.get_by_role("button", name="Run setup check").click()
            checks.ok(resp.value.status == 200, "Run setup check answers 200")
            items = page.locator("[data-setup-item]")
            expect(items.first).to_be_visible(timeout=10000)
            fails = page.locator('[data-setup-item][data-state="fail"]').count()
            by_hand = page.locator('[data-setup-item][data-state="confirm_by_hand"]')
            checks.ok(fails == 0 and by_hand.count() == 9,
                      f"the check passes, with 9 items to confirm by hand ({fails} fail, "
                      f"{by_hand.count()} by hand)")
            checks.ok(page.locator('[data-chip="PASS"]').count() >= 6
                      and page.locator('[data-chip="Confirm by hand"]').count() == 9,
                      "PASS and Confirm by hand chips")
            boxes = page.locator('input[type="checkbox"][aria-label^="Confirmed by hand"]')
            for i in range(boxes.count()):
                box = boxes.nth(i)
                with page.expect_response(lambda r: "/api/zuper/setup/confirmations/" in r.url):
                    box.click()
                expect(box).to_be_checked(timeout=5000)
            checks.ok(db_one("select count(*) from json_each((select confirmations from "
                             "zuper_settings where id = 1))") == 9,
                      "all nine confirmations are stored")
            expect(by_hand.first).to_contain_text("by " + ADMIN)
            checks.ok(True, "a confirmed item says who confirmed it")
            switch = page.get_by_role("switch", name="Sync with Zuper")
            expect(switch).to_be_visible(timeout=10000)
            checks.ok(switch.get_attribute("aria-checked") == "false"
                      and page.get_by_test_id("blockers").count() == 0,
                      "once nothing blocks it, the switch appears (off)")
            page.locator('[data-card="switch"]').screenshot(path=str(shots / "02-zuper-ready.png"))

            switch.click()
            dialog = page.get_by_role("alertdialog", name="Switch the Zuper sync on?")
            checks.ok(dialog.is_visible() and "writing to Zuper" in dialog.inner_text(),
                      "switching on asks first, and says it starts writing to Zuper")
            page.screenshot(path=str(shots / "03-switch-on-confirm.png"))
            dialog.get_by_role("button", name="Switch on").click()
            expect(page.get_by_test_id("sync-chip")).to_have_text("On", timeout=5000)
            checks.ok(db_one("select enabled from zuper_settings where id = 1") == 1
                      and switch.get_attribute("aria-checked") == "true",
                      "the sync is on in the database and on screen")

            # ---- the cutover date -------------------------------------------------------
            date = page.get_by_label("Workiz cutover date")
            date.fill("2026-01-01")
            page.locator('[data-card="cutover"]').get_by_role("button", name="Save").click()
            expect(page.get_by_test_id("cutover-sentence")).to_have_text(
                "Workiz retired on Jan 1, 2026 — the importer refuses to run", timeout=5000)
            checks.ok(db_one("select workiz_cutover_date from zuper_settings") == "2026-01-01",
                      "Save stores the cutover date and the sentence says Workiz is retired")
            page.locator('[data-card="cutover"]').get_by_role("button", name="Clear").click()
            expect(page.get_by_test_id("cutover-sentence")).to_have_text(
                "Workiz is still in use", timeout=5000)
            checks.ok(db_one("select workiz_cutover_date from zuper_settings") is None,
                      "Clear removes it")

            # ---- counts, heartbeat, load report, conflicts ------------------------------
            def cells(kind):
                row = page.locator(f'[data-count-row="{kind}"] td')
                return [row.nth(i).inner_text().strip() for i in range(1, 4)]

            def mapped(kind, state):
                return str(db_one("select count(*) from zuper_mappings where crm_type = ? "
                                  "and state = ?", kind, state))

            checks.ok(cells("contact") == [mapped("contact", "linked"), "0", "0"]
                      and cells("contact")[0] == "4",
                      f"Contacts: 4 linked ({cells('contact')})")
            checks.ok(cells("opportunity") == [mapped("opportunity", "linked"), "0",
                                               mapped("opportunity", "deleted")]
                      == ["2", "0", "1"],
                      f"Opportunities: 2 linked, 1 deleted ({cells('opportunity')})")
            checks.ok(cells("stage")[0] == "6" and cells("pipeline")[0] == "2",
                      "both pipelines and all six stages are linked")
            load_card = page.locator('[data-card="load-report"]')
            checks.ok(load_card.is_visible() and "Commit" in load_card.inner_text()
                      and "No mismatches" in load_card.inner_text(),
                      "the initial load report shows (commit, no mismatches)")
            conflicts = page.locator('[data-section="conflicts"]')
            text = conflicts.inner_text()
            checks.ok("Zuper owns money" in text and "Invoice fully paid → Won" in text
                      and "Checklist: How many leaks?" in text
                      and "The CRM question cannot hold Zuper\u2019s answer" in text,
                      "the conflict log shows rules and fields in plain words")
            checks.ok("$100.00 → $9,500.00" in text,
                      "the value conflict reads as money, before → after")
            page.screenshot(path=str(shots / "04-zuper-on-counts.png"))
            page.locator('[data-card="setup"]').screenshot(path=str(shots / "04a-setup.png"))
            page.locator('[data-card="counts"]').screenshot(path=str(shots / "04b-counts.png"))
            load_card.screenshot(path=str(shots / "04c-load-report.png"))
            conflicts.screenshot(path=str(shots / "04d-conflicts.png"))

            # ---- Restore a mirrored delete end to end -----------------------------------
            carl = ids["opp_carl"]
            checks.ok(db_one("select count(*) from opportunities where id = ?", carl) == 0,
                      "before Restore, Carl's card is gone from the CRM")
            row = page.locator("[data-delete]").first
            checks.ok("Deleted in Zuper → deleted in the CRM" in row.inner_text()
                      and "Carl gutter" in row.inner_text(),
                      "the delete log names the direction and the record")
            row.get_by_role("button", name="Restore").click()
            confirm = page.get_by_role("alertdialog", name=f"Restore opportunity #{carl}?")
            checks.ok(confirm.is_visible(), "Restore asks first")
            page.screenshot(path=str(shots / "05-restore-confirm.png"))
            confirm.get_by_role("button", name="Restore").click()
            result = page.get_by_test_id("restore-result")
            expect(result).to_be_visible(timeout=10000)
            checks.ok("Restored in the CRM" in result.inner_text()
                      and "recovered in Zuper" in result.inner_text(),
                      f"the result says what happened ({result.inner_text()!r})")
            checks.ok(db_one("select count(*) from opportunities where id = ?", carl) == 1
                      and db_one("select state from zuper_delete_snapshots") == "restored",
                      "Carl's card exists again and the snapshot is restored")
            expect(page.locator("[data-delete]").first.get_by_role("button", name="Restore")
                   ).to_have_count(0, timeout=5000)
            checks.ok(True, "a restored delete offers no second Restore")
            page.locator('[data-section="deletes"]').screenshot(path=str(shots / "06-restored.png"))

            # ---- the opportunity modal, opened by the CRM Link deep link ------------------
            jane = ids["opp_jane"]
            page.goto(f"{app_url}opportunities?opportunity={jane}")
            modal = page.get_by_role("dialog", name='Edit "Jane roof"')
            expect(modal).to_be_visible(timeout=10000)
            checks.ok(page.url.endswith("/opportunities"),
                      f"the deep link opens the card and drops its parameter ({page.url})")
            nav = modal.get_by_role("navigation", name="Opportunity sections")
            quotes_nav = nav.get_by_role("button", name="Quotes & invoices")
            expect(quotes_nav).to_be_visible(timeout=5000)
            names = [b.inner_text().strip() for b in nav.get_by_role("button").all()]
            checks.ok(names.index("Photos") + 1 == names.index("Quotes & invoices"),
                      f"'Quotes & invoices' sits right after Photos ({names})")
            quotes_nav.click()
            panel = modal.locator('[data-panel="zuper-money"]')
            expect(panel).to_be_visible(timeout=5000)
            quotes = panel.get_by_role("table", name="Quotes").inner_text()
            invoices = panel.get_by_role("table", name="Invoices").inner_text()
            checks.ok("Q-1001" in quotes and "Approved" in quotes and "$9,500.00" in quotes
                      and "Sep 10, 2026" in quotes and "Oct 10, 2026" in quotes,
                      "the quote: number, status, total, date, expiry")
            checks.ok("INV-7" in invoices and "Paid" in invoices and "$9,500.00" in invoices
                      and "$0.00" in invoices and "Sep 26, 2026" in invoices,
                      "the invoice: number, status, total, balance, due date")
            checks.ok("From Zuper — read only" in panel.inner_text()
                      and panel.get_by_test_id("zuper-value-source").inner_text()
                      == "The card\u2019s value follows the invoices\u2019 total in Zuper.",
                      "read-only subtitle and the value-source sentence")
            checks.ok(panel.locator("input, select, textarea").count() == 0,
                      "nothing in the panel is editable")
            checks.ok(db_one("select status from opportunities where id = ?", jane) == "won"
                      and db_one("select value_cents from opportunities where id = ?", jane)
                      == 950000, "the paid invoice made the card Won at $9,500 (engine)")
            page.screenshot(path=str(shots / "07-quotes-and-invoices.png"))

            nav.get_by_role("button", name="Photos", exact=True).click()
            group = modal.get_by_role("region", name="Zuper attachments")
            expect(group).to_be_visible(timeout=10000)
            img = group.locator("img").first
            expect(img).to_be_visible(timeout=5000)
            page.wait_for_function("(el) => el.complete && el.naturalWidth > 0",
                                   arg=img.element_handle(), timeout=10000)
            checks.ok(img.get_attribute("src")
                      == f"/api/opportunities/{jane}/zuper/attachments/att-1",
                      "the photo loads through the CRM relay path, never Zuper's URL")
            pdf = group.locator('[data-attachment="att-2"]')
            checks.ok(pdf.get_attribute("href")
                      == f"/api/opportunities/{jane}/zuper/attachments/att-2"
                      and pdf.get_attribute("target") == "_blank"
                      and "report.pdf" in pdf.inner_text(),
                      "a non-image is a row that opens in a new tab")
            page.screenshot(path=str(shots / "08-photos-zuper.png"))

            # A card outside the sync's pipelines: no nav item at all.
            page.goto(f"{app_url}opportunities?opportunity={ids['opp_dan']}")
            other = page.get_by_role("dialog", name='Edit "Dan warranty"')
            expect(other).to_be_visible(timeout=10000)
            page.wait_for_timeout(800)
            checks.ok(other.get_by_role("button", name="Quotes & invoices").count() == 0,
                      "a card not linked to Zuper, with no documents, has no such nav item")

            # ---- the contact panel ------------------------------------------------------
            page.goto(f"{app_url}contacts?contact={ids['contact_jane']}")
            section = page.get_by_role("button", name="Zuper quotes & invoices (2)")
            expect(section).to_be_visible(timeout=10000)
            checks.ok(page.url.endswith("/contacts"), "the contact deep link opens the panel")
            section.click()
            docs = page.locator("[data-document]")
            expect(docs).to_have_count(2, timeout=5000)
            joined = " ".join(docs.nth(i).inner_text() for i in range(2))
            checks.ok("Quote Q-1001" in joined and "Invoice INV-7" in joined
                      and "$9,500.00" in joined and "Jane roof" in joined,
                      "each row: kind, number, status, total, the card's title")
            page.screenshot(path=str(shots / "09-contact-section.png"))
            docs.first.get_by_role("button", name="Jane roof").click()
            expect(page.get_by_role("dialog", name='Edit "Jane roof"')).to_be_visible(timeout=10000)
            checks.ok(True, "the card's title opens that card")

            checks.ok(not errors, f"no uncaught page errors ({errors[:2]})")

            # ---- a restricted technician ------------------------------------------------
            tctx = browser.new_context(viewport={"width": 1440, "height": 900})
            tpage = tctx.new_page()
            terrors: list[str] = []
            tpage.on("pageerror", lambda e: terrors.append(str(e)))
            sign_in(tpage, TECH)
            other_card = tpage.request.get(f"{app_url}api/opportunities/{jane}/zuper")
            own = tpage.request.get(f"{app_url}api/opportunities/{ids['opp_bob']}/zuper")
            status = tpage.request.get(f"{app_url}api/zuper/status")
            checks.ok(other_card.status == 404,
                      f"someone else's card's money panel is 404 ({other_card.status})")
            checks.ok(own.status == 200 and [d["number"] for d in own.json()["invoices"]]
                      == ["INV-9"], "their own card's panel answers with its invoice")
            checks.ok(status.status == 403, f"Settings → Zuper's status is 403 ({status.status})")
            tpage.get_by_role("button", name="Settings", exact=True).first.click()
            tpage.wait_for_timeout(300)
            checks.ok(tpage.get_by_role("tab", name="Zuper", exact=True).count() == 0,
                      "a technician has no Zuper tab")
            tpage.goto(f"{app_url}opportunities?opportunity={ids['opp_bob']}")
            tmodal = tpage.get_by_role("dialog", name='Edit "Bob roof"')
            expect(tmodal).to_be_visible(timeout=10000)
            tmodal.get_by_role("button", name="Quotes & invoices").click()
            tpanel = tmodal.locator('[data-panel="zuper-money"]')
            expect(tpanel).to_be_visible(timeout=5000)
            text = tpanel.inner_text()
            checks.ok("INV-9" in text and "Sent" in text and "$1,200.00" in text,
                      "the technician's own card shows its invoice")
            tpage.screenshot(path=str(shots / "10-tech-own-card.png"))
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
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(p.pid, signal.SIGKILL)
        if not keep:
            for f in work.iterdir():
                f.unlink()
            work.rmdir()

    passed = sum(1 for ok, _ in checks.results if ok)
    print(f"\n{passed}/{len(checks.results)} Zuper browser checks passed")
    return 0 if passed == len(checks.results) else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        serve(int(sys.argv[2]), sys.argv[3], sys.argv[4])
        sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep the temp database")
    ap.add_argument("--shots", default=None, help="where to write screenshots")
    args = ap.parse_args()
    out = Path(args.shots or tempfile.mkdtemp(prefix="zuper-shots-"))
    out.mkdir(parents=True, exist_ok=True)
    print("screenshots:", out)
    sys.exit(run(out, args.keep))

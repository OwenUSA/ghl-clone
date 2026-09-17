"""The Zuper client's safety rails, the webhook route, the off switch, the listener and who
may see what. Behaviour: every refusal is checked for what it did NOT do — no request left, no
row stored, nothing changed."""
import base64
import hashlib
import hmac
import json
import re
from pathlib import Path

import httpx
import pytest
from app.db import SessionLocal
from app.main import app
from app.models import (
    Contact,
    Job,
    Opportunity,
    ZuperDocument,
    ZuperSettings,
    ZuperWebhookDelivery,
)
from app.zuper import client, config, engine, webhooks, worker
from app.zuper.client import ZuperError
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests import zuper_guard
from tests.zuper_fake import FakeZuper
from tests.zuper_support import drain, uid_of

APP_DIR = Path(__file__).resolve().parents[1] / "app" / "zuper"


def zuper_jobs() -> list[Job]:
    with SessionLocal() as s:
        return list(s.scalars(select(Job).where(Job.type.like("zuper_%"))).all())


def inbox_count() -> int:
    with SessionLocal() as s:
        return s.scalar(select(func.count(ZuperWebhookDelivery.id))) or 0


# ------------------------------------------------------------------ the client's rails

DENIED = [
    ("POST", "/telephony/message"), ("GET", "/telephony/calls"),
    ("POST", "/estimate/est-1/send"), ("POST", "/invoice/inv-1/send"),
    ("POST", "/invoice/inv-1/send_reminder"), ("POST", "/payment_request"),
    ("POST", "/customers/cus-1/portal/invite"), ("POST", "/customer_portal/invite"),
    ("POST", "/service/notifications/send"), ("POST", "/notifications/sms"),
    ("POST", "/estimate"), ("PUT", "/estimate/est-1"), ("PUT", "/invoice/inv-1/status"),
    ("POST", "/invoice"), ("POST", "/payments"), ("DELETE", "/invoice/inv-1"),
    ("POST", "/jobs/job-1/send"), ("POST", "/sms"), ("POST", "/email"),
]


@pytest.mark.parametrize("method,path", DENIED)
def test_denylisted_endpoints_are_refused_before_any_request(zuper_env, method, path):
    fake = FakeZuper().install()
    with pytest.raises(ZuperError) as exc:
        client.request(method, path, body={})
    assert exc.value.kind == "refused"
    assert fake.requests == []
    assert client.denied(method, path)


def test_reading_quotes_and_invoices_is_allowed_writing_them_never(zuper_env):
    assert client.denied("GET", "/estimate") is None and client.allowed("GET", "/estimate")
    assert client.denied("GET", "/invoice/inv-1") is None
    assert not any(m != "GET" and re.search("estimate|invoice|payment", rx)
                   for m, rx in client.ALLOWLIST)


def test_every_path_the_package_names_is_allowed_and_none_is_denylisted():
    """Package-wide: every endpoint the sync can call, and every string in app/zuper that
    looks like one, passes the denylist — so no code path here can name a send."""
    for name, template in client.PATHS.items():
        concrete = re.sub(r"\{[a-z_]+\}", "x1", template)
        methods = [m for m, rx in client.ALLOWLIST if re.match(rx, concrete)]
        assert methods, name
        for m in methods:
            assert client.denied(m, concrete) is None, (name, m)
    for source in APP_DIR.glob("*.py"):
        for literal in re.findall(r'"(/[a-z_]+(?:/[a-z_{}]+)*)"', source.read_text()):
            if literal.startswith("/api/"):
                continue            # the CRM's own routes
            concrete = re.sub(r"\{[a-z_]+\}", "x1", literal)
            for m in [m for m, rx in client.ALLOWLIST if re.match(rx, concrete)]:
                assert client.denied(m, concrete) is None, (source.name, m, literal)


def test_a_route_not_on_the_allowlist_is_refused(zuper_env):
    fake = FakeZuper().install()
    with pytest.raises(ZuperError, match="allowlist"):
        client.request("DELETE", "/jobs")
    assert fake.requests == []


def test_a_dry_run_refuses_every_write_before_it_is_built(zuper_env):
    fake = FakeZuper().install()
    with client.read_only(), pytest.raises(ZuperError, match="dry run"):
        client.request("POST", "/customers_new", body={"customer": {}})
    assert fake.requests == []


def test_429_backs_off_honouring_retry_after_and_then_succeeds(zuper_env, monkeypatch):
    fake = FakeZuper().install()
    slept = []
    monkeypatch.setattr(client, "_sleep", slept.append)
    fake.throttle = 3
    assert client.request("GET", "/jobs/category")["type"] == "success"
    assert slept == [1.0, 1.0, 1.0]
    assert len(fake.requests) == 4


def test_429_that_never_stops_is_a_rate_limited_error_not_a_loop(zuper_env, monkeypatch):
    fake = FakeZuper().install()
    monkeypatch.setattr(client, "_sleep", lambda s: None)
    fake.throttle = 100
    with pytest.raises(ZuperError) as exc:
        client.request("GET", "/jobs/category")
    assert exc.value.kind == "rate_limited"
    assert len(fake.requests) == client.RETRIES_ON_429 + 1


def test_pacing_holds_to_the_configured_requests_per_minute(zuper_env, monkeypatch):
    FakeZuper().install()
    monkeypatch.setenv("ZUPER_REQUESTS_PER_MINUTE", "3")
    now = {"t": 1000.0}
    slept = []
    monkeypatch.setattr(client, "_now", lambda: now["t"])

    def sleep(seconds):
        slept.append(round(seconds, 2))
        now["t"] += seconds
    monkeypatch.setattr(client, "_sleep", sleep)
    for _ in range(4):
        client.request("GET", "/jobs/category")
    assert slept == [60.0]


def test_the_key_goes_only_to_zuperpro_hosts(zuper_env, monkeypatch):
    monkeypatch.setenv("ZUPER_BASE_URL", "https://evil.example.com/api")
    FakeZuper().install()
    with pytest.raises(ZuperError, match=r"zuperpro\.com"):
        client.request("GET", "/jobs/category")


# ------------------------------------------------------------------ the network guard

def test_the_zuper_environment_was_stripped_and_the_guard_is_live(monkeypatch):
    import socket
    assert config.env_enabled() is False and config.key_set() is False
    with pytest.raises(OSError, match="network guard"):
        socket.getaddrinfo("accounts.zuperpro.com", 443)
    with pytest.raises(OSError, match="network guard"):
        socket.create_connection(("us-east-1.zuperpro.com", 443), timeout=1)
    assert zuper_guard.ATTEMPTS == ["accounts.zuperpro.com", "us-east-1.zuperpro.com"]
    zuper_guard.ATTEMPTS.clear()


def test_an_unmocked_request_is_stopped_by_the_guard(zuper_env):
    client.TRANSPORT = zuper_guard.GUARD_TRANSPORT
    with pytest.raises(OSError, match="network guard"):
        client.request("GET", "/jobs/category")
    assert zuper_guard.ATTEMPTS
    zuper_guard.ATTEMPTS.clear()


# ------------------------------------------------------------------ switched off

def test_with_the_env_flag_false_nothing_is_queued_sent_or_accepted(zworld, monkeypatch):
    """The DB says enabled and setup passed — only the environment flag is off."""
    monkeypatch.setenv("ZUPER_API_KEY", "zk_test")
    recorder = FakeZuper().configure_account().install()
    with SessionLocal() as s:
        row = config.settings(s)
        row.enabled, row.setup_passed = True, True
        s.commit()
    c = zworld.client("owner")
    ids = zworld.ids
    assert c.patch("/api/contacts/%d" % ids["jane"], json={"first_name": "J"}).status_code == 200
    assert c.post("/api/opportunities/%d/notes" % ids["jane_card"],
                  json={"body": "x"}).status_code == 201
    assert c.patch("/api/opportunities/%d" % ids["jane_card"],
                   json={"stage_id": ids["stages"]["ahs:Call Back"]}).status_code == 200
    assert c.delete("/api/opportunities/%d" % ids["tim_card"]).status_code == 200
    assert zuper_jobs() == []
    worker.tick(SessionLocal)
    assert c.get("/api/opportunities/%d/zuper" % ids["jane_card"]).json()["sync"] == "off"
    assert c.get("/api/opportunities/%d/zuper/attachments" % ids["jane_card"]).json()[
        "state"] == "off"
    assert c.post("/api/zuper/setup/check").status_code == 409
    r = TestClient(app).post("/api/zuper/webhook", headers={"X-Webhook-Token": "whk_test"},
                             json={"job_uid": "job-1"})
    assert r.status_code == 503 and "switched off" in r.json()["detail"]
    assert recorder.requests == []
    assert inbox_count() == 0
    with pytest.raises(ZuperError) as exc:
        client.request("GET", "/jobs/category")
    assert exc.value.kind == "off"


def test_with_the_switch_off_nothing_is_queued_even_with_the_env_flag_on(zworld, fake):
    c = zworld.client("owner")
    assert c.patch("/api/contacts/%d" % zworld.ids["jane"],
                   json={"first_name": "J"}).status_code == 200
    assert zuper_jobs() == []
    worker.tick(SessionLocal)
    assert fake.requests == []


# ------------------------------------------------------------------ the listener

def test_a_save_queues_one_push_after_commit_and_bursts_coalesce(loaded, fake):
    c = loaded.client("owner")
    card = loaded.ids["jane_card"]
    for answer in ("1", "2", "3+"):
        assert c.patch("/api/opportunities/%d/detail" % card, json={
            "custom_fields": {"checklist_stories": answer}}).status_code == 200
    pending = [j for j in zuper_jobs() if j.status == "pending"]
    assert [(j.type, j.dedupe_key) for j in pending] == [
        ("zuper_push", "zuper:push:opportunity:%d" % card)]
    drain()
    job = fake.jobs[uid_of("opportunity", card)]
    from app.zuper import mapping
    assert mapping.custom_values(job)["How many stories?"] == "3+"
    done = zuper_jobs()
    assert all(j.status == "done" and j.dedupe_key is None for j in done)
    # The key was released, so the next save queues again.
    c.patch("/api/opportunities/%d/detail" % card,
            json={"custom_fields": {"checklist_stories": "2"}})
    assert len([j for j in zuper_jobs() if j.status == "pending"]) == 1


def test_a_refused_save_queues_nothing(loaded, fake):
    r = loaded.client("tess").patch("/api/contacts/%d" % loaded.ids["jane"],
                                    json={"first_name": "Hacked"})
    assert r.status_code in (403, 404)
    assert zuper_jobs() == []


def test_the_main_worker_never_runs_zuper_jobs(loaded, fake):
    from app.worker import drain_once
    loaded.client("owner").post("/api/opportunities/%d/notes" % loaded.ids["jane_card"],
                                json={"body": "Ladder on the truck"})
    writes = len(fake.writes())
    assert drain_once() == 0
    assert len(fake.writes()) == writes
    assert [j.status for j in zuper_jobs()] == ["pending"]


# ------------------------------------------------------------------ the webhook

def post_webhook(body: dict | bytes, token: str | None = "whk_test", **headers):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    h = {"content-type": "application/json", **headers}
    if token is not None:
        h["X-Webhook-Token"] = token
    return TestClient(app).post("/api/zuper/webhook", content=raw, headers=h)


def test_a_bad_or_missing_token_is_401_and_nothing_is_stored(armed, fake):
    for token in (None, "wrong", ""):
        r = post_webhook({"module": "JOB", "job_uid": "job-1"}, token=token)
        assert r.status_code == 401
    assert inbox_count() == 0


def test_a_delivery_is_stored_raw_then_processed_by_the_worker(loaded, fake):
    uid = uid_of("opportunity", loaded.ids["jane_card"])
    fake.edit_job(uid, job_title="Retitled by the office")
    body = {"module": "JOB", "event": "JOB_UPDATED",
            "data": {"job_uid": uid, "updated_by": {"user_uid": "u-owner"}}}
    r = post_webhook(body)
    assert r.status_code == 202 and r.json() == {"received": True, "duplicate": False}
    assert post_webhook(body).json()["duplicate"] is True
    with SessionLocal() as s:
        row = s.scalar(select(ZuperWebhookDelivery))
        assert (row.status, row.module, row.record_uid) == ("pending", "job", uid)
        assert json.loads(row.body) == body
        webhooks.process_inbox(s)
    with SessionLocal() as s:
        assert s.get(Opportunity, loaded.ids["jane_card"]).title == "Retitled by the office"
        assert s.scalar(select(ZuperWebhookDelivery.status)) == "processed"


def test_an_event_made_by_crm_sync_is_ignored_without_reading_zuper(loaded, fake):
    uid = uid_of("opportunity", loaded.ids["jane_card"])
    reads = len(fake.requests)
    post_webhook({"module": "JOB", "job_uid": uid, "updated_by": {"user_uid": "u-sync"}})
    with SessionLocal() as s:
        webhooks.process_inbox(s)
        row = s.scalar(select(ZuperWebhookDelivery))
        assert (row.status, row.outcome) == ("ignored", "echo: made by CRM Sync")
    assert len(fake.requests) == reads


def test_an_event_whose_content_is_unchanged_is_an_echo_and_writes_nothing(loaded, fake):
    card = loaded.ids["jane_card"]
    uid = uid_of("opportunity", card)
    loaded.client("owner").patch("/api/opportunities/%d/detail" % card,
                                 json={"custom_fields": {"checklist_stories": "2"}})
    drain()                             # our push: Zuper now holds the new answer
    writes = len(fake.writes())
    with SessionLocal() as s:
        before = s.get(Opportunity, card).updated_at
    post_webhook({"module": "JOB", "job_uid": uid, "updated_by": {"user_uid": "someone"}})
    with SessionLocal() as s:
        webhooks.process_inbox(s)
        row = s.scalar(select(ZuperWebhookDelivery))
        assert (row.status, row.outcome) == ("ignored", "echo: content unchanged")
        assert s.get(Opportunity, card).updated_at == before
    assert len(fake.writes()) == writes


def test_a_signature_is_verified_when_one_arrives(armed, fake, monkeypatch):
    monkeypatch.setenv("ZUPER_WEBHOOK_SECRET", "s3cret")
    raw = json.dumps({"module": "CUSTOMER", "customer_uid": "cus-1"}).encode()
    mac = hmac.new(b"s3cret", raw, hashlib.sha256).digest()
    assert post_webhook(raw, **{"X-Zuper-Signature": "sha256=" + "0" * 64}).status_code == 401
    assert inbox_count() == 0
    assert post_webhook(raw, **{"X-Zuper-Signature": base64.b64encode(mac).decode()}
                        ).status_code == 202
    with SessionLocal() as s:
        assert s.scalar(select(ZuperWebhookDelivery.signature)) == "valid"


def test_a_delivery_with_no_uid_is_kept_for_inspection_as_failed(armed, fake):
    post_webhook({"hello": "world"})
    with SessionLocal() as s:
        webhooks.process_inbox(s)
        row = s.scalar(select(ZuperWebhookDelivery))
        assert row.status == "failed" and "No record uid" in row.error


def test_quote_and_invoice_deliveries_refresh_the_money_cache(loaded, fake):
    job_uid = uid_of("opportunity", loaded.ids["jane_card"])
    inv = fake.add_invoice(job_uid, status="PAID", total="250.00", balance="0.00")
    post_webhook({"webhook_module": "INVOICE", "data": {"invoice_uid": inv,
                                                        "job": {"job_uid": job_uid}}})
    with SessionLocal() as s:
        webhooks.process_inbox(s)
        doc = s.scalar(select(ZuperDocument))
        assert (doc.kind, doc.total_cents, doc.opportunity_id) == (
            "invoice", 25000, loaded.ids["jane_card"])


# ------------------------------------------------------------------ who sees what

def test_money_panel_for_the_card_and_the_contact(loaded, fake):
    card = loaded.ids["jane_card"]
    job_uid = uid_of("opportunity", card)
    with SessionLocal() as s:
        ctx = engine.Ctx(s)
        engine.pull_document(ctx, "quote", fake.add_estimate(job_uid, status="APPROVED",
                                                              total="100", number="Q-5"))
        engine.pull_document(ctx, "invoice", fake.add_invoice(job_uid, status="AWAIT_PAYMENT",
                                                               total="120", balance="20"))
        s.commit()
    panel = loaded.client("dana").get("/api/opportunities/%d/zuper" % card).json()
    assert panel["linked"] is True and panel["sync"] == "on"
    assert [(q["number"], q["status"], q["total_cents"]) for q in panel["quotes"]] == [
        ("Q-5", "APPROVED", 10000)]
    assert [(i["total_cents"], i["balance_cents"]) for i in panel["invoices"]] == [(12000, 2000)]
    assert panel["value_source"] == "invoices"
    docs = loaded.client("owner").get("/api/contacts/%d/zuper" % loaded.ids["jane"]).json()
    assert {d["opportunity_title"] for d in docs["documents"]} == {"Jane Roof - leak"}


def test_a_restricted_technician_cannot_read_another_customers_money_panel(loaded, fake):
    with SessionLocal() as s:
        ctx = engine.Ctx(s)
        engine.pull_document(ctx, "invoice", fake.add_invoice(
            uid_of("opportunity", loaded.ids["jane_card"]), status="PAID", total="9",
            balance="0"))
        s.commit()
    tess = loaded.client("tess")
    assert tess.get("/api/opportunities/%d/zuper" % loaded.ids["jane_card"]).status_code == 404
    assert tess.get("/api/contacts/%d/zuper" % loaded.ids["jane"]).status_code == 404
    assert tess.get("/api/opportunities/%d/zuper/attachments"
                    % loaded.ids["jane_card"]).status_code == 404
    own = tess.get("/api/opportunities/%d/zuper" % loaded.ids["tim_card"])
    assert own.status_code == 200 and own.json()["invoices"] == []
    assert tess.get("/api/contacts/%d/zuper" % loaded.ids["tim"]).status_code == 200


def test_settings_routes_are_admin_only(loaded, fake):
    for who in ("dana", "tess"):
        c = loaded.client(who)
        for method, path in (("GET", "/api/zuper/status"), ("GET", "/api/zuper/conflicts"),
                             ("GET", "/api/zuper/deletes"), ("POST", "/api/zuper/setup/check"),
                             ("POST", "/api/zuper/deletes/1/restore"),
                             ("PUT", "/api/zuper/setup/confirmations/lead_tag")):
            assert c.request(method, path, json={"confirmed": False}).status_code == 403


def test_status_reports_counts_heartbeat_and_no_secrets(loaded, fake):
    body = loaded.client("owner").get("/api/zuper/status").json()
    assert body["counts"]["contact"] == {"linked": 3}
    assert body["counts"]["opportunity"] == {"linked": 3}
    assert body["sync"] == "on" and body["blockers"] == []
    assert body["load_report"]["verification"]["mismatches"] == 0
    raw = json.dumps(body)
    assert "zk_test" not in raw and "whk_test" not in raw


# ------------------------------------------------------------------ Zuper's photos

def test_zuper_attachments_are_listed_and_relayed_without_the_key_or_url(loaded, fake):
    card = loaded.ids["jane_card"]
    job_uid = uid_of("opportunity", card)
    fake.attachments[job_uid] = [
        {"attachment_uid": "att-1", "file_name": "roof.jpg", "mime_type": "image/jpeg",
         "url": "https://files.zuperpro.com/roof.jpg"},
        {"attachment_uid": "att-2", "file_name": "evil.html", "mime_type": "text/html",
         "url": "https://storage.example-cdn.com/evil.html?sig=abc"}]
    fake.files["https://files.zuperpro.com/roof.jpg"] = (b"JPEGDATA", "image/jpeg")
    c = loaded.client("owner")
    listing = c.get("/api/opportunities/%d/zuper/attachments" % card).json()
    assert listing["state"] == "ok"
    assert [a["url"] for a in listing["attachments"]] == [
        "/api/opportunities/%d/zuper/attachments/att-1" % card,
        "/api/opportunities/%d/zuper/attachments/att-2" % card]
    assert "zuperpro" not in json.dumps(listing) and "example-cdn" not in json.dumps(listing)
    r = c.get("/api/opportunities/%d/zuper/attachments/att-1" % card)
    assert r.status_code == 200 and r.content == b"JPEGDATA"
    assert r.headers["content-type"] == "image/jpeg"
    seen: list[httpx.Request] = []

    def cdn(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host.endswith("zuperpro.com"):
            return fake.handle(request)
        return httpx.Response(200, content=b"<script>x</script>",
                              headers={"content-type": "text/html"})
    client.TRANSPORT = httpx.MockTransport(cdn)
    r = c.get("/api/opportunities/%d/zuper/attachments/att-2" % card)
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"] == "attachment"
    cdn_request = next(q for q in seen if q.url.host == "storage.example-cdn.com")
    assert "x-api-key" not in cdn_request.headers
    assert c.get("/api/opportunities/%d/zuper/attachments/nope" % card).status_code == 404


def test_the_setup_check_route_records_results(zworld, fake):
    from app.zuper import setup
    with SessionLocal() as s:
        setup.ensure_categories(s, commit=True)
    writes = len(fake.writes())
    r = zworld.client("owner").post("/api/zuper/setup/check")
    assert r.status_code == 200
    assert r.json()["passed"] is True
    with SessionLocal() as s:
        assert s.get(ZuperSettings, 1).setup_passed is True
    assert len(fake.writes()) == writes                  # the check itself only reads


def test_contact_sync_never_invents_an_email(loaded, fake):
    with SessionLocal() as s:
        assert s.get(Contact, loaded.ids["bob"]).email is None
    assert fake.customers[uid_of("contact", loaded.ids["bob"])]["customer_email"] == ""


# ------------------------------------------------------------------ region

def test_region_lookup_finds_the_data_centre_without_sending_the_key(zuper_env):
    fake = FakeZuper().install()
    assert client.region_lookup("Dream Team Roofing") == "https://us-east-1.zuperpro.com/api"
    assert fake.violations == []
    with pytest.raises(ZuperError) as exc:
        client.region_lookup("Nobody Roofing")
    assert exc.value.kind == "not_found"


def test_region_lookup_is_off_with_the_env_flag_false(monkeypatch):
    fake = FakeZuper().install()
    with pytest.raises(ZuperError) as exc:
        client.region_lookup("Dream Team Roofing")
    assert exc.value.kind == "off" and fake.requests == []
    with client.operator_mode():
        monkeypatch.setenv("ZUPER_API_KEY", "zk_test")
        assert client.region_lookup("Dream Team Roofing").endswith("zuperpro.com/api")


def test_setup_check_says_which_base_url_to_set_when_the_region_differs(zworld, fake,
                                                                          monkeypatch):
    from app.zuper import setup
    monkeypatch.setenv("ZUPER_COMPANY_NAME", "Dream Team Roofing")
    with SessionLocal() as s, client.operator_mode():
        assert {r["key"]: r["state"] for r in setup.run_checks(s, commit=False)}["region"] \
            == setup.PASS
    fake.dc_api_url = "https://us-west-1c.zuperpro.com"
    with SessionLocal() as s, client.operator_mode():
        region = next(r for r in setup.run_checks(s, commit=False) if r["key"] == "region")
    assert region["state"] == setup.FAIL
    assert "Set ZUPER_BASE_URL=https://us-west-1c.zuperpro.com/api" in region["sentences"][0]


def test_with_a_person_key_that_persons_own_edit_in_zuper_still_syncs(loaded, fake):
    """sync_user_uid is None for a shared key: an event by that person is read and applied."""
    with SessionLocal() as s:
        config.settings(s).sync_user_uid = None
        s.commit()
    uid = uid_of("opportunity", loaded.ids["jane_card"])
    fake.edit_job(uid, job_title="Edited by the owner in Zuper")
    post_webhook({"module": "JOB", "job_uid": uid, "updated_by": {"user_uid": "u-owner"}})
    with SessionLocal() as s:
        webhooks.process_inbox(s)
    with SessionLocal() as s:
        assert s.get(Opportunity, loaded.ids["jane_card"]).title == "Edited by the owner in Zuper"

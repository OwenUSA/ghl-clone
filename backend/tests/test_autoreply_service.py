"""The auto-reply end to end (2026-10-19, app/autoreply): a fake Zuper at the HTTP boundary, the
model scripted at the HTTP boundary (tests/ai_support.py), the real database. Nothing leaves the
process.

Pinned, by behaviour:
  * OFF three ways — the server gate, the Dispatch switch, "Pause all AI agents" — and over the
    daily limit: then NOT ONE request reaches Zuper or the model.
  * In test mode (the default) only the test customer is answered.
  * A reply is sent ONCE: recorded before the send, a second pass sends nothing, a row left
    "sending" by a crash is never retried.
  * Zuper's sender must still be 954-420-7373, or nothing is sent.
  * The model's text goes out when it passes the guards; a price in it, an unreadable answer or
    a provider that is down sends the fixed polite wording instead.
  * What reaches the model has no phone number, email or street address.
  * A private note on the job says what was sent, and "Needs a person" when the customer is upset.
  * The Zuper client lets the send through ONLY inside the auto-reply's scope, only with the
    server gate on, only in its exact shape.
  * Settings: ADMIN only; real customers need the typed "TURN ON", and restart the clock.
"""
import json
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.ai import vault
from app.auth import mint_api_token
from app.autoreply import config as c
from app.autoreply import context, service
from app.db import SessionLocal
from app.main import app
from app.models import AiConnection, AiSettings, AutoReply, DispatchJob, Role, User
from app.zuper import client
from app.zuper import config as zconfig
from fastapi.testclient import TestClient
from tests.ai_support import openai_completion

NOW = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)          # Tue 11 AM New York
TEST_UID = next(iter(c.TEST_CUSTOMERS))
CONV = "conv-test"


def iso(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class FakeConnect:
    """Zuper's conversations, histories, company config, send and notes."""

    def __init__(self):
        self.convs: dict[str, dict] = {}
        self.history: dict[str, list[dict]] = {}
        self.sender = "19544207373"
        self.system_enabled = True
        self.sent: list[dict] = []
        self.notes: list[tuple[str, dict]] = []
        self.requests: list[tuple[str, str]] = []
        self.fail_send = False

    def add(self, uid, customer_uid, name, phone, rows, blocked=False):
        last = max(r["created_at"] for r in rows)
        self.convs[uid] = {
            "conversation_uid": uid, "is_unresponded": True, "recent_conversation_at": last,
            "number": {"number": "19544207373", "display_name": "Primary"},
            "customer_number": {"customer_uid": customer_uid, "customer_full_name": name,
                                "number": "1" + phone, "is_blocked": blocked}}
        self.history[uid] = rows

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))
        body = json.loads(request.content) if request.content else None
        if path.endswith("/telephony/message/conversations"):
            rows = sorted(self.convs.values(), key=lambda x: x["recent_conversation_at"],
                          reverse=True)
            return httpx.Response(200, json={"type": "success", "data": rows,
                                             "total_pages": 1})
        if path.endswith("/telephony/messages"):
            uid = (body.get("filters") or {}).get("conversation_uid")
            rows = sorted(self.history.get(uid, []), key=lambda r: r["created_at"],
                          reverse=True)
            return httpx.Response(200, json={"type": "success", "data": rows})
        if path.endswith("/company/config"):
            return httpx.Response(200, json={"type": "success", "data": {"zuper_connect": {
                "system_notification_number": self.sender,
                "system_notification_enabled": self.system_enabled}}})
        m = re.search(r"/customers/([^/]+)/send$", path)
        if m and request.method == "POST":
            if self.fail_send:
                return httpx.Response(500, json={"type": "error"})
            self.sent.append({"customer_uid": m.group(1), **body})
            return httpx.Response(200, json={"type": "success",
                                             "data": {"message_uid": "sent-%d" % len(self.sent)}})
        m = re.search(r"/jobs/([^/]+)/note$", path)
        if m and request.method == "POST":
            self.notes.append((m.group(1), body))
            return httpx.Response(200, json={"type": "success", "data": {"note_uid": "n1"}})
        return httpx.Response(404, json={"type": "error", "message": "unknown " + path})


def msg(uid, at, *, out=False, body="", kind="SMS", user=None, call=None):
    return {"message_uid": uid, "message_type": kind, "direction": "OUTGOING" if out else
            "INCOMING", "message": body, "status": "DELIVERED", "created_at": iso(at),
            "user": {"first_name": user} if user else None, "call": call}


@pytest.fixture()
def zc(db, monkeypatch, secrets_key):
    monkeypatch.setenv("ZUPER_SYNC_ENABLED", "true")
    monkeypatch.setenv("ZUPER_API_KEY", "zk_test")
    monkeypatch.setenv("ZUPER_PULL_ONLY", "true")
    monkeypatch.setenv("ZUPER_AUTOREPLY_ENABLED", "true")
    monkeypatch.setattr(client, "_sleep", lambda s: None)
    monkeypatch.setattr(zconfig, "armed", lambda db: True)
    fake = FakeConnect()
    client.TRANSPORT = httpx.MockTransport(fake.handler)
    service.reset()
    conn = AiConnection(name="Deepseek", provider="deepseek",
                        api_key_encrypted=vault.encrypt("sk-test-DEEPSEEK-9999"),
                        api_key_last4="9999", default_model="deepseek-flash")
    db.add(conn)
    db.flush()
    s = service.settings(db, for_update=True)
    s.autoreply_enabled, s.autoreply_enabled_at = True, NOW - timedelta(days=1)
    s.autoreply_connection_id = conn.id
    db.add(DispatchJob(job_uid="job-1", job_number="701", board="AHS - Inspection",
                       status="Awaiting AHS Decision", customer_uid=TEST_UID,
                       customer_name="Santiago Villahermosa (TEST)", phones=["9549147244"],
                       address="100 Test St", city="Bradenton", is_open=True,
                       status_since=NOW - timedelta(days=2)))
    db.commit()
    fake.add(CONV, TEST_UID, "Santiago Villahermosa (TEST)", "9549147244", [
        msg("m1", NOW - timedelta(minutes=7),
            body="Hi, any news? I'm at 100 Test St, call me 941-555-0100")])
    return fake


def reply(text, **kw):
    return openai_completion(content=json.dumps(dict(
        {"language": "en", "upset": False, "needs_person": False, "reply": text}, **kw)),
        model="deepseek-flash")


GOOD = ("Hi Santiago, thanks for your message! We sent your inspection report to American Home "
        "Shield and are waiting for their decision. Someone from our team will follow up soon.")


def test_it_answers_the_test_customer_once_and_notes_the_job(db, zc, script):
    script.responses = [reply(GOOD)]
    out = service.run(db, NOW)
    assert out["replied"] == 1, out
    assert zc.sent == [{"customer_uid": TEST_UID, "type": "SMS",
                        "sms": {"body": GOOD, "to": "+19549147244"}}]
    row = db.query(AutoReply).one()
    assert (row.state, row.written_by, row.trigger_uid, row.message_uid, row.note_state) == (
        "sent", "model", "m1", "sent-1", "added")
    job_uid, note = zc.notes[0]
    assert job_uid == "job-1" and GOOD in note["note"]["note"]
    assert note["note"]["is_private"] is True and note["note"]["notify_users"] is False
    # What the model read: the conversation and the job, without the phone or the address.
    sent = json.dumps(script.requests[0]["body"])
    assert "any news" in sent and "Awaiting AHS Decision" in sent
    assert "555-0100" not in sent and "100 Test St" not in sent and "9549147244" not in sent
    assert "sk-test-DEEPSEEK" not in sent
    # Once: the next pass (our reply now in the history) sends nothing.
    zc.history[CONV].append(msg("sent-1", NOW, out=True, body=GOOD, user="Owen"))
    zc.convs[CONV]["recent_conversation_at"] = iso(NOW)
    service.run(db, NOW + timedelta(minutes=10))
    assert len(zc.sent) == 1 and script.calls == 1


def test_a_reply_left_sending_by_a_crash_is_never_retried(db, zc, script):
    db.add(AutoReply(conversation_uid=CONV, trigger_uid="m1", trigger_kind="text",
                     trigger_at=NOW, phone="9549147244", state="sending", reply="x"))
    db.commit()
    script.responses = [reply(GOOD)]
    service.run(db, NOW)
    assert zc.sent == []


def test_test_mode_answers_nobody_else(db, zc, script):
    zc.add("conv-real", "cust-real", "Jane Real", "9415550123",
           [msg("r1", NOW - timedelta(minutes=9), body="Is anyone there?")])
    script.responses = [reply(GOOD)]
    service.run(db, NOW)
    assert [s["customer_uid"] for s in zc.sent] == [TEST_UID]
    assert not any("conv-real" in json.dumps(r) for r in script.requests)


@pytest.mark.parametrize("gate", ["server", "switch", "paused", "cap"])
def test_off_or_over_the_limit_nothing_is_asked_or_sent(db, zc, script, monkeypatch, gate):
    s = service.settings(db, for_update=True)
    if gate == "server":
        monkeypatch.setenv("ZUPER_AUTOREPLY_ENABLED", "false")
    elif gate == "switch":
        s.autoreply_enabled = False
    elif gate == "paused":
        db.add(AiSettings(id=1, paused=True))
    else:
        s.autoreply_daily_cap = 0
    db.commit()
    out = service.run(db, NOW)
    assert out.get("stopped") and zc.requests == [] and script.calls == 0
    assert service.settings(db).autoreply_last_error == out["stopped"]


def test_a_changed_sender_stops_the_send(db, zc, script):
    zc.sender = "18063049902"
    script.responses = [reply(GOOD)]
    out = service.run(db, NOW)
    assert zc.sent == [] and "not (954) 420-7373" in out["stopped"]
    assert db.query(AutoReply).count() == 0          # nothing recorded: it tries again later


@pytest.mark.parametrize("answer", [
    reply("Hi Santiago, your repair will be $450, approved!"),
    openai_completion(content="Sure, here's a text!", model="deepseek-flash"),
    (500, {"error": {"message": "down"}}),
])
def test_a_bad_or_missing_answer_sends_the_fixed_wording(db, zc, script, answer):
    script.responses = [answer]
    service.run(db, NOW)
    body = zc.sent[0]["sms"]["body"]
    assert body.startswith("Hi Santiago, ") and "$" not in body and "approved" not in body
    row = db.query(AutoReply).one()
    assert row.written_by == "template" and row.reason


def test_after_hours_and_no_ai_connection_still_answers_politely(db, zc, script):
    s = service.settings(db, for_update=True)
    s.autoreply_connection_id = None
    db.commit()
    late = datetime(2026, 10, 20, 23, 30, tzinfo=UTC)              # 7:30 PM New York
    zc.history[CONV] = [msg("m2", late - timedelta(minutes=6), body="Water is coming in")]
    zc.convs[CONV]["recent_conversation_at"] = iso(late - timedelta(minutes=6))
    service.run(db, late)
    body = zc.sent[0]["sms"]["body"]
    assert "outside of business hours" in body and script.calls == 0


def test_an_upset_customer_gets_only_the_acknowledgement_and_the_note_says_so(db, zc, script):
    zc.history[CONV] = [msg("m3", NOW - timedelta(minutes=8),
                            body="This is ridiculous, nobody ever calls me back!!")]
    zc.convs[CONV]["recent_conversation_at"] = iso(NOW - timedelta(minutes=8))
    script.responses = [reply("Hi Santiago, we are very sorry. Someone from our team will call "
                              "you shortly.", upset=True, needs_person=True)]
    service.run(db, NOW)
    row = db.query(AutoReply).one()
    assert row.upset and row.needs_person and row.state == "sent"
    assert "Needs a person" in zc.notes[0][1]["note"]["note"]


def test_a_missed_call_with_a_negative_summary_counts_as_upset(db, zc, script):
    call = {"call_uid": "call-1", "direction": "INCOMING", "status": "VOICEMAIL", "duration": 25,
            "call_recordings": [{"recording_url": "https://rec.example.test/1.mp3",
                                 "call_summary": {"summary": "Angry about the delay",
                                                  "sentiment": "negative"}}]}
    zc.history[CONV] = [msg("v1", NOW - timedelta(minutes=8), kind="CALL", call=call)]
    zc.convs[CONV]["recent_conversation_at"] = iso(NOW - timedelta(minutes=8))
    script.responses = [reply("Hi Santiago, thank you for your voicemail. Someone from our team "
                              "will get back to you shortly.")]
    service.run(db, NOW)
    row = db.query(AutoReply).one()
    assert row.trigger_kind == "voicemail" and row.upset and row.needs_person
    assert "Angry about the delay" in json.dumps(script.requests[0]["body"])


def test_recordings_are_transcribed_once_with_the_openai_connection(db, zc, script, monkeypatch):
    from app.models import DispatchCall
    openai = AiConnection(name="OpenAI", provider="openai",
                          api_key_encrypted=vault.encrypt("sk-test-OPENAI-1"),
                          api_key_last4="0001", default_model="gpt-6-luna")
    db.add(openai)
    db.add(DispatchCall(call_uid="call-1", number="9549147244", direction="INCOMING"))
    db.flush()
    service.settings(db, for_update=True).autoreply_transcribe_connection_id = openai.id
    db.commit()
    heard = []
    monkeypatch.setattr(context, "transcribe_audio",
                        lambda prov, url: heard.append(url) or "Please call 941-555-0100 back")
    call = {"call_uid": "call-1", "direction": "INCOMING", "status": "MISSED", "duration": 0,
            "call_recordings": [{"recording_url": "https://rec.example.test/1.mp3"}]}
    zc.history[CONV].insert(0, msg("c1", NOW - timedelta(minutes=30), kind="CALL", call=call))
    script.responses = [reply(GOOD)]
    service.run(db, NOW)
    assert heard == ["https://rec.example.test/1.mp3"]
    assert db.query(DispatchCall).one().transcript == "Please call 941-555-0100 back"
    sent = json.dumps(script.requests[0]["body"])
    assert "Please call [phone] back" in sent


# ---- the Zuper client's scope --------------------------------------------------------------

BODY = {"type": "SMS", "sms": {"body": "Hi", "to": "+19549147244"}}


def test_the_send_is_refused_outside_the_scope_and_with_the_gate_off(zc, monkeypatch):
    with pytest.raises(client.ZuperError, match="never called"):
        client.request("POST", "/customers/c1/send", body=BODY)
    monkeypatch.setenv("ZUPER_AUTOREPLY_ENABLED", "false")
    with client.autoreply_write(), pytest.raises(client.ZuperError, match="switched off"):
        client.request("POST", "/customers/c1/send", body=BODY)
    assert zc.sent == []


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/customers/c1/send", {"type": "EMAIL", "sms": BODY["sms"]}),
    ("POST", "/customers/c1/send", {"type": "SMS", "sms": dict(BODY["sms"], template="x")}),
    ("POST", "/customers/c1/send", {"type": "SMS", "sms": {"body": "Hi", "to": "+44123"}}),
    ("POST", "/customers/c1/send", {"type": "SMS", "sms": {"body": "x" * 500,
                                                           "to": "+19549147244"}}),
    ("PUT", "/jobs", {"job": {"job_uid": "j"}}),
    ("DELETE", "/jobs/j/note/n", None),
    ("POST", "/jobs", {"job": {}}),
])
def test_inside_the_scope_only_the_exact_two_writes(zc, method, path, body):
    with client.autoreply_write(), pytest.raises(client.ZuperError, match="auto-reply"):
        client.request(method, path, body=body)
    assert zc.sent == [] and zc.notes == []


def test_inside_the_scope_the_exact_send_goes_through(zc):
    with client.autoreply_write():
        client.request("POST", "/customers/c1/send", body=BODY)
    assert zc.sent == [dict(customer_uid="c1", **BODY)]


def test_connect_reads_only_the_listed_ones(zc):
    client.connect_read("POST", "/message/conversations", body={})
    client.connect_read("POST", "/messages", body={"filters": {}})
    for method, path in (("POST", "/message/send"), ("POST", "/message"),
                         ("PUT", "/message/conversation/x"), ("POST", "/sms")):
        with pytest.raises(client.ZuperError, match="refused"):
            client.connect_read(method, path, body={})


# ---- settings ------------------------------------------------------------------------------

def _client(db, role):
    u = User(email="%s@x.test" % role.value.lower(), name=role.value.title(), role=role,
             password_hash="x")
    db.add(u)
    db.flush()
    plain, tok = mint_api_token(u, name="test")
    db.add(tok)
    db.commit()
    tc = TestClient(app)
    tc.headers["Authorization"] = "Bearer " + plain
    return tc


def test_settings_admin_only_and_real_customers_need_turn_on(db, zc):
    tech, disp, admin = (_client(db, r) for r in (Role.TECH, Role.DISPATCHER, Role.ADMIN))
    assert tech.get("/api/autoreply").status_code == 403
    got = disp.get("/api/autoreply")
    assert got.status_code == 200 and got.json()["test_only"] is True
    assert got.json()["connections"] == []                    # connections are ADMIN's
    assert disp.put("/api/autoreply/settings", json={"daily_cap": 5}).status_code == 403
    r = admin.put("/api/autoreply/settings", json={"test_only": False})
    assert r.status_code == 400 and "TURN ON" in r.text
    with SessionLocal() as s:
        assert service.settings(s).autoreply_test_only is True
    r = admin.put("/api/autoreply/settings", json={"test_only": False, "confirm": "TURN ON"})
    assert r.status_code == 200 and r.json()["test_only"] is False
    with SessionLocal() as s:
        at = c.aware(service.settings(s).autoreply_enabled_at)
    assert datetime.now(UTC) - at < timedelta(minutes=1)      # the clock restarted

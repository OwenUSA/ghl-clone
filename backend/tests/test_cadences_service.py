"""Follow-ups end to end (app/cadences): a fake Zuper at the HTTP boundary, owen-main's call request
replaced by a recorder, the real database and the real API. Nothing leaves the process.

Pinned, by behaviour:
  * Off unless FOLLOWUPS_ENABLED; with the old CALL_FOLLOWUP_ENABLED also set NEITHER runs.
  * An unanswered person's call to a Zuper customer with an open job starts ONE follow-up on
    that board's sequence, assigned to the caller; a closed job or a non-test number does not.
  * Shadow (the default) records the text and the AI call and sends nothing.
  * Automatic: ONE text in the exact send shape + a private note; ONE AI call; never repeated.
  * The customer's text or call back stops it and rings the assignee; a CSR's text does not.
  * A "call me" step rings once, then waits until the person calls.
  * A reply that arrives just before a send stops it: nothing is sent.
  * Outside 8 AM-8 PM nothing leaves; the 24-hour rule skips a text.
  * The API: a technician is refused; one open follow-up per number; stop needs a reason;
    only an admin edits a sequence, and a bad one is refused.
"""
from datetime import timedelta

import httpx
import pytest
from app import crmlink
from app.auth import mint_api_token
from app.cadences import service
from app.main import app
from app.models import (
    AgentPermission,
    AiAlert,
    AutoReply,
    CallFollowup,
    DispatchCall,
    DispatchJob,
    Followup,
    FollowupEvent,
    Role,
    User,
)
from app.zuper import client
from app.zuper import config as zconfig
from fastapi.testclient import TestClient
from tests.test_autoreply_service import NOW, TEST_UID, FakeConnect, msg

PHONE = "9549147244"


@pytest.fixture()
def fu(db, monkeypatch):
    monkeypatch.setenv("ZUPER_SYNC_ENABLED", "true")
    monkeypatch.setenv("ZUPER_API_KEY", "zk_test")
    monkeypatch.setenv("ZUPER_PULL_ONLY", "true")
    monkeypatch.setenv("FOLLOWUPS_ENABLED", "true")
    monkeypatch.delenv("CALL_FOLLOWUP_ENABLED", raising=False)
    monkeypatch.delenv("ZUPER_AUTOREPLY_ENABLED", raising=False)
    monkeypatch.setattr(client, "_sleep", lambda s: None)
    fake = FakeConnect()
    client.TRANSPORT = httpx.MockTransport(fake.handler)
    calls = []
    monkeypatch.setattr(crmlink, "configured", lambda: True)
    monkeypatch.setattr(crmlink, "agent_call", lambda *a, **k: calls.append((a, k)) or
                        crmlink.LinkResult(True, 202, "", {"call_id": "agent-outbound-1"}))
    fake.calls = calls
    db.add(DispatchJob(job_uid="job-1", job_number="701", board="AHS - Inspection",
                       status="Scheduled", status_type="ESTIMATING", customer_uid=TEST_UID,
                       customer_name="Santiago Villahermosa (TEST)", phones=[PHONE],
                       is_open=True, status_since=NOW - timedelta(days=2)))
    db.add(User(email="luis@x.test", name="Luis Perez", role=Role.DISPATCHER,
                password_hash="x"))
    db.commit()
    return fake


def team_call(db, at, number=PHONE, status="NO_ANSWER", direction="OUTGOING", secs=0, uid="c1"):
    db.add(DispatchCall(call_uid=uid, occurred_at=at, direction=direction, status=status,
                        duration_seconds=secs, number="+1" + number, staff_name="Luis"))
    db.commit()


def auto(db, *actions):
    for a in actions or ("send_text", "place_calls"):
        db.add(AgentPermission(entry_key="followups", action=a, level="auto",
                               changed_at=NOW - timedelta(days=1)))
    db.commit()


def kinds(db, row):
    return [e.kind for e in db.query(FollowupEvent).filter_by(followup_id=row.id)
            .order_by(FollowupEvent.at, FollowupEvent.id)]


def connect_says(fu, *rows):
    fu.add("conv-1", TEST_UID, "Santiago Villahermosa (TEST)", PHONE, list(rows))


# ---- the gate --------------------------------------------------------------------------------

def test_off_on_the_server_does_nothing(db, fu, monkeypatch):
    monkeypatch.setenv("FOLLOWUPS_ENABLED", "false")
    team_call(db, NOW - timedelta(minutes=3))
    out = service.run(db, NOW)
    assert "FOLLOWUPS_ENABLED" in out["halted"]
    assert fu.requests == [] and db.query(Followup).count() == 0


def test_never_together_with_the_old_call_follow_up(db, fu, monkeypatch):
    monkeypatch.setenv("CALL_FOLLOWUP_ENABLED", "true")
    team_call(db, NOW - timedelta(minutes=3))
    out = service.run(db, NOW)
    assert "old automatic call follow-up" in out["halted"]
    assert not zconfig.followup_enabled() and not zconfig.followups_enabled()
    assert db.query(Followup).count() == 0
    with client.followups_write(), pytest.raises(client.ZuperError, match="refused"):
        client.request("POST", "/customers/c1/send", body={
            "type": "SMS", "sms": {"body": "x", "to": "+1" + PHONE}})


# ---- starting --------------------------------------------------------------------------------

def test_an_unanswered_call_starts_one_follow_up_assigned_to_the_caller(db, fu):
    team_call(db, NOW - timedelta(minutes=3))
    assert service.run(db, NOW)["started"] == 1
    assert service.run(db, NOW + timedelta(seconds=30))["started"] == 0
    row = db.query(Followup).one()
    luis = db.query(User).filter_by(email="luis@x.test").one()
    assert (row.phone, row.source, row.board, row.job_number) == (
        PHONE, "auto", "AHS - Inspection", "701")
    assert row.sequence_name == "AHS - Inspection" and row.assigned_user_id == luis.id
    assert row.next_at.replace(tzinfo=None) == (NOW + timedelta(minutes=5)).replace(tzinfo=None)
    assert kinds(db, row) == ["team_call", "started"]


def test_a_closed_job_or_a_number_outside_the_test_list_does_not_start(db, fu):
    db.query(DispatchJob).one().status_type = "CANCELED"
    db.commit()
    team_call(db, NOW - timedelta(minutes=3))
    assert service.run(db, NOW)["started"] == 0
    team_call(db, NOW - timedelta(minutes=2), number="3055550101", uid="c2")
    db.add(DispatchJob(job_uid="job-2", board="Retail", status="New Lead", status_type="NEW",
                       customer_uid="real-1", customer_name="Real Person",
                       phones=["3055550101"], is_open=True))
    db.commit()
    assert service.run(db, NOW)["started"] == 0


# ---- shadow and automatic -----------------------------------------------------------------------

def test_shadow_records_the_text_and_the_ai_call_and_sends_nothing(db, fu):
    team_call(db, NOW - timedelta(minutes=3))
    service.run(db, NOW)
    out = service.run(db, NOW + timedelta(minutes=6))
    assert out["texted"] == 1, out
    row = db.query(Followup).one()
    shadow = db.query(FollowupEvent).filter_by(followup_id=row.id, kind="text").one()
    assert shadow.state == "shadow" and "Hi Santiago" in shadow.text
    assert row.step_index == 1 and fu.sent == [] and fu.notes == []
    # Skip the "call me" step, then the AI call comes due the day after at 2 PM.
    service.act(db, row, "skip", NOW + timedelta(minutes=7), by="Test")
    db.commit()
    out = service.run(db, row.next_at + timedelta(minutes=1))
    assert out["called"] == 1 and fu.calls == []
    assert db.query(FollowupEvent).filter_by(kind="ai_call").one().state == "shadow"


def test_automatic_texts_once_in_the_exact_shape_with_a_note(db, fu):
    auto(db)
    team_call(db, NOW - timedelta(minutes=3))
    service.run(db, NOW)
    service.run(db, NOW + timedelta(minutes=6))
    assert len(fu.sent) == 1
    sent = fu.sent[0]
    assert sent["customer_uid"] == TEST_UID and sent["type"] == "SMS"
    assert sent["sms"]["to"] == "+1" + PHONE and sent["sms"]["body"].startswith("Hi Santiago")
    assert fu.notes and fu.notes[0][0] == "job-1" and fu.notes[0][1]["note"]["is_private"]
    # Zuper shows our own text back in the conversation: neither a stop nor a team attempt.
    connect_says(fu, msg("m1", NOW + timedelta(minutes=6), out=True, body=sent["sms"]["body"]))
    service.run(db, NOW + timedelta(minutes=8))
    row = db.query(Followup).one()
    assert row.state == "open" and "team_text" not in kinds(db, row)
    service.run(db, NOW + timedelta(minutes=30))
    assert len(fu.sent) == 1


def test_automatic_ai_call_is_requested_once(db, fu):
    auto(db)
    team_call(db, NOW - timedelta(minutes=3))
    service.run(db, NOW)
    service.run(db, NOW + timedelta(minutes=6))
    row = db.query(Followup).one()
    service.act(db, row, "skip", NOW + timedelta(minutes=7), by="Test")
    db.commit()
    due = row.next_at
    service.run(db, due + timedelta(minutes=1))
    service.run(db, due + timedelta(minutes=2))
    assert len(fu.calls) == 1
    args, kw = fu.calls[0]
    assert args[0] == "+1" + PHONE and args[1] == "DTR Retell - Brian"
    assert kw["first_name"] == "Santiago"
    assert db.query(FollowupEvent).filter_by(kind="ai_call").one().state == "requested"


# ---- stopping ----------------------------------------------------------------------------------

def test_a_customer_text_stops_it_and_rings_the_assignee(db, fu):
    auto(db)
    team_call(db, NOW - timedelta(minutes=3))
    service.run(db, NOW)
    connect_says(fu, msg("m1", NOW + timedelta(minutes=2), body="Who is this?"))
    out = service.run(db, NOW + timedelta(minutes=3))
    row = db.query(Followup).one()
    assert out["stopped"] == 1 and row.state == "stopped"
    assert "texted back" in row.stop_reason and fu.sent == []
    luis = db.query(User).filter_by(email="luis@x.test").one()
    alert = db.query(AiAlert).filter_by(kind="followup_reply").one()
    assert alert.user_id == luis.id and alert.urgent


def test_a_call_back_stops_it(db, fu):
    team_call(db, NOW - timedelta(minutes=3))
    service.run(db, NOW)
    team_call(db, NOW + timedelta(minutes=2), direction="INCOMING", status="COMPLETED", secs=40,
              uid="c2")
    service.run(db, NOW + timedelta(minutes=3))
    row = db.query(Followup).one()
    assert row.state == "stopped" and "called back" in row.stop_reason


def test_a_csr_text_is_recorded_and_does_not_stop_it(db, fu):
    team_call(db, NOW - timedelta(minutes=3))
    service.run(db, NOW)
    connect_says(fu, msg("m1", NOW + timedelta(minutes=1), out=True, body="Call us back",
                         user="Luis"))
    service.run(db, NOW + timedelta(minutes=2))
    row = db.query(Followup).one()
    assert row.state == "open" and "team_text" in kinds(db, row)
    # A new unanswered call by the team is an attempt on the same follow-up, not a new one.
    team_call(db, NOW + timedelta(minutes=3), uid="c2")
    service.run(db, NOW + timedelta(minutes=4))
    assert db.query(Followup).count() == 1 and kinds(db, row).count("team_call") == 2


def test_a_reply_just_before_the_send_stops_it_and_nothing_is_sent(db, fu):
    auto(db)
    team_call(db, NOW - timedelta(minutes=3))
    service.run(db, NOW)
    row = db.query(Followup).one()
    # The inbox read said nothing new; the fresh read right before the send finds the reply.
    row.connect_seen_at = NOW + timedelta(days=1)
    db.commit()
    connect_says(fu, msg("m1", NOW + timedelta(minutes=5), body="Yes call me"))
    fu.convs["conv-1"]["recent_conversation_at"] = (NOW - timedelta(days=2)).isoformat()
    service.run(db, NOW + timedelta(minutes=6))
    db.refresh(row)
    assert row.state == "stopped" and fu.sent == []


# ---- call me, hours, the 24-hour rule ------------------------------------------------------------

def test_a_call_me_step_rings_once_and_waits_for_the_person(db, fu):
    team_call(db, NOW - timedelta(minutes=3))
    service.run(db, NOW)
    service.run(db, NOW + timedelta(minutes=6))                 # shadow text
    row = db.query(Followup).one()
    due = row.next_at
    service.run(db, due + timedelta(minutes=1))
    service.run(db, due + timedelta(minutes=30))
    assert db.query(AiAlert).filter_by(kind="followup_call_due").count() == 1
    assert row.step_index == 1
    team_call(db, due + timedelta(minutes=40), uid="c3")       # Luis calls, no answer
    service.run(db, due + timedelta(minutes=41))
    db.refresh(row)
    assert row.step_index == 2 and row.state == "open"


def test_nothing_leaves_outside_the_send_window(db, fu):
    auto(db)
    night = NOW + timedelta(hours=11)                          # Tue 10 PM New York
    team_call(db, night - timedelta(minutes=3))
    service.run(db, night)
    service.run(db, night + timedelta(minutes=6))
    row = db.query(Followup).one()
    assert fu.sent == [] and row.step_index == 0
    assert service.rules.sending_hours(row.next_at)


def test_a_number_texted_automatically_in_the_last_day_skips_the_first_text(db, fu):
    auto(db)
    db.add(AutoReply(conversation_uid="cv", trigger_uid="t", trigger_kind="text",
                     trigger_at=NOW - timedelta(hours=3), phone=PHONE, state="sent",
                     sent_at=NOW - timedelta(hours=3)))
    db.commit()
    team_call(db, NOW - timedelta(minutes=3))
    service.run(db, NOW)
    row = db.query(Followup).one()
    assert row.step_index == 1 and fu.sent == []
    assert db.query(CallFollowup).count() == 0


# ---- the API ------------------------------------------------------------------------------------

def _client(db, role, email=None):
    u = User(email=email or "%s@x.test" % role.value.lower(), name=role.value.title(),
             role=role, password_hash="x")
    db.add(u)
    db.flush()
    plain, tok = mint_api_token(u, name="test")
    db.add(tok)
    db.commit()
    tc = TestClient(app)
    tc.headers["Authorization"] = "Bearer " + plain
    return tc


def test_the_api(db, fu):
    tech, disp, admin = (_client(db, r) for r in (Role.TECH, Role.DISPATCHER, Role.ADMIN))
    assert tech.get("/api/followups").status_code == 403
    assert tech.post("/api/followups", json={"phone": PHONE}).status_code == 403
    r = disp.post("/api/followups", json={"phone": "(954) 914-7244", "note": "call Friday",
                                          "start_at": "2026-10-23T14:00:00+00:00"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["sequence_name"] == "AHS - Inspection" and body["assigned_label"] == "Dispatcher"
    assert body["next_at"].startswith("2026-10-23T14:05")
    again = disp.post("/api/followups", json={"phone": PHONE})
    assert again.status_code == 409 and again.json()["detail"]["followup_id"] == body["id"]
    fid = body["id"]
    assert disp.get("/api/followups/by-phone", params={"phone": PHONE}).json()[
        "followup"]["id"] == fid
    assert disp.post("/api/followups/%d/action" % fid, json={"action": "stop"}).status_code == 409
    r = disp.post("/api/followups/%d/action" % fid, json={"action": "stop",
                                                           "reason": "not interested"})
    assert r.status_code == 200 and r.json()["state"] == "stopped"
    assert disp.get("/api/followups", params={"show": "closed"}).json()["followups"][0][
        "id"] == fid
    seqs = disp.get("/api/followups/sequences").json()["sequences"]
    assert {s["name"] for s in seqs} == {"AHS Simple Flow", "AHS - Inspection",
                                         "AHS - Repair & Review", "Retail", "General"}
    one = seqs[0]
    edit = {"name": one["name"], "board": one["board"], "is_default": True, "active": True,
            "steps": [{"type": "call_me", "delay_minutes": 30}]}
    assert disp.put("/api/followups/sequences/%d" % one["id"], json=edit).status_code == 403
    bad = dict(edit, steps=[{"type": "text", "delay_minutes": 1, "template": "Hi {name}"}])
    assert admin.put("/api/followups/sequences/%d" % one["id"], json=bad).status_code == 422
    ok = admin.put("/api/followups/sequences/%d" % one["id"], json=edit)
    assert ok.status_code == 200 and ok.json()["labels"] == ["Call (me), 30 min after"]


def test_a_crash_right_after_the_send_never_sends_twice(db, fu, monkeypatch):
    """The step moves on in the SAME commit that records "sending": a pass that dies after Zuper
    took the text leaves nothing to send again."""
    auto(db)
    team_call(db, NOW - timedelta(minutes=3))
    service.run(db, NOW)
    real = service.zconnect.send

    def send_then_die(*a, **k):
        real(*a, **k)
        raise RuntimeError("the worker died here")

    monkeypatch.setattr(service.zconnect, "send", send_then_die)
    service.run(db, NOW + timedelta(minutes=6))
    monkeypatch.setattr(service.zconnect, "send", real)
    service.run(db, NOW + timedelta(minutes=7))
    service.run(db, NOW + timedelta(minutes=8))
    assert len(fu.sent) == 1
    row = db.query(Followup).one()
    assert row.step_index == 1

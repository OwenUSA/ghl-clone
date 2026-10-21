"""The call follow-up end to end (app/followup): a fake Zuper at the HTTP boundary, owen-main's
call request replaced by a recorder, the real database. Nothing leaves the process.

Pinned, by behaviour:
  * Off unless CALL_FOLLOWUP_ENABLED: then not one request, not one row.
  * Shadow (the default): the text and the call are recorded, and NOTHING is sent or dialled.
  * Automatic: ONE text through Zuper in the exact send shape + a private job note, then after
    the wait ONE call request to owen-main from the configured number - never repeated.
  * A customer reached in the meantime (answered call, call back) stops it.
  * Test only: a number that is not a test customer's is ignored.
  * Our own AI call reported back unanswered never starts another follow-up.
  * The Zuper send scope opens only under its own gate.
"""
from datetime import timedelta

import httpx
import pytest
from app import crmlink
from app.followup import service
from app.models import AgentPermission, CallFollowup, DispatchCall, DispatchJob
from app.registry import service as registry
from app.zuper import client
from tests.test_autoreply_service import NOW, TEST_UID, FakeConnect

PHONE = "9549147244"


@pytest.fixture()
def fu(db, monkeypatch):
    monkeypatch.setenv("ZUPER_SYNC_ENABLED", "true")
    monkeypatch.setenv("ZUPER_API_KEY", "zk_test")
    monkeypatch.setenv("ZUPER_PULL_ONLY", "true")
    monkeypatch.setenv("CALL_FOLLOWUP_ENABLED", "true")
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
                       status="Scheduled", customer_uid=TEST_UID,
                       customer_name="Santiago Villahermosa (TEST)", phones=[PHONE],
                       is_open=True, status_since=NOW - timedelta(days=2)))
    db.commit()
    return fake


def team_call(db, at, number=PHONE, status="NO_ANSWER", direction="OUTGOING", secs=0, uid="c1"):
    db.add(DispatchCall(call_uid=uid, occurred_at=at, direction=direction, status=status,
                        duration_seconds=secs, number="+1" + number, staff_name="Luis"))
    db.commit()


def auto(db):
    for a in ("send_text", "place_calls"):
        db.add(AgentPermission(entry_key="call_followup", action=a, level="auto",
                               changed_at=NOW - timedelta(days=1)))
    db.commit()


def test_off_on_the_server_does_nothing(db, fu, monkeypatch):
    monkeypatch.setenv("CALL_FOLLOWUP_ENABLED", "false")
    team_call(db, NOW - timedelta(minutes=5))
    out = service.run(db, NOW)
    assert "CALL_FOLLOWUP_ENABLED" in out["stopped"]
    assert fu.requests == [] and fu.calls == [] and db.query(CallFollowup).count() == 0


def test_shadow_records_the_text_and_the_call_and_sends_nothing(db, fu):
    team_call(db, NOW - timedelta(minutes=3))
    out = service.run(db, NOW)
    assert out["started"] == 1 and out["texted"] == 1, out
    row = db.query(CallFollowup).one()
    assert (row.text_state, row.customer_uid, row.job_number) == ("shadow", TEST_UID, "701")
    assert row.text_body.startswith("Hi Santiago, this is Dream Team Roofing.")
    service.run(db, NOW + timedelta(minutes=11))
    db.refresh(row)
    assert (row.call_state, row.state) == ("shadow", "done")
    assert fu.sent == [] and fu.notes == [] and fu.calls == []
    texts = [x["text"] for x in registry.activity(db, key="call_followup")]
    assert any("Would have texted Santiago" in t for t in texts)
    assert any("Would have had Anisa call" in t for t in texts)


def test_automatic_texts_once_then_calls_once(db, fu):
    auto(db)
    team_call(db, NOW - timedelta(minutes=3))
    service.run(db, NOW)
    assert len(fu.sent) == 1
    sent = fu.sent[0]
    assert sent["customer_uid"] == TEST_UID and sent["type"] == "SMS"
    assert sent["sms"]["to"] == "+1" + PHONE and sent["sms"]["body"].startswith("Hi Santiago")
    assert fu.notes and fu.notes[0][0] == "job-1" and fu.notes[0][1]["note"]["is_private"]
    service.run(db, NOW + timedelta(minutes=5))                    # still waiting
    assert fu.calls == [] and len(fu.sent) == 1
    service.run(db, NOW + timedelta(minutes=13))
    assert len(fu.calls) == 1
    args, kw = fu.calls[0]
    assert args[0] == "+1" + PHONE and args[1] == "DTR Retell - Brian"
    assert kw["from_number"] == "+19547758492" and kw["first_name"] == "Santiago"
    row = db.query(CallFollowup).one()
    assert (row.call_state, row.call_ref, row.state) == ("requested", "agent-outbound-1", "done")
    # Never again for this call - and our own unanswered AI call is not a new trigger.
    db.add(DispatchCall(call_uid="ours", occurred_at=NOW + timedelta(minutes=13, seconds=30),
                        direction="OUTGOING", status="NO_ANSWER", duration_seconds=0,
                        number="+1" + PHONE))
    db.commit()
    service.run(db, NOW + timedelta(minutes=40))
    assert len(fu.calls) == 1 and len(fu.sent) == 1


def test_a_reached_customer_is_left_alone(db, fu):
    auto(db)
    team_call(db, NOW - timedelta(minutes=3))
    service.run(db, NOW)
    team_call(db, NOW + timedelta(minutes=4), direction="INCOMING", status="COMPLETED", secs=60,
              uid="c2")
    service.run(db, NOW + timedelta(minutes=15))
    assert fu.calls == []
    row = db.query(CallFollowup).one()
    assert row.state == "done" and "answered" in row.reason


def test_test_only_ignores_everyone_else(db, fu):
    auto(db)
    team_call(db, NOW - timedelta(minutes=3), number="9415550123", uid="real")
    service.run(db, NOW)
    service.run(db, NOW + timedelta(minutes=15))
    assert db.query(CallFollowup).count() == 0 and fu.sent == [] and fu.calls == []


def test_the_send_scope_needs_its_own_gate(monkeypatch, fu):
    from app.followup import zconnect
    monkeypatch.setenv("CALL_FOLLOWUP_ENABLED", "false")
    monkeypatch.setenv("ZUPER_AUTOREPLY_ENABLED", "true")      # the OTHER feature's gate
    with pytest.raises(client.ZuperError):
        zconnect.send(TEST_UID, PHONE, "hello")
    with pytest.raises(client.ZuperError):                    # outside any scope: denylisted
        client.request("POST", "/customers/%s/send" % TEST_UID,
                       body={"type": "SMS", "sms": {"body": "x", "to": "+1" + PHONE}})
    assert fu.sent == []


def test_settings_are_validated_and_logged(world):
    a = world.client("admin")
    r = a.patch("/api/ai/registry/entries/call_followup", json={"config": {"wait_minutes": 0}})
    assert r.status_code == 400 and "between 1 and 240" in r.json()["detail"]
    r = a.patch("/api/ai/registry/entries/call_followup", json={"config": {
        "wait_minutes": 15, "test_phones": ["561-878-8090", "954 914 7244"]}})
    assert r.status_code == 200, r.text
    s = r.json()["settings"]
    assert s["wait_minutes"] == 15 and s["test_phones"] == ["5618788090", "9549147244"]
    assert s["test_only"] is True
    r = a.patch("/api/ai/registry/entries/autoreply", json={"config": {"x": 1}})
    assert r.status_code == 400 and "no settings" in r.json()["detail"]

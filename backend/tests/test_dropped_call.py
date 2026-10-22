"""The text to a caller who left an AI call before being helped (app/dropped_call.py).

A caller who hung up on Anisa gets ONE text from the Zuper line (a Zuper customer) or the CRM
line (anyone else); a call the AI reads as handled gets none; a transfer a person took gets
none without asking the AI; never twice a day, never after STOP; a call at night waits for 8 AM;
off without the server switch.
"""
import json
from datetime import timedelta

import httpx
import pytest
from app import automations, dropped_call, number_threads
from app.ai import vault
from app.dispatch import ai as dai
from app.models import (
    AgentRegistryLog,
    AiConnection,
    Direction,
    DispatchJob,
    EventType,
    Job,
    NumberThreadEvent,
)
from app.zuper import client
from tests.ai_support import openai_completion
from tests.test_autoreply_service import NOW, TEST_UID, FakeConnect

PHONE = "9415550101"
LEFT = {"summary": "The caller hung up immediately after the agent began speaking.",
        "disconnection_reason": "user_hangup", "successful": False}


@pytest.fixture()
def dc(db, monkeypatch, secrets_key):
    monkeypatch.setenv("DROPPED_CALL_TEXTS_ENABLED", "true")
    monkeypatch.setenv("ZUPER_SYNC_ENABLED", "true")
    monkeypatch.setenv("ZUPER_API_KEY", "zk_test")
    monkeypatch.setenv("ZUPER_PULL_ONLY", "true")
    monkeypatch.setattr(client, "_sleep", lambda s: None)
    fake = FakeConnect()
    client.TRANSPORT = httpx.MockTransport(fake.handler)
    conn = AiConnection(name="Deepseek", provider="deepseek",
                        api_key_encrypted=vault.encrypt("sk-2"), api_key_last4="2",
                        default_model="deepseek-flash")
    db.add(conn)
    db.flush()
    s = dai.settings(db, for_update=True)
    s.ai_enabled, s.connection_id, s.model = True, conn.id, "deepseek-flash"
    db.add(DispatchJob(job_uid="job-1", job_number="701", board="AHS - Inspection",
                       status="Scheduled", customer_uid=TEST_UID, customer_name="Ana Smith",
                       phones=[PHONE], is_open=True))
    db.commit()
    return fake


def call(db, ai: dict, phone: str = PHONE, at=NOW) -> dict:
    thread, _ = number_threads.thread_for_number(db, "+1" + phone)
    ev = NumberThreadEvent(number_thread_id=thread.id, type=EventType.CALL,
                           direction=Direction.INBOUND, occurred_at=at - timedelta(minutes=4),
                           ai_call=ai)
    db.add(ev)
    db.commit()
    return {"event_id": ev.id, "event_kind": "number", "event_key": "n%d" % ev.id,
            "caller_number": "+1" + phone}


def verdict(text: bool):
    return openai_completion(content=json.dumps({"text": text, "why": "test"}),
                             model="deepseek-flash")


def test_a_caller_who_left_gets_one_text_from_the_zuper_line(db, dc, script):
    script.responses = [verdict(True)]
    dropped_call._run(db, call(db, LEFT), NOW)
    assert [s["sms"]["body"] for s in dc.sent] == [dropped_call.TEXT]
    assert dc.notes and "left before being helped" in dc.notes[0][1]["note"]["note"]
    script.responses = [verdict(True)]
    dropped_call._run(db, call(db, LEFT), NOW + timedelta(hours=1))
    assert len(dc.sent) == 1                                      # never twice a day
    assert db.query(AgentRegistryLog).filter(
        AgentRegistryLog.reason == "already texted today").count() == 1


def test_a_handled_call_and_a_person_s_transfer_get_nothing(db, dc, script):
    script.responses = [verdict(False)]
    dropped_call._run(db, call(db, {**LEFT, "summary": "Joyce left a message; done."}), NOW)
    taken = {**LEFT, "transfer": {"target": "office", "outcome": "answered",
                                  "returned_to_agent": False}}
    dropped_call._run(db, call(db, taken), NOW)
    assert dc.sent == [] and script.calls == 1                    # no AI asked for the 2nd


def test_stop_is_respected(db, dc, script):
    thread, _ = number_threads.thread_for_number(db, "+1" + PHONE)
    db.add(NumberThreadEvent(number_thread_id=thread.id, type=EventType.SMS,
                             direction=Direction.INBOUND, occurred_at=NOW - timedelta(days=3),
                             body="STOP"))
    db.commit()
    script.responses = [verdict(True)]
    dropped_call._run(db, call(db, LEFT), NOW)
    assert dc.sent == []


def test_a_call_at_night_waits_for_8_am(db, dc, script):
    night = NOW.replace(hour=3)                                   # 11 PM New York
    dropped_call._run(db, call(db, LEFT, at=night), night)
    job = db.query(Job).filter(Job.type == dropped_call.JOB_TYPE).one()
    assert dc.sent == [] and script.calls == 0
    assert (job.run_after.hour, job.run_after.minute) == (12, 1)  # 8:01 AM New York


def test_a_caller_zuper_does_not_know_gets_it_from_the_crm_line(db, dc, script, monkeypatch):
    sent = []
    monkeypatch.setattr(automations, "send_outbound_to_number",
                        lambda db_, thread, body, **k: sent.append(body) or (None, "queued"))
    script.responses = [verdict(True)]
    dropped_call._run(db, call(db, LEFT, phone="9415550199"), NOW)
    assert sent == [dropped_call.TEXT] and dc.sent == []


def test_off_without_the_server_switch(db, dc, monkeypatch):
    monkeypatch.delenv("DROPPED_CALL_TEXTS_ENABLED")
    thread, _ = number_threads.thread_for_number(db, "+1" + PHONE)
    ev = NumberThreadEvent(number_thread_id=thread.id, type=EventType.CALL,
                           direction=Direction.INBOUND, occurred_at=NOW, ai_call=LEFT)
    db.add(ev)
    db.flush()
    assert dropped_call.maybe_enqueue(db, ev, "+1" + PHONE) == "off"

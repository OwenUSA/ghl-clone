"""The office's text after an AI voice-agent call (2026-10-06, app/ai_call_notify.py).

The one exception to "no automatic texts" — and only because it texts the OFFICE. What is
pinned here is behaviour, not status codes: who receives it (only the setting's numbers, never
a contact's, never the caller), what it says (a new lead's every field; an existing customer's
short note), the link (absolute, on CRM_PUBLIC_URL, only the CRM's own relay), and that a call
reported three times is still ONE queued job and ONE text.
"""
from datetime import timedelta

import pytest
from app import ai_call_notify, automations, worker
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.models import (
    Contact,
    DeliveryStatus,
    Direction,
    Job,
    NumberThread,
    NumberThreadEvent,
    Role,
    User,
)
from app.queue import utcnow
from app.transport import MessageRef
from fastapi.testclient import TestClient
from sqlalchemy import func, select

OFFICE = "+19549147244"
STRANGER = "+18135550142"
KNOWN = "+19415550123"
BASE = "https://crm.example.test"
REC = "/api/owen/recordings/owen-call-7781"

CAPTURED = {
    "name": "Maria Ruiz", "second_phone": "+18135550199", "email": "maria@example.test",
    "address": "412 Palm Ave", "city": "Bradenton", "need": "active leak over the kitchen",
    "leak_count": 2, "leak_location": "kitchen ceiling", "started": "last night",
    "roof_material": "shingle", "roof_age": "15 years", "previous_repairs": "none",
    "stories": 1, "owner_or_tenant": "owner", "payment_type": "AHS",
    "ahs_dispatch_number": "D-55821", "first_time_ahs": True,
    "preferred_inspection_time": "tomorrow morning", "access_notes": "gate code 1234",
    "urgency": "emergency", "notes": "dog in the yard", "pool_cage": "yes",
}


class Recorder:
    def __init__(self):
        self.sent = []

    def send_sms(self, to, body, from_number, **kw):
        self.sent.append({"to": to, "body": body, **kw})
        return MessageRef(provider_ref="rec-%d" % len(self.sent),
                          status=DeliveryStatus.LOGGED_ONLY)


@pytest.fixture()
def office(monkeypatch):
    monkeypatch.setenv(ai_call_notify.NUMBERS_ENV, OFFICE)
    monkeypatch.setenv(ai_call_notify.PUBLIC_URL_ENV, BASE + "/")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    feed = User(email="owen@notify.test", name="OWEN feed", role=Role.DISPATCHER)
    db.add(feed)
    known = Contact(first_name="Maria", last_name="Lopez", phone=KNOWN, created_by="Manual")
    db.add(known)
    db.flush()
    plain, tok = mint_api_token(feed, name="feed", scopes="events:write read")
    db.add(tok)
    db.commit()
    known_id = known.id
    db.close()
    rec = Recorder()
    monkeypatch.setattr(automations, "get_transport", lambda: rec)
    with TestClient(app_()) as c:
        c.h = {"Authorization": "Bearer " + plain}
        c.rec = rec
        c.known_id = known_id
        yield c


def app_():
    from app.main import app
    return app


def call(c, **over):
    body = {"type": "CALL", "direction": "INBOUND", "from_number": STRANGER,
            "call_status": "completed", "duration_seconds": 96,
            "provider_ref": "owen-call-7781", "source_number": "+19546859990",
            "dedupe_key": "owen:agent:7781",
            "ai_call": {"agent": "Intake", "engine": "retell", "retell_call_id": "call_r1"}}
    body.update(over)
    r = c.post("/api/events", json=body, headers=c.h)
    assert r.status_code == 201, r.text
    return r.json()


def jobs():
    db = SessionLocal()
    try:
        return list(db.scalars(select(Job).where(Job.type == ai_call_notify.JOB_TYPE)))
    finally:
        db.close()


def run_due():
    """Make the queued notification due and let the real worker drain it."""
    db = SessionLocal()
    for j in db.scalars(select(Job).where(Job.type == ai_call_notify.JOB_TYPE)):
        j.run_after = utcnow() - timedelta(seconds=1)
    db.commit()
    db.close()
    worker.drain_once()


def office_rows():
    db = SessionLocal()
    try:
        return list(db.scalars(
            select(NumberThreadEvent).join(NumberThread)
            .where(NumberThread.phone_key == "9549147244",
                   NumberThreadEvent.direction == Direction.OUTBOUND)))
    finally:
        db.close()


ENDED = {"summary": "Maria has an active leak over the kitchen after last night's storm. "
                    "She is an AHS customer and wants someone tomorrow. She has a dog.",
         "captured": CAPTURED, "outcome": "end_call"}


# ------------------------------------------------------------------------------ new lead

def test_a_new_lead_lists_every_captured_field_then_summary_then_link(office):
    call(office)
    call(office, ai_call=ENDED)
    call(office, recording_url=REC)
    run_due()

    assert len(office.rec.sent) == 1
    sent = office.rec.sent[0]
    assert sent["to"] == OFFICE and "media_ids" not in sent, "no pictures, ever"
    lines = sent["body"].split("\n")
    assert lines[0] == "New lead from AI call"
    for line in ("Name: Maria Ruiz", "Caller: (813) 555-0142",
                 "Second phone: (813) 555-0199", "Email: maria@example.test",
                 "Address: 412 Palm Ave", "City: Bradenton",
                 "What's happening: active leak over the kitchen", "Leaks: 2",
                 "Leak location: kitchen ceiling", "Started: last night",
                 "Roof material: shingle", "Roof age: 15 years", "Previous repairs: none",
                 "Stories: 1", "Owner or tenant: owner", "Payment: AHS",
                 "AHS dispatch #: D-55821", "First time with AHS: yes",
                 "Preferred inspection time: tomorrow morning",
                 "Access notes: gate code 1234", "Urgency: emergency",
                 "Notes: dog in the yard", "Pool cage: yes"):
        assert line in lines, line
    # order: known fields, then the unknown key, then the summary, then the link
    assert lines.index("Notes: dog in the yard") < lines.index("Pool cage: yes")
    assert lines[-2].startswith("Summary: Maria has an active leak")
    assert lines[-1] == ("Full details are in the call recording: " + BASE + REC)
    assert len(sent["body"]) <= ai_call_notify.MAX_CHARS


def test_only_the_keys_present_are_shown(office):
    call(office, ai_call={"agent": "Intake", "captured": {"name": "Ann Lee", "city": ""}})
    run_due()
    body = office.rec.sent[0]["body"]
    assert "Name: Ann Lee" in body and "City" not in body and "Email" not in body
    assert "Summary" not in body


def test_a_long_summary_is_cut_and_the_lead_fields_never_are(office):
    call(office, ai_call={"agent": "Intake", "captured": CAPTURED,
                          "summary": "word " * 600}, recording_url=REC)
    run_due()
    body = office.rec.sent[0]["body"]
    assert len(body) <= ai_call_notify.MAX_CHARS
    assert "Notes: dog in the yard" in body and "AHS dispatch #: D-55821" in body
    assert body.split("\n")[-1].endswith(REC)
    summary = [ln for ln in body.split("\n") if ln.startswith("Summary: ")]
    assert summary and summary[0].endswith("…")


# ------------------------------------------------------------------------------ existing

def test_an_existing_customer_gets_a_short_note_with_the_request_and_link(office):
    call(office, from_number=KNOWN, ai_call={
        "agent": "Intake", "engine": "retell", "captured": CAPTURED,
        "summary": "Maria wants to move her inspection. She asked for Friday. More detail.",
        "requests": [{"kind": "reschedule", "request": "move Thursday's visit to Friday",
                      "created": True, "where": "task"}]}, recording_url=REC)
    run_due()
    body = office.rec.sent[0]["body"]
    assert body.split("\n") == [
        "AI call from Maria Lopez (existing customer)",
        "Summary: Maria wants to move her inspection. She asked for Friday.",
        "Asked: reschedule — move Thursday's visit to Friday",
        "Full details are in the call recording: " + BASE + REC,
    ]
    assert "412 Palm Ave" not in body, "the existing-customer note is short"


def test_no_recording_links_the_customer_in_the_crm_instead(office):
    call(office, from_number=KNOWN, ai_call={"agent": "Intake", "summary": "Hi."})
    run_due()
    assert office.rec.sent[0]["body"].split("\n")[-1] == (
        "Recording not available yet. The call is on %s/contacts?contact=%d"
        % (BASE, office.known_id))


def test_no_recording_on_a_new_lead_says_so(office):
    call(office)
    run_due()
    assert office.rec.sent[0]["body"].endswith(
        "Full details are in the call recording: (recording not available yet)")


def test_without_a_public_url_the_text_carries_no_link(office, monkeypatch, caplog):
    monkeypatch.delenv(ai_call_notify.PUBLIC_URL_ENV)
    call(office, recording_url=REC)
    run_due()
    body = office.rec.sent[0]["body"]
    assert body.endswith("(recording in the CRM)") and "http" not in body
    assert "CRM_PUBLIC_URL is not set" in caplog.text


def test_a_recording_url_that_is_not_the_crm_relay_is_never_texted(office):
    call(office, recording_url="https://carrier.example/rec/abc.mp3")
    run_due()
    body = office.rec.sent[0]["body"]
    assert "carrier.example" not in body and body.endswith("(recording not available yet)")


# ------------------------------------------------------------------------------ guards

def test_with_the_setting_empty_nothing_is_queued_or_sent(office, monkeypatch):
    monkeypatch.setenv(ai_call_notify.NUMBERS_ENV, " , ")
    call(office, ai_call=ENDED, recording_url=REC)
    assert jobs() == []
    run_due()
    assert office.rec.sent == [] and office_rows() == []


def test_a_job_queued_before_the_setting_was_emptied_sends_nothing(office, monkeypatch):
    call(office)
    monkeypatch.setenv(ai_call_notify.NUMBERS_ENV, "")
    run_due()
    assert office.rec.sent == []


def test_a_recipient_any_contact_holds_is_refused_and_nothing_is_written(office, monkeypatch,
                                                                          caplog):
    monkeypatch.setenv(ai_call_notify.NUMBERS_ENV, "(941) 555-0123")      # Maria's line
    call(office)
    run_due()
    assert office.rec.sent == []
    db = SessionLocal()
    assert db.scalar(select(func.count(NumberThreadEvent.id)).where(
        NumberThreadEvent.direction == Direction.OUTBOUND)) == 0
    db.close()
    assert "never texts a customer" in caplog.text


def test_the_callers_own_number_is_never_a_recipient(office, monkeypatch):
    monkeypatch.setenv(ai_call_notify.NUMBERS_ENV, "%s,%s" % (STRANGER, OFFICE))
    call(office)
    run_due()
    assert [s["to"] for s in office.rec.sent] == [OFFICE]


def test_three_merging_reports_queue_one_job_and_send_one_text(office):
    ids = {call(office)["id"], call(office, ai_call=ENDED)["id"],
           call(office, recording_url=REC)["id"]}
    assert len(ids) == 1 and len(jobs()) == 1
    job = jobs()[0]
    assert job.max_attempts == 1, "never retried: a retry is a second text"
    assert (job.run_after.replace(tzinfo=None) - utcnow().replace(tzinfo=None)
            ).total_seconds() > 60, "it waits for the summary and the recording"
    run_due()
    assert len(office.rec.sent) == 1 and len(office_rows()) == 1


def test_running_the_job_twice_sends_once(office):
    call(office, ai_call=ENDED)
    payload = jobs()[0].payload
    for _ in range(2):
        db = SessionLocal()
        automations.HANDLERS[ai_call_notify.JOB_TYPE](db, payload)
        db.commit()
        db.close()
    assert len(office.rec.sent) == 1 and len(office_rows()) == 1


def test_a_call_that_becomes_an_ai_call_on_a_later_report_is_queued_once(office):
    call(office, ai_call=None)
    assert jobs() == []
    call(office, ai_call=ENDED)
    call(office, ai_call={"summary": "again"})
    assert len(jobs()) == 1


def test_a_call_no_agent_answered_sends_nothing(office):
    call(office, ai_call=None, dedupe_key="quo:call:1")
    call(office, type="SMS", ai_call=None, body="hello", dedupe_key="quo:sms:1",
         call_status=None, duration_seconds=None)
    assert jobs() == []
    run_due()
    assert office.rec.sent == []


def test_an_old_backfilled_ai_call_is_not_news(office):
    call(office, occurred_at=(utcnow() - timedelta(days=3)).isoformat())
    assert jobs() == []


def test_the_sent_text_is_on_the_office_thread_and_logged_only_while_unlinked(office,
                                                                             monkeypatch):
    # The real seam, not the recorder: with no CRM link configured nothing is transmitted.
    from app import transport
    monkeypatch.setattr(automations, "get_transport", transport.get_transport)
    assert isinstance(transport.get_transport(), transport.LoggingTransport)
    call(office, ai_call=ENDED, recording_url=REC)
    run_due()
    rows = office_rows()
    assert len(rows) == 1
    assert rows[0].delivery_status is DeliveryStatus.LOGGED_ONLY
    assert rows[0].body.startswith("New lead from AI call")


def test_it_is_reported_as_an_internal_rule(office):
    rule = {r["key"]: r for r in automations.rules()}["ai_call_notify"]
    assert rule["texts_customer"] is False and rule["enabled"] is True


def test_a_caller_saved_as_a_contact_before_the_job_runs_is_still_a_new_lead(office):
    call(office, ai_call=ENDED, recording_url=REC)
    db = SessionLocal()
    db.add(Contact(first_name="Maria", last_name="Ruiz", phone=STRANGER, created_by="Manual"))
    db.commit()     # adoption moves the call onto her new thread (a new row, same key)
    db.close()
    run_due()
    assert len(office.rec.sent) == 1
    assert office.rec.sent[0]["body"].startswith("New lead from AI call")
    assert "Caller: (813) 555-0142" in office.rec.sent[0]["body"]

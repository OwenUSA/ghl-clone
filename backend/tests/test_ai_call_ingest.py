"""A call an AI agent answered, arriving from owen-main.

Phase 1 of the voice-agent amendment (DECISIONS.md, 2026-09-22): owen-main runs the agent,
and the CRM has to be able to SHOW what happened. The wire is the ingest the telephony feed
already uses, plus one field: `ai_call`.

The rules under test, none of which is a status code:

  * an agent call lands on the thread carrying who answered and what they recorded;
  * a stranger's call still creates NO contact and NO deal — a capture is a record of what
    was said, not a lead. Promoting it is a person's decision while the agent is supervised
    (Q1/Q2), and nothing here may pre-empt that;
  * the SAME call reported twice does not become two rows, and the second report FILLS IN
    what the first could not know: the outcome, the transcript, the capture. That is the
    whole reason owen-main may report a call before it has ended;
  * a later report completes but never rewrites;
  * the thread hands `ai_call` to the browser whole.
"""
import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Contact,
    ConversationEvent,
    NumberThread,
    NumberThreadEvent,
    Opportunity,
    Role,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select

AGENT_LINE = "+19546859990"          # the Quo-overflow DID the agent answers on
STRANGER = "+18135550142"


@pytest.fixture()
def voice():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    feed = User(email="owen@voice.test", name="OWEN feed", role=Role.DISPATCHER)
    admin = User(email="admin@voice.test", name="Owner", role=Role.ADMIN)
    db.add_all([feed, admin])
    known = Contact(first_name="Maria", last_name="Lopez", phone="(941) 555-0123",
                    created_by="Manual")
    db.add(known)
    db.flush()          # tokens are minted against user_id, which does not exist yet
    tokens = {}
    for key, user, scopes in (("feed", feed, "events:write read"), ("admin", admin, "")):
        plain, tok = mint_api_token(user, name=key, scopes=scopes)
        db.add(tok)
        tokens[key] = plain
    db.commit()
    ids = {"known": known.id, "known_phone": known.phone}
    db.close()
    with TestClient(app) as c:
        c.tokens = tokens
        c.ids = ids
        yield c


CALL = {
    "type": "CALL",
    "direction": "INBOUND",
    "from_number": STRANGER,
    "call_status": "completed",
    "duration_seconds": 96,
    "provider_ref": "owen-call-7781",
    "source_system": "BulkVS",
    "source_number": AGENT_LINE,
    "body": "Inbound call answered by the AI receptionist.",
    "dedupe_key": "owen:agent:7781",
    "ai_call": {"agent": "Roofing Receptionist", "version": 10, "campaign": "Quo overflow"},
}


def post(c, who="feed", **overrides):
    body = {**CALL, **overrides}
    return c.post("/api/events", json=body,
                  headers={"Authorization": "Bearer " + c.tokens[who]})


def read(fn):
    db = SessionLocal()
    try:
        return fn(db)
    finally:
        db.close()


def test_an_agent_call_from_a_stranger_is_recorded_without_inventing_a_customer(voice):
    r = post(voice)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["contact_id"] is None and out["conversation_id"] is None

    ev = read(lambda db: db.get(NumberThreadEvent, out["id"]))
    assert ev is not None, "the call must be filed, not dropped"
    assert ev.ai_call["agent"] == "Roofing Receptionist"
    assert ev.ai_call["campaign"] == "Quo overflow"
    assert ev.ai_call["version"] == 10

    # The rule that outlives this feature: an unknown number becomes a thread, never a
    # customer. A voice agent that could mint contacts would fill the CRM with spam callers.
    counts = read(lambda db: (db.scalar(select(func.count(Contact.id))),
                              db.scalar(select(func.count(Opportunity.id))),
                              db.scalar(select(func.count(NumberThread.id)))))
    assert counts == (1, 0, 1), "one seeded contact, no deal, one number-only thread"


def test_the_capture_arrives_on_the_second_report_and_does_not_duplicate_the_call(voice):
    first = post(voice, duration_seconds=None, call_status=None,
                 ai_call={"agent": "Roofing Receptionist", "version": 10})
    assert first.status_code == 201
    event_id = first.json()["id"]

    # The call ends: NOW owen-main knows the outcome, the transcript and what was said.
    second = post(voice, transcript="caller: my roof is leaking\nagent: I've got it.",
                  ai_call={"agent": "Roofing Receptionist", "version": 10,
                           "outcome": "end_call",
                           "captured": {"name": "Maria Ruiz", "address": "412 Palm Ave",
                                        "intent": "active leak", "urgency": "emergency"}})
    assert second.status_code == 201
    assert second.json()["id"] == event_id, "the same call must not become two rows"

    rows, ev = read(lambda db: (db.scalar(select(func.count(NumberThreadEvent.id))),
                                db.get(NumberThreadEvent, event_id)))
    assert rows == 1
    assert ev.transcript.startswith("caller: my roof is leaking")
    assert ev.duration_seconds == 96 and ev.call_status == "completed"
    assert ev.ai_call["captured"]["name"] == "Maria Ruiz"
    assert ev.ai_call["captured"]["urgency"] == "emergency"
    # ...and what the first report established is still there.
    assert ev.ai_call["agent"] == "Roofing Receptionist"
    assert ev.ai_call["outcome"] == "end_call"


def test_a_later_report_completes_but_never_rewrites(voice):
    post(voice, ai_call={"agent": "Roofing Receptionist", "captured": {"name": "Maria"}})
    post(voice, ai_call={"agent": "SOMETHING ELSE", "captured": {"name": "WRONG"},
                         "outcome": "transfer"})

    ev = read(lambda db: db.scalars(select(NumberThreadEvent)).one())
    assert ev.ai_call["agent"] == "Roofing Receptionist"
    assert ev.ai_call["captured"]["name"] == "Maria"
    # ...while a key nobody had set is still allowed to arrive.
    assert ev.ai_call["outcome"] == "transfer"


def test_a_known_customer_gets_the_agent_call_on_their_own_thread(voice):
    r = post(voice, from_number=voice.ids["known_phone"])
    assert r.status_code == 201
    out = r.json()
    assert out["contact_id"] == voice.ids["known"]

    ev = read(lambda db: db.get(ConversationEvent, out["id"]))
    assert ev.ai_call["agent"] == "Roofing Receptionist"
    assert ev.conversation_id == out["conversation_id"]


def test_a_call_no_agent_answered_records_nothing_about_one(voice):
    r = post(voice, ai_call=None, dedupe_key="owen:human:1")
    ev = read(lambda db: db.get(NumberThreadEvent, r.json()["id"]))
    assert ev.ai_call is None, "a human-handled call must not claim an empty agent record"


def test_the_thread_hands_the_agent_record_to_the_browser(voice):
    post(voice, from_number=voice.ids["known_phone"],
         ai_call={"agent": "Roofing Receptionist", "outcome": "end_call",
                  "captured": {"intent": "active leak"}})
    conv_id = read(lambda db: db.scalars(select(ConversationEvent)).one().conversation_id)

    rows = voice.get("/api/conversations/%d/events" % conv_id,
                     headers={"Authorization": "Bearer " + voice.tokens["admin"]}).json()
    call = [e for e in rows if e["type"] == "CALL"][0]
    assert call["ai_call"]["agent"] == "Roofing Receptionist"
    assert call["ai_call"]["captured"]["intent"] == "active leak"
    assert all(e.get("ai_call") is None for e in rows if e["type"] != "CALL")

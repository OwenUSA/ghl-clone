"""Internal notes (NOTE, INTERNAL_COMMENT) are STAFF-only, at every door.

The rule and its reasoning are in DECISIONS.md (2026-09-10) and beside
`INTERNAL_TYPES` in backend/app/main.py. What matters here is that it is ONE rule
applied consistently: the thread view, the cross-thread message search and the
composer all have to agree, or a TECH finds the note through whichever door was
forgotten. There is a fourth door — GET /api/search — covered in test_search.py.

Behaviour, not status codes. The write test re-reads the thread as an ADMIN
afterwards, because a 403 that still inserted the row would pass a status check.
"""
from datetime import UTC, datetime, timedelta

import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Contact,
    Conversation,
    ConversationEvent,
    Direction,
    EventType,
    Opportunity,
    Pipeline,
    Role,
    Stage,
    User,
)
from fastapi.testclient import TestClient


def utcnow():
    return datetime.now(UTC)


@pytest.fixture()
def api():
    """One bearer client per role, over a small hand-built world.

    Tokens rather than passwords: scrypt costs ~700ms a hash on this host
    (DECISIONS.md) and nothing here exercises login.
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    users = {}
    for key, role in (("admin", Role.ADMIN), ("dispatcher", Role.DISPATCHER),
                      ("tech", Role.TECH)):
        u = User(email="%s@x.test" % key, name=key.title(), role=role)
        db.add(u)
        users[key] = u
    db.flush()

    # Three people who each match exactly one of name / email / phone, so a query
    # aimed at one column cannot accidentally pass because of another.
    jane = Contact(first_name="Jane", last_name="Doe",
                   email="jane@roofmail.test", phone="(941) 555-0101")
    marcus = Contact(first_name="Marcus", last_name="Webb",
                     email="skylight.person@example.test", phone="(941) 555-0202")
    priya = Contact(first_name="Priya", last_name="Nair",
                    email="priya@example.test", phone="(813) 777-4242")
    db.add_all([jane, marcus, priya])
    db.flush()

    pipe = Pipeline(name="Dream Team Roofing AHS")
    db.add(pipe)
    db.flush()
    new_lead = Stage(pipeline_id=pipe.id, name="New Lead", position=0)
    inspection = Stage(pipeline_id=pipe.id, name="Inspection", position=1)
    db.add_all([new_lead, inspection])
    db.flush()

    opps = {
        "roof": Opportunity(title="Jane roof replacement", contact_id=jane.id,
                            pipeline_id=pipe.id, stage_id=inspection.id,
                            value_cents=950000),
        "gutter": Opportunity(title="Webb gutter repair", contact_id=marcus.id,
                              pipeline_id=pipe.id, stage_id=new_lead.id,
                              value_cents=120000),
        "won": Opportunity(title="Nair roof tune-up", contact_id=priya.id,
                           pipeline_id=pipe.id, stage_id=inspection.id,
                           status="won", value_cents=40000),
    }
    db.add_all(opps.values())
    db.flush()

    convs = {}
    for key, c in (("jane", jane), ("marcus", marcus), ("priya", priya)):
        conv = Conversation(contact_id=c.id, last_event_at=utcnow())
        db.add(conv)
        convs[key] = conv
    db.flush()

    def ev(conv, type_, body, minutes_ago=1, direction=Direction.INBOUND,
           transcript=None):
        e = ConversationEvent(conversation_id=conv.id, type=type_,
                              direction=direction, body=body,
                              transcript=transcript,
                              occurred_at=utcnow() - timedelta(minutes=minutes_ago))
        db.add(e)
        return e

    ev(convs["jane"], EventType.SMS, "The skylight is leaking again", 10)
    ev(convs["marcus"], EventType.SMS, "Can you quote a new gutter run?", 9)
    ev(convs["priya"], EventType.EMAIL, "Nothing relevant in this one", 8)
    # An internal note: staff-only. Contains a word that appears NOWHERE else, so
    # a role test can look for that word alone.
    ev(convs["jane"], EventType.INTERNAL_COMMENT,
       "Crew note: homeowner is litigious, document everything", 7)
    ev(convs["marcus"], EventType.NOTE, "Crew note: dog on site, call ahead", 6)
    # A CALL whose transcript says "skylight". Transcripts are deliberately OUT of
    # the palette's scope, so this row must never surface in /api/search.
    ev(convs["priya"], EventType.CALL, None, 5,
       transcript="he mentioned the skylight three times")

    tokens = {}
    for key, u in users.items():
        plain, tok = mint_api_token(u, name=key)
        db.add(tok)
        tokens[key] = plain
    db.commit()

    ids = {"jane": jane.id, "marcus": marcus.id, "priya": priya.id,
           "conv_jane": convs["jane"].id, "conv_marcus": convs["marcus"].id,
           "pipeline": pipe.id,
           **{"opp_" + k: o.id for k, o in opps.items()}}
    db.close()

    def client(who="admin"):
        c = TestClient(app)
        c.headers["Authorization"] = "Bearer " + tokens[who]
        return c

    yield {"client": client, "ids": ids, "tokens": tokens}



# ---------------- the rule ----------------


def test_a_tech_cannot_read_internal_notes_through_the_message_search(api):
    tech = api["client"]("tech")

    both = tech.get("/api/messages", params={"q": "Crew note"}).json()
    assert both["total"] == 0

    # Asking for them by name is refused, not silently emptied: a CLI user who
    # types --type NOTE deserves to be told why nothing came back.
    r = tech.get("/api/messages", params={"type": "NOTE"})
    assert r.status_code == 403
    assert "internal notes" in r.json()["detail"]

    assert api["client"]().get(
        "/api/messages", params={"q": "Crew note"}).json()["total"] == 2


def test_a_tech_cannot_read_internal_notes_through_the_thread_view(api):
    conv = api["ids"]["conv_jane"]
    tech, admin = api["client"]("tech"), api["client"]()

    def types(client, **params):
        r = client.get("/api/conversations/%d/events" % conv, params=params)
        assert r.status_code == 200, r.text
        return [e["type"] for e in r.json()]

    assert "INTERNAL_COMMENT" in types(admin)
    assert "INTERNAL_COMMENT" not in types(tech)
    # The measured "Conversations" filter includes INTERNAL_COMMENT, so it is one
    # of the doors that has to narrow.
    assert "INTERNAL_COMMENT" in types(admin, filter="conversations")
    assert "INTERNAL_COMMENT" not in types(tech, filter="conversations")
    # ...and the thread is not emptied for the tech, only narrowed.
    assert "SMS" in types(tech)

    assert tech.get("/api/conversations/%d/events" % conv,
                    params={"filter": "internal_comment"}).status_code == 403


def test_a_tech_cannot_write_a_note_they_would_not_be_allowed_to_read(api):
    """A write that lands somewhere the writer cannot see it is worse than a
    refusal. Asserts the row was not created, not just the status code."""
    conv = api["ids"]["conv_jane"]
    tech, admin = api["client"]("tech"), api["client"]()

    before = admin.get("/api/conversations/%d/events" % conv).json()
    r = tech.post("/api/conversations/%d/messages" % conv,
                  json={"body": "tech-written note", "type": "INTERNAL_COMMENT"})
    assert r.status_code == 403
    after = admin.get("/api/conversations/%d/events" % conv).json()
    assert len(after) == len(before)
    assert not [e for e in after if e["body"] == "tech-written note"]

    # A customer-facing SMS from the same tech still goes through.
    assert tech.post("/api/conversations/%d/messages" % conv,
                     json={"body": "on my way", "type": "SMS"}
                     ).status_code == 201

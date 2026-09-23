"""POST /api/agent-context — the voice agent's customer brief (2026-09-24).

What a voice agent may say out loud about whoever is holding the phone. Every test here is
about what is NOT in the answer as much as what is: the fixture gives the known customer
money on the card, an opportunity note, an internal note and an internal comment on the
thread, Checklist answers, an email address and a home address — each a distinctive string —
and the tests read the RAW response body for every one of them.
"""
import json
from datetime import timedelta

import pytest
from app import agent_context
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Appointment,
    Calendar,
    Contact,
    Conversation,
    ConversationEvent,
    DeliveryStatus,
    Direction,
    EventType,
    Job,
    Opportunity,
    OpportunityNote,
    Pipeline,
    PipelinePermission,
    Role,
    Stage,
    User,
    utcnow,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select

MARIA = "+19415550123"

# Everything the brief must never carry. Each is unique in the fixture, so its presence in a
# response body can only mean it leaked.
SECRETS = {
    "money": "1450000",
    "money_dollars": "14,500",
    "opp_note": "GATE-CODE-4411",
    "thread_note": "INTERNAL-NOTE-slow-payer",
    "thread_comment": "INTERNAL-COMMENT-dog-bites",
    "checklist": "CHECKLIST-shingle-brand",
    "email": "maria.ruiz@example.test",
    "street": "742 Evergreen",
    "other_customer": "Bystander",
    "cancelled_visit": "Cancelled visit",
}
# What the DISPATCHER-owned feed may not see (a pipeline restricted to the admin). An
# admin-owned token legitimately does — see the pipeline test.
HIDDEN = {
    "hidden_pipeline": "Owner Only",
    "hidden_card": "Secret insurance claim",
    "hidden_visit": "Adjuster meeting",
}


@pytest.fixture()
def world():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    feed = User(email="owen@x.test", name="OWEN feed", role=Role.DISPATCHER)
    admin = User(email="admin@x.test", name="Owner", role=Role.ADMIN)
    tech = User(email="tech@x.test", name="Tech", role=Role.TECH)
    limited = User(email="limited@x.test", name="Limited", role=Role.DISPATCHER,
                   only_assigned_data=True)
    db.add_all([feed, admin, tech, limited])
    db.flush()

    retail = Pipeline(name="Retail", position=0)
    secret = Pipeline(name=HIDDEN["hidden_pipeline"], position=1)
    db.add_all([retail, secret])
    db.flush()
    inspection = Stage(pipeline_id=retail.id, name="Inspection", position=1)
    new_lead = Stage(pipeline_id=retail.id, name="New Lead", position=0)
    secret_stage = Stage(pipeline_id=secret.id, name="Claim", position=0)
    db.add_all([inspection, new_lead, secret_stage])
    db.flush()
    # The Owner Only pipeline is restricted to the admin: the DISPATCHER feed cannot see it.
    db.add(PipelinePermission(pipeline_id=secret.id, user_id=admin.id))
    secret_cal = Calendar(name="Owner calendar", pipeline_id=secret.id)
    db.add(secret_cal)

    maria = Contact(first_name="Maria", last_name="Ruiz", phone="(941) 555-0123",
                    email=SECRETS["email"], address_street=SECRETS["street"] + " Terrace")
    bystander = Contact(first_name=SECRETS["other_customer"], last_name="Jones",
                        phone="+19415550999")
    # Two contacts on ONE line: a household. Neither may be named.
    wife = Contact(first_name="Ana", last_name="Diaz", phone="813-555-7777")
    husband = Contact(first_name="Luis", last_name="Diaz", phone="+18135557777")
    db.add_all([maria, bystander, wife, husband])
    db.flush()

    now = utcnow()
    old = Opportunity(title="Gutter repair", contact_id=maria.id, pipeline_id=retail.id,
                      stage_id=new_lead.id, status="open", value_cents=5000,
                      updated_at=now - timedelta(days=30))
    current = Opportunity(title="Roof replacement", contact_id=maria.id,
                          pipeline_id=retail.id, stage_id=inspection.id, status="open",
                          value_cents=int(SECRETS["money"]),
                          custom_fields={"shingle": SECRETS["checklist"]},
                          updated_at=now - timedelta(days=2))
    won = Opportunity(title="Old job, won", contact_id=maria.id, pipeline_id=retail.id,
                      stage_id=inspection.id, status="won", updated_at=now)
    hidden_card = Opportunity(title=HIDDEN["hidden_card"], contact_id=maria.id,
                              pipeline_id=secret.id, stage_id=secret_stage.id,
                              status="open", updated_at=now)
    bystander_card = Opportunity(title="Bystander roof", contact_id=bystander.id,
                                 pipeline_id=retail.id, stage_id=inspection.id,
                                 status="open", updated_at=now)
    db.add_all([old, current, won, hidden_card, bystander_card])
    db.flush()
    db.add(OpportunityNote(opportunity_id=current.id, body=SECRETS["opp_note"]))

    def visit(title, days, **kw):
        start = now + timedelta(days=days)
        return Appointment(title=title, contact_id=maria.id, starts_at=start,
                           ends_at=start + timedelta(hours=1), **kw)
    db.add_all([
        visit("Past inspection", -3),
        visit(SECRETS["cancelled_visit"], 1, status="cancelled"),
        visit(HIDDEN["hidden_visit"], 1.5, opportunity_id=hidden_card.id),
        visit("Owner calendar hold", 1.6, calendar_id=secret_cal.id),
        visit("Roof inspection", 2, opportunity_id=current.id,
              notes="INTERNAL-appointment-notes"),
        visit("Later visit", 9),
    ])

    convo = Conversation(contact_id=maria.id)
    db.add(convo)
    db.flush()

    def event(type_, days, direction=Direction.INBOUND, **kw):
        return ConversationEvent(conversation_id=convo.id, type=type_, direction=direction,
                                 occurred_at=now - timedelta(days=days), **kw)
    db.add_all([
        event(EventType.CALL, 5, call_status="completed"),
        event(EventType.SMS, 4, body="See you Tuesday", direction=Direction.OUTBOUND,
              delivery_status=DeliveryStatus.DELIVERED),
        # Newer than the last real contact, and none of them is one:
        event(EventType.NOTE, 1, body=SECRETS["thread_note"], direction=Direction.OUTBOUND),
        event(EventType.INTERNAL_COMMENT, 0.5, body=SECRETS["thread_comment"],
              direction=Direction.OUTBOUND),
        event(EventType.SMS, 0.4, body="never left", direction=Direction.OUTBOUND,
              delivery_status=DeliveryStatus.REFUSED),
        event(EventType.APPOINTMENT, 0.3, body="Appointment booked"),
    ])

    tokens = {}
    for key, user, scopes in (("feed", feed, "events:write read"),
                              ("feed_only", feed, "events:write"),
                              ("read_only", feed, "read"),
                              ("admin", admin, ""),
                              ("tech", tech, ""),
                              ("tech_scoped", tech, "events:write"),
                              ("limited", limited, "events:write")):
        plain, tok = mint_api_token(user, name=key, scopes=scopes)
        db.add(tok)
        tokens[key] = plain
    db.commit()
    ids = {"maria": maria.id, "current": current.id, "last_call_sms":
           (now - timedelta(days=4))}
    db.close()
    with TestClient(app) as c:
        c.tokens = tokens
        c.ids = ids
        yield c


def ask(c, number, who="feed_only", **extra):
    return c.post(agent_context.PATH, json={"caller_number": number, **extra},
                  headers={"Authorization": "Bearer " + c.tokens[who]})


def assert_no_secrets(text: str, *, feed: bool = True):
    never = {**SECRETS, **HIDDEN} if feed else SECRETS
    leaked = {k: v for k, v in never.items() if v.lower() in text.lower()}
    assert not leaked, "the brief carried %s" % sorted(leaked)
    assert "INTERNAL-appointment-notes" not in text


def job_rows():
    db = SessionLocal()
    try:
        return db.scalar(select(func.count(Job.id)))
    finally:
        db.close()


# ------------------------------------------------------------------ a known caller

@pytest.mark.parametrize("rendering", [MARIA, "19415550123", "941-555-0123",
                                       "(941) 555-0123", "9415550123"])
def test_a_known_caller_gets_exactly_the_four_facts(world, rendering):
    r = ask(world, rendering)
    assert r.status_code == 200, r.text
    out = r.json()
    assert set(out) == {"known", "contact", "opportunity", "next_appointment",
                        "last_contact_at"}
    assert out["known"] is True
    assert out["contact"] == {"first_name": "Maria", "last_name": "Ruiz"}
    # The most recently updated OPEN card the feed may see: not the won one (newer), not the
    # one on the hidden pipeline (newer still), not the older open one.
    assert out["opportunity"] == {"title": "Roof replacement", "stage": "Inspection",
                                  "pipeline": "Retail"}
    # The next visit still on: not the past one, not the cancelled one, not the one on the
    # hidden deal, not the one on the hidden pipeline's calendar.
    assert set(out["next_appointment"]) == {"starts_at", "title"}
    assert out["next_appointment"]["title"] == "Roof inspection"
    assert out["next_appointment"]["starts_at"].endswith("+00:00")
    assert_no_secrets(r.text)


def test_last_contact_is_the_last_call_or_text_never_a_note_or_an_unsent_text(world):
    out = ask(world, MARIA).json()
    # The delivered SMS 4 days ago. The note (1 day), the internal comment (12h), the
    # REFUSED text (10h) and the appointment activity (7h) are all newer and none counts.
    got = out["last_contact_at"]
    want = world.ids["last_call_sms"]
    assert got[:16] == want.isoformat()[:16]


def test_money_and_internal_notes_are_absent_even_though_the_contact_has_both(world):
    # Precondition: the database really holds them, so absence is the endpoint's doing.
    db = SessionLocal()
    try:
        card = db.get(Opportunity, world.ids["current"])
        assert card.value_cents == int(SECRETS["money"])
        assert db.scalar(select(func.count(OpportunityNote.id))) == 1
        kinds = set(db.scalars(select(ConversationEvent.type)).all())
        assert {EventType.NOTE, EventType.INTERNAL_COMMENT} <= kinds
    finally:
        db.close()
    for who in ("feed", "feed_only", "admin"):
        r = ask(world, MARIA, who=who)
        assert r.status_code == 200
        body = json.dumps(r.json())
        assert "value" not in body and "cents" not in body and "$" not in body
        assert "note" not in body.lower()
        assert "email" not in body and "custom_fields" not in body
        assert_no_secrets(r.text, feed=who != "admin")


def test_an_admin_owned_token_sees_the_restricted_pipeline_the_feed_cannot(world):
    """The rule is the token owner's pipeline access. Proven from both sides, so the
    feed's narrower answer is the restriction working and not the card simply missing."""
    feed = ask(world, MARIA, who="feed_only").json()
    admin = ask(world, MARIA, who="admin").json()
    assert feed["opportunity"]["title"] == "Roof replacement"
    assert admin["opportunity"]["title"] == HIDDEN["hidden_card"]
    assert admin["next_appointment"]["title"] == HIDDEN["hidden_visit"]


def test_the_brief_writes_nothing_and_queues_nothing(world):
    before = job_rows()
    ask(world, MARIA)
    ask(world, "+10000000000")
    assert job_rows() == before


# ------------------------------------------------------------------ unknown / ambiguous

@pytest.mark.parametrize("number", [
    "+12125550000",          # nobody holds it
    "5550123",               # Maria's last seven — a fragment is never a match
    "0123",
    "",
    "maria",
    "+1 813 555 7777",       # two contacts share this line: a household
])
def test_an_unknown_or_ambiguous_number_gets_known_false_and_nothing_else(world, number):
    r = ask(world, number)
    assert r.status_code == 200, r.text
    assert r.json() == {"known": False}
    assert "Maria" not in r.text and "Diaz" not in r.text and "Ana" not in r.text


def test_the_body_takes_a_number_and_nothing_else(world):
    r = ask(world, "+12125550000", contact_id=world.ids["maria"])
    assert r.status_code == 422
    assert "Maria" not in r.text


# ------------------------------------------------------------------ who may ask

@pytest.mark.parametrize("who", ["read_only", "tech", "tech_scoped", "limited"])
def test_a_credential_without_the_feed_s_rights_gets_nothing(world, who):
    r = ask(world, MARIA, who=who)
    assert r.status_code == 403, r.text
    assert "Maria" not in r.text and "Roof" not in r.text
    assert_no_secrets(r.text)


def test_no_credential_gets_nothing(world):
    r = world.post(agent_context.PATH, json={"caller_number": MARIA})
    assert r.status_code == 401
    assert "Maria" not in r.text


def test_a_scoped_token_is_let_through_by_the_scope_list_not_by_accident():
    from app import auth
    assert agent_context.PATH in auth.EVENTS_WRITE_PATHS
    assert auth._scope_allows(frozenset({"events:write"}), "POST", agent_context.PATH)
    assert not auth._scope_allows(frozenset({"events:write"}), "GET", agent_context.PATH)

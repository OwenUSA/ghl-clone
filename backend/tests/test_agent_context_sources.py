"""The voice agent's brief, by the agent's own switches (2026-10-06, docs/RETELL-PLAN.md C2).

The 2026-09-24 brief (tests/test_agent_context.py, whose world this reuses) is what a caller's
brief was when no agent was named. Now owen-main names the agent, and the answer is built from
THAT agent's published "Customer information this agent receives" switches — every one off by
default. What is pinned, by behaviour:

  * no agent named (or a name no voice agent has) → the old answer, byte for byte, and Zuper is
    never consulted (a Zuper-only caller stays unknown);
  * each switch, on its own, adds exactly its key — and off, the key is absent;
  * a Zuper-only customer is known (`source: "zuper"`), named from Zuper, with no card or visit;
  * two people on one line — a household, two Zuper customers, or a CRM contact plus a Zuper
    customer who is somebody else — is unknown, exactly `{"known": false}`;
  * money, notes, the Checklist and email never appear, whatever is switched on;
  * a pipeline / Zuper board hidden from the token's owner stays hidden;
  * summaries ≤ 400 characters, texts ≤ 200, at most 3 of each, newest first.
"""
import json
import time
from datetime import timedelta

import pytest
from app import agent_context
from app.ai.config import default_config
from app.db import SessionLocal
from app.models import (
    AiAgent,
    AiAgentVersion,
    Contact,
    Conversation,
    ConversationEvent,
    DeliveryStatus,
    Direction,
    DispatchCall,
    DispatchJob,
    EventType,
    Opportunity,
    Pipeline,
    Stage,
    User,
    ZuperMapping,
    utcnow,
)
from sqlalchemy import select
from tests.test_agent_context import HIDDEN, MARIA, SECRETS, ask, job_rows
from tests.test_agent_context import world as base_world  # noqa: F401 - fixture

ALL = ("crm_card", "zuper_job", "call_summaries", "texts", "ai_calls", "address")
PEDRO = "+12395550188"          # a Zuper-only customer
TWO_ZUPER = "+12395550199"      # two Zuper customers on one line
CARL = "+17275550101"           # a CRM contact AND a different Zuper customer
LONG_SUMMARY = "Zuper Connect summary: " + "the gutter is loose " * 30
LONG_TEXT = "Inbound text: " + "please call me back " * 20


def _db():
    return SessionLocal()


@pytest.fixture()
def world(base_world):  # noqa: F811 - the imported fixture, extended
    """The 2026-09-24 world, plus calls with summaries, texts, Zuper jobs and a voice agent."""
    db = _db()
    now = utcnow()
    maria = db.get(Contact, base_world.ids["maria"])
    convo = db.scalar(select(Conversation).where(Conversation.contact_id == maria.id))
    feed = db.scalar(select(User).where(User.email == "owen@x.test"))

    def ev(type_, days, **kw):
        kw.setdefault("direction", Direction.INBOUND)
        return ConversationEvent(conversation_id=convo.id, type=type_,
                                 occurred_at=now - timedelta(days=days), **kw)
    db.add_all([
        ev(EventType.CALL, 3, source_system="OpenPhone", call_status="completed",
           duration_seconds=120, body="Call with Maria. Summary: She asked about shingles."),
        ev(EventType.CALL, 2, source_system="BulkVS", call_status="completed",
           duration_seconds=80, body="Answered by the AI receptionist.",
           ai_call={"agent": "Intake", "summary": "AI SUMMARY: wants the inspection moved",
                    "cost_cents": 41}),
        ev(EventType.CALL, 200, source_system="OpenPhone", call_status="completed",
           body="Summary: OLD-CALL-beyond-ninety-days"),
        ev(EventType.SMS, 0.2, body=LONG_TEXT),
        ev(EventType.SMS, 0.1, body="UNSENT-TEXT", direction=Direction.OUTBOUND,
           delivery_status=DeliveryStatus.FAILED),
    ])
    db.add(DispatchCall(call_uid="zc-1", occurred_at=now - timedelta(days=1),
                        direction="INBOUND", number="9415550123", summary=LONG_SUMMARY))
    db.add(DispatchCall(call_uid="zc-2", occurred_at=now - timedelta(hours=5),
                        direction="OUTBOUND", number="2395550188",
                        summary="Zuper Connect: told Pedro the crew comes Friday"))
    db.add_all([
        DispatchJob(job_uid="j-maria", job_number="1042", board="Retail",
                    status="Inspection Scheduled", status_since=now - timedelta(days=2),
                    customer_uid="cu-maria", customer_name="Maria Ruiz",
                    phones=["9415550123"], technician="Luis",
                    scheduled_start=now + timedelta(days=2), is_open=True),
        DispatchJob(job_uid="j-maria-secret", job_number="1043", board=HIDDEN["hidden_pipeline"],
                    status="SECRET-BOARD-STATUS", status_since=now,
                    customer_uid="cu-maria", customer_name="Maria Ruiz",
                    phones=["9415550123"], is_open=True),
        DispatchJob(job_uid="j-pedro", job_number="2001", board="AHS - Inspection",
                    status="Welcome Call!", customer_uid="cu-pedro",
                    customer_name="Pedro Alvarez", phones=["2395550188", "2395550000"],
                    address="15 Bay St", city="Sarasota", technician="Ana",
                    status_since=now - timedelta(hours=3), is_open=True),
        DispatchJob(job_uid="j-two-a", job_number="3001", board="Retail",
                    customer_uid="cu-a", customer_name="First Person", phones=["2395550199"]),
        DispatchJob(job_uid="j-two-b", job_number="3002", board="Retail",
                    customer_uid="cu-b", customer_name="Second Person",
                    phones=["2395550199"]),
        DispatchJob(job_uid="j-carl", job_number="4001", board="Retail",
                    customer_uid="cu-carl", customer_name="Somebody Else",
                    phones=["7275550101"]),
    ])
    carl = Contact(first_name="Carl", last_name="Smith", phone="(727) 555-0101")
    db.add(carl)
    db.flush()
    cfg = default_config("voice")
    cfg.update({"owen_agent": "Intake", "engine": "retell", "retell_agent_id": "agent_abc",
                "actions": ["end_call"]})
    agent = AiAgent(name="Receptionist", channel="voice", mode="off", draft=cfg,
                    created_by_id=feed.id)
    db.add(agent)
    db.flush()
    v = AiAgentVersion(agent_id=agent.id, version=1, config=cfg)
    db.add(v)
    db.flush()
    agent.published_version_id = v.id
    db.commit()
    base_world.ids.update({"agent": agent.id, "version": v.id, "carl": carl.id})
    db.close()
    return base_world


def switches(world, **on):
    """Set the PUBLISHED version's switches (what runs)."""
    db = _db()
    v = db.get(AiAgentVersion, world.ids["version"])
    cfg = dict(v.config)
    cfg["context_sources"] = {k: bool(on.get(k)) for k in ALL}
    v.config = cfg
    db.commit()
    db.close()


def brief(world, number=MARIA, who="feed_only", agent="Intake"):
    r = ask(world, number, who=who, **({"agent_name": agent} if agent is not None else {}))
    assert r.status_code == 200, r.text
    return r


# ------------------------------------------------------------------ the old answer is untouched

def test_no_agent_name_is_the_old_answer_byte_for_byte(world):
    plain = brief(world, agent=None)
    unknown_agent = brief(world, agent="Nobody By This Name")
    assert plain.content == unknown_agent.content
    assert set(plain.json()) == {"known", "contact", "opportunity", "next_appointment",
                                 "last_contact_at"}
    # ...and Zuper is not consulted without an agent: the Zuper-only caller and the CRM
    # contact whose line Zuper gives to somebody else answer as before.
    assert brief(world, PEDRO, agent=None).json() == {"known": False}
    assert brief(world, CARL, agent=None).json()["contact"]["first_name"] == "Carl"


def test_an_archived_or_text_agent_is_not_a_known_agent(world):
    db = _db()
    a = db.get(AiAgent, world.ids["agent"])
    a.channel = "text"
    db.commit()
    db.close()
    assert brief(world).content == brief(world, agent=None).content


# ------------------------------------------------------------------ every switch off / on

def test_every_switch_off_says_known_and_nothing_else(world):
    switches(world)
    assert brief(world).json() == {"known": True, "source": "crm"}
    assert brief(world, PEDRO).json() == {"known": True, "source": "zuper"}


@pytest.mark.parametrize("source, keys", [
    ("crm_card", {"contact", "opportunity", "next_appointment", "last_contact_at"}),
    ("zuper_job", {"zuper_job"}),
    ("call_summaries", {"recent_calls"}),
    ("ai_calls", {"recent_calls"}),
    ("texts", {"recent_texts"}),
    ("address", {"address"}),
])
def test_each_switch_adds_exactly_its_own_keys(world, source, keys):
    switches(world)
    off = brief(world).json()
    switches(world, **{source: True})
    on = brief(world).json()
    assert set(on) - set(off) == keys
    assert {k: on[k] for k in off} == off


def test_the_card_facts_are_the_old_facts(world):
    switches(world, crm_card=True)
    out = brief(world).json()
    old = brief(world, agent=None).json()
    assert {k: out[k] for k in old} == old
    assert out["source"] == "crm"


def test_call_summaries_quo_and_zuper_connect_newest_first_cut_to_400(world):
    switches(world, call_summaries=True)
    calls = brief(world).json()["recent_calls"]
    assert [c["channel"] for c in calls] == ["zuper_connect", "quo"]   # the AI call is off
    assert calls[0]["direction"] == "inbound"
    assert len(calls[0]["summary"]) <= 400 and calls[0]["summary"].endswith("…")
    assert calls[1]["summary"] == "She asked about shingles."
    assert "OLD-CALL" not in json.dumps(calls)                          # 90 days


def test_ai_calls_add_the_agents_own_summaries_and_never_more_than_three(world):
    switches(world, ai_calls=True)
    calls = brief(world).json()["recent_calls"]
    assert calls == [{"at": calls[0]["at"], "channel": "ai", "direction": "inbound",
                      "summary": "AI SUMMARY: wants the inspection moved"}]
    switches(world, ai_calls=True, call_summaries=True)
    calls = brief(world).json()["recent_calls"]
    assert [c["channel"] for c in calls] == ["zuper_connect", "ai", "quo"]
    assert len(calls) == 3
    assert "cost" not in json.dumps(calls)


def test_texts_are_sent_or_received_only_last_three_cut_to_200(world):
    switches(world, texts=True)
    texts = brief(world).json()["recent_texts"]
    assert len(texts) <= 3
    assert texts[0]["direction"] == "inbound"
    assert len(texts[0]["text"]) <= 200 and texts[0]["text"].endswith("…")
    body = json.dumps(texts)
    assert "UNSENT-TEXT" not in body and "never left" not in body    # FAILED / REFUSED
    assert SECRETS["thread_note"] not in body and SECRETS["thread_comment"] not in body
    assert "See you Tuesday" in body


def test_the_zuper_job_is_the_visible_one_and_the_address_is_the_contacts(world):
    switches(world, zuper_job=True, address=True)
    out = brief(world).json()
    assert out["zuper_job"] == {"job_number": "1042", "board": "Retail",
                                "status": "Inspection Scheduled",
                                "status_since": out["zuper_job"]["status_since"],
                                "technician": "Luis",
                                "scheduled_start": out["zuper_job"]["scheduled_start"]}
    assert "SECRET-BOARD-STATUS" not in json.dumps(out)       # board hidden from the feed
    assert out["address"].startswith(SECRETS["street"])
    # An admin-owned token sees the newer job on the restricted board.
    admin = brief(world, who="admin").json()
    assert admin["zuper_job"]["status"] == "SECRET-BOARD-STATUS"


# ------------------------------------------------------------------ who is "registered"

def test_a_zuper_only_customer_is_known_by_zuper(world):
    switches(world, **dict.fromkeys(ALL, True))
    out = brief(world, PEDRO).json()
    assert out["known"] is True and out["source"] == "zuper"
    assert out["contact"] == {"first_name": "Pedro", "last_name": "Alvarez"}
    assert out["opportunity"] is None and out["next_appointment"] is None
    assert out["zuper_job"]["job_number"] == "2001"
    assert out["address"] == "15 Bay St, Sarasota"
    assert [c["channel"] for c in out["recent_calls"]] == ["zuper_connect"]
    assert out["last_contact_at"] is not None


@pytest.mark.parametrize("number", [TWO_ZUPER, CARL, "+1 813 555 7777", "5550188"])
def test_two_people_on_one_line_is_unknown_and_nothing_else(world, number):
    switches(world, **dict.fromkeys(ALL, True))
    r = brief(world, number)
    assert r.json() == {"known": False}
    for name in ("Carl", "Somebody", "First Person", "Second Person", "Diaz", "Pedro"):
        assert name not in r.text


def test_a_zuper_customer_the_crm_links_to_the_contact_is_the_same_person(world):
    switches(world, crm_card=True, zuper_job=True)
    db = _db()
    db.add(ZuperMapping(crm_type="contact", crm_id=world.ids["carl"], zuper_type="customer",
                        zuper_uid="cu-carl"))
    db.commit()
    db.close()
    out = brief(world, CARL).json()
    assert out["source"] == "crm" and out["contact"]["first_name"] == "Carl"
    assert out["zuper_job"]["job_number"] == "4001"


def test_a_zuper_customer_with_the_contacts_own_name_is_the_same_person(world):
    switches(world, crm_card=True)
    db = _db()
    db.scalar(select(DispatchJob).where(DispatchJob.job_uid == "j-carl")).customer_name = \
        "  carl   SMITH "
    db.commit()
    db.close()
    assert brief(world, CARL).json()["contact"]["last_name"] == "Smith"


def test_a_zuper_only_customer_on_a_hidden_board_is_unknown(world):
    switches(world, crm_card=True)
    db = _db()
    db.scalar(select(DispatchJob).where(DispatchJob.job_uid == "j-pedro")).board = \
        HIDDEN["hidden_pipeline"]
    db.commit()
    db.close()
    assert brief(world, PEDRO).json() == {"known": False}
    assert brief(world, PEDRO, who="admin").json()["source"] == "zuper"


# ------------------------------------------------------------------ never

def test_money_notes_checklist_and_email_never_appear_whatever_is_on(world):
    switches(world, **dict.fromkeys(ALL, True))
    for who in ("feed_only", "admin"):
        r = brief(world, who=who)
        body = r.text
        assert "value" not in body and "cents" not in body and "$" not in body
        assert "note" not in body.lower() and "email" not in body
        assert "custom_fields" not in body
        never = {k: v for k, v in SECRETS.items() if k != "street"}
        if who == "feed_only":
            never.update(HIDDEN)
        leaked = {k: v for k, v in never.items() if v.lower() in body.lower()}
        assert not leaked, leaked
        assert "INTERNAL-appointment-notes" not in body


def test_the_address_only_with_its_switch(world):
    switches(world, **{k: True for k in ALL if k != "address"})
    body = brief(world).text
    assert SECRETS["street"] not in body and "Bay St" not in body
    assert "Bay St" not in brief(world, PEDRO).text


def test_the_agents_draft_switches_do_not_apply_until_published(world):
    switches(world)
    db = _db()
    a = db.get(AiAgent, world.ids["agent"])
    a.draft = {**a.draft, "context_sources": dict.fromkeys(ALL, True)}
    db.commit()
    db.close()
    assert brief(world).json() == {"known": True, "source": "crm"}


def test_the_brief_writes_nothing_queues_nothing_and_is_quick(world):
    switches(world, **dict.fromkeys(ALL, True))
    before = job_rows()
    db = _db()
    try:
        from app import auth
        feed = db.scalar(select(User).where(User.email == "owen@x.test"))
        principal = auth.Principal(user_id=feed.id, email=feed.email, name=feed.name,
                                   role=feed.role, kind="pat",
                                   scopes=frozenset({"events:write"}))
        agent_context.agent_brief(db, principal, MARIA, dict.fromkeys(ALL, True))  # warm
        start = time.perf_counter()
        for number in (MARIA, PEDRO, CARL, TWO_ZUPER):
            agent_context.agent_brief(db, principal, number, dict.fromkeys(ALL, True))
        assert (time.perf_counter() - start) / 4 < 0.8
    finally:
        db.close()
    assert job_rows() == before


def test_the_body_still_takes_nothing_but_the_number_and_the_agent(world):
    r = ask(world, MARIA, agent_name="Intake", contact_id=world.ids["maria"])
    assert r.status_code == 422 and "Maria" not in r.text


def test_opportunity_pipeline_hiding_still_applies_with_an_agent(world):
    switches(world, crm_card=True)
    assert brief(world).json()["opportunity"]["title"] == "Roof replacement"
    assert brief(world, who="admin").json()["opportunity"]["title"] == HIDDEN["hidden_card"]
    db = _db()
    assert db.scalar(select(Pipeline).where(Pipeline.name == HIDDEN["hidden_pipeline"]))
    assert db.scalar(select(Stage).where(Stage.name == "Claim"))
    assert db.scalar(select(Opportunity).where(Opportunity.title == HIDDEN["hidden_card"]))
    db.close()

"""Texting goes live (2026-09-15): manual texts only, and "New message" to any number.

The owner's decisions, asserted as behaviour with owen-main mocked at the HTTP boundary
(`link`, from tests/test_crm_link.py — the real `crmlink` code builds the real request):

  1. No automatic texts at all. Rules 3 (appointment reminders) and 4 (stage change) enqueue
     nothing, and a job of either type already in a queue sends nothing — re-read: no
     outbound row, no request to owen-main. The AI "stage entered" trigger still runs.
  2. "New message" to any number: a contact's number goes on the contact's conversation, an
     unknown number on its NUMBER-ONLY thread (no contact — counted before and after), a
     second text reuses that thread, a bad number writes nothing.
  3. The composer: one transport call with the normalised number; REFUSED keeps owen-main's
     sentence, FAILED is distinct; a delivery receipt advances the bubble.
  4. Inbound SMS from an unknown number creates no contact and raises the unread count.
  5. Quo (OpenPhone) is never a sender.
"""
from datetime import UTC, datetime, timedelta

import pytest
from app import automations
from app.db import SessionLocal
from app.models import (
    Appointment,
    Contact,
    Conversation,
    ConversationEvent,
    Direction,
    Job,
    NumberThread,
    NumberThreadEvent,
    Opportunity,
    Pipeline,
    Stage,
)
from app.queue import enqueue
from app.worker import drain_once
from tests import test_crm_link as crm
from tests.test_crm_link import thread


# The owen-main double and the seeded world of tests/test_crm_link.py, reused as they are.
@pytest.fixture()
def world():
    return crm.world.__wrapped__()


@pytest.fixture()
def link(monkeypatch):
    return crm.link.__wrapped__(monkeypatch)


QUEUED = {"ok": True, "message_id": "msg-live-1", "status": "queued"}
STRANGER = "+19415550199"


def count(model) -> int:
    with SessionLocal() as db:
        return db.query(model).count()


def sms_posts(rec) -> list[dict]:
    return [c for c in rec.calls if c["url"].endswith("/api/crm-link/messages")]


def outbound_rows() -> int:
    with SessionLocal() as db:
        return (db.query(ConversationEvent).filter(
                    ConversationEvent.direction == Direction.OUTBOUND).count()
                + db.query(NumberThreadEvent).filter(
                    NumberThreadEvent.direction == Direction.OUTBOUND).count())


# --- 1. no automatic texts --------------------------------------------------------------


def test_every_rule_that_texts_a_customer_is_off():
    assert automations.MISSED_CALL_TEXTBACK_ENABLED is False
    assert automations.APPOINTMENT_REMINDERS_ENABLED is False
    assert automations.STAGE_CHANGE_TEXT_ENABLED is False
    rules = {r["key"]: r for r in automations.rules()}
    assert set(rules) == set(automations.HANDLERS) - {"ai_agent_run"}
    for key, r in rules.items():
        if r["texts_customer"]:
            assert r["enabled"] is False and r["reason"].startswith("Off."), key


def test_the_automations_route_reports_the_flags_to_everyone(world):
    client, _, as_ = world
    for who in ("admin", "dispatcher", "tech"):
        got = client.get("/api/automations", headers=as_(who)).json()["rules"]
        by = {r["key"]: r for r in got}
        assert by["appointment_reminder"]["enabled"] is False
        assert "only texts a person sends" in by["appointment_reminder"]["reason"]
        assert by["stage_change_notify"]["enabled"] is False
        assert by["missed_call_textback"]["enabled"] is False
        assert by["new_lead_notify"]["texts_customer"] is False
    assert client.get("/api/automations").status_code == 401


def test_booking_an_appointment_queues_no_reminder_and_says_why(world, link):
    client, ids, as_ = world
    start = datetime.now(UTC) + timedelta(days=3)
    r = client.post("/api/appointments", headers=as_("dispatcher"), json={
        "title": "Roof inspection", "contact_id": ids["jane"],
        "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=1)).isoformat()})
    assert r.status_code in (200, 201), r.text
    with SessionLocal() as db:
        assert db.query(Job).filter(Job.type == "appointment_reminder").count() == 0
    appt = r.json()
    # A reschedule re-asks the rule — still nothing.
    moved = start + timedelta(days=1)
    p = client.patch("/api/appointments/%d" % appt["id"], headers=as_("dispatcher"), json={
        "starts_at": moved.isoformat(), "ends_at": (moved + timedelta(hours=1)).isoformat()})
    assert p.json()["automation"] == automations.APPOINTMENT_REMINDERS_DISABLED
    with SessionLocal() as db:
        assert db.query(Job).filter(Job.type == "appointment_reminder").count() == 0
    assert sms_posts(link) == []


def _deal(ids) -> tuple[int, int, int]:
    with SessionLocal() as db:
        p = Pipeline(name="Main")
        db.add(p)
        db.flush()
        a, b = Stage(pipeline_id=p.id, name="New Lead", position=0), \
            Stage(pipeline_id=p.id, name="Inspection", position=1)
        db.add_all([a, b])
        db.flush()
        o = Opportunity(title="Jane roof", contact_id=ids["jane"], pipeline_id=p.id,
                        stage_id=a.id)
        db.add(o)
        db.commit()
        return o.id, a.id, b.id


def test_a_stage_move_queues_no_text_but_still_asks_the_ai_trigger(world, link, monkeypatch):
    client, ids, as_ = world
    opp, _, inspection = _deal(ids)
    from app.ai import triggers
    asked = []
    monkeypatch.setattr(triggers, "stage_entered", lambda db, o: asked.append(o.stage_id))
    r = client.patch("/api/opportunities/%d" % opp, headers=as_("dispatcher"),
                     json={"stage_id": inspection})
    assert r.status_code == 200, r.text
    assert r.json()["automation"] == automations.STAGE_CHANGE_TEXT_DISABLED
    assert asked == [inspection], "the AI agents' stage-entered trigger must still run"
    with SessionLocal() as db:
        assert db.query(Job).filter(Job.type == "stage_change_notify").count() == 0
        assert db.get(Opportunity, opp).stage_id == inspection
    assert sms_posts(link) == []


def test_reminder_and_stage_jobs_already_queued_send_nothing(world, link):
    """Jobs queued before deploy: the worker refuses them. Re-read afterwards."""
    _, ids, _ = world
    link.reply("/api/crm-link/messages", 200, QUEUED)
    opp, _, inspection = _deal(ids)
    with SessionLocal() as db:
        start = datetime.now(UTC) + timedelta(hours=30)
        a = Appointment(title="Visit", contact_id=ids["jane"], starts_at=start,
                        ends_at=start + timedelta(hours=1))
        db.add(a)
        db.flush()
        enqueue(db, "appointment_reminder", {"appointment_id": a.id, "offset": "24h"})
        enqueue(db, "stage_change_notify", {"opportunity_id": opp, "stage_id": inspection})
        db.commit()
    before = outbound_rows()
    ran = 0
    while True:
        n = drain_once()
        if not n:
            break
        ran += n
    assert ran == 2
    assert outbound_rows() == before, "a queued automatic text wrote an outbound row"
    assert sms_posts(link) == [], "a queued automatic text reached owen-main"
    with SessionLocal() as db:
        assert sorted(j.status for j in db.query(Job).all()) == ["done", "done"]


# --- 2. New message to any number -------------------------------------------------------


def test_new_message_to_a_contacts_number_uses_their_conversation(world, link):
    client, ids, as_ = world
    link.reply("/api/crm-link/messages", 200, QUEUED)
    contacts, threads = count(Contact), count(NumberThread)
    r = client.post("/api/messages/new", headers=as_("dispatcher"),
                    json={"number": "941.555.0101", "body": "We can be there Tuesday"}).json()
    assert r["recorded"] is True and r["kind"] == "contact"
    assert r["conversation_id"] == ids["conv"] and r["key"] == "c%d" % ids["conv"]
    assert r["reason"] == "queued" and r["delivery_status"] == "QUEUED"
    assert (count(Contact), count(NumberThread)) == (contacts, threads)
    posts = sms_posts(link)
    assert len(posts) == 1
    assert posts[0]["json"] == {"from_number": "+19544829099", "to_number": "+19415550101",
                                "body": "We can be there Tuesday"}
    assert thread(client, as_, ids["conv"])[-1]["body"] == "We can be there Tuesday"


def test_new_message_to_an_unknown_number_makes_a_number_thread_and_no_contact(world, link):
    client, _, as_ = world
    link.reply("/api/crm-link/messages", 200, QUEUED)
    contacts = count(Contact)
    first = client.post("/api/messages/new", headers=as_("dispatcher"),
                        json={"number": "(941) 555-0199", "body": "Hi, Dream Team Roofing"}).json()
    assert first["recorded"] is True and first["kind"] == "number"
    assert first["key"] == "n%d" % first["number_thread_id"] and first["contact_id"] is None
    assert count(Contact) == contacts, "New message created a contact"
    assert count(NumberThread) == 1

    link.reply("/api/crm-link/messages", 200, {**QUEUED, "message_id": "msg-live-2"})
    second = client.post("/api/messages/new", headers=as_("dispatcher"),
                         json={"number": "+1 941 555 0199", "body": "Following up"}).json()
    assert second["number_thread_id"] == first["number_thread_id"], "a second thread was made"
    assert count(NumberThread) == 1 and count(Contact) == contacts
    assert [p["json"]["to_number"] for p in sms_posts(link)] == [STRANGER, STRANGER]

    # It is in the inbox like any unknown number, newest first, with both texts.
    inbox = client.get("/api/conversations", headers=as_("dispatcher")).json()
    assert inbox[0]["key"] == first["key"] and inbox[0]["kind"] == "number"
    events = client.get("/api/number-threads/%d/events" % first["number_thread_id"],
                        headers=as_("dispatcher")).json()
    assert [e["body"] for e in events] == ["Hi, Dream Team Roofing", "Following up"]
    assert all(e["source_system"] == "BulkVS" and e["source_number"] == "+19544829099"
               for e in events)


def test_an_invalid_number_writes_nothing_and_says_why(world, link):
    client, _, as_ = world
    before = (count(Contact), count(NumberThread), outbound_rows())
    for number, words in (("941-555-01", "too short"), ("", "Enter a number to text"),
                          ("+44 20 7946 0958", "US and Canadian"),
                          ("(054) 482-9099", "not a valid US number"),
                          ("(954) 482-9099", "own number")):
        r = client.post("/api/messages/new", headers=as_("admin"),
                        json={"number": number, "body": "hello"}).json()
        assert r["recorded"] is False and words in r["reason"], (number, r)
    assert (count(Contact), count(NumberThread), outbound_rows()) == before
    assert sms_posts(link) == []
    assert client.post("/api/messages/new", headers=as_("admin"),
                       json={"number": STRANGER, "body": "   "}).status_code == 400
    assert count(NumberThread) == 0


def test_new_message_to_a_contact_on_dnd_is_suppressed_and_writes_nothing(world, link):
    client, ids, as_ = world
    before = outbound_rows()
    r = client.post("/api/messages/new", headers=as_("admin"),
                    json={"number": "(941) 555-0202", "body": "hello"}).json()
    assert r["recorded"] is False and "DND" in r["reason"]
    assert outbound_rows() == before and sms_posts(link) == []


def test_the_server_and_the_browser_agree_on_every_sentence():
    """`textProblem` in lib/dialPad.ts mirrors `text_problem`; asserted there under node."""
    from app.main import text_problem
    assert text_problem("(941) 555-0199") == (STRANGER, None)
    assert text_problem("19415550199") == (STRANGER, None)


# --- 3. the composer: transport, refusal, failure, receipts -----------------------------


def test_the_composer_on_a_number_thread_calls_the_transport_once_normalised(world, link):
    client, _, as_ = world
    with SessionLocal() as db:
        t = NumberThread(phone="941-555-0199", phone_key="9415550199", unread_count=0)
        db.add(t)
        db.commit()
        tid = t.id
    link.reply("/api/crm-link/messages", 200, QUEUED)
    r = client.post("/api/number-threads/%d/messages" % tid, headers=as_("tech"),
                    json={"body": "On my way", "type": "SMS"}).json()
    assert r["reason"] == "queued"
    posts = sms_posts(link)
    assert len(posts) == 1 and posts[0]["json"]["to_number"] == STRANGER


def test_refused_keeps_owen_mains_sentence_and_failed_is_distinct(world, link, monkeypatch):
    client, ids, as_ = world
    link.reply("/api/crm-link/messages", 409, {"detail": "this contact has opted out of SMS"})
    r = client.post("/api/conversations/%d/messages" % ids["conv"], headers=as_("dispatcher"),
                    json={"body": "Are you home?", "type": "SMS"}).json()
    assert r["reason"].startswith("refused: ") and "opted out" in r["reason"]
    row = thread(client, as_, ids["conv"])[-1]
    assert row["delivery_status"] == "REFUSED"
    assert "opted out" in row["delivery_detail"] and "STOP" in row["delivery_detail"]

    link.reply("/api/crm-link/messages", 409, {"detail": "this contact is blocked in OWEN"})
    client.post("/api/conversations/%d/messages" % ids["conv"], headers=as_("dispatcher"),
                json={"body": "x", "type": "SMS"})
    assert "blocked" in thread(client, as_, ids["conv"])[-1]["delivery_detail"]

    link.reply("/api/crm-link/messages", 403,
               {"detail": "CRM-link SMS is dark (CRM_LINK_SMS_ENABLED=false)"})
    client.post("/api/conversations/%d/messages" % ids["conv"], headers=as_("dispatcher"),
                json={"body": "y", "type": "SMS"})
    dark = thread(client, as_, ids["conv"])[-1]["delivery_detail"]
    assert "switched off" in dark and "10DLC" not in dark and "approval" not in dark

    from app import crmlink

    def boom(*a, **k):
        raise TimeoutError("owen-main timed out")
    monkeypatch.setattr(crmlink.httpx, "post", boom)
    f = client.post("/api/conversations/%d/messages" % ids["conv"], headers=as_("dispatcher"),
                    json={"body": "retry me", "type": "SMS"}).json()
    assert f["reason"].startswith("failed: ")
    last = thread(client, as_, ids["conv"])[-1]
    assert last["delivery_status"] == "FAILED" and last["body"] == "retry me"


def test_a_delivery_receipt_advances_the_bubble(world, link):
    client, _, as_ = world
    link.reply("/api/crm-link/messages", 200, QUEUED)
    sent = client.post("/api/messages/new", headers=as_("dispatcher"),
                       json={"number": STRANGER, "body": "Quote attached"}).json()
    tid = sent["number_thread_id"]

    def status():
        return client.get("/api/number-threads/%d/events" % tid,
                          headers=as_("dispatcher")).json()[-1]["delivery_status"]
    assert status() == "QUEUED"
    for word, want in (("sent", "SENT"), ("delivered", "DELIVERED"), ("sent", "DELIVERED")):
        r = client.post("/api/events/delivery", headers=as_("feed"),
                        json={"provider_ref": "msg-live-1", "status": word})
        assert r.status_code == 200, r.text
        assert status() == want, (word, want)


# --- 4. inbound from an unknown number --------------------------------------------------


def test_an_inbound_text_from_an_unknown_number_makes_no_contact(world):
    """The body owen-main's `to_crm_message_event` builds, MMS note included."""
    client, _, as_ = world
    contacts = count(Contact)
    body = {"type": "SMS", "direction": "INBOUND", "from_number": STRANGER,
            "body": "Here is the leak [2 attachments — view in OWEN]",
            "provider_ref": "in-1", "source_system": "BulkVS", "source_number": "+19544829099"}
    r = client.post("/api/events", headers=as_("feed"), json=body).json()
    assert r["contact_id"] is None and r["number_thread_id"]
    assert count(Contact) == contacts
    client.post("/api/events", headers=as_("feed"), json={**body, "provider_ref": "in-2",
                                                          "body": "hello?"})
    row = next(c for c in client.get("/api/conversations", headers=as_("admin")).json()
               if c["key"] == "n%d" % r["number_thread_id"])
    assert row["unread_count"] == 2
    assert client.get("/api/conversations?tab=unread",
                      headers=as_("admin")).json()[0]["key"] == row["key"]


# --- 5. Quo is never a sender -----------------------------------------------------------


def test_a_quo_number_is_never_the_sender(world, link):
    """Whatever the client sends, the text leaves on the bound BulkVS DID."""
    client, ids, as_ = world
    link.reply("/api/crm-link/messages", 200, QUEUED)
    quo = "+19549147244"
    with SessionLocal() as db:
        conv = db.get(Conversation, ids["conv"])
        db.add(ConversationEvent(conversation_id=conv.id, type="SMS", direction="INBOUND",
                                 body="via Quo", source_system="OpenPhone",
                                 source_number=quo))
        db.commit()
    client.post("/api/messages/new", headers=as_("admin"),
                json={"number": "(941) 555-0101", "body": "a", "from_number": quo})
    client.post("/api/conversations/%d/messages" % ids["conv"], headers=as_("admin"),
                json={"body": "b", "type": "SMS", "from_number": quo})
    posts = sms_posts(link)
    assert len(posts) == 2
    assert {p["json"]["from_number"] for p in posts} == {"+19544829099"}
    outbound = [e for e in thread(client, as_, ids["conv"]) if e["direction"] == "OUTBOUND"]
    assert {e["source_system"] for e in outbound} == {"BulkVS"}
    assert quo not in {e["source_number"] for e in outbound}

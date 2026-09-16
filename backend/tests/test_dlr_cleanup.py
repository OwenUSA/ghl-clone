"""Carrier delivery receipts are not customers' texts (2026-09-16).

BulkVS posts delivery receipts to the same webhook as an inbound message. owen-main stored
each one as an inbound text and relayed it here, so this CRM filed them on customers'
threads as words the customers had written — measured on production, one number-only thread
holding two:

    id:1162999967 sub:001 dlvrd:000 submit date:2609160247 done date:2609160247
    stat:UNDELIV err:255 text:Dream Te...

Two halves are proved here:

  * **the guard** — `POST /api/events` refuses to file one as a message, whatever an older
    owen-main relays, and a REAL customer text is never mistaken for one;
  * **the cleanup** — `app/dlr_cleanup.py` takes the ones already on threads off them,
    repairs what they broke while they were there, and never touches anything else.

Behaviour, not status codes: every refusal and every sweep is re-read from the thread
afterwards.
"""
import json

import pytest
from app import dlr, dlr_cleanup
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Contact,
    Conversation,
    ConversationEvent,
    Direction,
    EventType,
    NumberThread,
    NumberThreadEvent,
    Role,
    User,
)
from fastapi.testclient import TestClient

UNDELIV = ("id:1162999967 sub:001 dlvrd:000 submit date:2609160247 done date:2609160247 "
           "stat:UNDELIV err:255 text:Dream Te...")
DELIVRD = ("id:1162999968 sub:001 dlvrd:001 submit date:2609160247 done date:2609160248 "
           "stat:DELIVRD err:000 text:Dream Te...")


@pytest.fixture()
def world():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    admin = User(email="owner@dlr.test", name="Owner", role=Role.ADMIN)
    db.add(admin)
    db.flush()
    jane = Contact(first_name="Jane", last_name="Doe", phone="(941) 555-0101")
    db.add(jane)
    db.flush()
    conv = Conversation(contact_id=jane.id)
    db.add(conv)
    db.flush()
    plain, tok = mint_api_token(admin, name="admin")
    db.add(tok)
    feed_plain, feed_tok = mint_api_token(admin, name="owen", scopes="events:write")
    db.add(feed_tok)
    db.commit()
    ids = {"conv": conv.id, "jane": jane.id}
    db.close()

    client = TestClient(app)
    tokens = {"admin": plain, "feed": feed_plain}

    def as_(who: str) -> dict:
        return {"Authorization": "Bearer " + tokens[who]}

    return client, ids, as_


def ingest(client, as_, body, number="+19415550101", **extra):
    payload = {"type": "SMS", "direction": "INBOUND", "from_number": number, "body": body,
               "source_system": "BulkVS", "source_number": "+19547758492"}
    payload.update(extra)
    r = client.post("/api/events", json=payload, headers=as_("feed"))
    assert r.status_code in (200, 201), r.text
    return r.json()


def thread(client, as_, conv_id, who="admin"):
    return client.get("/api/conversations/%d/events" % conv_id, headers=as_(who)).json()


# ---- what is a receipt --------------------------------------------------------------------


def test_a_receipt_is_recognised_and_a_customers_words_never_are():
    """The guard behind everything this module deletes."""
    assert dlr.looks_like_receipt(UNDELIV)
    assert dlr.looks_like_receipt(DELIVRD)
    for text in (
        "can you come Tuesday?",
        "id:",
        "My id:12 is on the invoice",
        "id:1 sub:1 dlvrd:1 stat:DELIVRD",                       # missing fields
        ("please call about id:99 sub:001 dlvrd:000 submit date:2609160247 "
         "done date:2609160247 stat:UNDELIV err:255"),           # not anchored
        "Dream Team Roofing: your appointment is submit date:tomorrow",
        "", None,
    ):
        assert not dlr.looks_like_receipt(text), text

    facts = dlr.fields(UNDELIV)
    assert facts == {"id": "1162999967", "submit": "2609160247", "stat": "UNDELIV",
                     "err": "255", "text": "Dream Te..."}
    assert dlr.fields("can you come Tuesday?") is None


def test_this_crms_pattern_matches_owen_mains_exactly():
    """Two repositories, two copies, deployed separately — so they must agree about what a
    receipt is. The duplication is deliberate (a guard that needed the other side deployed
    first would not be a guard); the drift is not."""
    from pathlib import Path

    # The owen-main half ships on its own branch (`feature/mms-media-relay`), so look in the
    # worktree it is being built in as well as the checkout it will be merged into. Skipped
    # rather than failed while neither has it: this CRM's guard does not depend on owen-main
    # having been deployed, which is the whole reason there are two copies.
    candidates = [Path("/home/qa/wt/mms-owen/backend/app/providers/bulkvs.py"),
                  Path("/home/qa/owen-main/backend/app/providers/bulkvs.py")]
    text = ""
    for owen in candidates:
        if owen.exists() and "_DLR = re.compile" in owen.read_text(encoding="utf-8"):
            text = owen.read_text(encoding="utf-8")
            break
    if not text:
        pytest.skip("owen-main's delivery-receipt parser is not merged here yet")
    for field in ("id:", "sub:", "dlvrd:", "submit", "done", "stat:", "err:"):
        assert field in text, "owen-main's receipt pattern lost %r" % field


# ---- the guard on ingest --------------------------------------------------------------------


def test_a_receipt_relayed_as_a_message_is_never_filed_on_a_thread(world):
    """The deploy-order case: an older owen-main is still relaying them."""
    client, ids, as_ = world
    before = len(thread(client, as_, ids["conv"]))

    out = ingest(client, as_, UNDELIV)
    assert out["id"] is None, "a receipt was written as an event"
    assert "delivery receipt" in out["automation"]
    assert len(thread(client, as_, ids["conv"])) == before, "a receipt reached the thread"

    db = SessionLocal()
    try:
        assert db.query(ConversationEvent).filter(
            ConversationEvent.body == UNDELIV).count() == 0
        assert db.query(NumberThread).count() == 0, "a receipt created a number thread"
    finally:
        db.close()


def test_a_receipt_from_an_unknown_number_creates_no_thread_and_no_contact(world):
    client, ids, as_ = world
    contacts = client.get("/api/contacts?page=1&page_size=1", headers=as_("admin")).json()
    out = ingest(client, as_, UNDELIV, number="+13055550123")
    assert out["id"] is None and out["number_thread_id"] is None
    after = client.get("/api/contacts?page=1&page_size=1", headers=as_("admin")).json()
    assert after["total"] == contacts["total"]
    db = SessionLocal()
    try:
        assert db.query(NumberThread).count() == 0
    finally:
        db.close()


def test_a_real_inbound_text_still_lands_exactly_as_it_did(world):
    """The guard must cost an ordinary message nothing."""
    client, ids, as_ = world
    out = ingest(client, as_, "can you come Tuesday?")
    assert out["id"] is not None
    rows = thread(client, as_, ids["conv"])
    assert rows[-1]["body"] == "can you come Tuesday?"
    assert rows[-1]["direction"] == "INBOUND"


def test_an_outbound_body_that_looks_like_one_is_left_alone(world):
    """The guard is scoped to INBOUND SMS. An outbound row carrying that text would be
    something we sent, and this is not the place to second-guess it."""
    client, ids, as_ = world
    out = ingest(client, as_, UNDELIV, direction="OUTBOUND")
    assert out["id"] is not None, "the guard reached past inbound messages"


# ---- the cleanup -----------------------------------------------------------------------------


def _plant(db, conv_id=None, thread_id=None, body=UNDELIV, direction=Direction.INBOUND,
           type_=EventType.SMS):
    """Put a row on a thread the way the old relay did, bypassing the new guard."""
    if conv_id is not None:
        ev = ConversationEvent(conversation_id=conv_id, type=type_, direction=direction,
                               body=body, source_system="BulkVS")
    else:
        ev = NumberThreadEvent(number_thread_id=thread_id, type=type_, direction=direction,
                               body=body, source_system="BulkVS")
    db.add(ev)
    db.flush()
    return ev


def test_the_dry_run_finds_them_and_writes_nothing(world):
    client, ids, as_ = world
    db = SessionLocal()
    try:
        _plant(db, conv_id=ids["conv"])
        _plant(db, conv_id=ids["conv"], body=DELIVRD)
        _plant(db, conv_id=ids["conv"], body="can you come Tuesday?")
        conv = db.get(Conversation, ids["conv"])
        conv.unread_count = 3
        db.commit()

        report = dlr_cleanup.sweep(db, commit=False)
        assert report["contact_thread_events"] == 2
        assert report["by_stat"] == {"UNDELIV": 1, "DELIVRD": 1}
        assert db.query(ConversationEvent).count() == 3, "the dry run deleted something"
        assert db.get(Conversation, ids["conv"]).unread_count == 3
    finally:
        db.close()

    rows = thread(client, as_, ids["conv"])
    assert len(rows) == 3, "the dry run changed the thread"


def test_commit_takes_them_off_the_thread_and_leaves_the_customer_alone(world):
    client, ids, as_ = world
    db = SessionLocal()
    try:
        _plant(db, conv_id=ids["conv"])
        _plant(db, conv_id=ids["conv"], body=DELIVRD)
        _plant(db, conv_id=ids["conv"], body="can you come Tuesday?")
        db.get(Conversation, ids["conv"]).unread_count = 3
        db.commit()
        report = dlr_cleanup.sweep(db, commit=True)
    finally:
        db.close()

    assert report["contact_thread_events"] == 2
    rows = thread(client, as_, ids["conv"])
    assert len(rows) == 1, "the receipts are still on the thread"
    assert rows[0]["body"] == "can you come Tuesday?", "the customer's text was removed"

    db = SessionLocal()
    try:
        conv = db.get(Conversation, ids["conv"])
        assert conv.unread_count == 1, (
            "the badge still counts messages that are no longer there")
        assert report["unread_badges_corrected"] == 1
    finally:
        db.close()


def test_an_emptied_number_only_thread_goes_with_them(world):
    """A thread a receipt CREATED, holding nothing else, is a row in the inbox for a
    conversation that never happened."""
    client, ids, as_ = world
    db = SessionLocal()
    try:
        t = NumberThread(phone="+13055550123", phone_key="3055550123", unread_count=2)
        db.add(t)
        db.flush()
        _plant(db, thread_id=t.id)
        _plant(db, thread_id=t.id, body=DELIVRD)
        keeper = NumberThread(phone="+13055550999", phone_key="3055550999", unread_count=1)
        db.add(keeper)
        db.flush()
        _plant(db, thread_id=keeper.id)
        _plant(db, thread_id=keeper.id, body="is someone coming today?")
        db.commit()
        junk_id, keeper_id = t.id, keeper.id

        dry = dlr_cleanup.sweep(db, commit=False)
        assert dry["number_thread_events"] == 3
        assert dry["empty_number_threads_removed"] == 1, "the dry run must predict it"

        report = dlr_cleanup.sweep(db, commit=True)
        assert report["empty_number_threads_removed"] == 1
        assert db.get(NumberThread, junk_id) is None
        surviving = db.get(NumberThread, keeper_id)
        assert surviving is not None, "a thread with a real message was removed"
        assert surviving.unread_count == 1
        left = db.query(NumberThreadEvent).filter(
            NumberThreadEvent.number_thread_id == keeper_id).all()
        assert [e.body for e in left] == ["is someone coming today?"]
    finally:
        db.close()

    inbox = client.get("/api/conversations", headers=as_("admin")).json()
    assert not [c for c in inbox if c["key"] == "n%d" % junk_id]


def test_it_is_idempotent(world):
    client, ids, as_ = world
    db = SessionLocal()
    try:
        _plant(db, conv_id=ids["conv"])
        db.commit()
        first = dlr_cleanup.sweep(db, commit=True)
        second = dlr_cleanup.sweep(db, commit=True)
    finally:
        db.close()
    assert first["contact_thread_events"] == 1
    assert second["contact_thread_events"] == 0, "a second run found something to do"
    assert second["threads_repaired"] == 0


def test_it_never_touches_a_call_a_note_or_an_outbound_row(world):
    """The sweep is scoped to INBOUND SMS. Everything else on a thread is somebody's record
    of what happened and is none of its business."""
    client, ids, as_ = world
    db = SessionLocal()
    try:
        _plant(db, conv_id=ids["conv"], type_=EventType.CALL, body=UNDELIV)
        _plant(db, conv_id=ids["conv"], type_=EventType.INTERNAL_COMMENT, body=UNDELIV)
        _plant(db, conv_id=ids["conv"], direction=Direction.OUTBOUND, body=UNDELIV)
        _plant(db, conv_id=ids["conv"])            # the one real receipt
        db.commit()
        report = dlr_cleanup.sweep(db, commit=True)
        assert report["contact_thread_events"] == 1
        assert db.query(ConversationEvent).count() == 3
    finally:
        db.close()


def test_it_never_touches_a_contact_or_anything_that_is_not_a_thread_event(world):
    client, ids, as_ = world
    db = SessionLocal()
    try:
        _plant(db, conv_id=ids["conv"])
        db.commit()
        contacts = db.query(Contact).count()
        dlr_cleanup.sweep(db, commit=True)
        assert db.query(Contact).count() == contacts
        assert db.get(Contact, ids["jane"]) is not None
        assert db.get(Conversation, ids["conv"]) is not None, "the thread itself was removed"
    finally:
        db.close()


def test_the_badge_is_only_ever_repaired_downwards(world):
    """A thread somebody had already read must not be marked unread again by a cleanup."""
    client, ids, as_ = world
    db = SessionLocal()
    try:
        _plant(db, conv_id=ids["conv"])
        _plant(db, conv_id=ids["conv"], body="can you come Tuesday?")
        db.get(Conversation, ids["conv"]).unread_count = 0
        db.commit()
        dlr_cleanup.sweep(db, commit=True)
        assert db.get(Conversation, ids["conv"]).unread_count == 0
    finally:
        db.close()


def test_the_report_carries_no_customer_data(world):
    """It is meant to be pasted into a ticket."""
    client, ids, as_ = world
    db = SessionLocal()
    try:
        _plant(db, conv_id=ids["conv"])
        _plant(db, conv_id=ids["conv"], body="can you come Tuesday?")
        db.commit()
        report = dlr_cleanup.sweep(db, commit=False)
    finally:
        db.close()
    blob = json.dumps(report) + dlr_cleanup.render(report, commit=False)
    for leak in ("Jane", "Doe", "9415550101", "555-0101", "can you come Tuesday"):
        assert leak not in blob, "the report leaked %r" % leak
    assert "DRY RUN" in dlr_cleanup.render(report, commit=False)
    assert "DRY RUN" not in dlr_cleanup.render(report, commit=True)


def test_the_command_defaults_to_a_dry_run():
    import inspect

    src = inspect.getsource(dlr_cleanup)
    assert '"--commit", action="store_true"' in src
    assert "sweep(db, args.commit)" in src, "the flag must be what decides"

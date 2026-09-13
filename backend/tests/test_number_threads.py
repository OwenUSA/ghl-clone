"""Number-only threads: every call and text, both lines, one inbox — and no junk contacts.

The owner's rules (DECISIONS.md, 2026-09-13), each asserted as BEHAVIOUR:

  * An unknown number — BulkVS or Quo, call or text — creates NO contact, NO
    opportunity and NO job. It gets a thread of its own that shows in the inbox,
    unread, marked as not a contact.
  * A known number lands on the contact's thread, labelled, in time order with every
    other event, whichever line it came through.
  * Nothing texts anybody back automatically — contacts included. Asserted on the
    `jobs` table, never on a response string.
  * A number-only thread is fully usable: it can be texted and called through the
    SAME guarded paths a contact can, and a refusal there writes what a refusal on a
    contact writes.
  * The moment a contact comes to exist with that number, by any path, the thread's
    whole history moves onto it: no event lost, none duplicated, dedupe keys intact.
  * The conversion command's dry run writes nothing, and `--commit` touches only the
    contacts that qualify.

owen-main is mocked at the HTTP boundary (`crmlink.httpx`), exactly as in
`test_crm_link.py`; nothing here can reach a live phone system.
"""
import csv
from datetime import UTC, datetime, timedelta

import pytest
from app import convert_auto_contacts, crmlink
from app import workiz_import as wi
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Contact,
    ContactTag,
    Conversation,
    ConversationEvent,
    EventType,
    Job,
    NumberThread,
    NumberThreadEvent,
    Opportunity,
    Role,
    Tag,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select

BULKVS_LINE = "+19544829099"
QUO_LINE = "+19417247244"
STRANGER = "+19415550199"
STRANGER_TYPED = "(941) 555-0199"


def now_iso(delta_minutes=0):
    return (datetime.now(UTC) + timedelta(minutes=delta_minutes)).isoformat()


# --- the world ------------------------------------------------------------------------


@pytest.fixture()
def world():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    tokens = {}
    for key, role in (("admin", Role.ADMIN), ("dispatcher", Role.DISPATCHER),
                      ("tech", Role.TECH)):
        u = User(email="%s@x.test" % key, name=key.title(), role=role)
        db.add(u)
        db.flush()
        plain, tok = mint_api_token(u, name=key)
        db.add(tok)
        tokens[key] = plain
        if key == "dispatcher":
            plain_feed, feed_tok = mint_api_token(u, name="owen", scopes="events:write")
            db.add(feed_tok)
            tokens["feed"] = plain_feed
    jane = Contact(first_name="Jane", last_name="Doe", phone="(941) 555-0101")
    db.add(jane)
    db.commit()
    ids = {"jane": jane.id}
    db.close()

    client = TestClient(app)

    def as_(who):
        return {"Authorization": "Bearer " + tokens[who]}

    return client, ids, as_


class Recorder:
    """A fake owen-main: records every request and answers what it is told to."""

    def __init__(self):
        self.calls = []
        self.answers = {}

    def post(self, url, json=None, headers=None, timeout=None, **kw):
        self.calls.append({"url": url, "json": json})
        status, body = self.answers.get(url.rsplit("/api/crm-link", 1)[-1],
                                        (200, {"ok": True}))
        return FakeResponse(status, body)

    def get(self, url, headers=None, timeout=None, **kw):
        self.calls.append({"url": url, "json": None})
        return FakeResponse(200, {"ok": True})


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = str(body)
        self.headers = {"content-type": "application/json"}
        self.content = b""

    def json(self):
        return self._body


@pytest.fixture()
def link(monkeypatch):
    rec = Recorder()
    monkeypatch.setenv("CRM_LINK_BASE_URL", "http://callmon_app:8888")
    monkeypatch.setenv("CRM_LINK_API_KEY", "owen_sk_test")
    monkeypatch.setenv("CRM_LINK_FROM_NUMBER", BULKVS_LINE)
    monkeypatch.setattr(crmlink.httpx, "post", rec.post)
    monkeypatch.setattr(crmlink.httpx, "get", rec.get)
    return rec


def ingest(client, as_, **body):
    return client.post("/api/events", json=body, headers=as_("feed"))


def counts():
    db = SessionLocal()
    try:
        return {m.__tablename__: db.scalar(select(func.count()).select_from(m)) or 0
                for m in (Contact, Opportunity, Job, Conversation, ConversationEvent,
                          NumberThread, NumberThreadEvent)}
    finally:
        db.close()


def jobs_of_type(kind):
    db = SessionLocal()
    try:
        return db.scalars(select(Job).where(Job.type == kind)).all()
    finally:
        db.close()


def inbox(client, as_, who="admin", **params):
    r = client.get("/api/conversations", params=params, headers=as_(who))
    assert r.status_code == 200, r.text
    return r.json()


def number_events(client, as_, thread_id, who="admin", filter="all"):
    return client.get("/api/number-threads/%d/events" % thread_id,
                      params={"filter": filter}, headers=as_(who))


# The four shapes of "an unknown number reached us": each line, call and text. The
# BulkVS bodies are what owen-main's CRM link sends; the Quo ones what the mirror sends.
UNKNOWN = {
    "bulkvs-call": {"from_number": STRANGER, "type": "CALL", "direction": "INBOUND",
                        "duration_seconds": 4, "call_status": "no-answer",
                        "provider_ref": "call-1", "source_system": "BulkVS",
                        "source_number": BULKVS_LINE},
    "bulkvs-text": {"from_number": STRANGER, "type": "SMS", "direction": "INBOUND",
                        "body": "Do you do skylights?", "provider_ref": "msg-1",
                        "source_system": "BulkVS", "source_number": BULKVS_LINE},
    "quo-call": {"from_number": STRANGER, "type": "CALL", "direction": "INBOUND",
                     "duration_seconds": 3, "call_status": "no-answer",
                     "dedupe_key": "openphone:call:AC1", "occurred_at": now_iso(-1),
                     "source_system": "OpenPhone", "source_number": QUO_LINE},
    "quo-text": {"from_number": STRANGER, "type": "SMS", "direction": "INBOUND",
                     "body": "WIN A FREE CRUISE", "dedupe_key": "openphone:message:M1",
                     "occurred_at": now_iso(-1), "source_system": "OpenPhone",
                     "source_number": QUO_LINE},
}


# --- 1. an unknown number is never a contact ------------------------------------------


@pytest.mark.parametrize("shape", sorted(UNKNOWN))
def test_an_unknown_number_creates_no_contact_no_deal_no_job_but_a_thread(world, shape):
    client, ids, as_ = world
    before = counts()
    r = ingest(client, as_, **UNKNOWN[shape])
    assert r.status_code == 201, r.text
    after = counts()

    assert after["contacts"] == before["contacts"], "an unknown number became a contact"
    assert after["opportunities"] == before["opportunities"]
    assert after["jobs"] == before["jobs"], "a job was queued (notify / text-back)"
    assert after["conversations"] == before["conversations"]
    assert after["number_threads"] == 1 and after["number_thread_events"] == 1
    assert r.json()["contact_id"] is None
    assert r.json()["number_thread_id"] is not None

    rows = [row for row in inbox(client, as_) if row["kind"] == "number"]
    assert len(rows) == 1
    row = rows[0]
    assert row["contact_phone"] == STRANGER
    assert row["phone_display"] == STRANGER_TYPED
    assert row["contact_id"] is None and row["contact_name"] is None
    assert row["unread_count"] == 1, "it must show unread"
    assert row["key"] == "n%d" % row["id"]

    ev = number_events(client, as_, row["id"]).json()
    assert len(ev) == 1
    assert ev[0]["source_system"] == UNKNOWN[shape]["source_system"]
    assert ev[0]["source_number"] == UNKNOWN[shape]["source_number"]


def test_a_second_event_from_the_same_number_lands_on_the_same_thread(world):
    client, _, as_ = world
    ingest(client, as_, **UNKNOWN["bulkvs-call"])
    # Written differently, same line: the last ten digits make it one number.
    ingest(client, as_, **{**UNKNOWN["bulkvs-text"], "from_number": STRANGER_TYPED})
    c = counts()
    assert c["number_threads"] == 1 and c["number_thread_events"] == 2
    assert [r["unread_count"] for r in inbox(client, as_) if r["kind"] == "number"] == [2]


def test_a_number_too_short_to_call_back_is_refused_and_writes_nothing(world):
    client, _, as_ = world
    before = counts()
    r = ingest(client, as_, from_number="555-0199", type="SMS", body="hi")
    assert r.status_code == 422
    assert counts() == before


def test_quos_name_is_shown_and_creates_nothing(world):
    client, _, as_ = world
    ingest(client, as_, **UNKNOWN["quo-text"], source_contact_name="Bob Builder")
    rows = [r for r in inbox(client, as_) if r["kind"] == "number"]
    assert rows[0]["quo_name"] == "Bob Builder"
    assert counts()["contacts"] == 1, "Quo's name must not create a contact"


# --- 2. a known number, both lines, one timeline -------------------------------------


def test_a_known_number_lands_on_the_contact_labelled_and_interleaved(world):
    client, ids, as_ = world
    base = datetime.now(UTC) - timedelta(hours=3)
    at = lambda m: (base + timedelta(minutes=m)).isoformat()  # noqa: E731
    # Delivered out of order on purpose: the thread orders by when it HAPPENED.
    ingest(client, as_, from_number="+19415550101", type="SMS", direction="INBOUND",
           body="quo 2", dedupe_key="openphone:message:Q2", occurred_at=at(30),
           source_system="OpenPhone", source_number=QUO_LINE)
    ingest(client, as_, from_number="9415550101", type="CALL", direction="INBOUND",
           duration_seconds=90, call_status="completed", occurred_at=at(20),
           source_system="BulkVS", source_number=BULKVS_LINE)
    ingest(client, as_, from_number="+19415550101", type="SMS", direction="INBOUND",
           body="quo 1", dedupe_key="openphone:message:Q1", occurred_at=at(10),
           source_system="OpenPhone", source_number=QUO_LINE)
    ingest(client, as_, from_number="(941) 555-0101", type="SMS", direction="OUTBOUND",
           body="bulkvs 3", occurred_at=at(40), source_system="BulkVS",
           source_number=BULKVS_LINE)

    assert counts()["number_threads"] == 0, "a known number must not get its own thread"
    conv = next(r for r in inbox(client, as_) if r["contact_id"] == ids["jane"])
    rows = client.get("/api/conversations/%d/events" % conv["id"],
                      headers=as_("admin")).json()
    assert [(r["source_system"], r["body"] or r["type"]) for r in rows] == [
        ("OpenPhone", "quo 1"), ("BulkVS", "CALL"), ("OpenPhone", "quo 2"),
        ("BulkVS", "bulkvs 3")]
    assert all(r["source_number"] for r in rows), "every row names its line"


# --- 3. nothing texts anybody back ----------------------------------------------------


@pytest.mark.parametrize("from_number", ["+19415550101", STRANGER])
def test_no_missed_call_text_back_is_ever_enqueued(world, from_number):
    client, _, as_ = world
    # The exact shape rule 1 used to fire on: inbound, short, fresh.
    r = ingest(client, as_, from_number=from_number, type="CALL", direction="INBOUND",
               duration_seconds=0, call_status="no-answer")
    assert r.status_code == 201, r.text
    assert jobs_of_type("missed_call_textback") == []
    assert jobs_of_type("new_lead_notify") == []


def test_a_text_back_job_queued_before_the_switch_sends_nothing(world):
    from app import automations

    db = SessionLocal()
    try:
        jane = db.scalar(select(Contact).where(Contact.first_name == "Jane"))
        automations._h_missed_call(db, {"contact_id": jane.id})
        db.commit()
        assert db.scalar(select(func.count()).select_from(ConversationEvent)) == 0
    finally:
        db.close()


# --- 4. the inbox treats both kinds the same ------------------------------------------


def _one_of_each(client, as_):
    ingest(client, as_, from_number="+19415550101", type="SMS", body="jane",
           occurred_at=now_iso(-30))
    ingest(client, as_, **{**UNKNOWN["bulkvs-text"], "occurred_at": now_iso(-5)})
    rows = inbox(client, as_)
    return (next(r for r in rows if r["kind"] == "contact"),
            next(r for r in rows if r["kind"] == "number"))


def test_the_list_is_mixed_by_last_activity_in_both_sort_orders(world):
    client, _, as_ = world
    _one_of_each(client, as_)
    assert [r["kind"] for r in inbox(client, as_)] == ["number", "contact"]
    assert [r["kind"] for r in inbox(client, as_, sort="oldest")] == ["contact", "number"]


def test_unread_tab_read_and_star_work_on_a_number_thread(world):
    client, _, as_ = world
    contact_row, number_row = _one_of_each(client, as_)
    assert {r["key"] for r in inbox(client, as_, tab="unread")} == {
        contact_row["key"], number_row["key"]}

    r = client.patch("/api/number-threads/%d" % number_row["id"], json={"read": True},
                     headers=as_("tech"))
    assert r.status_code == 200 and r.json()["unread_count"] == 0
    assert [x["key"] for x in inbox(client, as_, tab="unread")] == [contact_row["key"]]

    assert inbox(client, as_, tab="starred") == []
    client.patch("/api/number-threads/%d" % number_row["id"], json={"starred": True},
                 headers=as_("tech"))
    assert [x["key"] for x in inbox(client, as_, tab="starred")] == [number_row["key"]]


def test_assigned_to_me_leaves_number_threads_in_the_team_inbox_only(world):
    client, _, as_ = world
    _one_of_each(client, as_)
    assert not any(r["kind"] == "number" for r in inbox(client, as_, assigned="me"))
    assert any(r["kind"] == "number" for r in inbox(client, as_, assigned="all"))


@pytest.mark.parametrize("q", ["0199", "941-555-0199", STRANGER, "Builder"])
def test_the_inbox_search_finds_a_number_thread_by_number_and_quo_name(world, q):
    client, _, as_ = world
    ingest(client, as_, **UNKNOWN["quo-text"], source_contact_name="Bob Builder")
    ingest(client, as_, from_number="+19415550101", type="SMS", body="jane")
    rows = inbox(client, as_, q=q)
    assert [r["kind"] for r in rows] == ["number"]


def test_deleting_a_number_thread_is_admin_only_and_removes_only_it(world):
    client, _, as_ = world
    _, number_row = _one_of_each(client, as_)
    before = counts()
    r = client.delete("/api/number-threads/%d" % number_row["id"],
                      headers=as_("dispatcher"))
    assert r.status_code == 403
    assert counts() == before, "a refused delete must not have mutated anything"

    r = client.delete("/api/number-threads/%d" % number_row["id"], headers=as_("admin"))
    assert r.status_code == 200 and r.json()["events_deleted"] == 1
    after = counts()
    assert after["number_threads"] == 0 and after["number_thread_events"] == 0
    assert after["conversations"] == before["conversations"]
    assert after["conversation_events"] == before["conversation_events"]


def test_internal_notes_on_a_number_thread_are_staff_only(world):
    client, _, as_ = world
    ingest(client, as_, **UNKNOWN["bulkvs-text"])
    tid = next(r for r in inbox(client, as_) if r["kind"] == "number")["id"]

    r = client.post("/api/number-threads/%d/messages" % tid,
                    json={"type": "INTERNAL_COMMENT", "body": "spam, ignore"},
                    headers=as_("tech"))
    assert r.status_code == 403
    assert counts()["number_thread_events"] == 1, "a refused note wrote a row"

    r = client.post("/api/number-threads/%d/messages" % tid,
                    json={"type": "INTERNAL_COMMENT", "body": "spam, ignore"},
                    headers=as_("dispatcher"))
    assert r.status_code == 201 and r.json()["reason"] == "recorded"

    staff = [e["body"] for e in number_events(client, as_, tid, "dispatcher").json()]
    tech = [e["body"] for e in number_events(client, as_, tid, "tech").json()]
    assert "spam, ignore" in staff and "spam, ignore" not in tech
    assert number_events(client, as_, tid, "tech",
                         filter="internal_comment").status_code == 403


# --- 5. texting and calling a number nobody saved -------------------------------------


def test_texting_a_number_thread_is_logged_only_while_the_link_is_unarmed(world, monkeypatch):
    client, _, as_ = world
    monkeypatch.delenv("CRM_LINK_BASE_URL", raising=False)
    monkeypatch.delenv("CRM_LINK_API_KEY", raising=False)
    ingest(client, as_, **UNKNOWN["bulkvs-text"])
    tid = next(r for r in inbox(client, as_) if r["kind"] == "number")["id"]
    r = client.post("/api/number-threads/%d/messages" % tid,
                    json={"type": "SMS", "body": "Yes we do"}, headers=as_("tech"))
    assert r.status_code == 201, r.text
    assert r.json()["delivery_status"] == "LOGGED_ONLY"
    sent = [e for e in number_events(client, as_, tid).json() if e["direction"] == "OUTBOUND"]
    assert [(e["body"], e["delivery_status"], e["source_system"]) for e in sent] == [
        ("Yes we do", "LOGGED_ONLY", "BulkVS")]
    assert counts()["contacts"] == 1, "texting a number must not save it"


def test_texting_a_number_thread_is_refused_exactly_as_a_contact_is(world, link):
    client, ids, as_ = world
    dark = (403, {"detail": "CRM-link SMS is dark (CRM_LINK_SMS_ENABLED=false)"})
    link.answers["/messages"] = dark
    ingest(client, as_, **UNKNOWN["bulkvs-text"])
    tid = next(r for r in inbox(client, as_) if r["kind"] == "number")["id"]

    to_number = client.post("/api/number-threads/%d/messages" % tid,
                            json={"type": "SMS", "body": "hello"}, headers=as_("admin"))
    to_contact = client.post("/api/contacts/%d/messages" % ids["jane"],
                             json={"type": "SMS", "body": "hello"}, headers=as_("admin"))
    assert to_number.json()["delivery_status"] == to_contact.json()["delivery_status"] \
        == "REFUSED"
    assert to_number.json()["delivery_detail"] == to_contact.json()["delivery_detail"]
    # It went through the SAME transport, to the thread's own number, from BulkVS.
    posted = [c["json"] for c in link.calls if c["url"].endswith("/api/crm-link/messages")]
    assert posted[0]["to_number"] == STRANGER
    assert posted[0]["from_number"] == BULKVS_LINE


def test_calling_a_number_thread_uses_the_same_dialling_path(world, link):
    client, _, as_ = world
    ingest(client, as_, **UNKNOWN["bulkvs-call"])
    tid = next(r for r in inbox(client, as_) if r["kind"] == "number")["id"]

    # owen-main's allowlist refuses — the refusal comes back and nothing is written.
    link.answers["/calls"] = (403, {"detail": "destination is not on CRM_LINK_ALLOWLIST"})
    before = counts()
    r = client.post("/api/number-threads/%d/call" % tid, headers=as_("tech"))
    assert r.status_code == 200 and r.json()["placed"] is False
    assert counts() == before

    link.answers["/calls"] = (200, {"ok": True, "linkedid": "abc"})
    r = client.post("/api/number-threads/%d/call" % tid, headers=as_("tech"))
    assert r.json()["placed"] is True
    dialled = [c["json"] for c in link.calls if c["url"].endswith("/api/crm-link/calls")]
    assert [d["to_number"] for d in dialled] == [STRANGER, STRANGER]
    assert all(d["from_number"] == BULKVS_LINE for d in dialled)
    after = counts()
    assert after["number_thread_events"] == before["number_thread_events"] + 1
    assert after["contacts"] == before["contacts"], "calling a number must not save it"


def test_calling_is_refused_without_a_request_while_the_link_is_unarmed(world, monkeypatch):
    client, _, as_ = world
    monkeypatch.delenv("CRM_LINK_BASE_URL", raising=False)
    calls = []
    monkeypatch.setattr(crmlink.httpx, "post", lambda *a, **k: calls.append(a))
    ingest(client, as_, **UNKNOWN["bulkvs-call"])
    tid = next(r for r in inbox(client, as_) if r["kind"] == "number")["id"]
    before = counts()
    r = client.post("/api/number-threads/%d/call" % tid, headers=as_("admin"))
    assert r.json()["placed"] is False and calls == [] and counts() == before


def test_a_delivery_receipt_advances_a_text_sent_from_a_number_thread(world, link):
    client, _, as_ = world
    link.answers["/messages"] = (200, {"ok": True, "message_id": "om-77"})
    ingest(client, as_, **UNKNOWN["bulkvs-text"])
    tid = next(r for r in inbox(client, as_) if r["kind"] == "number")["id"]
    client.post("/api/number-threads/%d/messages" % tid,
                json={"type": "SMS", "body": "hi"}, headers=as_("admin"))
    r = client.post("/api/events/delivery", json={"provider_ref": "om-77",
                                                   "status": "delivered"},
                    headers=as_("feed"))
    assert r.status_code == 200, r.text
    sent = [e for e in number_events(client, as_, tid).json() if e["direction"] == "OUTBOUND"]
    assert sent[0]["delivery_status"] == "DELIVERED"


# --- 6. idempotency across both tables ------------------------------------------------


def test_the_same_quo_object_twice_is_one_event_and_later_parts_fill_it_in(world):
    client, _, as_ = world
    first = ingest(client, as_, **UNKNOWN["quo-call"])
    # The recording and transcript arrive later, for the same call (webhook parts).
    again = ingest(client, as_, **UNKNOWN["quo-call"],
                   recording_url="/api/openphone/recordings/AC1",
                   transcript="caller: hello?")
    assert again.json()["id"] == first.json()["id"]
    assert sorted(again.json()["enriched"]) == ["recording_url", "transcript"]
    c = counts()
    assert c["number_thread_events"] == 1
    ev = number_events(client, as_, first.json()["number_thread_id"]).json()[0]
    assert ev["recording_url"] == "/api/openphone/recordings/AC1"
    assert ev["transcript"] == "caller: hello?"
    # A later delivery never overwrites what is already recorded.
    ingest(client, as_, **UNKNOWN["quo-call"], transcript="something else")
    ev = number_events(client, as_, first.json()["number_thread_id"]).json()[0]
    assert ev["transcript"] == "caller: hello?"
    # A summary finished later is appended to the call's line once, never twice.
    for _ in range(2):
        ingest(client, as_, **UNKNOWN["quo-call"], summary="Wants a roof quote.")
    ev = number_events(client, as_, first.json()["number_thread_id"]).json()[0]
    assert (ev["body"] or "").count("Wants a roof quote.") == 1
    assert counts()["jobs"] == 0


# --- 7. adoption: a contact comes to exist with the number ----------------------------


def _stranger_history(client, as_):
    for shape in ("bulkvs-call", "quo-text", "bulkvs-text"):
        ingest(client, as_, **UNKNOWN[shape])
    tid = next(r for r in inbox(client, as_) if r["kind"] == "number")["id"]
    client.post("/api/number-threads/%d/messages" % tid,
                json={"type": "INTERNAL_COMMENT", "body": "real lead"},
                headers=as_("admin"))
    return tid, number_events(client, as_, tid).json()


def _payload(rows):
    keep = ("type", "direction", "body", "duration_seconds", "call_status",
            "recording_url", "delivery_status", "source_system", "source_number",
            "transcript")
    return sorted(tuple((k, r[k]) for k in keep) for r in rows)


def _dedupe_keys(model):
    db = SessionLocal()
    try:
        return sorted(k for k in db.scalars(select(model.dedupe_key)).all() if k)
    finally:
        db.close()


def test_add_as_contact_moves_every_event_and_keeps_dedupe_keys(world):
    client, _, as_ = world
    tid, history = _stranger_history(client, as_)
    keys = _dedupe_keys(NumberThreadEvent)
    assert keys == ["openphone:message:M1"]

    r = client.post("/api/contacts", json={"first_name": "Bob", "last_name": "Builder",
                                           "phone": STRANGER_TYPED},
                    headers=as_("dispatcher"))
    assert r.status_code == 201, r.text
    adopted = r.json()["adopted_number_thread"]
    assert adopted["number_thread_id"] == tid and adopted["events_moved"] == len(history)

    c = counts()
    assert c["number_threads"] == 0 and c["number_thread_events"] == 0
    conv_rows = client.get("/api/conversations/%d/events" % adopted["conversation_id"],
                           headers=as_("admin")).json()
    assert _payload(conv_rows) == _payload(history), "an event was lost or changed"
    assert [x["occurred_at"] for x in conv_rows] == sorted(x["occurred_at"] for x in conv_rows)
    assert _dedupe_keys(ConversationEvent) == keys

    # The key still works after the move: a repeat delivery lands on the moved row.
    again = ingest(client, as_, **UNKNOWN["quo-text"])
    assert again.json()["conversation_id"] == adopted["conversation_id"]
    assert counts()["conversation_events"] == len(history)
    # The unread badge came with it, and the number is gone from the list.
    rows = inbox(client, as_)
    assert not any(r["kind"] == "number" for r in rows)
    bob = next(r for r in rows if r["id"] == adopted["conversation_id"])
    assert bob["unread_count"] == 3, "the three unread inbound events came across"


def test_editing_a_contacts_phone_to_the_number_adopts_the_thread(world):
    client, ids, as_ = world
    ingest(client, as_, **UNKNOWN["bulkvs-text"])
    ingest(client, as_, from_number="+19415550101", type="SMS", body="jane's own")
    r = client.patch("/api/contacts/%d" % ids["jane"], json={"phone": STRANGER},
                     headers=as_("admin"))
    assert r.status_code == 200, r.text
    assert r.json()["adopted_number_thread"]["events_moved"] == 1
    c = counts()
    assert c["number_threads"] == 0
    assert c["conversations"] == 1 and c["conversation_events"] == 2


def test_a_contact_saved_by_any_other_code_path_adopts_the_thread(world):
    """The listener is on the Session, not on a route: a raw ORM write adopts too."""
    client, _, as_ = world
    ingest(client, as_, **UNKNOWN["quo-text"])
    db = SessionLocal()
    try:
        db.add(Contact(first_name="Raw", phone="9415550199", created_by="script"))
        db.commit()
    finally:
        db.close()
    c = counts()
    assert c["number_threads"] == 0 and c["conversation_events"] == 1


def test_the_workiz_import_adopts_a_thread_and_still_queues_nothing(world, tmp_path):
    client, _, as_ = world
    ingest(client, as_, **UNKNOWN["bulkvs-text"])
    cp, jp = str(tmp_path / "c.csv"), str(tmp_path / "j.csv")
    with open(cp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["Client #", "Name", "Email", "Address",
                                           "Phone", "Ad Source"])
        w.writeheader()
        w.writerow({"Client #": "1", "Name": "Bob Builder", "Email": "",
                    "Address": "", "Phone": "941-555-0199", "Ad Source": ""})
    with open(jp, "w", newline="") as fh:
        csv.DictWriter(fh, fieldnames=["Job #", "Client", "Phone", "Status"]).writeheader()
    db = SessionLocal()
    try:
        plan = wi.build_plan(db, wi.read_csv(cp), wi.read_csv(jp))
        wi.apply_plan(db, plan)
        db.commit()
    finally:
        db.close()
    c = counts()
    assert c["number_threads"] == 0 and c["conversation_events"] >= 1
    assert c["jobs"] == 0


def test_adoption_skips_an_event_the_contact_already_holds(world):
    client, _, as_ = world
    ingest(client, as_, **UNKNOWN["quo-text"])
    db = SessionLocal()
    try:
        # A key already on a contact thread (an inconsistency nothing should create,
        # but the unique index would turn it into a failed contact save).
        jane = db.scalar(select(Contact).where(Contact.first_name == "Jane"))
        conv = Conversation(contact_id=jane.id)
        db.add(conv)
        db.flush()
        db.add(ConversationEvent(conversation_id=conv.id, type=EventType.SMS,
                                 direction="INBOUND", body="x",
                                 dedupe_key="openphone:message:M1"))
        db.commit()
        db.add(Contact(first_name="Bob", phone=STRANGER))
        db.commit()
    finally:
        db.close()
    c = counts()
    assert c["number_threads"] == 0
    assert _dedupe_keys(ConversationEvent) == ["openphone:message:M1"]


def test_the_two_event_tables_carry_the_same_columns():
    conv = {c.name for c in inspect(ConversationEvent).columns} - {"conversation_id"}
    num = {c.name for c in inspect(NumberThreadEvent).columns} - {"number_thread_id"}
    assert conv == num
    from app.models import EVENT_PAYLOAD_COLUMNS
    assert set(EVENT_PAYLOAD_COLUMNS) == conv - {"id"}


# --- 8. converting the one auto-created contact back -----------------------------------


def _auto_contact(db, phone, **over):
    from app.phones import format_phone, store_phone

    stored = store_phone(phone)
    c = Contact(first_name=format_phone(stored), last_name="", phone=stored,
                source="Inbound call", created_by="owen-main", contact_type="Lead")
    for k, v in over.items():
        setattr(c, k, v)
    db.add(c)
    db.flush()
    conv = Conversation(contact_id=c.id, unread_count=1)
    db.add(conv)
    db.flush()
    db.add(ConversationEvent(conversation_id=conv.id, type=EventType.CALL,
                             direction="INBOUND", duration_seconds=5,
                             provider_ref="p-%s" % c.id))
    db.flush()
    return c


def test_the_conversion_dry_run_writes_nothing(world):
    db = SessionLocal()
    try:
        _auto_contact(db, "+19415550333")
        db.commit()
    finally:
        db.close()
    before = counts()
    db = SessionLocal()
    try:
        report = convert_auto_contacts.run(db, commit=False)
    finally:
        db.close()
    assert len(report["would_convert"]) == 1
    assert counts() == before


def test_the_conversion_commit_touches_only_qualifying_contacts(world):
    db = SessionLocal()
    try:
        ok = _auto_contact(db, "+19415550333")
        tagged = _auto_contact(db, "+19415550444")
        tag = Tag(name="vip")
        db.add(tag)
        db.flush()
        db.add(ContactTag(contact_id=tagged.id, tag_id=tag.id))
        renamed = _auto_contact(db, "+19415550555", first_name="Maria")
        manual = _auto_contact(db, "+19415550666", created_by="Manual")
        edited = _auto_contact(db, "+19415550777")
        edited.updated_at = edited.created_at + timedelta(days=1)
        db.commit()
        keep = {tagged.id, renamed.id, manual.id, edited.id}
        ok_id, manual_id = ok.id, manual.id
    finally:
        db.close()

    db = SessionLocal()
    try:
        report = convert_auto_contacts.run(db, commit=True)
    finally:
        db.close()
    assert [r["contact_id"] for r in report["converted"]] == [ok_id]
    # The Manual one is not even a candidate; the other three are named with reasons.
    assert {r["contact_id"] for r in report["left_alone"]} == keep - {manual_id}
    assert all(r["reasons"] for r in report["left_alone"])
    db = SessionLocal()
    try:
        remaining = set(db.scalars(select(Contact.id)).all())
        assert keep <= remaining and ok_id not in remaining
        t = db.scalar(select(NumberThread).where(NumberThread.phone_key == "9415550333"))
        assert t is not None and len(t.events) == 1 and t.unread_count == 1
        assert db.scalar(select(func.count()).select_from(Job)) == 0
    finally:
        db.close()

    # Idempotent: a second run has nothing left to do.
    db = SessionLocal()
    try:
        assert convert_auto_contacts.run(db, commit=True)["converted"] == []
    finally:
        db.close()


# --- 9. the browser: keys, not ids, and the not-a-contact affordances -----------------

FRONTEND = __import__("pathlib").Path(__file__).resolve().parents[2] / "frontend" / "src"


@pytest.mark.skipif(__import__("shutil").which("node") is None,
                    reason="node is not on PATH, so inbox.ts cannot be executed")
def test_marking_a_number_thread_read_does_not_clear_a_contact_thread_with_the_same_id():
    """Ids come from two tables. Clearing by id would clear the wrong badge."""
    import json
    import subprocess

    script = """
        import * as inbox from %s
        const rows = [{id: 1, key: 'c1', unread_count: 2}, {id: 1, key: 'n1', unread_count: 3}]
        const after = inbox.markReadIn(rows, 'n1')
        console.log('@@' + JSON.stringify({
          after, tab: inbox.unreadTabCount(after),
          legacy: inbox.markReadIn([{id: 7, unread_count: 1}], 7),
        }))
    """ % json.dumps((FRONTEND / "lib" / "inbox.ts").as_posix())
    proc = subprocess.run(["node", "--experimental-strip-types", "--input-type=module", "-"],
                          input=script, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    out = json.loads([ln for ln in proc.stdout.splitlines() if ln.startswith("@@")][-1][2:])
    assert [r["unread_count"] for r in out["after"]] == [2, 0]
    assert out["tab"] == 1
    assert out["legacy"] == [{"id": 7, "unread_count": 0}], "rows without a key still work"


def test_the_page_selects_by_key_and_shows_the_number_thread_affordances():
    page = (FRONTEND / "pages" / "ConversationsPage.tsx").read_text(encoding="utf-8")
    panel = (FRONTEND / "components" / "NumberDetailsPanel.tsx").read_text(encoding="utf-8")
    api = (FRONTEND / "lib" / "api.ts").read_text(encoding="utf-8")
    assert "useState<string | null>(null)" in page and "c.key === active" in page
    assert "c.id === active" not in page, "a row selected by id collides across kinds"
    # Every thread action goes through the row-addressed helpers.
    for helper in ("listThreadEvents(", "patchThread(", "deleteThread(", "sendToThread(",
                   "callThread("):
        assert helper in page, helper
    assert "`/api/number-threads/${t.id}`" in api
    # Not-a-contact marker on the row and in the header; Add as contact in the header
    # and in the right-hand panel instead of an empty contact.
    assert page.count("<NotAContactPill />") >= 2
    assert "Add as contact" in page and "Add as contact" in panel
    assert "<NumberDetailsPanel row={current}" in page
    assert "!isNumber && current.contact_id != null" in page, (
        "the contact panel would render an empty contact for a number thread")
    # Quo's name is labelled as Quo's, and the reply-line banner still names BulkVS.
    assert "from Quo" in panel and "<FromQuo />" in page
    assert "This reply goes from" in page and "'+19544829099'" in page

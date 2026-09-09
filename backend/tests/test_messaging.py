"""The endpoints the CLI needs: outbound send, global search, appointments, deletes.

Same standard as test_api.py — assert behaviour, not status codes. A suppressed send
must write nothing, a filter must actually narrow the set, and a 409 must not have
half-performed the delete.
"""
from datetime import UTC, datetime, timedelta

import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Contact,
    ContactTag,
    Conversation,
    ConversationEvent,
    Direction,
    EventType,
    Job,
    Opportunity,
    Pipeline,
    Role,
    Stage,
    Tag,
    User,
)
from fastapi.testclient import TestClient


def utcnow():
    return datetime.now(UTC)


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    admin = User(email="admin@x.test", name="Admin", role=Role.ADMIN)
    db.add(admin)
    db.flush()

    reachable = Contact(first_name="Reach", last_name="Able",
                        phone="(941) 555-0001", email="reach@x.test")
    dnd = Contact(first_name="Do", last_name="NotDisturb",
                  phone="(941) 555-0002", dnd=True)
    nophone = Contact(first_name="No", last_name="Phone", email="np@x.test")
    db.add_all([reachable, dnd, nophone])
    db.flush()

    conv = Conversation(contact_id=reachable.id, unread_count=3)
    db.add(conv)
    db.flush()

    # Six calls spanning status, direction, duration and date.
    base = utcnow() - timedelta(days=10)
    calls = [
        (Direction.INBOUND, "completed", 300, base + timedelta(days=1),
         "roof leak in the kitchen"),
        (Direction.INBOUND, "no-answer", 4, base + timedelta(days=2), None),
        (Direction.OUTBOUND, "completed", 120, base + timedelta(days=3),
         "discussed the skylight replacement"),
        (Direction.INBOUND, "voicemail", 20, base + timedelta(days=4), None),
        (Direction.OUTBOUND, "busy", 2, base + timedelta(days=5), None),
        (Direction.INBOUND, "completed", 600, base + timedelta(days=6),
         "insurance adjuster visit"),
    ]
    for direction, status, dur, when, transcript in calls:
        db.add(ConversationEvent(
            conversation_id=conv.id, type=EventType.CALL, direction=direction,
            occurred_at=when, duration_seconds=dur, call_status=status,
            transcript=transcript,
            recording_url="/media/rec-%d.mp3" % dur if dur > 60 else None))
    db.add(ConversationEvent(
        conversation_id=conv.id, type=EventType.SMS, direction=Direction.INBOUND,
        occurred_at=base + timedelta(days=7), body="can you come tuesday"))

    pipe = Pipeline(name="AHS")
    db.add(pipe)
    db.flush()
    stage = Stage(pipeline_id=pipe.id, name="New Lead", position=0)
    db.add(stage)
    db.flush()
    opp = Opportunity(title="Roof", contact_id=reachable.id, pipeline_id=pipe.id,
                      stage_id=stage.id, value_cents=500000,
                      custom_fields={"owen_call_id": "call-abc-123"})
    db.add(opp)

    tag = Tag(name="ahs")
    db.add(tag)
    db.flush()
    db.add(ContactTag(contact_id=reachable.id, tag_id=tag.id))

    plain, token = mint_api_token(admin, name="test")
    db.add(token)
    db.commit()

    ids = {"reachable": reachable.id, "dnd": dnd.id, "nophone": nophone.id,
           "conv": conv.id, "opp": opp.id, "pipeline": pipe.id,
           "stage": stage.id, "admin": admin.id}
    db.close()

    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + plain
        c.ids = ids
        yield c


def events_on(conv_id):
    db = SessionLocal()
    n = db.query(ConversationEvent).filter(
        ConversationEvent.conversation_id == conv_id).count()
    db.close()
    return n


# ---------------- outbound send ----------------

def test_sending_records_the_message_but_never_transmits_it(client):
    r = client.post("/api/contacts/%d/messages" % client.ids["reachable"],
                    json={"body": "we can be there tuesday"})
    assert r.status_code == 201
    b = r.json()
    assert b["suppressed"] is False
    # LoggingTransport is the only transport: intent recorded, nothing sent.
    assert b["delivery_status"] == "LOGGED_ONLY"
    assert b["direction"] == "OUTBOUND"

    thread = client.get("/api/conversations/%d/events" % b["conversation_id"]).json()
    assert any(e["body"] == "we can be there tuesday" for e in thread)


def test_dnd_suppresses_the_send_and_writes_nothing(client):
    """A 201 that still wrote the row would pass a status-only assertion."""
    before = events_on(client.ids["conv"])
    r = client.post("/api/contacts/%d/messages" % client.ids["dnd"],
                    json={"body": "should never be recorded"})
    assert r.status_code == 201
    assert r.json()["suppressed"] is True
    assert "DND" in r.json()["reason"]

    db = SessionLocal()
    total = db.query(ConversationEvent).count()
    db.close()
    assert total == before, "a suppressed send must not create an event"


def test_a_contact_with_no_phone_is_suppressed(client):
    r = client.post("/api/contacts/%d/messages" % client.ids["nophone"],
                    json={"body": "hello"})
    assert r.json()["suppressed"] is True
    assert "phone" in r.json()["reason"]


def test_a_note_is_recorded_even_for_a_contact_on_dnd(client):
    """DND is a promise to the customer, not a mute on our own notes."""
    r = client.post("/api/contacts/%d/messages" % client.ids["dnd"],
                    json={"body": "customer asked for no texts", "type": "NOTE"})
    assert r.status_code == 201
    assert r.json()["suppressed"] is False
    assert r.json()["delivery_status"] is None, "a note is not transmitted at all"


def test_an_empty_message_is_rejected(client):
    r = client.post("/api/contacts/%d/messages" % client.ids["reachable"],
                    json={"body": "   "})
    assert r.status_code == 400


def test_sending_to_an_unknown_contact_is_404(client):
    assert client.post("/api/contacts/99999/messages",
                       json={"body": "hi"}).status_code == 404


# ---------------- call search ----------------

def test_call_search_returns_only_calls(client):
    items = client.get("/api/calls").json()["items"]
    assert len(items) == 6
    assert all("duration_seconds" in i for i in items)


def test_duration_filters_actually_narrow_the_set(client):
    all_calls = client.get("/api/calls").json()
    long_calls = client.get("/api/calls?min_duration=100").json()
    assert long_calls["total"] < all_calls["total"]
    assert long_calls["total"] == 3
    assert all(i["duration_seconds"] >= 100 for i in long_calls["items"])

    short = client.get("/api/calls?max_duration=10").json()
    assert all(i["duration_seconds"] <= 10 for i in short["items"])


def test_direction_and_status_filters(client):
    inbound = client.get("/api/calls?direction=INBOUND").json()
    assert all(i["direction"] == "INBOUND" for i in inbound["items"])
    assert inbound["total"] == 4

    missed = client.get("/api/calls?call_status=no-answer,busy").json()
    assert {i["call_status"] for i in missed["items"]} == {"no-answer", "busy"}


def test_unknown_call_status_is_rejected(client):
    """Mirrors the existing contract that an unknown thread filter is a 400."""
    r = client.get("/api/calls?call_status=nonsense")
    assert r.status_code == 400
    assert "nonsense" in r.json()["detail"]


def test_search_matches_the_transcript_not_just_the_body(client):
    """The point of `ghl calls list --q`: none of these calls has a body."""
    r = client.get("/api/calls?q=skylight").json()
    assert r["total"] == 1
    hit = r["items"][0]
    assert hit["body"] is None
    assert "skylight" in hit["transcript"]


def test_recording_filter(client):
    with_rec = client.get("/api/calls?has_recording=true").json()
    assert with_rec["total"] == 3
    assert all(i["recording_url"] for i in with_rec["items"])


def test_date_range_filters(client):
    # Half a day off any sample, so the assertion cannot flip on the milliseconds
    # between the fixture building the rows and this line running.
    since = (utcnow() - timedelta(days=5, hours=12)).isoformat()
    # Passed as a param, not concatenated: the "+00:00" offset in an ISO timestamp
    # decodes as a space if it goes into the query string unencoded, and the
    # datetime then fails to parse. The CLI must build params the same way.
    r = client.get("/api/calls", params={"start": since})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2


def test_calls_are_paginated_and_newest_first(client):
    p1 = client.get("/api/calls?page_size=2&page=1").json()
    p2 = client.get("/api/calls?page_size=2&page=2").json()
    assert p1["pages"] == 3 and p1["total"] == 6
    assert {i["id"] for i in p1["items"]} & {i["id"] for i in p2["items"]} == set()
    assert p1["items"][0]["occurred_at"] > p1["items"][1]["occurred_at"]


# ---------------- message search ----------------

def test_message_search_excludes_calls(client):
    items = client.get("/api/messages").json()["items"]
    assert items and all(i["type"] != "CALL" for i in items)


def test_message_type_filter_rejects_a_call(client):
    assert client.get("/api/messages?type=CALL").status_code == 400
    assert client.get("/api/messages?type=nonsense").status_code == 400


def test_sent_message_is_findable_by_search(client):
    client.post("/api/contacts/%d/messages" % client.ids["reachable"],
                json={"body": "unmistakable-needle"})
    r = client.get("/api/messages?q=unmistakable-needle").json()
    assert r["total"] == 1
    assert r["items"][0]["delivery_status"] == "LOGGED_ONLY"


# ---------------- conversation patch ----------------

def test_marking_read_clears_the_unread_count(client):
    conv_id = client.ids["conv"]
    assert client.get("/api/conversations").json()[0]["unread_count"] == 3
    r = client.patch("/api/conversations/%d" % conv_id, json={"read": True})
    assert r.status_code == 200
    assert r.json()["unread_count"] == 0
    assert client.get("/api/conversations?tab=unread").json() == []


def test_starring_moves_it_into_the_starred_tab(client):
    conv_id = client.ids["conv"]
    assert client.get("/api/conversations?tab=starred").json() == []
    client.patch("/api/conversations/%d" % conv_id, json={"starred": True})
    starred = client.get("/api/conversations?tab=starred").json()
    assert [c["id"] for c in starred] == [conv_id]


# ---------------- appointments ----------------

def _book(client, hours_ahead=72):
    start = utcnow() + timedelta(hours=hours_ahead)
    return client.post("/api/appointments", json={
        "title": "Roof inspection",
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(hours=1)).isoformat(),
        "contact_id": client.ids["reachable"]})


def pending_reminders(appointment_id):
    db = SessionLocal()
    jobs = [j for j in db.query(Job).filter(
        Job.type == "appointment_reminder", Job.status == "pending").all()
        if j.payload.get("appointment_id") == appointment_id]
    db.close()
    return jobs


def test_rescheduling_an_appointment_schedules_new_reminders(client):
    """The bug this endpoint would otherwise ship.

    Reminder jobs were deduped on (appointment_id, offset). Moving the
    appointment re-enqueued with the same key, enqueue() returned None, and the
    customer got NO reminder at the new time — silently.
    """
    appt = _book(client).json()
    first = pending_reminders(appt["id"])
    assert len(first) == 2
    old_times = sorted(j.run_after for j in first)

    new_start = utcnow() + timedelta(days=5)
    r = client.patch("/api/appointments/%d" % appt["id"], json={
        "starts_at": new_start.isoformat(),
        "ends_at": (new_start + timedelta(hours=1)).isoformat()})
    assert r.status_code == 200

    after = pending_reminders(appt["id"])
    assert len(after) == 2, "rescheduling must leave exactly two live reminders"
    assert sorted(j.run_after for j in after) != old_times, \
        "the reminders must point at the NEW time"
    assert all(j.id not in {f.id for f in first} for j in after), \
        "the stale jobs must not still be pending"


def test_cancelling_an_appointment_keeps_the_row_and_stops_the_reminders(client):
    appt = _book(client).json()
    assert len(pending_reminders(appt["id"])) == 2

    assert client.delete("/api/appointments/%d" % appt["id"]).status_code == 200
    got = client.get("/api/appointments/%d" % appt["id"]).json()
    assert got["status"] == "cancelled", "cancel is a status change, not a delete"

    # The handler re-checks status at run time, so the queued jobs self-suppress.
    from app.automations import _h_appointment_reminder
    db = SessionLocal()
    before = db.query(ConversationEvent).count()
    _h_appointment_reminder(db, {"appointment_id": appt["id"], "offset": "1h"})
    assert db.query(ConversationEvent).count() == before
    db.close()


def test_appointment_edit_validates_the_range_against_stored_values(client):
    appt = _book(client).json()
    # Patch only the end, to a time before the stored start.
    r = client.patch("/api/appointments/%d" % appt["id"],
                     json={"ends_at": (utcnow() - timedelta(days=1)).isoformat()})
    assert r.status_code == 400


def test_appointment_rejects_an_unknown_status(client):
    appt = _book(client).json()
    r = client.patch("/api/appointments/%d" % appt["id"], json={"status": "maybe"})
    assert r.status_code == 400


def test_an_appointment_can_be_booked_onto_a_calendar(client):
    """calendar_id was missing from the create schema, so every API-created
    appointment was invisible to the calendar filters."""
    db = SessionLocal()
    from app.models import Calendar
    cal = Calendar(name="Owen's Calendar", user_id=client.ids["admin"])
    db.add(cal)
    db.commit()
    cal_id = cal.id
    db.close()

    start = utcnow() + timedelta(days=2)
    r = client.post("/api/appointments", json={
        "title": "On a calendar", "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(hours=1)).isoformat(),
        "calendar_id": cal_id})
    assert r.status_code == 201
    assert client.get("/api/appointments/%d" % r.json()["id"]
                      ).json()["calendar_id"] == cal_id


# ---------------- tags / jobs ----------------

def test_tags_report_how_many_contacts_carry_them(client):
    rows = client.get("/api/tags").json()
    assert rows[0]["name"] == "ahs"
    assert rows[0]["count"] == 1


def test_jobs_expose_what_the_automations_queued(client):
    client.post("/api/contacts", json={"first_name": "New", "last_name": "Lead",
                                       "phone": "(941) 555-7777"})
    jobs = client.get("/api/jobs?type=new_lead_notify").json()
    assert jobs and jobs[0]["status"] == "pending"


def test_a_completed_job_cannot_be_retried(client):
    """Retrying a done job would repeat its side effect — possibly a text."""
    client.post("/api/contacts", json={"first_name": "A", "last_name": "B",
                                       "phone": "(941) 555-8888"})
    job_id = client.get("/api/jobs").json()[0]["id"]
    db = SessionLocal()
    db.get(Job, job_id).status = "done"
    db.commit()
    db.close()
    assert client.post("/api/jobs/%d/retry" % job_id).status_code == 400


# ---------------- deletes ----------------

def test_deleting_a_contact_with_opportunities_needs_force(client):
    cid = client.ids["reachable"]
    r = client.delete("/api/contacts/%d" % cid)
    assert r.status_code == 409
    assert str(client.ids["opp"]) in r.json()["detail"]
    assert client.get("/api/contacts/%d" % cid).status_code == 200, \
        "a refused delete must not have partially deleted"


def test_force_delete_detaches_opportunities_rather_than_destroying_them(client):
    """`owen_call_id` in custom_fields is the telephony join key (DECISIONS.md).
    Tidying up a contact must not erase attribution history."""
    cid, oid = client.ids["reachable"], client.ids["opp"]
    r = client.delete("/api/contacts/%d?force=true" % cid)
    assert r.status_code == 200
    assert r.json()["detached_opportunities"] == [oid]

    assert client.get("/api/contacts/%d" % cid).status_code == 404
    opp = client.get("/api/opportunities/%d" % oid).json()
    assert opp["contact_id"] is None
    assert opp["custom_fields"]["owen_call_id"] == "call-abc-123"

    db = SessionLocal()
    assert db.query(Conversation).filter(
        Conversation.contact_id == cid).count() == 0
    db.close()


def test_the_telephony_join_key_cannot_be_rewritten_through_the_detail_form(client):
    """`owen_call_id` joins an opportunity to the telephony project's call record.

    The detail dialog rendered it as a plain text input, and the API accepted the
    change, so one careless edit silently orphans that call's attribution. Deletes
    already go out of their way to preserve this key (see the force-delete test above);
    an edit must not be the hole that the delete path is not.
    """
    oid = client.ids["opp"]
    before = client.get("/api/opportunities/%d" % oid).json()["custom_fields"]
    assert before["owen_call_id"] == "call-abc-123", "fixture changed — retarget this"

    r = client.patch("/api/opportunities/%d/detail" % oid,
                     json={"custom_fields": {"owen_call_id": "CLOBBERED"}})
    assert r.status_code == 400
    assert "owen_call_id" in r.json()["detail"]
    after = client.get("/api/opportunities/%d" % oid).json()["custom_fields"]
    assert after["owen_call_id"] == "call-abc-123", "a refused edit still clobbered the key"

    # The form posts the whole custom_fields object back on every save, so echoing
    # the key unchanged must keep working -- and must still save the other fields.
    ok = client.patch("/api/opportunities/%d/detail" % oid, json={
        "custom_fields": {"owen_call_id": "call-abc-123", "owen_campaign": "Spring"}})
    assert ok.status_code == 200
    saved = client.get("/api/opportunities/%d" % oid).json()["custom_fields"]
    assert saved["owen_campaign"] == "Spring", "the guard blocked an unrelated field"
    assert saved["owen_call_id"] == "call-abc-123"


def test_deleting_an_opportunity(client):
    oid = client.ids["opp"]
    assert client.delete("/api/opportunities/%d" % oid).status_code == 200
    assert client.get("/api/opportunities/%d" % oid).status_code == 404

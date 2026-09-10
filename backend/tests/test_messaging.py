"""The endpoints the CLI needs: outbound send, global search, appointments, deletes.

Same standard as test_api.py — assert behaviour, not status codes. A suppressed send
must write nothing, a filter must actually narrow the set, and a 409 must not have
half-performed the delete.
"""
from datetime import UTC, datetime, timedelta

import pytest
from app.auth import as_aware, mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Appointment,
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


# ---------------- the detail panel: edit, reschedule, cancel ----------------
#
# THE REMINDER TRAP. Every assertion below counts rows in `jobs`, because the
# failure this whole surface was deferred for is silent: a rescheduled
# appointment whose reminder never fires looks exactly like one whose reminder is
# waiting. `ghl jobs list --status pending` is how a dispatcher checks it, so
# `pending_reminders()` reads the same model with the same filter.

def _reminder_offsets(appointment_id):
    """Which offsets are live, as a list — so a duplicate shows up as ['1h','1h']
    rather than being hidden by a set."""
    return sorted(j.payload["offset"] for j in pending_reminders(appointment_id))


def _appointment_row(appointment_id):
    db = SessionLocal()
    try:
        return db.get(Appointment, appointment_id)
    finally:
        db.close()


def test_the_detail_panel_gets_the_data_it_draws(client):
    """Clicking a block opens the panel from `GET /api/appointments/{id}`, so
    everything the panel shows has to be in that one response: what it is, who
    it is with, which calendar, when, its status and its notes."""
    starts = utcnow().replace(microsecond=0) + timedelta(days=3)
    made = client.post("/api/appointments", json={
        "title": "Roof inspection", "contact_id": client.ids["reachable"],
        "notes": "gate code 1174",
        "starts_at": starts.isoformat(),
        "ends_at": (starts + timedelta(hours=1)).isoformat()}).json()

    got = client.get("/api/appointments/%d" % made["id"]).json()
    assert got["id"] == made["id"]
    assert got["title"] == "Roof inspection"
    assert got["contact_id"] == client.ids["reachable"]
    assert got["contact_name"] == "Reach Able", "the panel would have to show an id"
    assert got["notes"] == "gate code 1174"
    assert got["status"] == "confirmed"
    assert datetime.fromisoformat(got["starts_at"]).replace(tzinfo=UTC) == starts
    # A booking with no calendar must say so rather than 500 on `.name`.
    assert got["calendar_id"] is None and got["calendar_name"] is None


def test_editing_the_title_and_contact_persists_and_the_grid_sees_it(client):
    """The block on the grid is drawn from the range query, not from the panel,
    so an edit that only updates the detail response would leave the calendar
    showing the old title until a reload."""
    appt = _book(client).json()
    other = client.get("/api/contacts?q=NotDisturb").json()["items"][0]

    r = client.patch("/api/appointments/%d" % appt["id"], json={
        "title": "  Tarp the north slope  ", "contact_id": other["id"],
        "notes": "dog in the yard"})
    assert r.status_code == 200
    assert r.json()["title"] == "Tarp the north slope", "the title kept its spaces"

    row = _appointment_row(appt["id"])
    assert row.title == "Tarp the north slope"
    assert row.contact_id == other["id"]
    assert row.notes == "dog in the yard"

    window = client.get("/api/appointments", params={
        "start": (utcnow() - timedelta(days=1)).isoformat(),
        "end": (utcnow() + timedelta(days=10)).isoformat()}).json()
    drawn = [a for a in window if a["id"] == appt["id"]]
    assert len(drawn) == 1
    assert drawn[0]["title"] == "Tarp the north slope"
    assert drawn[0]["contact_name"] == "Do NotDisturb", (
        "the grid still names the old contact")


def test_rescheduling_moves_it_in_the_range_query(client):
    """The grid asks for a window and draws what comes back. A move that does not
    change which window returns it has not moved on screen."""
    appt = _book(client, hours_ahead=72).json()
    new_start = (utcnow() + timedelta(days=20)).replace(microsecond=0)

    def in_window(days_from, days_to):
        rows = client.get("/api/appointments", params={
            "start": (utcnow() + timedelta(days=days_from)).isoformat(),
            "end": (utcnow() + timedelta(days=days_to)).isoformat()}).json()
        return [a["id"] for a in rows]

    assert appt["id"] in in_window(0, 7)
    assert appt["id"] not in in_window(14, 28)

    r = client.patch("/api/appointments/%d" % appt["id"], json={
        "starts_at": new_start.isoformat(),
        "ends_at": (new_start + timedelta(hours=1)).isoformat()})
    assert r.status_code == 200

    assert appt["id"] not in in_window(0, 7), "the old slot still draws it"
    assert appt["id"] in in_window(14, 28), "the new slot does not draw it"
    row = _appointment_row(appt["id"])
    assert row.starts_at.replace(tzinfo=UTC) == new_start


def test_rescheduling_leaves_exactly_one_pending_reminder_per_offset(client):
    """The core of this task. Not "two exist" — exactly one per offset, at the
    NEW time, with the superseded ones gone from the queue."""
    appt = _book(client, hours_ahead=72).json()
    before = pending_reminders(appt["id"])
    assert _reminder_offsets(appt["id"]) == ["1h", "24h"]

    new_start = (utcnow() + timedelta(days=5)).replace(microsecond=0)
    client.patch("/api/appointments/%d" % appt["id"], json={
        "starts_at": new_start.isoformat(),
        "ends_at": (new_start + timedelta(hours=1)).isoformat()})

    after = pending_reminders(appt["id"])
    assert _reminder_offsets(appt["id"]) == ["1h", "24h"], (
        "the customer has a duplicate or a missing reminder")
    assert {f.id for f in before} & {j.id for j in after} == set(), (
        "a job queued against the old time is still pending")
    at = {j.payload["offset"]: as_aware(j.run_after) for j in after}
    assert at["1h"] == new_start - timedelta(hours=1), "T-1h is not at the new time"
    assert at["24h"] == new_start - timedelta(hours=24), "T-24h is not at the new time"


def test_rescheduling_twice_still_leaves_exactly_one_pending_reminder(client):
    appt = _book(client, hours_ahead=72).json()
    last = None
    for days in (5, 9):
        last = (utcnow() + timedelta(days=days)).replace(microsecond=0)
        client.patch("/api/appointments/%d" % appt["id"], json={
            "starts_at": last.isoformat(),
            "ends_at": (last + timedelta(hours=1)).isoformat()})
        assert _reminder_offsets(appt["id"]) == ["1h", "24h"], (
            "reschedule to +%dd left the wrong number of reminders" % days)

    at = {j.payload["offset"]: as_aware(j.run_after)
          for j in pending_reminders(appt["id"])}
    assert at["1h"] == last - timedelta(hours=1), (
        "the live reminder still points at the FIRST reschedule")


def test_rescheduling_back_to_the_original_time_still_leaves_a_reminder(client):
    """Undo. The dedupe key is `appt_reminder:<id>:<starts_at>:<offset>`, and
    `enqueue()` refuses a key that exists whatever its status — so retiring the
    first Tuesday jobs by status alone would make the move back to Tuesday a
    no-op and the customer would get NO reminder. A retired job releases its key
    for exactly this."""
    appt = _book(client, hours_ahead=72).json()
    original = _appointment_row(appt["id"])
    original_start = as_aware(original.starts_at)
    original_end = as_aware(original.ends_at)

    away = (utcnow() + timedelta(days=6)).replace(microsecond=0)
    client.patch("/api/appointments/%d" % appt["id"], json={
        "starts_at": away.isoformat(),
        "ends_at": (away + timedelta(hours=1)).isoformat()})
    back = client.patch("/api/appointments/%d" % appt["id"], json={
        "starts_at": original_start.isoformat(),
        "ends_at": original_end.isoformat()})
    assert back.status_code == 200

    assert _reminder_offsets(appt["id"]) == ["1h", "24h"], (
        "moving a booking back to its original time left the customer with no "
        "reminder — the retired jobs are still holding the dedupe keys")
    at = {j.payload["offset"]: as_aware(j.run_after)
          for j in pending_reminders(appt["id"])}
    assert at["1h"] == original_start - timedelta(hours=1)


def test_rescheduling_into_the_next_hours_drops_the_reminder_that_cannot_fire(client):
    """A booking moved to two hours from now cannot have a T-24h reminder — that
    would fire immediately. Exactly one reminder, and it is the T-1h."""
    appt = _book(client, hours_ahead=72).json()
    soon = (utcnow() + timedelta(hours=2)).replace(microsecond=0)
    client.patch("/api/appointments/%d" % appt["id"], json={
        "starts_at": soon.isoformat(),
        "ends_at": (soon + timedelta(hours=1)).isoformat()})

    live = pending_reminders(appt["id"])
    assert len(live) == 1, "a T-24h reminder was queued in the past"
    assert live[0].payload["offset"] == "1h"
    assert as_aware(live[0].run_after) == soon - timedelta(hours=1)


def test_editing_only_the_title_does_not_churn_the_reminders(client):
    """Retiring and re-queueing on every save would move the reminders' ids and
    their `run_after` for no reason, and a reschedule-to-the-same-time is the
    case that used to silently drop them."""
    appt = _book(client, hours_ahead=72).json()
    before = {j.id: as_aware(j.run_after) for j in pending_reminders(appt["id"])}

    r = client.patch("/api/appointments/%d" % appt["id"],
                     json={"title": "Roof inspection (rear)"})
    assert r.json()["automation"] == "unchanged"
    after = {j.id: as_aware(j.run_after) for j in pending_reminders(appt["id"])}
    assert after == before, "a title edit re-queued the customer's reminders"


def test_cancelling_leaves_no_pending_reminder(client):
    """A reminder for an appointment that is off must not sit in the queue. The
    handler would refuse to send it, but a dispatcher reading
    `ghl jobs list --status pending` would still see it."""
    appt = _book(client, hours_ahead=72).json()
    assert len(pending_reminders(appt["id"])) == 2

    r = client.delete("/api/appointments/%d" % appt["id"])
    assert r.status_code == 200
    assert r.json()["reminders_cancelled"] == 2

    assert pending_reminders(appt["id"]) == [], (
        "cancelling left a reminder queued for a cancelled appointment")
    assert _appointment_row(appt["id"]).status == "cancelled", (
        "cancel is a status change, not a delete — the Cancelled report tile "
        "counts these rows")


def test_cancelling_through_the_edit_form_also_stops_the_reminders(client):
    """The panel can reach `cancelled` two ways: the Cancel appointment button
    (DELETE) and the Status select (PATCH). Both have to end in the same place."""
    appt = _book(client, hours_ahead=72).json()
    r = client.patch("/api/appointments/%d" % appt["id"], json={"status": "cancelled"})
    assert r.status_code == 200
    assert r.json()["automation"] == "reminders cancelled"
    assert pending_reminders(appt["id"]) == []


def test_reviving_a_cancelled_appointment_queues_its_reminders_again(client):
    """The panel's Status select can go back to `confirmed`, so it must. Without
    the key release this is the undo trap again: the retired jobs hold the keys
    for this exact start time."""
    appt = _book(client, hours_ahead=72).json()
    client.delete("/api/appointments/%d" % appt["id"])
    assert pending_reminders(appt["id"]) == []

    r = client.patch("/api/appointments/%d" % appt["id"], json={"status": "confirmed"})
    assert r.status_code == 200
    assert _reminder_offsets(appt["id"]) == ["1h", "24h"], (
        "a re-confirmed appointment gets no reminder at all")


def test_a_reminder_already_sent_is_left_alone_by_a_reschedule(client):
    """Only `pending` jobs are superseded. A reminder that has already gone out
    is history: rewriting it would say the customer was told about a time they
    were never told about."""
    appt = _book(client, hours_ahead=72).json()
    db = SessionLocal()
    done = next(j for j in db.query(Job).filter(
        Job.type == "appointment_reminder").all()
        if j.payload.get("appointment_id") == appt["id"])
    done.status = "done"
    done_key, done_id = done.dedupe_key, done.id
    db.commit()
    db.close()

    start = (utcnow() + timedelta(days=5)).replace(microsecond=0)
    client.patch("/api/appointments/%d" % appt["id"], json={
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(hours=1)).isoformat()})

    db = SessionLocal()
    still = db.get(Job, done_id)
    assert still.status == "done" and still.dedupe_key == done_key, (
        "a reminder that had already been sent was rewritten")
    db.close()


@pytest.mark.parametrize("why,body", [
    ("end before start", {"ends_at": -1}),
    ("end equal to start", {"ends_at": 0}),
    ("blank title", {"title": ""}),
    ("whitespace title", {"title": "   "}),
    ("unknown status", {"status": "rained-off"}),
    ("missing contact", {"contact_id": 9_999}),
    ("missing calendar", {"calendar_id": 9_999}),
])
def test_an_invalid_edit_changes_nothing(client, why, body):
    """A refused PATCH must leave the row and the queue exactly as it found them.
    The endpoint assigns onto the loaded object BEFORE it validates, so "the
    response was a 400" is not the same statement as "nothing was written"."""
    appt = _book(client, hours_ahead=72).json()
    before = _appointment_row(appt["id"])
    was = (before.title, as_aware(before.starts_at), as_aware(before.ends_at),
           before.status, before.contact_id, before.calendar_id, before.notes)
    reminders = {j.id: as_aware(j.run_after) for j in pending_reminders(appt["id"])}

    payload = dict(body)
    if "ends_at" in payload:
        payload["ends_at"] = (as_aware(before.starts_at)
                              + timedelta(hours=payload["ends_at"])).isoformat()
    r = client.patch("/api/appointments/%d" % appt["id"], json=payload)
    assert r.status_code in (400, 404), why
    # A sentence, not a schema dump: the panel renders `detail` verbatim.
    assert isinstance(r.json()["detail"], str) and r.json()["detail"]

    after = _appointment_row(appt["id"])
    assert (after.title, as_aware(after.starts_at), as_aware(after.ends_at),
            after.status, after.contact_id, after.calendar_id,
            after.notes) == was, "%s still mutated the row" % why
    assert {j.id: as_aware(j.run_after)
            for j in pending_reminders(appt["id"])} == reminders, (
        "%s still disturbed the reminders" % why)


def test_a_tech_cannot_edit_or_cancel_an_appointment_and_no_row_is_mutated(client):
    """PATCH and DELETE are both `auth.STAFF`, unchanged by this work. The panel
    disables Save and Cancel appointment for a TECH, but the backend is the thing
    that enforces it — and a 403 that had already assigned onto the row would
    pass a status-only assertion."""
    appt = _book(client, hours_ahead=72).json()
    before = _appointment_row(appt["id"])
    was = (before.title, as_aware(before.starts_at), before.status)
    reminders = {j.id for j in pending_reminders(appt["id"])}

    tech_user = User(email="tech@x.test", name="Tech", role=Role.TECH)
    db = SessionLocal()
    db.add(tech_user)
    db.flush()
    plain, token = mint_api_token(tech_user, name="tech")
    db.add(token)
    db.commit()
    db.close()

    tech = TestClient(app)
    tech.headers["Authorization"] = "Bearer " + plain

    # A TECH may still READ it — the grid and the panel are ANY_USER.
    assert tech.get("/api/appointments/%d" % appt["id"]).status_code == 200

    assert tech.patch("/api/appointments/%d" % appt["id"],
                      json={"title": "TECH WAS HERE"}).status_code == 403
    assert tech.delete("/api/appointments/%d" % appt["id"]).status_code == 403

    after = _appointment_row(appt["id"])
    assert (after.title, as_aware(after.starts_at), after.status) == was, (
        "a refused edit still mutated the appointment")
    assert {j.id for j in pending_reminders(appt["id"])} == reminders, (
        "a refused cancel still retired the customer's reminders")

"""GoHighLevel's Book appointment modal, at the API (2026-09-13).

The modal (screenshot 29) changed what a booking carries and how one is made:

* the title arrives as `{{contact.name}}` and is stored RESOLVED;
* a Description and a Meeting location are stored on the appointment, the
  location defaulting to the contact's property address;
* the footer books with a Status;
* there is no Assigned to field — the calendar's user is the assignee;
* the Internal notes are STAFF-only, like every internal note in this app;
* a second tab blocks off time on a calendar, and booking over it asks first.

What must NOT change is asserted by counting rows in `jobs`: the reminders a
booking schedules, and what a reschedule does to them (CLAUDE.md, "Things that
have bitten us"). Times are asserted in both DST seasons.

Every refusal re-reads afterwards to prove it wrote nothing.
"""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Appointment,
    BlockedTime,
    Calendar,
    Contact,
    Job,
    Opportunity,
    Pipeline,
    Role,
    Stage,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select

EASTERN = ZoneInfo("America/New_York")
# Far enough out that both reminder offsets are in the future.
SOON = (datetime.now(UTC) + timedelta(days=9)).replace(microsecond=0)


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    admin = User(email="a@x.test", name="Owen", role=Role.ADMIN)
    dispatcher = User(email="d@x.test", name="Dana", role=Role.DISPATCHER)
    tech = User(email="t@x.test", name="Luis", role=Role.TECH)
    db.add_all([admin, dispatcher, tech])
    db.flush()

    jane = Contact(first_name="Jane", last_name="Doe", phone="+19415550004",
                   email="jane@example.test", address_street="9811 SW 16th St",
                   address_city="Pembroke Pines", address_state="FL",
                   address_postal_code="33025")
    nobody_home = Contact(first_name="Sam", last_name="Nohouse", phone="+19415550005")
    db.add_all([jane, nobody_home])

    pipe = Pipeline(name="Dream Team Roofing AHS", position=0)
    db.add(pipe)
    db.flush()
    stage = Stage(pipeline_id=pipe.id, name="New Lead", position=0)
    db.add(stage)
    db.flush()
    deal = Opportunity(title="Jane roof", contact_id=jane.id, pipeline_id=pipe.id,
                       stage_id=stage.id, value_cents=950000)
    db.add(deal)

    crew = Calendar(name="Workiz Jobs (imported)", user_id=tech.id)
    spare = Calendar(name="Luis Candialies's Personal Calendar")
    db.add_all([crew, spare])

    tokens = {}
    for key, u in (("admin", admin), ("dispatcher", dispatcher), ("tech", tech)):
        plain, token = mint_api_token(u, name="test-" + key)
        db.add(token)
        tokens[key] = plain
    db.commit()
    ids = {"jane": jane.id, "nobody_home": nobody_home.id, "deal": deal.id,
           "crew": crew.id, "spare": spare.id, "tech": tech.id, "admin": admin.id}
    db.close()

    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + tokens["admin"]
        c.ids = ids
        c.tokens = tokens
        yield c


def _as(client, who):
    return {"Authorization": "Bearer " + client.tokens[who]}


def _book(client, headers=None, **overrides):
    """What the modal sends for a default booking."""
    body = {"title": "{{contact.name}}", "starts_at": SOON.isoformat(),
            "ends_at": (SOON + timedelta(minutes=30)).isoformat(),
            "contact_id": client.ids["jane"], "calendar_id": client.ids["crew"],
            "location_kind": "calendar_default", "status": "confirmed"}
    body.update(overrides)
    return client.post("/api/appointments", json=body, headers=headers or {})


def _count(model) -> int:
    db = SessionLocal()
    try:
        return db.scalar(select(func.count()).select_from(model))
    finally:
        db.close()


def _pending_reminders(appointment_id: int) -> list[Job]:
    db = SessionLocal()
    try:
        return [j for j in db.scalars(select(Job).where(
            Job.type == "appointment_reminder", Job.status == "pending")).all()
            if j.payload.get("appointment_id") == appointment_id]
    finally:
        db.close()


def _jobs() -> list[tuple]:
    db = SessionLocal()
    try:
        return [(j.id, j.type, j.status, j.dedupe_key)
                for j in db.scalars(select(Job).order_by(Job.id)).all()]
    finally:
        db.close()


def _instant(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# ---------------- the modal books ----------------

def test_the_title_template_is_stored_resolved_to_the_contacts_name(client):
    r = _book(client)
    assert r.status_code == 201, r.text
    assert r.json()["title"] == "Jane Doe"
    stored = client.get("/api/appointments/%d" % r.json()["id"]).json()
    assert stored["title"] == "Jane Doe", "the braces were stored, not the name"
    # ...and the calendar grid draws the name, not the template.
    row = client.get("/api/appointments", params={
        "start": (SOON - timedelta(days=1)).isoformat(),
        "end": (SOON + timedelta(days=1)).isoformat()}).json()
    assert [a["title"] for a in row] == ["Jane Doe"]


def test_a_template_mixed_with_text_resolves_every_variable(client):
    r = _book(client, title="Inspection — {{ contact.first_name }} ({{contact.phone}})")
    assert r.status_code == 201, r.text
    assert r.json()["title"] == "Inspection — Jane ((941) 555-0004)"


def test_a_template_with_no_contact_is_refused_and_nothing_is_booked(client):
    before = (_count(Appointment), _jobs())
    r = _book(client, contact_id=None)
    assert r.status_code == 400
    assert "select a contact" in r.json()["detail"]
    assert (_count(Appointment), _jobs()) == before


def test_an_unknown_template_variable_is_refused_not_stored(client):
    before = _count(Appointment)
    r = _book(client, title="{{contact.roof_colour}}")
    assert r.status_code == 400 and "contact.roof_colour" in r.json()["detail"]
    assert _count(Appointment) == before


def test_the_default_location_is_the_contacts_property_address(client):
    r = _book(client)
    assert r.status_code == 201, r.text
    assert r.json()["location"] == "9811 SW 16th St, Pembroke Pines, FL 33025"
    detail = client.get("/api/appointments/%d" % r.json()["id"]).json()
    assert detail["location"] == "9811 SW 16th St, Pembroke Pines, FL 33025"


def test_a_contact_with_no_address_books_with_no_location(client):
    r = _book(client, contact_id=client.ids["nobody_home"])
    assert r.status_code == 201, r.text
    assert r.json()["location"] is None


def test_a_custom_location_is_stored_as_typed(client):
    r = _book(client, location_kind="custom", location="  Supply yard, 12 Main St  ")
    assert r.status_code == 201, r.text
    assert r.json()["location"] == "Supply yard, 12 Main St"


def test_a_custom_location_with_no_address_is_refused_and_writes_nothing(client):
    before = _count(Appointment)
    r = _book(client, location_kind="custom", location="   ")
    assert r.status_code == 400
    assert _count(Appointment) == before


def test_the_location_is_shown_wherever_the_appointment_is(client):
    r = _book(client, opportunity_id=client.ids["deal"])
    address = "9811 SW 16th St, Pembroke Pines, FL 33025"
    contact = client.get("/api/contacts/%d" % client.ids["jane"]).json()
    assert [a["location"] for a in contact["appointments"]] == [address]
    deal = client.get("/api/opportunities/%d" % client.ids["deal"]).json()
    assert [a["location"] for a in deal["appointments"]] == [address]
    assert client.get("/api/appointments/%d" % r.json()["id"]).json()["location"] == address


def test_the_description_is_stored_and_every_role_reads_it(client):
    r = _book(client, description="Leak over the kitchen. Gate code 4411.")
    assert r.status_code == 201, r.text
    for who in ("admin", "tech"):
        got = client.get("/api/appointments/%d" % r.json()["id"],
                         headers=_as(client, who)).json()
        assert got["description"] == "Leak over the kitchen. Gate code 4411.", who


def test_internal_notes_are_staff_only(client):
    r = _book(client, notes="Customer was rude on the phone")
    appointment_id = r.json()["id"]
    for who in ("admin", "dispatcher"):
        got = client.get("/api/appointments/%d" % appointment_id,
                         headers=_as(client, who)).json()
        assert got["notes"] == "Customer was rude on the phone" and got["notes_visible"]
    tech = client.get("/api/appointments/%d" % appointment_id,
                      headers=_as(client, "tech")).json()
    assert tech["notes"] is None and tech["notes_visible"] is False, (
        "a TECH can read the booking's internal notes")
    assert "rude" not in str(tech)
    # A TECH cannot write one either — and the refusal changes nothing.
    refused = client.patch("/api/appointments/%d" % appointment_id,
                           json={"notes": "overwritten"}, headers=_as(client, "tech"))
    assert refused.status_code == 403
    again = client.get("/api/appointments/%d" % appointment_id).json()
    assert again["notes"] == "Customer was rude on the phone"


def test_the_assignee_is_the_calendars_user(client):
    r = _book(client)
    assert r.json()["assigned_user_id"] == client.ids["tech"]
    assert client.get("/api/appointments/%d" % r.json()["id"]).json()[
        "assigned_user_id"] == client.ids["tech"]
    # A calendar with no user assigns nobody.
    r2 = _book(client, calendar_id=client.ids["spare"],
               starts_at=(SOON + timedelta(days=1)).isoformat(),
               ends_at=(SOON + timedelta(days=1, hours=1)).isoformat())
    assert r2.json()["assigned_user_id"] is None
    # The CLI's explicit --user still wins.
    r3 = _book(client, assigned_user_id=client.ids["admin"])
    assert r3.json()["assigned_user_id"] == client.ids["admin"]


def test_moving_a_booking_to_another_calendar_hands_it_to_that_calendars_user(client):
    r = _book(client, calendar_id=client.ids["spare"])
    assert r.json()["assigned_user_id"] is None
    moved = client.patch("/api/appointments/%d" % r.json()["id"],
                         json={"calendar_id": client.ids["crew"]})
    assert moved.status_code == 200, moved.text
    assert moved.json()["assigned_user_id"] == client.ids["tech"]


def test_the_footer_status_is_booked_with(client):
    r = _book(client, status="showed")
    assert r.status_code == 201, r.text
    assert client.get("/api/appointments/%d" % r.json()["id"]).json()["status"] == "showed"


def test_an_unknown_status_is_refused_and_nothing_is_booked(client):
    before = (_count(Appointment), _jobs())
    r = _book(client, status="maybe")
    assert r.status_code == 400
    assert (_count(Appointment), _jobs()) == before


def test_a_booking_made_cancelled_queues_no_reminder(client):
    r = _book(client, status="cancelled")
    assert r.status_code == 201
    assert _pending_reminders(r.json()["id"]) == []


def test_a_tech_cannot_book_and_nothing_is_written(client):
    before = (_count(Appointment), _jobs())
    r = _book(client, headers=_as(client, "tech"))
    assert r.status_code == 403
    assert (_count(Appointment), _jobs()) == before


# ---------------- the reminders behave exactly as before ----------------

def test_a_booking_from_the_modal_schedules_the_same_two_reminders(client):
    r = _book(client)
    appointment_id = r.json()["id"]
    assert r.json()["automation"] == "queued 24h,1h"
    jobs = _pending_reminders(appointment_id)
    assert sorted(j.payload["offset"] for j in jobs) == ["1h", "24h"]
    for j in jobs:
        assert j.dedupe_key == "appt_reminder:%d:%s:%s" % (
            appointment_id, SOON.isoformat(), j.payload["offset"])
        delta = {"24h": timedelta(hours=24), "1h": timedelta(hours=1)}[j.payload["offset"]]
        assert _instant(j.run_after.isoformat()) == SOON - delta


def test_rescheduling_through_the_panel_still_leaves_one_reminder_per_offset(client):
    appointment_id = _book(client).json()["id"]
    later = SOON + timedelta(days=2)
    for starts in (later, SOON):          # away, and back to the original slot
        r = client.patch("/api/appointments/%d" % appointment_id, json={
            "starts_at": starts.isoformat(),
            "ends_at": (starts + timedelta(minutes=30)).isoformat()})
        assert r.status_code == 200, r.text
        jobs = _pending_reminders(appointment_id)
        assert sorted(j.payload["offset"] for j in jobs) == ["1h", "24h"], starts
        assert all(starts.isoformat() in j.dedupe_key for j in jobs)


def test_editing_description_or_location_does_not_churn_the_reminders(client):
    appointment_id = _book(client).json()["id"]
    before = _jobs()
    r = client.patch("/api/appointments/%d" % appointment_id, json={
        "description": "Bring the tall ladder", "location_kind": "custom",
        "location": "Back entrance"})
    assert r.status_code == 200 and r.json()["automation"] == "unchanged"
    assert r.json()["location"] == "Back entrance"
    assert _jobs() == before


# ---------------- times survive both DST seasons ----------------

@pytest.mark.parametrize("wall,offset,utc_hour", [
    # 1:30 PM Eastern in summer (EDT, UTC-4) and in winter (EST, UTC-5).
    (datetime(2027, 7, 15, 13, 30), "-04:00", 17),
    (datetime(2027, 1, 15, 13, 30), "-05:00", 18),
])
def test_a_booking_at_1_30_pm_eastern_reads_back_as_1_30_pm_eastern(
        client, wall, offset, utc_hour):
    starts = wall.isoformat() + offset
    ends = wall.replace(minute=0, hour=14).isoformat() + offset
    r = _book(client, starts_at=starts, ends_at=ends)
    assert r.status_code == 201, r.text
    got = client.get("/api/appointments/%d" % r.json()["id"]).json()
    instant = _instant(got["starts_at"])
    assert (instant.astimezone(UTC).hour, instant.astimezone(UTC).minute) == (utc_hour, 30), (
        "the booking was stored at the wrong instant")
    eastern = instant.astimezone(EASTERN)
    assert (eastern.hour, eastern.minute) == (13, 30), "it no longer reads 1:30 PM Eastern"
    assert eastern.tzname() == ("EDT" if offset == "-04:00" else "EST")
    # The reminder an hour before is an hour before THAT instant.
    one_hour = [j for j in _pending_reminders(r.json()["id"]) if j.payload["offset"] == "1h"]
    assert _instant(one_hour[0].run_after.isoformat()) == instant - timedelta(hours=1)
    # And the range query finds it on its Eastern day.
    day = datetime(wall.year, wall.month, wall.day, tzinfo=EASTERN)
    rows = client.get("/api/appointments", params={
        "start": day.isoformat(), "end": (day + timedelta(days=1)).isoformat()}).json()
    assert [a["id"] for a in rows] == [r.json()["id"]]


def test_a_booking_moved_across_the_dst_change_keeps_its_wall_clock_time(client):
    summer = datetime(2026, 10, 30, 13, 30, tzinfo=EASTERN)   # EDT
    winter = datetime(2026, 11, 6, 13, 30, tzinfo=EASTERN)    # EST, after Nov 1
    r = _book(client, starts_at=summer.isoformat(),
              ends_at=(summer + timedelta(hours=1)).isoformat())
    moved = client.patch("/api/appointments/%d" % r.json()["id"], json={
        "starts_at": winter.isoformat(), "ends_at": (winter + timedelta(hours=1)).isoformat()})
    assert moved.status_code == 200, moved.text
    eastern = _instant(moved.json()["starts_at"]).astimezone(EASTERN)
    assert (eastern.day, eastern.hour, eastern.minute, eastern.tzname()) == (6, 13, 30, "EST")


# ---------------- blocked off time ----------------

def _block(client, headers=None, **overrides):
    body = {"title": "Crew day off", "calendar_id": client.ids["crew"],
            "starts_at": (SOON - timedelta(hours=1)).isoformat(),
            "ends_at": (SOON + timedelta(hours=2)).isoformat(),
            "notes": "Supplier run"}
    body.update(overrides)
    return client.post("/api/blocked-times", json=body, headers=headers or {})


def test_blocked_time_is_created_listed_edited_and_deleted(client):
    r = _block(client)
    assert r.status_code == 201, r.text
    block_id = r.json()["id"]
    window = {"start": (SOON - timedelta(days=1)).isoformat(),
              "end": (SOON + timedelta(days=1)).isoformat()}
    listed = client.get("/api/blocked-times", params=window).json()
    assert [(b["id"], b["title"], b["calendar_name"]) for b in listed] == [
        (block_id, "Crew day off", "Workiz Jobs (imported)")]
    # A block is NOT an appointment: the appointment range query does not return it.
    assert client.get("/api/appointments", params=window).json() == []
    # The calendar filter narrows it, and so does the Users filter (via the calendar).
    assert client.get("/api/blocked-times", params={
        **window, "calendar_ids": str(client.ids["spare"])}).json() == []
    assert len(client.get("/api/blocked-times", params={
        **window, "user_ids": str(client.ids["tech"])}).json()) == 1
    assert client.get("/api/blocked-times", params={
        **window, "user_ids": str(client.ids["admin"])}).json() == []

    edited = client.patch("/api/blocked-times/%d" % block_id,
                          json={"title": "Crew training", "calendar_id": client.ids["spare"]})
    assert edited.status_code == 200, edited.text
    got = client.get("/api/blocked-times/%d" % block_id).json()
    assert (got["title"], got["calendar_id"]) == ("Crew training", client.ids["spare"])

    gone = client.delete("/api/blocked-times/%d" % block_id)
    assert gone.status_code == 200
    assert client.get("/api/blocked-times/%d" % block_id).status_code == 404
    assert _count(BlockedTime) == 0


def test_blocked_time_never_sends_or_enqueues_anything(client):
    before = _jobs()
    block_id = _block(client).json()["id"]
    client.patch("/api/blocked-times/%d" % block_id, json={"title": "Moved"})
    client.delete("/api/blocked-times/%d" % block_id)
    assert _jobs() == before


def test_a_tech_reads_blocked_time_but_cannot_change_it(client):
    block_id = _block(client).json()["id"]
    tech = _as(client, "tech")
    assert client.get("/api/blocked-times/%d" % block_id, headers=tech).status_code == 200
    assert _block(client, headers=tech).status_code == 403
    assert client.patch("/api/blocked-times/%d" % block_id, json={"title": "x"},
                        headers=tech).status_code == 403
    assert client.delete("/api/blocked-times/%d" % block_id, headers=tech).status_code == 403
    assert _count(BlockedTime) == 1
    assert client.get("/api/blocked-times/%d" % block_id).json()["title"] == "Crew day off"


def test_blocked_time_refusals_write_nothing(client):
    assert _block(client, title="  ").status_code == 400
    assert _block(client, calendar_id=999).status_code == 404
    assert _block(client, ends_at=(SOON - timedelta(hours=2)).isoformat()).status_code == 400
    assert _count(BlockedTime) == 0
    block_id = _block(client).json()["id"]
    bad = client.patch("/api/blocked-times/%d" % block_id,
                       json={"ends_at": (SOON - timedelta(hours=3)).isoformat()})
    assert bad.status_code == 400
    assert _instant(client.get("/api/blocked-times/%d" % block_id).json()["ends_at"]) == \
        SOON + timedelta(hours=2)


def test_booking_over_blocked_time_asks_first_and_writes_nothing(client):
    _block(client)
    before = (_count(Appointment), _jobs())
    r = _book(client)
    assert r.status_code == 409
    assert "blocked off time" in r.json()["detail"] and "Crew day off" in r.json()["detail"]
    assert (_count(Appointment), _jobs()) == before, "a refused booking wrote something"


def test_confirming_books_over_blocked_time_with_the_usual_reminders(client):
    _block(client)
    r = _book(client, allow_blocked_time=True)
    assert r.status_code == 201, r.text
    assert sorted(j.payload["offset"] for j in _pending_reminders(r.json()["id"])) == [
        "1h", "24h"]


def test_blocked_time_on_another_calendar_or_touching_does_not_ask(client):
    _block(client)
    assert _book(client, calendar_id=client.ids["spare"]).status_code == 201
    # Starts exactly when the block ends.
    after = SOON + timedelta(hours=2)
    assert _book(client, starts_at=after.isoformat(),
                 ends_at=(after + timedelta(hours=1)).isoformat()).status_code == 201


def test_moving_a_booking_onto_blocked_time_asks_first_and_changes_nothing(client):
    later = SOON + timedelta(days=3)
    appointment_id = _book(client, starts_at=later.isoformat(),
                           ends_at=(later + timedelta(hours=1)).isoformat()).json()["id"]
    _block(client)
    before_row = client.get("/api/appointments/%d" % appointment_id).json()
    before_jobs = _jobs()
    move = {"starts_at": SOON.isoformat(), "ends_at": (SOON + timedelta(hours=1)).isoformat()}
    r = client.patch("/api/appointments/%d" % appointment_id, json=move)
    assert r.status_code == 409
    assert client.get("/api/appointments/%d" % appointment_id).json() == before_row
    assert _jobs() == before_jobs
    ok = client.patch("/api/appointments/%d" % appointment_id,
                      json={**move, "allow_blocked_time": True})
    assert ok.status_code == 200, ok.text
    jobs = _pending_reminders(appointment_id)
    assert sorted(j.payload["offset"] for j in jobs) == ["1h", "24h"]
    assert all(SOON.isoformat() in j.dedupe_key for j in jobs)


# ---------------- the opportunity modal still links its deal ----------------

def test_booking_from_the_opportunity_modal_still_links_the_deal(client):
    """The modal has no Opportunity field; the deal's own tab sends the id without
    drawing it, exactly as the dialog does when opened from a deal."""
    r = _book(client, opportunity_id=client.ids["deal"])
    assert r.status_code == 201, r.text
    assert r.json()["opportunity_id"] == client.ids["deal"]
    deal = client.get("/api/opportunities/%d" % client.ids["deal"]).json()
    assert [(a["id"], a["title"]) for a in deal["appointments"]] == [
        (r.json()["id"], "Jane Doe")]
    detail = client.get("/api/appointments/%d" % r.json()["id"]).json()
    assert detail["opportunity_title"] == "Jane roof"


def test_a_booking_row_is_unchanged_by_the_new_columns_when_they_are_not_sent(client):
    """The CLI's create, which sends none of the new fields, books as before."""
    r = client.post("/api/appointments", json={
        "title": "Roof inspection", "starts_at": SOON.isoformat(),
        "ends_at": (SOON + timedelta(hours=1)).isoformat(),
        "contact_id": client.ids["jane"]})
    assert r.status_code == 201, r.text
    got = client.get("/api/appointments/%d" % r.json()["id"]).json()
    assert (got["title"], got["status"], got["description"], got["location"],
            got["calendar_id"], got["assigned_user_id"]) == (
        "Roof inspection", "confirmed", None, None, None, None)
    assert r.json()["automation"] == "queued 24h,1h"

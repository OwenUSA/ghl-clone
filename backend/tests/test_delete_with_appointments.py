"""Deleting a contact that still has an appointment.

This whole module exists because of one production failure: `DELETE
/api/contacts/{id}` for a contact with any appointment row answered **500**, with
`ForeignKeyViolation on appointments_contact_id_fkey` in the API log. The endpoint
knew about opportunities (detached) and conversations (deleted) and nothing at all
about appointments, so the database refused the delete and the refusal reached the
user as "Something went wrong".

Two things are asserted here, and they are different assertions:

* the **behaviour** — a 409 that names what is in the way, a `force=true` that
  detaches rather than destroys, and a refusal that mutates nothing; and
* the **database invariant** the 500 came from. SQLite does not enforce foreign
  keys unless asked, so the production failure cannot reproduce itself in this
  suite by accident: `fk_enforced` turns the pragma on and proves the enforcement
  is live before trusting the test that depends on it.
"""
from datetime import UTC, datetime, timedelta

import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Appointment,
    Calendar,
    Contact,
    Conversation,
    ConversationEvent,
    Direction,
    EventType,
    Job,
    Opportunity,
    Pipeline,
    Role,
    Stage,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError


def _later(hours: int) -> datetime:
    return datetime.now(UTC) + timedelta(hours=hours)


@pytest.fixture()
def world():
    """One contact holding one of everything the delete has to reason about."""
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

    # A reachable number: the reminders only queue for a contact that could be
    # texted, and one of the tests is about those reminders.
    jane = Contact(first_name="Jane", last_name="Doe", phone="+19415550101",
                   email="jane@example.test")
    # The control: same world, no appointment, so the path that always worked is
    # regression-tested against the same fixture.
    nobody = Contact(first_name="Nobody", last_name="Booked", phone="+19415550202")
    db.add_all([jane, nobody])
    db.flush()

    pipe = Pipeline(name="Dream Team Roofing AHS")
    db.add(pipe)
    db.flush()
    stage = Stage(pipeline_id=pipe.id, name="New Lead", position=0)
    db.add(stage)
    db.flush()
    opp = Opportunity(title="Jane roof", contact_id=jane.id, pipeline_id=pipe.id,
                      stage_id=stage.id, value_cents=950000,
                      custom_fields={"owen_call_id": "call-abc-123"})
    cal = Calendar(name="Owen's Personal Calendar", user_id=users["admin"].id)
    db.add_all([opp, cal])
    db.flush()

    appt = Appointment(title="Roof inspection", contact_id=jane.id,
                       calendar_id=cal.id, assigned_user_id=users["admin"].id,
                       starts_at=_later(48), ends_at=_later(49),
                       status="confirmed", notes="Gate code 1234")
    db.add(appt)
    conv = Conversation(contact_id=jane.id, unread_count=1)
    db.add_all([appt, conv])
    db.flush()
    db.add(ConversationEvent(conversation_id=conv.id, type=EventType.SMS,
                             direction=Direction.INBOUND,
                             occurred_at=datetime.now(UTC), body="hello"))

    tokens = {}
    for key, u in users.items():
        plain, token = mint_api_token(u, name="test-%s" % key)
        db.add(token)
        tokens[key] = plain

    ids = {"contact": jane.id, "unbooked": nobody.id, "opp": opp.id,
           "appt": appt.id, "conv": conv.id, "calendar": cal.id,
           "pipeline": pipe.id, "stage": stage.id}
    db.commit()
    db.close()

    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + tokens["admin"]
        c.ids = ids
        c.tokens = tokens
        yield c


def as_role(client, role):
    other = TestClient(app)
    other.headers["Authorization"] = "Bearer " + client.tokens[role]
    return other


@pytest.fixture()
def fk_enforced(world):
    """Make SQLite behave like the Postgres this bug was found on.

    Without `PRAGMA foreign_keys=ON`, SQLite accepts a delete that leaves a child
    row pointing at nothing — so the production failure is INVISIBLE to this suite
    by default, and a test that only asserted "no 500" would have passed against
    the broken code while quietly orphaning the appointment.

    Ordering matters: the listener has to be attached and the pool disposed after
    the fixture data is committed, so every connection the request handler opens
    picks the pragma up.
    """
    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    engine.dispose()
    try:
        yield world
    finally:
        event.remove(engine, "connect", _fk_on)
        engine.dispose()


# ---------------- the 500 ----------------

def test_the_pragma_is_actually_on_or_the_next_test_proves_nothing(fk_enforced):
    """Guard for the guard. If this ever stops raising, the foreign keys are not
    being enforced and `test_force_delete_does_not_raise...` is vacuous."""
    db = SessionLocal()
    try:
        assert db.execute(text("PRAGMA foreign_keys")).scalar() == 1
        with pytest.raises(IntegrityError):
            db.execute(text(
                "INSERT INTO appointments "
                "(title, contact_id, starts_at, ends_at, status) "
                "VALUES ('orphan', 999999, '2030-01-01', '2030-01-02', 'x')"))
    finally:
        db.rollback()
        db.close()


def test_force_delete_does_not_leave_the_appointment_pointing_at_a_dead_contact(
        fk_enforced):
    """The production failure, reproduced end to end: with foreign keys enforced,
    the delete either works and leaves the child row consistent, or it raises."""
    client = fk_enforced
    cid, aid = client.ids["contact"], client.ids["appt"]

    r = client.delete("/api/contacts/%d?force=true" % cid)
    assert r.status_code == 200, r.text
    assert client.get("/api/contacts/%d" % cid).status_code == 404

    db = SessionLocal()
    try:
        row = db.get(Appointment, aid)
        assert row is not None, "the appointment was deleted with the contact"
        assert row.contact_id is None, "it still points at a contact that is gone"
        # And the database agrees there is nothing dangling.
        assert db.execute(text("PRAGMA foreign_key_check")).fetchall() == []
    finally:
        db.close()


# ---------------- the refusal ----------------

def test_deleting_a_contact_with_an_appointment_is_refused_and_mutates_nothing(world):
    """409, not 500, and a refusal that had already detached half of it would pass
    a status-only assertion."""
    cid, aid, oid = world.ids["contact"], world.ids["appt"], world.ids["opp"]
    before = world.get("/api/contacts/%d" % cid).json()
    appt_before = world.get("/api/appointments/%d" % aid).json()
    convs = [c["id"] for c in world.get("/api/conversations?tab=all").json()
             if c["contact_id"] == cid]
    assert convs, "fixture changed — retarget this test"

    r = world.delete("/api/contacts/%d" % cid)
    assert r.status_code == 409
    detail = r.json()["detail"]
    # Both kinds in one sentence: an admin who clears the opportunities should not
    # then be refused a second time over the appointments.
    assert "appointment" in detail and str(aid) in detail
    assert "opportunit" in detail and str(oid) in detail

    assert world.get("/api/contacts/%d" % cid).json() == before
    assert world.get("/api/appointments/%d" % aid).json() == appt_before
    assert world.get("/api/appointments/%d" % aid).json()["contact_id"] == cid
    assert [c["id"] for c in world.get("/api/conversations?tab=all").json()
            if c["contact_id"] == cid] == convs
    assert world.get("/api/opportunities/%d" % oid).json()["contact_id"] == cid


def test_the_refusal_counts_and_names_one_appointment_in_the_singular(world):
    """"1 appointments" is the plural bug this project has already fixed once."""
    cid = world.ids["contact"]
    detail = world.delete("/api/contacts/%d" % cid).json()["detail"]
    assert "1 appointment (" in detail and "1 appointments" not in detail

    world.post("/api/appointments", json={
        "title": "Second visit", "contact_id": cid,
        "calendar_id": world.ids["calendar"],
        "starts_at": _later(72).isoformat(), "ends_at": _later(73).isoformat()})
    detail = world.delete("/api/contacts/%d" % cid).json()["detail"]
    assert "2 appointments (" in detail


# ---------------- what force actually does ----------------

def test_force_detaches_the_appointment_and_keeps_every_field_of_it(world):
    """A booking is the record that a slot was taken — the same reason
    `DELETE /api/appointments/{id}` cancels instead of deleting. Tidying a contact
    must not quietly destroy one."""
    cid, aid = world.ids["contact"], world.ids["appt"]
    before = world.get("/api/appointments/%d" % aid).json()

    r = world.delete("/api/contacts/%d?force=true" % cid)
    assert r.status_code == 200
    assert r.json()["detached_appointments"] == [aid]

    after = world.get("/api/appointments/%d" % aid)
    assert after.status_code == 200, "the appointment was deleted with the contact"
    body = after.json()
    assert body["contact_id"] is None and body["contact_name"] is None
    # Everything that is not the customer link survives untouched: the slot, the
    # calendar, the tech it was assigned to, the notes, and the status — a
    # confirmed visit is NOT silently cancelled by a contact delete.
    for field in ("title", "starts_at", "ends_at", "status", "notes",
                  "calendar_id", "assigned_user_id"):
        assert body[field] == before[field], "%s changed" % field
    assert body["status"] == "confirmed"

    # It is still on the calendar, drawn without a customer name.
    listed = world.get("/api/appointments", params={
        "start": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        "end": (datetime.now(UTC) + timedelta(days=7)).isoformat()}).json()
    mine = [a for a in listed if a["id"] == aid]
    assert mine and mine[0]["contact_name"] is None


def test_force_withdraws_the_reminders_of_the_appointments_it_detaches(world):
    """A queued reminder for a customer who no longer exists can never send —
    `_h_appointment_reminder` checks — but "could never send" and "is not queued"
    are different things to the dispatcher reading `ghl jobs list --status
    pending`. Only the second one should be true here."""
    cid = world.ids["contact"]
    booked = world.post("/api/appointments", json={
        "title": "Tear-off quote", "contact_id": cid,
        "calendar_id": world.ids["calendar"],
        "starts_at": _later(96).isoformat(), "ends_at": _later(97).isoformat()})
    assert booked.status_code == 201
    assert booked.json()["automation"].startswith("queued"), booked.json()
    new_id = booked.json()["id"]

    pending_before = [j for j in world.get("/api/jobs?status=pending").json()
                      if j["type"] == "appointment_reminder"]
    assert pending_before, "fixture changed — no reminder was queued to withdraw"

    r = world.delete("/api/contacts/%d?force=true" % cid)
    assert r.status_code == 200
    assert r.json()["reminders_cancelled"] == len(pending_before)

    assert not [j for j in world.get("/api/jobs?status=pending").json()
                if j["type"] == "appointment_reminder"], (
        "a reminder for a deleted contact is still sitting in the queue")

    db = SessionLocal()
    try:
        rows = db.scalars(select(Job).where(
            Job.type == "appointment_reminder")).all()
        assert rows and all(j.status == "cancelled" for j in rows)
        # The dedupe key is RELEASED, not kept — the same rule every other retire
        # path follows, so a later booking on this slot can queue again.
        assert all(j.dedupe_key is None for j in rows)
        assert db.get(Appointment, new_id).contact_id is None
    finally:
        db.close()


def test_the_opportunity_is_still_detached_and_owen_call_id_is_untouched(world):
    """Unchanged by this branch, and asserted here because the appointment loop
    runs in the same transaction: a bug in one must not take the other with it."""
    cid, oid = world.ids["contact"], world.ids["opp"]
    r = world.delete("/api/contacts/%d?force=true" % cid)
    assert r.status_code == 200
    assert r.json()["detached_opportunities"] == [oid]

    opp = world.get("/api/opportunities/%d" % oid)
    assert opp.status_code == 200, "an opportunity was deleted with the contact"
    assert opp.json()["contact_id"] is None
    assert opp.json()["custom_fields"]["owen_call_id"] == "call-abc-123"


def test_the_conversations_still_go_with_the_contact(world):
    cid = world.ids["contact"]
    convs = [c["id"] for c in world.get("/api/conversations?tab=all").json()
             if c["contact_id"] == cid]
    assert convs
    assert world.delete("/api/contacts/%d?force=true" % cid).status_code == 200
    assert not [c for c in world.get("/api/conversations?tab=all").json()
                if c["id"] in convs]


def test_a_contact_with_nothing_attached_still_deletes_without_force(world):
    """The path that already worked. `force` is consent to detach, not a general
    "really delete" flag, so a contact with no opportunity and no appointment must
    not start needing it."""
    cid = world.ids["unbooked"]
    detail = world.get("/api/contacts/%d" % cid).json()
    assert detail["opportunities"] == [] and detail["appointments"] == []

    r = world.delete("/api/contacts/%d" % cid)
    assert r.status_code == 200
    assert r.json() == {"deleted": cid, "detached_opportunities": [],
                        "detached_appointments": [], "reminders_cancelled": 0}
    assert world.get("/api/contacts/%d" % cid).status_code == 404


def test_the_contact_payload_names_the_appointments_the_delete_would_sever(world):
    """The panel turns the 409 into "these will be detached" and builds that list
    from the loaded contact, not by parsing the error prose."""
    cid, aid = world.ids["contact"], world.ids["appt"]
    listed = world.get("/api/contacts/%d" % cid).json()["appointments"]
    assert [a["id"] for a in listed] == [aid]
    assert listed[0]["title"] == "Roof inspection"
    assert listed[0]["status"] == "confirmed"


# ---------------- who may do it ----------------

@pytest.mark.parametrize("role", ["dispatcher", "tech"])
def test_a_role_that_may_not_delete_is_refused_and_mutates_nothing(world, role):
    cid, aid, oid = world.ids["contact"], world.ids["appt"], world.ids["opp"]
    before = world.get("/api/contacts/%d" % cid).json()
    appt_before = world.get("/api/appointments/%d" % aid).json()
    other = as_role(world, role)

    for url in ("/api/contacts/%d" % cid, "/api/contacts/%d?force=true" % cid):
        assert other.delete(url).status_code == 403, "%s deleted via %s" % (role, url)
        assert world.get("/api/contacts/%d" % cid).json() == before
        assert world.get("/api/appointments/%d" % aid).json() == appt_before
        assert world.get("/api/appointments/%d" % aid).json()["contact_id"] == cid
        assert world.get("/api/opportunities/%d" % oid).json()["contact_id"] == cid


# ---------------- the appointment endpoint is honest ----------------

def test_cancelling_an_appointment_keeps_the_row_and_says_so(world):
    """`DELETE /api/appointments/{id}` is a cancel. The body has to say that: a
    caller reading a 200 off a DELETE should not have to read a docstring to find
    out the row is still there."""
    aid = world.ids["appt"]
    r = world.delete("/api/appointments/%d" % aid)
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] is False, "a DELETE that kept the row claims otherwise"
    assert body["status"] == "cancelled"
    assert body["id"] == aid

    still = world.get("/api/appointments/%d" % aid)
    assert still.status_code == 200, "the row did not survive the cancel"
    assert still.json()["status"] == "cancelled"
    assert still.json()["title"] == "Roof inspection"
    # And it is still attached to its customer — cancelling is not detaching.
    assert still.json()["contact_id"] == world.ids["contact"]


def test_cancelling_withdraws_the_pending_reminders_and_reports_how_many(world):
    cid = world.ids["contact"]
    booked = world.post("/api/appointments", json={
        "title": "Estimate", "contact_id": cid,
        "calendar_id": world.ids["calendar"],
        "starts_at": _later(96).isoformat(), "ends_at": _later(97).isoformat()})
    queued = len([j for j in world.get("/api/jobs?status=pending").json()
                  if j["type"] == "appointment_reminder"])
    assert queued

    r = world.delete("/api/appointments/%d" % booked.json()["id"])
    assert r.json()["reminders_cancelled"] == queued
    assert not [j for j in world.get("/api/jobs?status=pending").json()
                if j["type"] == "appointment_reminder"]


def test_a_tech_cannot_cancel_an_appointment_and_nothing_is_mutated(world):
    aid = world.ids["appt"]
    before = world.get("/api/appointments/%d" % aid).json()
    assert as_role(world, "tech").delete("/api/appointments/%d" % aid
                                        ).status_code == 403
    assert world.get("/api/appointments/%d" % aid).json() == before
    assert world.get("/api/appointments/%d" % aid).json()["status"] == "confirmed"

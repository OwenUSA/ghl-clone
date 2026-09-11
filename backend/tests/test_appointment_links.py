"""A booking and the deal it belongs to, in both directions.

`Appointment` had no link to `Opportunity` at all. It now has one nullable
`opportunity_id`, and the link is readable from either end: the deal lists the
visits booked for it, the visit names its deal.

The rule that matters most is the one about deleting. **Deleting an opportunity
leaves the appointment standing, unlinked.** A booked visit is a promise to a
customer — somebody is expecting a van on Tuesday — and tidying up a deal record
must not quietly cancel it. This codebase already makes that call once, in the
other direction: deleting a contact detaches its opportunities rather than
destroying them.

The reminder queue is deliberately out of scope here, and one test says so by
counting the jobs: linking a booking to a deal is not a change to when it happens,
so it must not touch a single reminder. CLAUDE.md records what goes wrong when
something reschedules carelessly.
"""
from datetime import UTC, datetime, timedelta

import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
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
from sqlalchemy import select

# Far enough out that both reminder offsets are still in the future, so the
# queue-counting test has something to count.
SOON = datetime.now(UTC) + timedelta(days=9)


def _iso(dt):
    return dt.isoformat()


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    admin = User(email="a@x.test", name="Owen", role=Role.ADMIN)
    tech = User(email="t@x.test", name="Tech", role=Role.TECH)
    db.add_all([admin, tech])

    person = Contact(first_name="Jane", last_name="Doe", phone="(941) 555-0004")
    db.add(person)

    pipe = Pipeline(name="Dream Team Roofing AHS", position=0)
    db.add(pipe)
    db.flush()
    stage = Stage(pipeline_id=pipe.id, name="New Lead", position=0)
    db.add(stage)
    db.flush()

    deal = Opportunity(title="Jane roof", contact_id=person.id, pipeline_id=pipe.id,
                       stage_id=stage.id, value_cents=950000,
                       custom_fields={"owen_call_id": "call-abc-123"})
    other = Opportunity(title="Jane gutters", contact_id=person.id,
                        pipeline_id=pipe.id, stage_id=stage.id, value_cents=120000)
    db.add_all([deal, other])

    cal = Calendar(name="Roof crew", pipeline_id=pipe.id)
    db.add(cal)

    tokens = {}
    for key, u in (("admin", admin), ("tech", tech)):
        plain, token = mint_api_token(u, name="test-" + key)
        db.add(token)
        tokens[key] = plain
    db.commit()
    ids = {"pipeline": pipe.id, "stage": stage.id, "deal": deal.id,
           "other": other.id, "contact": person.id, "calendar": cal.id}
    db.close()

    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + tokens["admin"]
        c.ids = ids
        c.tokens = tokens
        yield c


def _book(client, **overrides):
    body = {"title": "Roof inspection", "starts_at": _iso(SOON),
            "ends_at": _iso(SOON + timedelta(hours=1)),
            "contact_id": client.ids["contact"],
            "calendar_id": client.ids["calendar"]}
    body.update(overrides)
    return client.post("/api/appointments", json=body)


def _in_range(client, **params):
    """What the calendar grid asks for: everything overlapping a window."""
    q = {"start": _iso(SOON - timedelta(days=3)),
         "end": _iso(SOON + timedelta(days=3))}
    q.update(params)
    return client.get("/api/appointments", params=q).json()


def test_booking_from_an_opportunity_reaches_the_calendar(client):
    """The whole point of the link: a visit booked from a deal is a real
    appointment, not a note on the deal. The calendar's own range query has to
    return it."""
    r = _book(client, title="Jane roof — inspection",
              opportunity_id=client.ids["deal"])
    assert r.status_code == 201, r.text
    appointment_id = r.json()["id"]
    assert r.json()["opportunity_id"] == client.ids["deal"]

    drawn = _in_range(client)
    assert [a["id"] for a in drawn] == [appointment_id], (
        "the booking never reached the calendar range query")
    assert drawn[0]["opportunity_id"] == client.ids["deal"]
    assert drawn[0]["opportunity_title"] == "Jane roof", (
        "the grid cannot say which deal the visit belongs to")
    # ...and it is filed on the calendar it was booked onto, so the filters work.
    assert [a["id"] for a in _in_range(
        client, calendar_ids=str(client.ids["calendar"]))] == [appointment_id]


def test_the_opportunity_shows_the_visit_it_has_booked(client):
    """"Inspection — Tue 9:00am" has to come from somewhere."""
    deal = client.get("/api/opportunities/%d" % client.ids["deal"]).json()
    assert deal["appointments"] == [], "a deal with no bookings is not empty"

    first = _book(client, title="Inspection", opportunity_id=client.ids["deal"])
    second = _book(client, title="Repair",
                   starts_at=_iso(SOON + timedelta(days=1)),
                   ends_at=_iso(SOON + timedelta(days=1, hours=3)),
                   opportunity_id=client.ids["deal"])
    assert (first.status_code, second.status_code) == (201, 201)

    deal = client.get("/api/opportunities/%d" % client.ids["deal"]).json()
    # Soonest first, and BOTH of them: a roof job is an inspection and then a
    # repair, and showing only one would be a lie about what is scheduled.
    assert [a["title"] for a in deal["appointments"]] == ["Inspection", "Repair"]
    assert deal["appointments"][0]["calendar_name"] == "Roof crew"

    # The other deal's detail is unaffected.
    assert client.get("/api/opportunities/%d" % client.ids["other"]).json()[
        "appointments"] == []


def test_binding_from_the_calendar_links_an_existing_open_opportunity(client):
    """The same link, made from the other direction: a booking created on the
    calendar is bound to a deal afterwards."""
    appointment_id = _book(client).json()["id"]
    assert client.get("/api/appointments/%d" % appointment_id).json()[
        "opportunity_id"] is None

    r = client.patch("/api/appointments/%d" % appointment_id,
                     json={"opportunity_id": client.ids["deal"]})
    assert r.status_code == 200, r.text
    assert r.json()["opportunity_id"] == client.ids["deal"]
    assert r.json()["opportunity_title"] == "Jane roof"

    # Readable from the deal too — one link, two ends.
    assert [a["id"] for a in client.get(
        "/api/opportunities/%d" % client.ids["deal"]).json()["appointments"]] == [
        appointment_id]

    # And it can be moved to another deal, or taken off.
    client.patch("/api/appointments/%d" % appointment_id,
                 json={"opportunity_id": client.ids["other"]})
    assert client.get("/api/opportunities/%d" % client.ids["deal"]).json()[
        "appointments"] == []
    assert [a["id"] for a in client.get(
        "/api/opportunities/%d" % client.ids["other"]).json()["appointments"]] == [
        appointment_id]

    client.patch("/api/appointments/%d" % appointment_id,
                 json={"opportunity_id": None})
    assert client.get("/api/appointments/%d" % appointment_id).json()[
        "opportunity_id"] is None


def test_deleting_an_opportunity_leaves_the_appointment_standing(client):
    """NOT a cascade, and not an accident of a nullable column: the endpoint
    detaches on purpose and says how many it detached.

    On PostgreSQL the foreign key would refuse the delete outright if it did not,
    so this is also the test that the delete works at all against the real
    database rather than only against SQLite, which does not enforce FKs.
    """
    appointment_id = _book(client, title="Jane roof — inspection",
                           opportunity_id=client.ids["deal"]).json()["id"]

    r = client.delete("/api/opportunities/%d" % client.ids["deal"])
    assert r.status_code == 200, r.text
    assert r.json()["detached_appointments"] == [appointment_id]

    assert client.get("/api/opportunities/%d" % client.ids["deal"]).status_code == 404
    survivor = client.get("/api/appointments/%d" % appointment_id)
    assert survivor.status_code == 200, "the booking went with the deal"
    assert survivor.json()["opportunity_id"] is None
    assert survivor.json()["opportunity_title"] is None
    assert survivor.json()["title"] == "Jane roof — inspection"
    assert survivor.json()["status"] == "confirmed", (
        "the visit was quietly cancelled by a delete on a different record")
    # Still drawn on the calendar, which is where the customer's slot lives.
    assert [a["id"] for a in _in_range(client)] == [appointment_id]


def test_a_booking_bound_to_a_deal_that_does_not_exist_is_a_sentence_not_a_500(client):
    before = len(_in_range(client))
    r = _book(client, opportunity_id=9999)
    assert r.status_code == 404
    assert "9999" in r.json()["detail"]
    assert len(_in_range(client)) == before, "a refused booking was still created"

    appointment_id = _book(client).json()["id"]
    bad = client.patch("/api/appointments/%d" % appointment_id,
                       json={"opportunity_id": 9999})
    assert bad.status_code == 404
    assert client.get("/api/appointments/%d" % appointment_id).json()[
        "opportunity_id"] is None


def test_linking_a_booking_does_not_touch_the_reminder_queue(client):
    """CLAUDE.md is explicit about how easily reminder rescheduling breaks, so
    this change stays away from it. Asserted by counting rows in `jobs`, the way
    the existing reminder tests do — not by reading the code.
    """
    appointment_id = _book(client).json()["id"]

    db = SessionLocal()
    before = [(j.id, j.status, j.dedupe_key, j.run_after) for j in db.scalars(
        select(Job).where(Job.type == "appointment_reminder")
        .order_by(Job.id)).all()]
    db.close()
    assert len(before) == 2, "fixture changed — expected T-24h and T-1h"

    r = client.patch("/api/appointments/%d" % appointment_id,
                     json={"opportunity_id": client.ids["deal"]})
    assert r.status_code == 200, r.text
    assert r.json()["automation"] == "unchanged"

    db = SessionLocal()
    after = [(j.id, j.status, j.dedupe_key, j.run_after) for j in db.scalars(
        select(Job).where(Job.type == "appointment_reminder")
        .order_by(Job.id)).all()]
    db.close()
    assert after == before, "linking a deal churned the customer's reminders"


def test_an_unbound_booking_is_still_an_ordinary_booking(client):
    """The link is OPTIONAL in both directions. A call at 2am becomes a visit
    before anybody has filed a deal for it."""
    r = _book(client)
    assert r.status_code == 201, r.text
    assert r.json()["opportunity_id"] is None
    drawn = _in_range(client)
    assert [a["opportunity_id"] for a in drawn] == [None]
    assert [a["opportunity_title"] for a in drawn] == [None]


def test_a_tech_cannot_book_a_visit_from_a_deal_and_none_is_created(client):
    """`POST /api/appointments` is STAFF and was not widened by this change."""
    tech = TestClient(app)
    tech.headers["Authorization"] = "Bearer " + client.tokens["tech"]
    r = tech.post("/api/appointments", json={
        "title": "Sneaky", "starts_at": _iso(SOON),
        "ends_at": _iso(SOON + timedelta(hours=1)),
        "opportunity_id": client.ids["deal"]})
    assert r.status_code == 403
    assert _in_range(client) == []
    assert client.get("/api/opportunities/%d" % client.ids["deal"]).json()[
        "appointments"] == []


def test_the_open_deals_a_booking_can_bind_to_are_listable_across_pipelines(client):
    """The calendar dialog offers open deals without knowing which board they are
    filed on, so `GET /api/opportunities` had to stop requiring a pipeline."""
    second = Pipeline(name="Retail", position=1)
    db = SessionLocal()
    db.add(second)
    db.flush()
    stage = Stage(pipeline_id=second.id, name="New Lead", position=0)
    db.add(stage)
    db.flush()
    db.add(Opportunity(title="R-201", pipeline_id=second.id, stage_id=stage.id))
    db.commit()
    second_id = second.id
    db.close()

    everywhere = client.get("/api/opportunities", params={"status": "open"}).json()
    assert {o["title"] for o in everywhere} == {"Jane roof", "Jane gutters", "R-201"}
    assert {o["pipeline_id"] for o in everywhere} == {client.ids["pipeline"], second_id}

    # The board still narrows to one pipeline, unchanged.
    one = client.get("/api/opportunities",
                     params={"pipeline_id": client.ids["pipeline"]}).json()
    assert {o["title"] for o in one} == {"Jane roof", "Jane gutters"}

    # A won deal drops out of the default filter, which is what "open" means.
    client.patch("/api/opportunities/%d/detail" % client.ids["other"],
                 json={"status": "won"})
    assert "Jane gutters" not in {
        o["title"] for o in client.get("/api/opportunities",
                                       params={"status": "open"}).json()}

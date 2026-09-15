"""Every scheduled Workiz job is on the calendar, past or future (2026-09-15).

The owner's decisions, asserted as behaviour on the rows themselves:

  * every non-cancelled job with a Scheduled time has exactly ONE appointment on
    "Workiz Jobs (imported)", past or future, and a past one queues NOTHING;
  * a re-import MOVES it when Workiz's times changed — past and future — and follows a
    changed end, a multi-day job keeping its real end;
  * a job now cancelled or unscheduled in Workiz CANCELS the importer's appointment (a
    status, never a delete);
  * an appointment a person created or changed is left alone entirely, and reported;
  * the title is the customer's name, like the card, and the old "Inspection" titles
    an earlier import left are fixed;
  * a second commit changes nothing.

Fixture CSVs only — the real export is customer data.
"""
import hashlib
import io
from datetime import UTC, datetime, timedelta

import pytest
from app import workiz_import as wi
from app.auth import mint_api_token
from app.db import SessionLocal, engine
from app.main import app
from app.models import Appointment, Calendar, Job, Opportunity, Role, User
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.test_workiz_import import (
    as_utc,
    client_row,
    do_import,
    dump,
    job_row,
    write_csvs,
    wz,
)


@pytest.fixture()
def fresh(db):
    """An empty database (conftest's `db`), under test_workiz_import.py's name."""
    return db


NOW = datetime.now(UTC)
# Whole minutes: the export has no seconds.
BASE = NOW.replace(second=0, microsecond=0)
LAST_WEEK = BASE - timedelta(days=7)
NEXT_WEEK = BASE + timedelta(days=7)

CLIENTS = [client_row("1", "Ada Rowe", phone="9415550111"),
           client_row("2", "Ben Vale", phone="9415550222"),
           client_row("3", "Cy Moss", phone="9415550333")]


def row(number, client, phone, start, hours=2, **kw):
    return job_row(number, client, phone=phone, status=kw.pop("status", "Submitted"),
                   scheduled=wz(start) if start else "",
                   end=wz(start + timedelta(hours=hours)) if start else "", **kw)


def three(ada=LAST_WEEK, ben=NEXT_WEEK, cy=None):
    """J1 done last week, J2 booked next week, J3 unscheduled. Each argument is a new
    start (a datetime, or None for no Scheduled time) or a new Workiz STATUS (a str),
    which keeps that job's default start."""
    out = []
    for (number, client, phone, start, status), value in zip(
            [("J1", "Ada Rowe", "9415550111", LAST_WEEK, "Done"),
             ("J2", "Ben Vale", "9415550222", NEXT_WEEK, "Submitted"),
             ("J3", "Cy Moss", "9415550333", None, "Submitted")], (ada, ben, cy),
            strict=True):
        if isinstance(value, str):
            status = value
        else:
            start = value
        out.append(row(number, client, phone, start, status=status))
    return out


def card(db, job_id):
    db.expire_all()
    return next(o for o in db.scalars(select(Opportunity)).all()
                if (o.custom_fields or {}).get("workiz_id") == job_id)


def visits(db, job_id):
    return db.scalars(select(Appointment).where(
        Appointment.opportunity_id == card(db, job_id).id).order_by(Appointment.id)).all()


def visit(db, job_id):
    got = visits(db, job_id)
    assert len(got) == 1, "exactly one visit for %s, found %d" % (job_id, len(got))
    return got[0]


def action(plan, job_id):
    return next(a.action for a in plan.appointments + plan.appointment_cancels
                if a.job_workiz_id == job_id)


def jobs_rows(db):
    return db.scalar(select(func.count(Job.id)))


# ------------------------------------------------------------------ past and future


def test_a_past_job_gets_its_appointment_and_queues_nothing(fresh, tmp_path):
    plan = do_import(fresh, tmp_path, CLIENTS, three())
    a = visit(fresh, "J1")
    assert as_utc(a.starts_at) == LAST_WEEK
    assert as_utc(a.ends_at) == LAST_WEEK + timedelta(hours=2)
    assert a.status == "confirmed"
    assert fresh.get(Calendar, a.calendar_id).name == wi.IMPORT_CALENDAR
    assert as_utc(visit(fresh, "J2").starts_at) == NEXT_WEEK
    # J3 has no Scheduled time: no visit at all.
    assert visits(fresh, "J3") == []
    assert jobs_rows(fresh) == 0, "a past booking must never reach the queue"
    got = wi.appointments_json(plan)
    assert (got["create_past"], got["create_future"]) == (["J1"], ["J2"])


def test_the_command_line_books_past_visits_with_the_jobs_guard_still_at_zero(
        fresh, tmp_path):
    """Through `run`, which counts the jobs table inside the transaction."""
    fresh.close()
    cp, jp = write_csvs(tmp_path, CLIENTS, three())
    out = io.StringIO()
    assert wi.run(cp, jp, commit=True, stream=out) == 0
    with SessionLocal() as check:
        assert check.scalar(select(func.count(Appointment.id))) == 2
        assert check.scalar(select(func.count(Job.id))) == 0
    assert "appointments_created_past" in out.getvalue()


def test_a_multi_day_job_keeps_its_real_end(fresh, tmp_path):
    do_import(fresh, tmp_path, CLIENTS,
              [row("J2", "Ben Vale", "9415550222", NEXT_WEEK, hours=30)])
    a = visit(fresh, "J2")
    assert as_utc(a.ends_at) - as_utc(a.starts_at) == timedelta(hours=30)


# ------------------------------------------------------------------ moves


@pytest.mark.parametrize("job_id,old,new", [
    ("J1", LAST_WEEK, LAST_WEEK + timedelta(days=1, hours=3)),     # past -> past
    ("J1", LAST_WEEK, NEXT_WEEK + timedelta(hours=1)),             # past -> future
    ("J2", NEXT_WEEK, NEXT_WEEK - timedelta(days=2, minutes=30)),  # future -> future
    ("J2", NEXT_WEEK, LAST_WEEK - timedelta(days=1)),              # future -> past
])
def test_a_changed_time_moves_the_same_appointment(fresh, tmp_path, job_id, old, new):
    kw = {"ada": old} if job_id == "J1" else {"ben": old}
    do_import(fresh, tmp_path, CLIENTS, three(**kw))
    first = visit(fresh, job_id)
    first_id = first.id

    kw = {"ada": new} if job_id == "J1" else {"ben": new}
    plan = do_import(fresh, tmp_path, CLIENTS, three(**kw))
    assert action(plan, job_id) == "move"
    moved = visit(fresh, job_id)
    assert moved.id == first_id, "moved, not a second visit"
    assert as_utc(moved.starts_at) == new
    assert as_utc(moved.ends_at) == new + timedelta(hours=2)
    assert jobs_rows(fresh) == 0


def test_a_changed_end_alone_is_followed(fresh, tmp_path):
    do_import(fresh, tmp_path, CLIENTS, three())
    rows = three()
    rows[0]["End"] = wz(LAST_WEEK + timedelta(hours=5))
    plan = do_import(fresh, tmp_path, CLIENTS, rows)
    assert action(plan, "J1") == "move"
    assert as_utc(visit(fresh, "J1").ends_at) == LAST_WEEK + timedelta(hours=5)


# ------------------------------------------------------------------ cancellations


@pytest.mark.parametrize("status", ["Canceled", "Cancelled"])
def test_a_job_cancelled_in_workiz_cancels_the_importers_appointment(
        fresh, tmp_path, status):
    do_import(fresh, tmp_path, CLIENTS, three())
    appt_id = visit(fresh, "J2").id
    plan = do_import(fresh, tmp_path, CLIENTS, three(ben=status))
    assert action(plan, "J2") == "cancel"
    a = fresh.get(Appointment, appt_id)
    assert a is not None, "cancelled is a status, never a delete"
    assert a.status == "cancelled"
    assert as_utc(a.starts_at) == NEXT_WEEK
    # The other visits are untouched.
    assert visit(fresh, "J1").status == "confirmed"
    assert jobs_rows(fresh) == 0


def test_a_job_that_loses_its_scheduled_time_cancels_its_appointment(fresh, tmp_path):
    do_import(fresh, tmp_path, CLIENTS, three())
    plan = do_import(fresh, tmp_path, CLIENTS, three(ada=None))
    assert action(plan, "J1") == "cancel"
    assert visit(fresh, "J1").status == "cancelled"


def test_workiz_scheduling_it_again_restores_the_importers_cancellation(fresh, tmp_path):
    do_import(fresh, tmp_path, CLIENTS, three())
    do_import(fresh, tmp_path, CLIENTS, three(ben="Canceled"))
    plan = do_import(fresh, tmp_path, CLIENTS, three(ben=NEXT_WEEK + timedelta(days=1)))
    assert action(plan, "J2") == "restore"
    a = visit(fresh, "J2")
    assert a.status == "confirmed"
    assert as_utc(a.starts_at) == NEXT_WEEK + timedelta(days=1)


# ------------------------------------------------------------------ a person's changes


def test_a_visit_a_person_moved_is_left_alone_and_reported(fresh, tmp_path):
    do_import(fresh, tmp_path, CLIENTS, three())
    by_hand = NEXT_WEEK + timedelta(days=2)
    v = visit(fresh, "J2")
    v.starts_at, v.ends_at = by_hand, by_hand + timedelta(hours=1)
    fresh.commit()

    plan = do_import(fresh, tmp_path, CLIENTS, three(ben=NEXT_WEEK + timedelta(days=5)))
    assert action(plan, "J2") == "manual"
    a = visit(fresh, "J2")
    assert as_utc(a.starts_at) == by_hand
    assert as_utc(a.ends_at) == by_hand + timedelta(hours=1)
    text = wi.render(plan, None, committed=False)
    section = text.split("Appointments (", 1)[1].split("Technicians (", 1)[0]
    left_alone = section.split("LEFT ALONE", 1)[1].splitlines()[1]
    assert "J2" in left_alone and "its time" in left_alone

    # ...and Workiz cancelling it does not cancel a person's visit either.
    plan = do_import(fresh, tmp_path, CLIENTS, three(ben="Canceled"))
    assert action(plan, "J2") == "manual"
    assert visit(fresh, "J2").status == "confirmed"


def test_a_person_cancelling_the_visit_is_respected(fresh, tmp_path):
    do_import(fresh, tmp_path, CLIENTS, three())
    visit(fresh, "J2").status = "cancelled"
    fresh.commit()
    plan = do_import(fresh, tmp_path, CLIENTS, three(ben=NEXT_WEEK + timedelta(hours=4)))
    assert action(plan, "J2") == "manual"
    a = visit(fresh, "J2")
    assert a.status == "cancelled" and as_utc(a.starts_at) == NEXT_WEEK


def test_a_visit_edited_through_the_api_is_left_alone(fresh, tmp_path):
    """The real path a dispatcher uses: PATCH /api/appointments/{id}."""
    do_import(fresh, tmp_path, CLIENTS, three())
    admin = User(email="boss@dtr.test", name="Boss", role=Role.ADMIN, password_hash="x")
    fresh.add(admin)
    fresh.flush()
    plain, token = mint_api_token(admin, name="test")
    fresh.add(token)
    appt_id = visit(fresh, "J1").id
    fresh.commit()
    moved_to = LAST_WEEK + timedelta(hours=26)
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + plain
        r = c.patch("/api/appointments/%d" % appt_id,
                    json={"starts_at": moved_to.isoformat(),
                          "ends_at": (moved_to + timedelta(hours=2)).isoformat()})
        assert r.status_code == 200, r.text
    queued = jobs_rows(fresh)

    plan = do_import(fresh, tmp_path, CLIENTS, three(ada=LAST_WEEK - timedelta(days=3)))
    assert action(plan, "J1") == "manual"
    assert as_utc(fresh.get(Appointment, appt_id).starts_at) == moved_to
    assert jobs_rows(fresh) == queued, "the import added nothing to the queue"


def test_an_appointment_a_person_booked_on_the_card_is_never_touched(fresh, tmp_path):
    do_import(fresh, tmp_path, CLIENTS, three())
    o = card(fresh, "J1")
    other = Calendar(name="Owen's Personal Calendar")
    fresh.add(other)
    fresh.flush()
    mine = Appointment(title="Follow-up", calendar_id=other.id, opportunity_id=o.id,
                       contact_id=o.contact_id, starts_at=NEXT_WEEK,
                       ends_at=NEXT_WEEK + timedelta(hours=1), status="booked")
    fresh.add(mine)
    fresh.commit()
    before = (mine.id, mine.title, mine.calendar_id, mine.starts_at, mine.status)

    do_import(fresh, tmp_path, CLIENTS, three(ada=LAST_WEEK + timedelta(hours=1)))
    fresh.expire_all()
    mine = fresh.get(Appointment, before[0])
    assert (mine.id, mine.title, mine.calendar_id, mine.starts_at, mine.status) == before
    workiz = [a for a in visits(fresh, "J1") if a.calendar_id != other.id]
    assert len(workiz) == 1 and as_utc(workiz[0].starts_at) == LAST_WEEK + timedelta(hours=1)


def test_a_hand_booked_workiz_calendar_visit_with_no_record_is_not_adopted(
        fresh, tmp_path):
    """A card with no import record whose Workiz-calendar visit carries a location a
    person typed: left alone, and no second visit is booked beside it."""
    do_import(fresh, tmp_path, CLIENTS, three(ben=None))
    o = card(fresh, "J2")
    cal = fresh.scalar(select(Calendar).where(Calendar.name == wi.IMPORT_CALENDAR))
    fresh.add(Appointment(title="Ben Vale", calendar_id=cal.id, opportunity_id=o.id,
                          contact_id=o.contact_id, starts_at=NEXT_WEEK,
                          ends_at=NEXT_WEEK + timedelta(hours=1), status="confirmed",
                          location="Gate code 1234"))
    fresh.commit()
    plan = do_import(fresh, tmp_path, CLIENTS, three(ben=NEXT_WEEK + timedelta(days=1)))
    assert action(plan, "J2") == "manual"
    a = visit(fresh, "J2")
    assert as_utc(a.starts_at) == NEXT_WEEK and a.location == "Gate code 1234"


# ------------------------------------------------------------------ earlier imports, titles


def _as_an_earlier_import_left_it(db, job_id, *, title, start):
    """What production holds: a visit from an importer that kept no record, with the
    job's type as its title."""
    a = visit(db, job_id)
    a.title, a.starts_at, a.ends_at = title, start, start + timedelta(hours=2)
    db.commit()
    o = card(db, job_id)
    blob = dict(o.custom_fields)
    blob.pop(wi.WORKIZ_APPOINTMENT)
    o.custom_fields = blob
    db.commit()
    return a.id


def test_an_earlier_imports_visit_is_adopted_moved_and_retitled(fresh, tmp_path):
    """J2P6JO on production: Mon 10-12 from an older import, Workiz now says Tue 1-3."""
    do_import(fresh, tmp_path, CLIENTS, three())
    appt_id = _as_an_earlier_import_left_it(fresh, "J1", title="Inspection",
                                            start=LAST_WEEK - timedelta(hours=3))
    plan = do_import(fresh, tmp_path, CLIENTS, three())
    assert action(plan, "J1") == "move"
    assert wi.appointments_json(plan)["adopted_from_an_earlier_import"] == ["J1"]
    a = visit(fresh, "J1")
    assert a.id == appt_id
    assert as_utc(a.starts_at) == LAST_WEEK
    assert a.title == "Ada Rowe" == card(fresh, "J1").title
    assert card(fresh, "J1").custom_fields[wi.WORKIZ_APPOINTMENT]["id"] == appt_id


def test_an_inspection_title_alone_is_fixed(fresh, tmp_path):
    do_import(fresh, tmp_path, CLIENTS, three())
    _as_an_earlier_import_left_it(fresh, "J2", title="Inspection", start=NEXT_WEEK)
    plan = do_import(fresh, tmp_path, CLIENTS, three())
    assert action(plan, "J2") == "retitle"
    assert visit(fresh, "J2").title == "Ben Vale"


def test_every_imported_title_is_the_cards_customer_name(fresh, tmp_path):
    rows = three()
    for r in rows:
        r["Job name"] = "Inspection"
    do_import(fresh, tmp_path, CLIENTS, rows)
    for job_id in ("J1", "J2"):
        assert visit(fresh, job_id).title == card(fresh, job_id).title
    assert {visit(fresh, j).title for j in ("J1", "J2")} == {"Ada Rowe", "Ben Vale"}


# ------------------------------------------------------------------ idempotency, dry run


def test_a_second_commit_changes_nothing(fresh, tmp_path):
    do_import(fresh, tmp_path, CLIENTS, three())
    do_import(fresh, tmp_path, CLIENTS, three(ada=LAST_WEEK + timedelta(hours=1),
                                              ben="Canceled"))
    visit(fresh, "J1").title = "Renamed by a person"
    fresh.commit()
    rows = three(ada=LAST_WEEK + timedelta(hours=4), ben="Canceled")
    do_import(fresh, tmp_path, CLIENTS, rows)
    first = dump(fresh)

    plan = do_import(fresh, tmp_path, CLIENTS, rows)
    assert dump(fresh) == first
    assert {a.action for a in plan.appointments + plan.appointment_cancels} <= {
        "unchanged", "manual"}


def test_a_dry_run_with_moves_and_cancels_writes_nothing(fresh, tmp_path):
    do_import(fresh, tmp_path, CLIENTS, three())
    fresh.close()
    path = engine.url.database

    def digest():
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    before = digest()
    cp, jp = write_csvs(tmp_path, CLIENTS, three(ada=LAST_WEEK + timedelta(hours=2),
                                                 ben="Canceled",
                                                 cy=LAST_WEEK - timedelta(days=1)))
    out = io.StringIO()
    assert wi.run(cp, jp, commit=False, stream=out) == 0
    assert digest() == before
    text = out.getvalue()
    section = text.split("Appointments (", 1)[1].split("Technicians (", 1)[0]
    assert "1      to create — 1 in the past, 0 in the future" in section
    assert "past jobs J3" in section
    assert "jobs J1" in section.split("to move", 1)[1].splitlines()[1]
    assert "J2" in section.split("to cancel", 1)[1].splitlines()[1]
    for name in ("Ada", "Ben", "Cy", "Rowe", "Vale", "Moss"):
        assert name not in section, "the report carries no customer data"


def test_the_json_report_carries_the_same_job_numbers(fresh, tmp_path):
    do_import(fresh, tmp_path, CLIENTS, three())
    plan = do_import(fresh, tmp_path, CLIENTS,
                     three(ada=LAST_WEEK + timedelta(hours=2), ben=None,
                           cy=NEXT_WEEK), commit=False)
    got = wi.as_json(plan, None)["appointments"]
    assert got["move"] == ["J1"]
    assert got["create_future"] == ["J3"]
    assert got["cancel"] == [{"job": "J2", "why": "no Scheduled time in Workiz"}]
    assert got["reminders_enqueued"] == 0


def test_the_import_record_is_read_only_through_the_api(fresh, tmp_path):
    do_import(fresh, tmp_path, CLIENTS, three())
    admin = User(email="boss@dtr.test", name="Boss", role=Role.ADMIN, password_hash="x")
    fresh.add(admin)
    fresh.flush()
    plain, token = mint_api_token(admin, name="test")
    fresh.add(token)
    o = card(fresh, "J1")
    record = dict(o.custom_fields[wi.WORKIZ_APPOINTMENT])
    fresh.commit()
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + plain
        r = c.patch("/api/opportunities/%d/detail" % o.id, json={"custom_fields": {
            wi.WORKIZ_APPOINTMENT: "999"}})
        assert r.status_code == 400, r.text
        assert "read-only" in r.json()["detail"]
    assert card(fresh, "J1").custom_fields[wi.WORKIZ_APPOINTMENT] == record

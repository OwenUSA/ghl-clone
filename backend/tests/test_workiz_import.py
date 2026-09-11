"""The Workiz import, asserted as behaviour.

Everything here runs against FIXTURE CSVs written into `tmp_path`. The real export
holds 856 real customers' names, phone numbers, email addresses and home addresses,
and it is not test data.

What each test is actually protecting, because the status codes are not the point:

  * **A status is routed, never guessed.** One that is not in the table skips its
    row and says so; a cancelled job creates nothing at all; the six real rows whose
    source sends them to a board with no column for their status still land
    somewhere sensible.
  * **Two records sharing a phone become ONE contact with BOTH jobs**, the most
    complete record survives, and every value that lost is written into a note —
    asserted by reading the note, not by counting rows.
  * **Nothing an import does can text a customer.** The `jobs` queue is counted, and
    a future-dated appointment — the one shape that schedules reminders through the
    API — is imported with the count still at zero.
  * **`--dry-run` writes nothing**, asserted by hashing the database file.
  * **Running it twice is running it once**, asserted by comparing a full dump of
    every row the import touches, not by counting contacts.
"""
import csv
import hashlib
import io
import os
from datetime import UTC, datetime, timedelta

import pytest
from app import workiz_import as wi
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Appointment,
    Contact,
    ConversationEvent,
    CustomFieldDef,
    EventType,
    Job,
    Opportunity,
    Pipeline,
    Role,
    Stage,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select

CLIENT_COLUMNS = ["Client #", "Name", "Email", "Address", "Phone", "Ad Source"]
JOB_COLUMNS = ["Job #", "Job name", "Client", "Tags", "Type", "Job Created",
               "Scheduled", "End", "Phone", "Email", "Status", "Tech", "Created by",
               "Address", "City", "State", "Zip code", "Total", "Source",
               "Lead Created Date", "Job origin"]


def as_utc(moment: datetime) -> datetime:
    """SQLite hands back a naive datetime for a timezone-aware column; PostgreSQL
    hands back an aware one. Both are UTC. The tests run on SQLite."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def wz(moment: datetime) -> str:
    """A datetime in the export's own format, in the export's own timezone."""
    return moment.astimezone(wi.WORKIZ_TZ).strftime(wi.WORKIZ_DATETIME)


PAST = wz(datetime.now(UTC) - timedelta(days=400))
SOON = wz(datetime.now(UTC) + timedelta(days=9))
SOON_END = wz(datetime.now(UTC) + timedelta(days=9, hours=3))


def client_row(number, name, *, email="", address="", phone="", ad_source=""):
    return {"Client #": number, "Name": name, "Email": email, "Address": address,
            "Phone": phone, "Ad Source": ad_source}


def job_row(number, client, *, status="Done", source="AHS", total="100.00",
            job_type="Roof Repair", phone="", scheduled=PAST, end=PAST,
            created=PAST, name="", city="", state="", zip_code="", address=""):
    return {"Job #": number, "Job name": name, "Client": client, "Tags": "",
            "Type": job_type, "Job Created": created, "Scheduled": scheduled,
            "End": end, "Phone": phone, "Email": "", "Status": status, "Tech": "",
            "Created by": "Office", "Address": address, "City": city,
            "State": state, "Zip code": zip_code, "Total": total, "Source": source,
            "Lead Created Date": "", "Job origin": "New"}


def write_csvs(tmp_path, clients, jobs):
    cp, jp = str(tmp_path / "clients.csv"), str(tmp_path / "jobs.csv")
    for path, columns, rows in ((cp, CLIENT_COLUMNS, clients),
                                (jp, JOB_COLUMNS, jobs)):
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=columns)
            w.writeheader()
            w.writerows(rows)
    return cp, jp


@pytest.fixture()
def fresh():
    """An empty database with no pipelines at all — the importer makes both."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


def do_import(db, tmp_path, clients, jobs, *, commit=True):
    """Plan, and optionally apply, inside the caller's session."""
    cp, jp = write_csvs(tmp_path, clients, jobs)
    plan = wi.build_plan(db, wi.read_csv(cp), wi.read_csv(jp))
    if commit:
        wi.apply_plan(db, plan)
        db.commit()
    return plan


def stage_of(db, title_fragment):
    o = db.scalar(select(Opportunity).where(Opportunity.title.contains(title_fragment)))
    assert o is not None, "no opportunity matching %r" % title_fragment
    stage = db.get(Stage, o.stage_id)
    return db.get(Pipeline, o.pipeline_id).name, stage.name, o.status


# ------------------------------------------------------------------ routing


# Every row of the owner's routing table, plus the two cross-pipeline cases the
# export actually contains. `Source` picks the board; the status picks the column,
# spelled in that board's own vocabulary.
ROUTING = [
    ("Submitted", "AHS", wi.AHS, "New Lead", "open"),
    ("Submitted", "Google", wi.RETAIL, "New Lead", "open"),
    ("In Progress (Inspections)", "AHS", wi.AHS, "Inspection", "open"),
    ("In Progress (Inspections)", "Google", wi.RETAIL, "Inspection / Estimate", "open"),
    # Note the trailing spaces inside the parentheses — they are in the raw data.
    ("In Progress (Request Approval  )", "AHS", wi.AHS,
     "Request the Approval (AHS)", "open"),
    ("In Progress (Request Approval  )", "Google", wi.RETAIL, "Estimate Sent", "open"),
    ("In Progress (Repair Schedule)", "AHS", wi.AHS,
     "Approved- Repair Schedule", "open"),
    ("In Progress (Repair Schedule)", "Google", wi.RETAIL, "Scheduled", "open"),
    ("In Progress (Callback)", "AHS", wi.AHS, "Call Back", "open"),
    ("In Progress (Callback)", "Google", wi.RETAIL, "Follow Up", "open"),
    ("Pending (Collect Balance)", "AHS", wi.AHS, "Submit The Invoice", "open"),
    ("Pending (Estimate Follow Up)", "Google", wi.RETAIL, "Follow Up", "open"),
    ("Pending (Estimate Follow Up)", "AHS", wi.AHS, "Call Back", "open"),
    ("Pending (New Roof Estimate)", "Google", wi.RETAIL, "Estimate Sent", "open"),
    # Work done, money not collected. OPEN, and emphatically not won.
    ("done pending approval", "AHS", wi.AHS, "Submit The Invoice", "open"),
    ("done pending approval", "Google", wi.RETAIL, "Invoice", "open"),
    ("Done", "AHS", wi.AHS, "Submit The Invoice", "won"),
    ("Done", "Google", wi.RETAIL, "Invoice", "won"),
]


@pytest.mark.parametrize("status,source,pipeline,stage,opp_status", ROUTING)
def test_a_status_lands_in_the_right_pipeline_and_stage(
        fresh, tmp_path, status, source, pipeline, stage, opp_status):
    do_import(fresh, tmp_path,
              [client_row("1", "Ada Rowe", phone="9415550111")],
              [job_row("J1", "Ada Rowe", status=status, source=source,
                       phone="9415550111")])
    assert stage_of(fresh, "Ada Rowe") == (pipeline, stage, opp_status)


@pytest.mark.parametrize("status", ["Canceled", "canceled", "  CANCELED  "])
def test_a_cancelled_job_creates_nothing_at_all(fresh, tmp_path, status):
    """Not an opportunity, not an appointment, and not a skipped-row report either.

    A cancelled job is a job that did not happen. It still counts towards the
    client being a Customer rather than a Lead — the business dealt with them —
    which is the one thing it does leave behind.
    """
    plan = do_import(
        fresh, tmp_path,
        [client_row("1", "Ada Rowe", phone="9415550111")],
        [job_row("J1", "Ada Rowe", status=status, phone="9415550111",
                 scheduled=SOON, end=SOON_END)])

    assert fresh.scalar(select(func.count(Opportunity.id))) == 0
    assert fresh.scalar(select(func.count(Appointment.id))) == 0
    assert plan.skipped == []
    assert plan.dropped_cancelled == 1
    # ...but they are a Customer, not a Lead.
    assert fresh.scalar(select(Contact)).contact_type == "Customer"


def test_an_unknown_status_is_skipped_and_reported_never_guessed(fresh, tmp_path):
    plan = do_import(
        fresh, tmp_path,
        [client_row("1", "Ada Rowe", phone="9415550111"),
         client_row("2", "Ben Vale", phone="9415550222")],
        [job_row("J1", "Ada Rowe", status="In Progress (Chasing The Dog)",
                 phone="9415550111"),
         job_row("J2", "Ben Vale", status="Done", phone="9415550222")])

    # The good row still landed: one bad row does not abort the run.
    titles = [o.title for o in fresh.scalars(select(Opportunity)).all()]
    assert len(titles) == 1 and "Ben Vale" in titles[0]

    assert [(s.where, s.ident) for s in plan.skipped] == [("jobs", "J1")]
    assert "Chasing The Dog" in plan.skipped[0].reason
    assert "not guessed" in plan.skipped[0].reason
    # And it is in the human report, not only in the object.
    assert "J1" in wi.render(plan, None, committed=False)


def test_status_matching_ignores_case_and_whitespace(fresh, tmp_path):
    """The raw data has "done pending approval" in lower case and a status with
    trailing spaces INSIDE its parentheses. Both must resolve, not skip."""
    do_import(fresh, tmp_path,
              [client_row("1", "Ada Rowe", phone="9415550111"),
               client_row("2", "Ben Vale", phone="9415550222")],
              [job_row("J1", "Ada Rowe", status="  DONE Pending  Approval ",
                       phone="9415550111"),
               job_row("J2", "Ben Vale", status="in progress (request approval  )",
                       phone="9415550222")])
    assert stage_of(fresh, "Ada Rowe")[1:] == ("Submit The Invoice", "open")
    assert stage_of(fresh, "Ben Vale")[1:] == ("Request the Approval (AHS)", "open")


# ------------------------------------------------------------------ merging


MERGE_CLIENTS = [
    # The fuller record: name, email, address, phone, source. It is deliberately
    # NOT first in the file, so "the base is the most complete one" is being
    # tested rather than "the base is row one".
    client_row("77", "Dana Short", phone="(941) 555-0300"),
    client_row("42", "Dana Shortbridge", email="dana@example.test",
               address="18 Gulf Rd, Palmetto 34221", phone="941-555-0300",
               ad_source="Google"),
    client_row("91", "D. Shortbridge", email="other@example.test",
               phone="+1 941 555 0300"),
]
MERGE_JOBS = [
    job_row("JA", "Dana Shortbridge", phone="9415550300", total="1200.00"),
    job_row("JB", "D. Shortbridge", phone="9415550300", total="300.00",
            status="Submitted"),
]


def test_two_client_rows_sharing_a_phone_make_one_contact_with_both_jobs(
        fresh, tmp_path):
    do_import(fresh, tmp_path, MERGE_CLIENTS, MERGE_JOBS)

    contacts = fresh.scalars(select(Contact)).all()
    assert len(contacts) == 1, "three records, one phone, one person"
    person = contacts[0]
    assert len(person.opportunities) == 2
    assert {o.custom_fields["workiz_id"] for o in person.opportunities} == {"JA", "JB"}


def test_the_most_complete_record_wins_and_fills_its_blanks_from_the_others(
        fresh, tmp_path):
    do_import(fresh, tmp_path, MERGE_CLIENTS, MERGE_JOBS)
    person = fresh.scalar(select(Contact))

    # Client #42 has five fields filled; #77 and #91 have fewer. #42 is the base.
    assert person.custom_fields["workiz_id"] == "42"
    assert person.name == "Dana Shortbridge"
    assert person.email == "dana@example.test"
    assert person.address_city == "Palmetto"
    assert person.source == "Google"
    # Every number that folded in is recorded, so a re-run can still find this row.
    assert person.custom_fields["workiz_merged_ids"] == ["42", "77", "91"]


def test_nothing_a_merge_discards_is_lost_it_goes_in_a_note(fresh, tmp_path):
    do_import(fresh, tmp_path, MERGE_CLIENTS, MERGE_JOBS)
    person = fresh.scalar(select(Contact))

    notes = fresh.scalars(select(ConversationEvent).where(
        ConversationEvent.type == EventType.NOTE)).all()
    assert len(notes) == 1
    body = notes[0].body

    # Both losing records are named, with the values that lost.
    assert "Client #77" in body and "Client #91" in body
    assert "Dana Short" in body            # the name that lost
    assert "other@example.test" in body    # the email that lost
    # And the record that survived is named too, so the note is reviewable alone.
    assert "Kept: Client #42" in body
    # It is an internal NOTE, which is STAFF-only on every path (DECISIONS.md).
    assert notes[0].type is EventType.NOTE
    assert notes[0].conversation_id is not None
    assert person.id == fresh.get(type(notes[0]), notes[0].id).conversation.contact_id


def test_a_merge_note_is_rewritten_not_duplicated_on_a_re_run(fresh, tmp_path):
    do_import(fresh, tmp_path, MERGE_CLIENTS, MERGE_JOBS)
    do_import(fresh, tmp_path, MERGE_CLIENTS, MERGE_JOBS)
    assert fresh.scalar(select(func.count(ConversationEvent.id)).where(
        ConversationEvent.type == EventType.NOTE)) == 1


def test_a_phone_too_short_to_identify_anyone_does_not_merge_strangers(
        fresh, tmp_path):
    """Four unrelated people whose phone is a single character must stay four
    people. An empty match key is the one that collapses a database."""
    do_import(fresh, tmp_path,
              [client_row("1", "Ada Rowe", phone="1"),
               client_row("2", "Ben Vale", phone="1"),
               client_row("3", "Cal Ives", phone="0")],
              [])
    assert fresh.scalar(select(func.count(Contact.id))) == 3


# ------------------------------------------------------------------ phones


def test_a_phone_that_will_not_normalise_still_imports(fresh, tmp_path):
    """`store_phone` never raises, and this import must never drop a customer for
    a number nobody can parse — the number in the record beats no record."""
    do_import(fresh, tmp_path,
              [client_row("1", "Ada Rowe", phone="call the office x2"),
               client_row("2", "Ben Vale", phone="9415550222")],
              [job_row("J2", "Ben Vale", phone="9415550222")])

    people = {c.name: c for c in fresh.scalars(select(Contact)).all()}
    assert len(people) == 2
    assert people["Ada Rowe"].phone == "call the office x2"   # exactly as typed
    assert people["Ben Vale"].phone == "+19415550222"          # E.164 where it parses


# ------------------------------------------------------------------ money


@pytest.mark.parametrize("written,cents", [
    ("0.00", 0), ("0.01", 1), ("0.10", 10), ("100.00", 10000),
    ("1234.56", 123456), ("$1,234.56", 123456), ("19.99", 1999),
    ("0.07", 7), ("8675.30", 867530), ("", 0),
])
def test_money_round_trips_without_cent_drift(fresh, tmp_path, written, cents):
    do_import(fresh, tmp_path,
              [client_row("1", "Ada Rowe", phone="9415550111")],
              [job_row("J1", "Ada Rowe", phone="9415550111", total=written)])
    assert fresh.scalar(select(Opportunity)).value_cents == cents


def test_the_total_of_many_amounts_is_exact(fresh, tmp_path):
    """The failure this catches is float: 0.1 + 0.2 summed 300 times drifts."""
    amounts = ["0.10", "0.20", "1234.56", "0.07", "99.99"] * 20
    clients = [client_row(str(i), "Cust %d" % i, phone="94155%05d" % i)
               for i in range(len(amounts))]
    jobs = [job_row("J%d" % i, "Cust %d" % i, phone="94155%05d" % i, total=a)
            for i, a in enumerate(amounts)]
    do_import(fresh, tmp_path, clients, jobs)
    assert fresh.scalar(select(func.sum(Opportunity.value_cents))) == 20 * (
        10 + 20 + 123456 + 7 + 9999)


def test_an_unreadable_amount_skips_its_row_and_does_not_abort_the_run(
        fresh, tmp_path):
    plan = do_import(
        fresh, tmp_path,
        [client_row("1", "Ada Rowe", phone="9415550111"),
         client_row("2", "Ben Vale", phone="9415550222")],
        [job_row("J1", "Ada Rowe", phone="9415550111", total="about nine hundred"),
         job_row("J2", "Ben Vale", phone="9415550222", total="50.00")])
    assert fresh.scalar(select(func.count(Opportunity.id))) == 1
    assert [s.ident for s in plan.skipped] == ["J1"]


# ------------------------------------------------------------------ appointments


def test_a_past_job_creates_no_appointment_and_a_future_one_does(fresh, tmp_path):
    do_import(fresh, tmp_path,
              [client_row("1", "Ada Rowe", phone="9415550111"),
               client_row("2", "Ben Vale", phone="9415550222")],
              [job_row("J1", "Ada Rowe", phone="9415550111",
                       scheduled=PAST, end=PAST),
               job_row("J2", "Ben Vale", phone="9415550222", status="Submitted",
                       scheduled=SOON, end=SOON_END)])

    appts = fresh.scalars(select(Appointment)).all()
    assert len(appts) == 1
    assert "Ben Vale" in appts[0].title
    assert as_utc(appts[0].starts_at) > datetime.now(UTC)
    # Both jobs still became deals — only the BOOKING is filtered by date.
    assert fresh.scalar(select(func.count(Opportunity.id))) == 2


def test_an_imported_appointment_is_attached_to_its_job(fresh, tmp_path):
    do_import(fresh, tmp_path,
              [client_row("1", "Ada Rowe", phone="9415550111")],
              [job_row("J1", "Ada Rowe", phone="9415550111", status="Submitted",
                       scheduled=SOON, end=SOON_END)])
    appt = fresh.scalar(select(Appointment))
    opp = fresh.scalar(select(Opportunity))
    assert appt.opportunity_id == opp.id
    assert appt.contact_id == opp.contact_id
    assert appt.calendar.name == wi.IMPORT_CALENDAR


def test_a_visit_with_no_usable_end_time_still_gets_a_length(fresh, tmp_path):
    do_import(fresh, tmp_path,
              [client_row("1", "Ada Rowe", phone="9415550111")],
              [job_row("J1", "Ada Rowe", phone="9415550111", status="Submitted",
                       scheduled=SOON, end="")])
    appt = fresh.scalar(select(Appointment))
    assert appt.ends_at - appt.starts_at == wi.DEFAULT_VISIT


def test_times_are_read_as_eastern_not_as_utc(fresh, tmp_path):
    """A 9am job is a 9am job. Reading the export as UTC would move every visit."""
    when = datetime.now(UTC) + timedelta(days=30)
    eastern_9am = when.astimezone(wi.WORKIZ_TZ).replace(
        hour=9, minute=0, second=0, microsecond=0)
    do_import(fresh, tmp_path,
              [client_row("1", "Ada Rowe", phone="9415550111")],
              [job_row("J1", "Ada Rowe", phone="9415550111", status="Submitted",
                       scheduled=eastern_9am.strftime(wi.WORKIZ_DATETIME),
                       end=(eastern_9am + timedelta(hours=2)).strftime(
                           wi.WORKIZ_DATETIME))])
    appt = fresh.scalar(select(Appointment))
    assert as_utc(appt.starts_at).astimezone(wi.WORKIZ_TZ).hour == 9


# ------------------------------------------------------ NO REMINDERS, EVER


REMINDER_SHAPES = [
    # The exact shape that schedules T-24h and T-1h through the API.
    ("a future appointment", SOON, SOON_END),
    # ...and one far enough out that "too soon for a reminder" cannot be the
    # reason the queue is empty.
    ("a distant appointment", wz(datetime.now(UTC) + timedelta(days=120)),
     wz(datetime.now(UTC) + timedelta(days=120, hours=2))),
]


@pytest.mark.parametrize("_what,scheduled,end", REMINDER_SHAPES)
def test_no_reminder_job_is_ever_enqueued_by_an_import(fresh, tmp_path, _what,
                                                       scheduled, end):
    """The single most dangerous thing in this task, asserted on the queue itself.

    Not "no reminder job": NO job of any type. A contact create notifies the team,
    a stage change texts the customer, an appointment schedules two reminders —
    every one of those is a row in `jobs`, and there must be none.
    """
    assert fresh.scalar(select(func.count(Job.id))) == 0
    do_import(fresh, tmp_path,
              [client_row("1", "Ada Rowe", phone="9415550111"),
               client_row("2", "Ben Vale", phone="9415550222")],
              [job_row("J1", "Ada Rowe", phone="9415550111", status="Submitted",
                       scheduled=scheduled, end=end),
               job_row("J2", "Ben Vale", phone="9415550222", status="Done")])

    assert fresh.scalar(select(func.count(Appointment.id))) == 1, "it really booked"
    assert fresh.scalars(select(Job)).all() == [], "an import must queue nothing"


def test_the_importer_cannot_reach_the_queue_at_all(fresh, tmp_path):
    """Structural, and the reason the runtime guard is not the only defence.

    `app.workiz_import` does not import `app.automations` or `app.queue`, so there
    is no code path from an import to `enqueue()` for a future change to find. If
    this ever has to be relaxed, the docstring in that module has to change with
    it — which is the point.
    """
    with open(wi.__file__, encoding="utf-8") as fh:
        source = fh.read()
    for forbidden in ("from .automations", "from .queue", "import automations",
                      "import queue"):
        assert forbidden not in source, (
            "workiz_import must not be able to reach the job queue (%r)" % forbidden)


def test_a_job_row_appearing_mid_import_rolls_the_whole_thing_back(
        fresh, tmp_path, monkeypatch):
    """The runtime half of the guarantee: if anything DID queue work, nothing is
    written at all. Proven by making the import queue something on purpose."""
    real_apply = wi.apply_plan

    def apply_and_queue(db, plan):
        counts = real_apply(db, plan)
        db.add(Job(type="appointment_reminder", payload={}, dedupe_key="x"))
        db.flush()
        return counts

    monkeypatch.setattr(wi, "apply_plan", apply_and_queue)
    fresh.close()
    cp, jp = write_csvs(
        tmp_path, [client_row("1", "Ada Rowe", phone="9415550111")],
        [job_row("J1", "Ada Rowe", phone="9415550111", status="Submitted",
                 scheduled=SOON, end=SOON_END)])

    code = wi.run(cp, jp, commit=True, stream=io.StringIO())
    assert code != 0

    with SessionLocal() as check:
        assert check.scalar(select(func.count(Contact.id))) == 0
        assert check.scalar(select(func.count(Opportunity.id))) == 0
        assert check.scalar(select(func.count(Job.id))) == 0


# ------------------------------------------------------------------ dry run


def test_dry_run_writes_absolutely_nothing(fresh, tmp_path):
    """Asserted on the bytes of the database file, not on a row count."""
    fresh.close()
    path = engine.url.database

    def digest():
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    before = digest()

    cp, jp = write_csvs(
        tmp_path,
        [client_row("1", "Ada Rowe", phone="9415550111", address="1 Gulf Rd, FL 34221"),
         client_row("2", "Ben Vale", phone="9415550222")],
        [job_row("J1", "Ada Rowe", phone="9415550111", status="Submitted",
                 scheduled=SOON, end=SOON_END),
         job_row("J2", "Ben Vale", phone="9415550222")])

    out = io.StringIO()
    assert wi.run(cp, jp, commit=False, stream=out) == 0
    assert "DRY RUN" in out.getvalue()
    # It did describe real work — an empty plan would pass this test for free.
    assert "2" in out.getvalue()

    assert digest() == before, "a dry run changed the database file"


def test_dry_run_is_the_default_and_writing_needs_an_explicit_flag(fresh, tmp_path):
    fresh.close()
    cp, jp = write_csvs(tmp_path,
                        [client_row("1", "Ada Rowe", phone="9415550111")],
                        [job_row("J1", "Ada Rowe", phone="9415550111")])
    # No --commit anywhere on the line.
    assert wi.main(["--clients", cp, "--jobs", jp]) == 0
    with SessionLocal() as check:
        assert check.scalar(select(func.count(Contact.id))) == 0

    assert wi.main(["--clients", cp, "--jobs", jp, "--commit"]) == 0
    with SessionLocal() as check:
        assert check.scalar(select(func.count(Contact.id))) == 1


# ------------------------------------------------------------------ idempotency


def dump(db) -> list:
    """Everything the import writes, in a stable order, timestamps included."""
    rows = []
    for c in db.scalars(select(Contact).order_by(Contact.id)).all():
        rows.append(("contact", c.first_name, c.last_name, c.email, c.phone,
                     c.source, c.contact_type, c.address_street, c.address_city,
                     c.address_state, c.address_postal_code,
                     sorted((c.custom_fields or {}).items(), key=str),
                     c.created_at, c.updated_at))
    for o in db.scalars(select(Opportunity).order_by(Opportunity.id)).all():
        rows.append(("opp", o.title, o.contact_id, o.pipeline_id, o.stage_id,
                     o.value_cents, o.status, o.source,
                     sorted((o.custom_fields or {}).items(), key=str),
                     o.created_at, o.updated_at))
    for a in db.scalars(select(Appointment).order_by(Appointment.id)).all():
        rows.append(("appt", a.title, a.contact_id, a.opportunity_id, a.calendar_id,
                     a.starts_at, a.ends_at, a.status))
    for e in db.scalars(select(ConversationEvent).order_by(
            ConversationEvent.id)).all():
        rows.append(("event", e.type, e.body))
    for s in db.scalars(select(Stage).order_by(Stage.id)).all():
        rows.append(("stage", s.pipeline_id, s.name, s.position))
    for d in db.scalars(select(CustomFieldDef).order_by(CustomFieldDef.id)).all():
        rows.append(("field", d.key, d.label, d.field_type, list(d.options or [])))
    return rows


TWICE_CLIENTS = [
    *MERGE_CLIENTS,
    client_row("5", "Eve North", email="eve@example.test",
               address="9 Bay St, Bradenton 34205", phone="9415550555",
               ad_source="AHS"),
    client_row("6", "No Jobs Here", phone="9415550666"),
]
TWICE_JOBS = [
    *MERGE_JOBS,
    job_row("JC", "Eve North", phone="9415550555", status="Submitted",
            scheduled=SOON, end=SOON_END, source="AHS", total="7500.25"),
    job_row("JD", "Eve North", phone="9415550555", status="done pending approval",
            source="Google", total="410.00"),
]


def test_running_the_import_twice_produces_identical_data(fresh, tmp_path):
    do_import(fresh, tmp_path, TWICE_CLIENTS, TWICE_JOBS)
    first = dump(fresh)
    assert first, "the first run has to have done something"

    do_import(fresh, tmp_path, TWICE_CLIENTS, TWICE_JOBS)
    assert dump(fresh) == first


def test_a_second_run_updates_rather_than_duplicating(fresh, tmp_path):
    do_import(fresh, tmp_path, TWICE_CLIENTS, TWICE_JOBS)
    plan = do_import(fresh, tmp_path, TWICE_CLIENTS, TWICE_JOBS)

    assert all(c.existing_id is not None for c in plan.contacts)
    assert all(o.existing_id is not None for o in plan.opportunities)
    # Three client rows share a phone and are one person; two more stand alone.
    assert fresh.scalar(select(func.count(Contact.id))) == 3
    assert fresh.scalar(select(func.count(Opportunity.id))) == 4
    assert fresh.scalar(select(func.count(Appointment.id))) == 1


def test_a_changed_export_updates_the_record_in_place(fresh, tmp_path):
    do_import(fresh, tmp_path,
              [client_row("1", "Ada Rowe", phone="9415550111")],
              [job_row("J1", "Ada Rowe", phone="9415550111", total="100.00",
                       status="Submitted")])
    first_id = fresh.scalar(select(Opportunity)).id

    do_import(fresh, tmp_path,
              [client_row("1", "Ada Rowe", email="ada@example.test",
                          phone="9415550111")],
              [job_row("J1", "Ada Rowe", phone="9415550111", total="250.00",
                       status="Done")])

    opp = fresh.scalar(select(Opportunity))
    assert opp.id == first_id, "the same deal, not a second one"
    assert opp.value_cents == 25000
    assert opp.status == "won"
    assert fresh.scalar(select(Contact)).email == "ada@example.test"


def test_a_record_created_in_the_crm_by_hand_is_left_alone(fresh, tmp_path):
    """No `workiz_id` is expected, not an error — and not something to adopt."""
    fresh.add(Contact(first_name="Walk", last_name="In", phone="+19415559999"))
    fresh.commit()
    do_import(fresh, tmp_path,
              [client_row("1", "Ada Rowe", phone="9415550111")], [])
    by_name = {c.name for c in fresh.scalars(select(Contact)).all()}
    assert by_name == {"Walk In", "Ada Rowe"}
    walk_in = fresh.scalar(select(Contact).where(Contact.last_name == "In"))
    assert walk_in.custom_fields in (None, {})


# ------------------------------------------------------------------ stages


@pytest.fixture()
def ahs_board():
    """The measured AHS board, duplicates and all, with one deal on it."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    p = Pipeline(name=wi.AHS, position=0)
    db.add(p)
    db.flush()
    names = ["New Lead", "Inspection", "Request the Approval (AHS)",
             "Approved- Repair Schedule", "Repair in Process", "Submit The Invoice",
             "Call Back", "Call Back", "AHS Upgrades", "Submit Invoices"]
    stages = []
    for position, name in enumerate(names):
        s = Stage(pipeline_id=p.id, name=name, position=position)
        db.add(s)
        db.flush()
        stages.append(s)
    db.commit()
    try:
        yield db, p, stages
    finally:
        db.rollback()
        db.close()


def test_the_duplicate_call_back_column_is_removed_so_names_are_unique(
        ahs_board, tmp_path):
    db, pipeline, _ = ahs_board
    do_import(db, tmp_path, [client_row("1", "Ada Rowe", phone="9415550111")], [])

    names = [s.name for s in db.scalars(
        select(Stage).where(Stage.pipeline_id == pipeline.id)).all()]
    assert names.count("Call Back") == 1
    assert len(names) == len(set(names)), "every stage name is now unique"
    assert "Submit Invoices" not in names       # the near-duplicate, collapsed
    assert "Submit The Invoice" in names        # the one it collapsed into


def test_a_duplicate_stage_holding_deals_is_renamed_never_emptied(
        ahs_board, tmp_path):
    """Nothing in this importer moves or deletes a deal. `owen_call_id` is a live
    join key into the telephony project (CLAUDE.md)."""
    db, pipeline, stages = ahs_board
    person = Contact(first_name="Real", last_name="Customer", phone="+19415550001")
    db.add(person)
    db.flush()
    # One deal on the SECOND "Call Back", one on "Submit Invoices".
    for stage, call_id in ((stages[7], "call-1"), (stages[9], "call-2")):
        db.add(Opportunity(title="AHS " + call_id, contact_id=person.id,
                           pipeline_id=pipeline.id, stage_id=stage.id,
                           custom_fields={"owen_call_id": call_id}))
    db.commit()
    before = {o.id: o.stage_id for o in db.scalars(select(Opportunity)).all()}

    do_import(db, tmp_path, [client_row("1", "Ada Rowe", phone="9415550111")], [])

    names = [s.name for s in db.scalars(
        select(Stage).where(Stage.pipeline_id == pipeline.id)).all()]
    assert len(names) == len(set(names)), "still unique"
    # The empty one went; the populated one was renamed and kept its deal.
    assert "Submit Invoices" in names, "it holds a deal, so it is left alone"
    assert {o.id: o.stage_id for o in db.scalars(select(Opportunity)).all()} == before
    assert db.scalar(select(func.count(Opportunity.id))) == 2


def test_the_retail_board_is_created_in_the_owners_order(fresh, tmp_path):
    do_import(fresh, tmp_path, [client_row("1", "Ada Rowe", phone="9415550111")], [])
    retail = fresh.scalar(select(Pipeline).where(Pipeline.name == wi.RETAIL))
    assert [s.name for s in retail.stages] == wi.RETAIL_STAGES


def test_an_existing_board_does_not_have_columns_imposed_on_it(ahs_board, tmp_path):
    """The owner's board is his configuration. The import adds only what the
    routing table actually needs, and never reorders or renames beyond the
    duplicates."""
    db, pipeline, _ = ahs_board
    before = [s.name for s in db.scalars(
        select(Stage).where(Stage.pipeline_id == pipeline.id)).all()]
    do_import(db, tmp_path, [client_row("1", "Ada Rowe", phone="9415550111")], [])
    after = [s.name for s in db.scalars(
        select(Stage).where(Stage.pipeline_id == pipeline.id)).all()]
    assert set(after) <= set(before), "nothing new was added to a board that had it all"


# ------------------------------------------------------------------ job type


def test_job_type_becomes_a_dropdown_the_api_would_accept(fresh, tmp_path):
    do_import(fresh, tmp_path,
              [client_row("1", "Ada Rowe", phone="9415550111")],
              [job_row("J1", "Ada Rowe", phone="9415550111",
                       job_type="New Roof Replacement")])
    d = fresh.scalar(select(CustomFieldDef).where(CustomFieldDef.key == "job_type"))
    assert d.field_type == CustomFieldDef.DROPDOWN
    assert d.label == "Job type"
    assert "New Roof Replacement" in d.options
    assert fresh.scalar(select(Opportunity)).custom_fields["job_type"] == \
        "New Roof Replacement"
    # Attached to both boards, so the question is asked on either.
    assert len(d.pipelines) == 2


def test_a_type_the_measured_set_does_not_have_is_added_and_reported(
        fresh, tmp_path):
    plan = do_import(fresh, tmp_path,
                     [client_row("1", "Ada Rowe", phone="9415550111")],
                     [job_row("J1", "Ada Rowe", phone="9415550111",
                              job_type="Skylight Flashing")])
    d = fresh.scalar(select(CustomFieldDef).where(CustomFieldDef.key == "job_type"))
    assert "Skylight Flashing" in d.options
    assert any("Skylight Flashing" in n for n in plan.notes)
    assert fresh.scalar(select(Opportunity)).custom_fields["job_type"] == \
        "Skylight Flashing"


# ------------------------------------------ the reserved namespace, via the API


@pytest.fixture()
def api(fresh, tmp_path):
    """A signed-in admin, over a database that has been imported into."""
    do_import(fresh, tmp_path,
              [client_row("1", "Ada Rowe", phone="9415550111")],
              [job_row("J1", "Ada Rowe", phone="9415550111", status="Submitted")])
    admin = User(email="admin@x.test", name="Owen", role=Role.ADMIN)
    fresh.add(admin)
    fresh.flush()
    plain, token = mint_api_token(admin, name="test")
    fresh.add(token)
    opp_id = fresh.scalar(select(Opportunity)).id
    contact_id = fresh.scalar(select(Contact)).id
    fresh.commit()
    fresh.close()
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + plain
        c.opp_id = opp_id
        c.contact_id = contact_id
        yield c


def test_workiz_id_is_visible_in_the_ui(api):
    assert api.get("/api/opportunities/%d" % api.opp_id).json()[
        "custom_fields"]["workiz_id"] == "J1"
    assert api.get("/api/contacts/%d" % api.contact_id).json()[
        "custom_fields"]["workiz_id"] == "1"


def test_workiz_id_cannot_be_edited_through_the_api(api):
    r = api.patch("/api/opportunities/%d/detail" % api.opp_id,
                  json={"custom_fields": {"workiz_id": "J-FORGED"}})
    assert r.status_code == 400, r.text
    assert "read-only" in r.json()["detail"]
    assert api.get("/api/opportunities/%d" % api.opp_id).json()[
        "custom_fields"]["workiz_id"] == "J1"


def test_workiz_id_cannot_be_deleted_through_the_api(api):
    """Two ways to try: send it as null, or leave it out of the object entirely."""
    r = api.patch("/api/opportunities/%d/detail" % api.opp_id,
                  json={"custom_fields": {"workiz_id": None}})
    assert r.status_code == 400, r.text

    api.patch("/api/opportunities/%d/detail" % api.opp_id,
              json={"custom_fields": {}})
    assert api.get("/api/opportunities/%d" % api.opp_id).json()[
        "custom_fields"]["workiz_id"] == "J1"


def test_an_unchanged_echo_of_workiz_id_is_not_a_write(api):
    """The detail form posts the whole blob back on every save; refusing that
    would make every imported deal unsaveable."""
    r = api.patch("/api/opportunities/%d/detail" % api.opp_id,
                  json={"title": "Renamed", "custom_fields": {"workiz_id": "J1"}})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Renamed"
    assert r.json()["custom_fields"]["workiz_id"] == "J1"


def test_workiz_id_cannot_be_set_on_a_deal_that_has_none(api):
    """Setting one by hand would make the next import adopt, or duplicate, a
    record it has never seen."""
    made = api.post("/api/opportunities", json={
        "title": "Walk-in", "pipeline_id": api.get(
            "/api/opportunities/%d" % api.opp_id).json()["pipeline_id"],
        "stage_id": api.get("/api/opportunities/%d" % api.opp_id).json()["stage_id"]})
    assert made.status_code == 201, made.text
    r = api.patch("/api/opportunities/%d/detail" % made.json()["id"],
                  json={"custom_fields": {"workiz_id": "J1"}})
    assert r.status_code == 400, r.text


@pytest.mark.parametrize("label", [
    "workiz_id", "Workiz ID", "workiz id", "WORKIZ  Id", "Workiz merged ids",
])
def test_no_user_defined_field_may_claim_the_workiz_namespace(api, label):
    r = api.post("/api/custom-fields",
                 json={"label": label, "field_type": "text"})
    assert r.status_code == 400, r.text
    assert "Workiz" in r.json()["detail"]


def test_the_owen_namespace_is_still_guarded_too(api):
    """The `workiz_` guard extends the `owen_` one; it does not replace it."""
    r = api.post("/api/custom-fields",
                 json={"label": "Owen campaign", "field_type": "text"})
    assert r.status_code == 400, r.text
    assert "OWEN" in r.json()["detail"]


def test_an_ordinary_custom_field_is_still_perfectly_fine(api):
    r = api.post("/api/custom-fields", json={"label": "How old is the roof?",
                                             "field_type": "number"})
    assert r.status_code == 201, r.text
    assert r.json()["key"] == "how_old_is_the_roof"


# ------------------------------------------------------------------ preflight


def test_the_import_refuses_a_database_that_is_missing_its_columns(
        fresh, tmp_path, monkeypatch):
    """A sentence, not an OperationalError 400 contacts into the run."""
    monkeypatch.setitem(wi.REQUIRED_SCHEMA, "contacts",
                        ("address_street", "a_column_nobody_added"))
    fresh.close()
    cp, jp = write_csvs(tmp_path, [client_row("1", "Ada Rowe", phone="9415550111")],
                        [])
    assert wi.run(cp, jp, commit=True, stream=io.StringIO()) == 6
    with SessionLocal() as check:
        assert check.scalar(select(func.count(Contact.id))) == 0


def test_a_missing_export_file_is_an_exit_code_not_a_traceback(fresh, tmp_path):
    fresh.close()
    missing = str(tmp_path / "not-here.csv")
    assert wi.run(missing, missing, commit=True, stream=io.StringIO()) == 4


# ------------------------------------------------------------------ addresses


@pytest.mark.parametrize("written,expected", [
    ("1 Gulf Rd, Bradenton 34221",
     ("1 Gulf Rd", "Bradenton", None, "34221")),
    ("18 Bay St, Florida 34205",
     ("18 Bay St", None, "Florida", "34205")),
    ("8940 Palm Av, Palmetto, FL 33025 - 2410",
     ("8940 Palm Av", "Palmetto", "FL", "33025-2410")),
    # Trailing empty fields mean "no city recorded", not an empty city.
    ("22 Reef Way ,  ", ("22 Reef Way", None, None, None)),
    # No commas at all: take a trailing state+zip, leave the rest whole.
    ("540 Shell St Bradenton FL 34208",
     ("540 Shell St Bradenton", None, "FL", "34208")),
    # ...but a street that merely ends in five digits is left completely alone.
    ("PO Box 34208", ("PO Box 34208", None, None, None)),
    ("", (None, None, None, None)),
])
def test_an_address_is_split_where_it_can_be_and_kept_whole_where_it_cannot(
        written, expected):
    parsed = wi.parse_address(written)
    assert (parsed["address_street"], parsed["address_city"],
            parsed["address_state"], parsed["address_postal_code"]) == expected


def test_no_character_of_an_address_is_ever_dropped():
    """The rule that makes the parser safe: anything not confidently identified
    stays in `street`. Checked as a property over every shape above."""
    import re
    for written in ["1 Gulf Rd, Bradenton 34221", "18 Bay St, Florida 34205",
                    "8940 Palm Av, Palmetto, FL 33025 - 2410", "22 Reef Way ,  ",
                    "540 Shell St Bradenton FL 34208", "PO Box 34208",
                    "Apt 4, 9 Long Road, Sarasota, Florida 34231-1234"]:
        source = sorted(re.sub(r"[^0-9a-z]", "", written.lower()))
        parsed = wi.parse_address(written)
        got = sorted(re.sub(r"[^0-9a-z]", "",
                            "".join(v or "" for v in parsed.values()).lower()))
        assert source == got, "characters lost parsing %r" % written


def test_the_jobs_file_fills_a_city_the_clients_file_does_not_have(
        fresh, tmp_path):
    """The clients export writes "<street>, Florida <zip>" — no city anywhere. The
    jobs export has a real City column. Blank-fill only, never an override."""
    do_import(fresh, tmp_path,
              [client_row("1", "Ada Rowe", phone="9415550111",
                          address="1 Gulf Rd, Florida 34221")],
              [job_row("J1", "Ada Rowe", phone="9415550111", city="Palmetto",
                       state="Florida", zip_code="34221")])
    person = fresh.scalar(select(Contact))
    assert person.address_city == "Palmetto"
    assert person.address_street == "1 Gulf Rd", "the client's street is not replaced"
    assert person.address_state == "Florida"


# ------------------------------------------------------------------ the report


def test_the_report_carries_no_customer_data(fresh, tmp_path):
    """It gets pasted into tickets and chat windows. The export does not."""
    plan = do_import(
        fresh, tmp_path,
        [client_row("1", "Ada Rowe", email="ada@example.test",
                    address="1 Gulf Rd, Bradenton 34221", phone="9415550111")],
        [job_row("J1", "Ada Rowe", phone="9415550111",
                 status="In Progress (Not A Real Status)")],
        commit=False)
    text = wi.render(plan, None, committed=False)
    for secret in ("Ada", "Rowe", "ada@example.test", "Gulf Rd", "9415550111",
                   "Bradenton"):
        assert secret not in text, "%r leaked into the report" % secret
    # The skipped row is still identifiable — by its Workiz Job #.
    assert "J1" in text


def test_the_report_reconciles_to_the_source_row_counts(fresh, tmp_path):
    plan = do_import(
        fresh, tmp_path,
        [client_row("%d" % i, "Cust %d" % i, phone="94155%05d" % i)
         for i in range(5)],
        [job_row("J1", "Cust 1", phone="9415500001"),
         job_row("J2", "Cust 2", phone="9415500002", status="Canceled"),
         job_row("J3", "Cust 3", phone="9415500003", status="Who Knows")],
        commit=False)
    text = wi.render(plan, None, committed=False)
    assert plan.client_rows == 5 and plan.job_rows == 3
    assert plan.dropped_cancelled == 1
    assert len(plan.opportunities) == 1
    assert "5      client rows" in text
    assert "3      job rows" in text


def test_the_json_report_is_machine_readable_and_just_as_quiet(fresh, tmp_path):
    plan = do_import(fresh, tmp_path,
                     [client_row("1", "Ada Rowe", phone="9415550111")],
                     [job_row("J1", "Ada Rowe", phone="9415550111")],
                     commit=False)
    payload = wi.as_json(plan, None)
    assert payload["appointments"]["reminders_enqueued"] == 0
    assert payload["contacts"]["total"] == 1
    assert payload["opportunities"]["by_status"] == {"won": 1}
    import json as _json
    assert "Ada" not in _json.dumps(payload, default=str)


# ------------------------------------------------------------------ resolution


def test_a_job_whose_client_matches_nothing_is_skipped_and_named(fresh, tmp_path):
    plan = do_import(fresh, tmp_path,
                     [client_row("1", "Ada Rowe", phone="9415550111")],
                     [job_row("J9", "Nobody At All", phone="8135559999")])
    assert fresh.scalar(select(func.count(Opportunity.id))) == 0
    assert [(s.ident, "no client record" in s.reason) for s in plan.skipped] == \
        [("J9", True)]


def test_a_job_is_matched_by_phone_before_name(fresh, tmp_path):
    """Two people can share a name; a phone line is one line. This is the identity
    rule the picker, the contacts search and the telephony project already use."""
    plan = do_import(
        fresh, tmp_path,
        [client_row("1", "Ada Rowe", phone="9415550111"),
         client_row("2", "Ada Rowe", phone="9415550222")],
        [job_row("J1", "Ada Rowe", phone="9415550222")])
    owner = fresh.scalar(select(Opportunity)).contact
    assert owner.custom_fields["workiz_id"] == "2"
    assert plan.conflicts == [], "no ambiguity: the phone settled it"


def test_a_job_whose_name_is_ambiguous_and_whose_phone_is_unknown_is_skipped(
        fresh, tmp_path):
    plan = do_import(
        fresh, tmp_path,
        [client_row("1", "Ada Rowe", phone="9415550111"),
         client_row("2", "Ada Rowe", phone="9415550222")],
        [job_row("J1", "Ada Rowe", phone="7275558888")])
    assert fresh.scalar(select(func.count(Opportunity.id))) == 0
    assert "not guessed" in plan.skipped[0].reason


def test_a_phone_and_name_disagreement_is_reported_for_a_human(fresh, tmp_path):
    plan = do_import(
        fresh, tmp_path,
        [client_row("1", "Ada Rowe", phone="9415550111"),
         client_row("2", "Ben Vale", phone="9415550222")],
        [job_row("J1", "Ada Rowe", phone="9415550222")])
    owner = fresh.scalar(select(Opportunity)).contact
    assert owner.name == "Ben Vale", "the phone won"
    assert len(plan.conflicts) == 1 and "J1" in plan.conflicts[0]
    assert "Conflicts" in wi.render(plan, None, committed=False)


def test_a_duplicate_job_number_in_the_export_is_skipped_not_applied_twice(
        fresh, tmp_path):
    plan = do_import(fresh, tmp_path,
                     [client_row("1", "Ada Rowe", phone="9415550111")],
                     [job_row("J1", "Ada Rowe", phone="9415550111", total="100.00"),
                      job_row("J1", "Ada Rowe", phone="9415550111", total="999.00")])
    assert fresh.scalar(select(func.count(Opportunity.id))) == 1
    assert fresh.scalar(select(Opportunity)).value_cents == 10000
    assert "duplicate Job #" in plan.skipped[0].reason


# ------------------------------------------------------------------ misc


def test_a_deal_keeps_the_date_it_was_created_in_workiz(fresh, tmp_path):
    """Without this, two years of history all looks like it happened today, and
    the dashboard's creation-date range says so."""
    created = datetime.now(UTC) - timedelta(days=300)
    do_import(fresh, tmp_path,
              [client_row("1", "Ada Rowe", phone="9415550111")],
              [job_row("J1", "Ada Rowe", phone="9415550111",
                       created=wz(created))])
    stored = as_utc(fresh.scalar(select(Opportunity)).created_at)
    assert abs((stored - created).total_seconds()) < 120


def test_a_client_with_no_job_is_a_lead_and_one_with_a_job_is_a_customer(
        fresh, tmp_path):
    do_import(fresh, tmp_path,
              [client_row("1", "Ada Rowe", phone="9415550111"),
               client_row("2", "Ben Vale", phone="9415550222")],
              [job_row("J1", "Ada Rowe", phone="9415550111")])
    by_name = {c.name: c.contact_type for c in fresh.scalars(select(Contact)).all()}
    assert by_name == {"Ada Rowe": "Customer", "Ben Vale": "Lead"}


def test_a_single_word_name_is_all_first_name(fresh, tmp_path):
    do_import(fresh, tmp_path, [client_row("1", "Cher", phone="9415550111")], [])
    person = fresh.scalar(select(Contact))
    assert (person.first_name, person.last_name) == ("Cher", "")


def test_a_middle_name_stays_with_the_first_name(fresh, tmp_path):
    do_import(fresh, tmp_path,
              [client_row("1", "Ada Q Rowe", phone="9415550111")], [])
    person = fresh.scalar(select(Contact))
    assert (person.first_name, person.last_name) == ("Ada Q", "Rowe")


def test_a_client_row_with_no_client_number_is_skipped_not_imported(
        fresh, tmp_path):
    plan = do_import(fresh, tmp_path,
                     [client_row("", "Ada Rowe", phone="9415550111"),
                      client_row("2", "Ben Vale", phone="9415550222")],
                     [])
    assert fresh.scalar(select(func.count(Contact.id))) == 1
    assert [s.where for s in plan.skipped] == ["clients"]


def test_the_source_column_becomes_the_contact_source(fresh, tmp_path):
    do_import(fresh, tmp_path,
              [client_row("1", "Ada Rowe", phone="9415550111", ad_source="CL- ADS"),
               client_row("2", "Ben Vale", phone="9415550222")],
              [job_row("J2", "Ben Vale", phone="9415550222", source="Google")])
    by_name = {c.name: c.source for c in fresh.scalars(select(Contact)).all()}
    # Ada's own Ad Source; Ben has none, so his job's Source fills it in.
    assert by_name == {"Ada Rowe": "CL- ADS", "Ben Vale": "Google"}


def test_no_user_is_created_for_a_tech(fresh, tmp_path):
    """`Tech` is skipped entirely this pass, by the owner's decision."""
    rows = [job_row("J1", "Ada Rowe", phone="9415550111")]
    rows[0]["Tech"] = "Luis Candialies"
    do_import(fresh, tmp_path,
              [client_row("1", "Ada Rowe", phone="9415550111")], rows)
    assert fresh.scalar(select(func.count(User.id))) == 0
    assert fresh.scalar(select(Opportunity)).owner_id is None


def test_the_defaults_point_at_the_export_and_not_at_the_repository(fresh):
    """A typo in the path must not silently import an empty file from the cwd."""
    assert os.path.basename(wi.DEFAULT_CLIENTS) == "workiz_clients.csv"
    assert os.path.basename(wi.DEFAULT_JOBS) == "workiz_jobs.csv"
    assert os.path.isabs(wi.DEFAULT_CLIENTS)

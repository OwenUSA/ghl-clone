"""The Workiz `Tech` column (2026-09-15), asserted as behaviour.

The owner's decision, "assign the job's appointment":

  * the card carries the job's technician names, read-only, as `workiz_tech`;
  * a Workiz name maps to a CRM user by the `--tech-map` file, else by an ACTIVE user's
    full name — never by creating a user;
  * a job's FUTURE appointment stays on the Workiz calendar and is assigned to the first
    mapped technician; a re-import follows Workiz only while the assignment is still the
    importer's own, and a person's assignment is never overwritten;
  * the card's OWNER never changes.

Every refusal and every "left alone" is asserted by reading the rows back. Fixture CSVs
only — the real export is customer data.
"""
import hashlib
import io
import json

import pytest
from app import workiz_import as wi
from app.auth import mint_api_token
from app.db import SessionLocal, engine
from app.main import app
from app.models import (
    Appointment,
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
from tests.test_workiz_import import (
    PAST,
    SOON,
    SOON_END,
    client_row,
    do_import,
    job_row,
    write_csvs,
)

CLIENTS = [client_row("1", "Ada Rowe", phone="9415550111"),
           client_row("2", "Ben Vale", phone="9415550222"),
           client_row("3", "Cy Moss", phone="9415550333")]


def job(number, client, phone, tech, *, future=True, **kw):
    row = job_row(number, client, phone=phone, status="Submitted",
                  scheduled=SOON if future else PAST,
                  end=SOON_END if future else PAST, **kw)
    row["Tech"] = tech
    return row


def jobs(ada="", ben="", cy="", *, ada_future=True):
    return [job("J1", "Ada Rowe", "9415550111", ada, future=ada_future),
            job("J2", "Ben Vale", "9415550222", ben),
            job("J3", "Cy Moss", "9415550333", cy, future=False)]


def person(db, name, email, *, active=True, role=Role.TECH, password="x",
           only_assigned=False):
    u = User(name=name, email=email, role=role, is_active=active, password_hash=password,
             only_assigned_data=only_assigned)
    db.add(u)
    db.flush()
    return u


@pytest.fixture()
def crew(db):
    """Three technicians as My Staff would have them, plus a deactivated one, over an
    empty database (conftest's `db`: dropped and recreated, no pipelines at all)."""
    db.antonio = person(db, "Antonio Brown", "antonio@dtr.test")
    db.owen = person(db, "Owen  buzaglo", "owen@dtr.test", role=Role.ADMIN)
    db.nico = person(db, "Nicolas Perez", "nico@dtr.test")
    db.gone = person(db, "Shay", "shay@dtr.test", active=False)
    db.commit()
    return db


def card(db, job_id):
    db.expire_all()
    return next(o for o in db.scalars(select(Opportunity)).all()
                if (o.custom_fields or {}).get("workiz_id") == job_id)


def visit(db, job_id):
    o = card(db, job_id)
    return db.scalar(select(Appointment).where(Appointment.opportunity_id == o.id))


def imp(db, tmp_path, rows, *, tech_map=None, commit=True):
    cp, jp = write_csvs(tmp_path, CLIENTS, rows)
    plan = wi.build_plan(db, wi.read_csv(cp), wi.read_csv(jp), tech_map=tech_map)
    if commit:
        wi.apply_plan(db, plan)
        db.commit()
    return plan


def action_of(plan, job_id):
    return next(a.tech_action for a in plan.appointments if a.job_workiz_id == job_id)


# ------------------------------------------------------------------ names on the card


def test_the_names_are_stored_in_job_order_with_whitespace_collapsed(crew, tmp_path):
    imp(crew, tmp_path, jobs(ada="  Owen   Buzaglo ,Antonio Brown,, Sheila  &  Leo,"
                                   " owen buzaglo"))
    assert card(crew, "J1").custom_fields["workiz_tech"] == [
        "Owen Buzaglo", "Antonio Brown", "Sheila & Leo"]
    # A job with no technician carries no key at all, not an empty list.
    assert "workiz_tech" not in card(crew, "J2").custom_fields


def test_a_re_import_updates_the_names_and_an_empty_tech_clears_them(crew, tmp_path):
    imp(crew, tmp_path, jobs(ada="Antonio Brown", cy="NIco"))
    assert card(crew, "J3").custom_fields["workiz_tech"] == ["NIco"]

    plan = imp(crew, tmp_path, jobs(ada="NIco, Antonio Brown", cy=""))
    assert plan.tech_names_changed == ["J1"] and plan.tech_names_cleared == ["J3"]
    assert card(crew, "J1").custom_fields["workiz_tech"] == ["NIco", "Antonio Brown"]
    assert "workiz_tech" not in card(crew, "J3").custom_fields
    # The rest of the reserved blob is untouched by the clearing.
    assert card(crew, "J3").custom_fields["workiz_id"] == "J3"


# ------------------------------------------------------------------ mapping


def test_a_name_maps_to_the_one_active_user_with_that_full_name(crew, tmp_path):
    imp(crew, tmp_path, jobs(ada="antonio   BROWN", ben="Owen Buzaglo"))
    assert visit(crew, "J1").assigned_user_id == crew.antonio.id
    # Case-insensitive and whitespace-collapsed on the USER's side too.
    assert visit(crew, "J2").assigned_user_id == crew.owen.id


def test_the_map_file_beats_the_name_match(crew, tmp_path):
    plan = imp(crew, tmp_path, jobs(ada="Antonio Brown", ben="NIco"),
               tech_map={"antonio brown": "NICO@dtr.test", "NIco": "antonio@dtr.test"})
    assert visit(crew, "J1").assigned_user_id == crew.nico.id
    assert visit(crew, "J2").assigned_user_id == crew.antonio.id
    how = {m.name: m.how for m in plan.tech_mappings}
    assert how == {"Antonio Brown": "map file", "NIco": "map file"}


def test_a_null_in_the_map_file_leaves_a_matching_name_unmapped(crew, tmp_path):
    plan = imp(crew, tmp_path, jobs(ada="Antonio Brown"),
               tech_map={"Antonio Brown": None})
    assert visit(crew, "J1").assigned_user_id is None
    assert plan.tech_mappings[0].how == "UNMAPPED"


def test_an_inactive_user_is_never_matched_by_name_or_by_the_map(crew, tmp_path):
    plan = imp(crew, tmp_path, jobs(ada="Shay", ben="Nicolas Perez"),
               tech_map={"Nicolas Perez": "shay@dtr.test"})
    assert visit(crew, "J1").assigned_user_id is None
    assert visit(crew, "J2").assigned_user_id is None
    why = {m.name: m.why for m in plan.tech_mappings}
    assert "no active user" in why["Shay"]
    assert "DEACTIVATED" in why["Nicolas Perez"]


def test_a_machine_account_is_never_matched_by_name(crew, tmp_path):
    person(crew, "Telephony Feed", "feed@dtr.test", password="")
    crew.commit()
    imp(crew, tmp_path, jobs(ada="Telephony Feed"))
    assert visit(crew, "J1").assigned_user_id is None


def test_two_active_users_with_the_name_are_ambiguous_not_guessed(crew, tmp_path):
    person(crew, "Antonio Brown", "antonio2@dtr.test")
    crew.commit()
    plan = imp(crew, tmp_path, jobs(ada="Antonio Brown"))
    assert visit(crew, "J1").assigned_user_id is None
    assert "2 active users" in plan.tech_mappings[0].why


def test_an_unmapped_name_is_reported_and_assigns_nothing_and_creates_no_user(
        crew, tmp_path):
    users_before = crew.scalar(select(func.count(User.id)))
    plan = imp(crew, tmp_path, jobs(ada="Zuf Graziani Installer"))
    assert visit(crew, "J1").assigned_user_id is None
    assert crew.scalar(select(func.count(User.id))) == users_before
    text = wi.render(plan, None, committed=False)
    assert "Zuf Graziani Installer" in text and "UNMAPPED" in text


def test_the_first_mapped_technician_in_job_order_wins(crew, tmp_path):
    imp(crew, tmp_path, jobs(ada="NIco, Nicolas Perez, Antonio Brown"))
    # "NIco" maps to nobody, so the next name in the export's order is the one.
    assert visit(crew, "J1").assigned_user_id == crew.nico.id


# ------------------------------------------------------------------ appointments


def test_only_a_future_appointment_is_assigned(crew, tmp_path):
    imp(crew, tmp_path, jobs(ada="Antonio Brown", cy="Antonio Brown"))
    # J3 is in the past: still no appointment at all, and its names are on the card.
    assert visit(crew, "J3") is None
    assert card(crew, "J3").custom_fields["workiz_tech"] == ["Antonio Brown"]
    assert visit(crew, "J1").assigned_user_id == crew.antonio.id
    assert crew.scalar(select(func.count(Appointment.id))) == 2  # J1 and J2 only
    assert crew.scalar(select(func.count(Job.id))) == 0


def test_the_appointment_stays_on_the_workiz_calendar(crew, tmp_path):
    imp(crew, tmp_path, jobs(ada="Antonio Brown"))
    cal = crew.get(Calendar, visit(crew, "J1").calendar_id)
    assert cal.name == wi.IMPORT_CALENDAR and cal.user_id is None


def test_a_re_import_follows_a_changed_technician_it_had_assigned(crew, tmp_path):
    imp(crew, tmp_path, jobs(ada="Antonio Brown"))
    plan = imp(crew, tmp_path, jobs(ada="Nicolas Perez"))
    assert action_of(plan, "J1") == "reassign"
    assert visit(crew, "J1").assigned_user_id == crew.nico.id
    assert card(crew, "J1").custom_fields["workiz_tech_assigned_user_id"] == crew.nico.id


def test_removing_the_tech_clears_an_assignment_the_importer_made(crew, tmp_path):
    imp(crew, tmp_path, jobs(ada="Antonio Brown"))
    plan = imp(crew, tmp_path, jobs(ada=""))
    assert action_of(plan, "J1") == "clear"
    assert visit(crew, "J1").assigned_user_id is None
    assert "workiz_tech_assigned_user_id" not in card(crew, "J1").custom_fields


def test_a_tech_who_stops_mapping_also_clears_only_the_importers_assignment(
        crew, tmp_path):
    imp(crew, tmp_path, jobs(ada="Antonio Brown"))
    crew.get(User, crew.antonio.id).is_active = False
    crew.commit()
    plan = imp(crew, tmp_path, jobs(ada="Antonio Brown"))
    assert action_of(plan, "J1") == "clear"
    assert visit(crew, "J1").assigned_user_id is None


def test_removing_the_tech_never_clears_a_persons_assignment(crew, tmp_path):
    imp(crew, tmp_path, jobs(ben=""))          # visit exists, nobody assigned
    visit(crew, "J2").assigned_user_id = crew.owen.id   # a dispatcher assigns Owen
    crew.commit()
    imp(crew, tmp_path, jobs(ben="Antonio Brown"))
    assert visit(crew, "J2").assigned_user_id == crew.owen.id
    plan = imp(crew, tmp_path, jobs(ben=""))
    assert action_of(plan, "J2") == "none"
    assert visit(crew, "J2").assigned_user_id == crew.owen.id


def test_a_manual_assignment_is_never_overwritten_and_is_reported(crew, tmp_path):
    imp(crew, tmp_path, jobs(ada="Antonio Brown"))
    # Somebody moves the visit to Nico by hand, through the API's own path.
    visit(crew, "J1").assigned_user_id = crew.nico.id
    crew.commit()

    plan = imp(crew, tmp_path, jobs(ada="Owen Buzaglo"))
    assert action_of(plan, "J1") == "manual"
    assert visit(crew, "J1").assigned_user_id == crew.nico.id
    text = wi.render(plan, None, committed=True)
    section = text.split("Technicians (", 1)[1].split("Skipped rows", 1)[0]
    manual = section.split("LEFT ALONE", 1)[1].splitlines()[1]
    assert "J1" in manual

    # ...and a removal of the tech does not clear it either.
    plan = imp(crew, tmp_path, jobs(ada=""))
    assert action_of(plan, "J1") == "manual"
    assert visit(crew, "J1").assigned_user_id == crew.nico.id


def test_a_person_clearing_the_importers_assignment_is_respected(crew, tmp_path):
    imp(crew, tmp_path, jobs(ada="Antonio Brown"))
    visit(crew, "J1").assigned_user_id = None
    crew.commit()
    plan = imp(crew, tmp_path, jobs(ada="Nicolas Perez"))
    assert action_of(plan, "J1") == "manual"
    assert visit(crew, "J1").assigned_user_id is None


def test_a_person_assigning_the_same_tech_is_left_as_theirs(crew, tmp_path):
    imp(crew, tmp_path, jobs(ada=""))
    visit(crew, "J1").assigned_user_id = crew.antonio.id
    crew.commit()
    plan = imp(crew, tmp_path, jobs(ada="Antonio Brown"))
    assert action_of(plan, "J1") == "already"
    assert "workiz_tech_assigned_user_id" not in card(crew, "J1").custom_fields
    # It is not the importer's, so a later removal in Workiz does not clear it.
    imp(crew, tmp_path, jobs(ada=""))
    assert visit(crew, "J1").assigned_user_id == crew.antonio.id


def test_a_future_visit_that_has_become_past_is_no_longer_touched(crew, tmp_path):
    imp(crew, tmp_path, jobs(ada="Antonio Brown"))
    imp(crew, tmp_path, jobs(ada="Nicolas Perez", ada_future=False))
    assert visit(crew, "J1").assigned_user_id == crew.antonio.id


@pytest.mark.parametrize("current,marker,target,expected", [
    (None, None, 7, "assign"),
    (7, 7, 8, "reassign"),
    (7, 7, None, "clear"),
    (7, 7, 7, "unchanged"),
    (7, None, 7, "already"),
    (8, None, 7, "manual"),
    (8, 7, 7, "manual"),
    (None, 7, 8, "manual"),
    (8, 7, None, "manual"),
    (8, None, None, "none"),
    (None, None, None, "none"),
])
def test_the_assignment_rule_table(current, marker, target, expected):
    assert wi.tech_assignment(current, marker, target) == expected


# ------------------------------------------------------------------ owners, access


def test_the_card_owner_never_changes(crew, tmp_path):
    imp(crew, tmp_path, jobs(ada="", ben="Antonio Brown"))
    card(crew, "J1").owner_id = crew.owen.id
    crew.commit()
    imp(crew, tmp_path, jobs(ada="Antonio Brown", ben="Nicolas Perez"))
    assert card(crew, "J1").owner_id == crew.owen.id
    assert card(crew, "J2").owner_id is None


def test_a_restricted_tech_sees_the_assigned_future_job_and_not_an_unassigned_one(
        crew, tmp_path):
    tess = person(crew, "Tess Tech", "tess@dtr.test", only_assigned=True)
    crew.commit()
    imp(crew, tmp_path, jobs(ada="Tess Tech", ben="Antonio Brown", cy="Tess Tech"))
    plain, token = mint_api_token(crew.get(User, tess.id), name="test")
    crew.add(token)
    ids = {j: card(crew, j).id for j in ("J1", "J2", "J3")}
    crew.commit()
    crew.close()
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + plain
        board = {o["id"] for o in c.get("/api/opportunities").json()}
        # J1: her future visit. J2: Antonio's. J3: her name, but in the past, so no
        # appointment — the access rule has nothing to see it by, which is the brief.
        assert board == {ids["J1"]}
        assert c.get("/api/opportunities/%d" % ids["J1"]).status_code == 200
        assert c.get("/api/opportunities/%d" % ids["J2"]).status_code == 404
        assert c.get("/api/opportunities/%d" % ids["J3"]).status_code == 404
        # She reads her technicians on the card like anyone who can see it.
        assert c.get("/api/opportunities/%d" % ids["J1"]).json()[
            "custom_fields"]["workiz_tech"] == ["Tess Tech"]


def test_workiz_tech_is_read_only_through_the_api(crew, tmp_path):
    imp(crew, tmp_path, jobs(ada="Antonio Brown"))
    admin = crew.get(User, crew.owen.id)
    plain, token = mint_api_token(admin, name="test")
    crew.add(token)
    opp_id, antonio_id = card(crew, "J1").id, crew.antonio.id
    crew.commit()
    crew.close()
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + plain
        for forged in ({"workiz_tech": ["Somebody Else"]}, {"workiz_tech": None},
                       {"workiz_tech_assigned_user_id": 999}):
            r = c.patch("/api/opportunities/%d/detail" % opp_id,
                        json={"custom_fields": forged})
            assert r.status_code == 400, r.text
    with SessionLocal() as check:
        blob = check.get(Opportunity, opp_id).custom_fields
        assert blob["workiz_tech"] == ["Antonio Brown"]
        assert blob["workiz_tech_assigned_user_id"] == antonio_id


# ------------------------------------------------------------------ dry run, idempotency


def test_a_dry_run_with_a_tech_map_writes_absolutely_nothing(crew, tmp_path):
    imp(crew, tmp_path, jobs(ada="Antonio Brown"))
    crew.close()
    path = engine.url.database

    def digest():
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    before = digest()
    cp, jp = write_csvs(tmp_path, CLIENTS, jobs(ada="Nicolas Perez", ben="Owen Buzaglo"))
    tmap = tmp_path / "techs.json"
    tmap.write_text(json.dumps({"Owen Buzaglo": "antonio@dtr.test"}))
    out = io.StringIO()
    assert wi.run(cp, jp, commit=False, stream=out, tech_map_path=str(tmap)) == 0
    text = out.getvalue()
    # It described real work: one reassignment and one new assignment.
    assert "to reassign" in text and "jobs J1" in text and "jobs J2" in text
    assert digest() == before, "a dry run changed the database file"


def dump(db):
    rows = []
    for o in db.scalars(select(Opportunity).order_by(Opportunity.id)).all():
        rows.append((o.id, o.owner_id, sorted((o.custom_fields or {}).items(), key=str),
                     o.updated_at))
    for a in db.scalars(select(Appointment).order_by(Appointment.id)).all():
        rows.append((a.id, a.assigned_user_id, a.calendar_id, a.starts_at, a.status))
    return rows


def test_a_second_commit_changes_nothing(crew, tmp_path):
    rows = jobs(ada="NIco, Antonio Brown", ben="Owen Buzaglo", cy="Nicolas Perez")
    tmap = {"NIco": "nico@dtr.test"}
    imp(crew, tmp_path, rows, tech_map=tmap)
    first = dump(crew)
    plan = imp(crew, tmp_path, rows, tech_map=tmap)
    crew.expire_all()
    assert dump(crew) == first
    assert {a.tech_action for a in plan.appointments} == {"unchanged"}
    assert plan.tech_names_changed == [] and plan.tech_names_cleared == []


def test_the_command_line_takes_a_tech_map_and_refuses_a_bad_one(crew, tmp_path):
    crew.close()
    cp, jp = write_csvs(tmp_path, CLIENTS, jobs(ada="Antonio Brown"))
    bad = tmp_path / "bad.json"
    for content in ("not json", "[]", '{"Antonio Brown": 7}',
                    '{"Antonio Brown": "a@x", "antonio  brown": "b@x"}'):
        bad.write_text(content)
        assert wi.main(["--clients", cp, "--jobs", jp, "--commit",
                        "--tech-map", str(bad)]) == 4
        with SessionLocal() as check:
            assert check.scalar(select(func.count(Opportunity.id))) == 0

    good = tmp_path / "techs.json"
    good.write_text(json.dumps({"Antonio Brown": "nico@dtr.test"}))
    assert wi.main(["--clients", cp, "--jobs", jp, "--commit",
                    "--tech-map", str(good)]) == 0
    with SessionLocal() as check:
        assert visit(check, "J1").assigned_user_id == check.scalar(
            select(User.id).where(User.email == "nico@dtr.test"))


def test_the_technicians_report_carries_no_customer_data(crew, tmp_path):
    plan = imp(crew, tmp_path, jobs(ada="Antonio Brown, NIco", ben="Shay"),
               tech_map={"Ghost": "nobody@dtr.test"}, commit=False)
    text = wi.render(plan, None, committed=False)
    section = text.split("Technicians (", 1)[1].split("Skipped rows", 1)[0]
    blob = json.dumps(wi.as_json(plan, None)["technicians"])
    for private in ("Ada", "Rowe", "Ben", "Vale", "9415550", "555"):
        assert private not in section and private not in blob
    # ...and it does carry what the owner reads it for.
    assert "Antonio Brown" in section and "user id %d" % crew.antonio.id in section
    assert "'Ghost' is on no card" in section
    got = wi.as_json(plan, None)["technicians"]
    assert got["appointments"]["assign"] == ["J1"]
    assert {n["workiz_name"]: n["mapped_by"] for n in got["names"]} == {
        "Antonio Brown": "name", "NIco": "UNMAPPED", "Shay": "UNMAPPED"}


def test_an_ahs_email_card_gets_the_names_and_its_visit_the_tech(crew, tmp_path):
    """A paired AHS email card is the same job: its names and its visit follow Workiz."""
    do_import(crew, tmp_path, CLIENTS, [])
    ada = crew.scalar(select(Contact).where(Contact.first_name == "Ada"))
    lead = crew.scalar(select(Stage).join(Pipeline).where(
        Pipeline.name == wi.AHS, Stage.name == "New Lead"))
    email_card = Opportunity(title="Ada Rowe - 1234 ROOF", pipeline_id=lead.pipeline_id,
                             stage_id=lead.id, contact_id=ada.id,
                             created_by=wi.AHS_EMAIL_CREATED_BY,
                             created_at=wi.parse_dt(PAST),
                             custom_fields={"ahs_job_id": "1234"})
    crew.add(email_card)
    crew.commit()
    plan = imp(crew, tmp_path, [job("J9", "Ada Rowe", "9415550111", "Antonio Brown",
                                    source="AHS")])
    assert plan.ahs_attached == [("J9", email_card.id)]
    assert action_of(plan, "J9") == "assign"
    assert card(crew, "J9").id == email_card.id
    assert card(crew, "J9").custom_fields["workiz_tech"] == ["Antonio Brown"]
    assert visit(crew, "J9").assigned_user_id == crew.antonio.id

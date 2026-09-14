"""Every opportunity carries its job's address (2026-09-14) — behaviour, read back.

A customer with six roofs is one contact and six cards, so the contact's address
cannot say which roof a card is. What each group below protects:

  * **The migration only adds.** Stood up at the production head with real-shaped rows,
    upgraded, and every pre-existing row compared column for column.
  * **The API round-trips the four fields** on create, the detail PATCH, the board
    list and the detail read; a refusal (too long, a TECH) writes nothing.
  * **The Workiz importer puts each job's address on ITS card** — never another
    job's — and a re-run updates it. Card titles collapse Workiz's double spaces.
  * **An AHS card stores its service address** and a repeat delivery changes nothing.
  * **"Calendar default" is the card's address** when the booking is linked to a card
    that has one, else the contact's.
  * **Search finds a card by street or city**, and never one from a pipeline the
    reader cannot access.
"""
import ast
import contextlib
import pathlib
import tempfile

import pytest
from alembic import command
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Appointment,
    Contact,
    Opportunity,
    Pipeline,
    PipelinePermission,
    Role,
    Stage,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select, text
from tests import test_ahs_jobs as ahs_t
from tests import test_workiz_import as wz_t
from tests.test_migration_drift import _config

BACKEND = pathlib.Path(__file__).resolve().parent.parent
ADDRESS = ("address_street", "address_city", "address_state", "address_postal_code")


def read(fn):
    db = SessionLocal()
    try:
        return fn(db)
    finally:
        db.close()


def address_of(opp_id: int) -> tuple:
    return read(lambda db: tuple(getattr(db.get(Opportunity, opp_id), k) for k in ADDRESS))


# ============================================================== the migration

PRODUCTION_HEAD = "c8f2b6d41a93"
REVISION = "b3e9a7c51d28"


@contextlib.contextmanager
def _database_at(revision: str):
    """A throwaway SQLite migrated to `revision`. See test_migration_drift: env.py
    reads `app.db.DATABASE_URL`, so that is what is rebound, and put back."""
    import app.db as db_mod

    saved = db_mod.DATABASE_URL
    with tempfile.TemporaryDirectory() as tmp:
        url = "sqlite:///" + str(pathlib.Path(tmp) / "addr.db")
        db_mod.DATABASE_URL = url
        eng = create_engine(url)
        try:
            command.upgrade(_config(), revision)
            yield eng
        finally:
            eng.dispose()
            db_mod.DATABASE_URL = saved


def _dump(conn) -> dict:
    insp = inspect(conn)
    return {t: [dict(r._mapping)
                for r in conn.execute(text('SELECT * FROM "%s" ORDER BY 1' % t))]
            for t in insp.get_table_names()}


def _columns(conn) -> dict:
    insp = inspect(conn)
    return {t: {c["name"] for c in insp.get_columns(t)} for t in insp.get_table_names()}


def test_the_migration_only_adds_four_nullable_columns_and_keeps_every_row():
    with _database_at(PRODUCTION_HEAD) as eng:
        with eng.begin() as conn:
            # Representative production rows: a contact WITH an address, a card with
            # custom fields (owen_call_id, workiz_id), a won card, a card with no contact.
            conn.execute(text(
                "INSERT INTO contacts (id, first_name, last_name, phone, email, dnd, "
                "address_street, address_city, address_state, address_postal_code, "
                "contact_type, custom_fields, created_at, updated_at) VALUES "
                "(1, 'Helios', 'Holdings', '+19415550100', 'h@x.test', 0, "
                "'1 Palm Ave', 'Bradenton', 'FL', '34205', 'Customer', '{}', "
                "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"))
            conn.execute(text("INSERT INTO pipelines (id, name, position) "
                              "VALUES (1, 'Dream Team Roofing AHS', 0)"))
            conn.execute(text("INSERT INTO stages (id, pipeline_id, name, position) "
                              "VALUES (1, 1, 'New Lead', 0)"))
            for i, (contact, status, blob) in enumerate(
                    ((1, "open", '{"owen_call_id": "c-77", "workiz_id": "J1"}'),
                     (1, "won", '{"workiz_id": "J2"}'), (None, "open", "{}")), start=1):
                conn.execute(text(
                    "INSERT INTO opportunities (id, title, contact_id, pipeline_id, "
                    "stage_id, value_cents, status, position, custom_fields, created_at, "
                    "updated_at) VALUES (:id, :t, :c, 1, 1, :v, :s, :p, :b, "
                    "'2026-02-01 00:00:00', '2026-02-02 00:00:00')"),
                    {"id": i, "t": "Helios card %d" % i, "c": contact, "v": 1000 * i,
                     "s": status, "p": i - 1, "b": blob})
        with eng.connect() as conn:
            before_rows, before_cols = _dump(conn), _columns(conn)

        command.upgrade(_config(), "head")
        eng.dispose()
        with eng.connect() as conn:
            after_rows, after_cols = _dump(conn), _columns(conn)
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            nullable = {c["name"]: c["nullable"]
                        for c in inspect(conn).get_columns("opportunities")}

    assert version == REVISION
    # Schema: nothing but four new columns on opportunities.
    assert set(after_cols) == set(before_cols), "a table was created or dropped"
    for table, cols in before_cols.items():
        added = after_cols[table] - cols
        assert cols <= after_cols[table], "a column was dropped from %s" % table
        assert added == (set(ADDRESS) if table == "opportunities" else set()), table
    assert all(nullable[k] for k in ADDRESS)
    # Data: every row of every table unchanged; the cards gained four NULLs only.
    for table, rows in before_rows.items():
        if table in ("opportunities", "alembic_version"):
            continue
        assert after_rows[table] == rows, table
    assert len(after_rows["opportunities"]) == 3
    for old, new in zip(before_rows["opportunities"], after_rows["opportunities"],
                        strict=True):
        assert {k: v for k, v in new.items() if k not in ADDRESS} == old
        assert [new[k] for k in ADDRESS] == [None] * 4


def test_the_upgrade_is_four_add_columns_and_nothing_else():
    """The standing rule, read off the source: no ALTER, DROP, UPDATE or batch mode."""
    path = next((BACKEND / "migrations" / "versions").glob(REVISION + "_*.py"))
    tree = ast.parse(path.read_text(encoding="utf-8"))
    upgrade = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name == "upgrade")
    calls = [n for n in ast.walk(upgrade) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
             and n.func.value.id == "op"]
    assert [c.func.attr for c in calls] == ["add_column"] * 4
    assert {c.args[0].value for c in calls} == {"opportunities"}
    assigned = {n.target.id: n.value.value for n in tree.body
                if isinstance(n, ast.AnnAssign) and isinstance(n.value, ast.Constant)}
    assert assigned["revision"] == REVISION
    assert assigned["down_revision"] == PRODUCTION_HEAD


# ============================================================== the API

@pytest.fixture()
def world():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    users = {}
    for key, role in (("admin", Role.ADMIN), ("dispatcher", Role.DISPATCHER),
                      ("tech", Role.TECH), ("outsider", Role.DISPATCHER)):
        users[key] = User(email="%s@x.test" % key, name=key.title(), role=role)
        db.add(users[key])
    db.flush()
    helios = Contact(first_name="Helios", last_name="Holdings", phone="+19415550100",
                     address_street="900 Office Park Dr", address_city="Tampa",
                     address_state="FL", address_postal_code="33602")
    bare = Contact(first_name="No", last_name="Address", phone="+19415550199")
    db.add_all([helios, bare])
    main = Pipeline(name="Dream Team Roofing AHS", position=0)
    secret = Pipeline(name="Secret board", position=1)
    db.add_all([main, secret])
    db.flush()
    lead = Stage(pipeline_id=main.id, name="New Lead", position=0)
    hush = Stage(pipeline_id=secret.id, name="New Lead", position=0)
    db.add_all([lead, hush])
    db.flush()
    # Only the dispatcher may see the secret board; the outsider may not.
    db.add(PipelinePermission(pipeline_id=secret.id, user_id=users["dispatcher"].id))
    palm = Opportunity(title="Helios roof one", contact_id=helios.id, pipeline_id=main.id,
                       stage_id=lead.id, address_street="12 Palm Ave",
                       address_city="Bradenton", address_state="FL",
                       address_postal_code="34205", position=0)
    oak = Opportunity(title="Helios roof two", contact_id=helios.id, pipeline_id=main.id,
                      stage_id=lead.id, address_street="40 Oak Ln",
                      address_city="Sarasota", address_state="FL",
                      address_postal_code="34236", position=1)
    empty = Opportunity(title="Helios roof three", contact_id=helios.id,
                        pipeline_id=main.id, stage_id=lead.id, position=2)
    hidden = Opportunity(title="Quiet deal", contact_id=bare.id, pipeline_id=secret.id,
                         stage_id=hush.id, address_street="77 Palm Ave",
                         address_city="Bradenton", position=0)
    db.add_all([palm, oak, empty, hidden])
    tokens = {}
    for key, u in users.items():
        plain, tok = mint_api_token(u, name=key)
        db.add(tok)
        tokens[key] = plain
    db.commit()
    ids = {"helios": helios.id, "bare": bare.id, "main": main.id, "secret": secret.id,
           "lead": lead.id, "hush": hush.id, "palm": palm.id, "oak": oak.id,
           "empty": empty.id, "hidden": hidden.id}
    db.close()
    with TestClient(app) as c:
        c.ids = ids

        def call(method, path, who="admin", **kw):
            return c.request(method, path, headers={
                "Authorization": "Bearer " + tokens[who]}, **kw)
        c.call = call
        yield c


def test_the_four_fields_round_trip_through_create_patch_list_and_detail(world):
    ids = world.ids
    r = world.call("POST", "/api/opportunities", json={
        "title": "New roof", "pipeline_id": ids["main"], "stage_id": ids["lead"],
        "contact_id": ids["bare"], "address_street": "  5 Bay Rd ",
        "address_city": "Palmetto", "address_state": "FL", "address_postal_code": "34221"})
    assert r.status_code == 201, r.text
    new = r.json()["id"]
    assert address_of(new) == ("5 Bay Rd", "Palmetto", "FL", "34221")

    detail = world.call("GET", "/api/opportunities/%d" % new).json()
    assert [detail[k] for k in ADDRESS] == ["5 Bay Rd", "Palmetto", "FL", "34221"]

    r = world.call("PATCH", "/api/opportunities/%d/detail" % new,
                   json={"address_street": "6 Bay Rd", "address_postal_code": "   "})
    assert r.status_code == 200, r.text
    # Only what was sent moved; whitespace clears the ZIP rather than storing blanks.
    assert address_of(new) == ("6 Bay Rd", "Palmetto", "FL", None)
    assert [r.json()[k] for k in ADDRESS] == ["6 Bay Rd", "Palmetto", "FL", None]

    board = world.call("GET", "/api/opportunities",
                       params={"pipeline_id": ids["main"]}).json()
    row = next(o for o in board if o["id"] == new)
    assert [row[k] for k in ADDRESS] == ["6 Bay Rd", "Palmetto", "FL", None]
    empty = next(o for o in board if o["id"] == ids["empty"])
    assert [empty[k] for k in ADDRESS] == [None] * 4


def test_an_unrelated_patch_does_not_touch_the_address(world):
    before = address_of(world.ids["palm"])
    r = world.call("PATCH", "/api/opportunities/%d/detail" % world.ids["palm"],
                   json={"title": "Helios roof one (renamed)"})
    assert r.status_code == 200
    assert address_of(world.ids["palm"]) == before


def test_an_over_long_address_is_refused_and_writes_nothing(world):
    before = address_of(world.ids["palm"])
    r = world.call("PATCH", "/api/opportunities/%d/detail" % world.ids["palm"],
                   json={"address_street": "Main St", "address_postal_code": "1" * 21})
    assert r.status_code == 422
    assert "address_postal_code" in r.text
    assert address_of(world.ids["palm"]) == before
    n = read(lambda db: db.scalar(select(func.count(Opportunity.id))))
    r = world.call("POST", "/api/opportunities", json={
        "title": "x", "pipeline_id": world.ids["main"], "stage_id": world.ids["lead"],
        "address_city": "c" * 121})
    assert r.status_code == 422
    assert read(lambda db: db.scalar(select(func.count(Opportunity.id)))) == n


def test_a_tech_cannot_edit_a_cards_address_and_nothing_moves(world):
    before = address_of(world.ids["palm"])
    r = world.call("PATCH", "/api/opportunities/%d/detail" % world.ids["palm"], who="tech",
                   json={"address_street": "somewhere else"})
    assert r.status_code == 403
    assert address_of(world.ids["palm"]) == before
    # ...but reads it, like every other field on the card.
    got = world.call("GET", "/api/opportunities/%d" % world.ids["palm"], who="tech").json()
    assert got["address_street"] == "12 Palm Ave"


def test_a_hidden_cards_address_cannot_be_written_by_someone_who_cannot_see_it(world):
    before = address_of(world.ids["hidden"])
    r = world.call("PATCH", "/api/opportunities/%d/detail" % world.ids["hidden"],
                   who="outsider", json={"address_street": "x"})
    assert r.status_code == 404
    assert address_of(world.ids["hidden"]) == before


# ---------------------------------------------------------------- search

def _search_titles(world, q, who):
    body = world.call("GET", "/api/search", who=who, params={"q": q}).json()
    group = next(g for g in body["groups"] if g["type"] == "opportunities")
    return sorted(i["title"] for i in group["items"]), group["total"]


def test_the_palette_finds_a_card_by_its_street_and_by_its_city(world):
    assert _search_titles(world, "oak ln", "admin") == (["Helios roof two"], 1)
    assert _search_titles(world, "sarasota", "admin") == (["Helios roof two"], 1)
    # Neither the card without an address nor the contact's own address matches.
    assert _search_titles(world, "office park", "admin") == ([], 0)


def test_the_palette_never_returns_a_card_from_a_pipeline_the_user_cannot_access(world):
    assert _search_titles(world, "palm ave", "dispatcher") == (
        ["Helios roof one", "Quiet deal"], 2)
    # Same query, a user without access: the hidden card is neither listed nor counted.
    assert _search_titles(world, "palm ave", "outsider") == (["Helios roof one"], 1)
    assert _search_titles(world, "77 palm", "outsider") == ([], 0)


def test_the_board_search_matches_street_or_city_and_narrows(world):
    ids = world.ids

    def board(q, who="admin", pipeline="main"):
        return sorted(o["title"] for o in world.call(
            "GET", "/api/opportunities", who=who,
            params={"pipeline_id": ids[pipeline], "q": q}).json())

    assert board("40 oak") == ["Helios roof two"]
    assert board("bradenton") == ["Helios roof one"]
    assert board("roof") == ["Helios roof one", "Helios roof three", "Helios roof two"]
    assert board("nowhere") == []
    assert board("palm", who="dispatcher", pipeline="secret") == ["Quiet deal"]
    assert board("palm", who="outsider", pipeline="secret") == []


# ---------------------------------------------------------------- Calendar default

def _book(world, **kw):
    body = {"title": "Visit", "starts_at": "2026-10-01T14:00:00Z",
            "ends_at": "2026-10-01T15:00:00Z", "contact_id": world.ids["helios"],
            "location_kind": "calendar_default"}
    body.update(kw)
    return world.call("POST", "/api/appointments", json=body)


def test_calendar_default_is_the_cards_address_when_the_booking_is_for_a_card(world):
    r = _book(world, opportunity_id=world.ids["oak"])
    assert r.status_code == 201, r.text
    assert r.json()["location"] == "40 Oak Ln, Sarasota, FL 34236"
    stored = read(lambda db: db.get(Appointment, r.json()["id"]).location)
    assert stored == "40 Oak Ln, Sarasota, FL 34236"


def test_calendar_default_falls_back_to_the_contact_without_a_card_address(world):
    assert _book(world, opportunity_id=world.ids["empty"]).json()["location"] == (
        "900 Office Park Dr, Tampa, FL 33602")
    assert _book(world).json()["location"] == "900 Office Park Dr, Tampa, FL 33602"


def test_a_custom_location_still_wins_over_the_card(world):
    r = _book(world, opportunity_id=world.ids["oak"], location_kind="custom",
              location="Gate code 1234, 3 Side St")
    assert r.json()["location"] == "Gate code 1234, 3 Side St"


def test_rescheduling_to_calendar_default_uses_the_linked_cards_address(world):
    a = _book(world, location_kind="custom", location="somewhere").json()["id"]
    r = world.call("PATCH", "/api/appointments/%d" % a,
                   json={"opportunity_id": world.ids["palm"],
                         "location_kind": "calendar_default"})
    assert r.status_code == 200, r.text
    assert read(lambda db: db.get(Appointment, a).location) == (
        "12 Palm Ave, Bradenton, FL 34205")


def test_a_hidden_cards_address_never_reaches_a_booking_through_calendar_default(world):
    # Booked by someone who can see the secret board...
    a = world.call("POST", "/api/appointments", who="dispatcher", json={
        "title": "Visit", "starts_at": "2026-10-01T14:00:00Z",
        "ends_at": "2026-10-01T15:00:00Z", "contact_id": world.ids["helios"],
        "opportunity_id": world.ids["hidden"], "location_kind": "custom",
        "location": "typed"}).json()["id"]
    # ...then re-defaulted by someone who cannot: the contact's address, not the card's.
    r = world.call("PATCH", "/api/appointments/%d" % a, who="outsider",
                   json={"location_kind": "calendar_default"})
    assert r.status_code == 200, r.text
    assert read(lambda db: db.get(Appointment, a).location) == (
        "900 Office Park Dr, Tampa, FL 33602")


# ============================================================== Workiz

def test_a_workiz_jobs_address_lands_on_its_own_card(fresh, tmp_path):
    clients = [wz_t.client_row("1", "Helios Holdings", phone="9415550111",
                               address="900 Office Park Dr, Tampa, FL 33602")]
    jobs = [wz_t.job_row("J1", "Helios Holdings", phone="9415550111",
                         address="12 Palm Ave", city="Bradenton", state="FL",
                         zip_code="34205"),
            wz_t.job_row("J2", "Helios Holdings", phone="9415550111",
                         address="40 Oak Ln", city="Sarasota", state="FL",
                         zip_code="34236"),
            wz_t.job_row("J3", "Helios Holdings", phone="9415550111")]
    wz_t.do_import(fresh, tmp_path, clients, jobs)

    by_job = {o.custom_fields["workiz_id"]: o for o in fresh.scalars(select(Opportunity))}
    assert len(by_job) == 3
    got = {j: tuple(getattr(o, k) for k in ADDRESS) for j, o in by_job.items()}
    assert got["J1"] == ("12 Palm Ave", "Bradenton", "FL", "34205")
    assert got["J2"] == ("40 Oak Ln", "Sarasota", "FL", "34236")
    # No address on the row: empty, never a sibling job's and never the contact's.
    assert got["J3"] == (None, None, None, None)
    # One contact, and its address is still the clients file's.
    contact = fresh.scalar(select(Contact))
    assert fresh.scalar(select(func.count(Contact.id))) == 1
    assert (contact.address_street, contact.address_city) == ("900 Office Park Dr", "Tampa")


def test_a_re_import_fills_and_updates_a_cards_address(fresh, tmp_path):
    clients = [wz_t.client_row("1", "Ada Rowe", phone="9415550111")]
    # First run: the card exists with no address — what production has today.
    wz_t.do_import(fresh, tmp_path, clients, [wz_t.job_row("J1", "Ada Rowe",
                                                           phone="9415550111")])
    card = fresh.scalar(select(Opportunity))
    assert card.address_street is None

    wz_t.do_import(fresh, tmp_path, clients, [wz_t.job_row(
        "J1", "Ada Rowe", phone="9415550111", address="5 Bay Rd", city="Palmetto",
        state="FL", zip_code="34221")])
    fresh.expire_all()
    assert fresh.scalar(select(func.count(Opportunity.id))) == 1
    card = fresh.scalar(select(Opportunity))
    assert (card.address_street, card.address_city, card.address_postal_code) == (
        "5 Bay Rd", "Palmetto", "34221")

    # Corrected in Workiz: all four follow, as a unit (no stale ZIP survives).
    wz_t.do_import(fresh, tmp_path, clients, [wz_t.job_row(
        "J1", "Ada Rowe", phone="9415550111", address="7 Bay Rd", city="Ellenton")])
    fresh.expire_all()
    card = fresh.scalar(select(Opportunity))
    assert tuple(getattr(card, k) for k in ADDRESS) == ("7 Bay Rd", "Ellenton", None, None)


def test_a_blank_address_on_a_re_import_does_not_wipe_the_card(fresh, tmp_path):
    clients = [wz_t.client_row("1", "Ada Rowe", phone="9415550111")]
    wz_t.do_import(fresh, tmp_path, clients, [wz_t.job_row(
        "J1", "Ada Rowe", phone="9415550111", address="5 Bay Rd", city="Palmetto")])
    wz_t.do_import(fresh, tmp_path, clients, [wz_t.job_row("J1", "Ada Rowe",
                                                           phone="9415550111")])
    fresh.expire_all()
    assert fresh.scalar(select(Opportunity)).address_street == "5 Bay Rd"


def test_the_second_run_with_addresses_is_still_a_no_op(fresh, tmp_path):
    clients = [wz_t.client_row("1", "Ada Rowe", phone="9415550111")]
    jobs = [wz_t.job_row("J1", "Ada Rowe", phone="9415550111", address="5 Bay Rd",
                         city="Palmetto", state="FL", zip_code="34221")]
    wz_t.do_import(fresh, tmp_path, clients, jobs)
    first = wz_t.dump(fresh)
    wz_t.do_import(fresh, tmp_path, clients, jobs)
    fresh.expire_all()
    assert wz_t.dump(fresh) == first
    assert fresh.scalar(select(Opportunity)).updated_at is not None


@pytest.mark.parametrize("client", ["ANGELO  ALEXIA PURP", "  ANGELO \t ALEXIA   PURP "])
def test_card_titles_collapse_internal_whitespace(fresh, tmp_path, client):
    wz_t.do_import(fresh, tmp_path, [wz_t.client_row("1", client, phone="9415550111")],
                   [wz_t.job_row("J1", client, phone="9415550111", scheduled=wz_t.SOON,
                                 end=wz_t.SOON_END, status="Submitted")])
    assert fresh.scalar(select(Opportunity)).title == "ANGELO ALEXIA PURP"
    # The imported visit is named after the same card.
    assert fresh.scalar(select(Appointment)).title == "ANGELO ALEXIA PURP"


def test_the_report_still_carries_no_address(fresh, tmp_path):
    clients = [wz_t.client_row("1", "Ada Rowe", phone="9415550111")]
    jobs = [wz_t.job_row("J1", "Ada Rowe", phone="9415550111",
                         address="4217 Uniquestreet Blvd", city="Zzyzxville")]
    plan = wz_t.do_import(fresh, tmp_path, clients, jobs, commit=False)
    import json

    from app import workiz_import as wi
    report = wi.render(plan, None, committed=False) + json.dumps(wi.as_json(plan, None))
    assert "Uniquestreet" not in report and "Zzyzxville" not in report


@pytest.fixture()
def fresh():
    yield from wz_t.fresh.__wrapped__()


def test_an_email_card_takes_the_workiz_address_when_the_job_row_has_one(
        fresh, tmp_path):
    """A paired AHS email card: Workiz's split address replaces the email's one-string
    street (it is the better-structured record of the same job); a job row with no
    address leaves the email's alone."""
    from datetime import UTC, datetime

    from app import workiz_import as wi
    ahs = Pipeline(name=wi.AHS, position=0)
    fresh.add(ahs)
    fresh.flush()
    stage = Stage(pipeline_id=ahs.id, name="New Lead", position=0)
    fresh.add(stage)
    contact = Contact(first_name="Ada", last_name="Rowe", phone="+19415550111")
    fresh.add(contact)
    fresh.flush()
    created = datetime(2025, 8, 1, 15, 0, tzinfo=UTC)
    email_card = Opportunity(title="66450639 ROOF - Ada Rowe", contact_id=contact.id,
                             pipeline_id=ahs.id, stage_id=stage.id,
                             created_by="AHS email", created_at=created,
                             custom_fields={"ahs_job_id": "66450639"},
                             address_street="14436 SW 95TH LN MIAMI", address_state="FL",
                             address_postal_code="33186")
    fresh.add(email_card)
    fresh.commit()
    clients = [wz_t.client_row("1", "Ada Rowe", phone="9415550111")]
    when = wz_t.wz(created)
    wz_t.do_import(fresh, tmp_path, clients, [wz_t.job_row(
        "J1", "Ada Rowe", phone="9415550111", created=when, status="Submitted")])
    fresh.expire_all()
    card = fresh.get(Opportunity, email_card.id)
    assert card.custom_fields.get("workiz_id") == "J1", "the pairing itself must hold"
    assert tuple(getattr(card, k) for k in ADDRESS) == (
        "14436 SW 95TH LN MIAMI", None, "FL", "33186")

    wz_t.do_import(fresh, tmp_path, clients, [wz_t.job_row(
        "J1", "Ada Rowe", phone="9415550111", created=when, status="Submitted",
        address="14436 SW 95th Ln", city="Miami", state="FL", zip_code="33186")])
    fresh.expire_all()
    card = fresh.get(Opportunity, email_card.id)
    assert tuple(getattr(card, k) for k in ADDRESS) == (
        "14436 SW 95th Ln", "Miami", "FL", "33186")
    assert card.title == "66450639 ROOF - Ada Rowe", "the email's title is kept"


# ============================================================== AHS email cards

@pytest.fixture()
def ahs_world():
    yield from ahs_t.world.__wrapped__()


def test_an_ahs_card_stores_the_service_address(ahs_world):
    r = ahs_t.post(ahs_world, "/api/ahs-jobs", ahs_t.order(
        phone="+13055550000", email=None,
        service_address="14436 SW 95TH LN, MIAMI, FL 33186"))
    assert r.status_code == 201, r.text
    card = ahs_t.card(r.json()["opportunity"]["id"])
    assert tuple(getattr(card, k) for k in ADDRESS) == (
        "14436 SW 95TH LN", "MIAMI", "FL", "33186")


def test_an_ahs_card_for_a_known_customer_gets_the_job_address_not_the_contacts(
        ahs_world):
    existing = ahs_world.ids["existing"]
    r = ahs_t.post(ahs_world, "/api/ahs-jobs", ahs_t.order(phone="(941) 555-0123"))
    assert r.json()["contact"] == {"id": existing, "matched_by": "phone"}
    card = ahs_t.card(r.json()["opportunity"]["id"])
    # No comma between street and city: the importer's conservative split.
    assert tuple(getattr(card, k) for k in ADDRESS) == (
        "14436 SW 95TH LN MIAMI", None, "FL", "33186")
    # The matched contact is never edited — it still has no address.
    assert ahs_t.read(lambda db: db.get(Contact, existing).address_street) is None


def test_a_repeat_ahs_delivery_changes_nothing_on_the_card(ahs_world):
    first = ahs_t.post(ahs_world, "/api/ahs-jobs", ahs_t.order())
    opp = first.json()["opportunity"]["id"]
    snapshot = ahs_t.read(lambda db: {
        c.name: getattr(db.get(Opportunity, opp), c.name)
        for c in Opportunity.__table__.columns})
    counts = ahs_t.counts()

    again = ahs_t.post(ahs_world, "/api/ahs-jobs", ahs_t.order(
        service_address="1 Different St, Tampa, FL 33602", value_cents=99999))
    assert again.status_code == 200 and again.json()["outcome"] == "existing"
    assert ahs_t.read(lambda db: {
        c.name: getattr(db.get(Opportunity, opp), c.name)
        for c in Opportunity.__table__.columns}) == snapshot
    assert ahs_t.counts() == counts


def test_an_ahs_order_with_no_address_leaves_the_card_empty(ahs_world):
    r = ahs_t.post(ahs_world, "/api/ahs-jobs", ahs_t.order(service_address="  "))
    card = ahs_t.card(r.json()["opportunity"]["id"])
    assert tuple(getattr(card, k) for k in ADDRESS) == (None,) * 4

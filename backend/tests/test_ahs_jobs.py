"""AHS work orders from owen-main become cards — behaviour, read back from the database.

Every test re-reads what was written rather than trusting a status code, and every
refusal re-reads to prove nothing moved. The fixture is the real board shape: the AHS
pipeline with New Lead first, a Retail pipeline beside it, one existing customer.
"""
import pytest
from app import ahs_jobs, custom_fields
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Contact,
    Direction,
    EventType,
    Job,
    NumberThread,
    NumberThreadEvent,
    Opportunity,
    OpportunityNote,
    Pipeline,
    PipelinePermission,
    Role,
    Stage,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from tests.test_custom_fields_ui import FIELDS_TS, node, run_js

WORK_ORDER = ("Job 66450639 ROOF (Normal priority)\nCustomer: Guillermo Escala\n"
              "+13059629757 / scalas02@example.test\n"
              "Address: 14436 SW 95TH LN MIAMI, FL 33186\n"
              "Problem: Roof leak — leaking over the kitchen\n"
              "Payment: total $125, paid $0, remaining $125")


def order(**kw) -> dict:
    body = {
        "ahs_job_id": "66450639",
        "service": "ROOF",
        "customer_name": "Guillermo Escala",
        "phone": "+13059629757",
        "email": "scalas02@example.test",
        "service_address": "14436 SW 95TH LN MIAMI, FL 33186",
        "value_cents": 12500,
        "description": WORK_ORDER,
        "message_id": "<abc@dispatch.me>",
        "received_at": "2026-09-14T13:05:00+00:00",
    }
    body.update(kw)
    return body


@pytest.fixture()
def world():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    feed = User(email="owen@x.test", name="OWEN feed", role=Role.DISPATCHER)
    admin = User(email="admin@x.test", name="Owner", role=Role.ADMIN)
    tech = User(email="tech@x.test", name="Tech", role=Role.TECH)
    db.add_all([feed, admin, tech])
    db.flush()
    ahs = Pipeline(name="Dream Team Roofing AHS", position=0)
    retail = Pipeline(name="Retail", position=1)
    db.add_all([ahs, retail])
    db.flush()
    db.add_all([
        Stage(pipeline_id=ahs.id, name="New Lead", position=0),
        Stage(pipeline_id=ahs.id, name="Inspection", position=1),
        Stage(pipeline_id=retail.id, name="New Lead", position=0),
    ])
    existing = Contact(first_name="Maria", last_name="Lopez", phone="(941) 555-0123",
                       email="Maria.Lopez@Example.test", created_by="Manual")
    db.add(existing)
    tokens = {}
    for key, user, scopes in (("feed", feed, "events:write read"),
                              ("feed_only", feed, "events:write"),
                              ("admin", admin, ""), ("tech", tech, "")):
        plain, tok = mint_api_token(user, name=key, scopes=scopes)
        db.add(tok)
        tokens[key] = plain
    db.commit()
    ids = {"ahs": ahs.id, "retail": retail.id, "existing": existing.id,
           "feed_user": feed.id}
    db.close()
    with TestClient(app) as c:
        c.tokens = tokens
        c.ids = ids
        yield c


def post(c, path, body, who="feed"):
    return c.post(path, json=body, headers={"Authorization": "Bearer " + c.tokens[who]})


def read(fn):
    db = SessionLocal()
    try:
        return fn(db)
    finally:
        db.close()


def counts():
    return read(lambda db: {
        "contacts": db.scalar(select(func.count(Contact.id))),
        "cards": db.scalar(select(func.count(Opportunity.id))),
        "notes": db.scalar(select(func.count(OpportunityNote.id))),
        "jobs": db.scalar(select(func.count(Job.id))),
    })


def card(opp_id):
    return read(lambda db: db.get(Opportunity, opp_id))


# ------------------------------------------------------------------ the card

def test_a_work_order_makes_one_contact_one_open_card_in_ahs_new_lead_and_a_note(world):
    before = counts()
    r = post(world, "/api/ahs-jobs", order())
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["outcome"] == "created" and out["contact"]["matched_by"] == "created"

    def check(db):
        o = db.get(Opportunity, out["opportunity"]["id"])
        stage = db.get(Stage, o.stage_id)
        pipeline = db.get(Pipeline, o.pipeline_id)
        assert pipeline.name == "Dream Team Roofing AHS"
        assert stage.name == "New Lead" and stage.pipeline_id == pipeline.id
        assert o.status == "open"
        assert o.title == "66450639 ROOF - Guillermo Escala"
        assert o.value_cents == 12500 and isinstance(o.value_cents, int)
        assert o.source == "AHS"
        assert o.custom_fields == {"ahs_job_id": "66450639"}
        notes = db.scalars(select(OpportunityNote)
                           .where(OpportunityNote.opportunity_id == o.id)).all()
        assert [n.body for n in notes] == [WORK_ORDER]
        who = db.get(Contact, o.contact_id)
        assert (who.first_name, who.last_name) == ("Guillermo", "Escala")
        assert who.phone == "+13059629757" and who.email == "scalas02@example.test"
        assert who.created_by == "AHS email" and who.source == "AHS"
        assert who.address_street == "14436 SW 95TH LN MIAMI"
        assert (who.address_state, who.address_postal_code) == ("FL", "33186")
    read(check)
    after = counts()
    assert after["contacts"] == before["contacts"] + 1
    assert after["cards"] == before["cards"] + 1
    assert after["notes"] == before["notes"] + 1


def test_the_contract_with_the_workiz_importer(world):
    """The importer finds an email card by exactly these three facts. Do not change
    one without changing `workiz_import.py` in the same commit."""
    opp_id = post(world, "/api/ahs-jobs", order()).json()["opportunity"]["id"]
    o = card(opp_id)
    assert o.created_by == "AHS email"
    assert o.custom_fields.get("ahs_job_id") == "66450639"
    assert "workiz_id" not in o.custom_fields
    assert ahs_jobs.CREATED_BY == "AHS email" and ahs_jobs.AHS_JOB_ID == "ahs_job_id"


def test_the_value_is_integer_cents_and_a_float_is_refused_writing_nothing(world):
    before = counts()
    for bad in (125.0, 12500.5, "12500", True, -1):
        r = post(world, "/api/ahs-jobs", order(value_cents=bad))
        assert r.status_code == 422, (bad, r.text)
    assert counts() == before
    r = post(world, "/api/ahs-jobs", order(value_cents=30119962))
    assert card(r.json()["opportunity"]["id"]).value_cents == 30119962


def test_a_missing_customer_name_or_job_id_is_refused_and_writes_nothing(world):
    before = counts()
    for body in (order(customer_name="  "), order(ahs_job_id=""),
                 order(ahs_job_id="12 34"), {k: v for k, v in order().items()
                                              if k != "customer_name"}):
        assert post(world, "/api/ahs-jobs", body).status_code == 422
    assert counts() == before


# ------------------------------------------------------------------ the customer

def test_the_customer_is_matched_by_the_last_ten_digits_of_the_phone(world):
    before = counts()
    r = post(world, "/api/ahs-jobs", order(phone="+1 941-555-0123",
                                           email="someone.else@example.test",
                                           customer_name="M Lopez"))
    assert r.json()["contact"] == {"id": world.ids["existing"], "matched_by": "phone"}
    assert counts()["contacts"] == before["contacts"]
    # Matching never edits the customer it matched.
    maria = read(lambda db: db.get(Contact, world.ids["existing"]))
    assert (maria.first_name, maria.email, maria.created_by) == (
        "Maria", "Maria.Lopez@Example.test", "Manual")


def test_the_customer_is_matched_by_email_when_the_phone_matches_nobody(world):
    before = counts()
    r = post(world, "/api/ahs-jobs", order(phone="+13055550000",
                                           email="maria.lopez@example.TEST"))
    assert r.json()["contact"] == {"id": world.ids["existing"], "matched_by": "email"}
    assert counts()["contacts"] == before["contacts"]


def test_a_repeat_customers_second_job_is_a_second_card_on_the_same_contact(world):
    first = post(world, "/api/ahs-jobs", order()).json()
    second = post(world, "/api/ahs-jobs", order(ahs_job_id="68730389",
                                                service="PLUMBING")).json()
    assert second["outcome"] == "created"
    assert second["contact"] == {"id": first["contact"]["id"], "matched_by": "phone"}
    assert second["opportunity"]["id"] != first["opportunity"]["id"]
    cards = read(lambda db: db.scalars(select(Opportunity).where(
        Opportunity.contact_id == first["contact"]["id"]).order_by(Opportunity.id)).all())
    assert [o.title for o in cards] == ["66450639 ROOF - Guillermo Escala",
                                        "68730389 PLUMBING - Guillermo Escala"]
    assert [o.custom_fields["ahs_job_id"] for o in cards] == ["66450639", "68730389"]


def test_an_unknown_numbers_thread_is_adopted_by_the_contact_the_order_creates(world):
    """The number-thread rule still holds: the contact exists now, so the history
    moves onto it. Nothing is enqueued by the move."""
    def seed(db):
        t = NumberThread(phone="+13059629757", phone_key="3059629757")
        db.add(t)
        db.flush()
        db.add(NumberThreadEvent(number_thread_id=t.id, type=EventType.CALL,
                                 direction=Direction.INBOUND, body="missed call"))
        db.commit()
    read(seed)
    jobs = counts()["jobs"]
    post(world, "/api/ahs-jobs", order())
    assert read(lambda db: db.scalar(select(func.count(NumberThread.id)))) == 0
    assert counts()["jobs"] == jobs


# ------------------------------------------------------------------ idempotency

def test_the_same_email_delivered_twice_is_one_card(world):
    first = post(world, "/api/ahs-jobs", order())
    mid = counts()
    again = post(world, "/api/ahs-jobs", order(description="a changed body"))
    assert first.status_code == 201 and again.status_code == 200, again.text
    assert again.json()["outcome"] == "existing"
    assert again.json()["opportunity"]["id"] == first.json()["opportunity"]["id"]
    assert counts() == mid
    notes = read(lambda db: db.scalars(select(OpportunityNote.body)).all())
    assert notes == [WORK_ORDER]


def test_a_card_moved_on_by_staff_is_still_found_and_left_alone(world):
    opp_id = post(world, "/api/ahs-jobs", order()).json()["opportunity"]["id"]

    def move(db):
        o = db.get(Opportunity, opp_id)
        o.stage_id = db.scalar(select(Stage.id).where(Stage.name == "Inspection"))
        o.status = "won"
        db.commit()
    read(move)
    again = post(world, "/api/ahs-jobs", order())
    assert again.json()["outcome"] == "existing"
    o = card(opp_id)
    assert o.status == "won"
    assert read(lambda db: db.get(Stage, o.stage_id).name) == "Inspection"


# ------------------------------------------------------------------ no notification

def test_nothing_is_enqueued_for_a_card_a_contact_or_a_cancellation(world):
    assert counts()["jobs"] == 0
    post(world, "/api/ahs-jobs", order())
    post(world, "/api/ahs-jobs", order(ahs_job_id="1111", phone="+19415550123"))
    post(world, "/api/ahs-jobs/cancellations", {"ahs_job_id": "66450639"})
    post(world, "/api/ahs-jobs/cancellations", {"ahs_job_id": "999999"})
    assert counts()["jobs"] == 0
    types = read(lambda db: db.scalars(select(Job.type)).all())
    assert "new_lead_notify" not in types and types == []


def test_the_module_cannot_reach_the_queue_or_the_automations():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(ahs_jobs))
    imported = set()
    for stmt in ast.walk(tree):
        if isinstance(stmt, ast.ImportFrom):
            imported.update(a.name for a in stmt.names)
            imported.add(stmt.module or "")
        elif isinstance(stmt, ast.Import):
            imported.update(a.name for a in stmt.names)
    assert not imported & {"automations", "queue", "app.automations", "app.queue",
                           "enqueue"}, imported


def test_a_job_enqueued_underneath_rolls_the_whole_write_back(world, monkeypatch):
    """The jobs-table count is the guard that holds even if something below this
    module starts enqueueing on its own."""
    calls = {"n": 0}
    real_count = ahs_jobs._jobs_count

    def lying_count(db):
        calls["n"] += 1
        return real_count(db) + (1 if calls["n"] > 1 else 0)
    monkeypatch.setattr(ahs_jobs, "_jobs_count", lying_count)
    before = counts()
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post("/api/ahs-jobs", json=order(),
                   headers={"Authorization": "Bearer " + world.tokens["feed"]})
    assert r.status_code == 500
    assert counts() == before


# ------------------------------------------------------------------ cancellation

def test_a_cancellation_notes_the_card_and_leaves_it_open(world):
    opp_id = post(world, "/api/ahs-jobs", order()).json()["opportunity"]["id"]
    before = card(opp_id)
    r = post(world, "/api/ahs-jobs/cancellations",
             {"ahs_job_id": "66450639", "received_at": "2026-09-15T02:30:00+00:00"})
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == "noted" and r.json()["opportunity_id"] == opp_id
    after = card(opp_id)
    assert (after.status, after.stage_id, after.pipeline_id, after.value_cents) == (
        "open", before.stage_id, before.pipeline_id, before.value_cents)
    bodies = read(lambda db: db.scalars(select(OpportunityNote.body).where(
        OpportunityNote.opportunity_id == opp_id).order_by(OpportunityNote.id)).all())
    assert len(bodies) == 2
    # 02:30 UTC on the 15th is still the 14th in Bradenton.
    assert bodies[1].startswith("JOB CANCELLED by AHS: job 66450639 (2026-09-14)")


def test_the_same_cancellation_twice_is_one_note(world):
    opp_id = post(world, "/api/ahs-jobs", order()).json()["opportunity"]["id"]
    post(world, "/api/ahs-jobs/cancellations", {"ahs_job_id": "66450639"})
    mid = counts()
    again = post(world, "/api/ahs-jobs/cancellations", {"ahs_job_id": "66450639"})
    assert again.json()["outcome"] == "already_noted"
    assert again.json()["opportunity_id"] == opp_id
    assert counts() == mid


def test_a_cancellation_does_not_match_a_longer_job_number(world):
    post(world, "/api/ahs-jobs", order(ahs_job_id="123"))
    post(world, "/api/ahs-jobs", order(ahs_job_id="1234", phone="+19415550199",
                                       email=None))
    post(world, "/api/ahs-jobs/cancellations", {"ahs_job_id": "1234"})
    r = post(world, "/api/ahs-jobs/cancellations", {"ahs_job_id": "123"})
    assert r.json()["outcome"] == "noted"


def test_a_cancellation_for_an_unknown_job_is_a_recorded_no_op(world):
    post(world, "/api/ahs-jobs", order())
    before = counts()
    r = post(world, "/api/ahs-jobs/cancellations", {"ahs_job_id": "70396099"})
    assert r.status_code == 200
    assert r.json() == {"outcome": "no_card", "ahs_job_id": "70396099",
                        "opportunity_id": None, "note_id": None,
                        "automation": ahs_jobs.AUTOMATION}
    assert counts() == before


# ------------------------------------------------------------------ the reserved key

def test_ahs_job_id_cannot_be_edited_removed_or_forged_through_the_api(world):
    opp_id = post(world, "/api/ahs-jobs", order()).json()["opportunity"]["id"]
    admin = {"Authorization": "Bearer " + world.tokens["admin"]}
    url = "/api/opportunities/%d/detail" % opp_id
    for blob in ({"ahs_job_id": "FORGED"}, {"ahs_job_id": None}, {"ahs_job_id": ""}):
        r = world.patch(url, json={"custom_fields": blob}, headers=admin)
        assert r.status_code == 400, r.text
        assert "read-only" in r.json()["detail"]
    world.patch(url, json={"custom_fields": {}}, headers=admin)
    assert card(opp_id).custom_fields == {"ahs_job_id": "66450639"}
    # An unchanged echo is not a write.
    r = world.patch(url, json={"title": "Renamed", "custom_fields": {
        "ahs_job_id": "66450639"}}, headers=admin)
    assert r.status_code == 200, r.text
    assert card(opp_id).custom_fields == {"ahs_job_id": "66450639"}

    # Setting one on a hand-made deal, at create or on edit, is refused.
    stage = read(lambda db: db.scalar(select(Stage.id).where(
        Stage.pipeline_id == world.ids["ahs"], Stage.name == "New Lead")))
    made = world.post("/api/opportunities", headers=admin, json={
        "title": "Walk-in", "pipeline_id": world.ids["ahs"], "stage_id": stage,
        "custom_fields": {"ahs_job_id": "66450639"}})
    assert made.status_code == 400, made.text
    made = world.post("/api/opportunities", headers=admin, json={
        "title": "Walk-in", "pipeline_id": world.ids["ahs"], "stage_id": stage})
    r = world.patch("/api/opportunities/%d/detail" % made.json()["id"], headers=admin,
                    json={"custom_fields": {"ahs_job_id": "66450639"}})
    assert r.status_code == 400
    assert card(made.json()["id"]).custom_fields == {}


@pytest.mark.parametrize("label", ["ahs_job_id", "AHS job ID", "AHS Job number"])
def test_no_user_defined_field_may_claim_the_ahs_job_namespace(world, label):
    r = world.post("/api/custom-fields", json={"label": label, "field_type": "text"},
                   headers={"Authorization": "Bearer " + world.tokens["admin"]})
    assert r.status_code == 400, r.text
    assert "AHS email relay" in r.json()["detail"]


def test_the_owners_ahs_claim_number_question_is_not_swallowed_by_the_namespace(world):
    """Why the prefix is `ahs_job_`, not `ahs_`."""
    assert not custom_fields.is_reserved("ahs_claim_number")
    r = world.post("/api/custom-fields", json={
        "label": "AHS claim number", "field_type": "text",
        "pipeline_ids": [world.ids["ahs"]]},
        headers={"Authorization": "Bearer " + world.tokens["admin"]})
    assert r.status_code == 201, r.text
    assert r.json()["key"] == "ahs_claim_number"


# ------------------------------------------------------------------ the gate

def test_a_token_with_only_events_write_can_deliver(world):
    r = post(world, "/api/ahs-jobs", order(), who="feed_only")
    assert r.status_code == 201, r.text
    r = post(world, "/api/ahs-jobs/cancellations", {"ahs_job_id": "1"}, who="feed_only")
    assert r.status_code == 200, r.text


def test_a_tech_and_an_anonymous_caller_are_refused_and_write_nothing(world):
    before = counts()
    assert post(world, "/api/ahs-jobs", order(), who="tech").status_code == 403
    assert world.post("/api/ahs-jobs", json=order()).status_code == 401
    assert world.post("/api/ahs-jobs/cancellations",
                      json={"ahs_job_id": "1"}).status_code == 401
    assert counts() == before


def test_the_work_order_note_is_staff_only(world):
    opp_id = post(world, "/api/ahs-jobs", order()).json()["opportunity"]["id"]
    tech = {"Authorization": "Bearer " + world.tokens["tech"]}
    assert world.get("/api/opportunities/%d/notes" % opp_id,
                     headers=tech).status_code == 403
    admin = {"Authorization": "Bearer " + world.tokens["admin"]}
    listed = world.get("/api/opportunities/%d/notes" % opp_id, headers=admin).json()
    assert [n["body"] for n in listed["notes"]] == [WORK_ORDER]


def test_a_missing_or_hidden_board_is_refused_and_writes_nothing(world):
    def restrict(db):
        other = db.scalar(select(User).where(User.role == Role.ADMIN))
        db.add(PipelinePermission(pipeline_id=world.ids["ahs"], user_id=other.id))
        db.commit()
    read(restrict)
    before = counts()
    r = post(world, "/api/ahs-jobs", order())
    assert r.status_code == 422 and "not found" in r.json()["detail"]
    assert counts() == before

    def rename(db):
        db.execute(PipelinePermission.__table__.delete())
        db.get(Pipeline, world.ids["ahs"]).name = "Something else"
        db.commit()
    read(rename)
    r = post(world, "/api/ahs-jobs", order())
    assert r.status_code == 422
    assert counts() == before


def test_two_new_lead_columns_on_the_board_is_refused_not_guessed(world):
    def dup(db):
        db.add(Stage(pipeline_id=world.ids["ahs"], name="New Lead", position=9))
        db.commit()
    read(dup)
    before = counts()
    r = post(world, "/api/ahs-jobs", order())
    assert r.status_code == 409, r.text
    assert counts() == before


# ------------------------------------------------------------------ pieces

@pytest.mark.parametrize("raw,expected", [
    ("14436 SW 95TH LN MIAMI, FL 33186",
     {"address_street": "14436 SW 95TH LN MIAMI", "address_state": "FL",
      "address_postal_code": "33186"}),
    ("12 Palm Ave, Bradenton, FL 34205-1234",
     {"address_street": "12 Palm Ave", "address_city": "Bradenton",
      "address_state": "FL", "address_postal_code": "34205-1234"}),
    ("somewhere odd", {"address_street": "somewhere odd"}),
    (None, {}),
])
def test_the_address_split_never_drops_a_character(raw, expected):
    got = ahs_jobs.split_address(raw)
    assert got == expected
    if raw:
        kept = " ".join(got.values()).replace(",", "")
        assert all(word.strip(",") in kept for word in raw.split())


def test_the_title_matches_the_ghl_relays_name():
    body = ahs_jobs.AhsJobIn(**order(service=None))
    assert ahs_jobs.title_for(body) == "66450639 - Guillermo Escala"
    assert ahs_jobs.title_for(ahs_jobs.AhsJobIn(**order())) == \
        "66450639 ROOF - Guillermo Escala"


def test_a_naive_received_at_is_read_as_utc(world):
    post(world, "/api/ahs-jobs", order())
    r = post(world, "/api/ahs-jobs/cancellations",
             {"ahs_job_id": "66450639", "received_at": "2026-09-15T02:30:00"})
    assert r.json()["outcome"] == "noted"
    body = read(lambda db: db.get(OpportunityNote, r.json()["note_id"]).body)
    assert "(2026-09-14)" in body


# ------------------------------------------------------------------ never drawn

@node
def test_the_browser_never_draws_ahs_job_id_but_still_draws_ahs_claim_number():
    got = run_js("""
        const defs = [
          { id: 1, key: 'ahs_job_id', label: 'AHS job', field_type: 'text', options: [],
            position: 0, entity: 'opportunity', archived: false, pipeline_ids: [1] },
          { id: 2, key: 'ahs_claim_number', label: 'AHS claim number',
            field_type: 'text', options: [], position: 1, entity: 'opportunity',
            archived: false, pipeline_ids: [1] }]
        out({ reserved: ['ahs_job_id', 'ahs_claim_number'].map(f.isReserved),
              asked: f.askedOn(defs, 1).map((d) => d.key),
              sent: f.changedAnswers({ ahs_job_id: '66450639' },
                                     { ahs_job_id: 'FORGED', roof: 'Tile' }) })
    """)
    assert got["reserved"] == [True, False]
    assert got["asked"] == ["ahs_claim_number"]
    assert "ahs_job_id" not in got["sent"]


def test_the_browser_mirror_matches_the_server_namespace():
    import re as _re
    ts = FIELDS_TS.read_text(encoding="utf-8")
    listed = _re.search(r"RESERVED_PREFIXES = \[([^\]]*)\]", ts).group(1)
    assert tuple(_re.findall(r"'([^']+)'", listed)) == custom_fields.RESERVED_PREFIXES

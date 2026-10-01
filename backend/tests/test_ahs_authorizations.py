"""AHS authorized a repair: owen-main's relay becomes a Dispatch item and the bell (2026-10-01).

Behaviour, read back from the database: what was written, who heard it, and that a repeat or
a refusal wrote nothing. The amounts and numbers are INVENTED — built from the one redacted
template ("NCC $#### Net Total $#### AUTHO # ####RNCL"); no real customer is in this file.
Nothing reaches Zuper: the conftest guard fails any test that tries, and the `jobs` table is
counted around every write.
"""
from datetime import UTC, datetime, timedelta

import pytest
from app import ahs_authorizations, auth
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.dispatch import rules, service
from app.main import app
from app.models import (
    AiAlert,
    Contact,
    DispatchEvent,
    DispatchItem,
    DispatchJob,
    Job,
    Opportunity,
    Pipeline,
    PipelinePermission,
    Role,
    Stage,
    User,
    ZuperMapping,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select

PATH = "/api/ahs-jobs/authorizations"


def approval(**kw) -> dict:
    body = {"kind": "authorization", "ahs_job_id": "71234567", "autho_number": "4821",
            "autho_code": "RNCL", "net_total": "1350", "ncc": "1500",
            "dedupe_key": "ahs_auth:71234567:4821", "message_id": "<n1@dispatch.me>",
            "received_at": "2026-10-01T14:00:00+00:00"}
    body.update(kw)
    return body


def maybe(**kw) -> dict:
    body = {"kind": "authorization_possible", "ahs_job_id": "71234567",
            "dedupe_key": "ahs_auth_possible:71234567:<n2@dispatch.me>",
            "message_id": "<n2@dispatch.me>", "received_at": "2026-10-01T14:00:00+00:00"}
    body.update(kw)
    return body


@pytest.fixture()
def world():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    feed = User(email="owen@x.test", name="OWEN feed", role=Role.DISPATCHER)
    admin = User(email="admin@x.test", name="Owner", role=Role.ADMIN)
    disp = User(email="disp@x.test", name="Dispatcher", role=Role.DISPATCHER)
    hidden = User(email="hidden@x.test", name="Retail only", role=Role.DISPATCHER)
    restricted = User(email="r@x.test", name="Restricted", role=Role.DISPATCHER,
                      only_assigned_data=True)
    tech = User(email="tech@x.test", name="Tech", role=Role.TECH)
    db.add_all([feed, admin, disp, hidden, restricted, tech])
    db.flush()
    ahs = Pipeline(name="Dream Team Roofing AHS", position=0)
    db.add(ahs)
    db.flush()
    stage = Stage(pipeline_id=ahs.id, name="Work Order Received", position=0)
    db.add(stage)
    db.flush()
    # The AHS board is open to the feed and the dispatcher, so hidden from "Retail only".
    db.add_all([PipelinePermission(pipeline_id=ahs.id, user_id=feed.id),
                PipelinePermission(pipeline_id=ahs.id, user_id=disp.id)])
    customer = Contact(first_name="Jane", last_name="Doe", phone="(941) 555-0100")
    db.add(customer)
    db.flush()
    tokens = {}
    for key, user, scopes in (("feed", feed, "events:write"), ("tech", tech, ""),
                              ("admin", admin, ""), ("hidden", hidden, ""), ("disp", disp, "")):
        plain, tok = mint_api_token(user, name=key, scopes=scopes)
        db.add(tok)
        tokens[key] = plain
    db.commit()
    ids = {"ahs": ahs.id, "stage": stage.id, "contact": customer.id, "feed": feed.id,
           "admin": admin.id, "disp": disp.id, "hidden": hidden.id,
           "restricted": restricted.id, "tech": tech.id}
    db.close()
    with TestClient(app) as c:
        c.tokens = tokens
        c.ids = ids
        yield c


def post(c, body, who="feed"):
    return c.post(PATH, json=body, headers={"Authorization": "Bearer " + c.tokens[who]})


def read(fn):
    db = SessionLocal()
    try:
        return fn(db)
    finally:
        db.close()


def counts():
    return read(lambda db: {
        "items": db.scalar(select(func.count(DispatchItem.id))),
        "events": db.scalar(select(func.count(DispatchEvent.id))),
        "alerts": db.scalar(select(func.count(AiAlert.id))),
        "jobs": db.scalar(select(func.count(Job.id))),
        "cards": db.scalar(select(func.count(Opportunity.id))),
    })


def add_card(c, *, ahs_job_id="71234567", zuper_uid=None, board="AHS - Inspection",
             fields=None, number="Z-1001"):
    """An AHS email card for the job, optionally mirrored by a Zuper job the page has read."""
    def go(db):
        o = Opportunity(title="Jane Doe - %s ROOF" % ahs_job_id, contact_id=c.ids["contact"],
                        pipeline_id=c.ids["ahs"], stage_id=c.ids["stage"], status="open",
                        created_by="AHS email", custom_fields={"ahs_job_id": ahs_job_id})
        db.add(o)
        db.flush()
        if zuper_uid:
            db.add(ZuperMapping(crm_type="opportunity", crm_id=o.id, zuper_type="job",
                                zuper_uid=zuper_uid))
            db.add(DispatchJob(job_uid=zuper_uid, job_number=number, board=board,
                               status="Awaiting AHS Decision", customer_name="Jane Doe",
                               phones=["9415550100"], fields=fields))
        db.commit()
        return o.id
    return read(go)


def alerts_for():
    return read(lambda db: {(a.user_id, a.kind, a.title, a.opportunity_id, a.contact_id)
                            for a in db.scalars(select(AiAlert))})


# ------------------------------------------------------------------ an approval

def test_an_approval_on_a_known_job_is_one_item_in_book_and_one_bell(world):
    opp = add_card(world, zuper_uid="zj-1")
    before = counts()
    r = post(world, approval())
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["outcome"] == "created" and out["found"] is True
    assert out["opportunity_id"] == opp and out["job_uid"] == "zj-1"

    item = read(lambda db: db.get(DispatchItem, out["item_id"]))
    assert (item.kind, item.queue, item.state) == ("ahs_approved", "book", "open")
    assert item.key == "ahs_auth:71234567:4821"
    assert item.job_uid == "zj-1" and item.job_number == "Z-1001"
    assert item.board == "AHS - Inspection" and item.phone == "9415550100"
    assert item.title == "AHS approved #71234567: $1,350 — Jane Doe"
    assert item.todo == ("Move the job to AHS Approved, answer its questions (AHS authorized "
                         "$1,350), then book the repair.")
    assert "AUTHO #4821RNCL" in item.why and "NCC $1,500" in item.why
    assert item.evidence["autho_number"] == "4821" and item.evidence["net_total"] == "1350"
    assert item.due_at is not None and not item.urgent

    ev = read(lambda db: db.scalars(select(DispatchEvent)).all())
    assert [(e.key, e.kind, e.job_uid, e.rang) for e in ev] == [
        ("ahs_auth:71234567:4821", "ahs_approved", "zj-1", True)]
    got = alerts_for()
    title = "AHS approved #71234567: $1,350 — Jane Doe"
    contact = world.ids["contact"]
    # The Dispatch audience that can see the AHS board: the admin, the dispatcher and the feed
    # account (an unrestricted dispatcher) — never the tech, the restricted user, or the
    # dispatcher whose permissions hide the board.
    assert got == {(world.ids[u], "dispatch_ahs_approved", title, opp, contact)
                   for u in ("admin", "disp", "feed")}

    after = counts()
    assert after["jobs"] == before["jobs"], "an authorization must queue nothing"
    assert after["cards"] == before["cards"], "and never touch a card"


def test_the_same_approval_twice_is_one_item_and_one_bell(world):
    add_card(world, zuper_uid="zj-1")
    assert post(world, approval()).status_code == 201
    before = counts()
    r = post(world, approval(message_id="<another@dispatch.me>"))
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == "existing"
    assert counts() == before
    # A different AUTHO on the same job is a new approval.
    r = post(world, approval(autho_number="4822", message_id="<n3@dispatch.me>"))
    assert r.status_code == 201
    assert counts()["items"] == before["items"] + 1


def test_a_job_nobody_has_still_rings_and_keeps_the_number(world):
    before = counts()
    r = post(world, approval(ahs_job_id="79999999"))
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["found"] is False and out["opportunity_id"] is None
    item = read(lambda db: db.get(DispatchItem, out["item_id"]))
    assert item.job_uid is None and item.job_number == "79999999"
    # Not found is still an AHS job: filed on AHS - Inspection, so the board filters apply.
    assert item.board == "AHS - Inspection"
    assert item.title == "AHS approved #79999999: $1,350"
    assert "Nothing in the CRM or on the Dispatch page carries AHS job #79999999" in item.why
    got = alerts_for()
    assert {a[0] for a in got} == {world.ids[u] for u in ("admin", "disp", "feed")}
    assert all(a[3] is None for a in got)
    assert counts()["jobs"] == before["jobs"]


def dispatch_items(c, who):
    r = c.get("/api/dispatch/items", headers={"Authorization": "Bearer " + c.tokens[who]})
    assert r.status_code == 200, r.text
    return {i["id"] for i in r.json()["items"]}


@pytest.mark.parametrize("found", ["nobody", "hidden from the feed"])
def test_a_dispatcher_the_ahs_board_is_hidden_from_never_learns_of_it(world, found):
    """The AHS number and the amount must not reach someone the AHS pipeline is hidden from,
    whether the job was not found at all or found and hidden from the feed's owner."""
    if found == "hidden from the feed":
        add_card(world, zuper_uid="zj-1")
        read(lambda db: (db.query(PipelinePermission).filter(
            PipelinePermission.user_id == world.ids["feed"]).delete(), db.commit()))
        body = approval()
    else:
        body = approval(ahs_job_id="79999999")
    out = post(world, body).json()
    assert out["found"] is False
    item = read(lambda db: db.get(DispatchItem, out["item_id"]))
    assert item.board == "AHS - Inspection"
    rung = {a[0] for a in alerts_for()}
    assert world.ids["hidden"] not in rung
    assert world.ids["admin"] in rung and world.ids["disp"] in rung
    assert out["item_id"] not in dispatch_items(world, "hidden")
    assert out["item_id"] in dispatch_items(world, "admin")
    assert out["item_id"] in dispatch_items(world, "disp")


def test_a_card_with_no_zuper_job_still_opens_from_the_bell(world):
    opp = add_card(world)
    out = post(world, approval()).json()
    assert out["found"] is True and out["job_uid"] is None
    item = read(lambda db: db.get(DispatchItem, out["item_id"]))
    assert item.board == "AHS - Inspection" and item.job_number == "71234567"
    got = alerts_for()
    assert got and all(a[3] == opp and a[4] == world.ids["contact"] for a in got)
    assert world.ids["hidden"] not in {a[0] for a in got}


def test_a_zuper_job_is_found_by_its_ahs_field_without_a_card(world):
    read(lambda db: (db.add(DispatchJob(job_uid="zj-9", job_number="Z-9", board="AHS - Inspection",
                                        customer_name="Jane Doe",
                                        fields={"AHS Job ID": "71234567"})), db.commit()))
    out = post(world, approval()).json()
    assert out["job_uid"] == "zj-9"
    item = read(lambda db: db.get(DispatchItem, out["item_id"]))
    assert item.job_number == "Z-9" and item.board == "AHS - Inspection"


def test_a_zuper_number_or_another_board_never_stands_for_the_ahs_number(world):
    """Zuper's own job number is a different series: one that happens to equal the AHS number
    is not this job. Nor is a job on a non-AHS board whose fields mention the number."""
    read(lambda db: (db.add_all([
        DispatchJob(job_uid="zj-num", job_number="71234567", board="AHS - Inspection",
                    customer_name="Someone Else"),
        DispatchJob(job_uid="zj-retail", job_number="R-5", board="Retail",
                    customer_name="Someone Else", fields={"AHS Job ID": "71234567"}),
        DispatchJob(job_uid="zj-other", job_number="Z-7", board="AHS - Inspection",
                    customer_name="Someone Else", fields={"AHS Job ID": "712345678"}),
    ]), db.commit()))
    out = post(world, approval()).json()
    assert out["found"] is False and out["job_uid"] is None
    item = read(lambda db: db.get(DispatchItem, out["item_id"]))
    assert item.job_number == "71234567" and item.board == "AHS - Inspection"
    assert "Someone Else" not in item.title


def test_a_zuper_job_on_the_repair_board_is_found_by_its_ahs_field(world):
    read(lambda db: (db.add(DispatchJob(job_uid="zj-r", job_number="Z-12",
                                        board="AHS - Repair & Review", customer_name="Jane Doe",
                                        fields={"Other": "x", "AHS Job #": " 71234567 "})),
                     db.commit()))
    out = post(world, approval()).json()
    assert out["job_uid"] == "zj-r"
    item = read(lambda db: db.get(DispatchItem, out["item_id"]))
    assert item.board == "AHS - Repair & Review"


def test_a_card_hidden_from_the_feed_is_not_found(world):
    add_card(world, zuper_uid="zj-1")
    read(lambda db: (db.query(PipelinePermission).filter(
        PipelinePermission.user_id == world.ids["feed"]).delete(), db.commit()))
    out = post(world, approval()).json()
    assert out["found"] is False and out["opportunity_id"] is None


# ------------------------------------------------------------------ "items updated"

def test_items_updated_is_an_item_and_no_bell(world):
    add_card(world, zuper_uid="zj-1")
    r = post(world, maybe())
    assert r.status_code == 201, r.text
    item = read(lambda db: db.get(DispatchItem, r.json()["item_id"]))
    assert (item.kind, item.queue) == ("ahs_items_updated", "book")
    assert item.title == "AHS may have updated #71234567 — check the portal (Jane Doe)"
    assert counts()["alerts"] == 0 and counts()["events"] == 0
    assert post(world, maybe()).json()["outcome"] == "existing"
    assert post(world, maybe(message_id="<n9@dispatch.me>")).status_code == 201
    assert counts()["items"] == 2


# ------------------------------------------------------------------ refusals

@pytest.mark.parametrize("body", [
    approval(autho_number=None),
    approval(autho_number="12 34"),
    approval(net_total="abc"),
    approval(ncc="-5"),
    approval(ahs_job_id="not a job!"),
    approval(kind="approved"),
    approval(autho_code="R1"),
    approval(net_total=None, ncc=None),
    approval(net_total="", ncc=" "),
])
def test_bad_input_is_a_422_and_writes_nothing(world, body):
    before = counts()
    r = post(world, body)
    assert r.status_code == 422, r.text
    assert counts() == before


def test_an_approval_with_only_the_ncc_names_that_amount(world):
    out = post(world, approval(net_total=None, ncc="1500")).json()
    item = read(lambda db: db.get(DispatchItem, out["item_id"]))
    assert item.title == "AHS approved #71234567"
    assert "(AHS authorized $1,500)" in item.todo and "$)" not in item.todo


def test_who_may_post(world):
    before = counts()
    assert world.post(PATH, json=approval()).status_code == 401
    assert post(world, approval(), who="tech").status_code == 403
    assert counts() == before
    assert "/api/ahs-jobs/authorizations" in auth.EVENTS_WRITE_PATHS
    assert post(world, approval(), who="admin").status_code == 201


def test_amounts_are_kept_as_written_and_shown_as_dollars():
    assert ahs_authorizations._money("$1,350.50") == "1350.50"
    assert ahs_authorizations.dollars("1350") == "$1,350"
    assert ahs_authorizations.dollars("1350.5") == "$1,350.50"
    assert ahs_authorizations.dollars(None) is None


# ------------------------------------------------------------------ the Dispatch pass

def test_a_dispatch_pass_never_resolves_a_fed_item(world):
    out = post(world, approval()).json()
    assert {"ahs_approved", "ahs_items_updated"} == set(rules.FED_KINDS)

    def run_pass(now):
        db = SessionLocal()
        try:
            counts_ = service.store_items(db, [], now)
            db.commit()
            return counts_
        finally:
            db.close()

    item = read(lambda db: db.get(DispatchItem, out["item_id"]))
    assert run_pass(datetime(2026, 10, 1, 15, tzinfo=UTC))["resolved"] == 0
    item = read(lambda db: db.get(DispatchItem, out["item_id"]))
    assert item.state == "open" and not item.urgent
    run_pass(item.due_at.replace(tzinfo=UTC) + timedelta(minutes=1))
    item = read(lambda db: db.get(DispatchItem, out["item_id"]))
    assert item.state == "open" and item.urgent, "falls due -> urgent, still open"

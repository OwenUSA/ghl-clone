"""Bulk actions on the Opportunities board.

A bulk action is the single-record write applied to many records, and the two ways
it goes wrong are both invisible in a status code:

  * it moves more (or fewer) than the cards that were ticked;
  * it takes a shortcut past the automation, so texting the customer quietly stops
    working the moment anyone selects two cards instead of dragging one.

The second is the one this file exists for. Rule 4 texts the customer on a stage
change (`automations.on_opportunity_stage_changed`), and it has to fire once per
opportunity that ACTUALLY changed stage — not once per request, and not at all for
a card that was already sitting in the destination. "Your job is now at Inspection"
arriving because someone included the card in a rectangle selection is a message
the customer should never have received.

There is deliberately NO bulk delete, and that absence is asserted too:
`custom_fields.owen_call_id` is the telephony project's join key (DECISIONS.md).
"""
import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Contact, Job, Opportunity, Pipeline, Role, Stage, User
from fastapi.testclient import TestClient
from sqlalchemy import select


@pytest.fixture()
def client():
    """Two stages in one pipeline, plus a whole second pipeline to move nothing to.

      New Lead   A(100) B(200) C(300)   — each on a contact who can be texted
      Inspection D(400)                 — already in the destination
      Elsewhere  X(999)                 — a different pipeline entirely

    `B` is on a DND contact, so a bulk move must fire rule 4 for it and have the
    rule suppress the send — a suppression is not the same as never trying.
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    admin = User(email="a@x.test", name="Owen", role=Role.ADMIN)
    tech = User(email="t@x.test", name="Tech", role=Role.TECH)
    dispatcher = User(email="d@x.test", name="Dispatch", role=Role.DISPATCHER)
    db.add_all([admin, tech, dispatcher])
    db.flush()

    reachable = Contact(first_name="Reach", last_name="Able", phone="(941) 555-0001")
    quiet = Contact(first_name="Do", last_name="NotDisturb",
                    phone="(941) 555-0002", dnd=True)
    db.add_all([reachable, quiet])
    db.flush()

    pipe = Pipeline(name="AHS")
    other = Pipeline(name="Retail")
    db.add_all([pipe, other])
    db.flush()

    new_lead = Stage(pipeline_id=pipe.id, name="New Lead", position=0)
    inspection = Stage(pipeline_id=pipe.id, name="Inspection", position=1)
    elsewhere = Stage(pipeline_id=other.id, name="Only stage", position=0)
    db.add_all([new_lead, inspection, elsewhere])
    db.flush()

    opps = {}
    for key, stage, contact, cents, pos in (
            ("A", new_lead, reachable, 10000, 0),
            ("B", new_lead, quiet, 20000, 1),
            ("C", new_lead, reachable, 30000, 2),
            ("D", inspection, reachable, 40000, 0),
            ("X", elsewhere, reachable, 99900, 0)):
        o = Opportunity(title="OPP " + key, contact_id=contact.id,
                        pipeline_id=stage.pipeline_id, stage_id=stage.id,
                        value_cents=cents, position=pos, owner_id=admin.id)
        db.add(o)
        db.flush()
        opps[key] = o.id

    tokens = {}
    for key, u in (("admin", admin), ("tech", tech), ("dispatcher", dispatcher)):
        plain, token = mint_api_token(u, name="test-" + key)
        db.add(token)
        tokens[key] = plain
    db.commit()

    ids = {"pipeline": pipe.id, "other": other.id, "new_lead": new_lead.id,
           "inspection": inspection.id, "elsewhere": elsewhere.id,
           "opps": opps, "admin": admin.id, "tech": tech.id,
           "dispatcher": dispatcher.id}
    db.close()

    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + tokens["admin"]
        c.ids = ids
        c.tokens = tokens
        yield c


def _rows() -> dict[str, tuple[int, int | None]]:
    """Every opportunity as `title -> (stage_id, owner_id)`, read straight from the
    database rather than through the API being tested."""
    db = SessionLocal()
    try:
        return {o.title: (o.stage_id, o.owner_id)
                for o in db.scalars(select(Opportunity)).all()}
    finally:
        db.close()


def _stage_jobs() -> list[dict]:
    db = SessionLocal()
    try:
        return [j.payload for j in db.scalars(select(Job)).all()
                if j.type == "stage_change_notify"]
    finally:
        db.close()


def _as(client, role: str) -> TestClient:
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer " + client.tokens[role]
    return c


# ---------------- moving a selection ----------------

def test_a_bulk_move_moves_exactly_the_selection_and_nothing_else(client):
    o = client.ids["opps"]
    before = _rows()
    r = client.post("/api/opportunities/bulk/stage", json={
        "ids": [o["A"], o["C"]], "stage_id": client.ids["inspection"]})
    assert r.status_code == 200, r.text
    assert sorted(r.json()["moved"]) == sorted([o["A"], o["C"]])

    after = _rows()
    assert after["OPP A"][0] == client.ids["inspection"]
    assert after["OPP C"][0] == client.ids["inspection"]
    # B was never ticked, D was already there, X is in another pipeline entirely.
    for untouched in ("OPP B", "OPP D", "OPP X"):
        assert after[untouched] == before[untouched], untouched


def test_a_bulk_move_notifies_once_per_opportunity_that_actually_moved(client):
    """Rule 4, per record. Not once per request, and not for a no-op."""
    o = client.ids["opps"]
    assert _stage_jobs() == []
    r = client.post("/api/opportunities/bulk/stage", json={
        # A and C move; D is ALREADY in Inspection and must not be announced.
        "ids": [o["A"], o["C"], o["D"]], "stage_id": client.ids["inspection"]})
    body = r.json()
    assert sorted(body["moved"]) == sorted([o["A"], o["C"]])
    assert body["unchanged"] == [o["D"]]

    queued = {p["opportunity_id"] for p in _stage_jobs()}
    assert queued == {o["A"], o["C"]}, (
        "a customer was told about a stage change that did not happen")
    assert str(o["D"]) not in body["automation"], (
        "rule 4 ran for a card that never left its stage")


def test_a_bulk_move_reports_a_suppressed_send_rather_than_skipping_the_rule(client):
    """B's contact is on DND. The rule must run and suppress, so the response says
    so — silently omitting it is indistinguishable from the rule not firing."""
    o = client.ids["opps"]
    r = client.post("/api/opportunities/bulk/stage", json={
        "ids": [o["A"], o["B"]], "stage_id": client.ids["inspection"]})
    automation = r.json()["automation"]
    assert automation[str(o["A"])] == "queued"
    assert automation[str(o["B"])].startswith("suppressed:")
    assert {p["opportunity_id"] for p in _stage_jobs()} == {o["A"]}


def test_a_bulk_move_of_one_matches_a_single_move(client):
    """The bulk path must not be a second, subtly different implementation."""
    o = client.ids["opps"]
    single = client.patch("/api/opportunities/%d" % o["A"],
                          json={"stage_id": client.ids["inspection"]})
    assert single.json()["automation"] == "queued"

    bulk = client.post("/api/opportunities/bulk/stage", json={
        "ids": [o["C"]], "stage_id": client.ids["inspection"]})
    assert bulk.json()["automation"][str(o["C"])] == "queued"
    assert _rows()["OPP C"][0] == _rows()["OPP A"][0] == client.ids["inspection"]


def test_the_destination_keeps_a_stable_order(client):
    """Everyone who was already in the stage stays ahead of the arrivals, and no
    two cards end up claiming the same position."""
    o = client.ids["opps"]
    client.post("/api/opportunities/bulk/stage", json={
        "ids": [o["A"], o["C"]], "stage_id": client.ids["inspection"]})
    db = SessionLocal()
    try:
        rows = db.scalars(
            select(Opportunity)
            .where(Opportunity.stage_id == client.ids["inspection"])
            .order_by(Opportunity.position)).all()
    finally:
        db.close()
    assert [r.title for r in rows] == ["OPP D", "OPP A", "OPP C"]
    assert [r.position for r in rows] == [0, 1, 2]


def test_a_repeated_id_is_applied_once(client):
    o = client.ids["opps"]
    r = client.post("/api/opportunities/bulk/stage", json={
        "ids": [o["A"], o["A"], o["A"]], "stage_id": client.ids["inspection"]})
    assert r.json()["moved"] == [o["A"]]
    assert len(_stage_jobs()) == 1


# ---------------- a refusal mutates nothing ----------------

@pytest.mark.parametrize("why,payload", [
    ("an id nobody owns", {"ids": ["A", 999999], "stage_id": "inspection"}),
    ("a stage in another pipeline", {"ids": ["A", "C"], "stage_id": "elsewhere"}),
    ("a stage that does not exist", {"ids": ["A"], "stage_id": 999999}),
    ("an opportunity from another pipeline", {"ids": ["A", "X"],
                                              "stage_id": "inspection"}),
    ("an empty selection", {"ids": [], "stage_id": "inspection"}),
])
def test_a_refused_bulk_move_moves_nothing_and_notifies_nobody(client, why, payload):
    o = client.ids["opps"]
    ids = [o.get(i, i) if isinstance(i, str) else i for i in payload["ids"]]
    stage = payload["stage_id"]
    stage_id = client.ids[stage] if isinstance(stage, str) else stage

    before = _rows()
    r = client.post("/api/opportunities/bulk/stage",
                    json={"ids": ids, "stage_id": stage_id})
    assert r.status_code in (400, 404, 422), why
    assert _rows() == before, "a refused bulk move still moved a card: " + why
    assert _stage_jobs() == [], "a refused bulk move still texted a customer: " + why


def test_a_partly_valid_selection_is_refused_whole(client):
    """Applying the half that resolved would leave the user unable to tell which
    half landed, and re-running it would not be safe."""
    o = client.ids["opps"]
    before = _rows()
    r = client.post("/api/opportunities/bulk/stage", json={
        "ids": [o["A"], 999999, o["C"]], "stage_id": client.ids["inspection"]})
    assert r.status_code == 404
    assert "999999" in r.json()["detail"]
    assert _rows() == before


# ---------------- assigning an owner ----------------

def test_a_bulk_assign_sets_exactly_the_selection(client):
    o = client.ids["opps"]
    before = _rows()
    r = client.post("/api/opportunities/bulk/owner", json={
        "ids": [o["A"], o["B"]], "owner_id": client.ids["dispatcher"]})
    assert r.status_code == 200, r.text
    after = _rows()
    assert after["OPP A"][1] == after["OPP B"][1] == client.ids["dispatcher"]
    for untouched in ("OPP C", "OPP D", "OPP X"):
        assert after[untouched] == before[untouched]
    # ...and reassigning is not a stage change, so nobody is texted.
    assert _stage_jobs() == []


def test_a_bulk_assign_can_unassign(client):
    o = client.ids["opps"]
    r = client.post("/api/opportunities/bulk/owner",
                    json={"ids": [o["A"]], "owner_id": None})
    assert r.status_code == 200
    assert _rows()["OPP A"][1] is None


def test_an_unknown_owner_assigns_nobody(client):
    o = client.ids["opps"]
    before = _rows()
    r = client.post("/api/opportunities/bulk/owner",
                    json={"ids": [o["A"], o["C"]], "owner_id": 999999})
    assert r.status_code == 400
    assert _rows() == before


# ---------------- roles ----------------

def test_a_tech_can_bulk_move_because_a_tech_can_drag(client):
    """CLAUDE.md: a TECH may move an opportunity between stages. Selecting five
    cards must not need a right that dragging one of them does not."""
    o = client.ids["opps"]
    r = _as(client, "tech").post("/api/opportunities/bulk/stage", json={
        "ids": [o["A"]], "stage_id": client.ids["inspection"]})
    assert r.status_code == 200, r.text
    assert _rows()["OPP A"][0] == client.ids["inspection"]


def test_a_tech_cannot_bulk_assign_an_owner_and_writes_no_row(client):
    """Changing an owner is editing the record, which a TECH cannot do — the same
    gate as PATCH /api/opportunities/{id}/detail."""
    o = client.ids["opps"]
    before = _rows()
    r = _as(client, "tech").post("/api/opportunities/bulk/owner", json={
        "ids": [o["A"], o["C"]], "owner_id": client.ids["dispatcher"]})
    assert r.status_code == 403
    assert _rows() == before, "a forbidden bulk assign still wrote a row"


# ---------------- the action that is deliberately absent ----------------

def test_there_is_no_bulk_delete(client):
    """Not an oversight. `custom_fields.owen_call_id` is the telephony project's
    join key, opportunities are never cascade-deleted from a contact for exactly
    that reason, and a checkbox column is the fastest way to lose real records.

    Asserted against the route table so that adding one has to be a decision
    someone takes on purpose, and deletes this test to do it.
    """
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/opportunities/bulk/stage" in paths
    assert "/api/opportunities/bulk/owner" in paths
    assert not [p for p in paths if "bulk" in p and "delete" in p]

    o = client.ids["opps"]
    before = _rows()
    for method, path in (("POST", "/api/opportunities/bulk/delete"),
                         # This one collides with DELETE /api/opportunities/{id},
                         # so it is refused as a bad path parameter (422) rather
                         # than as a missing route. Either way it deletes nothing.
                         ("DELETE", "/api/opportunities/bulk")):
        r = client.request(method, path, json={"ids": [o["A"]]})
        assert r.status_code in (404, 405, 422), path
    assert _rows() == before

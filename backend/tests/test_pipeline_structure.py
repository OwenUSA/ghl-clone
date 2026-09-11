"""Editing the pipeline itself — create, rename, reorder, delete.

This is the most dangerous surface in the app, and it is built from zero: before
2026-09-10 there was no POST, PATCH or DELETE for a pipeline or a stage at all.
It edits the structure that real customer deals are filed in, on a board four
people use every day.

Three things must be true, and every test here is one of them:

  1. **Deleting only ever removes something EMPTY.** A stage holding an
     opportunity cannot be deleted and the refusal names how many are in the way.
     There is no `force` at any role. `Pipeline.stages` cascades delete-orphan, so
     a pipeline delete that ran with stages present would take the columns — and
     every deal in them — with it, and `custom_fields.owen_call_id` is the
     telephony project's join key.
  2. **Renaming and reordering move labels and columns, never deals.** After
     either, every opportunity is in the stage it started in. The assertions read
     the `opportunity_id -> stage_id` map before and after and compare the whole
     thing, not a sample.
  3. **A refusal writes nothing.** Not "returns 403" — the structure is re-read
     from the database and compared.

And one that is easy to "fix" by accident: **stage names are not unique**. The
measured pipeline has two distinct stages both called "Call Back", the `ghl` CLI
exits 5 rather than guess between them, and a uniqueness rule added here would
break that quietly.
"""
import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Calendar,
    Contact,
    Opportunity,
    Pipeline,
    Role,
    SavedView,
    Stage,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import select

# The measured shape, trimmed: two distinct stages share a name on purpose.
STAGE_NAMES = ["New Lead", "Inspection", "Call Back", "Call Back", "Submit Invoices"]


@pytest.fixture()
def client():
    """One populated pipeline and one empty one.

      AHS      New Lead(2 deals) · Inspection(1) · Call Back(0) · Call Back(1)
               · Submit Invoices(0)
      Retail   no stages at all — the only thing that can be deleted outright
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    admin = User(email="a@x.test", name="Owen", role=Role.ADMIN)
    dispatcher = User(email="d@x.test", name="Dispatch", role=Role.DISPATCHER)
    tech = User(email="t@x.test", name="Tech", role=Role.TECH)
    db.add_all([admin, dispatcher, tech])
    db.flush()

    person = Contact(first_name="Real", last_name="Customer", phone="(941) 555-0004")
    db.add(person)
    db.flush()

    pipe = Pipeline(name="Dream Team Roofing AHS", position=0)
    empty = Pipeline(name="Retail", position=1)
    db.add_all([pipe, empty])
    db.flush()

    stages = []
    for position, name in enumerate(STAGE_NAMES):
        s = Stage(pipeline_id=pipe.id, name=name, position=position)
        db.add(s)
        db.flush()
        stages.append(s.id)

    # deals: 2 in New Lead, 1 in Inspection, 1 in the SECOND Call Back.
    for i, (stage_index, title) in enumerate([
            (0, "AHS-101 - new"), (0, "AHS-102 - new"),
            (1, "AHS-103 - inspecting"), (3, "AHS-104 - call back")]):
        db.add(Opportunity(
            title=title, contact_id=person.id, pipeline_id=pipe.id,
            stage_id=stages[stage_index], value_cents=1000 * (i + 1), position=i,
            # The live join key: this is what must never be deleted as a side
            # effect of tidying the board.
            custom_fields={"owen_call_id": "call-%d" % i}))

    db.add(SavedView(name="Open AHS", pipeline_id=pipe.id, status="open", q=""))
    db.add(Calendar(name="Roof crew", pipeline_id=pipe.id))

    tokens = {}
    for key, u in (("admin", admin), ("dispatcher", dispatcher), ("tech", tech)):
        plain, token = mint_api_token(u, name="test-" + key)
        db.add(token)
        tokens[key] = plain
    db.commit()
    ids = {"pipeline": pipe.id, "empty": empty.id, "stages": stages}
    db.close()

    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + tokens["admin"]
        c.ids = ids
        c.tokens = tokens
        yield c


def _as(client, role: str) -> TestClient:
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer " + client.tokens[role]
    c.ids = client.ids
    return c


def _where_every_deal_is() -> dict[str, int]:
    """`title -> stage_id`, read straight from the database."""
    db = SessionLocal()
    try:
        return {o.title: o.stage_id for o in db.scalars(select(Opportunity)).all()}
    finally:
        db.close()


def _structure() -> list[tuple[int, str, list[tuple[int, str, int]]]]:
    """Every pipeline and its stages, ordered — the thing a refusal must not change."""
    db = SessionLocal()
    try:
        out = []
        for p in db.scalars(select(Pipeline).order_by(Pipeline.id)).all():
            out.append((p.id, p.name,
                        [(s.id, s.name, s.position) for s in
                         sorted(p.stages, key=lambda s: s.position)]))
        return out
    finally:
        db.close()


def _stage_names(client, pipeline_id=None) -> list[str]:
    pid = pipeline_id or client.ids["pipeline"]
    p = next(x for x in client.get("/api/pipelines").json() if x["id"] == pid)
    return [s["name"] for s in p["stages"]]


# ---------------- creating ----------------

def test_a_new_pipeline_starts_empty_rather_than_guessing_at_stages(client):
    r = client.post("/api/pipelines", json={"name": "  Retrofit  "})
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "Retrofit"
    assert r.json()["stages"] == [], (
        "a default set of stages was invented for a roofing pipeline nobody described")


def test_a_new_stage_is_appended_and_moves_nothing(client):
    before = _where_every_deal_is()
    r = client.post("/api/pipelines/%d/stages" % client.ids["pipeline"],
                    json={"name": "Warranty"})
    assert r.status_code == 201, r.text
    assert r.json()["position"] == len(STAGE_NAMES)
    assert r.json()["count"] == 0
    assert _stage_names(client) == [*STAGE_NAMES, "Warranty"]
    assert _where_every_deal_is() == before


@pytest.mark.parametrize("path,body", [
    ("/api/pipelines", {"name": ""}),
    ("/api/pipelines", {"name": "   "}),
    ("/api/pipelines", {"name": "P" * 161}),
])
def test_an_unnamed_pipeline_is_not_created(client, path, body):
    before = _structure()
    assert client.post(path, json=body).status_code in (400, 422)
    assert _structure() == before


def test_an_unnamed_stage_is_not_created(client):
    before = _structure()
    r = client.post("/api/pipelines/%d/stages" % client.ids["pipeline"],
                    json={"name": "   "})
    assert r.status_code == 400
    assert _structure() == before


# ---------------- renaming ----------------

def test_renaming_a_stage_leaves_every_opportunity_where_it_was(client):
    before = _where_every_deal_is()
    stage = client.ids["stages"][1]          # Inspection, holds one deal
    r = client.patch("/api/stages/%d" % stage, json={"name": "Site visit"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Site visit"
    assert _stage_names(client)[1] == "Site visit"
    assert _where_every_deal_is() == before, "a rename moved a deal"

    # ...and the deal is still reachable through the renamed stage.
    listed = client.get("/api/opportunities", params={
        "pipeline_id": client.ids["pipeline"], "status": "all"}).json()
    assert [o["title"] for o in listed if o["stage_id"] == stage] == [
        "AHS-103 - inspecting"]


def test_renaming_does_not_make_stage_names_unique(client):
    """Two distinct stages called "Call Back" are the MEASURED shape, and the
    `ghl` CLI exits 5 (ambiguous) rather than guessing between them. Renaming one
    to match another must stay possible, or that behaviour quietly disappears."""
    stages = client.ids["stages"]
    assert _stage_names(client).count("Call Back") == 2, "the fixture drifted"

    r = client.patch("/api/stages/%d" % stages[1], json={"name": "Call Back"})
    assert r.status_code == 200, "renaming to an existing name was refused"
    assert _stage_names(client).count("Call Back") == 3

    # Three distinct ids under one name is exactly what `resolve.pick` must see.
    p = next(x for x in client.get("/api/pipelines").json()
             if x["id"] == client.ids["pipeline"])
    same = [s["id"] for s in p["stages"] if s["name"] == "Call Back"]
    assert len(set(same)) == 3


def test_a_stage_cannot_be_moved_to_another_pipeline_by_renaming_it(client):
    """The rename body has no `pipeline_id`, deliberately — moving a stage across
    would carry every deal in it, which PATCH /api/opportunities already refuses."""
    before = _structure()
    r = client.patch("/api/stages/%d" % client.ids["stages"][0],
                     json={"name": "New Lead", "pipeline_id": client.ids["empty"]})
    assert r.status_code == 200
    assert _structure() == before, "a stage changed pipelines"


def test_renaming_a_pipeline_keeps_its_stages_and_deals(client):
    before = _where_every_deal_is()
    r = client.patch("/api/pipelines/%d" % client.ids["pipeline"],
                     json={"name": "AHS 2026"})
    assert r.status_code == 200 and r.json()["name"] == "AHS 2026"
    assert _stage_names(client) == STAGE_NAMES
    assert _where_every_deal_is() == before


def test_an_unnamed_rename_changes_nothing(client):
    before = _structure()
    assert client.patch("/api/stages/%d" % client.ids["stages"][0],
                        json={"name": "  "}).status_code == 400
    assert client.patch("/api/pipelines/%d" % client.ids["pipeline"],
                        json={"name": ""}).status_code == 400
    assert _structure() == before


# ---------------- reordering ----------------

def test_reordering_stages_moves_no_opportunity_between_stages(client):
    """The whole risk of this feature in one test."""
    before = _where_every_deal_is()
    stages = client.ids["stages"]
    # Reverse the board completely.
    r = client.post("/api/pipelines/%d/stages/reorder" % client.ids["pipeline"],
                    json={"stage_ids": list(reversed(stages))})
    assert r.status_code == 200, r.text
    assert _stage_names(client) == list(reversed(STAGE_NAMES))
    assert [s["position"] for s in r.json()["stages"]] == [0, 1, 2, 3, 4]
    assert _where_every_deal_is() == before, "reordering moved a deal"


def test_reordering_keeps_each_stages_own_deals(client):
    """Not just "nothing moved" — the counts have to follow the stage, not the
    column position, or a reorder silently re-labels every deal on the board."""
    stages = client.ids["stages"]
    counts_before = {s["id"]: s["count"] for s in next(
        p for p in client.get("/api/pipelines").json()
        if p["id"] == client.ids["pipeline"])["stages"]}

    client.post("/api/pipelines/%d/stages/reorder" % client.ids["pipeline"],
                json={"stage_ids": [stages[3], stages[0], stages[4],
                                    stages[1], stages[2]]})

    after = next(p for p in client.get("/api/pipelines").json()
                 if p["id"] == client.ids["pipeline"])["stages"]
    assert [s["id"] for s in after] == [stages[3], stages[0], stages[4],
                                        stages[1], stages[2]]
    assert {s["id"]: s["count"] for s in after} == counts_before


@pytest.mark.parametrize("why,build", [
    ("a stage left out", lambda s: s[:-1]),
    ("a stage named twice", lambda s: [s[0], s[0], s[1], s[2], s[3]]),
    ("a stage that does not exist", lambda s: [*s[:-1], 999999]),
    ("nothing at all", lambda s: []),
])
def test_a_reorder_that_is_not_a_permutation_is_refused_whole(client, why, build):
    """A board that changed underneath the user must be refused, not half-applied:
    positions written for some stages and not others leave duplicates and holes."""
    before = _structure()
    deals = _where_every_deal_is()
    r = client.post("/api/pipelines/%d/stages/reorder" % client.ids["pipeline"],
                    json={"stage_ids": build(client.ids["stages"])})
    assert r.status_code in (400, 422), why
    assert _structure() == before, "a refused reorder still wrote positions: " + why
    assert _where_every_deal_is() == deals


def test_a_stage_from_another_pipeline_cannot_be_smuggled_into_an_order(client):
    other = client.post("/api/pipelines", json={"name": "Other"}).json()
    foreign = client.post("/api/pipelines/%d/stages" % other["id"],
                          json={"name": "Foreign"}).json()
    before = _structure()
    r = client.post("/api/pipelines/%d/stages/reorder" % client.ids["pipeline"],
                    json={"stage_ids": [*client.ids["stages"], foreign["id"]]})
    assert r.status_code == 400
    assert _structure() == before


# ---------------- deleting ----------------

def test_deleting_an_empty_stage_works_and_repacks_the_rest(client):
    stages = client.ids["stages"]
    before = _where_every_deal_is()
    r = client.delete("/api/stages/%d" % stages[2])     # the empty "Call Back"
    assert r.status_code == 200, r.text
    assert _stage_names(client) == ["New Lead", "Inspection", "Call Back",
                                    "Submit Invoices"]
    positions = [s["position"] for s in next(
        p for p in client.get("/api/pipelines").json()
        if p["id"] == client.ids["pipeline"])["stages"]]
    assert positions == [0, 1, 2, 3], "the remaining stages have a hole in them"
    assert _where_every_deal_is() == before


@pytest.mark.parametrize("index,held", [(0, 2), (1, 1), (3, 1)])
def test_deleting_a_populated_stage_is_refused_and_mutates_nothing(
        client, index, held):
    before = _structure()
    deals = _where_every_deal_is()
    r = client.delete("/api/stages/%d" % client.ids["stages"][index])
    assert r.status_code == 409
    # The message has to say what is in the way, or the admin cannot act on it.
    assert str(held) in r.json()["detail"]
    assert _structure() == before, "a refused stage delete still changed the board"
    assert _where_every_deal_is() == deals


def test_a_refused_stage_delete_deletes_no_opportunity(client):
    """The specific catastrophe: `custom_fields.owen_call_id` is the telephony
    project's join key, so an opportunity lost here breaks attribution history in
    a separate system."""
    db = SessionLocal()
    try:
        before = sorted(
            (o.id, (o.custom_fields or {}).get("owen_call_id"))
            for o in db.scalars(select(Opportunity)).all())
    finally:
        db.close()

    assert client.delete(
        "/api/stages/%d" % client.ids["stages"][0]).status_code == 409

    db = SessionLocal()
    try:
        after = sorted(
            (o.id, (o.custom_fields or {}).get("owen_call_id"))
            for o in db.scalars(select(Opportunity)).all())
    finally:
        db.close()
    assert after == before and len(after) == 4


def test_there_is_no_force_that_empties_a_stage(client):
    """No query parameter, no body flag, no second attempt gets past the rule."""
    before = _where_every_deal_is()
    for suffix in ("?force=true", "?force=1", "?cascade=true"):
        r = client.delete("/api/stages/%d%s" % (client.ids["stages"][0], suffix))
        assert r.status_code == 409, suffix
    assert _where_every_deal_is() == before


def test_deleting_a_populated_pipeline_is_refused_and_mutates_nothing(client):
    """`Pipeline.stages` cascades delete-orphan — this refusal is what stands
    between a mis-click and five columns of real deals."""
    before = _structure()
    deals = _where_every_deal_is()
    r = client.delete("/api/pipelines/%d" % client.ids["pipeline"])
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "5 stage" in detail and "4 opportunit" in detail
    assert _structure() == before
    assert _where_every_deal_is() == deals


def test_a_pipeline_with_stages_but_no_deals_is_still_refused(client):
    """Empty means empty. The columns are somebody's configuration too."""
    other = client.post("/api/pipelines", json={"name": "Draft"}).json()
    client.post("/api/pipelines/%d/stages" % other["id"], json={"name": "Only"})
    r = client.delete("/api/pipelines/%d" % other["id"])
    assert r.status_code == 409
    assert "1 stage" in r.json()["detail"] and "0 opportunit" in r.json()["detail"]


def test_an_empty_pipeline_deletes_and_detaches_what_pointed_at_it(client):
    """A saved view and a calendar hold a nullable `pipeline_id` with no data of
    their own, so they are detached rather than blocking the delete — and named in
    the response, so it is not a silent side effect."""
    # Point them at the empty pipeline first.
    db = SessionLocal()
    try:
        view = db.scalars(select(SavedView)).one()
        cal = db.scalars(select(Calendar)).one()
        view.pipeline_id = client.ids["empty"]
        cal.pipeline_id = client.ids["empty"]
        db.commit()
        view_id, cal_id = view.id, cal.id
    finally:
        db.close()

    r = client.delete("/api/pipelines/%d" % client.ids["empty"])
    assert r.status_code == 200, r.text
    # `detached_custom_fields` joined this list on 2026-09-11: a custom field
    # attached to the pipeline loses the attachment, never the definition and
    # never an answer, and the response names that too.
    assert r.json() == {"deleted": client.ids["empty"],
                        "detached_saved_views": [view_id],
                        "detached_calendars": [cal_id],
                        "detached_custom_fields": []}

    assert [p["id"] for p in client.get("/api/pipelines").json()] == [
        client.ids["pipeline"]]
    # The view and the calendar survive, pointing at nothing.
    assert client.get("/api/saved-views").json()[0]["pipeline_id"] is None
    assert client.get("/api/calendars").json()[0]["pipeline_id"] is None


def test_deleting_a_stage_that_does_not_exist_is_a_404_not_a_no_op(client):
    assert client.delete("/api/stages/999999").status_code == 404
    assert client.delete("/api/pipelines/999999").status_code == 404


# ---------------- roles ----------------

@pytest.mark.parametrize("role", ["dispatcher", "tech"])
def test_only_an_admin_can_change_the_pipeline_structure(client, role):
    """Renaming a stage changes a label four people navigate by and the `ghl` CLI
    resolves against; deleting one removes a column of the board."""
    who = _as(client, role)
    stages = client.ids["stages"]
    before = _structure()
    deals = _where_every_deal_is()

    attempts = [
        who.post("/api/pipelines", json={"name": "Sneaky"}),
        who.patch("/api/pipelines/%d" % client.ids["pipeline"],
                  json={"name": "Renamed"}),
        who.delete("/api/pipelines/%d" % client.ids["empty"]),
        who.post("/api/pipelines/%d/stages" % client.ids["pipeline"],
                 json={"name": "Sneaky stage"}),
        who.patch("/api/stages/%d" % stages[0], json={"name": "Renamed"}),
        who.delete("/api/stages/%d" % stages[2]),        # an EMPTY stage
        who.post("/api/pipelines/%d/stages/reorder" % client.ids["pipeline"],
                 json={"stage_ids": list(reversed(stages))}),
    ]
    assert [r.status_code for r in attempts] == [403] * 7

    assert _structure() == before, "a forbidden request still changed the structure"
    assert _where_every_deal_is() == deals


def test_a_dispatcher_can_still_work_the_board_they_cannot_restructure(client):
    """The gate is on the STRUCTURE, not on the deals. Locking a dispatcher out of
    moving a card would be a different, much bigger change."""
    d = _as(client, "dispatcher")
    stages = client.ids["stages"]
    r = d.patch("/api/opportunities/%d" % _ids_by_title()["AHS-101 - new"],
                json={"stage_id": stages[1]})
    assert r.status_code == 200, r.text
    assert d.get("/api/pipelines").status_code == 200


def _ids_by_title() -> dict[str, int]:
    db = SessionLocal()
    try:
        return {o.title: o.id for o in db.scalars(select(Opportunity)).all()}
    finally:
        db.close()

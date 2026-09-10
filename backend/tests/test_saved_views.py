"""Saved views — GHL calls them smart lists.

A saved view is only worth anything if applying it actually changes what the board
shows. That is the claim this file is built around: not "the row was stored" but
"the filters it stores narrow the board to fewer, and the right, opportunities".

The second half is roles. A view is SHARED — four people, one company — so
creating one is a write (STAFF) and deleting one takes another person's list away
(ADMIN), following this app's standing rule: everyone reads, staff write, admin
deletes.

The last section runs `frontend/src/lib/savedViews.ts` under node, the same way
`test_calendar_grid.py` and `test_csv_export.py` do, because "applying a view puts
the board somewhere else" is arithmetic a regex cannot check.
"""
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Contact, Opportunity, Pipeline, Role, SavedView, Stage, User
from fastapi.testclient import TestClient
from sqlalchemy import select


@pytest.fixture()
def client():
    """Six opportunities in one pipeline, so a filter has something to remove.

      open  : "Skylight leak", "Skylight replace", "Ridge vent"
      won   : "Skylight claim", "Full reroof"
      lost  : "Gutter run"

    So `status=won` alone leaves 2, `q=skylight` alone leaves 3, and the two
    together leave exactly 1 — three different numbers, so a filter that was
    quietly ignored cannot coincide with the right answer.
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    admin = User(email="a@x.test", name="Owen", role=Role.ADMIN)
    dispatcher = User(email="d@x.test", name="Dispatch", role=Role.DISPATCHER)
    tech = User(email="t@x.test", name="Tech", role=Role.TECH)
    db.add_all([admin, dispatcher, tech])
    db.flush()

    person = Contact(first_name="Saved", last_name="View", phone="(941) 555-0003")
    db.add(person)
    db.flush()

    pipe = Pipeline(name="AHS")
    other = Pipeline(name="Retail")
    db.add_all([pipe, other])
    db.flush()
    stage = Stage(pipeline_id=pipe.id, name="New Lead", position=0)
    db.add(stage)
    db.flush()

    for i, (title, status) in enumerate([
            ("Skylight leak", "open"), ("Skylight replace", "open"),
            ("Ridge vent", "open"), ("Skylight claim", "won"),
            ("Full reroof", "won"), ("Gutter run", "lost")]):
        db.add(Opportunity(title=title, contact_id=person.id, pipeline_id=pipe.id,
                           stage_id=stage.id, status=status, value_cents=1000 * i,
                           position=i))

    tokens = {}
    for key, u in (("admin", admin), ("dispatcher", dispatcher), ("tech", tech)):
        plain, token = mint_api_token(u, name="test-" + key)
        db.add(token)
        tokens[key] = plain
    db.commit()
    ids = {"pipeline": pipe.id, "other": other.id}
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


def _stored() -> list[tuple[int, str, str, str]]:
    db = SessionLocal()
    try:
        return [(v.id, v.name, v.status, v.q)
                for v in db.scalars(select(SavedView).order_by(SavedView.id)).all()]
    finally:
        db.close()


def _board(client, view: dict) -> list[str]:
    """What the board shows for a view's filters."""
    params = {"pipeline_id": view["pipeline_id"] or client.ids["pipeline"],
              "status": view["status"]}
    if view["q"]:
        params["q"] = view["q"]
    r = client.get("/api/opportunities", params=params)
    assert r.status_code == 200, r.text
    return [o["title"] for o in r.json()]


# ---------------- a saved view narrows the board ----------------

def test_applying_a_saved_view_actually_narrows_the_board(client):
    default = _board(client, {"pipeline_id": None, "status": "open", "q": ""})
    assert sorted(default) == ["Ridge vent", "Skylight leak", "Skylight replace"]

    won = client.post("/api/saved-views", json={
        "name": "Won skylights", "pipeline_id": client.ids["pipeline"],
        "status": "won", "q": "skylight"}).json()

    narrowed = _board(client, won)
    assert narrowed == ["Skylight claim"], (
        "the saved view's filters did not narrow the board")
    assert len(narrowed) < len(default)


@pytest.mark.parametrize("status,q,expected", [
    ("won", "", ["Full reroof", "Skylight claim"]),
    ("open", "skylight", ["Skylight leak", "Skylight replace"]),
    ("won", "skylight", ["Skylight claim"]),
    ("all", "skylight", ["Skylight claim", "Skylight leak", "Skylight replace"]),
])
def test_each_saved_filter_is_carried_through_on_its_own(client, status, q, expected):
    """Three different answers, so a filter that was silently dropped cannot
    coincidentally produce the right set."""
    v = client.post("/api/saved-views", json={
        "name": "V %s %s" % (status, q), "pipeline_id": client.ids["pipeline"],
        "status": status, "q": q}).json()
    assert sorted(_board(client, v)) == expected


def test_a_view_without_a_pipeline_leaves_the_board_where_it_is(client):
    """"Won deals" should be useful on whichever board is open, so a view that
    names no pipeline must not pin one."""
    v = client.post("/api/saved-views", json={"name": "Won", "status": "won"}).json()
    assert v["pipeline_id"] is None and v["pipeline_name"] is None


def test_the_default_view_is_not_a_row(client):
    """"Open opportunities" is the board's own default, so there is nothing to
    delete and no seeded row to keep in step with the code."""
    assert client.get("/api/saved-views").json() == []
    assert _stored() == []


# ---------------- storing them ----------------

def test_a_saved_view_survives_and_is_listed_for_everyone(client):
    made = client.post("/api/saved-views", json={
        "name": "  Won skylights  ", "pipeline_id": client.ids["pipeline"],
        "status": "won", "q": " skylight "})
    assert made.status_code == 201, made.text
    assert made.json()["name"] == "Won skylights", "the name kept its padding"
    assert made.json()["q"] == "skylight"
    assert made.json()["pipeline_name"] == "AHS"

    # ANY_USER reads: a TECH works the same board.
    listed = _as(client, "tech").get("/api/saved-views")
    assert listed.status_code == 200
    assert [v["name"] for v in listed.json()] == ["Won skylights"]


def test_renaming_a_view_keeps_its_filters(client):
    v = client.post("/api/saved-views", json={
        "name": "Old", "status": "won", "q": "skylight"}).json()
    r = client.patch("/api/saved-views/%d" % v["id"], json={"name": "New"})
    assert r.status_code == 200
    assert r.json()["name"] == "New"
    assert (r.json()["status"], r.json()["q"]) == ("won", "skylight")
    assert _board(client, r.json()) == ["Skylight claim"]


def test_two_views_may_share_a_name(client):
    """The measured pipeline has two stages both called "Call Back" and this
    codebase resolves by id everywhere. A uniqueness rule here would be the only
    place that disagreed."""
    a = client.post("/api/saved-views", json={"name": "Mine", "status": "won"})
    b = client.post("/api/saved-views", json={"name": "Mine", "status": "lost"})
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] != b.json()["id"]


@pytest.mark.parametrize("why,body", [
    ("no name", {"name": "", "status": "open"}),
    ("whitespace name", {"name": "   ", "status": "open"}),
    ("a status the board has no filter for", {"name": "X", "status": "pending"}),
    ("a pipeline that does not exist", {"name": "X", "pipeline_id": 999999}),
    ("a name wider than its column", {"name": "N" * 81}),
])
def test_an_invalid_saved_view_stores_nothing(client, why, body):
    before = _stored()
    r = client.post("/api/saved-views", json=body)
    assert r.status_code in (400, 422), why
    assert _stored() == before, "a refused create still wrote a row: " + why


def test_deleting_a_view_removes_it_and_leaves_the_others(client):
    keep = client.post("/api/saved-views", json={"name": "Keep", "status": "won"}).json()
    drop = client.post("/api/saved-views", json={"name": "Drop", "status": "lost"}).json()
    r = client.delete("/api/saved-views/%d" % drop["id"])
    assert r.status_code == 200 and r.json()["deleted"] == drop["id"]
    assert [v[0] for v in _stored()] == [keep["id"]]
    # ...and deleting a list does not delete any opportunity.
    assert len(_board(client, {"pipeline_id": None, "status": "all", "q": ""})) == 6


# ---------------- roles ----------------

def test_a_tech_cannot_save_a_shared_list_and_writes_no_row(client):
    before = _stored()
    r = _as(client, "tech").post("/api/saved-views",
                                 json={"name": "Sneaky", "status": "won"})
    assert r.status_code == 403
    assert _stored() == before, "a forbidden create still wrote a row"


def test_a_dispatcher_can_save_but_not_delete(client):
    """Everyone reads, staff write, admin deletes (CLAUDE.md). A saved list is
    shared, so deleting one takes another person's list away."""
    d = _as(client, "dispatcher")
    made = d.post("/api/saved-views", json={"name": "Dispatch list", "status": "won"})
    assert made.status_code == 201

    before = _stored()
    refused = d.delete("/api/saved-views/%d" % made.json()["id"])
    assert refused.status_code == 403
    assert _stored() == before, "a forbidden delete still removed the row"

    assert client.delete("/api/saved-views/%d" % made.json()["id"]).status_code == 200


def test_a_tech_cannot_rename_a_shared_list(client):
    v = client.post("/api/saved-views", json={"name": "Owned", "status": "won"}).json()
    before = _stored()
    r = _as(client, "tech").patch("/api/saved-views/%d" % v["id"],
                                  json={"name": "Renamed"})
    assert r.status_code == 403
    assert _stored() == before


# ---------------- applying one, executed under node ----------------

VIEWS_TS = (Path(__file__).resolve().parents[2]
            / "frontend" / "src" / "lib" / "savedViews.ts")

NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None,
    reason="node is not on PATH, so savedViews.ts cannot be executed. CI's backend "
           "job installs it — see .github/workflows/ci.yml.")


def run_js(body: str):
    script = textwrap.dedent("""
        import * as v from %s
        const out = (x) => console.log('@@' + JSON.stringify(x))
    """) % json.dumps(VIEWS_TS.as_posix()) + textwrap.dedent(body)
    proc = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-"],
        input=script, capture_output=True, text=True, env={**os.environ})
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert line, "the script printed no result:\n" + proc.stdout + proc.stderr
    return json.loads(line[-1][2:])


@node
def test_applying_a_view_changes_what_the_board_asks_for():
    """The failure this covers: a view that resolves back to the filters already
    in effect. Nothing on screen would look wrong."""
    got = run_js("""
        const now = {pipelineId: 1, status: 'open', q: ''}
        const view = {pipeline_id: 2, status: 'won', q: 'skylight'}
        out({applied: v.applyView(view, now), was: now})
    """)
    assert got["applied"] == {"pipelineId": 2, "status": "won", "q": "skylight"}
    assert got["applied"] != got["was"]


@node
def test_a_view_with_no_pipeline_keeps_the_board_on_its_own():
    got = run_js("""
        out(v.applyView({pipeline_id: null, status: 'won', q: ''},
                        {pipelineId: 7, status: 'open', q: 'x'}))
    """)
    assert got == {"pipelineId": 7, "status": "won", "q": ""}


@node
def test_the_row_knows_which_view_the_board_is_currently_showing():
    got = run_js("""
        const view = {pipeline_id: 2, status: 'won', q: 'skylight'}
        out({
          exact: v.matchesView(view, {pipelineId: 2, status: 'won', q: 'skylight'}),
          padded: v.matchesView(view, {pipelineId: 2, status: 'won', q: ' skylight '}),
          otherPipeline: v.matchesView(view, {pipelineId: 3, status: 'won', q: 'skylight'}),
          otherStatus: v.matchesView(view, {pipelineId: 2, status: 'open', q: 'skylight'}),
          anyPipeline: v.matchesView({pipeline_id: null, status: 'won', q: ''},
                                     {pipelineId: 99, status: 'won', q: ''}),
          isDefault: v.isDefaultView({pipelineId: 1, status: 'open', q: '  '}),
          notDefault: v.isDefaultView({pipelineId: 1, status: 'won', q: ''}),
        })
    """)
    assert got == {"exact": True, "padded": True, "otherPipeline": False,
                   "otherStatus": False, "anyPipeline": True,
                   "isDefault": True, "notDefault": False}

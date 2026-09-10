"""The kanban drop arithmetic, executed rather than pattern-matched.

The project has no JS test runner, so frontend invariants are normally asserted
against source (test_frontend_layout.py, test_frontend_feedback.py). That is too
weak for this one, for the same reason it was too weak for the calendar grid: the
bug was arithmetic, and a regex saying "the file mentions position" would have
passed against the broken version too.

`frontend/src/lib/boardOrder.ts` is therefore written free of React and of any
import, so node can import the real shipped module directly, exactly as
test_calendar_grid.py does with calendarGrid.ts. These tests run the code the
browser runs.

The bugs they cover, both in `OpportunitiesPage`'s `onDragEnd`:

  * `if (o.stage_id === stageId) return` — a drop back into the card's own column
    was ignored, so the board could not be reordered at all;
  * the PATCH carried no position, so every cross-column drop landed at the TOP
    of the target column rather than where it was released.

The last section is the one that matters most. The board moves a card on screen
before the server answers, which means the browser predicts the new order — so
the prediction is run here against the same scenarios as the real endpoint and
the two are compared. If they ever disagree, a successful drag makes the card
jump a moment after it lands, and a failed one is invisible.
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
from app.models import Contact, Opportunity, Pipeline, Role, Stage, User
from fastapi.testclient import TestClient

ORDER_TS = (Path(__file__).resolve().parents[2]
            / "frontend" / "src" / "lib" / "boardOrder.ts")

NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None,
    reason="node is not on PATH, so boardOrder.ts cannot be executed. CI's "
           "backend job installs it precisely so this file is never skipped "
           "there — see .github/workflows/ci.yml.")


def run_js(body: str):
    """Execute `body` with the real module imported as `b`, and return its JSON."""
    script = textwrap.dedent("""
        import * as b from %s
        const out = (v) => console.log('@@' + JSON.stringify(v))
    """) % json.dumps(ORDER_TS.as_posix()) + textwrap.dedent(body)
    proc = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-"],
        input=script, capture_output=True, text=True, env=dict(os.environ))
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert line, "the script printed no result:\n" + proc.stdout + proc.stderr
    return json.loads(line[-1][2:])


def card(id, stage_id, position):
    return {"id": id, "stage_id": stage_id, "position": position}


# One column of four, and a second column of two. Stage ids are deliberately 1
# and 2 — the same small integers the card ids use — because that collision is
# real and is why the dnd-kit ids are namespaced.
COLUMN = [card(10, 1, 0), card(11, 1, 1), card(12, 1, 2), card(13, 1, 3),
          card(20, 2, 0), card(21, 2, 1)]


def drop(cards, active, over):
    """`moveForDrop` for one drop, as the page calls it."""
    return run_js("""
        out(b.moveForDrop(%s, %s, %s))
    """ % (json.dumps(cards), json.dumps(active), json.dumps(over)))


# ---------------- reordering inside one column ----------------

@node
def test_dropping_a_card_lower_in_its_own_column_sends_that_index():
    """The whole feature: this drop used to be discarded before it was read."""
    # Card 10 is at the top; drop it on card 12, the third.
    got = drop(COLUMN, "card:10", "card:12")
    assert got == {"id": 10, "stage_id": 1, "position": 2}


@node
def test_dropping_a_card_higher_in_its_own_column_sends_that_index():
    got = drop(COLUMN, "card:13", "card:11")
    assert got == {"id": 13, "stage_id": 1, "position": 1}


@node
def test_dropping_a_card_on_itself_asks_for_nothing():
    """No request at all, so nothing can be refused and nothing can be notified
    about — the drop that was never a move."""
    assert drop(COLUMN, "card:11", "card:11") is None


@node
def test_a_card_dropped_on_nothing_asks_for_nothing():
    """Released outside every column: the board leaves it where it was."""
    assert drop(COLUMN, "card:11", None) is None


@node
def test_dropping_on_the_empty_space_below_the_last_card_means_the_bottom():
    """The column's own droppable, not a card's. Its own column and another one
    give different answers, because a card leaving its slot frees one."""
    assert drop(COLUMN, "card:10", "stage:1") == {
        "id": 10, "stage_id": 1, "position": 3}
    assert drop(COLUMN, "card:10", "stage:2") == {
        "id": 10, "stage_id": 2, "position": 2}


@node
def test_a_stage_id_is_never_mistaken_for_an_opportunity_id():
    """Stage 1 and opportunity 1 both exist. Undecorated numeric ids made a drop
    on a column indistinguishable from a drop on a card."""
    got = run_js("""
        out({
          card: [b.cardIdFrom('card:7'), b.cardIdFrom('stage:7')],
          stage: [b.stageIdFrom('stage:7'), b.stageIdFrom('card:7')],
          ids: [b.cardDragId(7), b.stageDropId(7)],
        })
    """)
    assert got["card"] == [7, None]
    assert got["stage"] == [7, None]
    assert got["ids"] == ["card:7", "stage:7"]


# ---------------- moving between columns ----------------

@node
def test_a_card_dropped_in_the_middle_of_another_column_lands_there():
    """Not at the top, which is where every one of these went before."""
    assert drop(COLUMN, "card:10", "card:21") == {
        "id": 10, "stage_id": 2, "position": 1}
    assert drop(COLUMN, "card:10", "card:20") == {
        "id": 10, "stage_id": 2, "position": 0}


@node
def test_a_card_dropped_on_an_empty_column_lands_in_it():
    """A column with no cards has nothing sortable to drop onto, so this is the
    one drop that can only be resolved by the column's own droppable."""
    assert drop(COLUMN, "card:10", "stage:9") == {
        "id": 10, "stage_id": 9, "position": 0}


# ---------------- the filtered board ----------------

@node
def test_the_drop_is_anchored_to_the_card_above_it_not_to_its_visible_index():
    """`position` ranks a card among ALL of its stage's cards, but the board
    draws a column filtered by status and by the search box.

    Here the stage really holds four cards at positions 0..3, and the filter is
    hiding the one at position 1. Dropping between the two cards the user can
    see is "position 2", not "index 1" — sending the visible index would file the
    card above the very card it was dropped below.
    """
    visible = [card(10, 1, 0), card(12, 1, 2), card(13, 1, 3), card(30, 5, 0)]
    got = drop(visible, "card:30", "card:13")
    assert got == {"id": 30, "stage_id": 1, "position": 3}, (
        "the drop was anchored to the visible index, not to the card above it")

    # ...and the top of a filtered column is still the top.
    assert drop(visible, "card:30", "card:10") == {
        "id": 30, "stage_id": 1, "position": 0}


# ---------------- the optimistic board matches the server ----------------

@pytest.fixture()
def board():
    """A pipeline with a filtered column: 5 cards in stage one, of which the
    third is `won` and so hidden by the board's default Status: Open filter."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    admin = User(email="a@x.test", name="Owen", role=Role.ADMIN)
    db.add(admin)
    db.flush()
    # No phone and no email: `_can_message` suppresses Rule 4, so these tests
    # measure ordering without also queueing a text on every cross-stage move.
    c = Contact(first_name="No", last_name="Contact")
    db.add(c)
    db.flush()
    pipe = Pipeline(name="AHS")
    db.add(pipe)
    db.flush()
    s1 = Stage(pipeline_id=pipe.id, name="New Lead", position=0)
    s2 = Stage(pipeline_id=pipe.id, name="Inspection", position=1)
    db.add_all([s1, s2])
    db.flush()
    for i in range(5):
        db.add(Opportunity(title="A%d" % i, contact_id=c.id, pipeline_id=pipe.id,
                           stage_id=s1.id, position=i, value_cents=1000,
                           status="won" if i == 2 else "open"))
    for i in range(3):
        db.add(Opportunity(title="B%d" % i, contact_id=c.id, pipeline_id=pipe.id,
                           stage_id=s2.id, position=i, value_cents=1000))
    plain, token = mint_api_token(admin, name="test")
    db.add(token)
    db.commit()
    ids = {"pipeline": pipe.id, "stage1": s1.id, "stage2": s2.id}
    db.close()
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + plain
        client.ids = ids
        yield client


def _rows(client, status):
    pid = client.ids["pipeline"]
    return client.get(
        f"/api/opportunities?pipeline_id={pid}&status={status}").json()


def _slim(rows):
    return [card(o["id"], o["stage_id"], o["position"]) for o in rows]


@node
@pytest.mark.parametrize("why,active,over", [
    ("down its own column", "card:1", "card:4"),
    ("up its own column", "card:5", "card:2"),
    ("to the bottom of its own column", "card:1", "stage:1"),
    ("into the middle of the next column", "card:2", "card:7"),
    ("onto the top of the next column", "card:4", "card:6"),
    ("to the bottom of the next column", "card:2", "stage:2"),
])
def test_the_browsers_prediction_of_a_drop_matches_what_the_server_does(
        board, why, active, over):
    """The card moves on screen the instant it is dropped, which means the
    browser works out the new order itself. If that prediction and
    `move_opportunity` ever disagree, a successful drag makes the card jump when
    the refetch lands — and the failure looks exactly like the CSRF bug this
    change exists to make impossible to hide.

    So: run the same drop through `boardOrder.ts` and through the real endpoint,
    on the same board, and compare. The board here is FILTERED — a won card the
    user cannot see sits in the middle of the first column — which is the case
    the prediction has to get right without ever being told about it.
    """
    visible = _slim(_rows(board, "open"))
    # A `stage:N` in the table above names the fixture's Nth stage, not a raw id.
    kind, n = over.split(":")
    over_id = over if kind == "card" else "stage:%d" % board.ids["stage" + n]

    move = drop(visible, active, over_id)
    assert move is not None, "the scenario %r is a no-op — retarget it" % why

    predicted = run_js("""
        out(b.applyMove(%s, %s))
    """ % (json.dumps(visible), json.dumps(move)))

    r = board.patch("/api/opportunities/%d" % move["id"],
                    json={"stage_id": move["stage_id"], "position": move["position"]})
    assert r.status_code == 200, r.text

    after = {o["id"]: o for o in _rows(board, "all")}
    for p in predicted:
        actual = after[p["id"]]
        assert (p["stage_id"], p["position"]) == (actual["stage_id"], actual["position"]), (
            "dropping %s: the browser put card %d at stage %s position %s, "
            "the server put it at stage %s position %s"
            % (why, p["id"], p["stage_id"], p["position"],
               actual["stage_id"], actual["position"]))

    # ...and the drawn order, which is what a person actually sees.
    assert [p["id"] for p in predicted] == [
        o["id"] for o in _rows(board, "open")], (
        "dropping %s: the optimistic board and the refetched board differ" % why)


@node
def test_the_prediction_leaves_both_columns_packed(board):
    """Every card of every touched stage, 0..n-1, no gap and no duplicate —
    which is what lets the NEXT drag anchor itself correctly."""
    rows = _slim(_rows(board, "all"))
    got = run_js("""
        const after = b.applyMove(%s, {id: 2, stage_id: %d, position: 1})
        const stages = {}
        for (const c of after) (stages[c.stage_id] ??= []).push(c.position)
        out(stages)
    """ % (json.dumps(rows), board.ids["stage2"]))
    for stage_id, positions in got.items():
        assert positions == list(range(len(positions))), (
            "stage %s came back as %s" % (stage_id, positions))

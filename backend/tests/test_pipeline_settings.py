"""The pipeline modal and the ⋮ menu: every setting has an effect a test can see.

* the name is required and unique, case-insensitively;
* "Use opportunity-level probability" and each stage's Probability (%) change the
  FORECAST's numbers;
* each stage's "Show in reports" funnel and pie switches take it out of the
  Dashboard's Funnel and Stage distribution respectively, and nothing else;
* the display colour mode and the stage colours are stored and returned;
* Duplicate copies every setting, stage and job-question attachment, never a deal;
* dragging and "Move to position" reorder the pipelines the board's selector lists;
* deleting a stage or a pipeline that holds deals MOVES them — `custom_fields`
  byte-identical, ZERO jobs enqueued, all-or-nothing — and never deletes one.

Roles: DISPATCHER creates, edits, duplicates and reorders; ADMIN alone deletes and
manages permissions; a refusal writes nothing, re-read from the database.
"""
import pathlib
import tempfile

import pytest
from alembic import command
from alembic.config import Config
from app import main as main_mod
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Calendar,
    Contact,
    CustomFieldDef,
    CustomFieldPipeline,
    Job,
    Opportunity,
    Pipeline,
    PipelinePermission,
    Role,
    SavedView,
    Stage,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text

BACKEND = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture()
def client():
    """
      AHS     New Lead (2 deals) · Inspection (1) · Call Back (0) · Invoice (1)
      Retail  Lead (1) · Won (0)
    Every deal's contact has a phone and is not on DND, so an ORDINARY move would
    queue a stage_change_notify job — which is what makes "zero jobs" meaningful.
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    users = {"admin": User(email="a@x.test", name="Owen", role=Role.ADMIN),
             "dispatcher": User(email="d@x.test", name="Dispatch", role=Role.DISPATCHER),
             "tech": User(email="t@x.test", name="Tech", role=Role.TECH)}
    db.add_all(users.values())
    db.flush()
    person = Contact(first_name="Real", last_name="Customer", phone="+19415550004")
    db.add(person)
    db.flush()

    ahs = Pipeline(name="Dream Team Roofing AHS", position=0)
    retail = Pipeline(name="Retail", position=1)
    db.add_all([ahs, retail])
    db.flush()
    stages = {}
    for pipe, names in ((ahs, ["New Lead", "Inspection", "Call Back", "Invoice"]),
                        (retail, ["Lead", "Won"])):
        for i, n in enumerate(names):
            s = Stage(pipeline_id=pipe.id, name=n, position=i)
            db.add(s)
            db.flush()
            stages[(pipe.name, n)] = s.id
    deals = {}
    for title, pipe, stage, value, status, blob in (
            ("AHS-1", ahs, "New Lead", 100_00, "open", {"owen_call_id": "call-1",
                                                        "roof_age": 14}),
            ("AHS-2", ahs, "New Lead", 200_00, "open", {"owen_call_id": "call-2"}),
            ("AHS-3", ahs, "Inspection", 400_00, "open", {"owen_call_id": "call-3",
                                                          "nested": {"a": [1, 2]}}),
            ("AHS-4", ahs, "Invoice", 800_00, "won", {}),
            ("R-1", retail, "Lead", 50_00, "open", {"owen_call_id": "call-r"})):
        o = Opportunity(title=title, contact_id=person.id, pipeline_id=pipe.id,
                        stage_id=stages[(pipe.name, stage)], value_cents=value,
                        status=status, custom_fields=blob, position=len(deals))
        db.add(o)
        db.flush()
        deals[title] = o.id

    field = CustomFieldDef(key="roof_age", label="Roof age", field_type="number")
    db.add(field)
    db.flush()
    db.add(CustomFieldPipeline(field_id=field.id, pipeline_id=ahs.id))
    db.add(SavedView(name="Open AHS", pipeline_id=ahs.id))
    db.add(Calendar(name="Roof crew", pipeline_id=ahs.id))
    tokens = {}
    for k, u in users.items():
        plain, tok = mint_api_token(u, name="t-" + k)
        db.add(tok)
        tokens[k] = plain
    db.commit()
    ids = {"ahs": ahs.id, "retail": retail.id, "stages": stages, "deals": deals,
           "field": field.id, "users": {k: u.id for k, u in users.items()}}
    db.close()

    clients = {}
    for k in users:
        c = TestClient(app)
        c.headers["Authorization"] = "Bearer " + tokens[k]
        c.ids = ids
        clients[k] = c
    yield clients


def _read(fn):
    db = SessionLocal()
    try:
        return fn(db)
    finally:
        db.close()


def _deals() -> dict[int, tuple]:
    """Every column of every deal, custom_fields as its raw stored JSON text."""
    def go(db):
        rows = db.execute(text(
            "SELECT id, title, contact_id, pipeline_id, stage_id, value_cents, status, "
            "position, owner_id, business_name, source, expected_close_date, created_by, "
            "probability, CAST(custom_fields AS TEXT), created_at, updated_at "
            "FROM opportunities ORDER BY id")).all()
        return {r[0]: tuple(r) for r in rows}
    return _read(go)


def _structure():
    def go(db):
        return [(p.id, p.name, p.position, p.color_mode, p.use_opportunity_probability,
                 [(s.id, s.name, s.position, s.color, s.probability, s.show_in_funnel,
                   s.show_in_pie) for s in sorted(p.stages, key=lambda s: s.position)])
                for p in db.scalars(select(Pipeline).order_by(Pipeline.id)).all()]
    return _read(go)


def _everything():
    def go(db):
        return (_structure(), _deals(),
                sorted((v.id, v.pipeline_id) for v in db.scalars(select(SavedView))),
                sorted((c.id, c.pipeline_id) for c in db.scalars(select(Calendar))),
                sorted((x.field_id, x.pipeline_id)
                       for x in db.scalars(select(CustomFieldPipeline))),
                sorted((g.pipeline_id, g.user_id)
                       for g in db.scalars(select(PipelinePermission))),
                db.scalar(select(func.count(CustomFieldDef.id))))
    return _read(go)


def _jobs() -> list[tuple]:
    return _read(lambda db: sorted((j.id, j.type, j.status, j.dedupe_key)
                                   for j in db.scalars(select(Job)).all()))


def _pipe(c, name):
    return next(p for p in c.get("/api/pipelines").json() if p["name"] == name)


# ---------------- the modal: create / edit ----------------

def test_create_stores_every_setting_and_paints_stages_from_the_palette(client):
    c = client["dispatcher"]
    r = c.post("/api/pipelines", json={
        "name": "Marketing pipeline", "use_opportunity_probability": True,
        "color_mode": "dot",
        "stages": [{"name": "New Lead", "probability": 20},
                   {"name": "Contacted", "probability": 40, "show_in_pie": False},
                   {"name": "Closed", "probability": 80, "color": "#123abc",
                    "show_in_funnel": False}]})
    assert r.status_code == 201, r.text
    p = _pipe(c, "Marketing pipeline")
    assert p["color_mode"] == "dot" and p["use_opportunity_probability"] is True
    assert p["updated_at"] is not None
    assert [(s["name"], s["position"], s["probability"], s["show_in_funnel"],
             s["show_in_pie"]) for s in p["stages"]] == [
        ("New Lead", 0, 20, True, True), ("Contacted", 1, 40, True, False),
        ("Closed", 2, 80, False, True)]
    assert [s["color"] for s in p["stages"]] == [
        main_mod.STAGE_PALETTE[0], main_mod.STAGE_PALETTE[1], "#123ABC"]
    # ...and it is appended after the existing pipelines.
    assert [x["name"] for x in c.get("/api/pipelines").json()][-1] == "Marketing pipeline"


@pytest.mark.parametrize("body", [
    {"name": ""}, {"name": "   "},
    {"name": "RETAIL"}, {"name": " retail "},                       # unique, any case
    {"name": "Ok", "color_mode": "rainbow"},
    {"name": "Ok", "stages": [{"name": "A", "probability": 101}]},
    {"name": "Ok", "stages": [{"name": "A", "probability": -1}]},
    {"name": "Ok", "stages": [{"name": "A", "color": "red"}]},
    {"name": "Ok", "stages": [{"name": "  "}]},
])
def test_an_invalid_create_is_refused_and_writes_nothing(client, body):
    before = _everything()
    r = client["admin"].post("/api/pipelines", json=body)
    assert r.status_code in (400, 409, 422), r.text
    assert _everything() == before


def test_renaming_to_another_pipelines_name_in_any_case_is_refused(client):
    before = _everything()
    r = client["admin"].patch("/api/pipelines/%d" % client["admin"].ids["ahs"],
                              json={"name": "rEtAiL"})
    assert r.status_code == 409 and "unique" in r.json()["detail"]
    assert _everything() == before
    # Its own name in another case is not a clash.
    r = client["admin"].patch("/api/pipelines/%d" % client["admin"].ids["retail"],
                              json={"name": "RETAIL"})
    assert r.status_code == 200 and r.json()["name"] == "RETAIL"


def test_update_applies_the_whole_stage_list_in_one_go(client):
    c = client["dispatcher"]
    ids = c.ids
    st = ids["stages"]
    before_deals = _deals()
    r = c.patch("/api/pipelines/%d" % ids["ahs"], json={
        "name": "AHS", "color_mode": "tint", "use_opportunity_probability": False,
        "stages": [
            {"id": st[("Dream Team Roofing AHS", "Inspection")], "name": "Site visit",
             "probability": 50},
            {"id": st[("Dream Team Roofing AHS", "New Lead")], "name": "New Lead",
             "probability": 10, "show_in_funnel": False},
            {"name": "Brand new", "probability": 70},
            {"id": st[("Dream Team Roofing AHS", "Call Back")], "name": "Call Back"},
            {"id": st[("Dream Team Roofing AHS", "Invoice")], "name": "Invoice",
             "color": "#00ff00"},
        ]})
    assert r.status_code == 200, r.text
    p = _pipe(c, "AHS")
    assert p["color_mode"] == "tint"
    assert [(s["name"], s["probability"], s["show_in_funnel"]) for s in p["stages"]] == [
        ("Site visit", 50, True), ("New Lead", 10, False), ("Brand new", 70, True),
        ("Call Back", None, True), ("Invoice", None, True)]
    assert p["stages"][4]["color"] == "#00FF00"
    assert p["stages"][2]["color"] == main_mod.STAGE_PALETTE[2]
    assert _deals() == before_deals, "editing the stage list moved or touched a deal"


def test_a_stage_list_that_fails_half_way_changes_nothing(client):
    """The third row is invalid; the first two must not have been renamed."""
    ids = client["admin"].ids
    st = ids["stages"]
    before = _everything()
    r = client["admin"].patch("/api/pipelines/%d" % ids["ahs"], json={
        "name": "Renamed too",
        "stages": [
            {"id": st[("Dream Team Roofing AHS", "New Lead")], "name": "Changed"},
            {"id": st[("Dream Team Roofing AHS", "Inspection")], "name": "Changed 2"},
            {"id": st[("Dream Team Roofing AHS", "Call Back")], "name": "   "},
            {"id": st[("Dream Team Roofing AHS", "Invoice")], "name": "Invoice"}]})
    assert r.status_code == 400
    assert _everything() == before


def test_a_stage_from_another_pipeline_cannot_be_put_in_the_list(client):
    ids = client["admin"].ids
    before = _everything()
    r = client["admin"].patch("/api/pipelines/%d" % ids["ahs"], json={"stages": [
        {"id": ids["stages"][("Retail", "Lead")], "name": "Stolen"}]})
    assert r.status_code == 400
    assert _everything() == before


# ---------------- the opportunity's own probability ----------------

def test_opportunity_probability_is_read_write_and_validated(client):
    c = client["dispatcher"]
    deal = c.ids["deals"]["AHS-1"]
    assert c.get("/api/opportunities/%d" % deal).json()["probability"] is None
    r = c.patch("/api/opportunities/%d/detail" % deal, json={"probability": 65})
    assert r.status_code == 200 and r.json()["probability"] == 65
    listed = c.get("/api/opportunities", params={"pipeline_id": c.ids["ahs"]}).json()
    assert next(o for o in listed if o["id"] == deal)["probability"] == 65
    for bad in (101, -5, "high"):
        before = _deals()
        assert c.patch("/api/opportunities/%d/detail" % deal,
                       json={"probability": bad}).status_code == 422
        assert _deals() == before
    assert c.patch("/api/opportunities/%d/detail" % deal,
                   json={"probability": None}).json()["probability"] is None
    made = c.post("/api/opportunities", json={
        "title": "With p", "pipeline_id": c.ids["ahs"], "probability": 30,
        "stage_id": c.ids["stages"][("Dream Team Roofing AHS", "New Lead")]}).json()
    assert c.get("/api/opportunities/%d" % made["id"]).json()["probability"] == 30


# ---------------- probability drives the Forecast ----------------

def _stage_row(c, pipeline_id, name):
    f = c.get("/api/forecast", params={"pipeline_id": pipeline_id}).json()
    return f, next(s for s in f["stages"] if s["name"] == name)


def test_an_untouched_pipeline_forecasts_at_the_conversion_rate_as_before(client):
    c = client["admin"]
    f, row = _stage_row(c, c.ids["ahs"], "New Lead")
    # 1 won, 0 lost -> 100.00%; open value 300.00.
    assert f["conversion_rate"] == 100.0
    assert row["weighting"] == "conversion_rate" and row["probability"] is None
    assert row["weighted_value_cents"] == 300_00


def test_a_stage_probability_changes_the_forecast(client):
    c = client["admin"]
    st = c.ids["stages"]
    c.patch("/api/stages/%d" % st[("Dream Team Roofing AHS", "New Lead")],
            json={"probability": 25})
    f, row = _stage_row(c, c.ids["ahs"], "New Lead")
    assert row["weighting"] == "stage" and row["probability"] == 25
    assert row["weighted_value_cents"] == 75_00          # 300.00 x 25%
    assert row["projected_value_cents"] == 75_00
    assert f["totals"]["weighted_value_cents"] == sum(
        s["weighted_value_cents"] for s in f["stages"])


def test_opportunity_level_probability_weights_each_deal_and_falls_back_to_the_stage(client):
    c = client["admin"]
    ids = c.ids
    new_lead = ids["stages"][("Dream Team Roofing AHS", "New Lead")]
    c.patch("/api/stages/%d" % new_lead, json={"probability": 50})
    c.patch("/api/opportunities/%d/detail" % ids["deals"]["AHS-1"], json={"probability": 10})
    # Toggle OFF: the deal's own 10% is ignored; the stage's 50% applies.
    _, off = _stage_row(c, ids["ahs"], "New Lead")
    assert off["weighting"] == "stage" and off["weighted_value_cents"] == 150_00
    # Toggle ON: AHS-1 at its own 10% (10.00), AHS-2 has none -> stage 50% (100.00).
    c.patch("/api/pipelines/%d" % ids["ahs"], json={"use_opportunity_probability": True})
    f, on = _stage_row(c, ids["ahs"], "New Lead")
    assert f["use_opportunity_probability"] is True
    assert on["weighting"] == "opportunity" and on["weighted_value_cents"] == 110_00


# ---------------- Show in reports ----------------

def test_hiding_a_stage_from_the_funnel_takes_it_out_of_the_funnel_only(client):
    c = client["admin"]
    st = c.ids["stages"]
    before = c.get("/api/dashboard/funnel", params={"pipeline_id": c.ids["ahs"]}).json()
    assert "Inspection" in [s["name"] for s in before["stages"]]
    assert before["total"] == 4
    c.patch("/api/stages/%d" % st[("Dream Team Roofing AHS", "Inspection")],
            json={"show_in_funnel": False})
    after = c.get("/api/dashboard/funnel", params={"pipeline_id": c.ids["ahs"]}).json()
    assert [s["name"] for s in after["stages"]] == ["New Lead", "Call Back", "Invoice"]
    # Its deal left the funnel's arithmetic with it.
    assert after["total"] == 3 and after["stages"][0]["reached"] == 3
    # ...but it is still a slice of the pie, and still a column on the board.
    assert "Inspection" in [s["name"] for s in after["distribution"]]
    assert "Inspection" in [s["name"] for s in _pipe(c, "Dream Team Roofing AHS")["stages"]]


def test_hiding_a_stage_from_the_pie_takes_it_out_of_the_pie_only(client):
    c = client["admin"]
    st = c.ids["stages"]
    before = c.get("/api/dashboard/funnel", params={"pipeline_id": c.ids["ahs"]}).json()
    assert before["distribution_total"] == 4
    c.patch("/api/stages/%d" % st[("Dream Team Roofing AHS", "New Lead")],
            json={"show_in_pie": False})
    after = c.get("/api/dashboard/funnel", params={"pipeline_id": c.ids["ahs"]}).json()
    assert "New Lead" not in [s["name"] for s in after["distribution"]]
    assert after["distribution_total"] == 2, "the donut's centre still counts the slice"
    assert "New Lead" in [s["name"] for s in after["stages"]]
    assert after["total"] == 4


def test_the_stage_distribution_card_draws_the_distribution_not_the_funnel():
    source = (BACKEND.parent / "frontend" / "src" / "pages" / "DashboardPage.tsx").read_text(
        encoding="utf-8")
    assert "distData?.distribution ?? []" in source
    assert "distribution_total" in source
    assert "(dist.data?.stages ?? [])" not in source, "the pie still draws the funnel's stages"


# ---------------- Duplicate ----------------

def test_duplicate_copies_every_setting_and_stage_and_never_a_deal(client):
    c = client["dispatcher"]
    ids = c.ids
    c.patch("/api/pipelines/%d" % ids["ahs"], json={
        "color_mode": "dot", "use_opportunity_probability": True})
    c.patch("/api/stages/%d" % ids["stages"][("Dream Team Roofing AHS", "Inspection")],
            json={"probability": 40, "show_in_pie": False, "color": "#abcdef"})
    client["admin"].put("/api/pipelines/%d/permissions" % ids["ahs"],
                        json={"user_ids": [ids["users"]["dispatcher"]]})
    deals_before = _deals()

    r = c.post("/api/pipelines/%d/duplicate" % ids["ahs"])
    assert r.status_code == 201, r.text
    copy = r.json()
    src = _pipe(c, "Dream Team Roofing AHS")
    assert copy["name"] == "Dream Team Roofing AHS (copy)"
    assert (copy["color_mode"], copy["use_opportunity_probability"]) == ("dot", True)
    strip = [(s["name"], s["position"], s["probability"], s["show_in_funnel"],
              s["show_in_pie"], s["color"]) for s in src["stages"]]
    assert [(s["name"], s["position"], s["probability"], s["show_in_funnel"],
             s["show_in_pie"], s["color"]) for s in copy["stages"]] == strip
    assert {s["id"] for s in copy["stages"]}.isdisjoint({s["id"] for s in src["stages"]})
    assert all(s["count"] == 0 for s in copy["stages"])
    assert _deals() == deals_before, "duplicate touched or copied a deal"
    # The job questions follow, and so does the access list.
    attached = _read(lambda db: sorted(x.pipeline_id for x in db.scalars(
        select(CustomFieldPipeline).where(CustomFieldPipeline.field_id == ids["field"]))))
    assert attached == sorted([ids["ahs"], copy["id"]])
    granted = _read(lambda db: [g.user_id for g in db.scalars(select(PipelinePermission).where(
        PipelinePermission.pipeline_id == copy["id"]))])
    assert granted == [ids["users"]["dispatcher"]]
    # A second duplicate does not collide with the first.
    assert c.post("/api/pipelines/%d/duplicate" % ids["ahs"]).json()["name"] == \
        "Dream Team Roofing AHS (copy 2)"


# ---------------- reorder ----------------

def test_reordering_pipelines_changes_the_order_the_board_lists(client):
    c = client["dispatcher"]
    ids = c.ids
    third = c.post("/api/pipelines", json={"name": "Third"}).json()["id"]
    r = c.post("/api/pipelines/reorder", json={"pipeline_ids": [third, ids["ahs"], ids["retail"]]})
    assert r.status_code == 200, r.text
    assert [p["id"] for p in c.get("/api/pipelines").json()] == [third, ids["ahs"], ids["retail"]]
    before = _everything()
    for bad in ([third, ids["ahs"]], [third, third, ids["retail"]], [third, ids["ahs"], 999]):
        assert c.post("/api/pipelines/reorder", json={"pipeline_ids": bad}).status_code == 400
    assert _everything() == before


# ---------------- deleting a stage that holds deals ----------------

@pytest.mark.usefixtures("rule_4_armed")
def test_deleting_a_stage_moves_its_deals_byte_identical_and_queues_nothing(client):
    c = client["admin"]
    ids = c.ids
    st = ids["stages"]
    # Prove the contact WOULD be texted by an ordinary move, then put it back.
    moved_normally = c.patch("/api/opportunities/%d" % ids["deals"]["R-1"],
                             json={"stage_id": st[("Retail", "Won")]})
    assert moved_normally.json()["automation"] == "queued"
    jobs = _jobs()
    assert len(jobs) == 1
    before = _deals()

    r = c.delete("/api/stages/%d" % st[("Dream Team Roofing AHS", "New Lead")],
                 params={"move_to_stage_id": st[("Dream Team Roofing AHS", "Inspection")]})
    assert r.status_code == 200, r.text
    moved = sorted(r.json()["moved_opportunities"])
    assert moved == sorted([ids["deals"]["AHS-1"], ids["deals"]["AHS-2"]])

    after = _deals()
    assert _jobs() == jobs, "a structural move queued a customer notification"
    for deal_id, row in before.items():
        if deal_id not in moved:
            assert after[deal_id] == row, "a deal that was not in the stage changed"
            continue
        # id title contact pipeline stage value status position owner business source
        # close created_by probability custom_fields created updated
        changed = {i for i, (a, b) in enumerate(zip(row, after[deal_id], strict=True)) if a != b}
        assert changed <= {4, 7}, "a structural move changed more than stage and rank"
        assert after[deal_id][4] == st[("Dream Team Roofing AHS", "Inspection")]
        assert after[deal_id][14] == row[14], "custom_fields changed"
        assert after[deal_id][16] == row[16], "updated_at changed"
    # They land after the stage's own deal, in their old order, with no shared rank.
    ranks = _read(lambda db: [o.position for o in db.scalars(
        select(Opportunity).where(Opportunity.stage_id == st[("Dream Team Roofing AHS",
                                                              "Inspection")])
        .order_by(Opportunity.position))])
    assert len(set(ranks)) == len(ranks) == 3
    assert "New Lead" not in [s["name"] for s in _pipe(c, "Dream Team Roofing AHS")["stages"]]


@pytest.mark.parametrize("where", ["missing", "other pipeline", "itself", "nowhere"])
def test_a_stage_delete_without_a_valid_destination_changes_nothing(client, where):
    c = client["admin"]
    st = c.ids["stages"]
    victim = st[("Dream Team Roofing AHS", "New Lead")]
    params = {"missing": {}, "other pipeline": {"move_to_stage_id": st[("Retail", "Lead")]},
              "itself": {"move_to_stage_id": victim},
              "nowhere": {"move_to_stage_id": 99999}}[where]
    before, jobs = _everything(), _jobs()
    r = c.delete("/api/stages/%d" % victim, params=params)
    assert r.status_code == (409 if where == "missing" else 400), r.text
    if where == "missing":
        assert "2 opportunities" in r.json()["detail"]
    assert _everything() == before and _jobs() == jobs


def test_a_stage_delete_that_fails_part_way_moves_nothing(client, monkeypatch):
    c = TestClient(app, raise_server_exceptions=False)
    c.headers.update(client["admin"].headers)
    st = client["admin"].ids["stages"]

    def boom(*_a, **_k):
        raise RuntimeError("the disk filled up")
    monkeypatch.setattr(main_mod, "_repack_stages", boom)
    before, jobs = _everything(), _jobs()
    r = c.delete("/api/stages/%d" % st[("Dream Team Roofing AHS", "New Lead")],
                 params={"move_to_stage_id": st[("Dream Team Roofing AHS", "Inspection")]})
    assert r.status_code == 500
    assert _everything() == before, "the deals moved although the delete failed"
    assert _jobs() == jobs


def test_removing_stages_in_the_modal_moves_deals_the_same_way(client):
    c = client["admin"]
    ids = c.ids
    st = ids["stages"]
    jobs = _jobs()
    before = _deals()
    keep = [st[("Dream Team Roofing AHS", n)] for n in ("Inspection", "Call Back")]
    r = c.patch("/api/pipelines/%d" % ids["ahs"], json={
        "stages": [{"id": keep[0], "name": "Inspection"}, {"id": keep[1], "name": "Call Back"}],
        "stage_moves": {str(st[("Dream Team Roofing AHS", "New Lead")]): keep[1],
                        str(st[("Dream Team Roofing AHS", "Invoice")]): keep[0]}})
    assert r.status_code == 200, r.text
    after = _deals()
    assert after[ids["deals"]["AHS-1"]][4] == keep[1]
    assert after[ids["deals"]["AHS-4"]][4] == keep[0]
    assert all(after[d][14] == before[d][14] and after[d][16] == before[d][16] for d in before)
    assert _jobs() == jobs
    assert len(_deals()) == 5


def test_a_modal_removal_missing_one_destination_moves_nothing_at_all(client):
    """Two stages removed; the first has a destination and the second does not. The
    first stage's deals must NOT have moved when the request is refused."""
    c = client["admin"]
    ids = c.ids
    st = ids["stages"]
    before, jobs = _everything(), _jobs()
    r = c.patch("/api/pipelines/%d" % ids["ahs"], json={
        "stages": [{"id": st[("Dream Team Roofing AHS", "Call Back")], "name": "Call Back"},
                   {"id": st[("Dream Team Roofing AHS", "Invoice")], "name": "Invoice"}],
        "stage_moves": {str(st[("Dream Team Roofing AHS", "New Lead")]):
                        st[("Dream Team Roofing AHS", "Call Back")]}})
    assert r.status_code == 409, r.text
    assert _everything() == before and _jobs() == jobs


def test_a_dispatcher_cannot_remove_a_stage_through_the_modal(client):
    c = client["dispatcher"]
    ids = c.ids
    st = ids["stages"]
    before = _everything()
    r = c.patch("/api/pipelines/%d" % ids["ahs"], json={
        "name": "Renamed on the way", "stages": [
            {"id": st[("Dream Team Roofing AHS", n)], "name": n}
            for n in ("New Lead", "Inspection", "Invoice")]})       # Call Back left out
    assert r.status_code == 403
    assert _everything() == before


# ---------------- deleting a pipeline that holds deals ----------------

def test_deleting_a_pipeline_moves_every_deal_and_detaches_what_pointed_at_it(client):
    c = client["admin"]
    ids = c.ids
    dest = ids["stages"][("Retail", "Won")]
    client["admin"].put("/api/pipelines/%d/permissions" % ids["ahs"],
                        json={"user_ids": [ids["users"]["dispatcher"]]})
    before, jobs = _deals(), _jobs()

    r = c.delete("/api/pipelines/%d" % ids["ahs"], params={"move_to_stage_id": dest})
    assert r.status_code == 200, r.text
    body = r.json()
    ahs_deals = sorted(d for t, d in ids["deals"].items() if t.startswith("AHS"))
    assert sorted(body["moved_opportunities"]) == ahs_deals
    assert body["detached_custom_fields"] == [ids["field"]]

    after = _deals()
    assert set(after) == set(before), "a deal was deleted"
    for d in ahs_deals:
        assert (after[d][3], after[d][4]) == (ids["retail"], dest)
        assert after[d][14] == before[d][14], "custom_fields changed"
        assert after[d][16] == before[d][16], "updated_at changed"
        assert after[d][:3] == before[d][:3] and after[d][5:7] == before[d][5:7]
    assert _jobs() == jobs, "a structural move queued a notification"

    db_state = _read(lambda db: (
        [v.pipeline_id for v in db.scalars(select(SavedView))],
        [x.pipeline_id for x in db.scalars(select(Calendar))],
        db.scalar(select(func.count(CustomFieldPipeline.id))),
        db.scalar(select(func.count(CustomFieldDef.id))),
        db.scalar(select(func.count(PipelinePermission.id))),
        db.scalar(select(func.count(Stage.id)).where(Stage.pipeline_id == ids["ahs"]))))
    assert db_state == ([None], [None], 0, 1, 0, 0)

    # The board still works: the saved view lists, and the moved deals show on Retail.
    assert client["dispatcher"].get("/api/saved-views").json()[0]["pipeline_id"] is None
    board = c.get("/api/opportunities", params={"pipeline_id": ids["retail"], "status": "all"})
    assert sorted(o["id"] for o in board.json() if o["stage_id"] == dest) == ahs_deals


def test_an_empty_pipeline_with_stages_deletes_after_the_confirm(client):
    """The 2026-09-10 rule refused this. The owner's override: it deletes, stages
    and all, because there is no deal in it to protect."""
    c = client["admin"]
    empty = c.post("/api/pipelines", json={"name": "Draft", "stages": [{"name": "Only"}]}).json()
    r = c.delete("/api/pipelines/%d" % empty["id"])
    assert r.status_code == 200 and r.json()["moved_opportunities"] == []
    assert "Draft" not in [p["name"] for p in c.get("/api/pipelines").json()]


@pytest.mark.parametrize("where", ["missing", "same pipeline", "nowhere"])
def test_a_pipeline_delete_without_a_valid_destination_changes_nothing(client, where):
    c = client["admin"]
    ids = c.ids
    params = {"missing": {},
              "same pipeline": {"move_to_stage_id":
                                ids["stages"][("Dream Team Roofing AHS", "Call Back")]},
              "nowhere": {"move_to_stage_id": 99999}}[where]
    before, jobs = _everything(), _jobs()
    r = c.delete("/api/pipelines/%d" % ids["ahs"], params=params)
    assert r.status_code == (409 if where == "missing" else 400), r.text
    assert _everything() == before and _jobs() == jobs


def test_a_pipeline_delete_that_fails_part_way_moves_nothing_and_deletes_nothing(
        client, monkeypatch):
    c = TestClient(app, raise_server_exceptions=False)
    c.headers.update(client["admin"].headers)
    ids = client["admin"].ids

    def boom(*_a, **_k):
        raise RuntimeError("connection lost")
    monkeypatch.setattr(main_mod, "_finish_pipeline_delete", boom)
    before, jobs = _everything(), _jobs()
    r = c.delete("/api/pipelines/%d" % ids["ahs"],
                 params={"move_to_stage_id": ids["stages"][("Retail", "Won")]})
    assert r.status_code == 500
    assert _everything() == before, "a failed pipeline delete still moved or removed something"
    assert _jobs() == jobs


# ---------------- roles ----------------

def test_a_dispatcher_creates_edits_duplicates_and_reorders(client):
    c = client["dispatcher"]
    ids = c.ids
    assert c.post("/api/pipelines", json={"name": "D1"}).status_code == 201
    renamed = c.patch("/api/pipelines/%d" % ids["retail"], json={"name": "Retail 2"})
    assert renamed.status_code == 200
    assert c.post("/api/pipelines/%d/duplicate" % ids["retail"]).status_code == 201
    order = [p["id"] for p in c.get("/api/pipelines").json()][::-1]
    assert c.post("/api/pipelines/reorder", json={"pipeline_ids": order}).status_code == 200


@pytest.mark.parametrize("who", ["dispatcher", "tech"])
def test_only_an_admin_deletes_and_a_refusal_changes_nothing(client, who):
    c = client[who]
    ids = c.ids
    before, jobs = _everything(), _jobs()
    attempts = [
        c.delete("/api/stages/%d" % ids["stages"][("Dream Team Roofing AHS", "Call Back")]),
        c.delete("/api/pipelines/%d" % ids["ahs"],
                 params={"move_to_stage_id": ids["stages"][("Retail", "Lead")]}),
        c.put("/api/pipelines/%d/permissions" % ids["ahs"], json={"user_ids": []}),
    ]
    assert [r.status_code for r in attempts] == [403, 403, 403]
    assert _everything() == before and _jobs() == jobs


def test_a_tech_changes_no_structure_at_all(client):
    c = client["tech"]
    ids = c.ids
    before = _everything()
    attempts = [
        c.post("/api/pipelines", json={"name": "T"}),
        c.patch("/api/pipelines/%d" % ids["ahs"], json={"color_mode": "dot"}),
        c.post("/api/pipelines/%d/duplicate" % ids["ahs"]),
        c.post("/api/pipelines/reorder", json={"pipeline_ids": [ids["retail"], ids["ahs"]]}),
        c.post("/api/pipelines/%d/stages" % ids["ahs"], json={"name": "T"}),
        c.patch("/api/stages/%d" % ids["stages"][("Retail", "Lead")], json={"probability": 5}),
    ]
    assert [r.status_code for r in attempts] == [403] * 6
    assert _everything() == before


# ---------------- the migration: additive, and old rows behave as before ----------------

def _alembic_config() -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    return cfg


def test_the_migration_only_adds_and_leaves_existing_rows_behaving_as_before():
    """Stand a database up at b7e3f1a8c204 (production), write rows the old code
    could have written, upgrade, and read them back."""
    import app.db as db_mod

    saved = db_mod.DATABASE_URL
    with tempfile.TemporaryDirectory() as tmp:
        url = "sqlite:///" + str(pathlib.Path(tmp) / "prod-shape.db")
        db_mod.DATABASE_URL = url
        eng = create_engine(url)
        try:
            command.upgrade(_alembic_config(), "b7e3f1a8c204")
            with eng.begin() as conn:
                conn.execute(text("INSERT INTO users (id, email, name, password_hash, role, "
                                  "token_version, is_active, created_at) VALUES "
                                  "(1, 'a@x', 'A', '', 'ADMIN', 0, 1, '2026-09-01')"))
                conn.execute(text("INSERT INTO pipelines (id, name, position) VALUES "
                                  "(1, 'Dream Team Roofing AHS', 0), (2, 'Retail', 1)"))
                conn.execute(text("INSERT INTO stages (id, pipeline_id, name, position) VALUES "
                                  "(1, 1, 'New Lead', 0), (2, 1, 'Call Back', 1)"))
                conn.execute(text(
                    "INSERT INTO opportunities (id, title, pipeline_id, stage_id, value_cents, "
                    "status, position, custom_fields, created_at, updated_at) VALUES "
                    "(1, 'AHS-1', 1, 1, 100, 'open', 0, '{\"owen_call_id\": \"c-1\"}', "
                    "'2026-09-01', '2026-09-02')"))
                old = {t: conn.execute(text("SELECT * FROM %s ORDER BY id" % t)).all()
                       for t in ("pipelines", "stages", "opportunities")}

            command.upgrade(_alembic_config(), "head")

            with eng.connect() as conn:
                for table, rows in old.items():
                    new = conn.execute(
                        text("SELECT * FROM %s ORDER BY id" % table)).mappings().all()
                    assert len(new) == len(rows)
                    for before, after in zip(rows, new, strict=True):
                        assert tuple(after[k] for k in before._fields) == tuple(before), (
                            "an existing %s row changed" % table)
                pipes = conn.execute(text(
                    "SELECT color_mode, use_opportunity_probability, updated_at FROM pipelines"
                )).all()
                assert pipes == [("none", 0, None), ("none", 0, None)]
                assert conn.execute(text(
                    "SELECT color, probability, show_in_funnel, show_in_pie FROM stages")).all() \
                    == [(None, None, 1, 1), (None, None, 1, 1)]
                deal = conn.execute(text("SELECT probability FROM opportunities")).all()
                assert deal == [(None,)]
                assert conn.execute(text("SELECT COUNT(*) FROM pipeline_permissions")).scalar() == 0
        finally:
            eng.dispose()
            db_mod.DATABASE_URL = saved


def test_the_migration_text_creates_and_adds_and_does_nothing_else():
    path = next((BACKEND / "migrations" / "versions").glob("d4a9c1e7b352_*.py"))
    source = path.read_text(encoding="utf-8")
    upgrade = source.split("def upgrade() -> None:", 1)[1].split("def downgrade()", 1)[0]
    ops = [line.strip().split("(", 1)[0] for line in upgrade.splitlines()
           if line.strip().startswith("op.")]
    assert set(ops) == {"op.add_column", "op.create_table", "op.create_index"}, ops
    assert "batch_alter_table(" not in source and "op.execute" not in upgrade
    assert "down_revision: str | Sequence[str] | None = 'b7e3f1a8c204'" in source
    for line in upgrade.split("op.add_column")[1:]:
        col = line.split("sa.Column(", 1)[1].split("))", 1)[0]
        if "nullable=False" in col:
            assert "server_default" in col, "a NOT NULL column without a server_default: " + col

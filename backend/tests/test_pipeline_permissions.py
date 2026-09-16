"""Per-pipeline access — "Manage permissions" — enforced on EVERY surface.

The owner's rule (2026-09-13): choose which users can access a pipeline; nobody
selected = everyone can; ADMIN always can; roles still decide what a user may DO.
A user without access must not see that pipeline or ANY of its deals ANYWHERE, and
asking for one by id answers 404 — the same answer as an id that does not exist —
so its existence does not leak.

This is security work, so there is one test per surface rather than one test that
samples a few, and `test_every_route_that_reads_a_deal_is_on_the_audited_list` at the
bottom makes a route added later fail until somebody decides how it enforces access.

The fixture:

    Open     — no permission rows, so everyone sees it. Stages Lead · Quote.
    Secret   — restricted to ONE dispatcher ("insider"). Stages S1 · S2.
               Deal titles carry "skylight" so a search for it would find them.

`outsider` is a DISPATCHER and `tech` a TECH, neither granted. Both must see Open
and nothing of Secret; `insider` and the ADMIN see both.
"""
import inspect
from datetime import UTC, datetime, timedelta

import pytest
from app import companycam_api as companycam_mod
from app import main as main_mod
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Appointment,
    Calendar,
    Contact,
    Conversation,
    ConversationEvent,
    CustomFieldDef,
    CustomFieldPipeline,
    Direction,
    EventType,
    Opportunity,
    Pipeline,
    PipelinePermission,
    Role,
    SavedView,
    Stage,
    User,
)
from app.zuper import api as zuper_api
from fastapi.testclient import TestClient
from sqlalchemy import func, select


@pytest.fixture()
def world():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    users = {
        "admin": User(email="a@x.test", name="Owen", role=Role.ADMIN),
        "insider": User(email="i@x.test", name="Insider", role=Role.DISPATCHER),
        "outsider": User(email="o@x.test", name="Outsider", role=Role.DISPATCHER),
        "tech": User(email="t@x.test", name="Tech", role=Role.TECH),
    }
    db.add_all(users.values())
    db.flush()

    person = Contact(first_name="Real", last_name="Customer", phone="+19415550123")
    db.add(person)
    db.flush()

    open_p = Pipeline(name="Open", position=0)
    secret = Pipeline(name="Secret", position=1)
    db.add_all([open_p, secret])
    db.flush()
    lead = Stage(pipeline_id=open_p.id, name="Lead", position=0)
    quote = Stage(pipeline_id=open_p.id, name="Quote", position=1)
    s1 = Stage(pipeline_id=secret.id, name="S1", position=0)
    s2 = Stage(pipeline_id=secret.id, name="S2", position=1)
    db.add_all([lead, quote, s1, s2])
    db.flush()

    open_deal = Opportunity(title="Open skylight repair", contact_id=person.id,
                            pipeline_id=open_p.id, stage_id=lead.id, value_cents=10000,
                            status="won")
    secret_deal = Opportunity(title="Secret skylight job", contact_id=person.id,
                              pipeline_id=secret.id, stage_id=s1.id, value_cents=99900,
                              status="won", custom_fields={"owen_call_id": "call-s"})
    secret_open = Opportunity(title="Secret skylight open", contact_id=person.id,
                              pipeline_id=secret.id, stage_id=s1.id, value_cents=5000)
    db.add_all([open_deal, secret_deal, secret_open])
    db.flush()

    db.add(PipelinePermission(pipeline_id=secret.id, user_id=users["insider"].id))

    cal = Calendar(name="Crew", pipeline_id=secret.id)
    db.add(cal)
    db.flush()
    start = datetime.now(UTC) + timedelta(days=3)
    appt = Appointment(title="Visit", calendar_id=cal.id, contact_id=person.id,
                       starts_at=start, ends_at=start + timedelta(hours=1),
                       opportunity_id=secret_deal.id)
    db.add(appt)
    view = SavedView(name="Secret list", pipeline_id=secret.id)
    db.add(view)
    field = CustomFieldDef(key="roof_age", label="Roof age", field_type="number")
    db.add(field)
    db.flush()
    db.add_all([CustomFieldPipeline(field_id=field.id, pipeline_id=open_p.id),
                CustomFieldPipeline(field_id=field.id, pipeline_id=secret.id)])

    # A call from the customer, so the Call report attributes their won deals.
    conv = Conversation(contact_id=person.id)
    db.add(conv)
    db.flush()
    db.add(ConversationEvent(conversation_id=conv.id, type=EventType.CALL,
                             direction=Direction.INBOUND, duration_seconds=60,
                             call_status="completed",
                             occurred_at=datetime.now(UTC) - timedelta(hours=2)))

    tokens = {}
    for key, u in users.items():
        plain, token = mint_api_token(u, name="t-" + key)
        db.add(token)
        tokens[key] = plain
    db.commit()

    ids = {"open": open_p.id, "secret": secret.id, "lead": lead.id, "quote": quote.id,
           "s1": s1.id, "s2": s2.id, "open_deal": open_deal.id,
           "secret_deal": secret_deal.id, "secret_open": secret_open.id,
           "appt": appt.id, "view": view.id, "cal": cal.id, "field": field.id,
           "contact": person.id, "users": {k: u.id for k, u in users.items()}}
    db.close()

    clients = {}
    for key in users:
        c = TestClient(app)
        c.headers["Authorization"] = "Bearer " + tokens[key]
        clients[key] = c
    yield ids, clients, tokens


BLIND = ["outsider", "tech"]
SIGHTED = ["admin", "insider"]


def _snapshot():
    """Every deal's every column, and the permission table — what a refusal must not move."""
    db = SessionLocal()
    try:
        deals = sorted((o.id, o.title, o.pipeline_id, o.stage_id, o.status, o.owner_id,
                        o.value_cents, o.position, repr(o.custom_fields))
                       for o in db.scalars(select(Opportunity)).all())
        grants = sorted((g.pipeline_id, g.user_id)
                        for g in db.scalars(select(PipelinePermission)).all())
        pipes = sorted((p.id, p.name, p.position) for p in db.scalars(select(Pipeline)).all())
        return deals, grants, pipes
    finally:
        db.close()


# ---------------- the rule itself ----------------

@pytest.mark.parametrize("who", BLIND)
def test_the_pipeline_list_does_not_name_a_pipeline_the_user_cannot_access(world, who):
    ids, c, _ = world
    names = [p["name"] for p in c[who].get("/api/pipelines").json()]
    assert names == ["Open"]


@pytest.mark.parametrize("who", SIGHTED)
def test_a_granted_user_and_an_admin_see_it(world, who):
    ids, c, _ = world
    assert [p["name"] for p in c[who].get("/api/pipelines").json()] == ["Open", "Secret"]


def test_nobody_selected_means_everyone_again(world):
    ids, c, _ = world
    r = c["admin"].put("/api/pipelines/%d/permissions" % ids["secret"], json={"user_ids": []})
    assert r.status_code == 200 and r.json()["everyone"] is True
    for who in BLIND:
        assert "Secret" in [p["name"] for p in c[who].get("/api/pipelines").json()]


def test_an_admin_always_has_access_even_when_not_listed(world):
    ids, c, _ = world
    # Restrict it to the TECH alone — the admin is not in the list.
    c["admin"].put("/api/pipelines/%d/permissions" % ids["secret"],
                   json={"user_ids": [ids["users"]["tech"]]})
    assert "Secret" in [p["name"] for p in c["admin"].get("/api/pipelines").json()]
    assert "Secret" in [p["name"] for p in c["tech"].get("/api/pipelines").json()]
    assert "Secret" not in [p["name"] for p in c["insider"].get("/api/pipelines").json()]


@pytest.mark.parametrize("who", ["insider", "outsider", "tech"])
def test_only_an_admin_manages_permissions_and_a_refusal_changes_nothing(world, who):
    ids, c, _ = world
    before = _snapshot()
    assert c[who].get("/api/pipelines/%d/permissions" % ids["open"]).status_code == 403
    r = c[who].put("/api/pipelines/%d/permissions" % ids["open"],
                   json={"user_ids": [ids["users"][who]]})
    assert r.status_code == 403
    assert _snapshot() == before


def test_permissions_refuse_an_unknown_user_and_write_nothing(world):
    ids, c, _ = world
    before = _snapshot()
    r = c["admin"].put("/api/pipelines/%d/permissions" % ids["open"], json={"user_ids": [9999]})
    assert r.status_code == 400
    assert _snapshot() == before


# ---------------- one test per surface ----------------

@pytest.mark.parametrize("who", BLIND)
def test_board_list_across_pipelines_omits_hidden_deals(world, who):
    ids, c, _ = world
    rows = c[who].get("/api/opportunities", params={"status": "all"}).json()
    assert {r["id"] for r in rows} == {ids["open_deal"]}


@pytest.mark.parametrize("who", BLIND)
def test_board_list_for_a_hidden_pipeline_is_the_same_as_for_no_pipeline(world, who):
    ids, c, _ = world
    hidden = c[who].get("/api/opportunities",
                        params={"pipeline_id": ids["secret"], "status": "all"})
    missing = c[who].get("/api/opportunities", params={"pipeline_id": 99999, "status": "all"})
    assert hidden.status_code == missing.status_code == 200
    assert hidden.json() == missing.json() == []


@pytest.mark.parametrize("who", BLIND)
def test_fetching_a_hidden_deal_by_id_is_a_404_indistinguishable_from_none(world, who):
    ids, c, _ = world
    hidden = c[who].get("/api/opportunities/%d" % ids["secret_deal"])
    missing = c[who].get("/api/opportunities/999999")
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()


@pytest.mark.parametrize("who", BLIND)
def test_dragging_a_hidden_deal_is_a_404_and_moves_nothing(world, who):
    ids, c, _ = world
    before = _snapshot()
    r = c[who].patch("/api/opportunities/%d" % ids["secret_deal"],
                     json={"stage_id": ids["s2"], "position": 0})
    assert r.status_code == 404
    assert _snapshot() == before


def test_editing_a_hidden_deal_is_a_404_and_writes_nothing(world):
    ids, c, _ = world
    before = _snapshot()
    r = c["outsider"].patch("/api/opportunities/%d/detail" % ids["secret_deal"],
                            json={"title": "Pwned", "probability": 90})
    assert r.status_code == 404
    assert _snapshot() == before


def test_filing_a_deal_into_a_hidden_pipeline_is_refused_like_a_wrong_pair(world):
    ids, c, _ = world
    before = _snapshot()
    hidden = c["outsider"].post("/api/opportunities", json={
        "title": "Sneak", "pipeline_id": ids["secret"], "stage_id": ids["s1"]})
    wrong = c["outsider"].post("/api/opportunities", json={
        "title": "Sneak", "pipeline_id": ids["open"], "stage_id": ids["s1"]})
    assert hidden.status_code == wrong.status_code == 400
    assert hidden.json() == wrong.json()
    assert _snapshot() == before


@pytest.mark.parametrize("path,body", [
    ("/api/opportunities/bulk/stage", lambda ids: {"ids": [ids["secret_deal"]],
                                                  "stage_id": ids["s2"]}),
    ("/api/opportunities/bulk/stage", lambda ids: {"ids": [ids["open_deal"]],
                                                  "stage_id": ids["s2"]}),
    ("/api/opportunities/bulk/owner", lambda ids: {"ids": [ids["open_deal"],
                                                           ids["secret_deal"]],
                                                   "owner_id": None}),
])
def test_bulk_actions_cannot_reach_a_hidden_deal_or_stage(world, path, body):
    ids, c, _ = world
    before = _snapshot()
    r = c["outsider"].post(path, json=body(ids))
    assert r.status_code in (400, 404)
    assert "Secret" not in r.text and "skylight job" not in r.text
    assert _snapshot() == before


@pytest.mark.parametrize("who", BLIND)
def test_search_and_the_palette_neither_list_nor_count_a_hidden_deal(world, who):
    ids, c, _ = world
    group = next(g for g in c[who].get("/api/search", params={"q": "skylight"}).json()["groups"]
                 if g["type"] == "opportunities")
    assert [i["id"] for i in group["items"]] == [ids["open_deal"]]
    assert group["total"] == 1, "the total still counts the hidden deals"


def test_search_still_finds_them_for_someone_granted(world):
    ids, c, _ = world
    group = next(g for g in c["insider"].get("/api/search", params={"q": "skylight"}).json()
                 ["groups"] if g["type"] == "opportunities")
    assert group["total"] == 3


def test_the_forecast_of_a_hidden_pipeline_is_a_404(world):
    ids, c, _ = world
    hidden = c["outsider"].get("/api/forecast", params={"pipeline_id": ids["secret"]})
    missing = c["outsider"].get("/api/forecast", params={"pipeline_id": 99999})
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()
    granted = c["insider"].get("/api/forecast", params={"pipeline_id": ids["secret"]})
    assert granted.status_code == 200


def test_dashboard_figures_are_computed_without_hidden_deals(world):
    ids, c, _ = world
    blind = c["outsider"].get("/api/dashboard").json()
    sighted = c["admin"].get("/api/dashboard").json()
    assert sighted["total"] == 3 and sighted["won_value_cents"] == 109900
    assert blind["total"] == 1 and blind["won_value_cents"] == 10000
    narrowed = c["outsider"].get("/api/dashboard", params={"pipeline_id": ids["secret"]}).json()
    assert narrowed["total"] == 0 and narrowed["total_value_cents"] == 0


def test_the_funnel_skips_a_hidden_pipeline_and_404s_when_asked_for_it(world):
    ids, c, _ = world
    # Make Secret the FIRST pipeline, so an unnarrowed default would land on it.
    c["admin"].post("/api/pipelines/reorder", json={"pipeline_ids": [ids["secret"], ids["open"]]})
    assert c["admin"].get("/api/dashboard/funnel").json()["pipeline_name"] == "Secret"
    assert c["outsider"].get("/api/dashboard/funnel").json()["pipeline_name"] == "Open"
    hidden = c["outsider"].get("/api/dashboard/funnel", params={"pipeline_id": ids["secret"]})
    missing = c["outsider"].get("/api/dashboard/funnel", params={"pipeline_id": 99999})
    assert hidden.status_code == 404 and missing.status_code == 404
    tech = c["tech"].get("/api/dashboard/funnel", params={"pipeline_id": ids["secret"]})
    assert tech.status_code == 404


def test_the_call_report_does_not_credit_a_hidden_won_deal(world):
    ids, c, _ = world
    def won(who):
        return sum(s["won"] for s in c[who].get("/api/reports/calls").json()["top_sources"])
    assert won("admin") == 2
    assert won("outsider") == 1


@pytest.mark.parametrize("who", BLIND)
def test_the_contact_panel_does_not_list_a_hidden_deal(world, who):
    ids, c, _ = world
    listed = c[who].get("/api/contacts/%d" % ids["contact"]).json()["opportunities"]
    assert [o["id"] for o in listed] == [ids["open_deal"]]


def test_the_contact_panel_writes_answer_with_the_same_narrowed_list(world):
    """PATCH, add tag and remove tag all return the panel payload too."""
    ids, c, _ = world
    cid = ids["contact"]
    for r in (c["outsider"].patch("/api/contacts/%d" % cid, json={"business_name": "Acme"}),
              c["outsider"].post("/api/contacts/%d/tags" % cid, json={"name": "vip"})):
        assert r.status_code in (200, 201), r.text
        assert [o["id"] for o in r.json()["opportunities"]] == [ids["open_deal"]]
    tag_id = c["admin"].get("/api/contacts/%d" % cid).json()["tags"][0]["id"]
    r = c["outsider"].delete("/api/contacts/%d/tags/%d" % (cid, tag_id))
    assert [o["id"] for o in r.json()["opportunities"]] == [ids["open_deal"]]
    assert len(c["admin"].get("/api/contacts/%d" % cid).json()["opportunities"]) == 3


@pytest.mark.parametrize("who", BLIND)
def test_the_calendar_keeps_the_visit_but_not_the_hidden_deal_on_it(world, who):
    ids, c, _ = world
    rows = c[who].get("/api/appointments").json()
    row = next(a for a in rows if a["id"] == ids["appt"])
    assert row["opportunity_id"] is None and row["opportunity_title"] is None
    detail = c[who].get("/api/appointments/%d" % ids["appt"]).json()
    assert detail["opportunity_id"] is None and detail["opportunity_title"] is None
    assert "skylight" not in str(rows) + str(detail)
    admin_row = c["admin"].get("/api/appointments/%d" % ids["appt"]).json()
    assert admin_row["opportunity_title"] == "Secret skylight job"


def test_a_booking_cannot_be_bound_to_a_hidden_deal(world):
    ids, c, _ = world
    start = (datetime.now(UTC) + timedelta(days=5)).isoformat()
    end = (datetime.now(UTC) + timedelta(days=5, hours=1)).isoformat()
    hidden = c["outsider"].post("/api/appointments", json={
        "title": "x", "starts_at": start, "ends_at": end,
        "opportunity_id": ids["secret_open"]})
    missing = c["outsider"].post("/api/appointments", json={
        "title": "x", "starts_at": start, "ends_at": end, "opportunity_id": 999999})
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["detail"].replace(str(ids["secret_open"]), "N") == \
        missing.json()["detail"].replace("999999", "N")
    db = SessionLocal()
    try:
        assert db.scalar(select(func.count(Appointment.id))) == 1
    finally:
        db.close()
    r = c["outsider"].patch("/api/appointments/%d" % ids["appt"],
                            json={"opportunity_id": ids["secret_open"]})
    assert r.status_code == 404


@pytest.mark.parametrize("who", BLIND)
def test_a_saved_view_on_a_hidden_pipeline_is_not_listed(world, who):
    ids, c, _ = world
    assert c[who].get("/api/saved-views").json() == []


def test_a_saved_view_cannot_be_pointed_at_or_edited_on_a_hidden_pipeline(world):
    ids, c, _ = world
    before = [v["name"] for v in c["admin"].get("/api/saved-views").json()]
    assert c["outsider"].post("/api/saved-views", json={
        "name": "x", "pipeline_id": ids["secret"]}).status_code == 400
    assert c["outsider"].patch("/api/saved-views/%d" % ids["view"],
                               json={"name": "renamed"}).status_code == 404
    assert [v["name"] for v in c["admin"].get("/api/saved-views").json()] == before


@pytest.mark.parametrize("who", BLIND)
def test_a_calendar_does_not_name_a_hidden_pipeline(world, who):
    ids, c, _ = world
    cal = next(x for x in c[who].get("/api/calendars").json() if x["id"] == ids["cal"])
    assert cal["pipeline_id"] is None and cal["pipeline_name"] is None
    assert c["admin"].get("/api/calendars").json()[0]["pipeline_name"] == "Secret"


@pytest.mark.parametrize("who", BLIND)
def test_custom_fields_do_not_name_a_hidden_pipeline(world, who):
    ids, c, _ = world
    field = next(f for f in c[who].get("/api/custom-fields").json() if f["id"] == ids["field"])
    assert field["pipeline_ids"] == [ids["open"]]
    assert c[who].get("/api/custom-fields", params={"pipeline_id": ids["secret"]}).json() == []
    admin_field = c["admin"].get("/api/custom-fields").json()[0]
    assert sorted(admin_field["pipeline_ids"]) == sorted([ids["open"], ids["secret"]])


@pytest.mark.parametrize("call", [
    lambda c, ids: c.patch("/api/pipelines/%d" % ids["secret"], json={"name": "Mine"}),
    lambda c, ids: c.post("/api/pipelines/%d/duplicate" % ids["secret"]),
    lambda c, ids: c.post("/api/pipelines/%d/stages" % ids["secret"], json={"name": "X"}),
    lambda c, ids: c.patch("/api/stages/%d" % ids["s1"], json={"name": "X"}),
    lambda c, ids: c.post("/api/pipelines/%d/stages/reorder" % ids["secret"],
                          json={"stage_ids": [ids["s2"], ids["s1"]]}),
])
def test_the_structure_of_a_hidden_pipeline_answers_404(world, call):
    ids, c, _ = world
    before = _snapshot()
    r = call(c["outsider"], ids)
    assert r.status_code == 404, r.text
    assert _snapshot() == before


def test_reordering_pipelines_needs_only_the_ones_you_can_see_and_keeps_hidden_slots(world):
    """A dispatcher reorders their own list without learning — from a refusal that
    names ids — that anything else exists. Hidden pipelines keep their slots."""
    ids, c, _ = world
    extra = c["admin"].post("/api/pipelines", json={"name": "Third"}).json()["id"]
    # Admin order: Open, Secret, Third. The outsider sees Open, Third.
    wrong = c["outsider"].post("/api/pipelines/reorder",
                               json={"pipeline_ids": [extra, ids["secret"], ids["open"]]})
    assert wrong.status_code == 400 and "Secret" not in wrong.text
    r = c["outsider"].post("/api/pipelines/reorder", json={"pipeline_ids": [extra, ids["open"]]})
    assert r.status_code == 200, r.text
    assert [p["name"] for p in r.json()] == ["Third", "Open"]
    everything = [p["name"] for p in c["admin"].get("/api/pipelines").json()]
    assert everything == ["Third", "Secret", "Open"]


def test_the_cli_goes_through_the_api_and_sees_only_what_the_token_may(world, monkeypatch):
    """`ghl` has no database access of its own; drive the real CLI against the real app."""
    ids, c, tokens = world
    from ghl_cli import client as client_mod
    from ghl_cli import output
    from ghl_cli.main import app as cli
    from typer.testing import CliRunner

    real_init = client_mod.Client.__init__

    def through_the_app(self, *a, **kw):
        real_init(self, *a, **kw)
        self._http = TestClient(app, base_url=self.base_url)

    monkeypatch.setattr(client_mod.Client, "__init__", through_the_app)
    monkeypatch.setenv("GHL_API_URL", "http://testserver")
    output.set_json(False)

    def run(who, *args):
        monkeypatch.setenv("GHL_API_TOKEN", tokens[who])
        return CliRunner().invoke(cli, ["--json", *args])

    blind = run("outsider", "opps", "pipelines")
    assert blind.exit_code == 0, blind.output
    assert "Secret" not in blind.output and "Open" in blind.output
    assert run("admin", "opps", "pipelines").output.count("Secret") >= 1
    shown = run("outsider", "opps", "show", str(ids["secret_deal"]))
    assert shown.exit_code == 4, shown.output
    assert "skylight" not in shown.output


# ---------------- the audit: every route that reads a deal ----------------

# Every route whose handler reads opportunities or pipelines, and how it enforces
# access. A route added later that touches either fails the test below until it is
# added here — which means somebody had to decide.
AUDITED = {
    "list_pipelines": "hidden pipelines skipped",
    "create_pipeline": "creates; nothing to hide",
    "update_pipeline": "pipeline_access.get_pipeline -> 404",
    "reorder_pipelines": "visible permutation only; hidden keep slots",
    "duplicate_pipeline": "pipeline_access.get_pipeline -> 404",
    "get_pipeline_permissions": "ADMIN (sees all)",
    "set_pipeline_permissions": "ADMIN (sees all)",
    "delete_pipeline": "ADMIN; get_pipeline + destination can_see",
    "create_stage": "pipeline_access.get_pipeline -> 404",
    "update_stage": "pipeline_access.get_stage -> 404",
    "delete_stage": "ADMIN; pipeline_access.get_stage -> 404",
    "reorder_stages": "pipeline_access.get_pipeline -> 404",
    "list_custom_fields": "hidden ids stripped",
    "create_custom_field": "ADMIN (sees all)",
    "update_custom_field": "ADMIN (sees all)",
    "list_opportunities": "visible_opportunities()",
    "move_opportunity": "pipeline_access.get_opportunity -> 404",
    "get_opportunity": "pipeline_access.get_opportunity -> 404",
    "update_opportunity": "pipeline_access.get_opportunity -> 404",
    "create_opportunity": "can_see -> same 400 as a wrong pair",
    "ingest_ahs_job": "ahs_jobs._board: can_see, a hidden AHS board is 'not found'",
    "ingest_ahs_cancellation": "ahs_jobs.deliver_cancellation: can_see -> no_card",
    "delete_opportunity": "ADMIN; pipeline_access.get_opportunity",
    "bulk_move_stage": "_bulk_load visible_opportunities + stage can_see",
    "bulk_assign_owner": "_bulk_load visible_opportunities",
    "get_contact": "_contact_detail(hidden)",
    "update_contact": "_contact_detail(hidden)",
    "add_tag": "_contact_detail(hidden)",
    "remove_tag": "_contact_detail(hidden)",
    "create_contact": "_contact_detail(hidden)",
    "delete_contact": "ADMIN (sees all)",
    "list_saved_views": "views on hidden pipelines skipped",
    "create_saved_view": "_check_view_pipeline can_see",
    "update_saved_view": "404 on hidden; _check_view_pipeline",
    "list_calendars": "pipeline link blanked",
    "dashboard": "_opportunities_in_range(hidden)",
    "dashboard_funnel": "hidden pipelines excluded; _opportunities_in_range(hidden)",
    "forecast": "pipeline_access.get_pipeline -> 404",
    "list_appointments": "_appointment_deal(hidden)",
    "get_appointment": "_appointment_detail(hidden)",
    "create_appointment": "_check_appointment_opportunity -> 404",
    "update_appointment": "_check_appointment_opportunity; _appointment_detail(hidden)",
    "report_calls": "won deals visible_opportunities()",
    "search": "_search_opportunities(hidden)",
    # CompanyCam (app/companycam_api.py, 2026-09-14): a hidden card has no photos.
    "companycam_projects_for_opportunity": "pipeline_access.get_opportunity -> 404",
    "companycam_project_photos": "pipeline_access.get_opportunity -> 404",
    "companycam_photo_image": "served only if linked to a card outside hidden_pipeline_ids",
    "companycam_projects_for_contact": "visible_opportunities(hidden)",
    "companycam_unlink": "ADMIN; pipeline_access.get_opportunity -> 404",
    "companycam_review_list": "ADMIN (sees all)",
    "companycam_review_link": "ADMIN (sees all)",
    # Zuper sync (app/zuper/api.py, 2026-09-16): a hidden card has no money panel or photos.
    "zuper_opportunity_panel": "pipeline_access.get_opportunity -> 404",
    "zuper_contact_panel": "assigned_access.opportunities(scope) excludes hidden pipelines",
    "zuper_opportunity_attachments": "_job_for -> pipeline_access.get_opportunity -> 404",
    "zuper_attachment_file": "_job_for -> pipeline_access.get_opportunity -> 404",
    "zuper_status": "ADMIN (sees all)", "zuper_restore_delete": "ADMIN (sees all)",
    "zuper_deletes": "ADMIN (sees all)", "zuper_conflicts": "ADMIN (sees all)",
    "zuper_send_opportunity": "pipeline_access.get_opportunity -> 404",
    "bulk_lead_outcome": "_bulk_load visible_opportunities",
    "report_lead_outcomes": "assigned_access.opportunities(scope) excludes hidden pipelines",
}

MARKERS = ("Opportunity", "Pipeline", "Stage", "_contact_detail", "_appointment_detail",
           "_appointment_deal", "_opportunities_in_range", "_search_opportunities",
           "_bulk_load", "_check_view_pipeline", "_check_pipelines", "_saved_view_public",
           "custom_fields.describe")


def _routes():
    stack, out = list(app.routes), []
    while stack:
        r = stack.pop()
        stack.extend(getattr(getattr(r, "original_router", None), "routes", []) or [])
        stack.extend(getattr(r, "routes", []) or [])
        if getattr(r, "endpoint", None) is not None:
            out.append(r)
    return out


def test_every_route_that_reads_a_deal_is_on_the_audited_list():
    touching = set()
    for r in _routes():
        fn = r.endpoint
        if getattr(fn, "__module__", "") not in (main_mod.__name__, companycam_mod.__name__,
                                                 zuper_api.__name__):
            continue
        src = inspect.getsource(fn)
        if any(m in src for m in MARKERS):
            touching.add(fn.__name__)
    unaudited = touching - set(AUDITED) - {
        # Read no deal and name no pipeline, whatever the source mentions.
        "archive_custom_field", "restore_custom_field", "reorder_custom_fields",
        "delete_saved_view", "report_appointments",
    }
    assert not unaudited, (
        "these routes read opportunities or pipelines and nobody has recorded how they "
        "enforce per-pipeline access: %s — see app/pipeline_access.py" % sorted(unaudited))


def test_every_audited_route_still_exists():
    names = {r.endpoint.__name__ for r in _routes()}
    assert set(AUDITED) <= names, sorted(set(AUDITED) - names)

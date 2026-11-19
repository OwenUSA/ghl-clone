"""Owen AI makes the technicians' routes (2026-11-19, app/owen_ai/routes.py).

Pinned, by behaviour:
  * plan_routes keeps a DRAFT plan made by the owner (source "owen_ai") with the SAME rules as
    the Dispatch page, and the plan link Claude must copy — and the page reads — is in the first
    600 characters, all the step log keeps;
  * the tools are on Owen AI's server;
  * set_crew_days saves an on-call technician's days (with how many jobs he takes) and a day
    off, the next plan follows them, and a wrong request saves nothing;
  * nothing is sent to Zuper (no request leaves; the plan is a CRM row).
"""
import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from app.dispatch import ai as dispatch_ai
from app.dispatch import config as c
from app.dispatch import crew
from app.models import DispatchJob, DispatchPlan, Role, User
from app.owen_ai import agent, routes, tools

NOW = datetime(2026, 11, 19, 18, 0, tzinfo=UTC)            # Thu
OWNER = "owner@dtr.test"


@pytest.fixture()
def owner(db, monkeypatch):
    monkeypatch.setenv("OWEN_AI_OWNER_EMAIL", OWNER)
    monkeypatch.setattr(routes, "_now", lambda: NOW)
    u = User(email=OWNER, name="Owen", role=Role.ADMIN)
    db.add(u)
    db.commit()
    return u


def _jobs(db):
    for n, status in ((921, c.SF_NEW), (922, c.SF_PROPOSAL)):
        db.add(DispatchJob(job_uid="u%d" % n, job_number=str(n), board=c.SIMPLE_FLOW_BOARD,
                           status=status, customer_name="C%d" % n, city="Weston",
                           lat=26.1, lng=-80.4, is_open=True,
                           zuper_created_at=NOW - timedelta(days=4),
                           fields={"Service address verified": "Yes"}))
    db.commit()


def test_the_tools_are_on_owen_ais_server():
    names = [n for n, *_ in tools.SPECS]
    for name in ("plan_routes", "crew_days", "set_crew_days"):
        assert name in names and "mcp__owen__" + name in tools.TOOL_NAMES
    schema = {n: s for n, _, s, _ in tools.SPECS}["plan_routes"]
    assert schema["properties"]["agreed"] == dispatch_ai.AGREED_SCHEMA


def test_a_plan_is_kept_for_the_owner_and_its_link_comes_first(db, owner):
    _jobs(db)
    text = routes.tool_plan_routes({"days": 1, "agreed": [
        {"job_number": "922", "source": "call", "quote": "yes, do the repair",
         "said_at": "2026-11-18"}]})
    plan = db.query(DispatchPlan).one()
    assert plan.source == "owen_ai" and plan.created_by_id == owner.id
    head = text[:agent.STEP_RESULT_MAX]
    assert head.startswith("Plan #%d saved (DRAFT" % plan.id)
    assert "[Plan #%d — map and list](/dispatch?tab=book&plan=%d)" % (plan.id, plan.id) in head
    assert "Placed 2 (1 inspections, 1 repairs; 0 CALL FIRST)" in head
    body = json.loads(text.split("\n", 2)[2])
    assert body["plan_id"] == plan.id and body["crew"] and body["who_works"]
    visits = [v for d in plan.result["days"] for t in d["techs"] for v in t["visits"]]
    assert {v["job_number"] for v in visits} == {"921", "922"}
    assert "yes, do the repair" in next(v for v in visits if v["job_number"] == "922")["agreed"]
    assert routes.tool_plan_routes({"boards": "everything"}).startswith("ERROR")


def test_the_crew_days_said_in_the_chat_are_kept_and_the_next_plan_follows_them(db, owner):
    _jobs(db)
    crew.save(db, crew.decided_crew("Marx Real"), None, NOW)
    friday = (NOW + timedelta(days=1)).date().isoformat()
    out = routes.tool_set_crew_days({"technician": "Owen", "working": [
        {"from": friday, "to": friday}], "max_per_day": 1})
    assert out.startswith("Saved for Owen Buzaglo"), out
    owen = crew.find(crew.roster(db), "Owen Buzaglo")
    assert owen["only"] == [{"from": friday, "to": friday, "max": 1}]
    assert routes.tool_set_crew_days({"technician": "Antonio", "off": [
        {"from": friday, "to": friday}]}).startswith("Saved for Antonio Brown")
    days = json.loads(routes.tool_crew_days({}))["next_14_days"]
    fri = next(d for d in days if d["date"] == friday)
    assert [w["name"] for w in fri["working"]] == ["Marx Real", "Owen Buzaglo"]
    # Friday: Antonio off, Owen takes one; Marx does repairs only -> the inspection is Owen's.
    routes.tool_plan_routes({"days": 1})
    plan = db.query(DispatchPlan).order_by(DispatchPlan.id.desc()).first()
    techs = {t["tech"]: t for d in plan.result["days"] for t in d["techs"]}
    assert "Antonio Brown" not in techs
    assert techs["Owen Buzaglo"]["capacity"] == 1
    assert [v["kind"] for v in techs["Owen Buzaglo"]["visits"]] == ["inspection"]
    assert all(v["kind"] == "repair" for v in techs["Marx Real"]["visits"])
    # Wrong requests save nothing.
    before = crew.roster(db)
    assert routes.tool_set_crew_days({"technician": "Marx", "working": [
        {"from": friday, "to": friday}]}).startswith("ERROR: nothing saved")
    assert routes.tool_set_crew_days({"technician": "Nobody", "off": [
        {"from": friday, "to": friday}]}).startswith("ERROR: No technician")
    assert routes.tool_set_crew_days({"technician": "Owen", "working": [
        {"from": "2026-01-02", "to": "2026-01-03"}]}).startswith("ERROR: nothing saved")
    assert crew.roster(db) == before


def test_the_answer_draws_the_plan_from_its_link():
    # The same address the page's PLAN_LINK accepts (tests/test_owen_ai_routes_ui.py).
    assert re.fullmatch(r"\[Plan #12 — map and list\]\(/dispatch\?tab=book&plan=12\)",
                        routes.plan_link(12))

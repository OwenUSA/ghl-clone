"""Each trigger fires ONCE, for the right subject, through the existing queue — and an
agent's own change does not wake agents."""
from datetime import UTC, datetime, timedelta

from app.db import SessionLocal
from app.models import AiRun, Job
from sqlalchemy import select
from tests.ai_support import anthropic_message, count, drain, make_agent, text, tool_use


def _runs():
    db = SessionLocal()
    try:
        return list(db.scalars(select(AiRun).order_by(AiRun.id)).all())
    finally:
        db.close()


def _event(world, **payload):
    r = world.client("admin").post("/api/events", json={
        "contact_id": world.ids["jane"], "direction": "INBOUND", **payload})
    assert r.status_code == 201, r.text
    return r.json()


def test_unanswered_inbound_call_fires_once_for_that_contact(world):
    aid = make_agent(world, triggers=[{"type": "missed_call"}])
    ev = _event(world, type="CALL", duration_seconds=0, call_status="no-answer",
                dedupe_key="call-1")
    _event(world, type="CALL", duration_seconds=0, call_status="no-answer",
           dedupe_key="call-1")                                    # a repeat delivery
    _event(world, type="CALL", duration_seconds=240, call_status="completed")  # answered
    runs = _runs()
    assert len(runs) == 1
    assert (runs[0].agent_id, runs[0].trigger, runs[0].contact_id, runs[0].opportunity_id) == (
        aid, "missed_call", world.ids["jane"], None)
    assert runs[0].trigger_ref == "event:%s" % ev["id"]
    assert count(Job, Job.type == "ai_agent_run") == 1


def test_a_backfilled_missed_call_does_not_fire(world):
    make_agent(world, triggers=[{"type": "missed_call"}])
    old = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    _event(world, type="CALL", duration_seconds=0, call_status="no-answer", occurred_at=old)
    assert _runs() == []


def test_inbound_text_waits_its_minutes_and_one_follow_up_per_customer(world):
    make_agent(world, triggers=[{"type": "inbound_text", "minutes": 20}], wait_minutes=5)
    _event(world, type="SMS", body="hi")
    _event(world, type="SMS", body="hello??")
    runs = _runs()
    assert len(runs) == 1 and runs[0].trigger == "inbound_text"
    job = SessionLocal().scalar(select(Job).where(Job.type == "ai_agent_run"))
    wait = job.run_after.replace(tzinfo=UTC) - datetime.now(UTC)
    assert timedelta(minutes=19) < wait <= timedelta(minutes=20)   # the longer of the two


def test_a_text_answered_before_the_wait_ends_is_skipped(world, script):
    make_agent(world, triggers=[{"type": "inbound_text", "minutes": 0}],
               sleep_on_staff_reply=False)
    _event(world, type="SMS", body="hi")
    # A dispatcher answers straight from the thread; this agent does not sleep on replies,
    # but a run that finds the customer already answered still stands down.
    world.client("dispatcher").post("/api/contacts/%s/messages" % world.ids["jane"],
                                    json={"body": "Hi Jane!"})
    drain()
    run = _runs()[0]
    assert run.outcome == "skipped" and "already answered" in run.reason
    assert script.calls == 0


def test_opportunity_enters_stage_fires_once_with_the_deal_as_subject(world):
    aid = make_agent(world, triggers=[{"type": "stage_entered",
                                       "pipeline_id": world.ids["pipeline"],
                                       "stage_id": world.ids["s2"]}])
    dispatcher = world.client("dispatcher")
    dispatcher.patch("/api/opportunities/%s" % world.ids["deal"],
                     json={"stage_id": world.ids["s3"], "position": 0})   # another stage
    assert _runs() == []
    dispatcher.patch("/api/opportunities/%s" % world.ids["deal"],
                     json={"stage_id": world.ids["s2"], "position": 0})
    dispatcher.patch("/api/opportunities/%s" % world.ids["deal"],
                     json={"stage_id": world.ids["s2"], "position": 0})   # unchanged
    runs = _runs()
    assert len(runs) == 1
    assert (runs[0].agent_id, runs[0].contact_id, runs[0].opportunity_id) == (
        aid, world.ids["jane"], world.ids["deal"])


def test_appointment_booked_rescheduled_cancelled_each_fire_once(world):
    make_agent(world, triggers=[{"type": "appointment_booked"},
                                {"type": "appointment_rescheduled"},
                                {"type": "appointment_cancelled"}])
    admin = world.client("admin")
    start = datetime.now(UTC) + timedelta(days=3)
    r = admin.post("/api/appointments", json={
        "title": "Inspection", "contact_id": world.ids["jane"],
        "opportunity_id": world.ids["deal"], "calendar_id": world.ids["calendar"],
        "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=1)).isoformat()})
    appt = r.json()["id"]
    admin.patch("/api/appointments/%s" % appt, json={"title": "Roof inspection"})  # no move
    later = start + timedelta(days=1)
    admin.patch("/api/appointments/%s" % appt, json={
        "starts_at": later.isoformat(), "ends_at": (later + timedelta(hours=1)).isoformat()})
    admin.delete("/api/appointments/%s" % appt)
    admin.delete("/api/appointments/%s" % appt)                                  # again
    runs = _runs()
    assert [r.trigger for r in runs] == ["appointment_booked", "appointment_rescheduled",
                                         "appointment_cancelled"]
    assert {(r.contact_id, r.opportunity_id, r.appointment_id) for r in runs} == {
        (world.ids["jane"], world.ids["deal"], appt)}


def test_manual_run_needs_the_manual_trigger_and_a_subject(world):
    aid = make_agent(world, triggers=[{"type": "missed_call"}])
    r = world.client("dispatcher").post("/api/ai/agents/%s/run" % aid,
                                        json={"contact_id": world.ids["jane"]})
    assert r.status_code == 400 and "Manual" in r.json()["detail"]
    assert _runs() == []
    aid2 = make_agent(world, name="Manual one")
    r = world.client("dispatcher").post("/api/ai/agents/%s/run" % aid2, json={})
    assert r.status_code == 400
    r = world.client("dispatcher").post("/api/ai/agents/%s/run" % aid2,
                                        json={"opportunity_id": world.ids["deal"]})
    assert r.status_code == 202
    run = _runs()[0]
    assert (run.trigger, run.contact_id, run.opportunity_id, run.created_by_id) == (
        "manual", world.ids["jane"], world.ids["deal"], world.ids["user_dispatcher"])


def test_an_agents_own_stage_move_does_not_trigger_agents(world, script):
    make_agent(world, name="Mover", actions=["get_context", "move_stage"])
    make_agent(world, name="Watcher", triggers=[{"type": "stage_entered",
                                                "pipeline_id": world.ids["pipeline"],
                                                "stage_id": world.ids["s2"]}])
    script.responses.extend([
        anthropic_message([tool_use("move_stage", {"stage_id": world.ids["s2"]})], "tool_use"),
        anthropic_message([text("moved")]),
    ])
    mover = next(a for a in world.client("admin").get("/api/ai/agents").json()
                 if a["name"] == "Mover")
    world.client("dispatcher").post("/api/ai/agents/%s/run" % mover["id"],
                                    json={"opportunity_id": world.ids["deal"]})
    drain()
    triggers = [r.trigger for r in _runs()]
    assert triggers == ["manual"]

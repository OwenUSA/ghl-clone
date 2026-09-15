"""The engine, asserted on the database and on what reached the (mocked) provider.

Every "it did not happen" is a re-read: the stage still reads the old id, the thread holds no
new event, the jobs table holds no new job, no contact was created.
"""
from datetime import UTC, datetime, timedelta

from app.ai import engine, triggers
from app.db import SessionLocal
from app.models import (
    AiAgent,
    AiAgentThread,
    AiAgentVersion,
    AiRun,
    AiRunStep,
    AiSettings,
    AiSuggestion,
    Contact,
    ConversationEvent,
    Job,
    NumberThread,
    Opportunity,
    OpportunityNote,
    PipelinePermission,
)
from sqlalchemy import select
from tests.ai_support import (
    anthropic_message,
    count,
    drain,
    get,
    make_agent,
    text,
    tool_use,
)


def _manual(world, aid, **body):
    r = world.client("dispatcher").post("/api/ai/agents/%s/run" % aid,
                                        json=body or {"opportunity_id": world.ids["deal"]})
    return r


def _run(run_id) -> AiRun:
    return get(AiRun, run_id)


def _steps(run_id, kind=None):
    db = SessionLocal()
    try:
        stmt = select(AiRunStep).where(AiRunStep.run_id == run_id)
        if kind:
            stmt = stmt.where(AiRunStep.kind == kind)
        return list(db.scalars(stmt.order_by(AiRunStep.position)).all())
    finally:
        db.close()


def _events(contact_id):
    from app.models import Conversation
    db = SessionLocal()
    try:
        conv = db.scalar(select(Conversation).where(Conversation.contact_id == contact_id))
        if conv is None:
            return []
        return list(db.scalars(select(ConversationEvent).where(
            ConversationEvent.conversation_id == conv.id)).all())
    finally:
        db.close()


# ------------------------------------------------------------------ nothing by itself

def test_every_agent_is_created_off_and_off_never_triggers_or_calls(world, script):
    aid = make_agent(world, mode="off")
    assert get(AiAgent, aid).mode == "off"
    r = _manual(world, aid)
    assert r.status_code == 400 and "Off" in r.json()["detail"]
    # A stage move with an Off agent listening: no run, no job, no request.
    make_agent(world, mode="off", name="Stage watcher",
               triggers=[{"type": "stage_entered", "pipeline_id": world.ids["pipeline"],
                          "stage_id": world.ids["s2"]}])
    world.client("admin").patch("/api/opportunities/%s" % world.ids["deal"],
                                json={"stage_id": world.ids["s2"], "position": 0})
    assert count(AiRun) == 0 and count(Job, Job.type == "ai_agent_run") == 0
    drain()
    assert script.calls == 0


def test_an_agent_switched_off_after_queueing_skips_without_a_model_call(world, script):
    aid = make_agent(world)
    run_id = _manual(world, aid).json()["run_id"]
    world.client("dispatcher").post("/api/ai/agents/%s/mode" % aid, json={"mode": "off"})
    drain()
    run = _run(run_id)
    assert run.outcome == "skipped" and run.reason == "the agent is Off"
    assert script.calls == 0


def test_global_pause_blocks_every_run(world, script):
    aid = make_agent(world)
    queued = _manual(world, aid).json()["run_id"]
    assert world.client("admin").put("/api/ai/settings", json={"paused": True}).json()["paused"]
    drain()
    assert _run(queued).outcome == "skipped" and "paused" in _run(queued).reason
    # A trigger while paused is logged, cheaply, and queues nothing.
    later = _manual(world, aid).json()
    assert later["outcome"] == "skipped" and "paused" in later["reason"]
    assert count(Job, Job.type == "ai_agent_run", Job.status == "pending") == 0
    # And a dispatcher cannot lift the pause.
    assert world.client("dispatcher").put("/api/ai/settings",
                                          json={"paused": False}).status_code == 403
    assert get(AiSettings, 1).paused is True
    assert script.calls == 0


def test_outside_schedule_skips_and_is_logged_without_a_model_call(world, script, monkeypatch):
    aid = make_agent(world, schedule={"enabled": True, "days": ["mon"], "start": "09:00",
                                      "end": "10:00"})
    # Sunday 13 Sep 2026, noon in Florida.
    monkeypatch.setattr(engine, "utcnow", lambda: datetime(2026, 9, 13, 16, 0, tzinfo=UTC))
    run_id = _manual(world, aid).json()["run_id"]
    drain()
    assert _run(run_id).outcome == "skipped"
    assert _run(run_id).reason == "outside the agent's schedule"
    assert script.calls == 0
    sched = {"enabled": True, "days": ["mon"], "start": "09:00", "end": "10:00"}
    assert engine.in_schedule(sched, datetime(2026, 9, 14, 13, 30, tzinfo=UTC))  # Mon 9:30 EDT
    assert not engine.in_schedule(sched, datetime(2026, 9, 14, 14, 0, tzinfo=UTC))  # 10:00
    assert not engine.in_schedule(sched, datetime(2026, 12, 14, 13, 30, tzinfo=UTC))  # 8:30 EST


# ------------------------------------------------------------------ auto-pilot

def test_auto_pilot_executes_through_the_staff_path_and_records_it(world, script):
    aid = make_agent(world, actions=["get_context", "send_text", "move_stage", "add_note"])
    script.responses.extend([
        anthropic_message([tool_use("get_context", {})], "tool_use"),
        anthropic_message([tool_use("move_stage", {"stage_id": world.ids["s2"]}, "t2"),
                           tool_use("send_text", {"body": "We can come Tuesday."}, "t3"),
                           tool_use("add_note", {"body": "Customer wants Tuesday."}, "t4")],
                          "tool_use"),
        anthropic_message([text("Moved to Inspection and texted Jane.")]),
    ])
    run_id = _manual(world, aid).json()["run_id"]
    drain()
    run = _run(run_id)
    assert run.outcome == "completed", run.reason
    assert get(Opportunity, world.ids["deal"]).stage_id == world.ids["s2"]
    sms = [e for e in _events(world.ids["jane"]) if e.type.value == "SMS"]
    by_agent = [e for e in sms if e.ai_agent_id == aid]
    assert [e.body for e in by_agent] == ["We can come Tuesday."]
    assert by_agent[0].delivery_status.value == "LOGGED_ONLY"      # the existing transport
    # Rule 4 fired exactly as for a staff drag: its stage-change text went out (drained),
    # written by the automation, not by the agent.
    assert count(Job, Job.type == "stage_change_notify") == 1
    assert [e.ai_agent_id for e in sms if e not in by_agent] == [None]
    note = SessionLocal().scalar(select(OpportunityNote))
    assert note.ai_agent_id == aid and note.created_by_id is None
    notes = world.client("admin").get("/api/opportunities/%s/notes" % world.ids["deal"]).json()
    assert notes["notes"][0]["created_by"] == "AI: Follow-up"
    thread = SessionLocal().scalar(select(AiAgentThread))
    assert thread.messages_sent == 1
    # The run records what it ran on, what it used and what it cost.
    version = SessionLocal().scalar(select(AiAgentVersion).where(AiAgentVersion.agent_id == aid))
    assert (run.version_id, run.version, run.model, run.provider) == (
        version.id, 1, "claude-sonnet-5", "anthropic")
    assert run.input_tokens == 300 and run.output_tokens == 60
    assert run.cost_micros == 300 * 2 + 60 * 10
    executed = [s for s in _steps(run_id, "action") if s.action_status == "executed"]
    assert [s.tool_name for s in executed] == ["move_stage", "send_text", "add_note"]
    # The request carried the compiled prompt as a cached system block and the tools.
    first = script.requests[0]["body"]
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    # Catalogue order, whatever order they were ticked in: the tool list is stable.
    assert [t["name"] for t in first["tools"]] == ["get_context", "send_text", "add_note",
                                                   "move_stage"]
    # And the tool_result loop round-tripped: the second request answered the first call.
    second = script.requests[1]["body"]["messages"]
    assert second[-1]["content"][0]["type"] == "tool_result"
    assert second[-1]["content"][0]["tool_use_id"] == "toolu_get_context"
    assert "Jane" in second[-1]["content"][0]["content"]
    # The provider key reached the provider's header and nowhere in the run's log.
    from tests.ai_support import SECRET_KEY
    assert script.requests[0]["headers"]["x-api-key"] == SECRET_KEY
    logged = " ".join(str((s.text, s.data)) for s in _steps(run_id))
    assert SECRET_KEY not in logged and SECRET_KEY not in str(vars(run))
    detail = world.client("admin").get("/api/ai/runs/%s" % run_id).text
    assert SECRET_KEY not in detail


def test_a_model_asking_to_touch_another_customer_is_refused_and_nothing_mutates(world, script):
    aid = make_agent(world, actions=["get_context", "move_stage", "add_note"])
    script.responses.extend([
        anthropic_message([
            tool_use("move_stage", {"stage_id": world.ids["s2"],
                                    "opportunity_id": world.ids["other_deal"]}, "a"),
            tool_use("add_note", {"body": "x", "opportunity_id": world.ids["other_deal"]}, "b"),
            tool_use("move_stage", {"stage_id": world.ids["s2"], "contact_id": world.ids["bob"]},
                     "c")], "tool_use"),
        anthropic_message([text("I could not do that.")]),
    ])
    run_id = _manual(world, aid).json()["run_id"]
    drain()
    assert get(Opportunity, world.ids["other_deal"]).stage_id == world.ids["s1"]
    assert get(Opportunity, world.ids["deal"]).stage_id == world.ids["s1"]
    assert count(OpportunityNote) == 0 and count(Job, Job.type == "stage_change_notify") == 0
    refused = [s for s in _steps(run_id, "action") if s.action_status == "refused"]
    assert len(refused) == 3
    assert "not this run's opportunity" in refused[0].text
    assert "not an argument of this tool: contact_id" in refused[2].text
    results = script.requests[1]["body"]["messages"][-1]["content"]
    assert all(r.get("is_error") for r in results)


def test_auto_pilot_respects_pipeline_access(world, script):
    """A pipeline restricted to named users is hidden from every agent."""
    db = SessionLocal()
    db.add(PipelinePermission(pipeline_id=world.ids["pipeline"], user_id=world.ids["user_admin"]))
    db.commit()
    db.close()
    aid = make_agent(world, actions=["get_context", "move_stage"])
    script.responses.extend([
        anthropic_message([tool_use("move_stage", {"stage_id": world.ids["s2"]})], "tool_use"),
        anthropic_message([text("done")]),
    ])
    db = SessionLocal()
    agent = db.get(AiAgent, aid)
    run_id = engine.new_run(db, agent, "manual", contact_id=world.ids["jane"]).id
    db.commit()
    engine.execute_run(db, run_id)
    db.close()
    assert get(Opportunity, world.ids["deal"]).stage_id == world.ids["s1"]
    refused = [s for s in _steps(run_id, "action") if s.action_status == "refused"]
    assert refused and "no open opportunity" in refused[0].text


def test_invalid_tool_json_from_openai_is_refused_not_run(world, script):
    from tests.ai_support import openai_call, openai_completion
    r = world.client("admin").post("/api/ai/connections", json={
        "name": "GPT", "provider": "openai", "api_key": "sk-proj-0123456789abcdef",
        "default_model": "gpt-5-mini"})
    conn = r.json()["id"]
    aid = make_agent(world, connection_id=conn, model="gpt-5-mini",
                     actions=["get_context", "move_stage"])
    script.responses.extend([
        openai_completion(tool_calls=[openai_call("move_stage", '{"stage_id": 2,,}')],
                          finish="tool_calls", cached=40),
        openai_completion(content="Sorry."),
    ])
    run_id = _manual(world, aid).json()["run_id"]
    drain()
    assert get(Opportunity, world.ids["deal"]).stage_id == world.ids["s1"]
    refused = [s for s in _steps(run_id, "action") if s.action_status == "refused"]
    assert refused[0].text == "the tool arguments were not valid JSON"
    follow_up = script.requests[1]["body"]["messages"]
    assert follow_up[-1]["role"] == "tool" and follow_up[-1]["tool_call_id"] == "call_1"
    assert "Refused" in follow_up[-1]["content"]
    run = _run(run_id)
    assert run.outcome == "completed"
    assert (run.input_tokens, run.cache_read_tokens, run.output_tokens) == (160, 40, 40)
    assert run.cost_micros is None        # no price on this connection: unknown, not zero


def test_refusal_and_max_tokens_end_the_run_without_running_tools(world, script):
    aid = make_agent(world, actions=["get_context", "move_stage"])
    script.responses.append(anthropic_message([text("I can't help with that.")], "refusal"))
    refused = _manual(world, aid).json()["run_id"]
    drain()
    assert _run(refused).outcome == "refused"
    script.responses.append(anthropic_message(
        [tool_use("move_stage", {"stage_id": world.ids["s2"]})], "max_tokens"))
    cut = _manual(world, aid).json()["run_id"]
    drain()
    assert _run(cut).outcome == "error" and "cut off" in _run(cut).reason
    assert get(Opportunity, world.ids["deal"]).stage_id == world.ids["s1"]


def test_openai_refusal_field_is_a_refusal(world, script):
    from tests.ai_support import openai_completion
    conn = world.client("admin").post("/api/ai/connections", json={
        "name": "GPT", "provider": "openai", "api_key": "sk-proj-0123456789abcdef",
        "default_model": "gpt-5-mini", "price_input": "0.25", "price_output": "2"}).json()["id"]
    aid = make_agent(world, connection_id=conn, model="gpt-5-mini")
    script.responses.append(openai_completion(refusal="I can't assist with that."))
    run_id = _manual(world, aid).json()["run_id"]
    drain()
    assert _run(run_id).outcome == "refused"
    assert _run(run_id).cost_micros == 100 * 0.25 + 20 * 2


# ------------------------------------------------------------------ suggest

def test_suggest_never_executes_and_approving_executes_exactly_once(world, script):
    aid = make_agent(world, mode="suggest", actions=["get_context", "send_text", "move_stage"])
    script.responses.extend([
        anthropic_message([tool_use("move_stage", {"stage_id": world.ids["s2"]}, "a"),
                           tool_use("send_text", {"body": "Tuesday works"}, "b")], "tool_use"),
        anthropic_message([text("Suggested.")]),
    ])
    run_id = _manual(world, aid).json()["run_id"]
    drain()
    assert _run(run_id).outcome == "completed"
    assert get(Opportunity, world.ids["deal"]).stage_id == world.ids["s1"]
    assert not [e for e in _events(world.ids["jane"]) if e.type.value == "SMS"]
    assert count(Job, Job.type == "stage_change_notify") == 0
    listed = world.client("dispatcher").get(
        "/api/ai/suggestions?contact_id=%s" % world.ids["jane"]).json()
    assert [s["action"] for s in listed] == ["send_text", "move_stage"]
    move = next(s for s in listed if s["action"] == "move_stage")
    text_s = next(s for s in listed if s["action"] == "send_text")

    ok = world.client("dispatcher").post("/api/ai/suggestions/%s/approve" % move["id"])
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    again = world.client("admin").post("/api/ai/suggestions/%s/approve" % move["id"])
    assert again.status_code == 409
    assert get(Opportunity, world.ids["deal"]).stage_id == world.ids["s2"]
    assert count(Job, Job.type == "stage_change_notify") == 1        # once, not twice

    gone = world.client("dispatcher").post("/api/ai/suggestions/%s/dismiss" % text_s["id"])
    assert gone.json()["status"] == "dismissed"
    assert world.client("dispatcher").post(
        "/api/ai/suggestions/%s/approve" % text_s["id"]).status_code == 409
    assert not [e for e in _events(world.ids["jane"]) if e.type.value == "SMS"]
    statuses = [s.action_status for s in _steps(run_id, "action")]
    assert statuses == ["suggested", "suggested", "executed"]


def test_a_tech_cannot_approve(world, script):
    aid = make_agent(world, mode="suggest", actions=["get_context", "move_stage"])
    script.responses.extend([
        anthropic_message([tool_use("move_stage", {"stage_id": world.ids["s2"]})], "tool_use"),
        anthropic_message([text("ok")])])
    _manual(world, aid)
    drain()
    sid = SessionLocal().scalar(select(AiSuggestion.id))
    for who in ("tech", "restricted"):
        assert world.client(who).post("/api/ai/suggestions/%s/approve" % sid).status_code == 403
    assert get(AiSuggestion, sid).status == "pending"
    assert get(Opportunity, world.ids["deal"]).stage_id == world.ids["s1"]


# ------------------------------------------------------------------ try-it

def test_try_it_runs_reads_for_real_and_never_writes(world, script):
    aid = make_agent(world, publish=False, mode="off",
                     actions=["get_context", "send_text", "move_stage", "book_appointment"])
    script.responses.extend([
        anthropic_message([tool_use("get_context", {})], "tool_use"),
        anthropic_message([tool_use("move_stage", {"stage_id": world.ids["s2"]}, "m"),
                           tool_use("send_text", {"body": "hi"}, "s"),
                           tool_use("book_appointment", {"starts_at": "2030-01-08T10:00",
                                                         "calendar_id": world.ids["calendar"]},
                                    "b")], "tool_use"),
        anthropic_message([text("I'd book you for Tuesday.")]),
    ])
    jobs_before = count(Job)
    r = world.client("admin").post("/api/ai/agents/%s/try" % aid, json={
        "messages": [{"role": "user", "content": "Can you come look at my roof?"}],
        "opportunity_id": world.ids["deal"]})
    body = r.json()
    assert r.status_code == 200, body
    assert body["reply"] == "I'd book you for Tuesday."
    assert [w["action"] for w in body["would"]] == ["move_stage", "send_text", "book_appointment"]
    assert body["would"][0]["summary"] == "Move “Jane roof” to stage “Inspection”"
    assert get(Opportunity, world.ids["deal"]).stage_id == world.ids["s1"]
    assert _events(world.ids["jane"]) == []
    from app.models import Appointment
    assert count(Appointment) == 0 and count(Job) == jobs_before
    run = _run(body["run_id"])
    assert run.is_test and run.version_id is None and run.mode == "test"
    context = script.requests[1]["body"]["messages"][-1]["content"][0]["content"]
    assert "Jane roof" in context                                   # the read ran for real


def test_try_it_is_admin_only(world):
    aid = make_agent(world, publish=False, mode="off")
    r = world.client("dispatcher").post("/api/ai/agents/%s/try" % aid, json={
        "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 403 and count(AiRun) == 0


# ------------------------------------------------------------------ versions

def test_publishing_freezes_a_version_and_runs_record_it(world, script):
    aid = make_agent(world, persona="Version one persona")
    admin = world.client("admin")
    draft = admin.get("/api/ai/agents/%s" % aid).json()["draft"]
    admin.patch("/api/ai/agents/%s" % aid, json={"draft": {**draft, "persona": "Draft two"}})
    script.responses.append(anthropic_message([text("hello")]))
    first = _manual(world, aid).json()["run_id"]
    drain()
    assert "Version one persona" in script.requests[0]["body"]["system"][0]["text"]
    assert _run(first).version == 1
    v1 = SessionLocal().scalar(select(AiAgentVersion).where(AiAgentVersion.version == 1))
    frozen = dict(v1.config)
    admin.post("/api/ai/agents/%s/publish" % aid)
    assert dict(get(AiAgentVersion, v1.id).config) == frozen      # v1 did not move
    script.responses.append(anthropic_message([text("hello")]))
    second = _manual(world, aid).json()["run_id"]
    drain()
    assert "Draft two" in script.requests[1]["body"]["system"][0]["text"]
    assert _run(second).version == 2


def test_the_compiled_prompt_is_deterministic_and_has_no_timestamp(world):
    from app.ai.config import merged
    from app.ai.prompt import compile_prompt
    aid = make_agent(world, publish=False, mode="off", goals=["B", "A"])
    config = merged(get(AiAgent, aid).draft)
    one, two = compile_prompt(config, "X"), compile_prompt(dict(config), "X")
    assert one == two
    assert "2026" not in one and "Current time" not in one
    assert one.index("1. B") < one.index("2. A")
    preview = world.client("dispatcher").get("/api/ai/agents/%s" % aid).json()
    assert preview["compiled_prompt"] == compile_prompt(config, "Follow-up")


# ------------------------------------------------------------------ sleep & max messages

def test_a_staff_reply_puts_the_agent_to_sleep_and_cancels_its_follow_up(world, script):
    aid = make_agent(world, actions=["get_context", "send_text"],
                     triggers=[{"type": "inbound_text", "minutes": 30}])
    ev = world.client("admin").post("/api/events", json={
        "contact_id": world.ids["jane"], "type": "SMS", "direction": "INBOUND",
        "body": "Is anyone there?"})
    assert ev.status_code == 201
    job = SessionLocal().scalar(select(Job).where(Job.type == "ai_agent_run"))
    key = job.dedupe_key
    assert key and job.status == "pending"
    # A dispatcher answers the customer from the composer.
    world.client("dispatcher").post("/api/contacts/%s/messages" % world.ids["jane"],
                                    json={"body": "Yes! Calling you now."})
    job = get(Job, job.id)
    assert job.status == "cancelled" and job.dedupe_key is None
    assert job.payload["superseded_key"] == key                      # released, trail kept
    run = SessionLocal().scalar(select(AiRun))
    assert run.outcome == "skipped" and run.reason.startswith("staff replied")
    state = SessionLocal().scalar(select(AiAgentThread))
    assert state.asleep_at is not None
    # The next text does not wake it: the run is logged as skipped, with no model call.
    world.client("admin").post("/api/events", json={
        "contact_id": world.ids["jane"], "type": "SMS", "direction": "INBOUND", "body": "ok"})
    db = SessionLocal()
    for j in db.scalars(select(Job).where(Job.type == "ai_agent_run",
                                          Job.status == "pending")).all():
        j.run_after = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    drain()
    latest = SessionLocal().scalar(select(AiRun).order_by(AiRun.id.desc()))
    assert latest.outcome == "skipped" and latest.reason.startswith("asleep")
    assert script.calls == 0
    # An admin can wake it.
    world.client("admin").post("/api/ai/agents/%s/wake" % aid,
                               json={"contact_id": world.ids["jane"]})
    assert get(AiAgentThread, state.id).asleep_at is None


def test_max_messages_stops_the_agent(world, script):
    aid = make_agent(world, actions=["get_context", "send_text"], max_messages=1,
                     sleep_on_staff_reply=False)
    script.responses.extend([
        anthropic_message([tool_use("send_text", {"body": "one"}, "1"),
                           tool_use("send_text", {"body": "two"}, "2")], "tool_use"),
        anthropic_message([text("sent")]),
    ])
    first = _manual(world, aid).json()["run_id"]
    drain()
    sms = [e for e in _events(world.ids["jane"]) if e.type.value == "SMS"]
    assert [e.body for e in sms] == ["one"]
    refused = [s for s in _steps(first, "action") if s.action_status == "refused"]
    assert "maximum" in refused[0].text
    second = _manual(world, aid).json()["run_id"]
    drain()
    assert _run(second).outcome == "skipped" and "maximum" in _run(second).reason
    assert script.calls == 2


# ------------------------------------------------------------------ never a contact

def test_a_number_no_contact_holds_never_triggers_and_nothing_is_created(world, script):
    make_agent(world, triggers=[{"type": "inbound_text", "minutes": 0},
                                {"type": "missed_call"}])
    contacts = count(Contact)
    for payload in ({"type": "SMS", "body": "hello?"},
                    {"type": "CALL", "duration_seconds": 0, "call_status": "no-answer"}):
        r = world.client("admin").post("/api/events", json={
            "from_number": "+13055550199", "direction": "INBOUND", **payload})
        assert r.status_code == 201
    assert count(NumberThread) == 1
    assert count(Contact) == contacts and count(Opportunity) == 2
    assert count(AiRun) == 0 and count(Job, Job.type == "ai_agent_run") == 0
    drain()
    assert script.calls == 0


def test_escalation_creates_an_urgent_task_an_alert_and_a_dark_on_call_text(world, script):
    from app.models import AiAlert, OpportunityTask
    world.client("admin").put("/api/ai/settings", json={"on_call_phone": "(941) 555-0999"})
    aid = make_agent(world, actions=["get_context", "escalate"],
                     escalation_user_ids=[world.ids["user_dispatcher"]])
    script.responses.extend([
        anthropic_message([tool_use("escalate", {"reason": "Water pouring through ceiling",
                                                 "emergency": True})], "tool_use"),
        anthropic_message([text("Escalated.")]),
    ])
    run_id = _manual(world, aid).json()["run_id"]
    drain()
    assert _run(run_id).outcome == "escalated"
    task = SessionLocal().scalar(select(OpportunityTask))
    assert task.priority == "urgent" and task.assigned_user_id == world.ids["user_dispatcher"]
    assert task.ai_agent_id == aid and task.opportunity_id == world.ids["deal"]
    alerts = world.client("dispatcher").get("/api/ai/alerts").json()
    assert alerts["unread"] == 1 and alerts["items"][0]["urgent"]
    assert world.client("admin").get("/api/ai/alerts").json()["unread"] == 0
    assert count(AiAlert) == 1
    action = next(s for s in _steps(run_id, "action") if s.action_status == "executed")
    assert action.data["result"]["on_call_text"]["status"] == "LOGGED_ONLY"
    note = [e for e in _events(world.ids["jane"]) if e.type.value == "NOTE"]
    assert note and "escalated" in note[0].body


def test_a_trigger_failure_never_breaks_the_staff_request(world, monkeypatch):
    make_agent(world, triggers=[{"type": "stage_entered", "pipeline_id": world.ids["pipeline"],
                                 "stage_id": world.ids["s2"]}])

    def boom(*a, **k):
        raise RuntimeError("trigger exploded")

    monkeypatch.setattr(triggers, "_live_agents", boom)
    r = world.client("dispatcher").patch("/api/opportunities/%s" % world.ids["deal"],
                                         json={"stage_id": world.ids["s2"], "position": 0})
    assert r.status_code == 200
    assert get(Opportunity, world.ids["deal"]).stage_id == world.ids["s2"]

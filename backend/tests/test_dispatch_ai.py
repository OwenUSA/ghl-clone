"""The Dispatch page, phase 2 (2026-09-30): booking slots, the AI's explanations, suggestions,
the chat, the settings.

What is pinned, by behaviour:
  * **Booking** offers the preferred technician, next to what he already has, never on top of a
    visit, never past his daily maximum, never on a Sunday.
  * **The AI is OFF until an ADMIN switches it on with a connection** — and while off, paused
    ("Pause all AI agents") or over the daily cap, NO request reaches the provider.
  * **An explanation** lands on the item; a field change becomes a suggestion only for a field
    the job really has (or the job address / a note) — an invented field is dropped.
  * **Suggestions are never applied here.** Approve / Wrong are recorded; one closes itself
    when Zuper shows the value.
  * **The chat** reads through tools, never sees a board hidden from the user, and can only
    RECORD a suggestion.
The provider is mocked at the HTTP boundary (tests/ai_support.py); nothing leaves the process.
"""
import json
from datetime import UTC, datetime, time, timedelta

import pytest
from app.ai import vault
from app.auth import mint_api_token
from app.dispatch import ai, booking, service
from app.dispatch import config as c
from app.main import app
from app.models import (
    AiConnection,
    AiSettings,
    DispatchAiRun,
    DispatchItem,
    DispatchJob,
    DispatchSettings,
    DispatchSuggestion,
    Pipeline,
    PipelinePermission,
    Role,
    User,
)
from fastapi.testclient import TestClient
from tests.ai_support import openai_call, openai_completion

NOW = datetime(2026, 9, 30, 18, 0, tzinfo=UTC)            # Wed 2 PM New York
WESTON = (26.10, -80.40)
NEAR_WESTON = (26.11, -80.39)
HOMESTEAD = (25.47, -80.48)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """A New York time on October `day`, 2026."""
    return datetime(2026, 10, day, hour, minute, tzinfo=c.TZ)


# ---- booking (pure) ------------------------------------------------------------------------

def test_a_repair_goes_to_antonio_next_to_his_visit_in_the_same_area():
    visits = [booking.Visit("Antonio Brown", at(2, 7, 30), at(2, 10), *WESTON, "#273 Weston"),
              booking.Visit("Antonio Brown", at(1, 7, 30), at(1, 10), *HOMESTEAD, "#274 Homestead")]
    got = booking.slots(lat=NEAR_WESTON[0], lng=NEAR_WESTON[1], kind="repair", visits=visits,
                        now=NOW)
    assert len(got) == 3
    best = got[0]
    assert best.tech == "Antonio Brown"
    assert c.local(best.start).date().isoformat() == "2026-10-02"
    assert best.next_to == "#273 Weston"
    assert best.start >= at(2, 10)                      # after his 7:30-10:00 visit, not over it
    assert best.extra_drive_minutes < 15


def test_an_inspection_prefers_owen_and_a_full_day_is_skipped():
    full = [booking.Visit("Owen Buzaglo", at(1, 9 + i), at(1, 10 + i), *WESTON, "#%d" % i)
            for i in range(5)]
    got = booking.slots(lat=WESTON[0], lng=WESTON[1], kind="inspection", visits=full, now=NOW)
    assert got[0].tech == "Owen Buzaglo"
    assert all(not (s.tech == "Owen Buzaglo" and c.local(s.start).day == 1) for s in got)
    assert all(c.local(s.start).weekday() != 6 for s in got)          # never a Sunday


def test_slots_respect_the_technicians_hours():
    got = booking.slots(lat=WESTON[0], lng=WESTON[1], kind="repair", visits=[], now=NOW,
                        tech="Antonio Brown")
    for s in got:
        assert c.local(s.start).time() >= time(7, 30)
        assert c.local(s.end) <= c.local(s.start).replace(hour=17, minute=0)


# ---- the AI --------------------------------------------------------------------------------

@pytest.fixture()
def office(db, secrets_key):
    conn = AiConnection(name="OpenAI (Dispatch)", provider="openai",
                        api_key_encrypted=vault.encrypt("sk-test-DISPATCH-1234"),
                        api_key_last4="1234", default_model="gpt-6-luna")
    db.add(conn)
    db.flush()
    j = DispatchJob(job_uid="j1", job_number="678", board=c.INSPECTION_BOARD,
                    status="AHS Approved", status_since=NOW - timedelta(days=2),
                    customer_name="Marina Reyes", phones=["9415550100"],
                    address="1 Main St", city="Miami", lat=25.77, lng=-80.19,
                    zuper_created_at=NOW - timedelta(days=9), is_open=True,
                    fields={"Customer Responsibility": "", "Technician": "Antonio Brown"})
    item = DispatchItem(key="talked:j1:x", kind="talked_not_updated", queue="zuper",
                        job_uid="j1", job_number="678", board=c.INSPECTION_BOARD,
                        title="#678 Marina Reyes — talked 6 h ago, Zuper not updated",
                        why="Quo call, 87s.", todo="Put what was agreed into Zuper.",
                        evidence={"summary": "Accepted $175 with a $75 discount."},
                        due_at=NOW, urgent=True, state="open")
    db.add_all([j, item])
    db.commit()
    return {"conn": conn, "job": j, "item": item}


def switch_on(db, office, **kw):
    s = ai.settings(db, for_update=True)
    s.ai_enabled, s.connection_id = True, office["conn"].id
    for k, v in kw.items():
        setattr(s, k, v)
    db.commit()


ANSWER = json.dumps({
    "zuper_steps": ["Open #678", "Set Customer Responsibility to $100"],
    "say": "Thanks for confirming the $175 with the $75 discount.",
    "field_changes": [
        {"field": "Customer Responsibility", "current": "", "proposed": "$100",
         "evidence": "Quo call Sep 30: accepted $175 with a $75 discount"},
        {"field": "Invented Field", "current": "", "proposed": "x", "evidence": "none"},
        {"field": "Job address", "current": "1 Main St, Miami", "proposed": "12 Main St, Miami",
         "evidence": "customer corrected the number"}],
    "note": "", "confidence": "high"})


def test_off_by_default_nothing_reaches_the_provider(db, office, script):
    counts = ai.explain_pending(db, NOW)
    assert counts.get("explained") == 0 and "switched off" in counts["skipped"]
    assert script.calls == 0


def test_an_explanation_lands_and_only_real_fields_become_suggestions(db, office, script):
    switch_on(db, office)
    script.responses = [openai_completion(content=ANSWER, model="gpt-6-luna")]
    counts = ai.explain_pending(db, NOW)
    assert counts["explained"] == 1 and script.calls == 1
    req = script.requests[0]
    assert req["url"].endswith("/chat/completions") and req["body"]["model"] == "gpt-6-luna"
    assert "sk-test-DISPATCH-1234" not in json.dumps(req["body"])
    item = db.get(DispatchItem, office["item"].id)
    assert item.ai["say"].startswith("Thanks for confirming")
    assert item.ai["zuper_steps"][1] == "Set Customer Responsibility to $100"
    sugs = {(s.kind, s.field, s.proposed) for s in db.query(DispatchSuggestion)}
    assert sugs == {("field", "Customer Responsibility", "$100"),
                    ("address", "Job address", "12 Main St, Miami")}
    assert db.query(DispatchAiRun).count() == 1
    # Explained once: the next pass asks nothing.
    assert ai.explain_pending(db, NOW)["explained"] == 0 and script.calls == 1


@pytest.mark.parametrize("why", ["paused", "cap"])
def test_paused_or_over_the_cap_sends_nothing(db, office, script, why):
    switch_on(db, office, daily_cap=0 if why == "cap" else 300)
    if why == "paused":
        db.add(AiSettings(id=1, paused=True))
        db.commit()
    counts = ai.explain_pending(db, NOW)
    assert counts["explained"] == 0 and script.calls == 0
    assert ("paused" if why == "paused" else "limit") in counts["skipped"]


def test_a_suggestion_closes_itself_when_zuper_shows_the_value(db, office):
    s = DispatchSuggestion(job_uid="j1", job_number="678", board=c.INSPECTION_BOARD,
                           kind="field", field="Customer Responsibility", current="",
                           proposed="$100", state="approved")
    db.add(s)
    db.commit()
    office["job"].fields = {"Customer Responsibility": "$100"}
    assert service.close_suggestions(db, [office["job"]], NOW) == 1
    assert s.state == "done_in_zuper"


# ---- the API ---------------------------------------------------------------------------------

@pytest.fixture()
def people(db):
    out = {}
    for key, role, restricted in (("admin", Role.ADMIN, False),
                                  ("dispatcher", Role.DISPATCHER, False),
                                  ("tech", Role.TECH, False)):
        u = User(email="%s@x.test" % key, name=key.title(), role=role,
                 only_assigned_data=restricted)
        db.add(u)
        db.flush()
        plain, tok = mint_api_token(u, name=key)
        db.add(tok)
        out[key] = (u.id, {"Authorization": "Bearer " + plain})
    db.commit()
    return out


def test_settings_are_read_by_the_office_and_changed_by_an_admin_only(db, office, people):
    cl = TestClient(app)
    got = cl.get("/api/dispatch/settings", headers=people["dispatcher"][1]).json()
    assert got["ai_enabled"] is False and got["model"] == "gpt-6-luna"
    # The AI connections are ADMIN's: a dispatcher sees none (review 2026-10-01).
    assert got["connections"] == []
    admin_view = cl.get("/api/dispatch/settings", headers=people["admin"][1]).json()
    assert admin_view["connections"] == [{"id": office["conn"].id, "name": "OpenAI (Dispatch)",
                                          "provider": "openai", "last4": "1234"}]
    assert cl.put("/api/dispatch/settings", json={"ai_enabled": True},
                  headers=people["dispatcher"][1]).status_code == 403
    assert cl.put("/api/dispatch/settings", json={"ai_enabled": True},
                  headers=people["admin"][1]).status_code == 400          # no connection yet
    r = cl.put("/api/dispatch/settings", json={"connection_id": office["conn"].id,
                                               "ai_enabled": True, "daily_cap": 50},
               headers=people["admin"][1])
    assert r.status_code == 200 and r.json()["ai_enabled"] is True
    assert db.get(DispatchSettings, 1).daily_cap == 50
    assert cl.get("/api/dispatch/settings", headers=people["tech"][1]).status_code == 403


def test_approve_and_wrong_record_the_answer_and_change_nothing_else(db, office, people):
    a = DispatchSuggestion(job_uid="j1", board=c.INSPECTION_BOARD, kind="field",
                           field="Customer Responsibility", proposed="$100")
    b = DispatchSuggestion(job_uid="j1", board=c.INSPECTION_BOARD, kind="note", field="Note",
                           proposed="Customer prefers mornings")
    db.add_all([a, b])
    db.commit()
    cl = TestClient(app)
    h = people["dispatcher"][1]
    assert cl.post("/api/dispatch/suggestions/%d/approve" % a.id, headers=h).json()["state"] \
        == "approved"
    assert cl.post("/api/dispatch/suggestions/%d/approve" % a.id, headers=h).status_code == 409
    assert cl.post("/api/dispatch/suggestions/%d/wrong" % b.id, headers=h).json()["state"] \
        == "wrong"
    items = cl.get("/api/dispatch/items", headers=h).json()["items"]
    assert [s["field"] for s in items[0]["suggestions"]] == ["Customer Responsibility"]
    assert cl.post("/api/dispatch/suggestions/%d/wrong" % a.id,
                   headers=people["tech"][1]).status_code == 403


def test_slots_come_back_for_a_job(db, office, people):
    r = TestClient(app).get("/api/dispatch/jobs/j1/slots", headers=people["admin"][1]).json()
    assert len(r["slots"]) == 3 and r["note"] is None
    assert {s["tech"] for s in r["slots"]} <= {"Antonio Brown", "Owen Buzaglo"}


def test_explain_on_demand_refuses_while_off(db, office, people, script):
    r = TestClient(app).post("/api/dispatch/items/%d/explain" % office["item"].id,
                             headers=people["admin"][1])
    assert r.status_code == 409 and "switched off" in r.json()["detail"]
    assert script.calls == 0


def test_the_chat_reads_through_tools_and_only_records_suggestions(db, office, people, script):
    switch_on(db, office)
    hidden = DispatchJob(job_uid="r9", job_number="900", board=c.RETAIL_BOARD, status="New Lead",
                         customer_name="Hidden Person", is_open=True, phones=["9415550999"])
    db.add(hidden)
    p = Pipeline(name="Retail")
    db.add(p)
    db.flush()
    db.add(PipelinePermission(pipeline_id=p.id, user_id=people["admin"][0]))
    db.commit()
    script.responses = [
        openai_completion(content=None, finish="tool_calls", tool_calls=[
            openai_call("find_jobs", json.dumps({"text": ""}), "call_1")]),
        openai_completion(content=None, finish="tool_calls", tool_calls=[
            openai_call("suggest_change", json.dumps({
                "job_number": "678", "field": "Note", "proposed": "Prefers mornings",
                "evidence": "said on the Sep 30 call"}), "call_2")]),
        openai_completion(content="#678 Marina Reyes is AHS Approved; I noted her preference."),
    ]
    r = TestClient(app).post("/api/dispatch/chats/messages", json={
        "chat_id": None, "content": "What is open and what should I note?"},
        headers=people["dispatcher"][1])
    assert r.status_code == 200, r.text
    answer = r.json()["assistant_message"]
    assert answer["content"].startswith("#678") and answer["error"] is False
    assert [s["proposed"] for s in answer["suggestions"]] == ["Prefers mornings"]
    # Saved with what it looked up (the owner's ask, 2026-10-01).
    assert [st["tool"] for st in answer["steps"]] == ["find_jobs", "suggest_change"]
    saved = TestClient(app).get("/api/dispatch/chats/%d" % r.json()["chat"]["id"],
                                headers=people["dispatcher"][1]).json()
    assert [m["role"] for m in saved["messages"]] == ["user", "assistant"]
    assert saved["messages"][1]["steps"][0]["tool"] == "find_jobs"
    # The find_jobs result the model saw: the dispatcher's boards only.
    tool_msg = next(m for m in script.requests[1]["body"]["messages"] if m["role"] == "tool")
    seen = json.loads(tool_msg["content"])
    assert [j["job_number"] for j in seen] == ["678"]
    assert db.query(DispatchAiRun).filter(DispatchAiRun.kind == "chat").count() == 3


def test_the_chat_stops_when_the_cap_is_reached_mid_conversation(db, office, people, script):
    """Review 2026-10-01: every tool round is a request, so the cap is asked again each round."""
    switch_on(db, office, daily_cap=1)
    script.responses = [
        openai_completion(content=None, finish="tool_calls", tool_calls=[
            openai_call("list_items", json.dumps({}), "call_1")]),
        openai_completion(content="never reached"),
    ]
    r = TestClient(app).post("/api/dispatch/chats/messages", json={
        "chat_id": None, "content": "What is open?"}, headers=people["dispatcher"][1])
    answer = r.json()["assistant_message"]
    assert r.status_code == 200 and answer["content"].startswith("I stopped before finishing")
    assert answer["error"] is True
    assert script.calls == 1


def test_a_hidden_board_visit_blocks_time_but_is_never_named(db, office):
    busy = DispatchJob(job_uid="r5", job_number="555", board=c.RETAIL_BOARD, status="Scheduled",
                       is_open=True, city="Weston", lat=26.10, lng=-80.40,
                       assigned=["Antonio Brown"],
                       scheduled_start=datetime(2026, 10, 2, 11, 30, tzinfo=UTC),
                       scheduled_end=datetime(2026, 10, 2, 14, 0, tzinfo=UTC))
    db.add(busy)
    db.commit()
    visits = ai.visits_for_booking(db, NOW, hidden={c.RETAIL_BOARD})
    mine = [v for v in visits if v.tech == "Antonio Brown"]
    assert mine and all(v.label is None for v in mine)
    slots = ai.slots_for(db, office["job"], NOW, "repair", hidden={c.RETAIL_BOARD})
    assert all("#555" not in (s["next_to"] or "") for s in slots)


def test_a_single_step_sent_as_text_is_one_step_not_letters(db, office, script):
    switch_on(db, office)
    script.responses = [openai_completion(content=json.dumps({
        "zuper_steps": "Move #678 to Repair Scheduling Call", "say": "", "field_changes": [],
        "note": "", "confidence": "low"}))]
    ai.explain_pending(db, NOW)
    assert db.get(DispatchItem, office["item"].id).ai["zuper_steps"] == [
        "Move #678 to Repair Scheduling Call"]


def test_the_week_plan_endpoint_groups_waiting_jobs_and_hides_hidden_boards(db, office, people):
    db.add(DispatchJob(job_uid="r7", job_number="777", board=c.RETAIL_BOARD, status="New Lead",
                       is_open=True, lat=26.11, lng=-80.39, customer_name="Retail Lead"))
    p = Pipeline(name="Retail")
    db.add(p)
    db.flush()
    db.add(PipelinePermission(pipeline_id=p.id, user_id=people["admin"][0]))
    db.commit()
    cl = TestClient(app)
    mine = cl.get("/api/dispatch/plan?days=3", headers=people["admin"][1]).json()
    planned = {v["job_number"] for d in mine["days"] for t in d["techs"] for v in t["visits"]}
    assert {"678", "777"} <= planned and mine["pending"] == 2
    assert mine["assumptions"]["minutes"]["Antonio Brown"]["source"] in ("default", "history")
    theirs = cl.get("/api/dispatch/plan?days=3", headers=people["dispatcher"][1]).json()
    planned = {v["job_number"] for d in theirs["days"] for t in d["techs"] for v in t["visits"]}
    assert "777" not in planned and theirs["pending"] == 1
    assert cl.get("/api/dispatch/plan", headers=people["tech"][1]).status_code == 403


def test_out_of_look_ups_the_chat_still_answers_with_what_it_found(db, office, people, script):
    """2026-10-01: the owner's scheduling question ended in "I could not finish"."""
    switch_on(db, office)
    loop = [openai_completion(content=None, finish="tool_calls", tool_calls=[
        openai_call("list_items", json.dumps({}), "call_%d" % i)]) for i in range(ai.CHAT_STEPS)]
    script.responses = [*loop, openai_completion(content="Here is what I found so far.")]
    r = TestClient(app).post("/api/dispatch/chats/messages", json={
        "chat_id": None, "content": "Plan everything"}, headers=people["dispatcher"][1])
    answer = r.json()["assistant_message"]
    assert answer["content"] == "Here is what I found so far." and answer["error"] is False

"""Retell voice agents in the CRM (2026-10-06, docs/RETELL-PLAN.md C1, C3, C4, C5, C6).

What is pinned, by behaviour (C2, the brief, is tests/test_agent_context_sources.py):

  C1  A Retell agent publishes with a Retell agent id and nothing Retell owns; owen-main gets
      engine / retell_agent_id / tools and NEVER the context switches; `request_change` is a
      Retell tool only; `send_text` is refused for every engine; only an ADMIN changes a
      switch; an imported Retell agent round-trips.
  C3  A caller's change request is an urgent task on their card, attributed to the agent, or a
      Dispatch item and the bell; an unknown caller files nothing; a retry files nothing new;
      nothing is queued and nothing goes to Zuper; only the feed's rights reach it.
  C4  A later "ended" report adds Retell's summary, cost and outcome to the call it already
      filed, and never rewrites what is there.
  C5/C6  Phone numbers and the spend cap are ADMIN-only relays that ask nothing while the link
      is unset, refuse an after-hours assignment without hours (no invented default), and pass
      owen-main's "hand-built flow" 409 through for the page to confirm.

owen-main is mocked at the `crmlink.httpx` boundary; the owen and Retell guards fail any test
that reached a real host.
"""
import pytest
from app import crmlink
from app.ai import config as ai_config
from app.ai import voice as ai_voice
from app.db import SessionLocal
from app.dispatch import rules
from app.models import (
    AiAgent,
    AiAgentRequest,
    AiAlert,
    DispatchItem,
    DispatchJob,
    Job,
    Opportunity,
    OpportunityTask,
    Pipeline,
    PipelinePermission,
)
from sqlalchemy import select
from tests.ai_support import count, get
from tests.test_ai_voice_agents import FakeResponse, make_voice, voice_draft
from tests.test_ai_voice_agents import owen as owen_fixture  # noqa: F401 - fixture

BASE = "http://callmon_app:8000"
JANE = "+19415550101"
STRANGER = "+12125550000"
ZUPER_ONLY = "+12395550188"


def retell_draft(**over):
    d = voice_draft(engine="retell", retell_agent_id="agent_3f9c", persona="", greeting="",
                    model="", knowledge_text="", actions=["transfer_call", "end_call",
                                                          "capture_lead", "request_change"])
    d.update(over)
    return d


def publish(world, aid):
    return world.client("admin").post("/api/ai/agents/%s/publish" % aid)


# ================================================================== C1 — the agent's config

def test_a_retell_agent_publishes_with_its_id_and_nothing_retell_owns(world):
    aid = make_voice(world, **retell_draft(knowledge_text="x" * 7000))
    r = publish(world, aid)
    assert r.status_code == 201, r.text
    assert r.json()["publish_problems"] == []
    v = r.json()["versions"][0]
    cfg = world.client("admin").get("/api/ai/agents/%s/versions/%s" % (aid, v["version"]))
    sent = ai_voice.to_owen(cfg.json()["config"])
    assert sent["engine"] == "retell" and sent["retell_agent_id"] == "agent_3f9c"
    assert sent["tools"] == {"transfer": True, "end_call": True, "capture_lead": True,
                             "request_change": True}
    for owned in ("persona", "knowledge", "greeting", "model", "voice"):
        assert owned not in sent, owned
    assert "context_sources" not in sent


def test_a_retell_agent_needs_its_retell_agent_id(world):
    aid = make_voice(world, **retell_draft(retell_agent_id=""))
    r = publish(world, aid)
    assert r.status_code == 400 and "Retell agent id" in r.json()["detail"]
    assert "model" not in r.json()["detail"] and "6000" not in r.json()["detail"]
    r = world.client("admin").patch("/api/ai/agents/%s" % aid, json={
        "draft": retell_draft(retell_agent_id="a" * 101)})
    assert r.status_code == 400 and "retell_agent_id" in r.json()["detail"]


def test_the_owen_voice_rules_are_unchanged(world):
    aid = make_voice(world, model="", knowledge_text="x" * 6001)
    detail = publish(world, aid).json()["detail"]
    assert "Choose a model" in detail and "6001" in detail


@pytest.mark.parametrize("over, words", [
    ({"engine": "", "actions": ["request_change"]}, "only a Retell agent"),
    ({"engine": "owen_voice", "actions": ["end_call", "request_change"]}, "only a Retell"),
    ({"actions": ["end_call", "send_text"]}, "cannot send texts"),
    ({"engine": "vapi"}, "engine is"),
    ({"context_sources": {"crm_card": True, "bank_account": True}}, "bank_account"),
    ({"context_sources": {"crm_card": "yes"}}, "true or false"),
    ({"owen_settings": {"context_sources": {"crm_card": True}}}, "may not hold"),
])
def test_what_a_retell_agent_may_not_have_is_refused(world, over, words):
    aid = make_voice(world, **retell_draft())
    before = get(AiAgent, aid).draft
    r = world.client("admin").patch("/api/ai/agents/%s" % aid,
                                    json={"draft": retell_draft(**over)})
    assert r.status_code == 400, r.text
    assert words in r.json()["detail"]
    assert get(AiAgent, aid).draft == before


def test_the_switches_default_off_and_only_an_admin_changes_them(world):
    r = world.client("admin").post("/api/ai/agents", json={"name": "R", "channel": "voice"})
    aid = r.json()["id"]
    assert r.json()["draft"]["context_sources"] == dict.fromkeys(ai_voice.CONTEXT_SOURCES,
                                                                 False)
    assert r.json()["draft"]["engine"] == "" and r.json()["draft"]["retell_agent_id"] == ""
    before = get(AiAgent, aid).draft
    for who in ("dispatcher", "tech", "restricted"):
        r = world.client(who).patch("/api/ai/agents/%s" % aid, json={"draft": retell_draft(
            context_sources={"address": True}, engine="retell", retell_agent_id="x")})
        assert r.status_code == 403, (who, r.text)
    assert get(AiAgent, aid).draft == before
    r = world.client("admin").patch("/api/ai/agents/%s" % aid, json={"draft": retell_draft(
        context_sources={"address": True})})
    assert r.status_code == 200
    assert get(AiAgent, aid).draft["context_sources"]["address"] is True
    assert get(AiAgent, aid).draft["context_sources"]["crm_card"] is False


def test_the_catalogue_offers_request_change_to_voice_only():
    from app.ai.actions import actions_for_channel
    assert "request_change" in actions_for_channel("voice")
    assert "request_change" not in actions_for_channel("text")


def test_an_imported_retell_agent_round_trips():
    live = {"engine": "retell", "retell_agent_id": "agent_live",
            "tools": {"transfer": True, "end_call": True, "request_change": True,
                      "capture_lead": False},
            "transfer_targets": {"office": {"kind": "operator", "target": "owen"}},
            "context_provider": {"kind": "crm_link"}, "guardrails": {"max_call_seconds": 600},
            "crm_version": 3, "post_call_webhook": "x"}
    draft, notes = ai_voice.from_owen(live, "Intake")
    c = ai_config.clean(draft)
    assert notes == [] and ai_config.publish_problems(c) == []
    assert ai_voice.comparable(ai_voice.to_owen(c)) == ai_voice.comparable(live)
    assert c["context_sources"] == dict.fromkeys(ai_voice.CONTEXT_SOURCES, False)


def test_an_unknown_engine_from_owen_main_is_noted_not_kept():
    draft, notes = ai_voice.from_owen({"engine": "something_new", "persona": "p",
                                       "model": "m"}, "Intake")
    assert draft["engine"] == "" and "something_new" in notes[0]
    ai_config.clean(draft)


def test_publishing_a_retell_agent_pushes_the_owen_shape(world, owen_fixture):  # noqa: F811
    from tests.ai_support import drain
    aid = make_voice(world, **retell_draft(context_sources={"crm_card": True}))
    assert publish(world, aid).status_code == 201
    drain()
    sent = [b for m, u, b in owen_fixture.requests if m == "POST"][-1]["config"]
    assert sent["engine"] == "retell" and "context_sources" not in sent
    assert "persona" not in sent


# ================================================================== C3 — change requests

def _voice_agent(world, name="Receptionist", owen_agent="Intake"):
    aid = make_voice(world, **retell_draft(owen_agent=owen_agent))
    world.client("admin").patch("/api/ai/agents/%s" % aid, json={"name": name})
    assert publish(world, aid).status_code == 201
    return aid


def ask(world, number=JANE, who="dispatcher", **over):
    body = {"caller_number": number, "agent_name": "Intake", "owen_call_id": "call-77",
            "kind": "reschedule", "request": "Can the inspection move to Thursday morning?"}
    body.update(over)
    return world.client(who).post("/api/agent-requests", json=body)


def _jobs():
    return count(Job)


def test_a_known_customer_with_an_open_card_gets_an_urgent_task_from_the_agent(world):
    aid = _voice_agent(world)
    before = _jobs()
    r = ask(world)
    assert r.status_code == 200, r.text
    assert r.json() == {"created": True, "where": "task"}
    db = SessionLocal()
    t = db.scalars(select(OpportunityTask)).one()
    assert t.opportunity_id == world.ids["deal"] and t.priority == "urgent"
    assert t.ai_agent_id == aid and t.created_by_id is None
    assert "AI: Receptionist" in t.title and "Jane" in t.title and "reschedule" in t.title
    assert "Thursday morning" in t.description and t.completed_at is None
    assert db.scalar(select(DispatchItem)) is None
    db.close()
    assert _jobs() == before, "an agent request must queue nothing"


def test_a_retry_files_nothing_new(world):
    _voice_agent(world)
    first = ask(world).json()
    again = ask(world).json()
    assert first == {"created": True, "where": "task"}
    assert again == {"created": True, "where": "task", "existing": True}
    assert count(OpportunityTask) == 1 and count(AiAgentRequest) == 1
    # A different request on the same call is a new one.
    assert ask(world, kind="cancel", request="Cancel it").json()["where"] == "task"
    assert count(OpportunityTask) == 2


def test_an_unknown_caller_files_nothing(world):
    _voice_agent(world)
    for number in (STRANGER, "5550101", "not a number"):
        r = ask(world, number=number)
        assert r.status_code == 200
        assert r.json()["created"] is False and r.json()["where"] is None
        assert "Jane" not in r.text
    assert (count(OpportunityTask), count(DispatchItem), count(AiAgentRequest),
            count(AiAlert)) == (0, 0, 0, 0)


def test_a_zuper_only_caller_is_a_dispatch_item_and_rings_the_office(world):
    _voice_agent(world)
    db = SessionLocal()
    db.add(DispatchJob(job_uid="j-1", job_number="2001", board="Retail",
                       customer_uid="cu-1", customer_name="Pedro Alvarez",
                       phones=["2395550188"]))
    db.commit()
    db.close()
    before = _jobs()
    r = ask(world, number=ZUPER_ONLY, kind="cancel", request="Please cancel Friday")
    assert r.json() == {"created": True, "where": "dispatch"}
    item = SessionLocal().scalars(select(DispatchItem)).one()
    assert item.kind == "ai_change_request" and item.kind in rules.FED_KINDS
    assert item.urgent and item.state == "open" and item.job_uid == "j-1"
    assert "Pedro Alvarez" in item.title and "Please cancel Friday" in item.why
    alerts = SessionLocal().scalars(select(AiAlert)).all()
    assert {a.user_id for a in alerts} == {world.ids["user_admin"],
                                           world.ids["user_dispatcher"]}
    assert all(a.kind == "dispatch_ai_change_request" and a.urgent for a in alerts)
    assert count(OpportunityTask) == 0 and _jobs() == before


def test_a_contact_with_no_card_the_feed_may_see_goes_to_dispatch(world):
    """The card is on a pipeline restricted to the admin: the dispatcher-owned token must not
    file onto it — and the item does not name it."""
    _voice_agent(world)
    db = SessionLocal()
    pipe = db.get(Pipeline, world.ids["pipeline"])
    db.add(PipelinePermission(pipeline_id=pipe.id, user_id=world.ids["user_admin"]))
    db.commit()
    db.close()
    r = ask(world)
    assert r.json()["where"] == "dispatch"
    item = SessionLocal().scalars(select(DispatchItem)).one()
    assert "Jane roof" not in (item.title + item.why + (item.todo or ""))
    assert count(OpportunityTask) == 0
    # An admin-owned token sees the card and files a task there.
    r = ask(world, who="admin", owen_call_id="call-78")
    assert r.json()["where"] == "task"


def test_a_closed_card_is_not_where_a_request_goes(world):
    _voice_agent(world)
    db = SessionLocal()
    db.get(Opportunity, world.ids["deal"]).status = "won"
    db.commit()
    db.close()
    assert ask(world).json()["where"] == "dispatch"


def test_without_the_agent_in_the_crm_the_task_still_says_who_asked(world):
    r = ask(world, agent_name="Owen Receptionist")
    assert r.json()["where"] == "task"
    t = SessionLocal().scalars(select(OpportunityTask)).one()
    assert t.ai_agent_id is None and "AI: Owen Receptionist" in t.title


@pytest.mark.parametrize("who, status", [("tech", 403), ("restricted", 403), (None, 401)])
def test_only_the_feeds_rights_reach_it(world, who, status):
    r = ask(world, who=who)
    assert r.status_code == status
    assert count(OpportunityTask) == 0 and count(AiAgentRequest) == 0


def test_the_scope_list_lets_the_feed_token_through():
    from app import agent_requests, auth
    assert agent_requests.PATH in auth.EVENTS_WRITE_PATHS
    assert auth._scope_allows(frozenset({"events:write"}), "POST", agent_requests.PATH)
    assert not auth._scope_allows(frozenset({"events:write"}), "GET", agent_requests.PATH)


@pytest.mark.parametrize("over", [{"kind": "refund"}, {"request": "x" * 1001},
                                  {"request": "   "}, {"owen_call_id": ""}])
def test_a_malformed_request_is_refused(world, over):
    assert ask(world, **over).status_code == 422
    assert count(AiAgentRequest) == 0


# ================================================================== C4 — after the call

def test_a_later_report_adds_retells_summary_and_cost_and_never_rewrites():
    from app.auth import mint_api_token
    from app.db import Base, engine
    from app.main import app
    from app.models import NumberThreadEvent, Role, User
    from fastapi.testclient import TestClient
    from tests.test_ai_call_ingest import CALL

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    feed = User(email="feed@retell.test", name="Feed", role=Role.DISPATCHER)
    db.add(feed)
    db.flush()
    plain, tok = mint_api_token(feed, name="feed", scopes="events:write")
    db.add(tok)
    db.commit()
    db.close()
    c = TestClient(app)
    h = {"Authorization": "Bearer " + plain}
    first = c.post("/api/events", headers=h, json={**CALL, "ai_call": {
        "agent": "Intake", "engine": "retell", "retell_call_id": "call_r1"}})
    assert first.status_code == 201, first.text
    ended = {"agent": "Intake", "engine": "retell", "retell_call_id": "call_r1",
             "retell_agent_version": 4, "summary": "Caller wants a new inspection time.",
             "sentiment": "Neutral", "successful": False, "cost_cents": 0,
             "disconnection_reason": "user_hangup",
             "requests": [{"kind": "reschedule", "created": True, "where": "task"}]}
    second = c.post("/api/events", headers=h, json={**CALL, "ai_call": ended})
    assert second.json()["id"] == first.json()["id"]
    third = c.post("/api/events", headers=h, json={**CALL, "ai_call": {
        "summary": "REWRITTEN", "cost_cents": 999}})
    assert third.json()["id"] == first.json()["id"]
    db = SessionLocal()
    ev = db.get(NumberThreadEvent, first.json()["id"])
    assert ev.ai_call == ended, "every key arrived once and nothing was rewritten"
    db.close()


# ================================================================== C5 / C6 — numbers, spend

class Owen:
    def __init__(self):
        self.calls = []
        self.conflict = True

    def _r(self, method, url, json=None):
        self.calls.append((method, url.replace(BASE, ""), json))
        path = url.replace(BASE, "")
        if path == crmlink.NUMBERS_PATH:
            return FakeResponse(200, {"numbers": [
                {"id": "n1", "e164": "+19546859990", "label": "Spare DID", "assignable": True,
                 "reason": None, "assignment": {"agent_name": "Intake", "mode": "ai_first",
                                                "hours": None}},
                {"id": "n2", "e164": "+19547758492", "label": "Quo line", "assignable": False,
                 "reason": "not an Asterisk number", "assignment": None, "secret": "x"}]})
        if path.endswith("/assignment") and method == "PUT":
            if self.conflict and not (json or {}).get("replace"):
                return FakeResponse(409, {"detail": "number n1 has a hand-built flow"})
            return FakeResponse(200, {"ok": True, "number": {
                "id": "n1", "e164": "+19546859990", "assignable": True,
                "assignment": {"agent_name": json["agent_name"], "mode": json["mode"],
                               "hours": json.get("hours")}}})
        if path.endswith("/assignment") and method == "DELETE":
            return FakeResponse(200, {"ok": True})
        if path == crmlink.AGENT_SPEND_PATH:
            body = {"daily_cap_usd": 25.0, "alert_pct": 80, "today_usd": 21.5}
            if method == "PUT":
                body.update(json)
            return FakeResponse(200, body)
        return FakeResponse(404, {"detail": "no"})

    def get(self, url, **kw):
        return self._r("GET", url)

    def put(self, url, json=None, **kw):
        return self._r("PUT", url, json)

    def delete(self, url, **kw):
        return self._r("DELETE", url)


@pytest.fixture()
def link(monkeypatch):
    fake = Owen()
    for m in ("get", "put", "delete"):
        monkeypatch.setattr(crmlink.httpx, m, getattr(fake, m))
    return fake


def _configure(monkeypatch):
    monkeypatch.setenv("CRM_LINK_BASE_URL", BASE)
    monkeypatch.setenv("CRM_LINK_API_KEY", "owen_sk_test")


HOURS = {"tz": "America/New_York", "days": {"mon": [["08:00", "17:00"]],
                                            "tue": [["08:00", "12:00"], ["13:00", "17:00"]]}}


def test_an_unset_link_asks_nothing_and_says_so(world, link):
    admin = world.client("admin")
    r = admin.get("/api/ai/phone-numbers")
    assert r.status_code == 200 and r.json()["configured"] is False
    assert "not linked" in r.json()["detail"] and r.json()["numbers"] == []
    s = admin.get("/api/ai/agent-spend").json()
    assert s["configured"] is False and s["daily_cap_usd"] is None
    aid = _voice_agent(world)
    assert admin.put("/api/ai/phone-numbers/n1/assignment",
                     json={"agent_id": aid, "mode": "ai_first"}).status_code == 503
    assert admin.delete("/api/ai/phone-numbers/n1/assignment").status_code == 503
    assert admin.put("/api/ai/agent-spend",
                     json={"daily_cap_usd": 25, "alert_pct": 80}).status_code == 503
    assert link.calls == []


def test_the_numbers_list_names_the_crm_agent_and_drops_unknown_keys(world, link,
                                                                    monkeypatch):
    aid = _voice_agent(world)
    _configure(monkeypatch)
    out = world.client("admin").get("/api/ai/phone-numbers").json()
    n1, n2 = out["numbers"]
    assert n1["assignment"]["crm_agent_id"] == aid
    assert n1["assignment"]["mode_label"] == "AI answers every call"
    assert n2["assignable"] is False and n2["reason"] == "not an Asterisk number"
    assert "secret" not in n2
    assert [a["id"] for a in out["agents"]] == [aid]


def test_assigning_sends_the_agents_phone_system_name_and_confirms_a_hand_built_flow(
        world, link, monkeypatch):
    aid = _voice_agent(world)
    _configure(monkeypatch)
    admin = world.client("admin")
    r = admin.put("/api/ai/phone-numbers/n1/assignment", json={"agent_id": aid,
                                                               "mode": "staff_then_ai"})
    assert r.status_code == 409 and "hand-built" in r.json()["detail"]
    r = admin.put("/api/ai/phone-numbers/n1/assignment",
                  json={"agent_id": aid, "mode": "staff_then_ai", "replace": True})
    assert r.status_code == 200, r.text
    assert link.calls[-1] == ("PUT", "/api/crm-link/numbers/n1/assignment",
                              {"agent_name": "Intake", "mode": "staff_then_ai",
                               "replace": True})
    r = admin.delete("/api/ai/phone-numbers/n1/assignment")
    assert r.status_code == 200 and link.calls[-1][:2] == (
        "DELETE", "/api/crm-link/numbers/n1/assignment")


def test_after_hours_needs_hours_and_none_are_invented(world, link, monkeypatch):
    aid = _voice_agent(world)
    _configure(monkeypatch)
    link.conflict = False
    admin = world.client("admin")
    r = admin.put("/api/ai/phone-numbers/n1/assignment",
                  json={"agent_id": aid, "mode": "after_hours_ai"})
    assert r.status_code == 400 and "office hours" in r.json()["detail"]
    for bad in ({"days": {}}, {"days": {"mon": [["17:00", "08:00"]]}},
                {"days": {"mon": [["08:00", "12:00"], ["11:00", "13:00"]]}},
                {"tz": "UTC", "days": {"mon": [["08:00", "17:00"]]}},
                {"days": {"funday": [["08:00", "17:00"]]}}):
        r = admin.put("/api/ai/phone-numbers/n1/assignment",
                      json={"agent_id": aid, "mode": "after_hours_ai", "hours": bad})
        assert r.status_code == 400, bad
    assert link.calls == []
    r = admin.put("/api/ai/phone-numbers/n1/assignment",
                  json={"agent_id": aid, "mode": "after_hours_ai", "hours": HOURS})
    assert r.status_code == 200, r.text
    assert link.calls[-1][2]["hours"] == HOURS


@pytest.mark.parametrize("case", ["text", "unpublished", "bad_mode", "bad_id"])
def test_an_assignment_the_crm_cannot_stand_behind_is_refused_unasked(world, link,
                                                                     monkeypatch, case):
    from tests.ai_support import make_agent
    _configure(monkeypatch)
    if case == "text":
        aid, mode, nid = make_agent(world, mode="off"), "ai_first", "n1"
    elif case == "unpublished":
        aid, mode, nid = make_voice(world, **retell_draft()), "ai_first", "n1"
    elif case == "bad_mode":
        aid, mode, nid = _voice_agent(world), "always", "n1"
    else:
        aid, mode, nid = _voice_agent(world), "ai_first", "..%2F..%2Fadmin"
    r = world.client("admin").put("/api/ai/phone-numbers/%s/assignment" % nid,
                                  json={"agent_id": aid, "mode": mode})
    assert r.status_code in (400, 404), r.text
    assert link.calls == []


def test_the_spend_cap_reads_and_sets(world, link, monkeypatch):
    _configure(monkeypatch)
    admin = world.client("admin")
    s = admin.get("/api/ai/agent-spend").json()
    assert (s["daily_cap_usd"], s["alert_pct"], s["today_usd"]) == (25.0, 80, 21.5)
    assert s["used_pct"] == 86 and s["over_alert"] is True
    r = admin.put("/api/ai/agent-spend", json={"daily_cap_usd": 40, "alert_pct": 90})
    assert r.status_code == 200 and r.json()["daily_cap_usd"] == 40
    assert link.calls[-1] == ("PUT", "/api/crm-link/agent-spend",
                              {"daily_cap_usd": 40, "alert_pct": 90})
    assert admin.put("/api/ai/agent-spend",
                     json={"daily_cap_usd": -1, "alert_pct": 80}).status_code == 422


def test_a_dispatcher_cannot_touch_numbers_or_spend(world, link, monkeypatch):
    _configure(monkeypatch)
    d = world.client("dispatcher")
    assert d.get("/api/ai/phone-numbers").status_code == 403
    assert d.get("/api/ai/agent-spend").status_code == 403
    assert d.put("/api/ai/agent-spend", json={"daily_cap_usd": 1, "alert_pct": 5}
                 ).status_code == 403
    assert d.put("/api/ai/phone-numbers/n1/assignment",
                 json={"agent_id": 1, "mode": "ai_first"}).status_code == 403
    assert d.delete("/api/ai/phone-numbers/n1/assignment").status_code == 403
    assert link.calls == []

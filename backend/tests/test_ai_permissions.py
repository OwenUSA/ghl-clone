"""Who may use AI Agents — asserted on every route the router has, and on the database.

TECH and restricted users: 403 on EVERY /api/ai route, enumerated from the router so a route
added later is covered. DISPATCHER: reads agents, knowledge and templates, runs an agent by
hand, approves suggestions, switches an agent off — and nothing else. Only an ADMIN turns
an agent on, and Auto-pilot needs a confirmation.
"""
import re

import pytest
from app.ai.api import router
from app.models import AiAgent, AiConnection, AiFolder, AiKnowledgeBase, AiSettings, AiTemplate
from tests.ai_support import count, get, make_agent

ROUTES = sorted({(m, r.path) for r in router.routes for m in r.methods - {"HEAD", "OPTIONS"}})

# Every write and every ADMIN-only read. A dispatcher gets 403 on these.
ADMIN_ONLY = {
    ("PUT", "/api/ai/settings"), ("GET", "/api/ai/connections"),
    ("POST", "/api/ai/connections"), ("PATCH", "/api/ai/connections/{connection_id}"),
    ("DELETE", "/api/ai/connections/{connection_id}"), ("POST", "/api/ai/connections/test"),
    ("POST", "/api/ai/connections/models"), ("POST", "/api/ai/folders"),
    ("PATCH", "/api/ai/folders/{folder_id}"), ("DELETE", "/api/ai/folders/{folder_id}"),
    ("POST", "/api/ai/agents"), ("PATCH", "/api/ai/agents/{agent_id}"),
    ("POST", "/api/ai/agents/{agent_id}/publish"), ("DELETE", "/api/ai/agents/{agent_id}"),
    ("POST", "/api/ai/agents/{agent_id}/duplicate"), ("POST", "/api/ai/agents/{agent_id}/try"),
    ("POST", "/api/ai/agents/{agent_id}/wake"), ("POST", "/api/ai/templates"),
    ("DELETE", "/api/ai/templates/{template_id}"), ("POST", "/api/ai/knowledge-bases"),
    ("PATCH", "/api/ai/knowledge-bases/{kb_id}"), ("DELETE", "/api/ai/knowledge-bases/{kb_id}"),
    ("POST", "/api/ai/knowledge-bases/{kb_id}/items"),
    ("POST", "/api/ai/knowledge-bases/{kb_id}/files"), ("PATCH", "/api/ai/kb-items/{item_id}"),
    ("DELETE", "/api/ai/kb-items/{item_id}"), ("GET", "/api/ai/knowledge-gaps"),
    ("POST", "/api/ai/knowledge-gaps/{gap_id}/resolve"),
    ("POST", "/api/ai/knowledge-gaps/{gap_id}/dismiss"), ("GET", "/api/ai/runs"),
    ("GET", "/api/ai/runs/{run_id}"), ("GET", "/api/ai/metrics"),
}


def _url(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


def test_the_route_list_is_what_this_file_knows():
    assert len(ROUTES) >= 50
    assert set(ROUTES) >= ADMIN_ONLY


@pytest.mark.parametrize("who", ["tech", "restricted"])
def test_tech_and_restricted_users_are_refused_every_ai_route(world, who):
    aid = make_agent(world, mode="off", publish=False)
    client = world.client(who)
    before = (count(AiAgent), count(AiConnection), count(AiFolder), count(AiKnowledgeBase),
              count(AiTemplate), count(AiSettings))
    for method, path in ROUTES:
        r = client.request(method, _url(path).replace("/1", "/%s" % aid, 1)
                           if "{agent_id}" in path else _url(path), json={})
        assert r.status_code == 403, (method, path, r.status_code, r.text)
    after = (count(AiAgent), count(AiConnection), count(AiFolder), count(AiKnowledgeBase),
             count(AiTemplate), count(AiSettings))
    assert after == before
    assert get(AiAgent, aid).mode == "off" and get(AiAgent, aid).archived_at is None


def test_a_dispatcher_cannot_edit_or_see_logs(world):
    aid = make_agent(world, mode="off", publish=False)
    client = world.client("dispatcher")
    for method, path in sorted(ADMIN_ONLY):
        url = _url(path).replace("/1", "/%s" % aid, 1) if "{agent_id}" in path else _url(path)
        r = client.request(method, url, json={})
        assert r.status_code == 403, (method, path, r.status_code, r.text)
    agent = get(AiAgent, aid)
    assert agent.archived_at is None and agent.published_version_id is None
    assert count(AiAgent) == 1 and count(AiConnection) == 1
    # ...and reads what the module shows them.
    for url in ("/api/ai/agents", "/api/ai/agents/%s" % aid, "/api/ai/knowledge-bases",
                "/api/ai/templates", "/api/ai/suggestions", "/api/ai/catalogue",
                "/api/ai/alerts", "/api/ai/connection-names"):
        assert client.get(url).status_code == 200, url
    detail = client.get("/api/ai/agents/%s" % aid).json()
    assert detail["can_edit"] is False
    assert "api_key" not in str(client.get("/api/ai/connection-names").json())


def test_only_an_admin_turns_an_agent_on_and_auto_pilot_needs_confirmation(world):
    aid = make_agent(world, mode="off")
    dispatcher, admin = world.client("dispatcher"), world.client("admin")
    for mode in ("suggest", "auto"):
        r = dispatcher.post("/api/ai/agents/%s/mode" % aid, json={"mode": mode, "confirm": True})
        assert r.status_code == 403
        assert get(AiAgent, aid).mode == "off"
    r = admin.post("/api/ai/agents/%s/mode" % aid, json={"mode": "auto"})
    assert r.status_code == 409 and "Confirm" in r.json()["detail"]
    assert get(AiAgent, aid).mode == "off"
    assert admin.post("/api/ai/agents/%s/mode" % aid,
                      json={"mode": "auto", "confirm": True}).json()["mode"] == "auto"
    # A dispatcher can always switch it OFF — the emergency stop.
    assert dispatcher.post("/api/ai/agents/%s/mode" % aid,
                           json={"mode": "off"}).json()["mode"] == "off"
    assert get(AiAgent, aid).mode == "off"


def test_an_unpublished_agent_cannot_be_switched_on(world):
    aid = make_agent(world, mode="off", publish=False)
    r = world.client("admin").post("/api/ai/agents/%s/mode" % aid,
                                   json={"mode": "suggest", "confirm": True})
    assert r.status_code == 400 and get(AiAgent, aid).mode == "off"


def test_a_voice_agent_cannot_be_created_yet(world):
    r = world.client("admin").post("/api/ai/agents", json={"name": "Receptionist",
                                                          "channel": "voice"})
    assert r.status_code == 400 and "phase 3" in r.json()["detail"]
    assert count(AiAgent) == 0
    aid = make_agent(world, mode="off", publish=False)
    draft = world.client("admin").get("/api/ai/agents/%s" % aid).json()["draft"]
    r = world.client("admin").patch("/api/ai/agents/%s" % aid,
                                    json={"draft": {**draft, "actions": ["transfer_call"]}})
    assert r.status_code == 400


def test_escalation_users_must_be_active_unrestricted_staff(world):
    aid = make_agent(world, mode="off", publish=False)
    admin = world.client("admin")
    draft = admin.get("/api/ai/agents/%s" % aid).json()["draft"]
    for uid in (world.ids["user_tech"], world.ids["user_restricted"], 999):
        r = admin.patch("/api/ai/agents/%s" % aid,
                        json={"draft": {**draft, "escalation_user_ids": [uid]}})
        assert r.status_code == 400, uid
    assert get(AiAgent, aid).draft["escalation_user_ids"] == []


def test_the_manual_run_respects_the_callers_pipeline_access(world):
    from app.db import SessionLocal
    from app.models import AiRun, PipelinePermission
    aid = make_agent(world)
    db = SessionLocal()
    db.add(PipelinePermission(pipeline_id=world.ids["pipeline"], user_id=world.ids["user_admin"]))
    db.commit()
    db.close()
    r = world.client("dispatcher").post("/api/ai/agents/%s/run" % aid,
                                        json={"opportunity_id": world.ids["deal"]})
    assert r.status_code == 404 and count(AiRun) == 0

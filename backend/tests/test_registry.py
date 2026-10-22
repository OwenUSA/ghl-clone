"""The agent registry (2026-10-21, app/registry), by behaviour.

  * The catalog is consistent: known roles, actions and levels; an automation's levels are not
    editable here; an AI Agents row mirrors its own switches and cannot be set here.
  * Only an ADMIN changes anything; giving MORE freedom needs a reason and the typed PROMOTE;
    every change is a log row; a refused change writes nothing.
  * The auto-reply obeys it: Never asks nothing and sends nothing; Shadow writes the reply and
    records it, and sends nothing — no text, no note; its own spend limit stops it and rings
    the bell once a day; a dead model rings the bell once a day.
  * The Dispatch assistant's Zuper changes are refused at Never, whatever the switches say.
  * The activity feed reads each entry's own log.
"""
from datetime import timedelta

import pytest
from app.models import (
    AgentPermission,
    AgentRegistryLog,
    AiAlert,
    AutoReply,
    Role,
    User,
)
from app.registry import catalog as cat
from app.registry import service as registry
from tests.ai_support import count, make_agent
from tests.test_autoreply_service import GOOD, NOW, reply, zc  # noqa: F401  (the fixture)

# ------------------------------------------------------------------------- the catalog


def test_the_catalog_is_consistent():
    assert cat.validate() == []
    assert set(cat.ROLES) == set(range(1, 11))
    # The auto-reply's customer texts can be held back from here; its note goes with the text.
    a = cat.BY_KEY["autoreply"].action("send_text")
    assert a.default == cat.AUTO and set(a.choices) == {cat.NEVER, cat.SHADOW, cat.AUTO}
    assert cat.BY_KEY["autoreply"].never, "a customer-facing agent needs a never-list"
    # Nothing outside the CRM pretends to be switchable here.
    for e in cat.ENTRIES:
        if "outside" in e.tags:
            assert all(not x.choices for x in e.actions), e.key


# ------------------------------------------------------------------------- the API


def test_everyone_in_the_office_reads_it_and_an_ai_agent_mirrors_its_switches(world):
    aid = make_agent(world, mode="auto", name="Retail follow-up")
    r = world.client("dispatcher").get("/api/ai/registry")
    assert r.status_code == 200, r.text
    body = r.json()
    by_key = {e["key"]: e for e in body["entries"]}
    assert {"autoreply", "dispatch_assistant", "zuper_workflows", "ai_agent:%d" % aid} <= set(
        by_key)
    agent = by_key["ai_agent:%d" % aid]
    assert {a["action"]: a["level"] for a in agent["actions"]} == {
        "send_text": "auto", "crm_write": "auto"}
    assert all(a["choices"] == [] for a in agent["actions"])
    assert by_key["autoreply"]["actions"][0]["level"] == "auto"
    assert body["overall"]["daily_cap"] == "$25.00"
    # Its own switch stays the one place to change it.
    r = world.client("admin").put("/api/ai/registry/entries/ai_agent:%d/actions/send_text" % aid,
                                  json={"level": "never"})
    assert r.status_code == 400 and "not set here" in r.json()["detail"]
    assert count(AgentPermission) == 0


def test_a_dispatcher_cannot_change_anything(world):
    d = world.client("dispatcher")
    for method, url, body in (
            ("PUT", "/api/ai/registry/entries/autoreply/actions/send_text", {"level": "never"}),
            ("PATCH", "/api/ai/registry/entries/autoreply", {"daily_cap_usd": 1}),
            ("PUT", "/api/ai/registry/overall", {"daily_cap_usd": 1})):
        assert d.request(method, url, json=body).status_code == 403
    assert count(AgentPermission) == count(AgentRegistryLog) == 0


def test_more_freedom_needs_a_reason_and_promote_less_does_not(world):
    a = world.client("admin")
    url = "/api/ai/registry/entries/autoreply/actions/send_text"
    r = a.put(url, json={"level": "shadow"})            # less freedom: no ceremony
    assert r.status_code == 200, r.text
    assert next(x for x in r.json()["actions"] if x["action"] == "send_text")["level"] == "shadow"
    for body in ({"level": "auto", "confirm": "PROMOTE"},             # no reason
                 {"level": "auto", "reason": "two clean weeks"},       # no confirmation
                 {"level": "auto", "reason": "x", "confirm": "promote"}):
        r = a.put(url, json=body)
        assert r.status_code == 400, body
    assert count(AgentRegistryLog) == 1
    r = a.put(url, json={"level": "approve", "reason": "x", "confirm": "PROMOTE"})
    assert r.status_code == 400 and "can be" in r.json()["detail"]    # not one of its choices
    r = a.put(url, json={"level": "auto", "reason": "Two clean weeks in shadow",
                         "confirm": "PROMOTE"})
    assert r.status_code == 200
    rows = world.client("admin").get("/api/ai/registry/activity?key=autoreply").json()["items"]
    texts = [x["text"] for x in rows]
    assert any("from Shadow to Automatic. Reason: Two clean weeks in shadow" in t for t in texts)
    assert any("from Automatic to Shadow" in t for t in texts)


def test_an_automation_and_an_unknown_action_are_refused(world):
    a = world.client("admin")
    r = a.put("/api/ai/registry/entries/zuper_workflows/actions/send_text",
              json={"level": "never"})
    assert r.status_code == 400 and "Switched in Zuper" in r.json()["detail"]
    r = a.put("/api/ai/registry/entries/autoreply/actions/place_calls", json={"level": "auto"})
    assert r.status_code == 400 and "cannot do" in r.json()["detail"]
    assert count(AgentPermission) == 0


def test_entry_settings_are_logged_and_checked(world):
    a = world.client("admin")
    r = a.patch("/api/ai/registry/entries/autoreply", json={
        "daily_cap_usd": "5", "is_test": True, "role": 2,
        "owner_user_id": world.ids["user_dispatcher"]})
    assert r.status_code == 200, r.text
    e = r.json()
    assert (e["daily_cap"], e["is_test"], e["role"], e["owner"]) == ("$5.00", True, 2,
                                                                        "Dispatcher")
    assert count(AgentRegistryLog) == 4
    assert a.patch("/api/ai/registry/entries/autoreply", json={"role": 11}).status_code == 400
    assert a.patch("/api/ai/registry/entries/autoreply",
                   json={"daily_cap_usd": "five"}).status_code == 400
    assert a.patch("/api/ai/registry/entries/nope", json={"role": 1}).status_code == 400
    r = a.put("/api/ai/registry/overall", json={"daily_cap_usd": 40})
    assert r.status_code == 200 and r.json()["daily_cap"] == "$40.00"


# ------------------------------------------------------------------------- the auto-reply


def _hold(db, level):
    db.add(AgentPermission(entry_key="autoreply", action="send_text", level=level))
    db.commit()


def test_never_asks_nothing_and_sends_nothing(db, zc, script):  # noqa: F811
    from app.autoreply import service
    _hold(db, "never")
    out = service.run(db, NOW)
    assert "at Never" in out["stopped"]
    assert zc.requests == [] and script.calls == 0 and db.query(AutoReply).count() == 0


def test_shadow_writes_and_records_the_reply_and_sends_nothing(db, zc, script):  # noqa: F811
    from app.autoreply import service
    _hold(db, "shadow")
    script.responses = [reply(GOOD)]
    out = service.run(db, NOW)
    assert out["shadow"] == 1 and out["replied"] == 0, out
    assert zc.sent == [] and zc.notes == []
    assert not any(m == "POST" and p.endswith("/send") for m, p in zc.requests)
    row = db.query(AutoReply).one()
    assert (row.state, row.reply, row.sent_at) == ("shadow", GOOD, None)
    # Decided once: the next pass neither asks the model again nor sends.
    service.run(db, NOW + timedelta(minutes=2))
    assert script.calls == 1 and zc.sent == []
    items = registry.activity(db, key="autoreply")
    assert items[0]["what"] == "shadow" and GOOD in items[0]["text"]
    # A new message in the same unanswered stretch: still decided, the model is not asked again.
    from tests.test_autoreply_service import CONV, iso, msg
    zc.history[CONV].append(msg("m2", NOW + timedelta(minutes=3), body="hello??"))
    zc.convs[CONV]["recent_conversation_at"] = iso(NOW + timedelta(minutes=3))
    service.run(db, NOW + timedelta(minutes=10))
    assert script.calls == 1 and zc.sent == [] and db.query(AutoReply).count() == 1


def test_switched_off_is_a_status_not_a_failure(db, zc, script):  # noqa: F811
    from app.autoreply import service
    s = service.settings(db, for_update=True)
    s.autoreply_enabled = False
    db.commit()
    service.run(db, NOW)
    e = next(x for x in registry.overview(db, NOW)["entries"] if x["key"] == "autoreply")
    assert e["status"]["state"] == "off" and "Dispatch" in e["status"]["sentence"]
    assert e["health"] == {"last_error": None, "last_error_at": None, "error_today": False}


def test_its_spend_limit_stops_it_and_rings_once(db, zc, script):  # noqa: F811
    from app.autoreply import service
    db.add(User(email="boss@x.test", name="Boss", role=Role.ADMIN))
    registry.update_entry(db, "autoreply", {"daily_cap_usd": 0}, user_id=None)
    db.commit()
    out = service.run(db, NOW)
    assert "daily limit of $0.00" in out["stopped"] and zc.sent == [] and script.calls == 0
    service.run(db, NOW + timedelta(minutes=1))
    assert db.query(AiAlert).filter(AiAlert.kind == "agent_cap_reached").count() == 1


def test_a_dead_model_rings_the_bell_once_a_day(db, zc, script):  # noqa: F811
    from app.autoreply import service
    db.add(User(email="boss@x.test", name="Boss", role=Role.ADMIN))
    db.commit()
    script.responses = [(500, {"error": {"message": "insufficient balance"}})]
    service.run(db, NOW)
    assert len(zc.sent) == 1                       # the fixed wording still went
    alerts = db.query(AiAlert).filter(AiAlert.kind == "agent_failure").all()
    assert len(alerts) == 1 and alerts[0].title.startswith("Auto-reply stopped")
    assert registry.alert_failure(db, "autoreply", "again", NOW) is False


# ------------------------------------------------------------------------- Dispatch writes


@pytest.fixture()
def writes_on(db, monkeypatch):
    from app.dispatch import ai, writes
    from app.zuper import config as zconfig
    for f in ("dispatch_writes_enabled", "env_enabled", "key_set"):
        monkeypatch.setattr(zconfig, f, lambda: True)
    s = ai.settings(db, for_update=True)
    for k in writes.SWITCHES:
        setattr(s, k, True)
    db.commit()
    return writes


def test_dispatch_zuper_changes_are_refused_at_never(db, writes_on):
    kind = next(iter(writes_on.KIND_SWITCH))
    assert writes_on.why_not(db, kind) is None
    db.add(AgentPermission(entry_key="dispatch_assistant", action="zuper_change",
                           level="never"))
    db.commit()
    assert "at Never" in writes_on.why_not(db, kind)


def test_the_ahs_portal_agent_is_listed_off_and_cannot_be_switched_on(world):
    a = world.client("admin")
    e = next(x for x in a.get("/api/ai/registry").json()["entries"]
             if x["key"] == "ahs_submission")
    assert e["status"]["state"] == "off" and "Frontdoor" in e["status"]["sentence"]
    assert e["role"] == 5 and all(x["level"] == "never" and x["choices"] == []
                                  for x in e["actions"])
    r = a.put("/api/ai/registry/entries/ahs_submission/actions/ahs_portal",
              json={"level": "auto", "reason": "x", "confirm": "PROMOTE"})
    assert r.status_code == 400 and "not set here" in r.json()["detail"]
    assert count(AgentPermission) == 0

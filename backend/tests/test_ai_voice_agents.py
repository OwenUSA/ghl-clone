"""Voice agents are edited in the CRM and published to owen-main (phase 2b, 2026-09-25).

What is pinned, by behaviour:

  * **A voice agent cannot be given `send_text`** — nor any action, trigger, schedule or
    connection that makes no sense on a live call. The API refuses; the stored draft does not
    change.
  * **Knowledge over 6000 characters refuses to publish**, naming the number, and writes no
    version, no push and no job.
  * **Publish never waits on owen-main.** The request makes no network call at all; the worker
    pushes. Until owen-main answers with the version it stored, the agent reads "Published ·
    not yet live on the phone system".
  * **A failed push leaves it "not yet live"**, with the reason in words, and the queue retries
    an unreachable owen-main — never a refusal.
  * **A second push of the same CRM version makes no second owen-main version**: every retry
    carries the same `crm_version`, and a fake owen-main that keys on it (as the real one does,
    pinned in owen-main's tests/test_crm_agent_versions.py) stores one.
  * **A newer publish supersedes an older push in flight.** Version 1 is never pushed over 2.
  * **The import** is a dry run by default, creates the agent with version 1 when asked —
    answering calls, writes Off (phase 2c) — never overwrites, and is idempotent.

Phase 2c (2026-09-25) — "Answering calls" is its own switch, separate from the mode:

  * publish while NOT answering → owen-main stores the version and does not activate it;
  * switch on → owen-main activates the version it already holds; no second version;
  * switch off → owen-main deactivates; the screen says not answering, callers → fallback;
  * publish while answering → stored AND active;
  * a text agent refuses the switch; a dispatcher cannot touch it; it must be confirmed;
  * the mode never moves the switch and the switch never moves the mode.

owen-main is mocked at the `crmlink.httpx` boundary, like `test_number_threads.py`; the
owen guard fails any test here that reached a real host.
"""
from datetime import UTC, datetime

import pytest
from app import crmlink
from app.ai import import_voice_agent, voice
from app.db import SessionLocal
from app.models import AiAgent, AiAgentVersion, AiVoicePush, Job
from sqlalchemy import select
from tests.ai_support import count, drain, get

BASE = "http://callmon_app:8000"

LIVE_CONFIG = {
    "persona": "You are the receptionist for Dream Team Roofing. " * 60,   # ~3,000 chars
    "greeting": "Thanks for calling Dream Team Roofing, how can I help?",
    "model": "gpt-4o-mini",
    "voice": "aura-2-andromeda-en",
    "tts_provider": "deepgram",
    "stt_provider": "deepgram",
    "engine": "owen_voice",
    "tools": {"transfer": True, "end_call": True, "capture_lead": True},
    "knowledge": "We repair and replace roofs in Manatee and Sarasota counties.",
    "guardrails": {"max_call_seconds": 600, "max_silence_seconds": 20},
    "transfer_targets": {"office": {"kind": "operator", "target": "owen"}},
    "context_provider": {"kind": "crm_link"},
    "tts_instructions": "Warm, unhurried.",
}


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = str(body)
        self.headers = {"content-type": "application/json"}
        self.content = b""

    def json(self):
        return self._body


class Owen:
    """A fake owen-main that keeps agent versions the way the real one does: by agent name,
    idempotent on `crm_version`. `down` makes every request fail to connect; `lose_reply`
    stores the version and then drops the answer (a timeout after the write)."""

    def __init__(self, agents=("Intake",)):
        self.requests = []
        self.versions = {name: [] for name in agents}
        self.live = dict.fromkeys(agents)
        self.down = False
        self.lose_reply = False
        self.refuse = None

    def post(self, url, json=None, headers=None, timeout=None, **kw):
        self.requests.append(("POST", url, json))
        if self.down:
            raise ConnectionError("connection refused")
        if self.refuse:
            return FakeResponse(422, {"detail": {"message": self.refuse, "errors": [
                self.refuse]}})
        name = json["agent_name"]
        if name not in self.versions:
            return FakeResponse(404, {"detail": {
                "message": "the phone system has no agent named '%s'" % name}})
        rows = self.versions[name]
        if json.get("deactivate"):
            assert set(json) == {"agent_name", "deactivate"}, json   # owen-main's form
            previous = self.live[name]
            self.live[name] = None
            if self.lose_reply:
                raise TimeoutError("read timed out")
            return FakeResponse(200, {"ok": True, "agent_id": "a-" + name,
                                      "deactivated": previous is not None,
                                      "previous_version": self._number(name, previous),
                                      "answering": False, "active_version": None})
        found = [r for r in rows if r["config"]["crm_version"] == json["crm_version"]]
        if found:
            row, created = found[0], False
        else:
            row = {"id": "v-%d" % (len(rows) + 1), "version": len(rows) + 1,
                   "config": {**json["config"], "crm_version": json["crm_version"]}}
            rows.append(row)
            created = True
        if json.get("activate"):
            self.live[name] = row["id"]
        if self.lose_reply:
            raise TimeoutError("read timed out")
        return FakeResponse(200, {"ok": True, "agent_id": "a-" + name, "version_id": row["id"],
                                  "version": row["version"], "created": created,
                                  "active": self.live[name] == row["id"],
                                  "answering": self.live[name] is not None,
                                  "active_version": self._number(name, self.live[name]),
                                  "warnings": []})

    def _number(self, name, vid):
        return next((r["version"] for r in self.versions[name] if r["id"] == vid), None)

    def active(self, name="Intake"):
        """The number of owen-main's ACTIVE version of this agent, or None."""
        return self._number(name, self.live[name])

    def get(self, url, headers=None, timeout=None, **kw):
        self.requests.append(("GET", url, None))
        if self.down:
            raise ConnectionError("connection refused")
        return FakeResponse(200, {"agents": [
            {"agent_id": "a-Intake", "name": "Intake",
             "active_version": {"id": "live-4", "version": 4, "config": LIVE_CONFIG,
                                "created_at": "2026-09-20T12:00:00+00:00"}},
            {"agent_id": "a-Old", "name": "Old", "active_version": None}]})


@pytest.fixture()
def owen(monkeypatch):
    fake = Owen()
    monkeypatch.setenv("CRM_LINK_BASE_URL", BASE)
    monkeypatch.setenv("CRM_LINK_API_KEY", "owen_sk_test")
    monkeypatch.setattr(crmlink.httpx, "post", fake.post)
    monkeypatch.setattr(crmlink.httpx, "get", fake.get)
    return fake


def voice_draft(**over) -> dict:
    from app.ai.config import default_config
    d = default_config("voice")
    d.update({"persona": "You answer the phone for Dream Team Roofing.",
              "greeting": "Dream Team Roofing, how can I help?", "model": "gpt-4o-mini",
              "owen_agent": "Intake", "knowledge_text": "We fix roofs.",
              "actions": ["transfer_call", "end_call", "capture_lead"]})
    d.update(over)
    return d


def make_voice(world, answering=False, **over) -> int:
    admin = world.client("admin")
    r = admin.post("/api/ai/agents", json={"name": "Receptionist", "channel": "voice"})
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    r = admin.patch("/api/ai/agents/%s" % aid, json={"draft": voice_draft(**over)})
    assert r.status_code == 200, r.text
    if answering:
        switch(world, aid, True)
    return aid


def switch(world, aid, on, who="admin", confirm=True):
    return world.client(who).post("/api/ai/agents/%s/answering" % aid,
                                  json={"on": on, "confirm": confirm})


def jobs(status=None):
    db = SessionLocal()
    try:
        stmt = select(Job).where(Job.type == "ai_voice_push")
        if status:
            stmt = stmt.where(Job.status == status)
        return db.scalars(stmt).all()
    finally:
        db.close()


def make_due():
    """Time passes: every pending push job is due now."""
    db = SessionLocal()
    for j in db.scalars(select(Job).where(Job.type == "ai_voice_push",
                                          Job.status == "pending")).all():
        j.run_after = datetime.now(UTC)
    db.commit()
    db.close()


def phone(world, aid):
    return world.client("admin").get("/api/ai/agents/%s" % aid).json()["phone_system"]


# ------------------------------------------------------------------ creating and editing

def test_a_voice_agent_is_created_off_with_only_voice_actions(world):
    r = world.client("admin").post("/api/ai/agents", json={"name": "Receptionist",
                                                          "channel": "voice"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["channel"] == "voice" and body["mode"] == "off"
    assert body["draft"]["actions"] == ["end_call", "capture_lead"]
    assert body["phone_system"]["status"] == "unpublished"
    assert get(AiAgent, body["id"]).channel == "voice"
    cat = world.client("admin").get("/api/ai/catalogue").json()
    assert {c["value"]: c["available"] for c in cat["channels"]} == {"text": True,
                                                                     "voice": True}
    offered_voice = sorted(a["name"] for a in cat["actions"] if "voice" in a["channels"])
    assert offered_voice == ["capture_lead", "end_call", "transfer_call"]
    assert "send_text" not in offered_voice


def test_a_voice_agent_cannot_be_given_send_text(world):
    aid = make_voice(world)
    before = get(AiAgent, aid).draft
    r = world.client("admin").patch("/api/ai/agents/%s" % aid, json={
        "draft": voice_draft(actions=["end_call", "send_text"])})
    assert r.status_code == 400
    assert "cannot send texts" in r.json()["detail"]
    assert "send_sms" in r.json()["detail"]
    assert get(AiAgent, aid).draft == before
    assert "send_text" not in get(AiAgent, aid).draft["actions"]


@pytest.mark.parametrize("over, words", [
    ({"actions": ["book_appointment"]}, "only transfer the call"),
    ({"actions": ["get_context", "end_call"]}, "only transfer the call"),
    ({"triggers": [{"type": "manual"}]}, "no CRM triggers"),
    ({"triggers": [{"type": "missed_call"}]}, "no CRM triggers"),
    ({"schedule": {"enabled": True, "days": ["mon"], "start": "08:00", "end": "17:00"}},
     "business hours"),
    ({"transfer_targets": {"office": {"kind": "pager", "target": "x"}}}, "unknown kind"),
    ({"guardrails": {"max_call_seconds": 0}}, "max_call_seconds"),
    ({"owen_settings": {"persona": "sneaky"}}, "may not hold persona"),
])
def test_what_makes_no_sense_on_a_call_is_refused_by_the_api(world, over, words):
    aid = make_voice(world)
    before = get(AiAgent, aid).draft
    r = world.client("admin").patch("/api/ai/agents/%s" % aid,
                                    json={"draft": voice_draft(**over)})
    assert r.status_code == 400, r.text
    assert words in r.json()["detail"]
    assert get(AiAgent, aid).draft == before


def test_a_voice_agent_takes_no_ai_connection(world):
    aid = make_voice(world)
    r = world.client("admin").patch("/api/ai/agents/%s" % aid, json={
        "draft": voice_draft(connection_id=world.ids["connection"])})
    assert r.status_code == 400 and "no AI connection" in r.json()["detail"]


def test_a_text_agent_still_cannot_have_voice_actions_or_voice_settings(world):
    from tests.ai_support import config_for, make_agent
    aid = make_agent(world, mode="off", publish=False)
    admin = world.client("admin")
    for draft in (config_for(world, actions=["capture_lead"]),
                  {**config_for(world), "greeting": "hi"}):
        r = admin.patch("/api/ai/agents/%s" % aid, json={"draft": draft})
        assert r.status_code == 400, draft
    r = admin.patch("/api/ai/agents/%s" % aid, json={"draft": {**config_for(world),
                                                               "channel": "voice"}})
    assert r.status_code == 400 and "cannot be changed" in r.json()["detail"]
    assert get(AiAgent, aid).channel == "text"


def test_try_it_and_run_refuse_a_voice_agent(world, owen):
    aid = make_voice(world)
    admin = world.client("admin")
    r = admin.post("/api/ai/agents/%s/try" % aid,
                   json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 400 and "phone system" in r.json()["detail"]
    admin.post("/api/ai/agents/%s/publish" % aid)
    admin.post("/api/ai/agents/%s/mode" % aid, json={"mode": "suggest"})
    r = admin.post("/api/ai/agents/%s/run" % aid, json={"contact_id": world.ids["jane"]})
    assert r.status_code == 400 and "Manual" in r.json()["detail"]
    from app.models import AiRun
    assert count(AiRun) == 0


# ------------------------------------------------------------------ publishing

def test_knowledge_over_6000_characters_refuses_to_publish(world, owen):
    aid = make_voice(world, knowledge_text="x" * 6001)   # a draft may hold it...
    assert len(get(AiAgent, aid).draft["knowledge_text"]) == 6001
    r = world.client("admin").post("/api/ai/agents/%s/publish" % aid)
    assert r.status_code == 400                                 # ...a version may not
    detail = r.json()["detail"]
    assert "6001" in detail and "6000" in detail and "Cut 1 character" in detail
    assert count(AiAgentVersion) == 0 and count(AiVoicePush) == 0 and jobs() == []
    assert get(AiAgent, aid).published_version_id is None
    assert owen.requests == []
    # 6000 exactly is fine.
    make_voice_ok = world.client("admin").patch("/api/ai/agents/%s" % aid, json={
        "draft": voice_draft(knowledge_text="x" * 6000)})
    assert make_voice_ok.status_code == 200
    assert world.client("admin").post("/api/ai/agents/%s/publish" % aid).status_code == 201


def test_publish_needs_the_phone_system_agent_and_a_model(world):
    aid = make_voice(world, owen_agent="", model="")
    r = world.client("admin").post("/api/ai/agents/%s/publish" % aid)
    assert r.status_code == 400
    assert "phone system agent" in r.json()["detail"] and "model" in r.json()["detail"]
    assert count(AiAgentVersion) == 0


def test_publish_answers_without_asking_owen_main_and_stores_it_not_answering(world, owen):
    aid = make_voice(world)
    r = world.client("admin").post("/api/ai/agents/%s/publish" % aid)
    assert r.status_code == 201
    assert owen.requests == []                       # the request never waits on owen-main
    state = r.json()["phone_system"]
    assert state["label"] == "Published · not yet on the phone system"
    assert state["live"] is False and state["status"] == "pending"
    assert state["answering_calls"] is False
    assert r.json()["live_on_phone_system"] is False
    assert len(jobs("pending")) == 1

    drain()
    assert len(owen.requests) == 1
    method, url, body = owen.requests[0]
    assert (method, url) == ("POST", BASE + "/api/crm-link/agent-versions")
    assert body["agent_name"] == "Intake" and body["crm_version"] == 1
    # Answering calls is Off (every new agent), so it is STORED, not activated.
    assert body["crm_agent_id"] == aid and body["activate"] is False
    cfg = body["config"]
    assert cfg["persona"] == "You answer the phone for Dream Team Roofing."
    assert cfg["tools"] == {"transfer": True, "end_call": True, "capture_lead": True}
    assert cfg["knowledge"] == "We fix roofs." and "knowledge_text" not in cfg
    assert "send_sms" not in cfg["tools"] and "owen_agent" not in cfg

    assert len(owen.versions["Intake"]) == 1 and owen.active() is None
    state = phone(world, aid)
    assert state["live"] is False and state["status"] == "not_answering"
    assert state["label"] == "Not answering calls" and state["answering"] is False
    assert state["owen_version"] == 1 and state["owen_version_id"] == "v-1"
    assert "voicemail" in state["detail"] and "Version 1" in state["detail"]
    assert jobs("done") and not jobs("pending")


def test_a_failed_push_leaves_the_agent_not_yet_live_and_retries(world, owen):
    owen.down = True
    aid = make_voice(world, answering=True)
    assert world.client("admin").post("/api/ai/agents/%s/publish" % aid).status_code == 201
    drain()
    assert len(owen.requests) == 1
    state = phone(world, aid)
    assert state["live"] is False
    assert state["label"] == "Published · not yet live on the phone system"
    assert state["status"] == "retrying"
    assert "Could not reach the phone system" in state["detail"]
    assert "attempt 1 of 10" in state["detail"]
    assert state["pushed_at"] is None and state["owen_version"] is None
    assert world.client("admin").get("/api/ai/agents").json()[0]["live_on_phone_system"] \
        is False
    pending = jobs("pending")
    assert len(pending) == 1 and pending[0].max_attempts == 10
    assert pending[0].run_after.replace(tzinfo=UTC) > datetime.now(UTC)

    # owen-main comes back; the queue's next attempt lands it.
    owen.down = False
    make_due()
    drain()
    state = phone(world, aid)
    assert state["live"] is True and state["attempts"] == 2
    assert state["label"] == "Answering calls"
    assert len(owen.versions["Intake"]) == 1


def test_a_refusal_is_shown_in_words_and_not_retried(world, owen):
    owen.refuse = ("the phone system refused this version: tool 'capture_lead' is not "
                   "implemented by the 'x' engine")
    aid = make_voice(world)
    world.client("admin").post("/api/ai/agents/%s/publish" % aid)
    drain()
    state = phone(world, aid)
    assert state["status"] == "refused" and state["live"] is False
    assert "not implemented by the 'x' engine" in state["detail"]
    assert state["can_retry"] is True
    assert jobs("pending") == [] and len(owen.requests) == 1


def test_an_unknown_phone_system_agent_is_refused_not_created(world, owen):
    aid = make_voice(world, owen_agent="Nobody")
    world.client("admin").post("/api/ai/agents/%s/publish" % aid)
    drain()
    state = phone(world, aid)
    assert state["status"] == "refused"
    assert "no agent named 'Nobody'" in state["detail"]
    assert "Nobody" not in owen.versions


def test_a_second_push_of_the_same_crm_version_makes_no_second_owen_version(world, owen):
    owen.lose_reply = True               # owen-main stores it, and the answer is lost
    aid = make_voice(world, answering=True)
    world.client("admin").post("/api/ai/agents/%s/publish" % aid)
    drain()
    assert len(owen.versions["Intake"]) == 1
    assert phone(world, aid)["live"] is False          # no answer is not "live"
    owen.lose_reply = False
    make_due()
    drain()
    assert [r[2]["crm_version"] for r in owen.requests] == [1, 1]
    assert len(owen.versions["Intake"]) == 1           # ...and still ONE version there
    state = phone(world, aid)
    assert state["live"] is True and state["owen_version"] == 1

    # Retry on a live version is refused and asks nothing.
    r = world.client("admin").post("/api/ai/agents/%s/push" % aid)
    assert r.status_code == 400 and "already live" in r.json()["detail"]
    assert len(owen.requests) == 2


def test_a_newer_publish_supersedes_a_push_in_flight(world, owen):
    owen.down = True
    aid = make_voice(world, answering=True)
    admin = world.client("admin")
    admin.post("/api/ai/agents/%s/publish" % aid)
    drain()
    admin.patch("/api/ai/agents/%s" % aid, json={"draft": voice_draft(greeting="Hello!")})
    admin.post("/api/ai/agents/%s/publish" % aid)
    cancelled = jobs("cancelled")
    assert len(cancelled) == 1 and cancelled[0].dedupe_key is None
    owen.down = False
    make_due()
    drain()
    pushed = [r[2]["crm_version"] for r in owen.requests if r[2]]
    assert pushed == [1, 2]              # 1 failed while down; only 2 ever landed
    assert [v["config"]["crm_version"] for v in owen.versions["Intake"]] == [2]
    assert owen.versions["Intake"][0]["config"]["greeting"] == "Hello!"
    state = phone(world, aid)
    assert state["live"] is True and state["version"] == 2
    db = SessionLocal()
    statuses = sorted(p.status for p in db.scalars(select(AiVoicePush)).all())
    db.close()
    assert statuses == ["live", "superseded"]


def test_the_screen_says_which_version_is_still_running(world, owen):
    aid = make_voice(world, answering=True)
    admin = world.client("admin")
    admin.post("/api/ai/agents/%s/publish" % aid)
    drain()
    owen.down = True
    admin.patch("/api/ai/agents/%s" % aid, json={"draft": voice_draft(greeting="New")})
    admin.post("/api/ai/agents/%s/publish" % aid)
    drain()
    state = phone(world, aid)
    assert state["live"] is False and state["version"] == 2 and state["live_version"] == 1
    assert "still running version 1" in state["detail"]


def test_without_the_link_the_publish_succeeds_and_says_nothing_was_sent(world, monkeypatch):
    monkeypatch.delenv("CRM_LINK_BASE_URL", raising=False)
    monkeypatch.delenv("CRM_LINK_API_KEY", raising=False)

    def refuse(*a, **kw):
        raise AssertionError("a request was attempted with the link unconfigured")
    monkeypatch.setattr(crmlink.httpx, "post", refuse)
    aid = make_voice(world)
    assert world.client("admin").post("/api/ai/agents/%s/publish" % aid).status_code == 201
    drain()
    state = phone(world, aid)
    assert state["status"] == "failed" and state["live"] is False
    assert "CRM_LINK_BASE_URL" in state["detail"] and state["can_retry"] is True
    # Retry queues it again — and still sends nothing.
    r = world.client("admin").post("/api/ai/agents/%s/push" % aid)
    assert r.status_code == 202 and r.json()["phone_system"]["status"] == "pending"
    drain()
    assert phone(world, aid)["status"] == "failed"


def test_retry_after_giving_up_pushes_again(world, owen, monkeypatch):
    from app.ai import push
    monkeypatch.setattr(push, "MAX_ATTEMPTS", 2)
    owen.down = True
    aid = make_voice(world, answering=True)
    world.client("admin").post("/api/ai/agents/%s/publish" % aid)
    drain()
    make_due()
    drain()
    state = phone(world, aid)
    assert state["status"] == "failed" and "Gave up after 2 tries" in state["detail"]
    assert jobs("pending") == []
    owen.down = False
    r = world.client("admin").post("/api/ai/agents/%s/push" % aid)
    assert r.status_code == 202
    drain()
    assert phone(world, aid)["live"] is True


def test_a_dispatcher_cannot_push(world, owen):
    aid = make_voice(world)
    world.client("admin").post("/api/ai/agents/%s/publish" % aid)
    r = world.client("dispatcher").post("/api/ai/agents/%s/push" % aid)
    assert r.status_code == 403
    assert len(jobs()) == 1


def test_duplicating_a_voice_agent_does_not_point_the_copy_at_the_same_phone_agent(world):
    aid = make_voice(world)
    r = world.client("admin").post("/api/ai/agents/%s/duplicate" % aid)
    assert r.status_code == 201
    assert r.json()["draft"]["owen_agent"] == "" and r.json()["channel"] == "voice"


# ------------------------------------------------------------------ the mapping

def test_the_mapping_round_trips_the_live_agent():
    from app.ai import config
    draft, notes = voice.from_owen(LIVE_CONFIG, "Intake")
    c = config.clean(draft)
    assert notes == []
    assert c["persona"] == LIVE_CONFIG["persona"].strip()
    assert c["owen_settings"] == {"tts_instructions": "Warm, unhurried."}
    back = voice.to_owen(c)
    expected = {**LIVE_CONFIG, "persona": LIVE_CONFIG["persona"].strip()}
    assert back == expected
    assert voice.persona_for({"persona": "Just me."}) == "Just me."
    with_rules = voice.persona_for({"persona": "P", "goals": ["G"], "rules_dont": ["D"]})
    assert with_rules == "P\n\nGoals:\n1. G\n\nNever:\n- D"


# ------------------------------------------------------------------ the import

def test_the_import_is_a_dry_run_by_default(world, owen):
    db = SessionLocal()
    code, report = import_voice_agent.run(db, commit=False)
    db.close()
    assert code == 0 and report["outcome"] == "would import" and report["wrote"] is False
    assert count(AiAgent) == 0 and count(AiAgentVersion) == 0 and count(AiVoicePush) == 0
    assert [r[0] for r in owen.requests] == ["GET"]
    assert report["persona_chars"] == len(LIVE_CONFIG["persona"].strip())
    assert report["tools"] == ["transfer", "end_call", "capture_lead"]
    assert report["matches_live"] is True and report["publish_problems"] == []
    # The report is safe to paste: no persona, no knowledge.
    assert LIVE_CONFIG["knowledge"] not in str(report)
    assert "receptionist" not in str(report)


def test_the_import_creates_the_agent_off_with_version_1_recorded_as_live(world, owen):
    db = SessionLocal()
    code, report = import_voice_agent.run(db, commit=True)
    db.close()
    assert code == 0 and report["wrote"] is True
    a = get(AiAgent, report["crm_agent_id"])
    assert a.name == "Intake" and a.channel == "voice"
    # Phase 2c: it IS answering calls (owen-main reported it active) and writes nothing here.
    assert a.answering_calls is True and a.mode == "off"
    assert report["answering_calls"] is True and report["mode"] == "off"
    assert a.draft["owen_agent"] == "Intake"
    v = get(AiAgentVersion, a.published_version_id)
    assert v.version == 1 and voice.to_owen(v.config)["greeting"] == LIVE_CONFIG["greeting"]
    state = phone(world, a.id)
    assert state["live"] is True and state["imported"] is True
    assert state["label"] == "Answering calls" and state["answering_calls"] is True
    assert "Imported from the phone system's version 4" in state["detail"]
    row = world.client("admin").get("/api/ai/agents").json()[0]
    assert row["answering_calls"] is True and row["mode"] == "off"
    assert row["phone_label"] == "Answering calls"
    assert [r[0] for r in owen.requests] == ["GET"]     # nothing pushed: it is already there
    assert jobs() == []

    # Again: already imported, nothing written, nothing overwritten.
    world.client("admin").patch("/api/ai/agents/%s" % a.id, json={
        "draft": {**world.client("admin").get("/api/ai/agents/%s" % a.id).json()["draft"],
                  "greeting": "Edited in the CRM"}})
    db = SessionLocal()
    code, report = import_voice_agent.run(db, commit=True)
    db.close()
    assert code == 0 and report["outcome"] == "already imported" and not report["wrote"]
    assert count(AiAgent) == 1 and count(AiAgentVersion) == 1
    assert get(AiAgent, a.id).draft["greeting"] == "Edited in the CRM"


def test_the_import_never_overwrites_an_agent_of_that_name(world, owen):
    r = world.client("admin").post("/api/ai/agents", json={"name": "intake"})
    existing = r.json()["id"]
    before = get(AiAgent, existing).draft
    db = SessionLocal()
    code, report = import_voice_agent.run(db, commit=True)
    db.close()
    assert code == 0 and report["outcome"] == "already imported"
    assert report["crm_agent_id"] == existing
    assert count(AiAgent) == 1 and get(AiAgent, existing).draft == before


def test_the_import_drops_send_sms_and_does_not_claim_live(world, owen, monkeypatch):
    live = {**LIVE_CONFIG, "tools": {**LIVE_CONFIG["tools"], "send_sms": True}}
    monkeypatch.setattr(crmlink.httpx, "get", lambda *a, **k: FakeResponse(200, {"agents": [
        {"agent_id": "a-Intake", "name": "Intake",
         "active_version": {"id": "live-4", "version": 4, "config": live}}]}))
    db = SessionLocal()
    code, report = import_voice_agent.run(db, commit=True)
    db.close()
    assert code == 0 and any("send_sms" in n for n in report["notes"])
    assert report["matches_live"] is False
    a = get(AiAgent, report["crm_agent_id"])
    assert "send_text" not in a.draft["actions"]
    # Still answering (with owen-main's OWN version 4) — never "live", never "not answering".
    assert a.answering_calls is True and a.mode == "off"
    state = phone(world, a.id)
    assert state["live"] is False and state["status"] == "other_answering"
    assert state["label"] == "Answering calls — not with this version"
    assert state["answering"] is True and "version 4 is" in state["detail"]
    assert state["can_retry"] is True


@pytest.mark.parametrize("wanted, code", [(None, 0), ("Old", 4), ("Nope", 4)])
def test_the_import_chooses_only_an_agent_with_an_active_version(world, owen, wanted, code):
    db = SessionLocal()
    got, report = import_voice_agent.run(db, wanted=wanted)
    db.close()
    assert got == code
    assert count(AiAgent) == 0


def test_the_import_without_the_link_asks_nothing(world, monkeypatch):
    monkeypatch.delenv("CRM_LINK_BASE_URL", raising=False)
    monkeypatch.delenv("CRM_LINK_API_KEY", raising=False)
    db = SessionLocal()
    code, report = import_voice_agent.run(db, commit=True)
    db.close()
    assert code == 2 and "CRM_LINK_BASE_URL" in report["error"] and count(AiAgent) == 0


# ------------------------------------------------------------------ phase 2c: answering calls

def last_post(owen):
    return [r[2] for r in owen.requests if r[0] == "POST"][-1]


def push_jobs():
    return len(jobs())


def test_switching_on_activates_the_stored_version_and_makes_no_new_one(world, owen):
    aid = make_voice(world)
    world.client("admin").post("/api/ai/agents/%s/publish" % aid)
    drain()
    assert len(owen.versions["Intake"]) == 1 and owen.active() is None   # stored, not active

    # Unconfirmed: refused with a sentence, and nothing changes or is queued.
    before = push_jobs()
    r = switch(world, aid, True, confirm=False)
    assert r.status_code == 409 and "Confirm to switch it on" in r.json()["detail"]
    assert "version 1" in r.json()["detail"]
    assert get(AiAgent, aid).answering_calls is False and push_jobs() == before

    r = switch(world, aid, True)
    assert r.status_code == 200
    assert r.json()["answering_calls"] is True and r.json()["mode"] == "off"
    assert r.json()["phone_system"]["label"] == "Published · not yet live on the phone system"
    drain()
    body = last_post(owen)
    assert body["crm_version"] == 1 and body["activate"] is True
    assert len(owen.versions["Intake"]) == 1                 # NO new version on owen-main
    assert owen.active() == 1                                # ...the stored one is active
    state = phone(world, aid)
    assert state["status"] == "live" and state["label"] == "Answering calls"
    assert state["answering"] is True and state["owen_version"] == 1
    assert get(AiAgent, aid).mode == "off"                  # the switch never moves the mode


def test_switching_off_deactivates_and_callers_go_to_the_fallback(world, owen):
    aid = make_voice(world, answering=True)
    world.client("admin").post("/api/ai/agents/%s/publish" % aid)
    drain()
    assert owen.active() == 1 and phone(world, aid)["label"] == "Answering calls"

    r = switch(world, aid, False, confirm=False)
    assert r.status_code == 409 and "voicemail" in r.json()["detail"]
    assert get(AiAgent, aid).answering_calls is True

    r = switch(world, aid, False)
    assert r.status_code == 200
    state = r.json()["phone_system"]
    # Not confirmed yet: it must not claim either state.
    assert state["label"] == "Switching off · not confirmed by the phone system"
    assert state["live"] is False and state["answering"] is True
    drain()
    assert last_post(owen) == {"agent_name": "Intake", "deactivate": True}
    assert owen.active() is None
    assert len(owen.versions["Intake"]) == 1                 # nothing deleted
    state = phone(world, aid)
    assert state["status"] == "not_answering" and state["label"] == "Not answering calls"
    assert state["answering"] is False and "voicemail" in state["detail"]
    row = world.client("admin").get("/api/ai/agents").json()[0]
    assert row["answering_calls"] is False and row["phone_label"] == "Not answering calls"
    assert row["live_on_phone_system"] is False

    # ...and back on: the SAME stored version, still one on owen-main.
    switch(world, aid, True)
    drain()
    assert owen.active() == 1 and len(owen.versions["Intake"]) == 1
    assert phone(world, aid)["label"] == "Answering calls"


def test_publish_while_answering_is_stored_and_active(world, owen):
    aid = make_voice(world, answering=True)
    admin = world.client("admin")
    admin.post("/api/ai/agents/%s/publish" % aid)
    drain()
    assert last_post(owen)["activate"] is True and owen.active() == 1
    admin.patch("/api/ai/agents/%s" % aid, json={"draft": voice_draft(greeting="Hi there")})
    admin.post("/api/ai/agents/%s/publish" % aid)
    drain()
    assert last_post(owen)["activate"] is True and last_post(owen)["crm_version"] == 2
    assert owen.active() == 2 and len(owen.versions["Intake"]) == 2
    state = phone(world, aid)
    assert state["live"] is True and state["version"] == 2 and state["live_version"] == 2
    db = SessionLocal()
    statuses = sorted(p.status for p in db.scalars(select(AiVoicePush)).all())
    db.close()
    assert statuses == ["live", "not_answering"]     # version 1 no longer claims live


def test_publish_while_not_answering_leaves_what_is_answering_alone(world, owen):
    """A new CRM agent pointed at a phone-system agent that is already answering must not
    take it off the phone just by publishing. It is stored, and the screen says who answers."""
    owen.versions["Intake"].append({"id": "hand-1", "version": 1,
                                    "config": {"persona": "hand-written", "crm_version": None}})
    owen.live["Intake"] = "hand-1"
    aid = make_voice(world)
    world.client("admin").post("/api/ai/agents/%s/publish" % aid)
    drain()
    assert last_post(owen)["activate"] is False and owen.active() == 1   # untouched
    state = phone(world, aid)
    assert state["status"] == "other_answering" and state["answering"] is True
    assert state["label"] == "Answering calls — not with this version"
    assert "version 1 is" in state["detail"] and "Take it off the phone" in state["detail"]
    # Switching it off (it already reads Off) is how it comes off the phone.
    switch(world, aid, False)
    drain()
    assert owen.active() is None and phone(world, aid)["label"] == "Not answering calls"


def test_a_switch_off_on_its_way_survives_a_publish(world, owen):
    aid = make_voice(world, answering=True)
    admin = world.client("admin")
    admin.post("/api/ai/agents/%s/publish" % aid)
    drain()
    owen.down = True
    switch(world, aid, False)
    drain()
    assert phone(world, aid)["status"] == "retrying"
    assert phone(world, aid)["label"] == "Switching off · not confirmed by the phone system"
    admin.patch("/api/ai/agents/%s" % aid, json={"draft": voice_draft(greeting="Later")})
    admin.post("/api/ai/agents/%s/publish" % aid)
    owen.down = False
    make_due()
    drain()
    assert last_post(owen) == {"agent_name": "Intake", "deactivate": True}
    assert owen.active() is None
    assert phone(world, aid)["label"] == "Not answering calls"


def test_switching_on_before_publishing_is_carried_by_the_publish(world, owen):
    aid = make_voice(world)
    r = switch(world, aid, True)
    assert r.status_code == 200 and owen.requests == [] and jobs() == []
    state = r.json()["phone_system"]
    assert state["status"] == "unpublished" and "once it is there" in state["detail"]
    r = switch(world, aid, True, confirm=False)
    assert "once it is published" in r.json()["detail"]
    world.client("admin").post("/api/ai/agents/%s/publish" % aid)
    drain()
    assert last_post(owen)["activate"] is True and owen.active() == 1


def test_a_text_agent_refuses_the_switch(world, owen):
    r = world.client("admin").post("/api/ai/agents", json={"name": "Texter"})
    aid = r.json()["id"]
    assert r.json()["answering_calls"] is None and r.json()["phone_label"] is None
    for on in (True, False):
        r = switch(world, aid, on)
        assert r.status_code == 400 and "only a voice agent" in r.json()["detail"]
    assert get(AiAgent, aid).answering_calls is False
    assert owen.requests == [] and jobs() == []


def test_only_an_admin_switches_answering(world, owen):
    aid = make_voice(world, answering=True)
    world.client("admin").post("/api/ai/agents/%s/publish" % aid)
    drain()
    sent = len(owen.requests)
    for on in (True, False):
        assert switch(world, aid, on, who="dispatcher").status_code == 403
    assert get(AiAgent, aid).answering_calls is True and len(owen.requests) == sent
    assert jobs("pending") == []


def test_the_mode_never_moves_the_switch(world, owen):
    aid = make_voice(world)
    admin = world.client("admin")
    admin.post("/api/ai/agents/%s/publish" % aid)
    drain()
    sent = len(owen.requests)
    for mode in ("suggest", "off"):
        r = admin.post("/api/ai/agents/%s/mode" % aid, json={"mode": mode})
        assert r.status_code == 200 and r.json()["mode"] == mode
        assert r.json()["answering_calls"] is False
    assert len(owen.requests) == sent and jobs("pending") == []
    assert owen.active() is None

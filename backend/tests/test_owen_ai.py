"""Owen AI, the owner's own assistant (2026-11-02, app/owen_ai).

Pinned, by behaviour:
  * ONE person: an ADMIN whose email is OWEN_AI_OWNER_EMAIL. Every route answers 404 to anyone
    else — another admin, a dispatcher — and to everyone while the email is unset;
  * daily = one per day, job = one per job, task = always new; a message is QUEUED and a second
    one in the same thread waits (409);
  * the runner answers through the turn function it is given (tests never start the CLI): the
    answer, its tools and the Claude session are saved, the next turn resumes that session; off,
    no login or a registry Never is an answer marked error, not a crash; a message left RUNNING
    by a restart becomes an error, never re-run;
  * Phase 1 is read-only: inside `owen_ai_read` any GET the denylist allows goes out and every
    write is refused before a connection; Quo is GET-only on its listed paths.
"""
import httpx
import pytest
from app.auth import mint_api_token
from app.main import app
from app.models import OwenAiMessage, OwenAiThread, Role, User
from app.owen_ai import agent, quo, runner, tools
from app.registry import catalog
from app.zuper import client
from app.zuper.client import ZuperError
from fastapi.testclient import TestClient
from tests.zuper_support import zuper_env  # noqa: F401  (fixture)

OWNER = "owner@dtr.test"


@pytest.fixture()
def people(db, monkeypatch):
    monkeypatch.setenv("OWEN_AI_ENABLED", "true")
    monkeypatch.setenv("OWEN_AI_OWNER_EMAIL", OWNER.upper())      # case does not matter
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-test")
    out = {}
    for key, email, role in (("owner", OWNER, Role.ADMIN), ("admin", "a@dtr.test", Role.ADMIN),
                             ("dispatcher", "d@dtr.test", Role.DISPATCHER)):
        u = User(email=email, name=key.title(), role=role)
        db.add(u)
        db.flush()
        plain, tok = mint_api_token(u, name=key)
        db.add(tok)
        out[key] = (u.id, {"Authorization": "Bearer " + plain})
    db.commit()
    return out


def call(method, path, who, people, **kw):
    return TestClient(app).request(method, "/api/owen-ai" + path, headers=people[who][1], **kw)


# ------------------------------------------------------------------------------- access

ROUTES = [("GET", "/status", None), ("GET", "/threads", None),
          ("POST", "/threads", {"kind": "task"}), ("GET", "/threads/1", None),
          ("PATCH", "/threads/1", {"archived": True}),
          ("POST", "/threads/1/messages", {"content": "hi"})]


@pytest.mark.parametrize("who", ["admin", "dispatcher"])
@pytest.mark.parametrize("method,path,body", ROUTES)
def test_anyone_but_the_owner_gets_404_everywhere(db, people, who, method, path, body):
    assert call(method, path, who, people, json=body).status_code == 404


def test_with_no_owner_email_nobody_gets_in(db, people, monkeypatch):
    monkeypatch.delenv("OWEN_AI_OWNER_EMAIL")
    assert call("GET", "/status", "owner", people).status_code == 404


def test_the_owner_sees_status(db, people):
    r = call("GET", "/status", "owner", people)
    assert r.status_code == 200
    assert r.json()["enabled"] is True and r.json()["claude_login"] is True
    assert r.json()["sentence"] is None


def test_another_users_thread_is_404(db, people):
    th = OwenAiThread(user_id=people["admin"][0], kind="task", title="theirs")
    db.add(th)
    db.commit()
    assert call("GET", "/threads/%d" % th.id, "owner", people).status_code == 404


# ------------------------------------------------------------------------------ threads

def test_daily_is_one_per_day_job_one_per_job_task_always_new(db, people):
    d1 = call("POST", "/threads", "owner", people, json={"kind": "daily"}).json()
    d2 = call("POST", "/threads", "owner", people, json={"kind": "daily"}).json()
    assert d1["id"] == d2["id"] and d1["title"].startswith("Daily")
    j1 = call("POST", "/threads", "owner", people,
              json={"kind": "job", "job_number": "#701"}).json()
    j2 = call("POST", "/threads", "owner", people,
              json={"kind": "job", "job_number": "701"}).json()
    assert j1["id"] == j2["id"] and j1["title"] == "Job #701" and j1["job_number"] == "701"
    t1 = call("POST", "/threads", "owner", people, json={"kind": "task"}).json()
    t2 = call("POST", "/threads", "owner", people, json={"kind": "task"}).json()
    assert t1["id"] != t2["id"]
    assert call("POST", "/threads", "owner", people, json={"kind": "job"}).status_code == 422


def test_a_message_is_queued_and_a_second_waits(db, people):
    th = call("POST", "/threads", "owner", people, json={"kind": "task"}).json()
    r = call("POST", "/threads/%d/messages" % th["id"], "owner", people,
             json={"content": "What proposals are waiting?"})
    assert r.status_code == 200 and r.json()["status"] == "queued"
    again = call("POST", "/threads/%d/messages" % th["id"], "owner", people,
                 json={"content": "and?"})
    assert again.status_code == 409
    got = call("GET", "/threads/%d" % th["id"], "owner", people).json()
    assert got["busy"] is True and got["title"] == "What proposals are waiting?"


def test_off_refuses_new_messages(db, people, monkeypatch):
    th = call("POST", "/threads", "owner", people, json={"kind": "task"}).json()
    monkeypatch.setenv("OWEN_AI_ENABLED", "false")
    r = call("POST", "/threads/%d/messages" % th["id"], "owner", people, json={"content": "hi"})
    assert r.status_code == 409 and "OWEN_AI_ENABLED" in r.json()["detail"]


# ------------------------------------------------------------------------------- runner

class FakeTurn:
    def __init__(self, **result):
        self.calls = []
        self.result = result

    def __call__(self, prompt, session_id, note):
        self.calls.append((prompt, session_id, note))
        return agent.TurnResult(**{"text": "Two proposals are waiting.", "session_id": "s-1",
                                   "steps": [{"tool": "zuper_get",
                                              "input": {"path": "/estimate"},
                                              "result": "[]"}],
                                   "model": "claude-x", **self.result})


def _queue(db, people, content="Any proposals waiting?", **thread):
    th = OwenAiThread(user_id=people["owner"][0], **{"kind": "task", "title": "t", **thread})
    db.add(th)
    db.flush()
    m = OwenAiMessage(thread_id=th.id, role="user", content=content, status="queued")
    db.add(m)
    db.commit()
    return th, m


def test_the_runner_answers_saves_tools_and_resumes_the_session(db, people):
    th, m = _queue(db, people, kind="job", job_uid="uid-701", job_number="701")
    turn = FakeTurn()
    assert runner.run_once(db, turn) is True
    db.refresh(m)
    db.refresh(th)
    assert m.status == "done" and th.session_id == "s-1"
    prompt, session, note = turn.calls[0]
    assert prompt == "Any proposals waiting?" and session is None and "uid-701" in note
    reply = db.query(OwenAiMessage).filter_by(thread_id=th.id, role="assistant").one()
    assert reply.content == "Two proposals are waiting." and reply.steps[0]["tool"] == "zuper_get"
    db.add(OwenAiMessage(thread_id=th.id, role="user", content="and the next?", status="queued"))
    db.commit()
    runner.run_once(db, turn)
    assert turn.calls[1][1] == "s-1"                       # resumed the same Claude session
    assert runner.run_once(db, turn) is False              # nothing left


@pytest.mark.parametrize("env,expect", [("OWEN_AI_ENABLED", "OWEN_AI_ENABLED"),
                                        ("CLAUDE_CODE_OAUTH_TOKEN", "no Claude login")])
def test_off_or_no_login_is_an_error_answer_and_no_turn(db, people, monkeypatch, env, expect):
    monkeypatch.delenv(env)
    th, m = _queue(db, people)
    turn = FakeTurn()
    runner.run_once(db, turn)
    assert turn.calls == []
    reply = db.query(OwenAiMessage).filter_by(thread_id=th.id, role="assistant").one()
    assert reply.status == "error" and expect in reply.error


def test_a_registry_never_stops_the_chat(db, people, monkeypatch):
    from app.registry import service
    monkeypatch.setattr(service, "level", lambda db, key, action: catalog.NEVER)
    th, m = _queue(db, people)
    turn = FakeTurn()
    runner.run_once(db, turn)
    assert turn.calls == []
    assert db.query(OwenAiMessage).filter_by(role="assistant").one().status == "error"


def test_a_failed_turn_is_saved_as_an_error(db, people):
    th, m = _queue(db, people)
    runner.run_once(db, FakeTurn(text="", error="CLINotFoundError: no cli"))
    reply = db.query(OwenAiMessage).filter_by(role="assistant").one()
    db.refresh(m)
    assert reply.status == "error" and "CLINotFoundError" in reply.error and m.status == "error"


def test_a_message_left_running_by_a_restart_becomes_an_error(db, people):
    th, m = _queue(db, people)
    m.status = "running"
    db.commit()
    assert runner.recover(db) == 1
    db.refresh(m)
    assert m.status == "error" and "Send it again" in m.error


def test_the_registry_lists_owen_ai():
    e = next(e for e in catalog.ENTRIES if e.key == "owen_ai")
    assert e.action("ai_chat") is not None


# ---------------------------------------------------------------------- read-only tools

class Recorder:
    def __init__(self, payload=None):
        self.requests = []
        self.payload = payload if payload is not None else {"type": "success", "data": []}

    def __call__(self, request):
        self.requests.append(request)
        return httpx.Response(200, json=self.payload)


def test_owen_ai_reads_any_zuper_get_but_nothing_else(zuper_env):  # noqa: F811
    rec = Recorder()
    client.TRANSPORT = httpx.MockTransport(rec)
    # Not on the sync's allowlist, still readable inside the scope:
    out = tools.zuper_get("/invoice_estimate/proposal_template", {"count": 50})
    assert '"success"' in out
    assert rec.requests[0].method == "GET"
    assert rec.requests[0].url.path.endswith("/invoice_estimate/proposal_template")
    with client.owen_ai_read():
        for method, path in (("POST", "/jobs"), ("PUT", "/jobs"), ("DELETE", "/jobs/x/delete"),
                             ("POST", "/estimate")):
            with pytest.raises(ZuperError):
                client.request(method, path, body={})
    # The denylist still holds for GETs, and outside the scope the allowlist rules again.
    assert tools.zuper_get("/messages").startswith("ERROR")
    with pytest.raises(ZuperError, match="allowlist"):
        client.request("GET", "/invoice_estimate/proposal_template")
    assert len(rec.requests) == 1


def test_a_big_answer_is_cut_with_a_sentence(zuper_env, monkeypatch):  # noqa: F811
    client.TRANSPORT = httpx.MockTransport(Recorder({"data": ["x" * 100] * 1000}))
    monkeypatch.setattr(tools.config, "TOOL_RESULT_MAX_CHARS", 500)
    out = tools.zuper_get("/jobs")
    assert len(out) < 700 and "fewer rows" in out


def test_quo_is_get_only_on_its_listed_paths(monkeypatch):
    monkeypatch.setenv("OWEN_AI_QUO_API_KEY", "quo-test")
    rec = Recorder({"data": []})
    monkeypatch.setattr(quo, "TRANSPORT", httpx.MockTransport(rec))
    assert tools.quo_get("/v1/phone-numbers") == '{"data": []}'
    assert rec.requests[0].headers["Authorization"] == "quo-test"
    assert rec.requests[0].url.host == "api.openphone.com"
    assert not quo.allowed("POST", "/messages")
    assert tools.quo_get("/webhooks").startswith("ERROR")
    assert len(rec.requests) == 1


def test_quo_without_a_key_says_so(monkeypatch):
    monkeypatch.delenv("OWEN_AI_QUO_API_KEY", raising=False)
    assert "OWEN_AI_QUO_API_KEY" in tools.quo_get("/phone-numbers")


def test_a_saved_login_file_counts_as_a_login(monkeypatch, tmp_path):
    from app.owen_ai import config
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert config.token_set() is False
    (tmp_path / ".credentials.json").write_text('{"claudeAiOauth": {}}')
    assert config.token_set() is True

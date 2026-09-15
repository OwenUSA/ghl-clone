"""Shared fixtures for the AI Agents tests. Not collected (no `test_` prefix).

Providers are mocked at the HTTP BOUNDARY: `app.ai.providers.HTTP_CLIENT_FACTORY` hands the
official SDKs an httpx2 client on a MockTransport, so the real `anthropic` / `openai` code
builds every request and parses every response. Nothing leaves the process — and the
package-wide guard in conftest.py fails any test that tries.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx2
import pytest
from app import auth
from app.ai import providers
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    AiAgent,
    AiAgentVersion,
    AiConnection,
    Calendar,
    Contact,
    Opportunity,
    Pipeline,
    Role,
    Stage,
    User,
)
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

SECRET_KEY = "sk-ant-api03-THIS-IS-THE-SECRET-9Z8Y"


# ------------------------------------------------------------------ fake providers

def anthropic_message(content: list[dict], stop: str = "end_turn", *, input_tokens=100,
                      output_tokens=20, cache_write=0, cache_read=0,
                      model="claude-sonnet-5") -> dict:
    return {"id": "msg_test", "type": "message", "role": "assistant", "model": model,
            "content": content, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                      "cache_creation_input_tokens": cache_write,
                      "cache_read_input_tokens": cache_read}}


def text(t: str) -> dict:
    return {"type": "text", "text": t}


def tool_use(name: str, args: dict, id_: str | None = None) -> dict:
    return {"type": "tool_use", "id": id_ or "toolu_" + name, "name": name, "input": args}


def openai_completion(*, content: str | None = None, tool_calls: list | None = None,
                      finish: str = "stop", prompt_tokens=100, completion_tokens=20,
                      cached=0, refusal: str | None = None, model="gpt-5-mini") -> dict:
    message = {"role": "assistant", "content": content, "refusal": refusal}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                      "total_tokens": prompt_tokens + completion_tokens,
                      "prompt_tokens_details": {"cached_tokens": cached}}}


def openai_call(name: str, arguments: str, id_: str = "call_1") -> dict:
    return {"id": id_, "type": "function", "function": {"name": name, "arguments": arguments}}


@dataclass
class Script:
    """Answers each provider request with the next scripted response and records it."""
    responses: list = field(default_factory=list)
    requests: list = field(default_factory=list)

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append({"method": request.method, "url": str(request.url),
                              "headers": dict(request.headers), "body": body})
        if not self.responses:
            return httpx2.Response(500, json={"error": {"message": "script exhausted"}})
        nxt = self.responses.pop(0)
        if isinstance(nxt, httpx2.Response):
            return nxt
        if isinstance(nxt, tuple):
            status, payload = nxt
            return httpx2.Response(status, json=payload)
        return httpx2.Response(200, json=nxt)

    @property
    def calls(self) -> int:
        return len(self.requests)


@pytest.fixture()
def script(monkeypatch):
    s = Script()
    # One request per scripted response: a retried 429 would silently eat the next answer.
    monkeypatch.setattr(providers, "MAX_RETRIES", 0)
    monkeypatch.setattr(providers, "HTTP_CLIENT_FACTORY",
                        lambda: httpx2.Client(transport=httpx2.MockTransport(s.handler)))
    return s


# ------------------------------------------------------------------ the world

@pytest.fixture()
def secrets_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("AI_SECRETS_KEY", key)
    return key


@dataclass
class World:
    ids: dict
    tokens: dict

    def client(self, who: str | None) -> TestClient:
        c = TestClient(app)
        if who:
            c.headers["Authorization"] = "Bearer " + self.tokens[who]
        return c


@pytest.fixture()
def world(secrets_key):
    """Four users (admin, dispatcher, tech, a restricted dispatcher), a customer with a
    deal, a second customer with a deal, a calendar, and a saved Anthropic connection."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    users = {}
    for key, role, restricted in (("admin", Role.ADMIN, False),
                                  ("dispatcher", Role.DISPATCHER, False),
                                  ("tech", Role.TECH, False),
                                  ("restricted", Role.DISPATCHER, True)):
        u = User(email="%s@ai.test" % key, name=key.title(), role=role,
                 only_assigned_data=restricted)
        db.add(u)
        users[key] = u
    db.flush()
    tokens = {}
    for key, u in users.items():
        plain, tok = auth.mint_api_token(u, name=key)
        db.add(tok)
        tokens[key] = plain
    pipe = Pipeline(name="Retail")
    db.add(pipe)
    db.flush()
    s1, s2, s3 = (Stage(pipeline_id=pipe.id, name=n, position=i)
                  for i, n in enumerate(("New Lead", "Inspection", "Won")))
    db.add_all([s1, s2, s3])
    jane = Contact(first_name="Jane", last_name="Doe", phone="+19415550101",
                   email="jane@ai.test")
    bob = Contact(first_name="Bob", last_name="Other", phone="+19415550202")
    db.add_all([jane, bob])
    db.flush()
    deal = Opportunity(title="Jane roof", contact_id=jane.id, pipeline_id=pipe.id,
                       stage_id=s1.id)
    other = Opportunity(title="Bob roof", contact_id=bob.id, pipeline_id=pipe.id,
                        stage_id=s1.id)
    cal = Calendar(name="Crew", user_id=users["admin"].id)
    db.add_all([deal, other, cal])
    db.commit()
    ids = {"pipeline": pipe.id, "s1": s1.id, "s2": s2.id, "s3": s3.id, "jane": jane.id,
           "bob": bob.id, "deal": deal.id, "other_deal": other.id, "calendar": cal.id,
           **{"user_" + k: u.id for k, u in users.items()}}
    db.close()
    w = World(ids, tokens)
    r = w.client("admin").post("/api/ai/connections", json={
        "name": "Claude", "provider": "anthropic", "api_key": SECRET_KEY,
        "default_model": "claude-sonnet-5", "price_input": "2.00", "price_output": "10.00"})
    assert r.status_code == 201, r.text
    w.ids["connection"] = r.json()["id"]
    return w


def config_for(w: World, **over) -> dict:
    from app.ai.config import default_config
    c = default_config()
    c.update({"persona": "You are Dream Team Roofing's friendly assistant.",
              "goals": ["Book an inspection"], "connection_id": w.ids["connection"],
              "model": "claude-sonnet-5", "actions": ["get_context", "send_text",
                                                      "move_stage"],
              "triggers": [{"type": "manual"}], "max_messages": 3})
    c.update(over)
    return c


def make_agent(w: World, *, mode: str = "auto", publish: bool = True, name="Follow-up",
               **over) -> int:
    """An agent created, drafted and published through the API, then switched on."""
    admin = w.client("admin")
    r = admin.post("/api/ai/agents", json={"name": name})
    assert r.status_code == 201, r.text
    assert r.json()["mode"] == "off"
    aid = r.json()["id"]
    r = admin.patch("/api/ai/agents/%s" % aid, json={"draft": config_for(w, **over)})
    assert r.status_code == 200, r.text
    if publish:
        r = admin.post("/api/ai/agents/%s/publish" % aid)
        assert r.status_code == 201, r.text
    if mode != "off":
        r = admin.post("/api/ai/agents/%s/mode" % aid, json={"mode": mode, "confirm": True})
        assert r.status_code == 200, r.text
    return aid


def get(model, id_):
    db = SessionLocal()
    try:
        obj = db.get(model, id_)
        if obj is not None:
            db.expunge(obj)
        return obj
    finally:
        db.close()


def count(model, *where) -> int:
    from sqlalchemy import func, select
    db = SessionLocal()
    try:
        stmt = select(func.count()).select_from(model)
        for clause in where:
            stmt = stmt.where(clause)
        return db.scalar(stmt) or 0
    finally:
        db.close()


def drain() -> int:
    from app.worker import drain_once
    total = 0
    while True:
        n = drain_once()
        total += n
        if n == 0:
            return total


__all__ = ["AiAgent", "AiAgentVersion", "AiConnection"]

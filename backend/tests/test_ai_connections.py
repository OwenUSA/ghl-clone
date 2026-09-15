"""Settings → AI Connections: the API key is encrypted at rest, never returned, never logged;
Test answers with a sentence and writes nothing; no AI_SECRETS_KEY means no saving."""
import json
import logging

import pytest
from app.ai import pricing, vault
from app.db import SessionLocal
from app.models import AiConnection
from sqlalchemy import text as sql
from tests.ai_support import (
    SECRET_KEY,
    anthropic_message,
    count,
    get,
    openai_completion,
    text,
)


def _raw_key_column(connection_id: int) -> str:
    db = SessionLocal()
    try:
        return db.execute(sql("SELECT api_key_encrypted FROM ai_connections WHERE id = :i"),
                          {"i": connection_id}).scalar_one()
    finally:
        db.close()


def test_the_key_is_encrypted_at_rest_and_never_comes_back(world, caplog):
    caplog.set_level(logging.DEBUG)
    cid = world.ids["connection"]
    raw = _raw_key_column(cid)
    assert raw != SECRET_KEY and SECRET_KEY not in raw
    assert vault.decrypt(raw) == SECRET_KEY            # it IS the key, under the Fernet key
    admin = world.client("admin")
    bodies = [admin.get("/api/ai/connections").text,
              admin.patch("/api/ai/connections/%s" % cid, json={"name": "Claude 2"}).text,
              admin.get("/api/ai/catalogue").text,
              admin.get("/api/ai/connection-names").text]
    for body in bodies:
        assert SECRET_KEY not in body and SECRET_KEY[-12:] not in body
    listed = admin.get("/api/ai/connections").json()[0]
    assert listed["api_key"] == "•••• " + SECRET_KEY[-4:]
    assert SECRET_KEY not in caplog.text


def test_saving_is_refused_with_a_sentence_without_ai_secrets_key(world, monkeypatch):
    monkeypatch.delenv("AI_SECRETS_KEY")
    before = count(AiConnection)
    r = world.client("admin").post("/api/ai/connections", json={
        "name": "X", "provider": "openai", "api_key": "sk-proj-abcdefgh12345678",
        "default_model": "gpt-5-mini"})
    assert r.status_code == 503
    assert "AI_SECRETS_KEY" in r.json()["detail"] and "Fernet" in r.json()["detail"]
    assert count(AiConnection) == before
    # ...and nothing else breaks: the module still reads.
    assert world.client("admin").get("/api/ai/agents").status_code == 200
    assert world.client("admin").get("/api/ai/catalogue").json()["secrets_configured"] is False


def test_rotating_the_key_to_blank_on_edit_keeps_the_stored_key(world):
    cid = world.ids["connection"]
    before = _raw_key_column(cid)
    world.client("admin").patch("/api/ai/connections/%s" % cid,
                                json={"api_key": "", "default_model": "claude-opus-5"})
    assert _raw_key_column(cid) == before
    new = "sk-ant-api03-ROTATED-KEY-0000"
    world.client("admin").patch("/api/ai/connections/%s" % cid, json={"api_key": new})
    assert vault.decrypt(_raw_key_column(cid)) == new
    assert get(AiConnection, cid).api_key_last4 == "0000"


def test_test_reports_ok_with_latency_and_writes_nothing(world, script):
    script.responses.append(anthropic_message([text("OK")]))
    before = [(c.id, c.name, c.default_model, c.updated_at) for c in
              SessionLocal().query(AiConnection).all()]
    r = world.client("admin").post("/api/ai/connections/test", json={
        "provider": "anthropic", "connection_id": world.ids["connection"],
        "model": "claude-sonnet-5"})
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True
    assert body["sentence"].startswith("Connected") and isinstance(body["latency_ms"], int)
    sent = script.requests[0]
    assert sent["url"].startswith("https://api.anthropic.com/v1/messages")
    assert sent["headers"]["x-api-key"] == SECRET_KEY        # the key goes to the provider…
    assert "temperature" not in sent["body"] and "top_p" not in sent["body"]
    assert "thinking" not in sent["body"]
    assert SECRET_KEY not in r.text                          # …and never back to the browser
    after = [(c.id, c.name, c.default_model, c.updated_at) for c in
             SessionLocal().query(AiConnection).all()]
    assert after == before


@pytest.mark.parametrize("status,payload,expected", [
    (401, {"type": "error", "error": {"type": "authentication_error",
                                      "message": "invalid x-api-key"}}, "rejected the API key"),
    (404, {"type": "error", "error": {"type": "not_found_error", "message": "model"}},
     "does not know the model"),
    (429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}},
     "rate limiting"),
])
def test_test_reports_the_error_as_a_sentence(world, script, status, payload, expected):
    script.responses.extend([(status, payload)] * 3)
    r = world.client("admin").post("/api/ai/connections/test", json={
        "provider": "anthropic", "api_key": "sk-ant-typed-but-unsaved-1234",
        "model": "claude-nope"})
    body = r.json()
    assert body["ok"] is False and expected in body["sentence"]
    assert "sk-ant-typed" not in r.text
    assert count(AiConnection) == 1


def test_an_openai_401_that_quotes_the_key_is_redacted(world, script):
    typed = "sk-proj-LEAKYLEAKY123456"
    script.responses.extend([(400, {"error": {"message": "Bad thing with key %s" % typed}})] * 2)
    r = world.client("admin").post("/api/ai/connections/test", json={
        "provider": "openai", "api_key": typed, "model": "gpt-5-mini"})
    assert r.json()["ok"] is False
    assert typed not in r.text and "LEAKY" not in r.text


def test_openai_compatible_needs_a_base_url_and_uses_it(world, script):
    admin = world.client("admin")
    r = admin.post("/api/ai/connections", json={
        "name": "Local", "provider": "openai_compatible", "api_key": "anything-12345678",
        "default_model": "llama3.1"})
    assert r.status_code == 400 and "base URL" in r.json()["detail"]
    script.responses.append(openai_completion(content="OK", model="llama3.1"))
    r = admin.post("/api/ai/connections/test", json={
        "provider": "openai_compatible", "base_url": "http://llm.internal:8080/v1",
        "api_key": "anything-12345678", "model": "llama3.1"})
    assert r.json()["ok"] is True
    assert script.requests[0]["url"] == "http://llm.internal:8080/v1/chat/completions"
    assert "max_tokens" in script.requests[0]["body"]          # what compatible servers read


def test_load_models_lists_what_the_provider_lists(world, script):
    script.responses.append({"data": [
        {"type": "model", "id": "claude-sonnet-5", "display_name": "Sonnet 5",
         "created_at": "2026-01-01T00:00:00Z"},
        {"type": "model", "id": "claude-opus-5", "display_name": "Opus 5",
         "created_at": "2026-01-01T00:00:00Z"}], "has_more": False,
        "first_id": "claude-sonnet-5", "last_id": "claude-opus-5"})
    r = world.client("admin").post("/api/ai/connections/models", json={
        "provider": "anthropic", "connection_id": world.ids["connection"]})
    assert r.json()["models"] == ["claude-opus-5", "claude-sonnet-5"]


def test_prices_are_micro_dollars_and_an_unknown_price_is_no_cost():
    assert pricing.to_micros("2.50") == 2_500_000
    assert pricing.to_micros("") is None and pricing.to_micros(None) is None
    # 1000 input at $2/M + 500 output at $10/M + 100 cache write (1.25x) + 1000 cache read
    # (0.1x) = 2000 + 5000 + 250 + 200 micro-dollars.
    assert pricing.cost_micros("anthropic", 2_000_000, 10_000_000, input_tokens=1000,
                               output_tokens=500, cache_write=100, cache_read=1000) == 7450
    assert pricing.cost_micros("anthropic", None, 10_000_000, input_tokens=1000,
                               output_tokens=500) is None
    assert pricing.dollars(None) is None and pricing.dollars(7450) == "0.007450"
    assert pricing.KNOWN_PRICES["claude-sonnet-5"] == (2_000_000, 10_000_000)


def test_a_connection_in_use_cannot_be_deleted(world):
    from tests.ai_support import make_agent
    make_agent(world, mode="off", publish=False)
    r = world.client("admin").delete("/api/ai/connections/%s" % world.ids["connection"])
    assert r.status_code == 409 and "Follow-up" in r.json()["detail"]
    assert count(AiConnection) == 1
    json.dumps(r.json())

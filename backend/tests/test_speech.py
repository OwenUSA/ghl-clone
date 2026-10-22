"""Speak instead of type (app/speech.py): the recording becomes words for the box, nothing else.

A technician is refused; a recording is transcribed by the OpenAI connection (the provider is
mocked - no test reaches OpenAI) and kept nowhere; something that is not a recording is refused
before any request; "Pause all AI agents" stops it; with no OpenAI connection it says so.
"""
import pytest
from app.ai import providers
from app.db import SessionLocal
from app.models import AiSettings, DispatchAiRun

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 200
M4A = b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 200


@pytest.fixture()
def heard(world, monkeypatch):
    calls = []

    def fake(self, audio, filename, model=providers.TRANSCRIBE_MODEL):
        calls.append(filename)
        return "  Ana Smith paid the deposit by Zelle  "
    monkeypatch.setattr(providers.OpenAIProvider, "transcribe", fake)
    return calls


def _openai(world):
    r = world.client("admin").post("/api/ai/connections", json={
        "name": "OpenAI", "provider": "openai", "api_key": "sk-test-voice",
        "default_model": "gpt-5-mini", "price_input": "1.00", "price_output": "1.00"})
    assert r.status_code == 201, r.text


def _send(client, data, name="speech"):
    return client.post("/api/speech/transcribe", files={"file": (name, data, "audio/webm")})


def test_a_recording_becomes_words(world, heard):
    assert _send(world.client("tech"), WEBM).status_code == 403
    assert _send(world.client("dispatcher"), WEBM).status_code == 409   # only Claude saved
    _openai(world)
    d = world.client("dispatcher")
    r = _send(d, WEBM)
    assert r.status_code == 200 and r.json() == {"text": "Ana Smith paid the deposit by Zelle"}
    assert _send(d, M4A).status_code == 200
    assert heard == ["speech.webm", "speech.mp4"]                       # the bytes decide
    db = SessionLocal()
    try:
        assert db.query(DispatchAiRun).filter(DispatchAiRun.kind == "speech").count() == 2
    finally:
        db.close()


def test_not_a_recording_or_paused_sends_nothing(world, heard):
    _openai(world)
    d = world.client("dispatcher")
    assert _send(d, b"<html>not audio</html>").status_code == 422
    db = SessionLocal()
    try:
        s = db.get(AiSettings, 1) or AiSettings(id=1)
        s.paused = True
        db.add(s)
        db.commit()
    finally:
        db.close()
    r = _send(d, WEBM)
    assert r.status_code == 409 and "paused" in r.json()["detail"]
    assert heard == []

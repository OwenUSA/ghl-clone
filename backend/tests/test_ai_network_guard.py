"""The package-wide guard: no test reaches a real AI provider.

conftest.py refuses every DNS lookup and connection to api.anthropic.com / api.openai.com and
fails, in teardown, any test during which one was attempted. These tests prove the guard is
installed and that the real SDK path runs into it rather than around it.
"""
import socket

import pytest
from app.ai import providers
from tests import ai_guard as guard


def test_the_guard_refuses_a_provider_lookup_and_records_it():
    with pytest.raises(OSError, match="network guard"):
        socket.getaddrinfo("api.anthropic.com", 443)
    with pytest.raises(OSError, match="network guard"):
        socket.create_connection(("api.openai.com", 443), timeout=1)
    assert guard.ATTEMPTS == ["api.anthropic.com", "api.openai.com"]
    guard.ATTEMPTS.clear()      # expected here; any other test would now fail


def test_an_unmocked_sdk_call_is_stopped_by_the_guard_not_sent(monkeypatch):
    """With no mock transport, the official SDK really tries — and the guard stops it."""
    monkeypatch.setattr(providers, "HTTP_CLIENT_FACTORY", None)
    monkeypatch.setattr(providers, "MAX_RETRIES", 0)
    for provider in (providers.AnthropicProvider("sk-test"), providers.OpenAIProvider("sk-t")):
        result = provider.test("some-model")
        assert result.ok is False
        assert "Could not reach" in result.sentence
    assert set(guard.ATTEMPTS) == {"api.anthropic.com", "api.openai.com"}
    guard.ATTEMPTS.clear()


def test_other_hosts_are_not_affected():
    assert guard.provider_host("api.anthropic.com")
    assert guard.provider_host("eu.api.openai.com")
    assert not guard.provider_host("localhost")
    assert not guard.provider_host("api.anthropic.com.example.org")

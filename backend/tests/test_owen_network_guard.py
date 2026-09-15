"""The package-wide guard: no test reaches owen-main (texting goes live, 2026-09-15).

From the operator's switch on, a configured link sends REAL texts. conftest.py strips the
link's environment and refuses every lookup of, or connection to, an owen-main host; any
test that attempted one fails in teardown. These prove the guard is installed and that an
UNMOCKED send runs into it rather than around it.
"""
import os
import socket

import pytest
from app import crmlink
from app.models import DeliveryStatus
from app.transport import get_transport
from tests import owen_guard as guard


def test_the_link_environment_was_stripped_before_the_app_imported():
    assert not [k for k in os.environ if k.startswith(("CRM_LINK_", "OWEN_"))]
    assert crmlink.configured() is False


def test_the_guard_refuses_owen_main_hosts_and_records_them():
    with pytest.raises(OSError, match="network guard"):
        socket.getaddrinfo("callmon_app", 8888)
    with pytest.raises(OSError, match="network guard"):
        socket.create_connection(("api.owen.santiagoproperties.uk", 443), timeout=1)
    assert guard.ATTEMPTS == ["callmon_app", "api.owen.santiagoproperties.uk"]
    guard.ATTEMPTS.clear()      # expected here; any other test would now fail


def test_whatever_the_link_is_pointed_at_is_refused(monkeypatch):
    monkeypatch.setenv("CRM_LINK_BASE_URL", "http://some-new-owen-host:8000")
    assert guard.owen_host("some-new-owen-host")
    assert not guard.owen_host("localhost")
    assert not guard.owen_host("testserver")
    assert not guard.owen_host("api.anthropic.com.example.org")


def test_an_unmocked_text_is_stopped_by_the_guard_not_sent(monkeypatch):
    """The real `httpx.post` really tries — and the guard stops it before a packet."""
    monkeypatch.setenv("CRM_LINK_BASE_URL", "http://callmon_app:8888")
    monkeypatch.setenv("CRM_LINK_API_KEY", "owen_sk_test")
    ref = get_transport().send_sms(to="+19415550100", body="must never leave", from_number="")
    assert ref.status is DeliveryStatus.FAILED
    assert "callmon_app" in guard.ATTEMPTS
    guard.ATTEMPTS.clear()

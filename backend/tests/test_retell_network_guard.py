"""The package-wide guard: no test reaches Retell (2026-10-06).

The CRM has no Retell key and never calls Retell — owen-main does (decision 20). conftest.py
refuses every DNS lookup of and connection to a retellai.com host and fails, in teardown, any
test during which one was attempted. These prove it is installed and narrow.
"""
import os
import socket

import pytest
from tests import retell_guard as guard


def test_the_guard_refuses_a_retell_lookup_and_records_it():
    with pytest.raises(OSError, match="network guard"):
        socket.getaddrinfo("api.retellai.com", 443)
    with pytest.raises(OSError, match="network guard"):
        socket.create_connection(("sip.retellai.com", 5060), timeout=1)
    assert guard.ATTEMPTS == ["api.retellai.com", "sip.retellai.com"]
    guard.ATTEMPTS.clear()      # expected here; any other test would now fail


def test_an_http_client_is_stopped_by_the_guard_not_sent():
    import httpx
    with pytest.raises(httpx.ConnectError):
        httpx.get("https://api.retellai.com/v2/list-agents", timeout=2)
    assert guard.ATTEMPTS and guard.ATTEMPTS[0] == "api.retellai.com"
    guard.ATTEMPTS.clear()


def test_other_hosts_are_not_affected_and_no_key_survives():
    assert guard.retell_host("api.retellai.com") and guard.retell_host("RetellAI.com.")
    assert not guard.retell_host("localhost")
    assert not guard.retell_host("retellai.com.example.org")
    assert not [k for k in os.environ if k.startswith("RETELL_")]

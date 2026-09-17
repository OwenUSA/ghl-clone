"""No test may reach Zuper (Zuper sync, 2026-09-16). Installed by conftest.py.

There is no Zuper key on this server and none may be looked for; the sync is mocked at the
HTTP boundary (`app.zuper.client.TRANSPORT`, see tests/zuper_fake.py). This guard makes
forgetting a failure rather than a request:

  * at import, conftest strips every `ZUPER_*` variable, so a shell holding a real key or
    ZUPER_SYNC_ENABLED=true cannot arm the suite;
  * every DNS lookup of, or connection to, zuperpro.com or any host under it
    (accounts.zuperpro.com, us-east-1.zuperpro.com, …) is refused and recorded;
  * the client's default transport is a guard that refuses ANY request not sent through a
    test's own fake, and refuses a denylisted endpoint even through a fake;
  * conftest's autouse fixture FAILS any test during which one was attempted.

tests/test_zuper_network_guard.py proves it is live. Chained after the owen and AI guards.
"""
import os
import socket

import httpx

ZUPER_HOSTS = ("zuperpro.com", "zuper.co")
ATTEMPTS: list[str] = []
_next_getaddrinfo = None
_next_create_connection = None


def strip_environment() -> list[str]:
    gone = [k for k in list(os.environ) if k.startswith("ZUPER_")]
    for k in gone:
        os.environ.pop(k, None)
    return gone


def zuper_host(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode(errors="ignore")
    host = str(host or "").lower().rstrip(".")
    return any(host == h or host.endswith("." + h) for h in ZUPER_HOSTS)


def _refuse(what) -> None:
    ATTEMPTS.append(str(what))
    raise OSError("test network guard: a test tried to reach Zuper at %s" % what)


def _getaddrinfo(host, *args, **kwargs):
    if zuper_host(host):
        _refuse(host)
    return _next_getaddrinfo(host, *args, **kwargs)


def _create_connection(address, *args, **kwargs):
    if zuper_host(address[0]):
        _refuse(address[0])
    return _next_create_connection(address, *args, **kwargs)


def _unmocked(request: httpx.Request) -> httpx.Response:
    _refuse("%s %s (no fake installed)" % (request.method, request.url))
    raise AssertionError("unreachable")


GUARD_TRANSPORT = httpx.MockTransport(_unmocked)


def install() -> None:
    global _next_getaddrinfo, _next_create_connection
    if socket.getaddrinfo is _getaddrinfo:
        return
    _next_getaddrinfo = socket.getaddrinfo
    _next_create_connection = socket.create_connection
    socket.getaddrinfo = _getaddrinfo
    socket.create_connection = _create_connection

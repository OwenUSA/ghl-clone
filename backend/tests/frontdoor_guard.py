"""No test may reach Frontdoor, AHS's contractor portal (2026-11-12). Installed by conftest.py.

Reading Frontdoor drives a real browser logged in as the company, so a test that got through
would be a real login with real credentials. Two ways out are closed:

  * at import, every `FRONTDOOR_*` variable is stripped, so a shell holding the login or
    FRONTDOOR_READ_ENABLED=true cannot arm the suite;
  * every DNS lookup of, or connection to, frontdoorhome.com or any host under it is refused
    and recorded — this catches anything Python itself would send;
  * the browser runs in its own process, which no socket patch reaches, so `client.LAUNCH` is
    replaced by a refusal: no Chromium can start under pytest at all.

conftest's autouse fixture FAILS any test during which one was attempted.
tests/test_frontdoor.py proves it is live.
"""
import os
import socket

FRONTDOOR_HOSTS = ("frontdoorhome.com",)
ATTEMPTS: list[str] = []
_next_getaddrinfo = None
_next_create_connection = None


def strip_environment() -> list[str]:
    gone = [k for k in list(os.environ) if k.startswith("FRONTDOOR_")]
    for k in gone:
        os.environ.pop(k, None)
    return gone


def frontdoor_host(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode(errors="ignore")
    host = str(host or "").lower().rstrip(".")
    return any(host == h or host.endswith("." + h) for h in FRONTDOOR_HOSTS)


def _refuse(what) -> None:
    ATTEMPTS.append(str(what))
    raise OSError("test network guard: a test tried to reach Frontdoor (%s)" % what)


def _getaddrinfo(host, *args, **kwargs):
    if frontdoor_host(host):
        _refuse(host)
    return _next_getaddrinfo(host, *args, **kwargs)


def _create_connection(address, *args, **kwargs):
    if frontdoor_host(address[0]):
        _refuse(address[0])
    return _next_create_connection(address, *args, **kwargs)


def _no_browser(playwright):
    _refuse("a Chromium for Frontdoor (client.LAUNCH)")


def install() -> None:
    global _next_getaddrinfo, _next_create_connection
    from app.frontdoor import client
    client.LAUNCH = _no_browser
    if socket.getaddrinfo is _getaddrinfo:
        return
    _next_getaddrinfo = socket.getaddrinfo
    _next_create_connection = socket.create_connection
    socket.getaddrinfo = _getaddrinfo
    socket.create_connection = _create_connection

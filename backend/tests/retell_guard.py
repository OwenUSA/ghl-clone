"""No test may reach Retell (Retell voice agents, 2026-10-06). Installed by conftest.py.

The CRM never talks to Retell — owen-main holds the only key (docs/RETELL-PLAN.md, decision
20) — so nothing here should ever look one up. This guard makes that a fact rather than a
hope: every DNS lookup of, or connection to, a retellai.com host is refused and recorded, and
conftest's autouse fixture FAILS any test during which one was attempted. `RETELL_*` settings
are stripped from the environment at import, so a shell with a key exported cannot arm one.

The sibling of tests/ai_guard.py, chained after the others: it wraps whatever
`socket.getaddrinfo` / `socket.create_connection` are when it is installed.
tests/test_retell_network_guard.py proves it is live.
"""
import os
import socket

RETELL_HOSTS = ("retellai.com",)       # api.retellai.com, sip.retellai.com, …

ATTEMPTS: list[str] = []
_next_getaddrinfo = None
_next_create_connection = None


def strip_environment() -> list[str]:
    gone = [k for k in list(os.environ) if k.startswith("RETELL_")]
    for k in gone:
        os.environ.pop(k, None)
    return gone


def retell_host(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode(errors="ignore")
    host = str(host or "").lower().rstrip(".")
    return any(host == h or host.endswith("." + h) for h in RETELL_HOSTS)


def _refuse(host) -> None:
    ATTEMPTS.append(str(host))
    raise OSError("test network guard: a test tried to reach Retell (%s)" % host)


def _getaddrinfo(host, *args, **kwargs):
    if retell_host(host):
        _refuse(host)
    return _next_getaddrinfo(host, *args, **kwargs)


def _create_connection(address, *args, **kwargs):
    if retell_host(address[0]):
        _refuse(address[0])
    return _next_create_connection(address, *args, **kwargs)


def install() -> None:
    global _next_getaddrinfo, _next_create_connection
    if socket.getaddrinfo is _getaddrinfo:
        return
    _next_getaddrinfo = socket.getaddrinfo
    _next_create_connection = socket.create_connection
    socket.getaddrinfo = _getaddrinfo
    socket.create_connection = _create_connection

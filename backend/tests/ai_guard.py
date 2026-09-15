"""No test may reach an AI provider (AI Agents, 2026-09-15). Installed by conftest.py.

There are no provider keys on this server and a test must never spend money or send a
customer's words anywhere. Every name lookup of, or connection to, a provider host is refused
and recorded; conftest's autouse fixture FAILS any test during which one was attempted,
whatever it asserted. tests/test_ai_network_guard.py proves the guard is live.

A module of its own (imported as `tests.ai_guard` everywhere) so there is exactly one list
of attempts: pytest loads conftest.py as `conftest`, and importing it again under another
name would install a second, disconnected guard.
"""
import socket

from app.ai.providers import PROVIDER_HOSTS

ATTEMPTS: list[str] = []
_real_getaddrinfo = socket.getaddrinfo
_real_create_connection = socket.create_connection


def provider_host(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode(errors="ignore")
    host = str(host or "").lower().rstrip(".")
    return any(host == h or host.endswith("." + h) for h in PROVIDER_HOSTS)


def _refuse(host) -> None:
    ATTEMPTS.append(str(host))
    raise OSError("test network guard: a test tried to reach AI provider %s" % host)


def _getaddrinfo(host, *args, **kwargs):
    if provider_host(host):
        _refuse(host)
    return _real_getaddrinfo(host, *args, **kwargs)


def _create_connection(address, *args, **kwargs):
    if provider_host(address[0]):
        _refuse(address[0])
    return _real_create_connection(address, *args, **kwargs)


def install() -> None:
    socket.getaddrinfo = _getaddrinfo
    socket.create_connection = _create_connection

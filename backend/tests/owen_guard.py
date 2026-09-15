"""No test may reach owen-main (texting goes live, 2026-09-15). Installed by conftest.py.

From the day the operator switches SMS on in owen-main, a configured CRM link sends REAL
texts to real customers over the BulkVS DID. So every test mocks `crmlink` at the HTTP
boundary, and this guard makes forgetting to a failure rather than a text:

  * at import, conftest strips `CRM_LINK_*` and `OWEN_*` from the environment, so a shell
    that happens to have production's values exported cannot arm the suite;
  * every DNS lookup of, or connection to, an owen-main host is refused and recorded —
    the known names (`callmon_app`, the `*.santiagoproperties.uk` API) AND whatever host
    `CRM_LINK_BASE_URL` / `OWEN_BASE_URL` names at that moment, because whatever the link
    is pointed at IS the phone system as far as this code knows;
  * conftest's autouse fixture FAILS any test during which one was attempted.

tests/test_owen_network_guard.py proves it is live. The sibling of tests/ai_guard.py, and
chained after it: both wrap `socket.getaddrinfo` / `socket.create_connection`.
"""
import os
import socket
from urllib.parse import urlsplit

# The names owen-main answers on, read from owen-main's compose files and docs.
OWEN_HOSTS = ("callmon_app", "callmon_worker", "callmon_frontend", "owen-main",
              "santiagoproperties.uk")
LINK_ENV = ("CRM_LINK_BASE_URL", "OWEN_BASE_URL")
LOOPBACK = ("localhost", "127.0.0.1", "::1", "testserver")

ATTEMPTS: list[str] = []
_next_getaddrinfo = None
_next_create_connection = None


def strip_environment() -> list[str]:
    """Remove every link setting inherited from the shell. Returns the names removed."""
    gone = [k for k in list(os.environ) if k.startswith(("CRM_LINK_", "OWEN_"))]
    for k in gone:
        os.environ.pop(k, None)
    return gone


def _configured_hosts() -> set[str]:
    hosts = set()
    for key in LINK_ENV:
        value = os.environ.get(key) or ""
        if value:
            host = (urlsplit(value).hostname or "").lower()
            if host and host not in LOOPBACK:
                hosts.add(host)
    return hosts


def owen_host(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode(errors="ignore")
    host = str(host or "").lower().rstrip(".")
    if not host or host in LOOPBACK:
        return False
    if any(host == h or host.endswith("." + h) for h in OWEN_HOSTS):
        return True
    return host in _configured_hosts()


def _refuse(host) -> None:
    ATTEMPTS.append(str(host))
    raise OSError("test network guard: a test tried to reach owen-main at %s" % host)


def _getaddrinfo(host, *args, **kwargs):
    if owen_host(host):
        _refuse(host)
    return _next_getaddrinfo(host, *args, **kwargs)


def _create_connection(address, *args, **kwargs):
    if owen_host(address[0]):
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

"""The ONLY code in this repository that sends a request to Zuper.

Every call goes through `request()`, which, in this order and before any connection exists:

1. **Refuses a denylisted endpoint** (`DENYLIST`) — anything that messages, calls, invites
   or notifies a customer (Zuper Connect `/telephony/...`, quote / invoice / payment-request
   *send*, customer portal invites, notification sends, reminders) and any write to quotes,
   invoices, payments or credit notes, which are Zuper's and read-only here.
2. **Refuses anything not on the allowlist** (`ALLOWLIST`) — the exact endpoints the sync
   uses. A new call has to be added there on purpose.
3. **Refuses when the sync is off** — `ZUPER_SYNC_ENABLED` false and not inside an
   operator command (`operator_mode()`), or no key.
4. **Refuses a write inside a dry run** (`read_only()`).
5. **Paces** to `ZUPER_REQUESTS_PER_MINUTE` (default 180, under the account's 200) and
   **backs off on 429**, honouring Retry-After.

The host is fixed to the configured base URL, which must be under zuperpro.com. The key is
sent only there, never logged, never returned.

Nearly every request and response SHAPE below comes from Zuper's public reference and is
UNVERIFIED against the live account (there is no key on the build machine). Every path is a
constant in `PATHS` and every response is unwrapped in one place (`data_of`, `uid_of`,
`rows_of`), so the first live read-only probe fixes a wrong guess in one line.
`tests/zuper_guard.py` refuses any real connection to *.zuperpro.com during pytest.
"""
from __future__ import annotations

import contextlib
import logging
import re
import threading
import time
from collections import deque
from collections.abc import Iterator
from contextvars import ContextVar
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

import httpx

from . import config

log = logging.getLogger("zuper")

TIMEOUT_SECONDS = 20.0
PAGE_SIZE = 100
MAX_PAGES = 500
RETRIES_ON_429 = 6
RETRIES_ON_5XX = 2
MAX_BACKOFF_SECONDS = 60.0
ALLOWED_HOST = "zuperpro.com"

# Tests install an `httpx.MockTransport` here. Production leaves it None.
TRANSPORT: httpx.BaseTransport | None = None

_OPERATOR: ContextVar[bool] = ContextVar("zuper_operator", default=False)
_READ_ONLY: ContextVar[bool] = ContextVar("zuper_read_only", default=False)


@contextlib.contextmanager
def operator_mode():
    """An operator's command (setup, initial load): allowed with only the key set."""
    token = _OPERATOR.set(True)
    try:
        yield
    finally:
        _OPERATOR.reset(token)


@contextlib.contextmanager
def read_only():
    """A dry run: every non-GET is refused before it is built."""
    token = _READ_ONLY.set(True)
    try:
        yield
    finally:
        _READ_ONLY.reset(token)


def is_read_only() -> bool:
    return _READ_ONLY.get()


# ------------------------------------------------------------------------------ errors

class ZuperError(Exception):
    """Anything Zuper could not give us. `kind`:
    off | no_key | refused | unauthorized | not_found | rate_limited | unavailable |
    rejected | bad_response."""

    def __init__(self, kind: str, detail: str = "", status: int | None = None):
        super().__init__("%s%s" % (kind, (": " + detail) if detail else ""))
        self.kind = kind
        self.detail = detail
        self.status = status


SENTENCES = {
    "off": config.OFF_SENTENCE,
    "no_key": config.NO_KEY_SENTENCE,
    "refused": "The sync refused to make that request to Zuper (it is not one it may make).",
    "unauthorized": "Zuper refused the API key (401/403). Check the key and that its user "
                    "has Admin rights.",
    "not_found": "Zuper has no such record (404).",
    "rate_limited": "Zuper kept answering \"too many requests\" (429) after backing off.",
    "unavailable": "Zuper could not be reached or answered with a server error.",
    "rejected": "Zuper rejected the request.",
    "bad_response": "Zuper answered with something the sync could not read.",
}


def sentence(exc: Exception) -> str:
    """A plain-language line for Settings → Zuper. Carries Zuper's own message when it
    gave one (never the key, never a customer's details — Zuper's messages name fields)."""
    if isinstance(exc, ZuperError):
        base = SENTENCES.get(exc.kind, "Zuper sync error.")
        if exc.kind in ("rejected", "bad_response", "refused") and exc.detail:
            return "%s %s" % (base, exc.detail[:300])
        return base
    return "Internal error in the sync (%s)." % type(exc).__name__


# ---------------------------------------------------------------------------- endpoints

# Relative to the base URL (…/api). UNVERIFIED unless noted in DECISIONS.md.
PATHS = {
    "me": "/user",                                  # the key's own user
    "users": "/user/all",
    "customers": "/customers",
    "customer": "/customers/{uid}",
    "customer_create": "/customers_new",
    "customer_recover": "/customers/{uid}/recover",
    "categories": "/jobs/category",
    "statuses": "/jobs/status/{category_uid}",
    "status_create": "/jobs/status_new/{category_uid}",
    "status_update": "/jobs/status/{category_uid}/{status_uid}",
    "status_create_plain": "/jobs/status",          # a candidate create path (UNVERIFIED)
    "jobs": "/jobs",
    "job": "/jobs/{uid}",
    "job_delete": "/jobs/{uid}/delete",
    "job_recover": "/jobs/{uid}/recover",
    "job_status": "/jobs/{uid}/status",
    "job_status_rollback": "/jobs/{uid}/status/rollback",
    "job_notes": "/jobs/{uid}/note",
    "job_note": "/jobs/{uid}/note/{note_uid}",
    "job_attachments": "/jobs/{uid}/attachments",
    "job_service_tasks": "/jobs/{uid}/service_tasks",
    "job_service_task": "/jobs/{uid}/service_tasks/{task_uid}",
    "appointments": "/appointments",
    "appointment": "/appointments/{uid}",
    "estimates": "/estimate",
    "estimate": "/estimate/{uid}",
    "invoices": "/invoice",
    "invoice": "/invoice/{uid}",
    "custom_fields": "/settings/custom_fields",     # definitions: REST path unverified
    "lead_sources": "/settings/lead_sources",       # MCP-only per research: unverified
    "webhooks": "/service/notifications/webhook",
    "webhook_create": "/webhook",
    "webhook_list": "/webhook",                     # a second guess at the list (UNVERIFIED)
}

_UID = r"[^/]+"


def _rx(template: str) -> str:
    return "^" + re.sub(r"\{[a-z_]+\}", _UID, template) + "$"


# Everything the sync may call, and with which method. Nothing else leaves.
ALLOWLIST: list[tuple[str, str]] = [
    ("GET", _rx(PATHS["me"])), ("GET", _rx(PATHS["users"])),
    ("GET", _rx(PATHS["customers"])), ("GET", _rx(PATHS["customer"])),
    ("POST", _rx(PATHS["customer_create"])), ("PUT", _rx(PATHS["customer"])),
    ("DELETE", _rx(PATHS["customer"])), ("POST", _rx(PATHS["customer_recover"])),
    ("GET", _rx(PATHS["categories"])), ("POST", _rx(PATHS["categories"])),
    ("GET", _rx(PATHS["statuses"])), ("POST", _rx(PATHS["status_create"])),
    ("GET", _rx(PATHS["status_create"])), ("POST", _rx(PATHS["status_create_plain"])),
    ("PUT", _rx(PATHS["status_update"])),
    ("GET", _rx(PATHS["jobs"])), ("POST", _rx(PATHS["jobs"])), ("PUT", _rx(PATHS["jobs"])),
    ("GET", _rx(PATHS["job"])), ("DELETE", _rx(PATHS["job_delete"])),
    ("POST", _rx(PATHS["job_recover"])),
    ("PUT", _rx(PATHS["job_status"])), ("PUT", _rx(PATHS["job_status_rollback"])),
    ("GET", _rx(PATHS["job_notes"])), ("POST", _rx(PATHS["job_notes"])),
    ("PUT", _rx(PATHS["job_note"])), ("DELETE", _rx(PATHS["job_note"])),
    ("GET", _rx(PATHS["job_attachments"])),
    ("GET", _rx(PATHS["job_service_tasks"])), ("POST", _rx(PATHS["job_service_tasks"])),
    ("PUT", _rx(PATHS["job_service_task"])), ("DELETE", _rx(PATHS["job_service_task"])),
    ("GET", _rx(PATHS["appointments"])), ("POST", _rx(PATHS["appointments"])),
    ("GET", _rx(PATHS["appointment"])), ("PUT", _rx(PATHS["appointment"])),
    ("DELETE", _rx(PATHS["appointment"])),
    ("GET", _rx(PATHS["estimates"])), ("GET", _rx(PATHS["estimate"])),
    ("GET", _rx(PATHS["invoices"])), ("GET", _rx(PATHS["invoice"])),
    ("GET", _rx(PATHS["custom_fields"])), ("GET", _rx(PATHS["lead_sources"])),
    ("GET", _rx(PATHS["webhooks"])), ("POST", _rx(PATHS["webhook_create"])),
    ("GET", _rx(PATHS["webhook_list"])),
]

ANY = "*"
WRITES = "WRITE"

# Checked FIRST, whatever the allowlist says. (methods, pattern, why)
DENYLIST: list[tuple[str, str, str]] = [
    (ANY, r"^/telephony(/|$)", "Zuper Connect sends real texts and places real calls"),
    (ANY, r"^/(sms|messages?|conversations?|chat)(/|$)", "messaging reaches a customer"),
    (ANY, r"(^|/)send(_[a-z]+)?(/|$)", "a send endpoint emails or texts a customer"),
    (ANY, r"^/payment_requests?(/|$)|(^|/)payment_link(/|$)",
     "a payment request notifies a customer"),
    (ANY, r"portal|invite", "a customer portal invite emails a customer"),
    (ANY, r"^/service/notifications/(?!webhook(/|$|_history))",
     "a notification send reaches a customer"),
    (ANY, r"^/notifications?(/|$)", "a notification send reaches a customer"),
    (ANY, r"(^|/)reminders?(/|$)", "a reminder is sent to a customer"),
    (ANY, r"(^|/)(email|emails)(/|$)", "an email endpoint reaches a customer"),
    (WRITES, (r"^/(estimates?|quotes?|proposals?|invoices?|payments?|credit_notes?|"
              r"payment_modes?)(/|$)"),
     "quotes, invoices and payments are Zuper's — the CRM only reads them"),
]


def denied(method: str, path: str) -> str | None:
    """Why this request may never be made, or None."""
    method = method.upper()
    for methods, pattern, why in DENYLIST:
        if methods == WRITES and method == "GET":
            continue
        if re.search(pattern, path, flags=re.IGNORECASE):
            return why
    return None


def allowed(method: str, path: str) -> bool:
    method = method.upper()
    return any(m == method and re.match(rx, path) for m, rx in ALLOWLIST)


# ---------------------------------------------------------------------------- pacing

def _sleep(seconds: float) -> None:          # monkeypatched to a no-op in tests
    if seconds > 0:
        time.sleep(seconds)


def _now() -> float:                         # monkeypatched in tests
    return time.monotonic()


_window: deque[float] = deque()
_window_lock = threading.Lock()


def _pace() -> None:
    """At most `requests_per_minute` requests in any 60 seconds, across threads."""
    limit = config.requests_per_minute()
    while True:
        with _window_lock:
            now = _now()
            while _window and now - _window[0] >= 60.0:
                _window.popleft()
            if len(_window) < limit:
                _window.append(now)
                return
            wait = 60.0 - (now - _window[0])
        _sleep(max(wait, 0.05))


def reset_pacing() -> None:
    with _window_lock:
        _window.clear()


def _retry_after(resp: httpx.Response, attempt: int) -> float:
    raw = resp.headers.get("Retry-After")
    try:
        if raw is not None:
            return min(max(float(raw), 0.0), MAX_BACKOFF_SECONDS)
    except ValueError:
        pass
    return min(2.0 ** attempt, MAX_BACKOFF_SECONDS)


# ---------------------------------------------------------------------------- request

def _check_host(url: str) -> None:
    host = (urlsplit(url).hostname or "").lower()
    if urlsplit(url).scheme != "https" or not (
            host == ALLOWED_HOST or host.endswith("." + ALLOWED_HOST)):
        raise ZuperError("refused", "the Zuper base URL must be https under zuperpro.com")


def request(method: str, path: str, *, params: dict | None = None,
            body: dict | list | None = None) -> Any:
    method = method.upper()
    path = "/" + path.lstrip("/")
    why = denied(method, path)
    if why:
        raise ZuperError("refused", "%s %s is never called: %s" % (method, path, why))
    if not allowed(method, path):
        raise ZuperError("refused", "%s %s is not on the sync's allowlist" % (method, path))
    if not (config.env_enabled() or _OPERATOR.get()):
        raise ZuperError("off")
    if not config.key_set():
        raise ZuperError("no_key")
    if method != "GET" and _READ_ONLY.get():
        raise ZuperError("refused", "a dry run makes no %s request" % method)
    base = config.base_url()
    _check_host(base)
    headers = {"x-api-key": config.api_key(), "Accept": "application/json"}
    attempt = 0
    while True:
        _pace()
        try:
            with httpx.Client(base_url=base, transport=TRANSPORT,
                              timeout=TIMEOUT_SECONDS) as client:
                resp = client.request(method, base + path, params=params, json=body,
                                      headers=headers)
        except httpx.HTTPError as exc:
            if method == "GET" and attempt < RETRIES_ON_5XX:
                attempt += 1
                _sleep(min(2.0 ** attempt, MAX_BACKOFF_SECONDS))
                continue
            raise ZuperError("unavailable", type(exc).__name__) from None
        if resp.status_code == 429:
            if attempt < RETRIES_ON_429:
                attempt += 1
                wait = _retry_after(resp, attempt)
                log.info("zuper 429 on %s %s; backing off %.1fs", method, path, wait)
                _sleep(wait)
                continue
            raise ZuperError("rate_limited", status=429)
        if resp.status_code >= 500 and method == "GET" and attempt < RETRIES_ON_5XX:
            attempt += 1
            _sleep(min(2.0 ** attempt, MAX_BACKOFF_SECONDS))
            continue
        break
    if resp.status_code in (401, 403):
        raise ZuperError("unauthorized", "HTTP %d" % resp.status_code, resp.status_code)
    if resp.status_code == 404:
        raise ZuperError("not_found", path, 404)
    try:
        payload = resp.json() if resp.content else {}
    except ValueError:
        if resp.status_code >= 400:
            raise ZuperError("unavailable" if resp.status_code >= 500 else "rejected",
                             "HTTP %d" % resp.status_code, resp.status_code) from None
        raise ZuperError("bad_response", "not JSON") from None
    if resp.status_code >= 500:
        raise ZuperError("unavailable", "HTTP %d" % resp.status_code, resp.status_code)
    if resp.status_code >= 400 or (isinstance(payload, dict)
                                   and str(payload.get("type", "")).lower() == "error"):
        message = payload.get("message") or payload.get("title") \
            if isinstance(payload, dict) else None
        raise ZuperError("rejected", str(message or "HTTP %d" % resp.status_code)[:300],
                         resp.status_code)
    return payload


# ----------------------------------------------------------------------- unwrapping

def data_of(payload: Any) -> Any:
    """Zuper wraps records as {"type": "success", "data": ...}."""
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def rows_of(payload: Any) -> list[dict]:
    rows = data_of(payload)
    if isinstance(rows, dict):
        # "job_statuses": GET /jobs/status/{category} answers {data: {_id, job_statuses: [...]}}
        # (live, 2026-09-17).
        for key in ("rows", "records", "items", "list", "job_statuses"):
            if isinstance(rows.get(key), list):
                rows = rows[key]
                break
    if not isinstance(rows, list):
        raise ZuperError("bad_response", "expected a list (%s)" % shape(payload))
    return [r for r in rows if isinstance(r, dict)]


def total_of(payload: Any) -> int | None:
    if isinstance(payload, dict):
        for key in ("total_records", "total", "count"):
            value = payload.get(key)
            if isinstance(value, int):
                return value
    return None


def uid_of(payload: Any, key: str, *also: str) -> str:
    """The uid a create answered with: at the top, in `data`, one level inside `data` (a
    record wrapped once more), or as `data` itself. Not found: `bad_response` naming the
    answer's field names only (never values), so the first live answer shows its shape."""
    keys = (key, *also, "uid")
    data = data_of(payload)
    holders = [payload, data]
    if isinstance(data, dict):
        holders += [v for v in data.values() if isinstance(v, dict)]
    for holder in holders:
        if isinstance(holder, dict):
            for k in keys:
                value = holder.get(k)
                if isinstance(value, str) and value:
                    return value
    if isinstance(data, str) and data:
        return data
    raise ZuperError("bad_response", "the create answered without a %s (%s)" % (
        key, shape(payload)))


def shape(payload: Any) -> str:
    """An answer's structure as field names only — safe to print, no values."""
    def one(value: Any, depth: int) -> str:
        if isinstance(value, dict):
            if depth >= 4:
                return "{…}"
            return "{%s}" % ", ".join(
                k + ("" if not isinstance(v, (dict, list)) else one(v, depth + 1))
                for k, v in list(value.items())[:25])
        if isinstance(value, list):
            return "[%d%s]" % (len(value), " of " + one(value[0], depth + 1)
                               if value and isinstance(value[0], (dict, list)) else "")
        return ""
    return "answer fields: " + (one(payload, 0) or type(payload).__name__)


def cents(value: Any) -> int | None:
    """Zuper money (a number or a string of dollars) as integer cents."""
    if value is None or value == "":
        return None
    try:
        return int((Decimal(str(value)) * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        return None


def pages(path: str, params: dict | None = None) -> Iterator[list[dict]]:
    """Every page of a list endpoint (page + count, 1-based)."""
    for page in range(1, MAX_PAGES + 1):
        payload = request("GET", path, params={**(params or {}), "page": page,
                                               "count": PAGE_SIZE})
        rows = rows_of(payload)
        if not rows:
            return
        yield rows
        total_pages = payload.get("total_pages") if isinstance(payload, dict) else None
        if isinstance(total_pages, int) and page >= total_pages:
            return
        if len(rows) < PAGE_SIZE and not isinstance(total_pages, int):
            return


def iter_all(path: str, params: dict | None = None) -> Iterator[dict]:
    for rows in pages(path, params):
        yield from rows


def path(name: str, **ids: str) -> str:
    return PATHS[name].format(**ids)


# ---------------------------------------------------------------------------- files

FILE_MAX_BYTES = 25 * 1024 * 1024
INLINE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp", "image/heic",
                "application/pdf")


def fetch_file(url: str) -> tuple[bytes, str]:
    """The bytes of an attachment Zuper listed on a job, for the CRM to relay.

    Only with the sync switched on (never from an operator command). The API key goes ONLY
    to a host under zuperpro.com; a signed storage URL is asked without it."""
    if not config.env_enabled():
        raise ZuperError("off")
    if not config.key_set():
        raise ZuperError("no_key")
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or host == "localhost" or re.match(
            r"^[\d.:\[\]]+$", host):
        raise ZuperError("refused", "an attachment URL must be https on a named host")
    own = host == ALLOWED_HOST or host.endswith("." + ALLOWED_HOST)
    headers = {"x-api-key": config.api_key()} if own else {}
    try:
        with httpx.Client(transport=TRANSPORT, timeout=TIMEOUT_SECONDS,
                          follow_redirects=False) as http:
            resp = http.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise ZuperError("unavailable", type(exc).__name__) from None
    if resp.status_code == 404:
        raise ZuperError("not_found", "attachment", 404)
    if resp.status_code >= 400:
        raise ZuperError("unavailable", "attachment HTTP %d" % resp.status_code,
                         resp.status_code)
    if len(resp.content) > FILE_MAX_BYTES:
        raise ZuperError("bad_response", "attachment too large")
    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    return resp.content, ctype


# ---------------------------------------------------------------------------- region

REGION_LOOKUP_URL = "https://accounts.zuperpro.com/api/config"


def normalise_base(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    return url if url.endswith("/api") else url + "/api"


def region_lookup(company_name: str) -> str:
    """Which Zuper data centre holds this company: POST accounts.zuperpro.com/api/config
    {"company_name"} -> `dc_api_url` (zuper-research.md; the field name is UNVERIFIED). A lookup,
    not a write: allowed in a dry run. The API key is NOT sent to it."""
    if not (config.env_enabled() or _OPERATOR.get()):
        raise ZuperError("off")
    if not (company_name or "").strip():
        raise ZuperError("refused", "a region lookup needs the company name")
    _pace()
    try:
        with httpx.Client(transport=TRANSPORT, timeout=TIMEOUT_SECONDS,
                          follow_redirects=False) as http:
            resp = http.post(REGION_LOOKUP_URL, json={"company_name": company_name.strip()},
                             headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise ZuperError("unavailable", type(exc).__name__) from None
    if resp.status_code == 404:
        raise ZuperError("not_found", "no Zuper company by that name", 404)
    if resp.status_code >= 400:
        raise ZuperError("unavailable" if resp.status_code >= 500 else "rejected",
                         "HTTP %d" % resp.status_code, resp.status_code)
    try:
        data = data_of(resp.json())
    except ValueError:
        raise ZuperError("bad_response", "not JSON") from None
    url = data.get("dc_api_url") if isinstance(data, dict) else None
    if not isinstance(url, str) or not url:
        raise ZuperError("bad_response", "the region lookup answered without dc_api_url")
    url = normalise_base(url)
    _check_host(url)
    return url

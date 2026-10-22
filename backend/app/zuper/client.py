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
4. **Refuses a write inside a dry run** (`read_only()`), and inside the Dispatch page's scope
   (`dispatch_write()`) every write but its own four (`DISPATCH_WRITES`), all of them while
   the server gate DISPATCH_ZUPER_WRITES is off.
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
_AHS_CREATE: ContextVar[bool] = ContextVar("zuper_ahs_create", default=False)


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


@contextlib.contextmanager
def ahs_create():
    """One AHS work-order email creating its job in Zuper (2026-09-28).

    The only scope in which a one-way mirror writes something of its own, and the narrowest
    one that can work: Zuper has no email ingest at all, so an emailed work order reaches it
    only if the CRM creates it. Inside this scope the three POSTs in `AHS_CREATE_WRITES` are
    allowed and every other non-GET is still refused — an update, a status move, a delete, a
    second card. Opened by the worker for a send the AHS ingest queued, and by nothing else.
    """
    token = _AHS_CREATE.set(True)
    try:
        yield
    finally:
        _AHS_CREATE.reset(token)


_DISPATCH_WRITE: ContextVar[bool] = ContextVar("zuper_dispatch_write", default=False)
_PROPOSAL_LINES: ContextVar[bool] = ContextVar("zuper_proposal_lines", default=False)


@contextlib.contextmanager
def proposal_lines():
    """A signed proposal's accepted option copied onto its job's line items (2026-10-02,
    `proposals.py`). Inside this scope the ONE write is `PUT /jobs` whose job carries only
    `job_uid`, `products` and `job_total` (checked in `request`); every other non-GET is refused,
    and nothing at all while `ZUPER_PROPOSAL_LINES` is off."""
    token = _PROPOSAL_LINES.set(True)
    try:
        yield
    finally:
        _PROPOSAL_LINES.reset(token)


@contextlib.contextmanager
def dispatch_write():
    """One change a person told the Dispatch page's agent to make in Zuper (phase 3,
    2026-10-01) — a job field, the job's address, a note, a visit's time + Technician field,
    or a stage move on the job's own board.

    Modelled on `ahs_create()`: inside this scope the requests in `DISPATCH_WRITES` are
    allowed (and only while the server gate `DISPATCH_ZUPER_WRITES` is on), and EVERY other
    non-GET is refused — even on a deployment that is not pull-only, so this scope can never
    borrow the sync's wider allowlist (a delete, a customer edit, a second card). The denylist
    is still checked first. Opened by app/dispatch/writes.py and nothing else.
    """
    token = _DISPATCH_WRITE.set(True)
    try:
        yield
    finally:
        _DISPATCH_WRITE.reset(token)


_AUTOREPLY: ContextVar[bool] = ContextVar("zuper_autoreply", default=False)
_FOLLOWUP: ContextVar[bool] = ContextVar("zuper_followup", default=False)
_PAYMENTS: ContextVar[bool] = ContextVar("zuper_payments", default=False)
_REVIEWS: ContextVar[bool] = ContextVar("zuper_reviews", default=False)


@contextlib.contextmanager
def autoreply_write():
    """The auto-reply's own two writes (2026-10-19, owner-approved; app/autoreply):

      POST /customers/{uid}/send   ONE text to that customer, body exactly
                                   {"type": "SMS", "sms": {"body", "to"}} — the request Zuper's
                                   own "Thank you for confirming" workflow makes. Zuper sends it
                                   from the company's System Notification Number (954-420-7373).
      POST /jobs/{uid}/note        the note saying what was sent

    The send is on the DENYLIST for everyone else, and stays there: this scope is the only way
    past it, only for that exact path and body shape, and only while the server gate
    ZUPER_AUTOREPLY_ENABLED is on. Every other non-GET inside the scope is refused — no PUT, no
    DELETE, no email, no second kind of send. Opened by app/autoreply/zconnect.py and nothing
    else."""
    token = _AUTOREPLY.set(True)
    try:
        yield
    finally:
        _AUTOREPLY.reset(token)


@contextlib.contextmanager
def followup_write():
    """The call follow-up's own two writes (2026-10-21; app/followup) — the SAME two as the
    auto-reply's, in the same exact shape: one SMS through POST /customers/{uid}/send and the
    private job note saying what was sent. Its own server gate, CALL_FOLLOWUP_ENABLED, so
    switching one feature on never opens the other. Opened by app/followup/zconnect.py only."""
    token = _FOLLOWUP.set(True)
    try:
        yield
    finally:
        _FOLLOWUP.reset(token)


@contextlib.contextmanager
def payments_write():
    """The payments agent's customer reminder (2026-10-21; app/payments): the SAME send in the
    same exact shape as the auto-reply's, under its own server gate PAYMENTS_TEXTS_ENABLED.
    Opened by app/payments/service.py only, and only when an admin promoted its reminders."""
    token = _PAYMENTS.set(True)
    try:
        yield
    finally:
        _PAYMENTS.reset(token)


@contextlib.contextmanager
def reviews_write():
    """Reviews & QC's texts and job notes (2026-10-22; app/reviews): the SAME send and private
    note, in the same exact shape as the auto-reply's, under its own server gate
    REVIEWS_TEXTS_ENABLED. Opened by app/reviews/zconnect.py only."""
    token = _REVIEWS.set(True)
    try:
        yield
    finally:
        _REVIEWS.reset(token)


def _send_scope() -> tuple[bool, bool, str, str]:
    """(inside a send scope, its server gate is on, its off-sentence, its name)."""
    if _AUTOREPLY.get():
        return True, config.autoreply_enabled(), config.AUTOREPLY_OFF_SENTENCE, "auto-reply"
    if _FOLLOWUP.get():
        return (True, config.followup_enabled(), config.FOLLOWUP_OFF_SENTENCE,
                "call follow-up")
    if _PAYMENTS.get():
        return (True, config.payments_texts_enabled(), config.PAYMENTS_TEXTS_OFF_SENTENCE,
                "payments agent")
    if _REVIEWS.get():
        return (True, config.reviews_texts_enabled(), config.REVIEWS_TEXTS_OFF_SENTENCE,
                "reviews agent")
    return False, False, "", ""


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
    # A visit's times (Dispatch phase 3): verified live 2026-10-01 — a plain PUT /jobs ignores
    # scheduled_start_time / scheduled_end_time; this one takes them.
    "job_update": "/jobs/{uid}/update",
    "job_notes": "/jobs/{uid}/note",
    "job_note": "/jobs/{uid}/note/{note_uid}",
    # Live (2026-09-24): /jobs/{uid}/attachments answers 404 and the job record carries no
    # files; a job's pictures hang on its NOTES, so zapi.job_attachments reads job_notes.
    # The account-wide attachments module is listed here only as a read for the setup check:
    # nothing in it says which record a row belongs to, so it can never answer "this job's".
    "job_attachments": "/attachments",
    # A job's photo albums with each one's picture count (read-only, verified 2026-10-22):
    # GET /attachments/folders?filter.module=JOB&filter.module_uid=<job uid>.
    "job_folders": "/attachments/folders",
    "job_service_tasks": "/jobs/{uid}/service_tasks",
    "job_service_task": "/jobs/{uid}/service_tasks/{task_uid}",
    "appointments": "/appointments",
    "appointment": "/appointments/{uid}",
    "estimates": "/estimate",
    "estimate": "/estimate/{uid}",
    "invoices": "/invoice",
    "invoice": "/invoice/{uid}",
    # Read by the history pass only (2026-09-30, the KPI reports). GET only — writes to
    # payments stay on the denylist below.
    "payments": "/payments/transactions",
    "commissions": "/commissions",
    "product": "/product/{uid}",
    "custom_fields": "/settings/custom_fields",     # definitions: REST path unverified
    "lead_sources": "/settings/lead_sources",       # MCP-only per research: unverified
    # The account's activity log, newest first (Dispatch assistant, 2026-10-08). GET only;
    # verified live: ?count=50&page=N, each row with users, metadata.request_source and, for a
    # job, activity_action_uid = the job's uid.
    "activities": "/activities/recent",
    # The auto-reply (2026-10-19) reads this before every send to check the sender (verified
    # live: data.zuper_connect.system_notification_number / _enabled). Its send is NOT here:
    # nothing in PATHS may be a send (tests/test_zuper_api.py) — see AUTOREPLY_SEND below.
    "company_config": "/company/config",
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
    ("PUT", _rx(PATHS["job_update"])),
    ("GET", _rx(PATHS["job_notes"])), ("POST", _rx(PATHS["job_notes"])),
    ("PUT", _rx(PATHS["job_note"])), ("DELETE", _rx(PATHS["job_note"])),
    ("GET", _rx(PATHS["job_attachments"])), ("GET", _rx(PATHS["job_folders"])),
    ("GET", _rx(PATHS["job_service_tasks"])), ("POST", _rx(PATHS["job_service_tasks"])),
    ("PUT", _rx(PATHS["job_service_task"])), ("DELETE", _rx(PATHS["job_service_task"])),
    ("GET", _rx(PATHS["appointments"])), ("POST", _rx(PATHS["appointments"])),
    ("GET", _rx(PATHS["appointment"])), ("PUT", _rx(PATHS["appointment"])),
    ("DELETE", _rx(PATHS["appointment"])),
    ("GET", _rx(PATHS["estimates"])), ("GET", _rx(PATHS["estimate"])),
    ("GET", _rx(PATHS["invoices"])), ("GET", _rx(PATHS["invoice"])),
    ("GET", _rx(PATHS["payments"])), ("GET", _rx(PATHS["commissions"])),
    ("GET", _rx(PATHS["product"])), ("GET", _rx(PATHS["activities"])),
    ("GET", _rx(PATHS["company_config"])),
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


# Registering the webhook is a write a one-way mirror still makes: it is how Zuper is asked to
# TELL the CRM about a change, and only the operator's command sends it. (The other is
# `AHS_CREATE_WRITES` below — an emailed AHS work order creating its job.)
MIRROR_WRITES = (PATHS["webhook_create"], PATHS["webhooks"])


def _mirror_write_allowed(path: str) -> bool:
    return bool(_OPERATOR.get()) and any(path == p or path.startswith(p + "/")
                                         for p in MIRROR_WRITES)


# An AHS work-order email creating its job (2026-09-28): CREATES only, and only these three —
# the customer the job belongs to, the job itself, and the work order as a note. Deliberately
# no PUT and no DELETE anywhere on the list, so nothing inside this scope can change or remove
# a record that already exists in Zuper.
AHS_CREATE_WRITES: tuple[tuple[str, str], ...] = (
    ("POST", _rx(PATHS["customer_create"])),
    ("POST", _rx(PATHS["jobs"])),
    ("POST", _rx(PATHS["job_notes"])),
)


def _ahs_create_allowed(method: str, path: str) -> bool:
    if not (_AHS_CREATE.get() and config.ahs_email_creates_jobs()):
        return False
    return any(m == method and re.match(rx, path) for m, rx in AHS_CREATE_WRITES)


# The Dispatch page's agent applying ONE confirmed change (phase 3, 2026-10-01). Exactly the
# requests the five actions need, and no more:
#   PUT  /jobs                    a job custom field (with its group metadata) / the job address
#   PUT  /jobs/{uid}/update       a visit's scheduled start and end
#   PUT  /jobs/{uid}/status       a stage move (writes.py keeps it on the job's own board)
#   POST /jobs/{uid}/note         a note
# No DELETE, no POST /jobs (a new job), nothing on customers, attachments, invoices or
# estimates. The reads the writes need (the job, its notes, the board's statuses) are GETs.
DISPATCH_WRITES: tuple[tuple[str, str], ...] = (
    ("PUT", _rx(PATHS["jobs"])),
    ("PUT", _rx(PATHS["job_update"])),
    ("PUT", _rx(PATHS["job_status"])),
    ("POST", _rx(PATHS["job_notes"])),
)


def _dispatch_write_allowed(method: str, path: str) -> bool:
    if not (_DISPATCH_WRITE.get() and config.dispatch_writes_enabled()):
        return False
    return any(m == method and re.match(rx, path) for m, rx in DISPATCH_WRITES)


# A signed proposal's option onto its job (2026-10-02): one request, and only these job keys.
PROPOSAL_LINE_WRITES: tuple[tuple[str, str], ...] = (("PUT", _rx(PATHS["jobs"])),)
PROPOSAL_LINE_JOB_KEYS = frozenset({"job_uid", "products", "job_total"})


def _proposal_lines_allowed(method: str, path: str, body: Any = None) -> bool:
    if not (_PROPOSAL_LINES.get() and config.proposal_lines_enabled()):
        return False
    if not any(m == method and re.match(rx, path) for m, rx in PROPOSAL_LINE_WRITES):
        return False
    job = body.get("job") if isinstance(body, dict) else None
    return isinstance(job, dict) and set(job) <= PROPOSAL_LINE_JOB_KEYS and "job_uid" in job


# The auto-reply's two writes (2026-10-19). The send must carry exactly this shape: one SMS,
# one recipient, a body — nothing that could become an email, a template or a bulk send.
# The path lives in app/autoreply/zconnect.py; only its pattern is here, inside this scope.
AUTOREPLY_SEND = ("POST", r"^/customers/[^/]+/send$")
AUTOREPLY_WRITES: tuple[tuple[str, str], ...] = (AUTOREPLY_SEND,
                                                 ("POST", _rx(PATHS["job_notes"])))
AUTOREPLY_MAX_CHARS = 480


def _autoreply_send_body_ok(body: Any) -> bool:
    if not (isinstance(body, dict) and set(body) == {"type", "sms"} and body["type"] == "SMS"):
        return False
    sms = body["sms"]
    return (isinstance(sms, dict) and set(sms) == {"body", "to"}
            and isinstance(sms["body"], str) and 0 < len(sms["body"]) <= AUTOREPLY_MAX_CHARS
            and isinstance(sms["to"], str) and re.fullmatch(r"\+1\d{10}", sms["to"]) is not None)


def _autoreply_allowed(method: str, path: str, body: Any = None) -> bool:
    """Inside the auto-reply's OR the call follow-up's scope, with that scope's gate on."""
    inside, on, _, _ = _send_scope()
    if not (inside and on):
        return False
    if method == AUTOREPLY_SEND[0] and re.match(AUTOREPLY_SEND[1], path):
        return _autoreply_send_body_ok(body)
    return any(m == method and re.match(rx, path) for m, rx in AUTOREPLY_WRITES)


def request(method: str, path: str, *, params: dict | None = None,
            body: dict | list | None = None) -> Any:
    method = method.upper()
    path = "/" + path.lstrip("/")
    # Inside the auto-reply scope only its own two writes exist (the send is the ONE way past
    # the denylist, and only in its exact shape); nothing at all while the server gate is off.
    inside, gate_on, off_sentence, scope_name = _send_scope()
    if method != "GET" and inside and not _autoreply_allowed(method, path, body):
        raise ZuperError("refused", "%s %s is not one of the %s's writes%s" % (
            method, path, scope_name, "" if gate_on else " (" + off_sentence + ")"))
    autoreply_send = (inside and method == AUTOREPLY_SEND[0]
                      and re.match(AUTOREPLY_SEND[1], path) is not None)
    why = None if autoreply_send else denied(method, path)
    if why:
        raise ZuperError("refused", "%s %s is never called: %s" % (method, path, why))
    if not (autoreply_send or allowed(method, path)):
        raise ZuperError("refused", "%s %s is not on the sync's allowlist" % (method, path))
    if not (config.env_enabled() or _OPERATOR.get()):
        raise ZuperError("off")
    if not config.key_set():
        raise ZuperError("no_key")
    if method != "GET" and _READ_ONLY.get():
        raise ZuperError("refused", "a dry run makes no %s request" % method)
    if method != "GET" and _DISPATCH_WRITE.get() and not _dispatch_write_allowed(method, path):
        # Inside the Dispatch scope only its own four writes exist, whatever else the sync's
        # allowlist permits — and none of them while the server gate is off.
        raise ZuperError("refused", "%s %s is not a change the Dispatch page may make%s" % (
            method, path, "" if config.dispatch_writes_enabled()
            else " (" + config.DISPATCH_WRITES_OFF_SENTENCE + ")"))
    if method != "GET" and _PROPOSAL_LINES.get() \
            and not _proposal_lines_allowed(method, path, body):
        raise ZuperError("refused", "%s %s is not the proposal-lines write (only PUT /jobs with "
                                    "job_uid, products and job_total)%s" % (
                                        method, path, "" if config.proposal_lines_enabled()
                                        else "; ZUPER_PROPOSAL_LINES is off"))
    if method != "GET" and config.pull_only() and not _mirror_write_allowed(path) \
            and not _ahs_create_allowed(method, path) \
            and not _dispatch_write_allowed(method, path) \
            and not _proposal_lines_allowed(method, path, body) \
            and not _autoreply_allowed(method, path, body):
        # Backstop for the one-way mirror: every caller checks config.pull_only() first, so
        # reaching here is a bug — refuse before a connection exists rather than write.
        raise ZuperError("refused", config.PULL_ONLY_SENTENCE)
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


# ----------------------------------------------------------- Zuper Connect: call history

# The Dispatch page reads Zuper's CALL HISTORY (2026-09-30, the owner's ask: the office must see
# every call, Quo's and Zuper's). Zuper Connect lives on its own host and is denylisted above,
# because almost everything under it rings or texts a customer. These two reads are the only
# exception, and they are not on the allowlist either: they go through `connect_read` alone,
# which knows exactly two requests and refuses everything else before a connection exists.
#   POST {connect}/api/telephony/calls             — the history SEARCH (a body of filters; it
#                                                   reads, the web app's own call list uses it)
#   GET  {connect}/api/telephony/calls/{uid}/details
# Verified read-only against the live account on 2026-09-30.
#
# The auto-reply (2026-10-19, owner-approved) reads the TEXT conversations on the same host:
#   POST {connect}/api/telephony/message/conversations   — the inbox list (a body of filters)
#   GET  {connect}/api/telephony/message/conversation/{uid}
#   POST {connect}/api/telephony/messages                 — ONE conversation's history, a
#        search {"filters": {"conversation_uid", "message_type"}, "limit", "page"}: the request
#        Zuper's own conversation screen makes to show it (found read-only, 2026-10-19).
# Still reads only: sending a text is never a Connect request (see `autoreply_write`).
CONNECT_READS: tuple[tuple[str, str], ...] = (
    ("POST", r"^/calls$"),
    ("GET", r"^/calls/[A-Za-z0-9-]+/details$"),
    ("POST", r"^/message/conversations$"),
    ("GET", r"^/message/conversation/[A-Za-z0-9-]+$"),
    ("POST", r"^/messages$"),
)


def connect_base(base: str | None = None) -> str:
    """https://us-east-1.zuperpro.com/api -> https://us-east-1-connect.zuperpro.com/api/telephony"""
    parts = urlsplit(base or config.base_url())
    host = parts.hostname or ""
    region = host.split(".", 1)[0]
    return "https://%s-connect.%s/api/telephony" % (region, ALLOWED_HOST)


def connect_read(method: str, path: str, *, body: dict | None = None) -> Any:
    """One of the two Zuper Connect reads, or a refusal. Same switches as `request`: the sync
    must be on (or an operator command), the key set; pacing and 429 back-off are shared."""
    method = method.upper()
    path = "/" + path.lstrip("/")
    if not any(m == method and re.match(rx, path) for m, rx in CONNECT_READS):
        raise ZuperError("refused", "%s %s is not one of the Zuper Connect reads" % (method, path))
    if not (config.env_enabled() or _OPERATOR.get()):
        raise ZuperError("off")
    if not config.key_set():
        raise ZuperError("no_key")
    base = connect_base()
    _check_host(base)
    headers = {"x-api-key": config.api_key(), "Accept": "application/json"}
    for attempt in range(RETRIES_ON_429 + 1):
        _pace()
        try:
            with httpx.Client(transport=TRANSPORT, timeout=TIMEOUT_SECONDS) as http:
                resp = http.request(method, base + path, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise ZuperError("unavailable", type(exc).__name__) from None
        if resp.status_code != 429:
            break
        _sleep(_retry_after(resp, attempt + 1))
    else:
        raise ZuperError("rate_limited", status=429)
    if resp.status_code in (401, 403):
        raise ZuperError("unauthorized", "HTTP %d" % resp.status_code, resp.status_code)
    if resp.status_code == 404:
        raise ZuperError("not_found", path, 404)
    if resp.status_code >= 400:
        raise ZuperError("unavailable" if resp.status_code >= 500 else "rejected",
                         "HTTP %d" % resp.status_code, resp.status_code)
    try:
        return resp.json()
    except ValueError:
        raise ZuperError("bad_response", "not JSON") from None


def connect_calls(page: int = 1, limit: int = 100) -> dict:
    """A page of the account's call history, newest first (every user's calls)."""
    return connect_read("POST", "/calls", body={
        "page": page, "limit": limit, "sort": "DESC", "sortBy": "created_at",
        "filters": {"current_user": False}})


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

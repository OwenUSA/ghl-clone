"""`GET /api/connection-status` — is everything working? One answer for the top-bar dot.

The owner (2026-09-14): *"i want something more subtle ... on the top right that shows on
every page or view, but very subtle and if i hover onto it it should show any more details
if needed, but it should always show if we are online or not so i know if everything
working good"*. The dot is the worst of three checks. The browser already knows the first
(its own SIP registration); this endpoint answers the other two:

  * **link** — can this CRM reach owen-main, and is the CRM link enabled there?
  * **quo** — is the OpenPhone (Quo) mirror running: switched on, ticking, backfilled,
    webhook on, and when did the last webhook arrive?

## One hop, server-side, and the browser never sees a secret

    browser ──cookie──▶ /api/connection-status ──X-OWEN-Key──▶ owen-main GET /api/link-status

The key is the one this CRM already holds for owen-main: `CRM_LINK_API_KEY` when the
send link is configured, otherwise the softphone's `OWEN_SOFTPHONE_KEY`. Both carry
owen-main's `crm_link` scope. Neither the key nor owen-main's URL is in the response.

## Always 200

If owen-main cannot be reached, that IS the answer — `link.state = "down"` with a sentence —
not an error. A dot that went grey because its own status request failed would be hiding
exactly the outage it exists to show.

## Short timeout, short cache

Every signed-in browser polls this about once a minute and on window focus. One owen-main
request per `CACHE_SECONDS` serves all of them, and a timeout of `TIMEOUT_SECONDS` means a
dead phone system costs one slow request per window, not one per tab. Failures are cached
too, for the same reason.

## Names nobody — except our own line, deliberately (2026-09-16)

owen-main's payload carries no number, and this module forwards only what it names below —
states, sentences, switches and timestamps. **No key, no URL and no CUSTOMER'S number ever
survives into the body**, and `test_connection_status.py` asserts it.

`our_line` is the one exception and is not a leak: it is the company's own published
business number, already printed on the composer ("Sending from …"), the dialer ("Calling
from …") and the AI escalation copy. It is here because those three screens had it
HARD-CODED, in five files, and the owner changed the line — so the one place that actually
knows it (`crmlink`) now tells the browser, and a future change is one environment variable
rather than a hunt through the frontend.

It is read PER REQUEST rather than from the 30-second cache: the cache exists to stop N tabs
becoming N requests to owen-main, and our own configuration costs nothing to look up.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter

from . import auth, crmlink, softphone

router = APIRouter(prefix="/api/connection-status", tags=["connection-status"])

STATUS_PATH = "/api/link-status"
CACHE_SECONDS = 30.0
TIMEOUT_SECONDS = 3.0

# The four states a check can be in, worst first. `unknown` is a check that could not be
# asked because an earlier hop failed; it ranks below the failure that caused it.
OK, DEGRADED, DOWN, OFF, UNKNOWN = "ok", "degraded", "down", "off", "unknown"

# Sentences. Written for the owner at a glance, not for an engineer — the switch names are
# in owen-main's own refusals and in DECISIONS.md, not on his screen.
LINK_OFF = "This CRM is not connected to the phone system on this deployment."
LINK_UNREACHABLE = "Can't reach the phone system."
LINK_REFUSED = "The phone system refused this CRM's key."
LINK_TOO_OLD = "The phone system is reachable but cannot report its status yet (it needs updating)."
LINK_ERROR = "The phone system answered with an error (HTTP %d)."
LINK_DISABLED = ("The CRM link is switched off in the phone system — calls and texts are "
                 "not reaching the CRM.")
LINK_NO_TELEPHONY = "The phone system is not taking calls right now."
LINK_OK = "Connected to the phone system."

QUO_UNKNOWN = "Unknown — the phone system could not be asked."
QUO_UNREADABLE = "The phone system could not read the Quo sync state."
QUO_OFF = "Quo sync is switched off — Quo calls and texts are not reaching the CRM."
QUO_NO_KEY = "Quo sync has no Quo API key, so it cannot read Quo."
QUO_NEVER_TICKED = "Quo sync has not recorded a check yet."
QUO_STALE = "Quo sync has stopped checking in — it may not be running."
QUO_TICK_FAILED = "Quo sync's last check did not run."
QUO_BACKFILL = "The 30-day Quo history has not finished importing."
QUO_WEBHOOK_OFF = ("The Quo webhook is off — new Quo activity arrives on the next check "
                   "instead of within seconds.")
QUO_WEBHOOK_NO_SECRET = "The Quo webhook has no signing secret, so every delivery is refused."
QUO_OK = "Quo sync is running."

# A tick is stale after this many poll intervals without a heartbeat (the brief: "~2").
STALE_INTERVALS = 2


@dataclass(frozen=True)
class Target:
    base_url: str
    key: str

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.key)


def target() -> Target:
    """Where to ask, and with which key. Read per request like every other owen-main config
    here, so a test can set the environment after import."""
    link = crmlink.current()
    if link.configured:
        return Target(link.base_url, link.api_key)
    sp = softphone.config()
    return Target(sp.base_url, sp.key)


def get_json(url: str, *, key: str, timeout: float) -> tuple[int, Any]:
    """One GET to owen-main. Isolated so tests replace it wholesale — a test must never be
    able to reach a live telephony service."""
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(url, headers={"X-OWEN-Key": key, "Accept": "application/json"})
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, None


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _check(state: str, sentence: str) -> dict:
    return {"state": state, "sentence": sentence}


def link_check(status: int | None, body: Any) -> dict:
    """The link check from owen-main's answer. `status` None means we could not ask."""
    if status is None:
        return _check(DOWN, LINK_UNREACHABLE)
    if status in (401, 403):
        return _check(DOWN, LINK_REFUSED)
    if status == 404:
        return _check(DEGRADED, LINK_TOO_OLD)
    if status != 200 or not isinstance(body, dict):
        return _check(DOWN, LINK_ERROR % status)
    link = body.get("crm_link") if isinstance(body.get("crm_link"), dict) else {}
    if not link.get("enabled"):
        return _check(DOWN, LINK_DISABLED)
    if not link.get("telephony_enabled"):
        return _check(DOWN, LINK_NO_TELEPHONY)
    return _check(OK, LINK_OK)


def quo_check(body: Any) -> dict:
    """The Quo check, plus the facts the hover card lists. `body` None: not asked."""
    if not isinstance(body, dict) or not isinstance(body.get("quo"), dict):
        return {**_check(UNKNOWN, QUO_UNKNOWN), "facts": None}
    q = body["quo"]
    poll = q.get("poll_seconds") if isinstance(q.get("poll_seconds"), int) else 300
    checked = _parse(body.get("checked_at"))
    tick = _parse(q.get("last_tick_at"))
    # Measured on owen-main's own clock (checked_at vs the tick it wrote), so container
    # clock skew between the two services can never make a live sync look stale.
    stale = bool(tick and checked and (checked - tick).total_seconds() > STALE_INTERVALS * poll)
    facts = {
        "mirror_enabled": bool(q.get("mirror_enabled")),
        "poll_seconds": poll,
        "last_tick_at": q.get("last_tick_at") if tick else None,
        "last_tick_ran": (q.get("last_tick_ran")
                          if isinstance(q.get("last_tick_ran"), bool) else None),
        "tick_stale": stale,
        "backfill_days": q.get("backfill_days") if isinstance(q.get("backfill_days"), int) else 30,
        "backfill_completed_at": (q.get("backfill_completed_at")
                                  if _parse(q.get("backfill_completed_at")) else None),
        "webhook_enabled": bool(q.get("webhook_enabled")),
        "last_webhook_at": q.get("last_webhook_at") if _parse(q.get("last_webhook_at")) else None,
    }

    def out(state: str, sentence: str) -> dict:
        return {**_check(state, sentence), "facts": facts}

    if body.get("database_readable") is False:
        return out(DEGRADED, QUO_UNREADABLE)
    if not facts["mirror_enabled"]:
        return out(DOWN, QUO_OFF)
    if not q.get("api_key_present"):
        return out(DOWN, QUO_NO_KEY)
    if tick is None:
        return out(DEGRADED, QUO_NEVER_TICKED)
    if stale:
        return out(DEGRADED, QUO_STALE)
    if facts["last_tick_ran"] is False:
        return out(DEGRADED, QUO_TICK_FAILED)
    if facts["backfill_completed_at"] is None:
        return out(DEGRADED, QUO_BACKFILL)
    if not facts["webhook_enabled"]:
        return out(DEGRADED, QUO_WEBHOOK_OFF)
    if not q.get("webhook_secret_configured"):
        return out(DEGRADED, QUO_WEBHOOK_NO_SECRET)
    return out(OK, QUO_OK)


def build(fetch: Callable[..., tuple[int, Any]] | None = None) -> dict:
    """Ask owen-main once and turn the answer into the response body."""
    fetch = fetch or get_json
    tgt = target()
    now = datetime.now(UTC).isoformat()
    if not tgt.configured:
        return {"checked_at": now, "link": _check(OFF, LINK_OFF),
                "quo": {**_check(UNKNOWN, QUO_UNKNOWN), "facts": None}}
    try:
        status, body = fetch(tgt.base_url + STATUS_PATH, key=tgt.key, timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError:
        status, body = None, None
    link = link_check(status, body)
    quo = quo_check(body if status == 200 else None)
    return {"checked_at": now, "link": link, "quo": quo}


_lock = threading.Lock()
_cache: dict[str, Any] = {}


def cached(fetch: Callable[..., tuple[int, Any]] | None = None) -> dict:
    """`build()`, at most once per `CACHE_SECONDS` across every request. The lock makes a
    burst of polls (every tab refocusing at once) one owen-main request, not N."""
    with _lock:
        hit = _cache.get("body")
        if hit is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return hit
        body = build(fetch)
        _cache.update(body=body, at=time.monotonic())
        return body


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def our_line() -> str:
    """The number this CRM calls and texts FROM, as configured. One definition, in
    `app/crmlink.py`; this is a window onto it, not a second copy."""
    return crmlink.current().from_number


@router.get("")
def connection_status(_: auth.Principal = auth.ANY_USER) -> dict:
    """The link and Quo checks, and the line this CRM sends from.

    Every role reads it: the dot is on every signed-in page, "is the phone system up" is not
    privileged information, and neither is the number the company prints on its vans.
    """
    return {**cached(), "our_line": our_line()}

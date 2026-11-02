"""Quo (formerly OpenPhone) public API — Owen AI's READ-ONLY client (2026-11-02).

GET only, and only the paths in `READS`: phone numbers, users, contacts, conversations, calls,
call summaries / transcripts, messages. A send (`POST /messages`) or any other method is refused
before a connection exists. The key is `OWEN_AI_QUO_API_KEY` (the same Quo workspace key
owen-main's mirror uses); it never leaves the server.

Docs: https://www.quo.com/docs (API base https://api.openphone.com/v1, `Authorization: <key>`).
"""
from __future__ import annotations

import re
import time
from typing import Any

import httpx

from . import config

BASE = "https://api.openphone.com/v1"
TIMEOUT_SECONDS = 20.0
RETRIES_ON_429 = 4

# Tests install an `httpx.MockTransport` here.
TRANSPORT: httpx.BaseTransport | None = None

READS = (
    r"^/phone-numbers(/[A-Za-z0-9_-]+)?$",
    r"^/users(/[A-Za-z0-9_-]+)?$",
    r"^/contacts(/[A-Za-z0-9_-]+)?$",
    r"^/contact-custom-fields$",
    r"^/conversations$",
    r"^/calls$",
    r"^/calls/[A-Za-z0-9_-]+$",
    r"^/call-summaries/[A-Za-z0-9_-]+$",
    r"^/call-transcripts/[A-Za-z0-9_-]+$",
    r"^/call-voicemails/[A-Za-z0-9_-]+$",
    r"^/call-recordings/[A-Za-z0-9_-]+$",
    r"^/messages$",
    r"^/messages/[A-Za-z0-9_-]+$",
)


class QuoError(Exception):
    pass


def allowed(method: str, path: str) -> bool:
    return method.upper() == "GET" and any(re.match(rx, path) for rx in READS)


def _sleep(seconds: float) -> None:          # monkeypatched in tests
    time.sleep(seconds)


def get(path: str, params: dict | None = None) -> Any:
    path = "/" + path.strip().lstrip("/")
    if path.startswith("/v1/"):
        path = path[3:]
    if not allowed("GET", path):
        raise QuoError("GET %s is not one of Owen AI's Quo reads (%s)" % (
            path, ", ".join(rx.strip("^$") for rx in READS)))
    key = config.quo_key()
    if not key:
        raise QuoError("Quo is not connected: OWEN_AI_QUO_API_KEY is not set on this server.")
    headers = {"Authorization": key, "Accept": "application/json"}
    for attempt in range(RETRIES_ON_429 + 1):
        try:
            with httpx.Client(transport=TRANSPORT, timeout=TIMEOUT_SECONDS) as http:
                resp = http.get(BASE + path, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise QuoError("Quo did not answer (%s)" % type(exc).__name__) from None
        if resp.status_code != 429:
            break
        _sleep(min(2.0 ** (attempt + 1), 30.0))
    else:
        raise QuoError("Quo is rate limiting us (429); try again in a minute.")
    try:
        payload = resp.json() if resp.content else {}
    except ValueError:
        payload = {"raw": resp.text[:500]}
    if resp.status_code >= 400:
        msg = payload.get("message") if isinstance(payload, dict) else None
        raise QuoError("Quo answered HTTP %d: %s" % (resp.status_code, str(msg or payload)[:400]))
    return payload

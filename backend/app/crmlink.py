"""The link to owen-main, the telephony platform that owns the real phone number.

`+19544829099` is a BulkVS DID bound to this CRM. owen-main exposes an internal,
API-key-authenticated surface at `/api/crm-link/*` which this module is the only
caller of:

    POST /api/crm-link/calls      place a call from the bound DID
    POST /api/crm-link/messages   send an SMS from the bound DID
    GET  /api/crm-link/health     is the link up

Read out of owen-main's own source (`backend/app/integrations/crm/`), not guessed —
`api.py` for the request bodies, `config.py` for the refusal strings, and
`api/ai/deps.py` for the header. The shapes below are copied from there.

## Off unless configured, which preserves the locked safety contract

CLAUDE.md: "No message actually leaves the building." That stays true by default.
`CRM_LINK_BASE_URL` and `CRM_LINK_API_KEY` are both unset in every environment that
exists today, and with either missing `configured()` is False, `get_transport()`
hands back `LoggingTransport` and this module makes no network call at all. Wiring
the link is a deliberate act of configuration, not a consequence of deploying this
branch.

## SMS is DARK on the far side, and that is the expected answer today

owen-main refuses every SMS three times over: its own `CRM_LINK_SMS_ENABLED` is
false, the DID's `sms_enabled` is false, and the 10DLC campaign is SUBMITTED rather
than approved. So a send today comes back 403/409 with a reason. That is not a
failure of this code — it is the system working, and the reason is surfaced to the
operator as a sentence. The day 10DLC is approved nothing here changes.

## Every refusal is a sentence, never a wire body

owen-main answers a refusal two different ways: `{"detail": "<string>"}` from the
crm-link routes themselves, and `{"detail": {"error": ..., "message": ..., "hint":
...}}` from its API-key layer. `_detail_text` reads both. `_human` then turns the
handful of refusals an operator will actually hit into something a roofer can act
on — "the number is still waiting on carrier approval" rather than
"CRM_LINK_SMS_ENABLED=false". Anything unrecognised passes through verbatim rather
than being flattened into "something went wrong": a reason we did not anticipate is
still far more use than no reason.
"""
import logging
import os
from dataclasses import dataclass

import httpx

log = logging.getLogger("crmlink")

# The bound DID. Overridable, but this is the number the ring group is built on.
DEFAULT_FROM_NUMBER = "+19544829099"

CALLS_PATH = "/api/crm-link/calls"
MESSAGES_PATH = "/api/crm-link/messages"
HEALTH_PATH = "/api/crm-link/health"


@dataclass(frozen=True)
class LinkConfig:
    base_url: str = ""
    api_key: str = ""
    from_number: str = DEFAULT_FROM_NUMBER
    timeout_seconds: float = 10.0

    @property
    def configured(self) -> bool:
        """Both halves, or the link is off. A base URL with no key would reach
        owen-main and be refused 401 on every single send, which looks like an
        outage rather than like "not switched on"."""
        return bool(self.base_url and self.api_key)


def current() -> LinkConfig:
    """Read the environment on every call rather than at import.

    The tests flip these with `monkeypatch.setenv`, and a module-level constant
    would freeze whatever was set when the first test imported `app.main`.
    """
    try:
        timeout = float(os.getenv("CRM_LINK_TIMEOUT_SECONDS", "10") or 10)
    except ValueError:
        timeout = 10.0
    return LinkConfig(
        base_url=(os.getenv("CRM_LINK_BASE_URL", "") or "").rstrip("/"),
        api_key=os.getenv("CRM_LINK_API_KEY", "") or "",
        from_number=(os.getenv("CRM_LINK_FROM_NUMBER", "") or DEFAULT_FROM_NUMBER),
        timeout_seconds=timeout,
    )


def configured() -> bool:
    return current().configured


@dataclass(frozen=True)
class LinkResult:
    """The outcome of one call to owen-main.

    `reason` is always a sentence fit to show a person — empty only when `ok`.
    `refused` distinguishes "owen-main understood and said no" (a 4xx: dark SMS,
    not allowlisted, opted out) from "we could not ask it" (a 5xx, a timeout, a
    DNS failure). They render differently and they mean different things: the
    first will not improve by trying again, the second might.
    """

    ok: bool
    status: int = 0
    reason: str = ""
    data: dict | None = None

    @property
    def refused(self) -> bool:
        return 400 <= self.status < 500


# owen-main's refusals, in its own words, mapped to the owner's. Keys are matched as
# substrings because owen-main interpolates numbers into some of them.
_AWAITING_10DLC = ("Texting is not switched on yet — the number is still waiting "
                   "on carrier (10DLC) approval.")
_NO_OPERATOR = ("No one is set up to take this call — the phone system has no "
                "operator assigned to the number.")

_HUMAN = (
    ("CRM_LINK_SMS_ENABLED=false", _AWAITING_10DLC),
    ("pending 10DLC registration", _AWAITING_10DLC),
    ("no 10DLC campaign",
     "Texting is not switched on yet — the number has no carrier campaign assigned."),
    ("not on CRM_LINK_ALLOWLIST",
     "This number is not on the approved list for calls and texts yet."),
    ("has opted out", "This contact has opted out of text messages."),
    ("is blocked in OWEN", "This contact is blocked in the phone system."),
    ("not bound to the CRM", "The phone number is not linked to this CRM."),
    ("CRM_LINK_ENABLED=false",
     "The phone link is switched off in the phone system."),
    ("telephony is not enabled",
     "The phone system is not accepting calls right now."),
    ("no operator to ring", _NO_OPERATOR),
)


def _human(reason: str) -> str:
    for needle, sentence in _HUMAN:
        if needle in reason:
            return sentence
    return reason


def _detail_text(payload, status: int) -> str:
    """Pull a sentence out of whichever error shape owen-main used."""
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    if isinstance(detail, dict):
        # The API-key layer's envelope: {"error": code, "message": ..., "hint": ...}
        for key in ("message", "error"):
            value = detail.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(payload, dict):
        for key in ("message", "reason"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return "the phone system refused the request (HTTP %d)" % status


def _post(path: str, body: dict) -> LinkResult:
    cfg = current()
    if not cfg.configured:
        # Should be unreachable: callers check `configured()` and fall back to
        # LoggingTransport. Answering rather than raising keeps it that way.
        return LinkResult(False, 0, "the phone link is not configured")

    url = cfg.base_url + path
    try:
        resp = httpx.post(
            url, json=body, timeout=cfg.timeout_seconds,
            # X-OWEN-Key is owen-main's documented header (core/apikeys.extract_key).
            # It also accepts Bearer, but only for keys with its own `owen_sk_`
            # prefix, so the dedicated header is the one that cannot be ambiguous.
            headers={"X-OWEN-Key": cfg.api_key, "Accept": "application/json"},
        )
    except Exception as exc:  # noqa: BLE001 - any transport failure is one outcome
        log.warning("crm-link: POST %s failed: %r", url, exc)
        return LinkResult(False, 0, "could not reach the phone system")

    try:
        payload = resp.json()
    except ValueError:
        payload = {}

    if resp.status_code >= 400:
        raw = _detail_text(payload, resp.status_code)
        log.warning("crm-link: POST %s refused (%d): %s", path, resp.status_code, raw)
        return LinkResult(False, resp.status_code, _human(raw),
                          payload if isinstance(payload, dict) else None)
    return LinkResult(True, resp.status_code, "",
                      payload if isinstance(payload, dict) else None)


def send_sms(to_number: str, body: str) -> LinkResult:
    """`POST /api/crm-link/messages`. Shape from owen-main's `SendMessageIn`.

    On success owen-main answers `{"ok": true, "message_id": "...", "status":
    "queued"}`. That `message_id` is the join key a delivery receipt arrives with,
    so the caller stores it as `provider_ref`.
    """
    return _post(MESSAGES_PATH, {
        "from_number": current().from_number,
        "to_number": to_number,
        "body": body,
    })


def place_call(to_number: str, operator: str | None = None) -> LinkResult:
    """`POST /api/crm-link/calls`. Shape from owen-main's `OutboundCallIn`.

    `operator` is left None: owen-main falls back to the binding's own
    `outbound_operator`, which is where the ring group is configured. Naming an
    operator from the CRM would put a second copy of that configuration here.

    On success owen-main answers `{"ok": true, "operator_channel": ...,
    "callee_channel": ..., "linkedid": ...}` and rings a real phone.
    """
    body: dict[str, str] = {
        "from_number": current().from_number,
        "to_number": to_number,
    }
    if operator:
        body["operator"] = operator
    return _post(CALLS_PATH, body)


def health() -> LinkResult:
    cfg = current()
    if not cfg.configured:
        return LinkResult(False, 0, "the phone link is not configured")
    try:
        resp = httpx.get(cfg.base_url + HEALTH_PATH, timeout=cfg.timeout_seconds,
                         headers={"X-OWEN-Key": cfg.api_key,
                                  "Accept": "application/json"})
    except Exception as exc:  # noqa: BLE001
        log.warning("crm-link: health check failed: %r", exc)
        return LinkResult(False, 0, "could not reach the phone system")
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    if resp.status_code >= 400:
        return LinkResult(False, resp.status_code,
                          _human(_detail_text(payload, resp.status_code)))
    return LinkResult(True, resp.status_code, "",
                      payload if isinstance(payload, dict) else None)

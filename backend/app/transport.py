"""Outbound side-effect seam.

Originally LOCKED as UI-only: `LoggingTransport` was the only implementation and
nothing left the building. That is still the DEFAULT and still what an unconfigured
deployment does. As of 2026-09-11 there is a second implementation,
`CrmLinkTransport`, which hands the message to owen-main (the telephony platform)
for delivery over the real BulkVS DID `+19544829099`.

**Which one is live is decided by configuration, not by this branch.** `get_transport()`
returns `CrmLinkTransport` only when BOTH `CRM_LINK_BASE_URL` and `CRM_LINK_API_KEY`
are set; otherwise it returns `LoggingTransport` and behaviour is byte-for-byte what
it was. `GET /api/health` reports which one is wired, so "is this thing armed?" is a
question with an answer rather than a guess.

Only OUTBOUND side effects go through this seam. Inbound messages and call records
are plain data we own and display, written directly into the schema by
`POST /api/events`.

## Why `MessageRef` grew a status

It used to carry `delivered: bool`, which has exactly two answers and the real
lifecycle has five. A message handed to owen-main is QUEUED — accepted, not yet
sent, and not yet delivered — and a message owen-main refuses is REFUSED, which is
neither "delivered" nor "failed": nothing broke, the system declined on purpose and
can say why. Collapsing those into a boolean is what made every message in the
thread read "not sent (stub transport)" regardless of what actually happened.

`delivered` is kept as a derived property because that is the seam's original
promise and `test_outbound_is_never_marked_delivered` pins it.
"""
import logging
from dataclasses import dataclass
from typing import Protocol

from . import crmlink
from .models import DeliveryStatus

log = logging.getLogger("transport")


@dataclass(frozen=True)
class MessageRef:
    """What one attempted send actually did.

    `provider_ref` is the far side's id for the message, and it is the join key a
    delivery receipt arrives with later — owen-main's `message_id`. `detail` is a
    sentence for a person: why a refusal happened, or empty when nothing needs
    explaining.
    """

    provider_ref: str
    status: DeliveryStatus
    detail: str = ""

    @property
    def delivered(self) -> bool:
        """Whether the customer has it. Only ever true on a delivery receipt, which
        arrives after the send, so a transport can never answer True here."""
        return self.status is DeliveryStatus.DELIVERED


class MessageTransport(Protocol):
    def send_sms(self, to: str, body: str, from_number: str) -> MessageRef: ...

    def send_email(self, to: str, subject: str, html: str) -> MessageRef: ...


class LoggingTransport:
    """Records intent, transmits nothing. The default, and the whole transport in
    every environment where the CRM link is not configured."""

    def send_sms(self, to: str, body: str, from_number: str) -> MessageRef:
        log.info("SMS suppressed to=%s from=%s chars=%d", to, from_number, len(body))
        return MessageRef(provider_ref="logged",
                          status=DeliveryStatus.LOGGED_ONLY)

    def send_email(self, to: str, subject: str, html: str) -> MessageRef:
        log.info("EMAIL suppressed to=%s subject=%r", to, subject)
        return MessageRef(provider_ref="logged",
                          status=DeliveryStatus.LOGGED_ONLY)


class CrmLinkTransport:
    """Hands SMS to owen-main, which sends it from the bound BulkVS DID.

    Three outcomes, and they are deliberately three rather than two:

      * **QUEUED** — owen-main accepted it and queued it for the carrier. The
        `message_id` it returns is stored so the delivery receipt can find the row.
      * **REFUSED** — owen-main said no, with a reason: SMS is dark pending 10DLC,
        the destination is not allowlisted, the contact opted out or is blocked.
        Today this is the expected answer to every send, and the reason is what the
        operator reads in the thread.
      * **FAILED** — we could not ask: a timeout, a 5xx, a DNS failure. Distinct
        from REFUSED because this one might work on a retry and a refusal will not.

    EMAIL is not part of the link — owen-main is a telephony platform and has no
    email path — so email still goes through the logging seam. Saying so here beats
    a transport that silently drops half of what it is handed.
    """

    def send_sms(self, to: str, body: str, from_number: str) -> MessageRef:
        result = crmlink.send_sms(to_number=to, body=body)
        if result.ok:
            data = result.data or {}
            return MessageRef(provider_ref=str(data.get("message_id") or ""),
                              status=DeliveryStatus.QUEUED)
        return MessageRef(
            provider_ref="",
            status=(DeliveryStatus.REFUSED if result.refused
                    else DeliveryStatus.FAILED),
            detail=result.reason,
        )

    def send_email(self, to: str, subject: str, html: str) -> MessageRef:
        log.info("EMAIL suppressed (the CRM link carries SMS only) to=%s subject=%r",
                 to, subject)
        return MessageRef(provider_ref="logged",
                          status=DeliveryStatus.LOGGED_ONLY)


_LOGGING = LoggingTransport()
_CRM_LINK = CrmLinkTransport()


def get_transport() -> MessageTransport:
    """Resolved per call, from the environment.

    Not a module-level constant: the configuration is read from env vars that the
    tests flip, and freezing the choice at import time would mean the first test to
    import `app.main` decided it for the whole session.
    """
    return _CRM_LINK if crmlink.configured() else _LOGGING

"""Outbound side-effect seam.

LOCKED DECISION (DECISIONS.md): SMS and email are UI-only in v1. The screens are
real and complete; nothing leaves the building. A separate telephony project
supplies a real implementation later, as a second class here, with no UI change.

Only OUTBOUND side effects go through this seam. Inbound messages and call
records are plain data we own and display, written directly into the schema.
"""
import logging
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger("transport")


@dataclass(frozen=True)
class MessageRef:
    provider_ref: str
    delivered: bool


class MessageTransport(Protocol):
    def send_sms(self, to: str, body: str, from_number: str) -> MessageRef: ...

    def send_email(self, to: str, subject: str, html: str) -> MessageRef: ...


class LoggingTransport:
    """The only implementation in v1. Records intent, transmits nothing."""

    def send_sms(self, to: str, body: str, from_number: str) -> MessageRef:
        log.info("SMS suppressed to=%s from=%s chars=%d", to, from_number, len(body))
        return MessageRef(provider_ref="logged", delivered=False)

    def send_email(self, to: str, subject: str, html: str) -> MessageRef:
        log.info("EMAIL suppressed to=%s subject=%r", to, subject)
        return MessageRef(provider_ref="logged", delivered=False)


_transport: MessageTransport = LoggingTransport()


def get_transport() -> MessageTransport:
    return _transport

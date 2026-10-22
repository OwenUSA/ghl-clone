"""When the auto-reply answers, and when it must not. PURE: no database, no network, `now`
passed in — every rule is pinned by tests/test_autoreply_rules.py with plain objects.

The owner's answers (2026-10-19):

* A STRETCH is what the customer sent after the last time a PERSON answered them: their texts
  and their calls nobody picked up (any length, forwarded ones too — Q27). "Ok", "thanks", 👍,
  "C" / "yes" confirmations and STOP never open a stretch (Q14).
* A person answered (Q13, Q30) = a later outgoing text on the Zuper line that is NOT one of the
  six Zuper workflows' texts and NOT the auto-reply's own; an outgoing text on Quo or the CRM
  line not written by an AI agent; or a call somebody picked up, on any line, either direction.
* The stretch is answered once (Q5): after the auto-reply's text it stays quiet until a person
  has answered and the customer writes or calls again.
* It waits 5 minutes from the stretch's FIRST message (and a minute of quiet after the last),
  around the clock (Q3). Nothing older than a day, nothing that started waiting before the
  auto-reply was switched on (Q22), never a second text to one number within 24 hours (Q11).
* A blocked number, or a customer who texted STOP (and not START since), is never texted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..dispatch.comms import Comm
from . import config as c

# Zuper Connect's call statuses (verified live 2026-10-19: COMPLETED, MISSED, VOICEMAIL,
# CANCELED). An incoming call that did not end COMPLETED with talk time reached nobody.
CALL_ANSWERED = "COMPLETED"
VOICEMAIL = "VOICEMAIL"

# Q14: a message that closes the conversation or confirms a visit needs no answer.
ACKS = {"ok", "okay", "k", "kk", "okey", "oki", "thanks", "thank you", "thankyou", "thx", "ty",
        "great", "perfect", "sounds good", "got it", "will do", "cool", "awesome", "no problem",
        "ok thanks", "ok thank you", "ok great", "thanks so much", "thank you so much",
        "thank you very much", "yes", "y", "yeah", "yep", "yup", "sure", "c", "confirm",
        "confirmed", "yes confirmed", "i confirm", "yes thank you", "yes thanks", "si", "sí",
        "gracias", "ok gracias", "muchas gracias", "si gracias", "sí gracias", "perfecto",
        "listo", "dale", "👍", "👍🏻", "👍🏼", "👍🏽", "👍🏾", "👍🏿", "🙏", "👌", "❤️", "😊"}
STOPS = {"stop", "stopall", "unsubscribe", "quit", "end", "cancel", "opt out", "optout",
         "stop texting", "stop texting me", "remove me", "parar", "basta", "no mas", "no más"}
STARTS = {"start", "unstop", "yes start", "resume"}

# The Zuper workflows' texts (docs/ZUPER-CUSTOMER-TEXT-WORKFLOWS.md §2). A text with one of
# these is automatic, not a person answering — they go out under "Owen" or "Zuper".
TEMPLATE_PHRASES = (
    "to confirm the appointment", "your appointment is confirmed", "to rate our technician",
    "thank you for your feedback", "we have submitted your inspection report",
    "has authorized your roof repair", "can you confirm you received this message",
    "reminder: your roof", "is on schedule to arrive today",
    # 2026-10-22: workflow 7, the welcome text for every new AHS job.
    "welcome to dream team roofing",
)
AUTOMATIC_USERS = {"zuper"}


def words(text: str | None) -> str:
    t = (text or "").lower().strip()
    t = re.sub(r"[.,!?¡¿;:\"'()]+", " ", t)
    return " ".join(t.split())


def is_ack(text: str | None) -> bool:
    return words(text) in ACKS


def is_stop(text: str | None) -> bool:
    return words(text) in STOPS


def is_start(text: str | None) -> bool:
    return words(text) in STARTS


def is_template(text: str | None) -> bool:
    t = (text or "").lower()
    return any(p in t for p in TEMPLATE_PHRASES)


@dataclass
class ZMsg:
    """One row of a Zuper Connect conversation's history (POST /telephony/messages)."""
    uid: str
    at: datetime
    kind: str                       # text | call
    out: bool
    body: str = ""
    user: str | None = None         # who sent it, as Zuper names them ("Luis", "Owen", "Zuper")
    call_status: str | None = None  # COMPLETED | MISSED | VOICEMAIL | CANCELED ...
    seconds: int = 0
    summary: str = ""               # Zuper's summary of the recording
    sentiment: str | None = None    # Zuper's reading of the call (e.g. "negative")
    recording_url: str | None = None
    call_uid: str | None = None
    status: str | None = None       # the text's delivery status


def answered_call(m: ZMsg) -> bool:
    return m.kind == "call" and (m.call_status or "").upper() == CALL_ANSWERED and m.seconds > 0


def missed_call(m: ZMsg) -> bool:
    """Their call nobody picked up: any length, voicemail or not (Q27)."""
    return (m.kind == "call" and not m.out and not answered_call(m)
            and m.seconds >= c.MIN_CALL_SECONDS)


@dataclass
class Ours:
    """What the auto-reply itself has sent to this number, from autoreply_log."""
    message_uids: set[str] = field(default_factory=set)
    bodies: set[str] = field(default_factory=set)
    sent_at: list[datetime] = field(default_factory=list)

    def wrote(self, m: ZMsg) -> bool:
        return m.uid in self.message_uids or (m.body or "").strip() in self.bodies


def person_answered(m: ZMsg, ours: Ours) -> bool:
    """Is this Zuper row a PERSON answering the customer?"""
    if answered_call(m):
        return True
    if m.kind != "text" or not m.out:
        return False
    if (m.status or "").upper() in ("UNDELIVERED", "FAILED"):
        return False
    return not (ours.wrote(m) or is_template(m.body)
                or (m.user or "").strip().lower() in AUTOMATIC_USERS)


def other_line_answered(x: Comm) -> bool:
    """Quo / the CRM line / a Zuper call the reader stored: a person reaching the customer."""
    if x.kind == "call":
        return x.answered and not x.ai
    return x.out and not x.ai and x.source != "Zuper"


def opens_stretch(m: ZMsg) -> bool:
    if m.out:
        return False
    if m.kind == "call":
        return missed_call(m)
    return bool((m.body or "").strip()) and not is_ack(m.body) and not is_stop(m.body)


def opted_out(msgs: list[ZMsg]) -> bool:
    state = False
    for m in msgs:
        if m.kind == "text" and not m.out:
            if is_stop(m.body):
                state = True
            elif is_start(m.body):
                state = False
    return state


@dataclass
class Decision:
    action: str                     # reply | skip | wait | nothing
    reason: str = ""
    trigger: ZMsg | None = None
    kind: str = ""                  # text | missed_call | voicemail
    stretch: list[ZMsg] = field(default_factory=list)
    after_hours: bool = False


def decide(msgs: list[ZMsg], other: list[Comm], ours: Ours, *, now: datetime,
           enabled_at: datetime | None, blocked: bool = False) -> Decision:
    """What to do about one conversation. `msgs` oldest first; `other` = every call / text
    on the customer's number from Quo, the CRM line and Zuper's call history."""
    msgs = sorted(msgs, key=lambda m: m.at)
    human = [m.at for m in msgs if person_answered(m, ours)]
    human += [c.aware(x.at) for x in other if other_line_answered(x)]
    since = max(human) if human else None
    stretch = [m for m in msgs if opens_stretch(m) and (since is None or m.at > since)]
    if not stretch:
        return Decision("nothing")
    first, last = stretch[0], stretch[-1]
    kind = ("text" if any(m.kind == "text" for m in stretch)
            else "voicemail" if any((m.call_status or "").upper() == VOICEMAIL for m in stretch)
            else "missed_call")
    d = Decision("skip", trigger=first, kind=kind, stretch=stretch,
                 after_hours=not c.office_open(now))
    if any(t > first.at for t in ours.sent_at):
        d.action, d.reason = "nothing", "the auto-reply already answered this"
        return d
    if blocked:
        d.reason = "the number is blocked in Zuper"
        return d
    if opted_out(msgs):
        d.reason = "the customer texted STOP"
        return d
    if enabled_at is not None and first.at < c.aware(enabled_at):
        d.reason = "waiting since before the auto-reply was switched on"
        return d
    if now - first.at > timedelta(hours=c.MAX_AGE_HOURS):
        d.reason = "waiting more than %d hours" % c.MAX_AGE_HOURS
        return d
    if now < first.at + timedelta(minutes=c.WAIT_MINUTES) or \
            now < last.at + timedelta(seconds=c.QUIET_SECONDS):
        return Decision("wait", trigger=first, kind=kind, stretch=stretch)
    recent = [t for t in ours.sent_at if now - t < timedelta(hours=c.PER_CUSTOMER_HOURS)]
    if recent:
        d.reason = "the auto-reply already texted this number in the last %d hours" % (
            c.PER_CUSTOMER_HOURS)
        return d
    d.action = "reply"
    return d

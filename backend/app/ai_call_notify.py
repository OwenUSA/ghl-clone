"""After an AI voice-agent call, text the office a notification (2026-10-06).

The owner's request: every call an AI agent answered ends with a text to the company's own
staff line saying who called and what the agent learned. It is the ONE exception to "no
automatic texts — only texts a person sends" (2026-09-15), and it is an exception only because
it is INTERNAL: it texts the office, never a customer. See DECISIONS.md, 2026-10-06 (AI call
notifications).

    POST /api/events (a CALL carrying `ai_call`)
        └─ maybe_enqueue()  ─ ONE `ai_call_notify` job, ~120 s later, dedupe key per event
              └─ worker: handle_job() reads the event AS IT IS THEN (the summary and the
                 recording arrive in later reports, merged onto the same row) and sends one
                 text per configured recipient through `automations.send_outbound_to_number`
                 — the same `get_transport()` a staff text uses, recorded on that number's
                 thread like any staff text.

## The guards, every one of which has a test (tests/test_ai_call_notify.py)

  * Recipients come ONLY from `AI_CALL_NOTIFY_NUMBERS` (comma-separated E.164). Empty or unset
    = the feature is off: nothing is enqueued, and a job already queued sends nothing.
  * A recipient that any CONTACT holds (last ten digits, `number_threads.contact_holding`) is
    refused with a logged sentence. This can never text a customer, even if somebody puts a
    customer's number in the setting by mistake.
  * The caller's own number is never a recipient; neither is our own sending line.
  * Only CALL events with a non-empty `ai_call`, and only fresh ones (`automations.is_fresh`):
    a backfilled call from last month is history, not news.
  * One text per call per recipient: the sent row carries a dedupe key derived from the call,
    and the handler checks it before sending — a second run of the job sends nothing. The job
    is never retried (`max_attempts = 1`): a retry after a send that had already happened is
    exactly how the office would get the text twice.
  * At most `MAX_CHARS` characters. The summary is shortened first; the lead's own fields are
    never cut (each value is capped on its own, and unknown extra keys are dropped before a
    known field ever is). No pictures, ever.

## New lead or existing customer

Since 2026-09-13 the ingest never creates a contact, so a caller is a NEW LEAD when the call
landed on a number-only thread (nobody holds the number). If a person saves the caller as a
contact in the ~120 seconds before the job runs, the call moves onto the contact's thread
(`number_threads.adopt_on_flush`); it is still a new lead when the job recorded the number
thread at enqueue time, or the contact was created at or after the call began. Anything else
on a contact's thread is an EXISTING CUSTOMER.

## The text (2026-10-08 format)

Blocks separated by a blank line, no emoji. The header names the CALLER's number as
`+1 XXX-XXX-XXXX`:

    AI call from <first last> (existing customer) (+1 941-555-0123)
    Summary: <the full summary, trimmed only by the length cap>
    Asked: <kind> — <request>          (one per change request; block left out when none)
    Recording: <link>

    New lead from AI call (+1 813-555-0142)
    Name: ... / Address: ... / every captured field, one per line
    Summary: <summary>
    Recording: <link>

## The link

`<link>` is ALWAYS the conversation deep link `<CRM_PUBLIC_URL>/conversations?thread=<key>` for
the thread the call is on (`c<conversation id>` or `n<number thread id>`, the inbox's own row
key; `lib/zuper.ts recordFromLocation` opens it after sign-in, with the call's player). The
recording relay's own URL is never texted: on a phone that is not signed in it answers 401 JSON.
`Recording: <link>` once the event carries the CRM relay's `recording_url`
(`owen_recordings.RECORDINGS_PATH` + "/<call id>"); before that, "Recording not available yet.
The call is on:" and the link on its own line. Any other `recording_url` (a carrier's,
owen-main's, Quo's) counts as no recording. `CRM_PUBLIC_URL` has no default: unset = the old
link-less wording ("(recording in the CRM)" / "(recording not available yet)") and a log line.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import automations, number_threads
from .models import (
    Contact,
    Conversation,
    ConversationEvent,
    EventType,
    NumberThread,
    NumberThreadEvent,
)
from .owen_recordings import RECORDINGS_PATH
from .phones import format_phone
from .queue import enqueue, utcnow

log = logging.getLogger("ai_call_notify")

JOB_TYPE = "ai_call_notify"
NUMBERS_ENV = "AI_CALL_NOTIFY_NUMBERS"
PUBLIC_URL_ENV = "CRM_PUBLIC_URL"
# Long enough for owen-main's later reports (Retell's analysis, the recording) to land.
DELAY_SECONDS = 120
MAX_CHARS = 1200
# One captured value is never allowed to swallow the text on its own.
VALUE_CAP = 200

NO_RECORDING = "(recording not available yet)"
RECORDING_IN_CRM = "(recording in the CRM)"

# The captured keys a person reads, in the order they read them, with the aliases an agent's
# capture may use for the same thing. The FIRST key present in an alias group wins; the others
# in the group are consumed so they are not repeated as "extra" keys.
LEAD_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Name", ("name", "full_name", "caller_name")),
    ("Second phone", ("second_phone", "phone", "callback_number", "callback_phone",
                      "alt_phone", "other_phone")),
    ("Email", ("email",)),
    ("Address", ("address", "street_address", "property_address")),
    ("City", ("city",)),
    ("What's happening", ("need", "intent", "issue", "problem", "reason")),
    ("Leaks", ("leak_count", "leaks", "number_of_leaks")),
    ("Leak location", ("leak_location", "leak_locations", "where_leaking")),
    ("Started", ("started", "when_started", "start_date", "when_it_started")),
    ("Roof material", ("roof_material", "roof_type", "material")),
    ("Roof age", ("roof_age", "age_of_roof")),
    ("Previous repairs", ("previous_repairs", "prior_repairs")),
    ("Stories", ("stories", "floors", "number_of_stories")),
    ("Owner or tenant", ("occupancy", "owner_or_tenant", "ownership", "owner_tenant", "is_owner")),
    ("Payment", ("payment_type", "payment", "payer")),
    ("AHS dispatch #", ("ahs_dispatch_number", "ahs_dispatch", "dispatch_number")),
    ("First time with AHS", ("first_time_ahs", "ahs_first_time")),
    ("Preferred inspection time", ("preferred_inspection_time", "preferred_time",
                                   "inspection_time", "availability")),
    ("Access notes", ("access_notes", "access", "gate_code")),
    ("Urgency", ("urgency",)),
    ("Notes", ("notes", "note")),
)
# Never shown as an "extra" line: split names are folded into Name.
_NAME_PARTS = ("first_name", "last_name")


# ------------------------------------------------------------------------------- settings

def recipients() -> list[str]:
    """The configured recipients, as written, empty entries dropped. Read at call time."""
    raw = os.getenv(NUMBERS_ENV, "") or ""
    return [p.strip() for p in raw.split(",") if p.strip()]


def public_base() -> str:
    return (os.getenv(PUBLIC_URL_ENV, "") or "").strip().rstrip("/")


# ------------------------------------------------------------------------------- enqueue

def is_ai_call(ev) -> bool:
    return (ev is not None and ev.type is EventType.CALL
            and isinstance(ev.ai_call, dict) and bool(ev.ai_call))


def _event_key(ev) -> str:
    """Stable across adoption (which copies `dedupe_key` but gives the row a new id)."""
    if ev.dedupe_key:
        return ev.dedupe_key
    kind = "n" if isinstance(ev, NumberThreadEvent) else "c"
    return "%s%s" % (kind, ev.id)


def _short(prefix: str, key: str) -> str:
    out = prefix + key
    if len(out) <= 200:
        return out
    return prefix + hashlib.sha256(key.encode()).hexdigest()


def maybe_enqueue(db: Session, ev, caller_number: str | None = None) -> str:
    """Queue the one notification for this AI call. Returns a sentence (for logs/tests).

    Safe to call on every ingest of the call: the job's dedupe key is per call, so the second
    and later reports (the merges) find it and queue nothing."""
    if not is_ai_call(ev):
        return "not an AI call"
    if not recipients():
        return "off: %s is not set" % NUMBERS_ENV
    if not automations.is_fresh(ev.occurred_at):
        return "skipped: an old call, not news"
    key = _event_key(ev)
    payload = {"event_id": ev.id,
               "event_kind": "number" if isinstance(ev, NumberThreadEvent) else "contact",
               "event_key": key,
               "caller_number": caller_number or None}
    job = enqueue(db, JOB_TYPE, payload,
                  run_after=utcnow() + timedelta(seconds=DELAY_SECONDS),
                  dedupe_key=_short("ai_call_notify:", key))
    if job is None:
        return "already queued"
    # Never retried: a retry after a send that already happened is a second text.
    job.max_attempts = 1
    return "queued"


# ------------------------------------------------------------------------------- formatting

def _value(v) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, list | tuple):
        v = ", ".join(_value(x) for x in v if x not in (None, ""))
    elif isinstance(v, dict):
        v = ", ".join("%s %s" % (k, _value(x)) for k, x in v.items() if x not in (None, ""))
    text = re.sub(r"\s+", " ", str(v)).strip()
    if len(text) > VALUE_CAP:
        text = text[:VALUE_CAP - 1].rstrip() + "…"
    return text


def _human(key: str) -> str:
    return key.replace("_", " ").strip().capitalize() or key


def lead_lines(captured: dict | None, caller: str | None) -> tuple[list[str], list[str]]:
    """`(known lines, extra lines)` for a capture. Keys with no value are left out."""
    captured = captured if isinstance(captured, dict) else {}
    present = {k: v for k, v in captured.items() if v not in (None, "", [], {})}
    used: set[str] = set()
    known: list[str] = []

    name = None
    for alias in LEAD_FIELDS[0][1]:
        if alias in present:
            name = _value(present[alias])
            break
    if name is None:
        parts = [_value(present[k]) for k in _NAME_PARTS if k in present]
        name = " ".join(p for p in parts if p) or None
    used.update(LEAD_FIELDS[0][1])
    used.update(_NAME_PARTS)
    if name:
        known.append("Name: " + name)

    for label, aliases in LEAD_FIELDS[1:]:
        for alias in aliases:
            if alias in present:
                value = present[alias]
                text = _value(value)
                if label == "Second phone":
                    text = format_phone(str(value)) or text
                    # The caller's own number captured again is not a second phone.
                    if caller and number_threads.phone_key(str(value)) == \
                            number_threads.phone_key(caller):
                        text = ""
                if text:
                    known.append("%s: %s" % (label, text))
                break
        used.update(aliases)

    extra = ["%s: %s" % (_human(k), _value(v)) for k, v in present.items()
             if k not in used and _value(v)]
    return known, extra


def _sentences(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    parts = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(parts[:n]).strip()


def caller_phone(number: str | None) -> str:
    """The caller's number as the header shows it: `+1 813-555-0142`. A number that is not a
    US one is shown as given."""
    raw = (number or "").strip()
    d = re.sub(r"\D", "", raw)
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    if len(d) == 10 and (raw.startswith("+1") or not raw.startswith("+")):
        return "+1 %s-%s-%s" % (d[0:3], d[3:6], d[6:10])
    return raw


def _header(text: str, caller: str | None) -> str:
    phone = caller_phone(caller)
    return "%s (%s)" % (text, phone) if phone else text


def _fit_summary(room: int, summary: str) -> str:
    """The summary line, cut to `room` characters. Empty when there is none or no room."""
    if not summary:
        return ""
    if room <= len("Summary: …"):
        return ""
    line = "Summary: " + summary
    if len(line) <= room:
        return line
    return line[:room - 1].rstrip() + "…"


def _join(blocks: list[str]) -> str:
    return "\n\n".join(b for b in blocks if b)


def _assemble(before: list[str], summary: str, after: list[str]) -> str:
    """`before` + the summary block + `after`, the summary cut first to fit MAX_CHARS."""
    fixed = len(_join(before + after))
    s = _fit_summary(MAX_CHARS - fixed - 2, summary)
    return _join([*before, s, *after])


def new_lead_text(ai_call: dict, caller: str | None, link: str) -> str:
    known, extra = lead_lines(ai_call.get("captured"), caller)
    summary = re.sub(r"\s+", " ", str(ai_call.get("summary") or "")).strip()
    header = _header("New lead from AI call", caller)
    # Extra keys go before a known field ever would; known lines are never cut.
    while extra and len(_join([header, "\n".join(known + extra), link])) > MAX_CHARS:
        extra.pop()
    return _assemble([header, "\n".join(known + extra)], summary, [link])


def _request_lines(ai_call: dict) -> list[str]:
    out = []
    for r in ai_call.get("requests") or []:
        if isinstance(r, dict):
            kind = _value(r.get("kind") or "change")
            what = _value(r.get("request") or r.get("text") or r.get("detail") or "")
            out.append("Asked: %s%s" % (kind, (" — " + what) if what else ""))
        elif r not in (None, ""):
            out.append("Asked: " + _value(r))
    return out


def existing_text(ai_call: dict, contact: Contact, caller: str | None, link: str) -> str:
    name = contact.name or format_phone(contact.phone) or "a customer"
    header = _header("AI call from %s (existing customer)" % name, caller)
    summary = " ".join(str(ai_call.get("summary") or "").split())  # full; the cap trims it
    return _assemble([header], summary, ["\n".join(_request_lines(ai_call)), link])


def thread_key(ev) -> str:
    """The inbox row key of the thread the call is on: `c<conversation id>` / `n<thread id>`."""
    if isinstance(ev, NumberThreadEvent):
        return "n%d" % ev.number_thread_id
    return "c%d" % ev.conversation_id


def thread_link(ev) -> str | None:
    """`<CRM_PUBLIC_URL>/conversations?thread=<key>`, or None while the setting is unset."""
    base = public_base()
    return "%s/conversations?thread=%s" % (base, thread_key(ev)) if base else None


def link_line(ev) -> str:
    """The recording block: "Recording: <deep link>", "Recording not available yet. The call
    is on:" + the link on its own line, or the link-less wording when CRM_PUBLIC_URL is unset."""
    rec = (ev.recording_url or "").strip()
    has_recording = rec.startswith(RECORDINGS_PATH + "/")
    link = thread_link(ev)
    if link is None:
        log.warning("ai_call_notify: %s is not set, so the text carries no link",
                    PUBLIC_URL_ENV)
        return "Full details are in the call recording: " + (
            RECORDING_IN_CRM if has_recording else NO_RECORDING)
    if has_recording:
        return "Recording: " + link
    return "Recording not available yet. The call is on:\n" + link


# ------------------------------------------------------------------------------- the job

def _find_event(db: Session, payload: dict):
    model = NumberThreadEvent if payload.get("event_kind") == "number" else ConversationEvent
    ev = db.get(model, payload.get("event_id")) if payload.get("event_id") else None
    key = payload.get("event_key") or ""
    if ev is not None and _event_key(ev) == key:
        return ev
    # Adopted onto a contact's thread since (a new row, the same dedupe key), or the reverse.
    if key and not re.fullmatch(r"[nc]\d+", key):
        for m in (ConversationEvent, NumberThreadEvent):
            found = db.scalar(select(m).where(m.dedupe_key == key))
            if found is not None:
                return found
    return ev


def _caller_and_contact(db: Session, ev) -> tuple[str | None, Contact | None]:
    if isinstance(ev, NumberThreadEvent):
        thread = db.get(NumberThread, ev.number_thread_id)
        return (thread.phone if thread else None), None
    conv = db.get(Conversation, ev.conversation_id)
    contact = db.get(Contact, conv.contact_id) if conv else None
    return (contact.phone if contact else None), contact


def _is_new_lead(ev, contact: Contact | None, payload: dict) -> bool:
    if contact is None:
        return True
    if payload.get("event_kind") == "number":
        return True       # filed on a number thread, then saved as a contact before we ran
    made, began = automations.as_utc(contact.created_at), automations.as_utc(ev.occurred_at)
    return made is not None and began is not None and made >= began


def build_text(db: Session, ev, payload: dict) -> tuple[str, str | None]:
    """`(text, caller number)` for this call as it stands now."""
    on_thread, contact = _caller_and_contact(db, ev)
    # The number that actually called (the ingest's from_number) wins over the thread's.
    caller = payload.get("caller_number") or on_thread
    ai_call = ev.ai_call or {}
    line = link_line(ev)
    if _is_new_lead(ev, contact, payload):
        return new_lead_text(ai_call, caller, line), caller
    return existing_text(ai_call, contact, caller, line), caller


def refusal(db: Session, recipient: str, caller: str | None) -> str | None:
    """Why this recipient must not get the text, or None. The customer guard lives here."""
    key = number_threads.phone_key(recipient)
    if not key:
        return "%r is not a complete phone number" % recipient
    if caller and key == number_threads.phone_key(caller):
        return "it is the caller's own number"
    if number_threads.contact_holding(db, recipient) is not None:
        return "a contact holds that number — this notification never texts a customer"
    from .crmlink import current
    if key == number_threads.phone_key(current().from_number):
        return "it is our own sending line"
    return None


def handle_job(db: Session, payload: dict) -> None:
    """Send the notification once. Never raises: a failure is logged, never retried."""
    try:
        _run(db, payload)
    except Exception:
        db.rollback()
        log.exception("ai_call_notify failed for %r", payload.get("event_key"))


def _run(db: Session, payload: dict) -> None:
    targets = recipients()
    if not targets:
        log.info("ai_call_notify: %s is empty — the notification is off; nothing sent",
                 NUMBERS_ENV)
        return
    ev = _find_event(db, payload)
    if not is_ai_call(ev):
        log.info("ai_call_notify: the call is gone or is not an AI call; nothing sent")
        return
    text, caller = build_text(db, ev, payload)
    for recipient in targets:
        why = refusal(db, recipient, caller)
        if why:
            log.warning("ai_call_notify: refused to text %s: %s", recipient, why)
            continue
        marker = _short("ai_call_notify:%s:" % number_threads.phone_key(recipient),
                        _event_key(ev))
        already = db.scalar(select(NumberThreadEvent.id)
                            .where(NumberThreadEvent.dedupe_key == marker))
        if already is not None:
            log.info("ai_call_notify: already sent to %s for this call", recipient)
            continue
        thread, _ = number_threads.thread_for_number(db, recipient)
        sent, reason = automations.send_outbound_to_number(db, thread, text)
        if sent is not None:
            sent.dedupe_key = marker
        db.commit()
        log.info("ai_call_notify: %s to %s", reason, recipient)

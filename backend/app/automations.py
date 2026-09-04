"""The four hard-coded automations (DECISIONS.md).

No visual workflow builder in v1 — these four rules are what the business actually
runs, and a canvas to configure four rules is 30-40% of the build for no gain.

  1. missed call            -> auto-text the caller back
  2. new lead created       -> notify the team internally
  3. appointment booked     -> remind the customer at T-24h and T-1h
  4. opportunity stage move -> text the customer the status update

Every outbound message goes through MessageTransport, which in v1 is
LoggingTransport: the rule fires, the intent is recorded, nothing is transmitted.

Two suppression rules apply to ALL customer-facing sends:
  * contact.dnd            - explicit Do Not Disturb
  * no phone number        - nothing to send to
Internal team notifications are exempt from DND; DND is a promise to the customer,
not a mute button on our own staff.
"""
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from .models import (
    Appointment,
    Contact,
    Conversation,
    ConversationEvent,
    DeliveryStatus,
    Direction,
    EventType,
    Opportunity,
    Stage,
)
from .queue import enqueue
from .transport import get_transport

log = logging.getLogger("automations")

MISSED_CALL_TEXT = (
    "Sorry we missed your call! This is Dream Team Roofing — "
    "reply here and we'll get right back to you."
)
REMINDER_TEXT = "Reminder: your appointment with Dream Team Roofing is {when}."
STAGE_TEXT = "Update on your job: it's now at the '{stage}' stage."

# A call shorter than this with no answer is treated as missed.
MISSED_CALL_MAX_SECONDS = 15


def _utcnow():
    return datetime.now(UTC)


def _can_message(contact: Contact | None) -> tuple[bool, str]:
    if contact is None:
        return False, "no contact"
    if not contact.phone:
        return False, "contact has no phone number"
    if contact.dnd:
        return False, "contact is on DND"
    return True, ""


def _thread_for(db: Session, contact_id: int) -> Conversation:
    conv = db.query(Conversation).filter(
        Conversation.contact_id == contact_id).one_or_none()
    if conv is None:
        conv = Conversation(contact_id=contact_id, last_event_at=_utcnow())
        db.add(conv)
        db.flush()
    return conv


def _record_outbound(db: Session, contact: Contact, body: str,
                     type_: EventType = EventType.SMS) -> ConversationEvent:
    """Send via the transport seam and record the result on the thread."""
    ref = get_transport().send_sms(to=contact.phone or "", body=body,
                                   from_number="")
    conv = _thread_for(db, contact.id)
    ev = ConversationEvent(
        conversation_id=conv.id, type=type_, direction=Direction.OUTBOUND,
        occurred_at=_utcnow(), body=body,
        delivery_status=(DeliveryStatus.SENT if ref.delivered
                         else DeliveryStatus.LOGGED_ONLY),
        provider_ref=ref.provider_ref)
    db.add(ev)
    conv.last_event_at = ev.occurred_at
    db.flush()
    return ev


# Internal thread entries: recorded for the team, never transmitted, and never
# suppressed. DND is a promise to the customer, not a mute on our own notes.
INTERNAL_TYPES = {EventType.NOTE, EventType.INTERNAL_COMMENT}


def send_outbound(db: Session, contact: Contact, body: str, *,
                  type_: EventType = EventType.SMS,
                  subject: str | None = None
                  ) -> tuple[ConversationEvent | None, str]:
    """One send path shared by the API and the four rules.

    Returns `(event, reason)`. A suppressed customer-facing send returns
    `(None, "suppressed: ...")` and writes NOTHING — the suppression has to be
    real, not just reported, or a contact on DND still accumulates outbound rows.

    The four automation rules deliberately keep calling `_record_outbound`
    directly; routing them through here would change 15 passing tests for no gain.
    """
    if type_ in INTERNAL_TYPES:
        conv = _thread_for(db, contact.id)
        ev = ConversationEvent(
            conversation_id=conv.id, type=type_, direction=Direction.OUTBOUND,
            occurred_at=_utcnow(), body=body, subject=subject)
        db.add(ev)
        conv.last_event_at = ev.occurred_at
        db.flush()
        return ev, "recorded"

    ok, why = _can_message(contact)
    if not ok:
        return None, "suppressed: " + why

    if type_ is EventType.EMAIL:
        if not contact.email:
            return None, "suppressed: contact has no email address"
        ref = get_transport().send_email(to=contact.email,
                                         subject=subject or "", html=body)
    else:
        ref = get_transport().send_sms(to=contact.phone or "", body=body,
                                       from_number="")

    conv = _thread_for(db, contact.id)
    ev = ConversationEvent(
        conversation_id=conv.id, type=type_, direction=Direction.OUTBOUND,
        occurred_at=_utcnow(), body=body, subject=subject,
        delivery_status=(DeliveryStatus.SENT if ref.delivered
                         else DeliveryStatus.LOGGED_ONLY),
        provider_ref=ref.provider_ref)
    db.add(ev)
    conv.last_event_at = ev.occurred_at
    db.flush()
    return ev, "sent"


# ---------- triggers (called from the API) ----------

def on_inbound_call(db: Session, event: ConversationEvent) -> str:
    """Rule 1 — missed call -> auto text back."""
    if event.type != EventType.CALL or event.direction != Direction.INBOUND:
        return "not an inbound call"
    if (event.duration_seconds or 0) > MISSED_CALL_MAX_SECONDS:
        return "call was answered"

    conv = db.get(Conversation, event.conversation_id)
    contact = db.get(Contact, conv.contact_id) if conv else None
    ok, why = _can_message(contact)
    if not ok:
        return "suppressed: " + why

    job = enqueue(db, "missed_call_textback", {"contact_id": contact.id},
                  dedupe_key="missed_call:%s" % event.id)
    return "queued" if job else "already queued"


def on_contact_created(db: Session, contact: Contact) -> str:
    """Rule 2 — new lead -> notify the team (internal, DND does not apply)."""
    job = enqueue(db, "new_lead_notify", {"contact_id": contact.id},
                  dedupe_key="new_lead:%s" % contact.id)
    return "queued" if job else "already queued"


def on_appointment_booked(db: Session, appt: Appointment) -> str:
    """Rule 3 — appointment booked -> reminders at T-24h and T-1h.

    Reminders already in the past are not scheduled; back-dating a send would
    fire immediately, which is worse than not sending.
    """
    contact = db.get(Contact, appt.contact_id) if appt.contact_id else None
    ok, why = _can_message(contact)
    if not ok:
        return "suppressed: " + why

    now = _utcnow()
    starts = appt.starts_at
    if starts.tzinfo is None:
        starts = starts.replace(tzinfo=UTC)

    queued = []
    for label, delta in (("24h", timedelta(hours=24)), ("1h", timedelta(hours=1))):
        when = starts - delta
        if when <= now:
            continue
        job = enqueue(db, "appointment_reminder",
                      {"appointment_id": appt.id, "offset": label},
                      run_after=when,
                      # The start time is part of the key. With just
                      # (id, offset), rescheduling an appointment hit the existing
                      # key, enqueue() returned None, and the customer got NO
                      # reminder at the new time — silently. See
                      # test_rescheduling_an_appointment_schedules_new_reminders.
                      dedupe_key="appt_reminder:%s:%s:%s" % (
                          appt.id, starts.isoformat(), label))
        if job:
            queued.append(label)
    return "queued " + ",".join(queued) if queued else "nothing to schedule"


def on_opportunity_stage_changed(db: Session, opp: Opportunity,
                                 old_stage_id: int) -> str:
    """Rule 4 — stage move -> text the customer."""
    if opp.stage_id == old_stage_id:
        return "stage unchanged"
    contact = db.get(Contact, opp.contact_id) if opp.contact_id else None
    ok, why = _can_message(contact)
    if not ok:
        return "suppressed: " + why

    job = enqueue(db, "stage_change_notify",
                  {"opportunity_id": opp.id, "stage_id": opp.stage_id},
                  dedupe_key="stage_change:%s:%s" % (opp.id, opp.stage_id))
    return "queued" if job else "already queued"


# ---------- handlers (run by the worker) ----------

def _h_missed_call(db: Session, payload: dict) -> None:
    contact = db.get(Contact, payload["contact_id"])
    ok, why = _can_message(contact)
    if not ok:
        log.info("missed_call_textback skipped: %s", why)
        return
    _record_outbound(db, contact, MISSED_CALL_TEXT)


def _h_new_lead(db: Session, payload: dict) -> None:
    contact = db.get(Contact, payload["contact_id"])
    if not contact:
        return
    # Internal notification: no customer-facing send, so DND does not apply.
    log.info("NEW LEAD: %s %s (%s)", contact.name, contact.phone or "",
             contact.source or "unknown source")


def _h_appointment_reminder(db: Session, payload: dict) -> None:
    appt = db.get(Appointment, payload["appointment_id"])
    if not appt or appt.status in ("cancelled", "blocked"):
        log.info("reminder skipped: appointment missing or cancelled")
        return
    contact = db.get(Contact, appt.contact_id) if appt.contact_id else None
    ok, why = _can_message(contact)
    if not ok:
        log.info("reminder skipped: %s", why)
        return
    when = appt.starts_at.strftime("%b %d at %I:%M %p").replace(" 0", " ")
    _record_outbound(db, contact, REMINDER_TEXT.format(when=when))


def _h_stage_change(db: Session, payload: dict) -> None:
    opp = db.get(Opportunity, payload["opportunity_id"])
    if not opp:
        return
    contact = db.get(Contact, opp.contact_id) if opp.contact_id else None
    ok, why = _can_message(contact)
    if not ok:
        log.info("stage_change_notify skipped: %s", why)
        return
    stage = db.get(Stage, payload["stage_id"])
    _record_outbound(db, contact,
                     STAGE_TEXT.format(stage=stage.name if stage else "updated"))


HANDLERS = {
    "missed_call_textback": _h_missed_call,
    "new_lead_notify": _h_new_lead,
    "appointment_reminder": _h_appointment_reminder,
    "stage_change_notify": _h_stage_change,
}

"""Automation rules.

These decide whether a real customer gets a text, so the suppression paths matter
as much as the happy paths.
"""
from datetime import UTC, datetime, timedelta

from app import automations
from app.models import (
    Appointment,
    Conversation,
    ConversationEvent,
    DeliveryStatus,
    Direction,
    EventType,
    Job,
)
from app.worker import drain_once
from sqlalchemy import select


def utcnow():
    return datetime.now(UTC)


def _call(db, contact, seconds):
    conv = Conversation(contact_id=contact.id)
    db.add(conv)
    db.flush()
    ev = ConversationEvent(conversation_id=conv.id, type=EventType.CALL,
                           direction=Direction.INBOUND, occurred_at=utcnow(),
                           duration_seconds=seconds)
    db.add(ev)
    db.flush()
    return ev


def jobs(db, type_=None):
    stmt = select(Job)
    if type_:
        stmt = stmt.where(Job.type == type_)
    return list(db.scalars(stmt).all())


# ---------- rule 1: missed call ----------

def test_missed_call_queues_textback(db, contact):
    ev = _call(db, contact, seconds=4)
    assert automations.on_inbound_call(db, ev) == "queued"
    assert len(jobs(db, "missed_call_textback")) == 1


def test_answered_call_does_not_queue(db, contact):
    ev = _call(db, contact, seconds=95)
    assert automations.on_inbound_call(db, ev) == "call was answered"
    assert jobs(db) == []


def test_missed_call_is_deduped(db, contact):
    ev = _call(db, contact, seconds=3)
    automations.on_inbound_call(db, ev)
    assert automations.on_inbound_call(db, ev) == "already queued"
    assert len(jobs(db, "missed_call_textback")) == 1


def test_dnd_contact_is_never_texted(db, contact):
    contact.dnd = True
    db.flush()
    ev = _call(db, contact, seconds=3)
    assert "DND" in automations.on_inbound_call(db, ev)
    assert jobs(db) == []


def test_contact_without_phone_is_skipped(db, contact):
    contact.phone = None
    db.flush()
    ev = _call(db, contact, seconds=3)
    assert "no phone" in automations.on_inbound_call(db, ev)
    assert jobs(db) == []


# ---------- rule 2: new lead ----------

def test_new_lead_notifies_team(db, contact):
    assert automations.on_contact_created(db, contact) == "queued"
    assert len(jobs(db, "new_lead_notify")) == 1


def test_new_lead_notify_ignores_dnd(db, contact):
    """DND is a promise to the customer, not a mute on our own staff."""
    contact.dnd = True
    db.flush()
    assert automations.on_contact_created(db, contact) == "queued"


# ---------- rule 3: appointment reminders ----------

def test_booking_schedules_both_reminders(db, contact):
    a = Appointment(title="Roof Inspection", contact_id=contact.id,
                    starts_at=utcnow() + timedelta(days=3),
                    ends_at=utcnow() + timedelta(days=3, hours=1))
    db.add(a)
    db.flush()
    assert automations.on_appointment_booked(db, a) == "queued 24h,1h"
    assert len(jobs(db, "appointment_reminder")) == 2


def test_reminder_in_the_past_is_not_scheduled(db, contact):
    """An appointment in 2 hours can't get a T-24h reminder — that would fire
    immediately, which is worse than not sending."""
    a = Appointment(title="Tarp", contact_id=contact.id,
                    starts_at=utcnow() + timedelta(hours=2),
                    ends_at=utcnow() + timedelta(hours=3))
    db.add(a)
    db.flush()
    assert automations.on_appointment_booked(db, a) == "queued 1h"
    assert len(jobs(db, "appointment_reminder")) == 1


def test_rebooking_does_not_duplicate_reminders(db, contact):
    a = Appointment(title="Repair", contact_id=contact.id,
                    starts_at=utcnow() + timedelta(days=2),
                    ends_at=utcnow() + timedelta(days=2, hours=1))
    db.add(a)
    db.flush()
    automations.on_appointment_booked(db, a)
    automations.on_appointment_booked(db, a)
    assert len(jobs(db, "appointment_reminder")) == 2


# ---------- rule 4: stage change ----------

def test_stage_change_queues_customer_text(db, opportunity, pipeline):
    _, _, b = pipeline
    old = opportunity.stage_id
    opportunity.stage_id = b.id
    db.flush()
    assert automations.on_opportunity_stage_changed(db, opportunity, old) == "queued"
    assert len(jobs(db, "stage_change_notify")) == 1


def test_same_stage_does_not_notify(db, opportunity):
    outcome = automations.on_opportunity_stage_changed(
        db, opportunity, opportunity.stage_id)
    assert outcome == "stage unchanged"
    assert jobs(db) == []


# ---------- worker ----------

def test_worker_records_outbound_as_logged_only(db, contact):
    """The whole point of the stub: the message is recorded, never transmitted."""
    ev = _call(db, contact, seconds=5)
    automations.on_inbound_call(db, ev)
    db.commit()

    assert drain_once() == 1

    db.expire_all()
    sent = db.scalars(
        select(ConversationEvent).where(
            ConversationEvent.direction == Direction.OUTBOUND)).all()
    assert len(sent) == 1
    assert sent[0].delivery_status == DeliveryStatus.LOGGED_ONLY
    assert "missed your call" in sent[0].body

    done = db.scalars(select(Job)).all()
    assert [j.status for j in done] == ["done"]


def test_unknown_job_type_fails_fast(db, contact):
    """An unknown job type cannot succeed on a retry, so it must fail immediately
    rather than burn 5 attempts and delay anyone noticing."""
    from app.queue import enqueue
    enqueue(db, "does_not_exist", {})
    db.commit()

    assert drain_once() == 1
    db.expire_all()
    job = db.scalars(select(Job)).one()
    assert job.status == "failed"
    assert "no handler" in job.last_error


def test_cancelled_appointment_reminder_is_skipped(db, contact):
    a = Appointment(title="Cancelled job", contact_id=contact.id,
                    status="cancelled",
                    starts_at=utcnow() + timedelta(hours=2),
                    ends_at=utcnow() + timedelta(hours=3))
    db.add(a)
    db.flush()
    from app.queue import enqueue
    enqueue(db, "appointment_reminder", {"appointment_id": a.id, "offset": "1h"})
    db.commit()

    drain_once()
    db.expire_all()
    sent = db.scalars(
        select(ConversationEvent).where(
            ConversationEvent.direction == Direction.OUTBOUND)).all()
    assert sent == []

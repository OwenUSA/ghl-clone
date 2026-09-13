"""Number-only threads: correspondence with a phone number that no contact holds.

The owner, in his words (2026-09-13):

    "if its a number that is not registered as a contact, it should not be saved as
     contact but the numbers with the conversation must display anyways"

So an inbound call or text from an unknown number — on the BulkVS line or the Quo
line — creates NO Contact and NO Opportunity, notifies nobody and texts nobody. It
lands on a `NumberThread`, which the inbox shows beside the contact threads, unread,
marked as not a contact. He is protecting himself from spam: a contact is a decision
a person makes, never a side effect of the phone ringing.

## Adoption — the one rule that keeps the two kinds of thread from drifting apart

The moment a contact comes to exist with that number, BY ANY PATH, the number's
whole history moves onto the contact's thread and the number thread is deleted:

  * "Add as contact" in the thread header (a plain `POST /api/contacts`);
  * any other `POST /api/contacts`, e.g. the picker's "+ New";
  * editing an existing contact's phone to that number (`PATCH /api/contacts/{id}`,
    including the opportunity modal's Primary phone, which goes through it);
  * the Workiz import creating or updating a contact.

It is enforced ONCE, in a `before_flush` listener on every Session (`app/db.py`),
rather than in each of those call sites. A fifth path added next month is covered
without anybody remembering this module exists — the same "protected by default"
reasoning as the auth gate. `test_number_threads.py` drives each path above.

What adoption guarantees, and how:

  * **No event lost.** Every row is copied into `conversation_events` with every
    payload column (`models.EVENT_PAYLOAD_COLUMNS`) — the same instant, direction,
    delivery state, source chip, recording and transcript.
  * **None duplicated.** An event whose `dedupe_key` the contact's thread already
    holds is not copied a second time; the key stays unique across both tables.
  * **One transaction.** The copy, the delete and the contact write commit together
    or not at all — the listener runs inside the flush that writes the contact.
  * **Nothing is sent and nothing is queued.** This module imports neither
    `automations` nor `queue`, for the same reason `workiz_import` does not: the
    import counts the jobs table around its transaction and rolls back on one new
    row, and adoption runs inside that transaction.

## Quo's name

When Quo's own contact book knows the number, owen-main sends the name with the
event (`source_contact_name`). It is stored on the thread and shown labelled "from
Quo". It creates nothing here, and on adoption it is only a PREFILL for the form —
the person saving the contact decides what the contact is called.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import phone_match
from .models import (
    EVENT_PAYLOAD_COLUMNS,
    Contact,
    Conversation,
    ConversationEvent,
    NumberThread,
    NumberThreadEvent,
)
from .phones import store_phone

# The last ten digits: the identity rule the picker, the CRM link, owen-main and the
# Workiz import already share (DECISIONS.md, 2026-09-10).
KEY_DIGITS = 10


def phone_key(number: str | None) -> str:
    """The thread's identity for a number, or "" when it is not a usable number.

    Fewer than ten digits is not a line anybody can be called back on, and keying on
    a fragment would let "5550100" collide with every number ending in it. Such an
    event is refused at ingest rather than filed under a guess.
    """
    digits = phone_match.digits(number)
    return digits[-KEY_DIGITS:] if len(digits) >= KEY_DIGITS else ""


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def thread_for_number(db: Session, number: str,
                      at: datetime | None = None) -> tuple[NumberThread, bool]:
    """`(thread, created)` for the number. Caller has checked `phone_key`.

    `at` is when the first event on a NEW thread happened: a thread first seen
    through a backfilled three-week-old text must sort three weeks down the inbox,
    not at the top dated today.
    """
    key = phone_key(number)
    thread = db.scalar(select(NumberThread).where(NumberThread.phone_key == key))
    if thread is not None:
        return thread, False
    thread = NumberThread(phone=store_phone(number) or number, phone_key=key,
                          unread_count=0, starred=False)
    if at is not None:
        thread.last_event_at = at
    db.add(thread)
    db.flush()
    return thread, True


def contact_holding(db: Session, number: str | None) -> Contact | None:
    """The contact whose phone is this line, by the shared last-ten-digits rule.

    Ordered by id, so two contacts sharing a number (allowed — a couple, a property
    manager) always resolve to the same one.
    """
    if not phone_key(number):
        return None
    return db.scalars(
        select(Contact)
        .where(phone_match.phone_clause(Contact.phone, number))
        .order_by(Contact.id)
    ).first()


def _copy_payload(src, dst) -> None:
    for col in EVENT_PAYLOAD_COLUMNS:
        setattr(dst, col, getattr(src, col))


def _move_into(session: Session, thread: NumberThread, contact: Contact) -> dict:
    """Move `thread`'s events onto `contact`'s conversation and delete `thread`.

    Works for a contact that has no id yet (created in the flush being processed):
    the conversation is attached through the relationship, and the unit of work
    inserts the contact, then the conversation, then its events, in that order.
    """
    conv = None
    if contact.id is not None:
        conv = session.scalar(
            select(Conversation).where(Conversation.contact_id == contact.id))
    if conv is None:
        conv = Conversation(contact=contact, last_event_at=thread.last_event_at,
                            unread_count=0, starred=False)
        session.add(conv)

    # Keys already on a contact thread ANYWHERE, not just this one: the unique index
    # on `conversation_events.dedupe_key` is table-wide, and an insert that tripped
    # it would roll back the contact save the operator just made.
    keys = [ev.dedupe_key for ev in thread.events if ev.dedupe_key]
    held = set(session.scalars(
        select(ConversationEvent.dedupe_key)
        .where(ConversationEvent.dedupe_key.in_(keys))).all()) if keys else set()

    moved = skipped = 0
    for ev in list(thread.events):
        if ev.dedupe_key and ev.dedupe_key in held:
            skipped += 1
            continue
        copy = ConversationEvent(conversation=conv)
        _copy_payload(ev, copy)
        session.add(copy)
        moved += 1

    previous = _as_utc(conv.last_event_at)
    latest = _as_utc(thread.last_event_at)
    if latest is not None and (previous is None or latest > previous):
        conv.last_event_at = thread.last_event_at
    conv.unread_count = (conv.unread_count or 0) + (thread.unread_count or 0)
    conv.starred = bool(conv.starred) or bool(thread.starred)

    session.delete(thread)          # its events cascade
    return {"number_thread_id": thread.id, "moved": moved,
            "skipped_duplicates": skipped}


def _phone_changed(contact: Contact) -> bool:
    return sa_inspect(contact).attrs.phone.history.has_changes()


def adopt_on_flush(session: Session) -> list[dict]:
    """The `before_flush` hook. See the module docstring, "Adoption".

    Cheap when there is nothing to do, which is almost always: one `SELECT ... LIMIT
    1` on a table that is usually empty, and only for a flush that creates a contact
    or changes a phone.
    """
    candidates = [
        obj for obj in list(session.new) + list(session.dirty)
        if isinstance(obj, Contact) and obj.phone
        and (obj in session.new or _phone_changed(obj))
    ]
    if not candidates:
        return []
    results: list[dict] = []
    with session.no_autoflush:
        if session.scalar(select(NumberThread.id).limit(1)) is None:
            return []
        for contact in candidates:
            key = phone_key(contact.phone)
            if not key:
                continue
            thread = session.scalar(
                select(NumberThread).where(NumberThread.phone_key == key))
            if thread is None or thread in session.deleted:
                continue
            results.append({"contact": contact, **_move_into(session, thread, contact)})
    session.info.setdefault("adopted_number_threads", []).extend(results)
    return results


# ---------- turning an auto-created contact back into a number-only thread ----------
#
# Before 2026-09-13 an inbound call from an unknown number CREATED a contact
# (`created_by='owen-main'`). Production holds one, from 2026-09-11. The owner wants it
# back as a number-only thread with its history kept. This code cannot touch
# production, so it is a command the operator runs: `python -m app.convert_auto_contacts`
# (DRY RUN by default). The rules below decide what it may touch, and they are
# deliberately narrow: a contact a person has done ANYTHING with stays a contact.

# How far apart `created_at` and `updated_at` may be on a row nobody has edited. Both
# are stamped by separate `utcnow()` calls in one INSERT, microseconds apart; any PATCH
# moves `updated_at` by the length of a human's attention span at least.
UNEDITED_SLACK_SECONDS = 2.0

# The exact shape `_resolve_ingest_contact` wrote before 2026-09-13.
AUTO_CREATED_BY = "owen-main"


def conversion_blockers(db: Session, contact: Contact) -> list[str]:
    """Every reason `contact` must NOT be converted. Empty means it qualifies.

    Every reason is listed rather than the first, so a dry run tells the operator the
    whole story about a contact it is leaving alone.
    """
    from .models import (
        Appointment,
        ContactTag,
        EventType,
        Opportunity,
        OpportunityContact,
        OpportunityTask,
    )
    from .phones import format_phone

    why: list[str] = []
    if contact.created_by != AUTO_CREATED_BY:
        why.append("not created by owen-main (created_by=%r)" % contact.created_by)
    key = phone_key(contact.phone)
    if not key:
        why.append("no complete phone number")

    def count(stmt) -> int:
        from sqlalchemy import func
        return db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    checks = (
        ("opportunities", select(Opportunity.id).where(Opportunity.contact_id == contact.id)),
        ("additional-contact links on deals",
         select(OpportunityContact.id).where(OpportunityContact.contact_id == contact.id)),
        ("appointments", select(Appointment.id).where(Appointment.contact_id == contact.id)),
        ("tasks", select(OpportunityTask.id).where(OpportunityTask.contact_id == contact.id)),
        ("tags", select(ContactTag.id).where(ContactTag.contact_id == contact.id)),
        ("internal notes on the thread",
         select(ConversationEvent.id)
         .join(Conversation, Conversation.id == ConversationEvent.conversation_id)
         .where(Conversation.contact_id == contact.id,
                ConversationEvent.type.in_([EventType.NOTE, EventType.INTERNAL_COMMENT]))),
    )
    for label, stmt in checks:
        n = count(stmt)
        if n:
            why.append("has %d %s" % (n, label))

    # Never edited by a human: the row is exactly what the ingest wrote, and its
    # timestamps say nobody saved it since.
    edits = []
    expected_name = format_phone(contact.phone) or contact.phone
    if (contact.first_name or "") != (expected_name or ""):
        edits.append("first name")
    for field in ("last_name",):
        if (getattr(contact, field) or "") != "":
            edits.append(field.replace("_", " "))
    for field in ("email", "business_name", "owner_id", "date_of_birth",
                  "address_street", "address_city", "address_state",
                  "address_postal_code"):
        if getattr(contact, field) not in (None, ""):
            edits.append(field.replace("_", " "))
    if contact.dnd:
        edits.append("DND")
    if contact.custom_fields:
        edits.append("custom fields")
    if (contact.source or "") != "Inbound call":
        edits.append("source")
    if (contact.contact_type or "") != "Lead":
        edits.append("contact type")
    created, updated = _as_utc(contact.created_at), _as_utc(contact.updated_at)
    if created and updated and abs((updated - created).total_seconds()) > UNEDITED_SLACK_SECONDS:
        edits.append("saved after it was created")
    if edits:
        why.append("edited by a person (%s)" % ", ".join(edits))

    if key:
        others = db.scalars(
            select(Contact.id)
            .where(phone_match.phone_clause(Contact.phone, contact.phone),
                   Contact.id != contact.id)).all()
        if others:
            why.append("another contact holds the same number (%s)"
                       % ", ".join(str(i) for i in others))
    return why


def convert_contact(db: Session, contact: Contact) -> dict:
    """Move `contact`'s thread onto a number-only thread and delete the contact.

    The caller has checked `conversion_blockers` is empty. Nothing is committed
    here; the command commits once, after every contact, or not at all.
    """
    conv = db.scalar(select(Conversation).where(Conversation.contact_id == contact.id))
    events = list(conv.events) if conv else []
    thread, created = thread_for_number(
        db, contact.phone or "",
        at=conv.last_event_at if conv else contact.created_at)
    held = set(db.scalars(
        select(NumberThreadEvent.dedupe_key)
        .where(NumberThreadEvent.dedupe_key.in_(
            [e.dedupe_key for e in events if e.dedupe_key]))).all())
    moved = skipped = 0
    for ev in events:
        if ev.dedupe_key and ev.dedupe_key in held:
            skipped += 1
            continue
        copy = NumberThreadEvent(number_thread_id=thread.id)
        _copy_payload(ev, copy)
        db.add(copy)
        moved += 1
    if conv is not None:
        latest, previous = _as_utc(conv.last_event_at), _as_utc(thread.last_event_at)
        if not created and latest and (previous is None or latest > previous):
            thread.last_event_at = conv.last_event_at
        thread.unread_count = (thread.unread_count or 0) + (conv.unread_count or 0)
        thread.starred = bool(thread.starred) or bool(conv.starred)
        db.delete(conv)         # its events cascade — they were copied above
    db.flush()
    db.delete(contact)          # ContactTag rows cascade (there are none: checked)
    db.flush()
    return {"contact_id": contact.id, "number_thread_id": thread.id,
            "thread_created": created, "events_moved": moved,
            "events_skipped_duplicates": skipped}

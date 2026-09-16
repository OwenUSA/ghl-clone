"""The mirror locks: once a card is sent to Zuper, Zuper is the source of truth for the job.

The owner's rule (2026-09-16, zuper-decisions-v2): on a SENT card — Send to Zuper pressed, an
AHS email auto-send, or the day-one load — the stage, the visits (schedule), the job address,
the value, the title, the status, the pipeline and the owner are Zuper's, and so are the
name, phone, email and address of any CONTACT that has at least one sent card. Every CRM path
that could change one asks this module and is refused with a sentence starting "Change this
in Zuper" (409), before anything is written: the modal's PATCH, the board drag, bulk actions,
the calendar (book, drag, edit, cancel), the CLI (it is the API) and AI agents (they call the
same services; `app/ai/actions.py` turns their change requests into an urgent staff task).

Notes, Checklist answers and tasks stay editable and sync both ways. The locks do not depend
on the sync being switched on: a card that was sent stays a mirror.

What is NOT locked by these checks: the sync's own writes and the Workiz importer's, which go
through the ORM, not the routes (the importer bypass before cutover — DECISIONS.md).
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Appointment, Contact, Opportunity, ZuperMapping

SENT_STATES = ("queued", "creating", "linked")
PREFIX = "Change this in Zuper"

ADDRESS = ("address_street", "address_city", "address_state", "address_postal_code")
OPPORTUNITY_FIELDS = ("title", "stage_id", "pipeline_id", "status", "value_cents", "owner_id",
                      *ADDRESS)
CONTACT_FIELDS = ("first_name", "last_name", "phone", "email", *ADDRESS)
APPOINTMENTS = "appointments"

LABELS = {"title": "the title", "stage_id": "the stage", "pipeline_id": "the pipeline",
          "status": "the status", "value_cents": "the value", "owner_id": "the owner",
          "address_street": "the address", "address_city": "the address",
          "address_state": "the address", "address_postal_code": "the address",
          "first_name": "the name", "last_name": "the name", "phone": "the phone",
          "email": "the email", APPOINTMENTS: "its visits"}


def _sent_query():
    return select(ZuperMapping.crm_id).where(ZuperMapping.crm_type == "opportunity",
                                             ZuperMapping.state.in_(SENT_STATES))


def sent_ids(db: Session, ids: list[int] | set[int] | None = None) -> set[int]:
    stmt = _sent_query()
    if ids is not None:
        ids = [i for i in ids if i is not None]
        if not ids:
            return set()
        stmt = stmt.where(ZuperMapping.crm_id.in_(ids))
    with db.no_autoflush:
        return set(db.scalars(stmt).all())


def is_sent(db: Session, opportunity_id: int | None) -> bool:
    return opportunity_id is not None and bool(sent_ids(db, [opportunity_id]))


def locked_contact_ids(db: Session, contact_ids: list[int] | set[int]) -> set[int]:
    ids = [i for i in contact_ids if i is not None]
    if not ids:
        return set()
    with db.no_autoflush:
        return set(db.scalars(select(Opportunity.contact_id).where(
            Opportunity.contact_id.in_(ids), Opportunity.id.in_(_sent_query()))).all())


def contact_locked(db: Session, contact_id: int | None) -> bool:
    return contact_id is not None and bool(locked_contact_ids(db, [contact_id]))


def _words(fields) -> str:
    seen: list[str] = []
    for f in fields:
        label = LABELS.get(f, f)
        if label not in seen:
            seen.append(label)
    return ", ".join(seen)


def refusal(fields, what: str = "this job") -> HTTPException:
    return HTTPException(409, "%s: %s of %s %s managed in Zuper." % (
        PREFIX, _words(fields), what, "is" if what == "this job" else "are"))


def check_opportunity(db: Session, o: Opportunity, changes: dict) -> None:
    """Refuse a change to a locked field of a sent card. An unchanged echo is not a change."""
    changed = [k for k in OPPORTUNITY_FIELDS if k in changes and changes[k] != getattr(o, k)]
    if changed and is_sent(db, o.id):
        raise refusal(changed)


def check_opportunities(db: Session, cards: list[Opportunity], field: str) -> None:
    """A bulk action: refused whole when any selected card is sent."""
    hit = sorted(sent_ids(db, [o.id for o in cards]))
    if hit:
        raise HTTPException(409, "%s: %s of opportunity %s %s managed in Zuper. Nothing was "
                                 "changed." % (PREFIX, _words([field]),
                                               ", ".join(str(i) for i in hit),
                                               "is" if len(hit) == 1 else "are"))


def check_visit(db: Session, *opportunity_ids: int | None) -> None:
    """Booking, moving, editing or cancelling a visit on a sent card."""
    if any(is_sent(db, i) for i in opportunity_ids if i is not None):
        raise refusal([APPOINTMENTS])


def appointment_locked(db: Session, a: Appointment) -> bool:
    return is_sent(db, a.opportunity_id)


def check_contact(db: Session, c: Contact, changes: dict) -> None:
    changed = [k for k in CONTACT_FIELDS if k in changes and changes[k] != getattr(c, k)]
    if changed and contact_locked(db, c.id):
        raise HTTPException(409, "%s: %s of this contact %s managed in Zuper, because they "
                                 "are the customer on a job sent to Zuper." % (
                                     PREFIX, _words(changed), "is"))

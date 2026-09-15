"""GoHighLevel's "Only assigned data", enforced (2026-09-15).

The owner's rule, as given:

* A per-user switch, `users.only_assigned_data`. ON for a user created as a TECH, OFF
  for everyone else; an ADMIN flips it in Settings → My Staff. An ADMIN is never
  restricted — the same "ADMIN always" per-pipeline access has.
* A restricted user's JOBS are the opportunities they OWN, plus the opportunities with
  a NON-CANCELLED appointment on a calendar they own (`calendars.user_id`) or assigned
  to them (`appointments.assigned_user_id`). Per-pipeline access still applies on top:
  a job in a pipeline they cannot access is not theirs to see either.
* They see NOTHING outside their jobs, on any path: the board, lists, search, the
  contacts of those jobs only, the conversation threads of those contacts only (no
  number-only threads), only their own calendar entries and blocked time, and only
  the tasks, notes and photos of those jobs. Reporting, the Dashboard and the
  Forecast refuse them outright.
* A lookup by id of anything outside answers exactly what an id that does not exist
  answers — 404 — never 403, so existence does not leak.

The shape follows `pipeline_access.py`: the unrestricted case is `None` everywhere, and
every query is then exactly the query it was before this switch existed. That is what
makes "a DISPATCHER, an ADMIN, or anyone with the switch off is completely unaffected"
true by construction rather than by care.

What a restricted user may DO with what they can see is still their role's business,
with the owner's narrow additions for a TECH on their own jobs (answer the job
questions, add notes and tasks, complete their own tasks) — see `tech_on_own_job`.
"""
from dataclasses import dataclass

from fastapi import Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import auth
from .models import (
    Appointment,
    BlockedTime,
    Calendar,
    Contact,
    Conversation,
    Opportunity,
    OpportunityContact,
    Role,
)

# The status that does NOT make a visit a job. Every other status — booked, confirmed,
# showed, no-show, rescheduled... — is a visit that was or is on the tech's day.
CANCELLED = "cancelled"

REPORTING_REFUSED = ("Reporting, the Dashboard and the Forecast are not available to "
                     "a user with “Only assigned data” on — they show money and "
                     "other people's work.")


def restricted(principal: auth.Principal) -> bool:
    """Is this principal limited to their own jobs? Never for an ADMIN."""
    return principal.only_assigned and principal.role is not Role.ADMIN


@dataclass(frozen=True)
class Scope:
    """What one principal may see, computed once per request.

    `job_ids` and `contact_ids` are None for an unrestricted principal — "no extra
    filter" — and a (possibly empty) frozenset for a restricted one.
    """
    user_id: int
    hidden: frozenset[int]
    job_ids: frozenset[int] | None
    contact_ids: frozenset[int] | None

    @property
    def restricted(self) -> bool:
        return self.job_ids is not None

    def sees_job(self, o: Opportunity | None) -> bool:
        if o is None or o.pipeline_id in self.hidden:
            return False
        return self.job_ids is None or o.id in self.job_ids

    def sees_contact(self, contact_id: int | None) -> bool:
        if contact_id is None:
            return False
        return self.contact_ids is None or contact_id in self.contact_ids


def own_calendar_ids(user_id: int):
    """A SELECT of the calendars this user owns."""
    return select(Calendar.id).where(Calendar.user_id == user_id)


def appointment_is_theirs(user_id: int):
    """The appointments on a restricted user's calendar: on a calendar they own, or
    assigned to them. Every status — a cancelled visit still shows on their day; it
    just does not make its deal one of their jobs."""
    return or_(Appointment.assigned_user_id == user_id,
               Appointment.calendar_id.in_(own_calendar_ids(user_id)))


def job_ids(db: Session, user_id: int, hidden: set[int] | frozenset[int]) -> frozenset[int]:
    """The owner's definition of "their jobs", in two queries."""
    owned = select(Opportunity.id).where(Opportunity.owner_id == user_id)
    visited = select(Appointment.opportunity_id).where(
        Appointment.opportunity_id.is_not(None),
        Appointment.status != CANCELLED,
        appointment_is_theirs(user_id))
    stmt = select(Opportunity.id).where(
        or_(Opportunity.id.in_(owned), Opportunity.id.in_(visited)))
    if hidden:
        stmt = stmt.where(Opportunity.pipeline_id.not_in(hidden))
    return frozenset(db.scalars(stmt).all())


def contact_ids_for(db: Session, jobs: frozenset[int]) -> frozenset[int]:
    """The people on those jobs: each job's primary contact and its additional
    contacts — the job modal already names all of them."""
    if not jobs:
        return frozenset()
    primary = db.scalars(select(Opportunity.contact_id).where(
        Opportunity.id.in_(jobs), Opportunity.contact_id.is_not(None))).all()
    extra = db.scalars(select(OpportunityContact.contact_id).where(
        OpportunityContact.opportunity_id.in_(jobs))).all()
    return frozenset(primary) | frozenset(extra)


def scope(db: Session, principal: auth.Principal) -> Scope:
    # Imported here: pipeline_access imports this module for get_opportunity.
    from . import pipeline_access
    hidden = frozenset(pipeline_access.hidden_pipeline_ids(db, principal))
    if not restricted(principal):
        return Scope(principal.user_id, hidden, None, None)
    jobs = job_ids(db, principal.user_id, hidden)
    return Scope(principal.user_id, hidden, jobs, contact_ids_for(db, jobs))


# ---------- narrowing a query ----------

def opportunities(stmt, s: Scope):
    """Narrow any SELECT involving `Opportunity` to what `s` may see — pipelines AND
    jobs. The pipeline-only `pipeline_access.visible_opportunities` stays for callers
    that are not about a person's view (there are none on a read path any more)."""
    if s.hidden:
        stmt = stmt.where(Opportunity.pipeline_id.not_in(s.hidden))
    if s.job_ids is not None:
        stmt = stmt.where(Opportunity.id.in_(s.job_ids))
    return stmt


def contacts(stmt, s: Scope, column=Contact.id):
    if s.contact_ids is not None:
        stmt = stmt.where(column.in_(s.contact_ids))
    return stmt


def conversations(stmt, s: Scope):
    return contacts(stmt, s, Conversation.contact_id)


def appointments(stmt, s: Scope):
    if s.restricted:
        stmt = stmt.where(appointment_is_theirs(s.user_id))
    return stmt


def blocked_times(stmt, s: Scope):
    if s.restricted:
        stmt = stmt.where(BlockedTime.calendar_id.in_(own_calendar_ids(s.user_id)))
    return stmt


def calendars(stmt, s: Scope):
    """A restricted user's calendar list: the calendars they own, and any calendar
    holding an appointment assigned to them — the two places their day comes from."""
    if s.restricted:
        stmt = stmt.where(or_(
            Calendar.user_id == s.user_id,
            Calendar.id.in_(select(Appointment.calendar_id).where(
                Appointment.assigned_user_id == s.user_id))))
    return stmt


# ---------- lookups by id: 404, never 403 ----------

def get_contact(db: Session, principal: auth.Principal, contact_id: int) -> Contact:
    c = db.get(Contact, contact_id)
    if c is None or not scope(db, principal).sees_contact(c.id):
        raise HTTPException(404, "contact not found")
    return c


def get_conversation(db: Session, principal: auth.Principal,
                     conv_id: int) -> Conversation:
    conv = db.get(Conversation, conv_id)
    if conv is None or not scope(db, principal).sees_contact(conv.contact_id):
        raise HTTPException(404, "conversation not found")
    return conv


def get_appointment(db: Session, principal: auth.Principal,
                    appointment_id: int) -> Appointment:
    a = db.get(Appointment, appointment_id)
    if a is None or (restricted(principal) and not db.scalar(
            select(Appointment.id).where(Appointment.id == a.id,
                                         appointment_is_theirs(principal.user_id)))):
        raise HTTPException(404, "appointment not found")
    return a


def get_blocked_time(db: Session, principal: auth.Principal, blocked_id: int) -> BlockedTime:
    b = db.get(BlockedTime, blocked_id)
    if b is None or (restricted(principal) and not db.scalar(
            select(Calendar.id).where(Calendar.id == b.calendar_id,
                                      Calendar.user_id == principal.user_id))):
        raise HTTPException(404, "blocked off time not found")
    return b


def refuse_number_threads(principal: auth.Principal) -> None:
    """A number nobody has saved is nobody's job: hidden, and 404 by id."""
    if restricted(principal):
        raise HTTPException(404, "number thread not found")


def _require_reporting(principal: auth.Principal | None = Depends(
        auth.current_principal)) -> auth.Principal:
    """The STAFF gate for Reporting, the Dashboard and the Forecast, with the "Only
    assigned data" refusal asked FIRST — so a restricted technician is told why, in the
    same sentence a restricted dispatcher gets, rather than a bare role refusal."""
    if principal is None:
        raise HTTPException(401, "authentication required")
    refuse_reporting(principal)
    if principal.role not in (Role.ADMIN, Role.DISPATCHER):
        raise HTTPException(403, "role %s may not do this" % principal.role.value)
    return principal


def refuse_reporting(principal: auth.Principal) -> None:
    """Reporting, the Dashboard and the Forecast: 403 with a sentence. A 403 rather
    than a 404 on purpose — these screens exist for everyone and hide nothing's
    existence; the user needs to be told why they cannot open them."""
    if restricted(principal):
        raise HTTPException(403, REPORTING_REFUSED)


# ---------- what a TECH may do on their own job ----------

def tech_on_own_job(principal: auth.Principal) -> bool:
    """The owner's additions for a restricted TECH (2026-09-15): on a job that is
    theirs they may answer its questions, move its stage, add notes and tasks and
    complete their own tasks. Every route that uses this has already resolved the
    job through `pipeline_access.get_opportunity`, which 404s a job that is not
    theirs — so being restricted IS being on the job by the time this is asked.

    A TECH with the switch OFF keeps exactly today's rules: none of these."""
    return principal.role is Role.TECH and restricted(principal)


def sees_opportunity_notes(principal: auth.Principal) -> bool:
    """An opportunity's OWN notes: STAFF, and (2026-09-15) a restricted TECH on their
    own jobs. Conversation-thread NOTE / INTERNAL_COMMENT events and an appointment's
    internal notes are NOT widened — they stay `auth.sees_internal`."""
    return auth.sees_internal(principal) or tech_on_own_job(principal)


# The dependency the STAFF reporting routes use in place of `auth.STAFF`.
REPORTING = Depends(_require_reporting)

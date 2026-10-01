"""The bell, for the Dispatch page (Q4, Q17): a new job, an inspection or repair finished, no
pictures from a visit, a missed call or text from a customer, a limit passed.

In-app only — the bell, and the browser's own desktop notification while the CRM is open
(frontend/src/components/AiAlertBell.tsx). Nothing here texts or emails anyone.

Each event rings ONCE: its key goes into `dispatch_events` the first time. The first pass after
a deploy records every key it finds WITHOUT ringing (`rang` false) — otherwise turning the page
on would bury the office under a bell for every old job.

Who hears it: the people who can open the Dispatch page — an active ADMIN, or a DISPATCHER not
limited to their own jobs — and not one whose pipeline permissions hide that job's board.
"""
from __future__ import annotations

import logging

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..models import (
    AiAlert,
    DispatchEvent,
    Opportunity,
    Pipeline,
    PipelinePermission,
    Role,
    User,
    ZuperMapping,
)
from ..zuper import mapping
from .rules import Event

log = logging.getLogger("dispatch.alerts")
TITLE_MAX = 300


def recipients(db: Session) -> list[User]:
    return list(db.scalars(select(User).where(
        User.is_active.is_(True),
        or_(User.role == Role.ADMIN,
            and_(User.role == Role.DISPATCHER, User.only_assigned_data.is_(False))),
    ).order_by(User.id)))


def hidden_boards(db: Session, user_id: int, role: Role) -> set[str]:
    """The Zuper boards whose CRM pipeline this user may not see (`pipeline_access`'s rule:
    a pipeline with permission rows is open to the users named and every ADMIN)."""
    if role is Role.ADMIN:
        return set()
    allowed: dict[int, set[int]] = {}
    for pid, uid in db.execute(select(PipelinePermission.pipeline_id,
                                      PipelinePermission.user_id)).all():
        allowed.setdefault(pid, set()).add(uid)
    hidden_ids = {pid for pid, users in allowed.items() if user_id not in users}
    if not hidden_ids:
        return set()
    boards = mapping.mirrored_categories()
    names = db.scalars(select(Pipeline.name).where(Pipeline.id.in_(hidden_ids))).all()
    return {boards.get(n, n) for n in names}


def card_for(db: Session, job_uid: str | None) -> tuple[int | None, int | None]:
    """The CRM card mirroring this Zuper job, so the bell can open it."""
    if not job_uid:
        return None, None
    m = db.scalar(select(ZuperMapping).where(ZuperMapping.zuper_type == "job",
                                             ZuperMapping.zuper_uid == job_uid,
                                             ZuperMapping.crm_type == "opportunity"))
    if m is None:
        return None, None
    o = db.get(Opportunity, m.crm_id)
    return (o.id, o.contact_id) if o else (None, None)


def fire(db: Session, events: list[Event], *, ring: bool, boards: dict[str, str]) -> int:
    """Record each new event; ring the bell for it unless `ring` is False. `boards` maps a job
    uid to its board, for the pipeline check. Returns how many alerts were written."""
    written = 0
    known = set(db.scalars(select(DispatchEvent.key).where(
        DispatchEvent.key.in_([e.key for e in events])))) if events else set()
    people = recipients(db) if ring and events else []
    hidden = {u.id: hidden_boards(db, u.id, u.role) for u in people}
    for e in events:
        if e.key in known:
            continue
        known.add(e.key)
        db.add(DispatchEvent(key=e.key[:200], kind=e.kind[:40], job_uid=e.job_uid, rang=ring))
        if not ring:
            continue
        opp_id, contact_id = ((e.opportunity_id, e.contact_id) if e.opportunity_id
                              else card_for(db, e.job_uid))
        board = boards.get(e.job_uid or "")
        for u in people:
            if board and board in hidden[u.id]:
                continue
            db.add(AiAlert(user_id=u.id, kind=("dispatch_" + e.kind)[:30],
                           title=e.title[:TITLE_MAX], body=e.body, urgent=e.urgent,
                           opportunity_id=opp_id, contact_id=contact_id))
            written += 1
    return written

"""/api/dispatch — the Dispatch page (2026-09-30).

Who: an ADMIN, or a DISPATCHER not limited to their own jobs — the AI Agents audience. A
technician or a restricted user is refused 403 before anything is read. A board whose CRM
pipeline is hidden from the user (pipeline permissions) is hidden here too: its items are
left out of every list and count, and its job answers 404, as an id that does not exist would.

Phase 1 writes nothing to Zuper. Done and Wrong are staff's answer about an item, kept here:
Wrong is how the page's accuracy is measured before anything is ever allowed to write.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import assigned_access, auth
from ..db import get_db
from ..models import (
    DispatchItem,
    DispatchJob,
    DispatchState,
    DispatchSuggestion,
    Role,
    ZuperStatusHistory,
)
from ..zuper import config as zuper_config
from . import alerts, comms, rules
from . import config as c

router = APIRouter(prefix="/api/dispatch", tags=["dispatch"])

NOT_AVAILABLE = ("Dispatch is for admins and dispatchers only — not a technician or a user "
                 "with “Only assigned data” on.")


def _viewer(principal: auth.Principal | None = Depends(auth.current_principal)
            ) -> auth.Principal:
    if principal is None:
        raise HTTPException(401, "authentication required")
    if principal.role not in (Role.ADMIN, Role.DISPATCHER) or assigned_access.restricted(
            principal):
        raise HTTPException(403, NOT_AVAILABLE)
    return principal


VIEW = Depends(_viewer)


def _hidden(db: Session, principal: auth.Principal) -> set[str]:
    return alerts.hidden_boards(db, principal.user_id, principal.role)


def _zuper_url(job_uid: str | None) -> str | None:
    template = zuper_config.job_url_template()
    return template.format(uid=job_uid) if job_uid and template else None


def _iso(dt) -> str | None:
    dt = c.aware(dt)
    return dt.isoformat() if dt else None


def _item(r: DispatchItem) -> dict:
    return {"id": r.id, "kind": r.kind, "queue": r.queue, "title": r.title, "why": r.why,
            "todo": r.todo, "evidence": r.evidence or {}, "due_at": _iso(r.due_at),
            "urgent": r.urgent, "state": r.state, "job_uid": r.job_uid,
            "job_number": r.job_number, "board": r.board, "phone": r.phone,
            "zuper_url": _zuper_url(r.job_uid), "created_at": _iso(r.created_at),
            "closed_at": _iso(r.closed_at), "close_note": r.close_note,
            "ai": r.ai, "ai_error": r.ai_error, "suggestions": []}


def _suggestion(s: DispatchSuggestion) -> dict:
    return {"id": s.id, "item_id": s.item_id, "job_uid": s.job_uid, "job_number": s.job_number,
            "board": s.board, "kind": s.kind, "field": s.field, "current": s.current,
            "proposed": s.proposed, "evidence": s.evidence, "source": s.source,
            "state": s.state, "created_at": _iso(s.created_at),
            "applied_at": _iso(s.applied_at), "apply_result": s.apply_result,
            "zuper_url": _zuper_url(s.job_uid)}


# Shown on an item and in the Suggestions tab: still to do, or the agent could not confirm it
# (phase 3) — that one stays in sight until a person looks at Zuper.
SHOWN = ("open", "approved", "apply_failed")


def _with_suggestions(db: Session, items: list[dict]) -> list[dict]:
    """Each item carries the open / approved suggestions on its job."""
    uids = {i["job_uid"] for i in items if i["job_uid"]}
    if not uids:
        return items
    by_job: dict[str, list[dict]] = {}
    for s in db.scalars(select(DispatchSuggestion).where(
            DispatchSuggestion.job_uid.in_(uids),
            DispatchSuggestion.state.in_(SHOWN)).order_by(DispatchSuggestion.id)):
        by_job.setdefault(s.job_uid, []).append(_suggestion(s))
    for i in items:
        i["suggestions"] = by_job.get(i["job_uid"] or "", [])
    return items


def _visible(stmt, hidden: set[str]):
    if hidden:
        stmt = stmt.where((DispatchItem.board.is_(None)) | DispatchItem.board.not_in(hidden))
    return stmt


@router.get("/summary")
def dispatch_summary(principal: auth.Principal = VIEW, db: Session = Depends(get_db)):
    hidden = _hidden(db, principal)
    rows = db.execute(_visible(
        select(DispatchItem.queue, DispatchItem.urgent, func.count())
        .where(DispatchItem.state == "open").group_by(DispatchItem.queue, DispatchItem.urgent),
        hidden)).all()
    queues = {q: {"key": q, "label": rules.QUEUE_LABEL[q], "open": 0, "urgent": 0}
              for q in rules.QUEUES}
    for q, urgent, n in rows:
        if q in queues:
            queues[q]["open"] += n
            if urgent:
                queues[q]["urgent"] += n
    since = datetime.now(UTC) - timedelta(days=30)
    answers = dict(db.execute(_visible(
        select(DispatchItem.state, func.count())
        .where(DispatchItem.state.in_(("done", "wrong")), DispatchItem.closed_at >= since)
        .group_by(DispatchItem.state), hidden)).all())
    s = db.get(DispatchState, 1)
    return {
        "enabled": c.enabled(),
        "queues": list(queues.values()),
        "urgent": sum(q["urgent"] for q in queues.values()),
        "accuracy_30d": {"done": answers.get("done", 0), "wrong": answers.get("wrong", 0)},
        "heartbeat": {
            "last_success_at": _iso(s.last_success_at) if s else None,
            "last_error": s.last_error if s else None,
            "last_error_at": _iso(s.last_error_at) if s else None,
            "unknown_stages": ((s.last_counts or {}).get("jobs") or {}).get(
                "unknown_stages", []) if s else [],
            "poll_seconds": c.poll_seconds(),
        },
    }


@router.get("/items")
def dispatch_items(queue: str | None = None, state: str = "open",
                   principal: auth.Principal = VIEW, db: Session = Depends(get_db)):
    if queue is not None and queue not in rules.QUEUES:
        raise HTTPException(400, "unknown queue")
    if state not in ("open", "closed"):
        raise HTTPException(400, "state is open or closed")
    stmt = select(DispatchItem)
    if state == "open":
        stmt = stmt.where(DispatchItem.state == "open")
    else:
        stmt = stmt.where(DispatchItem.state != "open").order_by(
            DispatchItem.closed_at.desc()).limit(200)
    if queue:
        stmt = stmt.where(DispatchItem.queue == queue)
    rows = list(db.scalars(_visible(stmt, _hidden(db, principal))))
    if state == "open":
        rows.sort(key=_priority)
    return {"items": _with_suggestions(db, [_item(r) for r in rows])}


def _priority(r: DispatchItem):
    """Overdue first, the most RECENTLY overdue at the top (what just slipped beats what has
    been sitting for two weeks); then the rest by how soon they fall due; undated last."""
    due = c.aware(r.due_at)
    if r.urgent and due:
        return (0, -due.timestamp(), r.id)
    if due:
        return (1, due.timestamp(), r.id)
    return (2, 0, r.id)


class CloseIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


def _close(db: Session, principal: auth.Principal, item_id: int, state: str,
           body: CloseIn) -> dict:
    r = db.get(DispatchItem, item_id)
    if r is None or (r.board and r.board in _hidden(db, principal)):
        raise HTTPException(404, "no such item")
    if r.state != "open":
        raise HTTPException(409, "this item is already %s" % r.state)
    r.state, r.closed_at, r.closed_by_id = state, datetime.now(UTC), principal.user_id
    r.close_note = (body.note or "").strip() or None
    db.commit()
    return _item(r)


@router.post("/items/{item_id}/done")
def dispatch_item_done(item_id: int, body: CloseIn | None = None,
                       principal: auth.Principal = VIEW, db: Session = Depends(get_db)):
    """"Done — I did it (in Zuper / on the phone)". New evidence later is a new item."""
    return _close(db, principal, item_id, "done", body or CloseIn())


@router.post("/items/{item_id}/wrong")
def dispatch_item_wrong(item_id: int, body: CloseIn | None = None,
                        principal: auth.Principal = VIEW, db: Session = Depends(get_db)):
    """"Wrong — this should not have been flagged". Counted in the page's accuracy."""
    return _close(db, principal, item_id, "wrong", body or CloseIn())


@router.get("/jobs/{job_uid}")
def dispatch_job(job_uid: str, principal: auth.Principal = VIEW,
                 db: Session = Depends(get_db)):
    """One job as the page sees it: Zuper's state, its stage moves, and its calls and texts."""
    j = db.scalar(select(DispatchJob).where(DispatchJob.job_uid == job_uid))
    if j is None or (j.board and j.board in _hidden(db, principal)):
        raise HTTPException(404, "no such job")
    moves = db.scalars(select(ZuperStatusHistory).where(
        ZuperStatusHistory.job_uid == job_uid).order_by(ZuperStatusHistory.changed_at)).all()
    talk = comms.load(db, datetime.now(UTC) - timedelta(days=c.COMMS_DAYS))
    mine = sorted((x for p in j.phones or [] for x in talk.get(p, [])), key=lambda x: x.at)
    return {
        "job_uid": j.job_uid, "job_number": j.job_number, "title": j.title, "board": j.board,
        "status": j.status, "status_since": _iso(j.status_since), "status_by": j.status_by,
        "customer_name": j.customer_name, "phones": j.phones or [], "address": j.address,
        "city": j.city, "scheduled_start": _iso(j.scheduled_start),
        "scheduled_end": _iso(j.scheduled_end), "assigned": j.assigned or [],
        "technician": j.technician, "notes_count": j.notes_count,
        "last_note_at": _iso(j.last_note_at), "photos_today": j.photos_today,
        "last_photo_at": _iso(j.last_photo_at), "last_photo_by": j.last_photo_by,
        "zuper_url": _zuper_url(j.job_uid),
        "moves": [{"status": m.status_name, "board": m.category_name, "at": _iso(m.changed_at),
                   "by": m.done_by_name} for m in moves],
        "comms": [{"at": x.at.isoformat(), "kind": x.kind, "out": x.out, "talked": x.talked,
                   "missed": x.missed, "seconds": x.seconds, "source": x.source,
                   "summary": x.summary, "staff": x.staff} for x in mine[-15:]],
    }

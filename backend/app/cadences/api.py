"""/api/followups - the Follow-ups page, the "Follow up" button on a conversation or contact, and
the sequences.

Who: the Dispatch audience (`dispatch.api.VIEW`: ADMIN and unrestricted DISPATCHER; a technician
or restricted user 403) reads and works follow-ups; only an ADMIN creates or edits a sequence. A
follow-up on a board hidden from the user (pipeline permissions) answers 404, as a missing id.
Nothing here sends: texts and AI calls leave only from the worker's pass (service.run).
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import auth
from ..db import get_db
from ..dispatch.api import VIEW, _hidden
from ..dispatch.assist import ADMIN
from ..models import Followup, FollowupEvent, FollowupSequence, Role, User
from ..registry import catalog as rcat
from ..registry import service as registry
from ..zuper import config as zconfig
from . import config as c
from . import rules, service

router = APIRouter(prefix="/api/followups", tags=["followups"])
SHOWS = ("open", "due", "paused", "closed", "all")


def _iso(v) -> str | None:
    v = c.aware(v) if v else None
    return v.isoformat() if v else None


def _now() -> datetime:
    return datetime.now(UTC)


def _phone_display(p: str) -> str:
    return "(%s) %s-%s" % (p[:3], p[3:6], p[6:]) if len(p) == 10 else p


def _due(r: Followup, now: datetime) -> bool:
    """A "call me" step whose time has come: the person has to act."""
    step = r.steps[r.step_index] if r.step_index < len(r.steps or []) else None
    return (r.state == "open" and step is not None and step.get("type") == "call_me"
            and r.next_at is not None and c.aware(r.next_at) <= now)


def _row(r: Followup, now: datetime, last: FollowupEvent | None = None) -> dict:
    steps = r.steps or []
    step = steps[r.step_index] if r.step_index < len(steps) else None
    due = _due(r, now)
    return {
        "id": r.id, "phone": r.phone, "phone_display": _phone_display(r.phone),
        "customer_name": r.customer_name, "job_number": r.job_number, "job_uid": r.job_uid,
        "board": r.board, "contact_id": r.contact_id, "state": r.state,
        "stop_reason": r.stop_reason, "source": r.source, "sequence_id": r.sequence_id,
        "sequence_name": r.sequence_name, "step_index": r.step_index, "step_count": len(steps),
        "next_step": c.STEP_LABEL.get(step.get("type")) if step else None,
        "next_step_type": step.get("type") if step else None,
        "next_at": _iso(r.next_at) if r.state == "open" else None,
        "due": due, "overdue": due and (now - c.aware(r.next_at)).total_seconds() > 3600,
        "assigned_user_id": r.assigned_user_id, "assigned_label": r.assigned_label,
        "note": r.note, "started_at": _iso(r.started_at), "closed_at": _iso(r.closed_at),
        "created_by": r.created_by,
        "last": {"text": last.text, "at": _iso(last.at), "kind": last.kind} if last else None,
    }


def _get(db: Session, principal: auth.Principal, fid: int) -> Followup:
    r = db.get(Followup, fid)
    if r is None or (r.board and r.board in _hidden(db, principal)):
        raise HTTPException(404, "follow-up not found")
    return r


def _detail(db: Session, r: Followup) -> dict:
    now = _now()
    evs = service.events(db, r)
    out = _row(r, now, evs[-1] if evs else None)
    out["steps"] = [{"type": s.get("type"), "label": rules.describe(s),
                     "template": s.get("template"),
                     "status": ("done" if i < r.step_index else
                                "current" if i == r.step_index and r.state in service.OPEN_STATES
                                else "pending")}
                    for i, s in enumerate(r.steps or [])]
    out["events"] = [{"id": e.id, "at": _iso(e.at), "kind": e.kind, "state": e.state,
                      "step": e.step, "text": e.text, "who": e.who} for e in reversed(evs)
                     if e.kind != "text_sent"]          # the marker that recognises our own text
    return out


def _seq(s: FollowupSequence) -> dict:
    return {"id": s.id, "name": s.name, "board": s.board, "is_default": s.is_default,
            "active": s.active, "steps": s.steps,
            "labels": [rules.describe(x) for x in s.steps or []],
            "updated_at": _iso(s.updated_at or s.created_at)}


def _when(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return c.aware(datetime.fromisoformat(raw))
    except ValueError:
        raise HTTPException(422, "start_at must be a date and time") from None


# ---- reads -------------------------------------------------------------------------------------

@router.get("")
def followups_list(show: str = Query("open"), mine: bool = False,
                   principal: auth.Principal = VIEW, db: Session = Depends(get_db)) -> dict:
    if show not in SHOWS:
        raise HTTPException(422, "show must be one of %s" % ", ".join(SHOWS))
    now = _now()
    q = select(Followup)
    if show in ("open", "due"):
        q = q.where(Followup.state == "open")
    elif show == "paused":
        q = q.where(Followup.state == "paused")
    elif show == "closed":
        q = q.where(Followup.state.in_(("stopped", "no_response")))
    if mine:
        q = q.where(Followup.assigned_user_id == principal.user_id)
    hidden = _hidden(db, principal)
    rows = [r for r in db.scalars(q.order_by(Followup.id.desc()).limit(500))
            if not (r.board and r.board in hidden)]
    if show == "due":
        rows = [r for r in rows if _due(r, now)]
    last: dict[int, FollowupEvent] = {}
    if rows:
        for e in db.scalars(select(FollowupEvent).where(FollowupEvent.followup_id.in_(
                [r.id for r in rows])).order_by(FollowupEvent.at, FollowupEvent.id)):
            if e.kind != "text_sent":
                last[e.followup_id] = e

    def order(r: Followup):
        return (not _due(r, now), c.aware(r.next_at) if r.next_at and r.state == "open"
                else datetime.max.replace(tzinfo=UTC), -r.id)

    if show in ("open", "due", "paused"):
        rows.sort(key=order)
    return {"followups": [_row(r, now, last.get(r.id)) for r in rows]}


@router.get("/summary")
def followups_summary(principal: auth.Principal = VIEW, db: Session = Depends(get_db)) -> dict:
    """The counts Dispatch shows beside its link to the page."""
    now = _now()
    hidden = _hidden(db, principal)
    rows = [r for r in db.scalars(select(Followup).where(Followup.state == "open"))
            if not (r.board and r.board in hidden)]
    due = [r for r in rows if _due(r, now)]
    return {"open": len(rows), "due": len(due),
            "mine_due": sum(1 for r in due if r.assigned_user_id == principal.user_id)}


@router.get("/status")
def followups_status(principal: auth.Principal = VIEW, db: Session = Depends(get_db)) -> dict:
    now = _now()
    s = service.settings(db)
    gate = service.closed_gate()
    day = c.local(now).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)

    def today(kind: str, states: tuple[str, ...]) -> int:
        return db.scalar(select(func.count()).select_from(FollowupEvent).where(
            FollowupEvent.kind == kind, FollowupEvent.state.in_(states),
            FollowupEvent.at >= day)) or 0

    levels = {a: registry.level(db, c.KEY, a) for a in ("send_text", "place_calls")}
    return {"running": gate is None, "sentence": gate or (
                "On for test customers only." if s.test_only else "On for every customer."),
            "clash": zconfig.followups_clash(), "test_only": s.test_only,
            "levels": {k: rcat.LEVEL_LABEL[v] for k, v in levels.items()},
            "texts_today": today("text", ("sending", "sent")),
            "ai_calls_today": today("ai_call", ("requested",)),
            "texts_per_day": s.texts_per_day, "ai_calls_per_day": s.ai_calls_per_day,
            "is_admin": principal.role is Role.ADMIN}


@router.get("/staff")
def followups_staff(principal: auth.Principal = VIEW, db: Session = Depends(get_db)) -> dict:
    """Who a follow-up can be assigned to: active admins and dispatchers. Names only."""
    users = db.scalars(select(User).where(User.is_active.is_(True), User.role.in_(
        (Role.ADMIN, Role.DISPATCHER))).order_by(User.name))
    return {"users": [{"id": u.id, "name": u.name} for u in users]}


@router.get("/by-phone")
def followups_for_phone(phone: str, principal: auth.Principal = VIEW,
                        db: Session = Depends(get_db)) -> dict:
    """The "Follow up" button: is this number already being followed up?"""
    p = c.ten(phone)
    if not p:
        raise HTTPException(422, "a 10-digit phone number is needed")
    r = service.open_for(db, p)
    if r is not None and r.board and r.board in _hidden(db, principal):
        r = None
    return {"followup": _row(r, _now()) if r else None}


@router.get("/sequences")
def followups_sequences(principal: auth.Principal = VIEW, db: Session = Depends(get_db)) -> dict:
    service.ensure_sequences(db)
    db.commit()
    rows = db.scalars(select(FollowupSequence).order_by(FollowupSequence.id))
    return {"sequences": [_seq(s) for s in rows], "step_types": [
        {"type": t, "label": c.STEP_LABEL[t]} for t in c.STEP_TYPES]}


@router.get("/{fid}")
def followups_get(fid: int, principal: auth.Principal = VIEW,
                  db: Session = Depends(get_db)) -> dict:
    return _detail(db, _get(db, principal, fid))


# ---- changes -----------------------------------------------------------------------------------

class NewFollowup(BaseModel):
    phone: str = Field(max_length=40)
    sequence_id: int | None = None
    start_at: str | None = None
    note: str | None = Field(default=None, max_length=2000)
    assigned_user_id: int | None = None


def _assignee(db: Session, user_id: int | None) -> User | None:
    if user_id is None:
        return None
    u = db.get(User, user_id)
    if u is None or not u.is_active or u.role not in (Role.ADMIN, Role.DISPATCHER):
        raise HTTPException(422, "a follow-up can be assigned to an active admin or dispatcher")
    return u


@router.post("", status_code=201)
def followups_create(body: NewFollowup, principal: auth.Principal = VIEW,
                     db: Session = Depends(get_db)) -> dict:
    phone = c.ten(body.phone)
    if not phone:
        raise HTTPException(422, "a 10-digit phone number is needed")
    existing = service.open_for(db, phone)
    if existing is not None:
        raise HTTPException(409, {"message": "This number is already being followed up.",
                                  "followup_id": existing.id})
    now = _now()
    if body.sequence_id is not None:
        seq = db.get(FollowupSequence, body.sequence_id)
        if seq is None or not seq.active:
            raise HTTPException(422, "no such sequence")
    else:
        seq = service.sequence_for(db, service.customer_for(db, phone)["board"])
        if seq is None:
            raise HTTPException(422, "there is no sequence to start with")
    start_at = _when(body.start_at)
    assigned = (_assignee(db, body.assigned_user_id) if body.assigned_user_id is not None
                else db.get(User, principal.user_id))
    row = service.start(db, phone, now, source="manual", sequence=seq, start_at=start_at,
                        note=(body.note or "").strip() or None, assigned=assigned,
                        created_by=principal.name,
                        skip_first_text=service.auto_texted_recently(db, phone, now))
    if row is None:
        raise HTTPException(409, "This number is already being followed up.")
    if row.board and row.board in _hidden(db, principal):
        db.rollback()
        raise HTTPException(404, "follow-up not found")
    db.commit()
    return _detail(db, row)


class Action(BaseModel):
    action: str = Field(pattern="^(pause|resume|skip|stop|reassign|restart)$")
    reason: str | None = Field(default=None, max_length=500)
    user_id: int | None = None
    sequence_id: int | None = None
    start_at: str | None = None


@router.post("/{fid}/action")
def followups_action(fid: int, body: Action, principal: auth.Principal = VIEW,
                     db: Session = Depends(get_db)) -> dict:
    row = _get(db, principal, fid)
    seq = None
    if body.sequence_id is not None:
        seq = db.get(FollowupSequence, body.sequence_id)
        if seq is None or not seq.active:
            raise HTTPException(422, "no such sequence")
    user = _assignee(db, body.user_id) if body.action == "reassign" else None
    try:
        out = service.act(db, row, body.action, _now(), by=principal.name, reason=body.reason,
                          user=user, sequence=seq, start_at=_when(body.start_at))
    except service.Refused as e:
        db.rollback()
        raise HTTPException(409, str(e)) from None
    db.commit()
    return _detail(db, out)


class SequenceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    board: str | None = Field(default=None, max_length=120)
    is_default: bool = False
    active: bool = True
    steps: list[dict]


def _save_sequence(db: Session, s: FollowupSequence, body: SequenceIn) -> FollowupSequence:
    problems = rules.validate_steps(body.steps)
    if problems:
        raise HTTPException(422, "; ".join(problems))
    s.name, s.board = body.name.strip(), (body.board or "").strip() or None
    s.active, s.is_default = body.active, body.is_default and body.active
    s.steps = [{k: v for k, v in x.items() if v is not None} for x in body.steps]
    s.updated_at = _now()
    db.flush()
    if s.is_default:
        # One default per board: the others on the same board stop being it.
        for other in db.scalars(select(FollowupSequence).where(
                FollowupSequence.id != s.id, FollowupSequence.is_default.is_(True))):
            if (other.board or None) == s.board:
                other.is_default = False
    return s


@router.post("/sequences", status_code=201)
def followups_create_sequence(body: SequenceIn, principal: auth.Principal = ADMIN,
                              db: Session = Depends(get_db)) -> dict:
    s = FollowupSequence(name=body.name, steps=[])
    db.add(s)
    _save_sequence(db, s, body)
    db.commit()
    return _seq(s)


@router.put("/sequences/{sid}")
def followups_update_sequence(sid: int, body: SequenceIn, principal: auth.Principal = ADMIN,
                              db: Session = Depends(get_db)) -> dict:
    s = db.get(FollowupSequence, sid)
    if s is None:
        raise HTTPException(404, "no such sequence")
    _save_sequence(db, s, body)
    db.commit()
    return _seq(s)

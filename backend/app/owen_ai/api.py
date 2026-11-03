"""/api/owen-ai — the owner's assistant page (2026-11-02). See __init__.py.

ONE person: an ADMIN whose email is `OWEN_AI_OWNER_EMAIL`. Everyone else — other admins
included — gets 404 on every route, as for a route that does not exist. A message is saved
QUEUED and answered by the runner; the page polls the thread.
"""
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import auth
from ..db import get_db
from ..models import OwenAiAlert, OwenAiMessage, OwenAiThread, Role
from ..zuper import config as zuper_config
from . import alerts, config

router = APIRouter(prefix="/api/owen-ai", tags=["owen-ai"])
NY = ZoneInfo("America/New_York")
KINDS = ("daily", "job", "task")          # the owner creates these
LISTED = (*KINDS, "audit")                # audit threads are made by the sweep


def owner(principal: auth.Principal | None = Depends(auth.current_principal)) -> auth.Principal:
    if (principal is None or principal.role != Role.ADMIN or not config.owner_email()
            or (principal.email or "").strip().lower() != config.owner_email()):
        raise HTTPException(404, "Not Found")
    return principal


OWNER = Depends(owner)


def _iso(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def _thread(th: OwenAiThread, busy: bool = False) -> dict:
    return {"id": th.id, "kind": th.kind, "title": th.title, "job_uid": th.job_uid,
            "job_number": th.job_number, "day": th.day.isoformat() if th.day else None,
            "archived": th.archived, "busy": busy, "created_at": _iso(th.created_at),
            "updated_at": _iso(th.updated_at)}


def _message(m: OwenAiMessage) -> dict:
    return {"id": m.id, "role": m.role, "content": m.content, "status": m.status,
            "kind": m.kind, "job_uid": m.job_uid,
            "steps": m.steps or [], "error": m.error, "model": m.model,
            "cost_usd": m.cost_usd, "duration_ms": m.duration_ms, "num_turns": m.num_turns,
            "created_at": _iso(m.created_at), "finished_at": _iso(m.finished_at)}


def _busy_ids(db: Session) -> set[int]:
    return set(db.scalars(select(OwenAiMessage.thread_id).where(
        OwenAiMessage.status.in_(("queued", "running")))))


def _own(db: Session, p: auth.Principal, thread_id: int) -> OwenAiThread:
    th = db.get(OwenAiThread, thread_id)
    if th is None or th.user_id != p.user_id:
        raise HTTPException(404, "Not Found")
    return th


@router.get("/status")
def status(p: auth.Principal = OWNER, db: Session = Depends(get_db)) -> dict:
    last = db.scalars(select(OwenAiMessage).where(OwenAiMessage.role == "assistant")
                      .order_by(OwenAiMessage.id.desc()).limit(1)).first()
    return {"enabled": config.enabled(), "claude_login": config.token_set(),
            "zuper": zuper_config.env_enabled() and zuper_config.key_set(),
            "quo": bool(config.quo_key()), "model": config.model() or "plan default",
            "last_answer_at": _iso(last.finished_at) if last else None,
            "sentence": None if config.enabled() and config.token_set() else (
                config.OFF_SENTENCE if not config.enabled() else config.NO_TOKEN_SENTENCE)}


@router.get("/threads")
def threads(kind: str | None = None, archived: bool = False, p: auth.Principal = OWNER,
            db: Session = Depends(get_db)) -> list[dict]:
    q = select(OwenAiThread).where(OwenAiThread.user_id == p.user_id,
                                   OwenAiThread.archived.is_(archived))
    if kind in LISTED:
        q = q.where(OwenAiThread.kind == kind)
    busy = _busy_ids(db)
    return [_thread(t, t.id in busy) for t in
            db.scalars(q.order_by(OwenAiThread.updated_at.desc()).limit(300))]


class NewThread(BaseModel):
    kind: str = "task"
    title: str | None = Field(None, max_length=200)
    job_uid: str | None = Field(None, max_length=64)
    job_number: str | None = Field(None, max_length=40)


@router.post("/threads")
def create_thread(body: NewThread, p: auth.Principal = OWNER,
                  db: Session = Depends(get_db)) -> dict:
    """daily: today's (one per day, found or made). job: that job's (found or made, by uid or
    number). task: always a new one."""
    if body.kind not in KINDS:
        raise HTTPException(422, "kind must be one of %s" % ", ".join(KINDS))
    mine = select(OwenAiThread).where(OwenAiThread.user_id == p.user_id,
                                      OwenAiThread.kind == body.kind)
    th = None
    if body.kind == "daily":
        today = datetime.now(NY).date()
        th = db.scalars(mine.where(OwenAiThread.day == today)).first()
        if th is None:
            th = OwenAiThread(user_id=p.user_id, kind="daily", day=today,
                              title="Daily — %s %d" % (today.strftime("%a %b"), today.day))
    elif body.kind == "job":
        uid = (body.job_uid or "").strip() or None
        num = (body.job_number or "").strip().lstrip("#") or None
        if not uid and not num:
            raise HTTPException(422, "a job conversation needs job_uid or job_number")
        if uid:
            th = db.scalars(mine.where(OwenAiThread.job_uid == uid)).first()
        if th is None and num:
            th = db.scalars(mine.where(OwenAiThread.job_number == num)).first()
        if th is None:
            th = OwenAiThread(user_id=p.user_id, kind="job", job_uid=uid, job_number=num,
                              title=(body.title or "").strip()[:200] or "Job #%s" % (num or uid))
    else:
        th = OwenAiThread(user_id=p.user_id, kind="task",
                          title=(body.title or "").strip()[:200] or "New chat")
    if th.archived:
        th.archived = False
    if th.id is None:
        db.add(th)
    db.commit()
    db.refresh(th)
    return _thread(th, th.id in _busy_ids(db))


@router.get("/threads/{thread_id}")
def thread(thread_id: int, p: auth.Principal = OWNER, db: Session = Depends(get_db)) -> dict:
    th = _own(db, p, thread_id)
    msgs = db.scalars(select(OwenAiMessage).where(OwenAiMessage.thread_id == th.id)
                      .order_by(OwenAiMessage.id)).all()
    busy = any(m.status in ("queued", "running") for m in msgs)
    return {**_thread(th, busy), "messages": [_message(m) for m in msgs]}


class EditThread(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    archived: bool | None = None


@router.patch("/threads/{thread_id}")
def edit_thread(thread_id: int, body: EditThread, p: auth.Principal = OWNER,
                db: Session = Depends(get_db)) -> dict:
    th = _own(db, p, thread_id)
    if body.title is not None:
        th.title = body.title.strip()[:200]
    if body.archived is not None:
        th.archived = body.archived
    db.commit()
    return _thread(th, th.id in _busy_ids(db))


class NewMessage(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)


@router.post("/threads/{thread_id}/messages")
def send(thread_id: int, body: NewMessage, p: auth.Principal = OWNER,
         db: Session = Depends(get_db)) -> dict:
    th = _own(db, p, thread_id)
    if not config.enabled():
        raise HTTPException(409, config.OFF_SENTENCE)
    pending = db.scalar(select(func.count()).select_from(OwenAiMessage).where(
        OwenAiMessage.thread_id == th.id, OwenAiMessage.status.in_(("queued", "running"))))
    if pending:
        raise HTTPException(409, "Owen AI is still answering in this conversation.")
    now = datetime.now(UTC)
    m = OwenAiMessage(thread_id=th.id, role="user", content=body.content.strip(),
                      status="queued", created_at=now)
    db.add(m)
    if th.kind == "task" and th.title == "New chat":
        first = " ".join(body.content.split())
        th.title = first[:config.TITLE_MAX] + ("…" if len(first) > config.TITLE_MAX else "")
    th.updated_at = now
    db.commit()
    db.refresh(m)
    return _message(m)


# ------------------------------------------------------------------------------- alerts

@router.get("/alerts")
def list_alerts(status: str = "open", job_uid: str | None = None, p: auth.Principal = OWNER,
                db: Session = Depends(get_db)) -> dict:
    q = select(OwenAiAlert)
    if status in ("open", "resolved", "dismissed"):
        q = q.where(OwenAiAlert.status == status)
    if job_uid:
        q = q.where(OwenAiAlert.job_uid == job_uid)
    rows = list(db.scalars(q.order_by(OwenAiAlert.last_seen_at.desc()).limit(500)))
    rows.sort(key=lambda a: alerts.RANK.get(a.severity, 9))
    counts = dict.fromkeys(alerts.SEVERITIES, 0)
    for a in db.scalars(select(OwenAiAlert).where(OwenAiAlert.status == "open")):
        counts[a.severity] = counts.get(a.severity, 0) + 1
    return {"alerts": [alerts.out(a) for a in rows], "open_counts": counts}


class Dismiss(BaseModel):
    why: str = Field(min_length=1, max_length=1000)


@router.post("/alerts/{alert_id}/dismiss")
def dismiss_alert(alert_id: int, body: Dismiss, p: auth.Principal = OWNER,
                  db: Session = Depends(get_db)) -> dict:
    """The owner says it is not a problem (or handled). The next audit may raise it again only
    as a NEW alert if the problem is really still there."""
    a = db.get(OwenAiAlert, alert_id)
    if a is None:
        raise HTTPException(404, "Not Found")
    if a.status == "open":
        alerts.close(db, a, "dismissed", "Dismissed by %s: %s" % (p.name or p.email, body.why))
        db.commit()
    return alerts.out(a)

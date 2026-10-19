"""/api/autoreply — the auto-reply's switch, its settings and what it did (Dispatch → Settings).

Who: the Dispatch audience (ADMIN + unrestricted DISPATCHER) reads; only an ADMIN changes
anything. Answering REAL customers (switching "test customers only" off) needs the typed
confirmation "TURN ON". Switching it on, or leaving test mode, restarts the clock: nothing that
started waiting before that moment is ever answered (Q22, the backlog).
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth
from ..db import get_db
from ..dispatch.api import VIEW
from ..dispatch.assist import ADMIN
from ..models import AiConnection, AutoReply, Role
from ..zuper import config as zconfig
from . import config as c
from . import service

router = APIRouter(prefix="/api/autoreply", tags=["autoreply"])

CONFIRM = "TURN ON"
LOG_ROWS = 60


def _row(r: AutoReply) -> dict:
    return {"id": r.id, "state": r.state, "reason": r.reason, "kind": r.trigger_kind,
            "waiting_since": r.trigger_at.isoformat() if r.trigger_at else None,
            "customer": r.customer_name, "job_number": r.job_number, "reply": r.reply,
            "after_hours": r.after_hours, "upset": r.upset, "needs_person": r.needs_person,
            "written_by": r.written_by, "note": r.note_state, "error": r.error,
            "at": r.created_at.isoformat() if r.created_at else None,
            "sent_at": r.sent_at.isoformat() if r.sent_at else None}


def _out(db: Session, admin: bool) -> dict:
    s = service.settings(db)
    now = datetime.now(UTC)
    conns = db.scalars(select(AiConnection).order_by(AiConnection.name)).all() if admin else []
    rows = db.scalars(select(AutoReply).order_by(AutoReply.id.desc()).limit(LOG_ROWS)).all()
    return {
        "server_enabled": zconfig.autoreply_enabled(),
        "enabled": s.autoreply_enabled,
        "enabled_at": s.autoreply_enabled_at.isoformat() if s.autoreply_enabled_at else None,
        "test_only": s.autoreply_test_only is not False,
        "daily_cap": s.autoreply_daily_cap if s.autoreply_daily_cap is not None else 30,
        "sent_today": service.sent_today(db, now),
        "connection_id": s.autoreply_connection_id, "model": s.autoreply_model,
        "transcribe_connection_id": s.autoreply_transcribe_connection_id,
        "last_run_at": s.autoreply_last_run_at.isoformat() if s.autoreply_last_run_at else None,
        "last_error": s.autoreply_last_error,
        "wait_minutes": c.WAIT_MINUTES, "sender": c.SENDER_DISPLAY,
        "connections": [{"id": x.id, "name": x.name, "provider": x.provider,
                         "default_model": x.default_model} for x in conns],
        "log": [_row(r) for r in rows],
    }


@router.get("")
def autoreply_status(principal: auth.Principal = VIEW, db: Session = Depends(get_db)):
    return _out(db, principal.role is Role.ADMIN)


class SettingsIn(BaseModel):
    enabled: bool | None = None
    test_only: bool | None = None
    daily_cap: int | None = Field(None, ge=0, le=200)
    connection_id: int | None = None
    model: str | None = Field(None, max_length=120)
    transcribe_connection_id: int | None = None
    clear_transcribe: bool = False
    confirm: str | None = None


@router.put("/settings")
def autoreply_put_settings(body: SettingsIn, principal: auth.Principal = ADMIN,
                           db: Session = Depends(get_db)):
    s = service.settings(db, for_update=True)
    now = datetime.now(UTC)
    for cid in (body.connection_id, body.transcribe_connection_id):
        if cid is not None and db.get(AiConnection, cid) is None:
            raise HTTPException(404, "no such AI connection")
    if body.transcribe_connection_id is not None:
        conn = db.get(AiConnection, body.transcribe_connection_id)
        if conn.provider != "openai":
            raise HTTPException(400, "Transcribing recordings needs an OpenAI connection.")
    if body.test_only is False and s.autoreply_test_only is not False:
        if (body.confirm or "").strip() != CONFIRM:
            raise HTTPException(400, "Answering real customers needs the typed confirmation "
                                     "%r." % CONFIRM)
        s.autoreply_test_only = False
        s.autoreply_enabled_at = now
    elif body.test_only is True:
        s.autoreply_test_only = True
    if body.enabled is not None and body.enabled != s.autoreply_enabled:
        s.autoreply_enabled = body.enabled
        if body.enabled:
            s.autoreply_enabled_at = now
    if body.daily_cap is not None:
        s.autoreply_daily_cap = body.daily_cap
    if body.connection_id is not None:
        s.autoreply_connection_id = body.connection_id
    if body.model is not None:
        s.autoreply_model = body.model.strip() or None
    if body.transcribe_connection_id is not None:
        s.autoreply_transcribe_connection_id = body.transcribe_connection_id
    elif body.clear_transcribe:
        s.autoreply_transcribe_connection_id = None
    s.updated_at, s.updated_by_id = now, principal.user_id
    db.commit()
    return _out(db, True)

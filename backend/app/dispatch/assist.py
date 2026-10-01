"""/api/dispatch — phase 2's routes (2026-09-30): suggestions, booking slots, the AI.

Same gate as the page (`api._viewer`: ADMIN + unrestricted DISPATCHER), same hidden boards. The
settings are read by the office and changed by an ADMIN only. Nothing here writes to Zuper:
"Approve" on a suggestion means "correct — I'll make this change in Zuper". (Phase 3's "let the
agent do it" is act.py, off unless the server and an ADMIN both say so.)
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth
from ..db import get_db
from ..models import AiConnection, DispatchItem, DispatchJob, DispatchSuggestion, Role
from . import ai, booking, writes
from .api import SHOWN, VIEW, _hidden, _item, _suggestion, _viewer, _with_suggestions

router = APIRouter(prefix="/api/dispatch", tags=["dispatch"])


def _admin(principal: auth.Principal = Depends(_viewer)) -> auth.Principal:
    if principal.role is not Role.ADMIN:
        raise HTTPException(403, "only an admin can change Dispatch settings")
    return principal


ADMIN = Depends(_admin)


def _visible_item(db: Session, principal: auth.Principal, item_id: int) -> DispatchItem:
    r = db.get(DispatchItem, item_id)
    if r is None or (r.board and r.board in _hidden(db, principal)):
        raise HTTPException(404, "no such item")
    return r


def _visible_job(db: Session, principal: auth.Principal, job_uid: str) -> DispatchJob:
    j = db.scalar(select(DispatchJob).where(DispatchJob.job_uid == job_uid))
    if j is None or (j.board and j.board in _hidden(db, principal)):
        raise HTTPException(404, "no such job")
    return j


# ---- suggestions ---------------------------------------------------------------------------

@router.get("/suggestions")
def dispatch_suggestions(state: str = "open", principal: auth.Principal = VIEW,
                         db: Session = Depends(get_db)):
    """Changes the AI believes Zuper needs, newest first. Never applied here."""
    states = SHOWN if state == "open" else (state,)
    hidden = _hidden(db, principal)
    rows = db.scalars(select(DispatchSuggestion).where(DispatchSuggestion.state.in_(states))
                      .order_by(DispatchSuggestion.id.desc()).limit(200))
    return {"suggestions": [_suggestion(s) for s in rows
                            if not (s.board and s.board in hidden)]}


def _decide(db: Session, principal: auth.Principal, sid: int, state: str) -> dict:
    s = db.get(DispatchSuggestion, sid)
    if s is None or (s.board and s.board in _hidden(db, principal)):
        raise HTTPException(404, "no such suggestion")
    if s.state not in SHOWN or s.state == state:
        raise HTTPException(409, "this suggestion is already %s" % s.state)
    s.state, s.decided_at, s.decided_by_id = state, datetime.now(UTC), principal.user_id
    db.commit()
    return _suggestion(s)


@router.post("/suggestions/{sid}/approve")
def dispatch_suggestion_approve(sid: int, principal: auth.Principal = VIEW,
                                db: Session = Depends(get_db)):
    """"Correct — I'll make this change in Zuper." Nothing is written to Zuper; the suggestion
    closes itself (done_in_zuper) once Zuper shows the value."""
    return _decide(db, principal, sid, "approved")


@router.post("/suggestions/{sid}/wrong")
def dispatch_suggestion_wrong(sid: int, principal: auth.Principal = VIEW,
                              db: Session = Depends(get_db)):
    """"Wrong — the calls do not support this." Counted against the AI."""
    return _decide(db, principal, sid, "wrong")


# ---- booking ---------------------------------------------------------------------------------

@router.get("/jobs/{job_uid}/slots")
def dispatch_job_slots(job_uid: str, kind: str | None = None, technician: str | None = None,
                       principal: auth.Principal = VIEW, db: Session = Depends(get_db)):
    """Three visit slots, closest to what is already booked (booking.py). A person agrees one
    with the customer and books it in Zuper."""
    if kind is not None and kind not in booking.SLOT_MINUTES:
        raise HTTPException(400, "kind is inspection or repair")
    j = _visible_job(db, principal, job_uid)
    note = None if j.lat is not None and j.lng is not None else (
        "This job has no map position in Zuper, so the driving times are guesses.")
    return {"slots": ai.slots_for(db, j, datetime.now(UTC), kind, technician), "note": note}


# ---- the AI ----------------------------------------------------------------------------------

@router.post("/items/{item_id}/explain")
def dispatch_item_explain(item_id: int, principal: auth.Principal = VIEW,
                          db: Session = Depends(get_db)):
    """Ask the AI (again) about one item. Counts against the daily cap."""
    r = _visible_item(db, principal, item_id)
    try:
        ai.provider_for(db, datetime.now(UTC))
    except ai.Unavailable as e:
        raise HTTPException(409, str(e)) from None
    r.ai, r.ai_error = None, None
    ai.explain(db, r, datetime.now(UTC))
    db.commit()
    return _with_suggestions(db, [_item(r)])[0]


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=4000)


class ChatIn(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=30)


@router.post("/chat")
def dispatch_chat(body: ChatIn, principal: auth.Principal = VIEW,
                  db: Session = Depends(get_db)):
    """Ask the Dispatch assistant. It reads the boards this user may see and may record
    suggestions; it never writes to Zuper or contacts anyone."""
    if body.messages[-1].role != "user":
        raise HTTPException(400, "the last message must be the user's")
    try:
        out = ai.chat(db, [m.model_dump() for m in body.messages], user_id=principal.user_id,
                      hidden=_hidden(db, principal))
    except ai.Unavailable as e:
        raise HTTPException(409, str(e)) from None
    sugs = db.scalars(select(DispatchSuggestion).where(
        DispatchSuggestion.id.in_(out["suggestions"]))).all() if out["suggestions"] else []
    return {"reply": out["reply"], "suggestions": [_suggestion(s) for s in sugs]}


# ---- settings --------------------------------------------------------------------------------

def _settings_out(db: Session) -> dict:
    s = ai.settings(db)
    conns = db.scalars(select(AiConnection).order_by(AiConnection.name)).all()
    return {"ai_enabled": s.ai_enabled, "connection_id": s.connection_id, "model": s.model,
            "daily_cap": s.daily_cap, "runs_today": ai.runs_today(db, datetime.now(UTC)),
            "technicians": s.technicians or booking.DEFAULT_TECHNICIANS,
            "writes": writes.status(db),
            "connections": [{"id": x.id, "name": x.name, "provider": x.provider,
                             "last4": x.api_key_last4} for x in conns]}


@router.get("/settings")
def dispatch_get_settings(principal: auth.Principal = VIEW, db: Session = Depends(get_db)):
    return _settings_out(db)


HHMM = r"^\d{2}:\d{2}$"


class Technician(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    does: list[str] = Field(min_length=1)
    prefers: str
    days: list[int] = Field(min_length=1)
    start: str = Field(pattern=HHMM)
    end: str = Field(pattern=HHMM)
    max: int = Field(ge=1, le=12)
    home: list[float] | None = None


class SettingsIn(BaseModel):
    ai_enabled: bool | None = None
    connection_id: int | None = None
    model: str | None = Field(default=None, min_length=1, max_length=120)
    daily_cap: int | None = Field(default=None, ge=0, le=5000)
    technicians: list[Technician] | None = None


@router.put("/settings")
def dispatch_put_settings(body: SettingsIn, principal: auth.Principal = ADMIN,
                          db: Session = Depends(get_db)):
    """ADMIN. With the AI on, job details and call summaries / transcripts go to the chosen
    provider when it explains an item or answers the chat."""
    s = ai.settings(db, for_update=True)
    data = body.model_dump(exclude_unset=True)
    if data.get("connection_id") is not None and \
            db.get(AiConnection, data["connection_id"]) is None:
        raise HTTPException(400, "no such AI connection")
    for k in ("connection_id", "model", "daily_cap"):
        if k in data:
            setattr(s, k, data[k])
    if "technicians" in data:
        for t in data["technicians"] or []:
            if any(k not in booking.SLOT_MINUTES for k in t["does"]) or \
                    t["prefers"] not in booking.SLOT_MINUTES or \
                    any(d not in range(7) for d in t["days"]):
                raise HTTPException(400, "a technician's visits are inspection / repair and "
                                         "days are 0 (Monday) to 6")
        s.technicians = data["technicians"]
    if data.get("ai_enabled") is not None:
        if data["ai_enabled"] and s.connection_id is None:
            raise HTTPException(400, "choose an AI connection first")
        s.ai_enabled = data["ai_enabled"]
    s.updated_at, s.updated_by_id = datetime.now(UTC), principal.user_id
    db.commit()
    return _settings_out(db)

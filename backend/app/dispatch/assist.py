"""/api/dispatch — phase 2's routes (2026-09-30): suggestions, booking slots, the AI.

Same gate as the page (`api._viewer`: ADMIN + unrestricted DISPATCHER), same hidden boards. The
settings are read by the office and changed by an ADMIN only. Nothing here writes to Zuper:
"Approve" on a suggestion means "correct — I'll make this change in Zuper". (Phase 3's "let the
agent do it" is act.py, off unless the server and an ADMIN both say so.)
"""
from __future__ import annotations

import io
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth
from ..db import get_db
from ..models import (
    AiConnection,
    DispatchAvailability,
    DispatchItem,
    DispatchJob,
    DispatchPlan,
    DispatchSuggestion,
    Role,
)
from . import ai, availability, booking, plan_excel, planner, scheduling, writes
from . import config as c
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
    return {"slots": ai.slots_for(db, j, datetime.now(UTC), kind, technician,
                                  _hidden(db, principal)), "note": note}


# ---- the week planner ----------------------------------------------------------------------

@router.get("/plan")
def dispatch_plan(days: int = 6, kind: str = "all", principal: auth.Principal = VIEW,
                  db: Session = Depends(get_db)):
    """A DRAFT schedule for every job waiting for a visit (planner.py). Nothing is booked."""
    if kind not in ("all", "inspection", "repair"):
        raise HTTPException(400, "kind is all, inspection or repair")
    # The 2026-10-01 contract: every board. POST /plans (2026-10-08) is AHS-only by default.
    return ai.build_plan(db, datetime.now(UTC), days=days, kind=kind, boards="all",
                         hidden=_hidden(db, principal), user_id=principal.user_id)


class PlanBody(BaseModel):
    days: int = Field(6, ge=1, le=18)
    kind: str = "all"
    mode: str = "most_jobs"
    boards: str = "ahs"
    include_unreached: bool = True


@router.post("/plans")
def dispatch_make_plan(body: PlanBody, principal: auth.Principal = VIEW,
                       db: Session = Depends(get_db)):
    """Remake the schedule (2026-10-08): a DRAFT plan for every job that needs a visit, as many
    as fit with the least driving, kept so the calendar and the Excel show this same plan.
    Nothing is booked and nothing reaches Zuper."""
    if body.kind not in ("all", "inspection", "repair"):
        raise HTTPException(400, "kind is all, inspection or repair")
    if body.mode not in planner.MODES:
        raise HTTPException(400, "mode is most_jobs or oldest_first")
    if body.boards not in ("ahs", "all"):
        raise HTTPException(400, "boards is ahs or all")
    return scheduling.make_plan(db, datetime.now(UTC), days=body.days, kind=body.kind,
                                mode=body.mode, boards=body.boards,
                                include_unreached=body.include_unreached,
                                hidden=_hidden(db, principal), user_id=principal.user_id,
                                source="page")


def _own_plan(db: Session, principal: auth.Principal, plan_id: int) -> DispatchPlan:
    """A plan is read by the person who made it and an ADMIN — it was made with the maker's
    boards; anyone else gets 404, never 403."""
    p = db.get(DispatchPlan, plan_id)
    if p is None or not p.result or (principal.role is not Role.ADMIN and
                                     p.created_by_id != principal.user_id):
        raise HTTPException(404, "no such plan")
    return p


@router.get("/plans/{plan_id}")
def dispatch_get_plan(plan_id: int, principal: auth.Principal = VIEW,
                      db: Session = Depends(get_db)):
    return _own_plan(db, principal, plan_id).result


@router.get("/plans/{plan_id}/schedule.xlsx")
def dispatch_plan_excel(plan_id: int, principal: auth.Principal = VIEW,
                        db: Session = Depends(get_db)):
    p = _own_plan(db, principal, plan_id)
    made = p.created_at.astimezone(c.TZ) if p.created_at.tzinfo else p.created_at
    name = "Schedule plan %d - %s.xlsx" % (p.id, made.strftime("%Y-%m-%d %H%M"))
    return StreamingResponse(io.BytesIO(plan_excel.workbook(p.result)), media_type=(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        headers={"Content-Disposition": 'attachment; filename="%s"' % name})


# ---- what each customer said about when they can have the visit (2026-10-08) -------------

def _waiting_visible(db: Session, principal: auth.Principal) -> dict[str, DispatchJob]:
    uids = {pj.uid for pj in scheduling.waiting_jobs(db, datetime.now(UTC), boards="all",
                                                     hidden=_hidden(db, principal),
                                                     use_saved=False)}
    return {j.job_uid: j for j in db.scalars(select(DispatchJob).where(
        DispatchJob.job_uid.in_(uids)))} if uids else {}


def _job_by_number(db: Session, principal: auth.Principal, number: str) -> DispatchJob:
    j = db.scalar(select(DispatchJob).where(DispatchJob.job_number == number.lstrip("#")))
    if j is None or (j.board and j.board in _hidden(db, principal)):
        raise HTTPException(404, "no such job")
    return j


@router.get("/availability")
def dispatch_availability(principal: auth.Principal = VIEW, db: Session = Depends(get_db)):
    """Every job that needs a visit, with what its customer said about WHEN — read from the
    calls and texts, or set by the office. A job not read yet says so."""
    jobs = _waiting_visible(db, principal)
    rows = {r.job_uid: r for r in db.scalars(select(DispatchAvailability).where(
        DispatchAvailability.job_uid.in_(list(jobs))))} if jobs else {}
    out = []
    for uid, j in sorted(jobs.items(), key=lambda x: int(x[1].job_number or 0)
                         if (x[1].job_number or "").isdigit() else 0):
        r = rows.get(uid)
        out.append(availability.payload(r, j) if r else {
            "job_uid": uid, "job_number": j.job_number, "customer": j.customer_name,
            "limits": {}, "in_words": "", "summary": None, "evidence": [], "confidence": None,
            "source": None, "newer_messages": False, "read_at": None, "error": None})
    return {"jobs": out}


class AvailabilityBody(BaseModel):
    limits: dict = Field(default_factory=dict)
    summary: str | None = Field(None, max_length=500)


@router.put("/availability/{job_number}")
def dispatch_set_availability(job_number: str, body: AvailabilityBody,
                              principal: auth.Principal = VIEW, db: Session = Depends(get_db)):
    """The office corrects what the AI read. It stands until the office clears it."""
    j = _job_by_number(db, principal, job_number)
    row = availability.office_set(db, j, body.limits, body.summary, principal.user_id,
                                  datetime.now(UTC))
    return availability.payload(row, j)


@router.delete("/availability/{job_number}")
def dispatch_clear_availability(job_number: str, principal: auth.Principal = VIEW,
                                db: Session = Depends(get_db)):
    """Drop the office's correction: the calls are read again on the next pass."""
    j = _job_by_number(db, principal, job_number)
    availability.office_clear(db, j)
    return {"cleared": True}


@router.post("/availability/refresh")
def dispatch_refresh_availability(principal: auth.Principal = VIEW,
                                  db: Session = Depends(get_db)):
    """Read now the customers whose calls and texts changed (a few at a time; the rest are
    read on the next passes). Counts against the AI's daily cap."""
    jobs = _waiting_visible(db, principal)
    return availability.refresh(db, datetime.now(UTC), limit=8, job_uids=set(jobs))


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


# ---- settings --------------------------------------------------------------------------------

def _settings_out(db: Session, admin: bool = True) -> dict:
    s = ai.settings(db)
    conns = db.scalars(select(AiConnection).order_by(AiConnection.name)).all()
    return {"ai_enabled": s.ai_enabled, "connection_id": s.connection_id, "model": s.model,
            "daily_cap": s.daily_cap, "runs_today": ai.runs_today(db, datetime.now(UTC)),
            "technicians": s.technicians or booking.DEFAULT_TECHNICIANS,
            "writes": writes.status(db),
            # The AI connections are ADMIN's (CLAUDE.md); a dispatcher sees none.
            "connections": [{"id": x.id, "name": x.name, "provider": x.provider,
                             "last4": x.api_key_last4} for x in conns] if admin else []}


@router.get("/settings")
def dispatch_get_settings(principal: auth.Principal = VIEW, db: Session = Depends(get_db)):
    return _settings_out(db, principal.role is Role.ADMIN)


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

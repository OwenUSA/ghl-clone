"""/api/dispatch — phase 3's routes (2026-10-01): "let the agent do it". Built, wired, OFF.

Same gate as the page (`api._viewer`: ADMIN + unrestricted DISPATCHER; a hidden board's job or
suggestion answers 404). The switches are an ADMIN's, with a typed confirmation to turn one on.
Whether anything may be written is decided in `writes.why_not` — the server gate
DISPATCH_ZUPER_WRITES, the Zuper sync, the master switch and that action's switch — and a
refusal answers 409 with that sentence, having sent nothing.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import auth
from ..db import get_db
from ..models import DispatchSuggestion, User
from . import ai, booking, writes
from .api import VIEW, _hidden, _suggestion
from .assist import ADMIN, _visible_job

router = APIRouter(prefix="/api/dispatch", tags=["dispatch"])


@router.get("/writes")
def dispatch_get_writes(principal: auth.Principal = VIEW, db: Session = Depends(get_db)):
    """The switches, the server gate, what may run now and why not, the last ten changes."""
    return writes.status(db)


class SwitchesIn(BaseModel):
    writes_enabled: bool | None = None
    write_fields: bool | None = None
    write_address: bool | None = None
    write_note: bool | None = None
    write_booking: bool | None = None
    write_stage: bool | None = None
    confirm: str | None = Field(default=None, max_length=40)


@router.put("/writes")
def dispatch_put_writes(body: SwitchesIn, principal: auth.Principal = ADMIN,
                        db: Session = Depends(get_db)):
    """ADMIN. Turning a switch ON needs `confirm: "TURN ON"`; turning one off never does.
    Every flip is a dispatch_write_log row. The server gate is not a switch here: it is the
    deployment's, and the answer says whether it is on."""
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items()
               if k != "confirm" and v is not None}
    try:
        writes.set_switches(db, principal.user_id, changes, body.confirm)
    except writes.Invalid as e:
        raise HTTPException(400, str(e)) from None
    db.commit()
    return writes.status(db)


def _done(ok: bool, sentence: str) -> dict:
    return {"ok": ok, "sentence": sentence}


class ApplyIn(BaseModel):
    # Approve and apply in one press ("Approve and let the agent do it"). Without it only an
    # already-approved suggestion is applied.
    approve: bool = False


@router.post("/suggestions/{sid}/apply")
def dispatch_suggestion_apply(sid: int, body: ApplyIn | None = None,
                              principal: auth.Principal = VIEW, db: Session = Depends(get_db)):
    """Make one confirmed suggestion in Zuper, read it back, record the result. Never retried
    by itself: a failure says what to check, and a person decides."""
    body = body or ApplyIn()
    s = db.get(DispatchSuggestion, sid, with_for_update=True)
    if s is None or (s.board and s.board in _hidden(db, principal)):
        raise HTTPException(404, "no such suggestion")
    if s.kind not in ("field", "address", "note"):
        raise HTTPException(400, "the agent cannot apply this kind of suggestion")
    if s.state == "open" and not body.approve:
        raise HTTPException(409, "approve this suggestion first")
    if s.state not in ("open", "approved"):
        raise HTTPException(409, "this suggestion is already %s" % s.state)
    try:
        writes.require(db, s.kind)
    except writes.Refused as e:
        raise HTTPException(409, str(e)) from None
    j = _visible_job(db, principal, s.job_uid)
    if s.state == "open":
        s.state, s.decided_at, s.decided_by_id = "approved", datetime.now(UTC), principal.user_id
    ok, sentence = writes.apply_suggestion(db, s, j, principal.user_id)
    db.commit()
    return {**_done(ok, sentence), "suggestion": _suggestion(s)}


class BookIn(BaseModel):
    start: datetime
    end: datetime
    technician: str = Field(min_length=1, max_length=120)


@router.post("/jobs/{job_uid}/book")
def dispatch_job_book(job_uid: str, body: BookIn, principal: auth.Principal = VIEW,
                      db: Session = Depends(get_db)):
    """Book a visit in Zuper — the job's scheduled start / end and its Technician field."""
    j = _visible_job(db, principal, job_uid)
    try:
        writes.require(db, "booking")
    except writes.Refused as e:
        raise HTTPException(409, str(e)) from None
    if body.start.tzinfo is None or body.end.tzinfo is None:
        raise HTTPException(400, "start and end need a timezone")
    if body.end <= body.start or body.end - body.start > timedelta(hours=12):
        raise HTTPException(400, "the visit must end after it starts, within 12 hours")
    if body.start < datetime.now(UTC):
        raise HTTPException(400, "that time has passed")
    names = {t["name"] for t in ai.settings(db).technicians or booking.DEFAULT_TECHNICIANS}
    if body.technician not in names:
        raise HTTPException(400, "not one of the technicians in Dispatch → Settings")
    if writes.closing(j.status):
        raise HTTPException(400, "job #%s is closed (%s)" % (j.job_number, j.status))
    ok, sentence = writes.book(db, j, body.start, body.end, body.technician, principal.user_id)
    db.commit()
    return _done(ok, sentence)


class StageIn(BaseModel):
    status_name: str = Field(min_length=1, max_length=120)


@router.post("/jobs/{job_uid}/stage")
def dispatch_job_stage(job_uid: str, body: StageIn, principal: auth.Principal = VIEW,
                       db: Session = Depends(get_db)):
    """Move a job to another stage on ITS OWN board. Never a closing stage (Paid, Cancelled,
    Estimate Declined, Review Received, or one Zuper types as completed / cancelled), never a
    closed job, never another board."""
    j = _visible_job(db, principal, job_uid)
    try:
        writes.require(db, "stage")
    except writes.Refused as e:
        raise HTTPException(409, str(e)) from None
    user = db.get(User, principal.user_id) if principal.user_id else None
    try:
        ok, sentence = writes.move_stage(db, j, body.status_name.strip(), principal.user_id,
                                         user.name if user else None)
    except writes.Invalid as e:
        db.commit()                                  # the refusal is logged
        raise HTTPException(400, str(e)) from None
    db.commit()
    return _done(ok, sentence)

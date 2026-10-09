"""/api/reminders — appointment reminder texts from Zuper (2026-10-08).

The Dispatch audience (ADMIN + unrestricted DISPATCHER; a technician or a restricted user is
403) reads the status, the upcoming plan, the log and a customer's language, and may set a
customer's language. Only an ADMIN changes the settings, and turning reminders ON — test or on —
needs `confirm: "TURN ON"`; off never does. Every change is a `reminder_switch_log` row. A board
hidden from the reader (pipeline permissions) is left out of the plan and the log.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth
from ..db import get_db
from ..dispatch.api import VIEW, _hidden
from ..dispatch.assist import ADMIN
from ..models import AppointmentReminder, ReminderSwitchLog
from ..number_threads import phone_key
from . import config as c
from . import language, rules, service

router = APIRouter(prefix="/api/reminders", tags=["reminders"])


def _iso(dt) -> str | None:
    dt = rules.aware(dt)
    return dt.isoformat() if dt else None


def _status(db: Session) -> dict:
    s = service.read_settings(db)
    templates = {k: {lang: service.template(s, k, lang) for lang in c.LANGUAGES}
                 for k in c.KINDS}
    gate = c.enabled()
    if not gate:
        why = ("The server gate ZUPER_REMINDERS_ENABLED is not set on this deployment, so no "
               "reminder is sent whatever the mode says.")
    elif s.mode == "off":
        why = "Off. No reminder is sent."
    elif s.mode == "test":
        why = "Test: only the test numbers are texted; every other reminder is recorded."
    else:
        why = "On: customers are texted."
    log = db.scalars(select(ReminderSwitchLog).order_by(ReminderSwitchLog.id.desc()).limit(10))
    return {
        "server_gate": gate, "mode": s.mode, "sending": gate and s.mode in ("test", "on"),
        "sentence": why, "test_numbers": s.test_numbers or [], "templates": templates,
        "default_templates": c.DEFAULT_TEMPLATES,
        "columns": {b: list(cols) for b, cols in c.COLUMNS.items()},
        "times": {"day_before_from": c.DAY_BEFORE_FROM.strftime("%H:%M"),
                  "quiet_from": c.QUIET_FROM.strftime("%H:%M"),
                  "quiet_until": c.QUIET_UNTIL.strftime("%H:%M"),
                  "four_hour": c.FOUR_HOUR_HOURS, "four_hour_latest": c.FOUR_HOUR_LATEST_HOURS},
        "heartbeat": {"last_run_at": _iso(s.last_run_at),
                      "last_success_at": _iso(s.last_success_at),
                      "last_error": s.last_error, "last_error_at": _iso(s.last_error_at),
                      "last_counts": s.last_counts},
        "changes": [{"at": _iso(r.at), "user_id": r.user_id, "field": r.field,
                     "old": r.old_value, "new": r.new_value} for r in log],
    }


@router.get("")
def reminders_status(principal: auth.Principal = VIEW, db: Session = Depends(get_db)):
    """The mode, the server gate, the wording, the heartbeat, the last ten changes."""
    return _status(db)


@router.get("/upcoming")
def reminders_upcoming(principal: auth.Principal = VIEW, db: Session = Depends(get_db)):
    """Every visit today and tomorrow on a reminder board and what happens to it. Read only."""
    hidden = _hidden(db, principal)
    return {"visits": [v for v in service.plan(db) if v["board"] not in hidden]}


@router.get("/log")
def reminders_log(principal: auth.Principal = VIEW, db: Session = Depends(get_db)):
    """The last 200 reminder decisions — sent, refused, skipped, would have sent."""
    hidden = _hidden(db, principal)
    rows = db.scalars(select(AppointmentReminder).order_by(
        AppointmentReminder.id.desc()).limit(200))
    return {"reminders": [
        {"id": r.id, "job_uid": r.job_uid, "job_number": r.job_number, "board": r.board,
         "status": r.status, "kind": r.kind, "visit_start": _iso(r.visit_start),
         "phone": r.phone, "language": r.language, "state": r.state, "reason": r.reason,
         "body": r.body, "contact_id": r.contact_id, "created_at": _iso(r.created_at)}
        for r in rows if r.board not in hidden]}


class SettingsIn(BaseModel):
    mode: str | None = None
    test_numbers: list[str] | None = Field(default=None, max_length=20)
    templates: dict[str, dict[str, str | None]] | None = None
    confirm: str | None = Field(default=None, max_length=40)


def _log(db: Session, user_id, field: str, old, new) -> None:
    def text(v):
        return v if isinstance(v, str) or v is None else json.dumps(v, ensure_ascii=False)
    db.add(ReminderSwitchLog(user_id=user_id, field=field, old_value=text(old),
                             new_value=text(new)))


@router.put("/settings")
def reminders_put_settings(body: SettingsIn, principal: auth.Principal = ADMIN,
                           db: Session = Depends(get_db)):
    """ADMIN. Test or On needs `confirm: "TURN ON"`; Off is one press."""
    s = service.settings(db)
    now = datetime.now(UTC)
    if body.mode is not None:
        if body.mode not in c.MODES:
            raise HTTPException(400, "mode must be off, test or on")
        if body.mode != "off" and body.mode != s.mode and body.confirm != c.CONFIRM:
            raise HTTPException(400, 'Type "TURN ON" to switch reminders to %s.' % body.mode)
        if body.mode != s.mode:
            _log(db, principal.user_id, "mode", s.mode, body.mode)
            s.mode = body.mode
    if body.test_numbers is not None:
        keys = []
        for n in body.test_numbers:
            k = phone_key(n)
            if not k:
                raise HTTPException(400, "%r is not a ten-digit phone number" % n)
            keys.append(k)
        keys = sorted(set(keys))
        if keys != sorted(s.test_numbers or []):
            _log(db, principal.user_id, "test_numbers", s.test_numbers or [], keys)
            s.test_numbers = keys or None
    if body.templates is not None:
        merged = {k: dict(v) for k, v in (s.templates or {}).items()}
        for kind, langs in body.templates.items():
            if kind not in c.KINDS:
                raise HTTPException(400, "unknown reminder %r" % kind)
            for lang, text in langs.items():
                if lang not in c.LANGUAGES:
                    raise HTTPException(400, "unknown language %r" % lang)
                text = (text or "").strip()
                if text and len(text) > 600:
                    raise HTTPException(400, "a reminder is at most 600 characters")
                if text and "{window}" not in text and "{time}" not in text:
                    raise HTTPException(400, "the wording must keep {window} (or {time})")
                old = service.template(s, kind, lang)
                if not text or text == c.DEFAULT_TEMPLATES[kind][lang]:
                    merged.get(kind, {}).pop(lang, None)
                else:
                    merged.setdefault(kind, {})[lang] = text
                new = text or c.DEFAULT_TEMPLATES[kind][lang]
                if new != old:
                    _log(db, principal.user_id, "template:%s:%s" % (kind, lang), old, new)
        s.templates = {k: v for k, v in merged.items() if v} or None
    s.updated_at, s.updated_by = now, principal.user_id
    db.commit()
    return _status(db)


def _key(phone: str) -> str:
    k = phone_key(phone)
    if not k:
        raise HTTPException(400, "a ten-digit phone number is required")
    return k


@router.get("/language")
def reminders_get_language(phone: str, principal: auth.Principal = VIEW,
                           db: Session = Depends(get_db)):
    """The language this number's reminders go out in, and the evidence. Read only."""
    return language.describe(db, _key(phone))


class LanguageIn(BaseModel):
    phone: str
    language: str | None = None       # "en" | "es" | null = work it out again


@router.put("/language")
def reminders_put_language(body: LanguageIn, principal: auth.Principal = VIEW,
                           db: Session = Depends(get_db)):
    """A person's choice wins over the detection, for good, until set back to automatic."""
    if body.language is not None and body.language not in c.LANGUAGES:
        raise HTTPException(400, "language must be en, es or null")
    out = language.set_manual(db, _key(body.phone), body.language, principal.user_id)
    db.commit()
    return out

"""/api/ai/registry — registered ON the AI Agents router (`ai/api.py` imports this module last),
so its access rule and its route enumeration in tests/test_ai_permissions.py apply: ADMIN and
unrestricted DISPATCHER read; only an ADMIN changes anything."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import auth
from ..ai.api import ADMIN, VIEW, router
from ..db import get_db
from . import service


@router.get("/registry")
def registry_overview(db: Session = Depends(get_db), _: auth.Principal = VIEW):
    """Every agent and automation, its scope card, levels, status, spend and health."""
    return service.overview(db, datetime.now(UTC))


@router.get("/registry/activity")
def registry_activity(key: str | None = None, limit: int = Query(100, ge=1, le=500),
                      db: Session = Depends(get_db), _: auth.Principal = VIEW):
    return {"items": service.activity(db, key=key or None, limit=limit)}


class EntryIn(BaseModel):
    role: int | None = None
    owner_user_id: int | None = None
    is_test: bool | None = None
    daily_cap_usd: float | str | None = None
    notes: str | None = Field(None, max_length=2000)


@router.patch("/registry/entries/{key}")
def registry_update_entry(key: str, body: EntryIn, db: Session = Depends(get_db),
                          principal: auth.Principal = ADMIN):
    try:
        service.update_entry(db, key, body.model_dump(exclude_unset=True),
                             user_id=principal.user_id)
    except service.Invalid as e:
        db.rollback()
        raise HTTPException(400, str(e)) from None
    db.commit()
    v = service.view(db, key)
    return service.entry_out(db, v, datetime.now(UTC))


class LevelIn(BaseModel):
    level: str
    reason: str | None = Field(None, max_length=1000)
    confirm: str | None = None


@router.put("/registry/entries/{key}/actions/{action}")
def registry_set_level(key: str, action: str, body: LevelIn, db: Session = Depends(get_db),
                       principal: auth.Principal = ADMIN):
    try:
        service.set_level(db, key, action, body.level, user_id=principal.user_id,
                          reason=body.reason, confirm=body.confirm)
    except service.Invalid as e:
        db.rollback()
        raise HTTPException(400, str(e)) from None
    db.commit()
    return service.entry_out(db, service.view(db, key), datetime.now(UTC))


class OverallIn(BaseModel):
    daily_cap_usd: float | str


@router.put("/registry/overall")
def registry_set_overall(body: OverallIn, db: Session = Depends(get_db),
                         principal: auth.Principal = ADMIN):
    try:
        service.set_overall_cap(db, body.daily_cap_usd, user_id=principal.user_id)
    except service.Invalid as e:
        db.rollback()
        raise HTTPException(400, str(e)) from None
    db.commit()
    return service.overview(db, datetime.now(UTC))["overall"]

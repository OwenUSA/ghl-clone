"""Zuper routes: the webhook, Settings → Zuper, the money panels and Zuper's job photos.

Mounted by `main.py` as one router, under the app-level auth gate — except the webhook,
which carries no user credential and is on `auth.EXEMPT`; it authenticates with its own
shared token (see `webhooks.py`).

## Who sees what

* **Settings → Zuper is ADMIN** (status, switch, cutover date, setup check, confirmations,
  conflict log, delete log, Restore).
* **Money panels and Zuper photos: any signed-in role, on a card they can see.** The card is
  resolved through `pipeline_access.get_opportunity`, so a hidden pipeline or — with "Only
  assigned data" on — somebody else's job is the same 404 as a missing one. The contact
  panel lists only documents on the contact's cards the reader can see.
* **Nothing here asks Zuper anything unless the sync is armed**, and the panels read the
  cache (`zuper_documents`), so opening a card never waits on Zuper.
"""
from __future__ import annotations

import threading
import time
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import assigned_access, auth, pipeline_access
from ..db import get_db
from ..models import (
    Job,
    Opportunity,
    ZuperConflict,
    ZuperDeleteSnapshot,
    ZuperDocument,
    ZuperMapping,
    ZuperWebhookDelivery,
)
from . import client, config, engine, listener, mapping, setup, webhooks, zapi

router = APIRouter(tags=["zuper"])

WEBHOOK_PATH = "/api/zuper/webhook"
NO_WEBHOOK_TOKEN = ("No webhook token is configured on this deployment (ZUPER_WEBHOOK_TOKEN), "
                    "so no Zuper delivery is accepted.")
UNAVAILABLE = "Zuper photos are unavailable right now"


def _iso(dt: datetime | None) -> str | None:
    return mapping.aware(dt).isoformat() if dt else None


def sync_label(db: Session) -> str:
    if not config.env_enabled() or not config.key_set():
        return "off"
    return "on" if config.armed(db) else "paused"


# ------------------------------------------------------------------ the webhook

@router.post(WEBHOOK_PATH, status_code=202)
async def zuper_webhook(request: Request, db: Session = Depends(get_db)):
    """Stored raw, processed later. 503 while the sync is off; 401 (nothing stored) for a
    wrong token or a signature that does not verify."""
    if not config.env_enabled():
        raise HTTPException(503, config.OFF_SENTENCE)
    if not config.webhook_token():
        raise HTTPException(503, NO_WEBHOOK_TOKEN)
    sent = request.headers.get(webhooks.TOKEN_HEADER) or request.query_params.get("token")
    if not webhooks.token_ok(sent):
        raise HTTPException(401, "bad or missing webhook token")
    body = await request.body()
    if len(body) > webhooks.MAX_BODY_BYTES:
        raise HTTPException(413, "delivery too large")
    signature = webhooks.signature_state(request.headers, body)
    if signature == "invalid":
        raise HTTPException(401, "the delivery's signature does not verify")
    engine.mark_quiet(db)
    new, _ = webhooks.store(db, body, request.headers.get("content-type"), signature)
    return {"received": True, "duplicate": not new}


# ------------------------------------------------------------------ Settings → Zuper

def _setup_block(db: Session) -> dict:
    row = config.peek_settings(db)
    confirmed = (row.confirmations or {}) if row else {}
    results = []
    for r in (row.setup_results or []) if row else []:
        results.append({**r, "confirmed": confirmed.get(r["key"])
                        if r["state"] == setup.BY_HAND else None})
    return {"checked_at": _iso(row.setup_checked_at) if row else None,
            "passed": bool(row and row.setup_passed), "results": results}


@router.get("/api/zuper/status")
def zuper_status(db: Session = Depends(get_db), _: auth.Principal = auth.ADMIN):
    row = config.peek_settings(db)
    state = db.get(engine.ZuperSyncState, 1)
    counts: dict[str, dict[str, int]] = {}
    for crm_type, st, n in db.execute(select(ZuperMapping.crm_type, ZuperMapping.state,
                                             func.count(ZuperMapping.id)).group_by(
            ZuperMapping.crm_type, ZuperMapping.state)).all():
        counts.setdefault(crm_type, {})[st] = n
    queue = dict(db.execute(select(Job.status, func.count(Job.id)).where(
        Job.type.in_(listener.JOB_TYPES)).group_by(Job.status)).all())
    failures = [{"id": j.id, "type": j.type, "error": j.last_error,
                 "at": _iso(j.created_at)} for j in db.scalars(
        select(Job).where(Job.type.in_(listener.JOB_TYPES), Job.status == "failed")
        .order_by(Job.id.desc()).limit(10)).all()]
    inbox = dict(db.execute(select(ZuperWebhookDelivery.status,
                                   func.count(ZuperWebhookDelivery.id)).group_by(
        ZuperWebhookDelivery.status)).all())
    recent = [{"id": d.id, "received_at": _iso(d.received_at), "event": d.event,
               "module": d.module, "record_uid": d.record_uid, "status": d.status,
               "outcome": d.outcome, "error": d.error, "signature": d.signature}
              for d in db.scalars(select(ZuperWebhookDelivery).order_by(
                  ZuperWebhookDelivery.id.desc()).limit(10)).all()]
    heartbeat = None
    if state is not None:
        heartbeat = {
            "last_sweep_started_at": _iso(state.last_sweep_started_at),
            "last_sweep_finished_at": _iso(state.last_sweep_finished_at),
            "last_sweep_success_at": _iso(state.last_sweep_success_at),
            "sweep_cursor": _iso(state.sweep_cursor), "filter_checks": state.filter_checks,
            "last_push_at": _iso(state.last_push_at), "last_pull_at": _iso(state.last_pull_at),
            "last_webhook_at": _iso(state.last_webhook_at), "last_error": state.last_error,
            "last_error_at": _iso(state.last_error_at), "last_counts": state.last_counts,
            "last_digest_day": state.last_digest_day}
    return {
        "config": config.config_summary(), "sync": sync_label(db),
        "enabled": bool(row and row.enabled), "enabled_at": _iso(row.enabled_at) if row else None,
        "workiz_cutover_date": row.workiz_cutover_date if row else None,
        "before_cutover": config.before_cutover(db),
        "setup": _setup_block(db), "blockers": setup.blockers(db),
        "heartbeat": heartbeat, "counts": counts,
        "queue": {"by_status": queue, "recent_failures": failures},
        "inbox": {"by_status": inbox, "recent": recent},
        "load_report": state.load_report if state else None,
        "conflicts_total": db.scalar(select(func.count(ZuperConflict.id))) or 0,
        "deletes_total": db.scalar(select(func.count(ZuperDeleteSnapshot.id))) or 0,
    }


class SettingsIn(BaseModel):
    enabled: bool | None = None
    workiz_cutover_date: str | None = None


@router.put("/api/zuper/settings")
def zuper_update_settings(body: SettingsIn, db: Session = Depends(get_db),
                          principal: auth.Principal = auth.ADMIN):
    data = body.model_dump(exclude_unset=True)
    engine.mark_quiet(db)
    if "workiz_cutover_date" in data:
        raw = (data["workiz_cutover_date"] or "").strip()
        if raw:
            try:
                date.fromisoformat(raw)
            except ValueError:
                raise HTTPException(400, "the Workiz cutover date must be a date "
                                         "(YYYY-MM-DD)") from None
    if data.get("enabled"):
        why = setup.blockers(db)
        if why:
            raise HTTPException(409, "The sync cannot be switched on yet: " + " ".join(why))
    row = config.settings(db)
    if "workiz_cutover_date" in data:
        row.workiz_cutover_date = (data["workiz_cutover_date"] or "").strip() or None
    if "enabled" in data and data["enabled"] is not None:
        row.enabled = bool(data["enabled"])
        if row.enabled and row.enabled_at is None:
            row.enabled_at = datetime.now(UTC)
    row.updated_at = datetime.now(UTC)
    row.updated_by_id = principal.user_id
    db.commit()
    return zuper_status(db, principal)


@router.post("/api/zuper/setup/check")
def zuper_run_setup_check(db: Session = Depends(get_db),
                          principal: auth.Principal = auth.ADMIN):
    """Read-only GETs against Zuper; the results are recorded for the enable gate."""
    if not config.env_enabled():
        raise HTTPException(409, config.OFF_SENTENCE)
    if not config.key_set():
        raise HTTPException(409, config.NO_KEY_SENTENCE)
    engine.mark_quiet(db)
    results = setup.run_checks(db, commit=True)
    setup.record_results(db, results)
    if not config.peek_settings(db).setup_passed:
        config.settings(db).enabled = False
    db.commit()
    return _setup_block(db)


class ConfirmIn(BaseModel):
    confirmed: bool


@router.put("/api/zuper/setup/confirmations/{item_key}")
def zuper_confirm_setup_item(item_key: str, body: ConfirmIn, db: Session = Depends(get_db),
                             principal: auth.Principal = auth.ADMIN):
    row = config.peek_settings(db)
    items = {r["key"] for r in (row.setup_results or []) if r["state"] == setup.BY_HAND} \
        if row else set()
    if item_key not in items:
        raise HTTPException(404, "no such item to confirm by hand")
    engine.mark_quiet(db)
    confirmations = dict(row.confirmations or {})
    if body.confirmed:
        confirmations[item_key] = {"by": principal.email,
                                   "at": datetime.now(UTC).isoformat()}
    else:
        confirmations.pop(item_key, None)
        row.enabled = False           # an un-ticked safety item pauses the sync at once
    row.confirmations = confirmations
    db.commit()
    return _setup_block(db)


def _label(crm_type: str, record: dict | None) -> str | None:
    if not record:
        return None
    if crm_type == "contact":
        return (("%s %s" % (record.get("first_name") or "", record.get("last_name") or ""))
                .strip() or None)
    return record.get("title") or ((record.get("body") or "")[:80] or None)


@router.get("/api/zuper/conflicts")
def zuper_conflicts(page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                    db: Session = Depends(get_db), _: auth.Principal = auth.ADMIN):
    total = db.scalar(select(func.count(ZuperConflict.id))) or 0
    rows = db.scalars(select(ZuperConflict).order_by(ZuperConflict.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return {"total": total, "page": page, "page_size": page_size, "items": [
        {"id": r.id, "occurred_at": _iso(r.occurred_at), "crm_type": r.crm_type,
         "crm_id": r.crm_id, "zuper_uid": r.zuper_uid, "field": r.field, "rule": r.rule,
         "winner": r.winner, "written_to": r.written_to, "before": r.before, "after": r.after}
        for r in rows]}


@router.get("/api/zuper/deletes")
def zuper_deletes(page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                  db: Session = Depends(get_db), _: auth.Principal = auth.ADMIN):
    total = db.scalar(select(func.count(ZuperDeleteSnapshot.id))) or 0
    rows = db.scalars(select(ZuperDeleteSnapshot).order_by(ZuperDeleteSnapshot.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return {"total": total, "page": page, "page_size": page_size, "items": [
        {"id": r.id, "batch": r.batch, "occurred_at": _iso(r.occurred_at),
         "direction": r.direction, "crm_type": r.crm_type, "crm_id": r.crm_id,
         "zuper_uid": r.zuper_uid, "state": r.state, "error": r.error,
         "label": _label(r.crm_type, (r.snapshot or {}).get("record")),
         "restored_at": _iso(r.restored_at), "restore_detail": r.restore_detail,
         "restorable": r.state in ("mirrored", "skipped", "restore_failed")}
        for r in rows]}


@router.post("/api/zuper/deletes/{snapshot_id}/restore")
def zuper_restore_delete(snapshot_id: int, db: Session = Depends(get_db),
                         principal: auth.Principal = auth.ADMIN):
    row = db.get(ZuperDeleteSnapshot, snapshot_id)
    if row is None:
        raise HTTPException(404, "snapshot not found")
    if row.state not in ("mirrored", "skipped", "restore_failed"):
        raise HTTPException(409, "this delete has not been mirrored yet, or is already "
                                 "restored")
    return engine.restore(db, snapshot_id, principal.user_id)


# ------------------------------------------------------------------ money panels

def _doc_out(d: ZuperDocument) -> dict:
    return {"id": d.zuper_uid, "kind": d.kind, "number": d.number, "status": d.status,
            "total_cents": d.total_cents, "balance_cents": d.balance_cents,
            "issued_on": d.issued_on, "due_on": d.due_on, "updated_at": d.zuper_updated_at,
            "opportunity_id": d.opportunity_id}


@router.get("/api/opportunities/{opp_id}/zuper")
def zuper_opportunity_panel(opp_id: int, db: Session = Depends(get_db),
                            principal: auth.Principal = auth.ANY_USER):
    """Quotes and invoices for this card, as Zuper last reported them. Read-only."""
    o = pipeline_access.get_opportunity(db, principal, opp_id)
    m = mapping.mapping_for(db, "opportunity", o.id)
    linked = m is not None and m.state == "linked" and bool(m.zuper_uid)
    docs = db.scalars(select(ZuperDocument).where(
        ZuperDocument.opportunity_id == o.id, ZuperDocument.removed_in_zuper.is_(False))
        .order_by(ZuperDocument.issued_on.desc(), ZuperDocument.id.desc())).all()
    invoices = [d for d in docs if d.kind == ZuperDocument.INVOICE]
    quotes = [d for d in docs if d.kind == ZuperDocument.QUOTE]
    value_source = None
    if linked and mapping.documents_value(db, m.zuper_uid) is not None:
        value_source = "invoices" if any(
            (d.status or "").upper() not in mapping.INVOICE_VOID for d in invoices) \
            else "approved_quotes"
    return {"sync": sync_label(db), "linked": linked,
            "last_synced_at": _iso(max(filter(None, (m.last_pushed_at, m.last_pulled_at)),
                                       default=None)) if m else None,
            "quotes": [_doc_out(d) for d in quotes], "invoices": [_doc_out(d) for d in invoices],
            "value_source": value_source}


@router.get("/api/contacts/{contact_id}/zuper")
def zuper_contact_panel(contact_id: int, db: Session = Depends(get_db),
                        principal: auth.Principal = auth.ANY_USER):
    """Quotes and invoices on this customer's cards that the reader can see."""
    assigned_access.get_contact(db, principal, contact_id)
    scope = assigned_access.scope(db, principal)
    visible = select(Opportunity.id).where(Opportunity.contact_id == contact_id)
    visible = assigned_access.opportunities(visible, scope)
    rows = db.execute(select(ZuperDocument, Opportunity.title).join(
        Opportunity, Opportunity.id == ZuperDocument.opportunity_id).where(
        ZuperDocument.opportunity_id.in_(visible), ZuperDocument.removed_in_zuper.is_(False))
        .order_by(ZuperDocument.issued_on.desc(), ZuperDocument.id.desc())).all()
    m = mapping.mapping_for(db, "contact", contact_id)
    return {"sync": sync_label(db), "linked": m is not None and m.state == "linked",
            "documents": [{**_doc_out(d), "opportunity_title": title} for d, title in rows]}


# ------------------------------------------------------------------ Zuper's job photos

_cache: dict[str, tuple[float, list[dict]]] = {}
_cache_lock = threading.Lock()
LIST_CACHE_SECONDS = 120.0


def _attachments(job_uid: str, refresh: bool) -> list[dict]:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(job_uid)
    if hit and hit[0] > now and not refresh:
        return hit[1]
    rows = zapi.job_attachments(job_uid)
    with _cache_lock:
        _cache[job_uid] = (now + LIST_CACHE_SECONDS, rows)
    return rows


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _att_id(rec: dict) -> str | None:
    value = rec.get("attachment_uid") or rec.get("uid") or rec.get("file_uid")
    return str(value) if value else None


def _att_url(rec: dict) -> str | None:
    # A note's picture carries `attachment`, the attachments module `attachment_path`
    # (both measured live, 2026-09-24); the others are the documented names.
    return (rec.get("attachment") or rec.get("attachment_path") or rec.get("url")
            or rec.get("file_url") or rec.get("attachment_url"))


def _internal(rec: dict) -> bool:
    """A picture on a PRIVATE note, or one Zuper marks INTERNAL, is staff-only here too: the
    CRM already keeps internal notes from a technician (2026-09-10), and a photo filed on a
    note the office kept private is the same kind of thing."""
    return (str(rec.get("attachment_visibility") or "").upper() == "INTERNAL"
            or bool(rec.get("is_private")))


def _att_type(rec: dict) -> str:
    mime = str(rec.get("mime_type") or rec.get("file_type") or rec.get("content_type")
               or "").lower()
    if "/" in mime:
        return mime
    # A note's picture says only `attachment_type: "IMAGE"`; the name carries the rest.
    return ""


def _job_for(db: Session, principal: auth.Principal, opp_id: int) -> str | None:
    o = pipeline_access.get_opportunity(db, principal, opp_id)
    m = mapping.mapping_for(db, "opportunity", o.id)
    return m.zuper_uid if m is not None and m.state == "linked" else None


@router.get("/api/opportunities/{opp_id}/zuper/attachments")
def zuper_opportunity_attachments(opp_id: int, refresh: bool = False,
                                  db: Session = Depends(get_db),
                                  principal: auth.Principal = auth.ANY_USER):
    job_uid = _job_for(db, principal, opp_id)
    if sync_label(db) != "on":
        return {"state": "off", "message": None, "attachments": []}
    if not job_uid:
        return {"state": "not_linked", "message": None, "attachments": []}
    try:
        rows = _attachments(job_uid, refresh)
    except client.ZuperError:
        return {"state": "unavailable", "message": UNAVAILABLE, "attachments": []}
    out = []
    staff = auth.sees_internal(principal)
    for rec in rows:
        aid = _att_id(rec)
        if not aid or not _att_url(rec):
            continue
        if _internal(rec) and not staff:
            continue
        ctype = _att_type(rec)
        name = rec.get("file_name") or rec.get("attachment_name") or rec.get("name")
        out.append({"id": aid, "name": name, "content_type": ctype or None,
                    "is_image": ctype.startswith("image/") or str(name or "").lower().endswith(
                        (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic")),
                    "created_at": rec.get("created_at"),
                    "url": "/api/opportunities/%d/zuper/attachments/%s" % (opp_id, aid)})
    return {"state": "ok", "message": None, "attachments": out}


@router.get("/api/opportunities/{opp_id}/zuper/attachments/{attachment_id}")
def zuper_attachment_file(opp_id: int, attachment_id: str, db: Session = Depends(get_db),
                          principal: auth.Principal = auth.ANY_USER) -> Response:
    """The file, relayed: the browser never sees Zuper's URL or the key."""
    job_uid = _job_for(db, principal, opp_id)
    if sync_label(db) != "on" or not job_uid:
        raise HTTPException(404, "attachment not found")
    try:
        rec = next((r for r in _attachments(job_uid, False) if _att_id(r) == attachment_id),
                   None)
        if rec is None or (_internal(rec) and not auth.sees_internal(principal)):
            raise HTTPException(404, "attachment not found")
        data, ctype = client.fetch_file(_att_url(rec))
    except client.ZuperError as exc:
        raise HTTPException(404 if exc.kind == "not_found" else 502, UNAVAILABLE) from None
    headers = {"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"}
    if ctype not in client.INLINE_TYPES:
        # Never render something else from our own origin (an HTML or SVG file is a script).
        ctype = "application/octet-stream"
        headers["Content-Disposition"] = "attachment"
    return Response(content=data, media_type=ctype, headers=headers)


# ------------------------------------------------------------------ Send to Zuper (2026-09-16)

SEND_JOB = "zuper_send"


def may_send(principal: auth.Principal | None) -> bool:
    """ADMIN or DISPATCHER, never with "Only assigned data" on. Agents only suggest."""
    from ..models import Role
    return principal is not None and principal.role in (Role.ADMIN, Role.DISPATCHER) \
        and not assigned_access.restricted(principal)


def job_url(job_uid: str | None) -> str | None:
    template = config.job_url_template()
    return template.format(uid=job_uid) if job_uid and template else None


def send_state(m: ZuperMapping | None) -> str:
    if m is None or m.state == "deleted":
        return "not_sent"
    if m.state == "linked" and m.zuper_uid:
        return "sent"
    if m.state == "failed":
        return "failed"
    return "queued"


def send_block(db: Session, o: Opportunity, principal: auth.Principal | None) -> dict:
    """The card's Zuper state for the modal: sent or not, the locks, and whether this reader
    may press Send to Zuper now — with the reasons when not."""
    from . import locks
    m = mapping.mapping_for(db, "opportunity", o.id)
    state = send_state(m)
    problems: list[str] = []
    if state in ("not_sent", "failed"):
        if config.pull_only():
            problems.append(config.PULL_ONLY_SENTENCE)
        if not config.armed(db):
            problems.append("The Zuper sync is not switched on (Settings → Zuper), so nothing "
                            "can be sent yet.")
        problems.extend(engine.send_problems(db, o))
    locked = state in ("queued", "sent")
    return {
        "state": state, "job_uid": m.zuper_uid if m is not None else None,
        "job_url": job_url(m.zuper_uid) if state == "sent" else None,
        "sent_at": _iso(m.created_at) if locked and m is not None else None,
        "error": m.last_error if state == "failed" and m is not None else None,
        "locked_fields": [*locks.OPPORTUNITY_FIELDS, locks.APPOINTMENTS] if locked else [],
        "may_send": may_send(principal),
        "can_send": may_send(principal) and state in ("not_sent", "failed") and not problems,
        "send_problems": problems,
    }


def queue_send(db: Session, o: Opportunity, *, by: str) -> tuple[bool, str]:
    """Mark the card sent (it is a locked mirror from this moment) and queue the worker's
    send. (queued now?, state). A card already queued or sent queues nothing — a second press,
    or a second approval, does nothing. The caller has checked who may send; this checks the
    sync and the card, and raises 409 with the sentences."""
    from .. import queue
    from ..models import Job
    m = mapping.mapping_for(db, "opportunity", o.id)
    state = send_state(m)
    if state in ("queued", "sent"):
        return False, state
    why = []
    if config.pull_only():
        why.append(config.PULL_ONLY_SENTENCE)
    if not config.armed(db):
        why.append("The Zuper sync is not switched on (Settings → Zuper), so nothing can be "
                   "sent yet.")
    why.extend(engine.send_problems(db, o))
    if why:
        raise HTTPException(409, " ".join(why))
    if m is None:
        m = ZuperMapping(crm_type="opportunity", crm_id=o.id, zuper_type="job")
        db.add(m)
    m.state, m.last_error, m.updated_at = "queued", None, datetime.now(UTC)
    if m.zuper_uid is None:
        m.created_at = datetime.now(UTC)
    old = db.scalar(select(Job).where(Job.dedupe_key == "zuper:send:%d" % o.id))
    if old is not None and old.status != "pending":
        old.dedupe_key = None
    db.flush()
    queue.enqueue(db, SEND_JOB, {"opportunity_id": o.id, "by": by[:120]},
                  dedupe_key="zuper:send:%d" % o.id)
    return True, "queued"


@router.post("/api/opportunities/{opp_id}/zuper/send")
def zuper_send_opportunity(opp_id: int, response: Response, db: Session = Depends(get_db),
                           principal: auth.Principal = auth.ANY_USER):
    """Send to Zuper (ADMIN / DISPATCHER; never a technician, never "Only assigned data")."""
    if not may_send(principal):
        raise HTTPException(403, "Only an admin or a dispatcher can send a card to Zuper.")
    o = pipeline_access.get_opportunity(db, principal, opp_id)
    queued, state = queue_send(db, o, by=principal.email)
    db.commit()
    if not queued:
        return {"state": state, "already": True}
    response.status_code = 202
    return {"state": state}


def auto_send(db: Session, o: Opportunity, *, by: str) -> str:
    """An AHS email card (and a new Workiz AHS job, through the sweep): sent automatically
    when the sync is armed and the card has what a send needs. With the sync off nothing is
    queued — the card can be sent by hand later. Never raises into the caller."""
    if not config.armed(db):
        return "sync off: not sent"
    try:
        queued, state = queue_send(db, o, by=by)
    except HTTPException as exc:
        return "not sent: %s" % exc.detail
    return state if queued else "already " + state

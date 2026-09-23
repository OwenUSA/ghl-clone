"""The 15-minute sweep: whatever the webhooks and the queue missed, both directions.

**Zuper -> CRM.** For customers, jobs, appointments, quotes and invoices it reads the records
updated since the last sweep started (minus a 5-minute overlap) with
`filter.updated_at_from`. That filter was reported to return UNFILTERED results on some lists
(zuper-research.md), so each sweep first asks every list for records updated after a moment
in the FUTURE: a list that still answers with records does not honour the filter, and is
paged whole and compared on `updated_at` here instead (recorded per list in
`zuper_sync_state.filter_checks`). Deleted customers and jobs are asked for with
`filter.is_deleted=true` the same way.

**CRM -> Zuper.** Every record of a SENT job (2026-09-16, v2) — the card, its customer, visits,
notes and tasks — is read as its sync view and hashed; a record whose hash differs from the one
last synced is pushed, and a new note or task on a sent card is created. Before the Workiz
cutover, a new Workiz job that qualifies is sent (`_send_new_workiz_jobs`). That is what
carries a Workiz import (a quiet session) or a change made while the switch was off. A mapped
record that no longer exists in the CRM is deleted in Zuper, with what the mapping knows as its
snapshot. At most `PUSH_LIMIT` pushes a sweep; the
rest wait for the next one.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Appointment, ZuperDeleteSnapshot, ZuperDocument, ZuperMapping
from . import client, config, engine, mapping, zapi

log = logging.getLogger("zuper.sweep")

OVERLAP = timedelta(minutes=5)
PUSH_LIMIT = 300

# (list name, the path in client.PATHS, what a row is)
MODULES = [("customers", "customers", "customer"), ("jobs", "jobs", "job"),
           ("appointments", "appointments", "appointment"),
           ("estimates", "estimates", "estimate"), ("invoices", "invoices", "invoice")]


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def due(db: Session, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    state = db.get(engine.ZuperSyncState, 1)
    started = mapping.aware(state.last_sweep_started_at) if state else None
    return started is None or now - started >= timedelta(minutes=config.sweep_minutes())


def filter_narrows(path_name: str, now: datetime) -> bool:
    """Does `filter.updated_at_from` actually narrow this list? Ask for the future."""
    found = zapi.total(path_name, {"filter.updated_at_from": iso(now + timedelta(days=2))})
    return found == 0


def changed_since(path_name: str, since: datetime, narrows: bool, extra: dict | None = None):
    params = dict(extra or {})
    if narrows:
        params["filter.updated_at_from"] = iso(since)
    for rec in client.iter_all(client.path(path_name), params):
        if not narrows:
            updated = mapping.parse_time(rec.get("updated_at"))
            if updated is not None and updated < since:
                continue
        yield rec


def _pull_zuper(ctx: engine.Ctx, since: datetime, checks: dict) -> None:
    for name, path_name, what in MODULES:
        try:
            narrows = filter_narrows(path_name, ctx.now)
        except client.ZuperError as exc:
            # A module this account does not have: live, appointments answers 403 on every
            # read. Skipping it keeps the sweep a success, so the cursor moves and the rest
            # of the mirror stays level; one module's absence is not an outage.
            if exc.kind in ("unauthorized", "not_found", "refused", "rejected"):
                checks[name] = None
                ctx.count("module_unavailable:" + name)
                log.warning("zuper %s unavailable (%s) - skipped", name, exc.kind)
                continue
            raise
        checks[name] = narrows
        for rec in changed_since(path_name, since, narrows):
            uid = {"customer": lambda r: mapping.uid(r, "customer_uid"),
                   "job": lambda r: mapping.uid(r, "job_uid"),
                   "appointment": zapi.appointment_uid,
                   "estimate": lambda r: mapping.uid(r, "estimate_uid"),
                   "invoice": lambda r: mapping.uid(r, "invoice_uid")}[what](rec)
            if not uid:
                continue
            if what == "estimate":
                engine.pull_document(ctx, ZuperDocument.QUOTE, uid)
            elif what == "invoice":
                engine.pull_document(ctx, ZuperDocument.INVOICE, uid)
            elif what == "appointment":
                engine.pull(ctx, "appointment", uid, rec)
            else:
                # Lists may carry a partial record: re-read the whole one.
                engine.pull(ctx, what, uid)
            ctx.count("swept_" + name)
            ctx.db.commit()
        if what in ("customer", "job"):
            for rec in changed_since(path_name, since, narrows, {"filter.is_deleted": "true"}):
                uid = mapping.uid(rec, "customer_uid" if what == "customer" else "job_uid")
                m = mapping.mapping_by_uid(ctx.db, what, uid) if uid else None
                if m is not None and m.state == "linked" and zapi.is_deleted(rec):
                    engine.mirror_zuper_delete(ctx, engine.CRM_TYPE[what], m)
                    ctx.db.commit()


def _crm_rows(db: Session, kind: str):
    model = engine.MODELS[kind]
    stmt = select(model).order_by(model.id)
    if kind == "appointment":
        stmt = stmt.where(Appointment.opportunity_id.is_not(None))
    return db.scalars(stmt).all()


def _send_new_workiz_jobs(ctx: engine.Ctx) -> None:
    """Before the cutover date, a Workiz job the importer filed on a card that qualifies —
    every AHS one, a Retail one in a booked or owing stage — is sent (2026-09-16, v2). The
    importer queues nothing itself (its jobs-table guard), so the sweep does it. A card whose
    send was refused (missing name, phone or address) is not retried here; the modal says why."""
    from ..models import Opportunity
    from . import selection
    db = ctx.db
    if not ctx.before_cutover:
        return
    try:
        ahs_id, stages = selection.rule(db)
    except selection.Refused as why:
        ctx.count("workiz_auto_send_refused")
        engine.sync_state(db).last_error = str(why)
        return
    sent = {m.crm_id for m in db.scalars(select(ZuperMapping).where(
        ZuperMapping.crm_type == "opportunity")).all()}
    for o in db.scalars(select(Opportunity).order_by(Opportunity.id)).all():
        if o.id in sent or not selection.is_workiz_origin(o) \
                or not selection.qualifies(o, ahs_id, stages):
            continue
        outcome = engine.send(ctx, o.id)
        db.commit()
        ctx.count("workiz_auto_" + outcome)


def _push_crm(ctx: engine.Ctx) -> None:
    db = ctx.db
    pushed = 0
    for kind in engine.KINDS:
        maps = {m.crm_id: m for m in db.scalars(select(ZuperMapping).where(
            ZuperMapping.crm_type == kind)).all()}
        for obj in _crm_rows(db, kind):
            if pushed >= PUSH_LIMIT:
                break
            m = maps.get(obj.id)
            if m is not None and m.state == "deleted":
                continue
            if m is not None and m.state == "linked" and m.crm_hash == mapping.digest(
                    engine.view_crm(ctx, kind, obj, m)):
                continue
            if not engine.in_scope(ctx, kind, obj):
                continue
            outcome = engine.push(ctx, kind, obj.id)
            db.commit()
            if outcome not in ("unchanged", "out_of_scope", "gone"):
                pushed += 1
                ctx.count("sweep_pushed_" + kind)
        if pushed >= PUSH_LIMIT:
            ctx.count("push_limit_reached")
            break
        # Deleted in the CRM without the listener seeing it (the switch was off, or raw SQL).
        model = engine.MODELS[kind]
        present = set(db.scalars(select(model.id)).all())
        for crm_id, m in maps.items():
            if m.state != "linked" or not m.zuper_uid or crm_id in present:
                continue
            batch = engine.new_batch()
            db.add(ZuperDeleteSnapshot(
                batch=batch, direction="crm_to_zuper", crm_type=kind, crm_id=crm_id,
                zuper_type=m.zuper_type, zuper_uid=m.zuper_uid, state="pending",
                snapshot={"record": {"id": crm_id}, "children": {},
                          "parent_uid": m.parent_uid,
                          "note": "found missing by the sweep; the record was not captured"}))
            db.commit()
            engine.mirror_crm_deletes(ctx, batch)
            ctx.count("sweep_deleted_" + kind)


def run(db: Session, now: datetime | None = None) -> dict:
    """One sweep. Records its heartbeat whatever happens, and commits."""
    ctx = engine.Ctx(db, now=now or datetime.now(UTC))
    state = engine.sync_state(db)
    settings = config.peek_settings(db)
    start = ctx.now
    since = mapping.aware(state.sweep_cursor) or mapping.aware(
        settings.enabled_at if settings else None) or (start - timedelta(days=1))
    since = since - OVERLAP
    state.last_sweep_started_at = start
    db.commit()
    checks: dict[str, bool] = {}
    try:
        _pull_zuper(ctx, since, checks)
        if config.pull_only():
            # One-way mirror: the sweep only reads Zuper.
            ctx.count("crm_half_skipped")
        else:
            _send_new_workiz_jobs(ctx)
            _push_crm(ctx)
        state = engine.sync_state(db)
        state.sweep_cursor = start
        state.last_sweep_success_at = datetime.now(UTC)
        state.filter_checks = checks
        state.last_counts = ctx.counts
        state.last_error = None
    except Exception as exc:
        db.rollback()
        engine.mark_quiet(db)
        state = engine.sync_state(db)
        state.last_error = client.sentence(exc)
        state.last_error_at = datetime.now(UTC)
        state.filter_checks = checks or state.filter_checks
        state.last_counts = ctx.counts
        if not isinstance(exc, client.ZuperError):
            log.exception("zuper sweep crashed")
    state.last_sweep_finished_at = datetime.now(UTC)
    db.commit()
    return ctx.counts

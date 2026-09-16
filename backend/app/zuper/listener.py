"""CRM changes -> queued sync jobs, from ONE place: the session's flush.

Registered on the Session class in `app/db.py` (beside the number-thread adoption), so every
path that changes a contact, a deal, a visit, a deal's note or task — a route, the CLI, an AI
agent's action, the AHS email ingest — is covered without anyone remembering to call it.

* **Nothing at all** while `ZUPER_SYNC_ENABLED` is false: the first line returns, before any
  query. Nothing while the Settings switch is off or the setup check has not passed.
* **Quiet sessions are skipped**: the sync's own writes (`engine.mark_quiet`) and the Workiz
  importer, whose guard aborts an import that adds so much as one `jobs` row. What a quiet
  session changed is found by the 15-minute sweep, which compares every record's content
  hash with the one last synced.
* **Only a sent card's changes go out** (2026-09-16, v2): the card, its notes and its tasks.
  Contacts and visits of sent jobs are Zuper's and locked; unsent cards stay in the CRM.
* **Jobs are written AFTER the change commits, in a session of their own.** One job per
  record with a dedupe key (`zuper:push:<kind>:<id>`), due ten seconds later, so a burst of
  saves is one push; a key that is already pending is the coalescing, and a race between two
  saves can only make the second insert fail in THAT side session — never the save.
* **A delete is snapshotted BEFORE it happens**, in the same transaction as the delete, so
  Settings → Zuper can restore it; the Zuper side is deleted by the queued job.
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from itertools import chain

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    Appointment,
    CompanyCamLink,
    Contact,
    Job,
    Opportunity,
    OpportunityNote,
    OpportunityTask,
    Stage,
    ZuperDeleteSnapshot,
)
from . import config

log = logging.getLogger("zuper.listener")

PUSH_JOB = "zuper_push"
DELETE_JOB = "zuper_delete"
SEND_JOB = "zuper_send"
JOB_TYPES = (PUSH_JOB, DELETE_JOB, SEND_JOB)
COALESCE_SECONDS = 10

TRACKED: dict[type, str] = {Contact: "contact", Opportunity: "opportunity",
                            Appointment: "appointment", OpportunityNote: "note",
                            OpportunityTask: "task"}
RELATED = (CompanyCamLink, Stage)

_ready = {"checked_at": -1e9, "ok": False}


def reset() -> None:
    """Tests: forget whether the tables exist."""
    _ready.update(checked_at=-1e9, ok=False)


def _tables_ready(session: Session) -> bool:
    """The flush must never query a table the migration has not made yet: on PostgreSQL a
    failed statement aborts the transaction, which would break every save."""
    if _ready["ok"]:
        return True
    now = time.monotonic()
    if now - _ready["checked_at"] < 60:
        return False
    _ready["checked_at"] = now
    try:
        bind = session.get_bind()
        engine = getattr(bind, "engine", bind)
        with engine.connect() as conn:
            ok = sa_inspect(conn).has_table("zuper_settings") and sa_inspect(conn).has_table(
                "zuper_mappings")
    except Exception:  # noqa: BLE001 - "cannot tell" is "not ready": ask again in a minute
        ok = False
    _ready["ok"] = ok
    return ok


def _state(session: Session) -> dict:
    return session.info.setdefault("zuper_pending", {
        "new": [], "keys": set(), "batches": set(), "batch": None})


def _old_value(obj, attr: str):
    history = sa_inspect(obj).attrs[attr].history
    if history.deleted:
        return history.deleted[0]
    return None


def before_flush(session: Session) -> None:
    if not config.env_enabled() or session.info.get("zuper_quiet"):
        return
    touched = [o for o in chain(session.new, session.dirty, session.deleted)
               if type(o) in TRACKED or isinstance(o, RELATED)]
    if not touched or not _tables_ready(session) or not config.armed(session):
        return
    from . import engine, mapping  # imported late: db.py registers this module

    st = _state(session)
    detached = session.info.setdefault("zuper_detached", {})
    for obj in session.new:
        if type(obj) in TRACKED:
            st["new"].append((TRACKED[type(obj)], obj))
        elif isinstance(obj, CompanyCamLink):
            st["keys"].add(("opportunity", obj.opportunity_id))
        elif isinstance(obj, Stage):
            st["new"].append(("stage", obj))
    for obj in session.dirty:
        if not session.is_modified(obj, include_collections=False):
            continue
        if isinstance(obj, Stage):
            st["keys"].add(("stage", obj.id))
            continue
        if isinstance(obj, CompanyCamLink):
            st["keys"].add(("opportunity", obj.opportunity_id))
            continue
        if type(obj) not in TRACKED:
            continue
        kind = TRACKED[type(obj)]
        st["keys"].add((kind, obj.id))
        if isinstance(obj, Opportunity):
            old = _old_value(obj, "contact_id")
            for cid in {old, obj.contact_id} - {None}:
                st["keys"].add(("contact", cid))
            if old is not None and obj.contact_id is None:
                detached.setdefault(("contact_opportunities", old), []).append(obj.id)
        elif isinstance(obj, Appointment):
            old_opp = _old_value(obj, "opportunity_id")
            for oid in {old_opp, obj.opportunity_id} - {None}:
                st["keys"].add(("opportunity", oid))
            if old_opp is not None and obj.opportunity_id is None:
                detached.setdefault(("opportunity", old_opp), []).append(obj.id)
            old_contact = _old_value(obj, "contact_id")
            if old_contact is not None and obj.contact_id is None:
                detached.setdefault(("contact_appointments", old_contact), []).append(obj.id)
    for obj in session.deleted:
        if type(obj) not in TRACKED:
            continue
        kind = TRACKED[type(obj)]
        if isinstance(obj, Opportunity) and obj.contact_id is not None:
            st["keys"].add(("contact", obj.contact_id))
        m = mapping.mapping_for(session, kind, obj.id)
        if m is None or m.state != "linked" or not m.zuper_uid:
            continue
        if st["batch"] is None:
            st["batch"] = engine.new_batch()
        snap = engine.snapshot(session, kind, obj, detached=detached)
        snap["parent_uid"] = m.parent_uid
        session.add(ZuperDeleteSnapshot(
            batch=st["batch"], direction="crm_to_zuper", crm_type=kind, crm_id=obj.id,
            zuper_type=m.zuper_type, zuper_uid=m.zuper_uid, snapshot=snap, state="pending"))
        st["batches"].add(st["batch"])


def after_flush(session: Session) -> None:
    st = session.info.get("zuper_pending")
    if not st or not st["new"]:
        return
    for kind, obj in st["new"]:
        if obj.id is None:
            continue
        st["keys"].add((kind, obj.id))
        if kind == "opportunity" and obj.contact_id is not None:
            st["keys"].add(("contact", obj.contact_id))
        if kind == "appointment" and obj.opportunity_id is not None:
            st["keys"].add(("opportunity", obj.opportunity_id))
    st["new"].clear()


def after_commit(session: Session) -> None:
    # SQLAlchemy fires this for a SAVEPOINT's commit too (the AI hooks run in one). The outer
    # transaction is still open then — and may yet roll back — so only the root commit counts.
    if session.in_nested_transaction():
        return
    st = session.info.pop("zuper_pending", None)
    session.info.pop("zuper_detached", None)
    if not st or not (st["keys"] or st["batches"]):
        return
    try:
        enqueue(session.get_bind(), sorted(st["keys"]), sorted(st["batches"]))
    except Exception:
        # Never an error for the person who saved: the sweep finds the change by its hash.
        log.exception("zuper: could not queue sync jobs; the sweep will catch up")


def after_rollback(session: Session) -> None:
    session.info.pop("zuper_pending", None)
    session.info.pop("zuper_detached", None)


def _reaches_zuper(side: Session, kind: str, record_id: int) -> bool:
    """Which CRM changes are pushed (2026-09-16, v2): only on a card SENT to Zuper — the card
    itself (its Checklist answers and the CRM's own fields), and its notes and tasks. A
    contact's details and a sent job's visits are Zuper's and locked in the CRM, so a change to
    either is never pushed from here (the Workiz importer's, before cutover, is found by the
    sweep). A card that was never sent reaches Zuper only through Send to Zuper."""
    from . import engine
    if kind == "stage":
        return True
    if kind == "opportunity":
        return engine.is_linked(side, "opportunity", record_id)
    if kind in ("note", "task"):
        row = side.get(engine.MODELS[kind], record_id)
        return row is not None and engine.is_linked(side, "opportunity", row.opportunity_id)
    return False


def enqueue(bind, keys: list[tuple[str, int]], batches: list[str]) -> int:
    """Insert the jobs in a session of their own. A key already queued is the coalescing."""
    now = datetime.now(UTC)
    wanted: dict[str, Job] = {}
    for kind, record_id in keys:
        if record_id is None:
            continue
        key = "zuper:push:%s:%s" % (kind, record_id)
        wanted[key] = Job(type=PUSH_JOB, dedupe_key=key,
                          payload={"kind": kind, "id": record_id, "at": now.isoformat()},
                          run_after=now + timedelta(seconds=COALESCE_SECONDS))
    for batch in batches:
        key = "zuper:delete:%s" % batch
        wanted[key] = Job(type=DELETE_JOB, dedupe_key=key, payload={"batch": batch},
                          run_after=now)
    if not wanted:
        return 0
    engine = getattr(bind, "engine", bind)
    added = 0
    with Session(bind=engine) as side:
        side.info["zuper_quiet"] = "enqueue"
        for key in [k for k, job in wanted.items() if job.type == PUSH_JOB]:
            if not _reaches_zuper(side, wanted[key].payload["kind"], wanted[key].payload["id"]):
                del wanted[key]
        if not wanted:
            return 0
        taken = set(side.scalars(select(Job.dedupe_key).where(
            Job.dedupe_key.in_(list(wanted)))).all())
        fresh = [job for key, job in wanted.items() if key not in taken]
        side.add_all(fresh)
        try:
            side.commit()
            added = len(fresh)
        except IntegrityError:
            side.rollback()
            for job in fresh:
                side.add(Job(type=job.type, dedupe_key=job.dedupe_key, payload=job.payload,
                             run_after=job.run_after))
                try:
                    side.commit()
                    added += 1
                except IntegrityError:
                    side.rollback()
    return added

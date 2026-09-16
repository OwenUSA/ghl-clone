"""The worker's Zuper thread: queued pushes and deletes, webhook deliveries, the sweep, the
daily digest — every tick, and only while the sync is armed (env flag, key, Settings switch,
setup passed). A Zuper outage is a heartbeat error, never a crash, and never delays the main
queue: the main drainer does not claim `zuper_*` jobs and this thread claims nothing else.
"""
from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from .. import queue
from ..models import Job
from . import client, config, digest, engine, listener, setup, sweep, webhooks

log = logging.getLogger("zuper.worker")

TICK_SECONDS = 20
PERMANENT = ("refused", "rejected", "unauthorized", "no_key")


def handle(db: Session, job: Job) -> str:
    payload = job.payload or {}
    ctx = engine.Ctx(db, crm_changed_at=_parse(payload.get("at")))
    if job.type == listener.PUSH_JOB:
        if payload.get("kind") == "stage":
            outcome = setup.push_stage(ctx, int(payload["id"]))
        else:
            outcome = engine.push(ctx, payload["kind"], int(payload["id"]))
    elif job.type == listener.SEND_JOB:
        outcome = engine.send(ctx, int(payload["opportunity_id"]))
    elif job.type == listener.DELETE_JOB:
        outcome = str(engine.mirror_crm_deletes(ctx, payload["batch"]))
    else:
        raise ValueError("not a Zuper job: %s" % job.type)
    state = engine.sync_state(db)
    state.last_push_at = ctx.now
    db.commit()
    return outcome


def _parse(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def record_error(db: Session, exc: Exception) -> None:
    state = engine.sync_state(db)
    state.last_error = client.sentence(exc)
    state.last_error_at = datetime.now(UTC)
    db.commit()


def drain(db: Session, limit: int = 25) -> int:
    engine.mark_quiet(db)
    jobs = queue.claim(db, limit=limit, types=listener.JOB_TYPES)
    for job in jobs:
        if job.dedupe_key:
            # Released as it starts: a change saved while this runs queues a fresh push.
            job.dedupe_key = None
            db.commit()
        try:
            handle(db, job)
            queue.finish(db, job)
        except client.ZuperError as exc:
            db.rollback()
            engine.mark_quiet(db)
            permanent = exc.kind in PERMANENT
            queue.finish(db, job, client.sentence(exc), permanent=permanent)
            if job.type == listener.SEND_JOB and job.status == "failed":
                # The card shows why, and Send to Zuper can be pressed again.
                engine.mark_send_failed(db, int((job.payload or {}).get("opportunity_id", 0)),
                                        client.sentence(exc))
            record_error(db, exc)
            if exc.kind in ("unavailable", "rate_limited"):
                break
        except Exception as exc:
            db.rollback()
            engine.mark_quiet(db)
            queue.finish(db, job, repr(exc))
            record_error(db, exc)
            log.exception("zuper job %s#%s failed", job.type, job.id)
    return len(jobs)


def tick(session_factory, now: datetime | None = None) -> None:
    if not config.env_enabled():
        return
    db = engine.mark_quiet(session_factory())
    try:
        if not config.armed(db):
            return
        drain(db)
        webhooks.process_inbox(db)
        if sweep.due(db, now):
            sweep.run(db)
        day = digest.due_day(db, now)
        if day is not None:
            digest.run(db, day)
    except Exception as exc:
        db.rollback()
        try:
            record_error(engine.mark_quiet(db), exc)
        except Exception:
            log.exception("zuper heartbeat could not be written")
        if not isinstance(exc, client.ZuperError):
            log.exception("zuper tick failed")
    finally:
        db.close()


def start_worker_thread(session_factory, stop: threading.Event) -> threading.Thread:
    def loop():
        while True:
            tick(session_factory)
            if stop.wait(TICK_SECONDS):
                return
    t = threading.Thread(target=loop, name="zuper", daemon=True)
    t.start()
    return t

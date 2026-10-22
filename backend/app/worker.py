"""Queue drainer. Run with:  uv run python -m app.worker

Single replica. Claims due jobs, runs the matching handler, commits, and backs off
on failure. Nothing here transmits anything while LoggingTransport is the wired
implementation.
"""
import logging
import threading
import time

from . import companycam
from .automations import HANDLERS
from .db import SessionLocal
from .queue import claim, finish
from .zuper import listener as zuper_listener
from .zuper import worker as zuper_worker

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("worker")

POLL_SECONDS = 5


def drain_once() -> int:
    """Claim and run one batch. Returns how many jobs ran."""
    db = SessionLocal()
    try:
        # Zuper jobs are its own thread's (2026-09-16); see app/zuper/worker.py.
        jobs = claim(db, exclude_types=zuper_listener.JOB_TYPES)
        for job in jobs:
            handler = HANDLERS.get(job.type)
            if handler is None:
                # Not transient — retrying cannot register a handler.
                finish(db, job, "no handler for type %r" % job.type, permanent=True)
                log.error("no handler for job type %r (#%s)", job.type, job.id)
                continue
            try:
                handler(db, job.payload or {})
                db.commit()
                finish(db, job)
                log.info("ran %s#%s", job.type, job.id)
            except Exception as exc:
                db.rollback()
                finish(db, job, repr(exc))
                log.exception("job %s#%s failed", job.type, job.id)
        return len(jobs)
    finally:
        db.close()


def main() -> None:
    log.info("worker started; polling every %ss", POLL_SECONDS)
    # CompanyCam (2026-09-14): project creation requests every minute and the hourly
    # linking check, on a thread of their own so a slow CompanyCam page never delays a
    # reminder. Each tick re-reads the switches, and with no token it does nothing.
    stop = threading.Event()
    companycam.start_worker_thread(SessionLocal, stop)
    # Zuper sync (2026-09-16): pushes, webhooks, the 15-minute sweep and the daily digest, on
    # a thread of their own. Each tick does nothing unless the sync is armed.
    zuper_worker.start_worker_thread(SessionLocal, stop)
    # The Dispatch page's AI explanations (2026-10-01): their own thread, so a reasoning
    # model's minutes never hold up the Zuper sync. Off unless DISPATCH_ENABLED, and then
    # off again unless an admin switched the AI on.
    from .dispatch import service as dispatch_service
    dispatch_service.start_ai_thread(SessionLocal, stop)
    # The auto-reply (2026-10-19): its own thread, every minute — a model's answer never holds
    # up the sync. Does nothing unless ZUPER_AUTOREPLY_ENABLED and the Dispatch switch say so.
    from .autoreply import service as autoreply_service
    autoreply_service.start_thread(SessionLocal, stop)
    # The call follow-up (2026-10-21): a text, then Anisa's call, after an unanswered team
    # call. Its own thread; does nothing unless CALL_FOLLOWUP_ENABLED.
    from .followup import service as followup_service
    followup_service.start_thread(SessionLocal, stop)
    # The inspection-documentation agent (2026-10-21): reads Zuper, flags what is missing.
    from .inspection_docs import service as inspection_service
    inspection_service.start_thread(SessionLocal, stop)
    # The post-inspection report (2026-10-21): a draft report after photos go quiet.
    from .inspection_report import service as report_service
    report_service.start_thread(SessionLocal, stop)
    # The technicians' day-before briefing (2026-10-21): tomorrow's jobs, findings, tools.
    from .tech_briefing import service as briefing_service
    briefing_service.start_thread(SessionLocal, stop)
    # Payments & collections (2026-10-21): due items, Zuper suggestions, screenshots.
    from .payments import service as payments_service
    payments_service.start_thread(SessionLocal, stop)
    # Daily report & insights (2026-10-22): reads calls and texts once, raises customer issues.
    from .insights import service as insights_service
    insights_service.start_thread(SessionLocal, stop)
    # Reviews & QC (2026-10-22): a finished job's customer is asked how we did.
    from .reviews import service as reviews_service
    reviews_service.start_thread(SessionLocal, stop)
    while True:
        try:
            if drain_once() == 0:
                time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            log.info("worker stopping")
            stop.set()
            break
        except Exception:
            log.exception("drain loop error")
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()

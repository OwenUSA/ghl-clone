"""Queue drainer. Run with:  uv run python -m app.worker

Single replica. Claims due jobs, runs the matching handler, commits, and backs off
on failure. Nothing here transmits anything while LoggingTransport is the wired
implementation.
"""
import logging
import time

from .automations import HANDLERS
from .db import SessionLocal
from .queue import claim, finish

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("worker")

POLL_SECONDS = 5


def drain_once() -> int:
    """Claim and run one batch. Returns how many jobs ran."""
    db = SessionLocal()
    try:
        jobs = claim(db)
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
    while True:
        try:
            if drain_once() == 0:
                time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            log.info("worker stopping")
            break
        except Exception:
            log.exception("drain loop error")
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()

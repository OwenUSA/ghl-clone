"""Postgres-backed job queue. No Redis (DECISIONS.md).

Claiming uses SELECT ... FOR UPDATE SKIP LOCKED so several workers can drain the
same table without two of them running one job. On SQLite (dev bootstrap) the
locking clause is skipped — single worker only there.
"""
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Job

log = logging.getLogger("queue")


def utcnow():
    return datetime.now(UTC)


def enqueue(db: Session, type_: str, payload: dict, *,
            run_after: datetime | None = None,
            dedupe_key: str | None = None) -> Job | None:
    """Insert a job. Returns None if dedupe_key already exists.

    Dedupe is what stops a customer getting two reminder texts because a record
    was saved twice.
    """
    if dedupe_key:
        existing = db.scalar(select(Job).where(Job.dedupe_key == dedupe_key))
        if existing:
            return None
    job = Job(type=type_, payload=payload,
              run_after=run_after or utcnow(), dedupe_key=dedupe_key)
    db.add(job)
    db.flush()
    return job


def claim(db: Session, limit: int = 10) -> list[Job]:
    """Atomically claim due jobs."""
    stmt = (select(Job)
            .where(Job.status == "pending", Job.run_after <= utcnow())
            .order_by(Job.run_after)
            .limit(limit))
    if db.bind and db.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)

    jobs = list(db.scalars(stmt).all())
    for j in jobs:
        j.status = "running"
        j.attempts += 1
    db.commit()
    return jobs


def finish(db: Session, job: Job, error: str | None = None, *,
           permanent: bool = False) -> None:
    """Mark a job done, or schedule a retry.

    `permanent=True` fails immediately without retrying — for errors that cannot
    succeed on a later attempt (e.g. no handler registered for the job type).
    Retrying those just burns 5 attempts and delays the failure being noticed.
    """
    if error is None:
        job.status = "done"
        job.completed_at = utcnow()
    elif permanent or job.attempts >= job.max_attempts:
        job.status = "failed"
        job.last_error = error[:2000]
    else:
        # Exponential backoff: 1m, 2m, 4m, 8m...
        job.status = "pending"
        job.last_error = error[:2000]
        job.run_after = utcnow() + timedelta(minutes=2 ** (job.attempts - 1))
    db.commit()

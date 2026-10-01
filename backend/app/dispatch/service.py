"""One Dispatch pass: read Zuper, apply the rules, keep the items, ring what is new.

Runs on the worker's Zuper thread every DISPATCH_POLL_SECONDS while DISPATCH_ENABLED is on and
the Zuper sync is armed. It never raises into the worker: a failure is the heartbeat's
`last_error`, shown on the page.

  uv run python -m app.dispatch.service          # one pass now, DRY RUN: reads, prints counts
  uv run python -m app.dispatch.service --commit # ...and keeps what it found
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DispatchItem, DispatchJob, DispatchState, DispatchSuggestion
from ..zuper import client
from . import alerts, comms, reader, rules
from . import config as c

log = logging.getLogger("dispatch")


def state(db: Session) -> DispatchState:
    s = db.get(DispatchState, 1)
    if s is None:
        s = DispatchState(id=1)
        db.add(s)
        db.flush()
    return s


def due(db: Session, now: datetime | None = None) -> bool:
    if not c.enabled():
        return False
    now = now or datetime.now(UTC)
    s = db.get(DispatchState, 1)
    last = c.aware(s.last_started_at) if s else None
    return last is None or now - last >= timedelta(seconds=c.poll_seconds())


def store_items(db: Session, items: list[rules.Item], now: datetime) -> dict:
    """Upsert by key. Staff's Done / Wrong stand; an open item the rules no longer find is
    `resolved` (somebody acted — usually Zuper was updated)."""
    counts = {"opened": 0, "updated": 0, "resolved": 0}
    current = {}
    for it in items:
        current.setdefault(it.key[:200], it)
    rows = {r.key: r for r in db.scalars(select(DispatchItem).where(
        (DispatchItem.state == "open") | DispatchItem.key.in_(list(current))))}
    for key, it in current.items():
        r = rows.get(key)
        fields = {"kind": it.kind, "queue": it.queue, "job_uid": it.job_uid,
                  "job_number": it.job_number, "board": it.board, "phone": it.phone,
                  "title": it.title[:300], "why": it.why, "todo": it.todo,
                  "evidence": it.evidence, "due_at": it.due_at, "urgent": it.urgent(now)}
        if r is None:
            db.add(DispatchItem(key=key, state="open", created_at=now, updated_at=now,
                                **fields))
            counts["opened"] += 1
        elif r.state == "open":
            for k, v in fields.items():
                setattr(r, k, v)
            r.updated_at = now
            counts["updated"] += 1
    for key, r in rows.items():
        if r.state == "open" and key not in current:
            r.state, r.closed_at = "resolved", now
            counts["resolved"] += 1
    return counts


def _same(a: str | None, b: str | None) -> bool:
    return " ".join((a or "").lower().split()) == " ".join((b or "").lower().split())


def close_suggestions(db: Session, jobs: list[DispatchJob], now: datetime) -> int:
    """An open or approved suggestion whose value Zuper now shows is done (`done_in_zuper`)."""
    by_uid = {j.job_uid: j for j in jobs}
    done = 0
    for sug in db.scalars(select(DispatchSuggestion).where(
            DispatchSuggestion.state.in_(("open", "approved", "apply_failed")))):
        j = by_uid.get(sug.job_uid)
        if j is None or sug.kind == "note":
            continue
        if sug.kind == "field":
            hit = _same((j.fields or {}).get(sug.field), sug.proposed)
        else:
            hit = " ".join(sug.proposed.lower().split()) in " ".join(
                ", ".join(x for x in (j.address, j.city) if x).lower().split())
        if hit:
            sug.state, sug.decided_at = "done_in_zuper", now
            done += 1
    return done


def run(db: Session, now: datetime | None = None, *, commit: bool = True) -> dict:
    now = now or datetime.now(UTC)
    s = state(db)
    s.last_started_at = now
    if commit:
        db.commit()
    counts: dict = {}
    try:
        with client.read_only() if not commit else contextlib.nullcontext():
            counts["jobs"] = reader.read_jobs(db, now)
            try:
                counts["zuper_calls"] = reader.read_calls(db)
                s.calls_read_at = now
            except client.ZuperError as exc:
                counts["zuper_calls_error"] = client.sentence(exc)
        db.flush()
        jobs = list(db.scalars(select(DispatchJob)))
        found, events = rules.evaluate(
            jobs, comms.load(db, now - timedelta(days=c.COMMS_DAYS)), now)
        new_ids = set(counts["jobs"].pop("new_jobs", []))
        if s.seeded:
            for j in jobs:
                created = c.aware(j.zuper_created_at)
                if j.job_uid in new_ids and j.board in (c.INSPECTION_BOARD, c.RETAIL_BOARD) \
                        and created and now - created <= timedelta(days=1):
                    events.append(rules.Event(
                        key="new_job:%s" % j.job_uid, kind="new_job",
                        title="New %s job: #%s %s" % (
                            "AHS" if j.board == c.INSPECTION_BOARD else "Retail",
                            j.job_number, j.customer_name or ""),
                        body="%s · %s." % (j.status or "new", ", ".join(
                            x for x in (j.address, j.city) if x) or "no address"),
                        job_uid=j.job_uid))
        counts["items"] = store_items(db, found, now)
        counts["alerts"] = alerts.fire(db, events, ring=s.seeded,
                                       boards={j.job_uid: j.board for j in jobs})
        counts["open"] = len(found)
        counts["suggestions_done_in_zuper"] = close_suggestions(db, jobs, now)
        if commit:
            s.seeded = True
            s.last_success_at = now
            s.last_error = None
            s.last_counts = counts
            db.commit()
            # Phase 2: the AI words a few new items per pass. It never raises into the pass and
            # writes only onto the items (and suggestions) — never to Zuper.
            try:
                from . import ai
                counts["ai"] = ai.explain_pending(db, now)
            except Exception:
                db.rollback()
                log.exception("dispatch AI explanations failed")
        else:
            db.rollback()
    except Exception as exc:
        db.rollback()
        s = state(db)
        s.last_error = client.sentence(exc)
        s.last_error_at = now
        if commit:
            db.commit()
        if not isinstance(exc, client.ZuperError):
            log.exception("dispatch pass failed")
        counts["error"] = s.last_error
    return counts


def main(argv: list[str] | None = None) -> int:
    from ..db import SessionLocal
    from ..zuper import engine
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--commit", action="store_true", help="keep what the pass found")
    args = ap.parse_args(argv)
    db = engine.mark_quiet(SessionLocal())
    try:
        with client.operator_mode():
            counts = run(db, commit=args.commit)
    finally:
        db.close()
    print(("COMMITTED" if args.commit else "DRY RUN — nothing kept") + " " +
          json.dumps(counts, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

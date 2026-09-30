"""Zuper's history, kept in the CRM for the KPI reports (2026-09-30). READS Zuper only.

Two append-only tables — nothing here ever updates or deletes a row:

  zuper_status_history   every column move of every job, with Zuper's own time, who made it
                         and the checklist answered on it. Filled whenever the sync reads a
                         whole job (webhook, sweep — `capture_statuses` from `engine.pull`) and
                         by the daily pass below. Keyed on Zuper's `status_history_uid`, so a
                         move is stored once however often it is seen.
  zuper_record_versions  a full copy of each job, quote/proposal, invoice, payment and
                         commission, added only when it differs from the latest copy.

Why the CRM keeps its own copy: Zuper's history is not fixed. Renaming a column rewrote how old
moves read, the AHS split moved 290 jobs in one burst, and nothing before the 2026-09-17 load
survived. What is captured here stays exactly as it was first seen.

The daily pass runs once per America/New_York day after 02:00, on the worker's Zuper thread:
the job / quote / invoice LISTS (a few pages), then the WHOLE record of each one whose
`updated_at` moved since its latest copy (the list rows lack line items, profitability,
signatures and the chosen proposal option). Payments and commissions are stored as listed.
The first pass reads every record once.

  uv run python -m app.zuper.history            # DRY RUN: what the pass would add
  uv run python -m app.zuper.history --commit   # ...and add it
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import ZuperRecordVersion, ZuperStatusHistory
from . import client, config, engine, mapping, zapi

log = logging.getLogger("zuper.history")

RUN_AFTER = time(2, 0)

# (module, list path, detail path or None = store the list row, uid key)
MODULES: list[tuple[str, str, str | None, str]] = [
    ("job", "jobs", "job", "job_uid"),
    ("estimate", "estimates", "estimate", "estimate_uid"),
    ("invoice", "invoices", "invoice", "invoice_uid"),
    ("payment", "payments", None, "payment_uid"),
    ("commission", "commissions", None, "commission_uid"),
]
# A payment row's id key is unconfirmed (the account had none on 2026-09-30).
UID_FALLBACKS = ("transaction_uid", "uid", "_id", "id")


def content_hash(record: Any) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True, default=str)
                          .encode("utf-8")).hexdigest()


def _uid(row: dict, key: str) -> str | None:
    for k in (key, *UID_FALLBACKS):
        value = row.get(k)
        if isinstance(value, str | int) and str(value):
            return str(value)
    return None


def _text(value: Any, limit: int) -> str | None:
    if value is None or isinstance(value, dict | list):
        return None
    value = str(value).strip()
    return value[:limit] or None


def _person(done_by: Any) -> tuple[str | None, str | None]:
    if not isinstance(done_by, dict):
        return _text(done_by, 64), None
    name = " ".join(p for p in (done_by.get("first_name"), done_by.get("last_name"))
                    if isinstance(p, str) and p.strip())
    return _text(done_by.get("user_uid"), 64), _text(name, 120)


def _category(entry: dict, job: dict) -> tuple[str | None, str | None]:
    cat = entry.get("category")
    if isinstance(cat, dict):
        return _text(cat.get("category_uid"), 64), _text(cat.get("category_name"), 120)
    if isinstance(cat, str):
        return _text(cat, 64), None
    jc = job.get("job_category")
    if isinstance(jc, dict):
        return _text(jc.get("category_uid"), 64), _text(jc.get("category_name"), 120)
    return None, None


def status_rows(job: dict) -> list[dict]:
    """The moves on a whole job record, as rows for `zuper_status_history`."""
    job_uid = _text(job.get("job_uid"), 64)
    if not job_uid:
        return []
    out = []
    for entry in job.get("job_status") or []:
        if not isinstance(entry, dict):
            continue
        key = _text(entry.get("status_history_uid") or entry.get("_id"), 64)
        if not key:
            continue
        cat_uid, cat_name = _category(entry, job)
        by_uid, by_name = _person(entry.get("done_by"))
        checklist = entry.get("checklist")
        out.append({
            "history_uid": key, "job_uid": job_uid,
            "job_number": _text(job.get("work_order_number"), 40),
            "category_uid": cat_uid, "category_name": cat_name,
            "status_uid": _text(entry.get("status_uid"), 64),
            "status_name": _text(entry.get("status_name"), 120),
            "status_type": _text(entry.get("status_type"), 40),
            "changed_at": mapping.parse_time(entry.get("created_at")),
            "done_by_uid": by_uid, "done_by_name": by_name,
            "checklist": checklist if isinstance(checklist, list) and checklist else None})
    return out


def capture_statuses(db: Session, job: dict) -> int:
    """Add the moves on this job the CRM has not stored yet. Never raises into the caller (it
    runs inside the sync's pull), and a failure leaves the pull's own work untouched."""
    try:
        rows = status_rows(job)
        if not rows:
            return 0
        with db.begin_nested():
            known = set(db.scalars(select(ZuperStatusHistory.history_uid).where(
                ZuperStatusHistory.job_uid == rows[0]["job_uid"])).all())
            new = [r for r in rows if r["history_uid"] not in known]
            # Two entries sharing a key in one record: the first is kept.
            seen: set[str] = set()
            for r in new:
                if r["history_uid"] in seen:
                    continue
                seen.add(r["history_uid"])
                db.add(ZuperStatusHistory(**r))
            db.flush()
        return len(seen)
    except Exception:
        log.exception("zuper status history not captured")
        return 0


@dataclass(frozen=True)
class Latest:
    content_hash: str
    zuper_updated_at: str | None


def latest_versions(db: Session, module: str) -> dict[str, Latest]:
    """The latest copy's hash and `updated_at` per record — without loading the copies."""
    newest = (select(func.max(ZuperRecordVersion.id)).where(ZuperRecordVersion.module == module)
              .group_by(ZuperRecordVersion.zuper_uid))
    rows = db.execute(select(ZuperRecordVersion.zuper_uid, ZuperRecordVersion.content_hash,
                             ZuperRecordVersion.zuper_updated_at)
                      .where(ZuperRecordVersion.id.in_(newest))).all()
    return {uid: Latest(h, u) for uid, h, u in rows}


def capture_version(db: Session, module: str, uid: str, record: dict,
                    latest: Latest | None, now: datetime) -> bool:
    digest = content_hash(record)
    if latest is not None and latest.content_hash == digest:
        return False
    db.add(ZuperRecordVersion(
        module=module, zuper_uid=uid, content_hash=digest, record=record, captured_at=now,
        zuper_updated_at=_text(record.get("updated_at"), 40)))
    return True


def _detail(name: str, uid: str) -> dict | None:
    try:
        return zapi._record(client.request("GET", client.path(name, uid=uid)))
    except client.ZuperError as exc:
        if exc.kind == "not_found":
            return None
        raise


def run(db: Session, *, commit: bool = True, now: datetime | None = None) -> dict:
    """One pass. Dry run (`commit=False`) reads the same and writes nothing."""
    now = now or datetime.now(UTC)
    counts: dict[str, int] = {}

    def count(key: str, n: int = 1) -> None:
        counts[key] = counts.get(key, 0) + n

    for module, list_name, detail_name, key in MODULES:
        latest = latest_versions(db, module)
        try:
            rows = list(client.iter_all(client.path(list_name)))
        except client.ZuperError as exc:
            if exc.kind in ("unauthorized", "not_found", "refused", "rejected"):
                count(module + "_unavailable")
                log.warning("zuper %s unavailable for history (%s)", module, exc.kind)
                continue
            raise
        count(module + "_listed", len(rows))
        for row in rows:
            uid = _uid(row, key)
            if not uid:
                count(module + "_without_uid")
                continue
            prev = latest.get(uid)
            record = row
            if detail_name is not None:
                listed = _text(row.get("updated_at"), 40)
                if prev is not None and listed and prev.zuper_updated_at == listed:
                    count(module + "_unchanged")
                    continue
                record = _detail(detail_name, uid)
                if record is None:
                    count(module + "_gone")
                    continue
                count(module + "_read")
            changed = content_hash(record) != (prev.content_hash if prev else None)
            if changed:
                count(module + "_new_version")
            if module == "job":
                if commit:
                    count("status_moves_added", capture_statuses(db, record))
                else:
                    known = set(db.scalars(select(ZuperStatusHistory.history_uid).where(
                        ZuperStatusHistory.job_uid == uid)).all())
                    count("status_moves_added",
                          len({r["history_uid"] for r in status_rows(record)} - known))
            if commit and changed:
                capture_version(db, module, uid, record, prev, now)
        if commit:
            db.commit()
    return counts


def due(db: Session, now: datetime | None = None) -> str | None:
    """The America/New_York day to run for, or None (already ran today / before 02:00)."""
    local = (now or datetime.now(UTC)).astimezone(config.ACCOUNT_TZ)
    if local.time() < RUN_AFTER:
        return None
    state = db.get(engine.ZuperSyncState, 1)
    day = local.date().isoformat()
    if state is not None and state.last_history_day and state.last_history_day >= day:
        return None
    return day


def run_daily(db: Session, now: datetime | None = None) -> dict | None:
    """The worker's once-a-day pass. The day is marked BEFORE reading, so a Zuper outage
    costs one day's copy (the sweep still captures moves) rather than a retry every tick."""
    day = due(db, now)
    if day is None:
        return None
    state = engine.sync_state(db)
    state.last_history_day = day
    db.commit()
    counts = run(db, commit=True, now=now)
    log.info("zuper history pass %s: %s", day, counts)
    return counts


def main(argv: list[str] | None = None) -> int:
    from ..db import SessionLocal
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--commit", action="store_true", help="write (default: dry run)")
    args = ap.parse_args(argv)
    db = engine.mark_quiet(SessionLocal())
    try:
        with client.operator_mode():
            counts = run(db, commit=args.commit)
    finally:
        db.close()
    print(("COMMITTED" if args.commit else "DRY RUN — nothing written") + " "
          + json.dumps(counts, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


"""Read Zuper for the Dispatch page — GET requests and the call-history search, nothing else.

Each pass (every DISPATCH_POLL_SECONDS, on the worker's Zuper thread):

  1. the job LIST (every job, a few pages) -> one `dispatch_jobs` row per job;
  2. the whole job, for an open job that changed since the last pass or has a visit TODAY: who
     made the last move, the Technician field, the checklist pictures — and its stage moves go
     into `zuper_status_history` (app/zuper/history.py), which only takes whole records, never
     a list row, because a list row's moves carry no `done_by`;
  3. the job's NOTES under the same condition: when Zuper was last written to, and the
     pictures posted on the visit day (a picture is an IMAGE / VIDEO note);
  4. the latest page of Zuper Connect's call history -> `dispatch_calls`.

The first pass reads every open job once (about 150 jobs, two requests each); after that only
what changed, plus the day's visits.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DispatchCall, DispatchJob
from ..zuper import client, history, mapping
from . import config as c
from .comms import digits

log = logging.getLogger("dispatch.reader")


def _time(value) -> datetime | None:
    return mapping.parse_time(value) if value else None


def _name(person: Any) -> str | None:
    if not isinstance(person, dict):
        return None
    n = " ".join(x for x in (person.get("first_name"), person.get("last_name"))
                 if isinstance(x, str) and x.strip())
    return n or None


def _moves(record: dict) -> list[dict]:
    return sorted((e for e in record.get("job_status") or [] if isinstance(e, dict)),
                  key=lambda e: str(e.get("created_at") or ""))


def apply_row(j: DispatchJob, row: dict) -> None:
    """Everything a LIST row carries."""
    j.job_number = str(row.get("work_order_number") or "") or None
    j.title = (row.get("job_title") or "")[:300] or None
    j.board = ((row.get("job_category") or {}).get("category_name") or None) \
        if isinstance(row.get("job_category"), dict) else None
    cur = row.get("current_job_status") if isinstance(row.get("current_job_status"), dict) else {}
    moves = _moves(row)
    j.status = cur.get("status_name") or (moves[-1].get("status_name") if moves else None)
    j.status_type = cur.get("status_type") or (moves[-1].get("status_type") if moves else None)
    j.status_since = _time(moves[-1].get("created_at")) if moves else None
    cust = row.get("customer") if isinstance(row.get("customer"), dict) else {}
    j.customer_uid = cust.get("customer_uid")
    j.customer_name = (_name({"first_name": cust.get("customer_first_name"),
                              "last_name": cust.get("customer_last_name")})
                       or cust.get("customer_company_name") or None)
    nums = cust.get("customer_contact_no") if isinstance(cust.get("customer_contact_no"),
                                                         dict) else {}
    addr = row.get("customer_address") if isinstance(row.get("customer_address"), dict) else {}
    phones = {digits(v) for v in [*nums.values(), addr.get("phone_number")] if digits(v)}
    j.phones = sorted(phones) or None
    j.address = (addr.get("street") or "")[:300] or None
    j.city = (addr.get("city") or "")[:120] or None
    geo = addr.get("geo_cordinates")
    if isinstance(geo, list) and len(geo) == 2 and all(isinstance(x, int | float) for x in geo):
        j.lat, j.lng = float(geo[0]), float(geo[1])
    j.scheduled_start = _time(row.get("scheduled_start_time"))
    j.scheduled_end = _time(row.get("scheduled_end_time"))
    names = [_name(a.get("user")) for a in row.get("assigned_to") or [] if isinstance(a, dict)]
    j.assigned = [n for n in names if n] or None
    j.zuper_created_at = _time(row.get("created_at"))
    j.is_open = (j.status or "") not in c.CLOSED and not row.get("is_deleted")


def apply_detail(j: DispatchJob, record: dict, today: str) -> tuple[int, datetime | None,
                                                                    str | None]:
    """The whole job: who moved it last, the Technician field — and the checklist pictures
    answered TODAY (count, latest time, by whom)."""
    moves = _moves(record)
    if moves:
        j.status_by = _name(moves[-1].get("done_by"))
    fields: dict[str, str] = {}
    for f in record.get("custom_fields") or []:
        if not isinstance(f, dict) or not isinstance(f.get("label"), str):
            continue
        value = f.get("value")
        fields[f["label"][:200]] = "" if value is None else str(value)[:500]
        if f.get("label") == "Technician" and value:
            j.technician = str(value)[:120]
    j.fields = fields or None
    count, last, by = 0, None, None
    for e in moves:
        at = _time(e.get("created_at"))
        if not at or c.local(at).date().isoformat() != today:
            continue
        for q in e.get("checklist") or []:
            answer = q.get("answer") if isinstance(q, dict) else None
            pics = [a for a in answer if isinstance(a, str) and a.startswith("http")] \
                if isinstance(answer, list) else []
            if pics:
                count += len(pics)
                if last is None or at > last:
                    last, by = at, _name(e.get("done_by"))
    return count, last, by


def apply_notes(j: DispatchJob, notes: list[dict], today: str,
                checklist: tuple[int, datetime | None, str | None]) -> None:
    live = [n for n in notes if isinstance(n, dict) and not n.get("is_deleted")]
    j.notes_count = len(live)
    stamps = [t for t in (_time(n.get("created_at")) for n in live) if t]
    j.last_note_at = max(stamps) if stamps else None
    count, first, last, by = checklist[0], checklist[1], checklist[1], checklist[2]
    for n in live:
        if (n.get("note_type") or "").upper() not in ("IMAGE", "VIDEO"):
            continue
        at = _time(n.get("created_at"))
        if not at or c.local(at).date().isoformat() != today:
            continue
        count += max(1, len(n.get("attachments") or []))
        first = at if first is None or at < first else first
        if last is None or at > last:
            last, by = at, _name(n.get("created_by"))
    j.photo_day = today
    j.photos_today = count
    j.first_photo_at = first
    j.last_photo_at = last
    j.last_photo_by = by
    if count and last is not None:
        # The visit's record outlives the day (finding 3 of the 2026-10-01 review).
        j.visit_photo_at, j.visit_photo_count, j.visit_photo_by = last, count, by


def _visit_today(j: DispatchJob, today: str) -> bool:
    for t in (j.scheduled_start, j.scheduled_end):
        if t and c.local(t).date().isoformat() == today:
            return True
    return False


def read_jobs(db: Session, now: datetime) -> dict:
    today = c.local(now).date().isoformat()
    counts = {"listed": 0, "new": 0, "detail": 0, "notes": 0, "unknown_stages": [],
              "read_errors": 0, "closed_unlisted": 0}
    stop_details = False    # Zuper unavailable / rate-limited: no more per-job reads this pass
    listed: set[str] = set()
    existing = {j.job_uid: j for j in db.scalars(select(DispatchJob))}
    new: list[DispatchJob] = []
    known = set().union(*c.FIRST_CONTACT.values(), c.BOOK, c.CLOSED, c.WAITING_ON_AHS,
                        c.FOLLOW_UP, c.OLD_VISIT_NAMES,
                        c.AFTER_INSPECTION_STAGES, c.AFTER_REPAIR_STAGES,
                        c.INSPECTION_STAGES_RETAIL)
    for row in client.iter_all(client.path("jobs")):
        uid = row.get("job_uid")
        if not isinstance(uid, str) or not uid:
            continue
        counts["listed"] += 1
        listed.add(uid)
        j = existing.get(uid)
        if j is None:
            j = DispatchJob(job_uid=uid, first_seen_at=now)
            db.add(j)
            new.append(j)
            existing[uid] = j
        apply_row(j, row)
        if j.status and j.is_open and j.status not in known and not any(
                j.status.startswith(p) for p in c.NEEDS_DATE) and \
                "%s|%s" % (j.board, j.status) not in counts["unknown_stages"]:
            # "board|stage", so the page can leave out a board hidden from its reader.
            counts["unknown_stages"].append("%s|%s" % (j.board, j.status))
        updated = str(row.get("updated_at") or "")
        changed = j.zuper_updated_at != updated
        if j.photo_day != today:
            j.photo_day, j.photos_today = today, 0
            j.first_photo_at = j.last_photo_at = None
            j.last_photo_by = None
        if j.is_open and j.board not in c.IGNORED_BOARDS and not stop_details and (
                changed or j.notes_read_for is None or _visit_today(j, today)):
            # One job that cannot be read (deleted between the list and the read, a 404, a
            # bad answer) is skipped and read again next pass — it never throws away the
            # whole pass's work (review 2026-10-01).
            try:
                record = zapi_record(client.request("GET", client.path("job", uid=uid)))
                counts["detail"] += 1
                checklist = (0, None, None)
                if record:
                    checklist = apply_detail(j, record, today)
                    history.capture_statuses(db, record)
                notes = client.rows_of(client.request(
                    "GET", client.path("job_notes", uid=uid), params={"count": 100}))
                counts["notes"] += 1
                apply_notes(j, notes, today, checklist)
                j.notes_read_for = updated or "-"
            except client.ZuperError as exc:
                counts["read_errors"] += 1
                if exc.kind in ("unavailable", "rate_limited", "unauthorized"):
                    stop_details = True
                j.read_at = now
                continue                         # zuper_updated_at stays: retried next pass
        j.zuper_updated_at = updated
        j.read_at = now
    # A job Zuper's list no longer returns was deleted there: its items must close
    # (review 2026-10-01). Only after a list that read to its end, and never all at once.
    if counts["listed"]:
        gone = [j for uid, j in existing.items() if uid not in listed and j.is_open]
        if len(gone) <= max(20, counts["listed"] // 10):
            for j in gone:
                j.is_open = False
            counts["closed_unlisted"] = len(gone)
    counts["new"] = len(new)
    counts["new_jobs"] = [j.job_uid for j in new]
    return counts


def zapi_record(payload) -> dict | None:
    data = client.data_of(payload)
    return data if isinstance(data, dict) else None


def apply_call(row: DispatchCall, call: dict) -> None:
    row.occurred_at = _time(call.get("created_at"))
    row.direction = (call.get("direction") or "")[:20] or None
    row.status = (call.get("status") or "")[:30] or None
    row.duration_seconds = call.get("duration") if isinstance(call.get("duration"), int) \
        else None
    inbound = (call.get("direction") or "").upper() != "OUTGOING"
    side_cust = ((call.get("from") if inbound else call.get("to")) or {})
    number = ((side_cust.get("customer") or {}) if isinstance(side_cust, dict) else {}).get(
        "number") or (call.get("from_number") if inbound else call.get("to_number"))
    row.number = digits(number) or None
    staff_side = (call.get("to") if inbound else call.get("from")) or {}
    row.staff_name = ((staff_side.get("user") or {}).get("user_name")
                      if isinstance(staff_side, dict) else None)
    row.job_uids = [m.get("module_uid") for m in call.get("call_modules") or []
                    if isinstance(m, dict) and m.get("module") == "JOB"] or None
    row.summary = None
    for r in call.get("call_recordings") or []:
        text = summary_text(r.get("call_summary") if isinstance(r, dict) else None)
        if text:
            row.summary = text
            break


def summary_text(value) -> str | None:
    """Zuper's `call_summary` is an object — live on 2026-09-30 it carried only
    `{status: null, sentiment: "NEUTRAL"}`, no text (the account's call summaries are not being
    generated). Take text from it when there is any; never store the object itself."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in ("summary", "text", "call_summary", "content"):
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key].strip()
    return None


def read_calls(db: Session) -> int:
    payload = client.connect_calls(page=1, limit=100)
    rows = payload.get("data") if isinstance(payload, dict) else None
    seen = 0
    uids = [c.get("call_uid") for c in rows or [] if isinstance(c, dict) and c.get("call_uid")]
    known = {r.call_uid: r for r in db.scalars(select(DispatchCall).where(
        DispatchCall.call_uid.in_(uids)))} if uids else {}
    for call in rows or []:
        uid = call.get("call_uid") if isinstance(call, dict) else None
        if not uid:
            continue
        row = known.get(uid)
        if row is None:
            row = DispatchCall(call_uid=uid)
            db.add(row)
        apply_call(row, call)
        seen += 1
    return seen

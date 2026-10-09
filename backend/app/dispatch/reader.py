"""Read Zuper for the Dispatch page — GET requests and the call-history search, nothing else.

Each pass (every DISPATCH_POLL_SECONDS, on the worker's Zuper thread):

  1. the job LIST (every job, a few pages) -> one `dispatch_jobs` row per job;
  2. the whole job, for an open job that changed since the last pass or has a visit TODAY: who
     made the last move, the Technician field, the checklist pictures — and its stage moves go
     into `zuper_status_history` (app/zuper/history.py), which only takes whole records, never
     a list row, because a list row's moves carry no `done_by`;
  3. the job's NOTES under the same condition: when Zuper was last written to, and the
     pictures posted on the visit day (a picture is an IMAGE / VIDEO note);
  4. the latest page of Zuper Connect's call history -> `dispatch_calls`;
  5. Zuper's activity log, newest first, until a line already kept -> `dispatch_activity`
     (who moved, rescheduled, assigned or deleted what, and from where: office, field app or
     our own scripts). The notes of step 3 are kept as text too (`dispatch_notes`).

The first pass reads every open job once (about 150 jobs, two requests each); after that only
what changed, plus the day's visits.
"""
from __future__ import annotations

import ast
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DispatchActivity, DispatchCall, DispatchJob, DispatchNote
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
    # Which one is the mobile: a reminder text goes there (app/reminders, 2026-10-08).
    j.mobile = digits(nums.get("mobile")) or None
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


_TAG = re.compile(r"<[^>]+>")


def note_text(value) -> str | None:
    """A note's words: Zuper's editor stores HTML."""
    if not isinstance(value, str):
        return None
    text = _TAG.sub(" ", value.replace("<br>", "\n").replace("</p>", "\n"))
    text = re.sub(r"[ \t]+", " ", text.replace("&nbsp;", " ").replace("&amp;", "&")).strip()
    return text[:20000] or None


def store_notes(db: Session, j: DispatchJob, notes: list[dict]) -> int:
    """Keep every note's text (2026-10-08): what a technician dictated on site is the best
    record of the visit. Insert or refresh by note_uid; a note gone from Zuper is marked
    deleted, never removed here."""
    have = {n.note_uid: n for n in db.scalars(select(DispatchNote).where(
        DispatchNote.job_uid == j.job_uid))}
    seen = set()
    for n in notes:
        uid = n.get("note_uid") if isinstance(n, dict) else None
        if not isinstance(uid, str) or not uid:
            continue
        seen.add(uid)
        row = have.get(uid)
        if row is None:
            row = DispatchNote(note_uid=uid[:64], job_uid=j.job_uid)
            db.add(row)
            have[uid] = row
        row.job_number = j.job_number
        row.created_at = _time(n.get("created_at"))
        row.by_name = _name(n.get("created_by")) or row.by_name
        row.note_type = (n.get("note_type") or "TEXT")[:30]
        row.text = note_text(n.get("note"))
        row.attachments = len(n.get("attachments") or [])
        row.removed = bool(n.get("is_deleted"))
    for uid, row in have.items():
        if uid not in seen:
            row.removed = True
    return len(seen)


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
                store_notes(db, j, notes)
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
    # The list never carries the summary text (it is only in the call's details), so a
    # summary already fetched is kept, never wiped by the next list (2026-10-01).
    for r in call.get("call_recordings") or []:
        text = summary_text(r.get("call_summary") if isinstance(r, dict) else None)
        if text:
            row.summary = text
            break


def recorded(call: dict) -> bool:
    return any(isinstance(r, dict) and (r.get("recording_url") or r.get("call_recording_uid"))
               for r in call.get("call_recordings") or [])


def details_summary(payload) -> str | None:
    """Zuper Connect's summary of one call, from GET /calls/{uid}/details: the text and its
    "next action", as one string. None while Zuper has not written it yet."""
    data = client.data_of(payload) if isinstance(payload, dict) else None
    data = data if isinstance(data, dict) else (payload if isinstance(payload, dict) else {})
    found = [r.get("call_summary") for r in data.get("call_recordings") or []
             if isinstance(r, dict)] + [data.get("call_summary")]
    for cs in found:
        text = summary_text(cs)
        if text:
            nxt = cs.get("next_action") if isinstance(cs, dict) else None
            if isinstance(nxt, str) and nxt.strip():
                text += "\nNext step: " + nxt.strip()
            return text
    return None


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


# Zuper Connect writes a summary for every recorded call, but its call LIST leaves the text
# out — the web app only shows it once someone opens the call, which loads the details
# (verified live 2026-10-01: list {status: null}, details {summary, next_action, ...}). So
# each pass opens the recorded calls that still have no summary, newest first, a few at a time.
DETAILS_PER_PASS = 15
DETAILS_MAX_AGE = timedelta(days=14)


def read_calls(db: Session) -> int:
    payload = client.connect_calls(page=1, limit=100)
    rows = payload.get("data") if isinstance(payload, dict) else None
    seen = 0
    wanting: list[DispatchCall] = []
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
        if recorded(call) and not row.summary:
            wanting.append(row)
        seen += 1
    fetch_summaries(wanting)
    return seen


def fetch_summaries(rows: list[DispatchCall], now: datetime | None = None) -> int:
    """Open the details of recorded calls with no summary yet. A call whose summary is not
    written yet is simply asked again next pass; Zuper being down stops this pass."""
    now = now or datetime.now(UTC)
    rows = sorted((r for r in rows if r.occurred_at and
                   now - c.aware(r.occurred_at) <= DETAILS_MAX_AGE),
                  key=lambda r: c.aware(r.occurred_at), reverse=True)[:DETAILS_PER_PASS]
    got = 0
    for row in rows:
        try:
            text = details_summary(client.connect_read(
                "GET", "/calls/%s/details" % row.call_uid))
        except client.ZuperError as exc:
            if exc.kind in ("unavailable", "rate_limited", "unauthorized", "off", "no_key"):
                break
            continue
        if text:
            row.summary = text
            got += 1
    return got


# --------------------------------------------------------------------------- activity log

ACTIVITY_PAGE = 50
ACTIVITY_MAX_PAGES = 6      # 300 lines a pass at most; a pass runs every few minutes
SCRIPT_SOURCE = "API_KEY"   # our scripts' lines (the key belongs to a person's login)


def _source(meta) -> str | None:
    rs = meta.get("request_source") if isinstance(meta, dict) else None
    if isinstance(rs, str):
        try:
            rs = ast.literal_eval(rs)          # live: a Python-repr string of a dict
        except (ValueError, SyntaxError):
            return rs[:40] or None
    if isinstance(rs, dict) and isinstance(rs.get("type"), str):
        return rs["type"][:40]
    return None


def read_activity(db: Session) -> int:
    """New lines of Zuper's activity log, newest first, until a line already kept (or the page
    limit). Append-only: a kept line is never changed."""
    numbers = {j.job_uid: j.job_number for j in db.scalars(select(DispatchJob))}
    added = 0
    for page in range(1, ACTIVITY_MAX_PAGES + 1):
        rows = client.rows_of(client.request("GET", client.path("activities"), params={
            "count": ACTIVITY_PAGE, "page": page}))
        uids = [r.get("user_activity_uid") for r in rows if isinstance(r, dict)]
        known = set(db.scalars(select(DispatchActivity.activity_uid).where(
            DispatchActivity.activity_uid.in_([u for u in uids if u])))) if uids else set()
        caught_up = False
        for r in rows:
            uid = r.get("user_activity_uid") if isinstance(r, dict) else None
            if not isinstance(uid, str) or not uid:
                continue
            if uid in known:
                caught_up = True
                continue
            meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else None
            user = r.get("users") if isinstance(r.get("users"), dict) else {}
            module = (r.get("activity_module") or "")[:40] or None
            target = r.get("activity_action_uid") if isinstance(r.get("activity_action_uid"),
                                                               str) else None
            via = _source(meta)
            db.add(DispatchActivity(
                activity_uid=uid[:64], at=_time(r.get("created_at")),
                user_name=_name(user), user_uid=(user.get("user_uid") or "")[:64] or None,
                activity_type=(r.get("activity_type") or "")[:30] or None, module=module,
                message=(r.get("activity_message") or "")[:4000] or None,
                job_uid=target[:64] if module == "JOB" and target else None,
                job_number=numbers.get(target) if module == "JOB" else None,
                via=via, automatic=via == SCRIPT_SOURCE,
                meta={k: v for k, v in (meta or {}).items() if k != "request_source"} or None))
            known.add(uid)
            added += 1
        if caught_up or len(rows) < ACTIVITY_PAGE:
            break
    return added

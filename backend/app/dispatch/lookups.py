"""What the Dispatch assistant looks up to answer like the office does (2026-10-08).

Built from a working session in which a person checked the office's Excel against Zuper,
the calls and texts, and Zuper's activity log, to answer: which visits this week are not
right in Zuper, did the customer really confirm, who changed what, where is the technician.
Every function here READS: nothing is written to Zuper or sent to anyone.

  day_schedule(...)      visits in a date range, per technician, with what looks wrong
  search_comms(...)      calls and texts by any number, name or word, full transcripts on ask
  activity_log(...)      Zuper's activity log: who did what, from the office, the field app
                         or our own scripts (which Zuper shows under a person's name)
  job_notes(...)         the text of a job's notes (a technician's on-site dictation)
  tech_day(...)          one technician's day reconstructed from visits, moves, notes,
                         their own calls and the app's log — Zuper has no live location
  compare_schedule(...)  an attached Excel's dated visits against Zuper and the calls

Boards hidden from the person asking stay hidden: their jobs, calls, notes and log lines
are left out (pipeline permissions, CLAUDE.md).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Contact,
    Conversation,
    ConversationEvent,
    DispatchActivity,
    DispatchCall,
    DispatchJob,
    DispatchNote,
    EventType,
    NumberThread,
    NumberThreadEvent,
    ZuperStatusHistory,
)
from . import compare
from . import config as c
from .comms import NOT_SENT, digits

TRANSCRIPT_MAX = 12000        # one transcript, when the full text is asked for
SHORT = 400                   # a text or summary in a list
NO_LOCATION = ("Zuper reports no live location (tracking is off). This day is reconstructed "
               "from visits, stage moves, notes, the technician's own calls and the app's "
               "activity log; say so when you tell anyone where the technician is.")
# Stages that mean the visit is booked and on — a visit sitting in any other stage on its
# day is worth a word (e.g. Reschedule Required, Not Answering, Review Received).
VISIT_WORDS = ("schedul", "day-before", "same-day", "on the way", "in process", "in progress",
               "arrived", "antonio on the way", "call back")


def booked_stage(status: str | None) -> bool:
    """Does this stage mean the visit is on? "Reschedule Required" contains "schedul" and is
    exactly the opposite."""
    s = (status or "").lower()
    return "reschedul" not in s and any(w in s for w in VISIT_WORDS)


def _v(x) -> str:
    return getattr(x, "value", x) or ""


def _hm(dt: datetime | None) -> str | None:
    dt = c.aware(dt)
    return c.local(dt).strftime("%I:%M %p").lstrip("0") if dt else None


def _when(dt: datetime | None) -> str | None:
    dt = c.aware(dt)
    return c.local(dt).strftime("%a %m/%d %I:%M %p") if dt else None


def parse_day(text: str | None, now: datetime) -> date | None:
    """today / tomorrow / yesterday / 2026-10-08 / 10/08 / Thu 10/08 -> a New York date."""
    today = c.local(now).date()
    t = (text or "").strip().lower()
    if not t or t == "today":
        return today
    if t == "tomorrow":
        return today + timedelta(days=1)
    if t == "yesterday":
        return today - timedelta(days=1)
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t)
    if m:
        year = int(m[3]) if m[3] else today.year
        year = year + 2000 if year < 100 else year
        try:
            d = date(year, int(m[1]), int(m[2]))
        except ValueError:
            return None
        # "01/05" read in December is next year's.
        if not m[3] and (d - today).days < -180:
            d = date(year + 1, d.month, d.day)
        return d
    return None


def _visible(j: DispatchJob | None, hidden: set[str]) -> bool:
    return j is not None and not (j.board and j.board in hidden)


def _job_line(j: DispatchJob) -> dict:
    return {"job_number": j.job_number, "customer": j.customer_name, "board": j.board,
            "stage": j.status, "stage_since": _when(j.status_since),
            "address": ", ".join(x for x in (j.address, j.city) if x) or None,
            "visit": ("%s %s-%s" % (c.local(c.aware(j.scheduled_start)).strftime("%a %m/%d"),
                                    _hm(j.scheduled_start), _hm(j.scheduled_end))
                      if c.aware(j.scheduled_start) else None),
            "assigned": j.assigned or [], "technician_field": j.technician,
            "open": j.is_open}


def _phone_jobs(db: Session) -> dict[str, list[DispatchJob]]:
    out: dict[str, list[DispatchJob]] = {}
    for j in db.scalars(select(DispatchJob)):
        for p in j.phones or []:
            out.setdefault(p, []).append(j)
    return out


def _tech_match(name: str | None, who: str) -> bool:
    who = who.strip().lower()
    return bool(name) and bool(who) and (who in name.lower() or name.lower() in who)


# ---------------------------------------------------------------------------- the schedule

def day_schedule(db: Session, now: datetime, hidden: set[str], *, date_from: str | None,
                 date_to: str | None = None, technician: str | None = None) -> dict:
    start = parse_day(date_from, now)
    end = parse_day(date_to, now) if date_to else start
    if start is None or end is None:
        return {"error": "dates must look like today, tomorrow, 2026-10-08 or 10/08"}
    if (end - start).days > 14:
        end = start + timedelta(days=14)
    days: dict[str, list[dict]] = {}
    for j in db.scalars(select(DispatchJob).where(DispatchJob.scheduled_start.is_not(None))):
        s = c.aware(j.scheduled_start)
        d = c.local(s).date()
        if not (start <= d <= end) or not _visible(j, hidden):
            continue
        # Whose visit it is: the person ASSIGNED (routes follow them); the Technician field
        # only when nobody is.
        if technician and not any(_tech_match(n, technician)
                                  for n in (j.assigned or [j.technician])):
            continue
        warn = []
        stage = (j.status or "").lower()
        if not j.is_open:
            warn.append("the job is closed or gone from Zuper (%s)" % (j.status or "deleted"))
        elif not booked_stage(stage):
            warn.append("its stage “%s” is not a booked-visit stage" % j.status)
        if not j.assigned:
            warn.append("nobody is assigned, so it is on no route")
        if j.technician and j.assigned and not any(
                _tech_match(j.technician, a) for a in j.assigned):
            warn.append("the Technician field says %s but %s is assigned"
                        % (j.technician, ", ".join(j.assigned)))
        days.setdefault(d.isoformat(), []).append({**_job_line(j), "start": _hm(s),
                                                   "end": _hm(j.scheduled_end),
                                                   "_sort": s, "check": warn or None})
    out = []
    for d in sorted(days):
        rows = sorted(days[d], key=lambda r: r.pop("_sort"))
        by_tech: dict[str, list[dict]] = {}
        for r in rows:
            for t in (r["assigned"] or ["(nobody assigned)"]):
                by_tech.setdefault(t, []).append(r)
        out.append({"day": date.fromisoformat(d).strftime("%a %m/%d"),
                    "technicians": [{"technician": t, "visits": v} for t, v in by_tech.items()]})
    return {"from": start.isoformat(), "to": end.isoformat(), "days": out,
            "note": "Only visits with a time in Zuper are here. A job with no time is on no "
                    "route; find_jobs lists jobs by stage."}


# ------------------------------------------------------------------------------ the comms

def _events(db: Session, since: datetime):
    """Every call and text since `since`, as dicts, oldest first, NOT truncated."""
    rows = []
    q = (select(ConversationEvent, Contact.phone, Contact.first_name, Contact.last_name)
         .join(Conversation, Conversation.id == ConversationEvent.conversation_id)
         .join(Contact, Contact.id == Conversation.contact_id)
         .where(ConversationEvent.type.in_((EventType.CALL, EventType.SMS)),
                ConversationEvent.occurred_at >= since))
    for e, phone, first, last in db.execute(q):
        rows.append((e, phone, " ".join(x for x in (first, last) if x) or None))
    q = (select(NumberThreadEvent, NumberThread.phone, NumberThread.quo_name)
         .join(NumberThread, NumberThread.id == NumberThreadEvent.number_thread_id)
         .where(NumberThreadEvent.type.in_((EventType.CALL, EventType.SMS)),
                NumberThreadEvent.occurred_at >= since))
    rows += [(e, phone, name) for e, phone, name in db.execute(q)]
    for e, phone, name in rows:
        is_call = _v(e.type) == EventType.CALL.value
        out = _v(e.direction) == "OUTBOUND"
        if not is_call and out and _v(e.delivery_status) in NOT_SENT:
            continue
        body = e.body or ""
        yield {"at": c.aware(e.occurred_at), "phone": digits(phone), "name": name,
               "source": "Quo" if (e.source_system or "") == "OpenPhone" else "CRM line",
               "kind": "call" if is_call else "text", "who": "us" if out else "customer",
               "status": e.call_status, "seconds": e.duration_seconds,
               "text": body, "transcript": e.transcript or "", "staff": None}
    for z in db.scalars(select(DispatchCall).where(DispatchCall.occurred_at >= since)):
        yield {"at": c.aware(z.occurred_at), "phone": z.number, "name": None,
               "source": "Zuper Connect", "kind": "call",
               "who": "us" if (z.direction or "").upper() == "OUTGOING" else "customer",
               "status": z.status, "seconds": z.duration_seconds, "text": z.summary or "",
               "transcript": "", "staff": z.staff_name}


# A number written in a call or text: "my son 954-701-8639", "call her directly 954 573 0002".
PHONE_IN_TEXT = re.compile(r"(?<!\d)(?:\+?1[\s.\-]?)?\(?(\d{3})\)?[\s.\-]?(\d{3})[\s.\-]?"
                           r"(\d{4})(?!\d)")
SHARED = 3          # a number in this many customers' conversations is one of OUR lines


def customer_phones(db: Session, now: datetime, events: list[dict] | None = None,
                    days: int = 30) -> dict[str, set[str]]:
    """Every number each job's customer can be reached on: the Zuper customer's, plus a number
    given in their OWN calls or texts (live 2026-10-08: Janeth Palacio's son confirmed on a
    number she gave on 10/02; Gladys Barona's daughter texted her mother's). Our own lines are
    in every conversation (transcript speaker labels), so a number in SHARED or more
    customers' conversations is ours and never added. A number that is ANOTHER job's customer
    IS added: live, Janeth Palacio's son is himself the customer on his own AHS job."""
    from .. import crmlink
    evs = events if events is not None else list(_events(db, now - timedelta(days=days)))
    by_phone: dict[str, list[dict]] = {}
    for e in evs:
        if e["phone"]:
            by_phone.setdefault(e["phone"], []).append(e)
    jobs = list(db.scalars(select(DispatchJob)))
    mentioned: dict[str, set[str]] = {}
    # Counted per CUSTOMER (their set of numbers), not per job: live, Janeth Palacio has six
    # jobs on one number, and counting jobs made her son's number look like one of ours.
    seen_by: dict[str, set[frozenset]] = {}
    for j in jobs:
        found = set()
        for p in j.phones or []:
            for e in by_phone.get(p, []):
                for m in PHONE_IN_TEXT.finditer(" ".join((e["text"] or "", e["transcript"] or ""))):
                    found.add(m[1] + m[2] + m[3])
        mentioned[j.job_uid] = found
        who = frozenset(j.phones or [j.job_uid])
        for n in found - set(j.phones or []):
            seen_by.setdefault(n, set()).add(who)
    count = {n: len(customers) for n, customers in seen_by.items()}
    ours = {digits(crmlink.DEFAULT_FROM_NUMBER)} | {n for n, k in count.items() if k >= SHARED}
    return {j.job_uid: set(j.phones or []) | (mentioned[j.job_uid] - ours) for j in jobs}


def _comm_out(ev: dict, full: bool, jobs: list[DispatchJob]) -> dict:
    text = " ".join((ev["text"] or "").split())
    out = {"at": _when(ev["at"]), "source": ev["source"], "kind": ev["kind"], "from": ev["who"],
           "phone": ev["phone"], "contact": ev["name"],
           "jobs": ["#%s %s" % (j.job_number, j.status) for j in jobs][:4] or None}
    if ev["kind"] == "call":
        out.update(status=ev["status"], seconds=ev["seconds"], staff=ev["staff"],
                   summary=(text[:SHORT * 3] if full else text[:SHORT]) or None)
        tr = " ".join((ev["transcript"] or "").split())
        if tr:
            # The agreement is at the END of a call (live 2026-10-08: "mañana de una a 3 —
            # Perfecto" came after the first 400 characters): the start AND the end.
            out["transcript"] = tr[:TRANSCRIPT_MAX] if full else (
                tr if len(tr) <= 900 else tr[:200] + " … " + tr[-700:])
    else:
        out["text"] = text[:SHORT * 3]
    return out


def search_comms(db: Session, now: datetime, hidden: set[str], *, phone: str | None = None,
                 text: str | None = None, name: str | None = None, job_number: str | None = None,
                 days: int = 14, full: bool = False, limit: int = 40) -> dict:
    days = max(1, min(int(days or 14), 60))
    since = now - timedelta(days=days)
    pj = _phone_jobs(db)
    phones: set[str] = set()
    if phone:
        if not digits(phone):
            return {"error": "a phone number needs 10 digits"}
        phones.add(digits(phone))
    if job_number:
        j = db.scalar(select(DispatchJob).where(
            DispatchJob.job_number == str(job_number).lstrip("#")))
        if not _visible(j, hidden):
            return {"error": "no such job"}
        phones |= customer_phones(db, now).get(j.job_uid, set(j.phones or []))
    words = [w for w in re.split(r"\s+", (text or "").lower()) if w]
    nm = (name or "").strip().lower()
    hits = []
    for ev in _events(db, since):
        if phones and ev["phone"] not in phones:
            continue
        hay = " ".join(x for x in (ev["text"], ev["transcript"], ev["name"]) if x).lower()
        if words and not all(w in hay for w in words):
            continue
        if nm and nm not in hay and not any(
                nm in (j.customer_name or "").lower() for j in pj.get(ev["phone"], [])):
            continue
        jobs = pj.get(ev["phone"], [])
        if hidden and jobs and all(j.board in hidden for j in jobs):
            continue                                   # a hidden board's customer
        hits.append((ev, [j for j in jobs if _visible(j, hidden)]))
    hits.sort(key=lambda x: x[0]["at"] or now)
    more = max(0, len(hits) - limit)
    hits = hits[-limit:]
    return {"since": _when(since), "found": len(hits) + more, "shown": len(hits),
            "older_not_shown": more,
            "items_oldest_first": [_comm_out(ev, full, jobs) for ev, jobs in hits],
            "note": "Quo transcripts show only what was captured: a reply missing from a "
                    "transcript is NOT proof the customer said nothing; a reply that is there "
                    "is. Zuper Connect calls have a summary, never a transcript."}


# -------------------------------------------------------------------------- Zuper's log

VIA = {"WEB_APP": "office (Zuper web)", "zuper_v3_android": "field app (Android)",
       "zuper_v3_ios": "field app (iPhone)",
       "API_KEY": "OUR SCRIPT (routes / tasks / proposals / CRM) — Zuper shows the key "
                  "owner's name, the person did NOT do this"}


def _activity_out(a: DispatchActivity) -> dict:
    return {"at": _when(a.at), "who": a.user_name, "via": VIA.get(a.via or "", a.via or
                                                                    "Zuper itself"),
            "what": a.message, "job_number": a.job_number, "kind": a.activity_type}


def activity_log(db: Session, now: datetime, hidden: set[str], *, day: str | None = None,
                 hours: int | None = None, job_number: str | None = None,
                 person: str | None = None, text: str | None = None,
                 include_scripts: bool = False, limit: int = 80) -> dict:
    q = select(DispatchActivity)
    if hours:
        q = q.where(DispatchActivity.at >= now - timedelta(hours=max(1, min(int(hours), 24 * 14))))
    else:
        d = parse_day(day, now)
        if d is None:
            return {"error": "day must look like today, yesterday, 2026-10-08 or 10/08"}
        start = datetime(d.year, d.month, d.day, tzinfo=c.TZ)
        q = q.where(DispatchActivity.at >= start, DispatchActivity.at < start + timedelta(days=1))
    if job_number:
        q = q.where(DispatchActivity.job_number == str(job_number).lstrip("#"))
    boards = {j.job_uid: j.board for j in db.scalars(select(DispatchJob))}
    rows = []
    for a in db.scalars(q.order_by(DispatchActivity.at)):
        if a.job_uid and boards.get(a.job_uid) in hidden:
            continue
        if a.automatic and not include_scripts:
            continue
        if person and not _tech_match(a.user_name, person):
            continue
        if text and text.lower() not in (a.message or "").lower():
            continue
        rows.append(a)
    deleted = [_activity_out(a) for a in rows if (a.activity_type or "").upper() == "DELETE"]
    more = max(0, len(rows) - limit)
    return {"lines": len(rows), "older_not_shown": more,
            "log_oldest_first": [_activity_out(a) for a in rows[-limit:]],
            "deletions": deleted or None,
            "note": "Lines our scripts wrote are left out unless include_scripts is true; "
                    "they appear in Zuper under the API key owner's name. A deletion goes "
                    "against the owner's rule (jobs are never deleted): name it. Zuper can "
                    "restore a deleted job for 90 days (Settings → Data Administration → "
                    "Recovery)."}


def job_notes(db: Session, hidden: set[str], job_number: str, limit: int = 30) -> dict:
    j = db.scalar(select(DispatchJob).where(DispatchJob.job_number == str(job_number).lstrip("#")))
    if not _visible(j, hidden):
        return {"error": "no such job"}
    notes = db.scalars(select(DispatchNote).where(
        DispatchNote.job_uid == j.job_uid, DispatchNote.removed.is_(False))
        .order_by(DispatchNote.created_at)).all()[-limit:]
    return {"job": _job_line(j), "notes_oldest_first": [
        {"at": _when(n.created_at), "by": n.by_name, "type": n.note_type,
         "text": n.text, "pictures": n.attachments or None} for n in notes],
        "note": "A note's text may be a voice dictation: read it for meaning, not spelling."}


# --------------------------------------------------------------------- a technician's day

def tech_day(db: Session, now: datetime, hidden: set[str], *, technician: str,
             day: str | None = None) -> dict:
    d = parse_day(day, now)
    if d is None or not (technician or "").strip():
        return {"error": "a technician's name and a day are needed"}
    start = datetime(d.year, d.month, d.day, tzinfo=c.TZ)
    end = start + timedelta(days=1)
    jobs = {j.job_uid: j for j in db.scalars(select(DispatchJob)) if _visible(j, hidden)}
    timeline: list[tuple[datetime, dict]] = []
    visits = []
    for j in jobs.values():
        s = c.aware(j.scheduled_start)
        if s and start <= s < end and any(_tech_match(n, technician)
                                           for n in (j.assigned or [j.technician])):
            visits.append(j)
    for m in db.scalars(select(ZuperStatusHistory).where(
            ZuperStatusHistory.changed_at >= start, ZuperStatusHistory.changed_at < end)):
        j = jobs.get(m.job_uid)
        if j and (j in visits or _tech_match(m.done_by_name, technician)):
            timeline.append((c.aware(m.changed_at), {
                "what": "stage → %s" % m.status_name, "job": "#%s" % j.job_number,
                "by": m.done_by_name, "address": _job_line(j)["address"]}))
    for n in db.scalars(select(DispatchNote).where(DispatchNote.created_at >= start,
                                                   DispatchNote.created_at < end)):
        j = jobs.get(n.job_uid)
        if j and _tech_match(n.by_name, technician):
            timeline.append((c.aware(n.created_at), {
                "what": "note: %s" % ((n.text or "(no text — a picture or recording)")[:300]),
                "job": "#%s" % j.job_number, "by": n.by_name,
                "address": _job_line(j)["address"]}))
    for a in db.scalars(select(DispatchActivity).where(DispatchActivity.at >= start,
                                                       DispatchActivity.at < end)):
        if a.automatic or not _tech_match(a.user_name, technician):
            continue
        j = jobs.get(a.job_uid) if a.job_uid else None
        if a.job_uid and j is None:
            continue                                   # a hidden board's job
        timeline.append((c.aware(a.at), {
            "what": a.message, "via": VIA.get(a.via or "", a.via),
            "job": "#%s" % j.job_number if j else None,
            "address": _job_line(j)["address"] if j else None}))
    pj = _phone_jobs(db)
    for z in db.scalars(select(DispatchCall).where(DispatchCall.occurred_at >= start,
                                                   DispatchCall.occurred_at < end)):
        if not _tech_match(z.staff_name, technician):
            continue
        js = [x for x in pj.get(z.number or "", []) if _visible(x, hidden)]
        timeline.append((c.aware(z.occurred_at), {
            "what": "Zuper Connect call %s %s (%ss): %s" % (
                (z.direction or "").lower(), (z.status or "").lower(), z.duration_seconds or 0,
                (z.summary or "no summary")[:500]),
            "job": ", ".join("#%s" % x.job_number for x in js[:2]) or None,
            "address": _job_line(js[0])["address"] if js else None}))
    timeline.sort(key=lambda x: x[0])
    last = next((e for _, e in reversed(timeline) if e.get("address")), None)
    return {"technician": technician, "day": d.strftime("%a %m/%d"),
            "visits_booked": [{**_job_line(j), "start": _hm(j.scheduled_start)}
                              for j in sorted(visits, key=lambda x: c.aware(x.scheduled_start))],
            "timeline": [{"at": _hm(t), **e} for t, e in timeline],
            "last_place_with_evidence": last,
            "note": NO_LOCATION + " Texts from the office line are shared by everyone: use "
                    "search_comms for a visit's customer to see 'on the way' messages."}


# ------------------------------------------------------------- the Excel against Zuper

DATE_HEADERS = ("day", "date", "booked", "scheduled", "visit", "when", "appointment")
TECH_HEADERS = ("tech", "technician")
TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:[\u2013\u2014-]|to)+\s*"
                     r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.IGNORECASE)


def _window(text: str) -> tuple[int, int] | None:
    """'7:30-9:30 AM' / '10:30 AM-12:30 PM' / '2:00-4:00' (any dash) -> (start, end) in
    minutes after midnight. With no AM/PM at all, 1-6 is the afternoon and 7-12 the morning
    (a working day)."""
    m = TIME_RE.search(text or "")
    if not m:
        return None
    h1, m1, ap1, h2, m2, ap2 = m.groups()
    h1, h2, m1, m2 = int(h1), int(h2), int(m1 or 0), int(m2 or 0)
    if h1 > 12 or h2 > 12:
        return None

    def to24(h, ap):
        if ap is None:
            ap = "pm" if 1 <= h <= 6 or h == 12 else "am"
        return (h % 12) + (12 if ap == "pm" else 0)
    ap1 = (ap1 or "").lower() or None
    ap2 = (ap2 or "").lower() or None
    e = to24(h2, ap2) * 60 + m2
    if ap1:
        s = to24(h1, ap1) * 60 + m1
    else:
        s = to24(h1, ap2) * 60 + m1
        if s > e:                           # "11:00-1:00 PM": the start is the morning
            s = to24(h1, "am") * 60 + m1
    return s, e


def _fmt(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return "%d:%02d %s" % ((h - 1) % 12 + 1, m, "AM" if h < 12 else "PM")


def _file_visits(sheets: list[dict], start: date, end: date, now: datetime
                 ) -> tuple[list[dict], list[dict]]:
    """Every row that names a day in range: customer, day, window, technician, status — and
    where the file disagrees with itself. A customer listed on several sheets for the same day
    is one visit (the row with a time window wins). When a sheet with a time window (the day
    plan) has the customer on ANOTHER day, a summary sheet's row for this day is not a visit:
    it is reported as a disagreement (live 2026-10-08: By City kept Diana Reyes on Friday
    after the Schedule sheet moved her to Saturday)."""
    found: dict[tuple, dict] = {}
    planned: dict[str, set[date]] = {}            # customer -> days on a sheet with a window
    every: list[dict] = []
    for sheet in sheets:
        cols = compare.columns_of(sheet["columns"])
        datecols = [h for h in sheet["columns"] if any(k in h.lower() for k in DATE_HEADERS)]
        techcol = next((h for h in sheet["columns"]
                        if " ".join(compare.words(h)) in TECH_HEADERS), None)
        windowcol = next((h for h in sheet["columns"] if "window" in h.lower()
                          or h.lower() == "time"), None)
        carry = None                         # a day written once above its rows
        for r in sheet["rows"]:
            v = r["values"]
            name = v.get(cols["name"] or "", "")
            dtext = " ".join(v.get(h, "") for h in datecols)
            d = parse_day(dtext, now) if re.search(r"\d{1,2}/\d{1,2}|\d{4}-\d", dtext) else None
            if d and not name:
                carry = d
                continue
            d = d or carry
            if not name or d is None:
                continue
            if windowcol:
                planned.setdefault(" ".join(compare.words(name)), set()).add(d)
            if not (start <= d <= end):
                continue
            text = " ".join([v.get(windowcol or "", ""), dtext])
            win = _window(text)
            tech = v.get(techcol or "", "") or next(
                (t for t in re.findall(r"\((\w+)\)", dtext)), "")
            key = (" ".join(compare.words(name)), d)
            row = {"sheet": sheet["name"], "row": r["row"], "customer": name, "day": d,
                   "window": win, "technician": tech or None,
                   "address": v.get(cols["address"] or "", "") or None,
                   "phone": v.get(cols["phone"] or "", "") or None,
                   "job_number_in_file": v.get(cols["job"] or "", "") or None,
                   "status_in_file": v.get(cols["status"] or "", "") or None,
                   "planned": bool(windowcol)}
            every.append(row)
    for row in every:
        key = (" ".join(compare.words(row["customer"])), row["day"])
        have = found.get(key)
        if have is None or (row["window"] and not have["window"]):
            found[key] = row
    visits, disagree = [], []
    plan_days = {d for days in planned.values() for d in days}
    for (who, d), row in found.items():
        days = planned.get(who, set())
        if not row["planned"] and d in plan_days and d not in days:
            # The day plan (a sheet with times) covers this day and does not have this
            # customer: a Callbacks / By City row is not a visit (live 2026-10-09: Byfield and
            # Rivera were "booked today" from the Callbacks sheet only).
            disagree.append({"customer": row["customer"], "this_sheet": row["sheet"],
                             "row": row["row"], "says": d.strftime("%a %m/%d"),
                             "day_plan_says": ", ".join(x.strftime("%a %m/%d")
                                                        for x in sorted(days))
                             or "not on the day plan that day"})
            continue
        if not row["planned"] and days and d not in days:
            disagree.append({"customer": row["customer"], "this_sheet": row["sheet"],
                             "row": row["row"], "says": d.strftime("%a %m/%d"),
                             "day_plan_says": ", ".join(x.strftime("%a %m/%d")
                                                        for x in sorted(days))})
            continue
        visits.append(row)
    return sorted(visits, key=lambda x: (x["day"], x["window"] or (0, 0))), disagree


def _match(row: dict, jobs: list[DispatchJob]) -> list[DispatchJob]:
    """The row's Zuper job(s): job number, phone, name (+ address when shared), address."""
    for num in re.findall(r"\d{2,6}", row["job_number_in_file"] or ""):
        hit = [j for j in jobs if j.job_number == num]
        if hit:
            return hit
    p = digits(row["phone"])
    if p:
        hit = [j for j in jobs if p in (j.phones or [])]
        if hit:
            return hit
    key = compare.addr_key(row["address"])
    by_name = []
    for n in compare.names_in(row["customer"]):
        t = n.split()
        for j in jobs:
            jn = " ".join(compare.words(j.customer_name))
            if jn == n or (len(t) > 1 and jn.split()[:1] == t[:1] and jn.split()[-1:] == t[-1:]):
                by_name.append(j)
    if by_name:
        if key and len({compare.addr_key(j.address) for j in by_name}) > 1:
            same = [j for j in by_name if compare.addr_key(j.address) == key]
            return same or by_name
        return by_name
    return [j for j in jobs if key and compare.addr_key(j.address) == key]


def compare_schedule(db: Session, now: datetime, hidden: set[str], sheets: list[dict], *,
                     date_from: str | None, date_to: str | None = None) -> dict:
    start = parse_day(date_from, now)
    end = parse_day(date_to, now) if date_to else start
    if start is None or end is None:
        return {"error": "dates must look like today, tomorrow, 2026-10-08 or 10/08"}
    jobs = [j for j in db.scalars(select(DispatchJob)) if _visible(j, hidden)]
    pj: dict[str, list[DispatchJob]] = {}
    for j in jobs:
        for p in j.phones or []:
            pj.setdefault(p, []).append(j)
    recent = list(_events(db, now - timedelta(days=10)))
    reach = customer_phones(db, now)
    out, used = [], set()
    visits, disagree = _file_visits(sheets, start, end, now)
    for row in visits:
        cands = _match(row, jobs)
        live = [j for j in cands if j.is_open] or cands
        # The job booked nearest the file's day is the one this row is about.
        def gap(j, d=row["day"]):
            s = c.aware(j.scheduled_start)
            return abs((c.local(s).date() - d).days) if s else 999
        live.sort(key=gap)
        j = live[0] if live else None
        item = {"file": {"sheet": row["sheet"], "row": row["row"], "customer": row["customer"],
                         "day": row["day"].strftime("%a %m/%d"),
                         "window": "%s-%s" % (_fmt(row["window"][0]), _fmt(row["window"][1]))
                         if row["window"] else None, "technician": row["technician"],
                         "status": row["status_in_file"]},
                "zuper": _job_line(j) if j else None,
                "other_open_jobs": ["#%s %s" % (x.job_number, x.status) for x in live[1:4]]
                or None, "differences": []}
        diff = item["differences"]
        if j is None:
            diff.append("not in Zuper: no job by job number, phone, name or address")
        else:
            used.add(j.job_uid)
            s = c.aware(j.scheduled_start)
            if not j.is_open:
                diff.append("the Zuper job is closed or deleted (%s)" % (j.status or "gone"))
            if not s:
                diff.append("Zuper has NO visit time — the visit is on no route")
            elif c.local(s).date() != row["day"]:
                diff.append("Zuper has the visit on %s, the file on %s"
                            % (c.local(s).strftime("%a %m/%d"), row["day"].strftime("%a %m/%d")))
            elif row["window"]:
                zs = c.local(s).hour * 60 + c.local(s).minute
                if abs(zs - row["window"][0]) >= 30:
                    diff.append("time differs: file %s, Zuper %s" % (_fmt(row["window"][0]),
                                                                     _hm(s)))
            if row["technician"] and j.assigned and not any(
                    _tech_match(a, row["technician"]) for a in j.assigned):
                diff.append("technician differs: file %s, Zuper %s"
                            % (row["technician"], ", ".join(j.assigned)))
            if j.is_open and not booked_stage(j.status):
                diff.append("Zuper stage “%s” is not a booked-visit stage" % j.status)
            phones = reach.get(j.job_uid, set(j.phones or []))
            talk = [e for e in recent if e["phone"] in phones][-8:]
            theirs = [e for e in talk if e["who"] == "customer"]
            if theirs:
                last = theirs[-1]
                item["newest_from_customer"] = "%s %s %s: %s" % (
                    _when(last["at"]), last["source"], last["kind"],
                    " ".join((last["text"] or last["transcript"] or "").split())[-300:])
            extra = sorted(phones - set(j.phones or []))
            if extra:
                item["also_reached_on"] = extra          # numbers given in their own calls
            item["recent_calls_and_texts"] = [_comm_out(e, False, []) for e in talk] or None
        out.append(item)
    extra = []
    for j in jobs:
        s = c.aware(j.scheduled_start)
        if s and start <= c.local(s).date() <= end and j.job_uid not in used and j.is_open:
            extra.append({**_job_line(j), "difference": "in Zuper on this day, not in the file"})
    for item in out:
        if not item["differences"] and item["zuper"]:
            item["matches"] = "day, time, technician and stage agree with Zuper"
    return {"from": start.isoformat(), "to": end.isoformat(), "visits_in_file": len(out),
            "visits": out, "zuper_visits_not_in_file": extra or None,
            "file_disagrees_with_itself": disagree or None,
            "how_to_judge": "A visit is CONFIRMED only when a call or text shows the customer "
                            "agreeing to that day (and time). The file saying 'confirmed' is "
                            "not proof. Check recent_calls_and_texts (a call's END holds the "
                            "agreement; read it whole with search_comms(full=true) when cut); "
                            "also_reached_on are numbers the customer gave (a son, a tenant). "
                            "An entry with `matches` agrees with Zuper: say so, never call it "
                            "missing. file_disagrees_with_itself: a summary sheet still has an "
                            "old day; the day plan (the sheet with times) wins."}

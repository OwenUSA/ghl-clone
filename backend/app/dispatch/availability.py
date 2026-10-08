"""What each customer said about WHEN they can have the visit (2026-10-08).

The owner's question: "what if a customer said on a call or a text that they won't be available
next week — will the schedule consider that?" Before this, only the chat could, and only from
the last three messages cut to a few words. Now, for every job that needs a visit, the AI reads
ALL that customer's calls and texts of the last three weeks (Quo transcripts in full, Zuper
Connect summaries, texts, every number on the job) and writes down the customer's limits with
the quotes. Every plan — the button, the chat, the Excel — applies them.

  * Read again only when the customer's calls and texts change (`fingerprint`).
  * An office correction stands; newer messages only mark it `newer_messages`.
  * Off, paused or over the daily cap: nothing is read and the saved limits still apply.
  * Runs on the AI thread a few jobs a minute (`refresh`), and on ask from the plan panel.
Read only toward Zuper and the customer: nothing is written to Zuper, nobody is contacted.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai import providers
from ..models import DispatchAvailability, DispatchJob
from . import config as c
from . import lookups

log = logging.getLogger("dispatch.availability")

DAYS_BACK = 21
PER_TICK = 6
TEXT_MAX = 24000              # characters of conversation sent for one customer
MAX_TOKENS = 2000
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

SYSTEM = """You read a roofing company's calls and texts with ONE customer and write down what \
the customer (or someone speaking for them: family, a tenant, a property manager) said about \
WHEN a visit can or cannot happen. Today is {today} (New York).

Answer with ONE JSON object and nothing else:
{{"limits": {{"not_before": "YYYY-MM-DD" or null,
             "blocked": [{{"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}}],
             "only_days": ["Mon", ...], "not_days": ["Tue", ...],
             "after": "HH:MM" or null, "before": "HH:MM" or null,
             "technician": "Owen" or "Antonio" or null}},
 "summary": "one short sentence for the office, or empty",
 "evidence": [{{"at": "<the message's date and time as shown>", "quote": "<their words>"}}],
 "confidence": "high" | "medium" | "low"}}

Rules:
- Only what was said about being available for the visit. Never invent.
- Turn relative dates into dates using the date of the message they were said in: "next \
week" said on Thu 10/08 is blocked 2026-10-12 to 2026-10-17; "after the 20th" is not_before \
the 21st; "I'm traveling until Monday" is not_before that Monday.
- "only after 3 PM" / "can be there after 3" is after 15:00; "has to be in the morning" is \
before 12:00; "not on Tuesdays" is not_days Tue.
- The newest message wins when the customer changed their mind. Leave out a limit that ended \
before today.
- A booked appointment, a request to be called back, or "any day is fine" is not a limit.
- Nothing about availability: empty limits, empty evidence, summary empty."""


def _events_by_phone(db: Session, now: datetime) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for ev in lookups._events(db, now - timedelta(days=DAYS_BACK)):
        if ev["phone"]:
            out.setdefault(ev["phone"], []).append(ev)
    for evs in out.values():
        evs.sort(key=lambda e: e["at"] or now)
    return out


def _fingerprint(phones: list[str], evs: list[dict]) -> str:
    last = max((e["at"] for e in evs if e["at"]), default=None)
    raw = "%s|%d|%s" % (",".join(sorted(phones)), len(evs), last.isoformat() if last else "")
    return hashlib.sha1(raw.encode()).hexdigest()[:40]


def _conversation(evs: list[dict]) -> str:
    """The customer's calls and texts, oldest first, whole — cut from the OLDEST end."""
    lines = []
    for e in reversed(evs):
        when = c.local(e["at"]).strftime("%a %m/%d %I:%M %p") if e["at"] else "?"
        who = "we" if e["who"] == "us" else "customer"
        if e["kind"] == "call":
            body = " ".join((e["transcript"] or e["text"] or "").split()) or "(no words recorded)"
            head = "%s call (%s%s)" % (who, e["source"], ", %ss" % e["seconds"] if e["seconds"]
                                       else "")
        else:
            body = " ".join((e["text"] or "").split())
            head = "%s text (%s)" % (who, e["source"])
        lines.append("[%s] %s: %s" % (when, head, body))
        if sum(len(x) for x in lines) > TEXT_MAX:
            break
    return "\n".join(reversed(lines))


def _iso(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def clean(raw: dict | None, today: date) -> dict:
    """The AI's (or the office's) limits, checked: real dates, real days, real times; a range
    that already ended is dropped."""
    from .scheduling import parse_time
    raw = raw if isinstance(raw, dict) else {}
    out: dict = {}
    nb = _iso(raw.get("not_before"))
    if nb and nb > today:
        out["not_before"] = nb.isoformat()
    blocked = []
    for b in raw.get("blocked") or []:
        if not isinstance(b, dict):
            continue
        f, t = _iso(b.get("from")), _iso(b.get("to"))
        if f and t and t >= f and t >= today:
            blocked.append({"from": f.isoformat(), "to": t.isoformat()})
    if blocked:
        out["blocked"] = blocked
    for key in ("only_days", "not_days"):
        days = [d[:3].title() for d in raw.get(key) or [] if isinstance(d, str)
                and d[:3].title() in DAY_NAMES]
        if days:
            out[key] = sorted(set(days), key=DAY_NAMES.index)
    for key in ("after", "before"):
        t = parse_time(raw.get(key)) if raw.get(key) else None
        if t:
            out[key] = t.strftime("%H:%M")
    tech = raw.get("technician")
    if isinstance(tech, str) and tech.strip():
        out["technician"] = tech.strip()[:40]
    return out


def describe(limits: dict) -> str:
    """The limits in words, for the plan, the calendar and the Excel."""
    parts = []
    if limits.get("not_before"):
        parts.append("not before %s" % date.fromisoformat(limits["not_before"]).strftime(
            "%a %m/%d"))
    for b in limits.get("blocked") or []:
        f, t = date.fromisoformat(b["from"]), date.fromisoformat(b["to"])
        parts.append("away %s-%s" % (f.strftime("%a %m/%d"), t.strftime("%a %m/%d")))
    if limits.get("only_days"):
        parts.append("only " + "/".join(limits["only_days"]))
    if limits.get("not_days"):
        parts.append("not " + "/".join(limits["not_days"]))
    if limits.get("after"):
        parts.append("after %s" % _ampm(limits["after"]))
    if limits.get("before"):
        parts.append("done by %s" % _ampm(limits["before"]))
    if limits.get("technician"):
        parts.append("with %s" % limits["technician"])
    return ", ".join(parts)


def _ampm(hhmm: str) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    return "%d:%02d %s" % ((h - 1) % 12 + 1, m, "AM" if h < 12 else "PM")


def read_one(db: Session, j: DispatchJob, evs: list[dict], now: datetime, prov, model: str
             ) -> DispatchAvailability:
    from . import ai
    row = db.scalar(select(DispatchAvailability).where(DispatchAvailability.job_uid == j.job_uid))
    if row is None:
        row = DispatchAvailability(job_uid=j.job_uid)
        db.add(row)
    row.job_number = j.job_number
    row.fingerprint = _fingerprint(j.phones or [], evs)
    row.read_at = now
    if not evs:
        row.limits, row.summary, row.evidence, row.confidence = {}, None, None, None
        row.error, row.model = None, None
        return row
    system = SYSTEM.format(today=c.local(now).strftime("%a %b %d %Y"))
    facts = "Job #%s, customer %s, %s.\n\nTheir calls and texts:\n%s" % (
        j.job_number, j.customer_name or "?", j.city or "", _conversation(evs))
    try:
        turn = prov.complete(system=system, messages=[{"role": "user", "content": facts}],
                             tools=[], model=model, max_tokens=MAX_TOKENS)
    except providers.ProviderError as e:
        ai._record(db, "availability", model, None, error=str(e))
        row.error = str(e)[:500]
        return row
    ai._record(db, "availability", model, turn)
    data = ai._extract_json(turn.text)
    if data is None:
        row.error = "The AI's answer could not be read."
        return row
    row.limits = clean(data.get("limits"), c.local(now).date())
    row.summary = str(data.get("summary") or "")[:500] or None
    row.evidence = [{"at": str(e.get("at") or "")[:40], "quote": str(e.get("quote") or "")[:300]}
                    for e in (data.get("evidence") or []) if isinstance(e, dict)][:6] or None
    row.confidence = data.get("confidence") if data.get("confidence") in (
        "high", "medium", "low") else None
    row.model, row.error = turn.model or model, None
    return row


def refresh(db: Session, now: datetime, *, limit: int = PER_TICK,
            job_uids: set[str] | None = None) -> dict:
    """Read the customers whose calls and texts changed since their last reading — the jobs
    that need a visit, the most recently contacted first. Stops at the first refusal (AI off,
    paused, over the cap): that is the setting, not an error."""
    from . import ai, scheduling
    counts = {"read": 0, "unchanged": 0, "no_messages": 0, "office_kept": 0}
    waiting = {pj.uid for pj in scheduling.waiting_jobs(db, now, hidden=set(), boards="all",
                                                        use_saved=False)}
    if job_uids is not None:
        waiting &= job_uids
    if not waiting:
        return counts
    talk = _events_by_phone(db, now)
    # Fresh from the database: an office correction cleared on another session must count.
    rows = {r.job_uid: r for r in db.scalars(select(DispatchAvailability).where(
        DispatchAvailability.job_uid.in_(waiting)).execution_options(populate_existing=True))}
    todo = []
    for j in db.scalars(select(DispatchJob).where(DispatchJob.job_uid.in_(waiting))):
        evs = [e for p in j.phones or [] for e in talk.get(p, [])]
        evs.sort(key=lambda e: e["at"] or now)
        fp = _fingerprint(j.phones or [], evs)
        row = rows.get(j.job_uid)
        if row is not None and row.source == "office":
            if fp != row.fingerprint and not row.newer_messages:
                row.newer_messages = True
            counts["office_kept"] += 1
            continue
        if row is not None and row.fingerprint == fp and not row.error:
            counts["unchanged"] += 1
            continue
        last = max((e["at"] for e in evs if e["at"]), default=None)
        todo.append((last or datetime.min.replace(tzinfo=now.tzinfo), j, evs))
    todo.sort(key=lambda x: x[0], reverse=True)
    prov = model = None
    for _, j, evs in todo[:limit]:
        if not evs:
            read_one(db, j, evs, now, None, "")
            counts["no_messages"] += 1
            continue
        try:
            prov, model = ai.provider_for(db, now)
        except ai.Unavailable as e:
            counts["skipped"] = str(e)
            break
        read_one(db, j, evs, now, prov, model)
        counts["read"] += 1
        db.commit()
    counts["waiting_to_read"] = max(0, len(todo) - limit)
    db.commit()
    return counts


def as_limits(row: DispatchAvailability | None) -> dict | None:
    """A saved reading as the planner's limits (scheduling.apply_limits), evidence in words."""
    if row is None or not row.limits:
        return None
    lim = dict(row.limits)
    quote = "; ".join('%s "%s"' % (e.get("at", ""), e.get("quote", ""))
                      for e in (row.evidence or [])[:2])
    who = "office" if row.source == "office" else "calls"
    lim["evidence"] = ("from the %s: %s" % (who, quote)) if quote else "from the %s" % who
    return lim


def payload(row: DispatchAvailability, j: DispatchJob | None) -> dict:
    return {"job_uid": row.job_uid, "job_number": row.job_number,
            "customer": j.customer_name if j else None, "limits": row.limits or {},
            "in_words": describe(row.limits or {}), "summary": row.summary,
            "evidence": row.evidence or [], "confidence": row.confidence, "source": row.source,
            "newer_messages": row.newer_messages, "read_at": row.read_at.isoformat()
            if row.read_at else None, "error": row.error}


def office_set(db: Session, job: DispatchJob, limits: dict, summary: str | None,
               user_id: int, now: datetime) -> DispatchAvailability:
    row = db.scalar(select(DispatchAvailability).where(DispatchAvailability.job_uid == job.job_uid))
    if row is None:
        row = DispatchAvailability(job_uid=job.job_uid)
        db.add(row)
    row.job_number = job.job_number
    row.limits = clean(limits, c.local(now).date())
    row.summary = (summary or "")[:500] or None
    row.source, row.newer_messages = "office", False
    row.edited_by_id, row.edited_at = user_id, now
    db.commit()
    return row


def office_clear(db: Session, job: DispatchJob) -> None:
    """Back to the AI's reading: it is read again on the next pass."""
    row = db.scalar(select(DispatchAvailability).where(DispatchAvailability.job_uid == job.job_uid))
    if row is not None:
        row.source, row.fingerprint, row.newer_messages = "ai", None, False
        row.edited_by_id = row.edited_at = None
        db.commit()


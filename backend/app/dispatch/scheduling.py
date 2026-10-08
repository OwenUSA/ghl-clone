"""Remaking the schedule (2026-10-08): which jobs need a visit, the customers' limits, the plan,
and the plan kept so the chat, the calendar and the Excel show the same one.

The owner's ask: take every new job that needs an inspection, every job AHS approved that needs
the repair booked, and every job whose visit was cancelled or has to be rescheduled, and fit as
many as possible Monday to Saturday for Owen and Antonio (both do both), grouped so they drive
the least. planner.py does the fitting; this module decides what goes in.

Which jobs (open, on the boards asked for — AHS - Inspection and AHS - Repair & Review unless
"all"):
  * a stage whose next step is booking a visit (config.BOOK), with no visit from today on;
  * a stage that means a visit is booked (lookups.booked_stage) whose visit has NO time or whose
    date has PASSED — the visit did not happen or was never booked (live 2026-10-07: #721 stayed
    at "Same-Day Confirm" with Wednesday's date after the visit fell through);
  * a customer nobody has reached (first-contact stages, "Not Answering") is TENTATIVE: placed,
    and marked "call first" — or left out when include_unreached is false.

The customers' limits come from the chat: the assistant reads each customer's calls and texts
and passes what they said ("only after 3 PM", "away on the 8th, next week Tue-Thu"); a visit
never breaks one, and the evidence is printed beside it. Nothing is booked.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DispatchAvailability, DispatchJob, DispatchPlan
from . import availability, booking, comms, lookups, planner
from . import config as c

AHS_BOARDS = (c.INSPECTION_BOARD, c.REPAIR_BOARD)
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
            "lun": 0, "mar": 1, "mie": 2, "mié": 2, "jue": 3, "vie": 4, "sab": 5, "sáb": 5}


def parse_time(text) -> time | None:
    """'15:00' / '3 PM' / '3:30pm' / 'after 3' -> a time; a bare 1-6 is the afternoon."""
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", str(text or ""), re.IGNORECASE)
    if not m:
        return None
    h, mins, ap = int(m[1]), int(m[2] or 0), (m[3] or "").lower()
    if ap == "pm" and h < 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    elif not ap and 1 <= h <= 6:
        h += 12
    return time(h, mins) if 0 <= h <= 23 and 0 <= mins <= 59 else None


def _weekdays(value) -> frozenset | None:
    if not value:
        return None
    names = value if isinstance(value, list) else re.split(r"[,\s/]+", str(value))
    out = {WEEKDAYS[n.strip().lower()[:3]] for n in names
           if n and n.strip().lower()[:3] in WEEKDAYS}
    return frozenset(out) or None


NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def apply_limits(job: planner.PlanJob, lim: dict, now: datetime) -> None:
    """One customer's limits onto the job: saved from the calls (availability.py) or passed by
    the assistant. Several sets narrow each other: the later date, the fewer days."""
    words = []
    if lim.get("not_before"):
        d = lookups.parse_day(str(lim["not_before"]), now)
        if d:
            job.not_before = max(d, job.not_before) if job.not_before else d
            words.append("not before %s" % d.strftime("%a %m/%d"))
    for b in lim.get("blocked") or []:
        a = lookups.parse_day(str((b or {}).get("from")), now) if isinstance(b, dict) else None
        z = lookups.parse_day(str((b or {}).get("to")), now) if isinstance(b, dict) else None
        if a and z and z >= a:
            job.blocked.append((a, z))
            words.append("away %s-%s" % (a.strftime("%a %m/%d"), z.strftime("%a %m/%d")))
    days = _weekdays(lim.get("days") or lim.get("only_days"))
    not_days = _weekdays(lim.get("not_days"))
    if not_days:
        days = (days or frozenset(range(7))) - not_days
        words.append("not " + "/".join(NAMES[d] for d in sorted(not_days)))
    if days is not None:
        job.weekdays = days & job.weekdays if job.weekdays is not None else days
        if lim.get("days") or lim.get("only_days"):
            words.append("only " + "/".join(NAMES[d] for d in sorted(days)))
    if lim.get("after"):
        t = parse_time(lim["after"])
        if t:
            job.earliest = t
            words.append("start after %s" % t.strftime("%I:%M %p").lstrip("0"))
    if lim.get("before"):
        t = parse_time(lim["before"])
        if t:
            job.latest = t
            words.append("done by %s" % t.strftime("%I:%M %p").lstrip("0"))
    if lim.get("technician"):
        job.technician = str(lim["technician"])[:60]
        words.append("with %s" % job.technician)
    if lim.get("evidence"):
        words.append("(%s)" % str(lim["evidence"])[:200])
    if words:
        job.limits.append(" ".join(words))


def _unreached(status: str | None) -> bool:
    s = status or ""
    return any(s in stages for stages in c.FIRST_CONTACT.values()) or "not answering" in \
        s.lower()


def _kind(j: DispatchJob) -> str:
    from .rules import visit_kind
    if j.board == c.INSPECTION_BOARD and j.status == "AHS Approved":
        return "repair"
    return visit_kind(j)


def waiting_jobs(db: Session, now: datetime, *, hidden: set[str], kind: str = "all",
                 boards: str = "ahs", include_unreached: bool = True,
                 limits: dict[str, dict] | None = None,
                 use_saved: bool = True) -> list[planner.PlanJob]:
    from ..zuper import config as zuper_config
    template = zuper_config.job_url_template()
    saved = {r.job_uid: r for r in db.scalars(select(DispatchAvailability))} if use_saved \
        else {}
    allowed = AHS_BOARDS if boards != "all" else (*c.BOARDS, *c.OTHER_PIPELINES)
    today = c.local(now).date()
    out = []
    for j in db.scalars(select(DispatchJob).where(DispatchJob.is_open.is_(True))):
        if (j.board or "") in hidden or j.board not in allowed:
            continue
        start = c.aware(j.scheduled_start)
        day = c.local(start).date() if start else None
        if j.status in c.BOOK:
            if day and day >= today:
                continue                                   # already has a visit
            why = c.BOOK[j.status]
        elif lookups.booked_stage(j.status) and (day is None or day < today):
            why = ("in '%s' with no visit time - the visit was never booked" % j.status
                   if day is None else
                   "in '%s' but its visit (%s) has passed - it did not happen or Zuper was "
                   "not moved on" % (j.status, day.strftime("%a %m/%d")))
        else:
            continue
        k = _kind(j)
        if kind in ("inspection", "repair") and k != kind:
            continue
        tentative = _unreached(j.status)
        if tentative and not include_unreached:
            continue
        pj = planner.PlanJob(
            uid=j.job_uid, number=j.job_number, customer=j.customer_name, city=j.city,
            lat=j.lat, lng=j.lng, kind=k, waiting_since=j.status_since,
            zuper_url=template.format(uid=j.job_uid) if template else None, stage=j.status,
            address=", ".join(x for x in (j.address, j.city) if x) or None,
            tentative=tentative, why=why)
        said = availability.as_limits(saved.get(j.job_uid))
        if said:
            apply_limits(pj, said, now)             # what the customer said, read from calls
        lim = (limits or {}).get(str(j.job_number))
        if lim:
            apply_limits(pj, lim, now)              # what the assistant passed for this plan
        out.append(pj)
    return out


def technicians_and_minutes(db: Session) -> tuple[list[dict], dict, dict]:
    from . import ai
    techs = ai.settings(db).technicians or booking.DEFAULT_TECHNICIANS
    learned = ai.learned_minutes(db)
    minutes: dict[str, dict[str, int]] = {}
    sources: dict[str, dict] = {}
    for t in techs:
        mine, src = {}, "default"
        for k in ("inspection", "repair"):
            set_by_admin = (t.get("minutes") or {}).get(k)
            if set_by_admin:
                mine[k], src = int(set_by_admin), "settings"
            elif learned.get(t["name"], {}).get(k):
                mine[k] = learned[t["name"]][k]
                src = "history" if src == "default" else src
            else:
                mine[k] = planner.DEFAULT_MINUTES[k]
        minutes[t["name"]] = mine
        sources[t["name"]] = {**mine, "source": src}
    return techs, minutes, sources


def make_plan(db: Session, now: datetime, *, days: int = 6, kind: str = "all",
              mode: str = "most_jobs", boards: str = "ahs", include_unreached: bool = True,
              limits: list[dict] | None = None, hidden: set[str] | None = None,
              user_id: int | None = None, source: str = "page", store: bool = True) -> dict:
    from . import ai
    hidden = hidden or set()
    days = max(1, min(int(days), 18))
    by_number = {str(x.get("job_number", "")).lstrip("#"): x for x in (limits or [])
                 if isinstance(x, dict) and x.get("job_number")}
    jobs = waiting_jobs(db, now, hidden=hidden, kind=kind, boards=boards,
                        include_unreached=include_unreached, limits=by_number)
    techs, minutes, sources = technicians_and_minutes(db)
    out = planner.plan(jobs=jobs, booked=ai.visits_for_booking(db, now, hidden),
                       technicians=techs, minutes=minutes, now=now, days=days, mode=mode)
    out["generated_at"] = now.isoformat()
    out["assumptions"] = {
        "minutes": sources,
        "hours": {t["name"]: {"start": t["start"], "end": t["end"], "days": t.get("days", []),
                              "max": t.get("max", 5)} for t in techs},
        "note": "Visit lengths come from how jobs were booked before; change them per "
                "technician in Dispatch -> Settings. Driving is an estimate (straight line x "
                "1.3 at 50 km/h): it does not know roads, bridges or traffic."}
    read = {r.job_uid: r for r in db.scalars(select(DispatchAvailability).where(
        DispatchAvailability.job_uid.in_([j.uid for j in jobs])))} if jobs else {}
    out["availability"] = {
        "jobs": len(jobs), "read": sum(1 for r in read.values() if r.read_at),
        "with_limits": sum(1 for r in read.values() if r.limits),
        "office": sum(1 for r in read.values() if r.source == "office"),
        "not_read_yet": len(jobs) - sum(1 for r in read.values() if r.read_at),
        "note": "What each customer said about when they can have the visit is read from "
                "their calls and texts and applied; a job not read yet has only the limits "
                "given here."}
    out["asked"] = {"days": days, "kind": kind, "mode": out["mode"], "boards": boards,
                    "include_unreached": include_unreached,
                    "limits": list(by_number.values()) or None}
    if store:
        row = DispatchPlan(created_by_id=user_id, source=source, params=out["asked"],
                           result=out, created_at=now)
        db.add(row)
        db.flush()
        out["id"] = row.id
        row.result = dict(out)
        db.commit()
    return out


def for_model(db: Session, out: dict, now: datetime) -> dict:
    """The plan, compact, for the chat model — with each NEW visit's last calls and texts, so
    the assistant can see a customer's limits and plan again with them."""
    talk = comms.load(db, now - timedelta(days=14))
    phones = {j.job_number: j.phones or [] for j in db.scalars(select(DispatchJob))}

    def last_words(number):
        items = sorted((x for p in phones.get(number, []) for x in talk.get(p, [])),
                       key=lambda x: x.at)[-3:]
        return ["%s %s %s: %s" % (c.local(x.at).strftime("%a %m/%d %I:%M%p"),
                                  "us" if x.out else "customer", x.kind,
                                  " ".join((x.summary or x.transcript or "").split())[:220])
                for x in items] or None

    days = []
    for d in out["days"]:
        techs = []
        for t in d["techs"]:
            if not t["visits"]:
                continue
            techs.append({"tech": t["tech"], "visits": "%d of %d" % (t["count"], t["capacity"]),
                          "driving_min": t["drive_minutes"], "list": [{
                              "time": "%s-%s" % (_hm(v["start"]), _hm(v["end"])),
                              "job": v["job_number"], "customer": v["customer"],
                              "city": v["city"], "kind": v["kind"],
                              "booked" if v["existing"] else "proposed": True,
                              "tentative_call_first": v.get("tentative") or None,
                              "limits": v.get("limits"),
                              "last_calls_and_texts": None if v["existing"]
                              else last_words(v["job_number"])}
                              for v in t["visits"]]})
        if techs:
            days.append({"day": d["label"], "techs": techs})
    return {"plan_id": out.get("id"), "summary": out["summary"], "baseline": out["baseline"],
            "days": days,
            "not_placed": [{"job": u["job_number"], "customer": u["customer"],
                            "reason": u["reason"], "limits": u.get("limits")}
                           for u in out["unplaced"]],
            "availability": out.get("availability"),
            "how_to_use": "What each customer said about WHEN is read from ALL their calls and "
                          "texts and already applied (a visit's `limits`). Check "
                          "last_calls_and_texts only for something newer or missed; then call "
                          "plan_schedule again with limits=[{job_number, not_before, blocked: "
                          "[{from, to}], days, not_days, after, before, technician, evidence}]. "
                          "Tentative visits: nobody reached the customer, call before booking. "
                          "Give the person the links."}


def _hm(iso: str) -> str:
    return c.local(datetime.fromisoformat(iso)).strftime("%I:%M%p").lstrip("0").lower()


def week_of(d: date) -> date:
    return d - timedelta(days=d.weekday())

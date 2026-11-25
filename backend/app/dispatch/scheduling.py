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

2026-11-19 — the owner's rules, the same for the Dispatch page, its chat and Owen AI:
  * the boards in use are AHS Simple Flow and the Retail pipelines (`boards="active"`, the
    default); their stages are read by SIMPLE_FLOW_NEED / RETAIL_NEED below;
  * a REPAIR is planned only when the customer agreed (readiness.repair_agreed: an accepted /
    signed proposal, AHS authorization, or a yes from a call or text passed in `agreed`); the
    others are listed in `waiting_ok` with what was checked;
  * an INSPECTION nobody called the customer about is planned as CALL FIRST
    (readiness.inspection_verified);
  * a customer who can only do one day gets it first (planner.py); technicians' days off / on
    call come from their settings (booking.works);
  * rain is FLAGGED on each visit (weather_flags), never used to place one.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DispatchAvailability, DispatchJob, DispatchPlan, ZuperDocument
from . import availability, booking, comms, lookups, planner, readiness
from . import config as c

AHS_BOARDS = (c.INSPECTION_BOARD, c.REPAIR_BOARD, c.SIMPLE_FLOW_BOARD)
BOARD_SETS = {"active": c.ACTIVE_BOARDS, "ahs": AHS_BOARDS,
              "all": (*c.BOARDS, *c.OTHER_PIPELINES)}
RETAIL_BOARDS = (c.RETAIL_BOARD, *c.OTHER_PIPELINES)
# Retail's columns (and its six regional copies'), by what the next visit is.
RETAIL_INSPECTION = {"New Lead", "Contact Attempted", "Inspection / Estimate"}
RETAIL_PROPOSAL = {"Proposal Made", "Estimate Sent", "Follow Up", "Waiting for Customer"}
RETAIL_VISIT = {"Scheduled", "Repair In Process", "Reschedule Required", "Call Back"}
# A visit's pictures from this long before its start count as "the visit happened".
PHOTO_SLACK = timedelta(hours=2)
CALL_FIRST_WHY = ("Nobody has talked with the customer on the phone since the job came in: "
                  "call to get / verify the information first.")
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
    if lim.get("summary"):
        words = [str(lim["summary"])[:240]]    # the caller's own sentence (briefs.booking_limits)
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


def _visited(j: DispatchJob, start: datetime | None) -> bool:
    """Pictures were posted for the (past) visit: it happened, the stage was not moved on."""
    shot = c.aware(j.visit_photo_at)
    return bool(start and shot and j.visit_photo_count and shot >= start - PHOTO_SLACK)


def _missed(status: str, day: date | None) -> str:
    return ("in '%s' with no visit booked" % status if day is None else
            "in '%s' but its visit (%s) did not happen" % (status, day.strftime("%a %m/%d")))


def simple_flow_need(j: DispatchJob, start: datetime | None, day: date | None,
                     today: date) -> tuple[str, str] | None:
    """AHS Simple Flow (2026-11-06): (inspection | repair, why) or None. Its first column holds
    new jobs AND booked inspections, so only the date says which."""
    if day and day >= today:
        return None                                    # a visit is booked
    done = day is not None and _visited(j, start)
    if j.status in (c.SF_NEW, c.SF_INSPECTION_DAY):
        if done:
            return "repair", "inspected %s - the repair waits for AHS / the customer" % (
                day.strftime("%a %m/%d"))
        return "inspection", _missed(j.status, day)
    if j.status == c.SF_PROPOSAL:
        return "repair", "in Proposal Creation - book the repair once the customer agrees"
    if j.status in (c.SF_HANDOFF, c.SF_REPAIR_DAY):
        return None if done else ("repair", _missed(j.status, day))
    return None                          # Special Cases, Quality Call, Job Completed, foreign


def retail_need(j: DispatchJob, start: datetime | None, day: date | None, today: date,
                quoted: set[str]) -> tuple[str, str] | None:
    """Retail and its regional copies: (inspection | repair, why) or None."""
    if day and day >= today:
        return None
    done = day is not None and _visited(j, start)
    if j.status in RETAIL_INSPECTION:
        return None if done else ("inspection", _missed(j.status, day))
    if j.status in RETAIL_PROPOSAL:
        return "repair", "in '%s' - book the repair once the customer agrees" % j.status
    if j.status in RETAIL_VISIT:
        if done and j.status != "Reschedule Required":
            return None
        # "Scheduled" is the sold repair (Option chosen is required there) — or, on older
        # jobs, the inspection: a job with a proposal is past its inspection.
        return ("repair" if j.job_uid in quoted else "inspection"), _missed(j.status, day)
    return None


def hidden_boards(hidden: set[str]) -> set[str]:
    """Simple Flow is not a CRM pipeline, so permissions never name it: it is hidden from
    whoever may not see AHS - Inspection (the AHS work it replaced)."""
    return set(hidden) | ({c.SIMPLE_FLOW_BOARD} if c.INSPECTION_BOARD in hidden else set())


def waiting_jobs(db: Session, now: datetime, *, hidden: set[str], kind: str = "all",
                 boards: str = "active", include_unreached: bool = True,
                 limits: dict[str, dict] | None = None,
                 use_saved: bool = True) -> list[planner.PlanJob]:
    from ..zuper import config as zuper_config
    template = zuper_config.job_url_template()
    saved = {r.job_uid: r for r in db.scalars(select(DispatchAvailability))} if use_saved \
        else {}
    allowed = BOARD_SETS.get(boards, c.ACTIVE_BOARDS)
    hidden = hidden_boards(hidden)
    quoted = set(db.scalars(select(ZuperDocument.job_uid).where(
        ZuperDocument.kind == ZuperDocument.QUOTE, ZuperDocument.removed_in_zuper.is_(False))))
    today = c.local(now).date()
    out = []
    for j in db.scalars(select(DispatchJob).where(DispatchJob.is_open.is_(True))):
        if (j.board or "") in hidden or j.board not in allowed:
            continue
        start = c.aware(j.scheduled_start)
        day = c.local(start).date() if start else None
        if j.board == c.SIMPLE_FLOW_BOARD or j.board in RETAIL_BOARDS:
            need = simple_flow_need(j, start, day, today) if j.board == c.SIMPLE_FLOW_BOARD \
                else retail_need(j, start, day, today, quoted)
            if need is None:
                continue
            k, why = need
        elif j.status in c.BOOK:
            if day and day >= today:
                continue                                   # already has a visit
            why, k = c.BOOK[j.status], _kind(j)
        elif lookups.booked_stage(j.status) and (day is None or day < today):
            why = ("in '%s' with no visit time - the visit was never booked" % j.status
                   if day is None else
                   "in '%s' but its visit (%s) has passed - it did not happen or Zuper was "
                   "not moved on" % (j.status, day.strftime("%a %m/%d")))
            k = _kind(j)
        else:
            continue
        if kind in ("inspection", "repair") and k != kind:
            continue
        # A repair's customer nobody reaches ("Not Answering") is CALL FIRST by its stage; an
        # inspection's is decided by the calls themselves (readiness, in make_plan).
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


# A job the calls say not to schedule: why (briefs.to_book verdicts).
LEAVE_OUT = {"on_hold": "the customer put it on hold",
             "on_other_job": "the visit is already booked on the customer's other job",
             "visit_happened": "the visit looks like it happened — update the stage instead"}


def _from_the_calls(db: Session, now: datetime, jobs: list, hidden: set[str]
                    ) -> tuple[list, list[dict]]:
    """Read every waiting job's calls and texts (2026-10-14): leave out the ones that must not
    be booked, and narrow the rest to the days the customer agreed or offered
    (briefs.booking_limits)."""
    from . import briefs
    if not jobs:
        return jobs, []
    verdict = {r["job_number"]: (g["verdict"], r) for g in briefs.to_book(db, now, hidden)["groups"]
               for r in g["jobs"]}
    uids = {j.uid for j in jobs}
    found = {b.job.job_uid: b for b in briefs.load(
        db, now, hidden, jobs=list(db.scalars(select(DispatchJob).where(
            DispatchJob.job_uid.in_(uids)))), days=briefs.BOOK_DAYS)}
    keep, left = [], []
    for j in jobs:
        v, row = verdict.get(str(j.number), (None, None))
        if v in LEAVE_OUT:
            left.append({"job_number": j.number, "customer": j.customer, "city": j.city,
                         "reason": LEAVE_OUT[v], "said": row["say"] if row else None})
            continue
        b = found.get(j.uid)
        if b is not None:
            # A hold the to-book verdict confirmed was left out above; a cancel / "can't make
            # it" is exactly who needs a new date — planned, flagged "call first".
            lim, stop = briefs.booking_limits(b, now)
            if lim:
                apply_limits(j, lim, now)
            elif stop is not None and stop.kind == "cancel":
                j.tentative = True
                j.limits.append("asked to move %s: “%s”" % (briefs._when(stop.at),
                                                             stop.quote[:120]))
        keep.append(j)
    return keep, left


def gate(db: Session, now: datetime, jobs: list[planner.PlanJob],
         agreed: list[dict] | None = None) -> tuple[list[planner.PlanJob], list[dict]]:
    """The owner's two gates (readiness.py): a repair without the customer's yes is kept OUT
    and listed (`waiting_ok`); an inspection nobody called the customer about is CALL FIRST."""
    if not jobs:
        return jobs, []
    rows = {j.job_uid: j for j in db.scalars(select(DispatchJob).where(
        DispatchJob.job_uid.in_([pj.uid for pj in jobs])))}
    ev = readiness.Evidence.load(db, now, list(rows.values()), agreed)
    keep, waiting = [], []
    for pj in jobs:
        j = rows.get(pj.uid)
        if j is None:
            continue
        if pj.kind == "repair":
            yes = readiness.repair_agreed(j, ev)
            if yes is None:
                on, checked = readiness.waiting_on(j, ev)
                waiting.append({"job_number": pj.number, "job_uid": pj.uid,
                                "customer": pj.customer, "city": pj.city, "board": j.board,
                                "stage": pj.stage, "waiting_on": on, "checked": checked,
                                "zuper_url": pj.zuper_url})
                continue
            pj.agreed = yes["text"]
        else:
            ok = readiness.inspection_verified(j, ev)
            if ok is None:
                pj.tentative = True
                pj.call_first_why = CALL_FIRST_WHY
            else:
                pj.verified = ok["text"]
        if pj.tentative and not pj.call_first_why:
            pj.call_first_why = "Nobody has reached the customer yet (%s)." % pj.stage
        keep.append(pj)
    return keep, waiting


def make_plan(db: Session, now: datetime, *, days: int = 6, kind: str = "all",
              mode: str = "most_jobs", boards: str = "active", include_unreached: bool = True,
              limits: list[dict] | None = None, hidden: set[str] | None = None,
              user_id: int | None = None, source: str = "page", store: bool = True,
              agreed: list[dict] | None = None) -> dict:
    from . import ai
    hidden = hidden or set()
    days = max(1, min(int(days), 18))
    by_number = {str(x.get("job_number", "")).lstrip("#"): x for x in (limits or [])
                 if isinstance(x, dict) and x.get("job_number")}
    yes, refused = readiness.clean_agreed(agreed or [], now)
    jobs = waiting_jobs(db, now, hidden=hidden, kind=kind, boards=boards,
                        include_unreached=include_unreached, limits=by_number)
    jobs, left_out = _from_the_calls(db, now, jobs, hidden)
    jobs, waiting_ok = gate(db, now, jobs, yes)
    if not include_unreached:
        jobs = [j for j in jobs if not j.tentative]
    techs, minutes, sources = technicians_and_minutes(db)
    out = planner.plan(jobs=jobs, booked=ai.visits_for_booking(db, now, hidden),
                       technicians=techs, minutes=minutes, now=now, days=days, mode=mode)
    out["generated_at"] = now.isoformat()
    out["waiting_ok"] = waiting_ok
    out["agreed_refused"] = refused or None
    out["summary"]["waiting_ok"] = len(waiting_ok)
    weather_flags(db, out, now)
    out["assumptions"] = {
        "minutes": sources,
        "hours": {t["name"]: {"start": t["start"], "end": t["end"], "days": t.get("days", []),
                              "max": t.get("max", 5), "does": t.get("does", []),
                              "on_call": bool(t.get("on_call")) or None,
                              "only": t.get("only") or None, "off": t.get("off") or None}
                  for t in techs},
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
    # 2026-10-14: who was left out and why, and every customer with a time they cannot do.
    out["left_out"] = left_out
    out["flags"] = [{"job_number": j.number, "customer": j.customer, "city": j.city,
                     "limits": j.limits} for j in jobs if j.limits]
    out["asked"] = {"days": days, "kind": kind, "mode": out["mode"], "boards": boards,
                    "include_unreached": include_unreached,
                    "limits": list(by_number.values()) or None,
                    # Kept so the next plans (the page's button too) remember the yes.
                    "agreed": yes or None}
    if store:
        row = DispatchPlan(created_by_id=user_id, source=source, params=out["asked"],
                           result=out, created_at=now)
        db.add(row)
        db.flush()
        out["id"] = row.id
        row.result = dict(out)
        db.commit()
    return out


def weather_flags(db: Session, out: dict, now: datetime) -> None:
    """Rain at each visit's house in its hours, from the weather agent's KEPT forecast (no
    network call). A FLAG only (the owner, 2026-11-19): it never moves a visit."""
    from ..weather import config as wconf
    from ..weather import rules as wrules
    from ..weather import service as wsvc
    if not wconf.enabled():
        out["weather"] = {"enabled": False, "note": "The weather agent is off on this server "
                                                    "(WEATHER_ENABLED): no rain flags."}
        return
    pct = wsvc.settings(db).rain_pct
    first = out["days"][0]["date"] if out["days"] else None
    flagged, unknown, seen = [], 0, {}
    for d in out["days"]:
        for t in d["techs"]:
            for v in t["visits"]:
                if v.get("lat") is None or v.get("lng") is None:
                    unknown += 1
                    continue
                spot = (round(v["lat"], 2), round(v["lng"], 2))
                if spot not in seen:
                    seen[spot] = wsvc.forecast(db, v["lat"], v["lng"])
                hours, g = seen[spot]
                start, end = datetime.fromisoformat(v["start"]), datetime.fromisoformat(v["end"])
                w = wrules.window(hours, start, end) if hours else None
                if w is None or not w.known:
                    v["weather"] = {"known": False}
                    unknown += 1
                    continue
                risk = w.pop is not None and w.pop >= pct
                less = start - now > timedelta(days=wconf.SURE_DAYS)
                v["weather"] = {"pop": w.pop, "mm": w.mm, "risk": risk, "less_certain": less,
                                "made": wsvc.made(g)}
                if risk:
                    flagged.append({"date": d["date"], "label": d["label"], "tech": t["tech"],
                                    "job_number": v["job_number"], "customer": v["customer"],
                                    "existing": v["existing"], "start": v["start"],
                                    "pop": w.pop, "mm": w.mm, "text": wrules.pct_text(w),
                                    "less_certain": less, "next_day": d["date"] == first})
    flagged.sort(key=lambda x: (not x["next_day"], x["start"]))
    out["weather"] = {"enabled": True, "rain_pct": pct, "flagged": flagged,
                      "no_forecast": unknown,
                      "note": "Rain is flagged, never used to place a visit. Days more than %d "
                              "out are less certain." % wconf.SURE_DAYS}


def for_model(db: Session, out: dict, now: datetime) -> dict:
    """The plan, compact, for the chat model — with each NEW visit's last calls and texts, so
    the assistant can see a customer's limits and plan again with them."""
    talk = comms.load(db, now - timedelta(days=14))
    reach = lookups.customer_phones(db, now, days=14)
    phones = {j.job_number: sorted(reach.get(j.job_uid, set(j.phones or [])))
              for j in db.scalars(select(DispatchJob))}

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
                              "call_first_why": v.get("call_first_why"),
                              "agreed": v.get("agreed"),
                              "one_day": v.get("one_day"),
                              "rain": (v.get("weather") or {}).get("pop")
                              if (v.get("weather") or {}).get("risk") else None,
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
            "flagged_limits": out.get("flags") or None,
            "left_out": out.get("left_out") or None,
            "waiting_for_customer_ok": [{
                "job": w["job_number"], "customer": w["customer"], "stage": w["stage"],
                "waiting_on": w["waiting_on"], "checked": w["checked"],
                "last_calls_and_texts": last_words(w["job_number"])}
                for w in out.get("waiting_ok") or []] or None,
            "agreed_refused": out.get("agreed_refused"),
            "weather": out.get("weather"),
            "how_to_use": "What each customer said about WHEN is read from ALL their calls and "
                          "texts and already applied (a visit's `limits`). Check "
                          "last_calls_and_texts only for something newer or missed; then call "
                          "plan_schedule again with limits=[{job_number, not_before, blocked: "
                          "[{from, to}], days, not_days, after, before, technician, evidence}]. "
                          "Tentative visits (CALL FIRST): nobody has talked with the customer "
                          "on the phone yet - call to get / verify the information before the "
                          "visit. A REPAIR goes in only with the customer's OK: for each job in "
                          "waiting_for_customer_ok, look for a clear yes in their calls / texts "
                          "and plan again with agreed=[{job_number, source: call|text|proposal|"
                          "ahs|other, quote: their exact words, said_at}]. Never pass a yes "
                          "without their words. Rain is a flag only. Give the person the links."}


def _hm(iso: str) -> str:
    return c.local(datetime.fromisoformat(iso)).strftime("%I:%M%p").lstrip("0").lower()


def week_of(d: date) -> date:
    return d - timedelta(days=d.weekday())

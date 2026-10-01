"""The week planner (2026-10-01): every job waiting for a visit, fitted into the next business
days so jobs close together share a day — the owner's ask: "group them so we schedule the
closest ones the same day, 4-5 jobs per day, Monday to Saturday, and consider how we were
booking before so we don't over-book or pick wrong times".

PURE: the jobs waiting, the visits already booked, the technicians' rules, the visit lengths
and `now` in; a draft plan out. Pinned by tests/test_dispatch_planner.py.

How it fills the days (greedy cheapest insertion — simple, predictable, explainable):
  * jobs that have waited longest go first; each is placed where it adds the LEAST:
    extra driving + a small cost per day further out + a cost for the technician who does not
    prefer that kind of visit;
  * a day holds its booked visits FIXED at their times; a new visit goes between them, starting
    as early as the previous visit and the drive allow, ending before the next one and before
    the technician's day ends; never over his daily maximum;
  * driving: straight line between Zuper's map points x ROAD_FACTOR at AVERAGE_KMH (booking.py);
    Antonio starts from home, a technician with no home from his first visit;
  * visit lengths come from how the jobs were really booked (`learned_minutes`), unless an admin
    set them in Dispatch → Settings.
Nothing is booked: the office books what it agrees with the customer, in Zuper.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from statistics import median

from . import booking
from . import config as c

DAY_COST = 12                   # minutes of "driving" one day further out costs in the score
OTHER_TECH_COST = 25
ROUND_MINUTES = 15
DEFAULT_MINUTES = {"inspection": 90, "repair": 120}
MIN_SAMPLES = 3
IMPROVE_ROUNDS = 4


@dataclass
class PlanJob:
    uid: str
    number: str | None
    customer: str | None
    city: str | None
    lat: float | None
    lng: float | None
    kind: str                     # inspection | repair
    waiting_since: datetime | None = None
    zuper_url: str | None = None


@dataclass
class Stop:
    job: PlanJob | None           # None for a booked visit
    label: str
    start: datetime
    end: datetime
    lat: float | None
    lng: float | None
    existing: bool
    kind: str = "repair"
    uid: str | None = None
    drive_before: int = 0


@dataclass
class Day:
    tech: dict
    day: date
    stops: list[Stop] = field(default_factory=list)


def learned_minutes(history: list[tuple[str, str, float]]) -> dict[str, dict[str, int]]:
    """(tech, kind, minutes) of past visits -> the median per technician and kind, rounded to
    15 minutes; only with MIN_SAMPLES or more."""
    by: dict[tuple[str, str], list[float]] = {}
    for tech, kind, minutes in history:
        if 15 <= minutes <= 600:
            by.setdefault((tech, kind), []).append(minutes)
    out: dict[str, dict[str, int]] = {}
    for (tech, kind), xs in by.items():
        if len(xs) >= MIN_SAMPLES:
            out.setdefault(tech, {})[kind] = int(round(median(xs) / ROUND_MINUTES) * ROUND_MINUTES)
    return out


def _round_up(t: datetime) -> datetime:
    t = t.replace(second=0, microsecond=0)
    extra = (-t.minute) % ROUND_MINUTES
    return t + timedelta(minutes=extra)


def _hhmm(value: str):
    h, m = value.split(":")
    return int(h), int(m)


def business_days(now: datetime, n: int, weekdays: set[int]) -> list[date]:
    out, d = [], c.local(now).date() + timedelta(days=1)
    while len(out) < n:
        if d.weekday() in weekdays:
            out.append(d)
        d += timedelta(days=1)
    return out


def _schedule(day: Day, minutes: dict[str, int]) -> list[Stop] | None:
    """Times for the day's stops in order: booked visits stay put, new ones start as early as
    allowed. None when they do not fit."""
    t = day.tech
    h, m = _hhmm(t["start"])
    open_at = datetime(day.day.year, day.day.month, day.day.day, h, m, tzinfo=c.TZ)
    h, m = _hhmm(t["end"])
    close_at = datetime(day.day.year, day.day.month, day.day.day, h, m, tzinfo=c.TZ)
    home = tuple(t["home"]) if t.get("home") else None
    out: list[Stop] = []
    here, clock = home, open_at
    for s in day.stops:
        drive = booking.drive_minutes(here, (s.lat, s.lng)) if here else 0
        if s.existing:
            # A booked visit is a fact, never "infeasible": a 7:30 first visit with a drive
            # from home before it is how the office books (live 2026-10-01 — treating it as
            # impossible dropped Antonio's whole Friday, booked visits and free time alike).
            # Only NEW visits are fitted around it: one placed before it must end, drive
            # included, before it starts.
            if out and not out[-1].existing and \
                    clock + timedelta(minutes=drive) > s.start + timedelta(minutes=5):
                return None
            out.append(Stop(s.job, s.label, s.start, s.end, s.lat, s.lng, True, s.kind, s.uid,
                            drive))
            clock = s.end
        else:
            start = _round_up(max(clock + timedelta(minutes=drive), open_at))
            end = start + timedelta(minutes=minutes.get(s.kind, DEFAULT_MINUTES[s.kind]))
            if end > close_at:
                return None
            out.append(Stop(s.job, s.label, start, end, s.lat, s.lng, False, s.kind, s.uid,
                            drive))
            clock = end
        here = (s.lat, s.lng) if s.lat is not None else here
    return out


def _drive_total(stops: list[Stop]) -> int:
    return sum(s.drive_before for s in stops)


def plan(*, jobs: list[PlanJob], booked: list[booking.Visit], technicians: list[dict],
         minutes: dict[str, dict[str, int]], now: datetime, days: int = 6) -> dict:
    weekdays = set().union(*[set(t.get("days", c.BUSINESS_DAYS)) for t in technicians]) \
        if technicians else set(c.BUSINESS_DAYS)
    dates = business_days(now, days, weekdays)
    grid: dict[tuple[str, date], Day] = {}
    for t in technicians:
        for d in dates:
            if d.weekday() not in t.get("days", c.BUSINESS_DAYS):
                continue
            mine = sorted((v for v in booked if v.tech == t["name"]
                           and c.local(v.start).date() == d), key=lambda v: v.start)
            grid[(t["name"], d)] = Day(t, d, [
                Stop(None, v.label or "booked visit", v.start, v.end, v.lat, v.lng, True)
                for v in mine])
    unplaced: list[dict] = []
    order = sorted(jobs, key=lambda j: (j.lat is None, c.aware(j.waiting_since) or now))

    def best_place(job: PlanJob, skip: tuple | None = None):
        """The cheapest (cost, key, stops) for this job, or None."""
        best = None
        for key, day in grid.items():
            if key == skip:
                continue
            t = day.tech
            if job.kind not in t.get("does", []) or len(day.stops) >= int(t.get("max", 5)):
                continue
            mins = minutes.get(key[0], {})
            before = _schedule(day, mins)
            if before is None:
                continue
            base = _drive_total(before)
            for i in range(len(day.stops) + 1):
                new = Stop(job, "", now, now, job.lat, job.lng, False, job.kind, job.uid)
                trial = Day(t, key[1], [*day.stops[:i], new, *day.stops[i:]])
                timed = _schedule(trial, mins)
                if timed is None:
                    continue
                cost = (_drive_total(timed) - base) + dates.index(key[1]) * DAY_COST + (
                    0 if t.get("prefers") == job.kind else OTHER_TECH_COST)
                if best is None or cost < best[0]:
                    best = (cost, key, trial.stops)
        return best

    def cost_here(job: PlanJob, key: tuple) -> float:
        """What this job costs where it is now (same terms as best_place)."""
        day = grid[key]
        mins = minutes.get(key[0], {})
        without = Day(day.tech, key[1], [st for st in day.stops if st.job is not job])
        a, b = _schedule(day, mins), _schedule(without, mins)
        if a is None or b is None:
            return float("inf")
        return (_drive_total(a) - _drive_total(b)) + dates.index(key[1]) * DAY_COST + (
            0 if day.tech.get("prefers") == job.kind else OTHER_TECH_COST)

    # 1. Place each job, longest-waiting first, where it adds the least.
    for job in order:
        if job.lat is None or job.lng is None:
            unplaced.append({"job": job, "reason": "No map position in Zuper — book it by hand."})
            continue
        best = best_place(job)
        if best is None:
            unplaced.append({"job": job, "reason": "No room in the next %d business days "
                                                   "(each technician's day is full)." % days})
            continue
        grid[best[1]].stops = best[2]

    # 2. Improve: move a proposed visit to another day / technician when that clearly costs
    # less — a job placed before its neighbours existed rejoins them (review 2026-10-01).
    for _ in range(IMPROVE_ROUNDS):
        moved = False
        for key in list(grid):
            for st in [x for x in grid[key].stops if not x.existing and x.job]:
                here = cost_here(st.job, key)
                grid[key].stops = [x for x in grid[key].stops if x is not st]
                best = best_place(st.job, skip=key)
                if best is not None and best[0] < here - 1:
                    grid[best[1]].stops = best[2]
                    moved = True
                else:
                    back = best_place(st.job)          # put it back where it fits best here
                    if back is None:
                        unplaced.append({"job": st.job, "reason": "No room left."})
                    else:
                        grid[back[1]].stops = back[2]
        if not moved:
            break

    out_days = []
    for d in dates:
        techs = []
        for t in technicians:
            day = grid.get((t["name"], d))
            if day is None:
                continue
            timed = _schedule(day, minutes.get(t["name"], {})) or []
            techs.append({
                "tech": t["name"], "start_from": "home" if t.get("home") else "first job",
                "count": len(timed), "capacity": int(t.get("max", 5)),
                "drive_minutes": _drive_total(timed),
                "visits": [{
                    "job_uid": s.job.uid if s.job else None,
                    "job_number": s.job.number if s.job else None,
                    "customer": s.job.customer if s.job else s.label,
                    "city": s.job.city if s.job else None,
                    "kind": s.kind, "start": s.start.isoformat(), "end": s.end.isoformat(),
                    "existing": s.existing, "drive_minutes_before": s.drive_before,
                    "zuper_url": s.job.zuper_url if s.job else None} for s in timed]})
        out_days.append({"date": d.isoformat(),
                         "label": "%s %s %d" % (d.strftime("%a"), d.strftime("%b"), d.day),
                         "techs": techs})
    return {"days": out_days, "unplaced": [{
        "job_uid": u["job"].uid, "job_number": u["job"].number, "customer": u["job"].customer,
        "city": u["job"].city, "kind": u["job"].kind, "reason": u["reason"],
        "zuper_url": u["job"].zuper_url} for u in unplaced],
        "pending": len(jobs)}

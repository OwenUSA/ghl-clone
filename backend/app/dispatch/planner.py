"""The week planner: every job waiting for a visit, fitted into the next business days with as
little driving as possible (2026-10-01; rebuilt 2026-10-08).

The owner's asks: "group them so we schedule the closest ones the same day, 4-5 jobs per day,
Monday to Saturday, and consider how we were booking before" (10-01), and "fit as many jobs as
possible, grouped the optimized way, so the technicians drive the least time possible between
jobs and inspections" (10-08).

PURE: the jobs waiting, the visits already booked, the technicians' rules, the visit lengths
and `now` in; a draft plan out. Pinned by tests/test_dispatch_planner.py.

How it plans:
  * every visit's cost is the driving it adds + a small cost per day further out (+ a cost per
    day the job has already waited, in "oldest first" mode) + a cost for the technician who does
    not prefer that kind of visit;
  * a day holds its booked visits FIXED at their times; a new visit goes between them, starting
    as early as the previous visit and the drive allow, ending before the next one and before
    the technician's day ends; never over his daily maximum;
  * a job can carry the customer's limits (from the calls: not before a date, only some
    weekdays, after / before a time, one technician); a visit never breaks them;
  * it is built several times from different starting orders (longest waiting, most limited
    first, sweeps around the map) and each result is improved by local search: move a visit to
    another day or technician, swap two visits, and try every order of each day's stops. The
    best result wins: in "most jobs" mode, the one that places the most jobs, then the one with
    the least driving. `baseline` reports the simple one-pass plan, for comparison;
  * driving: `drive` (default booking.drive_minutes: straight line x ROAD_FACTOR at
    AVERAGE_KMH — the owner kept the estimate on 2026-10-08; a road-time function can replace
    it). Antonio starts from home, a technician with no home from his first visit.
Nothing is booked: the office books what it agrees with the customer, in Zuper.
"""
from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from itertools import permutations
from statistics import median

from . import booking
from . import config as c

DAY_COST = 5                    # one day further out, in "minutes of driving": a tie-breaker
                                # that never outweighs real driving (owner, 2026-10-08)
WAIT_COST = 3                   # oldest-first mode: per day waited, per day further out
OTHER_TECH_COST = 25
ROUND_MINUTES = 15
DEFAULT_MINUTES = {"inspection": 90, "repair": 120}
MIN_SAMPLES = 3
IMPROVE_ROUNDS = 8
RANDOM_STARTS = 4
EXACT_ORDER_MAX = 6             # stops in a day up to which every order is tried
MODES = ("most_jobs", "oldest_first")


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
    # 2026-10-08: what the plan must respect, and what the office must know.
    stage: str | None = None
    address: str | None = None
    tentative: bool = False       # nobody has reached the customer: call before booking
    why: str | None = None        # why it is waiting ("AHS approved", "the visit did not happen")
    not_before: date | None = None
    weekdays: frozenset | None = None          # 0 = Monday
    earliest: time | None = None               # the visit may not start before
    latest: time | None = None                 # the visit must end by
    technician: str | None = None
    blocked: list[tuple[date, date]] = field(default_factory=list)   # away, both days included
    limits: list[str] = field(default_factory=list)   # the evidence, in words


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


def _at(d: date, t: time) -> datetime:
    return datetime(d.year, d.month, d.day, t.hour, t.minute, tzinfo=c.TZ)


def _schedule(day: Day, minutes: dict[str, int],
              drive: Callable = booking.drive_minutes) -> list[Stop] | None:
    """Times for the day's stops in order: booked visits stay put, new ones start as early as
    allowed and within the customer's hours. None when they do not fit."""
    t = day.tech
    h, m = _hhmm(t["start"])
    open_at = datetime(day.day.year, day.day.month, day.day.day, h, m, tzinfo=c.TZ)
    h, m = _hhmm(t["end"])
    close_at = datetime(day.day.year, day.day.month, day.day.day, h, m, tzinfo=c.TZ)
    home = tuple(t["home"]) if t.get("home") else None
    out: list[Stop] = []
    here, clock = home, open_at
    for s in day.stops:
        minutes_away = drive(here, (s.lat, s.lng)) if here else 0
        if s.existing:
            # A booked visit is a fact, never "infeasible": a 7:30 first visit with a drive
            # from home before it is how the office books (live 2026-10-01 — treating it as
            # impossible dropped Antonio's whole Friday, booked visits and free time alike).
            # Only NEW visits are fitted around it: one placed before it must end, drive
            # included, before it starts.
            if out and not out[-1].existing and \
                    clock + timedelta(minutes=minutes_away) > s.start + timedelta(minutes=5):
                return None
            out.append(Stop(s.job, s.label, s.start, s.end, s.lat, s.lng, True, s.kind, s.uid,
                            minutes_away))
            clock = s.end
        else:
            j = s.job
            earliest = _at(day.day, j.earliest) if j and j.earliest else open_at
            start = _round_up(max(clock + timedelta(minutes=minutes_away), open_at, earliest))
            end = start + timedelta(minutes=minutes.get(s.kind, DEFAULT_MINUTES[s.kind]))
            if end > close_at or (j and j.latest and end > _at(day.day, j.latest)):
                return None
            out.append(Stop(s.job, s.label, start, end, s.lat, s.lng, False, s.kind, s.uid,
                            minutes_away))
            clock = end
        here = (s.lat, s.lng) if s.lat is not None else here
    return out


def _drive_total(stops: list[Stop]) -> int:
    return sum(s.drive_before for s in stops)


def _allowed(job: PlanJob, tech: dict, d: date) -> bool:
    if job.kind not in tech.get("does", []):
        return False
    if job.technician and job.technician.lower() not in tech["name"].lower():
        return False
    if job.not_before and d < job.not_before:
        return False
    if any(a <= d <= b for a, b in job.blocked):
        return False
    return not (job.weekdays is not None and d.weekday() not in job.weekdays)


class _Plan:
    """One candidate plan: the grid of days, what did not fit, and its cost."""

    def __init__(self, base: dict, minutes: dict, dates: list[date], mode: str, now: datetime,
                 drive: Callable):
        self.grid = {k: Day(d.tech, d.day, list(d.stops)) for k, d in base.items()}
        self.minutes, self.dates, self.mode, self.now, self.drive = (minutes, dates, mode, now,
                                                                     drive)
        self.unplaced: list[dict] = []
        self._cost: dict[tuple, float] = {}

    def visit_cost(self, job: PlanJob, key: tuple) -> float:
        idx = self.dates.index(key[1])
        cost = idx * DAY_COST + (0 if self.grid[key].tech.get("prefers") == job.kind
                                 else OTHER_TECH_COST)
        if self.mode == "oldest_first":
            waited = (self.now - (c.aware(job.waiting_since) or self.now)).days
            cost += idx * max(0, waited) * WAIT_COST
        return cost

    def day_cost(self, key: tuple, stops: list[Stop] | None = None) -> float:
        """Driving + every new visit's own cost; inf when the day does not fit."""
        if stops is None and key in self._cost:
            return self._cost[key]
        day = self.grid[key]
        trial = Day(day.tech, key[1], day.stops if stops is None else stops)
        timed = _schedule(trial, self.minutes.get(key[0], {}), self.drive)
        if timed is None:
            out = math.inf
        else:
            out = _drive_total(timed) + sum(self.visit_cost(s.job, key) for s in trial.stops
                                            if not s.existing and s.job)
        if stops is None:
            self._cost[key] = out
        return out

    def total(self) -> float:
        return sum(self.day_cost(k) for k in self.grid)

    def set_day(self, key: tuple, stops: list[Stop]) -> None:
        self.grid[key].stops = stops
        self._cost.pop(key, None)

    def placed(self) -> int:
        return sum(1 for d in self.grid.values() for s in d.stops if not s.existing)

    def best_insert(self, job: PlanJob, skip: tuple | None = None):
        """(added cost, key, stops) of the cheapest place for the job, or None."""
        best = None
        for key, day in self.grid.items():
            if key == skip or not _allowed(job, day.tech, key[1]) or \
                    len(day.stops) >= int(day.tech.get("max", 5)):
                continue
            base = self.day_cost(key)
            if base == math.inf:
                continue
            new = Stop(job, "", self.now, self.now, job.lat, job.lng, False, job.kind, job.uid)
            for i in range(len(day.stops) + 1):
                trial = [*day.stops[:i], new, *day.stops[i:]]
                added = self.day_cost(key, trial) - base
                if added < math.inf and (best is None or added < best[0]):
                    best = (added, key, trial)
        return best

    # ---- local search ------------------------------------------------------------------

    def reorder_day(self, key: tuple) -> bool:
        """Every order of the day's stops (booked visits keep their time order) when small
        enough; else 2-opt on the new ones. True when it found a cheaper order."""
        day = self.grid[key]
        if len(day.stops) < 2:
            return False
        best, best_stops = self.day_cost(key), None
        if len(day.stops) <= EXACT_ORDER_MAX:
            booked = [s for s in day.stops if s.existing]
            for order in permutations(day.stops):
                if [s for s in order if s.existing] != booked:
                    continue
                cost = self.day_cost(key, list(order))
                if cost < best - 0.5:
                    best, best_stops = cost, list(order)
        else:
            stops = list(day.stops)
            for i in range(len(stops) - 1):
                for k in range(i + 1, len(stops)):
                    seg = stops[i:k + 1]
                    if any(s.existing for s in seg):
                        continue
                    trial = stops[:i] + seg[::-1] + stops[k + 1:]
                    cost = self.day_cost(key, trial)
                    if cost < best - 0.5:
                        best, best_stops = cost, trial
        if best_stops is None:
            return False
        self.set_day(key, best_stops)
        return True

    def relocate(self) -> bool:
        moved = False
        for key in list(self.grid):
            for st in [x for x in self.grid[key].stops if not x.existing and x.job]:
                if st not in self.grid[key].stops:
                    continue
                here = self.day_cost(key)
                without = [x for x in self.grid[key].stops if x is not st]
                saved = here - self.day_cost(key, without)
                self.set_day(key, without)
                best = self.best_insert(st.job, skip=key)
                if best is not None and best[0] < saved - 1:
                    self.set_day(best[1], best[2])
                    moved = True
                else:
                    back = self.best_insert(st.job)
                    if back is None:
                        self.unplaced.append({"job": st.job})
                    else:
                        self.set_day(back[1], back[2])
        return moved

    def swap(self) -> bool:
        """Swap two new visits on different days / technicians when it lowers the cost."""
        new = [(k, s) for k, d in self.grid.items() for s in d.stops if not s.existing]
        for i in range(len(new)):
            ka, a = new[i]
            for kb, b in new[i + 1:]:
                if ka == kb or a not in self.grid[ka].stops or b not in self.grid[kb].stops:
                    continue
                if not (_allowed(a.job, self.grid[kb].tech, kb[1]) and
                        _allowed(b.job, self.grid[ka].tech, ka[1])):
                    continue
                before = self.day_cost(ka) + self.day_cost(kb)
                sa = [b if x is a else x for x in self.grid[ka].stops]
                sb = [a if x is b else x for x in self.grid[kb].stops]
                after = self.day_cost(ka, sa) + self.day_cost(kb, sb)
                if after < before - 1:
                    self.set_day(ka, sa)
                    self.set_day(kb, sb)
                    return True
        return False

    def retry_unplaced(self) -> bool:
        got = False
        for u in list(self.unplaced):
            if u.get("final"):
                continue
            best = self.best_insert(u["job"])
            if best is not None:
                self.set_day(best[1], best[2])
                self.unplaced.remove(u)
                got = True
        return got

    def improve(self) -> None:
        for _ in range(IMPROVE_ROUNDS):
            changed = False
            for key in list(self.grid):
                changed |= self.reorder_day(key)
            changed |= self.relocate()
            while self.swap():
                changed = True
            changed |= self.retry_unplaced()
            if not changed:
                break

    def score(self) -> tuple:
        return (-self.placed(), self.total())


def _reason(job: PlanJob, grid: dict, days: int) -> str:
    if not any(_allowed(job, d.tech, k[1]) for k, d in grid.items()):
        limit = "; ".join(job.limits) if job.limits else "its limits"
        return "No day in the next %d business days meets the customer's limits (%s)." % (
            days, limit)
    return "No room in the next %d business days (each technician's day is full)." % days


def _orders(jobs: list[PlanJob], now: datetime) -> list[list[PlanJob]]:
    """The starting orders tried: longest waiting, most limited first, two map sweeps and a
    few seeded shuffles (the same plan every time for the same input)."""
    def waited(j):
        return c.aware(j.waiting_since) or now
    out = [sorted(jobs, key=waited)]
    out.append(sorted(jobs, key=lambda j: (-(bool(j.weekdays) + bool(j.not_before) +
                                             bool(j.earliest) + bool(j.latest) +
                                             bool(j.technician) + bool(j.blocked)),
                                           waited(j))))
    if jobs:
        clat = sum(j.lat for j in jobs) / len(jobs)
        clng = sum(j.lng for j in jobs) / len(jobs)
        angle = sorted(jobs, key=lambda j: math.atan2(j.lat - clat, j.lng - clng))
        out += [angle, sorted(jobs, key=lambda j: -((j.lat - clat) ** 2 + (j.lng - clng) ** 2))]
    rng = random.Random(len(jobs) * 7919 + 17)
    for _ in range(RANDOM_STARTS):
        shuffled = list(jobs)
        rng.shuffle(shuffled)
        out.append(shuffled)
    return out


def plan(*, jobs: list[PlanJob], booked: list[booking.Visit], technicians: list[dict],
         minutes: dict[str, dict[str, int]], now: datetime, days: int = 6,
         mode: str = "most_jobs", drive: Callable = booking.drive_minutes) -> dict:
    mode = mode if mode in MODES else "most_jobs"
    weekdays = set().union(*[set(t.get("days", c.BUSINESS_DAYS)) for t in technicians]) \
        if technicians else set(c.BUSINESS_DAYS)
    dates = business_days(now, days, weekdays)
    base: dict[tuple[str, date], Day] = {}
    for t in technicians:
        for d in dates:
            if d.weekday() not in t.get("days", c.BUSINESS_DAYS):
                continue
            mine = sorted((v for v in booked if v.tech == t["name"]
                           and c.local(v.start).date() == d), key=lambda v: v.start)
            base[(t["name"], d)] = Day(t, d, [
                Stop(None, v.label or "booked visit", v.start, v.end, v.lat, v.lng, True)
                for v in mine])
    placeable = [j for j in jobs if j.lat is not None and j.lng is not None]
    no_map = [{"job": j, "final": True, "reason": "No map position in Zuper - book it by hand."}
              for j in jobs if j.lat is None or j.lng is None]

    def build(order: list[PlanJob]) -> _Plan:
        p = _Plan(base, minutes, dates, mode, now, drive)
        for job in order:
            best = p.best_insert(job)
            if best is None:
                p.unplaced.append({"job": job})
            else:
                p.set_day(best[1], best[2])
        return p

    orders = _orders(placeable, now)
    simple = build(orders[0])                       # one pass, longest waiting first
    baseline = {"placed": simple.placed(), "drive_minutes": _plan_drive(simple)}
    best = None
    candidates = orders if mode == "most_jobs" else orders[:1]
    for order in candidates:
        p = build(order)
        p.improve()
        if best is None or p.score() < best.score():
            best = p
    best = best or simple
    for u in best.unplaced:
        u["reason"] = _reason(u["job"], best.grid, days)
    best.unplaced = no_map + best.unplaced

    out_days = []
    for d in dates:
        techs = []
        for t in technicians:
            day = best.grid.get((t["name"], d))
            if day is None:
                continue
            timed = _schedule(day, minutes.get(t["name"], {}), drive) or []
            techs.append({
                "tech": t["name"], "start_from": "home" if t.get("home") else "first job",
                "count": len(timed), "capacity": int(t.get("max", 5)),
                "drive_minutes": _drive_total(timed),
                "first_start": c.local(timed[0].start).isoformat() if timed else None,
                "last_end": c.local(timed[-1].end).isoformat() if timed else None,
                "visits": [_visit_out(s) for s in timed]})
        out_days.append({"date": d.isoformat(),
                         "label": "%s %s %d" % (d.strftime("%a"), d.strftime("%b"), d.day),
                         "techs": techs})
    new = sum(1 for d in out_days for t in d["techs"] for v in t["visits"] if not v["existing"])
    return {
        "days": out_days, "mode": mode,
        "unplaced": [{
            "job_uid": u["job"].uid, "job_number": u["job"].number,
            "customer": u["job"].customer, "city": u["job"].city, "kind": u["job"].kind,
            "stage": u["job"].stage, "tentative": u["job"].tentative,
            "limits": u["job"].limits or None, "reason": u["reason"],
            "zuper_url": u["job"].zuper_url} for u in best.unplaced],
        "pending": len(jobs),
        "summary": {"placed": new, "not_placed": len(best.unplaced),
                    "tentative": sum(1 for d in out_days for t in d["techs"] for v in t["visits"]
                                     if v.get("tentative")),
                    "drive_minutes": sum(t["drive_minutes"] for d in out_days
                                         for t in d["techs"]),
                    "starts_tried": len(candidates)},
        "baseline": baseline,
    }


def _plan_drive(p: _Plan) -> int:
    total = 0
    for key, day in p.grid.items():
        timed = _schedule(day, p.minutes.get(key[0], {}), p.drive)
        total += _drive_total(timed) if timed else 0
    return total


def _visit_out(s: Stop) -> dict:
    j = s.job
    return {"job_uid": j.uid if j else None, "job_number": j.number if j else None,
            "customer": j.customer if j else s.label, "city": j.city if j else None,
            "address": j.address if j else None, "stage": j.stage if j else None,
            # Every time in New York, booked and proposed alike: mixed offsets would sort
            # wrongly as text.
            "kind": s.kind, "start": c.local(s.start).isoformat(),
            "end": c.local(s.end).isoformat(),
            "existing": s.existing, "drive_minutes_before": s.drive_before,
            "tentative": bool(j and j.tentative), "why": j.why if j else None,
            "limits": (j.limits or None) if j else None,
            "lat": s.lat, "lng": s.lng,
            "zuper_url": j.zuper_url if j else None}

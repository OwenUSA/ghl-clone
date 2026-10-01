"""Three slots to offer a customer on the call (phase 2, the owner's Q15/Q16/Q25).

PURE: the job, the visits already booked, the technicians' rules and `now` in; three slots out.
Pinned by tests/test_dispatch_booking.py.

How a slot is chosen:
  * who: the technician who PREFERS this kind of visit (Antonio repairs, Owen inspections) is
    tried first; the other one too, when he does that kind of visit — and wins only with a
    clearly better slot (less driving, or sooner);
  * when: business days from tomorrow, inside the technician's hours, under his daily maximum,
    never overlapping a visit he already has (driving time included);
  * where: the slot that adds the LEAST driving to his day. Distance is straight-line between
    Zuper's map coordinates x ROAD_FACTOR, at AVERAGE_KMH. No address leaves the CRM.

Each slot says who, when, how many extra minutes of driving it adds and which booked visit it
sits next to. A person still agrees it with the customer and books it in Zuper.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from . import config as c

ROAD_FACTOR = 1.3
AVERAGE_KMH = 50.0
GRID_MINUTES = 30
DAYS_AHEAD = 10
DAY_PENALTY_MINUTES = 12        # each day further out costs this much "driving" in the score
OTHER_TECH_PENALTY = 25         # the non-preferred technician must beat the other by this
SLOT_MINUTES = {"inspection": 90, "repair": 150}

# North Lauderdale, roughly the city centre (the owner: Antonio starts from home).
DEFAULT_TECHNICIANS = [
    {"name": "Antonio Brown", "does": ["repair", "inspection"], "prefers": "repair",
     "days": [0, 1, 2, 3, 4, 5], "start": "07:30", "end": "17:00", "max": 3,
     "home": [26.2173, -80.2259]},
    {"name": "Owen Buzaglo", "does": ["inspection", "repair"], "prefers": "inspection",
     "days": [0, 1, 2, 3, 4, 5], "start": "09:00", "end": "17:00", "max": 5, "home": None},
]


@dataclass
class Visit:
    tech: str
    start: datetime
    end: datetime
    lat: float | None
    lng: float | None
    label: str


@dataclass
class Slot:
    tech: str
    start: datetime
    end: datetime
    extra_drive_minutes: int
    next_to: str | None
    score: float = field(default=0.0, repr=False)

    def as_dict(self) -> dict:
        return {"tech": self.tech, "start": self.start.isoformat(), "end": self.end.isoformat(),
                "extra_drive_minutes": self.extra_drive_minutes, "next_to": self.next_to}


def km(a: tuple[float, float] | None, b: tuple[float, float] | None) -> float | None:
    if not a or not b or None in a or None in b:
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * \
        math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def drive_minutes(a, b) -> int:
    """Straight line x ROAD_FACTOR at AVERAGE_KMH. Unknown coordinates: a flat 30 minutes."""
    d = km(a, b)
    if d is None:
        return 30
    return round(d * ROAD_FACTOR / AVERAGE_KMH * 60)


def _hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def _at(day, t: time) -> datetime:
    return datetime.combine(day, t, tzinfo=c.TZ)


def slots(*, lat: float | None, lng: float | None, kind: str, visits: list[Visit],
          now: datetime, technicians: list[dict] | None = None, want: int = 3,
          tech: str | None = None) -> list[Slot]:
    techs = [t for t in (technicians or DEFAULT_TECHNICIANS)
             if kind in t.get("does", []) and (tech is None or t["name"] == tech)]
    length = timedelta(minutes=SLOT_MINUTES.get(kind, 120))
    here = (lat, lng) if lat is not None and lng is not None else None
    first_day = c.local(now).date() + timedelta(days=1)
    found: list[Slot] = []
    for t in techs:
        penalty = 0 if t.get("prefers") == kind else OTHER_TECH_PENALTY
        mine = sorted((v for v in visits if v.tech == t["name"]), key=lambda v: v.start)
        home = tuple(t["home"]) if t.get("home") else None
        for offset in range(DAYS_AHEAD):
            day = first_day + timedelta(days=offset)
            if day.weekday() not in t.get("days", c.BUSINESS_DAYS):
                continue
            today = [v for v in mine if c.local(v.start).date() == day]
            if len(today) >= int(t.get("max", 3)):
                continue
            open_at, close_at = _at(day, _hhmm(t["start"])), _at(day, _hhmm(t["end"]))
            best: Slot | None = None
            start = open_at
            while start + length <= close_at:
                end = start + length
                before = [v for v in today if v.end <= start]
                after = [v for v in today if v.start >= end]
                prev = before[-1] if before else None
                nxt = after[0] if after else None
                clash = any(v.start < end and v.end > start for v in today)
                prev_at = (prev.lat, prev.lng) if prev else home
                next_at = (nxt.lat, nxt.lng) if nxt else None
                to_here = drive_minutes(prev_at, here) if prev_at else 0
                from_here = drive_minutes(here, next_at) if next_at else 0
                direct = drive_minutes(prev_at, next_at) if prev_at and next_at else 0
                fits = (not prev or prev.end + timedelta(minutes=to_here) <= start) and \
                    (not nxt or end + timedelta(minutes=from_here) <= nxt.start)
                if not clash and fits:
                    extra = max(0, to_here + from_here - direct)
                    score = extra + offset * DAY_PENALTY_MINUTES + penalty
                    if best is None or score < best.score:
                        near = min((v for v in today), default=None, key=lambda v: (
                            km((v.lat, v.lng), here) or 1e9))
                        best = Slot(t["name"], start, end, extra,
                                    near.label if near else None, score)
                start += timedelta(minutes=GRID_MINUTES)
            if best:
                found.append(best)
    found.sort(key=lambda s: (s.score, s.start))
    out: list[Slot] = []
    for s in found:
        if not any(o.tech == s.tech and c.local(o.start).date() == c.local(s.start).date()
                   for o in out):
            out.append(s)
        if len(out) == want:
            break
    return out

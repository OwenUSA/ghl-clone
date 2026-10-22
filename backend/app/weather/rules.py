"""The weather agent's reading of a forecast - PURE: no database, no network
(tests/test_weather.py).

A forecast here is {"<UTC hour ISO>": [rain chance %, rain mm]} - the NWS grid's
probabilityOfPrecipitation and quantitativePrecipitation spread over the hours each value covers
(`hourly`). Everything the agent says comes from these numbers, quoted back with when the NWS made
them, so a flag can always be checked.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from ..dispatch import config as dc
from . import config as c

DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")


def _hour(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def key(dt: datetime) -> str:
    return _hour(dt).strftime("%Y-%m-%dT%H:00Z")


def span(valid_time: str) -> tuple[datetime, int] | None:
    """NWS "2026-10-22T18:00:00+00:00/PT3H" -> (start, hours)."""
    try:
        start_s, dur = valid_time.split("/")
        start = datetime.fromisoformat(start_s)
    except ValueError:
        return None
    m = DURATION.match(dur)
    if not m:
        return None
    d, h, mi = (int(x or 0) for x in m.groups())
    hours = max(1, d * 24 + h + (1 if mi else 0))
    return _hour(start), hours


def hourly(pop_values: list[dict], qpf_values: list[dict]) -> dict[str, list]:
    """Spread the NWS values over each hour they cover. Rain amount is shared evenly across its
    hours; a chance applies to every hour it covers."""
    out: dict[str, list] = {}
    for v in pop_values or []:
        s = span(str(v.get("validTime") or ""))
        if s is None or v.get("value") is None:
            continue
        for i in range(s[1]):
            out.setdefault(key(s[0] + timedelta(hours=i)), [None, None])[0] = int(v["value"])
    for v in qpf_values or []:
        s = span(str(v.get("validTime") or ""))
        if s is None or v.get("value") is None:
            continue
        share = float(v["value"]) / s[1]
        for i in range(s[1]):
            out.setdefault(key(s[0] + timedelta(hours=i)), [None, None])[1] = round(share, 2)
    return out


def merge(old: dict | None, new: dict, now: datetime, keep_hours: int = 48) -> dict:
    """The new forecast wins; hours that have passed stay for `keep_hours` (storm checks)."""
    cut = key(now - timedelta(hours=keep_hours))
    out = {k: v for k, v in (old or {}).items() if k >= cut and k < key(now)}
    out.update(new)
    return dict(sorted(out.items()))


@dataclass
class Window:
    pop: int | None           # the highest chance of rain in the window
    mm: float                 # rain expected in the window
    known: int                # hours the forecast covers
    hours: int                # hours in the window


def window(hours: dict, start: datetime, end: datetime) -> Window:
    t, pops, mm, known, total = _hour(start), [], 0.0, 0, 0
    end = end if end > start else start + timedelta(hours=1)
    while t < end:
        total += 1
        v = hours.get(key(t))
        if v is not None:
            known += 1
            if v[0] is not None:
                pops.append(v[0])
            mm += v[1] or 0.0
        t += timedelta(hours=1)
    return Window(max(pops) if pops else None, round(mm, 1), known, total)


def certainty(start: datetime, now: datetime) -> str:
    return "less certain (more than %d days out)" % c.SURE_DAYS \
        if start - now > timedelta(days=c.SURE_DAYS) else "forecast"


def visit_risk(hours: dict, start: datetime, end: datetime | None, pct: int) -> Window | None:
    """The visit's hours at the house; a Window when the chance of rain reaches `pct`."""
    w = window(hours, start, end or start + timedelta(hours=2))
    return w if w.pop is not None and w.pop >= pct else None


def heavy_rain(hours: dict, now: datetime, within_hours: int = c.URGENT_HOURS
               ) -> tuple[date, Window] | None:
    """(the New York day it rains most, the 48 h window) when heavy rain is coming."""
    w = window(hours, now, now + timedelta(hours=within_hours))
    if w.mm < c.HEAVY_MM or (w.pop or 0) < 50:
        return None
    by_day: dict[date, float] = {}
    for k, v in hours.items():
        at = datetime.fromisoformat(k)
        if now <= at < now + timedelta(hours=within_hours):
            d = dc.local(at).date()
            by_day[d] = by_day.get(d, 0.0) + (v[1] or 0.0)
    return max(by_day, key=by_day.get), w


def dry_days(hours: dict, today: date, days: int = c.HORIZON_DAYS) -> list[tuple[date, int]]:
    """Days whose working hours (8 AM - 5 PM New York) are all forecast and all under DRY_PCT:
    [(day, its highest chance)]."""
    out = []
    for i in range(1, days + 1):
        d = today + timedelta(days=i)
        start = datetime(d.year, d.month, d.day, c.DAY_OPEN, tzinfo=dc.TZ)
        w = window(hours, start, start + timedelta(hours=c.DAY_CLOSE - c.DAY_OPEN))
        if w.known == w.hours and w.pop is not None and w.pop < c.DRY_PCT:
            out.append((d, w.pop))
    return out


def rain_lookup(hours: dict):
    """A function for the booking slots: (start, end) -> highest chance of rain, or None."""
    def look(start: datetime, end: datetime) -> int | None:
        return window(hours, start, end).pop
    return look


def storm_hit(alerts: list[dict], zones: list[str], now: datetime) -> dict | None:
    """The latest official storm warning that covered these zones and ended within the lookback."""
    zs = set(zones or [])
    hits = [a for a in alerts if a.get("event") in c.STORM_EVENTS and zs & set(a.get("zones") or [])
            and a.get("ends") and now - timedelta(hours=c.STORM_LOOKBACK_HOURS) <= a["ends"] <= now]
    return max(hits, key=lambda a: a["ends"]) if hits else None


def pct_text(w: Window) -> str:
    """"70% chance of rain, about 0.25 in" - inches for the office, from the NWS millimetres."""
    rain = ", about %.2f in" % (w.mm / 25.4) if w.mm else ""
    return "%d%% chance of rain%s" % (w.pop or 0, rain)

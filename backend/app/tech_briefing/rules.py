"""The day-before briefing - PURE: no database, no network (tests/test_tech_briefing.py).

    due(now, hour, minute)      the date to brief (tomorrow) once it is past the send time
    by_technician(jobs, day)    tomorrow's visits grouped by technician, earliest first
    job_line(job, found, ...)   one job in a few words: when, who, where, what, bring, time,
                                and its Zuper link (2026-10-22) when one is given
    texts(tech, day, lines)     the briefing in texts of at most MAX_TEXT, never cutting a job

Recorded beats suggested: tools the technician wrote are shown as recorded; an AI suggestion is
shown only when nothing was recorded, and always says so.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..dispatch import config as dc

FIELDS = {
    "leak": "Leak location",
    "tools": "Tools & materials needed",
    "time": "Estimated time to complete",
    "couldnt": "Couldn't inspect yet (e.g. plywood under tiles)",
    "roof": "Roof type",
}
NOT_RECORDED = "not recorded"


def due(now: datetime, hour: int, minute: int) -> date | None:
    t = dc.local(now)
    if (t.hour, t.minute) < (hour, minute):
        return None
    return t.date() + timedelta(days=1)


def technician_of(job) -> str | None:
    name = (getattr(job, "technician", None) or "").strip()
    if name:
        return name
    assigned = [a for a in (getattr(job, "assigned", None) or []) if str(a).strip()]
    return str(assigned[0]).strip() if assigned else None


def by_technician(jobs, day: date, *, skip_stages=()) -> dict[str, list]:
    out: dict[str, list] = {}
    for j in jobs:
        start = dc.aware(getattr(j, "scheduled_start", None))
        if start is None or dc.local(start).date() != day:
            continue
        if not getattr(j, "is_open", True) or (getattr(j, "status", None) in skip_stages):
            continue
        tech = technician_of(j)
        if tech:
            out.setdefault(tech, []).append(j)
    for jobs_ in out.values():
        jobs_.sort(key=lambda j: dc.aware(j.scheduled_start))
    return out


def _hm(dt) -> str:
    t = dc.local(dc.aware(dt))
    return t.strftime("%I:%M %p").lstrip("0").replace(":00 ", " ")


def _value(fields: dict, found: dict, key: str) -> str | None:
    v = str((fields or {}).get(FIELDS[key]) or "").strip()
    if v and v.lower() not in ("-", "n/a", "na", "none"):
        return v
    alias = {"leak": "leak_location", "tools": "tools_materials", "time": "time_estimate",
             "couldnt": "couldnt_inspect"}.get(key)
    f = (found or {}).get(alias) if alias else None
    if isinstance(f, dict) and str(f.get("value") or "").strip():
        return str(f["value"]).strip()
    return None


@dataclass
class Line:
    job_number: str
    text: str
    needs_tools: bool = False
    facts: dict = field(default_factory=dict)


def job_line(job, found: dict | None, kind: str, suggestion: str | None = None,
             link: str | None = None) -> Line:
    fields = getattr(job, "fields", None) or {}
    when = _hm(job.scheduled_start)
    if getattr(job, "scheduled_end", None):
        when += "-" + _hm(job.scheduled_end)
    where = ", ".join(x for x in (getattr(job, "address", None), getattr(job, "city", None))
                      if x)
    head = "%s #%s %s%s - %s" % (when, job.job_number or "?",
                                  (getattr(job, "customer_name", None) or "").strip(),
                                  (", " + where) if where else "", kind)
    leak, tools = _value(fields, found, "leak"), _value(fields, found, "tools")
    hours, couldnt = _value(fields, found, "time"), _value(fields, found, "couldnt")
    roof = _value(fields, found, "roof")
    parts = [head + "."]
    if roof:
        parts.append("Roof: %s." % roof)
    if kind == "repair" or leak:
        parts.append("Leak: %s." % (leak or NOT_RECORDED))
    if tools:
        parts.append("Bring: %s." % tools)
    elif suggestion:
        parts.append("Bring (suggested - check the inspection): %s." % suggestion)
    elif kind == "repair":
        parts.append("Bring: %s." % NOT_RECORDED)
    if kind == "repair" or hours:
        parts.append("Time: %s." % (hours or NOT_RECORDED))
    if couldnt:
        parts.append("Not inspected yet: %s." % couldnt)
    if link:
        parts.append("Zuper: %s" % link)
    return Line(job.job_number or "?", " ".join(parts), needs_tools=not tools,
                facts={"roof": roof, "leak": leak, "couldnt": couldnt,
                       "title": getattr(job, "title", None)})


def texts(tech: str, day: date, lines: list[str], limit: int = 1000) -> list[str]:
    first = (tech or "").split()[0] if tech else ""
    header = "Dream Team Roofing - %syour jobs for %s:" % (
        (first + ", ") if first else "", day.strftime("%a %b ") + str(day.day))
    footer = "Reply here if something is wrong."
    out, cur = [], header
    for i, line in enumerate(lines, 1):
        item = "%d) %s" % (i, line)
        if len(cur) + 1 + len(item) > limit and cur != header:
            out.append(cur)
            cur = "(cont.) " + item
        else:
            cur += "\n" + item
    if len(cur) + 1 + len(footer) > limit:
        out.append(cur)
        cur = footer
    else:
        cur += "\n" + footer
    out.append(cur)
    return [t[:limit] for t in out]

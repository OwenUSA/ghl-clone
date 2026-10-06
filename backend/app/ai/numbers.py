"""AI Agents → Phone numbers and the agents' daily spend (2026-10-06, docs/RETELL-PLAN.md C5/C6).

Which number goes to which voice agent is decided HERE, by an ADMIN (decisions 6 and 18);
owen-main builds the flow for it from a template and keeps the flow it replaced. This module is
the CRM's half: what an assignment may say, checked before anything is asked, and what of
owen-main's answer reaches the browser. The routes are in `api.py` (ADMIN only); the requests
are `crmlink.phone_numbers` / `assign_number` / `unassign_number` / `agent_spend` /
`set_agent_spend`. Nothing here is sent while the link is unconfigured.

    mode            what owen-main builds
    ai_first        the agent answers every call
    staff_then_ai   the office rings first; the agent takes what nobody answers
    after_hours_ai  the office in its hours, the agent outside them — `hours` REQUIRED

There is NO default for the hours (the plan's rule: "no invented default"). An after-hours
assignment without them is refused here with a sentence, and owen-main refuses it too.
"""
from __future__ import annotations

import re
from itertools import pairwise

MODES = ("ai_first", "staff_then_ai", "after_hours_ai")
MODE_LABEL = {"ai_first": "AI answers every call",
              "staff_then_ai": "Office first, then AI",
              "after_hours_ai": "AI outside office hours"}
TIMEZONE = "America/New_York"
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MAX_RANGES = 4
# A number's id goes into a URL on a machine key: nothing but a plain token.
NUMBER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

NOT_LINKED = ("This server is not linked to the phone system (CRM_LINK_BASE_URL and "
              "CRM_LINK_API_KEY are not both set), so nothing was asked.")


class HoursError(ValueError):
    pass


def clean_hours(hours) -> dict:
    """{"tz": "America/New_York", "days": {"mon": [["08:00", "17:00"]], ...}} — only the days
    given, each with 1-4 non-overlapping ranges, start before end. The zone is the account's,
    always (the editor shows Eastern time)."""
    if not isinstance(hours, dict):
        raise HoursError("office hours must be an object of days")
    tz = hours.get("tz") or TIMEZONE
    if tz != TIMEZONE:
        raise HoursError("office hours are in Eastern time (%s)" % TIMEZONE)
    days = hours.get("days")
    if not isinstance(days, dict):
        raise HoursError("office hours need their days")
    unknown = sorted(set(days) - set(DAYS))
    if unknown:
        raise HoursError("unknown day(s): %s" % ", ".join(map(str, unknown)))
    out: dict[str, list[list[str]]] = {}
    for day in DAYS:
        ranges = days.get(day)
        if ranges in (None, []):
            continue
        if not isinstance(ranges, list) or len(ranges) > MAX_RANGES:
            raise HoursError("%s takes up to %d time ranges" % (day, MAX_RANGES))
        clean = []
        for r in ranges:
            if (not isinstance(r, (list, tuple)) or len(r) != 2
                    or not all(isinstance(x, str) and _HHMM.match(x) for x in r)):
                raise HoursError("%s: each range is [\"HH:MM\", \"HH:MM\"] (24-hour)" % day)
            if r[0] >= r[1]:
                raise HoursError("%s: %s-%s ends before it starts" % (day, r[0], r[1]))
            clean.append([r[0], r[1]])
        clean.sort()
        for a, b in pairwise(clean):
            if b[0] < a[1]:
                raise HoursError("%s: %s-%s overlaps %s-%s" % (day, *a, *b))
        out[day] = clean
    if not out:
        raise HoursError("choose at least one day with office hours")
    return {"tz": TIMEZONE, "days": out}


def number_out(n: dict, agents_by_owen: dict[str, dict]) -> dict | None:
    """One of owen-main's numbers, as the browser gets it: named keys only, and the CRM agent
    an assignment points at when the CRM has one."""
    if not isinstance(n, dict) or n.get("id") in (None, ""):
        return None
    a = n.get("assignment") if isinstance(n.get("assignment"), dict) else None
    assignment = None
    if a:
        name = str(a.get("agent_name") or "")
        crm = agents_by_owen.get(" ".join(name.split()).lower())
        assignment = {"agent_name": name, "mode": a.get("mode"),
                      "mode_label": MODE_LABEL.get(str(a.get("mode")), a.get("mode")),
                      "hours": a.get("hours") if isinstance(a.get("hours"), dict) else None,
                      "crm_agent_id": crm["id"] if crm else None,
                      "crm_agent_name": crm["name"] if crm else None}
    return {"id": str(n["id"]), "e164": n.get("e164"), "label": n.get("label"),
            "assignable": bool(n.get("assignable")),
            "reason": n.get("reason") if not n.get("assignable") else None,
            "assignment": assignment}


def spend_out(data: dict | None) -> dict:
    d = data if isinstance(data, dict) else {}

    def num(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    cap, today, pct = num(d.get("daily_cap_usd")), num(d.get("today_usd")), num(d.get("alert_pct"))
    return {"daily_cap_usd": cap, "alert_pct": pct, "today_usd": today,
            "used_pct": round(today / cap * 100) if cap and today is not None else None,
            "over_alert": bool(cap and today is not None and pct is not None
                               and today >= cap * pct / 100)}

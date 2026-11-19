"""Owen AI makes the technicians' routes (2026-11-19).

The owner: "in the CRM there is an AI agent that makes the routes for the technicians, with a map
— I want Owen AI to do something similar". It is the SAME planner as the Dispatch page and its
chat (app/dispatch/scheduling.py + planner.py), with the same rules for everyone:

  * a customer who can only do one day gets that day first; visits close together share a day;
  * a repair only with the customer's OK (accepted proposal, AHS authorization, or a yes Owen AI
    read in a call / text and passes in `agreed` with the customer's words);
  * an inspection nobody verified by phone is CALL FIRST;
  * the crew and the days each technician works (dispatch/crew.py; Owen is on call);
  * rain is a FLAG only.

The plan is a DRAFT kept in `dispatch_plans` (source "owen_ai"); the Owen AI page draws it under
the answer (the map, the week grid, the Excel) from the plan link this tool tells Claude to
write. Nothing is booked and nothing is sent to Zuper. `set_crew_days` writes the CRM's own
crew settings — the only thing these tools change.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from ..dispatch import ai as dispatch_ai
from ..dispatch import config as dc
from ..dispatch import crew, scheduling
from . import alerts

MAX_CHARS = 60_000


def _now() -> datetime:
    return datetime.now(UTC)


def _session():
    from ..db import SessionLocal
    return SessionLocal()


def _json(payload) -> str:
    from .tools import compact
    out = json.dumps(compact(payload), ensure_ascii=False, default=str)
    return out if len(out) <= MAX_CHARS else out[:MAX_CHARS] + "\n...[cut: ask for fewer days]"


def plan_link(plan_id: int) -> str:
    return "[Plan #%d — map and list](/dispatch?tab=book&plan=%d)" % (plan_id, plan_id)


def headline(out: dict) -> str:
    """One line Owen can read at a glance (and the first thing the step log keeps)."""
    new = [v for d in out["days"] for t in d["techs"] for v in t["visits"] if not v["existing"]]
    s = out["summary"]
    w = out.get("weather") or {}
    rain = ("rain flags: %d (%d next day)" % (len(w.get("flagged") or []),
                                              sum(1 for f in w.get("flagged") or []
                                                  if f.get("next_day")))
            if w.get("enabled") else "weather agent off - no rain flags")
    return ("Placed %d (%d inspections, %d repairs; %d CALL FIRST) · %d not placed · %d "
            "repairs waiting for the customer's OK · %.1f h driving · %s" % (
                len(new), sum(1 for v in new if v["kind"] == "inspection"),
                sum(1 for v in new if v["kind"] == "repair"), s.get("tentative", 0),
                s.get("not_placed", 0), s.get("waiting_ok", 0),
                (s.get("drive_minutes") or 0) / 60, rain))


def tool_plan_routes(args: dict) -> str:
    days = args.get("days") or 6
    kind = str(args.get("kind") or "all")
    mode = str(args.get("mode") or "most_jobs")
    boards = str(args.get("boards") or "active")
    if kind not in ("all", "inspection", "repair"):
        return "ERROR: kind is all, inspection or repair"
    if mode not in ("most_jobs", "oldest_first"):
        return "ERROR: mode is most_jobs or oldest_first"
    if boards not in scheduling.BOARD_SETS:
        return "ERROR: boards is active (AHS Simple Flow + Retail), ahs or all"
    db = _session()
    try:
        now = _now()
        who = alerts.owner(db)
        out = scheduling.make_plan(
            db, now, days=int(days), kind=kind, mode=mode, boards=boards,
            include_unreached=args.get("include_unreached") is not False,
            limits=args.get("limits") if isinstance(args.get("limits"), list) else None,
            agreed=args.get("agreed") if isinstance(args.get("agreed"), list) else None,
            hidden=set(), user_id=who.id if who else None, source="owen_ai")
        small = scheduling.for_model(db, out, now)
        small["crew"] = [crew.describe(t) for t in crew.roster(db)]
        small["who_works"] = crew.calendar(crew.roster(db), dc.local(now).date()
                                           + timedelta(days=1), n=min(14, int(days) + 2))
    finally:
        db.close()
    return ("Plan #%d saved (DRAFT: nothing booked, nothing sent to Zuper). Put this line in "
            "your answer exactly: %s\n%s\n%s" % (out["id"], plan_link(out["id"]), headline(out),
                                                 _json(small)))


def tool_crew_days(args: dict) -> str:
    db = _session()
    try:
        techs = crew.roster(db)
    finally:
        db.close()
    today = dc.local(_now()).date()
    return _json({"crew": [crew.describe(t) for t in techs],
                  "next_14_days": crew.calendar(techs, today + timedelta(days=1))})


def tool_set_crew_days(args: dict) -> str:
    db = _session()
    try:
        now = _now()
        today = dc.local(now).date()
        techs = crew.roster(db)
        found = crew.find(techs, str(args.get("technician") or ""))
        if isinstance(found, str):
            return "ERROR: " + found
        mx = args.get("max_per_day")
        new, problems = crew.change_ranges(
            found, today=today, working=args.get("working"), off=args.get("off"),
            remove=args.get("remove"), max_per_day=int(mx) if mx else None)
        if problems:
            return "ERROR: nothing saved - " + "; ".join(problems)
        techs = [new if t["name"] == found["name"] else t for t in techs]
        who = alerts.owner(db)
        crew.save(db, techs, who.id if who else None, now)
    finally:
        db.close()
    return "Saved for %s (CRM crew settings; every plan uses them).\n%s" % (
        new["name"], _json({"technician": crew.describe(new),
                            "next_14_days": crew.calendar(techs, today + timedelta(days=1))}))


RANGE = {"type": "array", "items": {
    "type": "object", "properties": {
        "from": {"type": "string", "description": "YYYY-MM-DD"},
        "to": {"type": "string", "description": "YYYY-MM-DD (same as from for one day)"},
        "note": {"type": "string"}},
    "required": ["from", "to"], "additionalProperties": False}}

SPECS = (
    ("plan_routes",
     ("Make the technicians' ROUTES / schedule (map + list): a DRAFT plan for every job that "
      "needs a visit on AHS Simple Flow and Retail (boards 'ahs' or 'all' for the old boards), "
      "fitted around what is booked, with the crew's days and daily maximums. Rules built in: a "
      "customer who can only do one day gets it first; nearby customers share a day; a REPAIR "
      "only with the customer's OK (accepted proposal, AHS authorization, or a yes you pass in "
      "`agreed` with their exact words) — the rest come back in waiting_for_customer_ok; an "
      "inspection nobody verified by phone is CALL FIRST; rain is flagged only. days = business "
      "days from tomorrow (1 = tomorrow only, 6 = a week). Nothing is booked. Put the plan link "
      "line it returns in your answer: the page draws the map and the week under it. Follow the "
      "route-schedule skill."),
     {"type": "object", "properties": {
         "days": {"type": "integer", "minimum": 1, "maximum": 18},
         "kind": {"type": "string", "enum": ["all", "inspection", "repair"]},
         "mode": {"type": "string", "enum": ["most_jobs", "oldest_first"]},
         "boards": {"type": "string", "enum": ["active", "ahs", "all"]},
         "include_unreached": {"type": "boolean"},
         "limits": dispatch_ai.LIMITS_SCHEMA,
         "agreed": dispatch_ai.AGREED_SCHEMA},
      "additionalProperties": False},
     tool_plan_routes),
    ("crew_days",
     ("The technicians the routes use: what each does (inspection / repair), his most visits a "
      "day, hours, usual days or ON CALL, the days he said he works / is off, and who works each "
      "of the next 14 days."),
     {"type": "object", "properties": {}, "additionalProperties": False},
     tool_crew_days),
    ("set_crew_days",
     ("Save the days a technician works or is off, as Owen tells you (CRM settings, kept for "
      "every plan; nothing in Zuper). `working` only for an ON-CALL technician (Owen): the days "
      "he will work, with `max_per_day` (ASK Owen how many jobs a day; default 2). `off` for "
      "anyone: days he does not work. `remove`: ranges to delete (same from / to). Dates "
      "YYYY-MM-DD."),
     {"type": "object", "properties": {
         "technician": {"type": "string"},
         "working": RANGE, "off": RANGE, "remove": RANGE,
         "max_per_day": {"type": "integer", "minimum": 1, "maximum": 12}},
      "required": ["technician"], "additionalProperties": False},
     tool_set_crew_days),
)

"""The crew the week planner fits visits to: who does what, how many a day, and which days they
work (2026-11-19).

The owner: "for now we are handling Antonio and a new technician Marx, 2 jobs per day maximum"
(Marx does repairs only), "at any point Owen can decide he is going to be working", and the days
each technician works are told in Owen AI's chat. One list for every plan — the Dispatch page,
its chat and Owen AI — kept in `dispatch_settings.technicians` (JSON, no migration):

    {name, does: [inspection, repair], prefers, days: [0..6], start "07:30", end "17:00",
     max, home: [lat, lng] | null, minutes?: {kind: minutes},
     on_call?: true,                        # works ONLY the days in `only` (Owen)
     only?: [{from, to, max?, note?}],      # the days an on-call technician said he works
     off?: [{from, to, note?}]}             # days off, for anyone

`booking.works` / `booking.day_max` read it. The Settings page edits hours, days and maximums
and never sends the ranges: `merge_page_save` keeps them, so an open page cannot wipe what was
said in the chat.

    python -m app.dispatch.crew                      # DRY RUN: the list now, the decided crew
    python -m app.dispatch.crew --marx "Marx Name" --commit   # save the decided crew
"""
from __future__ import annotations

import argparse
import copy
import json
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.orm import Session

from . import booking
from . import config as c

RANGE_KEYS = ("on_call", "only", "off", "minutes")
KEEP_DAYS = 30            # a range that ended longer ago than this is dropped on the next change
AHEAD_DAYS = 180
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MARX = "Marx"             # replaced by his exact Zuper name (booked visits match it exactly)

# The crew the owner decided on 2026-11-19.
CREW_2026_11 = [
    {"name": "Antonio Brown", "does": ["repair", "inspection"], "prefers": "repair",
     "days": [0, 1, 2, 3, 4, 5], "start": "07:30", "end": "17:00", "max": 2,
     "home": [26.2173, -80.2259]},
    {"name": MARX, "does": ["repair"], "prefers": "repair",
     "days": [0, 1, 2, 3, 4, 5], "start": "07:30", "end": "17:00", "max": 2, "home": None},
    {"name": "Owen Buzaglo", "does": ["inspection", "repair"], "prefers": "inspection",
     "days": [0, 1, 2, 3, 4, 5], "start": "09:00", "end": "17:00", "max": 2, "home": None,
     "on_call": True, "only": []},
]


def roster(db: Session) -> list[dict]:
    from . import ai
    return copy.deepcopy(ai.settings(db).technicians or booking.DEFAULT_TECHNICIANS)


def find(techs: list[dict], name: str) -> dict | str:
    """The technician called `name` (full name, or a first name only one has), else a sentence."""
    want = " ".join(str(name or "").split()).lower()
    exact = [t for t in techs if t["name"].lower() == want]
    if exact:
        return exact[0]
    near = [t for t in techs if want and (t["name"].lower().split()[0] == want.split()[0]
                                           or want in t["name"].lower())]
    if len(near) == 1:
        return near[0]
    names = ", ".join(t["name"] for t in techs)
    return ("No technician called %r (the crew: %s)." % (name, names) if not near else
            "%r matches %s: say which." % (name, " and ".join(t["name"] for t in near)))


def merge_page_save(stored: list[dict] | None, incoming: list[dict]) -> list[dict]:
    """The Settings page's list, keeping the ranges / on-call / visit lengths it did not send."""
    before = {t["name"]: t for t in (stored or [])}
    out = []
    for t in incoming:
        t = dict(t)
        old = before.get(t["name"], {})
        for k in RANGE_KEYS:
            if k not in t and k in old:
                t[k] = old[k]
        out.append(t)
    return out


def _day(value) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _ranges(raw, today: date, *, with_max: int | None = None) -> tuple[list[dict], list[str]]:
    keep, problems = [], []
    for r in raw or []:
        a = _day((r or {}).get("from"))
        z = _day((r or {}).get("to") or (r or {}).get("from"))
        if a is None or z is None:
            problems.append("dates must be YYYY-MM-DD (got %s)" % json.dumps(r))
            continue
        if z < a:
            problems.append("%s ends before it starts" % a.isoformat())
            continue
        if z < today:
            problems.append("%s - %s is already over" % (a.isoformat(), z.isoformat()))
            continue
        if a > today + timedelta(days=AHEAD_DAYS):
            problems.append("%s is more than %d days ahead" % (a.isoformat(), AHEAD_DAYS))
            continue
        out = {"from": a.isoformat(), "to": z.isoformat()}
        m = (r or {}).get("max") or with_max
        if m:
            out["max"] = max(1, min(int(m), 12))
        if (r or {}).get("note"):
            out["note"] = str(r["note"])[:120]
        keep.append(out)
    return keep, problems


def change_ranges(t: dict, *, today: date, working=None, off=None, remove=None,
                  max_per_day: int | None = None) -> tuple[dict, list[str]]:
    """A technician with days added / removed. Pure: (the new technician, problems)."""
    t = copy.deepcopy(t)
    problems: list[str] = []
    if working and not t.get("on_call"):
        problems.append("%s is not on call - he works his usual days; tell me the days he "
                        "is OFF instead." % t["name"])
        working = None
    add_on, p1 = _ranges(working, today, with_max=max_per_day)
    add_off, p2 = _ranges(off, today)
    problems += p1 + p2
    gone = {(_day(r.get("from")), _day(r.get("to") or r.get("from"))) for r in remove or []}

    def tidy(rs):
        return [r for r in rs if (_day(r.get("from")), _day(r.get("to"))) not in gone
                and (_day(r.get("to")) or today) >= today - timedelta(days=KEEP_DAYS)]
    t["only"] = tidy(list(t.get("only") or []) + add_on)
    t["off"] = tidy(list(t.get("off") or []) + add_off)
    if not t["only"] and not t.get("on_call"):
        t.pop("only")
    if not t["off"]:
        t.pop("off")
    return t, problems


def calendar(techs: list[dict], start: date, n: int = 14) -> list[dict]:
    """Who works each of the next `n` days, and how many visits each may take."""
    out = []
    for i in range(n):
        d = start + timedelta(days=i)
        who = [{"name": t["name"], "max": booking.day_max(t, d)} for t in techs
               if booking.works(t, d)]
        out.append({"date": d.isoformat(), "day": "%s %s" % (DAY_NAMES[d.weekday()],
                                                           d.strftime("%m/%d")),
                    "working": who})
    return out


def describe(t: dict) -> dict:
    """One technician, for a person or a model."""
    def span(r):
        a, z = r["from"], r["to"]
        return (a if a == z else "%s to %s" % (a, z)) + (
            " (max %d)" % r["max"] if r.get("max") else "")
    return {"name": t["name"], "does": t.get("does"), "max_per_day": t.get("max"),
            "hours": "%s-%s" % (t.get("start"), t.get("end")),
            "usual_days": "on call: only the days below" if t.get("on_call") else
            "/".join(DAY_NAMES[d] for d in t.get("days", c.BUSINESS_DAYS)),
            "working": [span(r) for r in t.get("only") or []] or None,
            "off": [span(r) for r in t.get("off") or []] or None}


def save(db: Session, techs: list[dict], user_id: int | None, now: datetime) -> None:
    from . import ai
    s = ai.settings(db, for_update=True)
    s.technicians = copy.deepcopy(techs)       # a new list, so the JSON change is seen
    s.updated_at, s.updated_by_id = now, user_id
    db.commit()


# What the decision changes on a technician already on the list (hours, home, visit lengths
# and days off stay as they are).
DECIDED_KEYS = ("does", "prefers", "max", "on_call")


def decided_crew(marx_name: str, current: list[dict] | None = None) -> list[dict]:
    """The 2026-11-19 crew, on top of the list as it is: Antonio and Marx 2 a day, Marx repairs
    only, Owen on call. Anyone else on the list is left as is."""
    now_by = {t["name"]: t for t in current or []}
    crew = []
    for t in copy.deepcopy(CREW_2026_11):
        if t["name"] == MARX:
            t["name"] = marx_name
        old = now_by.pop(t["name"], None)
        if old is not None:
            merged = copy.deepcopy(old)
            merged.update({k: t[k] for k in DECIDED_KEYS if k in t})
            if t.get("on_call"):
                merged.setdefault("only", [])
            t = merged
        crew.append(t)
    return crew + list(now_by.values())


def _zuper_names(word: str) -> list[str]:
    """Zuper users whose name has `word` — one read-only GET, for the operator's dry run."""
    from ..zuper import client, zapi
    with client.operator_mode(), client.read_only():
        users = zapi.users()
    names = [" ".join(x for x in (u.get("first_name"), u.get("last_name")) if x).strip()
             for u in users]
    return sorted(n for n in names if word.lower() in n.lower())


def main(argv: list[str] | None = None) -> int:
    from ..db import SessionLocal
    ap = argparse.ArgumentParser(prog="python -m app.dispatch.crew")
    ap.add_argument("--marx", help="Marx's name exactly as Zuper shows it")
    ap.add_argument("--commit", action="store_true", help="save the decided crew")
    args = ap.parse_args(argv)
    db = SessionLocal()
    try:
        print("Now:")
        for t in roster(db):
            print("  ", json.dumps(describe(t)))
        if not args.marx:
            try:
                print("Zuper users named like 'marx':", _zuper_names("marx") or "none")
            except Exception as exc:                    # noqa: BLE001 - a dry run never fails
                print("Zuper users could not be read: %s" % exc)
            print("Run again with --marx \"<exact name>\" (and --commit to save).")
            return 0
        crew = decided_crew(args.marx.strip(), roster(db))
        print("Decided crew (2026-11-19):")
        for t in crew:
            print("  ", json.dumps(describe(t)))
        if not args.commit:
            print("DRY RUN - nothing saved. Add --commit to save it.")
            return 0
        save(db, crew, None, datetime.now(UTC))
        print("Saved.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

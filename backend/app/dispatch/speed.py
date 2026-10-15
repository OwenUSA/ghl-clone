"""Speed to lead (2026-10-15): how fast a person on the team answers a new lead.

A LEAD is a Zuper job on a board a customer first lands on — AHS - Inspection, Retail and the
six regional copies of Retail — never AHS - Repair & Review (a job there was moved or made from
an inspection), never a job titled "TEST…". Everything is read from what the Dispatch reader
already keeps: the job (`dispatch_jobs`), its column moves (`zuper_status_history`) and every
call and text on its customer's numbers from Quo, the CRM's line and Zuper Connect
(`comms.load`). Nothing here asks Zuper for anything or writes anywhere.

The clock:
  * STARTS when the job was created in Zuper (for an AHS email card, minutes after the work
    order arrived) — or earlier, at the customer's own call or text in the three days before,
    when they reached out first.
  * STOPS at the first RESPONSE by a person: our call (answered or not), our text, or their
    call that a person answered and talked on for TALK_SECONDS. A call an AI agent answered
    alone, an AI agent's text and an automatic text are not a response.
  * Counts BUSINESS minutes (Mon-Sat 8 AM - 6 PM New York, the Dispatch clock), so a work order
    that arrives at 9 PM is not "late" overnight. Clock minutes are given beside them.

The target is the owner's (dispatch/config.SLA_MINUTES): 2 business hours on AHS, 1 on Retail.
A lead with no response yet is WAITING until its target passes, then NOT CONTACTED (late).

Missed calls are the other half — a Retail customer usually rings before any job exists: every
inbound call nobody answered, from any number, and how long until a person called or texted that
number back (target: SLA_MINUTES["missed"], 30 business minutes).

Known blind spots, reported with the numbers: texts staff send from inside Zuper Connect are not
read; calls from personal cell phones are invisible; Zuper Connect calls exist here only from the
first Dispatch read (2026-09-23 on production).
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import DispatchCall, DispatchJob, ZuperStatusHistory
from . import comms as cm
from . import config as c

LEAD_BOARDS = (c.INSPECTION_BOARD, c.RETAIL_BOARD, *c.OTHER_PIPELINES)
LOOKBACK = timedelta(days=3)       # "the customer reached out first" — how far back
TEST_PREFIX = "TEST"

# Result of one lead, in the order a person reads them.
ON_TIME, LATE, NOT_CONTACTED, WAITING, NO_PHONE = (
    "on_time", "late", "not_contacted", "waiting", "no_phone")
RESULT_LABEL = {ON_TIME: "On time", LATE: "Late", NOT_CONTACTED: "Not contacted",
                WAITING: "Waiting (within target)", NO_PHONE: "No phone on the job"}


def group_of(board: str | None) -> str:
    return "AHS" if board == c.INSPECTION_BOARD else "Retail"


def target_minutes(board: str | None) -> int:
    return c.SLA_MINUTES["new_ahs" if group_of(board) == "AHS" else "new_retail"]


def business_minutes(start: datetime, end: datetime) -> int:
    """Business minutes (Mon-Sat 8-6 New York) between two moments; 0 when end <= start."""
    a, b = c.local(start), c.local(end)
    if b <= a:
        return 0
    total = 0.0
    day = a.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= b:
        if day.weekday() in c.BUSINESS_DAYS:
            lo = max(a, day.replace(hour=c.OPEN.hour, minute=c.OPEN.minute))
            hi = min(b, day.replace(hour=c.CLOSE.hour, minute=c.CLOSE.minute))
            if hi > lo:
                total += (hi - lo).total_seconds() / 60
        # Next local midnight; built from the date so a DST change cannot skip a day.
        nxt = (day + timedelta(days=1, hours=2)).date()
        day = datetime(nxt.year, nxt.month, nxt.day, tzinfo=c.TZ)
    return round(total)


def arrival_bucket(at: datetime) -> str:
    t = c.local(at)
    if t.weekday() not in c.BUSINESS_DAYS:
        return "Sunday"
    if t.time() < c.OPEN or t.time() >= c.CLOSE:
        return "Mon-Sat, after hours"
    return "Mon-Sat 8 AM-12 PM" if t.hour < 12 else "Mon-Sat 12-6 PM"


ARRIVAL_ORDER = ("Mon-Sat 8 AM-12 PM", "Mon-Sat 12-6 PM", "Mon-Sat, after hours", "Sunday")


def how(x: cm.Comm) -> str:
    if x.out:
        return "%s %s" % (x.source, "call" if x.kind == "call" else "text")
    return "Answered their %s call" % x.source


def _iso(dt: datetime | None) -> str | None:
    return c.aware(dt).isoformat() if dt else None


@dataclass
class Job:
    """What one lead needs; built from a DispatchJob (or by a test)."""
    job_uid: str
    job_number: str | None
    board: str
    customer_name: str | None
    status: str | None
    created_at: datetime
    phones: list[str]


def measure(j: Job, mine: list[cm.Comm], moves: list[tuple[datetime, str | None, str | None]],
            now: datetime) -> dict:
    """One lead: when its clock started, the first response, the first conversation, the first
    column move in Zuper, and whether it met its target. `mine` = every comm on the job's
    numbers, oldest first; `moves` = (changed_at, column, by), oldest first."""
    created = c.aware(j.created_at)
    target = target_minutes(j.board)
    row = {"job_uid": j.job_uid, "job_number": j.job_number, "board": j.board,
           "group": group_of(j.board), "customer_name": j.customer_name, "status": j.status,
           "created_at": _iso(created), "target_minutes": target}
    mine = sorted(mine, key=lambda x: x.at)
    first_in = next((x for x in mine if not x.out and created - LOOKBACK <= x.at < created),
                    None)
    start = first_in.at if first_in else created
    row.update(start_at=_iso(start), arrived=arrival_bucket(start),
               started_by=("Customer %s first" % ("called" if first_in.kind == "call"
                                                  else "texted")) if first_in else "Job created")
    due = c.add_business_minutes(start, target)
    row["due_at"] = _iso(due)

    human = [x for x in mine if x.at >= start and not x.ai]
    resp = next((x for x in human if x.out or x.talked), None)
    talk = next((x for x in human if x.talked), None)
    row.update(
        first_response_at=_iso(resp.at) if resp else None,
        first_response_how=how(resp) if resp else None,
        first_response_by=(resp.staff or ("%s (person not recorded)" % resp.source))
        if resp else None,
        response_minutes=business_minutes(start, resp.at) if resp else None,
        response_clock_minutes=round((resp.at - start).total_seconds() / 60) if resp else None,
        first_talk_at=_iso(talk.at) if talk else None,
        talk_minutes=business_minutes(start, talk.at) if talk else None,
        attempts_before_talk=sum(1 for x in human if x.out and x.kind == "call" and
                                 (talk is None or x.at < talk.at)),
        missed_their_calls=sum(1 for x in mine if x.missed and x.at >= start and
                               (resp is None or x.at < resp.at)),
        ai_handled=any(x.ai for x in mine if x.at >= start and (resp is None or x.at < resp.at)),
    )
    # Zuper's own trace: the first time the job left the column it was created in.
    later = [m for m in moves if c.aware(m[0]) > created + timedelta(minutes=1)]
    first_move = later[0] if later else None
    row.update(first_move_at=_iso(first_move[0]) if first_move else None,
               first_move_to=first_move[1] if first_move else None,
               first_move_by=first_move[2] if first_move else None,
               move_minutes=business_minutes(created, c.aware(first_move[0]))
               if first_move else None)

    if not j.phones:
        result = NO_PHONE
    elif resp:
        result = ON_TIME if resp.at <= due else LATE
    else:
        result = WAITING if now < due else NOT_CONTACTED
    row["result"] = result
    row["result_label"] = RESULT_LABEL[result]
    row["waiting_minutes"] = business_minutes(start, now) if not resp else None
    return row


# ---------------------------------------------------------------------------- the numbers

def _median(xs):
    return round(statistics.median(xs)) if xs else None


def _pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def summarize(rows: list[dict]) -> dict:
    """The headline for a set of leads. `on_time_pct` is of the leads whose outcome is known:
    on time / (on time + late + not contacted) — a lead still within its target is not judged."""
    by = defaultdict(int)
    for r in rows:
        by[r["result"]] += 1
    judged = by[ON_TIME] + by[LATE] + by[NOT_CONTACTED]
    resp = [r["response_minutes"] for r in rows if r["response_minutes"] is not None]
    talk = [r["talk_minutes"] for r in rows if r["talk_minutes"] is not None]
    moved = [r["move_minutes"] for r in rows if r["move_minutes"] is not None]
    reached = [r for r in rows if r["first_talk_at"]]
    lag = [r["move_minutes"] - r["response_minutes"] for r in rows
           if r["move_minutes"] is not None and r["response_minutes"] is not None]
    return {
        "leads": len(rows),
        "on_time": by[ON_TIME], "late": by[LATE], "not_contacted": by[NOT_CONTACTED],
        "waiting": by[WAITING], "no_phone": by[NO_PHONE],
        "on_time_pct": round(100 * by[ON_TIME] / judged) if judged else None,
        "response_median": _median(resp), "response_p75": _pct(resp, .75),
        "response_p90": _pct(resp, .9),
        "response_avg": round(sum(resp) / len(resp)) if resp else None,
        "talk_median": _median(talk),
        "reached_pct": round(100 * len(reached) / len(rows)) if rows else None,
        "attempts_before_talk_avg": round(sum(r["attempts_before_talk"] for r in reached) /
                                          len(reached), 1) if reached else None,
        "customer_first": sum(1 for r in rows if r["started_by"] != "Job created"),
        "zuper_move_median": _median(moved),
        # How far Zuper's first column move trails our first response (business minutes).
        "zuper_move_lag_median": _median(lag),
    }


def _table(rows: list[dict], key, order=None) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        k = key(r)
        if k:
            groups[k].append(r)
    names = [k for k in (order or ()) if k in groups] + sorted(
        k for k in groups if k not in (order or ()))
    return [{"name": k, **summarize(groups[k])} for k in names]


def week_of(iso: str) -> str:
    d = c.local(datetime.fromisoformat(iso)).date()
    return (d - timedelta(days=d.weekday())).isoformat()


def missed_calls(comms: dict[str, list[cm.Comm]], since: datetime, until: datetime,
                 now: datetime) -> dict:
    """Every inbound call nobody answered, from any number, in the window — and how long until a
    person called, texted or talked to that number. Calls missed again before the callback are
    one wait (counted in `rings`), measured from the first."""
    target = c.SLA_MINUTES["missed"]
    waits: list[dict] = []
    for _number, mine in comms.items():
        mine = sorted(mine, key=lambda x: x.at)
        i = 0
        while i < len(mine):
            x = mine[i]
            if not (x.missed and since <= x.at < until):
                i += 1
                continue
            rings, back = 1, None
            for y in mine[i + 1:]:
                if not y.ai and (y.out or y.talked):
                    back = y
                    break
                if y.missed:
                    rings += 1
            due = c.add_business_minutes(x.at, target)
            mins = business_minutes(x.at, back.at) if back else None
            waits.append({"at": x.at, "source": x.source, "rings": rings, "minutes": mins,
                          "result": (ON_TIME if back.at <= due else LATE) if back else
                          (WAITING if now < due else NOT_CONTACTED),
                          "by": how(back) if back else None})
            # Skip the rings this wait already covers.
            j = i + 1
            while j < len(mine) and (back is None or mine[j].at < back.at):
                j += 1
            i = j
    mins = [w["minutes"] for w in waits if w["minutes"] is not None]
    by = defaultdict(int)
    for w in waits:
        by[w["result"]] += 1
    judged = by[ON_TIME] + by[LATE] + by[NOT_CONTACTED]
    per_source: dict[str, list[dict]] = defaultdict(list)
    for w in waits:
        per_source[w["source"]].append(w)
    return {
        "target_minutes": target, "missed": len(waits),
        "rings": sum(w["rings"] for w in waits),
        "called_back": len(mins), "on_time": by[ON_TIME], "late": by[LATE],
        "never_called_back": by[NOT_CONTACTED], "waiting": by[WAITING],
        "on_time_pct": round(100 * by[ON_TIME] / judged) if judged else None,
        "callback_median": _median(mins), "callback_p90": _pct(mins, .9),
        "by_line": [{"name": k, "missed": len(v),
                     "callback_median": _median([w["minutes"] for w in v
                                                 if w["minutes"] is not None]),
                     "never_called_back": sum(1 for w in v if w["result"] == NOT_CONTACTED)}
                    for k, v in sorted(per_source.items())],
    }


def build(jobs: list[Job], comms: dict[str, list[cm.Comm]],
          moves: dict[str, list[tuple[datetime, str | None, str | None]]],
          since: datetime, until: datetime, now: datetime) -> dict:
    """The whole report for leads whose clock started in [since, until)."""
    rows = []
    for j in jobs:
        mine = [x for p in j.phones for x in comms.get(p, [])]
        r = measure(j, mine, moves.get(j.job_uid, []), now)
        if since <= datetime.fromisoformat(r["start_at"]) < until:
            rows.append(r)
    rows.sort(key=lambda r: r["start_at"], reverse=True)
    return {
        "targets": {"AHS": target_minutes(c.INSPECTION_BOARD),
                    "Retail": target_minutes(c.RETAIL_BOARD)},
        "summary": {"all": summarize(rows),
                    "AHS": summarize([r for r in rows if r["group"] == "AHS"]),
                    "Retail": summarize([r for r in rows if r["group"] == "Retail"])},
        "by_board": _table(rows, lambda r: r["board"], LEAD_BOARDS),
        "by_first_response": _table(rows, lambda r: r["first_response_how"]),
        "by_person": _table(rows, lambda r: r["first_response_by"]),
        "by_arrival": _table(rows, lambda r: r["arrived"], ARRIVAL_ORDER),
        "by_week": sorted(_table(rows, lambda r: week_of(r["start_at"])),
                          key=lambda t: t["name"]),
        "missed_calls": missed_calls(comms, since, until, now),
        "leads": rows,
    }


# ---------------------------------------------------------------------------- from the database

def lead_jobs(db: Session, hidden: set[str] = frozenset()) -> list[Job]:
    out = []
    for j in db.scalars(select(DispatchJob).where(DispatchJob.board.in_(LEAD_BOARDS))):
        if j.board in hidden or not j.zuper_created_at or \
                (j.title or "").strip().upper().startswith(TEST_PREFIX):
            continue
        out.append(Job(job_uid=j.job_uid, job_number=j.job_number, board=j.board,
                       customer_name=j.customer_name, status=j.status,
                       created_at=c.aware(j.zuper_created_at), phones=list(j.phones or [])))
    return out


def report(db: Session, since: datetime, until: datetime, now: datetime,
           hidden: set[str] = frozenset(), group: str | None = None) -> dict:
    """`group` "AHS" / "Retail" narrows the leads (and every table built from them); the missed
    calls are every line's, whatever the group."""
    jobs = [j for j in lead_jobs(db, hidden) if group is None or group_of(j.board) == group]
    uids = [j.job_uid for j in jobs]
    moves: dict[str, list] = defaultdict(list)
    if uids:
        for m in db.scalars(select(ZuperStatusHistory)
                            .where(ZuperStatusHistory.job_uid.in_(uids))
                            .order_by(ZuperStatusHistory.changed_at)):
            if m.changed_at:
                moves[m.job_uid].append((c.aware(m.changed_at), m.status_name,
                                         m.done_by_name))
    comms = cm.load(db, since - LOOKBACK - timedelta(days=1))
    out = build(jobs, comms, moves, since, until, now)
    first_zuper_call = db.scalar(select(func.min(DispatchCall.occurred_at)))
    out["window"] = {"since": since.isoformat(), "until": until.isoformat(),
                     "now": now.isoformat()}
    out["coverage"] = {
        "zuper_calls_from": _iso(first_zuper_call),
        "notes": [
            ("Texts sent from inside Zuper Connect are not read, so a lead answered only by a "
             "Zuper text shows as not contacted."),
            "Calls from personal cell phones are invisible.",
            "Business minutes: Mon-Sat 8 AM-6 PM New York.",
        ]}
    if first_zuper_call is None or c.aware(first_zuper_call) > since:
        out["coverage"]["notes"].insert(
            0, ("Calls made inside Zuper are only known from %s; before that a lead may look "
                "slower than it was.") % (c.local(first_zuper_call).date().isoformat()
                                        if first_zuper_call else "the first Dispatch read"))
    return out

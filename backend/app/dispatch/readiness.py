"""Is a job ready to go on the schedule? The owner's two gates (2026-11-19).

  * **A repair goes on the schedule only when the customer agreed** — any of: a proposal the
    customer accepted / signed in Zuper, AHS authorized the repair (the "AHS Approved" column
    counts, the owner said), or the customer said yes on a call or text (passed in, with the
    words, by whoever read the call: Owen AI or the Dispatch assistant). A repair with none of
    these is NOT planned: it is listed as "waiting for the customer's OK", with what was checked.
    Callbacks too — the owner chose to apply the check to them.
  * **Before an inspection somebody must have called the customer** to get or verify the
    information. An inspection with no such call is still planned, marked CALL FIRST.

Every answer names its evidence (what, when, who), so the office can check it. The rules are
pure (`repair_agreed`, `inspection_verified`); `Evidence.load` reads the CRM's own copies once
per plan — never Zuper itself (the sweep keeps quotes within 15 minutes, the Dispatch pass keeps
moves and notes within minutes).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    DispatchEvent,
    DispatchJob,
    DispatchNote,
    DispatchPlan,
    ZuperDocument,
    ZuperProposalLine,
    ZuperStatusHistory,
)
from . import comms, lookups
from . import config as c

ACCEPTED = {"APPROVED", "ACCEPTED"}          # Zuper's estimate_status for a "yes"
DECLINED = {"DECLINED", "REJECTED"}
# A "yes" passed in a plan is remembered by the next plans (the page's button too) this long.
REMEMBER_DAYS = 21
SOURCES = ("call", "text", "proposal", "ahs", "other")
# On AHS Simple Flow, AHS authorizes the repair between Day of Inspection and Proposal Creation
# (the board's own flow): a job that MOVED into one of these went through it. A job created
# straight in Repair Day (a callback) did not, so it needs other evidence.
SF_AFTER_AUTHORIZATION = (c.SF_PROPOSAL, c.SF_HANDOFF, c.SF_REPAIR_DAY)
AHS_APPROVED = "AHS Approved"
AUTHO = re.compile(r"AUTHO\s*#?\s*:?\s*([A-Za-z0-9][A-Za-z0-9-]{2,39})", re.IGNORECASE)
VERIFIED_QUESTION = re.compile(r"service address\s*(verified|confirmed)", re.IGNORECASE)
AHS_AUTHORIZED_FIELD = re.compile(r"^AHS authorized", re.IGNORECASE)
OPTION_CHOSEN = re.compile(r"^option chosen", re.IGNORECASE)
YES = {"yes", "y", "true", "1", "si", "sí", "verified", "confirmed"}


@dataclass
class Move:
    status: str | None
    at: datetime | None
    by: str | None
    board: str | None
    checklist: list | None = None


@dataclass
class Evidence:
    """Everything the gates read for a set of jobs, loaded once."""
    quotes: dict[str, list[ZuperDocument]] = field(default_factory=dict)
    signed: dict[str, list[ZuperProposalLine]] = field(default_factory=dict)
    moves: dict[str, list[Move]] = field(default_factory=dict)
    autho: dict[str, tuple[str, datetime | None]] = field(default_factory=dict)
    ahs_events: dict[str, datetime | None] = field(default_factory=dict)
    talks: dict[str, list[comms.Comm]] = field(default_factory=dict)
    agreed: dict[str, dict] = field(default_factory=dict)        # job number -> a passed "yes"

    @classmethod
    def load(cls, db: Session, now: datetime, jobs: list[DispatchJob],
             agreed: list[dict] | None = None) -> Evidence:
        ev = cls()
        uids = [j.job_uid for j in jobs]
        if not uids:
            return ev
        for d in db.scalars(select(ZuperDocument).where(
                ZuperDocument.kind == ZuperDocument.QUOTE, ZuperDocument.job_uid.in_(uids),
                ZuperDocument.removed_in_zuper.is_(False))):
            ev.quotes.setdefault(d.job_uid, []).append(d)
        for p in db.scalars(select(ZuperProposalLine).where(ZuperProposalLine.job_uid.in_(uids))):
            ev.signed.setdefault(p.job_uid, []).append(p)
        for m in db.scalars(select(ZuperStatusHistory).where(
                ZuperStatusHistory.job_uid.in_(uids)).order_by(ZuperStatusHistory.changed_at)):
            ev.moves.setdefault(m.job_uid, []).append(Move(
                m.status_name, c.aware(m.changed_at), m.done_by_name, m.category_name,
                m.checklist))
        for n in db.scalars(select(DispatchNote).where(
                DispatchNote.job_uid.in_(uids), DispatchNote.removed.is_(False))):
            hit = AUTHO.search(n.text or "")
            if hit and n.job_uid not in ev.autho:
                ev.autho[n.job_uid] = (hit[1], c.aware(n.created_at))
        for e in db.scalars(select(DispatchEvent).where(
                DispatchEvent.kind == "ahs_approved", DispatchEvent.job_uid.in_(uids))):
            ev.ahs_events[e.job_uid] = c.aware(e.created_at)
        # Calls: every number the customer can be reached on (theirs + the ones they gave).
        since = now - timedelta(days=60)
        talk = comms.load(db, since)
        reach = lookups.customer_phones(db, now, days=30)
        for j in jobs:
            numbers = reach.get(j.job_uid, set()) | set(j.phones or [])
            ev.talks[j.job_uid] = sorted((x for p in numbers for x in talk.get(p, [])),
                                         key=lambda x: x.at)
        ev.agreed = remembered(db, now)
        for a in clean_agreed(agreed or [], now)[0]:
            ev.agreed[a["job_number"]] = a
        return ev


def _yes(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in YES


def _short(dt: datetime | None) -> str:
    return c.local(dt).strftime("%a %m/%d") if dt else "date unknown"


def clean_agreed(items: list, now: datetime) -> tuple[list[dict], list[str]]:
    """The "yes" items a chat passed: (kept, why each other was refused). A yes needs the job,
    where it came from, the customer's own words and when they said it."""
    keep, refused = [], []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        number = str(raw.get("job_number") or "").strip().lstrip("#")
        quote = " ".join(str(raw.get("quote") or "").split())
        source = str(raw.get("source") or "").strip().lower()
        said = lookups.parse_day(str(raw.get("said_at") or ""), now) if raw.get("said_at") \
            else None
        if not number:
            continue
        if len(quote) < 3:
            refused.append("#%s: a yes needs the customer's own words (quote)" % number)
            continue
        if source not in SOURCES:
            refused.append("#%s: source must be one of %s" % (number, ", ".join(SOURCES)))
            continue
        keep.append({"job_number": number, "source": source, "quote": quote[:300],
                     "said_at": said.isoformat() if said else str(raw.get("said_at") or "")[:40],
                     "passed_at": now.isoformat()})
    return keep, refused


def remembered(db: Session, now: datetime) -> dict[str, dict]:
    """The yeses passed in plans of the last REMEMBER_DAYS, newest wins: so the page's button
    plans with what Owen AI read in a call yesterday (no table of its own: the plan keeps it)."""
    out: dict[str, dict] = {}
    for p in db.scalars(select(DispatchPlan).where(
            DispatchPlan.created_at >= now - timedelta(days=REMEMBER_DAYS))
            .order_by(DispatchPlan.created_at)):
        for a in (p.params or {}).get("agreed") or []:
            if isinstance(a, dict) and a.get("job_number"):
                out[str(a["job_number"])] = {**a, "plan_id": p.id}
    return out


def _quote_state(docs: list[ZuperDocument]) -> tuple[ZuperDocument | None, ZuperDocument | None]:
    """(the newest accepted quote, the newest declined one) by Zuper's update time."""
    def when(d):
        return str(d.zuper_updated_at or d.fetched_at or "")
    ok = [d for d in docs if (d.status or "").upper() in ACCEPTED]
    no = [d for d in docs if (d.status or "").upper() in DECLINED]
    return (max(ok, key=when) if ok else None), (max(no, key=when) if no else None)


def repair_agreed(j: DispatchJob, ev: Evidence) -> dict | None:
    """The evidence that the customer agreed to this repair, or None. {source, text}."""
    accepted, declined = _quote_state(ev.quotes.get(j.job_uid, []))
    declined_at = str(declined.zuper_updated_at or "") if declined else ""
    if accepted is not None and str(accepted.zuper_updated_at or "") >= declined_at:
        return {"source": "proposal",
                "text": "Proposal %s accepted in Zuper" % (accepted.number or "")}
    if ev.signed.get(j.job_uid) and not declined:
        p = ev.signed[j.job_uid][0]
        return {"source": "proposal", "text": "Proposal %s signed (option %s)" % (
            p.estimate_no or "", p.option_name or "?")}
    moves = ev.moves.get(j.job_uid, [])
    ahs = next((m for m in reversed(moves) if m.status == AHS_APPROVED), None)
    if ahs is not None:
        return {"source": "ahs", "text": "Moved to AHS Approved %s%s" % (
            _short(ahs.at), " by %s" % ahs.by if ahs.by else "")}
    if j.status == AHS_APPROVED:
        return {"source": "ahs", "text": "In AHS Approved since %s%s" % (
            _short(c.aware(j.status_since)), " (%s)" % j.status_by if j.status_by else "")}
    if j.board == c.SIMPLE_FLOW_BOARD:
        moved = next((m for i, m in enumerate(moves) if m.status in SF_AFTER_AUTHORIZATION
                      and m.board in (None, c.SIMPLE_FLOW_BOARD)
                      and any(x.status in (c.SF_NEW, c.SF_INSPECTION_DAY) for x in moves[:i])),
                     None)
        if moved is not None:
            return {"source": "ahs", "text": "Moved to %s %s after the inspection (AHS "
                                             "authorizes before that column)" % (
                                                 moved.status, _short(moved.at))}
    if j.job_uid in ev.autho:
        number, at = ev.autho[j.job_uid]
        return {"source": "ahs", "text": "AHS AUTHO # %s in a Zuper note (%s)" % (
            number, _short(at))}
    for label, value in (j.fields or {}).items():
        if AHS_AUTHORIZED_FIELD.search(str(label)) and _money(value) > 0:
            return {"source": "ahs", "text": "%s: %s" % (label, value)}
    if j.job_uid in ev.ahs_events:
        return {"source": "ahs", "text": "AHS authorization email %s" % _short(
            ev.ahs_events[j.job_uid])}
    for m in moves:
        for q in m.checklist or []:
            if isinstance(q, dict) and OPTION_CHOSEN.search(str(q.get("question") or "")) \
                    and str(q.get("answer") or "").strip():
                return {"source": "proposal", "text": "Option chosen \"%s\" recorded when it "
                        "moved to %s (%s)" % (str(q["answer"])[:60], m.status, _short(m.at))}
    said = ev.agreed.get(str(j.job_number))
    if said is not None and not declined:
        where = " (passed in plan #%s)" % said["plan_id"] if said.get("plan_id") else ""
        return {"source": said["source"], "text": "Customer said yes (%s %s): \"%s\"%s" % (
            said["source"], said.get("said_at") or "", said["quote"][:160], where)}
    return None


def _money(value) -> float:
    try:
        return float(re.sub(r"[^\d.]", "", str(value or "")) or 0)
    except ValueError:
        return 0.0


def waiting_on(j: DispatchJob, ev: Evidence) -> tuple[str, str]:
    """For a repair with no "yes": (who we wait on, what was checked)."""
    _, declined = _quote_state(ev.quotes.get(j.job_uid, []))
    quotes = ev.quotes.get(j.job_uid, [])
    if declined is not None:
        return "customer", "Proposal %s was DECLINED in Zuper" % (declined.number or "")
    if quotes:
        states = ", ".join("%s %s" % (d.number or "proposal", (d.status or "?").lower())
                           for d in quotes[:3])
        return "customer", "Not accepted yet (%s); no yes in the calls passed" % states
    if j.board in (c.SIMPLE_FLOW_BOARD, c.INSPECTION_BOARD, c.REPAIR_BOARD):
        return "ahs", ("No AHS authorization found (no AHS Approved move, AUTHO # in the notes, "
                       "'AHS authorized' field or AHS email) and no proposal")
    return "proposal", "No proposal in Zuper and no yes in the calls passed"


def inspection_verified(j: DispatchJob, ev: Evidence) -> dict | None:
    """Did somebody call the customer to get / verify the information? {text} or None."""
    created = c.aware(j.zuper_created_at) or c.aware(j.first_seen_at)
    talk = next((x for x in ev.talks.get(j.job_uid, []) if x.kind == "call" and x.talked
                 and not x.ai and (created is None or x.at >= created)), None)
    if talk is not None:
        return {"text": "%s call %s (%d min, %s)" % (
            "Our" if talk.out else "Their", _short(talk.at), max(1, talk.seconds // 60),
            talk.staff or talk.source)}
    for label, value in (j.fields or {}).items():
        if VERIFIED_QUESTION.search(str(label)) and _yes(value):
            return {"text": "%s: %s" % (label, value)}
    moves = ev.moves.get(j.job_uid, [])
    for m in moves:
        for q in m.checklist or []:
            if isinstance(q, dict) and VERIFIED_QUESTION.search(str(q.get("question") or "")) \
                    and _yes(q.get("answer")):
                return {"text": "\"%s\" answered yes at %s (%s)" % (
                    q["question"], m.status, _short(m.at))}
    for i, m in enumerate(moves):
        if m.status != "Welcome Call!":
            continue
        # The migration moved jobs from Welcome Call! into "Got a job from AHS" (a category
        # change, not a call): only a move into a BOOKED stage proves the call happened.
        nxt = next((x for x in moves[i + 1:] if x.status != m.status), None)
        if nxt is not None and (lookups.booked_stage(nxt.status) or (nxt.status or "")
                                .startswith(c.NEEDS_DATE)):
            return {"text": "Welcome call made: moved to %s %s" % (nxt.status, _short(nxt.at))}
    return None

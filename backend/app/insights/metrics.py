"""Every KPI of the report, from ONE place, over a date range (New York days) and filters.

    period(range, day)            (first day, last day) of the day / week / month / quarter
    compute(db, first, last, ...) {"tiles", "series", "tables", "events"} - the whole report

The rules are the nightly KPI Excel's (`kpi_report`): a callback is `is_callback` (Zuper Revisit,
"Callback/Warranty", the tag) charged to the ORIGINAL job's Technician; an upsell is a move whose
checklist says "Customer chose" other than "AHS only", worth its "Customer paid ($)". Work done is
a column move in `zuper_status_history`: into Inspection Completed / Proposal Made (inspected),
Repair Complete (repaired), AHS Approved (approved, with "AHS authorized ($)"). What customers
SAID comes from `conversation_signals` (each with its quote). Boards hidden from the reader
(pipeline permissions) are left out of everything.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import kpi_report as kr
from ..dispatch import config as dc
from ..models import (
    ConversationSignal,
    DispatchItem,
    PaymentAgreement,
    PaymentMilestone,
    ReviewRequest,
    ZuperStatusHistory,
)
from . import config as c

RANGES = ("day", "week", "month", "quarter")
UNKNOWN = "(not recorded)"


def period(rng: str, day: date) -> tuple[date, date]:
    if rng == "day":
        return day, day
    if rng == "week":
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=6)
    if rng == "month":
        start = day.replace(day=1)
        nxt = (start.replace(year=start.year + 1, month=1) if start.month == 12
               else start.replace(month=start.month + 1))
        return start, nxt - timedelta(days=1)
    if rng == "quarter":
        q0 = (day.month - 1) // 3 * 3 + 1
        start = day.replace(month=q0, day=1)
        nxt = (start.replace(year=start.year + 1, month=1) if q0 == 10
               else start.replace(month=q0 + 3))
        return start, nxt - timedelta(days=1)
    raise ValueError(rng)


def previous(first: date, last: date, rng: str) -> tuple[date, date]:
    if rng in ("month", "quarter"):
        return period(rng, first - timedelta(days=1))
    span = (last - first).days + 1
    return first - timedelta(days=span), first - timedelta(days=1)


def _bounds(first: date, last: date) -> tuple[datetime, datetime]:
    a = datetime.combine(first, time(0), tzinfo=dc.TZ).astimezone(UTC)
    b = datetime.combine(last + timedelta(days=1), time(0), tzinfo=dc.TZ).astimezone(UTC)
    return a, b


def _day(dt) -> date | None:
    dt = dc.aware(dt) if isinstance(dt, datetime) else None
    return dc.local(dt).date() if dt else None


def _bucket(d: date, first: date, last: date) -> str:
    if (last - first).days <= 31:
        return d.isoformat()
    return (d - timedelta(days=d.weekday())).isoformat()


def _answer(checklist, question: str):
    for item in checklist or []:
        if isinstance(item, dict) and item.get("question") == question and \
                item.get("answer") not in (None, "", []):
            return item.get("answer")
    return None


def compute(db: Session, first: date, last: date, *, hidden: set[str] = frozenset(),
            board: str | None = None, technician: str | None = None) -> dict:
    start, end = _bounds(first, last)
    jobs = kr.latest_jobs(db)
    by_uid = {j.get("job_uid"): j for j in jobs}

    def board_ok(b: str | None) -> bool:
        return not (b and b in hidden) and (board is None or b == board)

    def tech_of(job: dict | None) -> str:
        v = kr.custom_field(job, "Technician") if job else None
        return str(v).strip() if v and str(v).strip() else UNKNOWN

    def tech_ok(t: str) -> bool:
        return technician is None or t.lower() == technician.lower()

    series: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    per_tech: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    events: list[dict] = []

    # ---- leads: jobs created in the range
    leads = 0
    for j in jobs:
        b = kr.board_of(j)
        d = _day(kr.parse_dt(str(j.get("created_at") or "")))
        if not d or not first <= d <= last or not board_ok(b) or not tech_ok(tech_of(j)):
            continue
        leads += 1
        series["leads"][_bucket(d, first, last)] += 1

    # ---- work done, approvals, upsells: column moves in the range
    inspected, repaired, approved = set(), set(), set()
    authorized = 0.0
    upsell_rows, sold = [], 0.0
    for m in db.scalars(select(ZuperStatusHistory).where(
            ZuperStatusHistory.changed_at >= start, ZuperStatusHistory.changed_at < end)):
        if not board_ok(m.category_name):
            continue
        job = by_uid.get(m.job_uid)
        t = tech_of(job)
        if not tech_ok(t):
            continue
        d = _day(m.changed_at)
        bk = _bucket(d, first, last)
        if m.status_name in c.STAGE_INSPECTED and m.job_uid not in inspected:
            inspected.add(m.job_uid)
            series["inspections"][bk] += 1
            per_tech[t]["inspections"] += 1
        if m.status_name in c.STAGE_REPAIRED and m.job_uid not in repaired:
            repaired.add(m.job_uid)
            series["repairs"][bk] += 1
            per_tech[t]["repairs"] += 1
        if m.status_name in c.STAGE_AHS_APPROVED and m.job_uid not in approved:
            approved.add(m.job_uid)
            authorized += kr.money(_answer(m.checklist, kr.Q_AHS_AUTHORIZED)) or 0
        chose = _answer(m.checklist, kr.Q_CUSTOMER_CHOSE)
        if chose and str(chose).strip().lower() != "ahs only":
            paid = kr.money(_answer(m.checklist, kr.Q_CUSTOMER_PAID)) or 0
            upsell_rows.append({"job": m.job_number, "board": m.category_name, "day": d,
                                "technician": t, "chose": str(chose), "paid": paid})
            per_tech[t]["upsells"] += 1
            per_tech[t]["upsell_paid"] += paid
            series["upsell_paid"][bk] += paid
        sp = kr.money(_answer(m.checklist, kr.Q_SOLD_PRICE))
        if sp:
            sold += sp

    # ---- callbacks: re-work jobs created in the range, charged to the original's technician
    cb_rows = []
    for j in jobs:
        if not kr.is_callback(j) or not board_ok(kr.board_of(j)):
            continue
        d = _day(kr.parse_dt(str(j.get("created_at") or j.get("scheduled_start_time") or "")))
        if not d or not first <= d <= last:
            continue
        orig = by_uid.get(kr.parent_uid(j))
        t = tech_of(orig) if orig else tech_of(j)
        if not tech_ok(t):
            continue
        od = _day(kr.parse_dt(str(orig.get("scheduled_start_time") or ""))) if orig else None
        cb_rows.append({"job": j.get("work_order_number"), "board": kr.board_of(j), "day": d,
                        "original": orig.get("work_order_number") if orig else None,
                        "technician": t, "days_later": (d - od).days if od else None})
        per_tech[t]["callbacks"] += 1
        series["callbacks"][_bucket(d, first, last)] += 1

    # ---- what customers said
    said = defaultdict(list)
    for s in db.scalars(select(ConversationSignal).where(
            ConversationSignal.at >= start, ConversationSignal.at < end)
            .order_by(ConversationSignal.at)):
        if (s.board and not board_ok(s.board)) or (board and s.board != board):
            continue
        t = s.technician or UNKNOWN
        if not tech_ok(t):
            continue
        said[s.tag].append(s)
    leaking_jobs: dict[str, ConversationSignal] = {}
    for s in said.get("still_leaking", []):
        key = s.job_uid or "phone:%s" % s.phone
        if key not in leaking_jobs:
            leaking_jobs[key] = s
            per_tech[s.technician or UNKNOWN]["still_leaking"] += 1
            series["still_leaking"][_bucket(_day(s.at), first, last)] += 1

    # ---- money collected (Collections) and what is overdue now
    collected = 0
    for m, a in db.execute(select(PaymentMilestone, PaymentAgreement).join(
            PaymentAgreement, PaymentAgreement.id == PaymentMilestone.agreement_id).where(
            PaymentMilestone.state == "paid", PaymentMilestone.paid_on >= first,
            PaymentMilestone.paid_on <= last)):
        if board_ok(a.board) or a.board is None:
            collected += m.paid_cents or 0
    overdue_work = sum(1 for i in db.scalars(select(DispatchItem).where(
        DispatchItem.state == "open", DispatchItem.urgent.is_(True)))
        if not (i.board and i.board in hidden))

    # ---- ratings customers gave (agent #9, app/reviews): answered in the range
    rating_rows = []
    for r in db.scalars(select(ReviewRequest).where(
            ReviewRequest.replied_at >= start, ReviewRequest.replied_at < end,
            ReviewRequest.score.isnot(None)).order_by(ReviewRequest.replied_at)):
        t = r.technician or UNKNOWN
        if not board_ok(r.board) or not tech_ok(t):
            continue
        rating_rows.append({"day": _day(r.replied_at).isoformat(), "job": r.job_number,
                            "customer": r.customer_name, "technician": t, "board": r.board,
                            "score": r.score, "leak": r.leak, "said": r.reply,
                            "to_improve": r.improve, "review_link_sent": r.link_sent})
        per_tech[t]["ratings"] += 1
        per_tech[t]["rating_sum"] += r.score
        if r.score < 5 or r.leak:
            per_tech[t]["low_ratings"] += 1
    asked = sum(1 for _ in db.scalars(select(ReviewRequest.id).where(
        ReviewRequest.asked_at >= start, ReviewRequest.asked_at < end,
        ReviewRequest.asked_state == "sent")))

    tech_rows = []
    for t, v in sorted(per_tech.items(), key=lambda kv: -kv[1].get("repairs", 0)):
        reps = int(v.get("repairs", 0))
        cbs = int(v.get("callbacks", 0))
        tech_rows.append({"technician": t, "inspections": int(v.get("inspections", 0)),
                          "repairs": reps, "callbacks": cbs,
                          "callback_rate": round(cbs / reps, 3) if reps else None,
                          "still_leaking": int(v.get("still_leaking", 0)),
                          "upsells": int(v.get("upsells", 0)),
                          "upsell_paid": round(v.get("upsell_paid", 0.0), 2),
                          "avg_rating": round(v["rating_sum"] / v["ratings"], 2)
                          if v.get("ratings") else None,
                          "low_ratings": int(v.get("low_ratings", 0))})
    tiles = {
        "leads": leads, "inspections": len(inspected), "ahs_approvals": len(approved),
        "ahs_authorized": round(authorized, 2), "repairs": len(repaired),
        "callbacks": len(cb_rows), "still_leaking": len(leaking_jobs),
        "callback_requests": len(said.get("callback_request", [])),
        "complaints": len(said.get("complaint", [])), "praise": len(said.get("praise", [])),
        "upsells": len(upsell_rows), "upsell_paid": round(sum(r["paid"] for r in upsell_rows), 2),
        "retail_sold": round(sold, 2), "collected": round(collected / 100, 2),
        "overdue_work": overdue_work,
        "ratings": len(rating_rows),
        "avg_rating": round(sum(r["score"] for r in rating_rows) / len(rating_rows), 2)
        if rating_rows else 0,
        "five_star_pct": round(100 * sum(1 for r in rating_rows if r["score"] == 5)
                               / len(rating_rows)) if rating_rows else 0,
        "answered_pct": round(100 * len(rating_rows) / asked) if asked else 0,
        "review_links": sum(1 for r in rating_rows if r["review_link_sent"]),
    }
    return {
        "first": first.isoformat(), "last": last.isoformat(), "tiles": tiles,
        "series": {k: dict(sorted(v.items())) for k, v in series.items()},
        "tables": {
            "technicians": tech_rows,
            "callbacks": [dict(r, day=r["day"].isoformat()) for r in cb_rows],
            "still_leaking": [_said(s) for s in leaking_jobs.values()],
            "complaints": [_said(s) for s in said.get("complaint", [])],
            "callback_requests": [_said(s) for s in said.get("callback_request", [])],
            "upsells": [dict(r, day=r["day"].isoformat() if r["day"] else None)
                        for r in upsell_rows],
            "ratings": rating_rows,
        },
        "events": events,
    }


def _said(s: ConversationSignal) -> dict:
    return {"id": s.id, "day": _day(s.at).isoformat() if s.at else None, "job": s.job_number,
            "customer": s.customer_name, "technician": s.technician or UNKNOWN,
            "board": s.board, "source": s.source, "quote": s.quote}


TILE_LABELS = {
    "leads": "New jobs", "inspections": "Inspections done", "ahs_approvals": "AHS approvals",
    "ahs_authorized": "AHS authorized ($)", "repairs": "Repairs done",
    "callbacks": "Callbacks", "still_leaking": "Still leaking",
    "callback_requests": "Asked us to come back", "complaints": "Complaints",
    "praise": "Praise", "upsells": "Upsells", "upsell_paid": "Upsell paid ($)",
    "retail_sold": "Retail sold ($)", "collected": "Collected ($)",
    "overdue_work": "Overdue work (now)",
    "ratings": "Ratings received", "avg_rating": "Average rating",
    "five_star_pct": "5-star %", "answered_pct": "Answered the rating text %",
    "review_links": "Review links sent",
}
# A rise is bad for these (shown red); good for the rest.
LOWER_IS_BETTER = {"callbacks", "still_leaking", "callback_requests", "complaints",
                   "overdue_work"}

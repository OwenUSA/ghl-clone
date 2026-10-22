"""Payments - PURE rules: no database, no network (tests/test_payments.py).

    split(total, "50/50")        two parts that add up to the cent: deposit before, balance after
    due_date(rule, on, job)      when a part is due, or None while it cannot be known yet
    status(...)                  upcoming | due | overdue | paid | waived
    agreement_state(...)         quoted | agreed | collecting | paid (cancelled is a person's)
    reminder_due(...)            the customer reminder's spacing and cap (off by default)
    money(cents) / cents(text)   "$1,500.00" <-> 150000
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ..dispatch import config as dc

AFTER_REPAIR = {"Repair Complete", "Call Satisfaction Check", "Invoice",
                "Invoice Submitted to AHS", "Collect Balance", "Review Requested",
                "Review Received", "Paid"}


def money(c: int | None) -> str:
    if c is None:
        return "-"
    sign = "-" if c < 0 else ""
    c = abs(int(c))
    return "%s$%s.%02d" % (sign, f"{c // 100:,}", c % 100)


def cents(text) -> int | None:
    """"$1,500", "1500.5", "1,500.00" -> cents; None if it is not a plain amount."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return round(float(text) * 100)
    s = str(text or "").strip().replace(",", "").replace("$", "").strip()
    if not re.fullmatch(r"\d+(\.\d{1,2})?", s):
        return None
    return round(float(s) * 100)


@dataclass
class Part:
    label: str
    amount_cents: int
    due_rule: str
    position: int


def split(total_cents: int, how: str = "50/50") -> list[Part]:
    """'50/50', '30/70', or 'full' - the first part before the job, the rest after."""
    if how in ("full", "100", "100/0"):
        return [Part("Full payment", total_cents, "after_completion", 0)]
    m = re.fullmatch(r"\s*(\d{1,2})\s*/\s*(\d{1,2})\s*", how or "")
    first = int(m.group(1)) if m and int(m.group(1)) + int(m.group(2)) == 100 else 50
    dep = total_cents * first // 100
    return [Part("Deposit %d%%" % first, dep, "before_start", 0),
            Part("Balance %d%%" % (100 - first), total_cents - dep, "after_completion", 1)]


def due_date(rule: str, on: date | None, *, scheduled_start: datetime | None,
             status: str | None, finished_at: datetime | None,
             deposit_days_before: int) -> date | None:
    if rule == "on_date":
        return on
    if rule == "before_start":
        start = dc.aware(scheduled_start)
        return (dc.local(start).date() - timedelta(days=deposit_days_before)) if start else None
    if rule == "after_completion":
        done = dc.aware(finished_at)
        if done:
            return dc.local(done).date()
        return None if status not in AFTER_REPAIR else (on or None)
    return None


def status(state: str, due: date | None, today: date, grace_days: int) -> str:
    if state in ("paid", "waived"):
        return state
    if due is None or today < due:
        return "upcoming"
    return "overdue" if (today - due).days > grace_days else "due"


def agreement_state(current: str, *, agreed: bool, milestones: list[str]) -> str:
    if current == "cancelled":
        return "cancelled"
    if milestones and all(s in ("paid", "waived") for s in milestones):
        return "paid"
    if any(s == "paid" for s in milestones):
        return "collecting"
    return "agreed" if agreed else "quoted"


def reminder_due(*, st: str, sent: int, last: datetime | None, now: datetime,
                 every_days: int, max_reminders: int) -> bool:
    if st != "overdue" or sent >= max_reminders:
        return False
    return last is None or now - dc.aware(last) >= timedelta(days=every_days)

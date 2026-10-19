"""The auto-reply's fixed rules (2026-10-19, the owner's answers when the design was grilled).
Everything a person may change at run time is in dispatch_settings (Dispatch → Settings)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from ..dispatch.config import TZ, aware, local

WAIT_MINUTES = 5                  # Q3: five minutes with nobody answering, around the clock
QUIET_SECONDS = 60                # ...and the customer's LAST message at least this old
MAX_AGE_HOURS = 24                # never answer something that has waited longer than this
PER_CUSTOMER_HOURS = 24           # Q11d: never a second text to one number within this
POLL_SECONDS = 60
CONVERSATION_PAGES = 4            # the inbox list, newest first: 4 x 25 is hours of traffic
HISTORY_LIMIT = 50                # messages of ONE conversation the reply reads
COMMS_DAYS = 60                   # Quo / CRM-line / Zuper calls read for context
MIN_CALL_SECONDS = 0              # Q27: EVERY missed call, any length, forwarded ones too

# The company's System Notification Number: Zuper sends `/customers/{uid}/send` from it. Checked
# before every send (Q12) — if anyone changes it in Zuper, nothing is sent.
SENDER = "19544207373"
SENDER_DISPLAY = "(954) 420-7373"

# Q29: while "test customers only" is on (the default), only these Zuper customers are answered.
# "Santiago Villahermosa (TEST)" — its number in Zuper is the office Quo line, 954-914-7244.
TEST_CUSTOMERS = frozenset({"2dbd7187-a6e8-4452-8600-c3d029599d75"})

# Office hours (Q17): Monday-Saturday 8 AM - 6 PM New York; Sunday and these holidays are
# after hours.
OPEN, CLOSE = time(8, 0), time(18, 0)
BUSINESS_DAYS = (0, 1, 2, 3, 4, 5)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def holidays(year: int) -> set[date]:
    """The major holidays the office is closed: New Year's Day, Memorial Day, Independence Day,
    Labor Day, Thanksgiving, Christmas."""
    return {date(year, 1, 1), _last_weekday(year, 5, 0), date(year, 7, 4),
            _nth_weekday(year, 9, 0, 1), _nth_weekday(year, 11, 3, 4), date(year, 12, 25)}


def office_open(when: datetime) -> bool:
    t = local(aware(when))
    return (t.weekday() in BUSINESS_DAYS and t.date() not in holidays(t.year)
            and OPEN <= t.time() < CLOSE)


__all__ = ["TZ", "aware", "holidays", "local", "office_open"]

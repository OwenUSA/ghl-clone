"""What the reminders watch, when they go out, and what they say (2026-10-08, the user's answers).

Column names are Zuper's, matched as a PREFIX: Zuper cuts long names ("Inspection: Day-Before
Cal", "Inspection: Same-Day Confi"), and a column renamed with a longer tail still matches.
A column not listed here never texts, whatever time the job has.
"""
from __future__ import annotations

import os
from datetime import time
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")

# board -> the columns that mean "the visit is confirmed". AHS - Repair & Review is left out on
# purpose (the user, 2026-10-08): add it here when the office wants repair reminders.
COLUMNS: dict[str, tuple[str, ...]] = {
    "Retail": ("Scheduled",),
    "AHS - Inspection": ("Scheduled", "Inspection: Day-Before", "Inspection: Same-Day"),
}
# Boards that hold only the owner's TEST jobs (the test customer): any open job with a time
# counts, whatever its column, so a reminder can be tried end to end without touching a live
# board (2026-10-09).
TEST_BOARDS: tuple[str, ...] = ("AHS - TEST",)


def boards() -> list[str]:
    return [*COLUMNS, *TEST_BOARDS]

DAY_BEFORE = "day_before"
FOUR_HOUR = "four_hour"
KINDS = (DAY_BEFORE, FOUR_HOUR)

# The day-before text: from 10:00 until 20:00 on the day before the visit.
DAY_BEFORE_FROM = time(10, 0)
# No text before 08:00 or from 20:00 (Florida's telephone solicitation hours, New York time).
QUIET_UNTIL = time(8, 0)
QUIET_FROM = time(20, 0)
# The 4-hour text: due 4 h before; a late pass still sends while the visit is 2 h away.
FOUR_HOUR_HOURS = 4
FOUR_HOUR_LATEST_HOURS = 2

# The Dispatch copy must be this fresh, or nothing is sent (a visit cancelled in Zuper an hour
# ago must not be reminded from an old copy).
STALE_MINUTES = 10
# A pass that finds more than this many texts to send stops before sending any: a burst means
# something is wrong (a board renamed, a bulk reschedule), and a person should look first.
MAX_PER_PASS = 40
# How often the pass runs (it is cheap; the Dispatch copy refreshes every 150 s).
EVERY_SECONDS = 60

CONFIRM = "TURN ON"
MODES = ("off", "test", "on")

# Where a customer asks to reschedule (the office line, 2026-10-08).
RESCHEDULE_NUMBER = "(954) 914-7244"

# The default wording. {first_name}, {day} ("Friday, October 9"), {window} ("from 2 PM to
# 4 PM", or "at 2 PM" when the visit has no end time), {time} (the start alone). Editable in
# Settings → Automations; a wording must keep {window} or {time}.
DEFAULT_TEMPLATES: dict[str, dict[str, str]] = {
    DAY_BEFORE: {
        "en": "Hi {first_name}, this is Dream Team Roofing. Reminder: our visit is tomorrow, "
              "{day}, {window}. Text or call " + RESCHEDULE_NUMBER + " if you need to "
              "reschedule. Reply STOP to opt out.",
        "es": "Hola {first_name}, le saluda Dream Team Roofing. Le recordamos nuestra visita de "
              "mañana, {day}, {window}. Si necesita cambiarla, escriba o llame al "
              + RESCHEDULE_NUMBER + ". Responda STOP para no recibir más mensajes.",
    },
    FOUR_HOUR: {
        "en": "Hi {first_name}, this is Dream Team Roofing. Reminder: our visit is today "
              "{window}. Text or call " + RESCHEDULE_NUMBER + " if you need to reschedule. "
              "Reply STOP to opt out.",
        "es": "Hola {first_name}, le saluda Dream Team Roofing. Le recordamos nuestra visita de "
              "hoy {window}. Si necesita cambiarla, escriba o llame al " + RESCHEDULE_NUMBER +
              ". Responda STOP para no recibir más mensajes.",
    },
}
LANGUAGES = ("en", "es")


def enabled() -> bool:
    """The deployment's gate. Unset = nothing is ever sent, whatever Settings says."""
    return os.getenv("ZUPER_REMINDERS_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def column_counts(board: str | None, status: str | None) -> bool:
    if board in TEST_BOARDS:
        return True
    prefixes = COLUMNS.get(board or "")
    if not prefixes or not status:
        return False
    s = status.strip().lower()
    return any(s.startswith(p.lower()) for p in prefixes)

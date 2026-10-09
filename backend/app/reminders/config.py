"""What the reminders watch, when they go out, and what they say (2026-10-08, the user's answers).

Column names are Zuper's, matched as a PREFIX: Zuper cuts long names ("Inspection: Day-Before
Cal", "Inspection: Same-Day Confi"), and a column renamed with a longer tail still matches.
A column not listed here never texts, whatever time the job has.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
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
REMINDER_KINDS = (DAY_BEFORE, FOUR_HOUR)

# Texts sent once per job when it MOVES into a column (reminders/stage_texts.py). Each was lifted
# by Owen one at a time; each has its own off | test | on (`reminder_settings.<kind>_mode`).
AHS_SUBMITTED = "ahs_submitted"     # 2026-10-09: the inspection report went to AHS
AHS_APPROVED = "ahs_approved"       # 2026-10-09: AHS authorized the repair


@dataclass(frozen=True)
class StageText:
    kind: str
    title: str
    board: str
    columns: tuple[str, ...]          # moving into any of these (prefix match) is the news
    later: tuple[str, ...]            # held overnight: still true at 8 AM in these columns...
    later_boards: tuple[str, ...]     # ...or on these boards


STAGE_TEXTS: dict[str, StageText] = {t.kind: t for t in (
    # The office sometimes skips "Submit To AHS…" and goes straight to "Awaiting AHS Decision":
    # it is the same news, one text.
    StageText(AHS_SUBMITTED, "Inspection report submitted to AHS", "AHS - Inspection",
              ("Submit To AHS", "Awaiting AHS Decision"), ("AHS Approved", "Proposal Made"),
              ("AHS - Repair & Review",)),
    StageText(AHS_APPROVED, "AHS authorized the repair", "AHS - Inspection",
              ("AHS Approved",), ("Proposal Made",), ("AHS - Repair & Review",)),
)}
STAGE_KINDS = tuple(STAGE_TEXTS)
# The column each job was in is remembered between passes; after a gap this long nothing is
# known about the moves in between, so the next pass only records where every job is.
STAGE_WATCH_STALE_MINUTES = 30
# A text held overnight that has still not gone after this long is cancelled, never sent late.
HELD_MAX_HOURS = 14

KINDS = (*REMINDER_KINDS, *STAGE_KINDS)

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
    AHS_SUBMITTED: {
        "en": "Hi {first_name}, this is Dream Team Roofing. We submitted your inspection report "
              "to American Home Shield and are waiting for their approval. We'll contact you as "
              "soon as we hear back. Questions? Text or call " + RESCHEDULE_NUMBER + ". Reply "
              "STOP to opt out.",
        "es": "Hola {first_name}, le saluda Dream Team Roofing. Enviamos su informe de inspección "
              "a American Home Shield y estamos esperando su aprobación. Le avisaremos en cuanto "
              "tengamos respuesta. ¿Preguntas? Escriba o llame al " + RESCHEDULE_NUMBER + ". "
              "Responda STOP para no recibir más mensajes.",
    },
    AHS_APPROVED: {
        "en": "Hi {first_name}, this is Dream Team Roofing. Good news: American Home Shield has "
              "authorized the repair on your roof. Someone from our team will reach out shortly "
              "to go over the details and schedule it. Questions? Text or call "
              + RESCHEDULE_NUMBER + ". Reply STOP to opt out.",
        "es": "Hola {first_name}, le saluda Dream Team Roofing. Buenas noticias: American Home "
              "Shield autorizó la reparación de su techo. Alguien de nuestro equipo se comunicará "
              "con usted en breve para revisar los detalles y programarla. ¿Preguntas? Escriba o "
              "llame al " + RESCHEDULE_NUMBER + ". Responda STOP para no recibir más mensajes.",
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


def _starts(status: str | None, prefixes: tuple[str, ...]) -> bool:
    s = (status or "").strip().lower()
    return bool(s) and any(s.startswith(p.lower()) for p in prefixes)


def in_columns(kind: str, board: str | None, status: str | None) -> bool:
    t = STAGE_TEXTS[kind]
    return board == t.board and _starts(status, t.columns)


def still_true(kind: str, board: str | None, status: str | None, is_open: bool) -> bool:
    """At 8 AM, does a text held overnight still say something true?"""
    t = STAGE_TEXTS[kind]
    if not is_open:
        return False
    if board in t.later_boards:
        return True
    return board == t.board and _starts(status, t.columns + t.later)

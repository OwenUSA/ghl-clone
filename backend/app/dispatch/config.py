"""What the Dispatch page watches and the owner's numbers (2026-09-30, the grilling session).

Stage names are Zuper's, exactly as the boards read on 2026-09-30. A stage the team adds later
falls in no group, so it produces no item until it is named here — the page's heartbeat lists
the stages it saw that no group knows, so that is visible rather than silent.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")

INSPECTION_BOARD = "AHS - Inspection"
REPAIR_BOARD = "AHS - Repair & Review"
RETAIL_BOARD = "Retail"
BOARDS = (INSPECTION_BOARD, REPAIR_BOARD, RETAIL_BOARD)
# Boards the team uses that are pipelines nonetheless (copies of Retail). Read, never flagged
# as "off a pipeline".
OTHER_PIPELINES = ("Miami Retail Repair", "Miami Retail Roof Replacement", "Miami Gutters",
                   "Sarasota Repairs", "Sarasota Roof Replacement", "Sarasota Gutters")
IGNORED_BOARDS = ("Inspection TEST",)

CLOSED = {"Paid", "Cancelled", "Estimate Declined", "Review Received"}

# A job nobody has spoken to yet.
FIRST_CONTACT = {
    INSPECTION_BOARD: {"Work Order Received", "Welcome Call!", "Not Answering -Keep Call!"},
    RETAIL_BOARD: {"New Lead"},
}
# A stage whose next step is booking a visit — and what to say about it.
BOOK = {
    "Work Order Received": "Call the customer and book the inspection",
    "Welcome Call!": "Make the welcome call and book the inspection",
    "Not Answering -Keep Call!": "Keep calling; book the inspection when they answer",
    "Not Answering- Keep Call!": "Keep calling; book the repair when they answer",
    "Not Answering": "Keep calling; book the repair when they answer",
    "New Lead": "Call the lead and book the inspection",
    "AHS Approved": "AHS approved it: call the customer and book the repair",
    "Repair Scheduling Call": "Call the customer and book the repair",
    "Reschedule Required": "Call the customer and book a new date",
    "Call Back": "Call back and book the return visit",
}
# Stages that only make sense with a visit on the calendar.
NEEDS_DATE = ("Scheduled", "Day-Before Call", "Inspection: Day-Before", "Inspection: Same-Day",
              "Antonio On the Way", "On the Way to Repair", "Inspection In Progress",
              "Repair In Process", "Arrived on Site")
# Waiting on the customer or on paperwork: no visit, no first contact — "gone quiet" applies.
FOLLOW_UP = {"Contact Attempted", "Proposal Made", "Estimate Sent", "Follow Up",
             "Waiting for Customer", "Invoice", "Collect Balance", "Review Requested",
             "Approval Requested"}
# Old names Zuper still shows on jobs not moved since a rename (2026-09-25): a visit stage.
OLD_VISIT_NAMES = {"Inspection", "Inspecting"}
# The customer is waiting on AHS, not on us.
WAITING_ON_AHS = {"Submit To AHS For Approval", "Awaiting AHS Decision",
                  "Invoice Submitted to AHS", "Awaiting AHS Payment"}
# A visit on these is an INSPECTION; on anything else it is a repair.
INSPECTION_STAGES_RETAIL = {"New Lead", "Contact Attempted", "Inspection / Estimate", "Scheduled"}
# Entering one of these means "the inspection / the repair is done" even without pictures.
AFTER_INSPECTION_STAGES = {"Inspection Completed"}
AFTER_REPAIR_STAGES = {"Repair Complete", "Call Satisfaction Check"}

# --- the owner's time limits (Q6), in business time --------------------------------------
BUSINESS_DAYS = (0, 1, 2, 3, 4, 5)          # Monday .. Saturday
OPEN, CLOSE = time(8, 0), time(18, 0)
SLA_MINUTES = {
    "new_ahs": 120,
    "new_retail": 60,
    "after_inspection": 120,
    "missed": 30,
    "book": 600,                 # one business day
    "talked_not_updated": 120,
}
STALE_DAYS = 7
PHOTO_QUIET_MINUTES = 20     # Q22: no new picture for this long = the technician has left
NO_PHOTOS_AFTER_MINUTES = 60  # ...and none at all this long after the scheduled end
TALK_SECONDS = 30            # an answered call this long is a conversation
COMMS_DAYS = 21              # how far back calls and texts are read
DONE_EVENT_DAYS = 3          # how long an "inspection / repair done" stays actionable


def enabled() -> bool:
    """Off unless the deployment says so: the reader asks Zuper for nothing while off."""
    return os.getenv("DISPATCH_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def poll_seconds() -> int:
    try:
        return max(60, int(os.getenv("DISPATCH_POLL_SECONDS", "150")))
    except ValueError:
        return 150


def aware(dt: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; everything stored here is UTC."""
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def local(dt: datetime) -> datetime:
    return aware(dt).astimezone(TZ)


def is_business_time(dt: datetime) -> bool:
    t = local(dt)
    return t.weekday() in BUSINESS_DAYS and OPEN <= t.time() < CLOSE


def _next_open(t: datetime) -> datetime:
    """The first business moment at or after `t` (local)."""
    while True:
        if t.weekday() in BUSINESS_DAYS:
            if t.time() < OPEN:
                return t.replace(hour=OPEN.hour, minute=OPEN.minute, second=0, microsecond=0)
            if t.time() < CLOSE:
                return t
        t = (t + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def add_business_minutes(start: datetime, minutes: int) -> datetime:
    """`start` plus `minutes` of business time (Mon-Sat 8-6, New York)."""
    t = _next_open(local(start))
    left = timedelta(minutes=minutes)
    while True:
        close = t.replace(hour=CLOSE.hour, minute=CLOSE.minute, second=0, microsecond=0)
        if t + left <= close:
            return t + left
        left -= close - t
        t = _next_open(close)


def end_of_next_business_day(start: datetime) -> datetime:
    """Q6 "repair finished: next business day" — by that day's close."""
    t = _next_open(local(start).replace(hour=CLOSE.hour, minute=CLOSE.minute) +
                   timedelta(minutes=1))
    return t.replace(hour=CLOSE.hour, minute=CLOSE.minute, second=0, microsecond=0)

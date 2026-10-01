"""The Dispatch rules (2026-09-30): Zuper's jobs + every call and text -> the page's items and
the bells. Pure functions, so every rule is pinned here with plain objects and a fixed clock.

What is pinned, by behaviour:
  * a new job nobody called is an item, and stops being one the moment we call or text;
  * pictures that stop for 20 minutes are "the visit is done" — one bell, and an item until
    the customer is called; pictures still arriving are not;
  * a conversation after Zuper's last change is "Zuper not updated", with the call's summary;
  * a missed call is an item until we call back; an unknown number is listed but never urgent
    and never rings;
  * business hours: limits run Mon-Sat 8-6 New York, so a job arriving at night is due the
    next morning, not at 2 a.m.
"""
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.dispatch import config as c
from app.dispatch import rules
from app.dispatch.comms import Comm

# Wednesday 2026-09-30, 2:00 PM New York.
NOW = datetime(2026, 9, 30, 18, 0, tzinfo=UTC)
PHONE = "9415550100"


@dataclass
class J:
    job_uid: str = "job-1"
    job_number: str = "700"
    board: str = c.INSPECTION_BOARD
    status: str = "Work Order Received"
    status_since: datetime | None = None
    customer_name: str = "Jane Doe"
    phones: list = field(default_factory=lambda: [PHONE])
    is_open: bool = True
    zuper_created_at: datetime | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    assigned: list = field(default_factory=list)
    technician: str | None = None
    last_note_at: datetime | None = None
    photo_day: str | None = None
    photos_today: int = 0
    last_photo_at: datetime | None = None
    last_photo_by: str | None = None


def call(at, *, out=False, talked=False, missed=False, seconds=0, summary=""):
    return Comm(at=at, kind="call", out=out, talked=talked, missed=missed, seconds=seconds,
                summary=summary, source="Quo")


def text(at, *, out=False, body="hi"):
    return Comm(at=at, kind="text", out=out, talked=False, missed=False, seconds=0,
                summary=body, source="Quo")


def one(items, kind):
    return next(i for i in items if i.kind == kind)


def kinds(items):
    return sorted(i.kind for i in items)


# ---- business time -------------------------------------------------------------------

def test_business_minutes_skip_the_night_and_sunday():
    sat_evening = datetime(2026, 10, 3, 21, 30, tzinfo=UTC)          # Sat 5:30 PM NY
    due = c.add_business_minutes(sat_evening, 120)
    # 30 minutes on Saturday, Sunday skipped, 90 on Monday from 8 AM.
    assert c.local(due).strftime("%a %H:%M") == "Mon 09:30"
    night = datetime(2026, 9, 30, 4, 0, tzinfo=UTC)                  # Wed 12:00 AM NY
    assert c.local(c.add_business_minutes(night, 60)).strftime("%a %H:%M") == "Wed 09:00"


def test_next_business_day_close():
    fri = datetime(2026, 10, 2, 15, 0, tzinfo=UTC)                   # Fri 11 AM NY
    assert c.local(c.end_of_next_business_day(fri)).strftime("%a %H:%M") == "Sat 18:00"
    sat = datetime(2026, 10, 3, 15, 0, tzinfo=UTC)
    assert c.local(c.end_of_next_business_day(sat)).strftime("%a %H:%M") == "Mon 18:00"


# ---- a new job ---------------------------------------------------------------------------

def test_a_new_job_nobody_called_is_an_item_and_overdue_after_two_business_hours():
    job = J(zuper_created_at=NOW - timedelta(hours=3), status_since=NOW - timedelta(hours=3))
    items, _ = rules.evaluate([job], {}, NOW)
    new = [i for i in items if i.kind == "new_not_called"]
    assert len(new) == 1 and new[0].queue == "new"
    assert new[0].urgent(NOW)
    assert "book the inspection" in new[0].todo
    fresh = J(zuper_created_at=NOW - timedelta(minutes=30), status_since=NOW)
    items, _ = rules.evaluate([fresh], {}, NOW)
    assert not one(items, "new_not_called").urgent(NOW)


def test_calling_or_texting_the_customer_clears_the_new_job_item():
    created = NOW - timedelta(hours=3)
    job = J(zuper_created_at=created, status_since=created)
    for comm in (call(created + timedelta(minutes=5), out=True, seconds=0),
                 text(created + timedelta(minutes=5), out=True)):
        items, _ = rules.evaluate([job], {PHONE: [comm]}, NOW)
        assert "new_not_called" not in kinds(items)
    # Their call that nobody answered is not us reaching them.
    items, _ = rules.evaluate([job], {PHONE: [call(created + timedelta(minutes=5),
                                                    missed=True)]}, NOW)
    assert "new_not_called" in kinds(items)
    assert "missed_call" in kinds(items)


def test_a_retail_lead_is_due_in_one_business_hour():
    job = J(board=c.RETAIL_BOARD, status="New Lead",
            zuper_created_at=NOW - timedelta(minutes=70), status_since=NOW)
    item = one(rules.evaluate([job], {}, NOW)[0], "new_not_called")
    assert item.urgent(NOW)


# ---- the visit is done (pictures) ----------------------------------------------------------

def visit(**kw):
    today = c.local(NOW).date().isoformat()
    base = {"status": "Inspection In Progress", "status_since": NOW - timedelta(hours=4),
            "zuper_created_at": NOW - timedelta(days=3), "photo_day": today,
            "photos_today": 14, "last_photo_by": "Antonio Brown",
            "scheduled_start": NOW - timedelta(hours=3),
            "scheduled_end": NOW - timedelta(hours=1)}
    base.update(kw)
    return J(**base)


def test_pictures_still_arriving_are_not_done():
    job = visit(last_photo_at=NOW - timedelta(minutes=5))
    items, events = rules.evaluate([job], {}, NOW)
    assert not [e for e in events if e.kind == "inspection_done"]
    assert "after_inspection" not in kinds(items)


def test_pictures_stopped_for_twenty_minutes_ring_once_and_ask_for_the_call():
    job = visit(last_photo_at=NOW - timedelta(minutes=25))
    items, events = rules.evaluate([job], {}, NOW)
    done = [e for e in events if e.kind == "inspection_done"]
    assert len(done) == 1 and "Antonio Brown posted 14 pictures" in done[0].body
    item = one(items, "after_inspection")
    assert "submitting it to AHS right away" in item.todo
    assert "Inspection Completed" in item.todo         # the stage still says "in progress"
    # A second pass finds the SAME event key: alerts.fire rings it once.
    assert rules.evaluate([job], {}, NOW)[1][0].key == done[0].key


def test_calling_the_customer_after_the_visit_clears_it():
    job = visit(last_photo_at=NOW - timedelta(minutes=40))
    talked = call(NOW - timedelta(minutes=10), out=True, talked=True, seconds=95)
    items, _ = rules.evaluate([job], {PHONE: [talked]}, NOW)
    assert "after_inspection" not in kinds(items)


def test_a_repair_finished_asks_for_the_satisfaction_call_next_business_day():
    job = visit(board=c.REPAIR_BOARD, status="Repair In Process",
                last_photo_at=NOW - timedelta(minutes=30))
    items, events = rules.evaluate([job], {}, NOW)
    assert [e.kind for e in events if e.kind.endswith("_done")] == ["repair_done"]
    item = one(items, "after_repair")
    assert "Google review" in item.todo
    assert c.local(item.due_at).strftime("%a %H:%M") == "Thu 18:00"


def test_no_pictures_an_hour_after_the_visit_ended_is_flagged():
    job = J(status="Scheduled", status_since=NOW - timedelta(days=1),
            zuper_created_at=NOW - timedelta(days=2), assigned=["Antonio Brown"],
            scheduled_start=NOW - timedelta(hours=3), scheduled_end=NOW - timedelta(minutes=70))
    items, events = rules.evaluate([job], {}, NOW)
    assert "no_photos" in kinds(items)
    assert [e.kind for e in events] == ["no_photos"]
    assert "Antonio Brown" in one(items, "no_photos").todo


# ---- Zuper not updated after a conversation ------------------------------------------------

def test_a_conversation_after_zupers_last_change_is_flagged_with_the_summary():
    job = J(status="AHS Approved", status_since=NOW - timedelta(days=2),
            zuper_created_at=NOW - timedelta(days=9),
            scheduled_start=NOW - timedelta(days=5), scheduled_end=NOW - timedelta(days=5))
    talk = call(NOW - timedelta(hours=2), out=True, talked=True, seconds=87,
                summary="Customer accepted $175 with a $75 discount.")
    items, _ = rules.evaluate([job], {PHONE: [talk]}, NOW)
    flag = one(items, "talked_not_updated")
    assert flag.queue == "zuper"
    assert flag.evidence["summary"] == "Customer accepted $175 with a $75 discount."
    # A note written in Zuper after the call is an update: the flag goes.
    job.last_note_at = NOW - timedelta(hours=1)
    assert "talked_not_updated" not in kinds(rules.evaluate([job], {PHONE: [talk]}, NOW)[0])


def test_a_call_in_the_last_half_hour_gets_time_to_be_written_up():
    job = J(status="AHS Approved", status_since=NOW - timedelta(days=2),
            zuper_created_at=NOW - timedelta(days=9))
    talk = call(NOW - timedelta(minutes=10), out=True, talked=True, seconds=87)
    assert "talked_not_updated" not in kinds(rules.evaluate([job], {PHONE: [talk]}, NOW)[0])


# ---- book a visit / stages that need a date ----------------------------------------------

def test_ahs_approved_with_nothing_on_the_calendar_must_be_booked():
    job = J(status="AHS Approved", status_since=NOW - timedelta(days=2),
            zuper_created_at=NOW - timedelta(days=9),
            scheduled_start=NOW - timedelta(days=5), scheduled_end=NOW - timedelta(days=5))
    item = one(rules.evaluate([job], {}, NOW)[0], "book_visit")
    assert item.queue == "book" and item.urgent(NOW)
    assert "book the repair" in item.todo
    job.scheduled_start = NOW + timedelta(days=2)
    assert "book_visit" not in kinds(rules.evaluate([job], {}, NOW)[0])


def test_a_scheduled_stage_with_no_future_date_is_flagged():
    job = J(board=c.REPAIR_BOARD, status="Day-Before Call", status_since=NOW - timedelta(days=1),
            zuper_created_at=NOW - timedelta(days=9))
    assert "needs_date" in kinds(rules.evaluate([job], {}, NOW)[0])


# ---- missed calls and texts --------------------------------------------------------------

def test_a_missed_customer_call_rings_and_clears_when_we_call_back():
    job = J(status="AHS Approved", status_since=NOW - timedelta(hours=1),
            zuper_created_at=NOW - timedelta(days=9), scheduled_start=NOW + timedelta(days=1))
    missed = call(NOW - timedelta(hours=1), missed=True)
    items, events = rules.evaluate([job], {PHONE: [missed]}, NOW)
    item = one(items, "missed_call")
    assert item.job_number == "700" and item.urgent(NOW)
    assert [e.kind for e in events if e.kind == "missed_call"] == ["missed_call"]
    back = call(NOW - timedelta(minutes=20), out=True, seconds=0)
    assert "missed_call" not in kinds(rules.evaluate([job], {PHONE: [missed, back]}, NOW)[0])


def test_an_unknown_number_is_listed_but_never_urgent_and_never_rings():
    stranger = "3055550199"
    items, events = rules.evaluate([], {stranger: [call(NOW - timedelta(hours=5),
                                                         missed=True)]}, NOW)
    item = one(items, "missed_call")
    assert "maybe a new lead" in item.why and not item.urgent(NOW)
    assert events == []


def test_an_unanswered_text_shows_what_they_wrote():
    job = J(status="Repair Complete", status_since=NOW - timedelta(days=1),
            zuper_created_at=NOW - timedelta(days=9))
    msg = text(NOW - timedelta(hours=3), body="You forgot to pick up the tarp")
    item = one(rules.evaluate([job], {PHONE: [msg]}, NOW)[0], "missed_text")
    assert item.evidence["text"] == "You forgot to pick up the tarp"


# ---- quiet, off-board, closed ------------------------------------------------------------

def test_a_job_quiet_for_a_week_is_listed_but_never_urgent():
    job = J(board=c.RETAIL_BOARD, status="Follow Up", status_since=NOW - timedelta(days=9),
            zuper_created_at=NOW - timedelta(days=20))
    item = one(rules.evaluate([job], {}, NOW)[0], "stale")
    assert not item.urgent(NOW)


def test_a_job_off_the_pipelines_is_flagged_and_a_closed_job_never_is():
    off = J(board="Leak repair", status=None, zuper_created_at=NOW - timedelta(days=2))
    assert "off_board" in kinds(rules.evaluate([off], {}, NOW)[0])
    closed = J(status="Paid", is_open=False, zuper_created_at=NOW - timedelta(days=30))
    assert rules.evaluate([closed], {}, NOW)[0] == []


def test_an_item_key_names_its_evidence_so_new_evidence_is_a_new_item():
    job = J(status="AHS Approved", status_since=NOW - timedelta(days=2),
            zuper_created_at=NOW - timedelta(days=9))
    first = call(NOW - timedelta(hours=3), out=True, talked=True, seconds=60)
    later = call(NOW - timedelta(hours=1), out=True, talked=True, seconds=60)
    k1 = [i.key for i in rules.evaluate([job], {PHONE: [first]}, NOW)[0]
          if i.kind == "talked_not_updated"]
    k2 = [i.key for i in rules.evaluate([job], {PHONE: [first, later]}, NOW)[0]
          if i.kind == "talked_not_updated"]
    assert k1 and k2 and k1 != k2


def test_a_repair_already_at_satisfaction_check_is_told_the_next_step_not_the_same_one():
    job = J(board=c.REPAIR_BOARD, status="Call Satisfaction Check",
            status_since=NOW - timedelta(hours=13), zuper_created_at=NOW - timedelta(days=9))
    item = one(rules.evaluate([job], {}, NOW)[0], "after_repair")
    assert "Review Requested" in item.todo
    assert "to Call Satisfaction Check" not in item.todo


def test_an_acknowledgement_needs_no_reply_and_an_old_miss_is_not_urgent():
    job = J(status="Follow Up", board=c.RETAIL_BOARD, status_since=NOW - timedelta(days=1),
            zuper_created_at=NOW - timedelta(days=9))
    for ack in ("Okay", "Thank you!", "ok thanks"):
        assert "missed_text" not in kinds(rules.evaluate(
            [job], {PHONE: [text(NOW - timedelta(hours=3), body=ack)]}, NOW)[0])
    old = call(NOW - timedelta(days=12), missed=True)
    item = one(rules.evaluate([job], {PHONE: [old]}, NOW)[0], "missed_call")
    assert not item.urgent(NOW)


def test_a_deadline_is_fixed_by_the_evidence_not_by_when_the_pass_ran():
    job = J(board=c.REPAIR_BOARD, status="Day-Before Call", status_since=NOW - timedelta(days=1),
            zuper_created_at=NOW - timedelta(days=9))
    first = one(rules.evaluate([job], {}, NOW)[0], "needs_date").due_at
    later = one(rules.evaluate([job], {}, NOW + timedelta(hours=2))[0], "needs_date").due_at
    assert first == later == NOW - timedelta(days=1)

"""Appointment reminders from Zuper (2026-10-08): the PURE parts — which reminders are due, and
in which language. No database, no clock: `now` is passed in.

Times are written in New York time (`ny(...)`) because that is how the owner reads them.
"""
from datetime import UTC, datetime

from app.reminders import config as c
from app.reminders import language, rules


def ny(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=c.TZ).astimezone(UTC)


def due(now, start, *, board="Retail", status="Scheduled", is_open=True, uid="j1"):
    return {d.kind for d in rules.due(job_uid=uid, board=board, status=status, is_open=is_open,
                                      scheduled_start=start, now=now)}


VISIT = ny(2026, 10, 9, 14, 0)          # Friday 2 PM


def test_day_before_goes_from_10_am_until_8_pm_the_day_before():
    assert due(ny(2026, 10, 8, 9, 59), VISIT) == set()
    assert due(ny(2026, 10, 8, 10, 0), VISIT) == {"day_before"}
    assert due(ny(2026, 10, 8, 19, 59), VISIT) == {"day_before"}
    assert due(ny(2026, 10, 8, 20, 0), VISIT) == set()


def test_four_hour_is_due_4h_before_and_late_passes_stop_2h_before():
    assert due(ny(2026, 10, 9, 9, 59), VISIT) == set()
    assert due(ny(2026, 10, 9, 10, 0), VISIT) == {"four_hour"}
    assert due(ny(2026, 10, 9, 11, 59), VISIT) == {"four_hour"}
    assert due(ny(2026, 10, 9, 12, 0), VISIT) == set()


def test_a_morning_visit_gets_no_four_hour_text_it_would_land_before_8_am():
    morning = ny(2026, 10, 9, 7, 30)
    assert rules.windows(morning)["four_hour"] is None
    for h in range(0, 8):
        assert due(ny(2026, 10, 9, h, 0), morning) == set()
    assert due(ny(2026, 10, 8, 10, 0), morning) == {"day_before"}
    noon = ny(2026, 10, 9, 12, 0)
    assert due(ny(2026, 10, 9, 8, 0), noon) == {"four_hour"}


def test_nothing_in_the_quiet_hours_even_inside_a_window():
    late = ny(2026, 10, 9, 23, 30)           # 4 h before is 7:30 PM, 2 h before is 9:30 PM
    assert due(ny(2026, 10, 9, 19, 45), late) == {"four_hour"}
    assert due(ny(2026, 10, 9, 20, 15), late) == set()


def test_only_the_columns_that_mean_a_confirmed_visit_count():
    at = ny(2026, 10, 8, 11, 0)
    assert due(at, VISIT, board="Retail", status="Scheduled") == {"day_before"}
    # Zuper cuts long names: prefixes match.
    assert due(at, VISIT, board="AHS - Inspection", status="Inspection: Day-Before Cal")
    assert due(at, VISIT, board="AHS - Inspection", status="Inspection: Same-Day Confi")
    assert due(at, VISIT, board="AHS - Inspection", status="Scheduled")
    for board, status in (("AHS - Inspection", "Welcome Call!"),
                          ("AHS - Inspection", "AHS Approved"),
                          ("AHS - Inspection", "Reschedule Required"),
                          ("AHS - Repair & Review", "Repair Scheduling Call"),
                          ("AHS - Repair & Review", "Day-Before Call"),
                          ("Retail", "New Lead"), ("Retail", "Reschedule Required"),
                          ("Miami Gutters", "Scheduled"), (None, "Scheduled")):
        assert due(at, VISIT, board=board, status=status) == set(), (board, status)


def test_a_closed_job_a_job_with_no_time_and_a_started_visit_get_nothing():
    at = ny(2026, 10, 8, 11, 0)
    assert due(at, VISIT, is_open=False) == set()
    assert due(at, None) == set()
    assert due(ny(2026, 10, 9, 14, 1), VISIT) == set()


def test_a_rescheduled_visit_is_a_new_key_and_moving_it_back_is_the_old_key():
    a = rules.key_for("j1", VISIT, "day_before")
    b = rules.key_for("j1", ny(2026, 10, 10, 14, 0), "day_before")
    assert a != b
    assert rules.key_for("j1", ny(2026, 10, 9, 14, 0), "day_before") == a
    assert rules.key_for("j1", VISIT, "four_hour") != a


def test_daylight_saving_ends_nov_1_and_the_windows_follow_new_york_time():
    # Monday Nov 2, 9 AM EST: the day-before window opens Sunday Nov 1, 10 AM EST (15:00 UTC).
    monday = ny(2026, 11, 2, 9, 0)
    start, _ = rules.windows(monday)["day_before"]
    assert start == datetime(2026, 11, 1, 15, 0, tzinfo=UTC)
    # Sunday Nov 1, 2 PM visit: the window opened Saturday 10 AM EDT (14:00 UTC).
    sunday = ny(2026, 11, 1, 14, 0)
    assert rules.windows(sunday)["day_before"][0] == datetime(2026, 10, 31, 14, 0, tzinfo=UTC)


# --- language -----------------------------------------------------------------------------

def test_spanish_needs_clear_evidence_and_english_is_the_default():
    assert language.detect([], []) == ("en", {"es_texts": 0, "en_texts": 0,
                                              "es_calls": 0, "en_calls": 0})
    one = ["Hola, gracias por la información"]
    assert language.detect(one, [])[0] == "en"            # one text is not enough
    two = [*one, "Sí, mañana está bien para la cita"]
    assert language.detect(two, [])[0] == "es"
    assert language.detect([*two, "Thanks, see you tomorrow at the house",
                            "Yes that works for my roof",
                            "ok thank you for the call"], [])[0] == "en"


def test_a_spanish_call_counts_even_without_texts():
    call = ("Buenos días, le llamo de Dream Team Roofing. Sí, buenas, tengo una gotera en el "
            "techo de la casa, cuando pueden venir? Mañana por la tarde está bien, gracias.")
    assert language.classify(call, call=True) == "es"
    assert language.detect([], [call])[0] == "es"
    english = ("Hi this is Dream Team Roofing calling about your roof inspection, can we come "
               "tomorrow? Yes that works, thank you, we will see you there.")
    assert language.classify(english, call=True) == "en"


def test_short_or_mixed_messages_say_nothing():
    assert language.classify("ok") is None
    assert language.classify("👍") is None
    assert language.classify("la casa") == "es"
    assert language.classify("the roof") == "en"


def test_the_test_board_counts_in_any_column_and_no_live_board_changes():
    at = ny(2026, 10, 8, 11, 0)
    assert due(at, VISIT, board="AHS - TEST", status="Intake") == {"day_before"}
    assert due(at, VISIT, board="AHS - TEST", status="Intake", is_open=False) == set()
    assert due(at, VISIT, board="AHS - Inspection", status="Intake") == set()

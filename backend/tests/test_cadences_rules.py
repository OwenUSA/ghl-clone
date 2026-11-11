"""Follow-ups' pure rules (app/cadences/rules.py): what a call or text means, when a step is due,
the send window, the wording and a sequence's checks."""
from datetime import UTC, datetime, timedelta

from app.cadences import config as c
from app.cadences import rules
from app.cadences.rules import Ev

TUE_11AM = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)          # Tue 11 AM New York


def ev(minutes, *, kind="call", out=True, answered=False, ai=False, final=True, staff="Luis",
       body=""):
    return Ev(uid="u%d" % minutes, at=TUE_11AM + timedelta(minutes=minutes), kind=kind, out=out,
              answered=answered, ai=ai, final=final, source="Zuper", staff=staff, body=body)


def test_what_stops_and_what_is_only_an_attempt():
    m = rules.meaning
    assert m(ev(1, kind="text", out=False), ours=False).what == rules.STOP           # reply
    assert m(ev(1, out=False, answered=True), ours=False).what == rules.STOP        # call back
    missed = m(ev(1, out=False), ours=False)
    assert missed.what == rules.STOP and "call them back" in missed.reason
    assert m(ev(1, answered=True), ours=False).what == rules.STOP                   # reached
    assert m(ev(1, answered=True, ai=True), ours=False).reason == "Anisa (AI) reached them"
    team_text = m(ev(1, kind="text"), ours=False)
    assert (team_text.what, team_text.kind) == (rules.ATTEMPT, "team_text")         # recorded
    assert m(ev(1, kind="text"), ours=True).what == rules.IGNORE                    # our text
    call = m(ev(1), ours=False)
    assert (call.what, call.person_call) == (rules.ATTEMPT, True)
    assert m(ev(1), ours=True).what == rules.IGNORE                                 # our AI call
    assert m(ev(1, out=False, final=False), ours=False).what == rules.IGNORE        # ringing


def test_trigger_is_the_latest_unanswered_person_call_not_yet_resolved():
    assert rules.trigger([ev(-10)], now=TUE_11AM, floor=None, ai_call_times=[]).uid == "u-10"
    # The customer called back after it: nothing to start.
    assert rules.trigger([ev(-10), ev(-5, out=False, answered=True)], now=TUE_11AM, floor=None,
                         ai_call_times=[]) is None
    # A team text after it does not resolve it.
    assert rules.trigger([ev(-10), ev(-5, kind="text")], now=TUE_11AM, floor=None,
                         ai_call_times=[]) is not None
    # Older than the lookback, before the switch-on, an AI's own call, still ringing: never.
    old = -60 * (c.LOOKBACK_HOURS + 1)
    assert rules.trigger([ev(old)], now=TUE_11AM, floor=None, ai_call_times=[]) is None
    assert rules.trigger([ev(-10)], now=TUE_11AM, floor=TUE_11AM - timedelta(minutes=5),
                         ai_call_times=[]) is None
    assert rules.trigger([ev(-10, ai=True)], now=TUE_11AM, floor=None, ai_call_times=[]) is None
    assert rules.trigger([ev(-10)], now=TUE_11AM, floor=None,
                         ai_call_times=[TUE_11AM - timedelta(minutes=12)]) is None
    assert rules.trigger([ev(-10, final=False)], now=TUE_11AM, floor=None,
                         ai_call_times=[]) is None


def test_the_send_window_mon_sat_8_to_8_never_a_holiday():
    assert rules.sending_hours(TUE_11AM)
    tue_9pm = datetime(2026, 10, 21, 1, 0, tzinfo=UTC)
    assert not rules.sending_hours(tue_9pm)
    assert rules.next_open(tue_9pm) == datetime(2026, 10, 21, 12, 0, tzinfo=UTC)   # Wed 8 AM
    sat_9pm = datetime(2026, 10, 25, 1, 0, tzinfo=UTC)
    assert rules.next_open(sat_9pm) == datetime(2026, 10, 26, 12, 0, tzinfo=UTC)   # Mon 8 AM
    thanksgiving = datetime(2026, 11, 26, 15, 0, tzinfo=UTC)
    assert not rules.sending_hours(thanksgiving)
    assert rules.next_open(thanksgiving) == datetime(2026, 11, 27, 13, 0, tzinfo=UTC)


def test_step_times():
    assert rules.due_at({"type": "text", "delay_minutes": 5}, TUE_11AM) == \
        TUE_11AM + timedelta(minutes=5)
    # Next day at 10:00 New York (EDT on Oct 21).
    assert rules.due_at({"type": "call_me", "delay_days": 1, "at": "10:00"}, TUE_11AM) == \
        datetime(2026, 10, 21, 14, 0, tzinfo=UTC)
    # Saturday + 1 day = Sunday: moved to Monday 8 AM.
    sat = datetime(2026, 10, 24, 15, 0, tzinfo=UTC)
    assert rules.due_at({"type": "ai_call", "delay_days": 1, "at": "14:00"}, sat) == \
        datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def test_wording_with_and_without_a_name():
    t = "Hi {first_name}, this is Dream Team Roofing."
    assert rules.render(t, "Maria") == "Hi Maria, this is Dream Team Roofing."
    assert rules.render(t, None) == "Hi, this is Dream Team Roofing."
    assert rules.render(t, "J.R.") == "Hi, this is Dream Team Roofing."


def test_the_default_sequences_pass_their_own_checks_and_never_say_approved():
    for d in c.DEFAULT_SEQUENCES:
        assert rules.validate_steps(d["steps"]) == [], d["name"]
        for s in d["steps"]:
            assert "approv" not in (s.get("template") or "").lower()
            assert "$" not in (s.get("template") or "")
    assert [s["type"] for s in c.DEFAULT_SEQUENCES[1]["steps"]] == [
        "text", "call_me", "ai_call", "text", "call_me"]


def test_a_sequence_a_person_built_is_checked():
    v = rules.validate_steps
    assert v([]) == ["a sequence needs at least one step"]
    assert v([{"type": "email", "delay_minutes": 1}])
    assert v([{"type": "text", "delay_minutes": 1}])                         # no wording
    assert v([{"type": "text", "delay_minutes": 1, "template": "Hi {name}"}])  # placeholder
    assert v([{"type": "call_me", "delay_days": 1}])                         # no time
    assert v([{"type": "call_me", "delay_days": 1, "at": "25:00"}])
    assert v([{"type": "ai_call", "delay_minutes": 5, "template": "x"}])     # wording on a call
    assert v([{"type": "text", "delay_minutes": 1, "template": "x" * 400}])
    assert v([{"type": "call_me", "delay_days": 2, "at": "9:30"}]) == []
    assert rules.describe({"type": "ai_call", "delay_days": 1, "at": "14:00"}) == \
        "AI call, next day at 2:00 PM"
    assert rules.describe({"type": "text", "delay_minutes": 5}) == "Text, 5 min after"

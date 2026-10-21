"""The call follow-up's rules (app/followup/rules.py) - pure, by behaviour."""
from datetime import UTC, datetime, timedelta

from app.followup import config as c
from app.followup import rules
from app.followup.rules import Call

T0 = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)          # Tue 11 AM New York
M = timedelta(minutes=1)
LOOK = timedelta(hours=6)


def team_call(at, **kw):
    return Call(at=at, kind="call", out=True, answered=False, ai=False, **kw)


def step(after=(), now=T0 + 3 * M, text_at=None, called=False, hours=True, today=0):
    return rules.next_step(trigger_at=T0, text_at=text_at, called=called, after=list(after),
                           now=now, text_delay=2 * M, wait=10 * M, lookback=LOOK,
                           in_call_hours=hours, calls_today=today, calls_per_day=2)


def test_only_a_final_unanswered_call_by_a_person_starts_one():
    assert rules.trigger([team_call(T0)], now=T0 + M, since=None, lookback=LOOK).at == T0
    for c_ in (Call(T0, "call", True, True, False),            # answered
               Call(T0, "call", False, False, False),          # their call, not ours
               Call(T0, "call", True, False, True),            # an AI's own call
               team_call(T0, final=False),                     # still ringing
               Call(T0, "text", True, False, False)):          # a text
        assert rules.trigger([c_], now=T0 + M, since=None, lookback=LOOK) is None, c_


def test_no_backlog_and_nothing_old():
    assert rules.trigger([team_call(T0)], now=T0 + M, since=T0 + M / 2, lookback=LOOK) is None
    assert rules.trigger([team_call(T0)], now=T0 + 7 * timedelta(hours=1), since=None,
                         lookback=LOOK) is None


def test_reached_in_any_way_stops_it():
    for later, why in ((Call(T0 + M, "call", True, True, False), "answered"),
                       (Call(T0 + M, "call", False, False, False), "called back"),
                       (Call(T0 + M, "text", False, False, False), "texted back"),
                       (Call(T0 + M, "text", True, False, False), "team texted")):
        assert rules.trigger([team_call(T0), later], now=T0 + 2 * M, since=None,
                             lookback=LOOK) is None
        s = step(after=[later])
        assert s.action == "done" and why.split()[0] in s.reason, (s, why)
    # A second unanswered team call is not "reached".
    assert step(after=[team_call(T0 + M)]).action == "text"


def test_the_timeline_text_then_wait_then_call():
    assert step(now=T0 + M).action == "wait"                        # before the text delay
    assert step(now=T0 + 2 * M).action == "text"
    texted = T0 + 2 * M
    w = step(now=texted + 4 * M, text_at=texted)
    assert w.action == "wait" and "call in 6 min" in w.reason
    assert step(now=texted + 10 * M, text_at=texted).action == "call"
    assert step(now=texted + 11 * M, text_at=texted, called=True).action == "done"


def test_calls_respect_hours_and_the_daily_limit():
    texted = T0 + 2 * M
    s = step(now=texted + 10 * M, text_at=texted, hours=False)
    assert s.action == "wait" and "8 AM-8 PM" in s.reason
    assert step(now=T0 + 7 * timedelta(hours=1), text_at=texted, hours=False).action == "expire"
    s = step(now=texted + 10 * M, text_at=texted, today=2)
    assert s.action == "done" and "2 times" in s.reason


def test_call_hours_are_8_to_8_monday_to_saturday():
    ny = lambda d, h: datetime(2026, 10, d, h, 0, tzinfo=UTC) + timedelta(hours=4)  # noqa: E731
    assert c.call_hours(ny(20, 8)) and c.call_hours(ny(20, 19))
    assert not c.call_hours(ny(20, 7)) and not c.call_hours(ny(20, 20))
    assert c.call_hours(ny(24, 12))                                  # Saturday
    assert not c.call_hours(ny(25, 12))                              # Sunday


def test_the_text_uses_a_real_first_name_or_the_no_name_wording():
    assert rules.render(c.DEFAULT_TEXT, "Maria", c.DEFAULT_TEXT_NO_NAME).startswith("Hi Maria, ")
    for name in (None, "", "Santiago(TEST)", "123"):
        assert rules.render(c.DEFAULT_TEXT, name, c.DEFAULT_TEXT_NO_NAME) == \
            c.DEFAULT_TEXT_NO_NAME


def test_settings_are_checked():
    assert c.validate({"wait_minutes": 10, "text_delay_minutes": 2}) == []
    assert c.validate({"wait_minutes": 0})
    assert c.validate({"text_template": "Your repair is $400"})
    assert c.validate({"caller_id": "123"})
    assert c.validate({"nonsense": 1})
    s = c.Settings.from_config({"wait_minutes": 15, "test_phones": ["(561) 878-8090", "x"]})
    assert s.wait_minutes == 15 and s.test_phones == ["5618788090"] and s.test_only is True

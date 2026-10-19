"""The auto-reply's rules (2026-10-19, app/autoreply/rules.py) and its text guards
(app/autoreply/writer.py), pinned with plain objects — no database, no network.

Pinned, by behaviour (the owner's answers when the design was grilled):
  * A customer's text unanswered 5 minutes gets ONE reply; 4 minutes waits; a minute of quiet
    after their last message.
  * A PERSON answering stops it: Luis's text, an answered call on any line, a Quo text. The six
    Zuper workflows' texts, "Zuper"'s own and the auto-reply's own do NOT count as answering.
  * "ok" / "thanks" / "C" / 👍 open nothing; STOP stops everything until START; a blocked number
    is never texted; nothing older than a day; nothing that started before the switch-on.
  * Every missed call opens a stretch, voicemail or not, any length.
  * One reply per stretch, and never a second text to the number within 24 hours.
  * A text that mentions money, "approved", a phone number or a link is never sent — the fixed
    polite wording goes instead.
"""
from datetime import UTC, datetime, timedelta

import pytest
from app.autoreply import config as c
from app.autoreply import context, writer
from app.autoreply.rules import Ours, ZMsg, decide
from app.dispatch.comms import Comm

NOW = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)          # Tue 11 AM New York: office open
ON = NOW - timedelta(days=1)                              # switched on yesterday


def ago(minutes: float) -> datetime:
    return NOW - timedelta(minutes=minutes)


def text_in(uid, minutes, body="Can someone come look at my roof?"):
    return ZMsg(uid=uid, at=ago(minutes), kind="text", out=False, body=body)


def text_out(uid, minutes, body="Hi, Luis here, we can come Thursday.", user="Luis"):
    return ZMsg(uid=uid, at=ago(minutes), kind="text", out=True, body=body, user=user,
                status="DELIVERED")


def call(uid, minutes, *, out=False, status="MISSED", seconds=0):
    return ZMsg(uid=uid, at=ago(minutes), kind="call", out=out, call_status=status,
                seconds=seconds)


def go(msgs, other=(), ours=None, now=NOW, enabled_at=ON, blocked=False):
    return decide(list(msgs), list(other), ours or Ours(), now=now, enabled_at=enabled_at,
                  blocked=blocked)


# ---- when it answers -----------------------------------------------------------------------

def test_a_text_unanswered_five_minutes_gets_a_reply():
    d = go([text_out("a", 600), text_in("b", 6)])
    assert d.action == "reply" and d.kind == "text" and d.trigger.uid == "b"
    assert d.after_hours is False


def test_four_minutes_still_waits():
    assert go([text_in("b", 4)]).action == "wait"


def test_it_waits_a_minute_of_quiet_after_their_last_message():
    d = go([text_in("b", 7), text_in("c", 0.5, "Also the gutter")])
    assert d.action == "wait" and d.trigger.uid == "b"


def test_the_stretch_is_timed_from_its_first_message():
    d = go([text_in("b", 7), text_in("c", 2, "Hello??")])
    assert d.action == "reply" and d.trigger.uid == "b" and len(d.stretch) == 2


# ---- a person answering --------------------------------------------------------------------

def test_a_staff_text_after_it_is_an_answer():
    assert go([text_in("b", 30), text_out("c", 20)]).action == "nothing"


@pytest.mark.parametrize("body,user", [
    (('Hi Mary, this is Dream Team Roofing. Reminder: your roof inspection is tomorrow ... '
      'Please reply "YES" to confirm the appointment. Reply STOP to opt out.'), "Owen"),
    ("Thank you, Mary! Your appointment is confirmed. See you then! - Dream Team Roofing",
     "Zuper"),
    (("Hi Mary, this is Dream Team Roofing. We have submitted your inspection report to "
      "American Home Shield."), "Owen"),
    ("anything at all", "Zuper"),
])
def test_a_workflow_text_is_not_a_person_answering(body, user):
    d = go([text_in("b", 30), text_out("c", 20, body, user)])
    assert d.action == "reply"


def test_an_answered_call_on_zuper_is_an_answer_but_a_cancelled_one_is_not():
    assert go([text_in("b", 30), call("c", 20, out=True, status="COMPLETED",
                                      seconds=45)]).action == "nothing"
    assert go([text_in("b", 30), call("c", 20, out=True, status="CANCELED")]).action == "reply"


def test_quo_counts_but_an_ai_agents_text_does_not():
    quo_text = Comm(at=ago(20), kind="text", out=True, talked=False, missed=False, seconds=0,
                    summary="On my way", source="Quo")
    quo_call = Comm(at=ago(20), kind="call", out=True, talked=True, missed=False, seconds=90,
                    summary="", source="Quo", answered=True)
    ai_text = Comm(at=ago(20), kind="text", out=True, talked=False, missed=False, seconds=0,
                   summary="Hi!", source="CRM line", ai=True)
    msgs = [text_in("b", 30)]
    assert go(msgs, [quo_text]).action == "nothing"
    assert go(msgs, [quo_call]).action == "nothing"
    assert go(msgs, [ai_text]).action == "reply"


def test_an_undelivered_staff_text_is_not_an_answer():
    out = text_out("c", 20)
    out.status = "UNDELIVERED"
    assert go([text_in("b", 30), out]).action == "reply"


# ---- what never opens a stretch / never gets a text ----------------------------------------

@pytest.mark.parametrize("body", ["ok", "Thanks!", "C", "yes", "👍", "Ok thank you.", "Sí gracias"])
def test_closers_and_confirmations_need_no_answer(body):
    assert go([text_in("b", 30, body)]).action == "nothing"


def test_stop_stops_everything_until_start():
    d = go([text_in("a", 60, "STOP"), text_in("b", 30)])
    assert d.action == "skip" and "STOP" in d.reason
    assert go([text_in("a", 90, "Stop"), text_in("s", 60, "START"),
               text_in("b", 30)]).action == "reply"


def test_a_blocked_number_is_never_texted():
    d = go([text_in("b", 30)], blocked=True)
    assert d.action == "skip" and "blocked" in d.reason


def test_the_backlog_before_switch_on_is_left_alone():
    d = go([text_in("b", 30)], enabled_at=ago(10))
    assert d.action == "skip" and "before the auto-reply was switched on" in d.reason


def test_nothing_older_than_a_day():
    d = go([text_in("b", 25 * 60)], enabled_at=NOW - timedelta(days=3))
    assert d.action == "skip" and "24 hours" in d.reason


# ---- missed calls --------------------------------------------------------------------------

def test_every_missed_call_opens_a_stretch_any_length():
    d = go([call("b", 6, status="MISSED", seconds=0)])
    assert d.action == "reply" and d.kind == "missed_call"
    d = go([call("b", 6, status="CANCELED", seconds=2)])
    assert d.action == "reply" and d.kind == "missed_call"


def test_a_voicemail_has_its_own_kind_and_a_text_wins_over_both():
    assert go([call("b", 8, status="VOICEMAIL", seconds=20)]).kind == "voicemail"
    assert go([call("b", 8), text_in("c", 6)]).kind == "text"


def test_an_answered_incoming_call_is_not_missed():
    assert go([call("b", 8, status="COMPLETED", seconds=40)]).action == "nothing"


# ---- once ----------------------------------------------------------------------------------

def test_one_reply_per_stretch():
    ours = Ours(message_uids={"r1"}, bodies={"Hi, thanks"}, sent_at=[ago(20)])
    msgs = [text_in("b", 30), text_out("r1", 20, "Hi, thanks", "Owen"),
            text_in("c", 10, "When?")]
    assert go(msgs, ours=ours).action == "nothing"


def test_never_a_second_text_to_the_number_within_a_day():
    ours = Ours(message_uids={"r1"}, sent_at=[ago(300)])
    msgs = [text_in("a", 320), text_out("r1", 300, "auto", "Owen"),
            text_out("p", 200), text_in("b", 30)]
    d = go(msgs, ours=ours)
    assert d.action == "skip" and "24 hours" in d.reason


# ---- office hours --------------------------------------------------------------------------

@pytest.mark.parametrize("when,open_", [
    (datetime(2026, 10, 20, 15, 0, tzinfo=UTC), True),    # Tue 11 AM
    (datetime(2026, 10, 20, 23, 0, tzinfo=UTC), False),   # Tue 7 PM
    (datetime(2026, 10, 18, 15, 0, tzinfo=UTC), False),   # Sunday
    (datetime(2026, 11, 26, 15, 0, tzinfo=UTC), False),   # Thanksgiving
    (datetime(2026, 12, 25, 15, 0, tzinfo=UTC), False),   # Christmas
    (datetime(2026, 10, 24, 13, 0, tzinfo=UTC), True),    # Saturday 9 AM
])
def test_office_hours(when, open_):
    assert c.office_open(when) is open_


def test_after_hours_is_flagged():
    late = datetime(2026, 10, 20, 23, 30, tzinfo=UTC)
    d = decide([ZMsg("b", late - timedelta(minutes=6), "text", False, "Leak!")], [], Ours(),
               now=late, enabled_at=ON)
    assert d.action == "reply" and d.after_hours is True


# ---- the text's guards ---------------------------------------------------------------------

GOOD = "Hi Maria, thanks for your message! Someone from our team will get back to you shortly."


@pytest.mark.parametrize("reply,why", [
    ("Hi Maria, the repair will be $450.", "money"),
    ("Hola Maria, su reparación fue aprobada.", "money, approval"),
    ("Good news, AHS approved it!", "approval"),
    ("Call Luis at 941-555-0100.", "phone"),
    ("See https://example.com", "link"),
    ("x" * 400, "longer"),
])
def test_a_text_that_breaks_a_rule_is_refused(reply, why):
    assert writer.refused(reply, after_hours=False, upset=False)


def test_our_own_number_is_allowed_and_a_good_text_passes():
    assert writer.refused(GOOD, after_hours=False, upset=False) is None
    assert writer.refused(GOOD + " Questions? (954) 420-7373.", after_hours=False,
                          upset=False) is None


def test_after_hours_must_say_so_and_upset_states_no_fact():
    assert writer.refused(GOOD, after_hours=True, upset=False)
    assert writer.refused("Hi Maria, we received your message. We are outside of business hours "
                          "but will get back to you as soon as possible.",
                          after_hours=True, upset=False) is None
    assert writer.refused("Hi Maria, sorry. Your visit is Thursday at 9.", after_hours=False,
                          upset=True)


def test_the_fixed_wording():
    t = writer.template("text", first="Maria", after_hours=True, language="en")
    assert t.startswith("Hi Maria, ") and "outside of business hours" in t
    assert writer.refused(t, after_hours=True, upset=True) is None
    es = writer.template("missed_call", first="Jose", after_hours=False, language="es")
    assert es.startswith("Hola Jose, ") and "llamada" in es
    assert writer.refused(es, after_hours=False, upset=True) is None
    for kind in ("text", "missed_call", "voicemail"):
        for lang in ("en", "es"):
            for late in (True, False):
                t = writer.template(kind, first=None, after_hours=late, language=lang)
                assert writer.refused(t, after_hours=late, upset=True) is None, t


# ---- what leaves for the model -------------------------------------------------------------

def test_phones_emails_and_addresses_are_removed():
    t = context.redact("Call me at (941) 555-0100 or 9415550100, mail jo@x.com, I'm at "
                       "1234 Palm Tree Dr, Bradenton and 12 Main St.", ("88 Oak Hollow",))
    assert "555" not in t and "jo@x.com" not in t and "Palm Tree" not in t
    assert "Main St" not in t and "[phone]" in t and "[email]" in t and "[address]" in t
    assert context.redact("Meet at 88 Oak Hollow", ("88 Oak Hollow",)) == "Meet at [address]"


def test_first_names():
    assert context.first_name("Santiago Villahermosa (TEST)") == "Santiago"
    assert context.first_name("ELISHA WRIGHT") == "Elisha"
    assert context.first_name(None) == ""

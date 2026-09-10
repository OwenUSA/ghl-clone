"""What the panel tells the user about the customer's reminders — executed.

This project's usual frontend idiom is "assert against source", and it is too
weak for this one string. The failure that made appointment editing a deferred
task is silent: a rescheduled appointment whose reminder never got re-queued
looks exactly like one whose reminder is waiting. The sentence in the panel is
the only place a dispatcher finds out which of the two they have — so a regex
saying "the file mentions rescheduled" would pass against wording that claims a
reminder was queued when the backend said it was suppressed.

`frontend/src/lib/reminders.ts` is therefore written import-free, the same way
`calendarGrid.ts` is, so node can run the real shipped function. The inputs below
are the actual `automation` strings `PATCH /api/appointments/{id}` returns — each
one is produced by a test in test_messaging.py or test_automations.py, so this
file and the backend cannot drift apart on the vocabulary.
"""
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REMINDERS_TS = (Path(__file__).resolve().parents[2]
                / "frontend" / "src" / "lib" / "reminders.ts")

NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None,
    reason="node is not on PATH, so reminders.ts cannot be executed. CI's "
           "backend job installs it — see .github/workflows/ci.yml.")


def say(automation: str) -> str:
    script = textwrap.dedent("""
        import { reminderSentence } from %s
        console.log('@@' + JSON.stringify(reminderSentence(%s)))
    """) % (json.dumps(REMINDERS_TS.as_posix()), json.dumps(automation))
    proc = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-"],
        input=script, capture_output=True, text=True,
        env={**os.environ, "TZ": "America/New_York"})
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert line, "the script printed no result:\n" + proc.stdout + proc.stderr
    return json.loads(line[-1][2:])


@node
def test_a_reschedule_says_the_customer_will_still_be_reminded():
    """`queued 24h,1h` is what the backend returns when both reminders were
    re-queued against the new time. The offsets have to survive into the
    sentence: "the reminder moved" and "the reminder moved to the right time"
    are different claims."""
    got = say("queued 24h,1h")
    assert "rescheduled" in got
    assert "24 hours" in got and "1 hour" in got, (
        "the sentence does not say when the customer will be reminded")
    assert "new time" in got
    assert "24h" not in got and "1h," not in got, (
        "the queue's own shorthand leaked into the sentence")


@node
def test_a_reschedule_too_soon_for_a_reminder_says_so_out_loud():
    """A booking moved to inside the hour cannot get a reminder — one scheduled
    in the past would fire immediately, which the backend refuses to do. This is
    the case where saying nothing is actively harmful: nobody tells the customer."""
    got = say("nothing to schedule")
    assert "too soon" in got, "the user is not told why no reminder was queued"
    assert "none was queued" in got
    assert "will be reminded" not in got, (
        "it claims a reminder was queued when none was")


@node
@pytest.mark.parametrize("automation,expect", [
    ("suppressed: contact is on DND", "contact is on DND"),
    ("suppressed: contact has no phone number", "contact has no phone number"),
    ("suppressed: no contact", "no contact"),
])
def test_a_suppressed_reminder_names_the_reason(automation, expect):
    """The booking still saved; the customer just will not hear from us. Both
    halves have to be in the sentence, or a DND contact's silent appointment
    reads as a normal one."""
    got = say(automation)
    assert "Saved" in got, "the user is not told the edit landed"
    assert expect in got, "the sentence does not say why there is no reminder"
    assert "no reminder was queued" in got
    assert "suppressed:" not in got, "the backend's own prefix leaked through"


@node
def test_cancelling_says_the_reminder_was_withdrawn():
    got = say("reminders cancelled")
    assert "withdrawn" in got
    assert "rescheduled" not in got


@node
def test_an_edit_that_did_not_move_it_does_not_claim_a_reschedule():
    """A title fix returns `unchanged`, and the reminders are deliberately left
    exactly as they were. Saying "rescheduled" here would be a lie in the other
    direction."""
    got = say("unchanged")
    assert got == "Saved."


@node
def test_an_unrecognised_outcome_is_shown_rather_than_swallowed():
    """A future automation outcome this function has not been taught must not be
    reported as a bare "Saved." — an unrecognised reminder result rendered as
    success is the exact silence this surface was deferred over."""
    got = say("escalated to the on-call dispatcher")
    assert "escalated to the on-call dispatcher" in got, (
        "an unknown outcome was swallowed")
    assert got != "Saved.", "an unknown outcome reads as a plain success"


@node
def test_every_outcome_the_backend_can_return_produces_a_distinct_sentence():
    """The vocabulary, in one place. If a backend outcome is ever added without a
    branch here it collapses onto the fallback, and two different things then
    read the same to the user."""
    outcomes = ["unchanged", "reminders cancelled", "queued 24h,1h", "queued 1h",
                "nothing to schedule", "suppressed: contact is on DND",
                "no reminders for a cancelled appointment"]
    said = [say(o) for o in outcomes]
    assert len(set(said)) == len(outcomes), (
        "two different reminder outcomes read identically: %s" % said)
    assert all(s.startswith("Saved") for s in said), (
        "an outcome does not begin by saying whether the edit landed")

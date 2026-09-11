"""What the composer tells the operator happened to their message — executed.

Same reasoning as test_reminder_sentence.py, and the same mechanism: the shipped
`frontend/src/lib/sendOutcome.ts` is import-free, so node runs the real function and
these assertions are about behaviour rather than about the file containing a word.

It matters more here than almost anywhere else in the app. The owner's requirement
is "i want to know if the text arrived or not", and the failure this guards against
is a message that did NOT go being described in words that sound like it did. A
source-level regex would happily pass against wording that says the opposite of what
it should.

The inputs are the backend's own `reason` strings from
`POST /api/conversations/{id}/messages` — every one of them is produced by a test in
test_crm_link.py, so this file and the backend cannot drift apart on the vocabulary.
"""
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

SEND_TS = (Path(__file__).resolve().parents[2]
           / "frontend" / "src" / "lib" / "sendOutcome.ts")

NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None,
    reason="node is not on PATH, so sendOutcome.ts cannot be executed. CI's "
           "backend job installs it — see .github/workflows/ci.yml.")


def say(reason: str) -> str:
    script = textwrap.dedent("""
        import { sendSentence } from %s
        console.log('@@' + JSON.stringify(sendSentence(%s)))
    """) % (json.dumps(SEND_TS.as_posix()), json.dumps(reason))
    proc = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-"],
        input=script, capture_output=True, text=True,
        env={**os.environ, "TZ": "America/New_York"})
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert line, "the script printed no result:\n" + proc.stdout + proc.stderr
    return json.loads(line[-1][2:])


# Everything that is NOT a message on its way to the customer. Each of these must
# be unmistakable about that, because this is the only place the operator finds out.
DID_NOT_GO = [
    "sent",                                   # the stub transport's word
    ("refused: Texting is not switched on yet — the number is still waiting "
     "on carrier (10DLC) approval."),
    "refused: This contact has opted out of text messages.",
    "failed: could not reach the phone system",
    "suppressed: contact is on DND",
    "suppressed: contact has no phone number",
]


@node
@pytest.mark.parametrize("reason", DID_NOT_GO)
def test_a_message_that_did_not_go_never_reads_as_one_that_did(reason):
    got = say(reason)
    assert "Not sent" in got or "NOT sent" in got, (
        "%r is reported as %r, which does not say it failed to send"
        % (reason, got))
    # The one word that would make a failure read as a success.
    assert "Delivered" not in got and "delivered" not in got


@node
def test_a_queued_message_says_it_is_on_its_way_but_not_that_it_arrived():
    """QUEUED means the carrier has it. It does NOT mean the customer does, and
    the difference is the entire question the owner asked."""
    got = say("queued")
    assert "Sent" in got
    assert "arrived" in got or "confirm" in got, (
        "it does not say we are still waiting to know: %r" % got)
    assert "Delivered." not in got


@node
def test_the_stub_transport_is_never_described_as_a_sent_text():
    """The backend says "sent" for LoggingTransport because the CLI and three
    tests expect that word on the wire. What the operator is told has to be the
    truth: nothing was transmitted."""
    got = say("sent")
    assert "NOT sent" in got
    assert "not connected" in got or "phone system" in got


@node
def test_a_refusal_carries_its_reason_through():
    got = say("refused: This contact has opted out of text messages.")
    assert "opted out" in got, "the reason was dropped: %r" % got


@node
def test_an_internal_note_says_the_customer_never_sees_it():
    got = say("recorded")
    assert "Internal" in got
    assert "Not sent" not in got, (
        "an internal note was never going to be sent, so reporting it as a "
        "failure would be wrong: %r" % got)


@node
def test_an_unrecognised_outcome_is_shown_rather_than_swallowed():
    """A backend that grows a sixth outcome must not have it reported as a
    cheerful success by a frontend that has not been taught the word."""
    got = say("something nobody has taught this function")
    assert "something nobody has taught this function" in got

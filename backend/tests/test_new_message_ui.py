""""New message", the composer's delivery line and Settings → Automations, in the browser
(2026-09-15).

**Executed.** `lib/dialPad.ts` (`textProblem`), `lib/smsSegments.ts`, `lib/mmsNote.ts` and
`lib/sendOutcome.ts` import nothing at runtime, so node runs the shipped code. The number
field's sentences are checked against the SERVER's `text_problem` for the same inputs, so
the explanation beside Send can never drift from the gate.

**Asserted against source.** The React wiring this project has no runner for. The dialog
and the composer are also driven in a real headless browser by `tests/browser_sms.py`
(not collected by pytest: it starts a dev server).
"""
import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from app.main import text_problem

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None,
    reason="node is not on PATH, so the TypeScript cannot be executed. CI's backend job "
           "installs it precisely so this file is never skipped there.")


def read(*parts: str) -> str:
    return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


def strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.DOTALL)
    src = re.sub(r"\{/\*.*?\*/\}", " ", src, flags=re.DOTALL)
    return re.sub(r"(?<![:'\"])//[^\n]*", " ", src)


def run_js(body: str):
    imports = "\n".join(
        "import * as %s from %s" % (name, json.dumps((FRONTEND / "lib" / file).as_posix()))
        for name, file in (("pad", "dialPad.ts"), ("seg", "smsSegments.ts"),
                           ("mms", "mmsNote.ts"), ("outc", "sendOutcome.ts")))
    script = imports + "\nconst out = (v) => console.log('@@' + JSON.stringify(v))\n" \
        + textwrap.dedent(body)
    proc = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-"],
        input=script, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert line, "the script printed no result:\n" + proc.stdout + proc.stderr
    return json.loads(line[-1][2:])


# ---------------- the number field: the browser and the server say the same thing -------

NUMBERS = ["", "   ", "941", "(941) 555-01", "(941) 555-0199", "19415550199", "+1 941 555 0199",
           "+44 20 7946 0958", "941555019912", "(054) 482-9099", "(941) 155-0199",
           "(954) 482-9099", "941*555#0199"]


@node
def test_text_problem_matches_the_server_word_for_word():
    got = run_js("out(%s.map((n) => [pad.textProblem(n), pad.normaliseTextNumber(n)]))"
                 % json.dumps(NUMBERS))
    for number, (problem, normalised) in zip(NUMBERS, got, strict=True):
        e164, server = text_problem(number)
        assert problem == server, (number, problem, server)
        assert normalised == e164, (number, normalised, e164)


@node
def test_an_invalid_number_is_explained_in_words_about_texting():
    got = run_js("out([pad.textProblem(''), pad.textProblem('(941) 555-01'), "
                 "pad.textProblem('(954) 482-9099')])")
    assert got[0] == "Enter a number to text — 10 digits, area code first."
    assert "too short" in got[1]
    assert got[2] == "That is this CRM's own number — it cannot text itself."


# ---------------- characters and segments ----------------

@node
def test_segments_follow_the_carriers_arithmetic():
    got = run_js("""
        const s = (t) => { const i = seg.segmentInfo(t); return [i.encoding, i.units, i.segments] }
        out([s(''), s('a'.repeat(160)), s('a'.repeat(161)), s('a'.repeat(306)), s('a'.repeat(307)),
             s('[' .repeat(80)), s('[' .repeat(81)), s('é'.repeat(160)),
             s('Roof done 👍'), s('x'.repeat(69) + '👍'), s('“quoted”'),
             seg.segmentLabel(''), seg.segmentLabel('hello'), seg.segmentLabel('hi 👍'),
             seg.segmentInfo('Roof done 👍').characters])
    """)
    assert got[0] == ["GSM-7", 0, 0]
    assert got[1] == ["GSM-7", 160, 1]
    assert got[2] == ["GSM-7", 161, 2]
    assert got[3] == ["GSM-7", 306, 2]
    assert got[4] == ["GSM-7", 307, 3]
    assert got[5] == ["GSM-7", 160, 1], "an extension character takes two septets"
    assert got[6] == ["GSM-7", 162, 2]
    assert got[7] == ["GSM-7", 160, 1], "é is in the GSM-7 alphabet"
    assert got[8] == ["UCS-2", 12, 1], "one emoji switches the whole text to UCS-2"
    assert got[9] == ["UCS-2", 71, 2]
    assert got[10][0] == "UCS-2", "a curly quote pasted from Word is not GSM-7"
    assert got[11] == "0 characters"
    assert got[12] == "5 characters · 1 segment"
    assert got[13].startswith("4 characters · 1 segment · special characters: 70")
    assert got[14] == 11, "an emoji is one character to a person"


# ---------------- MMS: what the event actually carries ----------------

@node
def test_the_mms_note_owen_main_appends_becomes_an_attachment_line():
    got = run_js("""
        out([mms.splitMmsNote('Here is the leak [2 attachments — view in OWEN]'),
             mms.splitMmsNote('[1 attachment — view in OWEN]'),
             mms.splitMmsNote('I said [2 attachments] in words'),
             mms.splitMmsNote(null), mms.attachmentLabel(1)])
    """)
    assert got[0] == {"text": "Here is the leak", "attachments": 2}
    assert got[1] == {"text": "", "attachments": 1}
    assert got[2] == {"text": "I said [2 attachments] in words", "attachments": 0}
    assert got[3] == {"text": "", "attachments": 0}
    assert got[4].startswith("1 attachment — view in OWEN")


def test_the_mms_split_reads_owen_mains_exact_wording():
    """The pattern is only right while owen-main writes this note. Pinned from its source."""
    owen = Path("/home/qa/owen-main/backend/app/integrations/crm/events.py")
    if not owen.exists():
        pytest.skip("owen-main is not checked out beside this repository")
    assert 'note = f"[{facts.num_media} {plural} — view in OWEN]"' in owen.read_text()


# ---------------- the bubble's delivery line ----------------

@node
def test_refused_shows_the_sentence_and_failed_offers_a_retry():
    got = run_js("""
        const ev = (status, body = 'hi', direction = 'OUTBOUND', type = 'SMS') =>
          ({ status, e: { direction, type, delivery_status: status, body } })
        out([
          outc.deliveryExplanation('REFUSED', 'This number has opted out of texts (they replied STOP). Nothing was sent.'),
          outc.deliveryExplanation('FAILED', 'could not reach the phone system'),
          outc.deliveryExplanation('FAILED', null),
          outc.deliveryExplanation('QUEUED', null),
          outc.deliveryExplanation('DELIVERED', null),
          outc.deliveryExplanation('LOGGED_ONLY', null),
          ['FAILED', 'REFUSED', 'QUEUED', 'SENT', 'DELIVERED', 'LOGGED_ONLY'].map((s) => outc.canRetry(ev(s).e)),
          outc.canRetry(ev('FAILED', '  ').e),
          outc.canRetry(ev('FAILED', 'hi', 'INBOUND').e),
          outc.sendSentence('failed: could not reach the phone system'),
          outc.sendSentence('refused: This number has opted out of texts (they replied STOP). Nothing was sent.'),
        ])
    """)
    assert "opted out" in got[0] and "retry" not in got[0].lower()
    assert got[1] == "Could not reach the phone system. It did not arrive — you can retry it."
    assert "retry" in got[2]
    assert got[3] is None and got[4] is None
    assert "nothing was sent" in got[5]
    assert got[6] == [True, False, False, False, False, False], "only FAILED is retried"
    assert got[7] is False and got[8] is False
    assert "retry" in got[9] and got[9].startswith("Not sent.")
    assert got[10].startswith("Not sent.") and "retry" not in got[10]


# ---------------- wiring, asserted against source ----------------

def test_new_message_sits_beside_call_a_number_in_the_inbox_header():
    page = strip_comments(read("pages", "ConversationsPage.tsx"))
    new, call, filt = (page.index('title="New message"'), page.index('title="Call a number"'),
                       page.index('title="Filter conversations"'))
    assert new < call < filt and filt - new < 600, "not side by side in the inbox header"
    assert "<NewMessageDialog" in page and "onNewMessageSent" in page


def test_after_a_send_the_page_opens_that_thread_after_refetching_the_list():
    page = strip_comments(read("pages", "ConversationsPage.tsx"))
    body = page.split("const onNewMessageSent = async", 1)[1].split("const onCall", 1)[0]
    refetch = body.index("await qc.invalidateQueries({ queryKey: ['conversations'] })")
    assert refetch < body.index("setSelected(r.key)"), (
        "selecting before the list refetches lets the 'dropped out of the list' effect clear it")
    assert "setScope('team')" in body and "setQ('')" in body


def test_the_dialog_never_offers_a_sender_and_names_the_one_line():
    dialog = strip_comments(read("components", "NewMessageDialog.tsx"))
    assert "Sending from" in dialog and "formatPhone(CALLING_FROM)" in dialog
    assert "sendNewMessage(to, body)" in dialog, "the request carries a number and a body only"
    for word in ("OpenPhone", "Quo", "from_number", "<select"):
        assert word not in dialog, "a sender choice leaked into New message: %s" % word
    assert "disabled={!canSend}" in dialog and "textProblem(number)" in dialog
    assert "segmentLabel(body)" in dialog


def test_the_api_helper_sends_no_from_number():
    api = read("lib", "api.ts")
    helper = api.split("export const sendNewMessage", 1)[1].split("\n\n", 1)[0]
    assert "{ number, body }" in helper and "from" not in helper.split("=>", 1)[1]


def test_failed_bubbles_get_a_retry_and_refused_ones_do_not():
    page = strip_comments(read("pages", "ConversationsPage.tsx"))
    note = page.split("function DeliveryNote(", 1)[1].split("function EventBubble(", 1)[0]
    assert "onRetry && canRetry(e)" in note
    assert "deliveryExplanation(e.delivery_status, e.delivery_detail)" in note


def test_no_screen_still_says_texting_waits_on_carrier_approval():
    for path in FRONTEND.rglob("*.ts*"):
        src = path.read_text(encoding="utf-8")
        assert not re.search(r"10DLC is approved|waiting on carrier|dark until 10DLC", src), path


def test_settings_lists_the_rules_with_no_switch():
    panel = strip_comments(read("components", "AutomationsSettings.tsx"))
    assert "listAutomations" in panel and "r.reason" in panel
    assert 'type="checkbox"' not in panel and "role=\"switch\"" not in panel
    assert "onClick" not in panel, "Automations is read-only: nothing to switch"
    settings = read("pages", "SettingsPage.tsx")
    assert "section === 'automations' ? <AutomationsSettings />" in settings

"""The Conversations dialer and the in-call window, in the browser (2026-09-14).

**Executed.** `lib/dialPad.ts`, `lib/outboundIntent.ts`, `lib/callWindow.ts` and
`lib/dtmfTone.ts` import nothing at runtime, so node runs the shipped code: typing, the
keypad, paste, every reason Call is disabled, the outbound intent's single-shot TTL and
number match, and the after-call decision. The dialer's sentences are checked against the
SERVER's `dial_problem` for the same inputs, so the explanation can never drift from the
gate.

**Asserted against source.** The React wiring this project has no runner for: one choke
point marks the intent, the hook claims it before anything can ring, the window is mounted
once, and Hold / Transfer are not rendered. The components themselves are driven in a real
headless browser with the SIP layer faked by `tests/browser_dialer.py` (not collected by
pytest: it starts a dev server), recorded in `.qa/state/dialer-done`.
"""
import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from app.main import dial_problem

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None,
    reason="node is not on PATH, so the dialer's TypeScript cannot be executed. CI's "
           "backend job installs it precisely so this file is never skipped there.")


def read(*parts: str) -> str:
    return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


def strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.DOTALL)
    return re.sub(r"(?<![:'\"])//[^\n]*", " ", src)


def run_js(body: str):
    imports = "\n".join(
        "import * as %s from %s" % (name, json.dumps((FRONTEND / "lib" / file).as_posix()))
        for name, file in (("pad", "dialPad.ts"), ("intent", "outboundIntent.ts"),
                           ("win", "callWindow.ts"), ("tone", "dtmfTone.ts")))
    script = imports + "\nconst out = (v) => console.log('@@' + JSON.stringify(v))\n" \
        + textwrap.dedent(body)
    proc = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-"],
        input=script, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert line, "the script printed no result:\n" + proc.stdout + proc.stderr
    return json.loads(line[-1][2:])


# ---------------- the number field ----------------

@node
def test_typing_formats_as_you_type():
    got = run_js("""
        let v = ''
        const seen = []
        for (const ch of '9415550199') { v = pad.formatDialInput(v + ch); seen.push(v) }
        out(seen)
    """)
    assert got == ["(9", "(94", "(941", "(941) 5", "(941) 55", "(941) 555", "(941) 555-0",
                   "(941) 555-01", "(941) 555-019", "(941) 555-0199"]


@node
def test_typing_a_leading_one_or_plus_one_keeps_it_readable():
    got = run_js("out([pad.formatDialInput('19415550199'), pad.formatDialInput('+19415550199'),"
                 " pad.formatDialInput('abc 941')])")
    assert got == ["1 (941) 555-0199", "+1 (941) 555-0199", "(941"]


@node
def test_the_keypad_appends_and_backspace_removes_the_last_digit_typed():
    got = run_js("""
        let v = ''
        for (const k of ['9','4','1','5','5','5','0','1','9','9']) v = pad.pressKey(v, k)
        const full = v
        const back = pad.backspace(v)
        const backToNothing = pad.backspace(pad.backspace('(9'))
        const star = pad.pressKey(full, '*')
        const junk = pad.pressKey(full, 'x')
        out({ full, back, backToNothing, star, junk })
    """)
    assert got == {"full": "(941) 555-0199", "back": "(941) 555-019", "backToNothing": "",
                   "star": "9415550199*", "junk": "(941) 555-0199"}


@node
def test_paste_takes_the_number_out_of_whatever_was_pasted():
    got = run_js("""
        out(['tel:+19415550199', '+1 (941) 555-0199', '941.555.0199',
             'Call me on 941-555-0199 after 5', '  9415550199\\n'].map(pad.pasteNumber))
    """)
    assert got == ["+1 (941) 555-0199", "+1 (941) 555-0199", "(941) 555-0199",
                   "(941) 555-0199", "(941) 555-0199"]


@node
def test_normalised_number_is_what_the_server_is_sent():
    got = run_js("""
        out(['(941) 555-0199', '+1 (941) 555-0199', '1 941 555 0199', '555-0199', '(941) 555-01*9']
            .map(pad.normaliseNumber))
    """)
    assert got == ["+19415550199", "+19415550199", "+19415550199", None, None]


CASES = ["", "9", "(941) 555-01", "555-0199", "(941) 555-0199", "+1 (941) 555-0199",
         "(941) 555-01*9", "#123", "+44 20 7946 0958", "941 555 0199 9",
         "(054) 555-0199", "(941) 155-0199", "(954) 482-9099", "1 (954) 482-9099"]


@node
def test_every_reason_call_is_disabled_matches_the_server_word_for_word():
    got = run_js("out(%s.map(pad.dialProblem))" % json.dumps(CASES))
    server = [dial_problem(c)[1] for c in CASES]
    assert got == server, list(zip(CASES, got, server, strict=True))
    assert got[4] is None and got[5] is None
    assert all(isinstance(x, str) and x.endswith(".") for i, x in enumerate(got) if i not in (4, 5))


def test_the_server_normalises_what_the_browser_normalises():
    assert dial_problem("(941) 555-0199") == ("+19415550199", None)
    assert dial_problem("+1 941.555.0199") == ("+19415550199", None)


@node
def test_calling_from_is_the_one_bound_line():
    got = run_js("out(pad.CALLING_FROM)")
    assert got == "+19544829099"


# ---------------- is the browser phone ready ----------------

@node
def test_call_is_disabled_with_a_sentence_for_every_phone_state_but_ready():
    hook = read("lib", "softphone.ts")
    union = hook.split("export type SoftphoneStatus =", 1)[1].split("/** `connecting`", 1)[0]
    states = set(re.findall(r"^\s*\|\s*'([a-z-]+)'", union, re.MULTILINE))
    assert len(states) >= 8, states
    got = run_js("out(Object.fromEntries(%s.map((s) => [s, win.phoneNotReadyReason(s)])))"
                 % json.dumps(sorted(states)))
    assert got.pop("ready") is None
    assert got["off"] == "Your browser phone is switched off — switch it on to call."
    assert all(isinstance(v, str) and len(v) > 20 for v in got.values()), got


@node
def test_a_ready_phone_already_on_a_call_cannot_place_another():
    got = run_js("out(['ringing','connecting','in-call','idle'].map((p) => "
                 "win.phoneNotReadyReason('ready', p)))")
    assert got[:3] == ["You are already on a call — hang up first."] * 3
    assert got[3] is None


# ---------------- the outbound intent ----------------

@node
def test_the_next_invite_for_the_dialled_number_within_the_ttl_is_claimed_once():
    got = run_js("""
        const T = 1_000_000
        intent.markOutboundIntent('+19415550199', T)
        const pendingBefore = intent.outboundIntentPending(T + 1)
        const first = intent.claimOutboundIntent('19415550199', T + 2_000)
        const second = intent.claimOutboundIntent('+19415550199', T + 2_500)
        out({ pendingBefore, first, second, pendingAfter: intent.outboundIntentPending(T + 3_000) })
    """)
    assert got == {"pendingBefore": True, "first": True, "second": False,
                   "pendingAfter": False}


@node
def test_an_invite_after_the_ttl_is_treated_as_inbound():
    got = run_js("""
        const T = 5_000_000
        intent.markOutboundIntent('(941) 555-0199', T)
        const TTL = intent.OUTBOUND_INTENT_TTL_MS
        const late = intent.claimOutboundIntent('+19415550199', T + TTL)
        const after = intent.claimOutboundIntent('+19415550199', T + TTL + 1)
        out({ ttl: intent.OUTBOUND_INTENT_TTL_MS, late, after })
    """)
    assert got == {"ttl": 45000, "late": False, "after": False}


@node
def test_a_genuine_inbound_call_during_the_window_is_not_swallowed():
    """A different caller rings while our leg is on its way: theirs is NOT claimed, and
    the intent survives for the leg that does match."""
    got = run_js("""
        const T = 9_000_000
        intent.markOutboundIntent('+19415550199', T)
        const stranger = intent.claimOutboundIntent('+13055550123', T + 1_000)
        const anonymous = intent.claimOutboundIntent(null, T + 1_100)
        const blocked = intent.claimOutboundIntent('anonymous', T + 1_200)
        const ours = intent.claimOutboundIntent('+19415550199', T + 2_000)
        out({ stranger, anonymous, blocked, ours })
    """)
    assert got == {"stranger": False, "anonymous": False, "blocked": False, "ours": True}


@node
def test_a_refused_call_clears_the_intent():
    got = run_js("""
        intent.markOutboundIntent('+19415550199', 100)
        intent.clearOutboundIntent()
        out([intent.outboundIntentPending(101), intent.claimOutboundIntent('+19415550199', 102)])
    """)
    assert got == [False, False]


# ---------------- the window ----------------

@node
def test_after_call_offers_add_as_contact_only_for_an_unknown_number():
    got = run_js("out([win.afterCallActions(null), win.afterCallActions(undefined), "
                 "win.afterCallActions(0), win.afterCallActions(42)])")
    assert got == [{"openConversation": False, "addContact": True},
                   {"openConversation": False, "addContact": True},
                   {"openConversation": True, "addContact": False},
                   {"openConversation": True, "addContact": False}]


@node
def test_timer_and_title():
    got = run_js("out([[0, 7_400, 723_000, 3_723_000, -5, NaN].map(win.formatCallDuration),"
                 " win.callTitle('Jane Doe', '(941) 555-0101'),"
                 " win.callTitle('  ', '(941) 555-0199'), win.callTitle(null, '')])")
    assert got == [["0:00", "0:07", "12:03", "1:02:03", "0:00", "0:00"], "Jane Doe",
                   "(941) 555-0199", "Unknown number"]


@node
def test_each_key_has_its_real_dtmf_pair():
    got = run_js("out(['1','5','9','*','0','#','x'].map(tone.dtmfFrequencies))")
    assert got == [[697, 1209], [770, 1336], [852, 1477], [941, 1209], [941, 1336],
                   [941, 1477], None]


# ---------------- wiring, asserted against source ----------------

def test_one_choke_point_marks_the_intent_before_the_request_leaves():
    for path in FRONTEND.rglob("*.ts*"):
        if path.name in ("outboundIntent.ts", "callLauncher.ts"):
            continue
        assert "markOutboundIntent" not in strip_comments(path.read_text(encoding="utf-8")), (
            f"{path.name} marks the outbound intent itself — only lib/callLauncher.ts may")
    launcher = strip_comments(read("lib", "callLauncher.ts"))
    body = launcher.split("async <T extends CallPlaced>", 1)[1]
    assert body.index("markOutboundIntent(") < body.index("await request("), (
        "the intent must be marked BEFORE the request: the INVITE can beat the response")
    assert body.count("clearOutboundIntent()") == 2, "refusal and failure must both clear it"


def test_every_call_button_goes_through_the_launcher():
    ringing = ("dialNumber(", "callThreadRinging(", "callContactRinging(")
    screens = [*(FRONTEND / "pages").glob("*.tsx"), *(FRONTEND / "components").glob("*.tsx")]
    for path in screens:
        src = strip_comments(path.read_text(encoding="utf-8"))
        for old in ("callThread(", "callContact(", "placeCall("):
            assert not re.search(r"(?<![A-Za-z])" + re.escape(old), src), (
                f"{path.name} still calls {old[:-1]} — it bypasses the outbound intent")
        for fn in ringing:
            for m in re.finditer(re.escape(fn), src):
                window = src[max(0, m.start() - 200):m.start()]
                assert "launch(" in window, f"{path.name}: {fn[:-1]} outside launch()"


def test_the_hook_claims_the_intent_before_the_incoming_card_can_show():
    hook = read("lib", "softphone.ts")
    received = hook.split("onCallReceived:", 1)[1].split("onCallAnswered:", 1)[0]
    assert "claimOutboundIntent(peer)" in received
    assert received.index("claimOutboundIntent(peer)") < received.index("phase: 'ringing'")
    claimed = received.split("claimOutboundIntent(peer)", 1)[1].split("return", 1)[0]
    assert "phase: 'connecting'" in claimed and "answerOwnCall()" in claimed
    assert "'ringing'" not in claimed
    card = read("components", "Softphone.tsx")
    assert "const ringing = state.phase === 'ringing'" in card
    assert "if (!ringing) return null" in card


def test_the_in_call_window_is_mounted_once_inside_the_provider():
    app = read("App.tsx")
    assert app.count("<InCallWindow") == 1
    assert (app.index("<SoftphoneProvider>") < app.index("<InCallWindow")
            < app.index("</SoftphoneProvider>"))


def test_hold_and_transfer_are_not_rendered():
    """The CRM cannot hold or transfer: owen-main's ARI control routes take an owen-main
    login, not the CRM link key, and an inbound call gives the CRM no channel id."""
    ui = strip_comments(read("components", "InCallWindow.tsx"))
    for word in ("Hold", "Resume", "Transfer"):
        assert not re.search(r"['\">]\s*%s" % word, ui), f"{word} is rendered but cannot work"


def test_the_dialer_lives_on_the_conversations_page_only():
    pages = {p.name: strip_comments(p.read_text(encoding="utf-8"))
             for p in (FRONTEND / "pages").glob("*.tsx")}
    assert [n for n, s in pages.items() if "<CallNumberDialog" in s] == ["ConversationsPage.tsx"]
    assert "<CallNumberDialog" not in strip_comments(read("App.tsx"))

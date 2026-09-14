"""The top-right status dot that replaced the floating softphone dock (2026-09-14).

**Executed.** `frontend/src/lib/connectionStatus.ts` imports nothing at runtime, so node
runs the shipped code: which colour each state is, that the dot shows the WORST of the
three checks, that a switched-off browser phone is grey, a stale Quo tick amber, an
unreachable phone system red, and that the phone row offers exactly the actions the dock
did. The server half of those inputs is the REAL `GET /api/connection-status` answer.

**Asserted against source.** The React component, which this project has no runner for:
mounted once in the app shell, opened by hover AND by keyboard focus, and the floating
bottom-right card gone from every page. A real browser hovering and tabbing to it is
recorded in `.qa/state/status-done`.
"""
import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
LIB = FRONTEND / "lib" / "connectionStatus.ts"
NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None,
    reason="node is not on PATH, so lib/connectionStatus.ts cannot be executed. CI's "
           "backend job installs it precisely so this file is never skipped there.")

NOW_MS = "Date.parse('2026-09-14T15:00:00Z')"


def read(*parts: str) -> str:
    return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


def run_js(body: str):
    script = textwrap.dedent("""
        import * as s from %s
        const out = (v) => console.log('@@' + JSON.stringify(v))
        const NOW = %s
    """) % (json.dumps(LIB.as_posix()), NOW_MS) + textwrap.dedent(body)
    proc = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-"],
        input=script, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert line, "the script printed no result:\n" + proc.stdout + proc.stderr
    return json.loads(line[-1][2:])


def server_json(link="ok", quo="ok", facts=None):
    return json.dumps({
        "checked_at": "2026-09-14T15:00:00+00:00",
        "link": {"state": link, "sentence": "link says"},
        "quo": {"state": quo, "sentence": "quo says", "facts": facts},
    })


# ---------------- each check maps to the right colour ----------------

@node
def test_each_server_state_maps_to_its_colour():
    got = run_js("out(['ok','degraded','down','off','unknown'].map(s.stateTone))")
    assert got == ["green", "amber", "red", "grey", "grey"]


@node
def test_each_phone_state_maps_to_its_colour_and_says_something():
    got = run_js("""
        const all = ['ready','connecting','reconnecting','failed','off','elsewhere',
                     'no-operator','unavailable']
        out(Object.fromEntries(all.map((st) => {
          const c = s.phoneCheck({ status: st, operator: 'owen-x.com', error: null })
          return [st, [c.tone, c.sentence.length > 0, c.details]]
        })))
    """)
    assert got["ready"] == ["green", True, ["Registered as owen-x.com"]]
    assert got["connecting"][0] == "amber" and got["reconnecting"][0] == "amber"
    assert got["failed"][0] == "red"
    assert got["off"][0] == "grey", "a switched-off browser phone must be grey"
    for st, (_tone, says, _) in got.items():
        assert says, f"{st} has no sentence"
    assert got["elsewhere"][0] == "grey" and got["no-operator"][0] == "grey"


@node
def test_a_registration_error_is_shown_on_the_phone_row():
    got = run_js("""
        out(s.phoneCheck({ status: 'failed', operator: null,
                           error: 'Microphone blocked.' }).details)
    """)
    assert got == ["Microphone blocked."]


# ---------------- the dot shows the worst ----------------

@node
def test_the_dot_is_the_worst_of_the_three():
    got = run_js("""
        const phone = (st) => s.phoneCheck({ status: st, operator: null, error: null })
        const srv = (j) => s.serverChecks(j, { failed: false, offline: false, nowMs: NOW })
        const dot = (st, j) => s.overall([phone(st), ...srv(j)], 'idle').tone
        out({
          allGood: dot('ready', %s),
          quoStale: dot('ready', %s),
          linkDown: dot('ready', %s),
          redBeatsAmber: dot('connecting', %s),
          phoneOffRestGood: dot('off', %s),
          phoneOffButQuoAmber: dot('off', %s),
          phoneFailedRestGood: dot('failed', %s),
          notConfigured: dot('unavailable', %s),
        })
    """ % (server_json(), server_json(quo="degraded"), server_json(link="down", quo="unknown"),
           server_json(link="down"), server_json(), server_json(quo="degraded"),
           server_json(), server_json(link="off", quo="unknown")))
    assert got == {
        "allGood": "green",
        "quoStale": "amber",
        "linkDown": "red",
        "redBeatsAmber": "red",
        "phoneOffRestGood": "grey",
        "phoneOffButQuoAmber": "amber",
        "phoneFailedRestGood": "red",
        "notConfigured": "grey",
    }


@node
def test_a_call_in_progress_is_its_own_live_state():
    got = run_js("""
        const checks = [s.phoneCheck({ status: 'ready', operator: null, error: null }),
                        ...s.serverChecks(%s, { failed: false, offline: false, nowMs: NOW })]
        out([s.overall(checks, 'in-call'), s.overall(checks, 'idle')])
    """ % server_json(link="down"))
    assert got[0] == {"tone": "live", "headline": "On a call"}
    assert got[1]["tone"] == "red"


@node
def test_the_crm_itself_unreachable_or_offline_is_red():
    got = run_js("""
        const f = (o) => s.serverChecks(null, { nowMs: NOW, ...o })
          .map((c) => [c.key, c.tone, c.sentence])
        out({ failed: f({ failed: true, offline: false }),
              offline: f({ failed: false, offline: true }),
              loading: f({ failed: false, offline: false }) })
    """)
    assert got["failed"][0] == ["link", "red", "Can't reach the CRM server."]
    assert got["offline"][0] == ["link", "red", "This computer is offline."]
    assert got["loading"][0][1] == "grey", "a first load must not flash red"


# ---------------- the real endpoint feeds the dot ----------------

@node
def test_the_real_endpoint_answer_drives_the_colour(monkeypatch):
    """owen-main unreachable -> the endpoint's real 200 body -> a red dot; a stale tick -> amber."""
    import httpx
    from app import connection_status as cs

    monkeypatch.setenv("OWEN_BASE_URL", "http://owen.internal:8000")
    monkeypatch.setenv("OWEN_SOFTPHONE_KEY", "owen_sk_x")
    monkeypatch.delenv("CRM_LINK_BASE_URL", raising=False)

    def down(url, *, key, timeout):
        raise httpx.ConnectError("refused")

    unreachable = cs.build(down)

    healthy = {
        "checked_at": "2026-09-14T15:00:00+00:00", "database_readable": True,
        "crm_link": {"enabled": True, "telephony_enabled": True},
        "quo": {"mirror_enabled": True, "api_key_present": True, "poll_seconds": 300,
                "last_tick_at": "2026-09-14T14:40:00+00:00", "last_tick_ran": True,
                "backfill_days": 30, "backfill_completed_at": "2026-09-12T00:00:00+00:00",
                "webhook_enabled": True, "webhook_secret_configured": True,
                "last_webhook_at": None},
    }
    stale = cs.build(lambda url, *, key, timeout: (200, healthy))

    got = run_js("""
        const phone = s.phoneCheck({ status: 'ready', operator: null, error: null })
        const one = (j) => {
          const srv = s.serverChecks(j, { failed: false, offline: false, nowMs: NOW })
          return { dot: s.overall([phone, ...srv], 'idle').tone,
                   rows: srv.map((c) => [c.key, c.tone, c.sentence, c.details]) }
        }
        out({ unreachable: one(%s), stale: one(%s) })
    """ % (json.dumps(unreachable), json.dumps(stale)))
    assert got["unreachable"]["dot"] == "red"
    assert got["unreachable"]["rows"][0][2] == cs.LINK_UNREACHABLE
    assert got["stale"]["dot"] == "amber"
    quo = got["stale"]["rows"][1]
    assert quo[2] == cs.QUO_STALE
    assert quo[3] == ["Last check 20 min ago (overdue)", "30-day history imported 3 days ago",
                      "Webhook on, no delivery yet"]


@node
def test_the_quo_details_say_only_what_is_known():
    got = run_js("""
        const base = { mirror_enabled: true, poll_seconds: 300, tick_stale: false,
          last_tick_at: '2026-09-14T14:58:00Z', last_tick_ran: true, backfill_days: 30,
          backfill_completed_at: null, webhook_enabled: false, last_webhook_at: null }
        out([s.quoDetails(base, NOW), s.quoDetails({ ...base, mirror_enabled: false }, NOW),
             s.quoDetails(null, NOW),
             s.quoDetails({ ...base, last_tick_at: null, webhook_enabled: true,
                            last_webhook_at: '2026-09-14T14:59:40Z' }, NOW)])
    """)
    assert got[0] == [
        "Last check 2 min ago", "30-day history not finished importing", "Webhook off"]
    assert got[1] == [] and got[2] == []
    assert got[3] == ["No check recorded yet", "30-day history not finished importing",
                      "Webhook on, last delivery just now"]


# ---------------- Switch off / on still works ----------------

@node
def test_the_phone_row_offers_exactly_the_docks_actions():
    got = run_js("""
        const all = ['ready','connecting','reconnecting','failed','off','elsewhere',
                     'no-operator','unavailable']
        out(Object.fromEntries(all.map((st) => [st, s.phoneAction(st)])))
    """)
    assert got == {
        "ready": {"label": "Switch off", "online": False},
        "off": {"label": "Switch on", "online": True},
        "failed": {"label": "Try again", "online": True},
        "elsewhere": {"label": "Use this tab", "online": True},
        "connecting": None, "reconnecting": None, "no-operator": None, "unavailable": None,
    }


def test_the_card_button_calls_the_softphones_own_switch():
    ui = read("components", "StatusIndicator.tsx")
    assert "setOnline(action.online)" in ui, "the card's action no longer drives setOnline"
    assert "useSoftphoneContext()" in ui, "the card must read THE softphone, not start one"
    assert "useSoftphone()" not in ui
    hook = read("lib", "softphone.ts")
    switch = hook.split("const setOnline = useCallback(", 1)[1]
    switch = switch.split("[clearRetry, patch, teardown]", 1)[0]
    assert "rememberOnline(on)" in switch, "Switch off/on is no longer remembered per browser"
    assert "leaseRef.current?.claim()" in switch and "teardown()" in switch
    # Mute and Hang up moved with the in-call bar.
    assert "toggleMute" in ui and "hangup()" in ui


# ---------------- hover and focus open the card ----------------

def test_hover_and_keyboard_focus_both_open_the_card():
    ui = read("components", "StatusIndicator.tsx")
    assert "onMouseEnter={enter}" in ui and "onMouseLeave={leave}" in ui
    assert "onFocus={() => setFocused(true)}" in ui, "keyboard focus does not open the card"
    assert "const open = hovered || focused" in ui
    assert "{open && (" in ui
    # A focusable, labelled control, not a div with a mouse handler.
    assert re.search(r"<button\s+ref=\{buttonRef\}\s+type=\"button\"\s+aria-label=\{label\}", ui)
    assert "aria-expanded={open}" in ui
    assert "e.key === 'Escape'" in ui
    # Focus moving INTO the card must not close it (otherwise its buttons are unreachable).
    assert "contains(e.relatedTarget" in ui


# ---------------- placement ----------------

def test_the_dot_is_mounted_once_in_the_shell_inside_the_provider():
    app = read("App.tsx")
    assert app.count("<StatusIndicator />") == 1
    assert app.index("<SoftphoneProvider>") < app.index("<StatusIndicator />") \
        < app.index("</SoftphoneProvider>")
    assert app.index("LoginPage onSignedIn") < app.index("<StatusIndicator />"), (
        "the dot would poll the status endpoint from the login screen")
    ui = read("components", "StatusIndicator.tsx")
    assert "position: 'fixed', top: 9, right: 16" in ui, "the dot is not pinned top-right"
    for page in sorted((FRONTEND / "pages").glob("*.tsx")):
        assert "<StatusIndicator" not in page.read_text(encoding="utf-8"), (
            f"{page.name} mounts the status dot; it belongs in the app shell")


def test_the_dashboard_header_leaves_the_corner_to_the_dot():
    """The Dashboard is the one page whose own header puts controls in the top-right corner
    (the range picker and its ⋮ menu). Seen in a real browser: the dot sat on the ⋮."""
    dash = read("pages", "DashboardPage.tsx")
    assert '<div className="ml-auto flex items-center gap-2" style={{ marginRight: 44 }}>' in dash


def test_the_floating_card_is_gone_from_every_page():
    """The owner replaced it. Nothing in the app may still draw a fixed bottom-right card."""
    for path in sorted(FRONTEND.rglob("*.tsx")):
        src = path.read_text(encoding="utf-8")
        assert "aria-label=\"Softphone\"" not in src, f"{path.name} still draws the dock"
        assert not re.search(r"position:\s*'fixed',[^}]*\bbottom:\s*16", src, re.DOTALL), (
            f"{path.name} draws a fixed bottom-right card")
        code = re.sub(r"/\*.*?\*/", " ", src, flags=re.DOTALL)
        assert "Ready for calls" not in code, f"{path.name} still says 'Ready for calls'"
    softphone = read("components", "Softphone.tsx")
    assert "if (!ringing) return null" in softphone, (
        "Softphone.tsx renders something other than the incoming-call card")

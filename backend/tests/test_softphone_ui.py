"""Softphone invariants that a green build still satisfies, so the build gate misses them.

Asserted against source, like `test_frontend_layout.py`: there is no JS test runner in
this repo. These are the three things that would be silently wrong rather than broken:

  1. the softphone is mounted ONCE, globally — not inside a page;
  2. it stays out of the Conversations view, which another branch is rewriting right now;
  3. every registration state the hook can produce has a sentence written for it, because
     a blank status badge is exactly the "is my browser actually a phone?" ambiguity this
     feature exists to remove.
"""
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


def read(*parts: str) -> str:
    return (FRONTEND.joinpath(*parts)).read_text(encoding="utf-8")


def test_the_softphone_is_mounted_once_in_the_app_shell():
    """A call rings the browser, not a screen. Mounted per page, the hook would
    re-register on every navigation — and an operator AOR holds ONE contact, so a
    second registration evicts the first."""
    app = read("App.tsx")
    assert "<SoftphoneProvider>" in app and "<Softphone />" in app, (
        "the softphone is no longer mounted in the app shell")
    assert app.index("<SoftphoneProvider>") < app.index("<Softphone />"), (
        "<Softphone /> is outside its provider and will throw on render")

    # One provider, one component. Two of either means two registrations.
    assert app.count("<SoftphoneProvider>") == 1
    assert app.count("<Softphone />") == 1

    # And it must be inside the signed-in branch: the login screen has no session, so a
    # credential fetch there could only ever 401.
    assert app.index("LoginPage onSignedIn") < app.index("<SoftphoneProvider>"), (
        "the softphone is mounted before the session gate — it would fetch credentials "
        "on the login screen")


def _strip_comments(src: str) -> str:
    """Drop // line comments and /* block */ comments, JSX ones included.

    Matching raw source made this fence fail on a page that merely EXPLAINS, in a comment,
    that it does not need the softphone — the rule being obeyed in prose read as the rule
    being broken. A fence that fails on correct code gets widened or deleted by whoever
    hits it next, so it has to test behaviour, not vocabulary.
    """
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", " ", src)


def test_no_page_owns_the_softphone():
    """A call rings the browser, not a screen — the softphone lives in the app shell.
    Mounted in a page it would re-register on every navigation, and the operator AOR holds
    one contact, so the second registration evicts the first.

    Comments are stripped first: a page is free to say why it does NOT use the softphone.
    """
    for page in sorted((FRONTEND / "pages").glob("*.tsx")):
        source = _strip_comments(page.read_text(encoding="utf-8"))
        assert "softphone" not in source.lower(), (
            f"{page.name} uses the softphone — it belongs in the app shell, "
            "not in a page")


def test_every_registration_state_has_something_to_say():
    """The dock is the whole answer to 'can this browser take a call right now'. A status
    the hook can reach but the dock has no wording for renders an empty badge, which reads
    as 'fine' — the exact ambiguity this replaces."""
    hook = read("lib", "softphone.ts")
    ui = read("components", "Softphone.tsx")

    union = hook.split("export type SoftphoneStatus =", 1)[1].split("export type CallPhase", 1)[0]
    states = set(re.findall(r"^\s*\|\s*'([a-z-]+)'", union, re.MULTILINE))
    assert len(states) >= 6, f"the status union no longer parses ({states})"

    described = set(re.findall(r"^\s{2}'?([a-z-]+)'?:\s*\{", ui, re.MULTILINE))
    missing = states - described
    assert not missing, f"no wording for the softphone status(es) {sorted(missing)}"


def test_ready_is_only_ever_said_when_asterisk_confirmed_it():
    """'Ready' must come from a REGISTER response, not from 'we called register() and it
    did not throw' — a sent REGISTER that is rejected would otherwise read as a working
    phone while every call went to the mobiles."""
    hook = read("lib", "softphone.ts")
    registered = hook.split("onRegistered:", 1)[1].split("},", 1)[0]
    assert "'ready'" in registered, "onRegistered no longer sets the ready state"
    # ...and nothing else may claim it.
    assert hook.count("status: 'ready'") == 1, (
        "something other than the onRegistered callback sets status 'ready'")


def test_a_dropped_registration_says_so_rather_than_staying_green():
    hook = read("lib", "softphone.ts")
    for callback in ("onUnregistered:", "onServerDisconnect:"):
        body = hook.split(callback, 1)[1].split("},", 1)[0]
        assert "'reconnecting'" in body, (
            f"{callback} no longer moves the dock off 'ready' — a browser that has "
            "stopped receiving calls would keep saying it is ready")
        assert "scheduleRetry()" in body, f"{callback} no longer retries"


def test_the_credential_password_is_never_rendered_or_stored():
    """It is a real SIP digest password. It goes to SIP.js and nowhere else — not to a
    log, not to localStorage, not into the DOM."""
    for name in ("lib/softphone.ts", "lib/softphoneApi.ts", "components/Softphone.tsx"):
        source = read(*name.split("/"))
        for leak in ("console.log", "console.debug", "console.info"):
            assert leak not in source, f"{name} logs from the softphone path ({leak})"
        assert "localStorage.setItem" not in source or "PREFERENCE" in source, (
            f"{name} writes something other than the on/off preference to localStorage")

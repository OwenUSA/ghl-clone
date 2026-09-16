"""The CRM's own line comes from ONE place (2026-09-16).

The owner moved the CRM from `+19544829099` to `+19547758492` and unbound the old number
entirely. The line was hard-coded in five files — `lib/dialPad.ts`, `ConversationsPage.tsx`,
`AiConnectionsSettings.tsx`, `ai/AgentBuilder.tsx` and `crmlink.py` — so four screens went on
telling customers to reply to a number that no longer reaches anybody.

What is proved here:

  1. the ONE definition is `crmlink`, and it is the new number;
  2. `GET /api/connection-status` reports it to every signed-in user, and still leaks no
     key, no URL and no customer's number;
  3. the dialogs show the CONFIGURED line, not a constant;
  4. "it cannot text itself" follows the configured line — and stops following the retired
     one, which is somebody else's number now;
  5. **history keeps its own number.** An event sent on `+19544829099` still says so.

`lib/dialPad.ts` and `lib/ourLine.ts` import nothing, so node executes the shipped code
rather than a test pattern-matching the source.
"""
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from app import crmlink
from app.auth import mint_api_token
from app.connection_status import our_line
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Contact,
    Conversation,
    ConversationEvent,
    Direction,
    EventType,
    Role,
    User,
)
from fastapi.testclient import TestClient

NEW_LINE = "+19547758492"
RETIRED_LINE = "+19544829099"

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None,
    reason="node is not on PATH, so lib/dialPad.ts cannot be executed. CI's backend job "
           "installs it precisely so this file is never skipped there.")


def read(*parts: str) -> str:
    return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


def run_js(imports: dict, body: str):
    lines = ["import * as %s from %s" % (alias, json.dumps(FRONTEND.joinpath(*p).as_posix()))
             for alias, p in imports.items()]
    script = ("\n".join(lines) + "\nconst out = (v) => console.log('@@' + JSON.stringify(v))\n"
              + textwrap.dedent(body))
    proc = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-"],
        input=script, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    hit = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert hit, "the script printed no result:\n" + proc.stdout + proc.stderr
    return json.loads(hit[-1][2:])


@pytest.fixture()
def world():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    users, tokens = {}, {}
    for key, role in (("admin", Role.ADMIN), ("tech", Role.TECH)):
        u = User(email="%s@line.test" % key, name=key.title(), role=role)
        db.add(u)
        users[key] = u
    db.flush()
    jane = Contact(first_name="Jane", last_name="Doe", phone="(941) 555-0101")
    db.add(jane)
    db.flush()
    conv = Conversation(contact_id=jane.id)
    db.add(conv)
    db.flush()
    # A message sent BEFORE the line moved. It must keep the number it went out on.
    db.add(ConversationEvent(
        conversation_id=conv.id, type=EventType.SMS, direction=Direction.OUTBOUND,
        body="We can be there Tuesday", source_system="BulkVS",
        source_number=RETIRED_LINE))
    for key, u in users.items():
        plain, tok = mint_api_token(u, name=key)
        db.add(tok)
        tokens[key] = plain
    db.commit()
    ids = {"conv": conv.id}
    db.close()

    client = TestClient(app)

    def as_(who: str) -> dict:
        return {"Authorization": "Bearer " + tokens[who]}

    return client, ids, as_


# ---- 1. one definition -------------------------------------------------------------------


def test_the_one_definition_is_crmlink_and_it_is_the_new_number():
    assert crmlink.DEFAULT_FROM_NUMBER == NEW_LINE
    assert crmlink.current().from_number == NEW_LINE
    assert RETIRED_LINE in crmlink.RETIRED_FROM_NUMBERS, (
        "the old line is recorded as retired, so history can still be read")


def test_the_environment_overrides_it_and_everything_follows(monkeypatch):
    """A deployment that is told a different line must be right everywhere at once."""
    monkeypatch.setenv("CRM_LINK_FROM_NUMBER", "+13055550111")
    assert crmlink.current().from_number == "+13055550111"
    assert our_line() == "+13055550111", "the endpoint reports the configured line"


def test_no_python_module_hard_codes_the_line_any_more():
    app_dir = Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in app_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for number in (NEW_LINE, RETIRED_LINE):
            if number.lstrip("+") in text.replace("+", "") and path.name != "crmlink.py":
                offenders.append("%s (%s)" % (path.name, number))
    assert not offenders, (
        "the line is defined in app/crmlink.py and nowhere else: %s" % sorted(offenders))


# ---- 2. the endpoint ----------------------------------------------------------------------


def test_every_signed_in_user_is_told_the_line(world):
    client, _, as_ = world
    for who in ("admin", "tech"):
        got = client.get("/api/connection-status", headers=as_(who))
        assert got.status_code == 200, got.text
        assert got.json()["our_line"] == NEW_LINE, who
    assert client.get("/api/connection-status").status_code == 401, (
        "the line is for signed-in users, not the open internet")


def test_the_endpoint_still_names_no_key_no_url_and_no_customer(world, monkeypatch):
    """`our_line` is OUR published business number and is deliberately in the body. The
    property this endpoint has always had — that nothing else identifying survives — must
    not have been widened along with it."""
    client, _, as_ = world
    monkeypatch.setenv("CRM_LINK_BASE_URL", "http://callmon_app:8888")
    monkeypatch.setenv("CRM_LINK_API_KEY", "owen_sk_secret_value")
    from app import connection_status

    # owen-main is mocked at the one call that reaches the network. The conftest guard
    # fails any test that tries it for real — a configured link sends REAL texts.
    monkeypatch.setattr(connection_status, "get_json",
                        lambda url, key, timeout: (200, {"crm_link": {"enabled": True,
                                                                      "telephony_enabled": True}}))
    connection_status.clear_cache()
    body = json.dumps(client.get("/api/connection-status", headers=as_("admin")).json())
    for secret in ("owen_sk_secret_value", "callmon_app", "9415550101"):
        assert secret not in body, "the status body leaked %r" % secret
    connection_status.clear_cache()


def test_the_line_is_read_per_request_and_not_frozen_by_the_cache(world, monkeypatch):
    """The 30-second cache exists to stop N tabs becoming N requests to owen-main. Our own
    configuration costs nothing to look up, and a stale number on screen is the exact bug
    this whole change is about."""
    client, _, as_ = world
    first = client.get("/api/connection-status", headers=as_("admin")).json()["our_line"]
    monkeypatch.setenv("CRM_LINK_FROM_NUMBER", "+13055550111")
    second = client.get("/api/connection-status", headers=as_("admin")).json()["our_line"]
    assert first == NEW_LINE and second == "+13055550111"


# ---- 3 + 4. the browser -------------------------------------------------------------------


@node
def test_the_fallback_is_the_new_line_and_is_never_empty():
    got = run_js({"o": ("lib", "ourLine.ts")}, """
        out([o.FALLBACK_LINE, o.ourLine(), o.ourLine(null), o.ourLine(''), o.ourLine('  '),
             o.ourLine('+13055550111')])
    """)
    assert got[0] == NEW_LINE, "the fallback is the CURRENT line, so a cold render is right"
    assert got[1] == got[2] == got[3] == got[4] == NEW_LINE
    assert got[5] == "+13055550111", "the server's answer wins over the fallback"


@node
def test_is_our_line_follows_the_configured_number_not_a_constant():
    got = run_js({"o": ("lib", "ourLine.ts")}, """
        out([
          o.isOurLine('+19547758492'),            // the configured line, default
          o.isOurLine('(954) 775-8492'),          // however it is written
          o.isOurLine('9547758492'),
          o.isOurLine('+19544829099'),            // the RETIRED line: not ours any more
          o.isOurLine('+19544829099', '+19544829099'),  // ...unless it is what is configured
          o.isOurLine('+19547758492', '+13055550111'),  // ...and then the new one is not
          o.isOurLine(''), o.isOurLine(null), o.isOurLine('12345'),
        ])
    """)
    assert got[0] is True and got[1] is True and got[2] is True
    assert got[3] is False, (
        "the retired number is somebody else's line now — there is no reason to refuse it")
    assert got[4] is True and got[5] is False, "it follows configuration, both ways"
    assert got[6] is False and got[7] is False and got[8] is False


@node
def test_cannot_text_or_call_itself_follows_the_configured_line():
    got = run_js({"d": ("lib", "dialPad.ts")}, """
        const own = "That is this CRM's own number"
        out([
          (d.textProblem('(954) 775-8492') ?? '').includes(own),
          (d.dialProblem('(954) 775-8492') ?? '').includes(own),
          // The RETIRED line is textable again: it is not this CRM's number any more.
          d.textProblem('(954) 482-9099'),
          d.dialProblem('(954) 482-9099'),
          // ...and with the old line configured, it is refused and the new one is not.
          (d.textProblem('(954) 482-9099', '+19544829099') ?? '').includes(own),
          d.textProblem('(954) 775-8492', '+19544829099'),
          // A normal customer number is fine either way.
          d.textProblem('(941) 555-0101'),
          // The refusal also stops the number being normalised for a send.
          d.normaliseTextNumber('(954) 775-8492'),
          d.normaliseNumber('(954) 775-8492'),
          d.normaliseTextNumber('(954) 482-9099'),
        ])
    """)
    assert got[0] is True and got[1] is True, "the configured line refuses itself"
    assert got[2] is None and got[3] is None, "the retired line is an ordinary number now"
    assert got[4] is True and got[5] is None, "the check follows configuration"
    assert got[6] is None
    assert got[7] is None and got[8] is None, "a refused number is never normalised"
    assert got[9] == "+19544829099"


def test_the_server_refuses_its_own_configured_line_too(world, monkeypatch):
    """The browser's rule is the explanation; this is the gate. They must agree."""
    client, _, as_ = world
    out = client.post("/api/messages/new", json={"number": "(954) 775-8492", "body": "hi"},
                      headers=as_("admin")).json()
    assert out["recorded"] is False and "cannot text itself" in out["reason"]

    # The retired line is an ordinary number to this CRM now.
    ok = client.post("/api/messages/new", json={"number": "(954) 482-9099", "body": "hi"},
                     headers=as_("admin")).json()
    assert "cannot text itself" not in (ok.get("reason") or "")

    monkeypatch.setenv("CRM_LINK_FROM_NUMBER", RETIRED_LINE)
    flipped = client.post("/api/messages/new",
                          json={"number": "(954) 482-9099", "body": "hi"},
                          headers=as_("admin")).json()
    assert flipped["recorded"] is False and "cannot text itself" in flipped["reason"], (
        "the server's refusal follows CRM_LINK_FROM_NUMBER")


def test_the_dialogs_read_the_line_from_the_server():
    """No runner for the React components, so this is asserted against source — and what it
    asserts is that the CONSTANT is gone from them, which is the whole bug."""
    for where, parts in (("New message", ("components", "NewMessageDialog.tsx")),
                         ("Call a number", ("components", "CallNumberDialog.tsx"))):
        src = read(*parts)
        assert "useOurLine()" in src, "%s does not read the configured line" % where
        assert "formatPhone(line)" in src, "%s does not SHOW the configured line" % where
        assert "CALLING_FROM" not in src, (
            "%s still imports the hard-coded constant" % where)
        assert "9544829099" not in src and "482-9099" not in src, (
            "%s still has the retired number in it" % where)


def test_the_thread_banner_names_the_line_the_reply_will_actually_leave_on():
    """It used to read the number off the newest outbound event, falling back to a
    hard-coded DID. Both are wrong once the line moves: an old message's number is not the
    number the next one goes out on, and the banner's whole job is to name what the customer
    will see."""
    src = read("pages", "ConversationsPage.tsx")
    assert "const ourNumber = useOurLine()" in src
    assert "formatPhone(ourNumber)" in src
    assert "BULKVS_LINE" not in src, "the hard-coded DID is gone"
    assert "replyLine" not in src, "the newest-outbound-event guess is gone"


def test_the_ai_copy_names_the_configured_line():
    for where, parts in (("AI Connections", ("components", "AiConnectionsSettings.tsx")),
                         ("Agent builder", ("components", "ai", "AgentBuilder.tsx"))):
        src = read(*parts)
        assert "useOurLine()" in src, "%s does not read the configured line" % where
        assert "formatPhone(ourNumber)" in src, "%s does not show it" % where
        assert "482-9099" not in src, "%s still names the retired number" % where


def test_no_frontend_file_hard_codes_a_line_except_the_documented_fallbacks():
    allowed = {"ourLine.ts", "dialPad.ts"}
    offenders = []
    for path in FRONTEND.rglob("*.ts*"):
        if path.name in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if "9544829099" in text or "9547758492" in text or "482-9099" in text:
            offenders.append(path.name)
    assert not offenders, (
        "the line belongs to the server; these still hard-code one: %s" % sorted(offenders))


# ---- 5. history keeps its own number --------------------------------------------------------


def test_a_message_sent_on_the_old_line_still_says_so(world):
    """The one thing that must NOT follow the configuration. A number on a thread is a fact
    about when something happened."""
    client, ids, as_ = world
    rows = client.get("/api/conversations/%d/events" % ids["conv"],
                      headers=as_("admin")).json()
    old = [e for e in rows if e["body"] == "We can be there Tuesday"]
    assert len(old) == 1
    assert old[0]["source_number"] == RETIRED_LINE, (
        "an old message was relabelled with the new line")
    assert old[0]["source_system"] == "BulkVS", "or had its label dropped"


def test_a_message_sent_now_carries_the_new_line(world, monkeypatch):
    client, ids, as_ = world
    monkeypatch.setenv("CRM_LINK_BASE_URL", "http://callmon_app:8888")
    monkeypatch.setenv("CRM_LINK_API_KEY", "owen_sk_test")
    from app import crmlink as link

    sent = {}

    def fake_post(url, **kw):
        sent.update(kw.get("json") or {})

        class R:
            status_code = 200

            def json(self):
                return {"ok": True, "message_id": "m-1", "status": "queued"}
        return R()

    monkeypatch.setattr(link.httpx, "post", fake_post)
    client.post("/api/conversations/%d/messages" % ids["conv"],
                json={"body": "on our way", "type": "SMS"}, headers=as_("admin"))
    assert sent["from_number"] == NEW_LINE, "the send went out on the configured line"

    rows = client.get("/api/conversations/%d/events" % ids["conv"],
                      headers=as_("admin")).json()
    fresh = next(e for e in rows if e["body"] == "on our way")
    assert fresh["source_number"] == NEW_LINE
    # And the old one is untouched, on the same thread, beside it.
    assert [e for e in rows if e["source_number"] == RETIRED_LINE], (
        "the two lines coexist on one thread, each labelled with its own")


@node
def test_the_source_chip_reads_the_events_number_and_never_the_configured_one():
    """The chip under each bubble is what labels history. It takes the event's own
    `source_number`; nothing about it consults the configured line."""
    src = read("pages", "ConversationsPage.tsx")
    chip = src.split("export function SourceChip(", 1)[1].split("\nfunction ", 1)[0]
    assert "e.source_number" in chip
    assert "ourNumber" not in chip and "useOurLine" not in chip, (
        "the source chip must label an event with the number it was sent on")

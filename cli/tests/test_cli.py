"""CLI contract tests.

Run against a mock transport, not a live server, so they work offline and in CI.

What is being pinned down here is the CLI's contract with its main caller, which is
an agent: exit codes, --json purity, and the fact that a guarded command does not
reach the network without --yes.
"""
import json

import httpx
import pytest
from ghl_cli import config, output
from ghl_cli.main import _hoist_json_flag, app
from typer.testing import CliRunner

runner = CliRunner()

PIPELINES = [{"id": 1, "name": "AHS", "stages": [
    {"id": 1, "name": "New Lead", "position": 0, "count": 3, "value_cents": 0},
    # The measured pipeline really does contain two stages with this name.
    {"id": 7, "name": "Call Back", "position": 6, "count": 0, "value_cents": 0},
    {"id": 8, "name": "Call Back", "position": 7, "count": 2, "value_cents": 0},
]}]

CONTACTS = {"items": [
    {"id": 5, "name": "Jane Doe", "first_name": "Jane", "last_name": "Doe",
     "phone": "(941) 555-0199", "email": "jane@x.test", "business_name": None,
     "created_at": "2026-01-01T00:00:00Z", "tags": [], "last_activity": None},
    {"id": 6, "name": "Jane Dawson", "first_name": "Jane", "last_name": "Dawson",
     "phone": "(941) 555-0200", "email": "jd@x.test", "business_name": None,
     "created_at": "2026-01-01T00:00:00Z", "tags": [], "last_activity": None},
], "total": 2, "page": 1, "page_size": 20, "pages": 1}


class Recorder:
    """Mock backend. Records every request so a test can assert none was made."""

    def __init__(self, routes=None, fail=None):
        self.calls = []
        self.routes = routes or {}
        self.fail = fail

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        if self.fail:
            raise self.fail
        key = (request.method, request.url.path)
        if key in self.routes:
            status, body = self.routes[key]
            return httpx.Response(status, json=body)
        return httpx.Response(404, json={"detail": "no mock for %s %s" % key})


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """Never touch the developer's real config, and always look authenticated."""
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    monkeypatch.setenv("GHL_API_TOKEN", "ghl_pat_test")
    monkeypatch.setenv("GHL_API_URL", "http://testbackend")
    monkeypatch.delenv("GHL_ASSUME_YES", raising=False)
    output.set_json(False)
    yield


def wire(monkeypatch, recorder: Recorder):
    from ghl_cli import client as client_mod

    real_init = client_mod.Client.__init__

    def patched(self, *a, **kw):
        real_init(self, *a, **kw)
        self._http = httpx.Client(
            base_url=self.base_url,
            transport=httpx.MockTransport(recorder.handler))

    monkeypatch.setattr(client_mod.Client, "__init__", patched)
    return recorder


# ---------------- --json ----------------

def test_json_flag_is_accepted_after_the_subcommand(monkeypatch):
    """`ghl contacts list --json` is what anyone writes first; Typer would
    otherwise only accept it before the subcommand."""
    wire(monkeypatch, Recorder({("GET", "/api/contacts"): (200, CONTACTS)}))
    r = runner.invoke(app, _hoist_json_flag(["contacts", "list", "--json"]))
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["total"] == 2


def test_json_output_is_parseable_with_nothing_else_on_stdout(monkeypatch):
    wire(monkeypatch, Recorder({("GET", "/api/contacts"): (200, CONTACTS)}))
    r = runner.invoke(app, _hoist_json_flag(["contacts", "list", "--json"]))
    parsed = json.loads(r.stdout)          # would raise on any stray banner
    assert [c["name"] for c in parsed["items"]] == ["Jane Doe", "Jane Dawson"]


def test_hoisting_leaves_anything_after_a_double_dash_alone():
    assert _hoist_json_flag(["msg", "send", "--", "--json"]) == \
        ["msg", "send", "--", "--json"]
    assert _hoist_json_flag(["contacts", "list", "--json"])[0] == "--json"


# ---------------- exit codes ----------------

def test_backend_unreachable_exits_6_and_names_the_fix(monkeypatch):
    wire(monkeypatch, Recorder(fail=httpx.ConnectError("refused")))
    r = runner.invoke(app, ["contacts", "list"])
    assert r.exit_code == 6
    assert "uvicorn app.main:app" in r.output


def test_unauthenticated_exits_3_and_says_how_to_fix_it(monkeypatch):
    wire(monkeypatch, Recorder(
        {("GET", "/api/contacts"): (401, {"detail": "authentication required"})}))
    r = runner.invoke(app, ["contacts", "list"])
    assert r.exit_code == 3
    assert "ghl auth login" in r.output


def test_forbidden_exits_7_with_the_reason(monkeypatch):
    wire(monkeypatch, Recorder(
        {("GET", "/api/contacts"): (403, {"detail": "role TECH may not do this"})}))
    r = runner.invoke(app, ["contacts", "list"])
    assert r.exit_code == 7
    assert "TECH" in r.output


def test_a_name_matching_nothing_exits_4(monkeypatch):
    wire(monkeypatch, Recorder({
        ("GET", "/api/contacts"): (200, {"items": [], "total": 0, "page": 1,
                                         "page_size": 20, "pages": 1})}))
    r = runner.invoke(app, ["contacts", "show", "nobody"])
    assert r.exit_code == 4


def test_a_conflict_exits_8(monkeypatch):
    wire(monkeypatch, Recorder({
        ("GET", "/api/contacts"): (200, CONTACTS),
        ("DELETE", "/api/contacts/5"): (409, {"detail": "contact has 1 opportunity"}),
    }))
    r = runner.invoke(app, ["contacts", "delete", "Jane Doe", "--yes"])
    assert r.exit_code == 8


# ---------------- ambiguity ----------------

def test_an_ambiguous_name_exits_5_and_lists_every_candidate(monkeypatch):
    """Never silently pick the first. The real pipeline has two "Call Back"
    stages, so an agent must be told both ids."""
    wire(monkeypatch, Recorder({("GET", "/api/pipelines"): (200, PIPELINES)}))
    r = runner.invoke(app, _hoist_json_flag(
        ["opps", "create", "-t", "x", "--stage", "Call Back", "--json"]))
    assert r.exit_code == 5
    body = json.loads(r.output)
    assert body["code"] == "ambiguous"
    assert len(body["candidates"]) == 2
    assert any("id 7" in c for c in body["candidates"])
    assert any("id 8" in c for c in body["candidates"])


def test_an_ambiguous_contact_name_exits_5(monkeypatch):
    wire(monkeypatch, Recorder({("GET", "/api/contacts"): (200, CONTACTS)}))
    r = runner.invoke(app, ["contacts", "show", "jane"])
    assert r.exit_code == 5
    assert "Jane Doe" in r.output and "Jane Dawson" in r.output


def test_an_exact_match_wins_over_a_partial_one(monkeypatch):
    """"Jane Doe" is a prefix of nothing else here, but "Jane" matches both —
    an exact hit must not be reported as ambiguous."""
    wire(monkeypatch, Recorder({
        ("GET", "/api/contacts"): (200, CONTACTS),
        ("GET", "/api/contacts/5"): (200, {**CONTACTS["items"][0], "tags": []}),
    }))
    r = runner.invoke(app, ["contacts", "show", "Jane Doe"])
    assert r.exit_code == 0, r.output


def test_a_numeric_reference_is_used_as_an_id(monkeypatch):
    rec = wire(monkeypatch, Recorder({
        ("GET", "/api/contacts/5"): (200, {**CONTACTS["items"][0], "tags": []})}))
    r = runner.invoke(app, ["contacts", "show", "5"])
    assert r.exit_code == 0, r.output
    assert ("GET", "/api/contacts") not in rec.calls, "should not have searched"


# ---------------- the --yes guard ----------------

def test_sending_without_yes_never_reaches_the_network(monkeypatch):
    """The important half of the guard: not just a non-zero exit, but no request.

    Claude Code runs this through Bash with no usable stdin, so the guard must
    refuse deterministically rather than prompt into the void.
    """
    rec = wire(monkeypatch, Recorder({("GET", "/api/contacts"): (200, CONTACTS)}))
    r = runner.invoke(app, ["msg", "send", "-c", "Jane Doe", "-b", "hello"])
    assert r.exit_code == 2
    assert ("POST", "/api/contacts/5/messages") not in rec.calls


def test_the_yes_flag_lets_it_through(monkeypatch):
    rec = wire(monkeypatch, Recorder({
        ("GET", "/api/contacts"): (200, CONTACTS),
        ("POST", "/api/contacts/5/messages"): (201, {
            "suppressed": False, "reason": "sent", "id": 1,
            "conversation_id": 2, "delivery_status": "LOGGED_ONLY"}),
    }))
    r = runner.invoke(app, ["msg", "send", "-c", "Jane Doe", "-b", "hi", "--yes"])
    assert r.exit_code == 0, r.output
    assert ("POST", "/api/contacts/5/messages") in rec.calls


def test_a_note_needs_no_confirmation(monkeypatch):
    """Notes are internal, so they are not guarded — only customer-facing sends."""
    rec = wire(monkeypatch, Recorder({
        ("GET", "/api/contacts"): (200, CONTACTS),
        ("POST", "/api/contacts/5/messages"): (201, {
            "suppressed": False, "reason": "recorded", "id": 1,
            "conversation_id": 2, "delivery_status": None}),
    }))
    r = runner.invoke(app, ["msg", "note", "-c", "Jane Doe", "-b", "called back"])
    assert r.exit_code == 0, r.output
    assert ("POST", "/api/contacts/5/messages") in rec.calls


def test_a_suppressed_send_is_reported_not_silently_swallowed(monkeypatch):
    wire(monkeypatch, Recorder({
        ("GET", "/api/contacts"): (200, CONTACTS),
        ("POST", "/api/contacts/5/messages"): (201, {
            "suppressed": True, "reason": "suppressed: contact is on DND",
            "id": None, "conversation_id": None}),
    }))
    r = runner.invoke(app, ["msg", "send", "-c", "Jane Doe", "-b", "hi", "--yes"])
    assert r.exit_code == 0
    assert "DND" in r.output


# ---------------- helpers ----------------

def test_relative_time_windows(monkeypatch):
    from datetime import datetime

    from ghl_cli.cmd._common import parse_since
    assert parse_since(None) is None
    assert parse_since("2026-01-01") == "2026-01-01"
    seven = parse_since("7d")
    assert datetime.fromisoformat(seven)          # parses as a real timestamp
    assert parse_since("24h") != parse_since("7d")


def test_an_expired_token_is_shown_as_expired_not_active(monkeypatch):
    """Found by review: the status was computed by comparing expires_at to
    created_at, which is never in the past for a valid token, so a token that had
    already expired still displayed as "active"."""
    past = "2020-01-01T00:00:00+00:00"
    future = "2099-01-01T00:00:00+00:00"
    wire(monkeypatch, Recorder({("GET", "/api/auth/tokens"): (200, [
        {"id": 1, "name": "stale", "prefix": "ghl_pat_aaaa", "scopes": "",
         "created_at": "2019-01-01T00:00:00+00:00", "last_used_at": None,
         "expires_at": past, "revoked_at": None},
        {"id": 2, "name": "live", "prefix": "ghl_pat_bbbb", "scopes": "",
         "created_at": "2019-01-01T00:00:00+00:00", "last_used_at": None,
         "expires_at": future, "revoked_at": None},
        {"id": 3, "name": "gone", "prefix": "ghl_pat_cccc", "scopes": "",
         "created_at": "2019-01-01T00:00:00+00:00", "last_used_at": None,
         "expires_at": future, "revoked_at": past},
    ])}))
    r = runner.invoke(app, _hoist_json_flag(["auth", "token", "list", "--json"]))
    assert r.exit_code == 0, r.output
    got = {row["name"]: row["status"] for row in json.loads(r.stdout)}
    assert got == {"stale": "expired", "live": "active", "gone": "revoked"}


def test_money_formatting_does_not_crash():
    """`money()` shipped with an invalid format string and raised ValueError the
    moment anything called it. It was unreferenced, which is exactly why nothing
    caught it."""
    from ghl_cli.output import money
    assert money(125050) == "$1250.50"
    assert money(0) == "$0.00"
    assert money(None) == "-"


def test_money_parsing():
    from ghl_cli.cmd._common import money_to_cents
    assert money_to_cents("1250") == 125000
    assert money_to_cents("1250.50") == 125050
    assert money_to_cents("$1,250.50") == 125050
    assert money_to_cents(None) is None


def test_a_corrupt_config_reads_as_logged_out_rather_than_crashing(tmp_path,
                                                                  monkeypatch):
    """Otherwise every command breaks, including the login that would fix it."""
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    (tmp_path / "config.json").write_text("{not json", encoding="utf-8")
    assert config.load() == {}


def test_help_lists_every_command_group():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    for group in ("auth", "contacts", "convos", "msg", "calls", "opps", "appts",
                  "reports", "users", "jobs"):
        assert group in r.output


def test_exit_codes_are_documented_in_the_cli_itself():
    """An agent should be able to learn the codes without reading source."""
    r = runner.invoke(app, _hoist_json_flag(["exit-codes", "--json"]))
    assert r.exit_code == 0
    codes = {row["code"] for row in json.loads(r.stdout)}
    assert {0, 1, 2, 3, 4, 5, 6, 7, 8} <= codes

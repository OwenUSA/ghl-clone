"""The guard is the only thing standing between a script bug and a live production
account, so it is tested offline against a fake page before it is ever pointed at GHL.

Run:  cd capture/build && uv run pytest test_guard.py -q

Per the repo's testing standard (CLAUDE.md): assert behaviour, not status codes. So
these check that a refusal actually PREVENTS the click and LOGS it, not merely that an
exception type was raised.
"""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import guard
from guard import Guard, Refused

# Real labels lifted from GHL's own UI. "Call: +1786..." and "Delete Conversation" are
# the two that sit 40px apart in the thread header (DECISIONS.md) - the exact pair that
# motivated label-resolution in the first place.
MUST_REFUSE = [
    "Delete", "Delete Field", "Delete Conversation", "Remove Folder", "Archive",
    "Send SMS", "Send Test Email", "Call: +17865551234", "Publish",
    "Save and Publish", "Export", "Import", "Test Workflow", "Execute",
    "Merge", "Deactivate", "Buy Number", "Sign out", "Move to trash",
    # Money / metered. GHL bills per SMS, per minute, and meters AI separately;
    # the Workflows empty state on this account puts an AI builder immediately
    # beside "Create workflow".
    "Build with AI", "AI Assistant", "Generate with AI", "Upgrade plan",
    "Add credits", "Buy Credits", "Enable Conversation AI", "Subscribe",
    "Register A2P", "Checkout", "Add-on", "Enable Premium Actions",
]

# Measured from the live panels, not assumed. GHL says "Create field"/"Create folder",
# and the submit button in the create panel is "Create custom field".
MUST_ALLOW = [
    "Save", "Update", "Cancel", "Create field", "Create folder",
    "Create custom field", "Create Workflow", "Save Action",
]


class FakePage:
    """Resolves every label to itself and records whether a click ever landed."""

    url = "https://app.gohighlevel.com/v2/location/FAKE/settings/custom_fields"

    def __init__(self, resolve=None):
        self.clicked = []
        self._resolve = resolve

    def evaluate(self, script, arg=None):
        if "window.__target.click()" in script:
            self.clicked.append(script)
            return None
        if self._resolve is not None:
            return self._resolve(arg)
        return {"found": True, "count": 1, "label": arg,
                "x": 10, "y": 20, "tag": "button"}

    def screenshot(self, path=None):
        pathlib.Path(path).write_bytes(b"")

    def wait_for_timeout(self, ms):
        pass

    def fill(self, sel, val):
        self.clicked.append(("fill", sel, val))


@pytest.fixture(autouse=True)
def _isolate_audit(tmp_path, monkeypatch):
    """Never write to the real captures/checklists/audit.jsonl from a test."""
    monkeypatch.setattr(guard, "SHOTS", tmp_path)
    monkeypatch.setattr(guard, "AUDIT", tmp_path / "audit.jsonl")
    return tmp_path


def _audit(tmp_path):
    p = tmp_path / "audit.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line]


@pytest.mark.parametrize("label", MUST_REFUSE)
def test_dangerous_label_is_refused_and_never_clicked(label, _isolate_audit):
    page = FakePage()
    g = Guard(page, dry_run=False, step="t")          # NOT dry-run: the real path
    with pytest.raises(Refused):
        g.click(label)
    assert page.clicked == [], "%r reached the page" % label
    assert g.clicks == 0
    trail = _audit(_isolate_audit)
    assert trail and trail[-1]["result"] == "REFUSED", "refusal was not audited"


@pytest.mark.parametrize("label", MUST_ALLOW)
def test_permitted_label_is_clicked(label, _isolate_audit):
    page = FakePage()
    g = Guard(page, dry_run=False, step="t")
    g.click(label)
    assert len(page.clicked) == 1, "%r did not reach the page" % label
    assert g.clicks == 1
    assert _audit(_isolate_audit)[-1]["result"] == "CLICKED"


def test_dry_run_never_clicks(_isolate_audit):
    page = FakePage()
    g = Guard(page, dry_run=True, step="t")
    for label in MUST_ALLOW:
        g.click(label)
    assert page.clicked == [], "dry run reached the page"
    assert g.clicks == 0
    assert {r["result"] for r in _audit(_isolate_audit)} == {"DRY_RUN"}


def test_ambiguous_label_is_refused_not_guessed():
    """Two matches must error, mirroring the ghl CLI's exit-5 rule: never guess."""
    page = FakePage(resolve=lambda a: {"found": False, "count": 2,
                                       "ambiguous": True, "at": [[1, 2], [3, 4]]})
    g = Guard(page, dry_run=False, step="t")
    with pytest.raises(Refused, match="ambiguous"):
        g.click("Save")
    assert page.clicked == []


def test_missing_label_is_refused():
    page = FakePage(resolve=lambda a: {"found": False, "count": 0})
    g = Guard(page, dry_run=False, step="t")
    with pytest.raises(Refused, match="not found"):
        g.click("Save")
    assert page.clicked == []


def test_resolver_returning_a_dangerous_element_is_caught():
    """Gate 2. We ask for 'Save'; a resolver bug hands back the Delete button.
    The requested label passes both gates, so only the re-read can catch this."""
    page = FakePage(resolve=lambda a: {"found": True, "count": 1,
                                       "label": "Delete Conversation",
                                       "x": 1, "y": 2, "tag": "button"})
    g = Guard(page, dry_run=False, step="t")
    with pytest.raises(Refused, match="resolved element matches DENY"):
        g.click("Save")
    assert page.clicked == [], "a Delete button was clicked while asking for Save"


def test_disabled_submit_is_refused_not_clicked():
    """GHL disables 'Create' until the form is valid. Clicking it saves nothing, so
    a run that ignored this would report success having created no record."""
    page = FakePage(resolve=lambda a: {"found": True, "count": 1, "label": a,
                                       "disabled": True, "x": 1, "y": 2,
                                       "tag": "button"})
    g = Guard(page, dry_run=False, step="t")
    with pytest.raises(Refused, match="DISABLED"):
        g.click("Create")
    assert page.clicked == []


def test_deep_button_resolves_by_inner_text():
    """The folder drawer's 'Create' button wraps its label 5 nodes deep. A
    leaf-only rule skipped it entirely and reported 'not found'."""
    page = FakePage(resolve=lambda a: {"found": True, "count": 1, "label": a,
                                       "disabled": False, "x": 1, "y": 2,
                                       "tag": "button"})
    g = Guard(page, dry_run=False, step="t")
    g.click("Create")
    assert len(page.clicked) == 1


def test_unknown_verb_is_refused_even_though_it_is_harmless():
    """ALLOW is a closed set. 'Rename' is not destructive, but it is also not a verb
    this migration needs - adding it must be a deliberate diff, not a surprise."""
    page = FakePage()
    g = Guard(page, dry_run=False, step="t")
    with pytest.raises(Refused, match="not in ALLOW"):
        g.click("Rename")
    assert page.clicked == []

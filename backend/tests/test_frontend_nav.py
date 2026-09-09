"""What the app shell offers, and where a retired link goes.

Same idiom as test_frontend_layout.py — asserted against source, because there is
no JS test runner here. These parse the nav out of Sidebar.tsx and the boot-time
path handling out of App.tsx rather than grepping for strings, so a rename fails
loudly and asks to be retargeted instead of passing on a coincidence.

Six items were removed from the sidebar on 2026-09-09 (DECISIONS.md): Launchpad,
Marketing, Sites, Memberships, Reputation, App Marketplace. Four out-of-scope
items were deliberately KEPT and are still dimmed, so "we removed the dead ones"
and "we removed every dimmed one" are different states and the difference is the
whole point of this file.
"""
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"

REMOVED = ["Launchpad", "Marketing", "Sites", "Memberships", "Reputation",
           "App Marketplace"]

# Navigable, in rendered order. Settings is a separate button pinned to the
# bottom, so it is not in this list.
LIVE = ["Dashboard", "Conversations", "Calendars", "Contacts", "Opportunities",
        "Payments", "Reporting"]

# Out of scope, kept visible on purpose, rendered dimmed and unclickable.
DIMMED = ["AI Agents", "Automation", "Media Storage"]


def _sidebar():
    return (FRONTEND / "components" / "Sidebar.tsx").read_text(encoding="utf-8")


def _labels(source, const):
    """The labels of one nav array, in the order the sidebar maps over them."""
    # Split on `= [` and then the closing `]` at column 0: SECONDARY's type
    # annotation carries its own `[]`, which a naive split would stop at.
    body = source.split(f"const {const}", 1)[1].split("= [", 1)[1].split("\n]", 1)[0]
    # A commented-out entry is not a rendered item. Without this, deleting a row
    # by prefixing it with `//` reads as still present and the test says nothing.
    body = "\n".join(
        line for line in body.splitlines() if not line.strip().startswith("//"))
    return re.findall(r"label: '([^']+)'", body)


def test_the_sidebar_renders_exactly_the_expected_items():
    """The nav is the product's table of contents; an extra row is a promise."""
    source = _sidebar()
    assert _labels(source, "PRIMARY") == LIVE, "the navigable sidebar items changed"
    assert _labels(source, "SECONDARY") == DIMMED, "the dimmed sidebar items changed"

    # Both arrays are actually rendered — a correct list that nothing maps over
    # would satisfy the assertions above and ship an empty sidebar.
    nav = source.split("<nav", 1)[1].split("</nav>", 1)[0]
    assert "PRIMARY.map(" in nav and "SECONDARY.map(" in nav, (
        "an array is no longer rendered, so the list above describes nothing")


def test_the_six_removed_items_are_gone_from_the_source_entirely():
    """Gone from the DOM, not hidden, not dimmed, and not commented out.

    Searching the whole file rather than the nav arrays is deliberate: a
    commented-out entry is exactly the thing this is meant to catch, and a
    comment is invisible to the parse in the test above.
    """
    source = _sidebar()
    # The prose at the top of SECONDARY names them as removed; that is a record,
    # not a nav entry. Everything below it is code.
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("//"))
    for label in REMOVED:
        assert label not in code, f"{label!r} is still in the sidebar"

    app = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    assert "LaunchpadPage" not in app, "App.tsx still routes to the Launchpad page"
    assert not (FRONTEND / "pages" / "LaunchpadPage.tsx").exists(), (
        "LaunchpadPage.tsx is back; nothing imports it")


def test_the_four_kept_out_of_scope_items_are_still_present_and_dimmed():
    """The owner kept these four. Removing them is a decision, not a tidy-up.

    Payments is the odd one: it sits in PRIMARY as a real button that lands on a
    "not built yet" placeholder, so it is kept-but-unbuilt rather than dimmed.
    The other three are dimmed rows with no click handler.
    """
    source = _sidebar()
    assert "Payments" in _labels(source, "PRIMARY"), "Payments was removed"
    for label in DIMMED:
        assert label in _labels(source, "SECONDARY"), f"{label} was removed"

    dimmed_row = source.split("SECONDARY.map(", 1)[1].split("))}", 1)[0]
    assert "onClick" not in dimmed_row, "a dimmed item became clickable"
    assert "rgba(255,255,255,0.35)" in dimmed_row, "the dimmed items are no longer dimmed"
    assert 'title="Out of scope for v1"' in dimmed_row, (
        "the dimmed items lost the tooltip that explains why they do nothing")


def test_the_shell_keeps_settings_and_the_account_header():
    """Neither is in the nav arrays, so the parse above cannot speak for them."""
    source = _sidebar()
    assert "onNavigate('settings')" in source, "Settings is no longer a target"
    assert "Dream Team Roofing" in source and "Bradenton, FL" in source, (
        "the account/workspace header is gone")


def test_launchpad_redirects_to_dashboard_rather_than_dead_ending():
    """An old bookmark to /launchpad must land somewhere real.

    There is no router (DECISIONS.md) and nginx serves index.html for every path,
    so nothing 404s — but without this, /launchpad would silently render whatever
    the default view is while leaving a path that no longer exists in the address
    bar. The redirect makes that explicit and survives the router landing.
    """
    app = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    retired = app.split("const RETIRED_PATHS", 1)[1].split("}", 1)[0]
    assert re.search(r"'/launchpad':\s*'dashboard'", retired), (
        "/launchpad no longer resolves to Dashboard")

    resolver = app.split("function initialView()", 1)[1].split("\n}\n", 1)[0]
    assert "window.location.pathname" in resolver, "the path is never read"
    assert "RETIRED_PATHS[" in resolver, "the redirect table is never consulted"
    assert "replaceState" in resolver, (
        "the retired path stays in the address bar, so a refresh redirects again")
    assert "pushState" not in resolver, (
        "pushing the redirect makes Back bounce straight off it")


def test_the_app_lands_on_dashboard():
    """Was Contacts — a 268-row table — and the owner moved it to Dashboard."""
    app = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    assert re.search(r"const DEFAULT_VIEW = 'dashboard'", app), (
        "the app no longer opens on Dashboard")
    assert "useState(initialView)" in app, (
        "the initial view is not resolved from the path, so /launchpad is ignored")
    # ...and Dashboard must be a view that renders, not a placeholder.
    assert "active === 'dashboard' ? (\n        <DashboardPage />" in app, (
        "Dashboard is the landing view but does not render DashboardPage")

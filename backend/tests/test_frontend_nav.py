"""What the app shell offers, and where a retired link goes.

Same idiom as test_frontend_layout.py — asserted against source, because there is
no JS test runner here. These parse the nav out of Sidebar.tsx and the boot-time
path handling out of App.tsx rather than grepping for strings, so a rename fails
loudly and asks to be retargeted instead of passing on a coincidence.

Six items were removed from the sidebar on 2026-09-09 (DECISIONS.md): Launchpad,
Marketing, Sites, Memberships, Reputation, App Marketplace. Payments followed on
2026-09-10. Three out-of-scope items were deliberately KEPT and are still dimmed,
so "we removed the dead ones" and "we removed every dimmed one" are different
states and the difference is the whole point of this file.
"""
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"

REMOVED = ["Launchpad", "Marketing", "Sites", "Memberships", "Reputation",
           "App Marketplace", "Payments"]

# Navigable, in rendered order. Settings is a separate button pinned to the
# bottom, so it is not in this list.
LIVE = ["Dashboard", "Conversations", "Calendars", "Contacts", "Opportunities",
        "Reporting"]

# Out of scope, kept visible on purpose, rendered dimmed and unclickable.
DIMMED = ["AI Agents", "Automation", "Media Storage"]


def _sidebar():
    return (FRONTEND / "components" / "Sidebar.tsx").read_text(encoding="utf-8")


def _code(source):
    """The source with whole-line `//` comments dropped.

    A commented-out entry is not a rendered item, and the comments in these two
    files are a deliberate record of what was removed and why — so a name that
    survives only in prose must not read as a live nav row or a live view.
    """
    return "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("//"))


def _labels(source, const):
    """The labels of one nav array, in the order the sidebar maps over them."""
    # Split on `= [` and then the closing `]` at column 0: SECONDARY's type
    # annotation carries its own `[]`, which a naive split would stop at.
    body = source.split(f"const {const}", 1)[1].split("= [", 1)[1].split("\n]", 1)[0]
    # Without _code, deleting a row by prefixing it with `//` reads as still
    # present and the test says nothing.
    return re.findall(r"label: '([^']+)'", _code(body))


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


def test_the_removed_items_are_gone_from_the_source_entirely():
    """Gone from the DOM, not hidden, not dimmed, and not commented out.

    Searching the whole file rather than the nav arrays is deliberate: a
    commented-out entry is exactly the thing this is meant to catch, and a
    comment is invisible to the parse in the test above.
    """
    source = _sidebar()
    # The prose at the top of SECONDARY names them as removed; that is a record,
    # not a nav entry. Everything below it is code.
    code = _code(source)
    for label in REMOVED:
        assert label not in code, f"{label!r} is still in the sidebar"

    # ...and _code is blind to a row that was commented out rather than deleted,
    # by design, since the record above is itself a comment. A dead entry left
    # in the file is the thing these removals were asked not to leave behind, so
    # look for the shape of a nav row in the comments rather than for a name.
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            assert "label:" not in stripped, (
                f"a nav entry is commented out rather than removed: {stripped}")

    app = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    assert "LaunchpadPage" not in app, "App.tsx still routes to the Launchpad page"
    assert not (FRONTEND / "pages" / "LaunchpadPage.tsx").exists(), (
        "LaunchpadPage.tsx is back; nothing imports it")


def test_payments_has_no_view_left_behind_the_removed_nav_row():
    """The row is only half of it: the view it opened has to go too.

    Payments never had a page component of its own — it fell through App.tsx's
    ternary chain to a `PLACEHOLDER[active]` card reading "Payments — not built
    yet", and it was the last key that did, so the whole PLACEHOLDER map went
    with it. An unknown key now lands on Dashboard, the same place a retired
    path does.
    """
    app = _code((FRONTEND / "App.tsx").read_text(encoding="utf-8"))
    assert "PaymentsPage" not in app, "App.tsx routes to a Payments page again"
    assert not (FRONTEND / "pages" / "PaymentsPage.tsx").exists(), (
        "a Payments page component exists; the view was meant to be gone")
    assert "active === 'payments'" not in app, "Payments is still a renderable view"
    assert "PLACEHOLDER" not in app, (
        "the not-built-yet placeholder is back; every nav key renders a real page")
    assert "not built yet" not in app, "a nav key still opens a not-built-yet card"

    # The fallback branch must render something real, not an empty pane.
    tail = app.split("active === 'settings' ?", 1)[1]
    assert "<DashboardPage />" in tail, (
        "an unrecognised view no longer falls back to Dashboard")


def test_the_three_kept_out_of_scope_items_are_still_present_and_dimmed():
    """The owner kept these three. Removing them is a decision, not a tidy-up.

    Payments used to be a fourth kept item — the odd one, a real button in
    PRIMARY onto a "not built yet" screen. It was removed on 2026-09-10 at the
    owner's request; these three were explicitly left alone in the same breath.
    """
    source = _sidebar()
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


def test_payments_redirects_to_dashboard_rather_than_dead_ending():
    """Same treatment /launchpad got, for the same reason: saved links.

    Payments was a sidebar row for the whole life of the app, so /payments is in
    somebody's bookmarks. Without an entry here it would fall through to the
    default view — the same screen, but with a path that means nothing left in
    the address bar, and no record that the link was retired on purpose.
    """
    app = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    retired = app.split("const RETIRED_PATHS", 1)[1].split("}", 1)[0]
    assert re.search(r"'/payments':\s*'dashboard'", retired), (
        "/payments no longer resolves to Dashboard")
    # The resolver itself (path read, table consulted, replaceState) is pinned by
    # the /launchpad test above; both paths go through the same three lines.


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

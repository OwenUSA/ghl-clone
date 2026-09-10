"""Layout invariants that a broken build still satisfies, so a build gate misses them.

Asserted against source because there is no JS test runner here; the behavioural
verification is a Playwright measurement recorded in orchestrate/findings/fix-log.md
(before the fix, 3 of 6 Sort items and 7 of 15 Filter items were off-screen or covered;
after it, 0 of both).
"""
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


def test_conversations_dropdown_anchors_itself_vertically():
    """An `absolute` menu with no `top` falls back to its static position.

    Both Conversations menus hang inside a `relative` flex row with `items-center`,
    so an unanchored menu is centred on the 24px icon row and overflows off the top
    of the window — taking `All` and the default sort with it, unreachable.
    """
    source = (FRONTEND / "pages" / "ConversationsPage.tsx").read_text(encoding="utf-8")
    dropdown = source.split("function Dropdown(", 1)[1].split("\n}\n", 1)[0]
    assert 'role="menu"' in dropdown, "Dropdown no longer renders the menu — retarget this"
    assert "absolute" in dropdown, "Dropdown is no longer absolutely positioned"
    style = dropdown.split("style={{", 1)[1].split("} as React.CSSProperties", 1)[0]
    style = re.sub(r"//[^\n]*", "", style)  # the comment explains `top`; don't match it
    assert re.search(r"\btop\s*:", style) or re.search(r"\bbottom\s*:", style), (
        "the Conversations dropdown sets neither top nor bottom, so it renders at its "
        "static position — vertically centred on its trigger and clipped off-screen")


def test_each_dashboard_card_is_filtered_by_its_own_select():
    """A card's pipeline select must drive the query that card renders.

    Conversion rate's select was wired to `valuePipe`, so choosing a pipeline on it
    re-filtered the *Opportunity value* card next door and left the conversion figure
    on whatever the untouched status query said. Funnel and Stage distribution do
    share one select on purpose — two renderings of a single pipeline — so this only
    covers the three cards in row 1.
    """
    source = (FRONTEND / "pages" / "DashboardPage.tsx").read_text(encoding="utf-8")
    cards = source.split('<Card title="')
    owners = {}
    for card in cards[1:]:
        title = card.split('"', 1)[0]
        select = card.split("<PipelineSelect", 1)[1].split("/>", 1)[0]
        owners[title] = select.split("value={", 1)[1].split("}", 1)[0]

    row1 = ["Opportunity status", "Opportunity value", "Conversion rate"]
    picked = [owners[t] for t in row1]
    assert len(set(picked)) == 3, (
        f"row-1 cards share pipeline state {picked} — one card's select filters another")

    # ...and the card must read the query its own select keys.
    conversion = source.split('<Card title="Conversion rate"', 1)[1].split("</Card>", 1)[0]
    assert "conversion_rate" in conversion, "retarget this test — the card moved"
    assert "status.data" not in conversion, (
        "Conversion rate renders the status query while its select drives another")


def test_the_contacts_count_pill_pluralises():
    """The pill read `1 Contacts` on any search that matched exactly one person.

    Measured GHL only ever shows it at 268, so there is no captured reference for
    the singular; this is our own copy and the deviation is only in English.
    """
    source = (FRONTEND / "pages" / "ContactsPage.tsx").read_text(encoding="utf-8")
    pill = source.split("{data ?", 1)[1].split("}\n", 1)[0]
    assert "data.total" in pill, "the count pill moved — retarget this test"
    assert "'Contact'" in pill and "=== 1" in pill, (
        "the pill appends a fixed `Contacts`, so one match reads `1 Contacts`")


def test_customize_card_cancel_is_not_a_second_apply():
    """`Cancel` and `Apply` were both `onClick={() => setShowFields(false)}`.

    The card layout applies live as you click it, so closing the modal any way at
    all kept the previewed layout — Cancel, the backdrop and Apply were three names
    for one behaviour. Cancel has to put back the layout that was in effect when
    the modal opened.
    """
    source = (FRONTEND / "pages" / "OpportunitiesPage.tsx").read_text(encoding="utf-8")
    assert "setLayout(layoutOnOpen)" in source, (
        "nothing restores the previewed layout, so Cancel cannot discard anything")
    modal = source.split("{showFields && (", 1)[1]
    cancel = modal.split("Cancel", 1)[0].rsplit("<button", 1)[1]
    apply_ = modal.split("Apply", 1)[0].rsplit("<button", 1)[1]
    assert "cancelFields" in cancel, "Cancel does not discard the previewed layout"
    assert "cancelFields" not in apply_, "Apply discards the layout it is meant to keep"
    backdrop = modal.split("<div", 1)[1].split(">", 1)[0]
    assert "cancelFields" in backdrop, (
        "clicking the backdrop keeps the previewed layout, so it is a silent Apply")


def test_the_opportunities_overflow_menu_can_be_dismissed():
    """The `⋯` menu closed only by re-clicking `⋯`.

    Escape did nothing, and with no backdrop an outside click fell through to
    whatever was underneath — during QA a click meant to dismiss the menu landed on
    an opportunity card and opened the detail dialog instead.
    """
    source = (FRONTEND / "pages" / "OpportunitiesPage.tsx").read_text(encoding="utf-8")
    menu = source.split("{showOverflow && (", 1)[1].split("role=\"menu\"", 1)[0]
    assert "fixed inset-0" in menu and "setShowOverflow(false)" in menu, (
        "the menu has no backdrop, so an outside click falls through to the board")
    assert "'Escape'" in source and "setShowOverflow(false)" in source, (
        "Escape does not close the overflow menu")
    # The backdrop must not swallow the trigger: re-clicking it is the documented
    # way to close, and it has to keep running its own toggle.
    trigger = source.split("aria-haspopup=\"menu\"", 1)[0].rsplit("<button", 1)[1]
    assert "setShowOverflow((s) => !s)" in trigger, "retarget this — the trigger moved"
    assert "z-20" in source.split("aria-haspopup=\"menu\"", 1)[1].split(">", 1)[0], (
        "the trigger sits under the backdrop, so its toggle never fires")


def test_the_pipeline_select_actually_selects_a_pipeline():
    """The board was hard-wired to `pipelines.data?.[0]`.

    The `<select>` had no `value` and no `onChange`, and its options carried no
    `value` either, so choosing a different pipeline changed the option text and
    nothing else. Invisible on production, which has one pipeline; DECISIONS.md
    records four on the live GHL account, so a real export makes it reachable.
    """
    source = (FRONTEND / "pages" / "OpportunitiesPage.tsx").read_text(encoding="utf-8")
    chosen = source.split("const pipeline =", 1)[1].splitlines()[0]
    assert "p.id === pipelineId" in chosen, (
        "the board still takes the first pipeline unconditionally")
    select = source.split("{/* pipeline row */}", 1)[1].split("</select>", 1)[0]
    assert "onChange=" in select, "the pipeline select ignores the choice"
    assert "value={pipeline?.id" in select, "the select does not show the chosen pipeline"
    assert "<option key={p.id} value={p.id}>" in select, (
        "the options carry no value, so onChange has nothing to read")


def test_reporting_shows_only_the_three_tabs_that_can_work():
    """Four Reporting tabs existed only to be disabled; they are gone from the DOM.

    Google Ads, Meta Ads (Facebook Ads) report and Local Marketing Audit are
    third-party marketing integrations, which CLAUDE.md puts out of scope, and
    Attribution report rendered empty on the live account -- so none of the four
    could ever be enabled by anything this app might do next. A greyed tab implies
    a switch exists somewhere; there is none. Removed at the owner's request,
    amended into DECISIONS.md on 2026-09-09.

    Custom reports stays: visible, disabled, reason on hover, by the same request.

    Asserted against source because there is no JS test runner here -- so this
    checks the tab row is *driven* by TABS before trusting TABS' contents.
    """
    source = (FRONTEND / "pages" / "ReportingPage.tsx").read_text(encoding="utf-8")

    # The row renders TABS and nothing else, so the array is the whole tab row.
    row = source.split("{TABS.map((t) => (", 1)[1].split("))}", 1)[0]
    assert "{t.label}" in row, "retarget this test -- the tab row no longer maps TABS"
    assert re.search(r"disabled=\{!!t\.off\}", row), (
        "a tab's `off` no longer disables it, so a stub is clickable")

    tabs = source.split("const TABS: TabDef[] = [", 1)[1].split("\n]", 1)[0]
    labels = re.findall(r"label: '([^']+)'", tabs)
    assert labels == ["Custom reports", "Call report", "Appointment report"], (
        f"the Reporting tab row is {labels}, not the three tabs that can work")

    # Custom reports is a stub on purpose -- it must keep saying why on hover.
    custom = next(t for t in tabs.splitlines() if "'Custom reports'" in t)
    assert "off:" in custom, "Custom reports lost its hover reason and now looks live"
    for live in ("'Call report'", "'Appointment report'"):
        entry = next(t for t in tabs.splitlines() if live in t)
        assert "off:" not in entry, f"{live} is disabled -- it is an implemented report"

    # ...and the four are not lurking anywhere else on the page either. The
    # leading block comment records why they went, so it is not evidence.
    body = source.split("*/", 1)[1]
    for gone in ("Google Ads", "Meta Ads (Facebook Ads) report",
                 "Attribution report", "Local Marketing Audit"):
        assert gone not in body, f"{gone} still renders on the Reporting page"

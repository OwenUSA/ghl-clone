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

    # ...and the card must render the query its own select keys. The percentage
    # is computed just above the JSX now, because the card offers two
    # denominators, so both halves are checked: the figure comes from `conv`,
    # and the card renders that figure rather than a neighbour's.
    rate = source.split("const rate =", 1)[1].split("\n", 1)[0]
    assert "conv.data" in rate and "conversion_rate" in rate, (
        "the conversion figure no longer comes from the Conversion rate card's query")
    conversion = source.split('<Card title="Conversion rate"', 1)[1].split("</Card>", 1)[0]
    assert "rate.toFixed" in conversion, "the card does not render the figure it computed"
    for other in ("status.data", "value.data", "funnel.data"):
        assert other not in conversion, (
            f"Conversion rate renders {other} while its select drives another query")


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


def test_the_calendar_grid_is_not_capped_at_a_week():
    """The Month view bug, at the site where it lived.

    Both grid renders did `dayList.slice(0, view === 'Month view' ? 7 : days)`,
    so Month view drew a week. The arithmetic is now tested by executing it
    (test_calendar_grid.py); what is asserted here is that the page renders the
    list that arithmetic produces, rather than re-deriving or truncating it.
    """
    source = (FRONTEND / "pages" / "CalendarsPage.tsx").read_text(encoding="utf-8")
    body = source.split("export function CalendarsPage", 1)[1]
    assert ".slice(0, view ===" not in body and "? 7 : days" not in body, (
        "the grid still caps its day list, so Month view draws a week")
    for call, why in (
        ("visibleDays(view, anchor)", "the drawn days"),
        ("queryWindow(view, anchor)", "the API range"),
        ("rangeLabel(view, anchor)", "the toolbar label"),
        ("shiftAnchor(view, a, dir)", "the prev/next arrows"),
    ):
        assert call in body, (
            f"{why} is derived somewhere other than calendarGrid.ts, so it can "
            "drift from what the grid draws — which is exactly the old bug")


def test_the_month_view_has_its_own_shape():
    """A month is day cells in weeks, not 35 columns of a 24-hour grid. The hour
    grid must stay for Day and Week view, which are measured against GHL."""
    source = (FRONTEND / "pages" / "CalendarsPage.tsx").read_text(encoding="utf-8")
    assert "function MonthGrid(" in source, "there is no month grid"
    month = source.split("function MonthGrid(", 1)[1]
    assert "gridTemplateColumns: 'repeat(7, minmax(0, 1fr))'" in month, (
        "the month grid is not seven columns wide")
    assert "MONTH_CELL_CHIPS" in month and "more" in month, (
        "a day with more appointments than fit has no overflow affordance")
    # The measured views must still render the hour grid, and only they.
    page = source.split("export function CalendarsPage", 1)[1]
    assert "view === 'Month view' ? (\n            <MonthGrid" in page, (
        "Month view does not render the month grid")
    assert "HOURS.map" in page, "the measured Day/Week hour grid is gone"


def test_double_clicking_an_empty_slot_books_it_and_a_booking_does_not():
    """Both entry points open the same dialog, and a double-click that lands on
    an existing appointment must not also fire the empty-slot handler beneath
    it — that would open a create dialog on top of the booking you clicked."""
    source = (FRONTEND / "pages" / "CalendarsPage.tsx").read_text(encoding="utf-8")
    page = source.split("export function CalendarsPage", 1)[1].split("function MonthGrid(", 1)[0]
    month = source.split("function MonthGrid(", 1)[1]

    assert "onDoubleClick={(e) => bookFromColumn(d, e)}" in page, (
        "an empty slot in Day/Week view cannot be double-clicked to book it")
    assert "onDoubleClick={() => onBook(" in month, (
        "a month day cell cannot be double-clicked to book it")

    # The drawn appointment in each grid must swallow the double-click.
    hour_grid = page.split("{/* hour grid", 1)[1]
    for block, what in ((hour_grid, "the hour grid"), (month, "the month grid")):
        appt = block.split("<div key={a.id}", 1)[1] if "<div key={a.id}" in block \
            else block.split("data-appointment", 1)[1]
        assert "stopPropagation" in appt.split("style={{", 1)[0], (
            f"double-clicking an appointment in {what} falls through to the "
            "empty-slot handler and opens a create dialog")

    # One dialog, two entry points -- the New button and the double-clicks all
    # set the same piece of state.
    assert source.count("setDraft(") >= 1 and "book(defaultSlot(dayList, now))" in source, (
        "the New button does not open the same dialog a double-click does")
    assert "{draft && (\n        <NewAppointmentDialog" in source, (
        "nothing renders the create dialog")


def test_a_new_appointment_appears_without_a_manual_refresh():
    """The range key changes with every view and every arrow press, so
    invalidating the one key in hand would leave the booking invisible as soon
    as the user paged anywhere."""
    source = (FRONTEND / "pages" / "CalendarsPage.tsx").read_text(encoding="utf-8")
    created = source.split("onCreated={() => {", 1)[1].split("}}", 1)[0]
    assert "invalidateQueries({ queryKey: ['appointments'] })" in created, (
        "the new appointment is not re-fetched, so it does not appear until "
        "something else happens to refresh the query")


# ---------------- the Dashboard's controls ----------------

def _jsx_tags(source: str, tag: str) -> list[str]:
    """Every `<tag ...>` opening tag in a .tsx file, with braces respected.

    Splitting on the next `>` does not work here: `onClick={() => close()}`
    contains one, and so does every other arrow function in a prop. (An attribute
    written as a double-quoted string containing `>` would still fool this; there
    are none, and one would be worth noticing anyway.)
    """
    out = []
    for at in re.finditer(r"<%s[\s/>]" % tag, source):
        i, depth = at.end() - 1, 0
        while i < len(source):
            ch = source[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            elif ch == ">" and depth == 0:
                break
            i += 1
        out.append(source[at.start():i + 1])
    return out


def test_nothing_on_the_dashboard_looks_clickable_and_does_nothing():
    """The owner's standing rule for this app, and the reason four controls on
    this screen were rebuilt: the "Dashboard" selector was a bare `<button>` with
    no handler, the `⋮` was a `<span>` that could not even be focused, and every
    per-card gear was `disabled` with "not implemented in v1" on hover.

    A button either does something or says why it cannot. Nothing in between.
    """
    source = (FRONTEND / "pages" / "DashboardPage.tsx").read_text(encoding="utf-8")
    buttons = _jsx_tags(source, "button")
    assert len(buttons) >= 5, "retarget this test — the header controls moved"
    for b in buttons:
        one_line = " ".join(b.split())[:120]
        assert "onClick=" in b or "disabled" in b, (
            "an enabled button with no handler: %s" % one_line)
        if "disabled" in b and "onClick=" not in b:
            assert "title=" in b, (
                "a disabled control with no reason on hover: %s" % one_line)
    # Every select must be wired too — a `<select>` without onChange changes the
    # option text and nothing else, which is how the Opportunities pipeline
    # picker shipped broken (test_the_pipeline_select_actually_selects_a_pipeline).
    for s in _jsx_tags(source, "select"):
        assert "onChange=" in s and "value=" in s, (
            "a select that ignores the choice: %s" % " ".join(s.split())[:120])


def test_every_dashboard_card_asks_for_the_chosen_date_range():
    """`/api/dashboard` took only `pipeline_id`, so "Last 30 days" and "All time"
    returned identical figures. Both halves have to be right: the window belongs
    in the query KEY, or changing the range never refetches, and in the query
    FUNCTION, or the request does not carry it.
    """
    source = (FRONTEND / "pages" / "DashboardPage.tsx").read_text(encoding="utf-8")
    win = source.split("const win =", 1)[1].split("\n", 1)[0]
    assert "rangeWindow(range)" in win, "the header range no longer produces a window"
    # ...and it MUST be memoised on `range`. `rangeWindow` defaults to `new Date()`,
    # so an unmemoised call returns a start a few milliseconds later on every
    # render; that start is part of all five query keys, so every render
    # invalidated every card, whose results re-rendered the page. The dashboard
    # refetched in a loop for as long as it was open and drew zeros throughout,
    # because the key each card was reading had already been replaced. Caught in a
    # browser, not here — which is why it is pinned here.
    assert "useMemo" in win and "[range]" in win, (
        "the range window is rebuilt on every render, so every card's query key "
        "changes on every render and the dashboard refetches in a loop")
    for name in ("status", "value", "conv", "funnel", "dist"):
        block = source.split("const %s = useQuery({" % name, 1)[1]
        key = block.split("queryKey:", 1)[1].split("]", 1)[0]
        fn = block.split("queryFn:", 1)[1].split("\n  })", 1)[0]
        assert "winFor(" in key, (
            "the %s card's query key ignores the range, so changing it does not "
            "refetch" % name)
        assert "winFor(" in fn, (
            "the %s card's request does not carry the range" % name)


def test_the_funnel_percentages_are_not_recomputed_in_the_browser():
    """Both columns were computed in the JSX and both were wrong.

    Cumulative was `count / total` — a distribution under a heading that promises
    a cumulative, so it rose and fell down the column (30% / 10% / 40% on
    production). Next step was `next.count / st.count`, a ratio of two stage
    OCCUPANCIES, which read 400% wherever a later stage held more deals than an
    earlier one. The server computes both from `reached` now; the card renders
    them and does no percentage arithmetic of its own.
    """
    source = (FRONTEND / "pages" / "DashboardPage.tsx").read_text(encoding="utf-8")
    card = source.split('<Card title="Funnel"', 1)[1].split("</Card>", 1)[0]
    assert "pct(st.cumulative_pct)" in card and "pct(st.next_step_pct)" in card, (
        "the funnel columns no longer render the server's figures")
    for gone in ("funnelTotal", "nextConv", "next?.count", "stages[i + 1]"):
        assert gone not in card, (
            "%s is back: the browser is deriving a funnel percentage again" % gone)


def test_the_funnel_select_does_not_offer_all_pipelines():
    """It did, and then rendered the first pipeline anyway. A funnel lays one
    pipeline's stages end to end; stages from two different pipelines cannot be
    put in a row, so "All pipelines" could never have meant anything here."""
    source = (FRONTEND / "pages" / "DashboardPage.tsx").read_text(encoding="utf-8")
    for title in ("Funnel", "Stage distribution"):
        card = source.split('<Card title="%s"' % title, 1)[1].split("</Card>", 1)[0]
        select = card.split("<PipelineSelect", 1)[1].split("/>", 1)[0]
        assert "allPipelines={false}" in select, (
            "the %s card still offers All pipelines" % title)


def test_the_status_donut_cannot_disagree_with_the_number_in_its_middle():
    """The legend drew Won/Open/Lost while the centre read `total`, which counts
    abandoned deals too. On any pipeline holding one, the ring was drawn full and
    its segments summed to less than the number printed inside it."""
    source = (FRONTEND / "pages" / "DashboardPage.tsx").read_text(encoding="utf-8")
    block = source.split("const segs", 1)[1].split("// ---- Opportunity value", 1)[0]
    assert "abandoned" in block, "an abandoned opportunity is still not drawn"
    assert "unaccounted" in block and "status.data?.total" in block, (
        "nothing reconciles the segments against the total in the centre, so a "
        "status the legend does not know about vanishes from the ring")


def test_the_value_bars_are_data_rather_than_arithmetic():
    """Lost was hard-coded to `0` and Open was drawn as `totalRev - wonRev`, which
    is only true when nothing has been lost or abandoned. Production has neither,
    so the card looked right and would have started lying on the first lost deal.
    """
    source = (FRONTEND / "pages" / "DashboardPage.tsx").read_text(encoding="utf-8")
    bars = source.split("const bars = [", 1)[1].split("]", 1)[0]
    assert "byValue.lost" in bars and "byValue.open" in bars and "byValue.won" in bars, (
        "the bars no longer read the server's per-status money")
    assert "totalRev - wonRev" not in source, "Open is being inferred again"

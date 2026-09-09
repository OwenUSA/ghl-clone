"""The Calendars month grid, executed rather than pattern-matched.

The project has no JS test runner, so frontend invariants are normally asserted
against source (test_frontend_layout.py, test_frontend_feedback.py). That is too
weak for this one: the Month view bug was arithmetic, and a regex saying "the
file mentions monthCells" would have passed against the broken version too.

`frontend/src/lib/calendarGrid.ts` is therefore written free of React and of any
import, so node can import the real shipped module directly (node >= 22.6 strips
the type annotations; the whole frontend is compiled with `erasableSyntaxOnly`,
so there is no syntax here that stripping cannot handle). These tests run the
actual code the browser runs.

The bug they cover: Month view set `days = 35`, which drove the range label, the
API query window and the prev/next arrows, while BOTH grid renders capped at
`dayList.slice(0, 7)`. Month view was pixel-identical to Week view, fetched five
times the data, and paged 35 days at a time past four weeks nobody could see.
"""
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

GRID_TS = (Path(__file__).resolve().parents[2]
           / "frontend" / "src" / "lib" / "calendarGrid.ts")

NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None,
    reason="node is not on PATH, so calendarGrid.ts cannot be executed. CI's "
           "backend job installs it precisely so this file is never skipped "
           "there — see .github/workflows/ci.yml.")


def run_js(body: str):
    """Execute `body` with the real module imported as `g`, and return its JSON.

    TZ is pinned. The module does local-time arithmetic on purpose — a calendar
    shows wall-clock days — so a floating timezone would make the DST assertions
    below pass or fail depending on the machine. America/New_York is the
    company's own zone (Bradenton FL).
    """
    script = textwrap.dedent("""
        import * as g from %s
        const out = (v) => console.log('@@' + JSON.stringify(v))
    """) % json.dumps(GRID_TS.as_posix()) + textwrap.dedent(body)
    proc = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-"],
        input=script, capture_output=True, text=True,
        env={**os.environ, "TZ": "America/New_York"})
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert line, "the script printed no result:\n" + proc.stdout + proc.stderr
    return json.loads(line[-1][2:])


# ---------------- the grid covers the whole month ----------------

@node
@pytest.mark.parametrize("year,month,expected_cells,day1_weekday", [
    # (months are JavaScript's 0-based ones, to match the assertions)
    # A non-leap February starting on a Sunday is the only 4-week grid there is —
    # the tightest case the layout must survive. February 2026 is one.
    (2026, 1, 28, 0),
    (2026, 8, 35, 2),   # September 2026: starts Tuesday, 30 days -> 5 weeks
    (2026, 4, 42, 5),   # May 2026: starts Friday, 31 days -> 6 weeks
    (2026, 7, 42, 6),   # August 2026: starts Saturday, 31 days -> 6 weeks
])
def test_the_month_grid_draws_whole_weeks_covering_every_day(
        year, month, expected_cells, day1_weekday):
    """The count, not a slice. The old view drew 7 cells for every month."""
    got = run_js("""
        const cells = g.monthCells(new Date(%d, %d, 15))
        out({
          count: cells.length,
          firstWeekday: cells[0].getDay(),
          lastWeekday: cells[cells.length - 1].getDay(),
          firstOfMonthWeekday: new Date(%d, %d, 1).getDay(),
          // every date of the anchor month that the grid actually contains
          covered: cells.filter((d) => d.getMonth() === %d).map((d) => d.getDate()),
          days: cells.map((d) => d.toDateString()),
        })
    """ % (year, month, year, month, month))

    assert got["count"] == expected_cells
    assert got["count"] % 7 == 0, "the grid does not draw whole weeks"
    assert got["firstWeekday"] == 0 and got["lastWeekday"] == 6, (
        "the grid does not run Sunday to Saturday, which is the measured week start")
    assert got["firstOfMonthWeekday"] == day1_weekday, "retarget this case — wrong month"

    # The point of the whole exercise: nothing in the month is missing.
    in_month = got["covered"]
    assert in_month == list(range(1, len(in_month) + 1)), (
        "the grid skips days of its own month")
    assert len(set(got["days"])) == got["count"], "the grid repeats a day"


@node
def test_month_view_draws_more_than_a_week():
    """The regression itself, stated as bluntly as it can be.

    `visibleDays` is the single list the page maps over; before the fix the page
    sliced it to 7 no matter what the view was.
    """
    got = run_js("""
        const anchor = new Date(2026, 8, 15)
        out({
          day: g.visibleDays('Day view', anchor).length,
          week: g.visibleDays('Week view', anchor).length,
          month: g.visibleDays('Month view', anchor).length,
        })
    """)
    assert got["day"] == 1
    assert got["week"] == 7
    assert got["month"] == 35, "Month view still renders a week"


@node
def test_the_query_window_is_exactly_what_is_drawn():
    """Fetching wider than the grid draws is what hid the bug: 35 days of
    appointments arrived and 28 days of them were silently discarded."""
    got = run_js("""
        const anchor = new Date(2026, 8, 15)
        const rows = ['Day view', 'Week view', 'Month view'].map((view) => {
          const days = g.visibleDays(view, anchor)
          const w = g.queryWindow(view, anchor)
          const last = days[days.length - 1]
          return {
            view,
            startsAtFirstCell: w.start.getTime() === days[0].getTime(),
            // midnight after the last drawn day, so a 23:30 booking is inside
            endsAfterLastCell:
              w.end.getTime() === new Date(last.getFullYear(), last.getMonth(),
                                           last.getDate() + 1).getTime(),
          }
        })
        out(rows)
    """)
    for row in got:
        assert row["startsAtFirstCell"], "%s asks for days before the first cell" % row["view"]
        assert row["endsAfterLastCell"], "%s stops short of the last drawn cell" % row["view"]


# ---------------- an appointment mid-month is actually drawn ----------------

@node
def test_an_appointment_on_the_20th_lands_in_the_cell_that_draws_it():
    """Day 20 of a 30-day month is in week 4 — the region the old `slice(0, 7)`
    threw away. `bucketByDay` returns one bucket per drawn cell, so a booking
    that reaches a bucket is a booking that reaches the screen."""
    got = run_js("""
        const anchor = new Date(2026, 8, 1)            // September 2026
        const cells = g.visibleDays('Month view', anchor)
        const appts = [
          { id: 1, starts_at: new Date(2026, 8, 20, 14, 0).toISOString() },
          { id: 2, starts_at: new Date(2026, 8, 20, 9, 0).toISOString() },
          { id: 3, starts_at: new Date(2026, 8, 3, 9, 0).toISOString() },
          // Outside the whole grid: must not be forced into a cell.
          { id: 4, starts_at: new Date(2026, 10, 4, 9, 0).toISOString() },
        ]
        const buckets = g.bucketByDay(cells, appts)
        const at = (dom) => buckets[cells.findIndex(
          (d) => d.getMonth() === 8 && d.getDate() === dom)].map((a) => a.id)
        out({
          buckets: buckets.length,
          cells: cells.length,
          on20: at(20),
          on3: at(3),
          total: buckets.flat().length,
          weekOf20: Math.floor(cells.findIndex(
            (d) => d.getMonth() === 8 && d.getDate() === 20) / 7),
        })
    """)
    assert got["buckets"] == got["cells"] == 35, "a cell exists with no bucket behind it"
    assert got["weekOf20"] == 3, "retarget this — the 20th is no longer in week 4"
    assert got["on20"] == [1, 2], "the 20th's appointments never reach a drawn cell"
    assert got["on3"] == [3]
    assert got["total"] == 3, "an appointment outside the grid was drawn anyway"


# ---------------- the arrows page by one month ----------------

@node
def test_the_arrows_page_by_one_of_whatever_is_on_screen():
    """Month view used to advance the anchor by 35 days, so paging forward from
    a 30-day month skipped five days entirely and drifted further every click."""
    got = run_js("""
        const rows = []
        let a = new Date(2026, 0, 31)                  // 31 January
        for (let i = 0; i < 13; i++) {
          rows.push({ y: a.getFullYear(), m: a.getMonth(), label: g.rangeLabel('Month view', a) })
          a = g.shiftAnchor('Month view', a, 1)
        }
        out({
          months: rows,
          backFromMarch: (() => {
            const b = g.shiftAnchor('Month view', new Date(2026, 2, 15), -1)
            return { y: b.getFullYear(), m: b.getMonth() }
          })(),
          week: (() => {
            const b = g.shiftAnchor('Week view', new Date(2026, 8, 15), 1)
            return Math.round((b - new Date(2026, 8, 15)) / 86400000)
          })(),
          day: (() => {
            const b = g.shiftAnchor('Day view', new Date(2026, 8, 15), 1)
            return Math.round((b - new Date(2026, 8, 15)) / 86400000)
          })(),
        })
    """)

    # 31 Jan + 1 month must be February, not "3 March" — the trap in adding a
    # month to a day-of-month that the next month does not have.
    assert [r["m"] for r in got["months"][:4]] == [0, 1, 2, 3]
    assert got["months"][12] == {"y": 2027, "m": 0,
                                 "label": "January 2027"}, "twelve steps is not a year"
    assert got["backFromMarch"] == {"y": 2026, "m": 1}, "back from March is not February"
    assert got["week"] == 7 and got["day"] == 1, "Day and Week paging changed"


@node
def test_the_label_names_the_range_that_is_drawn():
    """The label is read off the same `anchor` + `view` the grid is. Month view
    used to name a five-week range over a grid showing seven days."""
    got = run_js("""
        const sept = new Date(2026, 8, 15)
        const cells = g.visibleDays('Month view', sept)
        out({
          month: g.rangeLabel('Month view', sept),
          // Every cell whose month the label names must be in the grid, and the
          // grid must not be a grid of some other month.
          monthsInGrid: [...new Set(cells.map((d) => d.getMonth()))].sort(),
          majority: cells.filter((d) => d.getMonth() === 8).length,
          week: g.rangeLabel('Week view', new Date(2026, 6, 29)),
          day: g.rangeLabel('Day view', new Date(2026, 8, 9)),
        })
    """)
    assert got["month"] == "September 2026"
    assert got["majority"] == 30, "the grid does not hold all of the month it names"
    assert got["monthsInGrid"] == [7, 8, 9], "retarget this — September 2026 spills both ways"
    # Day and Week are measured against GHL; their formats must not have moved.
    # The separator really is an EN DASH: it is what GHL renders and what the
    # measured Week view label has always used.
    assert got["week"] == "Jul 26 \u2013 Aug 1, 2026"
    assert got["day"] == "Sep 9, 2026"


# ---------------- prefill ----------------

@node
def test_a_double_clicked_slot_prefills_the_time_that_was_clicked():
    """Snapped to the half hour, and clamped inside the day: a double-click 2px
    below the last row must not prefill 00:00 tomorrow."""
    got = run_js("""
        const d = new Date(2026, 8, 9)
        const at = (h) => { const s = g.slotAt(d, h); return [s.getHours(), s.getMinutes()] }
        out({ nine: at(9), quarterPast: at(9.25), twenty: at(9.4), half: at(9.5),
              below: at(25), above: at(-3) })
    """)
    assert got["nine"] == [9, 0]
    assert got["quarterPast"] == [9, 30], "0.25h must round up to the next half hour"
    assert got["twenty"] == [9, 30]
    assert got["half"] == [9, 30]
    assert got["below"] == [23, 30], "a click past midnight escaped the day"
    assert got["above"] == [0, 0]


@node
def test_the_new_button_prefills_a_day_that_is_on_screen():
    """Booking onto a date the user cannot see is how an appointment lands
    somewhere nobody looks."""
    got = run_js("""
        const now = new Date(2026, 8, 9, 16, 42)
        const onScreen = g.defaultSlot(g.visibleDays('Month view', now), now)
        // A month the user has paged away to: today is not in it.
        const away = g.visibleDays('Month view', new Date(2027, 3, 1))
        const off = g.defaultSlot(away, now)
        out({
          onScreen: [onScreen.getMonth(), onScreen.getDate(), onScreen.getHours()],
          off: [off.getMonth(), off.getDate(), off.getHours()],
          offIsFirstCell: off.toDateString() === away[0].toDateString(),
        })
    """)
    assert got["onScreen"] == [8, 9, 9], "today is on screen but was not the default"
    assert got["off"][2] == 9
    assert got["offIsFirstCell"], "the default landed on a day that is not drawn"


@node
def test_the_date_and_time_inputs_round_trip_in_local_time():
    """`toISOString()` is UTC. Prefilling `<input type=date>` from it moves a
    9 AM booking to the previous day for anyone west of Greenwich — which is
    everyone here (Bradenton FL is UTC-4/-5)."""
    got = run_js("""
        const d = new Date(2026, 8, 9, 9, 0)
        const back = g.fromInputs(g.dateInputValue(d), g.timeInputValue(d))
        out({
          date: g.dateInputValue(d),
          time: g.timeInputValue(d),
          sameInstant: back.getTime() === d.getTime(),
          isoWouldSay: d.toISOString().slice(0, 10),
          blank: g.fromInputs('', '09:00'),
          junk: g.fromInputs('not-a-date', '09:00'),
        })
    """)
    assert got["date"] == "2026-09-09" and got["time"] == "09:00"
    assert got["sameInstant"], "the inputs do not round-trip"
    assert got["isoWouldSay"] == "2026-09-09"
    assert got["blank"] is None and got["junk"] is None, (
        "a blank or unparseable input must not become a silent Invalid Date")


@node
def test_day_arithmetic_survives_a_dst_boundary():
    """Adding 86_400_000ms across the US spring-forward lands at 01:00, and the
    grid then draws the same calendar day twice. The module uses the Date
    constructor for exactly this reason; DST 2026 is 8 March / 1 November."""
    got = run_js("""
        const march = g.visibleDays('Month view', new Date(2026, 2, 15))
        const nov = g.visibleDays('Month view', new Date(2026, 10, 15))
        const noons = (cells) => cells.every((d) => d.getHours() === 0)
        const distinct = (cells) => new Set(cells.map((d) => d.toDateString())).size
        out({
          march: { count: march.length, midnight: noons(march), distinct: distinct(march) },
          nov: { count: nov.length, midnight: noons(nov), distinct: distinct(nov) },
        })
    """)
    for name, row in got.items():
        assert row["midnight"], "%s has a cell that is not local midnight" % name
        assert row["distinct"] == row["count"], "%s repeats or skips a day over DST" % name

"""The Day/Week grid's side-by-side layout (2026-09-15), executed rather than read.

`frontend/src/lib/calendarGrid.ts` is imported by node exactly as in
test_calendar_grid.py (TZ pinned to America/New_York). These run the code the browser
runs; `tests/browser_calendar_week.py` then checks the drawn boxes in real Chromium.

What the owner asked for, as properties:

  * overlapping appointments are laid out side by side — no two that overlap in time
    share any horizontal space, so nothing is hidden behind another block;
  * two at the same time split the column in two, three in three;
  * a block's height is its real duration;
  * a job that runs past midnight is drawn on every day it touches;
  * the block's text says the time RANGE, as GoHighLevel writes it;
  * the grid opens on 7 AM to 7 PM where the screen allows.
"""
import pytest
from tests.test_calendar_grid import node, run_js

# The Monday measured on production (2026-09-14, Eastern), and Tuesday's pair.
MONDAY = [
    ("03T4HE", "07:30", "10:00"),
    ("9CNTDD", "12:00", "14:00"),
    ("QNAXZ2", "12:00", "13:00"),
    ("IBZM14", "12:30", "13:30"),
    ("0J2X27", "15:00", "17:00"),
    ("Z6GSNN", "15:00", "16:00"),
    ("N61PDL", "16:00", "18:00"),
]


def items(rows, date="2026-09-14"):
    """JS source for a list of {id, starts_at, ends_at} in local (Eastern) time."""
    return "[%s]" % ", ".join(
        "{id: %r, starts_at: new Date('%sT%s:00').toISOString(), "
        "ends_at: new Date('%sT%s:00').toISOString()}" % (i, date, s, date, e)
        for i, s, e in rows)


def layout(rows, date="2026-09-14"):
    got = run_js("""
        const placed = g.layoutDay(new Date('%sT00:00:00'), %s)
        out(placed.map(p => ({id: p.item.id, start: p.startMin, end: p.endMin,
                              col: p.col, span: p.span, cols: p.cols,
                              fromBefore: p.fromBefore, untilAfter: p.untilAfter})))
    """ % (date, items(rows, date)))
    return {p["id"]: p for p in got}


def horizontal(p):
    return (p["col"] / p["cols"], (p["col"] + p["span"]) / p["cols"])


def assert_nothing_hidden(placed):
    ps = list(placed.values())
    for i, a in enumerate(ps):
        for b in ps[i + 1:]:
            if a["start"] < b["end"] and b["start"] < a["end"]:
                (a0, a1), (b0, b1) = horizontal(a), horizontal(b)
                assert a1 <= b0 or b1 <= a0, (
                    "%s and %s overlap in time AND share horizontal space" % (a["id"], b["id"]))


@node
def test_two_at_the_same_time_split_the_column_in_two():
    placed = layout([("K9HW88", "15:00", "17:00"), ("RC9C3E", "15:00", "17:00")],
                    date="2026-09-15")
    assert {p["cols"] for p in placed.values()} == {2}
    assert {p["col"] for p in placed.values()} == {0, 1}
    assert all(p["span"] == 1 for p in placed.values())
    assert_nothing_hidden(placed)


@node
def test_three_overlapping_split_in_three():
    placed = layout(MONDAY[1:4])
    assert {p["cols"] for p in placed.values()} == {3}
    assert sorted(p["col"] for p in placed.values()) == [0, 1, 2]
    assert all(p["span"] == 1 for p in placed.values())
    assert_nothing_hidden(placed)


@node
def test_the_measured_monday_draws_all_seven_and_hides_none():
    placed = layout(MONDAY)
    assert set(placed) == {m[0] for m in MONDAY}
    assert_nothing_hidden(placed)
    # 7:30-10 overlaps nothing: the whole width.
    assert (placed["03T4HE"]["cols"], placed["03T4HE"]["span"]) == (1, 1)
    # 3-5, 3-4 and 4-6: two lanes; 4-6 reuses the lane 3-4 left at 4.
    assert placed["0J2X27"]["cols"] == 2
    assert placed["Z6GSNN"]["col"] == placed["N61PDL"]["col"] == 1


@node
def test_height_is_the_real_duration():
    placed = layout(MONDAY)
    for job, start, end in MONDAY:
        h0, m0 = map(int, start.split(":"))
        h1, m1 = map(int, end.split(":"))
        p = placed[job]
        assert p["start"] == h0 * 60 + m0
        assert p["end"] - p["start"] == (h1 * 60 + m1) - (h0 * 60 + m0)


@node
def test_touching_is_not_overlapping():
    placed = layout([("A", "12:00", "13:00"), ("B", "13:00", "15:00")])
    assert all(p["cols"] == 1 and p["col"] == 0 for p in placed.values())


@node
def test_a_block_widens_into_a_lane_nothing_beside_it_uses():
    placed = layout([("long", "09:00", "12:00"), ("a", "09:00", "10:00"),
                     ("b", "09:30", "10:00"), ("later", "10:00", "11:00")])
    assert placed["later"]["cols"] == 3
    assert (placed["later"]["col"], placed["later"]["span"]) == (1, 2)
    assert_nothing_hidden(placed)


@node
def test_a_job_past_midnight_is_drawn_on_both_days_to_and_from_midnight():
    got = run_js("""
        const job = {id: 'J2P6JO', starts_at: new Date('2026-09-14T07:30:00').toISOString(),
                     ends_at: new Date('2026-09-15T17:00:00').toISOString()}
        const days = g.visibleDays('Week view', new Date('2026-09-15T12:00:00'))
        const buckets = g.bucketByOverlap(days, [job])
        out({
          on: buckets.map((b, i) => b.length ? days[i].getDate() : null).filter(x => x),
          mon: g.layoutDay(days[1], [job])[0],
          tue: g.layoutDay(days[2], [job])[0],
          wed: g.layoutDay(days[3], [job]).length,
        })
    """)
    assert got["on"] == [14, 15]
    assert (got["mon"]["startMin"], got["mon"]["endMin"]) == (450, 1440)
    assert (got["mon"]["fromBefore"], got["mon"]["untilAfter"]) == (False, True)
    assert (got["tue"]["startMin"], got["tue"]["endMin"]) == (0, 1020)
    assert (got["tue"]["fromBefore"], got["tue"]["untilAfter"]) == (True, False)
    assert got["wed"] == 0


@node
def test_ending_exactly_at_midnight_stays_on_its_own_day():
    got = run_js("""
        const job = {starts_at: new Date('2026-09-14T22:00:00').toISOString(),
                     ends_at: new Date('2026-09-15T00:00:00').toISOString()}
        const days = g.visibleDays('Week view', new Date('2026-09-15T12:00:00'))
        const b = g.bucketByOverlap(days, [job])
        out({count: b.map(x => x.length), end: g.layoutDay(days[1], [job])[0].endMin,
             label: g.timeRange(job.starts_at, job.ends_at)})
    """)
    assert got["count"] == [0, 1, 0, 0, 0, 0, 0]
    assert got["end"] == 1440
    assert got["label"] == "10:00 PM \u2013 12:00 AM"


@node
def test_a_zero_length_booking_is_still_visible():
    placed = layout([("z", "09:00", "09:00")])
    assert placed["z"]["end"] - placed["z"]["start"] == 15


@node
@pytest.mark.parametrize("start,end,label", [
    ("2026-09-14T07:30:00", "2026-09-14T10:00:00", "7:30 \u2013 10:00 AM"),
    ("2026-09-14T11:30:00", "2026-09-14T13:00:00", "11:30 AM \u2013 1:00 PM"),
    ("2026-09-14T12:00:00", "2026-09-14T13:00:00", "12:00 \u2013 1:00 PM"),
    ("2026-09-14T15:00:00", "2026-09-14T17:00:00", "3:00 \u2013 5:00 PM"),
    ("2026-09-14T07:30:00", "2026-09-15T17:00:00", "Mon 7:30 AM \u2013 Tue 5:00 PM"),
])
def test_the_time_range_is_written_the_way_ghl_writes_it(start, end, label):
    got = run_js("out(g.timeRange(new Date('%s').toISOString(), new Date('%s').toISOString()))"
                 % (start, end))
    assert got == label


@node
def test_7_am_to_7_pm_fits_where_the_screen_allows():
    got = run_js("""
        out({first: g.FIRST_VISIBLE_HOUR, hours: g.VISIBLE_HOURS,
             tall: g.hourPxFor(780), laptop: g.hourPxFor(600), tiny: g.hourPxFor(300)})
    """)
    assert (got["first"], got["hours"]) == (7, 12)
    assert got["tall"] == 65 and got["tall"] * 12 <= 780
    assert got["laptop"] == 50 and got["laptop"] * 12 <= 600
    assert got["tiny"] == 48, "never unreadably small: scroll instead"


@node
def test_the_month_view_counts_every_booking_of_a_crowded_day():
    """Seven on the Monday: three chips and "+4 more", and the popover lists all seven."""
    got = run_js("""
        const days = g.visibleDays('Month view', new Date('2026-09-14T12:00:00'))
        const b = g.bucketByOverlap(days, %s)
        const i = days.findIndex(d => d.getDate() === 14 && d.getMonth() === 8)
        out({count: b[i].length, chips: g.MONTH_CELL_CHIPS})
    """ % items(MONDAY))
    assert got["count"] == 7
    assert got["count"] - got["chips"] == 4

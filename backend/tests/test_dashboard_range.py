"""The Dashboard's date-range control, executed rather than pattern-matched.

Same arrangement, and the same reasoning, as test_calendar_grid.py: this is
arithmetic, so a regex asserting that the page "mentions rangeWindow" would pass
against a version that computes the wrong window.
`frontend/src/lib/dashboardRange.ts` imports nothing and contains no React, so
node can run the real shipped module.

The bug they cover: the control offered "Last 7/30/90 days" while `/api/dashboard`
accepted no date parameter at all, so every card showed all-time figures under
whichever label had last been clicked.
"""
import json
import os
import shutil
import subprocess
import textwrap
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

RANGE_TS = (Path(__file__).resolve().parents[2]
            / "frontend" / "src" / "lib" / "dashboardRange.ts")

NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None,
    reason="node is not on PATH, so dashboardRange.ts cannot be executed. CI's "
           "backend job installs it precisely so this file is never skipped "
           "there — see .github/workflows/ci.yml.")



def run_js(body: str):
    """Execute `body` with the real module imported as `r`, and return its JSON.

    TZ is pinned only so a failure reads the same on any machine; the module
    works in UTC milliseconds on purpose, because the window it produces is sent
    to an API that stores UTC.
    """
    script = textwrap.dedent("""
        import * as r from %s
        const out = (v) => console.log('@@' + JSON.stringify(v))
    """) % json.dumps(RANGE_TS.as_posix()) + textwrap.dedent(body)
    proc = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-"],
        input=script, capture_output=True, text=True,
        env={**os.environ, "TZ": "America/New_York"})
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert line, "the script printed no result:\n" + proc.stdout + proc.stderr
    return json.loads(line[-1][2:])


@node
def test_all_time_is_the_default_and_asks_for_no_window():
    """The owner's decision (2026-09-10). Production's opportunities were created
    between 8 and 16 August; a 30-day default would have emptied this screen the
    moment the control started working, and a fix that reads as a regression is
    worse than the bug it fixes."""
    got = run_js("""
        out({default: r.DEFAULT_RANGE,
             window: r.rangeWindow(r.DEFAULT_RANGE),
             first: r.DASHBOARD_RANGES[0],
             all: [...r.DASHBOARD_RANGES]})
    """)
    assert got["default"] == "All time"
    assert got["window"] == {}, "the default range still sends a start date"
    assert got["first"] == "All time", "all time is not the first option offered"
    # The ranges the owner asked to keep offering.
    assert got["all"] == ["All time", "Last 7 days", "Last 30 days",
                          "Last 90 days", "Last 12 months"]


@node
def test_each_range_asks_for_the_window_it_names():
    got = run_js("""
        const now = new Date('2026-09-10T12:00:00Z')
        out(Object.fromEntries(r.DASHBOARD_RANGES.map(
            (label) => [label, r.rangeWindow(label, now)])))
    """)
    now = datetime(2026, 9, 10, 12, tzinfo=UTC)
    assert got["All time"] == {}
    for label, days in (("Last 7 days", 7), ("Last 30 days", 30),
                        ("Last 90 days", 90), ("Last 12 months", 365)):
        expected = (now - timedelta(days=days)).isoformat().replace("+00:00", "Z")
        assert got[label]["start"].replace(".000Z", "Z") == expected, label
        # No upper bound: an opportunity cannot be created in the future, so an
        # `end` would add a clock-skew edge for nothing.
        assert "end" not in got[label], "%s bounds the top of the window" % label


@node
def test_the_windows_nest_so_a_longer_range_can_only_add_deals():
    """"Last 90 days" that started later than "Last 30 days" would show FEWER
    opportunities than the shorter range — the failure mode is silent."""
    starts = run_js("""
        const now = new Date('2026-09-10T12:00:00Z')
        out(r.DASHBOARD_RANGES.slice(1).map((l) => r.rangeWindow(l, now).start))
    """)
    assert starts == sorted(starts, reverse=True), (
        "the ranges do not widen in the order they are offered: %s" % starts)


@node
def test_an_unrecognised_range_shows_everything_rather_than_nothing():
    """A stale label in localStorage, or a renamed range, must fall back to all
    time. Falling back to an empty window would render a dashboard of zeros that
    looks exactly like a company with no deals."""
    got = run_js("""
        out({unknown: r.rangeWindow('Last fortnight'),
             empty: r.rangeWindow(''),
             isAll: r.isAllTime('Last fortnight'),
             caption: r.rangeCaption('Last 30 days')})
    """)
    assert got["unknown"] == {} and got["empty"] == {}
    assert got["isAll"] is True
    assert got["caption"] == "Last 30 days"

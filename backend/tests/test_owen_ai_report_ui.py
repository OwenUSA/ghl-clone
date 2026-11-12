"""The after-inspection report editor in the browser (2026-11-12): executed where it can be.

**Executed under node.** `lib/owenAiReport.ts` is import-free, so the shipped helpers run here:
dragging a corner keeps it on the photo, a corner is added halfway along an edge, an area never
drops below three corners or grows past sixteen, a textarea becomes a clean list, a price is
caught before the server refuses it, and a saved copy's file name still gives its photo number.

**Asserted against source.** The Reports tab is React, which this project has no runner for.
Each assertion names the failure it would catch.
"""
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None, reason="node is not on PATH, so lib/owenAiReport.ts cannot be executed. CI's "
    "backend job installs it so this file is never skipped there.")


def run_js(body: str):
    script = textwrap.dedent("""
        import * as m from %s
        const out = (v) => console.log('@@' + JSON.stringify(v))
    """) % json.dumps((FRONTEND / "lib" / "owenAiReport.ts").as_uri()) + textwrap.dedent(body)
    proc = subprocess.run([NODE, "--experimental-strip-types", "--input-type=module", "-"],
                          input=script, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert line, "the script printed no result:\n" + proc.stdout + proc.stderr
    return json.loads(line[-1][2:])


def _read(*parts: str) -> str:
    return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


@node
def test_a_dragged_corner_stays_on_the_photo():
    got = run_js("""
        const a = m.newArea()
        out([m.movePoint(a, 0, -0.4, 1.7).points[0], m.movePoint(a, 2, 0.123456, 0.5).points[2],
             a.points[0]])
    """)
    assert got == [[0, 1], [0.123, 0.5], [0.35, 0.35]]     # the original is not changed


@node
def test_corners_are_added_halfway_and_an_area_keeps_three_to_sixteen():
    got = run_js("""
        const a = m.newArea()
        const b = m.addPoint(a, 1)
        let tri = { ...a, points: a.points.slice(0, 3) }
        let big = a
        for (let i = 0; i < 20; i++) big = m.addPoint(big, 0)
        out([b.points.length, b.points[2], m.removePoint(tri, 0).points.length, big.points.length])
    """)
    assert got == [5, [0.65, 0.5], 3, 16]


@node
def test_text_prices_and_photo_numbers():
    got = run_js("""
        out([m.linesToList(' a \\n\\n b\\n'), m.hasPrice('You pay $1,825'),
             m.hasPrice('300 dollars'), m.hasPrice('3 leaks, 2 days, 1 year'),
             m.photoNumber('lib/021-3fa9c1d2.jpg'), m.photoNumber('lib/104a-00aa11bb.jpg'),
             m.photoNumber('lib/photo-1.jpg')])
    """)
    assert got == [["a", "b"], True, True, False, 21, 104, None]


def test_a_save_names_the_version_the_editor_loaded():
    src = _read("components", "owenAi", "ReportsView.tsx")
    # Saving from the NEWEST version would silently overwrite what Owen AI saved meanwhile.
    assert "base_version: loadedVersion ??" in src
    # "Unsaved" measured against the newest version would freeze the editor after an AI save.
    assert "JSON.stringify(draft) !== loadedSpec" in src
    # A picture is paid for only when Owen ticks it: the save sends `regenerate` from that set.
    assert "regenerate: [...regen]" in src


def test_the_reports_tab_is_wired_in():
    assert "'reports'" in _read("lib", "owenAi.ts")
    assert "<ReportsView />" in _read("pages", "OwenAiPage.tsx")
    assert "onTab('reports')" in _read("components", "owenAi", "ThreadList.tsx")

"""The Dispatch page's wording and the bell's desktop notifications, executed under node
(2026-09-30). Both modules import nothing, so node runs the shipped files directly.

Pinned:
  * a limit reads "Overdue 2 h" / "Due in 40 min" / "Due Thu 6:00 PM" (New York time, whatever
    the laptop's zone);
  * a desktop notification is shown only for an alert that is NEW: the first list a tab sees
    is remembered silently (opening the CRM never replays old alerts), a read alert never
    shows, and an alert shows once.
"""
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib"
NODE = shutil.which("node")
node = pytest.mark.skipif(NODE is None, reason="node is not on PATH (CI installs it)")


def run_js(module: str, body: str, tz: str = "Europe/Berlin"):
    script = textwrap.dedent("""
        import * as m from %s
        const out = (v) => console.log('@@' + JSON.stringify(v))
    """) % json.dumps((SRC / module).as_uri()) + textwrap.dedent(body)
    proc = subprocess.run([NODE, "--experimental-strip-types", "--input-type=module", "-"],
                          input=script, capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "TZ": tz})
    assert proc.returncode == 0, proc.stderr
    return json.loads([ln for ln in proc.stdout.splitlines() if ln.startswith("@@")][-1][2:])


@node
def test_due_labels():
    now = "2026-09-30T18:00:00Z"                       # Wed 2:00 PM New York
    got = run_js("dispatch.ts", """
        const now = new Date('%s')
        out([m.dueLabel('2026-09-30T16:00:00Z', now), m.dueLabel('2026-09-30T18:40:00Z', now),
             m.dueLabel('2026-10-01T22:00:00Z', now), m.dueLabel(null, now),
             m.formatPhone('9549147244'),
             m.queueLabel({key: 'book', label: 'Book a visit', open: 3, urgent: 1}),
             m.queueLabel({key: 'stale', label: 'Gone quiet', open: 0, urgent: 0})])
    """ % now)
    assert got == ["Overdue 2 h", "Due in 40 min", "Due Thu 6:00 PM", "", "(954) 914-7244",
                   "Book 3!", "Quiet"]


@node
def test_desktop_notifications_only_for_new_unread_alerts():
    got = run_js("desktopAlerts.ts", """
        const seen = {primed: false, ids: new Set()}
        const first = m.fresh(seen, [{id: 1, read: false}, {id: 2, read: true}])
        const second = m.fresh(seen, [{id: 3, read: false}, {id: 4, read: true},
                                      {id: 1, read: false}])
        const third = m.fresh(seen, [{id: 3, read: false}])
        out([first.map(a => a.id), second.map(a => a.id), third.map(a => a.id)])
    """)
    assert got == [[], [3], []]

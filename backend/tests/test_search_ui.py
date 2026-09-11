"""The ctrl+K palette: its arithmetic executed, its wiring asserted against source.

Two halves, for two different reasons.

`frontend/src/lib/search.ts` is written free of React and of every import so node
can run the real shipped module — the same trick, and the same justification, as
test_calendar_grid.py. Arrow-key movement and "which record does Enter open" are
arithmetic, and a regex saying the file mentions `move` would pass against a
version that walks off the end of the list.

The rest — that the sidebar row is a button rather than the inert <div> it was,
that ctrl+K is bound, that the request is debounced — is structure, and this
project has no JS test runner, so it is asserted against source in the idiom of
test_frontend_nav.py.
"""
import json
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
SEARCH_TS = FRONTEND / "lib" / "search.ts"

NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None,
    reason="node is not on PATH, so search.ts cannot be executed. CI's backend "
           "job installs it precisely so this file is never skipped there — see "
           ".github/workflows/ci.yml.")


def run_js(body: str):
    """Execute `body` with the real module imported as `s`, and return its JSON."""
    script = textwrap.dedent("""
        import * as s from %s
        const out = (v) => console.log('@@' + JSON.stringify(v))
    """) % json.dumps(SEARCH_TS.as_posix()) + textwrap.dedent(body)
    proc = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-"],
        input=script, capture_output=True, text=True, env=dict(os.environ))
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert line, "the script printed no result:\n" + proc.stdout + proc.stderr
    return json.loads(line[-1][2:])


# A response in the shape /api/search really sends: three groups, the middle one
# empty, the last one capped.
RESPONSE = """
const res = {
  q: 'roof', limit: 2, total: 9,
  groups: [
    { type: 'contacts', label: 'Contacts', total: 1, truncated: false,
      items: [{ id: 11, name: 'Jane Doe', email: 'j@x.test', phone: '941' }] },
    { type: 'opportunities', label: 'Opportunities', total: 0, truncated: false,
      items: [] },
    { type: 'messages', label: 'Messages', total: 7, truncated: true,
      items: [{ id: 501, conversation_id: 77, contact_id: 11,
                contact_name: 'Jane Doe', type: 'SMS', direction: 'INBOUND',
                occurred_at: '2026-09-09T12:00:00Z', snippet: 'the roof leaks' },
              { id: 502, conversation_id: 78, contact_id: 12,
                contact_name: 'Marcus Webb', type: 'SMS', direction: 'INBOUND',
                occurred_at: '2026-09-08T12:00:00Z', snippet: 'roof again' }] },
  ],
}
"""


# ---------------- one navigable list across three headings ----------------

@node
def test_the_arrow_keys_walk_every_group_in_rendered_order():
    """The eye sees three lists; the keyboard has to see one, and an empty group
    in the middle must not leave a hole in it."""
    got = run_js(RESPONSE + """
        out(s.flatten(res).map((r) => [r.group, r.item.id]))
    """)
    assert got == [["contacts", 11], ["messages", 501], ["messages", 502]]


@node
def test_flatten_survives_no_response_at_all():
    """The palette renders before the first request resolves."""
    assert run_js("out([s.flatten(undefined), s.flatten(null)])") == [[], []]


@node
def test_the_selection_wraps_at_both_ends():
    got = run_js("""
        out({
          down_from_last: s.move(3, 2, 1),
          up_from_first: s.move(3, 0, -1),
          middle: s.move(3, 1, 1),
          empty: s.move(0, 0, 1),
        })
    """)
    assert got == {"down_from_last": 0, "up_from_first": 2, "middle": 2,
                   "empty": 0}


@node
def test_moving_never_returns_an_index_off_the_end():
    """Walking the whole list twice in both directions must stay in range —
    an off-by-one here opens the wrong record, silently."""
    got = run_js("""
        let i = 0
        const seen = []
        for (const d of [1, 1, 1, 1, 1, 1, -1, -1, -1, -1, -1, -1]) {
          i = s.move(3, i, d)
          seen.push(i)
        }
        out({ min: Math.min(...seen), max: Math.max(...seen) })
    """)
    assert got == {"min": 0, "max": 2}


# ---------------- where Enter goes ----------------

@node
def test_enter_on_a_message_opens_its_conversation_not_its_message_id():
    """There is no per-message screen. 501 is the event; 77 is the thread."""
    got = run_js(RESPONSE + """
        const rows = s.flatten(res)
        out(rows.map(s.target))
    """)
    assert got == [
        {"view": "contacts", "id": 11},
        {"view": "conversations", "id": 77},
        {"view": "conversations", "id": 78},
    ]


@node
def test_enter_on_an_opportunity_opens_the_opportunity():
    got = run_js("""
        out(s.target({ group: 'opportunities', item: { id: 42 } }))
    """)
    assert got == {"view": "opportunities", "id": 42}


# ---------------- the four states ----------------

@node
def test_the_palette_reports_idle_loading_empty_and_results_apart():
    got = run_js(RESPONSE + """
        out({
          nothing_typed: s.paletteState('', false, undefined),
          only_spaces: s.paletteState('   ', false, undefined),
          first_request: s.paletteState('roof', true, undefined),
          no_matches: s.paletteState('roof', false,
            { q: 'roof', limit: 5, total: 0, groups: [
              { type: 'contacts', label: 'Contacts', items: [], total: 0,
                truncated: false }] }),
          found: s.paletteState('roof', false, res),
        })
    """)
    assert got == {"nothing_typed": "idle", "only_spaces": "idle",
                   "first_request": "loading", "no_matches": "empty",
                   "found": "results"}


@node
def test_refining_a_query_keeps_the_previous_results_on_screen():
    """`loading` means "nothing to show yet". With results already in hand the
    state stays `results`, so the list does not flash between keystrokes."""
    assert run_js(RESPONSE + "out(s.paletteState('roofs', false, res))") == "results"


# ---------------- the cap is said out loud ----------------

@node
def test_a_capped_group_says_how_many_it_is_hiding():
    got = run_js(RESPONSE + """
        out({
          messages: s.capNote(res.groups[2]),
          contacts: s.capNote(res.groups[0]),
        })
    """)
    assert got["contacts"] is None, "a group that fits must not claim to be cut"
    assert "2" in got["messages"] and "7" in got["messages"], got["messages"]


# ---------------- wiring, asserted against source ----------------

def read(*parts):
    return (FRONTEND.joinpath(*parts)).read_text(encoding="utf-8")


def test_the_sidebar_search_row_is_a_real_control_now():
    """It was a <div> with no handler while showing a ctrlK badge — a control
    that advertised a shortcut that did nothing."""
    source = read("components", "Sidebar.tsx")
    row = source.split("aria-label=\"Search\"", 1)
    assert len(row) == 2, "the search row lost its accessible name"
    head = source[:source.index("aria-label=\"Search\"")]
    assert head.rstrip().endswith("onClick={onOpenSearch}"), \
        "the search row must call onOpenSearch"
    assert "<button" in head.rsplit("\n", 4)[0], "the row must be a button"


def test_the_search_row_keeps_its_measured_appearance():
    """The ROW is measured against GHL (the ctrlK badge is), even though the
    overlay it opens is ours. Turning it into a button must not restyle it."""
    source = read("components", "Sidebar.tsx")
    row = source.split("onClick={onOpenSearch}", 1)[1].split("</button>", 1)[0]
    for measured in ("height: 32", "borderRadius: 6",
                     "backgroundColor: 'rgb(26,32,44)'",
                     "color: 'rgb(152,162,179)'", "fontSize: 14", "ctrlK"):
        assert measured in row, "the search row lost %r" % measured


def test_ctrl_k_and_cmd_k_both_open_the_palette_from_anywhere():
    source = read("App.tsx")
    binding = source.split("const onKey = (e: KeyboardEvent)", 1)[1].split("}\n", 1)[0]
    assert "e.ctrlKey" in binding and "e.metaKey" in binding, \
        "cmd+K is the Mac spelling of the same shortcut"
    assert "'k'" in binding
    assert "e.preventDefault()" in binding, \
        "without this the browser's own ctrl+K takes the keystroke"
    assert "window.addEventListener('keydown'" in source
    assert "window.removeEventListener('keydown'" in source, "the listener leaks"


def test_the_palette_debounces_instead_of_firing_per_keystroke():
    source = read("components", "SearchPalette.tsx")
    assert "SEARCH_DEBOUNCE_MS" in source
    debounce = source.split("useEffect(() => {\n    const t = setTimeout", 1)
    assert len(debounce) == 2, "the debounce effect is gone"
    assert "clearTimeout(t)" in debounce[1], \
        "without the cleanup every keystroke still reaches the server"


def test_escape_closes_the_palette_and_gives_focus_back():
    source = read("components", "SearchPalette.tsx")
    assert "returnTo.current = document.activeElement" in source
    assert "returnTo.current?.focus?.()" in source
    assert re.search(r"e\.key === 'Escape'[\s\S]{0,80}onClose\(\)", source), \
        "Escape must close"


def test_the_palette_asks_the_one_endpoint_not_three():
    """A fan-out would put three requests per debounced keystroke on the wire and
    scatter ranking, the cap and the role rule across three call sites."""
    source = read("components", "SearchPalette.tsx")
    assert "globalSearch" in source
    for fanned_out in ("listContacts", "listOpportunities", "listMessages"):
        assert fanned_out not in source, "the palette must not fan out"
    assert read("lib", "api.ts").count("`/api/search?q=") == 1, \
        "exactly one call site builds the search URL"


def test_a_searched_record_opens_the_same_panel_a_clicked_row_opens():
    """The palette hands the shell a view and an id; each page opens its own
    detail panel. A second rendering of a contact would be a second thing to
    keep in step with the measured one."""
    app = read("App.tsx")
    assert "onOpen={openRecord}" in app
    for page, state in (("ContactsPage", "setOpenContact"),
                        ("OpportunitiesPage", "setOpenOpp"),
                        ("ConversationsPage", "setSelected")):
        assert "focus={focusFor(" in app
        source = read("pages", page + ".tsx")
        # The effect keyed on `focus`, whatever else it has grown to do.
        # (Conversations now also widens its scope and clears its in-place
        # search, so that the thread the palette asked for is one the narrowed
        # list can actually show -- it opens the record either way, which is
        # what this pins.)
        effect = source.split("}, [focus])", 1)
        assert len(effect) == 2, "%s has no effect keyed on focus" % page
        body = effect[0].rsplit("useEffect(", 1)[1]
        assert re.search(r"%s\(focus\.id\)" % state, body), page


def test_the_internal_comment_filter_is_disabled_for_a_tech_not_removed():
    """Internal notes are staff-only, and the backend 403s a TECH who asks for
    them. Disabled-with-a-reason is this project's precedent (DECISIONS.md);
    silently shortening the menu is not."""
    source = read("pages", "ConversationsPage.tsx")
    assert "user.role === 'TECH'" in source
    assert "internal_comment" in source
    assert "staff-only" in source
    assert "{ key: 'internal_comment', label: 'Internal Comment' }," in source, \
        "the row itself must still be rendered"

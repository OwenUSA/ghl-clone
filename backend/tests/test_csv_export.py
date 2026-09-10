"""The CSV export, executed rather than pattern-matched.

Same reasoning as `test_calendar_grid.py`: "the export contains exactly the
selected rows" is a claim about behaviour, and a regex over the page source cannot
check it. `frontend/src/lib/csv.ts` is therefore written free of React and of any
import, so node can run the real shipped module.

The failures this covers are all invisible on screen and only surface when someone
reconciles the file against the board: a row silently dropped or duplicated, a
comma in a business name splitting a row into two columns, `$9,500.00` landing in
a spreadsheet as text, a cent lost to a float, and a contact name that begins with
`=` being executed as a formula on open.
"""
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

CSV_TS = (Path(__file__).resolve().parents[2]
          / "frontend" / "src" / "lib" / "csv.ts")

NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None,
    reason="node is not on PATH, so csv.ts cannot be executed. CI's backend job "
           "installs it precisely so this file is never skipped there — see "
           ".github/workflows/ci.yml.")


def run_js(body: str):
    """Execute `body` with the real module imported as `c`, and return its JSON."""
    script = textwrap.dedent("""
        import * as c from %s
        const out = (v) => console.log('@@' + JSON.stringify(v))
    """) % json.dumps(CSV_TS.as_posix()) + textwrap.dedent(body)
    proc = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-"],
        input=script, capture_output=True, text=True,
        env={**os.environ, "TZ": "America/New_York"})
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert line, "the script printed no result:\n" + proc.stdout + proc.stderr
    return json.loads(line[-1][2:])


# The fixture rows, restated here so the assertions are hand-checkable. Note the
# awkward ones: a comma, an embedded quote, a newline, a leading `=`, and 9500.005
# dollars — the amount that breaks a float conversion.
ROWS = """
  const rows = [
    {id: 11, title: 'Jane roof', value_cents: 950001, stage_id: 1, status: 'open',
     contact_name: 'Jane Doe', business_name: 'Doe, Roofing & Co',
     source: 'Google LSA', updated_at: '2026-09-01T10:00:00Z'},
    {id: 12, title: 'Bob "Big" Smith', value_cents: 0, stage_id: 2, status: 'won',
     contact_name: null, business_name: null, source: null,
     updated_at: '2026-09-02T10:00:00Z'},
    {id: 13, title: 'Line\\nbreak', value_cents: 1, stage_id: 2, status: 'lost',
     contact_name: '=cmd|calc', business_name: '  padded  ', source: 'Referral',
     updated_at: '2026-09-03T10:00:00Z'},
  ]
  const names = new Map([[1, 'New Lead'], [2, 'Call Back']])
  const stageName = (id) => names.get(id) ?? ''
"""


def _parse(text: str) -> list[list[str]]:
    """Read the CSV back with Python's own parser.

    Deliberately not a hand-rolled `split(',')`: the whole point is that a real
    reader recovers the exact values, and the stdlib module is a reader nobody
    here wrote.
    """
    import csv
    import io
    return list(csv.reader(io.StringIO(text)))


# ---------------- exactly the rows it was given ----------------

@node
def test_the_export_holds_exactly_the_rows_it_was_given():
    text = run_js(ROWS + """
        out(c.opportunitiesCsv(rows, stageName))
    """)
    got = _parse(text)
    assert got[0] == c_headers()
    assert len(got) == 4, "a row was dropped or duplicated"
    assert [r[0] for r in got[1:]] == ["11", "12", "13"]


@node
def test_a_selection_of_one_exports_one_row():
    text = run_js(ROWS + """
        out(c.opportunitiesCsv([rows[1]], stageName))
    """)
    got = _parse(text)
    assert len(got) == 2 and got[1][0] == "12"


@node
def test_an_empty_selection_is_a_header_and_nothing_else():
    """Not an empty file: a spreadsheet with no header row reads as corrupt."""
    text = run_js("out(c.opportunitiesCsv([], () => ''))")
    assert _parse(text) == [c_headers()]


@node
def test_the_order_given_is_the_order_written():
    text = run_js(ROWS + """
        out(c.opportunitiesCsv([rows[2], rows[0]], stageName))
    """)
    assert [r[0] for r in _parse(text)[1:]] == ["13", "11"]


def c_headers() -> list[str]:
    return ["Id", "Opportunity name", "Stage", "Status", "Value", "Contact",
            "Business name", "Source", "Updated"]


# ---------------- the values survive the round trip ----------------

@node
def test_a_comma_a_quote_and_a_newline_do_not_split_a_row():
    text = run_js(ROWS + "out(c.opportunitiesCsv(rows, stageName))")
    got = _parse(text)
    assert all(len(r) == 9 for r in got), "a field leaked into the next column"
    assert got[1][6] == "Doe, Roofing & Co"
    assert got[2][1] == 'Bob "Big" Smith'
    assert got[3][1] == "Line\nbreak"


@node
def test_money_is_a_plain_decimal_and_keeps_its_cents():
    """`money()` renders `$9,500.01`, which a spreadsheet imports as text — and
    `cents / 100` is the float round trip `centsFromDollars` exists to avoid."""
    got = run_js("""
        out([950001, 0, 1, 99, 100, 12345678, 950000, -250].map(c.centsToDecimal))
    """)
    assert got == ["9500.01", "0.00", "0.01", "0.99", "1.00", "123456.78",
                   "9500.00", "-2.50"]


@node
def test_the_two_call_back_stages_are_told_apart_by_id():
    """The measured pipeline has two distinct stages both named "Call Back", so a
    row carries a stage id and the board resolves it."""
    text = run_js(ROWS + "out(c.opportunitiesCsv(rows, stageName))")
    got = _parse(text)
    assert [r[2] for r in got[1:]] == ["New Lead", "Call Back", "Call Back"]


@node
def test_an_empty_field_is_empty_and_not_the_word_null():
    text = run_js(ROWS + "out(c.opportunitiesCsv(rows, stageName))")
    row = _parse(text)[2]
    assert row[5] == "" and row[6] == "" and row[7] == ""


@node
def test_padding_survives_the_round_trip():
    """Unquoted leading spaces are trimmed by most importers."""
    text = run_js(ROWS + "out(c.opportunitiesCsv(rows, stageName))")
    assert _parse(text)[3][6] == "  padded  "


# ---------------- a spreadsheet is an interpreter ----------------

@node
def test_a_field_that_starts_like_a_formula_is_defused():
    """A contact called `=cmd|...` is a name, not an instruction."""
    got = run_js("""
        out(['=1+1', '+1', '@SUM(A1)', '\\tlead', 'Jane', '-5', '2026-09-10']
            .map(c.csvField))
    """)
    # Quoted or not, none of the dangerous ones may still LEAD with the trigger.
    assert got[0] == "'=1+1" and got[1] == "'+1" and got[2] == "'@SUM(A1)"
    assert got[3] == "'\tlead"
    # ...and a plain value is left completely alone, including a negative number
    # and a date, which are legitimate values far more often than they are attacks.
    assert got[4] == "Jane" and got[5] == "-5" and got[6] == "2026-09-10"


@node
def test_the_document_uses_crlf_like_every_spreadsheet_expects():
    text = run_js("out(c.toCsv(['a', 'b'], [[1, 2], [3, 4]]))")
    assert text == "a,b\r\n1,2\r\n3,4"


@node
def test_the_filename_carries_the_date_and_no_path_separator():
    got = run_js("""
        out([c.csvFilename('Dream Team Roofing AHS', new Date(2026, 8, 10)),
             c.csvFilename('a/b\\\\c', new Date(2026, 0, 5))])
    """)
    assert got[0] == "dream-team-roofing-ahs-2026-09-10.csv"
    assert "/" not in got[1] and "\\" not in got[1]
    assert got[1] == "a-b-c-2026-01-05.csv"

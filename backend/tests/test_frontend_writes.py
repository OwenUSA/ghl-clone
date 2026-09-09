"""Every cookie-authenticated write from the browser must carry the CSRF token.

There is no JS test runner in this project, so this invariant is asserted against
the source of `frontend/src/lib/api.ts` instead. It is not a style check: a write
issued with a bare `fetch()` reaches the backend without `X-CSRF-Token`, and
`require_auth` rejects it with 403 "csrf token missing or mismatched". That is
exactly how the kanban drag was dead in production — `moveOpportunity` called
`fetch()` directly while every other write went through `send()`.

`send()` is the one place that attaches `cookie('ghl_csrf')`, so "the only helpers
that call fetch are get/send/refresh" is the same statement as "every write is
CSRF-signed", and it catches the next write that forgets.
"""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

API_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "api.ts"

# The three transport helpers. `get` is read-only, `refresh` signs itself by hand
# because it runs when `send` would recurse, and `send` is the CSRF-signing path.
TRANSPORT_HELPERS = {"get", "send", "refresh"}

# A top-level `export function x`, `function x`, `export const x =` or `const x =`.
DECL = re.compile(
    r"^(?:export\s+)?(?:async\s+)?(?:function\s+(\w+)|const\s+(\w+)\s*=)")


def _fetch_callers(source: str) -> set[str]:
    """Name of the nearest enclosing top-level declaration for each `fetch(` call."""
    current, callers = None, set()
    for line in source.splitlines():
        if line.lstrip().startswith(("//", "*", "/*")):
            continue  # comments mention fetch() precisely because it is banned here
        m = DECL.match(line)
        if m:
            current = m.group(1) or m.group(2)
        if re.search(r"\bfetch\(", line):
            callers.add(current)
    return callers


def test_only_the_transport_helpers_call_fetch():
    callers = _fetch_callers(API_TS.read_text(encoding="utf-8"))
    assert callers, "found no fetch() calls at all — the scanner is broken"
    assert callers <= TRANSPORT_HELPERS, (
        f"{sorted(callers - TRANSPORT_HELPERS)} call fetch() directly and so send no "
        "X-CSRF-Token; route them through send()")


def test_move_opportunity_goes_through_send():
    """The regression itself: the kanban drag must use the CSRF-signing path."""
    source = API_TS.read_text(encoding="utf-8")
    body = source.split("moveOpportunity", 1)[1].split("export type", 1)[0]
    assert "send<" in body, "moveOpportunity must issue its PATCH via send()"


def test_create_opportunity_goes_through_send():
    """Add opportunity is a cookie-authenticated write like the drag above it."""
    source = API_TS.read_text(encoding="utf-8")
    body = source.split("createOpportunity", 1)[1].split("export const createContact", 1)[0]
    assert "send<" in body, "createOpportunity must issue its POST via send()"


# ---- money -------------------------------------------------------------------
#
# The money inputs send integer cents. `Math.round(Number(v) * 100)` looks like the
# obvious conversion and quietly loses a cent: 9500.005 * 100 is 950000.49999999994
# in IEEE-754, so the half rounds down. `centsFromDollars` does the arithmetic on
# the decimal digits instead, and is executed here rather than only read.

FRONTEND = API_TS.parents[1]

CENTS_CASES = [
    ("", 0),
    ("9500", 950000),
    ("9500.00", 950000),
    ("9500.004", 950000),
    ("9500.005", 950001),   # the float path returns 950000
    ("9500.995", 950100),
    ("0.1", 10),
    (".5", 50),
    ("12345678.99", 1234567899),
    ("1e3", 100000),        # a number input can still hand back exponent notation
]


def _cents_from_dollars_js() -> str:
    """The real helper out of api.ts, with its two type annotations stripped.

    Reading the shipped source rather than a copy is the point: a copy would keep
    passing after someone changed the helper back to the float conversion.
    """
    src = API_TS.read_text(encoding="utf-8")
    start = src.index("export function centsFromDollars")
    end = src.index("\n}\n", start) + 3
    js = (src[start:end]
          .replace("export function", "function")
          .replace("(input: string): number", "(input)"))
    assert ": string" not in js and ": number" not in js, (
        "the helper's signature changed — retarget this test")
    return js


def test_the_money_input_does_not_lose_or_invent_a_cent():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; the frontend build job in CI runs this")

    typed = json.dumps([case for case, _ in CENTS_CASES])
    script = "%s\nconsole.log(JSON.stringify(%s.map(centsFromDollars)))" % (
        _cents_from_dollars_js(), typed)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "cents.mjs"
        path.write_text(script, encoding="utf-8")
        out = subprocess.run([node, str(path)], capture_output=True, text=True,
                             check=True).stdout
    got = json.loads(out)
    assert got == [cents for _, cents in CENTS_CASES], dict(
        zip([c for c, _ in CENTS_CASES], got, strict=True))


def test_both_money_inputs_use_the_shared_conversion():
    """Create and edit must convert the same way, or the same typed amount saves as
    two different numbers depending on which form the user reached."""
    for parts in (("pages", "OpportunitiesPage.tsx"),
                  ("components", "OpportunityDetail.tsx")):
        source = FRONTEND.joinpath(*parts).read_text(encoding="utf-8")
        assert "centsFromDollars(" in source, f"{parts[-1]} converts money by hand"
        assert "Number(e.target.value) * 100" not in source, (
            f"{parts[-1]} is back on the float conversion")

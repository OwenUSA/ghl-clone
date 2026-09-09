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
import re
from pathlib import Path

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

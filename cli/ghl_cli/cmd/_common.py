"""Shared option types and the client factory.

Every command wraps its body in `run()` so a CliError becomes a clean message and
exit code rather than a traceback.
"""
from collections.abc import Callable
from datetime import UTC
from typing import Annotated

import typer

from .. import output
from ..client import Client
from ..errors import CliError

# NOTE: there is deliberately no per-command JsonOpt. `--json` is a single global
# option hoisted to the front of argv by main._hoist_json_flag, so declaring it
# again per command would render a second, non-functional --json in that command's
# help.
YesOpt = Annotated[bool, typer.Option("--yes", "-y",
                                      help="Confirm. Required when there is no "
                                           "terminal to prompt on.")]


def client() -> Client:
    return Client()


def run(fn: Callable):
    """Execute a command body, converting CliError into exit code + message."""
    try:
        return fn()
    except CliError as exc:
        output.fail(exc)


def parse_since(value: str | None) -> str | None:
    """Accept `7d`, `24h`, `30m` or an ISO date/timestamp.

    Relative windows are what an agent reaches for ("calls in the last week") and
    spelling out an ISO timestamp for that is a needless chance to get it wrong.
    """
    if not value:
        return None
    from datetime import datetime, timedelta
    raw = value.strip()
    units = {"d": "days", "h": "hours", "m": "minutes", "w": "weeks"}
    if len(raw) > 1 and raw[-1].lower() in units and raw[:-1].isdigit():
        delta = timedelta(**{units[raw[-1].lower()]: int(raw[:-1])})
        return (datetime.now(UTC) - delta).isoformat()
    return raw


def money_to_cents(value: str | None) -> int | None:
    """`1250`, `1250.50` and `$1,250.50` all mean 125050 cents."""
    if value is None:
        return None
    from ..errors import Rejected
    cleaned = str(value).replace("$", "").replace(",", "").strip()
    try:
        return round(float(cleaned) * 100)
    except ValueError as exc:
        raise Rejected("could not read %r as an amount — try 1250 or 1250.50"
                       % value) from exc

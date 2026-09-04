"""Rendering.

Two audiences. A person reads aligned columns; an agent parses `--json`. In JSON
mode stdout carries the API payload and NOTHING else — no banners, no progress —
so `ghl ... --json | jq` is always safe. Errors go to stderr in both modes.
"""
import json
import sys
from datetime import datetime

import typer

# Set by the root callback so commands don't have to thread it through.
JSON_MODE = False


def set_json(enabled: bool) -> None:
    global JSON_MODE
    JSON_MODE = enabled


def money(cents: int | None) -> str:
    """Cents to a display string. Never a float anywhere else — see models.py."""
    return "-" if cents is None else "$%.2f" % (cents / 100)


def when(value) -> str:
    """Trim ISO timestamps to something scannable; pass anything else through."""
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    return dt.strftime("%Y-%m-%d %H:%M")


def _cell(value) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def table(rows: list[dict], columns: list[tuple[str, str]]) -> None:
    """`columns` is [(key, heading)]. Widths come from the data."""
    if not rows:
        typer.echo("(none)")
        return
    keys = [k for k, _ in columns]
    heads = [h for _, h in columns]
    widths = [max(len(h), *(len(_cell(r.get(k))) for r in rows))
              for k, h in zip(keys, heads, strict=False)]

    typer.echo("  ".join(h.ljust(w) for h, w in zip(heads, widths, strict=False)).rstrip())
    typer.echo("  ".join("-" * w for w in widths))
    for r in rows:
        typer.echo("  ".join(_cell(r.get(k)).ljust(w)
                             for k, w in zip(keys, widths, strict=False)).rstrip())


def detail(obj: dict, fields: list[tuple[str, str]] | None = None) -> None:
    pairs = ([(label, obj.get(key)) for key, label in fields] if fields
             else list(obj.items()))
    width = max((len(str(k)) for k, _ in pairs), default=0)
    for label, value in pairs:
        typer.echo("%s  %s" % (str(label).ljust(width), _cell(value)))


def emit(payload, *, render=None) -> None:
    """The single exit point for success output.

    In --json mode print the payload verbatim, so what an agent parses is exactly
    what the API returned and the CLI is never a lossy translation layer.
    """
    if JSON_MODE:
        typer.echo(json.dumps(payload, indent=2, default=str))
    elif render is not None:
        render(payload)
    else:
        typer.echo(json.dumps(payload, indent=2, default=str))


def note(message: str) -> None:
    """Human-only commentary. Suppressed in JSON mode to keep stdout clean."""
    if not JSON_MODE:
        typer.echo(message)


def fail(exc) -> None:
    """Print an error to stderr and exit with its code.

    Uses SystemExit rather than typer.Exit so it behaves identically whether it is
    raised inside a command (where click is handling exits) or from the top-level
    wrapper in main().
    """
    from .errors import CliError
    code = getattr(exc, "code", 1)
    if JSON_MODE:
        body = {"error": str(exc), "code": getattr(exc, "kind", "error")}
        if isinstance(exc, CliError) and exc.detail:
            body.update(exc.detail)
        print(json.dumps(body, indent=2, default=str), file=sys.stderr)
    else:
        print("error: %s" % exc, file=sys.stderr)
        if isinstance(exc, CliError) and exc.detail.get("candidates"):
            for c in exc.detail["candidates"]:
                print("  %s" % c, file=sys.stderr)
    sys.exit(code)


def confirm(action: str, yes: bool) -> None:
    """Guard for anything that reaches a customer or destroys a record.

    Non-interactive callers (Claude Code runs this through Bash, with no TTY) must
    pass --yes explicitly. Refusing is deliberate: prompting would hang forever
    with nobody to answer, and proceeding silently is how an agent accidentally
    texts a customer.
    """
    import os

    from .errors import NeedsConfirmation
    if yes or os.environ.get("GHL_ASSUME_YES") == "1":
        return

    refusal = NeedsConfirmation(
        "refusing to %s without --yes" % action,
        {"hint": "re-run with --yes, or set GHL_ASSUME_YES=1"})

    # isatty() alone is not enough: under some runners stdin looks like a terminal
    # but reads EOF immediately, which turns the prompt into a click Abort and a
    # confusing exit 1. Treat every "could not get a real answer" the same way, so
    # a non-interactive caller always gets exit 2 and a message naming the fix.
    if JSON_MODE or not sys.stdin.isatty():
        raise refusal
    try:
        answered = typer.confirm("About to %s. Continue?" % action)
    except (EOFError, KeyboardInterrupt, typer.Abort, click_abort()) as exc:
        raise refusal from exc
    if not answered:
        raise NeedsConfirmation("cancelled")


def click_abort():
    """click.exceptions.Abort, fetched lazily so output.py imports cheaply."""
    import click
    return click.exceptions.Abort

"""ghl — drive the Dream Team Roofing CRM from the command line.

Designed to be driven by a person OR by Claude Code over Bash, so:

  * every command takes --json and prints exactly the API payload
  * names work wherever ids do: --contact "jane doe", --stage "Inspection"
  * an ambiguous name lists every candidate with its id instead of guessing
  * failures use distinct exit codes (see `ghl exit-codes`)
  * anything that reaches a customer or destroys a row needs --yes
"""
import typer

from . import output
from .cmd import appts, auth_cmd, calls, contacts, convos, jobs, msg, opps, reports, users
from .errors import CliError

app = typer.Typer(
    name="ghl",
    help=__doc__,
    no_args_is_help=True,
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)

app.add_typer(auth_cmd.app, name="auth", help="Log in, and manage API tokens.")
app.add_typer(contacts.app, name="contacts", help="Clients: find, add, edit, tag.")
app.add_typer(convos.app, name="convos", help="Conversation threads.")
app.add_typer(msg.app, name="msg", help="Send texts and emails; search messages.")
app.add_typer(calls.app, name="calls", help="Search call records and transcripts.")
app.add_typer(opps.app, name="opps", help="Opportunities: create, move, update.")
app.add_typer(appts.app, name="appts", help="Appointments: book, edit, cancel.")
app.add_typer(reports.app, name="reports", help="Dashboard and reports.")
app.add_typer(users.app, name="users", help="Staff accounts (admin).")
app.add_typer(jobs.app, name="jobs", help="Automation queue (admin).")


@app.callback()
def root(
    json_out: bool = typer.Option(
        False, "--json", help="Emit the raw API payload as JSON on stdout. "
                              "Accepted anywhere on the command line."),
):
    """Global options apply to every subcommand."""
    import os
    output.set_json(json_out or os.environ.get("GHL_JSON") == "1")


@app.command()
def health():
    """Check the backend is up, and which message transport it is using."""
    from .client import Client
    data = Client().get("/api/health")
    output.emit(data, render=lambda d: output.detail(d))


@app.command("exit-codes")
def exit_codes():
    """Explain the exit codes, for scripting."""
    rows = [
        {"code": 0, "meaning": "success"},
        {"code": 1, "meaning": "unexpected error"},
        {"code": 2, "meaning": "usage error, or missing --yes on a guarded command"},
        {"code": 3, "meaning": "not authenticated - run: ghl auth login"},
        {"code": 4, "meaning": "not found (404, or a name matched nothing)"},
        {"code": 5, "meaning": "ambiguous name - several records matched"},
        {"code": 6, "meaning": "backend unreachable"},
        {"code": 7, "meaning": "forbidden - your role may not do this"},
        {"code": 8, "meaning": "rejected (validation error or conflict)"},
    ]
    output.emit(rows, render=lambda r: output.table(
        r, [("code", "CODE"), ("meaning", "MEANING")]))


def _hoist_json_flag(argv: list[str]) -> list[str]:
    """Let --json appear anywhere, not just before the subcommand.

    Typer binds callback options to the group, so strictly `ghl --json contacts
    list` is the only valid placement — but `ghl contacts list --json` is what
    anyone (and any agent) writes first, and failing on it with "No such option"
    is a bad first impression of the tool.

    Only a standalone `--json` token is hoisted. `--json=...` and any occurrence
    after `--` are left alone, so it can still be passed as a value if a message
    body ever needs to contain it.
    """
    cut = argv.index("--") if "--" in argv else len(argv)
    # Check the head only. Testing the whole of argv first would treat a --json
    # that appears solely AFTER the -- separator as a flag to hoist.
    if "--json" not in argv[:cut]:
        return argv
    head = [a for a in argv[:cut] if a != "--json"]
    return ["--json", *head, *argv[cut:]]


def main() -> None:
    """Entry point. Converts CliError into a clean message plus exit code."""
    import sys
    try:
        app(args=_hoist_json_flag(sys.argv[1:]))
    except CliError as exc:
        output.fail(exc)


if __name__ == "__main__":
    main()

"""Conversation threads: list, read, mark read, star."""
import typer

from .. import output, resolve
from ._common import client, run

app = typer.Typer(no_args_is_help=True, help=__doc__)


@app.command("list")
def list_convos(
    tab: str = typer.Option("all", "--tab",
                            help="unread | all | recent | starred"),
    sort: str = typer.Option("latest", "--sort", help="latest | oldest"),
):
    """List conversation threads.

        ghl convos list --tab unread --json
    """
    def body():
        rows = client().get("/api/conversations", params={"tab": tab, "sort": sort})
        for r in rows:
            r["last"] = output.when(r["last_event_at"])
            r["star"] = "*" if r["starred"] else ""
        output.emit(rows, render=lambda rs: (
            output.table(rs, [
                ("id", "ID"), ("contact_name", "CONTACT"),
                ("contact_phone", "PHONE"), ("unread_count", "UNREAD"),
                ("star", "*"), ("last", "LAST ACTIVITY")]),
            output.note("\n%d threads" % len(rs)),
        ))
    run(body)


@app.command()
def show(
    ref: str = typer.Argument(..., help="Thread id, contact id, or contact name."),
    filter_: str = typer.Option("all", "--filter",
                                help="all | conversations | activities | SMS | "
                                     "CALL | EMAIL | NOTE | APPOINTMENT | ..."),
    limit: int = typer.Option(50, "--limit", help="Show the last N entries."),
):
    """Show a thread's timeline: texts, calls and activities interleaved.

        ghl convos show "jane doe"
        ghl convos show 12 --filter CALL
    """
    def body():
        c = client()
        conv = resolve.conversation(c, ref)
        rows = c.get("/api/conversations/%d/events" % conv["id"],
                     params={"filter": filter_})
        shown = rows[-limit:]
        for r in shown:
            r["at"] = output.when(r["occurred_at"])
            r["dir"] = "->" if r["direction"] == "OUTBOUND" else "<-"
            r["text"] = (r.get("body") or
                         ("call %ss" % r["duration_seconds"]
                          if r.get("duration_seconds") is not None else ""))[:70]
        output.emit(rows, render=lambda _: (
            output.note("Thread %d — %s (%s)\n"
                        % (conv["id"], conv["contact_name"],
                           conv["contact_phone"] or "no phone")),
            output.table(shown, [
                ("at", "WHEN"), ("dir", ""), ("type", "TYPE"),
                ("delivery_status", "STATUS"), ("text", "TEXT")]),
        ))
    run(body)


def _patch(ref: str, payload: dict, message: str):
    def body():
        c = client()
        conv = resolve.conversation(c, ref)
        data = c.patch("/api/conversations/%d" % conv["id"], json=payload)
        output.emit(data, render=lambda d: output.note(
            message % d["contact_name"]))
    run(body)


@app.command()
def read(ref: str):
    """Mark a thread read (clears its unread badge)."""
    _patch(ref, {"read": True}, "Marked %s's thread read.")


@app.command()
def unread(ref: str):
    """Mark a thread unread."""
    _patch(ref, {"read": False}, "Marked %s's thread unread.")


@app.command()
def star(ref: str):
    """Star a thread."""
    _patch(ref, {"starred": True}, "Starred %s's thread.")


@app.command()
def unstar(ref: str):
    """Unstar a thread."""
    _patch(ref, {"starred": False}, "Unstarred %s's thread.")

"""Search call records, including transcripts."""
import typer

from .. import output, resolve
from ._common import client, parse_since, run

app = typer.Typer(no_args_is_help=True, help=__doc__)


@app.command("list")
def list_calls(
    q: str = typer.Option(None, "--q", "-q",
                          help="Search the TRANSCRIPT as well as the body."),
    direction: str = typer.Option("all", "--direction",
                                  help="all | INBOUND | OUTBOUND"),
    status: str = typer.Option(None, "--status",
                               help="completed, no-answer, busy, voicemail, "
                                    "failed. Comma-separate for several."),
    min_duration: int = typer.Option(None, "--min-duration", help="Seconds."),
    max_duration: int = typer.Option(None, "--max-duration", help="Seconds."),
    has_recording: bool = typer.Option(None, "--recording/--no-recording"),
    contact: str = typer.Option(None, "--contact", "-c"),
    since: str = typer.Option(None, "--since", help="7d, 24h, or a date."),
    until: str = typer.Option(None, "--until"),
    page: int = typer.Option(1, "--page"),
    page_size: int = typer.Option(50, "--page-size"),
):
    """Search calls across every conversation.

    `--q` matches the transcript, so you can find a call by what was said:

        ghl calls list --q skylight --json
        ghl calls list --since 7d --direction INBOUND --status no-answer
        ghl calls list --min-duration 300 --recording
    """
    def body():
        c = client()
        params = {"q": q, "direction": direction, "call_status": status,
                  "min_duration": min_duration, "max_duration": max_duration,
                  "start": parse_since(since), "end": parse_since(until),
                  "page": page, "page_size": page_size}
        if has_recording is not None:
            params["has_recording"] = str(has_recording).lower()
        if contact:
            params["contact_id"] = resolve.contact(c, contact)["id"]

        data = c.get("/api/calls", params=params)
        for r in data["items"]:
            r["at"] = output.when(r["occurred_at"])
            r["mins"] = ("%d:%02d" % divmod(r["duration_seconds"], 60)
                         if r.get("duration_seconds") is not None else "-")
            r["rec"] = "yes" if r.get("recording_url") else "-"
        output.emit(data, render=lambda d: (
            output.table(d["items"], [
                ("id", "ID"), ("at", "WHEN"), ("contact_name", "CONTACT"),
                ("direction", "DIR"), ("call_status", "STATUS"),
                ("mins", "LENGTH"), ("rec", "REC")]),
            output.note("\n%d calls" % d["total"]),
        ))
    run(body)


@app.command()
def show(call_id: int = typer.Argument(..., help="Call event id.")):
    """Show one call in full, including its transcript."""
    def body():
        data = client().get("/api/calls", params={"page_size": 200})
        match = [i for i in data["items"] if i["id"] == call_id]
        if not match:
            from ..errors import NotFound
            raise NotFound("no call with id %d in the recent window" % call_id)
        output.emit(match[0], render=lambda d: (
            output.detail(d, [
                ("id", "id"), ("contact_name", "contact"),
                ("contact_phone", "phone"), ("occurred_at", "when"),
                ("direction", "direction"), ("call_status", "status"),
                ("duration_seconds", "seconds"), ("recording_url", "recording")]),
            output.note("\ntranscript:\n%s" % (d.get("transcript") or "(none)")),
        ))
    run(body)

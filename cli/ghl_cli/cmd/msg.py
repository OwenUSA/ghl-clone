"""Send texts and emails, add notes, and search messages."""
import typer

from .. import output, resolve
from ._common import YesOpt, client, parse_since, run

app = typer.Typer(no_args_is_help=True, help=__doc__)


@app.command()
def send(
    contact: str = typer.Option(..., "--contact", "-c",
                                help="Id, name, phone or email."),
    body_text: str = typer.Option(..., "--body", "-b", help="Message text."),
    type_: str = typer.Option("SMS", "--type",
                              help="SMS | EMAIL | NOTE | INTERNAL_COMMENT"),
    subject: str = typer.Option(None, "--subject", help="EMAIL only."),
    yes: YesOpt = False,
):
    """Send a text or email to a client.

    Requires --yes: this is a customer-facing action. Today the only transport is
    LoggingTransport, so the message is recorded on the thread and NOT
    transmitted — the reply comes back with delivery_status LOGGED_ONLY.

    Do Not Disturb and a missing phone number both suppress the send; the response
    then has suppressed=true and no message is recorded.

        ghl msg send -c "jane doe" -b "We can be there Tuesday" --yes
    """
    def run_body():
        c = client()
        found = resolve.contact(c, contact)
        kind = type_.upper()
        if kind in ("SMS", "EMAIL"):
            output.confirm("send %s to %s" % (kind, found["name"]), yes)
        data = c.post("/api/contacts/%d/messages" % found["id"],
                      json={"body": body_text, "type": kind, "subject": subject})
        output.emit(data, render=lambda d: output.note(
            "Suppressed: %s" % d["reason"] if d["suppressed"]
            else "Recorded on thread %d (%s)."
                 % (d["conversation_id"], d["delivery_status"])))
    run(run_body)


@app.command()
def note(
    contact: str = typer.Option(..., "--contact", "-c"),
    body_text: str = typer.Option(..., "--body", "-b"),
):
    """Add an internal note to a client's thread.

    Not customer-facing, so no --yes and no DND suppression.
    """
    def run_body():
        c = client()
        found = resolve.contact(c, contact)
        data = c.post("/api/contacts/%d/messages" % found["id"],
                      json={"body": body_text, "type": "NOTE"})
        output.emit(data, render=lambda d: output.note(
            "Note added to thread %d." % d["conversation_id"]))
    run(run_body)


@app.command("list")
def list_messages(
    q: str = typer.Option(None, "--q", "-q", help="Search the text."),
    type_: str = typer.Option("all", "--type",
                              help="all | SMS | EMAIL | NOTE | INTERNAL_COMMENT"),
    direction: str = typer.Option("all", "--direction",
                                  help="all | INBOUND | OUTBOUND"),
    contact: str = typer.Option(None, "--contact", "-c"),
    since: str = typer.Option(None, "--since", help="7d, 24h, or a date."),
    until: str = typer.Option(None, "--until"),
    status: str = typer.Option(None, "--status",
                               help="PENDING|SENT|DELIVERED|FAILED|LOGGED_ONLY"),
    page: int = typer.Option(1, "--page"),
    page_size: int = typer.Option(50, "--page-size"),
):
    """Search messages across every conversation.

        ghl msg list --q "invoice" --since 30d --json
    """
    def body():
        c = client()
        params = {"q": q, "type": type_, "direction": direction,
                  "start": parse_since(since), "end": parse_since(until),
                  "delivery_status": status, "page": page, "page_size": page_size}
        if contact:
            params["contact_id"] = resolve.contact(c, contact)["id"]
        data = c.get("/api/messages", params=params)
        for r in data["items"]:
            r["at"] = output.when(r["occurred_at"])
            r["text"] = (r.get("body") or "")[:60]
        output.emit(data, render=lambda d: (
            output.table(d["items"], [
                ("id", "ID"), ("at", "WHEN"), ("contact_name", "CONTACT"),
                ("type", "TYPE"), ("direction", "DIR"),
                ("delivery_status", "STATUS"), ("text", "TEXT")]),
            output.note("\n%d messages" % d["total"]),
        ))
    run(body)

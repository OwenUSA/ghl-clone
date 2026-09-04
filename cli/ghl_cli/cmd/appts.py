"""Appointments: list, book, edit, cancel."""
import typer

from .. import output, resolve
from ._common import YesOpt, client, parse_since, run

app = typer.Typer(no_args_is_help=True, help=__doc__)


@app.command("list")
def list_appts(
    since: str = typer.Option(None, "--since", help="7d, 24h, or a date."),
    until: str = typer.Option(None, "--until"),
    user: str = typer.Option(None, "--user", help="Assigned staff name or id."),
    calendar: str = typer.Option(None, "--calendar"),
    kind: str = typer.Option("all", "--kind",
                             help="all | appointments | blocked"),
):
    """List appointments in a date range.

        ghl appts list --since 0d --until 7d --json
    """
    def body():
        c = client()
        params = {"start": parse_since(since), "end": parse_since(until),
                  "kind": kind}
        if user:
            params["user_ids"] = str(resolve.user(c, user)["id"])
        if calendar:
            params["calendar_ids"] = str(resolve.calendar(c, calendar)["id"])
        rows = c.get("/api/appointments", params=params)
        for r in rows:
            r["starts"] = output.when(r["starts_at"])
            r["ends"] = output.when(r["ends_at"])
        output.emit(rows, render=lambda rs: (
            output.table(rs, [
                ("id", "ID"), ("starts", "STARTS"), ("ends", "ENDS"),
                ("title", "TITLE"), ("contact_name", "CONTACT"),
                ("calendar_name", "CALENDAR"), ("status", "STATUS")]),
            output.note("\n%d appointments" % len(rs)),
        ))
    run(body)


@app.command()
def show(appointment_id: int):
    """Show one appointment in full."""
    def body():
        output.emit(client().get("/api/appointments/%d" % appointment_id),
                    render=lambda d: output.detail(d))
    run(body)


@app.command()
def create(
    title: str = typer.Option(..., "--title", "-t"),
    start: str = typer.Option(..., "--start", help="ISO, e.g. 2026-08-05T14:00"),
    end: str = typer.Option(None, "--end",
                            help="ISO. Defaults to one hour after --start."),
    contact: str = typer.Option(None, "--contact", "-c"),
    user: str = typer.Option(None, "--user", help="Assign to staff."),
    calendar: str = typer.Option(None, "--calendar"),
    notes: str = typer.Option(None, "--notes"),
):
    """Book an appointment.

    Schedules the T-24h and T-1h reminder texts.

        ghl appts create -t "Roof inspection" --start 2026-08-05T14:00 -c "jane doe"
    """
    def body():
        from datetime import datetime, timedelta
        c = client()
        starts = datetime.fromisoformat(start)
        ends = datetime.fromisoformat(end) if end else starts + timedelta(hours=1)
        payload = {"title": title, "starts_at": starts.isoformat(),
                   "ends_at": ends.isoformat(), "notes": notes}
        if contact:
            payload["contact_id"] = resolve.contact(c, contact)["id"]
        if user:
            payload["assigned_user_id"] = resolve.user(c, user)["id"]
        if calendar:
            payload["calendar_id"] = resolve.calendar(c, calendar)["id"]
        data = c.post("/api/appointments", json=payload)
        output.emit(data, render=lambda d: output.note(
            "Booked %r (id %d). Reminders: %s"
            % (d["title"], d["id"], d.get("automation", "-"))))
    run(body)


@app.command()
def update(
    appointment_id: int = typer.Argument(...),
    title: str = typer.Option(None, "--title"),
    start: str = typer.Option(None, "--start"),
    end: str = typer.Option(None, "--end"),
    status: str = typer.Option(None, "--status",
                               help="booked|confirmed|showed|no-show|cancelled|..."),
    user: str = typer.Option(None, "--user"),
    calendar: str = typer.Option(None, "--calendar"),
    notes: str = typer.Option(None, "--notes"),
):
    """Edit an appointment.

    Changing --start reschedules the reminder texts to match the new time.
    """
    def body():
        c = client()
        payload = {k: v for k, v in {
            "title": title, "status": status, "notes": notes}.items()
            if v is not None}
        if start:
            payload["starts_at"] = start
        if end:
            payload["ends_at"] = end
        if user:
            payload["assigned_user_id"] = resolve.user(c, user)["id"]
        if calendar:
            payload["calendar_id"] = resolve.calendar(c, calendar)["id"]
        if not payload:
            output.note("nothing to change")
            return
        data = c.patch("/api/appointments/%d" % appointment_id, json=payload)
        output.emit(data, render=lambda d: output.note(
            "Updated. Reminders: %s" % d.get("automation", "-")))
    run(body)


@app.command()
def cancel(appointment_id: int, yes: YesOpt = False):
    """Cancel an appointment. Pending reminders stop firing."""
    def body():
        output.confirm("cancel appointment %d" % appointment_id, yes)
        output.emit(client().delete("/api/appointments/%d" % appointment_id),
                    render=lambda d: output.note("Cancelled (id %d)." % d["id"]))
    run(body)


@app.command()
def calendars():
    """List calendars."""
    def body():
        rows = client().get("/api/calendars")
        output.emit(rows, render=lambda rs: output.table(rs, [
            ("id", "ID"), ("name", "NAME"), ("user_name", "OWNER")]))
    run(body)

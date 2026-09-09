"""Dashboard and reports."""
import typer

from .. import output, resolve
from ._common import client, parse_since, run

app = typer.Typer(no_args_is_help=True, help=__doc__)


@app.command()
def dashboard(pipeline: str = typer.Option(None, "--pipeline")):
    """Opportunity totals, value and conversion rate."""
    def body():
        c = client()
        params = {}
        if pipeline:
            params["pipeline_id"] = resolve.pipeline(c, pipeline)["id"]
        d = c.get("/api/dashboard", params=params)
        output.emit(d, render=lambda x: output.detail({
            "opportunities": x["total"],
            "won": x["status"]["won"], "open": x["status"]["open"],
            "lost": x["status"]["lost"],
            "total value": "$%.2f" % (x["total_value_cents"] / 100),
            "won value": "$%.2f" % (x["won_value_cents"] / 100),
            "conversion": "%.2f%%" % x["conversion_rate"],
        }))
    run(body)


@app.command()
def calls(
    since: str = typer.Option(None, "--since", help="7d, 30d, or a date."),
    until: str = typer.Option(None, "--until"),
    direction: str = typer.Option("all", "--direction"),
):
    """Call report: volume by status, durations, top sources."""
    def body():
        d = client().get("/api/reports/calls", params={
            "start": parse_since(since), "end": parse_since(until),
            "direction": direction})
        output.emit(d, render=lambda x: (
            output.detail({
                "total calls": x["total_calls"],
                "avg duration": "%ds" % (x["avg_duration_seconds"] or 0),
                "total duration": "%ds" % (x["total_duration_seconds"] or 0),
                # First-time callers have their own durations; printing the
                # overall ones twice is what the web report used to do.
                "first-time calls": sum((x["first_time_by_status"] or {}).values()),
                "first-time avg duration":
                    "%ds" % (x["first_time_avg_duration_seconds"] or 0)}),
            output.note("\nby status:"),
            output.table([{"status": k, "calls": v}
                          for k, v in (x["by_status"] or {}).items()],
                         [("status", "STATUS"), ("calls", "CALLS")]),
            output.note("\ntop sources:"),
            output.table(x["top_sources"] or [],
                         [("source", "SOURCE"), ("calls", "TOTAL CALLS"),
                          ("won", "WON DEALS"), ("avg_duration", "AVG DURATION")]),
        ))
    run(body)


@app.command()
def appointments(
    since: str = typer.Option(None, "--since"),
    until: str = typer.Option(None, "--until"),
    calendar: str = typer.Option(None, "--calendar"),
):
    """Appointment report: status tiles, sources, top calendars."""
    def body():
        c = client()
        params = {"start": parse_since(since), "end": parse_since(until)}
        if calendar:
            params["calendar_ids"] = str(resolve.calendar(c, calendar)["id"])
        d = c.get("/api/reports/appointments", params=params)
        output.emit(d, render=lambda x: (
            output.note("total: %d\n" % x["total"]),
            output.table([{"status": k, "count": v}
                          for k, v in (x["tiles"] or {}).items()],
                         [("status", "STATUS"), ("count", "COUNT")]),
        ))
    run(body)

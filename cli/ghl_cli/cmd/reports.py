"""Dashboard and reports."""
import typer

from .. import output, resolve
from ._common import client, parse_since, run

app = typer.Typer(no_args_is_help=True, help=__doc__)


def _range_of(payload: dict) -> str:
    """The window the API says it used, so a printed figure names its own range."""
    window = payload.get("range") or {}
    start, end = window.get("start"), window.get("end")
    if not start and not end:
        return "all time"
    return "%s to %s" % (start or "the beginning", end or "now")


@app.command()
def dashboard(
    pipeline: str = typer.Option(None, "--pipeline"),
    since: str = typer.Option(None, "--since", help="7d, 30d, or a date."),
    until: str = typer.Option(None, "--until"),
):
    """Opportunity totals, value and conversion rate.

    No --since means ALL TIME, matching the web dashboard's default. The range
    filters on the date the opportunity was CREATED.
    """
    def body():
        c = client()
        params = {"start": parse_since(since), "end": parse_since(until)}
        if pipeline:
            params["pipeline_id"] = resolve.pipeline(c, pipeline)["id"]
        d = c.get("/api/dashboard", params=params)
        output.emit(d, render=lambda x: output.detail({
            "range": _range_of(x),
            "opportunities": x["total"],
            "won": x["status"]["won"], "open": x["status"]["open"],
            "lost": x["status"]["lost"],
            "abandoned": x["status"].get("abandoned", 0),
            "total value": "$%.2f" % (x["total_value_cents"] / 100),
            "won value": "$%.2f" % (x["won_value_cents"] / 100),
            "conversion (won/decided)": "%.2f%%" % x["conversion_rate"],
            "conversion (won/all)": "%.2f%%" % x["conversion_rate_all"],
        }))
    run(body)


@app.command()
def funnel(
    pipeline: str = typer.Option(None, "--pipeline"),
    since: str = typer.Option(None, "--since", help="7d, 30d, or a date."),
    until: str = typer.Option(None, "--until"),
):
    """One pipeline stage by stage: how many got there, and how many moved on.

    `reached` is how many opportunities are in this stage or a later one, which is
    inferred from where deals sit today — the schema keeps no stage history.
    `cumulative` is that as a share of the whole pipeline and can only fall;
    `next step` is the share of the deals that reached a stage which went on to
    the next one, and can never exceed 100%.
    """
    def body():
        c = client()
        params = {"start": parse_since(since), "end": parse_since(until)}
        if pipeline:
            params["pipeline_id"] = resolve.pipeline(c, pipeline)["id"]
        d = c.get("/api/dashboard/funnel", params=params)

        def pct(v):
            return "--" if v is None else "%.2f%%" % v

        output.emit(d, render=lambda x: (
            output.note("%s — %d opportunities, %s\n"
                        % (x["pipeline_name"] or "no pipeline", x["total"],
                           _range_of(x))),
            output.table([{"stage": s["name"], "in_stage": s["count"],
                           "value": "$%.2f" % (s["value_cents"] / 100),
                           "reached": s["reached"],
                           "cumulative": pct(s["cumulative_pct"]),
                           "next_step": pct(s["next_step_pct"])}
                          for s in x["stages"]],
                         [("stage", "STAGE"), ("in_stage", "IN STAGE"),
                          ("value", "VALUE"), ("reached", "REACHED"),
                          ("cumulative", "CUMULATIVE"), ("next_step", "NEXT STEP")]),
        ))
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

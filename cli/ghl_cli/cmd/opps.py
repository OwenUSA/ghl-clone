"""Opportunities: create, move between stages, update, delete."""
import typer

from .. import output, resolve
from ._common import YesOpt, client, money_to_cents, run

app = typer.Typer(no_args_is_help=True, help=__doc__)


def _fmt(rows):
    for r in rows:
        r["value"] = "$%.2f" % ((r.get("value_cents") or 0) / 100)
    return rows


@app.command("list")
def list_opps(
    pipeline: str = typer.Option(None, "--pipeline", help="Name or id."),
    status: str = typer.Option("open", "--status",
                               help="open | won | lost | abandoned | all"),
    q: str = typer.Option(None, "--q", "-q"),
):
    """List opportunities in a pipeline.

        ghl opps list --status all --json
    """
    def body():
        c = client()
        pipe = resolve.pipeline(c, pipeline)
        stages = {s["id"]: s["name"] for s in pipe["stages"]}
        rows = c.get("/api/opportunities", params={
            "pipeline_id": pipe["id"], "status": status, "q": q})
        for r in rows:
            r["stage"] = stages.get(r["stage_id"], r["stage_id"])
        output.emit(rows, render=lambda rs: (
            output.table(_fmt(rs), [
                ("id", "ID"), ("title", "TITLE"), ("stage", "STAGE"),
                ("status", "STATUS"), ("value", "VALUE"),
                ("contact_name", "CONTACT")]),
            output.note("\n%d opportunities in %s" % (len(rs), pipe["name"])),
        ))
    run(body)


@app.command()
def show(opp_id: int):
    """Show one opportunity in full."""
    def body():
        data = client().get("/api/opportunities/%d" % opp_id)
        data["value"] = "$%.2f" % ((data.get("value_cents") or 0) / 100)
        output.emit(data, render=lambda d: output.detail(d, [
            ("id", "id"), ("title", "title"), ("status", "status"),
            ("value", "value"), ("stage_id", "stage id"),
            ("contact_name", "contact"), ("owner_name", "owner"),
            ("source", "source"), ("expected_close_date", "expected close"),
            ("custom_fields", "custom fields")]))
    run(body)


@app.command()
def stages(pipeline: str = typer.Option(None, "--pipeline")):
    """List the stages of a pipeline, with counts and totals.

    Useful before `ghl opps move` — two stages can share a name.
    """
    def body():
        pipe = resolve.pipeline(client(), pipeline)
        rows = list(pipe["stages"])
        for s in rows:
            s["value"] = "$%.2f" % ((s.get("value_cents") or 0) / 100)
        output.emit(rows, render=lambda rs: output.table(rs, [
            ("id", "ID"), ("position", "POS"), ("name", "NAME"),
            ("count", "CARDS"), ("value", "VALUE")]))
    run(body)


@app.command()
def create(
    title: str = typer.Option(..., "--title", "-t"),
    contact: str = typer.Option(None, "--contact", "-c"),
    pipeline: str = typer.Option(None, "--pipeline"),
    stage: str = typer.Option(None, "--stage", help="Stage name or id."),
    value: str = typer.Option(None, "--value", help="e.g. 9500 or 9500.50"),
):
    """Create an opportunity.

        ghl opps create -t "Jane roof" -c "jane doe" --stage "New Lead" --value 9500
    """
    def body():
        c = client()
        pipe = resolve.pipeline(c, pipeline)
        stage_row = (resolve.stage(c, pipe, stage) if stage
                     else min(pipe["stages"], key=lambda s: s["position"]))
        payload = {"title": title, "pipeline_id": pipe["id"],
                   "stage_id": stage_row["id"],
                   "value_cents": money_to_cents(value) or 0}
        if contact:
            payload["contact_id"] = resolve.contact(c, contact)["id"]
        data = c.post("/api/opportunities", json=payload)
        output.emit(data, render=lambda d: output.note(
            "Created %r (id %d) in %s." % (d["title"], d["id"], stage_row["name"])))
    run(body)


@app.command()
def move(
    opp_id: int = typer.Argument(...),
    stage: str = typer.Option(..., "--stage", help="Stage name or id."),
    position: int = typer.Option(0, "--position"),
):
    """Move an opportunity to another stage.

    Fires the stage-change automation, which texts the customer.

        ghl opps move 42 --stage "Inspection"
    """
    def body():
        c = client()
        current = c.get("/api/opportunities/%d" % opp_id)
        pipe = resolve.pipeline(c, str(current["pipeline_id"]))
        stage_row = resolve.stage(c, pipe, stage)
        data = c.patch("/api/opportunities/%d" % opp_id,
                       json={"stage_id": stage_row["id"], "position": position})
        output.emit(data, render=lambda d: output.note(
            "Moved to %s. Automation: %s"
            % (stage_row["name"], d.get("automation", "-"))))
    run(body)


@app.command()
def update(
    opp_id: int = typer.Argument(...),
    title: str = typer.Option(None, "--title"),
    status: str = typer.Option(None, "--status",
                               help="open | won | lost | abandoned"),
    value: str = typer.Option(None, "--value"),
    owner: str = typer.Option(None, "--owner"),
    stage: str = typer.Option(None, "--stage"),
    source: str = typer.Option(None, "--source"),
    close_date: str = typer.Option(None, "--close-date"),
    field: list[str] = typer.Option(None, "--field",
                                    help="Custom field as key=value. Repeatable."),
):
    """Edit an opportunity. Only the flags you pass are changed."""
    def body():
        c = client()
        payload = {k: v for k, v in {
            "title": title, "status": status, "source": source,
            "expected_close_date": close_date,
        }.items() if v is not None}
        if value is not None:
            payload["value_cents"] = money_to_cents(value)
        if owner:
            payload["owner_id"] = resolve.user(c, owner)["id"]
        if stage:
            current = c.get("/api/opportunities/%d" % opp_id)
            pipe = resolve.pipeline(c, str(current["pipeline_id"]))
            payload["stage_id"] = resolve.stage(c, pipe, stage)["id"]
        if field:
            current = c.get("/api/opportunities/%d" % opp_id)
            merged = dict(current.get("custom_fields") or {})
            for pair in field:
                if "=" not in pair:
                    from ..errors import Rejected
                    raise Rejected("--field expects key=value, got %r" % pair)
                k, v = pair.split("=", 1)
                merged[k.strip()] = v
            payload["custom_fields"] = merged
        if not payload:
            output.note("nothing to change")
            return
        data = c.patch("/api/opportunities/%d/detail" % opp_id, json=payload)
        output.emit(data, render=lambda d: output.note(
            "Updated %r. Automation: %s" % (d["title"], d.get("automation", "-"))))
    run(body)


@app.command()
def win(opp_id: int):
    """Mark an opportunity won."""
    def body():
        data = client().patch("/api/opportunities/%d/detail" % opp_id,
                              json={"status": "won"})
        output.emit(data, render=lambda d: output.note("%r marked won." % d["title"]))
    run(body)


@app.command()
def lose(opp_id: int):
    """Mark an opportunity lost."""
    def body():
        data = client().patch("/api/opportunities/%d/detail" % opp_id,
                              json={"status": "lost"})
        output.emit(data, render=lambda d: output.note("%r marked lost." % d["title"]))
    run(body)


@app.command()
def delete(opp_id: int, yes: YesOpt = False):
    """Delete an opportunity. Admin only, and irreversible."""
    def body():
        output.confirm("delete opportunity %d" % opp_id, yes)
        output.emit(client().delete("/api/opportunities/%d" % opp_id),
                    render=lambda _: output.note("Deleted."))
    run(body)


@app.command("pipelines")
def list_pipelines():
    """List pipelines and their stage counts."""
    def body():
        rows = client().get("/api/pipelines")
        flat = [{"id": p["id"], "name": p["name"], "stages": len(p["stages"]),
                 "cards": sum(s.get("count", 0) for s in p["stages"])}
                for p in rows]
        output.emit(rows, render=lambda _: output.table(flat, [
            ("id", "ID"), ("name", "NAME"), ("stages", "STAGES"),
            ("cards", "CARDS")]))
    run(body)

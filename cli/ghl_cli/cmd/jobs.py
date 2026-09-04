"""The automation queue. Admin only.

This is how you tell whether a rule actually fired — the alternative is reading the
worker's stdout.
"""
import typer

from .. import output
from ._common import YesOpt, client, run

app = typer.Typer(no_args_is_help=True, help=__doc__)


@app.command("list")
def list_jobs(
    status: str = typer.Option(None, "--status",
                               help="pending | running | done | failed | cancelled"),
    type_: str = typer.Option(None, "--type",
                              help="missed_call_textback | new_lead_notify | "
                                   "appointment_reminder | stage_change_notify"),
    limit: int = typer.Option(50, "--limit"),
):
    """List queued and completed automation jobs.

        ghl jobs list --status pending --json
    """
    def body():
        rows = client().get("/api/jobs", params={
            "status": status, "type": type_, "limit": limit})
        for r in rows:
            r["run_at"] = output.when(r["run_after"])
            r["tries"] = "%d/%d" % (r["attempts"], r["max_attempts"])
            r["error"] = (r.get("last_error") or "")[:40]
        output.emit(rows, render=lambda rs: (
            output.table(rs, [
                ("id", "ID"), ("type", "TYPE"), ("status", "STATUS"),
                ("tries", "TRIES"), ("run_at", "RUN AFTER"), ("error", "ERROR")]),
            output.note("\n%d jobs" % len(rs)),
        ))
    run(body)


@app.command()
def retry(job_id: int, yes: YesOpt = False):
    """Re-queue a failed job.

    A completed job cannot be retried — doing so would repeat its side effect,
    which for these rules means sending a customer another text.
    """
    def body():
        output.confirm("retry job %d" % job_id, yes)
        output.emit(client().post("/api/jobs/%d/retry" % job_id),
                    render=lambda d: output.note(
                        "Job %d re-queued." % d["id"]))
    run(body)

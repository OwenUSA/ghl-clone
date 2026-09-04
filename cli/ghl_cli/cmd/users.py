"""Staff accounts. Creating and editing require ADMIN."""
import typer

from .. import output, resolve
from ._common import YesOpt, client, run

app = typer.Typer(no_args_is_help=True, help=__doc__)


@app.command("list")
def list_users():
    """List staff. Email addresses are visible to admins only."""
    def body():
        rows = client().get("/api/users")
        output.emit(rows, render=lambda rs: output.table(rs, [
            ("id", "ID"), ("name", "NAME"), ("email", "EMAIL"),
            ("role", "ROLE"), ("is_active", "ACTIVE")]))
    run(body)


@app.command()
def create(
    email: str = typer.Option(..., "--email"),
    name: str = typer.Option(..., "--name"),
    role: str = typer.Option("TECH", "--role", help="ADMIN | DISPATCHER | TECH"),
):
    """Create a staff account. Prompts for the password."""
    def body():
        password = typer.prompt("Password for %s" % email, hide_input=True,
                                confirmation_prompt=True)
        data = client().post("/api/users", json={
            "email": email, "name": name, "role": role.upper(),
            "password": password})
        output.emit(data, render=lambda d: output.note(
            "Created %s (%s), id %d." % (d["email"], d["role"], d["id"])))
    run(body)


@app.command()
def update(
    ref: str = typer.Argument(..., help="Staff name or id."),
    name: str = typer.Option(None, "--name"),
    role: str = typer.Option(None, "--role"),
    active: bool = typer.Option(None, "--active/--inactive"),
    set_password: bool = typer.Option(False, "--set-password"),
    yes: YesOpt = False,
):
    """Edit a staff account.

    Changing a role, deactivating, or resetting a password signs that user out
    everywhere immediately.
    """
    def body():
        c = client()
        target = resolve.user(c, ref)
        payload = {}
        if name:
            payload["name"] = name
        if role:
            payload["role"] = role.upper()
        if active is not None:
            payload["is_active"] = active
        if set_password:
            payload["password"] = typer.prompt(
                "New password for %s" % target["name"], hide_input=True,
                confirmation_prompt=True)
        if not payload:
            output.note("nothing to change")
            return
        output.confirm("update %s (id %d)" % (target["name"], target["id"]), yes)
        data = c.patch("/api/users/%d" % target["id"], json=payload)
        output.emit(data, render=lambda d: output.note(
            "Updated %s (%s, %s)."
            % (d["name"], d["role"], "active" if d["is_active"] else "inactive")))
    run(body)

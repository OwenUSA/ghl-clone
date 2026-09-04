"""Clients: find, add, edit, tag, delete."""
import typer

from .. import output, resolve
from ._common import YesOpt, client, run

app = typer.Typer(no_args_is_help=True, help=__doc__)
tag_app = typer.Typer(no_args_is_help=True, help="Add and remove tags.")
app.add_typer(tag_app, name="tag")

COLUMNS = [("id", "ID"), ("name", "NAME"), ("phone", "PHONE"),
           ("email", "EMAIL"), ("business_name", "BUSINESS"), ("tags", "TAGS")]


@app.command("list")
def list_contacts(
    q: str = typer.Option(None, "--q", "-q", help="Search name, phone, email."),
    page: int = typer.Option(1, "--page"),
    page_size: int = typer.Option(20, "--page-size"),
    sort: str = typer.Option("created_at", "--sort",
                             help="name|email|phone|business_name|created_at"),
    order: str = typer.Option("desc", "--order", help="asc|desc"),
):
    """List or search clients.

        ghl contacts list --q smith --json
    """
    def body():
        data = client().get("/api/contacts", params={
            "q": q, "page": page, "page_size": page_size,
            "sort": sort, "order": order})
        for r in data["items"]:
            r["tags"] = ", ".join(r.get("tags") or [])
        output.emit(data, render=lambda d: (
            output.table(d["items"], COLUMNS),
            output.note("\npage %d of %d — %d contacts"
                        % (d["page"], d["pages"], d["total"])),
        ))
    run(body)


@app.command()
def show(ref: str = typer.Argument(..., help="Id, name, phone or email.")):
    """Show one client in full.

        ghl contacts show "jane doe"
    """
    def body():
        c = client()
        found = resolve.contact(c, ref)
        data = c.get("/api/contacts/%d" % found["id"])
        data["tag_names"] = ", ".join(t["name"] for t in data.get("tags", []))
        output.emit(data, render=lambda d: output.detail(d, [
            ("id", "id"), ("name", "name"), ("phone", "phone"),
            ("email", "email"), ("business_name", "business"),
            ("contact_type", "type"), ("source", "source"),
            ("owner_name", "owner"), ("dnd", "do not disturb"),
            ("tag_names", "tags"), ("created_at", "created"),
        ]))
    run(body)


@app.command()
def create(
    first: str = typer.Option("", "--first"),
    last: str = typer.Option("", "--last"),
    phone: str = typer.Option(None, "--phone"),
    email: str = typer.Option(None, "--email"),
    business: str = typer.Option(None, "--business"),
    source: str = typer.Option(None, "--source"),
):
    """Add a client. Needs at least a phone or an email.

    Fires the new-lead automation.

        ghl contacts create --first Jane --last Doe --phone "(941) 555-0199"
    """
    def body():
        data = client().post("/api/contacts", json={
            "first_name": first, "last_name": last, "phone": phone,
            "email": email, "business_name": business, "source": source})
        output.emit(data, render=lambda d: output.note(
            "Created %s (id %d). Automation: %s"
            % (d["name"], d["id"], d.get("automation", "-"))))
    run(body)


@app.command()
def update(
    ref: str = typer.Argument(..., help="Id, name, phone or email."),
    first: str = typer.Option(None, "--first"),
    last: str = typer.Option(None, "--last"),
    phone: str = typer.Option(None, "--phone"),
    email: str = typer.Option(None, "--email"),
    business: str = typer.Option(None, "--business"),
    source: str = typer.Option(None, "--source"),
    contact_type: str = typer.Option(None, "--type"),
    owner: str = typer.Option(None, "--owner", help="Staff name or id."),
    dnd: bool = typer.Option(None, "--dnd/--no-dnd",
                             help="Do Not Disturb suppresses all texts to them."),
):
    """Edit a client. Only the flags you pass are changed."""
    def body():
        c = client()
        found = resolve.contact(c, ref)
        payload = {k: v for k, v in {
            "first_name": first, "last_name": last, "phone": phone,
            "email": email, "business_name": business, "source": source,
            "contact_type": contact_type, "dnd": dnd,
        }.items() if v is not None}
        if owner:
            payload["owner_id"] = resolve.user(c, owner)["id"]
        if not payload:
            output.note("nothing to change")
            return
        data = c.patch("/api/contacts/%d" % found["id"], json=payload)
        output.emit(data, render=lambda d: output.note(
            "Updated %s (id %d)." % (d["name"], d["id"])))
    run(body)


@app.command()
def delete(
    ref: str = typer.Argument(...),
    yes: YesOpt = False,
    force: bool = typer.Option(False, "--force",
                               help="Detach opportunities and delete anyway."),
):
    """Delete a client and their conversation history. Admin only.

    Opportunities are detached, never deleted — they carry the telephony join key.
    """
    def body():
        c = client()
        found = resolve.contact(c, ref)
        output.confirm("delete contact %s (id %d)" % (found["name"], found["id"]),
                       yes)
        data = c.delete("/api/contacts/%d" % found["id"],
                        params={"force": "true" if force else None})
        output.emit(data, render=lambda d: output.note(
            "Deleted. Detached opportunities: %s"
            % (d["detached_opportunities"] or "none")))
    run(body)


@app.command("tags")
def all_tags():
    """List every tag, with how many clients carry it."""
    def body():
        rows = client().get("/api/tags")
        output.emit(rows, render=lambda r: output.table(
            r, [("id", "ID"), ("name", "NAME"), ("count", "CONTACTS")]))
    run(body)


@tag_app.command("add")
def tag_add(ref: str = typer.Argument(...), name: str = typer.Argument(...)):
    """Add a tag to a client (creating the tag if new)."""
    def body():
        c = client()
        found = resolve.contact(c, ref)
        data = c.post("/api/contacts/%d/tags" % found["id"], json={"name": name})
        output.emit(data, render=lambda d: output.note(
            "%s now tagged: %s"
            % (d["name"], ", ".join(t["name"] for t in d["tags"]) or "-")))
    run(body)


@tag_app.command("remove")
def tag_remove(ref: str = typer.Argument(...), name: str = typer.Argument(...)):
    """Remove a tag from a client."""
    def body():
        c = client()
        found = resolve.contact(c, ref)
        detail = c.get("/api/contacts/%d" % found["id"])
        match = [t for t in detail["tags"] if t["name"].lower() == name.lower()]
        if not match:
            from ..errors import NotFound
            raise NotFound("%s is not tagged %r" % (detail["name"], name),
                           {"tags": [t["name"] for t in detail["tags"]]})
        data = c.delete("/api/contacts/%d/tags/%d" % (found["id"], match[0]["id"]))
        output.emit(data, render=lambda d: output.note(
            "%s now tagged: %s"
            % (d["name"], ", ".join(t["name"] for t in d["tags"]) or "-")))
    run(body)

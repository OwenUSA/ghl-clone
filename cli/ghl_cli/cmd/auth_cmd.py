"""Logging in and managing API tokens."""
import contextlib
import socket
from datetime import UTC

import typer

from .. import config, output
from ..client import Client
from ..errors import NotAuthenticated
from ._common import YesOpt, client, run

app = typer.Typer(no_args_is_help=True, help=__doc__)
token_app = typer.Typer(no_args_is_help=True, help="Create, list and revoke tokens.")
app.add_typer(token_app, name="token")


@app.command()
def login(
    email: str = typer.Option(None, "--email", help="Defaults to a prompt."),
    password: str = typer.Option(None, "--password",
                                 help="Prompted for if omitted. Prefer the prompt "
                                      "— an argument lands in shell history."),
    url: str = typer.Option(None, "--url", help="Backend URL."),
    token_name: str = typer.Option(None, "--token-name",
                                   help="Label for the token. Defaults to this "
                                        "machine's hostname."),
    show_token: bool = typer.Option(False, "--show-token",
                                    help="Print the token. Off by default so it "
                                         "does not end up in a transcript."),
):
    """Log in and store an API token for future commands.

    Signs in with your password, mints a personal access token, then discards the
    browser session — so the CLI holds a revocable token rather than a password,
    and you can revoke it from the Settings screen without changing your password.

        ghl auth login
    """
    def body():
        base = url or config.api_url()
        addr = email or typer.prompt("Email")
        secret = password or typer.prompt("Password", hide_input=True)

        c = Client(base_url=base, token=None)
        # Login is cookie-based; keep the session only long enough to mint a token.
        resp = c._http.post("/api/auth/login",
                            json={"email": addr, "password": secret})
        if resp.status_code == 401:
            raise NotAuthenticated("invalid email or password")
        data = c._unwrap(resp)
        user = data["user"]

        name = token_name or ("%s-cli" % socket.gethostname().lower())
        csrf = c._http.cookies.get("ghl_csrf")
        minted = c._unwrap(c._http.post(
            "/api/auth/tokens", json={"name": name, "expires_in_days": 365},
            headers={"X-CSRF-Token": csrf} if csrf else {}))

        c._http.post("/api/auth/logout",
                     headers={"X-CSRF-Token": csrf} if csrf else {})

        path = config.save({"api_url": base, "token": minted["token"],
                            "token_prefix": minted["prefix"],
                            "token_id": minted["id"], "user": user})

        payload = {"user": user, "api_url": base,
                   "token_prefix": minted["prefix"], "config": str(path)}
        if show_token:
            payload["token"] = minted["token"]
        output.emit(payload, render=lambda p: (
            output.note("Logged in as %s (%s)." % (user["name"], user["role"])),
            output.note("Token %s… saved to %s" % (minted["prefix"], path)),
        ))
    run(body)


@app.command()
def whoami():
    """Show who the stored token belongs to, and what it can do.

    `--json` is global — it works here like it does everywhere else.
    """
    def body():
        data = client().get("/api/auth/me")
        output.emit(data, render=lambda d: output.detail({
            "name": d["user"]["name"], "email": d["user"]["email"],
            "role": d["user"]["role"], "auth": d["kind"],
            "scopes": ", ".join(d["scopes"]) or "(full role rights)",
            "token": (d["token"] or {}).get("name", "-"),
            "api_url": config.api_url(),
        }))
    run(body)


@app.command()
def logout(
    revoke: bool = typer.Option(True, "--revoke/--keep",
                                help="Also revoke the token server-side."),
):
    """Forget the stored token (and revoke it, unless --keep)."""
    def body():
        cfg = config.load()
        if revoke and cfg.get("token_id"):
            # Already revoked, or the backend is down. Clearing the local
            # config is still the right outcome, so this failure is ignored
            # on purpose.
            with contextlib.suppress(Exception):
                client().delete("/api/auth/tokens/%d" % cfg["token_id"])
        config.clear()
        output.emit({"logged_out": True},
                    render=lambda _: output.note("Local credentials cleared."))
    run(body)


@app.command()
def password():
    """Change your own password. Invalidates other sessions."""
    def body():
        current = typer.prompt("Current password", hide_input=True)
        new = typer.prompt("New password", hide_input=True,
                           confirmation_prompt=True)
        data = client().post("/api/auth/password",
                             json={"current_password": current,
                                   "new_password": new})
        output.emit(data, render=lambda _: output.note(
            "Password changed. Other sessions have been signed out; this CLI "
            "token still works."))
    run(body)


@token_app.command("create")
def token_create(
    name: str = typer.Argument(..., help="A label, e.g. 'owen-ingest'."),
    expires_days: int = typer.Option(None, "--expires-days"),
    scopes: str = typer.Option("", "--scopes",
                               help="Space-separated. Empty means full rights for "
                                    "your role. e.g. 'events:write' or 'read'."),
):
    """Mint a token. The secret is shown ONCE."""
    def body():
        data = client().post("/api/auth/tokens",
                             json={"name": name, "expires_in_days": expires_days,
                                   "scopes": scopes})
        output.emit(data, render=lambda d: (
            output.note("%s\n" % d["token"]),
            output.note("Shown once only — store it now."),
        ))
    run(body)


@token_app.command("list")
def token_list(
    user: str = typer.Option(None, "--user", help="Admin only: another user's."),
):
    """List your tokens (prefix, last use, expiry)."""
    def body():
        params = {}
        if user:
            from .. import resolve
            params["user_id"] = resolve.user(client(), user)["id"]
        from datetime import datetime
        now = datetime.now(UTC)
        rows = client().get("/api/auth/tokens", params=params)
        for r in rows:
            # Compare the expiry to NOW. An earlier version compared it to
            # created_at, which is never in the past for a valid token, so an
            # expired token displayed as "active".
            expired = False
            if r["expires_at"]:
                try:
                    expired = datetime.fromisoformat(
                        str(r["expires_at"])) <= now
                except ValueError:
                    expired = False
            r["status"] = ("revoked" if r["revoked_at"]
                           else "expired" if expired else "active")
            r["last_used"] = output.when(r["last_used_at"])
        output.emit(rows, render=lambda rs: output.table(rs, [
            ("id", "ID"), ("name", "NAME"), ("prefix", "PREFIX"),
            ("scopes", "SCOPES"), ("status", "STATUS"), ("last_used", "LAST USED"),
        ]))
    run(body)


@token_app.command("revoke")
def token_revoke(token_id: int, yes: YesOpt = False):
    """Revoke a token. It stops working on its next request."""
    def body():
        output.confirm("revoke token %d" % token_id, yes)
        output.emit(client().delete("/api/auth/tokens/%d" % token_id),
                    render=lambda _: output.note("Revoked."))
    run(body)

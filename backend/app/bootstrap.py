"""Set the first password, and mint machine tokens.

Every user seeded before auth existed has `password_hash = ""`, which never
verifies. Without this module, turning on the auth gate would lock everyone out of
their own database — so it ships BEFORE the gate, not after.

    uv run python -m app.bootstrap set-password --email you@example.com
    uv run python -m app.bootstrap create-token --email owen@... --name owen-cli
    uv run python -m app.bootstrap create-token --email owen-telephony@... \
        --name owen-ingest --scopes events:write
    uv run python -m app.bootstrap list-users

Passwords are always prompted for, never taken as an argument: a password on the
command line ends up in shell history and in the process list.
"""
import argparse
import getpass
import sys

from sqlalchemy import select

from .auth import hash_password, mint_api_token
from .db import SessionLocal
from .models import ApiToken, Role, User


def _read_password(prompt: str, *, confirm: bool = False) -> str:
    """Prompt on a terminal, otherwise read one line from stdin.

    The stdin path is what makes this scriptable (`echo pw | python -m app.bootstrap
    set-password ...`). There is deliberately no `--password` flag: an argument would
    land in shell history and in the process list, where a pipe does not.
    """
    if sys.stdin.isatty():
        pw = getpass.getpass(prompt)
        if confirm and pw != getpass.getpass("Confirm: "):
            sys.exit("passwords do not match")
    else:
        pw = sys.stdin.readline().rstrip("\n")
    if len(pw) < 8:
        sys.exit("password must be at least 8 characters")
    return pw


def _get_user(db, email: str) -> User:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        sys.exit("no user with email %r — run `list-users` to see who exists" % email)
    return user


def cmd_set_password(args) -> None:
    with SessionLocal() as db:
        user = _get_user(db, args.email)
        pw = _read_password("New password for %s: " % user.email, confirm=True)
        user.password_hash = hash_password(pw)
        # Any token minted against the old credentials stops working.
        user.token_version += 1
        db.commit()
        print("password set for %s (%s)" % (user.email, user.role.value))


def cmd_create_user(args) -> None:
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == args.email)):
            sys.exit("a user with email %r already exists" % args.email)
        if args.machine:
            # An empty password_hash never verifies, so a machine account cannot
            # sign in to the UI at all — it exists only to own an API token.
            pw_hash = ""
        else:
            pw_hash = hash_password(_read_password("Password for %s: " % args.email))
        user = User(email=args.email, name=args.name, role=Role[args.role],
                    password_hash=pw_hash)
        db.add(user)
        db.commit()
        print("created %s (%s), id=%d%s" % (
            user.email, user.role.value, user.id,
            " — machine account, cannot log in" if args.machine else ""))


def cmd_create_token(args) -> None:
    with SessionLocal() as db:
        user = _get_user(db, args.email)
        plain, token = mint_api_token(user, name=args.name, scopes=args.scopes,
                                      expires_in_days=args.expires_days)
        db.add(token)
        db.commit()
        print("token %r for %s (scopes: %s)" % (
            token.name, user.email, token.scopes or "<full role rights>"))
        print()
        print("  " + plain)
        print()
        print("This is the only time it is shown. Store it now.")


def cmd_list_users(args) -> None:
    with SessionLocal() as db:
        rows = db.scalars(select(User).order_by(User.id)).all()
        for u in rows:
            tokens = db.scalars(select(ApiToken).where(
                ApiToken.user_id == u.id, ApiToken.revoked_at.is_(None))).all()
            print("%3d  %-38s %-11s %-9s pw:%-3s tokens:%d" % (
                u.id, u.email, u.role.value,
                "active" if u.is_active else "INACTIVE",
                "yes" if u.password_hash else "NO", len(tokens)))


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="app.bootstrap", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("set-password", help="set or reset a user's password")
    sp.add_argument("--email", required=True)
    sp.set_defaults(func=cmd_set_password)

    sp = sub.add_parser("create-user", help="create a user")
    sp.add_argument("--email", required=True)
    sp.add_argument("--name", required=True)
    sp.add_argument("--role", default="ADMIN", choices=[r.name for r in Role])
    sp.add_argument("--machine", action="store_true",
                    help="no password — the account can only be used via an API "
                         "token (for the telephony feed and similar)")
    sp.set_defaults(func=cmd_create_user)

    sp = sub.add_parser("create-token", help="mint an API token for a user")
    sp.add_argument("--email", required=True)
    sp.add_argument("--name", required=True, help="a label, e.g. owen-ingest")
    sp.add_argument("--scopes", default="",
                    help="space-separated; empty means full role rights")
    sp.add_argument("--expires-days", type=int, default=None)
    sp.set_defaults(func=cmd_create_token)

    sp = sub.add_parser("list-users", help="who exists, and can they log in")
    sp.set_defaults(func=cmd_list_users)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

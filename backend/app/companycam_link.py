"""Link the CompanyCam projects that already exist to their cards. DRY RUN BY DEFAULT.

    uv run python -m app.companycam_link              # reads CompanyCam, writes nothing
    uv run python -m app.companycam_link --commit     # ...and records the links
    uv run python -m app.companycam_link --json       # the same counts, for a machine
    uv run python -m app.companycam_link --status     # the hourly check's heartbeat

Reads every project (50 a page, paced) and applies the rules in `app/companycam.py`,
in order: the Workiz job number in a project's name, then the address. A name-only
match is put on the review list (Settings -> CompanyCam), never linked. Nothing is
ever written to CompanyCam, and nothing here creates a contact, a card or a project.

Idempotent: a second `--commit` reports every project it linked as "already linked"
and writes nothing. The report is COUNTS ONLY — no customer name, address or project
name — so it is safe to paste into a ticket.

Like `app.workiz_import`, it uses whatever `DATABASE_URL` points at.
"""
import argparse
import json
import sys

from . import companycam
from .db import SessionLocal

LABELS = (
    ("scanned", "projects scanned"),
    ("linked_workiz_job", "linked by Workiz job number"),
    ("linked_one", "linked to one card by address"),
    ("linked_several", "linked to several cards by address"),
    ("name_only_review", "name-only, for review"),
    ("unmatched", "unmatched"),
    ("already_linked", "already linked"),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.companycam_link",
                                     description=__doc__.split("\n\n")[0])
    parser.add_argument("--commit", action="store_true", help="write the links")
    parser.add_argument("--json", action="store_true", help="print JSON only")
    parser.add_argument("--status", action="store_true",
                        help="print the hourly check's heartbeat and exit")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        if args.status:
            out = {**companycam.config_summary(), "heartbeat": companycam.heartbeat(db)}
            print(json.dumps(out, indent=2))
            return 0
        if not companycam.enabled():
            print("COMPANYCAM_API_TOKEN is not set; nothing to read.", file=sys.stderr)
            return 3
        try:
            counts = companycam.link_all(db, commit=args.commit)
        except companycam.CompanyCamError as exc:
            db.rollback()
            print("CompanyCam could not be read (%s). Nothing was written." % exc.kind,
                  file=sys.stderr)
            return 6
        if args.commit:
            db.commit()
        else:
            db.rollback()
    finally:
        db.close()

    if args.json:
        print(json.dumps({"committed": args.commit, **counts}))
        return 0
    print("CompanyCam linking — %s" % ("COMMITTED" if args.commit
                                       else "DRY RUN (nothing written; --commit writes)"))
    for key, label in LABELS:
        print("  %-38s %6d" % (label, counts[key]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

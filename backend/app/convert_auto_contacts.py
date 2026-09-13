"""Turn contacts an inbound call auto-created back into number-only threads.

    uv run python -m app.convert_auto_contacts            # DRY RUN — writes nothing
    uv run python -m app.convert_auto_contacts --commit   # ...and this one writes
    uv run python -m app.convert_auto_contacts --json     # the same report, for jq

Until 2026-09-13 a call from an unknown number created a contact named by the number
(`created_by='owen-main'`). The owner reversed that — an unknown number is never saved
as a contact — and asked for the one such contact in production to become a
number-only thread again, with its history kept. See DECISIONS.md, 2026-09-13.

A contact is converted only when EVERY one of these holds (`number_threads.
conversion_blockers` lists each that does not):

  * created_by = 'owen-main';
  * no opportunities, no additional-contact link on any deal, no appointments;
  * no tasks, no tags, no internal notes on its thread;
  * never edited by a person — every field is what the ingest wrote, and
    `updated_at` is within two seconds of `created_at`;
  * no other contact holds the same number (then the number is known, and its
    calls belong on that contact).

Idempotent: a converted contact no longer exists, so a second run finds nothing to do.
It sends nothing and queues nothing — it imports neither `automations` nor `queue`.
The report names contacts by id and number only; it prints no message bodies.
"""
from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select

from .db import SessionLocal
from .models import Contact
from .number_threads import AUTO_CREATED_BY, conversion_blockers, convert_contact


def run(db, *, commit: bool) -> dict:
    """Plan (and with `commit`, perform) the conversion. Rolls back unless `commit`."""
    candidates = db.scalars(select(Contact).where(Contact.created_by == AUTO_CREATED_BY)
                            .order_by(Contact.id)).all()
    plan, skipped = [], []
    for c in candidates:
        blockers = conversion_blockers(db, c)
        row = {"contact_id": c.id}
        if blockers:
            skipped.append({**row, "reasons": blockers})
        else:
            plan.append(row)

    done = []
    try:
        for row in plan:
            done.append(convert_contact(db, db.get(Contact, row["contact_id"])))
        if commit:
            db.commit()
        else:
            db.rollback()
    except Exception:
        db.rollback()
        raise
    return {"mode": "commit" if commit else "dry-run", "candidates": len(candidates),
            "converted" if commit else "would_convert": done, "left_alone": skipped}


def _print(report: dict) -> None:
    dry = report["mode"] == "dry-run"
    rows = report["would_convert" if dry else "converted"]
    print("MODE: %s" % ("DRY RUN — nothing was written" if dry else "COMMIT"))
    print("contacts created by owen-main: %d" % report["candidates"])
    print("%s: %d" % ("would convert" if dry else "converted", len(rows)))
    for r in rows:
        print("  contact %s -> number thread %s (%s), %d event(s) moved, %d duplicate(s) "
              "skipped, then the contact is deleted"
              % (r["contact_id"], r["number_thread_id"],
                 "new" if r["thread_created"] else "existing",
                 r["events_moved"], r["events_skipped_duplicates"]))
    print("left alone: %d" % len(report["left_alone"]))
    for r in report["left_alone"]:
        print("  contact %s: %s" % (r["contact_id"], "; ".join(r["reasons"])))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--commit", action="store_true",
                    help="write the conversion (default: dry run, writes nothing)")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args(argv)
    db = SessionLocal()
    try:
        report = run(db, commit=args.commit)
    finally:
        db.close()
    if args.json:
        print(json.dumps(report, default=str, indent=2))
    else:
        _print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())

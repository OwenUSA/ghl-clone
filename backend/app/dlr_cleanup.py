"""Take the carrier delivery receipts off the threads they were filed on (2026-09-16).

    cd backend
    uv run python -m app.dlr_cleanup            # DRY RUN. Writes nothing.
    uv run python -m app.dlr_cleanup --commit   # ...and this one writes.
    uv run python -m app.dlr_cleanup --json     # the same counts, for a machine

BulkVS posts delivery receipts to the same webhook as an inbound text, and before owen-main
learned to tell them apart it relayed each one here as a message. They landed on customers'
threads as words the customers had written — measured on production, one number-only thread
holding two of them.

owen-main no longer relays them and `POST /api/events` no longer accepts one (`app/dlr.py`).
This is for the ones already on a thread.

## What it does, and what it refuses to do

For each INBOUND SMS whose body is a delivery receipt (`dlr.looks_like_receipt`, a strict
seven-field match) it removes that event from the thread, then repairs what the event had
changed while it was there:

  * **the unread badge.** Each junk event incremented it. The badge is recomputed from the
    events that remain, so a thread does not sit on "2 unread" for messages that no longer
    exist — and so nobody opens it looking for something to read.
  * **`last_event_at`.** It orders the inbox. Left pointing at a deleted row, a thread would
    keep a position in the list it no longer earns. Recomputed from the remaining events, or
    from when the thread was created if none are left.
  * **an emptied number-only thread.** A thread that a receipt CREATED, and that holds
    nothing else, is a row in the inbox for a conversation that never happened. It is
    removed, and counted separately so the number is visible rather than folded in.

**It never touches a contact, an opportunity or an appointment**, and it only ever deletes a
row it has positively identified as a receipt — never one that merely resembles one. The
guard is that `looks_like_receipt` is strict and is the SAME function the ingest guard uses:
if it were ever loose enough to delete a customer's text here, it would already be dropping
that customer's texts at the door, which is far louder.

## Why this deletes where owen-main hides

owen-main marks its junk rows and hides them, because there the row is the only record that
a carrier ever said anything, and the receipt is applied to the outbound message from it.
Here it is neither: this CRM gets the receipt through `POST /api/events/delivery`, which is
a different route entirely, so a junk thread event carries no information at all. A hidden
row would mean a read-time filter on every thread query for ever, and a permanent regex over
every customer's words is a worse thing to own than a one-off, dry-run-by-default removal of
rows that are demonstrably machine output.

## Dry run by default, counts only

The report carries no customer name, no phone number and no message body — only counts and
the receipts' own machine fields (id, stat, err). It is safe to paste into a ticket, and a
test asserts that.

Idempotent: a second run finds nothing, because the first one removed it.
"""
import argparse
import json
import sys
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import dlr
from .db import SessionLocal
from .models import (
    Conversation,
    ConversationEvent,
    Direction,
    EventType,
    NumberThread,
    NumberThreadEvent,
)


def _receipts(db: Session, model, parent_column):
    """Every INBOUND SMS on `model` whose body is a delivery receipt.

    Filtered in Python rather than in SQL: the match is a seven-field regex, and a `LIKE`
    that approximated it would be exactly the kind of loose pattern this must not use to
    decide what to delete. The candidate set is already narrow — inbound texts whose body
    begins "id:" — so the scan is small.
    """
    rows = db.scalars(
        select(model).where(model.type == EventType.SMS,
                            model.direction == Direction.INBOUND,
                            model.body.like("id:%"))
    ).all()
    return [(r, getattr(r, parent_column)) for r in rows if dlr.looks_like_receipt(r.body)]


def _repair_thread(db: Session, thread, model, parent_column, thread_id) -> None:
    """Recompute the unread badge and `last_event_at` from what is actually left."""
    remaining = db.scalars(
        select(model).where(getattr(model, parent_column) == thread_id)
        .order_by(model.occurred_at.desc())
    ).all()
    unread = db.scalar(
        select(func.count(model.id)).where(getattr(model, parent_column) == thread_id,
                                           model.direction == Direction.INBOUND))
    # Never INVENTED upward: the badge can only be repaired down to what is there. A thread
    # whose badge was already lower than its inbound count (somebody had read it) keeps the
    # lower number rather than being marked unread again by a cleanup.
    thread.unread_count = min(thread.unread_count, unread or 0)
    if remaining:
        thread.last_event_at = remaining[0].occurred_at
    elif getattr(thread, "created_at", None) is not None:
        thread.last_event_at = thread.created_at


def sweep(db: Session, commit: bool) -> dict:
    """Find the receipts, and on `--commit` take them off their threads."""
    report = {
        "contact_thread_events": 0,
        "number_thread_events": 0,
        "threads_repaired": 0,
        "empty_number_threads_removed": 0,
        "unread_badges_corrected": 0,
        "by_stat": Counter(),
        "receipt_ids": [],
    }
    touched: dict[tuple[str, int], object] = {}

    for kind, model, parent_column, thread_model, key in (
        ("contact", ConversationEvent, "conversation_id", Conversation,
         "contact_thread_events"),
        ("number", NumberThreadEvent, "number_thread_id", NumberThread,
         "number_thread_events"),
    ):
        for event, thread_id in _receipts(db, model, parent_column):
            report[key] += 1
            facts = dlr.fields(event.body) or {}
            report["by_stat"][facts.get("stat", "?")] += 1
            if facts.get("id"):
                report["receipt_ids"].append(facts["id"])
            thread = db.get(thread_model, thread_id)
            if thread is not None:
                touched[(kind, thread_id)] = (thread, model, parent_column)
            if commit:
                db.delete(event)

    if commit:
        db.flush()

    for (kind, thread_id), (thread, model, parent_column) in touched.items():
        before = thread.unread_count
        if commit:
            _repair_thread(db, thread, model, parent_column, thread_id)
            if thread.unread_count != before:
                report["unread_badges_corrected"] += 1
        else:
            # A dry run must predict the same numbers the real run will produce, so the
            # badge is worked out without writing it.
            left = db.scalar(select(func.count(model.id)).where(
                getattr(model, parent_column) == thread_id,
                model.direction == Direction.INBOUND))
            junk = sum(1 for e, t in _receipts(db, model, parent_column) if t == thread_id)
            if min(before, max((left or 0) - junk, 0)) != before:
                report["unread_badges_corrected"] += 1
        report["threads_repaired"] += 1

        if kind == "number":
            still = db.scalar(select(func.count(model.id)).where(
                getattr(model, parent_column) == thread_id))
            if commit and not still:
                db.delete(thread)
                report["empty_number_threads_removed"] += 1
            elif not commit:
                junk = sum(1 for e, t in _receipts(db, model, parent_column) if t == thread_id)
                if (still or 0) - junk <= 0:
                    report["empty_number_threads_removed"] += 1

    if commit:
        db.commit()
    report["by_stat"] = dict(report["by_stat"])
    return report


def render(report: dict, commit: bool) -> str:
    lines = [
        "carrier delivery receipts found on threads:",
        "  on a contact's conversation:        %d" % report["contact_thread_events"],
        "  on a number-only thread:            %d" % report["number_thread_events"],
        "  by carrier status:                  %s"
        % (", ".join("%s %d" % (k, v) for k, v in sorted(report["by_stat"].items()))
           or "none"),
        "",
        "%s:" % ("removed from their threads" if commit else "would be removed"),
        "  threads repaired:                   %d" % report["threads_repaired"],
        "  unread badges corrected:            %d" % report["unread_badges_corrected"],
        "  emptied number-only threads gone:   %d" % report["empty_number_threads_removed"],
    ]
    if not commit:
        lines += ["", "DRY RUN — nothing was written. Re-run with --commit."]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Take carrier delivery receipts off threads.")
    ap.add_argument("--commit", action="store_true", help="write (default is a dry run)")
    ap.add_argument("--json", action="store_true", help="print the counts as JSON")
    args = ap.parse_args(argv)
    with SessionLocal() as db:
        report = sweep(db, args.commit)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render(report, args.commit))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Create the call Checklist: one "Checklist" tab and its 16 questions.

    uv run python -m app.checklist_seed            # DRY RUN — writes nothing
    uv run python -m app.checklist_seed --commit   # ...and this one writes
    uv run python -m app.checklist_seed --json     # the same report, for jq

The owner's decisions (DECISIONS.md, 2026-09-14): the Checklist is ordinary custom
fields in ONE custom tab, so he can reword, reorder, add and archive its questions
himself in Settings → Custom Fields. This command only puts the first version there.
It is deliberately NOT a migration: it is data the owner then owns.

**Idempotent, and it never overrules the owner.** Every question has a fixed key
(`checklist_*`). A key that already exists is left exactly as it is — a reworded
label, a changed script, a different set of pipelines, even an archived question —
and reported as "exists". A second `--commit` therefore changes nothing, and a run
after the owner's edits does not undo them. The tab is found by name; a second one is
never created.

It refuses, writing nothing, when: the migration that adds the question settings has
not been applied; a pipeline it attaches to is missing or its name is ambiguous; two
tabs are already called "Checklist"; or an existing field holds one of these keys with
a different type (then it is somebody else's field, and guessing would be wrong).

It writes definitions only. No opportunity, no answer and no job is touched, and it
imports neither `automations` nor `queue`.
"""
from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import func, inspect, select

from . import custom_fields
from .db import SessionLocal
from .models import CustomFieldDef, CustomFieldGroup, CustomFieldPipeline, Pipeline

AHS = "Dream Team Roofing AHS"
RETAIL = "Retail"
BOTH = (AHS, RETAIL)
GROUP = custom_fields.CHECKLIST_GROUP

TEXT = CustomFieldDef.TEXT
NUMBER = CustomFieldDef.NUMBER
DROPDOWN = CustomFieldDef.DROPDOWN
BOOLEAN = CustomFieldDef.BOOLEAN
PARAGRAPH = CustomFieldDef.PARAGRAPH

# (key, label, type, options, settings, pipelines) — the owner's table, in his order.
QUESTIONS: list[tuple[str, str, str, list[str], dict, tuple[str, ...]]] = [
    ("checklist_service_address_verified", "Service address verified", BOOLEAN, [],
     {"linked_field": CustomFieldDef.LINK_OPPORTUNITY_ADDRESS}, BOTH),
    ("checklist_second_phone", "Second phone number", TEXT, [],
     {"script": "Best contact phone number — always ask for a second number."}, BOTH),
    ("checklist_email_verified", "Email verified", BOOLEAN, [],
     {"linked_field": CustomFieldDef.LINK_CONTACT_EMAIL}, BOTH),
    ("checklist_whats_happening", "What's happening there?", PARAGRAPH, [],
     {"script": "May you tell me what's happening there?"}, BOTH),
    ("checklist_leak_count", "How many leaks?", NUMBER, [], {}, BOTH),
    ("checklist_leak_location", "Where is the leak located inside the house?", TEXT, [],
     {}, BOTH),
    ("checklist_roof_age", "How old is the roof?", DROPDOWN,
     # The owner's en dashes, kept: these are the exact words he asked for.
     ["Under 5 yrs", "5–10 yrs", "10–15 yrs", "15–20 yrs", "20+ yrs", "Unknown"],  # noqa: RUF001
     {}, BOTH),
    ("checklist_previous_repair", "Any previous repair?", DROPDOWN,
     ["Yes", "No", "Unknown"], {"details_when": ["Yes"]}, BOTH),
    ("checklist_roof_material", "Roof material", DROPDOWN,
     ["Shingle", "Tile", "Flat (single-ply TPO)", "Metal", "Other"], {}, BOTH),
    ("checklist_stories", "How many stories?", DROPDOWN, ["1", "2", "3+"], {}, BOTH),
    ("checklist_occupancy", "Does the customer live there, or are there tenants?",
     DROPDOWN, ["Owner lives there", "Tenants", "Vacant"], {}, BOTH),
    ("checklist_leak_started", "When did the leak start?", TEXT, [], {}, BOTH),
    ("checklist_first_time_ahs", "First time using AHS for roofing?", DROPDOWN,
     ["Yes", "No"], {}, (AHS,)),
    ("checklist_explained_ahs_process", "Explained the AHS repair process", BOOLEAN, [],
     {"script": "Just to let you know about the process. We first book an appointment "
                "for an inspection, then we submit the info to AHS, and once it gets "
                "approved we book again for the repair."}, (AHS,)),
    ("checklist_introduced_antonio", "Introduced Antonio", BOOLEAN, [],
     {"script": "Antonio has over 28 years of experience, he is our best technician — "
                "you're in good hands."}, (AHS,)),
    ("checklist_preferred_inspection", "Preferred inspection day & time", TEXT, [],
     {"script": "Ask their preferred appointment day and time for the inspection."},
     BOTH),
]

SETTINGS_COLUMNS = {"script", "linked_field", "details_when"}


class Refused(Exception):
    """A reason to write nothing at all."""


def _pipelines(db) -> dict[str, int]:
    found = {}
    for name in BOTH:
        ids = db.scalars(select(Pipeline.id).where(Pipeline.name == name)).all()
        if not ids:
            raise Refused("no pipeline is named %r — the Checklist attaches to it" % name)
        if len(ids) > 1:
            raise Refused("%d pipelines are named %r; refusing to guess which one"
                          % (len(ids), name))
        found[name] = ids[0]
    return found


def run(db, *, commit: bool) -> dict:
    """Plan (and with `commit`, write) the Checklist. Rolls back unless `commit`."""
    columns = {c["name"] for c in inspect(db.get_bind()).get_columns("custom_field_defs")}
    if SETTINGS_COLUMNS - columns:
        raise Refused("the database has no question settings yet — apply the migration "
                      "first (alembic upgrade head, revision a7d4c2e9f130)")
    for key, *_ in QUESTIONS:
        assert not custom_fields.is_reserved(key) and custom_fields.slug_for(key) == key

    pipelines = _pipelines(db)
    groups = [g for g in db.scalars(select(CustomFieldGroup)
                                    .order_by(CustomFieldGroup.id)).all()
              if g.name.strip().lower() == GROUP.lower()]
    if len(groups) > 1:
        raise Refused("%d tabs are already called %r; refusing to guess which is the "
                      "Checklist" % (len(groups), GROUP))

    existing = {d.key: d for d in db.scalars(select(CustomFieldDef).where(
        CustomFieldDef.key.in_([q[0] for q in QUESTIONS]))).all()}
    for key, _label, field_type, *_ in QUESTIONS:
        d = existing.get(key)
        if d is not None and d.field_type != field_type:
            raise Refused("the field %r already exists as a %s question (%r), not %s; "
                          "refusing to take it over" % (key, d.field_type, d.label,
                                                        field_type))

    report = {"mode": "commit" if commit else "dry-run", "group": None,
              "fields": [], "created": 0, "exists": 0}
    try:
        if groups:
            group = groups[0]
            report["group"] = {"name": group.name, "id": group.id, "action": "exists"}
        else:
            position = (db.scalar(select(func.max(CustomFieldGroup.position))) or 0) + 1
            group = CustomFieldGroup(name=GROUP, position=position)
            db.add(group)
            db.flush()
            report["group"] = {"name": GROUP, "id": group.id if commit else None,
                               "action": "create"}

        position = (db.scalar(select(func.max(CustomFieldDef.position))) or 0) + 1
        for key, label, field_type, options, settings, attach_to in QUESTIONS:
            row = {"key": key, "label": label, "type": field_type,
                   "pipelines": list(attach_to)}
            if options:
                row["options"] = options
            row.update(settings)
            if key in existing:
                row["action"] = "exists"
                report["exists"] += 1
                report["fields"].append(row)
                continue
            d = CustomFieldDef(key=key, label=label, field_type=field_type,
                               options=options, position=position,
                               entity="opportunity", group_id=group.id,
                               script=settings.get("script"),
                               linked_field=settings.get("linked_field"),
                               details_when=settings.get("details_when"))
            position += 1
            db.add(d)
            db.flush()
            for name in attach_to:
                db.add(CustomFieldPipeline(field_id=d.id, pipeline_id=pipelines[name]))
            row["action"] = "create"
            report["created"] += 1
            report["fields"].append(row)
        db.flush()
        if commit:
            db.commit()
        else:
            db.rollback()
    except Exception:
        db.rollback()
        raise
    return report


def _print(report: dict) -> None:
    dry = report["mode"] == "dry-run"
    verb = "would create" if dry else "created"
    print("MODE: %s" % ("DRY RUN — nothing was written" if dry else "COMMIT"))
    g = report["group"]
    print("tab %r: %s" % (g["name"], "exists, reused" if g["action"] == "exists" else verb))
    print("questions: %d %s, %d already exist (left exactly as they are)"
          % (report["created"], "to create" if dry else "created", report["exists"]))
    for i, f in enumerate(report["fields"], start=1):
        extra = []
        if f.get("options"):
            extra.append("options: " + " · ".join(f["options"]))
        if f.get("linked_field"):
            extra.append("shows beside it: " + f["linked_field"])
        if f.get("details_when"):
            extra.append("details box when: " + ", ".join(f["details_when"]))
        if f.get("script"):
            extra.append("script: " + f["script"])
        print("%2d. [%s] %s (%s, key %s) on %s" % (
            i, verb if f["action"] == "create" else "exists", f["label"], f["type"],
            f["key"], " + ".join(f["pipelines"])))
        for line in extra:
            print("      " + line)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--commit", action="store_true",
                    help="write the Checklist (default: dry run, writes nothing)")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args(argv)
    db = SessionLocal()
    try:
        report = run(db, commit=args.commit)
    except Refused as why:
        print("REFUSED, nothing was written: %s" % why, file=sys.stderr)
        return 1
    finally:
        db.close()
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())

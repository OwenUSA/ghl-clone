"""Verify the owner's Zuper setup checklist, and create the two job categories.

    uv run python -m app.zuper.setup              # DRY RUN: read-only checks, and what it
                                                  # WOULD create. Writes nothing anywhere.
    uv run python -m app.zuper.setup --commit     # creates the "AHS" and "Retail" categories
                                                  # and their statuses, maps them, and records
                                                  # the check results for Settings → Zuper
    uv run python -m app.zuper.setup --json       # the same report, for jq

Every check is a GET. Where Zuper cannot report a setting through its API (the research
file: notification switches, tags, company time zone — and, until the first probe shows
otherwise, custom-field definitions and lead sources), the item is shown as **confirm by
hand**: an ADMIN ticks it on Settings → Zuper, and the sync cannot be switched on until every
such item is ticked and every other item passes.

Categories: each pipeline's stages become that category's statuses ONE-TO-ONE, in board order
(position, then id — every Retail stage has position 0 in production, so id order decides),
mapped by uid. The AHS pipeline's empty "Submit Invoices" stage is included like any other. A
second stage with the same name as an earlier one is created as "<name> (2)", because a
category's status names may have to be unique (UNVERIFIED); the mapping is by uid, so that
suffix never matters to the sync.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import Pipeline, Stage, ZuperMapping
from . import client, config, engine, mapping, webhook, zapi
from .client import ZuperError

PASS, FAIL, BY_HAND = "pass", "fail", "confirm_by_hand"

# Items the Zuper API cannot report: always confirmed by hand (checklist letters in brackets).
HAND_ITEMS = [
    ("company", "Company time zone America/New_York and currency USD (A)"),
    ("old_key_deleted", ("The API key the sync uses is NOT deleted — with no free seat it is "
                         "the owner's own Admin key, so checklist step B3 does not apply (B3)")),
    ("lead_tag", "Master tag “Lead” exists (F)"),
    ("portal_invites", "Customer portal welcome / invite emails are OFF (G1)"),
    ("job_notifications",
     "Customer notifications for jobs and appointments are OFF for email AND SMS (G2)"),
    ("quote_invoice_reminders", "Automatic quote and invoice follow-up reminders are OFF (G3)"),
    ("zuper_connect", "Zuper Connect (texting / calling) is not enabled (G4)"),
    ("workflows", "No active workflow emails or texts customers (G5)"),
    ("booking_widget", "The booking widget is not published (G6)"),
]
HAND_TITLES = dict(HAND_ITEMS)


def item(key: str, title: str, state: str, *sentences: str) -> dict:
    return {"key": key, "title": title, "state": state, "sentences": [s for s in sentences if s]}


# ---------------------------------------------------------------------------- checks

def _field_type(rec: dict) -> str:
    return str(rec.get("field_type") or rec.get("type") or "").upper().replace("-", "_")


def _field_options(rec: dict) -> list[str]:
    raw = rec.get("field_options") or rec.get("options") or rec.get("values") or []
    out = []
    for o in raw if isinstance(raw, list) else []:
        if isinstance(o, dict):
            o = o.get("label") or o.get("value") or o.get("name")
        if o is not None:
            out.append(str(o).strip())
    return out


def check_fields(module: str, wanted: dict[str, tuple[tuple[str, ...], list[str]]],
                 key: str, title: str) -> dict:
    try:
        defs = zapi.custom_field_defs(module)
    except ZuperError as exc:
        if exc.kind in ("not_found", "rejected", "bad_response"):
            return item(key, title, BY_HAND,
                        "Zuper does not list custom field definitions through its API, so "
                        "check these by hand: " + "; ".join(
                            "“%s” (%s%s)" % (label, _type_words(types),
                                              (": " + ", ".join(opts)) if opts else "")
                            for label, (types, opts) in wanted.items()) + ".")
        raise
    by_label = {str(d.get("label") or d.get("field_name") or "").strip(): d for d in defs}
    problems = []
    for label, (types, options) in wanted.items():
        d = by_label.get(label)
        if d is None:
            problems.append("The %s field “%s” is missing (it must be %s, with exactly that "
                            "label)." % (module.lower(), label, _type_words(types)))
            continue
        if _field_type(d) and _field_type(d) not in types:
            problems.append("The %s field “%s” is %s, not %s." % (
                module.lower(), label, _field_type(d).lower(), _type_words(types)))
        if options:
            have = _field_options(d)
            missing = [o for o in options if o not in have]
            if missing:
                problems.append("The %s field “%s” is missing the option%s %s." % (
                    module.lower(), label, "" if len(missing) == 1 else "s",
                    ", ".join("“%s”" % o for o in missing)))
    if problems:
        return item(key, title, FAIL, *problems)
    return item(key, title, PASS, "All %d fields are there with the right types." % len(wanted))


def _type_words(types: tuple[str, ...]) -> str:
    return {mapping.SINGLE_LINE: "Single line", mapping.MULTI_LINE: "Multi line",
            mapping.NUMBER: "Number", mapping.SINGLE_ITEM: "Single item"}.get(types, types[0])


def check_region() -> dict:
    title = "The configured base URL is this company's Zuper data centre"
    try:
        expected = client.region_lookup(config.company_name())
    except ZuperError as exc:
        return item("region", title, FAIL, "The region lookup for “%s” failed: %s" % (
            config.company_name(), client.sentence(exc)))
    if client.normalise_base(config.base_url()) != expected:
        return item("region", title, FAIL,
                    "Zuper keeps “%s” at %s, but ZUPER_BASE_URL is %s. Set ZUPER_BASE_URL=%s."
                    % (config.company_name(), expected, config.base_url(), expected))
    return item("region", title, PASS, "Zuper keeps this company at %s." % expected)


def check_sync_user(db: Session, commit: bool) -> dict:
    """Whose key is this, and can the sync tell its own writes apart by user?

    Preferred: a dedicated "CRM Sync" user, whose events are dropped as echoes before Zuper
    is even read. The owner's Zuper plan had no seat left for one (2026-09-17), so a key
    that belongs to a PERSON with Admin rights is accepted too. Then the user id is NOT
    recorded as the sync's own: that person's real edits in Zuper must keep syncing, and
    echoes are recognised by content alone (`crm_hash` / `zuper_hash`, engine.py).
    """
    title = "The API key's user has Admin rights (B1, B2)"
    try:
        user = zapi.me()
    except ZuperError as exc:
        if exc.kind in ("not_found", "bad_response"):
            return item("sync_user", title, BY_HAND,
                        "Zuper did not say which user the key belongs to; confirm it is an "
                        "Admin user's key with full access.")
        raise
    first = str(user.get("first_name") or "").strip()
    last = str(user.get("last_name") or "").strip()
    uid = zapi.user_uid(user)
    role = user.get("role") or user.get("role_details") or ""
    if isinstance(role, dict):
        role = role.get("role_name") or role.get("name") or ""
    role = str(role)
    if role and "admin" not in role.lower():
        return item("sync_user", title, FAIL,
                    "The key belongs to “%s %s”, whose role is “%s”; it must be Admin."
                    % (first, last, role))
    dedicated = (first, last) == ("CRM", "Sync")
    if commit:
        config.settings(db).sync_user_uid = uid if (dedicated and uid) else None
    if dedicated:
        return item("sync_user", title, PASS, "The key belongs to CRM Sync.")
    return item("sync_user", title, PASS,
                "The key belongs to “%s %s” (Admin), not a dedicated CRM Sync user. The sync "
                "recognises its own writes by content only, so this person's edits in Zuper "
                "keep syncing; Zuper's activity log will show the sync's changes under this "
                "person's name." % (first, last))


def check_lead_sources(db: Session, commit: bool) -> dict:
    title = "Lead sources exist (E)"
    try:
        rows = zapi.lead_sources()
    except ZuperError as exc:
        if exc.kind in ("not_found", "rejected", "bad_response"):
            return item("lead_sources", title, BY_HAND,
                        "Zuper does not list lead sources through its API, so check these by "
                        "hand: " + ", ".join(mapping.ZUPER_LEAD_SOURCES) + ". Until Zuper "
                        "lists them the sync sends no lead source.")
        raise
    found = {}
    for r in rows:
        name = str(r.get("source_name") or r.get("name") or "").strip()
        uid = r.get("source_uid") or r.get("uid")
        if name and uid:
            found[name] = str(uid)
    missing = [n for n in mapping.ZUPER_LEAD_SOURCES if n not in found]
    if commit:
        config.settings(db).lead_sources = found
    if missing:
        return item("lead_sources", title, FAIL, "Missing lead source%s: %s." % (
            "" if len(missing) == 1 else "s", ", ".join("“%s”" % m for m in missing)))
    return item("lead_sources", title, PASS, "All %d lead sources are there."
                % len(mapping.ZUPER_LEAD_SOURCES))


def check_webhook(db: Session) -> dict:
    title = "A Zuper webhook points at this CRM"
    state, sentence = webhook.setup_state(db)
    return item("webhook", title, {"pass": PASS, "fail": FAIL}.get(state, BY_HAND), sentence)


def pipelines(db: Session) -> dict[str, Pipeline]:
    out = {}
    for name in mapping.CATEGORIES:
        rows = db.scalars(select(Pipeline).where(Pipeline.name == name)).all()
        if len(rows) == 1:
            out[name] = rows[0]
    return out


def ordered_stages(db: Session, pipeline_id: int) -> list[Stage]:
    return list(db.scalars(select(Stage).where(Stage.pipeline_id == pipeline_id)
                           .order_by(Stage.position, Stage.id)).all())


def status_names(stages: list[Stage]) -> list[str]:
    seen: dict[str, int] = {}
    names = []
    for s in stages:
        seen[s.name] = seen.get(s.name, 0) + 1
        names.append(s.name if seen[s.name] == 1 else "%s (%d)" % (s.name, seen[s.name]))
    return names


def check_categories(db: Session) -> dict:
    title = "Job categories “AHS” and “Retail” hold each pipeline's stages 1:1"
    problems = []
    unlisted: list[str] = []
    found = pipelines(db)
    for name, category in mapping.CATEGORIES.items():
        if name not in found:
            problems.append("The CRM has no single pipeline named “%s”." % name)
            continue
        pm = mapping.mapping_for(db, "pipeline", found[name].id)
        if pm is None or pm.state != "linked":
            problems.append("The job category “%s” is not created and mapped yet — run "
                            "python -m app.zuper.setup --commit." % category)
            continue
        listing = zapi.statuses_or_none(pm.zuper_uid)
        statuses = {zapi.status_uid(s): s for s in listing or []}
        for stage in ordered_stages(db, found[name].id):
            sm = mapping.mapping_for(db, "stage", stage.id)
            if sm is None or sm.state != "linked" or not sm.zuper_uid or (
                    sm.zuper_uid not in statuses and sm.parent_uid != pm.zuper_uid):
                problems.append("Stage “%s” (%s) has no status in “%s” yet." % (
                    stage.name, name, category))
            elif sm.zuper_uid not in statuses and category not in unlisted:
                unlisted.append(category)
    if problems:
        return item("categories", title, FAIL, *problems)
    return item("categories", title, PASS, "Both categories match their pipelines.",
                ("Zuper does not list the statuses of %s through its API; each status was "
                 "mapped by the CRM when it created it." % " and ".join(
                     "“%s”" % c for c in unlisted)) if unlisted else "")


def run_checks(db: Session, *, commit: bool) -> list[dict]:
    results: list[dict] = []
    if not config.key_set():
        return [item("connection", "API key and region", FAIL, config.NO_KEY_SENTENCE)]
    try:
        zapi.categories()
        results.append(item("connection", "API key and region", PASS,
                            "Zuper answered at %s." % config.base_url()))
    except ZuperError as exc:
        return [item("connection", "API key and region", FAIL, client.sentence(exc))]
    if config.company_name():
        results.append(check_region())
    checks = [
        lambda: check_sync_user(db, commit),
        lambda: check_fields("CUSTOMER", {label: (mapping.SINGLE_LINE, [])
                                          for label in mapping.CUSTOMER_FIELDS},
                             "customer_fields", "Customer fields in group “CRM” (C)"),
        lambda: check_fields("JOB", {label: (mapping.SINGLE_LINE, [])
                                     for label in mapping.JOB_CRM_FIELDS},
                             "job_fields_crm", "Job fields in group “CRM” (D)"),
        lambda: check_fields("JOB", mapping.CHECKLIST_TYPES, "job_fields_checklist",
                             "Job fields in group “Checklist” (D)"),
        lambda: check_lead_sources(db, commit),
        lambda: check_categories(db),
        lambda: check_webhook(db),
    ]
    for check in checks:
        try:
            results.append(check())
        except ZuperError as exc:
            results.append(item("error", "A check could not run", FAIL, client.sentence(exc)))
    for key, title in HAND_ITEMS:
        results.append(item(key, title, BY_HAND, "The Zuper API cannot report this setting."))
    return results


def record_results(db: Session, results: list[dict]) -> None:
    row = config.settings(db)
    row.setup_results = results
    row.setup_checked_at = datetime.now(UTC)
    row.setup_passed = not any(r["state"] == FAIL for r in results)


def blockers(db: Session) -> list[str]:
    """Why the sync may not be switched on, as sentences. Empty = it may."""
    out = []
    if not config.env_enabled():
        out.append(config.OFF_SENTENCE)
    if not config.key_set():
        out.append(config.NO_KEY_SENTENCE)
    row = config.peek_settings(db)
    if row is None or not row.setup_results:
        out.append("The setup check has not been run.")
        return out
    for r in row.setup_results:
        if r["state"] == FAIL:
            out.append("Setup: %s — %s" % (r["title"], " ".join(r["sentences"])))
    confirmed = row.confirmations or {}
    for r in row.setup_results:
        if r["state"] == BY_HAND and r["key"] not in confirmed:
            out.append("Not confirmed by hand yet: %s." % r["title"])
    return out


# ---------------------------------------------------------------------------- categories

def ensure_categories(db: Session, *, commit: bool) -> dict:
    """Create (commit) or plan (dry run) the categories and statuses; map them by uid."""
    ctx = engine.Ctx(db)
    report = {"categories": [], "created_categories": 0, "created_statuses": 0,
              "linked_statuses": 0, "renamed_statuses": 0}
    for kind in ("category", "status"):
        zapi.ACCEPTED_SHAPES.pop(kind, None)
    existing = {zapi.category_name(c): c for c in zapi.categories()}
    found = pipelines(db)
    for pipeline_name, category_name in mapping.CATEGORIES.items():
        p = found.get(pipeline_name)
        entry = {"pipeline": pipeline_name, "category": category_name, "statuses": []}
        report["categories"].append(entry)
        if p is None:
            entry["problem"] = "no single pipeline named %r" % pipeline_name
            continue
        pm = mapping.mapping_for(db, "pipeline", p.id)
        cat_uid = pm.zuper_uid if pm and pm.state == "linked" else None
        if cat_uid is None and category_name in existing:
            cat_uid = zapi.category_uid(existing[category_name])
            entry["action"] = "link existing"
        elif cat_uid is None:
            entry["action"] = "create"
            report["created_categories"] += 1
            if commit:
                cat_uid = zapi.create_category(category_name)
        else:
            entry["action"] = "exists"
        if commit and cat_uid and (pm is None or pm.zuper_uid != cat_uid):
            if pm is None:
                pm = ZuperMapping(crm_type="pipeline", crm_id=p.id, zuper_type="category")
                db.add(pm)
            pm.zuper_uid, pm.state, pm.updated_at = cat_uid, "linked", ctx.now
            db.commit()                      # a checkpoint: the category is made
        # None: Zuper would not list this category's statuses — then the CRM's own mapping
        # of each status it created is trusted, so a rerun never creates one twice.
        listing, entry["status_sources"] = (zapi.status_sources(cat_uid) if cat_uid
                                            else ([], []))
        entry["statuses_listed"] = listing is not None
        entry["zuper_statuses"] = [zapi.status_name(s) or "?" for s in listing or []]
        if listing:
            entry["status_fields"] = sorted(listing[0].keys())[:30]
        unreadable = [s for s in listing or [] if not (zapi.status_name(s)
                                                       and zapi.status_uid(s))]
        if commit and unreadable:
            # A status the sync cannot name or identify may be one it already made: creating
            # more could duplicate it. Stop and show the fields Zuper does send.
            raise ZuperError("bad_response", "Zuper listed %d status(es) of “%s” whose name or "
                             "uid the sync cannot read (fields: %s); nothing more was created."
                             % (len(unreadable), category_name,
                                ", ".join(sorted(unreadable[0].keys())[:30])))
        statuses = {zapi.status_uid(s): s for s in listing or []}
        by_name = {zapi.status_name(s): u for u, s in statuses.items()}
        stages = ordered_stages(db, p.id)
        entry["ordered_by"] = ("id (every stage has the same position)"
                               if len({s.position for s in stages}) <= 1 and len(stages) > 1
                               else "position, then id")
        stage_maps = db.scalars(select(ZuperMapping).where(
            ZuperMapping.crm_type == "stage")).all()
        taken = {sm.zuper_uid for sm in stage_maps}
        # Zuper's list is RELIABLE for this category when it shows a status the CRM knows it
        # made there: then a status missing from it was not made, and may be sent again.
        reliable = any(sm.state == "linked" and sm.parent_uid == cat_uid
                       and sm.zuper_uid in statuses for sm in stage_maps)
        for i, (stage, name) in enumerate(zip(stages, status_names(stages), strict=True)):
            row = {"stage_id": stage.id, "status": name}
            sm = mapping.mapping_for(db, "stage", stage.id)
            if sm is not None and sm.state == "linked" and sm.parent_uid == cat_uid \
                    and sm.zuper_uid not in statuses:
                # Made and mapped by the CRM, not shown by Zuper's lists (which live answer
                # empty): trusted — re-creating it could only make a duplicate.
                row["action"] = "mapped (not listed by Zuper)"
            elif sm is not None and sm.zuper_uid in statuses:
                row["action"] = "mapped"
                if zapi.status_name(statuses[sm.zuper_uid]) != name:
                    row["action"] = "rename"
                    report["renamed_statuses"] += 1
                    if commit:
                        zapi.rename_status(cat_uid, sm.zuper_uid, name)
            elif name in by_name and by_name[name] not in taken:
                row["action"] = "link existing"
                report["linked_statuses"] += 1
                if commit:
                    _map_stage(db, stage, by_name[name], cat_uid)
            elif sm is not None and sm.state == "creating" and not reliable:
                # An earlier run sent this create and never learned its uid: it may exist in
                # Zuper, and Zuper's lists cannot be trusted to show it (they do not show the
                # statuses the CRM knows are there). Never sent again — a person checks.
                row["action"] = "unresolved"
                entry["statuses"].append(row)
                if commit:
                    raise ZuperError("bad_response", UNRESOLVED % (name, category_name, "; ".join(
                        entry["status_sources"])))
                continue
            else:
                row["action"] = "create"
                report["created_statuses"] += 1
                if commit and cat_uid:
                    uid = _create_status(db, stage, cat_uid, name,
                                         "NEW" if i == 0 else "STARTED", taken)
                    taken.add(uid)
            entry["statuses"].append(row)
    if commit:
        db.commit()
    report["accepted_shapes"] = {k: v for k, v in zapi.ACCEPTED_SHAPES.items()
                                 if k in ("category", "status")}
    return report


UNRESOLVED = ("The status “%s” in “%s” was sent to Zuper earlier but Zuper never said what it "
              "made, and does not list it now (%s). It is not sent again, so it is never "
              "made twice: check Settings → Jobs → Categories in Zuper, then tell the "
              "operator whether it exists.")


def _create_status(db: Session, stage: Stage, cat_uid: str, name: str, status_type: str,
                   taken: set) -> str:
    """Create one status with a checkpoint: the stage's mapping is committed as "creating"
    BEFORE the request. Refused by Zuper (nothing made): the checkpoint is removed. Answered
    without a uid (live, 2026-09-17): the status is looked up by exact name — one unmapped
    match is mapped; otherwise the checkpoint stays and the run stops, so a later run never
    sends it again."""
    _map_stage(db, stage, None, cat_uid, state="creating")
    db.commit()
    try:
        uid = zapi.create_status(cat_uid, name, status_type)
    except ZuperError as exc:
        if exc.kind == "rejected":
            db.delete(mapping.mapping_for(db, "stage", stage.id))
            db.commit()
            raise
        if exc.kind != "bad_response":
            raise
        listing, notes = zapi.status_sources(cat_uid)
        found = [zapi.status_uid(s) for s in listing or []
                 if zapi.status_name(s) == name and zapi.status_uid(s)
                 and zapi.status_uid(s) not in taken]
        if len(found) != 1:
            raise ZuperError("bad_response", "%s; the status “%s” was found %d time(s) in "
                             "Zuper afterwards (%s), so it stays unresolved and is never sent "
                             "again." % (exc.detail, name, len(found), "; ".join(notes))
                             ) from None
        uid = found[0]
    _map_stage(db, stage, uid, cat_uid)
    db.commit()
    return uid


def _map_stage(db: Session, stage: Stage, status_uid: str | None, category_uid: str, *,
               state: str = "linked") -> None:
    sm = mapping.mapping_for(db, "stage", stage.id)
    if sm is None:
        sm = ZuperMapping(crm_type="stage", crm_id=stage.id, zuper_type="status")
        db.add(sm)
    sm.zuper_uid, sm.parent_uid, sm.state = status_uid, category_uid, state
    sm.updated_at = datetime.now(UTC)
    db.flush()


def push_stage(ctx: engine.Ctx, stage_id: int) -> str:
    """A stage renamed or added in the CRM, after setup: follow it in Zuper."""
    db = ctx.db
    stage = db.get(Stage, stage_id)
    if stage is None:
        return "gone"
    pm = mapping.mapping_for(db, "pipeline", stage.pipeline_id)
    if pm is None or pm.state != "linked":
        return "out_of_scope"
    names = dict(zip([s.id for s in ordered_stages(db, stage.pipeline_id)],
                     status_names(ordered_stages(db, stage.pipeline_id)), strict=True))
    sm = mapping.mapping_for(db, "stage", stage_id)
    if sm is None:
        uid = zapi.create_status(pm.zuper_uid, names[stage_id], "STARTED")
        _map_stage(db, stage, uid, pm.zuper_uid)
        db.commit()
        return "created"
    zapi.rename_status(pm.zuper_uid, sm.zuper_uid, names[stage_id])
    return "renamed"


# ---------------------------------------------------------------------------- command

def run(db: Session, *, commit: bool) -> dict:
    engine.mark_quiet(db)
    with client.operator_mode():
        if not commit:
            with client.read_only():
                categories = ensure_categories(db, commit=False) if config.key_set() else None
                results = run_checks(db, commit=False)
            db.rollback()
        else:
            categories = ensure_categories(db, commit=True)
            results = run_checks(db, commit=True)
            record_results(db, results)
            db.commit()
    return {"mode": "commit" if commit else "dry-run", "categories": categories,
            "checks": results,
            "passed": not any(r["state"] == FAIL for r in results),
            "confirm_by_hand": [r["title"] for r in results if r["state"] == BY_HAND]}


def render(report: dict) -> str:
    lines = ["MODE: %s" % ("DRY RUN — nothing was written in the CRM or in Zuper"
                           if report["mode"] == "dry-run" else "COMMIT")]
    cats = report.get("categories")
    if cats:
        verb = "would " if report["mode"] == "dry-run" else ""
        lines.append("categories: %d to create; statuses: %d to create, %d to link, %d to "
                     "rename" % (cats["created_categories"], cats["created_statuses"],
                                 cats["linked_statuses"], cats["renamed_statuses"]))
        if cats.get("accepted_shapes"):
            lines.append("Zuper accepted these create bodies: %s" % ", ".join(
                "%s = %s" % kv for kv in sorted(cats["accepted_shapes"].items())))
        for c in cats["categories"]:
            lines.append("  %s -> %s: %s%s (ordered by %s)" % (
                c["pipeline"], c["category"], verb, c.get("action", c.get("problem")),
                c.get("ordered_by", "—")))
            if "zuper_statuses" in c:
                lines.append("      Zuper lists %d status(es) here%s" % (
                    len(c["zuper_statuses"]), (": " + ", ".join(
                        "“%s”" % n for n in c["zuper_statuses"])) if c["zuper_statuses"]
                    else ""))
            if c.get("status_fields"):
                lines.append("      status fields: " + ", ".join(c["status_fields"]))
            for note in c.get("status_sources") or []:
                lines.append("      source: " + note)
            for s in c["statuses"]:
                lines.append("      stage #%d -> status “%s”: %s%s" % (
                    s["stage_id"], s["status"], verb, s["action"]))
    lines.append("checks:")
    marks = {PASS: "PASS", FAIL: "FAIL", BY_HAND: "CONFIRM BY HAND"}
    for r in report["checks"]:
        lines.append("  [%s] %s" % (marks[r["state"]], r["title"]))
        for s in r["sentences"]:
            lines.append("        " + s)
    lines.append("result: %s" % ("no failures" if report["passed"] else "FAILED"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--commit", action="store_true",
                    help="create categories/statuses and record the results (default: dry run)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    db = SessionLocal()
    try:
        report = run(db, commit=args.commit)
    except ZuperError as exc:
        print("REFUSED: %s" % client.sentence(exc), file=sys.stderr)
        return 2
    finally:
        db.close()
    print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else render(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

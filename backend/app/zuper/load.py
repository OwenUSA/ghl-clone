"""The initial load: everything in one go (the owner's choice).

    uv run python -m app.zuper.load              # DRY RUN: reads Zuper, writes nothing on
                                                 # either side, prints counts
    uv run python -m app.zuper.load --commit     # ...and this one writes
    uv run python -m app.zuper.load --json       # the same report, for jq

What is sent (2026-09-16, v2 — app/zuper/selection.py): EVERY card in the AHS pipeline, and
Retail cards in Scheduled, Inspection / Estimate, Estimate Sent (open) and Invoice (open and
won); the dry run prints the count per group, and a missing named stage is a refusal. Retail
New Lead / Follow Up and every lead stay CRM-only. A selected card a send would refuse (no
customer, name, phone or job address) is skipped and listed by id. Contacts go only as the
customers of the selected cards.

Order: setup verify -> categories / statuses -> customers -> jobs -> appointments ->
notes (the Checklist travels with each job) -> tasks -> verification report.

* **Idempotent.** Customers and jobs are indexed by their "CRM Contact ID" / "CRM Opportunity
  ID" custom field before anything is created (Zuper has no idempotency keys), so a second
  `--commit` links what is there and creates nothing.
* **Resumable.** Every record is its own checkpoint: the mapping row is committed as
  `creating` BEFORE the create request and as `linked` after, so a crash mid-page is followed
  by a search, never a duplicate. Run the same command again to carry on.
* **Rate-limited** by the client (ZUPER_REQUESTS_PER_MINUTE, 429 backoff). A Zuper outage stops
  the load where it is, with the sentence; nothing is half-written.
* **Nothing reaches a customer.** The client denylist refuses every send, and the customer
  notification switches are on the setup checklist, which must pass first.
* **Reports carry counts and ids only** — no names, phones, emails or addresses.
* **Digests are not backfilled**: they start the day the sync is switched on.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import (
    Appointment,
    Contact,
    Opportunity,
    OpportunityNote,
    OpportunityTask,
    ZuperDigest,
    ZuperMapping,
)
from . import client, config, engine, mapping, selection, setup, zapi
from .client import ZuperError

PHASES = ("contact", "opportunity", "appointment", "note", "task")
ID_FIELD = {"contact": "CRM Contact ID", "opportunity": "CRM Opportunity ID"}
MAX_IDS = 200


def index(kind: str) -> dict[int, dict]:
    """Zuper's customers or jobs, by the CRM id they carry."""
    out: dict[int, dict] = {}
    rows = zapi.customers() if kind == "contact" else zapi.jobs()
    for rec in rows:
        raw = str(mapping.custom_values(rec).get(ID_FIELD[kind]) or "").strip()
        if raw.isdigit():
            out.setdefault(int(raw), rec)
    return out


CATEGORY_PROBLEM = "Only cards in the AHS or Retail pipeline"
SKIP_KINDS = (("customer (a contact)", "no customer"), ("needs a name", "no customer name"),
              ("phone number", "no customer phone"), ("its address", "no job address"))


def selected(db: Session) -> tuple[set[int], dict]:
    """The day-one selection (selection.day_one), minus the cards a send would refuse — no
    customer, name, phone or job address — which are listed by id, never sent half-made.
    Raises selection.Refused when a named Retail stage is missing."""
    cards, groups = selection.day_one(db)
    eligible: set[int] = set()
    skipped: list[int] = []
    reasons: dict[str, int] = {}
    for o in cards:
        problems = [p for p in engine.send_problems(db, o)
                    if not p.startswith(CATEGORY_PROBLEM)]
        if not problems:
            eligible.add(o.id)
            continue
        skipped.append(o.id)
        for p in problems:
            for words, key in SKIP_KINDS:
                if words in p:
                    reasons[key] = reasons.get(key, 0) + 1
    return eligible, {"groups": groups, "selected": len(cards), "to_send": len(eligible),
                      "skipped": len(skipped), "skip_reasons": reasons,
                      "skipped_ids": skipped[:MAX_IDS]}


def crm_records(db: Session, kind: str, pipelines: set[int]) -> list:
    """The records of the selected cards (`pipelines` is the set of selected card ids): their
    customers, the cards, their visits, notes and tasks. Leads never reach Zuper."""
    ids = sorted(pipelines)
    if kind == "contact":
        return list(db.scalars(select(Contact).where(Contact.id.in_(
            select(Opportunity.contact_id).where(Opportunity.id.in_(ids))))
            .order_by(Contact.id)).all())
    if kind == "opportunity":
        return list(db.scalars(select(Opportunity).where(
            Opportunity.id.in_(ids)).order_by(Opportunity.id)).all())
    in_scope = ids
    if kind == "appointment":
        return list(db.scalars(select(Appointment).where(
            Appointment.opportunity_id.in_(in_scope), Appointment.status != "cancelled")
            .order_by(Appointment.id)).all())
    model = OpportunityNote if kind == "note" else OpportunityTask
    return list(db.scalars(select(model).where(model.opportunity_id.in_(in_scope))
                           .order_by(model.id)).all())


def _ids(values) -> list[int]:
    return sorted(values)[:MAX_IDS]


def plan_phase(db: Session, kind: str, pipelines: set[int], idx: dict[int, dict] | None) -> dict:
    counts = {"crm": 0, "already_linked": 0, "link_existing": 0, "create": 0}
    for obj in crm_records(db, kind, pipelines):
        counts["crm"] += 1
        m = mapping.mapping_for(db, kind, obj.id)
        if m is not None and m.state == "linked":
            counts["already_linked"] += 1
        elif idx is not None and obj.id in idx:
            counts["link_existing"] += 1
        else:
            counts["create"] += 1
    return counts


def commit_phase(ctx: engine.Ctx, kind: str, pipelines: set[int]) -> dict:
    db = ctx.db
    counts: dict = {"crm": 0, "already_linked": 0, "linked_existing": 0, "created": 0,
                    "updated": 0, "unchanged": 0, "failed": 0, "failed_ids": []}
    ctx.sending = True
    for obj in crm_records(db, kind, pipelines):
        counts["crm"] += 1
        m = mapping.mapping_for(db, kind, obj.id)
        was_linked = m is not None and m.state == "linked"
        try:
            outcome = engine.push(ctx, kind, obj.id)
            db.commit()
        except ZuperError as exc:
            db.rollback()
            engine.mark_quiet(db)
            if exc.kind in ("unavailable", "rate_limited", "unauthorized", "off", "no_key"):
                raise
            counts["failed"] += 1
            if len(counts["failed_ids"]) < MAX_IDS:
                counts["failed_ids"].append(obj.id)
            continue
        if was_linked:
            counts["already_linked"] += 1
            counts["updated" if outcome == "updated" else "unchanged"] += 1
        elif outcome == "linked_existing":
            counts["linked_existing"] += 1
        elif outcome == "created":
            counts["created"] += 1
        else:
            counts[outcome] = counts.get(outcome, 0) + 1
    return counts


def verify(db: Session, pipelines: set[int], *, deep: bool) -> dict:
    """Counts per type, CRM vs Zuper, and every mismatch by id."""
    out: dict = {}
    for kind in ("contact", "opportunity"):
        crm_ids = {o.id for o in crm_records(db, kind, pipelines)}
        maps = {m.crm_id: m for m in db.scalars(select(ZuperMapping).where(
            ZuperMapping.crm_type == kind, ZuperMapping.state == "linked")).all()}
        idx = index(kind)
        zuper_uids = {mapping.uid(r, "customer_uid" if kind == "contact" else "job_uid"): cid
                      for cid, r in idx.items()}
        out[kind] = {
            "crm": len(crm_ids), "mapped": len(set(maps) & crm_ids), "zuper": len(idx),
            "crm_not_mapped": _ids(crm_ids - set(maps)),
            "mapped_not_in_zuper": _ids(cid for cid, m in maps.items()
                                        if cid in crm_ids and m.zuper_uid not in zuper_uids),
            "zuper_not_mapped": sorted(uid for uid, cid in zuper_uids.items()
                                       if uid and (cid not in maps
                                                   or maps[cid].zuper_uid != uid))[:MAX_IDS],
        }
    digests = set(db.scalars(select(ZuperDigest.note_uid).where(
        ZuperDigest.note_uid.is_not(None))).all())
    for kind, lister, uid_fn in (("appointment", zapi.job_appointments, zapi.appointment_uid),
                                 ("note", zapi.job_notes, zapi.note_uid),
                                 ("task", zapi.job_tasks, zapi.task_uid)):
        crm_ids = {o.id for o in crm_records(db, kind, pipelines)}
        maps = {m.crm_id: m for m in db.scalars(select(ZuperMapping).where(
            ZuperMapping.crm_type == kind, ZuperMapping.state == "linked")).all()}
        entry = {"crm": len(crm_ids), "mapped": len(set(maps) & crm_ids),
                 "crm_not_mapped": _ids(crm_ids - set(maps))}
        if deep:
            seen: set[str] = set()
            for jm in db.scalars(select(ZuperMapping).where(
                    ZuperMapping.crm_type == "opportunity", ZuperMapping.state == "linked")).all():
                try:
                    seen |= {uid_fn(r) for r in lister(jm.zuper_uid) if uid_fn(r)} - digests
                except ZuperError as exc:
                    if exc.kind != "not_found":
                        raise
            mapped_uids = {m.zuper_uid for m in maps.values()}
            entry["zuper"] = len(seen)
            entry["mapped_not_in_zuper"] = _ids(cid for cid, m in maps.items()
                                                if m.zuper_uid not in seen)
            entry["zuper_not_mapped"] = sorted(seen - mapped_uids)[:MAX_IDS]
        out[kind] = entry
    out["mismatches"] = sum(len(v.get(k, [])) for v in out.values() if isinstance(v, dict)
                            for k in ("crm_not_mapped", "mapped_not_in_zuper",
                                      "zuper_not_mapped"))
    return out


def run(db: Session, *, commit: bool) -> tuple[int, dict]:
    engine.mark_quiet(db)
    report: dict = {"mode": "commit" if commit else "dry-run",
                    "started_at": datetime.now(UTC).isoformat(), "phases": {}}
    if not config.key_set():
        report["refused"] = config.NO_KEY_SENTENCE
        return 2, report
    with client.operator_mode():
        try:
            if not commit:
                with client.read_only():
                    checks = setup.run_checks(db, commit=False)
                    report["setup"] = _summary(checks)
                    report["categories"] = setup.ensure_categories(db, commit=False)
                    pipelines, report["selection"] = selected(db)
                    for kind in PHASES:
                        idx = index(kind) if kind in ID_FIELD else None
                        report["phases"][kind] = plan_phase(db, kind, pipelines, idx)
                db.rollback()
                return 0, report
            checks = setup.run_checks(db, commit=True)
            report["setup"] = _summary(checks)
            blocking = [c for c in checks if c["state"] == setup.FAIL
                        and c["key"] != "categories" and c["key"] != "webhook"]
            if blocking:
                db.rollback()
                report["refused"] = ("The setup check failed; nothing was loaded. " + " ".join(
                    s for c in blocking for s in c["sentences"]))
                return 3, report
            report["categories"] = setup.ensure_categories(db, commit=True)
            # The first sweep reads Zuper from the moment this load STARTED, so an edit made
            # in Zuper during the load, or before the switch is turned on, is not missed.
            state = engine.sync_state(db)
            if state.sweep_cursor is None:
                state.sweep_cursor = datetime.fromisoformat(report["started_at"])
                db.commit()
            pipelines, report["selection"] = selected(db)
            for kind in PHASES:
                ctx = engine.Ctx(db, index={kind: index(kind)} if kind in ID_FIELD else None)
                report["phases"][kind] = commit_phase(ctx, kind, pipelines)
                _checkpoint(db, report)
            setup.record_results(db, setup.run_checks(db, commit=True))
            report["verification"] = verify(db, pipelines, deep=True)
            _checkpoint(db, report)
        except selection.Refused as why:
            db.rollback()
            report["refused"] = str(why)
            return 3, report
        except ZuperError as exc:
            db.rollback()
            report["stopped"] = client.sentence(exc) + " Run the same command again to resume."
            if commit:
                _checkpoint(db, report)
            return 2, report
    return (1 if report["verification"]["mismatches"] else 0), report


def _summary(checks: list[dict]) -> dict:
    return {"pass": sum(c["state"] == setup.PASS for c in checks),
            "fail": [c["title"] for c in checks if c["state"] == setup.FAIL],
            "confirm_by_hand": sum(c["state"] == setup.BY_HAND for c in checks)}


def _checkpoint(db: Session, report: dict) -> None:
    engine.mark_quiet(db)
    state = engine.sync_state(db)
    state.load_report = json.loads(json.dumps(report, default=str))
    db.commit()


def render(report: dict) -> str:
    dry = report["mode"] == "dry-run"
    lines = ["MODE: %s" % ("DRY RUN — nothing was written in the CRM or in Zuper" if dry
                           else "COMMIT")]
    if report.get("refused"):
        lines.append("REFUSED: " + report["refused"])
    if report.get("setup"):
        s = report["setup"]
        lines.append("setup: %d pass, %d fail, %d to confirm by hand" % (
            s["pass"], len(s["fail"]), s["confirm_by_hand"]))
        for title in s["fail"]:
            lines.append("  FAIL: " + title)
    if report.get("selection"):
        sel = report["selection"]
        lines.append("day-one selection: %d card(s), %d to send, %d skipped" % (
            sel["selected"], sel["to_send"], sel["skipped"]))
        for group, n in sel["groups"].items():
            lines.append("  %s: %d" % (group, n))
        for reason, n in sel["skip_reasons"].items():
            lines.append("  skipped, %s: %d" % (reason, n))
        if sel["skipped_ids"]:
            lines.append("  skipped ids: %s" % ", ".join(map(str, sel["skipped_ids"])))
    if report.get("categories"):
        c = report["categories"]
        lines.append("categories: %d %s, statuses: %d %s" % (
            c["created_categories"], "to create" if dry else "created",
            c["created_statuses"], "to create" if dry else "created"))
    for kind, counts in report["phases"].items():
        lines.append("%s: %s" % (kind, ", ".join("%s %s" % (k, v) for k, v in counts.items()
                                                 if k != "failed_ids")))
        if counts.get("failed_ids"):
            lines.append("  failed ids: %s" % ", ".join(map(str, counts["failed_ids"])))
    if report.get("verification"):
        v = report["verification"]
        lines.append("verification: %d mismatch(es)" % v["mismatches"])
        for kind in PHASES:
            e = v.get(kind, {})
            lines.append("  %s: CRM %s, mapped %s, Zuper %s" % (
                kind, e.get("crm"), e.get("mapped"), e.get("zuper", "—")))
            for key in ("crm_not_mapped", "mapped_not_in_zuper", "zuper_not_mapped"):
                if e.get(key):
                    lines.append("    %s: %s" % (key, ", ".join(map(str, e[key]))))
    if report.get("stopped"):
        lines.append("STOPPED: " + report["stopped"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--commit", action="store_true", help="write (default: dry run)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    db = SessionLocal()
    try:
        code, report = run(db, commit=args.commit)
    finally:
        db.close()
    print(json.dumps(report, indent=2, default=str) if args.json else render(report))
    return code


if __name__ == "__main__":
    sys.exit(main())

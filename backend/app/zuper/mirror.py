"""Make the CRM imitate Zuper: `python -m app.zuper.mirror` (dry run; `--commit` writes).

The owner's decision (2026-09-23) reverses the original direction. `setup.py` pushed the CRM's
own stages INTO Zuper as statuses; from now on **Zuper's boards are the truth** and the CRM
copies them, one way (`ZUPER_PULL_ONLY`). This command is what makes the two sides line up
once; after it the webhook and the 15-minute sweep keep them level.

Three phases, each idempotent and safe to re-run:

  boards    every CRM pipeline's stages become the Zuper category's statuses, in board order:
            a stage whose name already matches is reused, one named in ALIASES is RENAMED (so
            its deals, its colour and its reports settings survive), the rest are created.
            A stage Zuper does not have is emptied — its deals move to the stage ALIASES names,
            else the first — and then deleted. Writes the pipeline<->category and
            stage<->status rows the sync reads (`zuper_mappings`).
  links     pairs the records that already exist on both sides, so nothing is duplicated: a
            Zuper job to the card holding the same Workiz job number (or the card its
            "CRM Opportunity ID" names), and that job's customer to the card's contact.
  backfill  pulls every in-scope Zuper job through the ordinary sync engine, which brings the
            stage, title, address, owner, Checklist answers, notes, tasks and visits across.

Reads Zuper with the operator's key (like `setup.py` and `load.py`), so it runs before the
Settings switch is on. It never writes to Zuper: the client refuses a non-GET while
`ZUPER_PULL_ONLY` is set, and `--commit` only writes to the CRM.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import Opportunity, Pipeline, Stage, ZuperMapping
from . import client, config, engine, mapping, zapi

# A CRM stage that means the same thing as a Zuper status under a different name. The stage is
# renamed rather than replaced, so the deals on it, its colour and its report switches stay.
ALIASES = {
    "Dream Team Roofing AHS": {
        "New Lead": "Work Order Received",
        # 2026-09-25: the owner renamed Zuper's "Inspecting" to "Inspection" (same status uid).
        "Inspecting": "Inspection",
        "Request the Approval (AHS)": "Approval Requested",
        "Repair in Process": "Repair In Process",
        "Submit The Invoice": "Invoice Submitted to AHS",
        "Submit Invoices": "Awaiting AHS Payment",
        "AHS Upgrades": "Proposal Made",
    },
    "Retail": {
        "Repair in Process": "Repair In Process",
    },
}

# A CRM stage whose Zuper status was DELETED: never renamed (that would steal the row an alias
# above renames), only emptied into the named stage before it goes. 2026-09-25: the owner took
# "On My Way" off the AHS board; a card still on it belongs in "Inspection".
MERGES = {
    "Dream Team Roofing AHS": {
        "On My Way": "Inspection",
    },
}


@dataclass
class Report:
    boards: list[dict] = field(default_factory=list)
    links: dict[str, int] = field(default_factory=dict)
    backfill: dict[str, int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def _stages(db: Session, pipeline_id: int) -> list[Stage]:
    return list(db.scalars(select(Stage).where(Stage.pipeline_id == pipeline_id)
                           .order_by(Stage.position, Stage.id)))


def _link(db: Session, crm_type: str, crm_id: int, zuper_type: str, uid: str,
          parent_uid: str | None = None) -> ZuperMapping:
    m = mapping.mapping_for(db, crm_type, crm_id)
    if m is None:
        m = ZuperMapping(crm_type=crm_type, crm_id=crm_id, zuper_type=zuper_type)
        db.add(m)
    m.zuper_uid, m.zuper_type, m.state = uid, zuper_type, "linked"
    m.parent_uid, m.updated_at = parent_uid, datetime.now(UTC)
    return m


# =============================================================================== boards

def boards(db: Session, commit: bool, report: Report) -> None:
    """The CRM's stages become Zuper's statuses, in Zuper's order."""
    by_name = {zapi.category_name(c): c for c in zapi.categories()}
    for pipeline_name, category_name in mapping.mirrored_categories().items():
        mirror_only = pipeline_name in mapping.MIRROR_ONLY_CATEGORIES
        entry: dict = {"pipeline": pipeline_name, "category": category_name,
                       "renamed": [], "created": [], "kept": [], "removed": []}
        p = db.scalars(select(Pipeline).where(Pipeline.name == pipeline_name)).first()
        cat = by_name.get(category_name)
        if mirror_only and cat is None:
            continue       # an optional board the account does not have (yet): nothing to copy
        report.boards.append(entry)
        if cat is None or (p is None and not mirror_only):
            entry["problem"] = ("no CRM pipeline named %r" % pipeline_name if p is None
                                else "no Zuper category named %r" % category_name)
            report.problems.append(entry["problem"])
            continue
        cat_uid = zapi.category_uid(cat)
        want = [zapi.status_name(s) for s in zapi.statuses(cat_uid)]
        uids = {zapi.status_name(s): zapi.status_uid(s) for s in zapi.statuses(cat_uid)}
        if not want:
            entry["problem"] = "Zuper listed no statuses for %r" % category_name
            report.problems.append(entry["problem"])
            continue
        if p is None:
            # A mirror-only board the CRM does not have yet: the pipeline is made here, open to
            # everyone (no permission rows), after the existing ones.
            entry["pipeline_created"] = True
            if commit:
                last = db.scalar(select(func.max(Pipeline.position))) or 0
                p = Pipeline(name=pipeline_name, position=last + 1,
                             updated_at=datetime.now(UTC))
                db.add(p)
                db.flush()
        have = _stages(db, p.id) if p is not None else []
        by_stage_name = {s.name.strip().lower(): s for s in have}
        aliases = {k.strip().lower(): v for k, v in ALIASES.get(pipeline_name, {}).items()}
        merges = {k.strip().lower(): v for k, v in MERGES.get(pipeline_name, {}).items()}
        taken: set[int] = set()
        plan: list[tuple[str, Stage | None, str]] = []   # (status name, stage, what)
        for name in want:
            s = by_stage_name.get(name.strip().lower())
            if s is not None and s.id not in taken:
                taken.add(s.id)
                plan.append((name, s, "kept"))
                continue
            old = next((x for x in have if x.id not in taken
                        and aliases.get(x.name.strip().lower()) == name), None)
            if old is not None:
                taken.add(old.id)
                plan.append((name, old, "renamed"))
            else:
                plan.append((name, None, "created"))
        extra = [s for s in have if s.id not in taken]
        # A stage Zuper does not have: its deals move to the alias target, else the first stage.
        first_id = None
        for i, (name, s, what) in enumerate(plan):
            entry[what].append(name if what != "renamed" else "%s -> %s" % (s.name, name))
            if commit:
                if s is None:
                    s = Stage(pipeline_id=p.id, name=name, position=i)
                    db.add(s)
                    db.flush()
                else:
                    s.name, s.position = name, i
                _link(db, "stage", s.id, "status", uids.get(name), parent_uid=cat_uid)
            if i == 0 and s is not None:
                first_id = s.id
        for s in extra:
            key = s.name.strip().lower()
            target_name = aliases.get(key) or merges.get(key)
            target = next((x for (n, x, _w) in plan if n == target_name and x is not None),
                          None)
            n_deals = db.scalar(select(func.count(Opportunity.id))
                                .where(Opportunity.stage_id == s.id)) or 0
            entry["removed"].append({"stage": s.name, "deals": n_deals,
                                     "moved_to": (target.name if target is not None
                                                  else plan[0][0])})
            if commit:
                to_id = target.id if target is not None else first_id
                if n_deals and to_id:
                    db.query(Opportunity).filter(Opportunity.stage_id == s.id) \
                        .update({Opportunity.stage_id: to_id}, synchronize_session=False)
                m = mapping.mapping_for(db, "stage", s.id)
                if m is not None:
                    db.delete(m)
                db.delete(s)
        if commit:
            pm = mapping.mapping_for(db, "pipeline", p.id)
            if pm is None:
                pm = ZuperMapping(crm_type="pipeline", crm_id=p.id, zuper_type="category")
                db.add(pm)
            pm.zuper_uid, pm.state, pm.updated_at = cat_uid, "linked", datetime.now(UTC)
            db.commit()


# =============================================================================== links

def _workiz(record: dict) -> str | None:
    values = mapping.custom_values(record)
    return mapping.text(values.get("Workiz Job #"))


def _crm_id(record: dict, label: str) -> int | None:
    raw = mapping.custom_values(record).get(label)
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def links(db: Session, commit: bool, report: Report) -> None:
    """Pair what already exists on both sides: job <-> card, customer <-> contact."""
    counts = {"jobs_seen": 0, "jobs_linked": 0, "jobs_already": 0, "jobs_unmatched": 0,
              "contacts_linked": 0, "contacts_already": 0, "contacts_taken": 0,
              "cards_without_job": 0}
    # A mapping is one-to-one on both sides (two unique indexes). Two Zuper customers can
    # point at the SAME CRM contact — the owner split customers who shared a phone in Workiz
    # (2026-09-23) — so the first pairing wins and the second is counted, not attempted.
    taken_cards = {m.crm_id for m in db.scalars(
        select(ZuperMapping).where(ZuperMapping.crm_type == "opportunity"))}
    taken_contacts = {m.crm_id for m in db.scalars(
        select(ZuperMapping).where(ZuperMapping.crm_type == "contact"))}
    cards = list(db.scalars(select(Opportunity)))
    by_workiz: dict[str, Opportunity] = {}
    for o in cards:
        wid = mapping.text((o.custom_fields or {}).get("workiz_id"))
        if wid:
            by_workiz.setdefault(wid, o)
    in_scope = {zapi.category_uid(c): zapi.category_name(c) for c in zapi.categories()
                if zapi.category_name(c) in set(mapping.mirrored_categories().values())}
    matched: set[int] = set()
    for record in zapi.jobs():
        if mapping.job_category_uid(record) not in in_scope:
            continue
        counts["jobs_seen"] += 1
        uid = mapping.uid(record, "job_uid")
        if uid is None:
            continue
        if mapping.mapping_by_uid(db, "job", uid) is not None:
            counts["jobs_already"] += 1
            continue
        o = None
        crm_id = _crm_id(record, "CRM Opportunity ID")
        if crm_id is not None:
            o = db.get(Opportunity, crm_id)
        if o is None:
            wid = _workiz(record)
            o = by_workiz.get(wid) if wid else None
        if o is None or o.id in matched or o.id in taken_cards:
            counts["jobs_unmatched"] += 1
            continue
        matched.add(o.id)
        taken_cards.add(o.id)
        counts["jobs_linked"] += 1
        if commit:
            _link(db, "opportunity", o.id, "job", uid)
            db.flush()
        cust_uid = mapping.uid(record, "customer_uid") or mapping.uid(
            record.get("customer") or {}, "customer_uid")
        if cust_uid and o.contact_id:
            if mapping.mapping_by_uid(db, "customer", cust_uid) is not None:
                counts["contacts_already"] += 1
            elif o.contact_id in taken_contacts:
                counts["contacts_taken"] += 1
            else:
                taken_contacts.add(o.contact_id)
                counts["contacts_linked"] += 1
                if commit:
                    _link(db, "contact", o.contact_id, "customer", cust_uid)
                    db.flush()
    counts["cards_without_job"] = sum(
        1 for o in cards if mapping.mapping_for(db, "opportunity", o.id) is None)
    if commit:
        db.commit()
    report.links = counts


# =============================================================================== backfill

def backfill(db: Session, commit: bool, report: Report, limit: int = 0) -> None:
    """Pull every in-scope Zuper job through the sync engine."""
    counts: dict[str, int] = {"jobs": 0}
    if not commit:
        report.backfill = {"would_pull": sum(
            1 for r in zapi.jobs()
            if mapping.stage_for_status(db, mapping.job_status_uid(r)) is not None
            or mapping.job_category_uid(r))}
        return
    uids = [mapping.uid(r, "job_uid") for r in zapi.jobs()]
    for i, uid in enumerate([u for u in uids if u]):
        if limit and i >= limit:
            break
        ctx = engine.Ctx(db)
        try:
            outcome = engine.pull(ctx, "job", uid)
        except client.ZuperError as exc:
            db.rollback()
            counts["error:" + exc.kind] = counts.get("error:" + exc.kind, 0) + 1
            report.problems.append("job %s: %s" % (uid, client.sentence(exc)))
            continue
        db.commit()
        counts["jobs"] += 1
        counts[outcome] = counts.get(outcome, 0) + 1
        for k, v in ctx.counts.items():
            counts[k] = counts.get(k, 0) + v
    report.backfill = counts


# =============================================================================== command

def run(db: Session, *, commit: bool, phases: set[str], limit: int = 0) -> tuple[int, dict]:
    report = Report()
    with client.operator_mode():
        if "boards" in phases:
            boards(db, commit, report)
        if "links" in phases:
            links(db, commit, report)
        if "backfill" in phases:
            backfill(db, commit, report, limit=limit)
    out = {"commit": commit, "pull_only": config.pull_only(), "boards": report.boards,
           "links": report.links, "backfill": report.backfill, "problems": report.problems}
    return (1 if report.problems else 0), out


def render(report: dict) -> str:
    lines = ["Zuper -> CRM mirror (%s)" % ("COMMITTED" if report["commit"] else "dry run")]
    if not report["pull_only"]:
        lines.append("  ! ZUPER_PULL_ONLY is not set: the CRM may still write to Zuper")
    for b in report["boards"]:
        made = " (new CRM pipeline)" if b.get("pipeline_created") else ""
        lines.append("  %s <- %s%s" % (b["pipeline"], b["category"], made))
        if b.get("problem"):
            lines.append("     problem: %s" % b["problem"])
            continue
        lines.append("     kept %d, renamed %d, created %d, removed %d"
                     % (len(b["kept"]), len(b["renamed"]), len(b["created"]),
                        len(b["removed"])))
        for r in b["renamed"]:
            lines.append("       rename %s" % r)
        for c in b["created"]:
            lines.append("       create %s" % c)
        for r in b["removed"]:
            lines.append("       remove %s (%d deal(s) -> %s)"
                         % (r["stage"], r["deals"], r["moved_to"]))
    if report["links"]:
        lines.append("  links: " + ", ".join("%s %s" % (k, v)
                                             for k, v in report["links"].items()))
    if report["backfill"]:
        lines.append("  backfill: " + ", ".join("%s %s" % (k, v)
                                                for k, v in report["backfill"].items()))
    for p in report["problems"][:20]:
        lines.append("  problem: %s" % p)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--commit", action="store_true", help="write (default: dry run)")
    ap.add_argument("--phase", action="append", choices=["boards", "links", "backfill"],
                    help="run only this phase (repeatable; default: all three)")
    ap.add_argument("--limit", type=int, default=0, help="backfill at most N jobs")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    db = SessionLocal()
    try:
        code, report = run(db, commit=args.commit,
                           phases=set(args.phase or ["boards", "links", "backfill"]),
                           limit=args.limit)
    finally:
        db.close()
    print(json.dumps(report, indent=2, default=str) if args.json else render(report))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

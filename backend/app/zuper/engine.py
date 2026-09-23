"""The sync itself: push a CRM record to Zuper, pull a Zuper record into the CRM, merge the
two by the owner's field-ownership rules, mirror deletes with a restorable snapshot first.

## The merge (`reconcile`)

Both records are read as a *view* (mapping.py) and compared, field by field, with the
`base` the two last agreed on. A field that differs from `base` changed on that side. Then:

| field | rule |
|---|---|
| money (the job value once a quote or invoice exists) | **Zuper** |
| a Workiz card's title, stage, address; a Workiz visit's schedule — before cutover | **Workiz** |
| a visit's schedule after cutover, when both sides changed it | **Zuper** |
| CRM-only fields (the "CRM" field group, the card owner, the Lead tag) | **CRM** |
| everything else — contact details, address, stage, notes, Checklist, tasks | **latest edit** |

("Workiz" means the CRM's value wins, because the CRM holds what Workiz last imported.)

Every value one side had changed that the sync then overwrote goes to `zuper_conflict_log`
with the rule, the side written to, and before / after. A plain propagation (one side
changed, the other still held `base`) is not a conflict: it is counted, not logged.

## Loops

Every session this module writes through is marked `info["zuper_quiet"]`, so the flush
listener never queues the sync's own CRM writes back out. `crm_hash` / `zuper_hash` are
recorded after every sync, so an event whose content is unchanged — our own write echoing
back through a webhook — does nothing. Events made by the "CRM Sync" user are dropped before
any read (`webhooks.py`).

This module never texts anyone: it imports neither `app.automations` nor the transport, and
its CRM writes go through the ORM, never through the routes that fire rules or agents.
"""
from __future__ import annotations

import enum
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import DateTime, Enum, func, select, update
from sqlalchemy.orm import Session

from .. import opportunity_workspace
from ..models import (
    Appointment,
    Calendar,
    CompanyCamLink,
    Contact,
    ContactTag,
    Conversation,
    ConversationEvent,
    Job,
    Opportunity,
    OpportunityContact,
    OpportunityFollower,
    OpportunityNote,
    OpportunityTask,
    Stage,
    Tag,
    User,
    ZuperConflict,
    ZuperDeleteSnapshot,
    ZuperDigest,
    ZuperDocument,
    ZuperMapping,
    ZuperSyncState,
)
from ..phones import store_phone
from . import config, mapping, outcomes, zapi
from .client import ZuperError, cents, is_read_only

log = logging.getLogger("zuper.engine")

MISSING = object()
LATEST, CRM, ZUPER = "latest", "crm", "zuper"

KINDS = ("contact", "opportunity", "appointment", "note", "task")
ZUPER_TYPE = {"contact": "customer", "opportunity": "job", "appointment": "appointment",
              "note": "job_note", "task": "service_task", "pipeline": "category",
              "stage": "status"}
CRM_TYPE = {v: k for k, v in ZUPER_TYPE.items()}
MODELS: dict[str, type] = {"contact": Contact, "opportunity": Opportunity,
                           "appointment": Appointment, "note": OpportunityNote,
                           "task": OpportunityTask}
# Parents before children, whenever order matters (restore, the initial load).
ORDER = {"contact": 0, "opportunity": 1, "appointment": 2, "note": 3, "task": 4}

SYNC_CALENDAR = "Zuper appointments"


def utcnow() -> datetime:
    return datetime.now(UTC)


def mark_quiet(db: Session, why: str = "zuper sync") -> Session:
    """A session the sync writes through: the listener queues nothing for it."""
    db.info["zuper_quiet"] = why
    return db


def sync_state(db: Session) -> ZuperSyncState:
    row = db.get(ZuperSyncState, 1)
    if row is None:
        row = ZuperSyncState(id=1)
        db.add(row)
        db.flush()
    return row


# ============================================================================ context

@dataclass
class Ctx:
    db: Session
    now: datetime = field(default_factory=utcnow)
    counts: dict[str, int] = field(default_factory=dict)
    # Set by the initial load: {kind: {crm id: Zuper record}}, so creation does not search
    # Zuper once per record.
    index: dict[str, dict[int, dict]] | None = None
    # When a CRM change was observed (a queued job's time), for latest-edit-wins on records
    # that carry no updated_at of their own (appointments).
    crm_changed_at: datetime | None = None
    # True while a card is being sent (Send to Zuper, AHS auto-send, the day-one load): the
    # only time a customer or a job may be created in Zuper from the CRM.
    sending: bool = False
    _users: dict[str, str] | None = None
    _before_cutover: bool | None = None

    def __post_init__(self):
        mark_quiet(self.db)

    def count(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    @property
    def before_cutover(self) -> bool:
        if self._before_cutover is None:
            self._before_cutover = config.before_cutover(self.db, self.now)
        return self._before_cutover

    @property
    def sources_by_name(self) -> dict[str, str]:
        row = config.peek_settings(self.db)
        return dict((row.lead_sources or {}) if row else {})

    @property
    def sources_by_uid(self) -> dict[str, str]:
        return {v: k for k, v in self.sources_by_name.items()}

    def zuper_user_for(self, email: str | None) -> str | None:
        """The Zuper user with this email — office staff only; technicians have none."""
        if not email:
            return None
        return self._user_index().get(email.lower())

    def zuper_email_for(self, user_uids: list[str]) -> str | None:
        """The email of the first assigned Zuper user the sync knows."""
        if not user_uids:
            return None
        by_uid = {u: e for e, u in self._user_index().items()}
        return next((by_uid[u] for u in user_uids if u in by_uid), None)

    def _user_index(self) -> dict[str, str]:
        if self._users is None:
            self._users = {}
            for user in zapi.users():
                e, u = zapi.user_email(user), zapi.user_uid(user)
                if e and u:
                    self._users[e] = u
        return self._users


# ============================================================================ policy and merge

WORKIZ_JOB_FIELDS = ("title", "stage", "street", "city", "state", "zip")


def policy(ctx: Ctx, kind: str, f: str, obj: Any) -> tuple[str, str, str]:
    """(mode, rule name, winner label) for one field of one SENT record (2026-09-16, v2).

    Zuper is the source of truth for a sent job: its stage, visits, address, value, title and
    owner, and its customer's details, are Zuper's. The CRM's own fields (the "CRM" field
    group) are the CRM's. Notes, Checklist answers and tasks are edited on both sides: latest
    edit wins. Before the Workiz cutover, a Workiz-origin job's title, stage, address and its
    Workiz visit's schedule are Workiz's — the CRM holds what the importer wrote.

    Under the one-way mirror (`ZUPER_PULL_ONLY`, 2026-09-23) there is nothing to arbitrate:
    Zuper wins every field it has a value for, including a Workiz-origin card's stage and a
    Checklist answer, because the CRM is a copy. The CRM's own "crm:" group stays the CRM's —
    it is computed here (the card's link, its technicians) and never travels either way."""
    if config.pull_only():
        if kind == "opportunity" and f.startswith("crm:"):
            return CRM, "crm_owned", "crm"
        return ZUPER, "zuper_mirror", "zuper"
    if kind == "contact":
        return ZUPER, "zuper_owned", "zuper"
    if kind == "opportunity":
        if f.startswith("crm:"):
            return CRM, "crm_owned", "crm"
        if f.startswith("cl:"):
            return LATEST, "latest_edit", ""
        if f in WORKIZ_JOB_FIELDS and ctx.before_cutover and mapping.is_workiz_card(obj):
            return CRM, "workiz_before_cutover", "workiz"
        return ZUPER, "zuper_owned", "zuper"
    if kind == "appointment":
        if ctx.before_cutover and mapping.is_workiz_appointment(ctx.db, obj):
            return CRM, "workiz_before_cutover", "workiz"
        return ZUPER, "zuper_owned", "zuper"
    return LATEST, "latest_edit", ""


@dataclass
class Plan:
    to_crm: dict[str, Any] = field(default_factory=dict)
    to_zuper: dict[str, Any] = field(default_factory=dict)
    conflicts: list[dict] = field(default_factory=list)


def merge(ctx: Ctx, kind: str, obj: Any, crm_view: dict, zup_view: dict,
          base: dict | None, crm_time: datetime | None, zuper_time: datetime | None) -> Plan:
    plan = Plan()
    for f in sorted(set(crm_view) | set(zup_view)):
        if f == "value" and "value" not in zup_view:
            continue                        # no quote or invoice yet: the CRM's value stands
        c, z = crm_view.get(f), zup_view.get(f)
        if kind == "opportunity" and f == "stage" and z is None:
            continue                        # a Zuper status the sync has no stage for
        if c == z:
            continue
        b = base.get(f, MISSING) if base is not None else MISSING
        crm_changed = b is MISSING or c != b
        zup_changed = b is MISSING or z != b
        mode, rule, label = policy(ctx, kind, f, obj)
        if mode == CRM:
            winner = "crm"
        elif mode == ZUPER:
            winner = "zuper"
        elif crm_changed and not zup_changed:
            winner = "crm"
        elif zup_changed and not crm_changed:
            winner = "zuper"
        else:
            winner = "zuper" if (zuper_time and crm_time and zuper_time > crm_time) else "crm"
        if winner == "crm":
            plan.to_zuper[f] = c
            if b is not MISSING and zup_changed:
                plan.conflicts.append({"field": f, "rule": rule, "winner": label or "crm",
                                       "written_to": "zuper", "before": z, "after": c})
        else:
            plan.to_crm[f] = z
            if b is not MISSING and crm_changed:
                plan.conflicts.append({"field": f, "rule": rule, "winner": label or "zuper",
                                       "written_to": "crm", "before": c, "after": z})
    return plan


def log_conflict(ctx: Ctx, kind: str, crm_id: int | None, zuper_uid: str | None,
                 c: dict) -> None:
    ctx.db.add(ZuperConflict(crm_type=kind, crm_id=crm_id, zuper_uid=zuper_uid,
                             field=c["field"][:120], rule=c["rule"], winner=c["winner"],
                             written_to=c["written_to"], before=_jsonable(c["before"]),
                             after=_jsonable(c["after"])))
    ctx.count("conflicts")


# ============================================================================ views

def view_crm(ctx: Ctx, kind: str, obj: Any, m: ZuperMapping | None) -> dict:
    if kind == "contact":
        return mapping.contact_view(ctx.db, obj)
    if kind == "opportunity":
        return mapping.opportunity_view(ctx.db, obj, m.zuper_uid if m else None)
    if kind == "appointment":
        return mapping.appointment_view(obj)
    if kind == "note":
        return mapping.note_view(obj)
    return mapping.task_view(obj)


def view_zuper(ctx: Ctx, kind: str, record: dict, m: ZuperMapping | None) -> dict:
    if kind == "contact":
        return mapping.customer_view(record, ctx.sources_by_uid)
    if kind == "opportunity":
        view = mapping.job_view(ctx.db, record, m.zuper_uid if m else None)
        if view.get("owner_email") is None:
            view["owner_email"] = ctx.zuper_email_for(mapping.assigned_user_uids(record))
        return view
    if kind == "appointment":
        return mapping.zuper_appointment_view(record)
    if kind == "note":
        return mapping.zuper_note_view(record)
    return mapping.zuper_task_view(record)


def crm_time(ctx: Ctx, obj: Any) -> datetime | None:
    value = getattr(obj, "updated_at", None)
    return mapping.aware(value) if value else ctx.crm_changed_at


# ============================================================================ writing the CRM

def _card_to_stage(db: Session, o: Opportunity, stage: Stage) -> None:
    """File the card at the bottom of `stage` and close the gap it left — the board's own
    contiguity rule. Through the ORM: no rule, no agent trigger (DECISIONS.md)."""
    old = o.stage_id
    o.pipeline_id = stage.pipeline_id
    o.stage_id = stage.id
    with db.no_autoflush:
        o.position = db.scalar(select(func.count(Opportunity.id)).where(
            Opportunity.stage_id == stage.id, Opportunity.id != o.id)) or 0
        if old != stage.id:
            left = db.scalars(select(Opportunity).where(
                Opportunity.stage_id == old, Opportunity.id != o.id)
                .order_by(Opportunity.position, Opportunity.id)).all()
    if old != stage.id:
        for i, card in enumerate(left):
            card.position = i


def apply_crm(ctx: Ctx, kind: str, obj: Any, changes: dict) -> set[str]:
    """Write Zuper's values onto the CRM record. Returns the fields it could not hold."""
    db = ctx.db
    rejected: set[str] = set()
    if kind == "contact":
        columns = {"street": "address_street", "city": "address_city",
                   "state": "address_state", "zip": "address_postal_code",
                   "company": "business_name", "email": "email"}
        for f, v in changes.items():
            if f in ("first_name", "last_name"):
                setattr(obj, f, v or "")
            elif f == "phone":
                obj.phone = store_phone(mapping.e164(v)) if v else None
            elif f == "source":
                if mapping.canonical_source(obj.source) != v:
                    obj.source = v
            elif f in columns:
                setattr(obj, columns[f], v)
            else:
                rejected.add(f)
    elif kind == "opportunity":
        columns = {"street": "address_street", "city": "address_city",
                   "state": "address_state", "zip": "address_postal_code"}
        blob = dict(obj.custom_fields or {})
        defs = mapping._checklist_defs(db)
        keys = {label: key for key, label in mapping.CHECKLIST}
        for f, v in changes.items():
            if f == "title":
                if v:
                    obj.title = str(v)[:255]
                else:
                    rejected.add(f)
            elif f == "stage":
                stage = db.get(Stage, v) if v else None
                if stage is None:
                    rejected.add(f)
                else:
                    _card_to_stage(db, obj, stage)
            elif f in columns:
                setattr(obj, columns[f], v)
            elif f == "value":
                obj.value_cents = int(v or 0)
            elif f == "owner_email":
                # Zuper's assigned office user, when a CRM user has that email. A job with no
                # such user (technicians have none) leaves the CRM owner as it is.
                user = db.scalar(select(User).where(func.lower(User.email) == v)) if v \
                    else None
                if user is not None:
                    obj.owner_id = user.id
            elif f.startswith("cl:") and f[3:] in keys:
                key = keys[f[3:]]
                ok, answer = mapping.checklist_to_crm(defs.get(key.removesuffix("__details")),
                                                      key, v)
                if not ok:
                    rejected.add(f)
                elif answer is None:
                    blob.pop(key, None)
                else:
                    blob[key] = answer
            else:
                rejected.add(f)
        if blob != (obj.custom_fields or {}):
            obj.custom_fields = blob
    elif kind == "appointment":
        for f, v in changes.items():
            if f == "title":
                if v:
                    obj.title = str(v)[:255]
                else:
                    rejected.add(f)
            elif f in ("starts_at", "ends_at"):
                moment = mapping.parse_time(v)
                if moment is None:
                    rejected.add(f)
                else:
                    setattr(obj, f, moment)
            elif f == "cancelled":
                if v:
                    obj.status = "cancelled"
                elif obj.status == "cancelled":
                    obj.status = "confirmed"
    elif kind == "note":
        for f, v in changes.items():
            if f == "body" and v:
                obj.body = v
            else:
                rejected.add(f)
    elif kind == "task":
        for f, v in changes.items():
            if f == "title":
                if v:
                    obj.title = str(v)[:255]
                else:
                    rejected.add(f)
            elif f == "description":
                obj.description = v
            elif f == "done":
                if v and obj.completed_at is None:
                    obj.completed_at = ctx.now
                elif not v:
                    obj.completed_at = None
            elif f == "due_at":
                obj.due_at = mapping.parse_time(v)
            else:
                rejected.add(f)
    return rejected


# ============================================================================ writing Zuper

def pipeline_category(db: Session, pipeline_id: int | None) -> str | None:
    if pipeline_id is None:
        return None
    m = mapping.mapping_for(db, "pipeline", pipeline_id)
    return m.zuper_uid if m and m.state == "linked" else None


def status_for_stage(db: Session, stage_id: int | None) -> str | None:
    if stage_id is None:
        return None
    m = mapping.mapping_for(db, "stage", stage_id)
    return m.zuper_uid if m and m.state == "linked" else None


def stage_order(db: Session, stage: Stage) -> int:
    """A stage's place in its pipeline: by position, then id. In production every Retail
    stage has position 0, so id order decides there (DECISIONS.md)."""
    with db.no_autoflush:
        ids = db.scalars(select(Stage.id).where(Stage.pipeline_id == stage.pipeline_id)
                         .order_by(Stage.position, Stage.id)).all()
    return ids.index(stage.id) if stage.id in ids else 0


def move_job_status(ctx: Ctx, job_uid: str, from_stage_id: int | None,
                    to_stage_id: int) -> None:
    db = ctx.db
    if config.pull_only():
        ctx.count("status_moves_suppressed")
        log.info("pull-only: not moving Zuper job %s to stage %s", job_uid, to_stage_id)
        return
    target = status_for_stage(db, to_stage_id)
    if not target:
        raise ZuperError("rejected", "stage %d has no Zuper status yet — run the setup "
                                     "command" % to_stage_id)
    to_stage = db.get(Stage, to_stage_id)
    from_stage = db.get(Stage, from_stage_id) if from_stage_id else None
    if from_stage is not None and to_stage is not None \
            and from_stage.pipeline_id == to_stage.pipeline_id \
            and stage_order(db, to_stage) < stage_order(db, from_stage):
        zapi.rollback_job_status(job_uid, target)
        ctx.count("status_rollbacks")
    else:
        zapi.set_job_status(job_uid, target)
        ctx.count("status_moves")


def ensure_zuper(ctx: Ctx, kind: str, crm_id: int | None) -> str | None:
    """The Zuper uid of a parent record, creating it first when it is not there yet."""
    if crm_id is None:
        return None
    m = mapping.mapping_for(ctx.db, kind, crm_id)
    if m is None or m.state != "linked" or not m.zuper_uid:
        push(ctx, kind, crm_id)
        m = mapping.mapping_for(ctx.db, kind, crm_id)
    return m.zuper_uid if m and m.state == "linked" else None


def _job_body(ctx: Ctx, o: Opportunity, view: dict) -> dict:
    stage = ctx.db.get(Stage, view.get("stage")) if view.get("stage") else None
    return mapping.job_payload(
        view, category_uid=pipeline_category(ctx.db, stage.pipeline_id if stage else
                                             o.pipeline_id),
        customer_uid=ensure_zuper(ctx, "contact", o.contact_id),
        assigned_user_uid=ctx.zuper_user_for(view.get("owner_email")))


def write_zuper(ctx: Ctx, kind: str, obj: Any, m: ZuperMapping, merged: dict,
                record: dict, to_zuper: dict) -> None:
    if config.pull_only():
        # One-way mirror: Zuper is the truth, so what the CRM would have sent is dropped here
        # (counted, not raised) and the next pull brings Zuper's value back.
        ctx.count("zuper_writes_suppressed")
        log.info("pull-only: not writing %s %s to Zuper (%s)", kind, m.zuper_uid,
                 ",".join(sorted(to_zuper)))
        return
    if kind == "contact":
        tags = record.get("customer_tags") or record.get("tags") or []
        zapi.update_customer(m.zuper_uid, mapping.customer_payload(
            obj, merged, sources_by_name=ctx.sources_by_name,
            existing_tags=[str(t) for t in tags] if isinstance(tags, list) else []))
    elif kind == "opportunity":
        if set(to_zuper) - {"stage", "value"}:
            zapi.update_job(m.zuper_uid, _job_body(ctx, obj, merged))
        if "stage" in to_zuper:
            current = mapping.stage_for_status(ctx.db, mapping.job_status_uid(record))
            move_job_status(ctx, m.zuper_uid, current, to_zuper["stage"])
    elif kind == "appointment":
        if merged.get("cancelled"):
            zapi.delete_appointment(m.zuper_uid)
        else:
            zapi.update_appointment(m.zuper_uid, mapping.appointment_payload(
                merged, m.parent_uid or ""))
    elif kind == "note":
        zapi.update_note(m.parent_uid or "", m.zuper_uid, mapping.note_payload(merged))
    elif kind == "task":
        zapi.update_task(m.parent_uid or "", m.zuper_uid, mapping.task_payload(merged))
    ctx.count("zuper_writes")


# ============================================================================ reconcile

def reconcile(ctx: Ctx, kind: str, obj: Any, m: ZuperMapping, record: dict) -> str:
    db = ctx.db
    crm_view = view_crm(ctx, kind, obj, m)
    zup_view = view_zuper(ctx, kind, record, m)
    plan = merge(ctx, kind, obj, crm_view, zup_view, m.base, crm_time(ctx, obj),
                 mapping.parse_time(record.get("updated_at")))
    rejected = apply_crm(ctx, kind, obj, plan.to_crm)
    for f in rejected:
        # The CRM cannot hold Zuper's value (an option its question does not offer). For a
        # field edited on both sides the CRM's value goes back to Zuper, logged; a Zuper-owned
        # field is simply left as the CRM has it.
        before = plan.to_crm.pop(f)
        plan.conflicts = [c for c in plan.conflicts if c["field"] != f]
        if policy(ctx, kind, f, obj)[0] == ZUPER:
            continue
        if crm_view.get(f) != before:
            plan.to_zuper[f] = crm_view.get(f)
            plan.conflicts.append({"field": f, "rule": "crm_cannot_hold", "winner": "crm",
                                   "written_to": "zuper", "before": before,
                                   "after": crm_view.get(f)})
    merged = {**crm_view, **plan.to_crm}
    if plan.to_crm:
        db.flush()
        ctx.count("crm_writes")
    if plan.to_zuper:
        write_zuper(ctx, kind, obj, m, merged, record, plan.to_zuper)
    for c in plan.conflicts:
        log_conflict(ctx, kind, obj.id, m.zuper_uid, c)
    expected_zuper = {**zup_view, **plan.to_zuper}
    after = view_crm(ctx, kind, obj, m)
    m.base = _jsonable(merged)
    m.crm_hash = mapping.digest(after)
    m.zuper_hash = mapping.digest(expected_zuper)
    m.zuper_updated_at = str(record.get("updated_at") or "")[:40] or None
    m.updated_at = ctx.now
    m.last_error = None
    if plan.to_zuper:
        m.last_pushed_at = ctx.now
    if plan.to_crm:
        m.last_pulled_at = ctx.now
    db.flush()
    return "updated" if (plan.to_crm or plan.to_zuper) else "unchanged"


# ============================================================================ push (CRM -> Zuper)

def is_linked(db: Session, kind: str, crm_id: int | None) -> bool:
    m = mapping.mapping_for(db, kind, crm_id) if crm_id is not None else None
    return m is not None and m.state == "linked" and bool(m.zuper_uid)


def in_scope(ctx: Ctx, kind: str, obj: Any) -> bool:
    """Which CRM records reach Zuper (2026-09-16, v2): only what belongs to a SENT job.

    A card or a customer goes to Zuper only by being sent (`send`, the AHS auto-send, the
    day-one load — `ctx.sending`). After that, its notes and tasks follow it; its visits are
    Zuper's, so a CRM visit is created there only while sending, or when the Workiz importer
    booked it on a Workiz-origin sent job before cutover."""
    db = ctx.db
    if kind in ("contact", "opportunity"):
        return ctx.sending or is_linked(db, kind, obj.id)
    opp_id = getattr(obj, "opportunity_id", None)
    if opp_id is None or not (ctx.sending or is_linked(db, "opportunity", opp_id)):
        return False
    if kind == "appointment" and not is_linked(db, "appointment", obj.id):
        if obj.status == "cancelled":
            return False                    # never create a cancelled visit in Zuper
        return ctx.sending or (ctx.before_cutover and mapping.is_workiz_appointment(db, obj))
    return True


def fetch(ctx: Ctx, kind: str, m: ZuperMapping) -> dict | None:
    try:
        if kind == "contact":
            return zapi.customer(m.zuper_uid)
        if kind == "opportunity":
            return zapi.job(m.zuper_uid)
        if kind == "appointment":
            return zapi.appointment(m.zuper_uid)
        rows = zapi.job_notes(m.parent_uid) if kind == "note" else zapi.job_tasks(m.parent_uid)
        uid_fn = zapi.note_uid if kind == "note" else zapi.task_uid
        return next((r for r in rows if uid_fn(r) == m.zuper_uid), None)
    except ZuperError as exc:
        if exc.kind == "not_found":
            return None
        raise


def push(ctx: Ctx, kind: str, crm_id: int) -> str:
    """Bring Zuper up to date with one CRM record (creating it there when it is new)."""
    db = ctx.db
    obj = db.get(MODELS[kind], crm_id)
    if obj is None:
        return "gone"
    if not in_scope(ctx, kind, obj):
        ctx.count("out_of_scope")
        return "out_of_scope"
    m = mapping.mapping_for(db, kind, crm_id)
    if m is not None and m.state == "deleted":
        return "deleted"
    if m is None or m.state != "linked" or not m.zuper_uid:
        return create_in_zuper(ctx, kind, obj, m)
    if m.crm_hash == mapping.digest(view_crm(ctx, kind, obj, m)):
        return "unchanged"
    record = fetch(ctx, kind, m)
    if record is None or zapi.is_deleted(record):
        return mirror_zuper_delete(ctx, kind, m)
    outcome = reconcile(ctx, kind, obj, m, record)
    ctx.count("pushed_" + kind if outcome == "updated" else "unchanged")
    return outcome


def _existing_in_zuper(ctx: Ctx, kind: str, obj: Any, m: ZuperMapping,
                       view: dict) -> dict | None:
    """Zuper has no idempotency keys: before creating, look for the record we may already
    have made — by the CRM id custom field (customers, jobs), or, for a create interrupted
    after it was committed as `creating`, by content under the same job."""
    if kind in ("contact", "opportunity"):
        if ctx.index is not None and kind in ctx.index:
            return ctx.index[kind].get(obj.id)
        found = (zapi.find_customers_by_crm_id(obj.id) if kind == "contact"
                 else zapi.find_jobs_by_crm_id(obj.id))
        return found[0] if found else None
    if m.id is None or not m.parent_uid:
        return None                         # never started before: nothing to find
    if kind == "note":
        rows, uid_fn, fn = zapi.job_notes(m.parent_uid), zapi.note_uid, mapping.zuper_note_view
    elif kind == "task":
        rows, uid_fn, fn = zapi.job_tasks(m.parent_uid), zapi.task_uid, mapping.zuper_task_view
    else:
        rows = zapi.job_appointments(m.parent_uid)
        uid_fn, fn = zapi.appointment_uid, mapping.zuper_appointment_view
    taken = set(ctx.db.scalars(select(ZuperMapping.zuper_uid).where(
        ZuperMapping.parent_uid == m.parent_uid, ZuperMapping.zuper_uid.is_not(None))).all())
    for rec in rows:
        if uid_fn(rec) and uid_fn(rec) not in taken and fn(rec) == {
                k: view.get(k) for k in fn(rec)}:
            return rec
    return None


def _uid_for(kind: str, record: dict) -> str | None:
    return {"contact": lambda r: mapping.uid(r, "customer_uid"),
            "opportunity": lambda r: mapping.uid(r, "job_uid"),
            "appointment": zapi.appointment_uid, "note": zapi.note_uid,
            "task": zapi.task_uid}[kind](record)


def create_in_zuper(ctx: Ctx, kind: str, obj: Any, m: ZuperMapping | None) -> str:
    db = ctx.db
    if is_read_only():
        raise ZuperError("refused", "a dry run creates nothing")
    parent_uid = None
    if kind in ("appointment", "note", "task"):
        parent_uid = ensure_zuper(ctx, "opportunity", obj.opportunity_id)
        if not parent_uid:
            ctx.count("waiting_for_job")
            return "waiting_for_job"
    if m is None:
        m = ZuperMapping(crm_type=kind, crm_id=obj.id, zuper_type=ZUPER_TYPE[kind],
                         state="creating", parent_uid=parent_uid)
        db.add(m)
    m.parent_uid = parent_uid or m.parent_uid
    view = view_crm(ctx, kind, obj, m)
    existing = _existing_in_zuper(ctx, kind, obj, m, view)
    if existing is not None and _uid_for(kind, existing):
        m.zuper_uid, m.state, m.base = _uid_for(kind, existing), "linked", None
        db.flush()
        ctx.count("linked_existing_" + kind)
        reconcile(ctx, kind, obj, m, existing)
        db.commit()
        return "linked_existing"
    # The checkpoint: committed BEFORE the create, so a crash after Zuper answers is
    # followed by the search above, not by a second record.
    m.state = "creating"
    db.commit()
    if kind == "contact":
        uid = zapi.create_customer(mapping.customer_payload(
            obj, view, sources_by_name=ctx.sources_by_name))
    elif kind == "opportunity":
        uid = zapi.create_job(_job_body(ctx, obj, view))
    elif kind == "appointment":
        uid = zapi.create_appointment(mapping.appointment_payload(view, parent_uid))
    elif kind == "note":
        uid = zapi.create_note(parent_uid, mapping.note_payload(view))
    else:
        uid = zapi.create_task(parent_uid, mapping.task_payload(view))
    m.zuper_uid, m.state = uid, "linked"
    m.base = _jsonable(view)
    m.crm_hash = m.zuper_hash = mapping.digest(view)
    m.last_pushed_at = m.updated_at = ctx.now
    m.last_error = None
    db.commit()
    if kind == "opportunity" and view.get("stage"):
        move_job_status(ctx, uid, None, view["stage"])
    ctx.count("created_" + kind)
    return "created"


# ============================================================================ Send to Zuper

ADDRESS_PARTS = (("address_street", "street"), ("address_city", "city"),
                 ("address_state", "state"), ("address_postal_code", "ZIP code"))


def send_problems(db: Session, o: Opportunity) -> list[str]:
    """What stops this card being sent, as sentences (2026-09-16, v2): the customer's name and
    phone, and the JOB's address (the card's own — the contact's can be copied onto it with
    "Use contact address"). Not the switches or the role: those are the route's."""
    problems = []
    if pipeline_category(db, o.pipeline_id) is None:
        problems.append("Only cards in the AHS or Retail pipeline go to Zuper, and that "
                        "pipeline's category must be set up first (python -m "
                        "app.zuper.setup --commit).")
    c = db.get(Contact, o.contact_id) if o.contact_id else None
    if c is None:
        problems.append("Choose the customer (a contact) for this card first.")
    else:
        if not (c.first_name or "").strip() and not (c.last_name or "").strip():
            problems.append("The customer needs a name.")
        if not (c.phone or "").strip():
            problems.append("The customer needs a phone number.")
    missing = [label for col, label in ADDRESS_PARTS if not (getattr(o, col) or "").strip()]
    if missing:
        problems.append("The job needs its address — missing: %s. The contact's address can "
                        "be copied with “Use contact address”." % ", ".join(missing))
    return problems


def send(ctx: Ctx, opportunity_id: int) -> str:
    """Send one card to Zuper: its customer (found by CRM id, else created), the job, its
    visits, notes and tasks. Idempotent: a card already linked does nothing, and every record
    is created through the `creating` checkpoint, so a retry after a crash searches first."""
    db = ctx.db
    o = db.get(Opportunity, opportunity_id)
    if o is None:
        return "gone"
    m = mapping.mapping_for(db, "opportunity", o.id)
    if m is not None and m.state == "linked" and m.zuper_uid:
        return "already_sent"
    if m is not None and m.state == "deleted":
        return "deleted"
    problems = send_problems(db, o)
    if problems:
        if m is None:
            m = ZuperMapping(crm_type="opportunity", crm_id=o.id, zuper_type="job")
            db.add(m)
        m.state, m.last_error, m.updated_at = "failed", " ".join(problems), ctx.now
        db.commit()
        ctx.count("send_refused")
        return "refused"
    ctx.sending = True
    try:
        outcome = create_in_zuper(ctx, "opportunity", o, m)
        if outcome in ("created", "linked_existing"):
            for kind, model in (("appointment", Appointment), ("note", OpportunityNote),
                                ("task", OpportunityTask)):
                stmt = select(model.id).where(model.opportunity_id == o.id).order_by(model.id)
                if kind == "appointment":
                    stmt = stmt.where(Appointment.status != "cancelled")
                for child_id in db.scalars(stmt).all():
                    push(ctx, kind, child_id)
                    db.commit()
    finally:
        ctx.sending = False
    ctx.count("sent")
    return "sent" if outcome == "created" else outcome


def mark_send_failed(db: Session, opportunity_id: int, why: str) -> None:
    m = mapping.mapping_for(db, "opportunity", opportunity_id)
    if m is not None and m.state in ("queued", "creating") and not m.zuper_uid:
        m.state, m.last_error, m.updated_at = "failed", why[:1000], utcnow()
        db.commit()


# ============================================================================ pull (Zuper -> CRM)

def fetch_uid(kind: str, uid: str) -> dict | None:
    try:
        if kind == "contact":
            return zapi.customer(uid)
        if kind == "opportunity":
            return zapi.job(uid)
        if kind == "appointment":
            return zapi.appointment(uid)
    except ZuperError as exc:
        if exc.kind == "not_found":
            return None
        raise
    raise ValueError(kind)


def pull(ctx: Ctx, zuper_type: str, uid: str, record: dict | None = None, *,
         via_job: bool = False) -> str:
    """Bring the CRM up to date with one Zuper record (creating it here when it is new — a
    customer only as the customer of a job being pulled)."""
    kind = CRM_TYPE[zuper_type]
    db = ctx.db
    if record is None:
        record = fetch_uid(kind, uid)
    m = mapping.mapping_by_uid(db, zuper_type, uid)
    if record is None or zapi.is_deleted(record):
        if m is not None and m.state == "linked":
            return mirror_zuper_delete(ctx, kind, m)
        return "deleted_unmapped"
    if m is None:
        return create_in_crm(ctx, kind, uid, record, via_job=via_job)
    if m.state != "linked":
        return "ignored_" + m.state
    obj = db.get(MODELS[kind], m.crm_id)
    if obj is None:
        return "crm_deleted"                # the CRM delete path mirrors it
    zup_view = view_zuper(ctx, kind, record, m)
    if m.zuper_hash == mapping.digest(zup_view) \
            and m.crm_hash == mapping.digest(view_crm(ctx, kind, obj, m)):
        outcome = "unchanged"
    else:
        outcome = reconcile(ctx, kind, obj, m, record)
        ctx.count("pulled_" + kind if outcome == "updated" else "unchanged")
    if kind == "opportunity":
        pull_children(ctx, obj, m)
    return outcome


def _link_new(ctx: Ctx, kind: str, obj: Any, uid: str, parent_uid: str | None,
              record: dict) -> ZuperMapping:
    m = ZuperMapping(crm_type=kind, crm_id=obj.id, zuper_type=ZUPER_TYPE[kind],
                     zuper_uid=uid, parent_uid=parent_uid, state="linked")
    ctx.db.add(m)
    ctx.db.flush()
    zup_view = view_zuper(ctx, kind, record, m)
    m.base = _jsonable(zup_view)
    m.zuper_hash = mapping.digest(zup_view)
    m.crm_hash = mapping.digest(view_crm(ctx, kind, obj, m))
    m.zuper_updated_at = str(record.get("updated_at") or "")[:40] or None
    m.last_pulled_at = m.updated_at = ctx.now
    return m


def _sync_calendar(db: Session, o: Opportunity) -> int | None:
    """Where a visit booked in Zuper lands: the calendar of the card's latest visit, else
    one calendar named "Zuper appointments", made the first time it is needed."""
    latest = db.scalar(select(Appointment.calendar_id).where(
        Appointment.opportunity_id == o.id, Appointment.calendar_id.is_not(None))
        .order_by(Appointment.starts_at.desc()))
    if latest:
        return latest
    cal = db.scalar(select(Calendar).where(Calendar.name == SYNC_CALENDAR))
    if cal is None:
        cal = Calendar(name=SYNC_CALENDAR, pipeline_id=o.pipeline_id)
        db.add(cal)
        db.flush()
    return cal.id


def _first_stage(db: Session, pipeline_id: int) -> Stage | None:
    return db.scalar(select(Stage).where(Stage.pipeline_id == pipeline_id)
                     .order_by(Stage.position, Stage.id))


def _claimable(db: Session, kind: str, obj: Any) -> ZuperMapping | bool | None:
    """A Zuper record naming an existing CRM record by its CRM id: link to it when that record
    has no mapping (None) or one still `creating` with no uid (that row) — our own create,
    arriving before its commit. False when it is already linked to something else."""
    if obj is None:
        return False
    m = mapping.mapping_for(db, kind, obj.id)
    if m is None:
        return None
    return m if m.state == "creating" and not m.zuper_uid else False


def create_in_crm(ctx: Ctx, kind: str, uid: str, record: dict, *,
                  via_job: bool = False) -> str:
    db = ctx.db
    if kind == "contact":
        claimed = mapping.custom_values(record).get("CRM Contact ID")
        c = db.get(Contact, int(claimed)) if str(claimed or "").strip().isdigit() else None
        m = _claimable(db, "contact", c)
        if not via_job and not (c is not None and m is not None and m is not False):
            # A customer reaches the CRM only as the customer of a job (2026-09-16) — or as
            # our own create seen before its commit (a `creating` row naming it).
            ctx.count("customer_without_job")
            return "customer_without_job"
        if c is not None and m is not False:
            if m is None:
                m = ZuperMapping(crm_type="contact", crm_id=c.id, zuper_type="customer")
                db.add(m)
            m.zuper_uid, m.state = uid, "linked"
            db.flush()
            reconcile(ctx, "contact", c, m, record)
            ctx.count("linked_existing_contact")
            return "linked_existing"
        view = mapping.customer_view(record, ctx.sources_by_uid)
        c = Contact(first_name=view.get("first_name") or "", last_name=view.get("last_name") or "",
                    email=view.get("email"),
                    phone=store_phone(mapping.e164(view.get("phone"))) if view.get("phone")
                    else None,
                    business_name=view.get("company"), address_street=view.get("street"),
                    address_city=view.get("city"), address_state=view.get("state"),
                    address_postal_code=view.get("zip"), source=view.get("source"),
                    created_by=mapping.SYNC_CREATED_BY)
        db.add(c)
        db.flush()
        m = _link_new(ctx, "contact", c, uid, None, record)
        # The customer learns its CRM id and link (and the Lead tag) straight away.
        write_zuper(ctx, "contact", c, m, mapping.contact_view(db, c), record, {"crm": True})
        m.crm_hash = m.zuper_hash = mapping.digest(mapping.contact_view(db, c))
        m.base = _jsonable(mapping.contact_view(db, c))
        ctx.count("created_contact")
        return "created_in_crm"

    if kind == "opportunity":
        category = mapping.job_category_uid(record)
        pm = mapping.mapping_by_uid(db, "category", category) if category else None
        if pm is None or pm.state != "linked":
            ctx.count("out_of_scope")
            return "out_of_scope"
        claimed = mapping.custom_values(record).get("CRM Opportunity ID")
        o = db.get(Opportunity, int(claimed)) if str(claimed or "").strip().isdigit() else None
        m = _claimable(db, "opportunity", o)
        if o is not None and m is not False:
            if m is None:
                m = ZuperMapping(crm_type="opportunity", crm_id=o.id, zuper_type="job")
                db.add(m)
            m.zuper_uid, m.state = uid, "linked"
            db.flush()
            reconcile(ctx, "opportunity", o, m, record)
            pull_children(ctx, o, m)
            ctx.count("linked_existing_opportunity")
            return "linked_existing"
        customer_uid = mapping.uid(record.get("customer") or {}, "customer_uid") \
            if isinstance(record.get("customer"), dict) else (
            record.get("customer_uid") or (record.get("customer")
                                           if isinstance(record.get("customer"), str) else None))
        contact_id = None
        if customer_uid:
            cm = mapping.mapping_by_uid(db, "customer", customer_uid)
            if cm is None:
                pull(ctx, "customer", customer_uid, via_job=True)
                cm = mapping.mapping_by_uid(db, "customer", customer_uid)
            contact_id = cm.crm_id if cm is not None and cm.state == "linked" else None
        view = mapping.job_view(db, record, uid)
        stage = db.get(Stage, view["stage"]) if view.get("stage") else None
        if stage is None or stage.pipeline_id != pm.crm_id:
            stage = _first_stage(db, pm.crm_id)
        if stage is None:
            return "out_of_scope"
        o = Opportunity(title=view.get("title") or "Zuper job", pipeline_id=pm.crm_id,
                        stage_id=stage.id, contact_id=contact_id,
                        address_street=view.get("street"), address_city=view.get("city"),
                        address_state=view.get("state"),
                        address_postal_code=view.get("zip"),
                        created_by=mapping.SYNC_CREATED_BY, custom_fields={})
        o.position = db.scalar(select(func.count(Opportunity.id)).where(
            Opportunity.stage_id == stage.id)) or 0
        db.add(o)
        db.flush()
        m = ZuperMapping(crm_type="opportunity", crm_id=o.id, zuper_type="job",
                         zuper_uid=uid, state="linked")
        db.add(m)
        db.flush()
        # Base = the card as just made, so every field Zuper holds reads as Zuper's change.
        m.base = _jsonable(view_crm(ctx, "opportunity", o, m))
        # A fresh merge against no base: Zuper's Checklist answers land, and the CRM-owned
        # fields (its id, its link) go back to the job.
        reconcile(ctx, "opportunity", o, m, record)
        pull_children(ctx, o, m)
        ctx.count("created_opportunity")
        return "created_in_crm"

    if kind == "appointment":
        job_uid = zapi.appointment_job_uid(record)
        jm = mapping.mapping_by_uid(db, "job", job_uid) if job_uid else None
        if jm is None and job_uid:
            pull(ctx, "job", job_uid)
            jm = mapping.mapping_by_uid(db, "job", job_uid)
        o = db.get(Opportunity, jm.crm_id) if jm is not None and jm.state == "linked" else None
        if o is None:
            ctx.count("out_of_scope")
            return "out_of_scope"
        existing = mapping.mapping_by_uid(db, "appointment", uid)
        if existing is not None:
            return "already_linked"
        view = mapping.zuper_appointment_view(record)
        start, end = mapping.parse_time(view.get("starts_at")), mapping.parse_time(
            view.get("ends_at"))
        if view.get("cancelled") or start is None:
            return "skipped"
        a = Appointment(title=view.get("title") or o.title, calendar_id=_sync_calendar(db, o),
                        contact_id=o.contact_id, opportunity_id=o.id, starts_at=start,
                        ends_at=end or start, status="confirmed")
        db.add(a)
        db.flush()
        _link_new(ctx, "appointment", a, uid, job_uid, record)
        ctx.count("created_appointment")
        return "created_in_crm"
    raise ValueError(kind)


def pull_children(ctx: Ctx, o: Opportunity, jm: ZuperMapping) -> None:
    """A job's notes, service tasks and appointments, both ways of new and deleted."""
    db = ctx.db
    job_uid = jm.zuper_uid
    digests = set(db.scalars(select(ZuperDigest.note_uid).where(
        ZuperDigest.note_uid.is_not(None))).all())
    for kind, lister, uid_fn in (("note", zapi.job_notes, zapi.note_uid),
                                 ("task", zapi.job_tasks, zapi.task_uid),
                                 ("appointment", zapi.job_appointments, zapi.appointment_uid)):
        try:
            rows = lister(job_uid)
        except ZuperError as exc:
            if exc.kind == "not_found":
                rows = []
            else:
                raise
        seen: set[str] = set()
        for rec in rows:
            child_uid = uid_fn(rec)
            if not child_uid or child_uid in digests:
                continue
            seen.add(child_uid)
            if kind == "appointment":
                pull(ctx, "appointment", child_uid, rec)
                continue
            cm = mapping.mapping_by_uid(db, ZUPER_TYPE[kind], child_uid)
            if cm is None:
                view = view_zuper(ctx, kind, rec, None)
                if kind == "note":
                    if not view.get("body"):
                        continue
                    child = OpportunityNote(opportunity_id=o.id, body=view["body"])
                else:
                    if not view.get("title"):
                        continue
                    child = OpportunityTask(
                        opportunity_id=o.id, contact_id=o.contact_id, title=view["title"],
                        description=view.get("description"),
                        due_at=mapping.parse_time(view.get("due_at")),
                        completed_at=ctx.now if view.get("done") else None)
                db.add(child)
                db.flush()
                _link_new(ctx, kind, child, child_uid, job_uid, rec)
                ctx.count("created_" + kind)
            elif cm.state == "linked":
                child = db.get(MODELS[kind], cm.crm_id)
                if child is None:
                    continue
                changed = cm.zuper_hash != mapping.digest(view_zuper(ctx, kind, rec, cm)) or \
                    cm.crm_hash != mapping.digest(view_crm(ctx, kind, child, cm))
                if changed and reconcile(ctx, kind, child, cm, rec) == "updated":
                    ctx.count("pulled_" + kind)
        for cm in db.scalars(select(ZuperMapping).where(
                ZuperMapping.crm_type == kind, ZuperMapping.parent_uid == job_uid,
                ZuperMapping.state == "linked")).all():
            if cm.zuper_uid and cm.zuper_uid not in seen:
                mirror_zuper_delete(ctx, kind, cm)
    db.flush()


# ============================================================================ quotes and invoices

def _status(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("status") or value.get("status_name") or value.get("name")
    return str(value).strip().upper() if value else None


def _date(value: Any) -> str | None:
    if not value:
        return None
    return str(value)[:10]


def pull_document(ctx: Ctx, kind: str, uid: str, record: dict | None = None) -> str:
    """A quote or an invoice into the money panel's cache, and the owner's three rules:
    invoice fully paid -> Won; quote declined -> an urgent task for the card owner; the card's
    value follows the invoice (else approved quote) total."""
    db = ctx.db
    if record is None:
        try:
            record = zapi.estimate(uid) if kind == ZuperDocument.QUOTE else zapi.invoice(uid)
        except ZuperError as exc:
            if exc.kind != "not_found":
                raise
            record = None
    doc = db.scalar(select(ZuperDocument).where(ZuperDocument.zuper_uid == uid))
    if record is None:
        if doc is not None and not doc.removed_in_zuper:
            doc.removed_in_zuper = True
            _follow_value(ctx, doc.job_uid)
        return "deleted"
    if doc is None:
        doc = ZuperDocument(kind=kind, zuper_uid=uid)
        db.add(doc)
    quote = kind == ZuperDocument.QUOTE
    job = record.get("job")
    doc.job_uid = (job.get("job_uid") if isinstance(job, dict) else None) or record.get(
        "job_uid") or doc.job_uid
    customer = record.get("customer")
    doc.customer_uid = (customer.get("customer_uid") if isinstance(customer, dict) else None) \
        or record.get("customer_uid") or doc.customer_uid
    number = (record.get("estimate_number") or record.get("estimate_no")) if quote else (
        record.get("invoice_number") or record.get("invoice_no"))
    prefix = record.get("prefix") or ""
    doc.number = ("%s%s" % (prefix, number))[:60] if number is not None else None
    doc.status = _status(record.get("estimate_status" if quote else "invoice_status")
                         or record.get("status"))
    doc.total_cents = cents(record.get("total") if record.get("total") is not None
                            else record.get("grand_total"))
    if not quote:
        for key in ("balance_due", "amount_due", "due_amount", "remaining_amount", "balance"):
            if record.get(key) is not None:
                doc.balance_cents = cents(record.get(key))
                break
    doc.issued_on = _date(record.get("estimate_date" if quote else "invoice_date")
                          or record.get("created_at"))
    doc.due_on = _date(record.get("expiry_date") or record.get("valid_till")) if quote else \
        _date(record.get("due_date"))
    doc.zuper_updated_at = str(record.get("updated_at") or "")[:40] or None
    doc.removed_in_zuper = bool(record.get("is_deleted"))
    doc.fetched_at = ctx.now
    jm = mapping.mapping_by_uid(db, "job", doc.job_uid) if doc.job_uid else None
    o = db.get(Opportunity, jm.crm_id) if jm is not None and jm.state == "linked" else None
    doc.opportunity_id = o.id if o else None
    cm = mapping.mapping_by_uid(db, "customer", doc.customer_uid) if doc.customer_uid else None
    doc.contact_id = cm.crm_id if cm is not None and cm.state == "linked" else (
        o.contact_id if o else None)
    db.flush()
    ctx.count("documents")
    if o is not None:
        # The owner dropped automatic outcomes (2026-09-16): a paid invoice does not mark the
        # card Won and a declined quote makes no task. The one place a future, owner-approved
        # rule would hook in — none ship, and the hook is off (app/zuper/outcomes.py).
        outcomes.document_changed(ctx, doc, o)
    _follow_value(ctx, doc.job_uid)
    return "stored"


def _follow_value(ctx: Ctx, job_uid: str | None) -> None:
    db = ctx.db
    jm = mapping.mapping_by_uid(db, "job", job_uid) if job_uid else None
    o = db.get(Opportunity, jm.crm_id) if jm is not None and jm.state == "linked" else None
    if o is None:
        return
    value = mapping.documents_value(db, job_uid)
    if value is None or o.value_cents == value:
        return
    log_conflict(ctx, "opportunity", o.id, job_uid, {
        "field": "value", "rule": "zuper_money", "winner": "zuper", "written_to": "crm",
        "before": o.value_cents, "after": value})
    o.value_cents = value
    db.flush()
    base = dict(jm.base or {})
    base["value"] = value
    jm.base = base
    jm.crm_hash = mapping.digest(view_crm(ctx, "opportunity", o, jm))
    ctx.count("value_from_documents")


# ============================================================================ deletes and snapshots

def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return mapping.aware(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


def row_of(obj: Any) -> dict:
    return {c.key: _jsonable(getattr(obj, c.key)) for c in obj.__table__.columns}


def rebuild(model: type, data: dict) -> Any:
    kwargs = {}
    for col in model.__table__.columns:
        if col.key not in data:
            continue
        value = data[col.key]
        if value is not None and isinstance(col.type, DateTime):
            value = datetime.fromisoformat(value)
        elif value is not None and isinstance(col.type, Enum) and col.type.enum_class:
            value = col.type.enum_class(value)
        kwargs[col.key] = value
    return model(**kwargs)


def snapshot(db: Session, kind: str, obj: Any, *, detached: dict | None = None) -> dict:
    """Everything needed to put the record back: its row, and its own children."""
    detached = detached or {}
    with db.no_autoflush:
        if kind == "opportunity":
            m = mapping.mapping_for(db, "opportunity", obj.id)
            appointment_ids = set(db.scalars(select(Appointment.id).where(
                Appointment.opportunity_id == obj.id)).all())
            appointment_ids |= set(detached.get(("opportunity", obj.id), []))
            if m is not None and m.zuper_uid:
                appointment_ids |= set(db.scalars(select(ZuperMapping.crm_id).where(
                    ZuperMapping.crm_type == "appointment",
                    ZuperMapping.parent_uid == m.zuper_uid)).all())
            children = {
                "notes": [row_of(r) for r in db.scalars(select(OpportunityNote).where(
                    OpportunityNote.opportunity_id == obj.id)).all()],
                "tasks": [row_of(r) for r in db.scalars(select(OpportunityTask).where(
                    OpportunityTask.opportunity_id == obj.id)).all()],
                "followers": [row_of(r) for r in db.scalars(select(OpportunityFollower).where(
                    OpportunityFollower.opportunity_id == obj.id)).all()],
                "additional_contacts": [row_of(r) for r in db.scalars(
                    select(OpportunityContact).where(
                        OpportunityContact.opportunity_id == obj.id)).all()],
                "companycam_links": [row_of(r) for r in db.scalars(select(CompanyCamLink).where(
                    CompanyCamLink.opportunity_id == obj.id)).all()],
                "appointment_ids": sorted(appointment_ids),
            }
        elif kind == "contact":
            conversations = []
            for conv in db.scalars(select(Conversation).where(
                    Conversation.contact_id == obj.id)).all():
                conversations.append({"row": row_of(conv), "events": [
                    row_of(e) for e in db.scalars(select(ConversationEvent).where(
                        ConversationEvent.conversation_id == conv.id)).all()]})
            children = {
                "tags": [row_of(t) for t in db.scalars(select(ContactTag).where(
                    ContactTag.contact_id == obj.id)).all()],
                "conversations": conversations,
                "opportunity_ids": sorted(set(db.scalars(select(Opportunity.id).where(
                    Opportunity.contact_id == obj.id)).all())
                    | set(detached.get(("contact_opportunities", obj.id), []))),
                "appointment_ids": sorted(set(db.scalars(select(Appointment.id).where(
                    Appointment.contact_id == obj.id)).all())
                    | set(detached.get(("contact_appointments", obj.id), []))),
                "additional_contact_links": [row_of(r) for r in db.scalars(
                    select(OpportunityContact).where(
                        OpportunityContact.contact_id == obj.id)).all()],
                "task_ids": sorted(db.scalars(select(OpportunityTask.id).where(
                    OpportunityTask.contact_id == obj.id)).all()),
            }
        else:
            children = {}
    return {"record": row_of(obj), "children": children}


def new_batch() -> str:
    return uuid.uuid4().hex


def _release_reminders(db: Session, appointment_id: int) -> None:
    """The contact delete's reminder tidy-up, without importing main: a pending reminder for
    a visit that lost its contact is cancelled and its dedupe key released."""
    db.execute(update(Job).where(
        Job.type == "appointment_reminder", Job.status == "pending",
        Job.payload["appointment_id"].as_integer() == appointment_id).values(
        status="cancelled", dedupe_key=None))


def delete_in_crm(ctx: Ctx, kind: str, obj: Any) -> None:
    """The CRM's own delete semantics, exactly: a deal's visits are detached and its own
    notes, tasks and links removed; a contact's deals and visits are detached and its
    conversations removed. Nothing beyond the record's own children."""
    db = ctx.db
    if kind == "opportunity":
        m = mapping.mapping_for(db, "opportunity", obj.id)
        for a in db.scalars(select(Appointment).where(
                Appointment.opportunity_id == obj.id)).all():
            a.opportunity_id = None
        db.flush()
        opportunity_workspace.remove_opportunity_records(db, obj.id)
        for link in db.scalars(select(CompanyCamLink).where(
                CompanyCamLink.opportunity_id == obj.id)).all():
            db.delete(link)
        if m is not None and m.zuper_uid:
            db.execute(update(ZuperMapping).where(
                ZuperMapping.parent_uid == m.zuper_uid,
                ZuperMapping.crm_type.in_(("note", "task"))).values(state="deleted"))
    elif kind == "contact":
        for o in db.scalars(select(Opportunity).where(Opportunity.contact_id == obj.id)).all():
            o.contact_id = None
        for a in db.scalars(select(Appointment).where(Appointment.contact_id == obj.id)).all():
            a.contact_id = None
            _release_reminders(db, a.id)
        for conv in db.scalars(select(Conversation).where(
                Conversation.contact_id == obj.id)).all():
            db.delete(conv)
        opportunity_workspace.release_contact(db, obj.id)
    db.flush()
    db.delete(obj)
    db.flush()


def mirror_zuper_delete(ctx: Ctx, kind: str, m: ZuperMapping) -> str:
    """Zuper deleted it: snapshot the CRM record in full, log it, then delete it here.
    A visit is never deleted in the CRM — it is cancelled, as everywhere else."""
    db = ctx.db
    obj = db.get(MODELS[kind], m.crm_id)
    if kind == "appointment":
        if obj is not None and obj.status != "cancelled":
            log_conflict(ctx, "appointment", obj.id, m.zuper_uid, {
                "field": "status", "rule": "mirrored_cancel", "winner": "zuper",
                "written_to": "crm", "before": obj.status, "after": "cancelled"})
            obj.status = "cancelled"
        m.state = "deleted"
        m.updated_at = ctx.now
        db.flush()
        ctx.count("cancelled_from_zuper")
        return "cancelled"
    if obj is None:
        m.state = "deleted"
        return "already_gone"
    if kind == "contact":
        return _mirror_customer_delete(ctx, obj, m)
    db.add(ZuperDeleteSnapshot(
        batch=new_batch(), direction="zuper_to_crm", crm_type=kind, crm_id=obj.id,
        zuper_type=m.zuper_type, zuper_uid=m.zuper_uid,
        snapshot=snapshot(db, kind, obj), state="mirrored", mirrored_at=ctx.now))
    db.flush()
    delete_in_crm(ctx, kind, obj)
    m.state = "deleted"
    m.updated_at = ctx.now
    db.flush()
    ctx.count("deleted_in_crm_" + kind)
    return "deleted_in_crm"


def _mirror_customer_delete(ctx: Ctx, c: Contact, m: ZuperMapping) -> str:
    """A customer deleted in Zuper (2026-09-16, v2). Its SENT cards — the mirrored job side —
    are deleted here, each snapshotted. The contact itself is deleted only when nothing else
    of the CRM's hangs off it: a contact that also has cards never sent to Zuper, or any
    conversation, is kept, and that is logged (state "kept", not restorable — nothing of it
    was removed)."""
    from . import locks
    db = ctx.db
    batch = new_batch()
    for oid in sorted(locks.sent_ids(db, db.scalars(select(Opportunity.id).where(
            Opportunity.contact_id == c.id)).all())):
        om = mapping.mapping_for(db, "opportunity", oid)
        o = db.get(Opportunity, oid)
        if o is None or om is None:
            continue
        db.add(ZuperDeleteSnapshot(
            batch=batch, direction="zuper_to_crm", crm_type="opportunity", crm_id=o.id,
            zuper_type="job", zuper_uid=om.zuper_uid, snapshot=snapshot(db, "opportunity", o),
            state="mirrored", mirrored_at=ctx.now))
        db.flush()
        delete_in_crm(ctx, "opportunity", o)
        om.state, om.updated_at = "deleted", ctx.now
        ctx.count("deleted_in_crm_opportunity")
    unsent = db.scalar(select(func.count(Opportunity.id)).where(
        Opportunity.contact_id == c.id)) or 0
    talks = db.scalar(select(func.count(Conversation.id)).where(
        Conversation.contact_id == c.id)) or 0
    m.state, m.updated_at = "deleted", ctx.now
    if unsent or talks:
        why = []
        if unsent:
            why.append("%d card%s never sent to Zuper" % (unsent, "" if unsent == 1 else "s"))
        if talks:
            why.append("a conversation")
        db.add(ZuperDeleteSnapshot(
            batch=batch, direction="zuper_to_crm", crm_type="contact", crm_id=c.id,
            zuper_type="customer", zuper_uid=m.zuper_uid,
            snapshot={"record": {"id": c.id, "first_name": c.first_name,
                                 "last_name": c.last_name}, "children": {}},
            state="kept", mirrored_at=ctx.now,
            error="The contact was kept: it has %s." % " and ".join(why)))
        db.flush()
        ctx.count("contact_kept")
        return "contact_kept"
    db.add(ZuperDeleteSnapshot(
        batch=batch, direction="zuper_to_crm", crm_type="contact", crm_id=c.id,
        zuper_type="customer", zuper_uid=m.zuper_uid, snapshot=snapshot(db, "contact", c),
        state="mirrored", mirrored_at=ctx.now))
    db.flush()
    delete_in_crm(ctx, "contact", c)
    db.flush()
    ctx.count("deleted_in_crm_contact")
    return "deleted_in_crm"


def mirror_crm_deletes(ctx: Ctx, batch: str) -> dict:
    """The CRM deleted these (snapshots written by the flush listener): delete them in Zuper.
    A note or task that went with its own job is not deleted separately — the job took it."""
    db = ctx.db
    rows = db.scalars(select(ZuperDeleteSnapshot).where(
        ZuperDeleteSnapshot.batch == batch, ZuperDeleteSnapshot.direction == "crm_to_zuper",
        ZuperDeleteSnapshot.state == "pending")).all()
    jobs_in_batch = {r.zuper_uid for r in rows if r.crm_type == "opportunity"}
    done: dict[str, int] = {}
    for row in sorted(rows, key=lambda r: ORDER.get(r.crm_type, 9)):
        m = mapping.mapping_for(db, row.crm_type, row.crm_id)
        parent = m.parent_uid if m is not None else row.snapshot.get("parent_uid")
        try:
            if not row.zuper_uid:
                row.state = "skipped"
            elif row.crm_type in ("note", "task") and parent in jobs_in_batch:
                row.state = "skipped"
                row.error = "deleted with its job"
            elif row.crm_type == "contact":
                zapi.delete_customer(row.zuper_uid)
                row.state = "mirrored"
            elif row.crm_type == "opportunity":
                zapi.delete_job(row.zuper_uid)
                row.state = "mirrored"
            elif row.crm_type == "note":
                zapi.delete_note(parent, row.zuper_uid)
                row.state = "mirrored"
            elif row.crm_type == "task":
                zapi.delete_task(parent, row.zuper_uid)
                row.state = "mirrored"
            else:
                row.state = "skipped"
        except ZuperError as exc:
            if exc.kind != "not_found":
                row.error = str(exc)[:500]
                raise
            row.state = "mirrored"          # already gone there
        row.mirrored_at = ctx.now
        if m is not None:
            m.state = "deleted"
            m.updated_at = ctx.now
        done[row.state] = done.get(row.state, 0) + 1
        db.commit()
    return done


# ============================================================================ restore

def _restore_crm(db: Session, row: ZuperDeleteSnapshot) -> list[str]:
    """Put the record back from its snapshot, and re-attach what it had, where the thing
    it had is still there and still detached. Returns sentences; raises ValueError with a
    sentence when the record cannot come back."""
    model = MODELS[row.crm_type]
    if db.get(model, row.crm_id) is not None:
        return ["already in the CRM"]
    data = row.snapshot or {}
    record = dict(data.get("record") or {})
    children = data.get("children") or {}
    said: list[str] = []
    if row.crm_type == "opportunity":
        stage = db.get(Stage, record.get("stage_id"))
        if stage is None:
            raise ValueError("its stage no longer exists — recreate the stage first")
        if record.get("contact_id") and db.get(Contact, record["contact_id"]) is None:
            record["contact_id"] = None
        db.add(rebuild(Opportunity, record))
        db.flush()
        for key, child in (("notes", OpportunityNote), ("tasks", OpportunityTask),
                           ("followers", OpportunityFollower),
                           ("additional_contacts", OpportunityContact),
                           ("companycam_links", CompanyCamLink)):
            for r in children.get(key, []):
                if db.get(child, r.get("id")) is None:
                    db.add(rebuild(child, r))
        for aid in children.get("appointment_ids", []):
            a = db.get(Appointment, aid)
            if a is not None and a.opportunity_id is None:
                a.opportunity_id = row.crm_id
        said.append("restored in the CRM")
    elif row.crm_type == "contact":
        db.add(rebuild(Contact, record))
        db.flush()
        for r in children.get("tags", []):
            if db.get(Tag, r.get("tag_id")) is not None and db.get(ContactTag, r.get("id")) is None:
                db.add(rebuild(ContactTag, r))
        for conv in children.get("conversations", []):
            if db.get(Conversation, conv["row"].get("id")) is None:
                db.add(rebuild(Conversation, conv["row"]))
                db.flush()
                for e in conv.get("events", []):
                    if db.get(ConversationEvent, e.get("id")) is None:
                        db.add(rebuild(ConversationEvent, e))
        for oid in children.get("opportunity_ids", []):
            o = db.get(Opportunity, oid)
            if o is not None and o.contact_id is None:
                o.contact_id = row.crm_id
        for aid in children.get("appointment_ids", []):
            a = db.get(Appointment, aid)
            if a is not None and a.contact_id is None:
                a.contact_id = row.crm_id
        for r in children.get("additional_contact_links", []):
            if db.get(Opportunity, r.get("opportunity_id")) is not None and \
                    db.get(OpportunityContact, r.get("id")) is None:
                db.add(rebuild(OpportunityContact, r))
        for tid in children.get("task_ids", []):
            t = db.get(OpportunityTask, tid)
            if t is not None and t.contact_id is None:
                t.contact_id = row.crm_id
        said.append("restored in the CRM")
    else:
        if db.get(Opportunity, record.get("opportunity_id")) is None:
            raise ValueError("its opportunity no longer exists — restore that first")
        db.add(rebuild(model, record))
        said.append("restored in the CRM")
    db.flush()
    return said


def _recover_zuper(ctx: Ctx, row: ZuperDeleteSnapshot, m: ZuperMapping | None,
                   went_with_job: bool) -> str:
    kind = row.crm_type
    if went_with_job:
        return "recovered in Zuper with its job"
    if kind in ("contact", "opportunity"):
        try:
            record = fetch_uid(kind, row.zuper_uid)
        except ZuperError as exc:
            if exc.kind != "not_found":
                raise
            record = None
        if record is not None and not zapi.is_deleted(record):
            return "already in Zuper"
        (zapi.recover_customer if kind == "contact" else zapi.recover_job)(row.zuper_uid)
        return "recovered in Zuper"
    # Notes and service tasks have no recover endpoint: the next sync creates them again.
    if m is not None:
        ctx.db.delete(m)
        ctx.db.flush()
    return "will be created again in Zuper by the next sync"


def restore(db: Session, snapshot_id: int, user_id: int | None) -> dict:
    """Restore the snapshot's whole batch, parents first. CRM side from the snapshot; Zuper
    side through its 90-day recover endpoints (sync on) — or re-created (notes, tasks)."""
    ctx = Ctx(db)
    first = db.get(ZuperDeleteSnapshot, snapshot_id)
    if first is None:
        raise LookupError("snapshot not found")
    rows = db.scalars(select(ZuperDeleteSnapshot).where(
        ZuperDeleteSnapshot.batch == first.batch)).all()
    results = []
    for row in sorted(rows, key=lambda r: ORDER.get(r.crm_type, 9)):
        if row.state == "restored":
            results.append({"id": row.id, "state": row.state, "detail": row.restore_detail})
            continue
        said: list[str] = []
        failed = False
        went_with_job = row.state == "skipped" and row.error == "deleted with its job"
        try:
            said += _restore_crm(db, row)
        except ValueError as why:
            db.rollback()
            mark_quiet(db)
            row = db.get(ZuperDeleteSnapshot, row.id)
            row.state, row.restore_detail = "restore_failed", "Not restored: %s." % why
            row.restored_by_id = user_id
            db.commit()
            results.append({"id": row.id, "state": row.state, "detail": row.restore_detail})
            continue
        m = mapping.mapping_for(db, row.crm_type, row.crm_id)
        if row.zuper_uid:
            if config.env_enabled() and config.key_set():
                try:
                    said.append(_recover_zuper(ctx, row, m, went_with_job))
                except ZuperError as exc:
                    failed = True
                    said.append("not recovered in Zuper: " + str(exc)[:200])
            else:
                failed = True
                said.append("not recovered in Zuper: " + config.OFF_SENTENCE)
        m = mapping.mapping_for(db, row.crm_type, row.crm_id)
        if m is not None:
            # The next sync compares both sides afresh rather than trusting a stale base.
            m.state, m.base, m.crm_hash, m.zuper_hash = "linked", None, None, None
            m.updated_at = ctx.now
        row.state = "restore_failed" if failed else "restored"
        row.restored_at = ctx.now
        row.restored_by_id = user_id
        detail = "; ".join(said)
        row.restore_detail = detail[:1].upper() + detail[1:] + "."
        db.commit()
        results.append({"id": row.id, "state": row.state, "detail": row.restore_detail})
    return {"batch": first.batch, "results": results}

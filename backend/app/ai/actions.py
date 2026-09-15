"""The actions catalogue: every tool an agent may be allowed, and the ONLY code that turns a
model's tool call into a change in the CRM.

Each action is a tool with a JSON schema. The owner ticks which ones an agent may use.
Every call, whatever the model says, goes through the same three steps:

  1. `validate_arguments` — the arguments are an object that matches the schema exactly
     (types, required keys, lengths, no extra keys). Otherwise refused.
  2. `prepare` — the SUBJECT check. A tool never names a contact: it acts on the run's own
     customer, and on the run's own opportunity when it has one. An `opportunity_id` or
     `appointment_id` the model supplies must belong to that customer (and to that
     opportunity), and must be visible to an agent under per-pipeline access. Otherwise
     refused — and nothing is written.
  3. By mode: Auto-pilot EXECUTES through the same service functions the staff routes use
     (main.move_to_stage, main.book_appointment, workspace.add_task …); Suggest stores a
     pending suggestion; Try-it records "Would: …". A refusal is logged with its reason.

Agents act with DISPATCHER rules and are never narrowed by "Only assigned data". They act
as a system principal with no user id, so a pipeline restricted to named users is hidden
from every agent. What they write carries `ai_agent_id` and reads "AI: <agent name>".

Voice-only actions (transfer_call, end_call) are defined for phase 3 and offered to no Text
agent. Agents never create contacts or opportunities: there is no such action, and a tool
that would need a record that does not exist is refused.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select

from .. import auth, custom_fields, pipeline_access
from ..models import (
    AiAgentThread,
    Appointment,
    Calendar,
    Contact,
    Conversation,
    ConversationEvent,
    DeliveryStatus,
    EventType,
    Opportunity,
    OpportunityTask,
    Pipeline,
    Role,
    Stage,
    User,
)
from ..phones import format_phone
from . import knowledge

TZ = ZoneInfo("America/New_York")

READ = "read"          # runs in every mode, Try-it included
INTERNAL = "internal"  # AI-only bookkeeping (a knowledge gap): runs except in Try-it
WRITE = "write"        # a change to the CRM or a message: Auto executes, Suggest suggests
VOICE = "voice"        # phase 3


class Refused(Exception):
    """A tool call that will not be carried out, with the reason written for the log."""


@dataclass
class Subject:
    contact_id: int
    opportunity_id: int | None = None
    appointment_id: int | None = None


@dataclass
class Context:
    """Everything an action may use. Built by the engine for a run, and again when a
    person approves a suggestion."""
    db: object
    agent_id: int
    agent_name: str
    config: dict
    subject: Subject
    run_id: int | None = None
    principal: auth.Principal = None  # type: ignore[assignment]
    escalated: bool = False
    # The staff user approving a suggestion, when this context is an approval.
    approved_by: int | None = None
    notes: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.principal is None:
            self.principal = agent_principal(self.agent_name)


def agent_principal(agent_name: str) -> auth.Principal:
    """DISPATCHER rules, no user id (so no per-pipeline grant), never restricted."""
    return auth.Principal(user_id=0, email="ai-agent@system.invalid",
                          name="AI: " + agent_name, role=Role.DISPATCHER, kind="pat",
                          scopes=frozenset(), only_assigned=False)


# ------------------------------------------------------------------ schema validation

def _obj(props: dict, required: tuple = ()) -> dict:
    return {"type": "object", "properties": props, "required": list(required),
            "additionalProperties": False}


def _str(desc: str, max_length: int = 1000, min_length: int = 1) -> dict:
    return {"type": "string", "description": desc, "minLength": min_length,
            "maxLength": max_length}


def _int(desc: str, minimum: int = 1, maximum: int | None = None) -> dict:
    s = {"type": "integer", "description": desc, "minimum": minimum}
    if maximum is not None:
        s["maximum"] = maximum
    return s


def validate_arguments(schema: dict, args) -> str | None:
    """None when `args` matches `schema`, else what is wrong. The subset this catalogue
    uses: object / string / integer / boolean, required, additionalProperties, lengths,
    bounds, and one free-form object of scalar answers."""
    if not isinstance(args, dict):
        return "the arguments must be an object"
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in args:
            return "missing %s" % key
    if schema.get("additionalProperties") is False:
        extra = sorted(set(args) - set(props))
        if extra:
            return "not an argument of this tool: %s" % ", ".join(extra)
    for key, value in args.items():
        spec = props.get(key)
        if spec is None:
            continue
        t = spec.get("type")
        if t == "string":
            if not isinstance(value, str):
                return "%s must be text" % key
            if len(value.strip()) < spec.get("minLength", 0):
                return "%s is empty" % key
            if len(value) > spec.get("maxLength", 10**6):
                return "%s is too long" % key
        elif t == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                return "%s must be a whole number" % key
            if value < spec.get("minimum", -(10**12)) or value > spec.get("maximum", 10**12):
                return "%s is out of range" % key
        elif t == "boolean":
            if not isinstance(value, bool):
                return "%s must be true or false" % key
        elif t == "object":
            if not isinstance(value, dict):
                return "%s must be an object" % key
            if len(value) > 60:
                return "%s has too many entries" % key
            for k, v in value.items():
                if not isinstance(k, str) or not (v is None or isinstance(
                        v, (str, int, float, bool))):
                    return "%s must map question keys to plain answers" % key
                if isinstance(v, str) and len(v) > 5000:
                    return "an answer in %s is too long" % key
    return None


# ------------------------------------------------------------------ subject resolution

def _http(e: HTTPException) -> Refused:
    return Refused(str(e.detail))


def contact_of(ctx: Context) -> Contact:
    c = ctx.db.get(Contact, ctx.subject.contact_id)
    if c is None:
        raise Refused("this run's contact no longer exists")
    return c


def _visible(ctx: Context, o: Opportunity) -> bool:
    try:
        pipeline_access.get_opportunity(ctx.db, ctx.principal, o.id)
    except HTTPException:
        return False
    return True


def opportunity_of(ctx: Context, args: dict) -> Opportunity:
    """The deal a write acts on. Never one outside the run's subject."""
    db = ctx.db
    wanted = args.get("opportunity_id")
    if ctx.subject.opportunity_id is not None:
        if wanted is not None and wanted != ctx.subject.opportunity_id:
            raise Refused("opportunity %s is not this run's opportunity (%s) — an agent "
                          "acts only on its own subject" % (wanted, ctx.subject.opportunity_id))
        wanted = ctx.subject.opportunity_id
    if wanted is None:
        deals = [o for o in db.scalars(select(Opportunity).where(
            Opportunity.contact_id == ctx.subject.contact_id,
            Opportunity.status == "open").order_by(Opportunity.id)).all() if _visible(ctx, o)]
        if not deals:
            raise Refused("this customer has no open opportunity, and agents never create one")
        if len(deals) > 1:
            raise Refused("this customer has %d open opportunities — name one with "
                          "opportunity_id (see get_context)" % len(deals))
        return deals[0]
    o = db.get(Opportunity, wanted)
    if o is None or o.contact_id != ctx.subject.contact_id:
        raise Refused("opportunity %s does not belong to this run's customer — an agent "
                      "acts only on its own subject" % wanted)
    if not _visible(ctx, o):
        raise Refused("opportunity %s is in a pipeline AI agents cannot access" % wanted)
    return o


def appointment_of(ctx: Context, args: dict) -> Appointment:
    a = ctx.db.get(Appointment, args["appointment_id"])
    if a is None or a.contact_id != ctx.subject.contact_id:
        raise Refused("appointment %s does not belong to this run's customer"
                      % args["appointment_id"])
    if (ctx.subject.opportunity_id is not None and a.opportunity_id is not None
            and a.opportunity_id != ctx.subject.opportunity_id):
        raise Refused("appointment %s is for a different opportunity than this run's"
                      % a.id)
    return a


def parse_when(value: str) -> datetime:
    """An ISO time; without an offset it is America/New_York wall-clock time."""
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        raise Refused("%r is not a date and time (use YYYY-MM-DDTHH:MM, Eastern time)"
                      % value) from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    dt = dt.astimezone(UTC)
    if dt <= datetime.now(UTC) + timedelta(minutes=5):
        raise Refused("that time is not in the future")
    return dt


def local(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return auth.as_aware(dt).astimezone(TZ).strftime("%a %b %d %Y, %I:%M %p Eastern")


# ------------------------------------------------------------------ the catalogue

@dataclass
class Prepared:
    summary: str
    args: dict
    opportunity_id: int | None = None
    data: dict = field(default_factory=dict)


@dataclass
class Action:
    name: str
    label: str
    description: str
    schema: dict
    kind: str
    channels: tuple = ("text",)

    def prepare(self, ctx: Context, args: dict) -> Prepared:
        return Prepared(self.label, args)

    def execute(self, ctx: Context, prep: Prepared) -> dict:
        raise NotImplementedError


# --- reads ---------------------------------------------------------------

class GetContext(Action):
    def execute(self, ctx, prep):
        return build_context(ctx)


def build_context(ctx: Context) -> dict:
    """The run subject's CRM record — nothing about anyone else. Internal notes are left
    out: they are the team talking to itself and are not sent to a model provider."""
    db = ctx.db
    c = contact_of(ctx)
    out: dict = {
        "contact": {"id": c.id, "name": c.name, "first_name": c.first_name,
                    "phone": format_phone(c.phone) if c.phone else None,
                    "email": c.email, "type": c.contact_type,
                    "do_not_disturb": bool(c.dnd),
                    "address": _address(c)},
        "opportunities": [], "upcoming_appointments": [], "recent_conversation": [],
        "open_tasks": [],
    }
    checklist_groups = custom_fields.checklist_ids(db)
    defs = custom_fields.load_defs(db, include_archived=False)
    deals = db.scalars(select(Opportunity).where(Opportunity.contact_id == c.id)
                       .order_by(Opportunity.updated_at.desc())).all()
    for o in deals:
        if not _visible(ctx, o):
            continue
        if ctx.subject.opportunity_id is not None and o.id != ctx.subject.opportunity_id \
                and o.status != "open":
            continue
        stage = db.get(Stage, o.stage_id)
        pipeline = db.get(Pipeline, o.pipeline_id)
        answers = o.custom_fields or {}
        checklist = []
        for d in defs:
            if d.group_id in checklist_groups and custom_fields.shows_on(d, o.pipeline_id) \
                    and not custom_fields.is_reserved(d.key):
                checklist.append({"key": d.key, "question": d.label, "type": d.field_type,
                                  "options": list(d.options or []),
                                  "answer": answers.get(d.key)})
        out["opportunities"].append({
            "id": o.id, "title": o.title, "status": o.status,
            "pipeline": pipeline.name if pipeline else None,
            "stage": stage.name if stage else None, "stage_id": o.stage_id,
            "this_runs_opportunity": o.id == ctx.subject.opportunity_id,
            "address": _address(o),
            "stages_in_pipeline": [{"id": s.id, "name": s.name}
                                   for s in (pipeline.stages if pipeline else [])],
            "checklist": checklist})
    now = datetime.now(UTC)
    for a in db.scalars(select(Appointment).where(
            Appointment.contact_id == c.id, Appointment.starts_at >= now - timedelta(hours=1))
            .order_by(Appointment.starts_at).limit(10)).all():
        out["upcoming_appointments"].append({
            "id": a.id, "title": a.title, "starts": local(a.starts_at),
            "ends": local(a.ends_at), "status": a.status, "location": a.location,
            "opportunity_id": a.opportunity_id})
    conv = db.scalar(select(Conversation).where(Conversation.contact_id == c.id))
    if conv is not None:
        events = db.scalars(select(ConversationEvent).where(
            ConversationEvent.conversation_id == conv.id,
            ConversationEvent.type.in_([EventType.SMS, EventType.EMAIL, EventType.CALL]))
            .order_by(ConversationEvent.occurred_at.desc(), ConversationEvent.id.desc())
            .limit(20)).all()
        for e in reversed(events):
            who = ("customer" if e.direction.value == "INBOUND"
                   else ("AI agent" if e.ai_agent_id else "company"))
            entry = {"when": local(e.occurred_at), "from": who, "type": e.type.value,
                     "text": (e.body or "")[:1000]}
            if e.type is EventType.CALL:
                entry.update({"call_status": e.call_status,
                              "duration_seconds": e.duration_seconds})
            out["recent_conversation"].append(entry)
    tasks = db.scalars(select(OpportunityTask).where(
        OpportunityTask.contact_id == c.id, OpportunityTask.completed_at.is_(None))
        .order_by(OpportunityTask.id).limit(20)).all()
    for t in tasks:
        out["open_tasks"].append({"id": t.id, "title": t.title, "due": local(t.due_at),
                                  "priority": t.priority, "opportunity_id": t.opportunity_id})
    out["calendars"] = [{"id": cal.id, "name": cal.name} for cal in
                        db.scalars(select(Calendar).order_by(Calendar.id)).all()]
    out["now"] = local(now)
    return out


def _address(r) -> str | None:
    parts = [p for p in (r.address_street, r.address_city, r.address_state,
                         r.address_postal_code) if p and p.strip()]
    return ", ".join(parts) or None


class SearchKnowledge(Action):
    def execute(self, ctx, prep):
        kb_ids = list(ctx.config.get("knowledge_base_ids") or [])
        hits = knowledge.RETRIEVER.search(ctx.db, kb_ids, prep.args["query"])
        if not knowledge.useful(hits):
            prep.data["gap"] = prep.args["query"]
            return {"results": [], "note": "Nothing in the knowledge base answers this. Do "
                    "not guess: say you will find out, or escalate."}
        return {"results": [{"title": h.title, "kind": h.kind, "text": h.text}
                            for h in hits]}


class ReportGap(Action):
    def prepare(self, ctx, args):
        return Prepared("Report a knowledge gap: “%s”" % args["question"][:120], args)

    def execute(self, ctx, prep):
        knowledge.record_gap(ctx.db, prep.args["question"], agent_id=ctx.agent_id,
                             run_id=ctx.run_id)
        return {"recorded": True}


# --- writes --------------------------------------------------------------

def _thread(ctx: Context, *, create: bool = True) -> AiAgentThread | None:
    t = ctx.db.scalar(select(AiAgentThread).where(
        AiAgentThread.agent_id == ctx.agent_id,
        AiAgentThread.contact_id == ctx.subject.contact_id))
    if t is None and create:
        t = AiAgentThread(agent_id=ctx.agent_id, contact_id=ctx.subject.contact_id,
                          messages_sent=0)
        ctx.db.add(t)
        ctx.db.flush()
    return t


class SendText(Action):
    def prepare(self, ctx, args):
        c = contact_of(ctx)
        if not c.phone:
            raise Refused("this customer has no phone number")
        if c.dnd:
            raise Refused("this customer is on Do Not Disturb")
        # Read, never created here: checking a Try-it text must write nothing.
        t = _thread(ctx, create=False)
        sent = t.messages_sent if t is not None else 0
        limit = int(ctx.config.get("max_messages") or 0)
        if sent >= limit:
            raise Refused("this agent has already sent %d message(s) on this conversation — "
                          "its maximum" % sent)
        # A person approving a suggestion has decided; the agent's sleep is about the
        # agent acting on its own.
        if t is not None and t.asleep_at is not None and ctx.approved_by is None:
            raise Refused("this agent is asleep on this conversation: %s"
                          % (t.asleep_reason or "a staff member replied"))
        return Prepared("Send text: “%s”" % args["body"][:160], args)

    def execute(self, ctx, prep):
        from .. import automations
        c = contact_of(ctx)
        # The SAME send path a staff member's composer uses: the same transport resolved
        # the same way (LoggingTransport today; owen-main refuses while SMS is dark), the
        # same DND / no-phone suppression. No new transport.
        ev, reason = automations.send_outbound(ctx.db, c, prep.args["body"],
                                               type_=EventType.SMS, ai_agent_id=ctx.agent_id)
        if ev is None:
            raise Refused(reason)
        t = _thread(ctx)
        t.messages_sent += 1
        t.updated_at = datetime.now(UTC)
        status = ev.delivery_status.value if ev.delivery_status else None
        return {"sent": ev.delivery_status not in (DeliveryStatus.REFUSED,
                                                   DeliveryStatus.FAILED),
                "delivery_status": status, "detail": ev.delivery_detail or reason,
                "event_id": ev.id}


class BookAppointment(Action):
    def prepare(self, ctx, args):
        db = ctx.db
        c = contact_of(ctx)
        starts = parse_when(args["starts_at"])
        minutes = args.get("duration_minutes", 60)
        cal_id = args.get("calendar_id")
        if cal_id is None:
            ids = db.scalars(select(Calendar.id)).all()
            if len(ids) != 1:
                raise Refused("name a calendar_id (see get_context: calendars)")
            cal_id = ids[0]
        if db.get(Calendar, cal_id) is None:
            raise Refused("calendar %s does not exist" % cal_id)
        opp_id = None
        if args.get("opportunity_id") is not None or ctx.subject.opportunity_id is not None:
            opp_id = opportunity_of(ctx, args).id
        clean = {**args, "calendar_id": cal_id, "duration_minutes": minutes,
                 "starts_at": starts.isoformat(), "opportunity_id": opp_id}
        return Prepared("Book appointment for %s on %s" % (c.name, local(starts)), clean,
                        opp_id)

    def execute(self, ctx, prep):
        from .. import main
        a = prep.args
        starts = datetime.fromisoformat(a["starts_at"])
        body = main.AppointmentCreate(
            title=(a.get("title") or "{{contact.name}}"),
            starts_at=starts, ends_at=starts + timedelta(minutes=a["duration_minutes"]),
            contact_id=ctx.subject.contact_id, calendar_id=a["calendar_id"],
            opportunity_id=a.get("opportunity_id"), description=a.get("description"),
            location_kind="calendar_default", status="booked")
        try:
            appt, automation = main.book_appointment(ctx.db, ctx.principal, body,
                                                     ai_agent_id=ctx.agent_id)
        except HTTPException as e:
            raise _http(e) from None
        return {"booked": True, "appointment_id": appt.id, "starts": local(appt.starts_at),
                "reminders": automation}


class RescheduleAppointment(Action):
    def prepare(self, ctx, args):
        a = appointment_of(ctx, args)
        if a.status == "cancelled":
            raise Refused("appointment %s is cancelled" % a.id)
        starts = parse_when(args["starts_at"])
        minutes = args.get("duration_minutes") or int(
            (auth.as_aware(a.ends_at) - auth.as_aware(a.starts_at)).total_seconds() // 60) or 60
        return Prepared("Reschedule “%s” to %s" % (a.title, local(starts)),
                        {**args, "starts_at": starts.isoformat(),
                         "duration_minutes": minutes}, a.opportunity_id)

    def execute(self, ctx, prep):
        from .. import main
        a = appointment_of(ctx, prep.args)
        starts = datetime.fromisoformat(prep.args["starts_at"])
        body = main.AppointmentPatch(
            starts_at=starts, ends_at=starts + timedelta(minutes=prep.args["duration_minutes"]))
        try:
            outcome = main.edit_appointment(ctx.db, ctx.principal, a, body,
                                            ai_agent_id=ctx.agent_id)
        except HTTPException as e:
            raise _http(e) from None
        return {"rescheduled": True, "appointment_id": a.id, "starts": local(a.starts_at),
                "reminders": outcome}


class CancelAppointment(Action):
    def prepare(self, ctx, args):
        a = appointment_of(ctx, args)
        if a.status == "cancelled":
            raise Refused("appointment %s is already cancelled" % a.id)
        return Prepared("Cancel “%s” on %s" % (a.title, local(a.starts_at)), args,
                        a.opportunity_id)

    def execute(self, ctx, prep):
        from .. import main
        a = appointment_of(ctx, prep.args)
        dropped = main.cancel_appointment_record(ctx.db, a, ai_agent_id=ctx.agent_id)
        return {"cancelled": True, "appointment_id": a.id, "reminders_withdrawn": dropped}


class FillChecklist(Action):
    def prepare(self, ctx, args):
        db = ctx.db
        o = opportunity_of(ctx, args)
        groups = custom_fields.checklist_ids(db)
        asked = {d.key: d for d in custom_fields.load_defs(db, include_archived=False)
                 if d.group_id in groups and custom_fields.shows_on(d, o.pipeline_id)
                 and not custom_fields.is_reserved(d.key)}
        allowed = set(asked) | {custom_fields.details_key(k) for k, d in asked.items()
                                if d.field_type == custom_fields.DROPDOWN}
        answers = args["answers"]
        if not answers:
            raise Refused("no answers were given")
        bad = sorted(k for k in answers if k not in allowed)
        if bad:
            raise Refused("not a Checklist question on this opportunity: %s" % ", ".join(bad))
        # Validated now, so a Suggest-mode suggestion is never one that cannot be approved.
        try:
            custom_fields.merge_answers(db, pipeline_id=o.pipeline_id,
                                       existing=o.custom_fields, incoming=answers)
        except HTTPException as e:
            raise _http(e) from None
        labels = ", ".join("%s: %s" % (asked[k].label if k in asked else k, v)
                           for k, v in answers.items())
        return Prepared("Fill checklist on “%s” — %s" % (o.title, labels[:300]),
                        {**args, "opportunity_id": o.id}, o.id)

    def execute(self, ctx, prep):
        from .. import main
        o = opportunity_of(ctx, prep.args)
        try:
            main.answer_questions(ctx.db, o, prep.args["answers"])
        except HTTPException as e:
            raise _http(e) from None
        return {"answered": sorted(prep.args["answers"]), "opportunity_id": o.id}


class AddNote(Action):
    def prepare(self, ctx, args):
        o = opportunity_of(ctx, args)
        return Prepared("Add note to “%s”: “%s”" % (o.title, args["body"][:160]),
                        {**args, "opportunity_id": o.id}, o.id)

    def execute(self, ctx, prep):
        from .. import opportunity_workspace
        n = opportunity_workspace.add_note(ctx.db, prep.args["opportunity_id"],
                                           prep.args["body"], created_by_id=None,
                                           ai_agent_id=ctx.agent_id)
        return {"note_id": n.id}


class CreateTask(Action):
    def prepare(self, ctx, args):
        o = opportunity_of(ctx, args)
        due = parse_when(args["due_at"]).isoformat() if args.get("due_at") else None
        return Prepared("Create task on “%s”: “%s”" % (o.title, args["title"][:160]),
                        {**args, "opportunity_id": o.id, "due_at": due}, o.id)

    def execute(self, ctx, prep):
        from .. import opportunity_workspace as ws
        o = opportunity_of(ctx, prep.args)
        due = datetime.fromisoformat(prep.args["due_at"]) if prep.args.get("due_at") else None
        t = ws.add_task(ctx.db, o, ws.TaskCreate(
            title=prep.args["title"], description=prep.args.get("description"), due_at=due,
            assigned_user_id=o.owner_id), created_by_id=None, ai_agent_id=ctx.agent_id)
        return {"task_id": t.id}


class MoveStage(Action):
    def prepare(self, ctx, args):
        o = opportunity_of(ctx, args)
        stage = ctx.db.get(Stage, args["stage_id"])
        if stage is None or stage.pipeline_id != o.pipeline_id:
            raise Refused("stage %s is not in this opportunity's pipeline" % args["stage_id"])
        if stage.id == o.stage_id:
            raise Refused("the opportunity is already in “%s”" % stage.name)
        return Prepared("Move “%s” to stage “%s”" % (o.title, stage.name),
                        {**args, "opportunity_id": o.id}, o.id)

    def execute(self, ctx, prep):
        from .. import main
        o = opportunity_of(ctx, prep.args)
        end = len(ctx.db.scalars(select(Opportunity.id).where(
            Opportunity.stage_id == prep.args["stage_id"], Opportunity.id != o.id)).all())
        try:
            result = main.move_to_stage(ctx.db, ctx.principal, o, prep.args["stage_id"], end)
        except HTTPException as e:
            raise _http(e) from None
        return {"moved": True, "stage_id": result["stage_id"],
                "customer_text": result["automation"]}


class Escalate(Action):
    def prepare(self, ctx, args):
        users = [u for u in (ctx.db.get(User, i) for i in
                             ctx.config.get("escalation_user_ids") or [])
                 if u is not None and u.is_active]
        if not users:
            raise Refused("this agent has nobody to escalate to")
        opp_id = None
        try:
            opp_id = opportunity_of(ctx, args).id
        except Refused:
            if args.get("opportunity_id") is not None:
                raise
        label = "EMERGENCY escalation" if args.get("emergency") else "Escalate to a human"
        return Prepared("%s: “%s”" % (label, args["reason"][:200]),
                        {**args, "opportunity_id": opp_id}, opp_id,
                        {"user_ids": [u.id for u in users]})

    def execute(self, ctx, prep):
        from .. import automations
        from .. import opportunity_workspace as ws
        from ..models import AiAlert, AiSettings
        from ..transport import get_transport
        db = ctx.db
        c = contact_of(ctx)
        reason = prep.args["reason"].strip()
        emergency = bool(prep.args.get("emergency"))
        o = db.get(Opportunity, prep.opportunity_id) if prep.opportunity_id else None
        tasks = []
        title = ("EMERGENCY: " if emergency else "URGENT: ") + "%s — %s" % (c.name, reason)
        for uid in prep.data["user_ids"]:
            task_id = None
            if o is not None:
                t = ws.add_task(db, o, ws.TaskCreate(
                    title=title[:255], description=reason, assigned_user_id=uid,
                    due_at=datetime.now(UTC)), created_by_id=None,
                    ai_agent_id=ctx.agent_id, priority="urgent")
                task_id = t.id
                tasks.append(t.id)
            db.add(AiAlert(user_id=uid, kind="escalation", urgent=True, title=title[:300],
                           body=reason, run_id=ctx.run_id, agent_id=ctx.agent_id,
                           contact_id=c.id, opportunity_id=o.id if o else None,
                           task_id=task_id))
        # The customer's thread says it happened, as a staff-only note.
        automations.send_outbound(db, c, "AI: %s escalated this conversation%s: %s" % (
            ctx.agent_name, " (EMERGENCY)" if emergency else "", reason),
            type_=EventType.NOTE, ai_agent_id=ctx.agent_id)
        on_call = None
        if emergency:
            settings = db.get(AiSettings, 1)
            phone = settings.on_call_phone if settings else None
            if phone:
                # The same transport seam every text uses — dark today, so this records
                # LOGGED_ONLY or owen-main's refusal rather than ringing anyone.
                ref = get_transport().send_sms(
                    to=phone, body="EMERGENCY (AI: %s) — %s: %s" % (
                        ctx.agent_name, c.name, reason)[:600], from_number="")
                on_call = {"to": format_phone(phone) or phone, "status": ref.status.value,
                           "detail": ref.detail}
            else:
                on_call = {"to": None, "status": "not_sent",
                           "detail": "no on-call phone is set in Settings → AI Connections"}
        ctx.escalated = True
        return {"escalated": True, "task_ids": tasks, "alerted_user_ids":
                prep.data["user_ids"], "emergency": emergency, "on_call_text": on_call}


class VoiceOnly(Action):
    def prepare(self, ctx, args):
        raise Refused("%s is a voice action (phase 3)" % self.name)


CATALOGUE: dict[str, Action] = {a.name: a for a in (
    GetContext("get_context", "Read context",
               "Read this customer's CRM record: contact details, their opportunities with "
               "stage, address and checklist answers, upcoming appointments, the recent "
               "conversation, open tasks, and the calendars. Call this first.",
               _obj({}), READ),
    SearchKnowledge("search_knowledge", "Search knowledge",
                    "Search the company's knowledge bases (services, prices, warranty, "
                    "policies). Use it before answering any question about the business.",
                    _obj({"query": _str("what to look up", 500)}, ("query",)), READ),
    SendText("send_text", "Send text",
             "Text this customer. Short, plain, friendly; one message at a time.",
             _obj({"body": _str("the text message", 1000)}, ("body",)), WRITE),
    BookAppointment("book_appointment", "Book appointment",
                    "Book a visit for this customer.",
                    _obj({"starts_at": _str("start, YYYY-MM-DDTHH:MM Eastern time", 40),
                          "duration_minutes": _int("length in minutes", 15, 480),
                          "calendar_id": _int("calendar id from get_context"),
                          "opportunity_id": _int("which of the customer's opportunities"),
                          "title": _str("title", 200),
                          "description": _str("what the visit is for", 2000)},
                         ("starts_at",)), WRITE),
    RescheduleAppointment("reschedule_appointment", "Reschedule appointment",
                          "Move one of this customer's appointments to a new time.",
                          _obj({"appointment_id": _int("appointment id from get_context"),
                                "starts_at": _str("new start, YYYY-MM-DDTHH:MM Eastern", 40),
                                "duration_minutes": _int("length in minutes", 15, 480)},
                               ("appointment_id", "starts_at")), WRITE),
    CancelAppointment("cancel_appointment", "Cancel appointment",
                      "Cancel one of this customer's appointments.",
                      _obj({"appointment_id": _int("appointment id from get_context"),
                            "reason": _str("why", 500)}, ("appointment_id",)), WRITE),
    FillChecklist("fill_checklist", "Fill checklist answers",
                  "Record answers to the opportunity's Checklist questions, by question key "
                  "(see get_context). Only what the customer actually said.",
                  _obj({"answers": {"type": "object",
                                    "description": "question key -> answer"},
                        "opportunity_id": _int("which opportunity")}, ("answers",)), WRITE),
    AddNote("add_note", "Add note", "Add an internal note to the opportunity for the team.",
            _obj({"body": _str("the note", 5000),
                  "opportunity_id": _int("which opportunity")}, ("body",)), WRITE),
    CreateTask("create_task", "Create task", "Create a to-do for the team on the opportunity.",
               _obj({"title": _str("task title", 200),
                     "description": _str("details", 2000),
                     "due_at": _str("due, YYYY-MM-DDTHH:MM Eastern", 40),
                     "opportunity_id": _int("which opportunity")}, ("title",)), WRITE),
    MoveStage("move_stage", "Move stage",
              "Move the opportunity to another stage of its pipeline (stage ids from "
              "get_context).",
              _obj({"stage_id": _int("the destination stage id"),
                    "opportunity_id": _int("which opportunity")}, ("stage_id",)), WRITE),
    Escalate("escalate", "Escalate to a human",
             "Hand this to a person now: an urgent task and an alert for the team. Use "
             "emergency only for active leaks, storm damage or safety risks.",
             _obj({"reason": _str("what the person needs to know", 1000),
                   "emergency": {"type": "boolean", "description": "an emergency"},
                   "opportunity_id": _int("which opportunity")}, ("reason",)), WRITE),
    ReportGap("report_knowledge_gap", "Report knowledge gap",
              "Record a question the knowledge base could not answer, so the owner can "
              "add the answer.",
              _obj({"question": _str("the customer's question", 1000)}, ("question",)),
              INTERNAL),
    VoiceOnly("transfer_call", "Transfer call", "Transfer the live call to a person.",
              _obj({"to": _str("who", 100)}, ("to",)), VOICE, ("voice",)),
    VoiceOnly("end_call", "End call", "End the live call.", _obj({}), VOICE, ("voice",)),
)}


def actions_for_channel(channel: str) -> list[str]:
    return [name for name, a in CATALOGUE.items() if channel in a.channels]


def catalogue_payload() -> list[dict]:
    return [{"name": a.name, "label": a.label, "description": a.description,
             "kind": a.kind, "channels": list(a.channels),
             "phase": 3 if a.kind == VOICE else 1} for a in CATALOGUE.values()]


def result_text(result: dict) -> str:
    return json.dumps(result, default=str, ensure_ascii=False)[:20000]


"""The Dispatch page's AI (phase 2, 2026-09-30): it EXPLAINS an item and ANSWERS questions.
It never writes to Zuper, never texts or calls anyone, and is OFF until an ADMIN picks an AI
connection and switches it on (Dispatch → Settings).

  explain(item)  -> what to change in Zuper and how, what to say on the call, and field
                    changes the calls support (each a `dispatch_suggestions` row a person
                    approves or marks wrong)
  chat(messages) -> an answer across every job, through READ tools plus `suggest_change`

The rules still find the work (rules.py); the AI only words it. Every request is a
`dispatch_ai_runs` row, and the daily cap (`daily_cap`, New York day) counts those. "Pause all
AI agents" (Settings → AI Connections) stops this too. The provider is the CRM's own
(app/ai/providers.py): the key stays encrypted at rest and never reaches a log.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..ai import engine as ai_engine
from ..ai import providers, vault
from ..models import (
    AiConnection,
    DispatchAiRun,
    DispatchItem,
    DispatchJob,
    DispatchSettings,
    DispatchSuggestion,
    ZuperStatusHistory,
)
from . import booking, comms, playbook
from . import config as c

log = logging.getLogger("dispatch.ai")

PER_PASS = 8                 # explanations per reader pass (the rest wait for the next pass)
EXPLAIN_KINDS = {"new_not_called", "after_inspection", "after_repair", "talked_not_updated",
                 "book_visit", "needs_date", "missed_call", "missed_text", "visit_unconfirmed",
                 "off_board", "no_photos"}
CHAT_STEPS = 6
# Reasoning models (gpt-6-luna) spend part of this on thinking before they answer: 900 cut a
# third of the live answers short (2026-10-01), so the room is generous; cost stays cents.
EXPLAIN_MAX_TOKENS = 4000
CHAT_MAX_TOKENS = 4000
ADDRESS = "Job address"
NOTE = "Note"


class Unavailable(Exception):
    """The AI may not run now — a sentence for the page."""


def settings(db: Session, *, for_update: bool = False) -> DispatchSettings:
    """The settings row. Reading never writes: with no row yet, the defaults are returned
    unsaved (a read that inserted would hold SQLite's write lock past its request)."""
    s = db.get(DispatchSettings, 1)
    if s is None:
        s = DispatchSettings(id=1, ai_enabled=False, model="gpt-6-luna", daily_cap=300)
        if for_update:
            db.add(s)
            db.flush()
    return s


def runs_today(db: Session, now: datetime) -> int:
    start = c.local(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(select(func.count()).select_from(DispatchAiRun).where(
        DispatchAiRun.created_at >= start.astimezone(UTC))) or 0


def provider_for(db: Session, now: datetime) -> tuple[providers.Provider, str]:
    s = settings(db)
    if not s.ai_enabled:
        raise Unavailable("The Dispatch AI is switched off (Dispatch → Settings).")
    if ai_engine.settings(db).paused:
        raise Unavailable("All AI agents are paused (Settings → AI Connections).")
    if runs_today(db, now) >= s.daily_cap:
        raise Unavailable("Today's AI limit (%d) is used up; it resets at midnight."
                          % s.daily_cap)
    conn = db.get(AiConnection, s.connection_id) if s.connection_id else None
    if conn is None:
        raise Unavailable("No AI connection is chosen for Dispatch.")
    try:
        key = vault.decrypt(conn.api_key_encrypted)
    except vault.SecretsUnavailable as e:
        raise Unavailable(str(e)) from None
    return providers.build(conn.provider, key, conn.base_url), s.model


def _record(db: Session, kind: str, model: str, turn: providers.Turn | None, *,
            item_id: int | None = None, user_id: int | None = None,
            error: str | None = None) -> None:
    db.add(DispatchAiRun(kind=kind, model=model, item_id=item_id, user_id=user_id,
                         input_tokens=turn.usage.input_tokens if turn else 0,
                         output_tokens=turn.usage.output_tokens if turn else 0,
                         ok=error is None, error=error))
    # The sessions do not autoflush (app/db.py): without this, the cap's count in the next
    # chat round or the next item would not see this run (review 2026-10-01).
    db.flush()


# ------------------------------------------------------------------------------- context

def job_context(db: Session, j: DispatchJob, talk: dict[str, list[comms.Comm]],
                now: datetime) -> dict:
    moves = db.scalars(select(ZuperStatusHistory).where(ZuperStatusHistory.job_uid == j.job_uid)
                       .order_by(ZuperStatusHistory.changed_at.desc()).limit(8)).all()
    mine = sorted((x for p in j.phones or [] for x in talk.get(p, [])),
                  key=lambda x: x.at)[-10:]
    last_talks = [x for x in mine if x.talked][-2:]
    return {
        "job_number": j.job_number, "board": j.board, "stage": j.status,
        "in_stage_since": _local(j.status_since), "stage_set_by": j.status_by,
        "customer": j.customer_name, "address": ", ".join(x for x in (j.address, j.city) if x),
        "visit": _local(j.scheduled_start), "assigned": j.assigned or [],
        "technician_field": j.technician, "zuper_notes": j.notes_count,
        "last_zuper_note": _local(j.last_note_at), "pictures_today": j.photos_today,
        "job_fields": dict(j.fields or {}),
        "stage_history_newest_first": [{"stage": m.status_name, "board": m.category_name,
                                        "at": _local(m.changed_at), "by": m.done_by_name}
                                       for m in moves],
        "calls_and_texts_oldest_first": [{
            "at": _local(x.at), "kind": x.kind, "who": "us" if x.out else "customer",
            "line": x.source, "seconds": x.seconds, "answered": x.talked,
            "missed": x.missed, "summary_or_text": x.summary[:600]} for x in mine],
        "transcripts_of_last_conversations": [
            {"at": _local(x.at), "transcript": x.transcript[:3000]} for x in last_talks
            if x.transcript],
        "now": _local(now),
    }


def _local(dt) -> str | None:
    dt = c.aware(dt)
    return c.local(dt).strftime("%a %b %d %Y, %I:%M %p") if dt else None


EXPLAIN_SYSTEM = """You are the dispatch assistant of Dream Team Roofing (Florida). The office \
works its jobs in Zuper. A fixed rule has flagged ONE item for the office; your job is to tell a \
person exactly what to do about it — in Zuper and on the phone — using only the facts given.

{rules}
The boards and what each stage means:
{boards}

Answer with ONE JSON object and nothing else:
{{"zuper_steps": ["short imperative step", ...],   // Zuper changes in order; [] if none
 "say": "what to tell the customer on the call, 1-3 sentences, or empty",
 "field_changes": [{{"field": "<exact label from job_fields, or 'Job address', or 'Note'>",
                    "current": "<value now>", "proposed": "<new value>",
                    "evidence": "<the call/text that supports it, with its date>"}}],
 "note": "anything the office should know, or empty",
 "confidence": "high" | "medium" | "low"}}

Rules for field_changes: only propose a change a call or text clearly supports (a confirmed \
address that differs from the job's, a number or answer the customer gave). Never guess. Use \
the exact field label. Prefer [] to a weak suggestion. Use plain English; no customer data that \
is not in the facts."""


def _extract_json(text: str) -> dict | None:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        out = json.loads(t[start:end + 1])
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def _clean_changes(raw, j: DispatchJob) -> list[dict]:
    labels = set((j.fields or {}).keys())
    out = []
    for ch in raw if isinstance(raw, list) else []:
        if not isinstance(ch, dict):
            continue
        name = str(ch.get("field") or "").strip()
        proposed = str(ch.get("proposed") or "").strip()
        if not name or not proposed:
            continue
        if name == ADDRESS:
            kind, current = "address", ", ".join(x for x in (j.address, j.city) if x)
        elif name == NOTE:
            kind, current = "note", None
        elif name in labels:
            kind, current = "field", (j.fields or {}).get(name)
        else:
            continue                         # never a field the job does not have
        if current is not None and current.strip().lower() == proposed.lower():
            continue
        out.append({"kind": kind, "field": name, "current": current, "proposed": proposed[:1000],
                    "evidence": str(ch.get("evidence") or "")[:1000] or None})
    return out


def add_suggestions(db: Session, j: DispatchJob, changes: list[dict], *, item_id: int | None,
                    source: str) -> list[DispatchSuggestion]:
    made = []
    for ch in changes:
        exists = db.scalar(select(DispatchSuggestion.id).where(
            DispatchSuggestion.job_uid == j.job_uid, DispatchSuggestion.field == ch["field"],
            DispatchSuggestion.proposed == ch["proposed"]))
        if exists:
            continue
        s = DispatchSuggestion(item_id=item_id, job_uid=j.job_uid, job_number=j.job_number,
                               board=j.board, source=source, **ch)
        db.add(s)
        made.append(s)
    db.flush()
    return made


def explain(db: Session, item: DispatchItem, now: datetime,
            talk: dict[str, list[comms.Comm]] | None = None) -> bool:
    """Write the AI's explanation onto one item. True when the provider answered."""
    prov, model = provider_for(db, now)
    j = db.scalar(select(DispatchJob).where(DispatchJob.job_uid == item.job_uid)) \
        if item.job_uid else None
    talk = talk if talk is not None else comms.load(db, now - timedelta(days=c.COMMS_DAYS))
    facts = {"item": {"title": item.title, "why": item.why, "rule_says_to_do": item.todo,
                      "due": _local(item.due_at), "evidence": item.evidence or {}},
             "job": job_context(db, j, talk, now) if j else None}
    if item.phone and not j:
        facts["calls_and_texts"] = [{"at": _local(x.at), "kind": x.kind, "summary_or_text":
                                     x.summary[:600], "who": "us" if x.out else "caller"}
                                    for x in talk.get(item.phone, [])[-8:]]
    system = EXPLAIN_SYSTEM.format(rules=playbook.HOUSE_RULES, boards=playbook.board_text())
    try:
        turn = prov.complete(system=system, messages=[{"role": "user", "content": json.dumps(
            facts, default=str)}], tools=[], model=model, max_tokens=EXPLAIN_MAX_TOKENS)
    except providers.ProviderError as e:
        _record(db, "explain", model, None, item_id=item.id, error=str(e))
        item.ai_error, item.ai_at = str(e)[:500], now
        return False
    _record(db, "explain", model, turn, item_id=item.id)
    data = _extract_json(turn.text)
    if data is None:
        item.ai_error, item.ai_at = "The AI's answer could not be read.", now
        return True
    raw_steps = data.get("zuper_steps")
    if isinstance(raw_steps, str):
        raw_steps = [raw_steps]           # one step sent as text, not a list of letters
    steps = [str(x)[:300] for x in raw_steps if str(x).strip()][:8] \
        if isinstance(raw_steps, list) else []
    item.ai = {"zuper_steps": steps, "say": str(data.get("say") or "")[:800],
               "note": str(data.get("note") or "")[:800],
               "confidence": data.get("confidence") if data.get("confidence") in (
                   "high", "medium", "low") else None, "model": turn.model or model}
    item.ai_error, item.ai_at = None, now
    if j:
        add_suggestions(db, j, _clean_changes(data.get("field_changes"), j), item_id=item.id,
                        source="explain")
    return True


def explain_pending(db: Session, now: datetime, limit: int = PER_PASS) -> dict:
    """The pass's share: open items with no explanation yet, urgent first. Stops at the first
    refusal (switched off, paused, cap) — that is not an error, it is the setting."""
    counts = {"explained": 0}
    rows = db.scalars(select(DispatchItem).where(
        DispatchItem.state == "open", DispatchItem.ai.is_(None), DispatchItem.ai_error.is_(None),
        DispatchItem.kind.in_(EXPLAIN_KINDS),
        or_(DispatchItem.job_uid.is_not(None), DispatchItem.kind.like("missed_%")))
        .order_by(DispatchItem.urgent.desc(), DispatchItem.id).limit(limit)).all()
    if not rows:
        return counts
    talk = comms.load(db, now - timedelta(days=c.COMMS_DAYS))
    for item in rows:
        try:
            if explain(db, item, now, talk):
                counts["explained"] += 1
        except Unavailable as e:
            counts["skipped"] = str(e)
            break
        db.commit()
    return counts


# ---------------------------------------------------------------------------------- chat

CHAT_SYSTEM = """You are the dispatch assistant of Dream Team Roofing (Florida), talking with \
the office staff inside their CRM. Answer questions about jobs, calls and what to do next, using \
the tools to look things up — never from memory. Be brief and concrete: job numbers, names, \
dates. You cannot change Zuper or contact anyone; when something should change in Zuper, say \
exactly what and how, and you may record it with suggest_change so the office can approve it.

{rules}
The boards:
{boards}
Today is {today}."""

TOOLS = [
    providers.ToolSpec("list_items", "The open Dispatch items (what the office must do), "
                       "optionally for one queue: new, after_visit, missed, book, zuper, visits, "
                       "stale.", {"type": "object", "properties": {
                           "queue": {"type": "string"}}, "additionalProperties": False}),
    providers.ToolSpec("find_jobs", "Jobs matching a name, job number, city or text, and/or a "
                       "board / stage. Open jobs unless include_closed.",
                       {"type": "object", "properties": {
                           "text": {"type": "string"}, "board": {"type": "string"},
                           "stage": {"type": "string"},
                           "include_closed": {"type": "boolean"}},
                        "additionalProperties": False}),
    providers.ToolSpec("get_job", "Everything about one job: stage history, fields, visit, "
                       "calls and texts with summaries.", {"type": "object", "properties": {
                           "job_number": {"type": "string"}}, "required": ["job_number"],
                           "additionalProperties": False}),
    providers.ToolSpec("find_slots", "Three visit slots for a job, closest to what is already "
                       "booked. kind: inspection or repair (default from the job).",
                       {"type": "object", "properties": {
                           "job_number": {"type": "string"}, "kind": {"type": "string"},
                           "technician": {"type": "string"}},
                        "required": ["job_number"], "additionalProperties": False}),
    providers.ToolSpec("suggest_change", "Record a change Zuper needs on a job, for the office to "
                       "approve (it is NOT applied). field: an exact job field label, 'Job "
                       "address' or 'Note'.", {"type": "object", "properties": {
                           "job_number": {"type": "string"}, "field": {"type": "string"},
                           "proposed": {"type": "string"}, "evidence": {"type": "string"}},
                        "required": ["job_number", "field", "proposed", "evidence"],
                        "additionalProperties": False}),
]


def _visible_job(db: Session, number: str, hidden: set[str]) -> DispatchJob | None:
    j = db.scalar(select(DispatchJob).where(DispatchJob.job_number == str(number).lstrip("#")))
    return None if j is None or (j.board and j.board in hidden) else j


def visits_for_booking(db: Session, now: datetime,
                       hidden: set[str] | None = None) -> list[booking.Visit]:
    """Every booked visit. A visit on a board hidden from the reader still blocks the time
    (the technician is busy) but is never named (review 2026-10-01)."""
    out = []
    horizon = now + timedelta(days=booking.DAYS_AHEAD + 2)
    for v in db.scalars(select(DispatchJob).where(
            DispatchJob.scheduled_start >= now - timedelta(days=1),
            DispatchJob.scheduled_start <= horizon)):
        start, end = c.aware(v.scheduled_start), c.aware(v.scheduled_end)
        if not start or not end or end - start > timedelta(hours=12) or not v.is_open:
            continue
        label = None if v.board and v.board in (hidden or set()) else \
            "#%s %s" % (v.job_number, v.city or "")
        for tech in set(v.assigned or []) | ({v.technician} if v.technician else set()):
            out.append(booking.Visit(tech, start, end, v.lat, v.lng, label))
    return out


def slots_for(db: Session, j: DispatchJob, now: datetime, kind: str | None = None,
              tech: str | None = None, hidden: set[str] | None = None) -> list[dict]:
    from .rules import visit_kind
    kind = kind if kind in booking.SLOT_MINUTES else (
        "repair" if j.board == c.INSPECTION_BOARD and j.status == "AHS Approved"
        else visit_kind(j))
    techs = settings(db).technicians or booking.DEFAULT_TECHNICIANS
    return [s.as_dict() for s in booking.slots(
        lat=j.lat, lng=j.lng, kind=kind, visits=visits_for_booking(db, now, hidden), now=now,
        technicians=techs, tech=tech)]


def run_tool(db: Session, name: str, args: dict, now: datetime, hidden: set[str],
             made: list[DispatchSuggestion]) -> str:
    if name == "list_items":
        q = select(DispatchItem).where(DispatchItem.state == "open")
        if args.get("queue"):
            q = q.where(DispatchItem.queue == args["queue"])
        rows = [r for r in db.scalars(q.order_by(DispatchItem.urgent.desc()).limit(40))
                if not (r.board and r.board in hidden)]
        return json.dumps([{"title": r.title, "queue": r.queue, "urgent": r.urgent,
                            "due": _local(r.due_at), "todo": r.todo} for r in rows])
    if name == "find_jobs":
        q = select(DispatchJob)
        if not args.get("include_closed"):
            q = q.where(DispatchJob.is_open.is_(True))
        if args.get("board"):
            q = q.where(DispatchJob.board == args["board"])
        if args.get("stage"):
            q = q.where(DispatchJob.status == args["stage"])
        text = (args.get("text") or "").strip().lower().lstrip("#")
        rows = [j for j in db.scalars(q) if not (j.board and j.board in hidden) and (
            not text or text in " ".join(str(x or "") for x in (
                j.job_number, j.customer_name, j.city, j.address, j.title)).lower())]
        return json.dumps([{"job_number": j.job_number, "customer": j.customer_name,
                            "board": j.board, "stage": j.status, "city": j.city,
                            "visit": _local(j.scheduled_start)} for j in rows[:30]])
    j = _visible_job(db, args.get("job_number", ""), hidden)
    if j is None:
        return json.dumps({"error": "no such job"})
    if name == "get_job":
        return json.dumps(job_context(db, j, comms.load(db, now - timedelta(days=c.COMMS_DAYS)),
                                      now), default=str)
    if name == "find_slots":
        return json.dumps(slots_for(db, j, now, args.get("kind"), args.get("technician"),
                                    hidden))
    if name == "suggest_change":
        changes = _clean_changes([args], j)
        if not changes:
            return json.dumps({"error": "not recorded: the field must be one of the job's own "
                                        "field labels, 'Job address' or 'Note'"})
        made.extend(add_suggestions(db, j, changes, item_id=None, source="chat"))
        return json.dumps({"recorded": True, "note": "the office will approve or reject it"})
    return json.dumps({"error": "unknown tool"})


def chat(db: Session, messages: list[dict], *, user_id: int, hidden: set[str],
         now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    prov, model = provider_for(db, now)
    system = CHAT_SYSTEM.format(rules=playbook.HOUSE_RULES, boards=playbook.board_text(),
                                today=_local(now))
    convo = [{"role": m["role"], "content": m["content"]} if m["role"] == "user" else
             {"role": "assistant", "text": m["content"]} for m in messages]
    made: list[DispatchSuggestion] = []
    for step in range(CHAT_STEPS):
        if step:
            # Every round is a request: the cap and "Pause all AI agents" are asked again
            # each time, not only before the first (review 2026-10-01).
            try:
                prov, model = provider_for(db, now)
            except Unavailable as e:
                db.commit()
                return {"reply": "I stopped before finishing: %s" % e,
                        "suggestions": [s.id for s in made]}
        try:
            turn = prov.complete(system=system, messages=convo, tools=TOOLS, model=model,
                                 max_tokens=CHAT_MAX_TOKENS)
        except providers.ProviderError as e:
            _record(db, "chat", model, None, user_id=user_id, error=str(e))
            db.commit()
            raise Unavailable(str(e)) from None
        _record(db, "chat", model, turn, user_id=user_id)
        if not turn.tool_calls:
            db.commit()
            return {"reply": turn.text, "suggestions": [s.id for s in made]}
        convo.append({"role": "assistant", "text": turn.text, "tool_calls": turn.tool_calls,
                      "raw": turn.raw})
        results = []
        for call in turn.tool_calls:
            out = run_tool(db, call.name, call.arguments or {}, now, hidden, made) \
                if call.arguments is not None else json.dumps({"error": "bad arguments"})
            results.append({"id": call.id, "name": call.name, "content": out,
                            "is_error": False})
        convo.append({"role": "tool_results", "results": results})
    db.commit()
    return {"reply": "I could not finish that in a few steps — please ask more narrowly.",
            "suggestions": [s.id for s in made]}

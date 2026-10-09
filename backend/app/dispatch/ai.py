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
from . import booking, comms, lookups, playbook
from . import config as c

log = logging.getLogger("dispatch.ai")

PER_PASS = 8                 # explanations per reader pass (the rest wait for the next pass)
EXPLAIN_KINDS = {"new_not_called", "after_inspection", "after_repair", "talked_not_updated",
                 "book_visit", "needs_date", "missed_call", "missed_text", "visit_unconfirmed",
                 "off_board", "no_photos"}
# A question about a day or a week takes many look-ups: the schedule, the file, then the calls of
# every customer whose visit is not clearly confirmed (the 2026-10-08 session took 15-30).
CHAT_STEPS = 30
# What the look-ups of ONE answer may add to the conversation before it must answer: a
# model's context is finite, and a provider that is not Claude may have far less than 1M.
CHAT_READ_BUDGET = 300_000
TOOL_RESULT_MAX = 40_000
# Reasoning models (gpt-6-luna) spend part of this on thinking before they answer: 900 cut a
# third of the live answers short (2026-10-01), so the room is generous; cost stays cents.
EXPLAIN_MAX_TOKENS = 4000
# deepseek-v4-pro thinks by default and spent ALL of 8,000 on thinking in its final answer
# (live 2026-10-09: 11 look-ups, then "(no answer)"). 16,000 a request; an empty answer cut by
# the limit is asked once more with ANSWER_RETRY_TOKENS and "write the answer now".
CHAT_MAX_TOKENS = 16000
ANSWER_RETRY_TOKENS = 32000
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
    last_talks = [x for x in mine if x.talked][-4:]
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
            {"at": _local(x.at), "transcript": x.transcript[:4000]} for x in last_talks
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

CHAT_SYSTEM = """You are the dispatch assistant of Dream Team Roofing (South Florida), talking \
with the office staff inside their CRM. You check Zuper, every call and text (Quo and Zuper \
Connect), Zuper's activity log, the technicians' notes and the office's spreadsheet, and tell \
the person what is right, what is wrong and exactly what to do, like a careful dispatcher who \
has read everything before answering.

Today is {today} (New York). "Today", "tomorrow" and "this week" (to Saturday) are New York \
dates.

How to work
- Look everything up; never answer from memory. Use as many look-ups as the question needs. \
A question about a day or a week usually needs day_schedule, compare_schedule when a file is \
attached, then search_comms for each customer whose visit is not clearly confirmed.
- A visit is CONFIRMED only when a call or text shows the customer agreeing to that day (and \
time). A spreadsheet saying "confirmed" is not proof. If a transcript has only our side, say \
the customer's answer is not on record.
- The newest evidence wins: a later "can I reschedule?" beats an earlier booking. When the \
file, Zuper and the calls disagree, say which says what, with the time of each.
- Read a cut-off conversation in full (search_comms with full=true) before deciding.
- Zuper's activity log: lines from OUR SCRIPT appear under the API key owner's name (Owen); \
never say that person did them. "field app" is the technician in the field; "office" is the \
office. Name every deletion of a job or customer: the owner's rule is never to delete.
- Zuper has no live location. "Where is X" is reconstructed with tech_day: give the last place \
with evidence and its time, and say it is an inference.
- Remaking the schedule: call plan_schedule. What each customer said about when they can \
have the visit is already read from all their calls and texts and applied (`limits`); if a \
proposed visit's last calls and texts show something newer or missed (a day, a time, "away \
next week", a technician), call plan_schedule again with those limits and their evidence. \
Then give the summary (visits placed, not placed, \
driving), a table per day and technician, the tentative ones (call first) and the links. \
Nothing is booked: the office books each visit in Zuper.
- You cannot change Zuper or contact anyone. When Zuper needs a change, say exactly which job, \
which stage / time / technician / field, to what, and the evidence. You may record a field, \
address or note change with suggest_change for the office to approve.

How to answer
- First the direct answer in one or two sentences (yes / no, and how many).
- Then a short table per day or per group: job #, customer, what Zuper shows, what the calls \
show, what to do. Keep "confirmed by a call / text" apart from "not confirmed, call first".
- Tables: a Markdown table, ONE ROW PER JOB OR VISIT, at most 6 short columns, every row on \
its own line. Never put several jobs in one cell, never a list inside a cell. A schedule: \
one table per day with Time | Tech | Job | Customer | City | Confirmed?. More than about 15 \
visits: give the per-day totals, the tentative ones (call first) and the calendar and Excel \
links, and show the full table only for the day asked.
- Times as 7:30 AM, dates as Thu 10/08, jobs as #724. Short and concrete, no filler. Answer \
in the language the person writes in.

{rules}
The boards:
{boards}"""

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
    providers.ToolSpec("plan_schedule", "Remake the schedule: a DRAFT plan for EVERY job that "
                       "needs a visit (new inspections, AHS-approved repairs, reschedules, "
                       "visits that did not happen or have no time), fitted Monday to Saturday "
                       "for the technicians around what is booked, as many jobs as possible "
                       "with the least driving. Returns the plan, each proposed visit's last "
                       "calls and texts, and links to an Excel and a calendar. Customers' "
                       "limits from the calls go in `limits`; call it again with them. "
                       "mode: most_jobs (default) or oldest_first; kind: all, inspection or "
                       "repair; days: business days ahead (6 = a week, 12 = two); boards: ahs "
                       "(default) or all; include_unreached: place customers nobody reached "
                       "yet as tentative (default true).",
                       {"type": "object", "properties": {
                           "days": {"type": "integer"}, "kind": {"type": "string"},
                           "mode": {"type": "string"}, "boards": {"type": "string"},
                           "include_unreached": {"type": "boolean"},
                           "limits": {"type": "array", "items": {
                               "type": "object", "properties": {
                                   "job_number": {"type": "string"},
                                   "not_before": {"type": "string"},
                                   "blocked": {"type": "array", "items": {
                                       "type": "object", "properties": {
                                           "from": {"type": "string"},
                                           "to": {"type": "string"}},
                                       "required": ["from", "to"],
                                       "additionalProperties": False}},
                                   "not_days": {"type": "array", "items": {"type": "string"}},
                                   "days": {"type": "array", "items": {"type": "string"}},
                                   "after": {"type": "string"},
                                   "before": {"type": "string"},
                                   "technician": {"type": "string"},
                                   "evidence": {"type": "string"}},
                               "required": ["job_number"], "additionalProperties": False}}},
                        "additionalProperties": False}),
    providers.ToolSpec("read_file", "Read rows of a spreadsheet attached to this chat, in order "
                       "(top to bottom). Page with offset; up to 100 rows a call.",
                       {"type": "object", "properties": {
                           "file_id": {"type": "integer"}, "sheet": {"type": "string"},
                           "offset": {"type": "integer"}, "limit": {"type": "integer"}},
                        "required": ["file_id"], "additionalProperties": False}),
    providers.ToolSpec("compare_file", "Compare EVERY row of an attached spreadsheet with "
                       "Zuper: the matching job, its stage now, visit, technician and every "
                       "difference; rows not in Zuper. Also makes a downloadable comparison "
                       "workbook for the person. Use it for any 'compare with Zuper' request.",
                       {"type": "object", "properties": {"file_id": {"type": "integer"}},
                        "required": ["file_id"], "additionalProperties": False}),
    providers.ToolSpec("day_schedule", "Every visit with a time in Zuper for a day or a range "
                       "(up to 14 days), per technician, in time order, each with what looks "
                       "wrong (closed job, stage that is not a booked visit, nobody assigned, "
                       "Technician field disagreeing). Dates: today, tomorrow, 2026-10-08, 10/08.",
                       {"type": "object", "properties": {
                           "date_from": {"type": "string"}, "date_to": {"type": "string"},
                           "technician": {"type": "string"}},
                        "required": ["date_from"], "additionalProperties": False}),
    providers.ToolSpec("compare_schedule", "Compare the DATED visits of an attached "
                       "spreadsheet (its schedule / booked-for days) with Zuper for a day or a "
                       "range: for each visit, the Zuper job, its day, time, technician and "
                       "stage, every difference, and the customer's recent calls and texts; "
                       "plus Zuper visits the file does not have. Use it for 'what needs "
                       "updating in Zuper this week'.", {"type": "object", "properties": {
                           "file_id": {"type": "integer"}, "date_from": {"type": "string"},
                           "date_to": {"type": "string"}},
                           "required": ["file_id", "date_from"],
                           "additionalProperties": False}),
    providers.ToolSpec("search_comms", "Calls and texts (Quo, the CRM line and Zuper Connect) "
                       "by phone (ANY number, e.g. a relative's), job number, customer name "
                       "and/or words in the text or transcript, newest last. full=true returns "
                       "whole transcripts; use it when a conversation is cut off.",
                       {"type": "object", "properties": {
                           "phone": {"type": "string"}, "job_number": {"type": "string"},
                           "name": {"type": "string"}, "text": {"type": "string"},
                           "days": {"type": "integer"}, "full": {"type": "boolean"}},
                        "additionalProperties": False}),
    providers.ToolSpec("activity_log", "Zuper's activity log: who moved, rescheduled, assigned, "
                       "noted or DELETED what, when, and from where (office, field app). For a "
                       "day (default today) or the last N hours; filter by job number, person "
                       "or words. Our own scripts' lines are left out unless include_scripts.",
                       {"type": "object", "properties": {
                           "day": {"type": "string"}, "hours": {"type": "integer"},
                           "job_number": {"type": "string"}, "person": {"type": "string"},
                           "text": {"type": "string"}, "include_scripts": {"type": "boolean"}},
                        "additionalProperties": False}),
    providers.ToolSpec("job_notes", "The text of a job's notes in Zuper (a technician's on-site "
                       "dictation, the office's notes), oldest first.",
                       {"type": "object", "properties": {"job_number": {"type": "string"}},
                        "required": ["job_number"], "additionalProperties": False}),
    providers.ToolSpec("tech_day", "One technician's day (default today): booked visits and a "
                       "timeline of their stage moves, notes, own calls and field-app actions, "
                       "with the last place there is evidence for. Zuper has no GPS.",
                       {"type": "object", "properties": {
                           "technician": {"type": "string"}, "day": {"type": "string"}},
                        "required": ["technician"], "additionalProperties": False}),
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
             made: list[DispatchSuggestion], files: dict | None = None,
             downloads: list | None = None, user_id: int | None = None) -> str:
    if name in ("read_file", "compare_file"):
        f = (files or {}).get(int(args.get("file_id") or 0))
        if f is None:
            return json.dumps({"error": "no such file in this chat"})
        if name == "read_file":
            sheet = next((s for s in f.sheets if s["name"] == args.get("sheet")),
                         f.sheets[0] if f.sheets else None)
            if sheet is None:
                return json.dumps({"error": "the file has no rows"})
            off = max(0, int(args.get("offset") or 0))
            lim = max(1, min(int(args.get("limit") or 50), 100))
            return json.dumps({"sheet": sheet["name"], "sheets": [s["name"] for s in f.sheets],
                               "total_rows": len(sheet["rows"]), "offset": off,
                               "rows": sheet["rows"][off:off + lim]}, default=str)
        from . import compare
        result = compare.compare(f.sheets, db.scalars(select(DispatchJob)).all(),
                                 hidden=hidden, now=now)
        if downloads is not None:
            link = {"label": "Comparison of %s with Zuper (Excel)" % f.filename,
                    "url": "/api/dispatch/chats/files/%d/comparison.xlsx" % f.id}
            if link not in downloads:
                downloads.append(link)
        notable = [r for r in result["rows"] if r["result"] != "same"]
        return json.dumps({"counts": result["counts"], "columns_used": result["columns_used"],
                           "rows_needing_attention": notable[:80],
                           "more": max(0, len(notable) - 80),
                           "note": "The full row-by-row comparison is in the download."},
                          default=str)
    if name == "compare_schedule":
        f = (files or {}).get(int(args.get("file_id") or 0))
        if f is None:
            return json.dumps({"error": "no such file in this chat"})
        return json.dumps(lookups.compare_schedule(db, now, hidden, f.sheets,
                                                   date_from=args.get("date_from"),
                                                   date_to=args.get("date_to")), default=str)
    if name == "day_schedule":
        return json.dumps(lookups.day_schedule(db, now, hidden, date_from=args.get("date_from"),
                                               date_to=args.get("date_to"),
                                               technician=args.get("technician")), default=str)
    if name == "search_comms":
        return json.dumps(lookups.search_comms(
            db, now, hidden, phone=args.get("phone"), text=args.get("text"),
            name=args.get("name"), job_number=args.get("job_number"),
            days=args.get("days") or 14, full=bool(args.get("full"))), default=str)
    if name == "activity_log":
        return json.dumps(lookups.activity_log(
            db, now, hidden, day=args.get("day"), hours=args.get("hours"),
            job_number=args.get("job_number"), person=args.get("person"),
            text=args.get("text"), include_scripts=bool(args.get("include_scripts"))),
            default=str)
    if name == "job_notes":
        return json.dumps(lookups.job_notes(db, hidden, args.get("job_number", "")), default=str)
    if name == "tech_day":
        return json.dumps(lookups.tech_day(db, now, hidden, technician=args.get("technician", ""),
                                           day=args.get("day")), default=str)
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
    if name == "plan_schedule":
        from . import scheduling
        kind = str(args.get("kind") or "all")
        out = scheduling.make_plan(
            db, now, days=int(args.get("days") or 6),
            kind=kind if kind in ("all", "inspection", "repair") else "all",
            mode=str(args.get("mode") or "most_jobs"), boards=str(args.get("boards") or "ahs"),
            include_unreached=args.get("include_unreached") is not False,
            limits=args.get("limits") if isinstance(args.get("limits"), list) else None,
            hidden=hidden, user_id=user_id, source="chat")
        if downloads is not None and out.get("id"):
            for link in ({"label": "Schedule plan #%d (Excel)" % out["id"],
                          "url": "/api/dispatch/plans/%d/schedule.xlsx" % out["id"]},
                         {"label": "Open plan #%d on the calendar" % out["id"],
                          "url": "/dispatch?tab=book&plan=%d" % out["id"]}):
                downloads.append(link)
        return json.dumps(scheduling.for_model(db, out, now), default=str)
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


STEP_RESULT_MAX = 4000


def chat(db: Session, messages: list[dict], *, user_id: int | None, hidden: set[str],
         now: datetime | None = None, files: dict | None = None) -> dict:
    """Answer the last user message of `messages` (oldest first, user / assistant).

    Never raises for the AI being unavailable: the result says so (`error`), so a saved
    conversation keeps the attempt. Returns {reply, error, suggestions (ids), steps (every
    tool the assistant used: tool, args, result), model, input_tokens, output_tokens}."""
    now = now or datetime.now(UTC)
    made: list[DispatchSuggestion] = []
    steps: list[dict] = []
    downloads: list[dict] = []
    usage = {"input_tokens": 0, "output_tokens": 0}

    def result(reply: str, *, error: bool = False, model: str | None = None) -> dict:
        db.commit()
        return {"reply": reply, "error": error, "suggestions": [x.id for x in made],
                "steps": steps, "model": model, "downloads": downloads, **usage}

    try:
        prov, model = provider_for(db, now)
    except Unavailable as e:
        return result(str(e), error=True)
    system = CHAT_SYSTEM.format(rules=playbook.HOUSE_RULES, boards=playbook.board_text(),
                                today=_local(now))
    convo = [{"role": m["role"], "content": m["content"]} if m["role"] == "user" else
             {"role": "assistant", "text": m["content"]} for m in messages]
    read = 0
    for step in range(CHAT_STEPS):
        if step:
            # Every round is a request: the cap and "Pause all AI agents" are asked again
            # each time, not only before the first (review 2026-10-01).
            try:
                prov, model = provider_for(db, now)
            except Unavailable as e:
                return result("I stopped before finishing: %s" % e, error=True, model=model)
        try:
            turn = prov.complete(system=system, messages=convo, tools=TOOLS, model=model,
                                 max_tokens=CHAT_MAX_TOKENS)
        except providers.ProviderError as e:
            _record(db, "chat", model, None, user_id=user_id, error=str(e))
            return result(str(e), error=True, model=model)
        _record(db, "chat", model, turn, user_id=user_id)
        usage["input_tokens"] += turn.usage.input_tokens
        usage["output_tokens"] += turn.usage.output_tokens
        model = turn.model or model
        if not turn.tool_calls and not turn.text.strip():
            # No answer and no look-up: a thinking model spent its whole room thinking. Ask
            # once more, with more room, for the answer itself — never show an empty reply.
            again = _answer_now(db, prov, system, convo, model, user_id, usage)
            if again:
                return result(again, model=model)
            return result("I ran out of room while working this out and could not write the "
                          "answer. Please ask again, or ask about fewer jobs at a time.",
                          error=True, model=model)
        if not turn.tool_calls:
            return result(turn.text, model=model)
        convo.append({"role": "assistant", "text": turn.text, "tool_calls": turn.tool_calls,
                      "raw": turn.raw})
        results = []
        for call in turn.tool_calls:
            out = run_tool(db, call.name, call.arguments or {}, now, hidden, made,
                           files, downloads, user_id) \
                if call.arguments is not None else json.dumps({"error": "bad arguments"})
            if len(out) > TOOL_RESULT_MAX:
                out = out[:TOOL_RESULT_MAX] + "... [cut: ask more narrowly for the rest]"
            read += len(out)
            steps.append({"tool": call.name, "args": call.arguments or {},
                          "result": out[:STEP_RESULT_MAX]})
            results.append({"id": call.id, "name": call.name, "content": out,
                            "is_error": False})
        convo.append({"role": "tool_results", "results": results})
        if read > CHAT_READ_BUDGET:
            break                       # enough read for one answer: answer with it
    # Out of look-ups: one last request WITHOUT more look-ups, answering with what it found
    # (the owner's chat on 2026-10-01 ended in "I could not finish" instead).
    try:
        prov, model = provider_for(db, now)
        convo.append({"role": "user", "content": "You have no more look-ups. Answer now with "
                      "what you found, and say briefly what you could not check."})
        turn = prov.complete(system=system, messages=convo, tools=TOOLS, model=model,
                             max_tokens=CHAT_MAX_TOKENS)
        _record(db, "chat", model, turn, user_id=user_id)
        usage["input_tokens"] += turn.usage.input_tokens
        usage["output_tokens"] += turn.usage.output_tokens
        if turn.text.strip():
            return result(turn.text, model=turn.model or model)
        again = _answer_now(db, prov, system, convo, model, user_id, usage)
        if again:
            return result(again, model=model)
    except (Unavailable, providers.ProviderError):
        pass
    return result("I could not finish that in a few steps — please ask more narrowly.",
                  error=True, model=model)


def _answer_now(db: Session, prov, system: str, convo: list[dict], model: str,
                user_id: int | None, usage: dict) -> str | None:
    """One more request for the ANSWER, with more room, after a reply that came back empty
    (a thinking model that used its whole limit thinking). None when that fails too."""
    try:
        turn = prov.complete(system=system, messages=[*convo, {
            "role": "user", "content": "Write your answer to my question now, from what you "
                                       "found. Keep it short. Do not look anything else up."}],
            tools=TOOLS, model=model, max_tokens=ANSWER_RETRY_TOKENS)
    except providers.ProviderError as e:
        _record(db, "chat", model, None, user_id=user_id, error=str(e))
        return None
    _record(db, "chat", model, turn, user_id=user_id)
    usage["input_tokens"] += turn.usage.input_tokens
    usage["output_tokens"] += turn.usage.output_tokens
    return turn.text.strip() or None



# ------------------------------------------------------------------------------ the planner

def learned_minutes(db: Session) -> dict[str, dict[str, int]]:
    """How long visits were really booked, per technician and kind (planner.learned_minutes)."""
    from . import planner
    from .rules import visit_kind
    rows = []
    for j in db.scalars(select(DispatchJob).where(DispatchJob.scheduled_start.is_not(None))):
        s, e = c.aware(j.scheduled_start), c.aware(j.scheduled_end)
        if not s or not e or e <= s:
            continue
        for tech in j.assigned or []:
            rows.append((tech, visit_kind(j), (e - s).total_seconds() / 60))
    return planner.learned_minutes(rows)


def _plan_kind(j: DispatchJob) -> str:
    from .rules import visit_kind
    if j.board == c.INSPECTION_BOARD and j.status == "AHS Approved":
        return "repair"
    return visit_kind(j)


def build_plan(db: Session, now: datetime, *, days: int = 6, kind: str = "all",
               hidden: set[str] | None = None, **kw) -> dict:
    """The draft schedule (scheduling.make_plan): every job that needs a visit, fitted with the
    least driving. Read only; kept as a DispatchPlan so the Excel and calendar match."""
    from . import scheduling
    return scheduling.make_plan(db, now, days=days, kind=kind, hidden=hidden, **kw)


def plan_for_model(out: dict) -> dict:
    """The plan, compact, for the chat model."""
    days = []
    for d in out["days"]:
        techs = []
        for t in d["techs"]:
            new = [v for v in t["visits"] if not v["existing"]]
            if not t["visits"]:
                continue
            techs.append({"tech": t["tech"], "visits": "%d of %d" % (t["count"], t["capacity"]),
                          "driving_min": t["drive_minutes"], "list": [
                              "%s-%s %s #%s %s (%s)%s" % (
                                  _hm(v["start"]), _hm(v["end"]), v["kind"],
                                  v["job_number"] or "?", v["customer"] or "", v["city"] or "",
                                  " BOOKED" if v["existing"] else "")
                              for v in t["visits"]], "new": len(new)})
        if techs:
            days.append({"day": d["label"], "techs": techs})
    return {"pending": out["pending"], "days": days,
            "not_placed": ["#%s %s: %s" % (u["job_number"], u["customer"] or "", u["reason"])
                           for u in out["unplaced"]],
            "visit_minutes": out.get("assumptions", {}).get("minutes")}


def _hm(iso: str) -> str:
    t = c.local(datetime.fromisoformat(iso))
    return t.strftime("%I:%M%p").lstrip("0").lower()

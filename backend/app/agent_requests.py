"""A caller asked a voice agent to change something — the office hears it (2026-10-06, C3).

docs/RETELL-PLAN.md, decision 13: a Retell agent's `request_change` tool. The caller wants to
reschedule, cancel or change something; the agent CHANGES NOTHING (no calendar write from a
phone call — 2026-09-22, decision 4) and owen-main posts the request here:

    owen-main ──Bearer ghl_pat_ (events:write)──▶ POST /api/agent-requests
        {"caller_number", "agent_name", "owen_call_id",
         "kind": "reschedule" | "cancel" | "other", "request": "<= 1000 chars"}

## Where it lands

The caller is recognised by `agent_context.identify` — the SAME rule as the brief (decision 5):
last ten digits, exactly one customer, a CRM contact or a Zuper-only customer, a household or
two people on one line is unknown.

  * a CRM contact with an OPEN card the token's owner may see (`pipeline_access`) → an URGENT
    task on the most recently updated one, through the staff service
    (`opportunity_workspace.add_task`), attributed to the agent (`ai_agent_id`, so it reads
    "AI: <agent>") when the CRM has that voice agent, assigned to the card's owner, due now;
  * anyone else known — a Zuper-only customer, or a contact with no open card → a Dispatch
    item (kind `ai_change_request`, urgent) and the bell, through `dispatch.alerts.fire`, on
    the Zuper job's board when the caller has one the token's owner may see (so a dispatcher
    a board is hidden from never hears it). `rules.FED_KINDS` keeps a Dispatch pass from
    resolving it; Done / Wrong close it.
  * unknown → 200 `{"created": false, "where": null, "reason": ...}` and nothing written.

## What it never does

* **Nothing reaches Zuper.** The session is marked `zuper_quiet`, so the CRM→Zuper listener
  queues nothing for the task. Under the one-way mirror (`ZUPER_PULL_ONLY`, production) a CRM
  task never travels to Zuper at all. With a two-way sync armed, a task on a SENT card is
  synced by the 15-minute sweep like any task a person adds — that is the sync's own rule for
  tasks, not something this route asks for.
* **Nothing is queued.** The `jobs` table is counted before and after; one new row and the
  request is refused and rolled back. No text, no call, no automation.
* **Idempotent** on (owen_call_id, kind, request): `ai_agent_requests.key`. A retry answers
  `{"created": true, "where": <the first answer's>, "existing": true}` and writes nothing.

## Who may ask

The feed's machine token, exactly as `/api/agent-context`: `auth.EVENTS_INGEST` (scope
`events:write`, listed in `auth.EVENTS_WRITE_PATHS`) — ADMIN or DISPATCHER owners only, and a
token whose owner has "Only assigned data" on is refused (403), never narrowed.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import agent_context, assigned_access, auth, pipeline_access
from . import opportunity_workspace as ws
from .db import get_db
from .dispatch import alerts, rules
from .dispatch import config as dc
from .models import AiAgentRequest, DispatchItem, Job, Opportunity

log = logging.getLogger("agent_requests")
router = APIRouter(tags=["agent-requests"])

PATH = "/api/agent-requests"
KIND = "ai_change_request"
KIND_LABEL = {"reschedule": "reschedule", "cancel": "cancel", "other": "change something"}
# Which Dispatch queue the office reads it in: a new time is a booking job, the rest is
# "Update Zuper" (a cancellation or a change is made there by a person).
QUEUE = {"reschedule": "book", "cancel": "zuper", "other": "zuper"}
UNKNOWN_REASON = ("the caller is not one known customer (no match, a fragment, or more than "
                  "one customer on that line), so nothing was filed")


class AgentRequestIn(BaseModel):
    caller_number: str = Field(..., max_length=40)
    agent_name: str = Field(..., min_length=1, max_length=200)
    owen_call_id: str = Field(..., min_length=1, max_length=120)
    kind: Literal["reschedule", "cancel", "other"]
    request: str = Field(..., min_length=1, max_length=1000)

    # Unknown keys are dropped, not refused: none of them can choose the customer (the number
    # decides), and a newer owen-main adding a field must not lose a caller's request.
    model_config = {"extra": "ignore"}

    @field_validator("agent_name", "owen_call_id", "request", mode="after")
    @classmethod
    def _text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


def key_for(body: AgentRequestIn) -> str:
    raw = "\n".join((body.owen_call_id, body.kind, " ".join(body.request.split())))
    return "ai_req:" + hashlib.sha256(raw.encode()).hexdigest()[:40]


def _jobs(db: Session) -> int:
    return db.scalar(select(func.count(Job.id))) or 0


def _card(db: Session, principal: auth.Principal, contact_id: int) -> Opportunity | None:
    hidden = pipeline_access.hidden_pipeline_ids(db, principal)
    return db.scalar(pipeline_access.visible_opportunities(
        select(Opportunity).where(Opportunity.contact_id == contact_id,
                                  Opportunity.status == "open"), hidden)
        .order_by(Opportunity.updated_at.desc(), Opportunity.id.desc()).limit(1))


def _who(caller: agent_context.Caller) -> str:
    c = caller.contact
    if c is not None:
        return (" ".join(p for p in (c.first_name, c.last_name) if p) or c.name
                or caller.ten)
    return caller.zuper_name or caller.ten


def deliver(db: Session, body: AgentRequestIn, principal: auth.Principal) -> dict:
    """The answer. Flushes; the route commits."""
    key = key_for(body)
    seen = db.scalar(select(AiAgentRequest).where(AiAgentRequest.key == key))
    if seen is not None:
        return {"created": True, "where": seen.filed_as, "existing": True}

    caller = agent_context.identify(db, principal, body.caller_number)
    if caller is None:
        return {"created": False, "where": None, "reason": UNKNOWN_REASON}

    db.info["zuper_quiet"] = "agent request"     # see the module docstring
    jobs_before = _jobs(db)
    agent, _ = agent_context.find_agent(db, body.agent_name)
    label = "AI: %s" % (agent.name if agent is not None else body.agent_name)
    who = _who(caller)
    what = KIND_LABEL[body.kind]
    now = datetime.now(UTC)
    row = AiAgentRequest(key=key, owen_call_id=body.owen_call_id,
                         agent_id=agent.id if agent is not None else None,
                         agent_name=body.agent_name[:200], kind=body.kind,
                         request=body.request, caller_number=body.caller_number[:40],
                         source=caller.source,
                         contact_id=caller.contact.id if caller.contact else None)

    card = _card(db, principal, caller.contact.id) if caller.contact is not None else None
    if card is not None:
        t = ws.add_task(db, card, ws.TaskCreate(
            title=("URGENT: %s — %s asks to %s" % (label, who, what))[:ws.TASK_TITLE_MAX],
            description="%s\n\nAsked during a call (%s, call %s). The agent changed nothing; "
                        "a person makes the change." % (body.request, label,
                                                        body.owen_call_id),
            due_at=now, assigned_user_id=card.owner_id),
            created_by_id=None, ai_agent_id=agent.id if agent is not None else None,
            priority="urgent")
        row.opportunity_id, row.task_id, row.filed_as = card.id, t.id, "task"
    else:
        job = caller.jobs[0] if caller.jobs else None
        queue = QUEUE[body.kind]
        title = ("%s asks to %s — %s" % (who, what, label))[:300]
        why = ("%s took a call from %s (%s) and passed on: \"%s\". The agent changed nothing."
               % (label, who, caller.ten, body.request))
        todo = ("Call them back and make the change in %s." %
                ("Zuper" if job is not None else "the CRM or Zuper"))
        item = DispatchItem(
            key=key, kind=KIND, queue=queue, state="open", created_at=now, updated_at=now,
            job_uid=job.job_uid if job is not None else None,
            job_number=job.job_number if job is not None else None,
            board=job.board if job is not None else None,
            phone=caller.ten, title=title, why=why, todo=todo, urgent=True,
            # A caller is waiting on an answer: the missed-call limit, whichever queue.
            due_at=dc.add_business_minutes(now, dc.SLA_MINUTES["missed"]),
            evidence={"source": "AI agent request", "agent": label, "kind": body.kind,
                      "owen_call_id": body.owen_call_id, "caller": caller.source,
                      "contact_id": caller.contact.id if caller.contact else None})
        db.add(item)
        db.flush()
        event = rules.Event(key=key, kind=KIND, title=title, body=why,
                            job_uid=job.job_uid if job is not None else None, urgent=True,
                            contact_id=caller.contact.id if caller.contact else None)
        alerts.fire(db, [event], ring=True,
                    boards={(event.job_uid or ""): job.board} if job is not None else {})
        row.dispatch_item_id, row.filed_as = item.id, "dispatch"
    db.add(row)
    db.flush()
    if _jobs(db) != jobs_before:
        raise RuntimeError("an agent request enqueued a job; refusing to commit")
    return {"created": True, "where": row.filed_as}


@router.post(PATH)
def ingest_agent_request(body: AgentRequestIn, db: Session = Depends(get_db),
                         principal: auth.Principal = auth.EVENTS_INGEST) -> dict:
    """A caller's change request from a voice agent: an urgent task on their card, else a
    Dispatch item and the bell; `{"created": false}` for a caller who is not one known
    customer. Writes nothing to Zuper and queues nothing. Pipeline access through
    `pipeline_access.hidden_pipeline_ids` (the card), Zuper boards through
    `alerts.hidden_boards` (via `agent_context.identify`)."""
    if assigned_access.restricted(principal):
        raise HTTPException(403, "a user limited to their own jobs cannot file callers' "
                                 "requests")
    try:
        out = deliver(db, body, principal)
        if out.get("created") and not out.get("existing"):
            db.commit()
        return out
    except IntegrityError:
        # The same request racing its own retry: the other one won. Answer as a repeat.
        db.rollback()
        seen = db.scalar(select(AiAgentRequest).where(AiAgentRequest.key == key_for(body)))
        if seen is None:
            raise
        return {"created": True, "where": seen.filed_as, "existing": True}

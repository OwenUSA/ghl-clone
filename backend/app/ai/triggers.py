"""What the rest of the CRM calls when something happens — the only way an agent starts.

Each hook finds the agents whose PUBLISHED version has a matching trigger and whose mode is
not Off, creates a queued run for the right subject, and enqueues ONE `ai_agent_run` job
through the existing queue (`app.queue.enqueue`, the dedupe key making a repeat delivery a
no-op). The worker runs it after the agent's wait time; every "should it still act?" check
happens then, in `engine.preflight`, and a no is logged with its reason.

Hard rules, enforced here and not left to configuration:

* An agent is never triggered for a NUMBER NO CONTACT HOLDS. `inbound_event` is called
  only from the contact branch of `POST /api/events`; the number-only branch returns before
  it. Nothing here creates a contact, an opportunity or a thread.
* Mode Off never triggers — no run row, no job (a switched-off agent is silent, not
  noisy). A PAUSED module or an out-of-schedule trigger IS logged, cheaply, so the owner
  can see why an agent did not act.
* A change an agent itself made does not trigger agents (`acting_agent`), so two agents —
  or one — cannot loop on each other's stage moves or bookings.
* A hook never breaks the request that called it: a failure is logged and swallowed.

Staff replies go the other way: `staff_replied` puts every sleeping-on-reply agent to sleep on
that customer's conversation and cancels its queued follow-ups, releasing their dedupe keys.
"""
from __future__ import annotations

import contextvars
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AiAgent, AiAgentVersion, AiRun, Direction, EventType
from .config import merged

log = logging.getLogger("ai.triggers")

# Set while an agent's action runs, so what it changes does not wake agents.
acting_agent: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "acting_agent", default=None)

MISSED_CALL_STATUSES = {"no-answer", "busy", "voicemail", "failed"}
MISSED_CALL_MAX_SECONDS = 15


def _live_agents(db: Session, trigger: str):
    """(agent, config, trigger entry) for every non-Off published agent with this trigger."""
    rows = db.scalars(select(AiAgent).where(AiAgent.archived_at.is_(None),
                                            AiAgent.mode != AiAgent.OFF,
                                            AiAgent.published_version_id.is_not(None))
                      .order_by(AiAgent.id)).all()
    for agent in rows:
        version = db.get(AiAgentVersion, agent.published_version_id)
        if version is None:
            continue
        config = merged(version.config)
        if config["channel"] != "text":
            continue
        for t in config["triggers"]:
            if t.get("type") == trigger:
                yield agent, config, t


def _enqueue(db: Session, agent: AiAgent, config: dict, trigger: str, *, key: str,
             contact_id: int, opportunity_id: int | None = None,
             appointment_id: int | None = None, conversation_id: int | None = None,
             delay_minutes: int = 0) -> AiRun | None:
    from ..queue import enqueue
    from . import engine

    dedupe = "ai_run:%s:%s:%s" % (agent.id, trigger, key)
    from ..models import Job
    if db.scalar(select(Job.id).where(Job.dedupe_key == dedupe)) is not None:
        return None
    when = datetime.now(UTC) + timedelta(minutes=delay_minutes)
    run = engine.new_run(db, agent, trigger, contact_id=contact_id,
                         opportunity_id=opportunity_id, appointment_id=appointment_id,
                         conversation_id=conversation_id, trigger_ref=key[:120],
                         run_after=when)
    # Cheap skips are decided now and logged without a job: nothing would change by the
    # time a worker picked it up, and the owner still sees why the agent stayed quiet.
    if engine.settings(db).paused:
        engine.skip(db, run, "all AI agents are paused (Settings → AI Connections)")
        return run
    job = enqueue(db, engine.JOB_TYPE, {"run_id": run.id}, run_after=when, dedupe_key=dedupe)
    if job is None:
        engine.skip(db, run, "already queued")
    return run


def _guarded(fn):
    """No agent-made change triggers agents; a failure rolls back only the hook's own
    writes (a SAVEPOINT) and never reaches the request that called it."""
    def wrapper(db: Session, *args, **kwargs):
        if acting_agent.get() is not None:
            return []
        try:
            with db.begin_nested():
                return fn(db, *args, **kwargs) or []
        except Exception:
            log.exception("AI trigger %s failed", fn.__name__)
            return []
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


@_guarded
def inbound_event(db: Session, contact, conversation, event) -> list[AiRun]:
    """A fresh inbound call or text on a CONTACT's thread (POST /api/events)."""
    runs = []
    if event.direction != Direction.INBOUND:
        return runs
    if event.type == EventType.CALL:
        missed = ((event.call_status or "") in MISSED_CALL_STATUSES
                  or (event.call_status in (None, "") and
                      (event.duration_seconds or 0) <= MISSED_CALL_MAX_SECONDS))
        if not missed:
            return runs
        for agent, config, _t in _live_agents(db, "missed_call"):
            run = _enqueue(db, agent, config, "missed_call", key="event:%s" % event.id,
                           contact_id=contact.id, conversation_id=conversation.id,
                           delay_minutes=config["wait_minutes"])
            if run:
                runs.append(run)
    elif event.type == EventType.SMS:
        for agent, config, t in _live_agents(db, "inbound_text"):
            # One follow-up per customer at a time: a second text while one is queued is
            # read by that run (it reads the latest texts when it runs).
            waiting = db.scalar(select(AiRun.id).where(
                AiRun.agent_id == agent.id, AiRun.contact_id == contact.id,
                AiRun.trigger == "inbound_text", AiRun.outcome == "queued").limit(1))
            if waiting is not None:
                continue
            minutes = max(int(t.get("minutes", 10)), config["wait_minutes"])
            run = _enqueue(db, agent, config, "inbound_text", key="event:%s" % event.id,
                           contact_id=contact.id, conversation_id=conversation.id,
                           delay_minutes=minutes)
            if run:
                runs.append(run)
    return runs


@_guarded
def stage_entered(db: Session, opp) -> list[AiRun]:
    """A deal moved into a stage (every path that fires rule 4)."""
    runs = []
    if opp.contact_id is None:
        return runs          # no customer to act for, and an agent never creates one
    for agent, config, t in _live_agents(db, "stage_entered"):
        if t.get("stage_id") != opp.stage_id:
            continue
        run = _enqueue(db, agent, config, "stage_entered",
                       key="opp:%s:stage:%s:%s" % (opp.id, opp.stage_id,
                                                   datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")),
                       contact_id=opp.contact_id, opportunity_id=opp.id,
                       delay_minutes=config["wait_minutes"])
        if run:
            runs.append(run)
    return runs


@_guarded
def appointment_changed(db: Session, appt, trigger: str) -> list[AiRun]:
    """appointment_booked | appointment_rescheduled | appointment_cancelled."""
    runs = []
    if appt.contact_id is None:
        return runs
    stamp = appt.starts_at.isoformat() if appt.starts_at else ""
    for agent, config, _t in _live_agents(db, trigger):
        run = _enqueue(db, agent, config, trigger,
                       key="appt:%s:%s:%s" % (appt.id, trigger, stamp),
                       contact_id=appt.contact_id, opportunity_id=appt.opportunity_id,
                       appointment_id=appt.id, delay_minutes=config["wait_minutes"])
        if run:
            runs.append(run)
    return runs


def manual(db: Session, agent: AiAgent, *, contact_id: int, opportunity_id: int | None,
           user_id: int) -> AiRun:
    """Staff pressed "Run AI agent". Runs now: the wait time is for automatic triggers."""
    config = merged(db.get(AiAgentVersion, agent.published_version_id).config)
    run = _enqueue(db, agent, config, "manual",
                   key="%s:%s:%s:%s" % (contact_id, opportunity_id or "-", user_id,
                                        datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")),
                   contact_id=contact_id, opportunity_id=opportunity_id)
    run.created_by_id = user_id
    db.flush()
    return run


@_guarded
def staff_replied(db: Session, contact_id: int, reason: str) -> list[int]:
    from . import engine
    return engine.put_to_sleep(db, contact_id, reason)

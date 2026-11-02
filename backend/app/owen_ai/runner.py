"""Owen AI's runner: claims queued messages and answers them, one at a time (2026-11-02).

Run with `python -m app.owen_ai.runner` (the `owen_ai` container). One message at a time on
purpose: it is one person's plan, and two turns in the same thread must never run together.
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import OwenAiMessage, OwenAiThread
from ..registry import service as registry
from . import agent, config

log = logging.getLogger("owen_ai")
NY = ZoneInfo("America/New_York")

TurnFn = Callable[[str, str | None, str], agent.TurnResult]


def _now() -> datetime:
    return datetime.now(UTC)


def recover(db: Session) -> int:
    """A message left RUNNING by a crash or deploy is an error, said plainly, never retried
    behind the owner's back (it may have been half-answered)."""
    n = db.execute(update(OwenAiMessage).where(OwenAiMessage.status == "running").values(
        status="error", error="The assistant restarted while answering. Send it again.",
        finished_at=_now())).rowcount
    db.commit()
    return n or 0


def claim(db: Session) -> OwenAiMessage | None:
    m = db.scalars(select(OwenAiMessage).where(
        OwenAiMessage.status == "queued", OwenAiMessage.role == "user")
        .order_by(OwenAiMessage.id).limit(1)).first()
    if m is None:
        return None
    took = db.execute(update(OwenAiMessage).where(
        OwenAiMessage.id == m.id, OwenAiMessage.status == "queued").values(
        status="running", started_at=_now())).rowcount
    db.commit()
    if not took:
        return None
    db.refresh(m)
    return m


def answer(db: Session, m: OwenAiMessage, turn: TurnFn) -> OwenAiMessage:
    """Run one turn for the user message `m` and save the answer. Never raises."""
    th = db.get(OwenAiThread, m.thread_id)
    why = None
    if not config.enabled():
        why = config.OFF_SENTENCE
    elif not config.token_set():
        why = config.NO_TOKEN_SENTENCE
    else:
        why = registry.level_refusal(db, config.KEY, "ai_chat")
    if th is None:
        why = "This conversation no longer exists."
    if why:
        result = agent.TurnResult(text="", session_id=th.session_id if th else None, error=why)
    else:
        note = agent.context_note(th.kind, th.title, th.job_uid, th.job_number,
                                  datetime.now(NY).date().isoformat())
        result = turn(m.content, th.session_id, note)
    now = _now()
    reply = OwenAiMessage(
        thread_id=m.thread_id, role="assistant",
        content=result.text or ("" if result.error else "(no answer)"),
        status="error" if result.error else "done", error=result.error,
        steps=result.steps or None, model=result.model, cost_usd=result.cost_usd,
        duration_ms=result.duration_ms, num_turns=result.num_turns,
        created_at=now, started_at=m.started_at, finished_at=now)
    db.add(reply)
    m.status = "error" if result.error and not result.text else "done"
    m.finished_at = now
    if th is not None:
        if result.session_id:
            th.session_id = result.session_id
        th.updated_at = now
    db.commit()
    db.refresh(reply)
    return reply


def run_once(db: Session, turn: TurnFn) -> bool:
    m = claim(db)
    if m is None:
        return False
    try:
        answer(db, m, turn)
    except Exception:                             # a bug here must not kill the runner
        log.exception("owen_ai: message %s failed", m.id)
        db.rollback()
        db.execute(update(OwenAiMessage).where(OwenAiMessage.id == m.id).values(
            status="error", error="Internal error in the runner (see owen_ai logs).",
            finished_at=_now()))
        db.commit()
    return True


def main() -> None:                               # pragma: no cover - the container's loop
    from ..db import SessionLocal
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # The plan, never the API: an API key in the environment would take precedence over the
    # Claude login and bill the API account instead.
    os.environ.pop("ANTHROPIC_API_KEY", None)
    db = SessionLocal()
    try:
        n = recover(db)
        if n:
            log.warning("owen_ai: %d message(s) were mid-answer at start; marked error", n)
    finally:
        db.close()
    log.info("owen_ai runner up (enabled=%s, token=%s, workspace=%s)",
             config.enabled(), config.token_set(), config.workspace())
    while True:
        db = SessionLocal()
        try:
            busy = run_once(db, agent.run_turn)
        finally:
            db.close()
        if not busy:
            time.sleep(config.POLL_SECONDS)


if __name__ == "__main__":
    main()

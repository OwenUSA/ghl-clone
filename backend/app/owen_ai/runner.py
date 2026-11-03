"""Owen AI's runner: claims queued messages and answers them, one at a time (2026-11-02).

Run with `python -m app.owen_ai.runner` (the `owen_ai` container). One message at a time on
purpose: it is one person's plan, and two turns in the same thread must never run together.
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session

from ..models import OwenAiMessage, OwenAiThread
from ..registry import service as registry
from . import agent, alerts, config

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
    # The owner's own messages always go before a sweep's audits.
    m = db.scalars(select(OwenAiMessage).where(
        OwenAiMessage.status == "queued", OwenAiMessage.role == "user")
        .order_by(case((OwenAiMessage.kind == "chat", 0), else_=1), OwenAiMessage.id)
        .limit(1)).first()
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
    elif m.kind == "audit":
        # One job of a sweep: a FRESH session every time, never saved on the thread.
        note = agent.context_note("job", th.title, m.job_uid, None,
                                  datetime.now(NY).date().isoformat())
        result = turn(m.content, None, note)
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
        if result.session_id and m.kind != "audit":
            th.session_id = result.session_id
        th.updated_at = now
    db.commit()
    db.refresh(reply)
    if m.kind == "audit" and th is not None:
        if not result.error and m.job_uid and m.started_at:
            alerts.settle_job(db, m.job_uid, m.started_at)
        finish_sweep(db, th)
    return reply


# ------------------------------------------------------------------------- the sweep

AUDIT_PROMPT = ("Scheduled audit. Audit job #{number} (job_uid {uid}, stage: {stage}) with the "
                "job-audit skill: record every failure with report_issue, clear the ones that are "
                "fixed, and answer in the skill's short format.")
SWEEP_STALE_HOURS = 3      # a slot missed by more than this (container down) is skipped


def slot_title(day, hhmm: str) -> str:
    h, m = (int(x) for x in hhmm.split(":"))
    return "Audit — %s %d, %d:%02d %s" % (day.strftime("%a %b"), day.day, (h % 12) or 12, m,
                                          "AM" if h < 12 else "PM")


def due_slot(db: Session, now_ny: datetime) -> str | None:
    """The sweep time that is due now and has not run yet today, or None."""
    if now_ny.weekday() not in config.SWEEP_WEEKDAYS:
        return None
    for hhmm in config.sweep_times():
        h, m = (int(x) for x in hhmm.split(":"))
        at = now_ny.replace(hour=h, minute=m, second=0, microsecond=0)
        if not (at <= now_ny < at + timedelta(hours=SWEEP_STALE_HOURS)):
            continue
        title = slot_title(now_ny.date(), hhmm)
        if db.scalars(select(OwenAiThread.id).where(OwenAiThread.kind == "audit",
                                                    OwenAiThread.title == title)).first():
            continue
        return hhmm
    return None


def start_sweep(db: Session, hhmm: str, jobs: list[dict], now_ny: datetime) -> OwenAiThread:
    """One audit thread for this slot, one queued audit message per open job."""
    owner = alerts._owner(db)
    th = OwenAiThread(user_id=owner.id if owner else 0, kind="audit", day=now_ny.date(),
                      title=slot_title(now_ny.date(), hhmm))
    db.add(th)
    db.flush()
    for j in jobs:
        db.add(OwenAiMessage(thread_id=th.id, role="user", kind="audit", status="queued",
                             job_uid=j["uid"], content=AUDIT_PROMPT.format(
                                 number=j.get("number") or "?", uid=j["uid"],
                                 stage=j.get("stage") or "?")))
    if not jobs:
        db.add(OwenAiMessage(thread_id=th.id, role="assistant", status="done",
                             content="No open jobs to audit on %s." % config.sweep_board()))
    db.commit()
    return th


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def finish_sweep(db: Session, th: OwenAiThread) -> OwenAiMessage | None:
    """When the thread's last audit is done: one summary, built from the alerts table."""
    pending = db.scalar(select(func.count()).select_from(OwenAiMessage).where(
        OwenAiMessage.thread_id == th.id, OwenAiMessage.kind == "audit",
        OwenAiMessage.role == "user", OwenAiMessage.status.in_(("queued", "running"))))
    if pending:
        return None
    audits = list(db.scalars(select(OwenAiMessage).where(
        OwenAiMessage.thread_id == th.id, OwenAiMessage.kind == "audit",
        OwenAiMessage.role == "user")))
    failed = sum(1 for m in audits if m.status == "error")
    started = _aware(min((m.started_at for m in audits if m.started_at),
                         default=th.created_at))
    open_now = alerts.open_for(db)
    new = [a for a in open_now if _aware(a.first_seen_at) >= started]
    counts = {s: sum(1 for a in open_now if a.severity == s) for s in alerts.SEVERITIES}
    lines = ["## %s — done" % th.title,
             "**%d jobs audited**%s · open issues: **%d urgent**, %d today, %d watch · "
             "%d new this sweep" % (len(audits) - failed,
                                    (" (%d failed)" % failed) if failed else "",
                                    counts["urgent"], counts["today"], counts["watch"],
                                    len(new)), ""]
    for a in sorted(new, key=lambda a: alerts.RANK.get(a.severity, 9))[:25]:
        lines.append("- [%s] #%s — %s%s" % (a.severity, a.job_number or "?", a.title,
                                             (" → " + a.action) if a.action else ""))
    m = OwenAiMessage(thread_id=th.id, role="assistant", status="done", kind="audit",
                      content="\n".join(lines), finished_at=_now())
    db.add(m)
    th.updated_at = _now()
    db.commit()
    return m


def maybe_sweep(db: Session, now_ny: datetime, list_jobs) -> OwenAiThread | None:
    if not (config.enabled() and config.token_set() and config.sweep_times()):
        return None
    hhmm = due_slot(db, now_ny)
    if hhmm is None:
        return None
    try:
        jobs = list_jobs(config.sweep_board())
    except Exception:
        log.exception("owen_ai: could not list jobs for the %s sweep", hhmm)
        return None
    return start_sweep(db, hhmm, jobs, now_ny)


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
    from . import dossier
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
            maybe_sweep(db, datetime.now(NY), dossier.open_jobs)
            busy = run_once(db, agent.run_turn)
        except Exception:
            log.exception("owen_ai: runner loop error")
            db.rollback()
            busy = False
        finally:
            db.close()
        if not busy:
            time.sleep(config.POLL_SECONDS)


if __name__ == "__main__":
    main()

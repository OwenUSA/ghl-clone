"""Publishing a voice agent pushes it to owen-main — in the background, and honestly.

DECISIONS.md 2026-09-22, decision 13 (Q21): Publish succeeds in the CRM whatever owen-main is
doing, and the screen says "Published · not yet live on the phone system" until owen-main
answers with the version it stored. It must never claim live when it is not.

    publish (request)  → writes the CRM version, an `ai_voice_pushes` row (pending) and ONE
                         `ai_voice_push` job. No network in the request.
    handle_job (worker) → POST /api/crm-link/agent-versions, then:
        ok              → live, with owen-main's version number and id, and when
        4xx (refused)   → refused, owen-main's sentence; not retried (it would say it again)
        unreachable/5xx → retrying; the job raises and `app.queue` backs off (1, 2, 4 … min)
                          up to MAX_ATTEMPTS, then failed
        link unset      → failed with the sentence naming the two settings; no request
    retry (ADMIN)      → the same push again, from the start.

A newer publish SUPERSEDES an older push still in flight: its job is cancelled and its key
released (the `_drop_pending_reminders` rule — `enqueue` refuses a key whatever its status),
and a job that runs anyway for a version that is no longer the published one pushes nothing.
Pushing version 7 after version 8 would put the older persona back on the phone.

owen-main is idempotent on the CRM version, so a retry after a lost response cannot make a
second version there.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crmlink, queue
from ..models import AiAgent, AiAgentVersion, AiVoicePush, Job
from . import config, voice

JOB_TYPE = "ai_voice_push"
MAX_ATTEMPTS = 10   # 1+2+4+…+256 minutes of backoff: about eight and a half hours

NOT_LIVE = "Published · not yet live on the phone system"
LIVE_LABEL = "Live on the phone system"
NOT_CONFIGURED = ("This server is not linked to the phone system (CRM_LINK_BASE_URL and "
                  "CRM_LINK_API_KEY are not both set), so nothing was sent.")


class PushNotDelivered(Exception):
    """Raised by the handler so the queue retries. The push row is already committed."""


def _now() -> datetime:
    return datetime.now(UTC)


def _key(version_id: int) -> str:
    return "%s:%s" % (JOB_TYPE, version_id)


def _cancel_jobs(db: Session, agent_id: int, keep_version_id: int | None = None) -> int:
    """Cancel this agent's queued pushes (except one version's), releasing their keys."""
    cancelled = 0
    for job in db.scalars(select(Job).where(Job.type == JOB_TYPE,
                                            Job.status.in_(("pending", "running")))).all():
        vid = (job.payload or {}).get("version_id")
        if vid == keep_version_id:
            continue
        v = db.get(AiAgentVersion, vid)
        if v is None or v.agent_id != agent_id or job.status != "pending":
            continue
        job.status = "cancelled"
        if job.dedupe_key:
            job.payload = {**(job.payload or {}), "superseded_key": job.dedupe_key}
            job.dedupe_key = None
        cancelled += 1
    return cancelled


def _enqueue(db: Session, version: AiAgentVersion) -> None:
    job = queue.enqueue(db, JOB_TYPE, {"version_id": version.id}, dedupe_key=_key(version.id))
    if job is not None:
        job.max_attempts = MAX_ATTEMPTS


def request(db: Session, agent: AiAgent, version: AiAgentVersion) -> AiVoicePush:
    """Called by Publish once the version row exists. Queues the push; sends nothing."""
    for older in db.scalars(select(AiVoicePush).where(
            AiVoicePush.agent_id == agent.id, AiVoicePush.version_id != version.id,
            AiVoicePush.status.in_((AiVoicePush.PENDING, AiVoicePush.RETRYING,
                                    AiVoicePush.FAILED, AiVoicePush.REFUSED)))).all():
        older.status = AiVoicePush.SUPERSEDED
    _cancel_jobs(db, agent.id, keep_version_id=version.id)
    push = db.scalar(select(AiVoicePush).where(AiVoicePush.version_id == version.id))
    if push is None:
        push = AiVoicePush(version_id=version.id, agent_id=agent.id,
                           status=AiVoicePush.PENDING, attempts=0)
        db.add(push)
    db.flush()
    _enqueue(db, version)
    return push


def retry(db: Session, agent: AiAgent) -> AiVoicePush:
    """Push the published version again. Refused (ValueError, a sentence) when there is
    nothing to push or it is already live."""
    if agent.channel != AiAgent.VOICE:
        raise ValueError("only a voice agent is pushed to the phone system")
    if agent.published_version_id is None:
        raise ValueError("publish the agent first")
    version = db.get(AiAgentVersion, agent.published_version_id)
    push = db.scalar(select(AiVoicePush).where(AiVoicePush.version_id == version.id))
    if push is not None and push.status == AiVoicePush.LIVE:
        raise ValueError("version %d is already live on the phone system" % version.version)
    for job in db.scalars(select(Job).where(Job.dedupe_key == _key(version.id))).all():
        if job.status == "pending":
            job.status = "cancelled"
        job.payload = {**(job.payload or {}), "superseded_key": job.dedupe_key}
        job.dedupe_key = None
    if push is None:
        push = AiVoicePush(version_id=version.id, agent_id=agent.id)
        db.add(push)
    push.status, push.attempts, push.last_error = AiVoicePush.PENDING, 0, None
    db.flush()
    _enqueue(db, version)
    return push


def handle_job(db: Session, payload: dict) -> None:
    """`automations.HANDLERS["ai_voice_push"]`."""
    push = db.scalar(select(AiVoicePush).where(
        AiVoicePush.version_id == payload.get("version_id")))
    version = db.get(AiAgentVersion, payload.get("version_id"))
    if push is None or version is None or push.status in (AiVoicePush.LIVE,
                                                          AiVoicePush.SUPERSEDED):
        return
    agent = db.get(AiAgent, version.agent_id)
    if agent is None or agent.archived_at is not None \
            or agent.published_version_id != version.id:
        push.status = AiVoicePush.SUPERSEDED
        return
    push.attempts += 1
    push.last_attempt_at = _now()
    if not crmlink.configured():
        push.status, push.last_error = AiVoicePush.FAILED, NOT_CONFIGURED
        return
    c = config.merged(version.config)
    r = crmlink.publish_agent_version(c["owen_agent"], voice.to_owen(c), version.version,
                                      crm_agent_id=agent.id, activate=True)
    if r.ok:
        data = r.data or {}
        push.status, push.last_error, push.pushed_at = AiVoicePush.LIVE, None, _now()
        push.owen_agent_id = str(data.get("agent_id") or "") or None
        push.owen_version_id = str(data.get("version_id") or "") or None
        push.owen_version = data.get("version") if isinstance(data.get("version"), int) \
            else None
        if data.get("active") is False:
            # Stored but not the active version: that is not live, whatever else it is.
            push.status = AiVoicePush.FAILED
            push.last_error = ("The phone system stored it as its version %s but did not "
                               "make it the active one." % (push.owen_version or "?"))
        return
    if r.refused:
        push.status, push.last_error = AiVoicePush.REFUSED, r.reason
        return
    if push.attempts >= MAX_ATTEMPTS:
        push.status = AiVoicePush.FAILED
        push.last_error = "Gave up after %d tries: %s." % (push.attempts, _sentence(r.reason))
        return
    push.status, push.last_error = AiVoicePush.RETRYING, r.reason
    db.commit()
    raise PushNotDelivered(r.reason)


def _sentence(reason: str) -> str:
    reason = (reason or "no answer").strip().rstrip(".")
    return reason[:1].upper() + reason[1:]


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).isoformat()


def state(db: Session, agent: AiAgent) -> dict | None:
    """What the agent's screen says about the phone system. None for a text agent."""
    if agent.channel != AiAgent.VOICE:
        return None
    live = db.scalar(select(AiVoicePush).where(
        AiVoicePush.agent_id == agent.id, AiVoicePush.status == AiVoicePush.LIVE)
        .order_by(AiVoicePush.pushed_at.desc(), AiVoicePush.id.desc()).limit(1))
    live_version = db.get(AiAgentVersion, live.version_id) if live else None
    still = ("The phone system is still running version %d." % live_version.version
             if live_version is not None else "")
    if agent.published_version_id is None:
        return {"status": "unpublished", "live": False, "label": "Not published",
                "detail": "Publish to send it to the phone system.", "can_retry": False,
                "live_version": None}
    version = db.get(AiAgentVersion, agent.published_version_id)
    push = db.scalar(select(AiVoicePush).where(AiVoicePush.version_id == version.id))
    out = {"version": version.version, "live_version": live_version.version
           if live_version else None, "attempts": push.attempts if push else 0,
           "pushed_at": _iso(push.pushed_at) if push else None,
           "last_attempt_at": _iso(push.last_attempt_at) if push else None,
           "owen_version": push.owen_version if push else None,
           "owen_version_id": push.owen_version_id if push else None,
           "last_error": push.last_error if push else None,
           "imported": bool(push and push.imported)}
    if push is not None and push.status == AiVoicePush.LIVE:
        detail = ("Imported from the phone system's version %s." % push.owen_version
                  if push.imported else "Pushed as the phone system's version %s."
                  % push.owen_version)
        return {**out, "status": "live", "live": True, "label": LIVE_LABEL,
                "detail": detail, "can_retry": False}
    status = push.status if push else "not_pushed"
    if status == AiVoicePush.PENDING:
        detail = "Sending version %d to the phone system." % version.version
    elif status == AiVoicePush.RETRYING:
        detail = ("%s. Trying again automatically (attempt %d of %d)."
                  % (_sentence(push.last_error), push.attempts, MAX_ATTEMPTS))
    elif status == AiVoicePush.REFUSED:
        detail = ("%s. It will not be retried until it is changed and published again, or "
                  "you press Retry." % _sentence(push.last_error))
    elif status == AiVoicePush.FAILED:
        detail = "%s. Press Retry to send it again." % _sentence(push.last_error)
    else:
        detail = "It has not been sent to the phone system."
    if still:
        detail += " " + still
    return {**out, "status": status, "live": False, "label": NOT_LIVE, "detail": detail,
            "can_retry": status in (AiVoicePush.REFUSED, AiVoicePush.FAILED, "not_pushed")}

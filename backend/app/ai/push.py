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

ANSWERING CALLS is its own switch (phase 2c, 2026-09-25), `AiAgent.answering_calls`, and the
mode (Off / Suggest / Auto-pilot) governs only what the agent may WRITE in the CRM. Every
request to owen-main goes through the one queued job here, so a switch retries and reports
exactly like a publish:

    publish            → PUSH, `activate = agent.answering_calls` (read when the job RUNS)
    switch on          → PUSH of the published version, activate=True: owen-main activates
                         the version it already holds (no new version) or stores and
                         activates it if it never arrived
    switch off         → DEACTIVATE: owen-main clears the agent's active version, and a call
                         reaching it takes the flow's fallback (voicemail)

Each switch cancels the agent's queued pushes first (keys released), so the LAST switch wins.
owen-main answers every request with what is answering NOW (`answering`, `active_version`);
that, not the request, is what `state` reports.
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

# What the phone-system chip says. Never "Answering" unless owen-main said THIS version is.
ANSWERING = "Answering calls"
NOT_ANSWERING = "Not answering calls"
OTHER_ANSWERING = "Answering calls — not with this version"
NOT_LIVE = "Published · not yet live on the phone system"
NOT_SENT = "Published · not yet on the phone system"
SWITCHING_OFF = "Switching off · not confirmed by the phone system"
UNPUBLISHED = "Not published"
LIVE_LABEL = ANSWERING   # the phase-2b name, kept for callers
FALLBACK = ("A call that reaches this agent goes to the flow's fallback (voicemail).")
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
        if keep_version_id is not None and vid == keep_version_id:
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


def _release(db: Session, version_id: int) -> None:
    """Free this version's dedupe key, whatever its job's status (`enqueue` refuses a key
    that exists at all), cancelling the job if it has not run."""
    for job in db.scalars(select(Job).where(Job.dedupe_key == _key(version_id))).all():
        if job.status == "pending":
            job.status = "cancelled"
        job.payload = {**(job.payload or {}), "superseded_key": job.dedupe_key}
        job.dedupe_key = None


def _enqueue(db: Session, version: AiAgentVersion, op: str = AiVoicePush.PUSH) -> None:
    job = queue.enqueue(db, JOB_TYPE, {"version_id": version.id, "op": op},
                        dedupe_key=_key(version.id))
    if job is not None:
        job.max_attempts = MAX_ATTEMPTS


def _row(db: Session, agent: AiAgent, version: AiAgentVersion) -> AiVoicePush:
    push = db.scalar(select(AiVoicePush).where(AiVoicePush.version_id == version.id))
    if push is None:
        push = AiVoicePush(version_id=version.id, agent_id=agent.id,
                           status=AiVoicePush.PENDING, attempts=0)
        db.add(push)
    return push


def _queue(db: Session, agent: AiAgent, version: AiAgentVersion, op: str) -> AiVoicePush:
    """(Re)queue ONE request for this version; the agent's other queued ones are cancelled."""
    _cancel_jobs(db, agent.id)
    _release(db, version.id)
    push = _row(db, agent, version)
    push.op, push.status, push.attempts, push.last_error = op, AiVoicePush.PENDING, 0, None
    db.flush()
    _enqueue(db, version, op)
    return push


def request(db: Session, agent: AiAgent, version: AiAgentVersion) -> AiVoicePush:
    """Called by Publish once the version row exists. Queues the push; sends nothing. It
    activates on owen-main only if the agent is answering calls when the job runs.

    Publishing while answering is Off never deactivates by itself (a new agent pointed at a
    live phone-system agent must not take it off the phone just by publishing) — EXCEPT that
    a switch-off still on its way is carried forward instead of being dropped by the publish:
    the person asked for it, and the new version reaches owen-main when answering goes on."""
    op = AiVoicePush.PUSH
    for older in db.scalars(select(AiVoicePush).where(
            AiVoicePush.agent_id == agent.id, AiVoicePush.version_id != version.id,
            AiVoicePush.status.in_((AiVoicePush.PENDING, AiVoicePush.RETRYING,
                                    AiVoicePush.FAILED, AiVoicePush.REFUSED)))).all():
        if older.op == AiVoicePush.DEACTIVATE and not agent.answering_calls:
            op = AiVoicePush.DEACTIVATE
        older.status = AiVoicePush.SUPERSEDED
    return _queue(db, agent, version, op)


def set_answering(db: Session, agent: AiAgent, on: bool) -> AiVoicePush | None:
    """The "Answering calls" switch. Records it, and — when a version is published — queues
    the request that makes owen-main match: activate it (on) or deactivate the agent (off).
    With nothing published, nothing is sent; the next publish carries the switch."""
    if agent.channel != AiAgent.VOICE:
        raise ValueError("only a voice agent answers calls — a text agent has no such switch")
    agent.answering_calls = bool(on)
    if agent.published_version_id is None:
        return None
    version = db.get(AiAgentVersion, agent.published_version_id)
    return _queue(db, agent, version, AiVoicePush.PUSH if on else AiVoicePush.DEACTIVATE)


def _someone_answering(db: Session, agent: AiAgent, push: AiVoicePush | None) -> bool:
    """As far as owen-main last told us, is any version of this agent answering?"""
    if push is not None and push.owen_answering:
        return True
    return db.scalar(select(AiVoicePush.id).where(
        AiVoicePush.agent_id == agent.id, AiVoicePush.status == AiVoicePush.LIVE)
        .limit(1)) is not None


def retry(db: Session, agent: AiAgent) -> AiVoicePush:
    """Make the phone system match the switch again, for the published version. Refused
    (ValueError, a sentence) when there is nothing to send or it already matches."""
    if agent.channel != AiAgent.VOICE:
        raise ValueError("only a voice agent is pushed to the phone system")
    if agent.published_version_id is None:
        raise ValueError("publish the agent first")
    version = db.get(AiAgentVersion, agent.published_version_id)
    push = db.scalar(select(AiVoicePush).where(AiVoicePush.version_id == version.id))
    if agent.answering_calls:
        if push is not None and push.status == AiVoicePush.LIVE:
            raise ValueError("version %d is already live on the phone system"
                             % version.version)
        return _queue(db, agent, version, AiVoicePush.PUSH)
    if push is not None and push.status == AiVoicePush.NOT_ANSWERING \
            and push.owen_answering is False:
        raise ValueError("the phone system already confirmed this agent is not answering "
                         "calls")
    op = AiVoicePush.DEACTIVATE if (push is not None and push.op == AiVoicePush.DEACTIVATE) \
        or _someone_answering(db, agent, push) else AiVoicePush.PUSH
    return _queue(db, agent, version, op)


def _record_reality(push: AiVoicePush, data: dict, *, fallback_active: bool | None) -> None:
    answering = data.get("answering")
    push.owen_answering = answering if isinstance(answering, bool) else fallback_active
    av = data.get("active_version")
    push.owen_active_version = av if isinstance(av, int) and not isinstance(av, bool) else None


def _settle_others(db: Session, push: AiVoicePush) -> None:
    """Older rows that said LIVE stop saying it once owen-main's answer shows they are not."""
    for other in db.scalars(select(AiVoicePush).where(
            AiVoicePush.agent_id == push.agent_id, AiVoicePush.id != push.id,
            AiVoicePush.status == AiVoicePush.LIVE)).all():
        if push.status == AiVoicePush.LIVE or push.owen_answering is False \
                or (push.owen_active_version is not None
                    and other.owen_version != push.owen_active_version):
            other.status = AiVoicePush.NOT_ANSWERING
            other.owen_answering = push.owen_answering
            other.owen_active_version = push.owen_active_version


def handle_job(db: Session, payload: dict) -> None:
    """`automations.HANDLERS["ai_voice_push"]`."""
    push = db.scalar(select(AiVoicePush).where(
        AiVoicePush.version_id == payload.get("version_id")))
    version = db.get(AiAgentVersion, payload.get("version_id"))
    op = payload.get("op") or AiVoicePush.PUSH
    if push is None or version is None or push.op != op or push.status in (
            AiVoicePush.LIVE, AiVoicePush.NOT_ANSWERING, AiVoicePush.SUPERSEDED):
        return
    agent = db.get(AiAgent, version.agent_id)
    if agent is None or agent.archived_at is not None \
            or agent.published_version_id != version.id:
        push.status = AiVoicePush.SUPERSEDED
        return
    if op == AiVoicePush.DEACTIVATE and agent.answering_calls:
        return     # switched back on since; that switch queued its own request
    push.attempts += 1
    push.last_attempt_at = _now()
    if not crmlink.configured():
        push.status, push.last_error = AiVoicePush.FAILED, NOT_CONFIGURED
        return
    c = config.merged(version.config)
    activate = bool(agent.answering_calls)
    if op == AiVoicePush.DEACTIVATE:
        r = crmlink.deactivate_agent(c["owen_agent"])
    else:
        r = crmlink.publish_agent_version(c["owen_agent"], voice.to_owen(c), version.version,
                                          crm_agent_id=agent.id, activate=activate)
    if r.ok:
        data = r.data or {}
        push.last_error = None
        if op == AiVoicePush.DEACTIVATE:
            push.status = AiVoicePush.NOT_ANSWERING
            _record_reality(push, data, fallback_active=False)
            _settle_others(db, push)
            return
        push.pushed_at = _now()
        push.owen_agent_id = str(data.get("agent_id") or "") or None
        push.owen_version_id = str(data.get("version_id") or "") or None
        push.owen_version = data.get("version") if isinstance(data.get("version"), int) \
            else None
        active = data.get("active") is True
        _record_reality(push, data, fallback_active=True if active else None)
        if activate and not active:
            # Stored but not the active version: that is not answering, whatever else it is.
            push.status = AiVoicePush.FAILED
            push.last_error = ("The phone system stored it as its version %s but did not "
                               "make it the active one." % (push.owen_version or "?"))
        else:
            push.status = AiVoicePush.LIVE if active else AiVoicePush.NOT_ANSWERING
        _settle_others(db, push)
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
    """What the agent's screen says about the phone system. None for a text agent.

    `status` is one of: unpublished · live (THIS version answers) · not_answering (owen-main
    confirmed nothing of this agent answers) · other_answering (owen-main confirmed another
    version answers) · pending · retrying · refused · failed · not_pushed. `answering` is
    reality as owen-main last reported it (None = it has not said); `answering_calls` is the
    switch. The two are shown side by side and never merged."""
    if agent.channel != AiAgent.VOICE:
        return None
    switch = bool(agent.answering_calls)
    live = db.scalar(select(AiVoicePush).where(
        AiVoicePush.agent_id == agent.id, AiVoicePush.status == AiVoicePush.LIVE)
        .order_by(AiVoicePush.pushed_at.desc(), AiVoicePush.id.desc()).limit(1))
    live_version = db.get(AiAgentVersion, live.version_id) if live else None
    if agent.published_version_id is None:
        return {"status": "unpublished", "live": False, "answering": None,
                "answering_calls": switch, "label": UNPUBLISHED,
                "detail": "Publish to send it to the phone system."
                + (" It will answer calls once it is there." if switch else ""),
                "can_retry": False, "live_version": None}
    version = db.get(AiAgentVersion, agent.published_version_id)
    push = db.scalar(select(AiVoicePush).where(AiVoicePush.version_id == version.id))
    out = {"version": version.version, "live_version": live_version.version
           if live_version else None, "attempts": push.attempts if push else 0,
           "pushed_at": _iso(push.pushed_at) if push else None,
           "last_attempt_at": _iso(push.last_attempt_at) if push else None,
           "owen_version": push.owen_version if push else None,
           "owen_version_id": push.owen_version_id if push else None,
           "owen_active_version": push.owen_active_version if push else None,
           "last_error": push.last_error if push else None,
           "imported": bool(push and push.imported),
           "answering_calls": switch,
           "op": push.op if push else AiVoicePush.PUSH}
    if push is not None and push.status == AiVoicePush.LIVE:
        detail = ("Imported from the phone system's version %s, which is answering calls."
                  % push.owen_version if push.imported else
                  "Answering with version %d (the phone system's version %s)."
                  % (version.version, push.owen_version))
        return {**out, "status": "live", "live": True, "answering": True,
                "label": ANSWERING, "detail": detail, "can_retry": False}
    if push is not None and push.status == AiVoicePush.NOT_ANSWERING:
        if push.owen_answering:
            other = ("the phone system's version %s" % push.owen_active_version
                     if push.owen_active_version is not None else "another version")
            held = ("Version %d is on the phone system but is not the one answering"
                    % version.version if push.owen_version is not None else
                    "Version %d is not on the phone system yet" % version.version)
            detail = "%s; %s is. %s" % (held, other, (
                "Press Retry to make version %d the one answering." % version.version
                if switch else "Answering calls is Off here — press Take it off the phone "
                "to stop it."))
            return {**out, "status": "other_answering", "live": False, "answering": True,
                    "label": OTHER_ANSWERING, "detail": detail, "can_retry": True}
        detail = ("Version %d is on the phone system, switched off. %s"
                  % (version.version, FALLBACK) if push.owen_version is not None
                  else "The phone system confirmed this agent is not answering. %s"
                  % FALLBACK)
        return {**out, "status": "not_answering", "live": False,
                "answering": False if push.owen_answering is False else None,
                "label": NOT_ANSWERING, "detail": detail,
                # Switched on while owen-main still says off: offer to send it again.
                "can_retry": switch}
    status = push.status if push else "not_pushed"
    deactivating = push is not None and push.op == AiVoicePush.DEACTIVATE
    if status == AiVoicePush.PENDING:
        detail = ("Asking the phone system to stop answering with this agent."
                  if deactivating else "Sending version %d to the phone system."
                  % version.version)
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
    was_answering = _someone_answering(db, agent, push)
    if live_version is not None:
        detail += " The phone system is still running version %d." % live_version.version
    elif was_answering and push is not None and push.owen_active_version is not None:
        detail += (" The phone system is still answering with its version %d."
                   % push.owen_active_version)
    label = SWITCHING_OFF if deactivating else NOT_LIVE if switch else NOT_SENT
    return {**out, "status": status, "live": False,
            "answering": True if was_answering else None, "label": label,
            "detail": detail,
            "can_retry": status in (AiVoicePush.REFUSED, AiVoicePush.FAILED, "not_pushed")}

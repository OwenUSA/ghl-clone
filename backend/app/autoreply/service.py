"""One pass of the auto-reply, every POLL_SECONDS on its own worker thread.

    run(db, now) -> {"checked", "replied", "skipped", "waiting", "stopped"?}

Order, every pass — the first gate that is closed stops it with a sentence (the heartbeat):
  1. the server gate ZUPER_AUTOREPLY_ENABLED and the Zuper sync armed;
  2. the switch in Dispatch → Settings;
  3. "Pause all AI agents" (Settings → AI Connections) — it stops the fixed wording too;
  4. today's limit (New York day; sent texts are counted);
  5. the registry (2026-10-21): texts held at Never, its own spend limit or the overall one.
     Held at Shadow, it decides and writes the reply, records it as "shadow", and sends nothing.
Then, per conversation with activity in the last day: read its history, decide (rules.py),
and for a reply: write it (writer.py), check Zuper's sender, RECORD the decision and commit,
send, add the note. The record comes first so that a crash after Zuper accepted the text can
never produce a second one: a row left "sending" is never retried.
"""
from __future__ import annotations

import contextlib
import logging
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..ai import engine as ai_engine
from ..ai import providers, vault
from ..dispatch import comms
from ..dispatch import config as dc
from ..models import AiConnection, AutoReply, DispatchSettings
from ..registry import catalog as reg_catalog
from ..registry import service as registry
from ..zuper import client
from ..zuper import config as zconfig
from . import config as c
from . import context, rules, writer, zconnect

log = logging.getLogger("autoreply")

SENT_STATES = ("sending", "sent")
KEY = "autoreply"                              # its registry entry
# Conversations whose latest activity was already decided ("nothing to do" or recorded): the
# history is not read again until something new happens in them. Per process.
_seen: dict[str, str] = {}


def reset() -> None:
    _seen.clear()


def settings(db: Session, *, for_update: bool = False) -> DispatchSettings:
    from ..dispatch import ai as dispatch_ai
    return dispatch_ai.settings(db, for_update=for_update)


def sent_today(db: Session, now: datetime) -> int:
    start = c.local(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(select(func.count()).select_from(AutoReply).where(
        AutoReply.state.in_(SENT_STATES), AutoReply.created_at >= start.astimezone(UTC))) or 0


def closed_gate(db: Session, now: datetime) -> str | None:
    if not zconfig.autoreply_enabled():
        return zconfig.AUTOREPLY_OFF_SENTENCE
    if not (zconfig.env_enabled() and zconfig.armed(db)):
        return "The Zuper sync is not armed on this server."
    s = settings(db)
    if not s.autoreply_enabled:
        return "The auto-reply is switched off (Dispatch → Settings)."
    if ai_engine.settings(db).paused:
        return "All AI agents are paused (Settings → AI Connections)."
    cap = s.autoreply_daily_cap if s.autoreply_daily_cap is not None else 30
    if sent_today(db, now) >= cap:
        return "Today's limit of %d auto-replies is used up; it resets at midnight." % cap
    held = registry.level_refusal(db, KEY, "send_text")
    if held:
        return held
    over = registry.cap_refusal(db, KEY, now)
    if over:
        registry.alert_failure(db, KEY, over, now, what="cap_reached")
        return over
    return None


def shadow(db: Session) -> bool:
    return registry.level(db, KEY, "send_text") == reg_catalog.SHADOW


def _provider(db: Session, s: DispatchSettings) -> tuple[providers.Provider | None, str | None,
                                                         str | None]:
    conn = db.get(AiConnection, s.autoreply_connection_id) if s.autoreply_connection_id else None
    if conn is None:
        return None, None, "no AI connection is chosen for the auto-reply"
    try:
        prov = providers.build(conn.provider, vault.decrypt(conn.api_key_encrypted),
                               conn.base_url)
    except (vault.SecretsUnavailable, providers.ProviderError) as e:
        return None, None, str(e)
    return prov, (s.autoreply_model or conn.default_model or None), None


def ours(db: Session, phone: str) -> rules.Ours:
    o = rules.Ours()
    if not phone:
        return o
    for r in db.scalars(select(AutoReply).where(AutoReply.phone == phone,
                                                AutoReply.state.in_(SENT_STATES))):
        if r.message_uid:
            o.message_uids.add(r.message_uid)
        if r.reply:
            o.bodies.add(r.reply.strip())
        o.sent_at.append(c.aware(r.sent_at or r.created_at))
    return o


def _record(db: Session, **kw) -> AutoReply | None:
    """Insert the decision; None if this stretch already has one (another pass got there)."""
    row = AutoReply(**kw)
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        return None
    db.commit()
    return row


def note_text(row: AutoReply, now: datetime) -> str:
    what = {"text": "text", "missed_call": "missed call", "voicemail": "voicemail"}[
        row.trigger_kind]
    lines = ["Auto-reply sent %s to the customer's unanswered %s on %s:" % (
        c.local(now).strftime("%b %d, %I:%M %p"), what, c.SENDER_DISPLAY),
        "\"%s\"" % row.reply]
    if row.upset:
        lines.append("⚠️ Needs a person: the customer seemed upset. Please call them.")
    elif row.needs_person:
        lines.append("⚠️ Needs a person: they asked something the auto-reply could not answer.")
    else:
        lines.append("Please follow up with the customer.")
    return "\n".join(lines)


def handle(db: Session, conv: dict, now: datetime, s: DispatchSettings,
           talk: dict[str, list[comms.Comm]]) -> str:
    """One conversation -> "replied" | "skipped" | "waiting" | "nothing" | "failed"."""
    uid = conv.get("conversation_uid")
    cust = zconnect.customer_of(conv)
    # Test mode unless it is explicitly False (an unsaved default row reads None).
    if s.autoreply_test_only is not False and cust["customer_uid"] not in c.TEST_CUSTOMERS:
        return "nothing"
    if zconnect.our_line(conv) and zconnect.our_line(conv) != c.SENDER[-10:]:
        return "nothing"                      # a conversation on another Zuper line
    from ..reviews import service as reviews  # the reviews agent asked them; it answers
    if reviews.active_on(db, cust["phone"], now):
        return "nothing"
    msgs = zconnect.messages(uid)
    other = talk.get(cust["phone"], []) if cust["phone"] else []
    mine = ours(db, cust["phone"])
    d = rules.decide(msgs, other, mine, now=now, enabled_at=s.autoreply_enabled_at,
                     blocked=cust["blocked"])
    if d.action == "wait":
        return "waiting"
    if d.action == "nothing":
        return "nothing"
    if db.scalar(select(AutoReply.id).where(AutoReply.conversation_uid == uid,
                                            AutoReply.trigger_uid == d.trigger.uid)):
        # Already decided for this stretch (sent, shadow, skipped, or left "sending" by a
        # crash): never ask the model again. A shadow reply leaves the stretch unanswered, so
        # without this every new message in it would cost a model call (registry, 2026-10-21).
        return "nothing"
    base = {"conversation_uid": uid, "trigger_uid": d.trigger.uid, "trigger_kind": d.kind,
            "trigger_at": d.trigger.at, "customer_uid": cust["customer_uid"],
            "customer_name": cust["name"], "phone": cust["phone"] or None,
            "after_hours": d.after_hours}
    jobs = context.jobs_for(db, cust["customer_uid"], cust["phone"])
    job = next((j for j in jobs if j.is_open), jobs[0] if jobs else None)
    if job is not None:
        base.update(job_uid=job.job_uid, job_number=job.job_number)
    if d.action == "skip":
        _record(db, state="skipped", reason=d.reason, **base)
        return "skipped"
    if not cust["customer_uid"] or not cust["phone"]:
        _record(db, state="skipped", reason="the number is not a Zuper customer's, so Zuper "
                "has no one to send to", **base)
        return "skipped"
    # --- write it
    brief = context.build(db, s, conv_customer=cust, msgs=msgs, other=other, jobs=jobs,
                          stretch=d.stretch, now=now)
    negative = any((m.sentiment or "").lower() == "negative" for m in d.stretch)
    prov, model, why = _provider(db, s)
    w = writer.write(prov, model, brief, kind=d.kind, after_hours=d.after_hours,
                     sentiment_negative=negative, unavailable=why)
    base.update(reply=w.reply, upset=w.upset, needs_person=w.needs_person, written_by=w.by,
                model=w.model, input_tokens=w.input_tokens, output_tokens=w.output_tokens)
    if w.by == "template" and (why or (w.why_template or "").startswith("the AI did not")):
        # The provider is down or unusable: the fixed wording still goes, and the office hears
        # about it once today (Q13) — a dead model must never go unnoticed for days again.
        registry.alert_failure(db, KEY, w.why_template or why or "the AI did not answer", now)
    if shadow(db):
        # Shadow: everything up to the send, recorded, and nothing leaves (no text, no note).
        row = _record(db, state="shadow", reason=w.why_template, **base)
        return "shadow" if row is not None else "nothing"
    # --- the sender, then the record, then the send
    try:
        problem = zconnect.sender_check()
    except client.ZuperError as e:
        problem = "could not read Zuper's sender number: %s" % client.sentence(e)
    if problem:
        db.rollback()
        raise _Stop(problem)
    if closed_gate(db, now):                  # the limit, re-read just before the send
        db.rollback()
        raise _Stop(closed_gate(db, now))
    row = _record(db, state="sending", reason=w.why_template, **base)
    if row is None:
        return "nothing"
    try:
        answer = zconnect.send(cust["customer_uid"], cust["phone"], w.reply)
    except client.ZuperError as e:
        row.state, row.error = "failed", client.sentence(e)[:500]
        registry.alert_failure(db, KEY, "Zuper did not send a text: %s" % row.error, now)
        db.commit()
        log.warning("auto-reply to conversation %s failed: %s", uid, e.kind)
        return "failed"
    row.state, row.sent_at = "sent", now
    with contextlib.suppress(client.ZuperError):
        row.message_uid = client.uid_of(answer, "message_uid")
    db.commit()
    if job is None:
        row.note_state = "no_job"
    else:
        try:
            zconnect.add_note(job.job_uid, note_text(row, now))
            row.note_state = "added"
        except client.ZuperError as e:
            row.note_state = "failed"
            row.error = ("note: %s" % client.sentence(e))[:500]
    db.commit()
    return "replied"


class _Stop(Exception):
    """Stop the whole pass (the sender changed, the limit is reached)."""


def run(db: Session, now: datetime | None = None) -> dict:
    now = c.aware(now or datetime.now(UTC))
    out = {"checked": 0, "replied": 0, "shadow": 0, "skipped": 0, "waiting": 0, "failed": 0}
    gate = closed_gate(db, now)
    s = settings(db, for_update=True)
    s.autoreply_last_run_at = now
    if gate:
        s.autoreply_last_error = gate
        db.commit()
        return dict(out, stopped=gate)
    try:
        convs = zconnect.recent_conversations()
    except client.ZuperError as e:
        s.autoreply_last_error = "Zuper's conversations could not be read: %s" % (
            client.sentence(e))
        db.commit()
        return dict(out, stopped=s.autoreply_last_error)
    talk = comms.load(db, now - timedelta(days=c.COMMS_DAYS))
    stopped = None
    for conv in convs:
        at = dc.aware(zconnect._time(conv.get("recent_conversation_at")))
        uid = conv.get("conversation_uid")
        if not uid or at is None or now - at > timedelta(hours=c.MAX_AGE_HOURS):
            continue
        if _seen.get(uid) == conv.get("recent_conversation_at"):
            continue
        out["checked"] += 1
        try:
            result = handle(db, conv, now, s, talk)
        except _Stop as e:
            stopped = str(e)
            break
        except client.ZuperError as e:
            db.rollback()
            log.info("auto-reply could not read conversation %s: %s", uid, e.kind)
            continue
        except Exception:
            db.rollback()
            log.exception("auto-reply failed on conversation %s", uid)
            continue
        if result in out:
            out[result] += 1
        if result in ("nothing", "skipped", "replied", "shadow", "failed"):
            _seen[uid] = conv.get("recent_conversation_at")
    s = settings(db, for_update=True)
    s.autoreply_last_run_at = now
    s.autoreply_last_error = stopped
    db.commit()
    if stopped:
        out["stopped"] = stopped
    return out


def tick(session_factory, now: datetime | None = None) -> None:
    if not zconfig.autoreply_enabled():
        return
    db = session_factory()
    try:
        run(db, now)
    except Exception:
        db.rollback()
        log.exception("auto-reply pass failed")
    finally:
        db.close()


def start_thread(session_factory, stop: threading.Event) -> threading.Thread:
    def loop():
        while True:
            tick(session_factory)
            if stop.wait(c.POLL_SECONDS):
                return
    t = threading.Thread(target=loop, name="autoreply", daemon=True)
    t.start()
    return t

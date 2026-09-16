"""The daily activity digest: one private note per customer per day on their job in Zuper.

    Sep 16 - 3 texts (2 in, 1 out) - 1 call, 4 min, answered - https://crm…/opportunities?…

Conversations and calls stay in the CRM (the owner's decision); Zuper gets COUNTS, never a
message's words, a recording or a transcript. The note goes on the customer's most recently
updated OPEN card that has a Zuper job. Days are America/New_York days; the digest for a day
is written after 01:00 the next morning. Digests start on the day the switch was first turned
on — nothing is backfilled. `zuper_digests` is unique per (customer, day), so a day is never
written twice.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    Conversation,
    ConversationEvent,
    Direction,
    EventType,
    Opportunity,
    ZuperDigest,
    ZuperMapping,
)
from . import client, config, engine, mapping, zapi

log = logging.getLogger("zuper.digest")

RUN_AFTER = time(1, 0)
ANSWERED = {"completed", "answered"}
VOICEMAIL = {"voicemail"}


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0, 0), tzinfo=config.ACCOUNT_TZ)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def due_day(db: Session, now: datetime | None = None) -> date | None:
    now = now or datetime.now(UTC)
    settings = config.peek_settings(db)
    if settings is None or settings.enabled_at is None:
        return None
    local = now.astimezone(config.ACCOUNT_TZ)
    if local.time() < RUN_AFTER:
        return None
    day = local.date() - timedelta(days=1)
    if day < mapping.aware(settings.enabled_at).astimezone(config.ACCOUNT_TZ).date():
        return None
    state = db.get(engine.ZuperSyncState, 1)
    if state is not None and state.last_digest_day and state.last_digest_day >= day.isoformat():
        return None
    return day


def _plural(n: int, word: str) -> str:
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def sentence(day: date, counts: dict, link: str) -> str:
    parts = [day.strftime("%b ") + str(day.day)]
    texts = counts.get("texts_in", 0) + counts.get("texts_out", 0)
    if texts:
        split = []
        if counts.get("texts_in"):
            split.append("%d in" % counts["texts_in"])
        if counts.get("texts_out"):
            split.append("%d out" % counts["texts_out"])
        parts.append("%s (%s)" % (_plural(texts, "text"), ", ".join(split)))
    calls = counts.get("calls", 0)
    if calls:
        call = [_plural(calls, "call"), "%d min" % counts.get("call_minutes", 0)]
        outcomes = []
        for key, word in (("answered", "answered"), ("missed", "missed"),
                          ("voicemail", "voicemail")):
            n = counts.get(key, 0)
            if n:
                outcomes.append(word if calls == 1 else "%d %s" % (n, word))
        call.extend(outcomes)
        parts.append(", ".join(call))
    parts.append(link)
    return " - ".join(parts)


def activity(db: Session, day: date) -> dict[int, dict]:
    """Per contact: texts in / out, calls, minutes, answered / missed / voicemail. Counts."""
    start, end = day_bounds(day)
    rows = db.execute(select(Conversation.contact_id, ConversationEvent.type,
                             ConversationEvent.direction, ConversationEvent.duration_seconds,
                             ConversationEvent.call_status)
                      .join(Conversation, Conversation.id == ConversationEvent.conversation_id)
                      .where(ConversationEvent.occurred_at >= start,
                             ConversationEvent.occurred_at < end,
                             ConversationEvent.type.in_((EventType.SMS, EventType.CALL)))).all()
    out: dict[int, dict] = defaultdict(lambda: defaultdict(int))
    seconds: dict[int, int] = defaultdict(int)
    for contact_id, kind, direction, duration, status in rows:
        c = out[contact_id]
        if kind == EventType.SMS:
            c["texts_in" if direction == Direction.INBOUND else "texts_out"] += 1
        else:
            c["calls"] += 1
            seconds[contact_id] += int(duration or 0)
            status = (status or "").lower()
            c["answered" if status in ANSWERED else
              "voicemail" if status in VOICEMAIL else "missed"] += 1
    for contact_id, total in seconds.items():
        out[contact_id]["call_minutes"] = math.ceil(total / 60) if total else 0
    return {k: dict(v) for k, v in out.items()}


def target_job(db: Session, contact_id: int) -> tuple[Opportunity, str] | None:
    rows = db.execute(select(Opportunity, ZuperMapping.zuper_uid).join(
        ZuperMapping, (ZuperMapping.crm_type == "opportunity")
        & (ZuperMapping.crm_id == Opportunity.id)).where(
        Opportunity.contact_id == contact_id, Opportunity.status == "open",
        ZuperMapping.state == "linked", ZuperMapping.zuper_uid.is_not(None))
        .order_by(Opportunity.updated_at.desc(), Opportunity.id.desc())).first()
    return (rows[0], rows[1]) if rows else None


def run(db: Session, day: date) -> dict:
    ctx = engine.Ctx(db)
    counts = {"written": 0, "no_open_job": 0, "already": 0, "failed": 0}
    for contact_id, numbers in sorted(activity(db, day).items()):
        target = target_job(db, contact_id)
        if target is None:
            counts["no_open_job"] += 1
            continue
        o, job_uid = target
        row = db.scalar(select(ZuperDigest).where(ZuperDigest.contact_id == contact_id,
                                                  ZuperDigest.day == day.isoformat()))
        if row is not None and row.state == "posted":
            counts["already"] += 1
            continue
        if row is None:
            row = ZuperDigest(contact_id=contact_id, day=day.isoformat(), opportunity_id=o.id,
                              job_uid=job_uid, counts=numbers)
            db.add(row)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                counts["already"] += 1
                continue
        text = sentence(day, numbers, mapping.crm_link("opportunity", o.id))
        try:
            row.note_uid = zapi.create_note(job_uid, {"note": text, "is_private": True})
            row.state, row.posted_at = "posted", ctx.now
            counts["written"] += 1
        except client.ZuperError as exc:
            row.state = "failed"
            counts["failed"] += 1
            log.warning("zuper digest for contact #%s: %s", contact_id, exc.kind)
            if exc.kind in ("unavailable", "rate_limited", "unauthorized"):
                db.commit()
                raise
        db.commit()
    state = engine.sync_state(db)
    state.last_digest_day = day.isoformat()
    db.commit()
    return counts

"""Zuper webhook deliveries: stored raw FIRST, processed idempotently later by the worker.

Zuper documents neither its event names, nor its payload shape, nor how (or whether) it
signs a delivery (zuper-research.md). So:

* **Authentication** is a shared secret the operator types into the webhook's custom header
  when registering it: `X-Webhook-Token: <ZUPER_WEBHOOK_TOKEN>`. A wrong or missing token is
  401 and NOTHING is stored. If Zuper turns out not to allow custom headers, `?token=` on
  the URL is accepted instead (UNVERIFIED which one Zuper can do; the header is preferred
  because a query string lands in access logs).
* **A signature is verified if one arrives**: a `X-Zuper-Signature` / `X-Signature` /
  `X-Hub-Signature-256` header is checked as HMAC-SHA256 of the raw body under
  `ZUPER_WEBHOOK_SECRET` (hex or base64, an optional "sha256=" prefix). A bad one is 401 and
  nothing is stored; one that arrives with no secret configured is stored as "unchecked".
* **The payload is never trusted for content.** Processing finds the record's uid, drops an
  event made by the "CRM Sync" user (our own write), and otherwise re-reads the record from
  Zuper and runs the ordinary pull — which also ignores content that has not changed. So a
  delivery repeated, reordered or forged with a valid token cannot write anything Zuper does
  not actually hold.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import ZuperDocument, ZuperWebhookDelivery
from . import client, config, engine

log = logging.getLogger("zuper.webhooks")

MAX_BODY_BYTES = 1024 * 1024
MAX_ATTEMPTS = 5
TOKEN_HEADER = "x-webhook-token"
SIGNATURE_HEADERS = ("x-zuper-signature", "x-signature", "x-hub-signature-256")

# Priority when a payload names several uids: the most specific record first.
UID_KEYS = [("estimate_uid", "estimate"), ("invoice_uid", "invoice"),
            ("appointment_uid", "appointment"), ("job_uid", "job"),
            ("customer_uid", "customer")]
MODULE_WORDS = {"ESTIMATE": "estimate", "QUOTE": "estimate", "PROPOSAL": "estimate",
                "INVOICE": "invoice", "APPOINTMENT": "appointment", "JOB": "job",
                "NOTE": "job", "SERVICE_TASK": "job", "CUSTOMER": "customer"}
USER_KEYS = ("updated_by", "created_by", "modified_by", "performed_by", "user", "actor")


def token_ok(sent: str | None) -> bool:
    expected = config.webhook_token()
    return bool(expected and sent and hmac.compare_digest(sent.strip(), expected))


def signature_state(headers, body: bytes) -> str:
    """none | valid | unchecked | invalid"""
    sent = next((headers.get(h) for h in SIGNATURE_HEADERS if headers.get(h)), None)
    if not sent:
        return "none"
    secret = config.webhook_secret()
    if not secret:
        return "unchecked"
    sent = sent.strip()
    if sent.lower().startswith("sha256="):
        sent = sent[7:]
    mac = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    for candidate in (mac.hex(), base64.b64encode(mac).decode()):
        if hmac.compare_digest(sent, candidate):
            return "valid"
    return "invalid"


def _walk(value: Any, depth: int = 0):
    if depth > 4:
        return
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _walk(v, depth + 1)
    elif isinstance(value, list):
        for v in value[:20]:
            yield from _walk(v, depth + 1)


def parse_delivery(payload: Any) -> tuple[str | None, str | None, str | None, str | None]:
    """(module, record uid, event name, acting user uid) — whatever the payload's shape."""
    event = None
    module_hint = None
    if isinstance(payload, dict):
        for key in ("event", "webhook_event", "event_type", "event_name", "action"):
            if isinstance(payload.get(key), str):
                event = payload[key][:120]
                break
        for key in ("module", "webhook_module", "entity", "object", "event", "webhook_event"):
            raw = payload.get(key)
            if isinstance(raw, str):
                upper = raw.upper()
                for word, module in MODULE_WORDS.items():
                    if word in upper:
                        module_hint = module
                        break
            if module_hint:
                break
    found: dict[str, str] = {}
    user_uid = None
    for holder in _walk(payload):
        for key, module in UID_KEYS:
            value = holder.get(key)
            if isinstance(value, str) and value and module not in found:
                found[module] = value
        if user_uid is None:
            for key in USER_KEYS:
                who = holder.get(key)
                if isinstance(who, dict):
                    who = who.get("user_uid") or who.get("uid")
                if isinstance(who, str) and who:
                    user_uid = who
                    break
    if module_hint and module_hint in found:
        return module_hint, found[module_hint], event, user_uid
    for _key, module in UID_KEYS:
        if module in found:
            return module, found[module], event, user_uid
    return module_hint, None, event, user_uid


def store(db: Session, body: bytes, content_type: str | None, signature: str) -> tuple[bool, int]:
    """(new?, row id). The same body twice — a retried delivery — is one row."""
    sha = hashlib.sha256(body).hexdigest()
    existing = db.scalar(select(ZuperWebhookDelivery).where(
        ZuperWebhookDelivery.body_sha256 == sha))
    if existing is not None:
        return False, existing.id
    text = body.decode("utf-8", errors="replace")
    module = uid = event = None
    with contextlib.suppress(ValueError):
        module, uid, event, _ = parse_delivery(json.loads(text))
    row = ZuperWebhookDelivery(body=text, body_sha256=sha, content_type=(content_type or "")[:120],
                               module=module, record_uid=uid, event=event, signature=signature)
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(ZuperWebhookDelivery).where(
            ZuperWebhookDelivery.body_sha256 == sha))
        return False, existing.id if existing else 0
    state = engine.sync_state(db)
    state.last_webhook_at = datetime.now(UTC)
    db.commit()
    return True, row.id


def process_one(db: Session, row: ZuperWebhookDelivery) -> None:
    ctx = engine.Ctx(db)
    row.attempts += 1
    db.commit()
    try:
        payload = json.loads(row.body)
    except ValueError:
        row.status, row.error, row.processed_at = "failed", "The delivery is not JSON.", ctx.now
        db.commit()
        return
    module, uid, event, user_uid = parse_delivery(payload)
    row.module, row.record_uid, row.event = module, uid, event
    settings = config.peek_settings(db)
    if user_uid and settings is not None and settings.sync_user_uid \
            and user_uid == settings.sync_user_uid:
        row.status, row.outcome, row.processed_at = "ignored", "echo: made by CRM Sync", ctx.now
        db.commit()
        return
    if not uid:
        row.status, row.processed_at = "failed", ctx.now
        row.error = "No record uid was found in the delivery; kept for inspection."
        db.commit()
        return
    try:
        if module == "estimate":
            outcome = engine.pull_document(ctx, ZuperDocument.QUOTE, uid)
        elif module == "invoice":
            outcome = engine.pull_document(ctx, ZuperDocument.INVOICE, uid)
        else:
            outcome = engine.pull(ctx, {"job": "job", "customer": "customer",
                                        "appointment": "appointment"}[module], uid)
        row.status = "ignored" if outcome == "unchanged" else "processed"
        row.outcome = ("echo: content unchanged" if outcome == "unchanged" else outcome)[:200]
        row.error = None
        row.processed_at = ctx.now
        engine.sync_state(db).last_pull_at = ctx.now
        db.commit()
    except Exception as exc:
        db.rollback()
        row = db.get(ZuperWebhookDelivery, row.id)
        row.error = client.sentence(exc)
        permanent = isinstance(exc, client.ZuperError) and exc.kind in (
            "refused", "unauthorized", "rejected")
        if row.attempts >= MAX_ATTEMPTS or permanent:
            row.status, row.processed_at = "failed", datetime.now(UTC)
        db.commit()
        if isinstance(exc, client.ZuperError):
            raise
        log.exception("zuper webhook #%s failed", row.id)


def process_inbox(db: Session, limit: int = 50) -> dict:
    """Pending deliveries, oldest first. Stops at the first Zuper outage (the rest wait)."""
    engine.mark_quiet(db)
    done = {"processed": 0, "ignored": 0, "failed": 0}
    rows = db.scalars(select(ZuperWebhookDelivery).where(
        ZuperWebhookDelivery.status == "pending").order_by(ZuperWebhookDelivery.id)
        .limit(limit)).all()
    for row in rows:
        try:
            process_one(db, row)
        except client.ZuperError as exc:
            if exc.kind in ("unavailable", "rate_limited"):
                break
        row = db.get(ZuperWebhookDelivery, row.id)
        if row is not None and row.status in done:
            done[row.status] += 1
    return done

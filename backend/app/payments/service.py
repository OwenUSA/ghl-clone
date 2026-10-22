"""One pass of payments & collections, every POLL_SECONDS on its own worker thread.

    run(db, now) -> {"due", "overdue", "resolved", "zuper", "screenshots", "reminded",
                     "stopped"?}

Off unless PAYMENTS_ENABLED (the page and the chat work regardless - this is the background
part). Then:
  1. every unpaid part of an agreed agreement keeps ONE Dispatch item (queue "Payments to
     collect") while it is due or overdue; the bell rings once when it becomes overdue; the item
     resolves itself when the part is paid or waived or the agreement is cancelled;
  2. Zuper's money for the job (its invoices' paid amount, read from the CRM's copy) above what
     is recorded here becomes ONE suggestion - a person confirms;
  3. a picture a customer with an unpaid part texted is checked once by the vision model; a
     likely payment becomes a proof "to review" + a suggestion + the bell - never "paid";
  4. customer reminders (registry `send_customer`, Never by default) for overdue parts,
     recorded before sending, at most `max_reminders`, every `remind_every_days`.
Nothing is written to Zuper except, when a person has promoted reminders, the text itself.
"""
from __future__ import annotations

import logging
import threading
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import attachments
from ..ai import engine as ai_engine
from ..dispatch import alerts
from ..dispatch import config as dc
from ..dispatch import rules as drules
from ..followup.config import call_hours
from ..inspection_report import vision as rvision
from ..models import (
    AgentRegistryEntry,
    AgentRegistryLog,
    Conversation,
    ConversationEvent,
    Direction,
    DispatchItem,
    DispatchJob,
    MessageAttachment,
    PaymentAgreement,
    PaymentMilestone,
    PaymentProof,
    PaymentSuggestion,
    ZuperDocument,
)
from ..registry import catalog as rcat
from ..registry import service as registry
from ..zuper import client
from ..zuper import config as zconfig
from . import config as c
from . import ledger, rules, vision

log = logging.getLogger("payments")
KIND = "payment_due"
QUEUE = "collect"
OPEN = ("agreed", "collecting")


def settings(db: Session) -> c.Settings:
    row = db.get(AgentRegistryEntry, c.KEY)
    return c.Settings.from_config(row.config if row is not None else None)


def started_at(db: Session, now: datetime) -> datetime:
    at = db.scalar(select(AgentRegistryLog.at).where(
        AgentRegistryLog.entry_key == c.KEY, AgentRegistryLog.what == "started")
        .order_by(AgentRegistryLog.at))
    if at is not None:
        return dc.aware(at)
    db.add(AgentRegistryLog(at=now, entry_key=c.KEY, what="started",
                            new_value="first pass; pictures before this are not checked"))
    db.commit()
    return now


def _job(db: Session, a: PaymentAgreement) -> DispatchJob | None:
    if not a.job_uid:
        return None
    return db.scalar(select(DispatchJob).where(DispatchJob.job_uid == a.job_uid))


def due_of(m: PaymentMilestone, j: DispatchJob | None, s: c.Settings) -> date | None:
    finished = None
    if j is not None and j.status in rules.AFTER_REPAIR:
        finished = dc.aware(j.status_since)
    return rules.due_date(m.due_rule, m.due_on,
                          scheduled_start=j.scheduled_start if j is not None else None,
                          status=j.status if j is not None else None, finished_at=finished,
                          deposit_days_before=s.deposit_days_before)


def view(db: Session, a: PaymentAgreement, s: c.Settings, today: date) -> list[dict]:
    """Each part with its due date and status - the page and the chat read this."""
    j = _job(db, a)
    out = []
    for m in ledger.milestones(db, a.id):
        due = due_of(m, j, s)
        out.append({"id": m.id, "label": m.label, "amount_cents": m.amount_cents,
                    "due_rule": m.due_rule, "due_on": due.isoformat() if due else None,
                    "status": rules.status(m.state, due, today, s.grace_days)
                    if a.state != "cancelled" else "cancelled",
                    "paid_cents": m.paid_cents,
                    "paid_on": m.paid_on.isoformat() if m.paid_on else None,
                    "method": m.method, "source": m.source,
                    "reminders_sent": m.reminders_sent})
    return out


# ------------------------------------------------------------------------- 1. due items

def _items(db: Session, s: c.Settings, now: datetime, out: dict) -> None:
    today = dc.local(now).date()
    for a in db.scalars(select(PaymentAgreement)):
        for part in view(db, a, s, today):
            key = "pay:%d" % part["id"]
            item = db.scalar(select(DispatchItem).where(DispatchItem.key == key))
            live = a.state in OPEN and part["status"] in ("due", "overdue")
            if not live:
                if item is not None and item.state == "open":
                    item.state, item.closed_at = "resolved", now
                    item.close_note = "Paid, waived or no longer due."
                    out["resolved"] += 1
                continue
            late = (today - date.fromisoformat(part["due_on"])).days
            title = "%s - %s %s %s" % (
                (a.customer_name or "Customer").strip(), part["label"],
                rules.money(part["amount_cents"]),
                "overdue %d day%s" % (late, "" if late == 1 else "s") if late > 0 else "due today")
            why = "Agreed %s%s. Due %s." % (rules.money(a.agreed_cents), " (%s)" % a.terms
                                            if a.terms else "", part["due_on"])
            if item is None:
                item = DispatchItem(key=key, kind=KIND, queue=QUEUE, state="open",
                                    created_at=now, job_uid=a.job_uid,
                                    job_number=a.job_number, board=a.board, phone=a.phone,
                                    title=title[:300], why=why,
                                    todo="Collect it, then mark it paid in Collections.",
                                    urgent=False, due_at=now, evidence={"agreement_id": a.id})
                db.add(item)
                out["due"] += 1
            item.title, item.why, item.updated_at = title[:300], why, now
            item.urgent = part["status"] == "overdue"
            if part["status"] == "overdue":
                out["overdue"] += 1
                alerts.fire(db, [drules.Event(key="pay_overdue:%d" % part["id"], kind=KIND,
                                              title="Payment overdue: %s" % title, body=why,
                                              job_uid=a.job_uid, urgent=True)],
                            ring=True, boards={a.job_uid or "": a.board or ""})


# ------------------------------------------------------------------------- 2. Zuper money

def _zuper(db: Session, out: dict) -> None:
    for a in db.scalars(select(PaymentAgreement).where(PaymentAgreement.state.in_(OPEN),
                                                       PaymentAgreement.job_uid.isnot(None))):
        paid_zuper = 0
        for d in db.scalars(select(ZuperDocument).where(
                ZuperDocument.job_uid == a.job_uid, ZuperDocument.kind != ZuperDocument.QUOTE,
                ZuperDocument.removed_in_zuper.is_(False))):
            if d.total_cents is not None and d.balance_cents is not None:
                paid_zuper += max(0, d.total_cents - d.balance_cents)
        parts = ledger.milestones(db, a.id)
        recorded = sum(m.paid_cents or 0 for m in parts if m.state == "paid")
        extra = paid_zuper - recorded
        pending = next((m for m in parts if m.state == "pending"), None)
        if extra <= 0 or pending is None:
            continue
        sg = PaymentSuggestion(key="zuper:%s:%d" % (a.job_uid, paid_zuper),
                               agreement_id=a.id, milestone_id=pending.id, source="zuper",
                               amount_cents=extra,
                               evidence="Zuper's invoices for job #%s show %s paid; %s is "
                                        "recorded here." % (a.job_number or "?",
                                                            rules.money(paid_zuper),
                                                            rules.money(recorded)))
        try:
            with db.begin_nested():
                db.add(sg)
                db.flush()
            out["zuper"] += 1
        except IntegrityError:
            pass


# ------------------------------------------------------------------------- 3. screenshots

def _screenshots(db: Session, s: c.Settings, now: datetime, start: datetime, out: dict) -> None:
    if not s.vision_connection_id or registry.level(db, c.KEY, "ai_vision") == rcat.NEVER:
        return
    owing = {}
    for a in db.scalars(select(PaymentAgreement).where(PaymentAgreement.state.in_(OPEN),
                                                       PaymentAgreement.contact_id.isnot(None))):
        if any(m.state == "pending" for m in ledger.milestones(db, a.id)):
            owing[a.contact_id] = a
    if not owing:
        return
    seen = set(db.scalars(select(PaymentProof.message_attachment_id).where(
        PaymentProof.message_attachment_id.isnot(None))))
    rows = db.execute(
        select(MessageAttachment, Conversation.contact_id)
        .join(ConversationEvent, ConversationEvent.id == MessageAttachment.conversation_event_id)
        .join(Conversation, Conversation.id == ConversationEvent.conversation_id)
        .where(MessageAttachment.status == "STORED",
               MessageAttachment.direction == Direction.INBOUND,
               MessageAttachment.created_at >= start,
               Conversation.contact_id.in_(list(owing)))).all()
    for att, contact_id in rows:
        if att.id in seen:
            continue
        data = attachments.read(att.storage_path)
        if not data:
            continue
        a = owing[contact_id]
        try:
            reading = vision.read(db, s, data)
        except rvision.Unavailable as e:
            registry.alert_failure(db, c.KEY, "a screenshot could not be checked: %s" % e, now)
            return
        except Exception:  # noqa: BLE001 - an unreadable picture is not a payment
            reading = {"looks_like_payment": False, "error": "unreadable"}
        pending = next((m for m in ledger.milestones(db, a.id) if m.state == "pending"), None)
        proof = PaymentProof(agreement_id=a.id, milestone_id=pending.id if pending else None,
                             message_attachment_id=att.id, content_type=att.content_type,
                             ai_reading=reading,
                             state="to_review" if reading.get("looks_like_payment")
                             else "not_payment")
        db.add(proof)
        db.flush()
        seen.add(att.id)
        if not reading.get("looks_like_payment"):
            continue
        amt = reading.get("amount_cents")
        db.add(PaymentSuggestion(
            key="shot:%d" % att.id, agreement_id=a.id,
            milestone_id=pending.id if pending else None, source="screenshot",
            amount_cents=amt, proof_id=proof.id,
            paid_on=date.fromisoformat(reading["date"]) if reading.get("date") else None,
            evidence="%s texted what looks like a %s payment%s." % (
                a.customer_name or "The customer", reading.get("app") or "",
                " of %s" % rules.money(amt) if amt else "")))
        alerts.fire(db, [drules.Event(
            key="pay_shot:%d" % att.id, kind="payment_screenshot",
            title="%s sent what looks like a payment%s - check it" % (
                a.customer_name or "A customer", " of %s" % rules.money(amt) if amt else ""),
            body="Open Collections to confirm the amount.", job_uid=a.job_uid)],
            ring=True, boards={a.job_uid or "": a.board or ""})
        out["screenshots"] += 1


# ------------------------------------------------------------------------- 4. reminders

def _remind(db: Session, s: c.Settings, now: datetime, out: dict) -> None:
    lv = registry.level(db, c.KEY, "send_customer")
    if lv == rcat.NEVER or not call_hours(now):
        return
    today = dc.local(now).date()
    for a in db.scalars(select(PaymentAgreement).where(PaymentAgreement.state.in_(OPEN))):
        j = _job(db, a)
        for part in view(db, a, s, today):
            m = db.get(PaymentMilestone, part["id"])
            if not rules.reminder_due(st=part["status"], sent=m.reminders_sent,
                                      last=m.last_reminded_at, now=now,
                                      every_days=s.remind_every_days,
                                      max_reminders=s.max_reminders):
                continue
            first = (a.customer_name or "").split(" ")[0]
            body = s.reminder_text.replace("{first_name}", first if first.isalpha() else "there")
            body = body.replace("{label}", m.label.lower()).replace(
                "{amount}", rules.money(m.amount_cents))[:320]
            m.reminders_sent += 1
            m.last_reminded_at = now
            db.add(AgentRegistryLog(at=now, entry_key=c.KEY, what="reminder",
                                    target=str(m.id), new_value=body,
                                    reason="shadow" if lv == rcat.SHADOW else "sending"))
            db.commit()                              # recorded BEFORE the send - never twice
            if lv == rcat.SHADOW:
                out["reminded"] += 1
                continue
            if j is None or not j.customer_uid or not a.phone:
                continue
            try:
                with client.payments_write():
                    client.request("POST", "/customers/%s/send" % j.customer_uid, body={
                        "type": "SMS", "sms": {"body": body, "to": "+1" + a.phone}})
                out["reminded"] += 1
            except client.ZuperError as e:
                registry.alert_failure(db, c.KEY, "a payment reminder failed: %s" % e, now)


def run(db: Session, now: datetime | None = None) -> dict:
    now = dc.aware(now or datetime.now(UTC))
    out = {"due": 0, "overdue": 0, "resolved": 0, "zuper": 0, "screenshots": 0, "reminded": 0}
    if not zconfig.payments_enabled():
        return dict(out, stopped=zconfig.PAYMENTS_OFF_SENTENCE)
    if ai_engine.settings(db).paused:
        return dict(out, stopped="All AI agents are paused (Settings -> AI Connections).")
    s = settings(db)
    start = started_at(db, now)
    for step in (lambda: _items(db, s, now, out), lambda: _zuper(db, out),
                 lambda: _screenshots(db, s, now, start, out),
                 lambda: _remind(db, s, now, out)):
        try:
            step()
            db.commit()
        except Exception:
            db.rollback()
            log.exception("a payments step failed")
    return out


def tick(session_factory, now: datetime | None = None) -> None:
    if not zconfig.payments_enabled():
        return
    db = session_factory()
    try:
        run(db, now)
    except Exception:
        db.rollback()
        log.exception("payments pass failed")
    finally:
        db.close()


def start_thread(session_factory, stop: threading.Event) -> threading.Thread:
    def loop():
        while True:
            tick(session_factory)
            if stop.wait(c.POLL_SECONDS):
                return
    t = threading.Thread(target=loop, name="payments", daemon=True)
    t.start()
    return t

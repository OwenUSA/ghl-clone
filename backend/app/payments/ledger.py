"""Every change to an agreement goes through here - from the page, the chat or a confirmed
suggestion - so each one is logged with the values before and after (`payment_changes`), and the
LATEST one can be undone. Nothing here is sent anywhere or written to Zuper."""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PaymentAgreement, PaymentChange, PaymentMilestone
from . import rules

A_FIELDS = ("contact_id", "opportunity_id", "job_uid", "job_number", "board", "customer_name",
            "phone", "quoted_cents", "agreed_cents", "description", "terms", "state", "notes")
M_FIELDS = ("position", "label", "amount_cents", "due_rule", "due_on", "state", "paid_cents",
            "paid_on", "method", "marked_by_id", "source")


class Invalid(ValueError):
    """A change refused, with the sentence to show."""


def milestones(db: Session, agreement_id: int) -> list[PaymentMilestone]:
    return list(db.scalars(select(PaymentMilestone).where(
        PaymentMilestone.agreement_id == agreement_id).order_by(PaymentMilestone.position,
                                                                PaymentMilestone.id)))


def _val(v):
    return v.isoformat() if isinstance(v, date) else v


def snapshot(db: Session, a: PaymentAgreement) -> dict:
    return {"agreement": {k: _val(getattr(a, k)) for k in A_FIELDS},
            "milestones": [{"id": m.id, **{k: _val(getattr(m, k)) for k in M_FIELDS}}
                           for m in milestones(db, a.id)]}


def _refresh_state(db: Session, a: PaymentAgreement) -> None:
    a.state = rules.agreement_state(a.state, agreed=a.agreed_cents is not None,
                                    milestones=[m.state for m in milestones(db, a.id)])


def _log(db: Session, a: PaymentAgreement, before: dict | None, summary: str, *,
         user_id: int | None, via: str) -> PaymentChange:
    db.flush()
    _refresh_state(db, a)
    a.updated_at = datetime.now(UTC)
    db.flush()
    ch = PaymentChange(agreement_id=a.id, user_id=user_id, via=via, summary=summary,
                       before=before, after=snapshot(db, a))
    db.add(ch)
    db.flush()
    return ch


def create(db: Session, *, user_id: int | None, via: str, customer_name: str | None,
           contact_id: int | None = None, opportunity_id: int | None = None,
           job_uid: str | None = None, job_number: str | None = None,
           board: str | None = None, phone: str | None = None,
           quoted_cents: int | None = None, agreed_cents: int | None = None,
           description: str | None = None, terms: str | None = None,
           split: str | None = None) -> tuple[PaymentAgreement, PaymentChange]:
    if not (customer_name or contact_id or job_uid):
        raise Invalid("Say whose agreement this is.")
    for c in (quoted_cents, agreed_cents):
        if c is not None and c <= 0:
            raise Invalid("Amounts must be more than $0.")
    a = PaymentAgreement(contact_id=contact_id, opportunity_id=opportunity_id, job_uid=job_uid,
                         job_number=job_number, board=board, customer_name=customer_name,
                         phone=phone, quoted_cents=quoted_cents, agreed_cents=agreed_cents,
                         description=description, terms=terms or (
                             "%s before / after the job" % split if split else None),
                         state="quoted", created_by_id=user_id)
    db.add(a)
    db.flush()
    if split and agreed_cents:
        for p in rules.split(agreed_cents, split):
            db.add(PaymentMilestone(agreement_id=a.id, position=p.position, label=p.label,
                                    amount_cents=p.amount_cents, due_rule=p.due_rule))
    who = customer_name or job_number or "a customer"
    summary = "New agreement for %s%s%s" % (
        who, ": agreed %s" % rules.money(agreed_cents) if agreed_cents else "",
        " (%s)" % split if split and agreed_cents else (
            ", quoted %s" % rules.money(quoted_cents) if quoted_cents and not agreed_cents
            else ""))
    return a, _log(db, a, None, summary, user_id=user_id, via=via)


def update(db: Session, a: PaymentAgreement, fields: dict, *, user_id: int | None,
           via: str) -> PaymentChange | None:
    before = snapshot(db, a)
    changed = []
    for k in ("quoted_cents", "agreed_cents", "description", "terms", "notes", "customer_name",
              "job_number"):
        if k in fields and fields[k] != getattr(a, k):
            v = fields[k]
            if k.endswith("_cents") and v is not None and v <= 0:
                raise Invalid("Amounts must be more than $0.")
            setattr(a, k, v)
            changed.append("%s %s" % (k.replace("_cents", "").replace("_", " "),
                                      rules.money(v) if k.endswith("_cents") else (v or "-")))
    if "state" in fields and fields["state"] == "cancelled" and a.state != "cancelled":
        a.state = "cancelled"
        changed.append("cancelled")
    if not changed:
        return None
    return _log(db, a, before, "Updated: %s" % "; ".join(changed), user_id=user_id, via=via)


def set_split(db: Session, a: PaymentAgreement, how: str, *, user_id: int | None,
              via: str) -> PaymentChange:
    if not a.agreed_cents:
        raise Invalid("Set the agreed amount first.")
    if any(m.state == "paid" for m in milestones(db, a.id)):
        raise Invalid("A part is already paid; change the parts one by one instead.")
    before = snapshot(db, a)
    for m in milestones(db, a.id):
        db.delete(m)
    db.flush()
    for p in rules.split(a.agreed_cents, how):
        db.add(PaymentMilestone(agreement_id=a.id, position=p.position, label=p.label,
                                amount_cents=p.amount_cents, due_rule=p.due_rule))
    a.terms = "%s before / after the job" % how if how != "full" else "Paid in full after"
    return _log(db, a, before, "Split %s of %s" % (how, rules.money(a.agreed_cents)),
                user_id=user_id, via=via)


def add_milestone(db: Session, a: PaymentAgreement, *, label: str, amount_cents: int,
                  due_rule: str, due_on: date | None, user_id: int | None,
                  via: str) -> PaymentChange:
    if amount_cents <= 0:
        raise Invalid("Amounts must be more than $0.")
    if due_rule not in ("before_start", "after_completion", "on_date"):
        raise Invalid("When is it due: before the job, after the job, or on a date?")
    before = snapshot(db, a)
    pos = max([m.position for m in milestones(db, a.id)] or [-1]) + 1
    db.add(PaymentMilestone(agreement_id=a.id, position=pos, label=label[:120],
                            amount_cents=amount_cents, due_rule=due_rule, due_on=due_on))
    return _log(db, a, before, "Added %s of %s" % (label, rules.money(amount_cents)),
                user_id=user_id, via=via)


def record_payment(db: Session, a: PaymentAgreement, m: PaymentMilestone, *, amount_cents: int,
                   paid_on: date, method: str | None, user_id: int | None, via: str,
                   source: str) -> PaymentChange:
    if m.agreement_id != a.id:
        raise Invalid("That part belongs to another agreement.")
    if amount_cents <= 0:
        raise Invalid("A payment must be more than $0.")
    if m.state == "paid":
        raise Invalid("%s is already marked paid." % m.label)
    before = snapshot(db, a)
    m.state, m.paid_cents, m.paid_on = "paid", amount_cents, paid_on
    m.method, m.marked_by_id, m.source = (method or None), user_id, source
    note = "" if amount_cents == m.amount_cents else " (the part was %s)" % rules.money(
        m.amount_cents)
    return _log(db, a, before, "%s marked paid: %s on %s%s%s" % (
        m.label, rules.money(amount_cents), paid_on.strftime("%b %d"),
        " by %s" % method if method else "", note), user_id=user_id, via=via)


def waive(db: Session, a: PaymentAgreement, m: PaymentMilestone, *, user_id: int | None,
          via: str) -> PaymentChange:
    if m.state == "paid":
        raise Invalid("%s is already paid." % m.label)
    before = snapshot(db, a)
    m.state, m.marked_by_id = "waived", user_id
    return _log(db, a, before, "%s waived" % m.label, user_id=user_id, via=via)


def latest(db: Session, agreement_id: int) -> PaymentChange | None:
    return db.scalars(select(PaymentChange).where(
        PaymentChange.agreement_id == agreement_id, PaymentChange.undone_at.is_(None))
        .order_by(PaymentChange.id.desc()).limit(1)).first()


def undo(db: Session, ch: PaymentChange, *, user_id: int | None) -> PaymentAgreement:
    """Put the agreement back as it was before `ch` - only the latest change can be undone."""
    if ch.undone_at is not None:
        raise Invalid("That change was already undone.")
    last = latest(db, ch.agreement_id)
    if last is None or last.id != ch.id:
        raise Invalid("Only the latest change on this agreement can be undone.")
    a = db.get(PaymentAgreement, ch.agreement_id)
    now = datetime.now(UTC)
    if ch.before is None:                                   # undo a creation: cancel it
        a.state = "cancelled"
    else:
        for k, v in ch.before["agreement"].items():
            setattr(a, k, v)
        keep = {m["id"]: m for m in ch.before["milestones"]}
        for m in milestones(db, a.id):
            if m.id not in keep:
                db.delete(m)
        db.flush()
        for mid, vals in keep.items():
            m = db.get(PaymentMilestone, mid)
            if m is None:
                m = PaymentMilestone(id=mid, agreement_id=a.id)
                db.add(m)
            for k, v in vals.items():
                if k == "id":
                    continue
                if k in ("due_on", "paid_on") and isinstance(v, str):
                    v = date.fromisoformat(v)
                setattr(m, k, v)
    a.updated_at = now
    ch.undone_at, ch.undone_by_id = now, user_id
    db.flush()
    return a

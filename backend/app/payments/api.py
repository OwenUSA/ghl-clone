"""/api/payments - the Collections page and its chat. Money is office work: Dispatch's access rule
(`dispatch.api.VIEW`: ADMIN and unrestricted DISPATCHER; a technician or restricted user 403). An
agreement on a board hidden from the user (pipeline permissions) answers 404, as a missing id.
Every write goes through `ledger` and can be undone (the latest change). Nothing here writes to
Zuper or texts anyone."""
from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import attachments, auth
from ..db import get_db
from ..dispatch import config as dc
from ..dispatch.api import VIEW, _hidden
from ..models import (
    DispatchJob,
    MessageAttachment,
    PaymentAgreement,
    PaymentChange,
    PaymentChatMessage,
    PaymentMilestone,
    PaymentProof,
    PaymentSuggestion,
)
from . import chat, ledger, rules, service

router = APIRouter(prefix="/api/payments", tags=["payments"])
FILTERS = ("open", "due", "overdue", "upcoming", "quoted", "paid", "cancelled", "all")


def _iso(v):
    return v.isoformat() if v else None


def _get(db: Session, principal: auth.Principal, agreement_id: int) -> PaymentAgreement:
    a = db.get(PaymentAgreement, agreement_id)
    if a is None or (a.board and a.board in _hidden(db, principal)):
        raise HTTPException(404, "agreement not found")
    return a


def _change(ch: PaymentChange | None) -> dict | None:
    if ch is None:
        return None
    return {"id": ch.id, "summary": ch.summary, "via": ch.via, "created_at": _iso(ch.created_at),
            "undone_at": _iso(ch.undone_at), "user_id": ch.user_id}


def _row(db: Session, a: PaymentAgreement, today: date) -> dict:
    parts = service.view(db, a, service.settings(db), today)
    owed = sum(p["amount_cents"] for p in parts if p["status"] in ("due", "overdue"))
    worst = "overdue" if any(p["status"] == "overdue" for p in parts) else (
        "due" if any(p["status"] == "due" for p in parts) else (
            "upcoming" if any(p["status"] == "upcoming" for p in parts) else a.state))
    return {"id": a.id, "customer_name": a.customer_name, "job_number": a.job_number,
            "board": a.board, "state": a.state, "quoted_cents": a.quoted_cents,
            "agreed_cents": a.agreed_cents, "terms": a.terms, "owed_now_cents": owed,
            "status": worst, "parts": parts, "updated_at": _iso(a.updated_at or a.created_at)}


def _detail(db: Session, a: PaymentAgreement) -> dict:
    today = dc.local(datetime.now(UTC)).date()
    out = _row(db, a, today)
    proofs = list(db.scalars(select(PaymentProof).where(PaymentProof.agreement_id == a.id)
                             .order_by(PaymentProof.id.desc())))
    sugs = list(db.scalars(select(PaymentSuggestion).where(
        PaymentSuggestion.agreement_id == a.id).order_by(PaymentSuggestion.id.desc())))
    changes = list(db.scalars(select(PaymentChange).where(PaymentChange.agreement_id == a.id)
                              .order_by(PaymentChange.id.desc()).limit(50)))
    last = ledger.latest(db, a.id)
    out.update(
        description=a.description, notes=a.notes, phone=a.phone, contact_id=a.contact_id,
        proofs=[{"id": p.id, "milestone_id": p.milestone_id, "state": p.state,
                 "ai_reading": p.ai_reading, "created_at": _iso(p.created_at),
                 "url": "/api/payments/%d/proofs/%d/image" % (a.id, p.id)}
                for p in proofs if p.state != "not_payment"],
        suggestions=[{"id": s.id, "source": s.source, "amount_cents": s.amount_cents,
                      "paid_on": _iso(s.paid_on), "evidence": s.evidence,
                      "milestone_id": s.milestone_id, "proof_id": s.proof_id, "state": s.state}
                     for s in sugs],
        history=[dict(_change(ch), undoable=last is not None and ch.id == last.id)
                 for ch in changes])
    return out


@router.get("")
def payments_list(show: str = Query("open"), db: Session = Depends(get_db),
                  principal: auth.Principal = VIEW):
    if show not in FILTERS:
        raise HTTPException(400, "show is one of %s" % ", ".join(FILTERS))
    hidden = _hidden(db, principal)
    today = dc.local(datetime.now(UTC)).date()
    rows = [_row(db, a, today) for a in db.scalars(
        select(PaymentAgreement).order_by(PaymentAgreement.id.desc()).limit(500))
        if not (a.board and a.board in hidden)]
    pick = {"open": lambda r: r["state"] in ("quoted", "agreed", "collecting"),
            "due": lambda r: r["status"] == "due", "overdue": lambda r: r["status"] == "overdue",
            "upcoming": lambda r: r["status"] == "upcoming",
            "quoted": lambda r: r["state"] == "quoted", "paid": lambda r: r["state"] == "paid",
            "cancelled": lambda r: r["state"] == "cancelled", "all": lambda r: True}[show]
    summary = {"overdue_cents": sum(sum(p["amount_cents"] for p in r["parts"]
                                        if p["status"] == "overdue") for r in rows),
               "due_cents": sum(sum(p["amount_cents"] for p in r["parts"]
                                    if p["status"] == "due") for r in rows),
               "open": sum(1 for r in rows if r["state"] in ("agreed", "collecting")),
               "quoted": sum(1 for r in rows if r["state"] == "quoted"),
               "suggestions": db.query(PaymentSuggestion).filter(
                   PaymentSuggestion.state == "open").count()}
    return {"agreements": [r for r in rows if pick(r)], "summary": summary}


@router.get("/{agreement_id}")
def payments_get(agreement_id: int, db: Session = Depends(get_db),
                 principal: auth.Principal = VIEW):
    return _detail(db, _get(db, principal, agreement_id))


class AgreementIn(BaseModel):
    job_number: str | None = Field(None, max_length=40)
    customer_name: str | None = Field(None, max_length=200)
    quoted: str | float | None = None
    agreed: str | float | None = None
    description: str | None = Field(None, max_length=2000)
    terms: str | None = Field(None, max_length=300)
    split: str | None = Field(None, max_length=10)


def _amount(v, what: str) -> int | None:
    if v in (None, ""):
        return None
    c = rules.cents(v)
    if c is None or c <= 0:
        raise HTTPException(400, "The %s amount is a dollar amount, like 1500 or 1,500.00." % what)
    return c


@router.post("", status_code=201)
def payments_create(body: AgreementIn, db: Session = Depends(get_db),
                    principal: auth.Principal = VIEW):
    j = None
    if body.job_number:
        j = db.scalar(select(DispatchJob).where(DispatchJob.job_number ==
                                                body.job_number.strip().lstrip("#")))
        if j is None:
            raise HTTPException(400, "There is no Zuper job #%s." % body.job_number)
        if j.board and j.board in _hidden(db, principal):
            raise HTTPException(400, "There is no Zuper job #%s." % body.job_number)
    try:
        a, _ch = ledger.create(
            db, user_id=principal.user_id, via="page",
            customer_name=(j.customer_name if j else None) or body.customer_name,
            job_uid=j.job_uid if j else None, job_number=j.job_number if j else None,
            board=j.board if j else None, phone=(j.phones or [None])[0] if j else None,
            quoted_cents=_amount(body.quoted, "quoted"), agreed_cents=_amount(body.agreed,
                                                                              "agreed"),
            description=body.description, terms=body.terms, split=body.split)
    except ledger.Invalid as e:
        db.rollback()
        raise HTTPException(400, str(e)) from None
    db.commit()
    return _detail(db, a)


class UpdateIn(BaseModel):
    quoted: str | float | None = None
    agreed: str | float | None = None
    description: str | None = Field(None, max_length=2000)
    terms: str | None = Field(None, max_length=300)
    notes: str | None = Field(None, max_length=4000)
    cancel: bool | None = None
    split: str | None = Field(None, max_length=10)


@router.patch("/{agreement_id}")
def payments_update(agreement_id: int, body: UpdateIn, db: Session = Depends(get_db),
                    principal: auth.Principal = VIEW):
    a = _get(db, principal, agreement_id)
    fields: dict = {}
    if body.quoted is not None:
        fields["quoted_cents"] = _amount(body.quoted, "quoted")
    if body.agreed is not None:
        fields["agreed_cents"] = _amount(body.agreed, "agreed")
    for k in ("description", "terms", "notes"):
        if getattr(body, k) is not None:
            fields[k] = getattr(body, k).strip() or None
    if body.cancel:
        fields["state"] = "cancelled"
    try:
        ledger.update(db, a, fields, user_id=principal.user_id, via="page")
        if body.split:
            ledger.set_split(db, a, body.split, user_id=principal.user_id, via="page")
    except ledger.Invalid as e:
        db.rollback()
        raise HTTPException(400, str(e)) from None
    db.commit()
    return _detail(db, a)


class PartIn(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    amount: str | float
    due_rule: str = "on_date"
    due_on: date | None = None


@router.post("/{agreement_id}/parts", status_code=201)
def payments_add_part(agreement_id: int, body: PartIn, db: Session = Depends(get_db),
                      principal: auth.Principal = VIEW):
    a = _get(db, principal, agreement_id)
    try:
        ledger.add_milestone(db, a, label=body.label, amount_cents=_amount(body.amount, "part"),
                             due_rule=body.due_rule, due_on=body.due_on,
                             user_id=principal.user_id, via="page")
    except ledger.Invalid as e:
        db.rollback()
        raise HTTPException(400, str(e)) from None
    db.commit()
    return _detail(db, a)


class PaidIn(BaseModel):
    amount: str | float | None = None
    paid_on: date | None = None
    method: str | None = Field(None, max_length=60)


def _part(db: Session, a: PaymentAgreement, part_id: int) -> PaymentMilestone:
    m = db.get(PaymentMilestone, part_id)
    if m is None or m.agreement_id != a.id:
        raise HTTPException(404, "part not found")
    return m


@router.post("/{agreement_id}/parts/{part_id}/paid")
def payments_mark_paid(agreement_id: int, part_id: int, body: PaidIn,
                       db: Session = Depends(get_db), principal: auth.Principal = VIEW):
    a = _get(db, principal, agreement_id)
    m = _part(db, a, part_id)
    try:
        ledger.record_payment(db, a, m, amount_cents=_amount(body.amount, "paid") or
                              m.amount_cents, paid_on=body.paid_on or dc.local(
                                  datetime.now(UTC)).date(), method=body.method,
                              user_id=principal.user_id, via="page", source="manual")
    except ledger.Invalid as e:
        db.rollback()
        raise HTTPException(400, str(e)) from None
    db.commit()
    return _detail(db, a)


@router.post("/{agreement_id}/parts/{part_id}/waive")
def payments_waive(agreement_id: int, part_id: int, db: Session = Depends(get_db),
                   principal: auth.Principal = VIEW):
    a = _get(db, principal, agreement_id)
    try:
        ledger.waive(db, a, _part(db, a, part_id), user_id=principal.user_id, via="page")
    except ledger.Invalid as e:
        db.rollback()
        raise HTTPException(400, str(e)) from None
    db.commit()
    return _detail(db, a)


@router.post("/changes/{change_id}/undo")
def payments_undo(change_id: int, db: Session = Depends(get_db),
                  principal: auth.Principal = VIEW):
    ch = db.get(PaymentChange, change_id)
    if ch is None:
        raise HTTPException(404, "change not found")
    a = _get(db, principal, ch.agreement_id)
    try:
        ledger.undo(db, ch, user_id=principal.user_id)
    except ledger.Invalid as e:
        db.rollback()
        raise HTTPException(409, str(e)) from None
    db.commit()
    return _detail(db, a)


@router.post("/suggestions/{suggestion_id}/accept")
def payments_accept_suggestion(suggestion_id: int, db: Session = Depends(get_db),
                               principal: auth.Principal = VIEW):
    s = db.get(PaymentSuggestion, suggestion_id)
    if s is None:
        raise HTTPException(404, "suggestion not found")
    a = _get(db, principal, s.agreement_id)
    if s.state != "open":
        raise HTTPException(409, "This suggestion is already %s." % s.state)
    m = db.get(PaymentMilestone, s.milestone_id) if s.milestone_id else None
    if m is None or m.state != "pending":
        raise HTTPException(409, "There is no unpaid part left for it; record it by hand.")
    try:
        ledger.record_payment(db, a, m, amount_cents=s.amount_cents or m.amount_cents,
                              paid_on=s.paid_on or dc.local(datetime.now(UTC)).date(),
                              method="Zuper" if s.source == "zuper" else None,
                              user_id=principal.user_id, via="suggestion", source=s.source)
    except ledger.Invalid as e:
        db.rollback()
        raise HTTPException(400, str(e)) from None
    s.state, s.decided_by_id, s.decided_at = "accepted", principal.user_id, datetime.now(UTC)
    if s.proof_id:
        p = db.get(PaymentProof, s.proof_id)
        if p is not None:
            p.state, p.milestone_id = "accepted", m.id
    db.commit()
    return _detail(db, a)


@router.post("/suggestions/{suggestion_id}/dismiss")
def payments_dismiss_suggestion(suggestion_id: int, db: Session = Depends(get_db),
                                principal: auth.Principal = VIEW):
    s = db.get(PaymentSuggestion, suggestion_id)
    if s is None:
        raise HTTPException(404, "suggestion not found")
    a = _get(db, principal, s.agreement_id)
    if s.state == "open":
        s.state, s.decided_by_id, s.decided_at = "dismissed", principal.user_id, datetime.now(UTC)
        if s.proof_id:
            p = db.get(PaymentProof, s.proof_id)
            if p is not None and p.state == "to_review":
                p.state = "rejected"
        db.commit()
    return _detail(db, a)


@router.post("/{agreement_id}/proofs", status_code=201)
async def payments_upload_proof(agreement_id: int, file: UploadFile = File(...),
                                part_id: int | None = None, db: Session = Depends(get_db),
                                principal: auth.Principal = VIEW):
    a = _get(db, principal, agreement_id)
    data = await file.read(attachments.MAX_BYTES + 1)
    if len(data) > attachments.MAX_BYTES:
        raise HTTPException(413, attachments.refuse_size(len(data)))
    ctype = attachments.sniff(data)
    if ctype is None:
        raise HTTPException(415, "Only a picture (JPEG, PNG, GIF, WebP or HEIC) can be a proof.")
    if part_id is not None:
        _part(db, a, part_id)
    sha, _ = attachments.store(data)
    db.add(PaymentProof(agreement_id=a.id, milestone_id=part_id, sha=sha, content_type=ctype,
                        state="accepted", created_by_id=principal.user_id))
    db.commit()
    return _detail(db, a)


@router.get("/{agreement_id}/proofs/{proof_id}/image")
def payments_proof_image(agreement_id: int, proof_id: int, db: Session = Depends(get_db),
                         principal: auth.Principal = VIEW) -> Response:
    a = _get(db, principal, agreement_id)
    p = db.get(PaymentProof, proof_id)
    if p is None or p.agreement_id != a.id:
        raise HTTPException(404, "proof not found")
    data, ctype = None, p.content_type
    if p.sha:
        data = attachments.read("%s/%s" % (p.sha[:2], p.sha))
    elif p.message_attachment_id:
        att = db.get(MessageAttachment, p.message_attachment_id)
        data = attachments.read(att.storage_path) if att else None
        ctype = att.content_type if att else None
    if not data:
        raise HTTPException(404, "proof not found")
    ctype = attachments.sniff(data) or "application/octet-stream"
    return Response(data, media_type=ctype, headers={
        "Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def _chat_out(rows: list[PaymentChatMessage], db: Session) -> list[dict]:
    out = []
    for r in rows:
        chs = [db.get(PaymentChange, i) for i in (r.change_ids or [])]
        out.append({"id": r.id, "role": r.role, "text": r.text, "error": r.error,
                    "created_at": _iso(r.created_at),
                    "changes": [dict(_change(c), agreement_id=c.agreement_id)
                                for c in chs if c is not None]})
    return out


@router.get("/chat/messages")
def payments_chat_history(db: Session = Depends(get_db), principal: auth.Principal = VIEW):
    rows = list(db.scalars(select(PaymentChatMessage).where(
        PaymentChatMessage.user_id == principal.user_id)
        .order_by(PaymentChatMessage.id.desc()).limit(60)))
    return {"messages": _chat_out(list(reversed(rows)), db)}


@router.post("/chat/messages")
def payments_chat_send(body: ChatIn, db: Session = Depends(get_db),
                       principal: auth.Principal = VIEW):
    if principal.role.value != "ADMIN" and _hidden(db, principal):
        # The chat reads every agreement; a user with hidden boards uses the page instead.
        raise HTTPException(403, "The payments chat is not available with hidden pipelines.")
    db.add(PaymentChatMessage(user_id=principal.user_id, role="user", text=body.text.strip()))
    db.commit()
    rows = list(db.scalars(select(PaymentChatMessage).where(
        PaymentChatMessage.user_id == principal.user_id)
        .order_by(PaymentChatMessage.id.desc()).limit(20)))
    history = [{"role": r.role, "text": r.text} for r in reversed(rows) if not r.error]
    res = chat.answer(db, history, user_id=principal.user_id)
    reply = PaymentChatMessage(user_id=principal.user_id, role="assistant", text=res["reply"],
                               steps=res["steps"], change_ids=res["change_ids"],
                               error=res["error"])
    db.add(reply)
    db.commit()
    return {"messages": _chat_out([reply], db)}

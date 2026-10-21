"""/api/inspection-reports - the drafts, for the office to review (Dispatch -> Reports).

Dispatch's access rule (`dispatch.api.VIEW`): ADMIN and unrestricted DISPATCHER; a technician or
a restricted user is refused 403. A report on a board hidden from the user (pipeline
permissions) answers 404, as an id that does not exist. Approving records who checked it -
NOTHING is sent to the customer. Rebuild is ADMIN only.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import attachments, auth
from ..db import get_db
from ..dispatch.api import VIEW, _hidden
from ..models import InspectionReport, Role
from . import pdf, service

router = APIRouter(prefix="/api/inspection-reports", tags=["inspection-reports"])
STATES = ("building", "queued", "draft", "approved", "discarded", "failed")


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _get(db: Session, principal: auth.Principal, report_id: int) -> InspectionReport:
    r = db.get(InspectionReport, report_id)
    if r is None or (r.board and r.board in _hidden(db, principal)):
        raise HTTPException(404, "report not found")
    return r


def _out(r: InspectionReport, *, full: bool) -> dict:
    out = {"id": r.id, "job_uid": r.job_uid, "job_number": r.job_number,
           "customer_name": r.customer_name, "address": r.address, "board": r.board,
           "visit_at": _iso(r.visit_at), "technician": r.technician, "state": r.state,
           "title": r.title, "language": r.language, "error": r.error,
           "photo_count": len(r.photos or []), "created_at": _iso(r.created_at),
           "approved_at": _iso(r.approved_at)}
    if full:
        url = "/api/inspection-reports/%d/photos/%%s" % r.id
        out.update(summary=r.summary, findings=r.findings or [],
                   photos=[dict(p, url=url % p["sha"]) for p in (r.photos or [])],
                   candidates=[dict(p, url=url % p["sha"]) for p in (r.candidates or [])],
                   model=r.model, pdf_url="/api/inspection-reports/%d/report.pdf" % r.id)
    return out


@router.get("")
def inspection_reports_list(state: str | None = Query(None), db: Session = Depends(get_db),
                            principal: auth.Principal = VIEW):
    hidden = _hidden(db, principal)
    q = select(InspectionReport).order_by(InspectionReport.created_at.desc()).limit(200)
    if state:
        q = q.where(InspectionReport.state == state)
    return {"reports": [_out(r, full=False) for r in db.scalars(q)
                        if not (r.board and r.board in hidden)]}


@router.get("/{report_id}")
def inspection_report_get(report_id: int, db: Session = Depends(get_db),
                          principal: auth.Principal = VIEW):
    return _out(_get(db, principal, report_id), full=True)


class PhotoIn(BaseModel):
    sha: str = Field(min_length=64, max_length=64)
    caption: str = Field("", max_length=200)


class ReportIn(BaseModel):
    title: str | None = Field(None, max_length=200)
    summary: str | None = Field(None, max_length=3000)
    findings: list[str] | None = None
    photos: list[PhotoIn] | None = None


@router.patch("/{report_id}")
def inspection_report_edit(report_id: int, body: ReportIn, db: Session = Depends(get_db),
                           principal: auth.Principal = VIEW):
    r = _get(db, principal, report_id)
    if r.state != "draft":
        raise HTTPException(409, "Only a draft can be edited (this one is %s)." % r.state)
    if body.title is not None:
        r.title = body.title.strip() or None
    if body.summary is not None:
        r.summary = body.summary.strip() or None
    if body.findings is not None:
        r.findings = [" ".join(f.split())[:300] for f in body.findings if f and f.strip()][:12]
    if body.photos is not None:
        known = {p["sha"]: p for p in (r.candidates or []) + (r.photos or [])}
        if any(p.sha not in known for p in body.photos):
            raise HTTPException(400, "A picture that is not part of this report was sent.")
        if not body.photos:
            raise HTTPException(400, "A report needs at least one picture.")
        r.photos = [{"sha": p.sha, "caption": " ".join(p.caption.split())[:200],
                     "taken_at": known[p.sha].get("taken_at")} for p in body.photos[:12]]
    r.updated_at = datetime.now(UTC)
    db.commit()
    return _out(r, full=True)


@router.post("/{report_id}/approve")
def inspection_report_approve(report_id: int, db: Session = Depends(get_db),
                              principal: auth.Principal = VIEW):
    r = _get(db, principal, report_id)
    if r.state != "draft":
        raise HTTPException(409, "Only a draft can be approved (this one is %s)." % r.state)
    now = datetime.now(UTC)
    r.state, r.approved_at, r.approved_by_id, r.updated_at = "approved", now, \
        principal.user_id, now
    service.close_item(db, r, now, "Report approved. Nothing was sent to the customer.")
    db.commit()
    return _out(r, full=True)


@router.post("/{report_id}/discard")
def inspection_report_discard(report_id: int, db: Session = Depends(get_db),
                              principal: auth.Principal = VIEW):
    r = _get(db, principal, report_id)
    if r.state not in ("draft", "failed"):
        raise HTTPException(409, "This report is %s." % r.state)
    now = datetime.now(UTC)
    r.state, r.updated_at = "discarded", now
    service.close_item(db, r, now, "Report discarded.")
    db.commit()
    return _out(r, full=True)


@router.post("/{report_id}/rebuild")
def inspection_report_rebuild(report_id: int, db: Session = Depends(get_db),
                              principal: auth.Principal = VIEW):
    if principal.role is not Role.ADMIN:
        raise HTTPException(403, "only an admin can rebuild a report")
    r = _get(db, principal, report_id)
    if r.state not in ("draft", "failed"):
        raise HTTPException(409, "This report is %s." % r.state)
    r.state, r.error, r.updated_at = "queued", None, datetime.now(UTC)
    db.commit()
    return _out(r, full=True)


@router.get("/{report_id}/photos/{sha}")
def inspection_report_photo(report_id: int, sha: str, db: Session = Depends(get_db),
                            principal: auth.Principal = VIEW) -> Response:
    r = _get(db, principal, report_id)
    mine = {p.get("sha") for p in (r.photos or []) + (r.candidates or [])}
    if sha not in mine or len(sha) != 64:
        raise HTTPException(404, "picture not found")
    data = attachments.read("%s/%s" % (sha[:2], sha))
    if data is None or attachments.sniff(data) != "image/jpeg":
        raise HTTPException(404, "picture not found")
    return Response(data, media_type="image/jpeg", headers={
        "Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


@router.get("/{report_id}/report.pdf")
def inspection_report_pdf(report_id: int, db: Session = Depends(get_db),
                          principal: auth.Principal = VIEW) -> Response:
    r = _get(db, principal, report_id)
    if r.state not in ("draft", "approved"):
        raise HTTPException(409, "This report is %s." % r.state)
    name = "inspection-%s.pdf" % (r.job_number or r.id)
    return Response(pdf.build(r), media_type="application/pdf", headers={
        "Content-Disposition": 'attachment; filename="%s"' % name,
        "X-Content-Type-Options": "nosniff"})

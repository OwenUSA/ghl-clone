"""/api/owen-ai — the owner's assistant page (2026-11-02). See __init__.py.

ONE person: an ADMIN whose email is `OWEN_AI_OWNER_EMAIL`. Everyone else — other admins
included — gets 404 on every route, as for a route that does not exist. A message is saved
QUEUED and answered by the runner; the page polls the thread.
"""
from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import auth
from ..db import get_db
from ..frontdoor import config as frontdoor_config
from ..models import (
    OwenAiAlert,
    OwenAiLeadDraft,
    OwenAiMessage,
    OwenAiProposalDraft,
    OwenAiThread,
    OwenAiUpload,
    Role,
)
from ..zuper import config as zuper_config
from . import (
    alerts,
    changes,
    config,
    infographics,
    leads,
    pictures,
    proposals,
    report,
    report_attach,
    review,
    saved_reports,
    uploads,
)

router = APIRouter(prefix="/api/owen-ai", tags=["owen-ai"])
NY = ZoneInfo("America/New_York")
KINDS = ("daily", "job", "task")          # the owner creates these
LISTED = (*KINDS, "audit")                # audit threads are made by the sweep


def owner(principal: auth.Principal | None = Depends(auth.current_principal)) -> auth.Principal:
    if (principal is None or principal.role != Role.ADMIN or not config.owner_email()
            or (principal.email or "").strip().lower() != config.owner_email()):
        raise HTTPException(404, "Not Found")
    return principal


OWNER = Depends(owner)


def _iso(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def _thread(th: OwenAiThread, busy: bool = False) -> dict:
    return {"id": th.id, "kind": th.kind, "title": th.title, "job_uid": th.job_uid,
            "job_number": th.job_number, "day": th.day.isoformat() if th.day else None,
            "archived": th.archived, "busy": busy, "parent_id": th.parent_id,
            "created_at": _iso(th.created_at),
            "updated_at": _iso(th.updated_at)}


def _message(m: OwenAiMessage, pics: list[OwenAiUpload] | None = None) -> dict:
    return {"id": m.id, "role": m.role, "content": m.content, "status": m.status,
            "kind": m.kind, "job_uid": m.job_uid,
            "uploads": [uploads.view(u) for u in pics or []],
            "steps": m.steps or [], "error": m.error, "model": m.model,
            "cost_usd": m.cost_usd, "duration_ms": m.duration_ms, "num_turns": m.num_turns,
            "created_at": _iso(m.created_at), "finished_at": _iso(m.finished_at)}


def _busy_ids(db: Session) -> set[int]:
    return set(db.scalars(select(OwenAiMessage.thread_id).where(
        OwenAiMessage.status.in_(("queued", "running")))))


def _own(db: Session, p: auth.Principal, thread_id: int) -> OwenAiThread:
    th = db.get(OwenAiThread, thread_id)
    if th is None or th.user_id != p.user_id:
        raise HTTPException(404, "Not Found")
    return th


@router.get("/status")
def status(p: auth.Principal = OWNER, db: Session = Depends(get_db)) -> dict:
    last = db.scalars(select(OwenAiMessage).where(OwenAiMessage.role == "assistant")
                      .order_by(OwenAiMessage.id.desc()).limit(1)).first()
    return {"enabled": config.enabled(), "claude_login": config.token_set(),
            "zuper": zuper_config.env_enabled() and zuper_config.key_set(),
            "quo": bool(config.quo_key()), "frontdoor": frontdoor_config.ready(),
            "model": config.model() or "plan default",
            "last_answer_at": _iso(last.finished_at) if last else None,
            "sentence": None if config.enabled() and config.token_set() else (
                config.OFF_SENTENCE if not config.enabled() else config.NO_TOKEN_SENTENCE)}


@router.get("/threads")
def threads(kind: str | None = None, archived: bool = False, p: auth.Principal = OWNER,
            db: Session = Depends(get_db)) -> list[dict]:
    q = select(OwenAiThread).where(OwenAiThread.user_id == p.user_id,
                                   OwenAiThread.archived.is_(archived))
    if kind in LISTED:
        q = q.where(OwenAiThread.kind == kind)
    busy = _busy_ids(db)
    return [_thread(t, t.id in busy) for t in
            db.scalars(q.order_by(OwenAiThread.updated_at.desc()).limit(300))]


class NewThread(BaseModel):
    kind: str = "task"
    title: str | None = Field(None, max_length=200)
    job_uid: str | None = Field(None, max_length=64)
    job_number: str | None = Field(None, max_length=40)


@router.post("/threads")
def create_thread(body: NewThread, p: auth.Principal = OWNER,
                  db: Session = Depends(get_db)) -> dict:
    """daily: today's (one per day, found or made). job: that job's (found or made, by uid or
    number). task: always a new one."""
    if body.kind not in KINDS:
        raise HTTPException(422, "kind must be one of %s" % ", ".join(KINDS))
    mine = select(OwenAiThread).where(OwenAiThread.user_id == p.user_id,
                                      OwenAiThread.kind == body.kind)
    th = None
    if body.kind == "daily":
        today = datetime.now(NY).date()
        th = db.scalars(mine.where(OwenAiThread.day == today)).first()
        if th is None:
            th = OwenAiThread(user_id=p.user_id, kind="daily", day=today,
                              title="Daily — %s %d" % (today.strftime("%a %b"), today.day))
    elif body.kind == "job":
        uid = (body.job_uid or "").strip() or None
        num = (body.job_number or "").strip().lstrip("#") or None
        if not uid and not num:
            raise HTTPException(422, "a job conversation needs job_uid or job_number")
        if uid:
            th = db.scalars(mine.where(OwenAiThread.job_uid == uid)).first()
        if th is None and num:
            th = db.scalars(mine.where(OwenAiThread.job_number == num)).first()
        if th is None:
            th = OwenAiThread(user_id=p.user_id, kind="job", job_uid=uid, job_number=num,
                              title=(body.title or "").strip()[:200] or "Job #%s" % (num or uid))
    else:
        th = OwenAiThread(user_id=p.user_id, kind="task",
                          title=(body.title or "").strip()[:200] or "New chat")
    if th.archived:
        th.archived = False
    if th.id is None:
        db.add(th)
    db.commit()
    db.refresh(th)
    return _thread(th, th.id in _busy_ids(db))


@router.get("/threads/{thread_id}")
def thread(thread_id: int, p: auth.Principal = OWNER, db: Session = Depends(get_db)) -> dict:
    th = _own(db, p, thread_id)
    msgs = db.scalars(select(OwenAiMessage).where(OwenAiMessage.thread_id == th.id)
                      .order_by(OwenAiMessage.id)).all()
    busy = any(m.status in ("queued", "running") for m in msgs)
    pics = uploads.of_messages(db, [m.id for m in msgs if m.role == "user"])
    return {**_thread(th, busy), "messages": [_message(m, pics.get(m.id)) for m in msgs],
            # Zuper change lists proposed in this chat (2026-11-21), drawn as cards.
            "changes": [changes.out(r) for r in changes.for_thread(db, th.id)]}


class EditThread(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    archived: bool | None = None


@router.patch("/threads/{thread_id}")
def edit_thread(thread_id: int, body: EditThread, p: auth.Principal = OWNER,
                db: Session = Depends(get_db)) -> dict:
    th = _own(db, p, thread_id)
    if body.title is not None:
        th.title = body.title.strip()[:200]
    if body.archived is not None:
        th.archived = body.archived
    db.commit()
    return _thread(th, th.id in _busy_ids(db))


class NewMessage(BaseModel):
    content: str = Field("", max_length=20_000)
    # Pictures uploaded to this chat first (POST /threads/{id}/uploads), sent with the text.
    upload_ids: list[int] = Field(default_factory=list, max_length=uploads.MAX_PER_MESSAGE)


@router.post("/threads/{thread_id}/messages")
def send(thread_id: int, body: NewMessage, p: auth.Principal = OWNER,
         db: Session = Depends(get_db)) -> dict:
    th = _own(db, p, thread_id)
    if not config.enabled():
        raise HTTPException(409, config.OFF_SENTENCE)
    pending = db.scalar(select(func.count()).select_from(OwenAiMessage).where(
        OwenAiMessage.thread_id == th.id, OwenAiMessage.status.in_(("queued", "running"))))
    if pending:
        raise HTTPException(409, "Owen AI is still answering in this conversation.")
    text = body.content.strip()
    if not text and not body.upload_ids:
        raise HTTPException(422, "Write a message or add a picture.")
    now = datetime.now(UTC)
    m = OwenAiMessage(thread_id=th.id, role="user", content=text or "(pictures)",
                      status="queued", created_at=now)
    db.add(m)
    db.flush()
    try:
        pics = uploads.attach(db, th.id, m.id, body.upload_ids)
    except uploads.Invalid as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from None
    if th.kind == "task" and th.title == "New chat":
        first = " ".join(body.content.split())
        th.title = first[:config.TITLE_MAX] + ("…" if len(first) > config.TITLE_MAX else "")
    th.updated_at = now
    db.commit()
    db.refresh(m)
    return _message(m, pics)


@router.post("/threads/{thread_id}/uploads")
async def upload_picture(thread_id: int, file: UploadFile, p: auth.Principal = OWNER,
                         db: Session = Depends(get_db)) -> dict:
    """A picture Owen drops in the composer (2026-11-21): JPEG, PNG, WebP or HEIC, 15 MB. It
    waits here until he sends the message with its id. Nothing reaches Zuper from here."""
    th = _own(db, p, thread_id)
    data = await file.read(uploads.MAX_BYTES + 1)
    try:
        return uploads.view(uploads.save(db, th.id, file.filename, data))
    except uploads.Invalid as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/uploads/{upload_id}")
def upload_file(upload_id: int, p: auth.Principal = OWNER,
                db: Session = Depends(get_db)) -> FileResponse:
    row = db.get(OwenAiUpload, upload_id)
    if row is None:
        raise HTTPException(404, "Not Found")
    _own(db, p, row.thread_id)
    path = uploads.path_of(row)
    if not path.is_file():
        raise HTTPException(404, "Not Found")
    return FileResponse(path, media_type=row.content_type,
                        headers={"Cache-Control": "private, max-age=86400",
                                 "X-Content-Type-Options": "nosniff"})


# ------------------------------------------------------------------------------- alerts

@router.get("/alerts")
def list_alerts(status: str = "open", job_uid: str | None = None, p: auth.Principal = OWNER,
                db: Session = Depends(get_db)) -> dict:
    q = select(OwenAiAlert)
    if status in ("open", "resolved", "dismissed"):
        q = q.where(OwenAiAlert.status == status)
    if job_uid:
        q = q.where(OwenAiAlert.job_uid == job_uid)
    rows = list(db.scalars(q.order_by(OwenAiAlert.last_seen_at.desc()).limit(500)))
    rows.sort(key=lambda a: alerts.RANK.get(a.severity, 9))
    counts = dict.fromkeys(alerts.SEVERITIES, 0)
    for a in db.scalars(select(OwenAiAlert).where(OwenAiAlert.status == "open")):
        counts[a.severity] = counts.get(a.severity, 0) + 1
    return {"alerts": [alerts.out(a) for a in rows], "open_counts": counts}


class Dismiss(BaseModel):
    why: str = Field(min_length=1, max_length=1000)


@router.post("/alerts/{alert_id}/dismiss")
def dismiss_alert(alert_id: int, body: Dismiss, p: auth.Principal = OWNER,
                  db: Session = Depends(get_db)) -> dict:
    """The owner says it is not a problem (or handled). The next audit may raise it again only
    as a NEW alert if the problem is really still there."""
    a = db.get(OwenAiAlert, alert_id)
    if a is None:
        raise HTTPException(404, "Not Found")
    if a.status == "open":
        alerts.close(db, a, "dismissed", "Dismissed by %s: %s" % (p.name or p.email, body.why))
        db.commit()
    return alerts.out(a)


# ------------------------------------------------------------------------------- reports

@router.get("/reports")
def list_reports(job_uid: str | None = None, p: auth.Principal = OWNER) -> dict:
    """The inspection reports make_inspection_report saved (newest first), for one job or all."""
    return {"reports": report.list_reports((job_uid or "").strip() or None)}


@router.get("/reports/{report_id}/{file}")
def report_file(report_id: str, file: str, p: auth.Principal = OWNER) -> FileResponse:
    """One report's report.html or report.pdf, as a download named for the customer. Any other
    name, a malformed id or a path outside the reports folder is 404."""
    path = report.file_path(report_id, file)
    if path is None:
        raise HTTPException(404, "Not Found")
    meta = report.read_meta(report_id) or {"job_number": report_id.split("-", 1)[0]}
    name = report.friendly_name(meta, file)          # ASCII letters, digits, spaces, - and '
    return FileResponse(path, media_type=report.FILES[file], headers={
        "Content-Disposition": "attachment; filename=\"%s\"; filename*=utf-8''%s"
                               % (name, quote(name)),
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/reviews/{review_id}/{file}")
def review_file(review_id: str, file: str, p: auth.Principal = OWNER) -> FileResponse:
    """A customer review's Excel (review.py). Any other name or id is 404."""
    path = review.file_path(review_id, file)
    if path is None:
        raise HTTPException(404, "Not Found")
    name = "Customer review %s.xlsx" % review_id
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument."
                                         "spreadsheetml.sheet", headers={
        "Content-Disposition": "attachment; filename=\"%s\"; filename*=utf-8''%s"
                               % (name, quote(name)),
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


# ------------------------------------------------- saved inspection reports (2026-11-12)
#
# The Reports tab: every saved report, its versions, and EVERY part of it editable by Owen — a
# save is a new version (never an overwrite), restore is a save too, and a save that started
# from an older version is 409 (Owen AI or another tab saved in between). Nothing here is sent
# to anyone or written to Zuper; a picture is made only when the save names it (OpenAI only).

def _saved(db: Session, report_id: int):
    try:
        return saved_reports.get(db, report_id)
    except saved_reports.NotFound:
        raise HTTPException(404, "Not Found") from None


def _spec_error(exc: report.SpecError) -> HTTPException:
    # One sentence (the page shows a string `detail` as it is): every problem, with where.
    return HTTPException(422, "Fix these before saving: " + "; ".join(exc.problems))


@router.get("/inspection-reports")
def list_saved_reports(job_uid: str | None = None, p: auth.Principal = OWNER,
                       db: Session = Depends(get_db)) -> dict:
    return {"reports": saved_reports.list_out(db, (job_uid or "").strip() or None),
            "pictures_enabled": config.report_pictures_enabled(),
            "attach_enabled": report_attach.refusal(db) is None,
            "picture_cost_usd": infographics.EST_COST}


@router.get("/inspection-reports/{report_id}")
def get_saved_report(report_id: int, p: auth.Principal = OWNER,
                     db: Session = Depends(get_db)) -> dict:
    return {**saved_reports.out(db, _saved(db, report_id), with_spec=True),
            "pictures_enabled": config.report_pictures_enabled(),
            "attach_enabled": report_attach.refusal(db) is None,
            "picture_cost_usd": infographics.EST_COST}


@router.get("/inspection-reports/{report_id}/versions/{number}")
def get_saved_version(report_id: int, number: int, p: auth.Principal = OWNER,
                      db: Session = Depends(get_db)) -> dict:
    _saved(db, report_id)
    try:
        v = saved_reports.version(db, report_id, number)
    except saved_reports.NotFound:
        raise HTTPException(404, "Not Found") from None
    return {**saved_reports.version_out(v), "spec": v.spec}


class SaveReport(BaseModel):
    base_version: int = Field(ge=1)
    spec: dict
    note: str = Field("", max_length=300)
    regenerate: list[str] = Field(default_factory=list, max_length=3)
    refresh_facts: bool = False


def _save_call(fn, db: Session, report_id: int):
    _saved(db, report_id)
    try:
        return fn()
    except report.SpecError as exc:
        raise _spec_error(exc) from None
    except saved_reports.Conflict as exc:
        raise HTTPException(409, str(exc)) from None
    except saved_reports.NotFound:
        raise HTTPException(404, "Not Found") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/inspection-reports/{report_id}/versions")
def save_report(report_id: int, body: SaveReport, p: auth.Principal = OWNER,
                db: Session = Depends(get_db)) -> dict:
    """Save Owen's changes as a new version. `regenerate`: option names whose AI picture to make
    again (paid; only when Owen pressed it). 409 when the report moved past `base_version`."""
    bad = [x for x in body.regenerate if x.upper() not in report.TIERS]
    if bad:
        raise HTTPException(422, "regenerate takes GOOD, BETTER or BEST")
    v = _save_call(lambda: saved_reports.update(
        db, report_id, saved_reports.load_spec(report_id, body.spec), body.base_version,
        source="page", author=p.name or p.email or "the owner", note=body.note,
        regenerate=body.regenerate, refresh_facts=body.refresh_facts), db, report_id)
    return saved_reports.version_out(v)


class Restore(BaseModel):
    version: int = Field(ge=1)
    base_version: int = Field(ge=1)


@router.post("/inspection-reports/{report_id}/restore")
def restore_report(report_id: int, body: Restore, p: auth.Principal = OWNER,
                   db: Session = Depends(get_db)) -> dict:
    """Bring an old version back — as a NEW version, so nothing is lost either way."""
    v = _save_call(lambda: saved_reports.restore(
        db, report_id, body.version, body.base_version,
        author=p.name or p.email or "the owner"), db, report_id)
    return saved_reports.version_out(v)


class Preview(BaseModel):
    spec: dict


@router.post("/inspection-reports/{report_id}/preview")
def preview_report(report_id: int, body: Preview, p: auth.Principal = OWNER,
                   db: Session = Depends(get_db)) -> dict:
    """The page as it would look with these changes. Saves nothing, makes no picture."""
    html = _save_call(lambda: saved_reports.preview(
        db, report_id, saved_reports.load_spec(report_id, body.spec)), db, report_id)
    return {"html": html}


@router.post("/inspection-reports/{report_id}/uploads")
async def upload_report_picture(report_id: int, file: UploadFile, p: auth.Principal = OWNER,
                                db: Session = Depends(get_db)) -> dict:
    """Owen's own picture for an option (JPEG/PNG, 8 MB). Returns the name to put in the
    option's `picture_upload`; it shows on the report once he saves."""
    _saved(db, report_id)
    data = await file.read(saved_reports.UPLOAD_MAX_BYTES + 1)
    try:
        return {"name": saved_reports.save_upload(report_id, data)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


class AttachReport(BaseModel):
    proposal: str | None = Field(None, max_length=20)
    version: int | None = Field(None, ge=1)


@router.post("/inspection-reports/{report_id}/attach")
def attach_report(report_id: int, body: AttachReport, p: auth.Principal = OWNER,
                  db: Session = Depends(get_db)) -> dict:
    """Put this report's PDF (the latest version unless `version`) on its DRAFT proposal in
    Zuper (the linked DTR number unless `proposal`): one upload + one attach, read back, never
    sent (report_attach.py). 409 with a sentence when refused - nothing was written then."""
    _saved(db, report_id)
    try:
        res = report_attach.attach(db, report_id, p.email or "the owner", body.proposal,
                                   body.version)
    except report_attach.Refused as exc:
        raise HTTPException(409, str(exc)) from None
    except saved_reports.NotFound:
        raise HTTPException(404, "Not Found") from None
    return {**res, "message": report_attach.sentence(res)}


@router.get("/inspection-reports/{report_id}/files/{folder}/{name}")
def saved_report_file(report_id: int, folder: str, name: str, download: bool = False,
                      p: auth.Principal = OWNER,
                      db: Session = Depends(get_db)) -> FileResponse:
    """lib/<photo>, pictures/<picture> or v<n>/report.html|pdf of this report. Anything else,
    or a path outside the report's folder, is 404. The HTML opens in the browser unless
    `download` (the "Download HTML" button, 2026-11-13); its photos are embedded, so the file
    works on its own."""
    row = _saved(db, report_id)
    path = saved_reports.file_path(report_id, "%s/%s" % (folder, name))
    if path is None:
        raise HTTPException(404, "Not Found")
    headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
    if name in report.FILES:
        meta = {"job_number": row.job_number,
                "customer": row.customer}
        fname = report.friendly_name(meta, name)
        inline = name.endswith(".html") and not download
        headers["Content-Disposition"] = ("inline" if inline else "attachment") \
            + "; filename=\"%s\"; filename*=utf-8''%s" % (fname, quote(fname))
        return FileResponse(path, media_type=report.FILES[name], headers=headers)
    media = "image/png" if name.lower().endswith(".png") else "image/jpeg"
    return FileResponse(path, media_type=media, headers=headers)


# ------------------------------------------------------------------------------ proposals

@router.get("/proposals")
def list_proposals(status: str | None = None, p: auth.Principal = OWNER,
                   db: Session = Depends(get_db)) -> dict:
    """Owen AI's proposal drafts (2026-11-09): pending first, then newest."""
    q = select(OwenAiProposalDraft)
    if status in proposals.STATUSES:
        q = q.where(OwenAiProposalDraft.status == status)
    rows = list(db.scalars(q.order_by(OwenAiProposalDraft.id.desc()).limit(300)))
    rows.sort(key=lambda r: r.status != "pending")
    pending = db.scalar(select(func.count()).select_from(OwenAiProposalDraft).where(
        OwenAiProposalDraft.status == "pending")) or 0
    why = pictures.refusal(db)
    return {"proposals": [proposals.out(r) for r in rows], "pending": pending,
            "enabled": proposals.refusal(db) is None, "sentence": proposals.refusal(db),
            "pictures_enabled": why is None, "pictures_sentence": why}


def _draft(db: Session, draft_id: int) -> OwenAiProposalDraft:
    row = db.get(OwenAiProposalDraft, draft_id)
    if row is None:
        raise HTTPException(404, "Not Found")
    return row


@router.get("/proposals/{draft_id}")
def get_proposal(draft_id: int, p: auth.Principal = OWNER, db: Session = Depends(get_db)) -> dict:
    return proposals.out(_draft(db, draft_id))


@router.post("/proposals/{draft_id}/approve")
def approve_proposal(draft_id: int, p: auth.Principal = OWNER,
                     db: Session = Depends(get_db)) -> dict:
    """Create it in Zuper as a DRAFT (or fill the empty draft), read it back. Never sends.
    Refused (409, nothing sent) unless it is pending, OWEN_AI_PROPOSALS_ENABLED is on and the
    registry allows it. A Zuper failure is a 200 with status "failed" and Zuper's message."""
    _draft(db, draft_id)
    try:
        row = proposals.approve(db, draft_id, p.name or p.email or "the owner")
    except proposals.Refused as exc:
        raise HTTPException(409, str(exc)) from None
    except LookupError:
        raise HTTPException(404, "Not Found") from None
    return proposals.out(row)


class Reject(BaseModel):
    why: str = Field(min_length=1, max_length=1000)


@router.post("/proposals/{draft_id}/reject")
def reject_proposal(draft_id: int, body: Reject, p: auth.Principal = OWNER,
                    db: Session = Depends(get_db)) -> dict:
    """Writes nothing to Zuper."""
    _draft(db, draft_id)
    try:
        row = proposals.reject(db, draft_id, p.name or p.email or "the owner", body.why)
    except proposals.Refused as exc:
        raise HTTPException(409, str(exc)) from None
    return proposals.out(row)


# ------------------------------------------------------------------------- new leads (2026-11-19)

@router.get("/leads")
def list_leads(p: auth.Principal = OWNER, db: Session = Depends(get_db)) -> dict:
    """Owen AI's new leads (customer + job): pending first, then newest."""
    rows = list(db.scalars(select(OwenAiLeadDraft).order_by(OwenAiLeadDraft.id.desc())
                           .limit(200)))
    rows.sort(key=lambda r: r.status != "pending")
    why = leads.refusal(db)
    return {"leads": [leads.out(r) for r in rows],
            "pending": sum(1 for r in rows if r.status == "pending"),
            "enabled": why is None, "sentence": why}


def _lead(db: Session, lead_id: int) -> OwenAiLeadDraft:
    row = db.get(OwenAiLeadDraft, lead_id)
    if row is None:
        raise HTTPException(404, "Not Found")
    return row


@router.post("/leads/{lead_id}/approve")
def approve_lead(lead_id: int, p: auth.Principal = OWNER, db: Session = Depends(get_db)) -> dict:
    """Create the customer (unless an existing one was chosen), the job in its board's first
    column and the private note in Zuper, read the job back. Nothing is sent to anyone.
    Refused (409, nothing sent) unless it is pending, OWEN_AI_LEADS_ENABLED is on and the
    registry allows it. A Zuper failure is a 200 with status "failed" saying what exists."""
    _lead(db, lead_id)
    try:
        row = leads.approve(db, lead_id, p.name or p.email or "the owner")
    except leads.Refused as exc:
        raise HTTPException(409, str(exc)) from None
    except LookupError:
        raise HTTPException(404, "Not Found") from None
    return leads.out(row)


@router.post("/leads/{lead_id}/reject")
def reject_lead(lead_id: int, body: Reject, p: auth.Principal = OWNER,
                db: Session = Depends(get_db)) -> dict:
    """Writes nothing to Zuper."""
    _lead(db, lead_id)
    try:
        row = leads.reject(db, lead_id, p.name or p.email or "the owner", body.why)
    except leads.Refused as exc:
        raise HTTPException(409, str(exc)) from None
    return leads.out(row)


# ----------------------------------------------------------------- step pictures (2026-11-09)

def _pictures_call(fn, db: Session, draft_id: int, p: auth.Principal) -> dict:
    _draft(db, draft_id)
    try:
        row = fn(db, draft_id, p.name or p.email or "the owner")
    except pictures.Refused as exc:
        raise HTTPException(409, str(exc)) from None
    except LookupError:
        raise HTTPException(404, "Not Found") from None
    return proposals.out(row)


@router.post("/proposals/{draft_id}/pictures")
def generate_pictures(draft_id: int, p: auth.Principal = OWNER,
                      db: Session = Depends(get_db)) -> dict:
    """Make the optional step-picture banners for a CREATED proposal, for preview (OpenAI only —
    nothing reaches Zuper). 409 unless created, both server switches and the registry allow it,
    and no other generation is running for this row. An OpenAI failure is a 200 with
    pictures_status "failed" and a sentence."""
    return _pictures_call(pictures.generate, db, draft_id, p)


@router.post("/proposals/{draft_id}/pictures/attach")
def attach_pictures(draft_id: int, p: auth.Principal = OWNER,
                    db: Session = Depends(get_db)) -> dict:
    """Put the previewed banners on the proposal: uploads + ONE PUT /estimate/{its uid} that
    changes only option_image, then a read-back. Never retried; never sent to the customer."""
    return _pictures_call(pictures.attach, db, draft_id, p)


@router.post("/proposals/{draft_id}/pictures/discard")
def discard_pictures(draft_id: int, p: auth.Principal = OWNER,
                     db: Session = Depends(get_db)) -> dict:
    """Throw the previews away. Writes nothing to Zuper."""
    return _pictures_call(pictures.discard, db, draft_id, p)


@router.get("/proposals/{draft_id}/pictures/{name}")
def picture_file(draft_id: int, name: str, p: auth.Principal = OWNER,
                 db: Session = Depends(get_db)) -> FileResponse:
    """One current banner of this draft (owner only). Any other name is 404."""
    path = pictures.file_path(_draft(db, draft_id), name)
    if path is None:
        raise HTTPException(404, "Not Found")
    return FileResponse(path, media_type="image/jpeg", headers={
        "Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})

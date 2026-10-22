"""The evidence for one finished visit - everything is READ, nothing is written anywhere:

  * the job's fields, as the Dispatch reader copied them (`DispatchJob.fields`);
  * the checklist answers of stage moves since the visit day, live from `GET /jobs/{uid}`
    (already on the client's read list) - written answers and photo / video links;
  * the job's notes since shortly before the visit (`DispatchNote`, copied by the reader) and
    their attachments' types, live through `zapi.job_attachments`;
  * the technician's own texts and calls back to us since we asked (`dispatch.comms`).
A Zuper read that fails leaves that part out; the check still runs on what the CRM holds.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..dispatch import comms as dcomms
from ..dispatch import config as dc
from ..models import DispatchJob, DispatchNote
from ..zuper import client, zapi
from . import config as c
from . import rules

log = logging.getLogger("inspection_docs")


def _time(value) -> datetime | None:
    if not value:
        return None
    try:
        return dc.aware(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


def gather(db: Session, j: DispatchJob, visit_at: datetime, *, tech_phone: str | None = None,
           asked_at: datetime | None = None, live: bool = True
           ) -> tuple[rules.Evidence, list[tuple[str, str]]]:
    """(evidence, written sources for the AI)."""
    ev = rules.Evidence()
    sources: list[tuple[str, str]] = []
    rules.from_fields(ev, j.fields)
    since = visit_at - timedelta(hours=c.NOTES_BEFORE_HOURS)
    day_start = dc.local(visit_at).replace(hour=0, minute=0, second=0, microsecond=0)
    if live:
        try:
            record = zapi.job(j.job_uid)
            for move in record.get("job_status") or []:
                at = _time(move.get("created_at"))
                if at is None or at < day_start:
                    continue
                texts = rules.from_checklist(ev, move.get("checklist") or [])
                if texts:
                    sources.append(("checklist (%s)" % (move.get("status_name") or "stage"),
                                    "\n".join(texts)))
        except client.ZuperError as e:
            log.info("job %s checklist not read: %s", j.job_number, e.kind)
        try:
            atts = [a for a in zapi.job_attachments(j.job_uid)
                    if (_time(a.get("created_at")) or visit_at) >= day_start]
            rules.from_attachments(ev, atts)
        except client.ZuperError as e:
            log.info("job %s attachments not read: %s", j.job_number, e.kind)
        try:
            ev.albums = zapi.job_albums(j.job_uid)
        except client.ZuperError as e:
            log.info("job %s albums not read: %s", j.job_number, e.kind)
    if ev.photos == 0 and (j.visit_photo_count or 0) > 0:
        ev.photos = j.visit_photo_count        # the reader's own count of the visit's pictures
    notes = [n for n in db.scalars(select(DispatchNote).where(
        DispatchNote.job_uid == j.job_uid, DispatchNote.removed.is_(False)))
        if (dc.aware(n.created_at) or visit_at) >= since and (n.text or "").strip()]
    for n in sorted(notes, key=lambda n: dc.aware(n.created_at) or visit_at):
        sources.append(("note by %s" % (n.by_name or "someone"), n.text))
    if tech_phone and asked_at:
        talk = dcomms.load(db, asked_at).get(tech_phone, [])
        for x in talk:
            if x.at >= asked_at and not x.out and x.kind == "text":
                sources.append(("the technician's text", x.summary))
            elif x.at >= asked_at and x.kind == "call" and (x.summary or x.transcript):
                sources.append(("the technician's call", (x.summary or "") + "\n" + (
                    x.transcript or "")))
    return ev, sources

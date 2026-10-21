"""The visit's pictures: listed from Zuper (note images and checklist photo answers since the
visit day), downloaded through `client.fetch_file`, turned into JPEG (HEIC included) and kept in
the CRM's own media store (`app/attachments.py`). Reads only; videos are skipped."""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from datetime import datetime

from PIL import Image, ImageOps

from .. import attachments
from ..dispatch import config as dc
from ..inspection_docs import rules as irules
from ..models import DispatchJob
from ..zuper import client, zapi
from . import config as c

log = logging.getLogger("inspection_report")

try:  # HEIC from iPhones; without the plugin those pictures are skipped, never a crash.
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:  # noqa: BLE001
    log.warning("pillow-heif not available: HEIC pictures will be skipped")


@dataclass
class Candidate:
    url: str
    taken_at: datetime | None
    question: str | None
    sha: str = ""
    ai_jpeg: bytes = b""


def _time(value) -> datetime | None:
    if not value:
        return None
    try:
        return dc.aware(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


def to_jpeg(data: bytes, px: int) -> bytes:
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    img.thumbnail((px, px))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    out = io.BytesIO()
    img.save(out, "JPEG", quality=c.JPEG_QUALITY, optimize=True)
    return out.getvalue()


def listed(j: DispatchJob, visit_at: datetime) -> list[Candidate]:
    """Every picture of the visit day, oldest first, de-duplicated by URL."""
    day = dc.local(visit_at).replace(hour=0, minute=0, second=0, microsecond=0)
    out: dict[str, Candidate] = {}
    try:
        record = zapi.job(j.job_uid)
        for move in record.get("job_status") or []:
            at = _time(move.get("created_at"))
            if at is None or at < day:
                continue
            for q in move.get("checklist") or []:
                if not isinstance(q, dict) or not isinstance(q.get("answer"), list):
                    continue
                question = str(q.get("question") or "")
                qtype = str(q.get("type") or "").upper()
                if "VIDEO" in qtype or "video" in question.lower():
                    continue
                for u in q["answer"]:
                    if isinstance(u, str) and u.startswith("http") and not irules.is_video(u):
                        out.setdefault(u, Candidate(u, at, question))
    except client.ZuperError as e:
        log.info("job %s checklist not read: %s", j.job_number, e.kind)
    try:
        for a in zapi.job_attachments(j.job_uid):
            u = a.get("attachment") or a.get("attachment_path") or a.get("url")
            at = _time(a.get("created_at"))
            name = str(a.get("file_name") or a.get("attachment_name") or u or "")
            if not u or (at is not None and at < day) or irules.is_video(name):
                continue
            out.setdefault(u, Candidate(u, at, None))
    except client.ZuperError as e:
        log.info("job %s attachments not read: %s", j.job_number, e.kind)
    return sorted(out.values(), key=lambda x: x.taken_at or visit_at)


def fetch(cands: list[Candidate], limit: int) -> list[Candidate]:
    """Download and store up to `limit` pictures; spread over the visit when there are more."""
    if len(cands) > limit:
        step = len(cands) / limit
        cands = [cands[int(i * step)] for i in range(limit)]
    kept = []
    for cand in cands:
        try:
            data, _ctype = client.fetch_file(cand.url)
            report = to_jpeg(data, c.REPORT_PX)
            cand.ai_jpeg = to_jpeg(report, c.AI_PX)
        except client.ZuperError as e:
            log.info("picture not downloaded: %s", e.kind)
            continue
        except Exception:  # noqa: BLE001 - an unreadable file is one picture fewer
            log.info("picture not readable, skipped")
            continue
        cand.sha, _ = attachments.store(report)
        kept.append(cand)
    return kept

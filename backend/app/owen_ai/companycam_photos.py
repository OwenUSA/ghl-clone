"""companycam_photos — Owen AI LOOKS at a job's CompanyCam photos (2026-11-12). READ-ONLY.

Why: on job #292 the evidence that mattered was only in CompanyCam — the photos inside the house
and the technician's MARKED photos ("2 old repairs the leaks is back", cracked tiles #1-#9) —
while Zuper had the plain copies. So the after-inspection report reads both.

The Zuper job -> its CRM card (zuper_mappings, crm_type "opportunity") -> the CompanyCam
projects linked to that card (companycam_links, not unlinked) -> every photo, oldest first. Each
photo is saved like job_photos does (1024 px JPEG) under photo_dir()/<job>-companycam/, numbered
from 101 so the numbers never collide with Zuper's: '101.jpg' is the plain photo and '101a.jpg'
the technician's marked copy when there is one. An index remembers which photo became which
file, so a second call downloads nothing again.

Every request goes through app/companycam.py (GET only; the token never leaves CompanyCam).
Nothing is written anywhere but the local photo folder. Off when there is no CompanyCam token.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from sqlalchemy import select

from .. import companycam
from ..models import CompanyCamLink, ZuperMapping
from . import config, photos
from .dossier import Reader, et, find_job

FIRST_NUMBER = 101
MAX_PHOTOS = 150
MAX_PAGES = 6                          # 300 photos per project at most
OFF = "CompanyCam is not set up on this server (COMPANYCAM_API_TOKEN), so there are no photos."


def _projects(job_uid: str) -> list[tuple[str, str | None]]:
    from ..db import SessionLocal
    with SessionLocal() as db:
        cards = list(db.scalars(select(ZuperMapping.crm_id).where(
            ZuperMapping.crm_type == "opportunity", ZuperMapping.zuper_uid == job_uid)))
        if not cards:
            return []
        rows = db.execute(select(CompanyCamLink.project_id, CompanyCamLink.project_name).where(
            CompanyCamLink.opportunity_id.in_(cards), CompanyCamLink.unlinked_at.is_(None))
            .order_by(CompanyCamLink.id)).all()
    seen: dict[str, str | None] = {}
    for pid, name in rows:
        seen.setdefault(str(pid), name)
    return list(seen.items())


def _list(project_id: str) -> list[dict]:
    out: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        rows = companycam.photo_page(project_id, page)
        if not rows:
            break
        out += rows
    out.sort(key=lambda p: (companycam._unix(p.get("captured_at"))
                            or companycam._unix(p.get("created_at")) or 0))
    return out


def _download(uri: str) -> bytes:
    data, _ = companycam.fetch_image(uri)
    return photos.to_jpeg(data)


def gather(job: str, max_photos: int = MAX_PHOTOS) -> dict:
    if not companycam.enabled():
        raise LookupError(OFF)
    rec = find_job(Reader(), job)
    number = str(rec.get("work_order_number") or rec["job_uid"][:8])
    projects = _projects(str(rec["job_uid"]))
    if not projects:
        return {"job": number, "projects": [], "photos": [],
                "note": "No CompanyCam project is linked to this job's CRM card."}
    folder = config.photo_dir() / ("%s-companycam" % photos._safe_folder(number))
    folder.mkdir(parents=True, exist_ok=True)
    idx_file = folder / "index.json"
    try:
        index = json.loads(idx_file.read_text("utf-8"))
    except (OSError, ValueError):
        index = {}
    files: dict[str, str] = index.get("files") if isinstance(index.get("files"), dict) else {}
    nxt = max([int(Path(v).stem.rstrip("a")) for v in files.values() if v[:3].isdigit()]
              + [FIRST_NUMBER - 1])
    picked: list[dict[str, Any]] = []
    for pid, name in projects:
        for p in _list(pid):
            if len(picked) >= max_photos:
                break
            picked.append({"project": name or pid, "photo": p})
    todo = []
    for item in picked:
        p = item["photo"]
        key = str(p["id"])
        if key not in files:
            nxt += 1
            files[key] = "%03d.jpg" % nxt
        item["file"] = files[key]
        plain = companycam._uri(p, "web")
        marked = companycam._uri(p, "web_annotation")
        item["uris"] = (plain, marked)
        for uri, name in ((plain, item["file"]),
                          (marked, item["file"].replace(".jpg", "a.jpg"))):
            if uri and not (folder / name).is_file():
                todo.append((uri, folder / name))

    def work(job_: tuple[str, Path]) -> str | None:
        uri, path = job_
        try:
            path.write_bytes(_download(uri))
            return None
        except (companycam.CompanyCamError, OSError, ValueError) as exc:
            return "%s: %s" % (path.name, exc)
    with ThreadPoolExecutor(max_workers=photos.WORKERS) as pool:
        failed = [x for x in pool.map(work, todo) if x]
    idx_file.write_text(json.dumps({"files": files}, indent=1), "utf-8")
    out = []
    for item in picked:
        p = item["photo"]
        plain = folder / item["file"]
        marked = folder / item["file"].replace(".jpg", "a.jpg")
        if not plain.is_file() and not marked.is_file():
            continue
        row: dict[str, Any] = {
            "path": str(plain) if plain.is_file() else None,
            "at": et(companycam.iso_from_unix(p.get("captured_at"))),
            "by": p.get("creator_name"), "project": item["project"]}
        if marked.is_file():
            row["marked_path"] = str(marked)
        if p.get("description"):
            row["description"] = photos.short(p.get("description"), photos.CAPTION_MAX)
        out.append({k: v for k, v in row.items() if v})
    res = {"job": number, "projects": [n or pid for pid, n in projects], "folder": str(folder),
           "count": len(out), "marked": sum(1 for x in out if "marked_path" in x),
           "photos": out,
           "next": "Use the Read tool on each `path` (and `marked_path`: the technician's "
                   "own circles and numbers) to look at the pictures."}
    if failed:
        res["failed"] = failed[:20]
    return res


def companycam_photos(job: str, max_photos: int = MAX_PHOTOS) -> str:
    from ..zuper import client as zc
    try:
        return json.dumps(gather(job, max(1, min(int(max_photos or MAX_PHOTOS), MAX_PHOTOS))),
                          ensure_ascii=False, default=str)
    except (LookupError, ValueError) as exc:
        return "ERROR: %s" % exc
    except companycam.CompanyCamError as exc:
        return "ERROR from CompanyCam: %s" % exc
    except zc.ZuperError as exc:
        return "ERROR from Zuper: %s" % zc.sentence(exc)


SPECS = ((
    "companycam_photos",
    ("LOOK at one job's CompanyCam photos (read-only): the photos the crew took in CompanyCam, "
     "INCLUDING the technician's marked copies (circles, numbers, notes written on the photo) "
     "and photos inside the house, which Zuper often does not have. `job` = job number or uid. "
     "Saves each photo as a 1024 px JPEG numbered from 101 (101.jpg plain, 101a.jpg marked) and "
     "returns JSON with each `path`, `marked_path`, time, who and description. THEN use the "
     "Read tool on the files. Use it with job_photos for every inspection report."),
    {"type": "object", "properties": {"job": {"type": "string"},
                                      "max_photos": {"type": "integer"}},
     "required": ["job"]},
    lambda a: companycam_photos(str(a["job"]), a.get("max_photos") or MAX_PHOTOS)),)

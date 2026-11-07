"""job_photos — Owen AI LOOKS at a Zuper job's pictures (2026-11-06). READ-ONLY.

Where a job's pictures are (the same three places proposal-photo-audit/audit.py read live):

  GET /attachments/folders?filter.module=JOB&filter.module_uid=<uid>   albums + their counts
  GET /attachments/group?filter.module=JOB&filter.module_uid=<uid>     the job's gallery, paged;
      + &filter.folder_uid=<f>   one album — trusted only when its total equals the album count
  GET /jobs/{uid}/note           a note's attachments[] (technicians' field pictures)
  job_status[].checklist[].answer   photo answers are lists of https URLs

The selection is pure (`select`): `inspection` = photos up to the first proposal, nearest the
inspection first; `repair` = photos after the accepted proposal; `all` = everything. Within
the window, a picture whose album / note / name says leak, failure point, roof, damage... comes
first. Each picked picture is fetched with `client.fetch_file` (no Zuper write is possible
there), downscaled to 1024 px JPEG q80 into `config.photo_dir()/<job number>/NNN.jpg`, and an
`index.json` remembers which source became which file so a second call fetches nothing again.
Claude then opens the files with its Read tool (agent.py passes the folder as an --add-dir).
Folders untouched for 14 days are removed at the start of every call.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..zuper import client as zc
from ..zuper.mapping import parse_time
from . import config
from .dossier import Reader, et, find_job, person, plain, short

with contextlib.suppress(Exception):  # HEIC from iPhones; without it those are just "failed"
    from pillow_heif import register_heif_opener
    register_heif_opener()

MAX_PHOTOS = 24
HARD_MAX = 40
SIDE = 1024
QUALITY = 80
KEEP_DAYS = 14
CAPTION_MAX = 120
GALLERY_PAGES = 10
ALBUM_PAGES = 10
WORKERS = 4
WHICH = ("inspection", "repair", "all")

INSPECTION_WORDS = re.compile(
    r"inspect|leak|fail|damage|problem|cause|before|roof|stain|crack|missing|deck", re.IGNORECASE)
REPAIR_WORDS = re.compile(r"after|repair|complete|finish|done|final|install|fixed", re.IGNORECASE)
IMAGE_EXT = re.compile(r"\.(jpe?g|png|gif|webp|heic|heif|bmp|tiff?)(\?|$)", re.IGNORECASE)
VIDEO_EXT = re.compile(r"\.(mp4|mov|m4v|avi|webm|3gp)(\?|$)", re.IGNORECASE)
URL = re.compile(r"https://[^\s\"',\]]+")


# ------------------------------------------------------------------------------ gathering

def _is_image(mime: Any, url: Any) -> bool:
    mime = str(mime or "").lower()
    if mime.startswith("image/"):
        return True
    if mime.startswith("video/") or VIDEO_EXT.search(str(url or "")):
        return False
    return bool(IMAGE_EXT.search(str(url or "")))


def _pages(r: Reader, params: dict, max_pages: int) -> tuple[list[dict], Any]:
    items, page, total = [], 1, None
    while True:
        payload = r.get("/attachments/group", {**params, "page": page})
        total = payload.get("total_records") if isinstance(payload, dict) else None
        data = zc.data_of(payload)
        groups = data if isinstance(data, list) else []
        for g in groups:
            if isinstance(g, dict):
                items += [a for a in g.get("attachments") or [] if isinstance(a, dict)]
        pages = (payload.get("total_pages") if isinstance(payload, dict) else None) or 1
        if page >= pages or not groups or page >= max_pages:
            return items, total
        page += 1


def gather(r: Reader, job: dict) -> tuple[list[dict], dict]:
    """Every picture on the job, deduplicated, oldest first; plus counts and errors."""
    uid = str(job["job_uid"])
    base = {"filter.module": "JOB", "filter.module_uid": uid}
    items: dict[str, dict] = {}
    info: dict[str, Any] = {"errors": [], "videos": 0, "other_files": 0}

    def add(key: Any, rec: dict) -> None:
        key = str(key or rec.get("url") or "")
        if not key or not rec.get("url"):
            return
        old = items.get(key)
        if old is None:
            items[key] = rec
            return
        for k in ("album", "caption"):
            if rec.get(k) and not old.get(k):
                old[k] = rec[k]
        if rec["source"] not in old["source"].split("+"):
            old["source"] += "+" + rec["source"]

    folders: list[dict] = []
    try:
        folders = zc.rows_of(r.get("/attachments/folders", base))
    except zc.ZuperError as exc:
        info["errors"].append("albums: " + zc.sentence(exc))
    try:
        gallery, total = _pages(r, base, GALLERY_PAGES)
        album_of: dict[str, str] = {}
        for f in folders:
            if not f.get("attachment_count") or not f.get("folder_uid"):
                continue
            try:
                got, tot = _pages(r, {**base, "filter.folder_uid": f["folder_uid"]}, ALBUM_PAGES)
            except zc.ZuperError as exc:
                info["errors"].append("album %s: %s" % (f.get("folder_name"), zc.sentence(exc)))
                continue
            # the folder filter is trusted only when it demonstrably narrowed to that album
            if tot == f["attachment_count"] and (total is None or tot <= total):
                for a in got:
                    album_of[str(a.get("attachment_uid"))] = str(f.get("folder_name") or "")
        for a in gallery:
            if a.get("is_deleted"):
                continue
            album = album_of.get(str(a.get("attachment_uid")))
            add(a.get("attachment_uid"), {
                "source": "album" if album else "gallery", "album": album,
                "url": a.get("attachment_path") or a.get("attachment") or a.get("url"),
                "name": a.get("attachment_name"), "mime": a.get("mime_type"),
                "caption": plain(a.get("attachment_description")),
                "at": a.get("created_at"), "by": person(a.get("created_by"))})
    except zc.ZuperError as exc:
        info["errors"].append("gallery: " + zc.sentence(exc))
    try:
        for n in zc.rows_of(r.get("/jobs/%s/note" % uid, {"count": 100})):
            if n.get("is_deleted"):
                continue
            for a in n.get("attachments") or []:
                if not isinstance(a, dict):
                    continue
                add(a.get("attachment_uid"), {
                    "source": "note", "album": None,
                    "url": a.get("attachment") or a.get("attachment_path") or a.get("url"),
                    "name": a.get("file_name") or a.get("attachment_name"),
                    "mime": a.get("mime_type"), "caption": plain(n.get("note")),
                    "at": a.get("created_at") or n.get("created_at"),
                    "by": person(n.get("created_by"))})
    except zc.ZuperError as exc:
        info["errors"].append("notes: " + zc.sentence(exc))
    for s in job.get("job_status") or []:
        for c in s.get("checklist") or []:
            ans = c.get("answer") if isinstance(c, dict) else None
            if not ans or isinstance(ans, (int, float, bool)):
                continue
            photo_q = "IMAGE" in str(c.get("type") or "").upper() or \
                "PHOTO" in str(c.get("type") or "").upper()
            for u in URL.findall(json.dumps(ans)):
                add(u, {"source": "checklist", "album": None, "url": u,
                        "name": u.rsplit("/", 1)[-1].split("?")[0],
                        "mime": "image/*" if photo_q else None,
                        "caption": "checklist %s: %s" % (s.get("status_name"), c.get("question")),
                        "at": s.get("created_at"), "by": person(s.get("done_by"))})
    images = []
    for key, rec in items.items():
        if _is_image(rec.get("mime"), rec.get("url")):
            rec["key"] = key
            images.append(rec)
        elif str(rec.get("mime") or "").lower().startswith("video/") \
                or VIDEO_EXT.search(str(rec.get("url"))):
            info["videos"] += 1
        else:
            info["other_files"] += 1
    images.sort(key=lambda x: str(parse_time(x.get("at")) or ""))
    return images, info


# ------------------------------------------------------------------------------ selecting

def inspection_time(job: dict) -> tuple[datetime | None, str]:
    moves = job.get("job_status") or []
    for rx in (r"inspection in progress", r"inspect|on the way"):
        for s in moves:
            if re.search(rx, str(s.get("status_name") or ""), re.IGNORECASE) and \
                    parse_time(s.get("created_at")):
                return parse_time(s["created_at"]), "stage '%s'" % s.get("status_name")
    for k, why in (("scheduled_start_time", "scheduled visit"), ("created_at", "job created")):
        if parse_time(job.get(k)):
            return parse_time(job[k]), why
    return None, "unknown"


def proposal_times(rows: list[dict]) -> tuple[datetime | None, datetime | None]:
    """(first proposal created, first proposal accepted) among the job's live proposals."""
    rows = [x for x in rows if isinstance(x, dict) and not x.get("is_deleted")]
    created = sorted(t for t in (parse_time(x.get("created_at")) for x in rows) if t)
    accepted = sorted(t for t in (
        parse_time(x.get("accepted_date"))
        or (parse_time(x.get("updated_at"))
            if str(x.get("estimate_status") or "").upper() in ("ACCEPTED", "APPROVED") else None)
        for x in rows) if t)
    return (created[0] if created else None), (accepted[0] if accepted else None)


def select(images: list[dict], which: str, inspection: datetime | None,
           first_proposal: datetime | None, accepted: datetime | None,
           since: datetime | None, max_photos: int) -> tuple[list[dict], str]:
    """The pictures to look at, best first, and a sentence saying which window was used."""
    pool = [x for x in images if since is None or (parse_time(x.get("at")) or since) >= since]
    if which == "repair":
        start = accepted or first_proposal
        window = ("after the accepted proposal %s" % et(accepted) if accepted else
                  "after the first proposal %s (none accepted)" % et(first_proposal)
                  if first_proposal else "none: no proposal on this job yet")
        pool = [x for x in pool if start and (parse_time(x.get("at")) or start) > start]
        words, anchor = REPAIR_WORDS, start
    else:
        words, anchor = INSPECTION_WORDS, inspection
        window = "all"
        if which == "inspection":
            window = "inspection (nearest %s)" % (et(inspection) or "?")
            if first_proposal:
                inside = [x for x in pool
                          if (parse_time(x.get("at")) or first_proposal) <= first_proposal]
                if inside:
                    pool = inside
                    window += ", up to the first proposal %s" % et(first_proposal)
                else:
                    window += "; none before the first proposal %s, so all" % et(
                        first_proposal)

    def key(x: dict) -> tuple:
        text = " ".join(str(x.get(k) or "") for k in ("album", "caption", "name"))
        t = parse_time(x.get("at"))
        dist = abs((t - anchor).total_seconds()) if t and anchor else 9e12
        return (0 if words.search(text) else 1, dist)

    return sorted(pool, key=key)[:max_photos], window


# ------------------------------------------------------------------------------ files

def cleanup(root: Path, now: float | None = None) -> int:
    """Remove job folders untouched for KEEP_DAYS (index.json's time, else the folder's)."""
    now = now or time.time()
    removed = 0
    if not root.is_dir():
        return 0
    for d in root.iterdir():
        if not d.is_dir():
            continue
        idx = d / "index.json"
        stamp = (idx if idx.is_file() else d).stat().st_mtime
        if now - stamp > KEEP_DAYS * 86400:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    return removed


def to_jpeg(data: bytes) -> bytes:
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((SIDE, SIDE))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=QUALITY, optimize=True)
        return out.getvalue()


def _fetch(url: str) -> bytes:
    data, ctype = zc.fetch_file(url)
    if ctype and not (ctype.startswith("image/") or ctype in (
            "application/octet-stream", "binary/octet-stream")):
        raise ValueError("not a picture (%s)" % ctype)
    return to_jpeg(data)


def _safe_folder(job_number: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", job_number)[:40] or "job"


def store(folder: Path, picked: list[dict]) -> dict[str, int]:
    """Each picked picture -> folder/NNN.jpg (rec["path"]); already-saved ones are reused."""
    folder.mkdir(parents=True, exist_ok=True)
    idx_file = folder / "index.json"
    try:
        index = json.loads(idx_file.read_text("utf-8"))
    except (OSError, ValueError):
        index = {}
    files: dict[str, str] = index.get("files") if isinstance(index.get("files"), dict) else {}
    counts = {"reused": 0, "downloaded": 0, "failed": 0}
    todo = []
    for rec in picked:
        name = files.get(rec["key"])
        if name and (folder / name).is_file():
            rec["path"] = str(folder / name)
            counts["reused"] += 1
        else:
            todo.append(rec)
    used = {int(m.group(1)) for n in files.values() if (m := re.match(r"(\d+)\.jpg$", n))}
    used |= {int(p.stem) for p in folder.glob("*.jpg") if p.stem.isdigit()}
    nxt = max(used, default=0)

    def work(rec: dict) -> tuple[dict, bytes | None, str | None]:
        try:
            return rec, _fetch(str(rec["url"])), None
        except zc.ZuperError as exc:
            return rec, None, zc.sentence(exc)
        except Exception as exc:  # noqa: BLE001 - a broken picture is one "failed", not a crash
            return rec, None, "%s: %s" % (type(exc).__name__, str(exc)[:120])

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for rec, jpeg, err in pool.map(work, todo):
            if jpeg is None:
                rec["error"] = err
                counts["failed"] += 1
                continue
            nxt += 1
            name = "%03d.jpg" % nxt
            (folder / name).write_bytes(jpeg)
            files[rec["key"]] = name
            rec["path"] = str(folder / name)
            counts["downloaded"] += 1
    index = {"job_number": folder.name, "updated_at": datetime.now(UTC).isoformat(),
             "files": files}
    idx_file.write_text(json.dumps(index, indent=1), "utf-8")
    return counts


# ------------------------------------------------------------------------------ the tool

def photos(job: str, max_photos: int = MAX_PHOTOS, since: str | None = None,
           which: str = "inspection") -> dict:
    started = time.monotonic()
    which = (which or "inspection").strip().lower()
    if which not in WHICH:
        raise ValueError("`which` must be one of %s" % ", ".join(WHICH))
    max_photos = max(1, min(int(max_photos or MAX_PHOTOS), HARD_MAX))
    since_dt = None
    if since:
        since_dt = parse_time(str(since).strip()) or parse_time(str(since).strip() + "T00:00:00")
        if since_dt is None:
            raise ValueError("`since` must be a date like 2026-10-20")
    root = config.photo_dir()
    root.mkdir(parents=True, exist_ok=True)
    removed = cleanup(root)

    r = Reader()
    rec = find_job(r, job)
    if not isinstance(rec, dict) or not rec.get("job_uid"):
        raise LookupError("Zuper answered without a job for %r" % job)
    number = str(rec.get("work_order_number") or rec["job_uid"][:8])
    images, info = gather(r, rec)
    try:
        rows = zc.rows_of(r.get("/estimate", {"filter.job": rec["job_uid"], "count": 50,
                                              "page": 1}))
    except zc.ZuperError as exc:
        rows = []
        info["errors"].append("proposals: " + zc.sentence(exc))
    first, accepted = proposal_times(rows)
    insp, insp_why = inspection_time(rec)
    picked, window = select(images, which, insp, first, accepted, since_dt, max_photos)
    folder = root / _safe_folder(number)
    counts = store(folder, picked) if picked else {"reused": 0, "downloaded": 0, "failed": 0}

    out: dict[str, Any] = {
        "job": number, "job_uid": rec["job_uid"], "tz": "America/New_York",
        "which": which, "window": window,
        "inspection_time": "%s (%s)" % (et(insp), insp_why) if insp else None,
        "first_proposal": et(first), "accepted": et(accepted),
        "counts": {"images": len(images), "selected": len(picked),
                   "skipped": len(images) - len(picked), **counts,
                   "videos": info["videos"], "other_files": info["other_files"]},
        "folder": str(folder),
        "photos": [{"path": x["path"], "at": et(x.get("at")), "source": x["source"],
                    **({"album": x["album"]} if x.get("album") else {}),
                    **({"by": x["by"]} if x.get("by") else {}),
                    **({"caption": short(x["caption"], CAPTION_MAX)} if x.get("caption")
                       else {})}
                   for x in picked if x.get("path")],
        "failed": [{"at": et(x.get("at")), "source": x["source"], "why": x.get("error")}
                   for x in picked if not x.get("path")],
        "next": "Use the Read tool on each `path` to look at the picture." if picked else
                "No pictures in this window; try which=\"all\".",
        "requests": {"zuper": r.counts["zuper"], "files": counts["downloaded"]
                     + counts["failed"]},
        "seconds": round(time.monotonic() - started, 1),
    }
    if info["errors"]:
        out["errors"] = info["errors"]
    if removed:
        out["old_folders_removed"] = removed
    if not out["failed"]:
        out.pop("failed")
    return out


def job_photos(job: str, max_photos: int = MAX_PHOTOS, since: str | None = None,
               which: str = "inspection") -> str:
    try:
        return json.dumps(photos(job, max_photos, since, which), ensure_ascii=False,
                          default=str)
    except (LookupError, ValueError, TypeError) as exc:
        return "ERROR: %s" % exc
    except zc.ZuperError as exc:
        return "ERROR from Zuper: %s" % zc.sentence(exc)


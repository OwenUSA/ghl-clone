"""Pictures Owen drops in an Owen AI chat (2026-11-21).

The composer uploads each picture (`POST /api/owen-ai/threads/{id}/uploads`) and sends the
message with their ids. A picture is checked by its magic number (JPEG, PNG, WebP, HEIC/HEIF —
never the browser's header), WebP / HEIC are turned into a JPEG here (Zuper's gallery and
Claude's Read tool both take JPEG), and the file is kept on the pictures volume under
`chat-uploads/<sha256>.<ext>` — the volume BOTH the api (which saves it) and the owen_ai runner
(where Claude looks at it, and from where a `pictures` change uploads it) can reach.

Owen AI sees the pictures of the message it is answering (`prompt_lines`). Nothing here talks
to Zuper: a picture reaches a job's gallery only as a `pictures` change Owen OK'd in the chat
(changes.py).
"""
from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..attachments import sniff
from ..models import OwenAiMessage, OwenAiUpload
from . import config

MAX_BYTES = 15 * 1024 * 1024
MAX_PER_MESSAGE = 10
KEPT_TYPES = {"image/jpeg": "jpg", "image/png": "png"}
CONVERTED = ("image/webp", "image/heic", "image/heif")
_SAFE = re.compile(r"[^A-Za-z0-9._ -]+")


class Invalid(ValueError):
    """The picture is refused (type, size); nothing was saved."""


def folder() -> Path:
    return config.picture_dir() / "chat-uploads"


def _to_jpeg(data: bytes) -> bytes:
    from PIL import Image
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:                       # pragma: no cover - a dependency of the image
        pass
    try:
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
    except Exception as exc:                  # noqa: BLE001 - any decoder failure is a refusal
        raise Invalid("that picture could not be read (%s)" % type(exc).__name__) from None
    out = io.BytesIO()
    img.save(out, "JPEG", quality=92)
    return out.getvalue()


def clean_name(name: str | None, ext: str) -> str:
    stem = _SAFE.sub("", Path(name or "").stem).strip()[:80] or "picture"
    return "%s.%s" % (stem, ext)


def save(db: Session, thread_id: int, file_name: str | None, data: bytes) -> OwenAiUpload:
    if not data:
        raise Invalid("the file is empty")
    if len(data) > MAX_BYTES:
        raise Invalid("a picture may be at most %d MB" % (MAX_BYTES // (1024 * 1024)))
    kind = sniff(data)
    if kind in CONVERTED:
        data, kind = _to_jpeg(data), "image/jpeg"
    if kind not in KEPT_TYPES:
        raise Invalid("only pictures (JPEG, PNG, WebP or HEIC) can be added")
    sha = hashlib.sha256(data).hexdigest()
    ext = KEPT_TYPES[kind]
    path = folder() / ("%s.%s" % (sha, ext))
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
    row = OwenAiUpload(thread_id=thread_id, file_name=clean_name(file_name, ext),
                       content_type=kind, size=len(data), sha256=sha)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def path_of(row: OwenAiUpload) -> Path:
    return folder() / ("%s.%s" % (row.sha256, KEPT_TYPES.get(row.content_type, "bin")))


def read(row: OwenAiUpload) -> bytes:
    """The bytes, checked again against what was saved (a file swapped on disk is refused)."""
    data = path_of(row).read_bytes()
    if hashlib.sha256(data).hexdigest() != row.sha256:
        raise Invalid("picture %d changed on disk since it was saved" % row.id)
    return data


def attach(db: Session, thread_id: int, message_id: int, ids: list[int]) -> list[OwenAiUpload]:
    """Link uploads of THIS chat, not yet sent, to the message. Refuses the whole list on any
    bad id (the caller has not committed the message yet)."""
    ids = list(dict.fromkeys(int(i) for i in ids))
    if len(ids) > MAX_PER_MESSAGE:
        raise Invalid("at most %d pictures in one message" % MAX_PER_MESSAGE)
    rows = list(db.scalars(select(OwenAiUpload).where(OwenAiUpload.id.in_(ids)))) if ids else []
    by_id = {r.id: r for r in rows}
    for i in ids:
        r = by_id.get(i)
        if r is None or r.thread_id != thread_id or r.message_id is not None:
            raise Invalid("picture %d is not waiting to be sent in this chat" % i)
    for r in rows:
        r.message_id = message_id
    return [by_id[i] for i in ids]


def of_messages(db: Session, message_ids: list[int]) -> dict[int, list[OwenAiUpload]]:
    out: dict[int, list[OwenAiUpload]] = {}
    if not message_ids:
        return out
    for r in db.scalars(select(OwenAiUpload).where(OwenAiUpload.message_id.in_(message_ids))
                        .order_by(OwenAiUpload.id)):
        out.setdefault(r.message_id, []).append(r)
    return out


def view(r: OwenAiUpload) -> dict:
    return {"id": r.id, "file_name": r.file_name, "content_type": r.content_type,
            "size": r.size, "url": "/api/owen-ai/uploads/%d" % r.id}


def prompt_lines(db: Session, m: OwenAiMessage) -> list[str]:
    """What Claude is told about the pictures sent with this message."""
    rows = of_messages(db, [m.id]).get(m.id) or []
    if not rows:
        return []
    lines = ["Owen attached %d picture%s to this message (Read the path to look at one; to add "
             "them to a job's gallery, propose a `pictures` change with their upload ids):" % (
                 len(rows), "" if len(rows) == 1 else "s")]
    lines += ["  upload %d — %s — %s" % (r.id, r.file_name, path_of(r)) for r in rows]
    return lines

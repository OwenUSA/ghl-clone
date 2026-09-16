"""Pictures on a text message: the store, the limits, and the fetch (2026-09-16).

The owner's decision: a customer texts a photo of the roof and the operator sees the photo,
in the thread, for as long as the job exists. Two consequences drive everything here.

## 1. The CRM keeps its own copy, and a mirrored recording does not

`app/openphone.py` streams a call recording from owen-main every time it is played and
stores nothing, and that is right for audio OpenPhone will hold as long as the account does.
A **carrier MMS media link expires** — days, sometimes hours. So a picture is FETCHED ONCE,
when the text arrives, and kept. The tradeoff is the opposite one, taken deliberately: disk
instead of a dead link.

## 2. The bytes do not go in Postgres

They go on disk under `MEDIA_ROOT`, content-addressed:

    <MEDIA_ROOT>/<sha256[:2]>/<sha256>

and `message_attachments` holds a row per picture saying where. Two reasons, both measured
against how this database is actually used: a `pg_dump` the operator takes to look at 800
contacts should not carry a gigabyte of JPEG, and every byte written to a bytea column is
written again to the WAL and again into every base backup. Content addressing also makes
"stored once" literal — the same picture sent twice is one file, and deleting one row does
not orphan the other, which is why `forget()` counts the rows holding a sha before it
unlinks.

**`MEDIA_ROOT` must be a persistent volume.** With none, the pictures are inside the
container and the next deploy loses them. `docker-compose.prod.yml` mounts one; the
operator steps are in DECISIONS.md.

## The type limit is decided by the BYTES, not by the header

`sniff()` reads the magic number. A `Content-Type: image/png` on a zip is a claim by
whoever sent it, and the whole point of the limit is that nothing executable is ever stored
or handed back to a browser. What is served is what was sniffed, never what was declared.
"""
import hashlib
import logging
import os
import re
from pathlib import Path

log = logging.getLogger("attachments")

# ---- the limits ------------------------------------------------------------------------
#
# Carriers cap an MMS well below these (BulkVS documents 1 MB; most US carriers re-encode
# above ~600 KB), so these are a backstop against a bad actor and a runaway disk, not a
# working limit anyone will meet. Stated in one place because four different paths check
# them — ingest, the fetch job, the upload route and the send.

MAX_BYTES = 5 * 1024 * 1024
# Per message. The carrier's own limit is lower; this stops a malformed relay claiming 4000.
MAX_INBOUND_ATTACHMENTS = 10
# Per outbound message. BulkVS accepts a list; 5 pictures is already a 5 MB MMS.
MAX_OUTBOUND_ATTACHMENTS = 5

# Images, and nothing else. HEIC is here because iPhones send it and the carrier passes it
# through unconverted; Safari renders it and Chrome does not, which is a display problem
# rather than a reason to throw the customer's picture away.
ALLOWED_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/heif": "heif",
}

REFUSE_TYPE = ("Only pictures can be attached to a text — JPEG, PNG, GIF, WebP or HEIC. "
               "That file is not one of them.")


def refuse_size(byte_size: int) -> str:
    return ("That picture is %.1f MB — the limit is %d MB."
            % (byte_size / 1024 / 1024, MAX_BYTES // 1024 // 1024))


def refuse_count(limit: int) -> str:
    return ("A text can carry %d picture%s. Remove one to add another."
            % (limit, "" if limit == 1 else "s"))


# ---- what the bytes actually are ---------------------------------------------------------

def sniff(data: bytes) -> str | None:
    """The image type of `data`, from its magic number, or None if it is not one we allow.

    Deliberately short and literal rather than a library: six formats, each identified by
    bytes the format's own specification fixes. `imghdr` was removed from the standard
    library in 3.13 and never knew about WebP or HEIC.
    """
    if len(data) < 12:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp":
        # ISO base media. The brand says which flavour; only the still-image ones are images.
        brand = data[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs"):
            return "image/heic"
        if brand in (b"mif1", b"msf1"):
            return "image/heif"
    return None


def extension_for(content_type: str | None) -> str:
    return ALLOWED_TYPES.get(content_type or "", "bin")


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def clean_filename(name: str | None, content_type: str | None, position: int) -> str:
    """A name safe to put in a Content-Disposition header.

    Never the customer's string verbatim: it arrives from a carrier and reaches a browser's
    "Save as" box. Everything outside a small alphabet is collapsed, the extension is forced
    to match what was SNIFFED, and a name that survives none of that becomes "photo-N.jpg".
    """
    ext = extension_for(content_type)
    base = _SAFE_NAME.sub("-", (name or "").strip()).strip("-.")
    base = base.rsplit(".", 1)[0][:60]
    if not base:
        base = "photo-%d" % (position + 1)
    return "%s.%s" % (base, ext)


# ---- the store ---------------------------------------------------------------------------

DEFAULT_MEDIA_ROOT = "var/media"


def media_root() -> Path:
    """Read from the environment on every call, never frozen at import.

    The same reason `crmlink.current()` gives: the tests point this at a throwaway directory
    with `monkeypatch.setenv`, and a module-level constant would let the first test to
    import `app.main` decide where every later one wrote.
    """
    return Path(os.getenv("MEDIA_ROOT") or DEFAULT_MEDIA_ROOT)


def _path_for(sha: str) -> Path:
    # Two-character fan-out: one directory per picture would put a hundred thousand entries
    # in one inode on a busy year, which ext4 tolerates and `ls` does not.
    return media_root() / sha[:2] / sha


def store(data: bytes) -> tuple[str, str]:
    """Write `data` and return `(sha256, relative path)`. Idempotent by construction.

    Writing to a temporary name in the same directory and renaming means a reader never
    opens a half-written file: rename is atomic within a filesystem. A second store of the
    same bytes finds the file already there and writes nothing.
    """
    sha = hashlib.sha256(data).hexdigest()
    dest = _path_for(sha)
    rel = "%s/%s" % (sha[:2], sha)
    if dest.exists():
        return sha, rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part-%d" % os.getpid())
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    return sha, rel


def read(storage_path: str | None) -> bytes | None:
    """The bytes, or None if the file is gone.

    None rather than an exception: a volume that was not mounted, or a file restored from a
    backup that predates it, is a picture the thread shows as unavailable — not a 500 that
    takes the whole conversation down with it.
    """
    if not storage_path:
        return None
    # Never join an attacker-controlled string onto the root: `storage_path` is written by
    # `store()` and is always "<2 hex>/<64 hex>", so anything else is a corrupted row.
    if not re.fullmatch(r"[0-9a-f]{2}/[0-9a-f]{64}", storage_path):
        log.warning("attachments: refusing to read a malformed storage path %r", storage_path)
        return None
    path = media_root() / storage_path
    try:
        return path.read_bytes()
    except OSError:
        return None


def forget(storage_path: str | None, still_referenced: int) -> bool:
    """Unlink the file behind `storage_path`, but only when nothing else points at it.

    `still_referenced` is the caller's count of OTHER rows holding the same sha — content
    addressing means one file can serve several attachments, and a removed draft must not
    take the customer's inbound picture with it.
    """
    if not storage_path or still_referenced > 0:
        return False
    if not re.fullmatch(r"[0-9a-f]{2}/[0-9a-f]{64}", storage_path):
        return False
    try:
        (media_root() / storage_path).unlink()
        return True
    except OSError:
        return False

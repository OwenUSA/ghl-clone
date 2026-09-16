"""The four routes a picture message needs (2026-09-16).

    GET    /api/attachments/{id}          the bytes, to a signed-in browser
    POST   /api/attachments               upload one, before sending
    DELETE /api/attachments/{id}          take an unsent one back off the composer
    POST   /api/attachments/{id}/retry    fetch an inbound one again

A router of its own rather than four more entries in `main.py`, for the reason
`app/openphone.py` is one: these are a surface with a single subject, and `main.py` is
already the place everything else went.

## Nothing here is public

`GET /api/attachments/{id}` is the ONLY way a picture reaches a browser, and it is behind
the app-level `require_auth` dependency like every other `/api` route — so the session
cookie is what unlocks it, and `<img src="/api/attachments/12">` sends that cookie because
the path is same-origin. That shape is not decoration: an `<img>` tag cannot send an
Authorization header, which is exactly why a CRM-relative path is right and a direct link
to owen-main would not have worked. The same argument as the call player, one floor down.

**The carrier's URL never reaches here at all** and neither does owen-main's key. What the
browser gets is an integer, and what it gets back is bytes.

## Who may see one is who may read the thread

`message_media.visible` asks the same question `GET /api/conversations/{id}/events` asks,
through the same `assigned_access` scope, and answers 404 — never 403 — so an id cannot be
walked to find out whose customers send photographs.
"""
import logging

from fastapi import APIRouter, Depends, File, Header, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from . import attachments, auth, message_media
from .byte_range import ranged_response
from .db import get_db
from .models import ATTACHMENT_DRAFT, ATTACHMENT_STORED

log = logging.getLogger("media_api")

router = APIRouter(prefix="/api/attachments", tags=["attachments"])


@router.get("/{attachment_id}")
def get_attachment(attachment_id: int,
                   principal: auth.Principal = auth.ANY_USER,
                   range_header: str | None = Header(None, alias="Range"),
                   db: Session = Depends(get_db)) -> Response:
    """The picture's bytes.

    ANY_USER for the same reason the call recording is: a TECH who can read the thread can
    see what the customer sent on it. The narrowing that matters is which THREADS they can
    read, and that happens in `message_media.visible`.

    404 rather than 410 when the file is gone from disk: from the browser's side there is
    no picture at this id, and the thread already shows the sentence beside the bubble.
    """
    row = message_media.visible(db, principal, attachment_id)
    if row.status != ATTACHMENT_STORED and row.status != ATTACHMENT_DRAFT:
        raise HTTPException(404, "that picture is not here")
    data = attachments.read(row.storage_path)
    if data is None:
        log.warning("media: attachment %s has no bytes at %r — is MEDIA_ROOT mounted?",
                    row.id, row.storage_path)
        raise HTTPException(404, "that picture is not here")
    return ranged_response(
        data, row.content_type or "", range_header, default_type="image/jpeg",
        extra_headers={
            # `inline` so the viewer shows it; the filename is what a "Save image as"
            # offers, and it has been through `clean_filename` — never the carrier's
            # string verbatim.
            "Content-Disposition": 'inline; filename="%s"' % (row.filename or "photo.jpg"),
            # A customer's photograph must never sit in a shared cache. Same reasoning,
            # and the same header, as the call recording.
            "Cache-Control": "private, max-age=300",
            # The bytes were sniffed, not believed — but a browser that sniffs for itself
            # could still find something else in them, so it is told not to.
            "X-Content-Type-Options": "nosniff",
        })


@router.post("", status_code=201)
async def upload_attachment(file: UploadFile = File(...),
                            principal: auth.Principal = auth.ANY_USER,
                            db: Session = Depends(get_db)) -> dict:
    """Keep a picture the operator picked, so the composer can show it before sending.

    ANY_USER: a restricted technician may text their own customers, so they may attach a
    photo to that text. WHICH customer they may send it to is decided at send time by the
    send routes, which already enforce it — checking it twice here would mean an upload
    that had to name a thread before the operator had chosen one.

    Read with a cap rather than `await file.read()`: an unbounded read is how a 400 MB
    upload becomes 400 MB of this process's memory before anyone gets to refuse it.
    """
    data = await file.read(attachments.MAX_BYTES + 1)
    if len(data) > attachments.MAX_BYTES:
        raise HTTPException(400, attachments.refuse_size(len(data)))
    row = message_media.store_upload(db, principal, data, file.filename)
    return message_media.public(row)


@router.delete("/{attachment_id}")
def remove_attachment(attachment_id: int,
                      principal: auth.Principal = auth.ANY_USER,
                      db: Session = Depends(get_db)) -> dict:
    """Take a picture back off the composer before it is sent.

    A DRAFT only. A picture already on a message is part of the record of what was said,
    and this CRM does not let anyone edit that — the same rule that makes a cancelled
    appointment keep its row and a refused text stay on the thread.
    """
    row = message_media.visible(db, principal, attachment_id)
    if row.status != ATTACHMENT_DRAFT:
        raise HTTPException(409, "That picture has already been sent.")
    message_media.discard(db, row)
    return {"deleted": attachment_id}


@router.post("/{attachment_id}/retry")
def retry_attachment(attachment_id: int,
                     principal: auth.Principal = auth.ANY_USER,
                     db: Session = Depends(get_db)) -> dict:
    """Fetch an inbound picture again, now.

    Answers 200 with the attachment's new state whatever happened — including "it failed
    again". This is a person pressing a button next to a gap in a conversation; the answer
    they need is what the picture's state is now, and an error status would make the
    browser throw away the sentence saying why.
    """
    row = message_media.visible(db, principal, attachment_id)
    if row.direction.value != "INBOUND":
        raise HTTPException(409, "Only a picture the customer sent can be fetched again.")
    return message_media.public(message_media.retry(db, row))

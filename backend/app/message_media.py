"""Pictures on a thread: rows, fetching, access and serving (2026-09-16).

`app/attachments.py` is the disk and the limits and knows nothing about the database.
This module is the other half: it owns `message_attachments` rows, the fetch from
owen-main, who may look at a picture, and what the thread's JSON says about one.

## Inbound, end to end

    customer's phone --MMS--> BulkVS --webhook--> owen-main   (stores the media URLs)
                                                    |
                          POST /api/events {type: SMS, num_media: 3, provider_ref: <id>}
                                                    v
                          this CRM: 3 PENDING rows + one `fetch_message_media` job
                                                    |
                          worker: GET /api/crm-link/messages/<id>/media/<i> through
                          `crmlink`, sniff, cap, store under MEDIA_ROOT, mark STORED

The fetch is a QUEUED JOB and not part of the ingest request, and that is the one design
decision here worth arguing about. Fetching inline would be simpler to read and would make
owen-main's `crm_report` job wait on three carrier round-trips before it got its 201 — on a
slow carrier that is a timeout, a retry, and a second delivery of a text that already
landed. So the text lands immediately and the pictures arrive seconds later, which is also
what the operator sees in every other messaging app they use.

**A failed fetch never loses the text.** The row goes FAILED with a sentence, the thread
shows "Picture unavailable" and a Retry, and the words the customer typed are exactly where
they were.

## Outbound

The composer uploads a picture first (`POST /api/attachments`), which stores it here as a
DRAFT and gives the browser an id. Sending passes the ids. On send, the bytes go to
owen-main, which publishes them for the carrier and hands back opaque media ids; the draft
rows are then attached to the new event. A send that is refused leaves NOTHING on the
thread — the drafts stay drafts, so the operator can fix the number and press send again
without picking the pictures a second time.

## Who may look at one

The same rule as the thread it is on, asked the same way — `assigned_access` for a contact
thread, and a flat refusal for a number-only thread when "Only assigned data" is on. Answer
is 404 and never 403, so an id cannot be probed for whether it exists. There is no separate
"photos" permission: a picture IS the message, and a person who can read the message the
customer sent can see what they sent.
"""
import logging
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import assigned_access, attachments, auth, crmlink, queue
from .models import (
    ATTACHMENT_DRAFT,
    ATTACHMENT_FAILED,
    ATTACHMENT_PENDING,
    ATTACHMENT_REFUSED,
    ATTACHMENT_STORED,
    Conversation,
    ConversationEvent,
    Direction,
    MessageAttachment,
    NumberThreadEvent,
)

log = logging.getLogger("message_media")

FETCH_JOB = "fetch_message_media"

# The sentence under a picture that did not arrive. Deliberately not "error": the operator
# is being told the state of one picture, on a thread whose text is fine.
UNAVAILABLE = "Picture unavailable."
NO_LINK = "The phone system is not connected, so the picture could not be fetched."


def _utcnow():
    return datetime.now(UTC)


# ---- rows ---------------------------------------------------------------------------------

def plan_inbound(db: Session, event, owen_message_id: str | None,
                 num_media: int) -> list[MessageAttachment]:
    """Create the PENDING rows for an inbound MMS. Idempotent.

    Idempotent TWICE over, because a carrier webhook and a relay job both retry:
      * `POST /api/events` returns the existing event for a repeated `dedupe_key`, so this
        is normally not reached a second time at all;
      * and if it is, the unique index on (parent, position) means the rows already exist
        and none are added.

    Returns the rows that need fetching — empty when there is nothing new to do, which is
    what stops a re-delivery from queueing a second fetch of bytes already on disk.
    """
    if num_media <= 0:
        return []
    wanted = min(int(num_media), attachments.MAX_INBOUND_ATTACHMENTS)
    existing = {a.position: a for a in _rows_for(db, event)}
    made: list[MessageAttachment] = []
    for i in range(wanted):
        if i in existing:
            continue
        row = MessageAttachment(
            position=i,
            direction=Direction.INBOUND,
            status=ATTACHMENT_PENDING,
            # The locator on the link, never a carrier URL. Null when owen-main did not name
            # the message — then there is nothing to ask for and the row goes FAILED below.
            source_ref=("%s/%d" % (owen_message_id, i)) if owen_message_id else None,
        )
        _attach_to(row, event)
        db.add(row)
        made.append(row)
    if made:
        db.flush()
    # Anything above the cap is a relay claiming more pictures than a carrier can send. The
    # text still lands; the operator is told, once, that some were dropped.
    if num_media > wanted and made:
        made[-1].detail = ("Only the first %d pictures of %d were kept."
                           % (wanted, int(num_media)))
    return made


def _attach_to(row: MessageAttachment, event) -> None:
    if isinstance(event, NumberThreadEvent):
        row.number_thread_event_id = event.id
    else:
        row.conversation_event_id = event.id


def _rows_for(db: Session, event) -> list[MessageAttachment]:
    if isinstance(event, NumberThreadEvent):
        clause = MessageAttachment.number_thread_event_id == event.id
    else:
        clause = MessageAttachment.conversation_event_id == event.id
    return list(db.scalars(select(MessageAttachment).where(clause)
                           .order_by(MessageAttachment.position)).all())


def enqueue_fetch(db: Session, rows: list[MessageAttachment]) -> int:
    """One job per picture, deduped on the attachment id.

    Per picture rather than per message so one carrier link that has already expired does
    not stop the other two arriving, and because a retry is then the same job type a person
    presses in the thread.
    """
    queued = 0
    for row in rows:
        if queue.enqueue(db, FETCH_JOB, {"attachment_id": row.id},
                         dedupe_key="media:%d" % row.id) is not None:
            queued += 1
    return queued


# ---- the fetch ---------------------------------------------------------------------------

def fetch_one(db: Session, row: MessageAttachment) -> str:
    """Fetch, check and store one inbound picture. Returns the status it ended on.

    Every outcome is a STATUS plus a SENTENCE, never an exception that reaches the worker:
    a picture that cannot be had is a fact about one bubble, and the job is not worth
    retrying for ever over it. The four outcomes are the four things that can be true —
    it worked, it will never work (too big, not an image), it might work later (owen-main
    was unreachable), or nobody has wired the link at all.
    """
    if row.status == ATTACHMENT_STORED:
        return ATTACHMENT_STORED
    if not row.source_ref or "/" not in row.source_ref:
        return _fail(db, row, ATTACHMENT_FAILED,
                     "The phone system did not say where this picture is.")
    if not crmlink.configured():
        return _fail(db, row, ATTACHMENT_FAILED, NO_LINK)

    message_id, _, index = row.source_ref.rpartition("/")
    result = crmlink.fetch_message_media(message_id, int(index))
    if not isinstance(result, tuple):
        # A LinkResult. 404 means the carrier link is gone for good — every other answer
        # might come out differently on a retry, so the sentence differs and Retry stays.
        if result.status == 404:
            return _fail(db, row, ATTACHMENT_FAILED,
                         "The carrier no longer has this picture — MMS links expire.")
        return _fail(db, row, ATTACHMENT_FAILED,
                     _sentence(result.reason) or "The picture could not be fetched.")

    data, declared = result
    if len(data) > attachments.MAX_BYTES:
        return _fail(db, row, ATTACHMENT_REFUSED, attachments.refuse_size(len(data)))
    sniffed = attachments.sniff(data)
    if sniffed is None:
        # Deliberately REFUSED and not FAILED: retrying cannot turn a PDF into a picture.
        return _fail(db, row, ATTACHMENT_REFUSED, attachments.REFUSE_TYPE)

    sha, path = attachments.store(data)
    row.status = ATTACHMENT_STORED
    row.detail = None
    row.content_type = sniffed
    row.byte_size = len(data)
    row.sha256 = sha
    row.storage_path = path
    row.filename = attachments.clean_filename(None, sniffed, row.position)
    row.fetched_at = _utcnow()
    db.flush()
    log.info("media: stored attachment %s (%s, %d bytes, declared %r)",
             row.id, sniffed, len(data), declared)
    return ATTACHMENT_STORED


def _sentence(text: str) -> str:
    """A reason from `crmlink` as a sentence a person reads under a bubble.

    Those strings are written to be interpolated ("could not reach the phone system") and
    read as a fragment on their own line. Capitalising and closing them here rather than
    rewording them keeps owen-main's own words — including ones we did not anticipate —
    while making the line look like something a person wrote.
    """
    text = (text or "").strip()
    if not text:
        return ""
    if not text.endswith((".", "!", "?")):
        text += "."
    return text[0].upper() + text[1:]


def _fail(db: Session, row: MessageAttachment, status: str, detail: str) -> str:
    row.status = status
    row.detail = detail
    db.flush()
    return status


def handle_fetch_job(db: Session, payload: dict) -> None:
    """The queue handler. Registered in `automations.HANDLERS` under `fetch_message_media`.

    It never raises for a picture that could not be had — the row already says so and the
    thread already shows it, and a raise would only make the worker retry the same expired
    carrier link four more times. It DOES let a genuine bug (a missing row, a broken import)
    raise, which is what the queue's error column is for.
    """
    row = db.get(MessageAttachment, int(payload.get("attachment_id") or 0))
    if row is None:
        log.warning("media: fetch job for attachment %r, which is gone",
                    payload.get("attachment_id"))
        return
    fetch_one(db, row)


def retry(db: Session, row: MessageAttachment) -> MessageAttachment:
    """Fetch it again, now, from the thread's Retry button.

    Synchronous rather than re-queued: the operator is looking at the picture's place in
    the thread and pressing a button, and "queued a job" is not an answer to "show me the
    photo". A REFUSED row is not retried — the answer will not change — and says so.
    """
    if row.status == ATTACHMENT_REFUSED:
        return row
    row.status = ATTACHMENT_PENDING
    row.detail = None
    db.flush()
    fetch_one(db, row)
    db.commit()
    db.refresh(row)
    return row


# ---- outbound ----------------------------------------------------------------------------

# How long an unsent picture is kept. A draft only exists between "the operator picked a
# photo" and "they pressed Send", which is seconds — except when the send was refused and
# they walked away, or they closed the tab. Those are the ones that would otherwise sit on
# the volume for ever.
DRAFT_TTL_HOURS = 24


def sweep_drafts(db: Session, user_id: int | None) -> int:
    """Remove this operator's abandoned drafts. Returns how many went.

    Swept HERE, on the next upload, rather than from a scheduled job: the only way drafts
    accumulate at all is by uploading more of them, so the sweep runs exactly when it can
    have anything to do and never wakes a worker up to find nothing. Scoped to one user so
    it can never touch a picture somebody else is still looking at in a composer.
    """
    if user_id is None:
        return 0
    cutoff = _utcnow() - timedelta(hours=DRAFT_TTL_HOURS)
    stale = db.scalars(select(MessageAttachment).where(
        MessageAttachment.status == ATTACHMENT_DRAFT,
        MessageAttachment.created_by_id == user_id,
        MessageAttachment.created_at < cutoff)).all()
    for row in stale:
        path, sha = row.storage_path, row.sha256
        db.delete(row)
        db.flush()
        others = db.scalar(select(func.count(MessageAttachment.id))
                           .where(MessageAttachment.sha256 == sha)) if sha else 0
        attachments.forget(path, others or 0)
    if stale:
        log.info("media: swept %d abandoned draft(s) for user %s", len(stale), user_id)
    return len(stale)


def store_upload(db: Session, principal: auth.Principal, data: bytes,
                 filename: str | None) -> MessageAttachment:
    """Keep a picture the operator picked, as a DRAFT with no message yet.

    Refusals are 400 with a sentence and write NOTHING — not a row, not a file. A refused
    upload that left a row behind would count against the per-message cap for a picture
    nobody can see.
    """
    if not data:
        raise HTTPException(400, "That file is empty.")
    if len(data) > attachments.MAX_BYTES:
        raise HTTPException(400, attachments.refuse_size(len(data)))
    sniffed = attachments.sniff(data)
    if sniffed is None:
        raise HTTPException(400, attachments.REFUSE_TYPE)

    # Before writing another one: a send that was refused, or a tab that was closed, leaves
    # a draft behind, and nothing else would ever remove it.
    sweep_drafts(db, principal.user_id)

    sha, path = attachments.store(data)
    row = MessageAttachment(
        position=0, direction=Direction.OUTBOUND, status=ATTACHMENT_DRAFT,
        content_type=sniffed, byte_size=len(data), sha256=sha, storage_path=path,
        filename=attachments.clean_filename(filename, sniffed, 0),
        created_by_id=principal.user_id, fetched_at=_utcnow())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def take_drafts(db: Session, principal: auth.Principal,
                ids: list[int] | None) -> list[MessageAttachment]:
    """The draft rows named by `ids`, checked and in the order asked for.

    A draft belongs to whoever uploaded it. Not a privacy boundary — it is two operators
    with the same thread open, and an id typed into one composer must not take a picture out
    of another's. An id that is not a live draft of this user's is a 400 with a sentence,
    because it means the browser and the server disagree about what is attached and sending
    the text without the picture would be worse than refusing.
    """
    wanted = [int(i) for i in (ids or [])]
    if not wanted:
        return []
    if len(wanted) > attachments.MAX_OUTBOUND_ATTACHMENTS:
        raise HTTPException(400, attachments.refuse_count(
            attachments.MAX_OUTBOUND_ATTACHMENTS))
    rows = []
    for i in wanted:
        row = db.get(MessageAttachment, i)
        if (row is None or row.status != ATTACHMENT_DRAFT
                or row.created_by_id != principal.user_id):
            raise HTTPException(400, "One of those pictures is no longer attached. "
                                     "Add it again.")
        rows.append(row)
    return rows


def attach_sent(db: Session, event, rows: list[MessageAttachment]) -> None:
    """Move the drafts onto the message that was just written."""
    for position, row in enumerate(rows):
        row.position = position
        row.status = ATTACHMENT_STORED
        row.direction = Direction.OUTBOUND
        _attach_to(row, event)
    if rows:
        db.flush()


def discard(db: Session, row: MessageAttachment) -> None:
    """Remove a draft, and its bytes when no other row holds them."""
    path, sha = row.storage_path, row.sha256
    db.delete(row)
    db.flush()
    others = db.scalar(select(func.count(MessageAttachment.id))
                       .where(MessageAttachment.sha256 == sha)) if sha else 0
    attachments.forget(path, others or 0)
    db.commit()


def upload_for_send(rows: list[MessageAttachment]) -> tuple[list[str], str]:
    """Hand the bytes to owen-main and return `(media_ids, problem)`.

    `problem` is a sentence and non-empty only when something went wrong, in which case
    NOTHING is sent — a picture that did not reach the phone system must not become a text
    that arrives without it, because the picture is usually the whole message.
    """
    media_ids: list[str] = []
    for row in rows:
        data = attachments.read(row.storage_path)
        if data is None:
            return [], "That picture is no longer on the server. Add it again."
        result = crmlink.upload_media(data, row.filename or "photo.jpg",
                                      row.content_type or "image/jpeg")
        if not result.ok:
            return [], (result.reason
                        or "The phone system would not take the picture.")
        media_id = str((result.data or {}).get("media_id") or "")
        if not media_id:
            return [], "The phone system did not say where it put the picture."
        media_ids.append(media_id)
    return media_ids, ""


# ---- access ------------------------------------------------------------------------------

def visible(db: Session, principal: auth.Principal, attachment_id: int) -> MessageAttachment:
    """The attachment, or 404 — the same 404 for "no such row" and "not yours".

    404 and never 403 for the reason `pipeline_access` gives: a 403 tells the caller the
    id exists, and walking the ids would map out how many pictures other people's customers
    have sent. A TECH with "Only assigned data" gets this for a number-only thread whatever
    the picture is, because a number nobody has saved is nobody's job.
    """
    row = db.get(MessageAttachment, attachment_id)
    if row is None:
        raise HTTPException(404, "that picture is not here")
    if row.conversation_event_id is not None:
        ev = db.get(ConversationEvent, row.conversation_event_id)
        conv = db.get(Conversation, ev.conversation_id) if ev else None
        if conv is None or not assigned_access.scope(db, principal).sees_contact(
                conv.contact_id):
            raise HTTPException(404, "that picture is not here")
        return row
    if row.number_thread_event_id is not None:
        if assigned_access.restricted(principal):
            raise HTTPException(404, "that picture is not here")
        return row
    # A draft: only the operator who uploaded it, until it is sent.
    if row.created_by_id != principal.user_id:
        raise HTTPException(404, "that picture is not here")
    return row


# ---- what the thread's JSON says ---------------------------------------------------------

def public(row: MessageAttachment) -> dict:
    """One attachment as the browser sees it.

    NO storage path, NO sha, NO carrier URL and no owen-main locator — the browser gets an
    id it can ask this server for, a type, a size and, when something went wrong, the
    sentence to print. `url` is null unless the bytes are actually here, which is what the
    thread branches on rather than re-deriving "is it ready" from the status string.
    """
    # A DRAFT has its bytes too — that is the whole point of uploading on pick, so the
    # composer's preview is the picture that will actually be sent rather than a local blob
    # that only resembles it.
    ready = row.status in (ATTACHMENT_STORED, ATTACHMENT_DRAFT) and bool(row.storage_path)
    return {
        "id": row.id,
        "position": row.position,
        "status": row.status,
        "detail": row.detail,
        "content_type": row.content_type,
        "byte_size": row.byte_size,
        "filename": row.filename,
        "url": ("/api/attachments/%d" % row.id) if ready else None,
        # A picture that may yet arrive shows a spinner; one that never will shows the
        # sentence and a Retry. The browser should not have to know which statuses are
        # which, so the server says.
        "pending": row.status == ATTACHMENT_PENDING,
        "retryable": row.status == ATTACHMENT_FAILED,
    }


def by_event(db: Session, events: list) -> dict[tuple[str, int], list[dict]]:
    """Every attachment for a page of thread events, in ONE query.

    Keyed by `(kind, event id)` because the two event tables have overlapping ids — the
    same reason the inbox addresses rows by `c12` / `n3`.
    """
    conv_ids = [e.id for e in events if isinstance(e, ConversationEvent)]
    num_ids = [e.id for e in events if isinstance(e, NumberThreadEvent)]
    out: dict[tuple[str, int], list[dict]] = {}
    if not conv_ids and not num_ids:
        return out
    clauses = []
    if conv_ids:
        clauses.append(MessageAttachment.conversation_event_id.in_(conv_ids))
    if num_ids:
        clauses.append(MessageAttachment.number_thread_event_id.in_(num_ids))
    stmt = select(MessageAttachment).where(
        clauses[0] if len(clauses) == 1 else clauses[0] | clauses[1]
    ).order_by(MessageAttachment.position, MessageAttachment.id)
    for row in db.scalars(stmt).all():
        key = (("conv", row.conversation_event_id) if row.conversation_event_id is not None
               else ("number", row.number_thread_event_id))
        out.setdefault(key, []).append(public(row))
    return out


def event_key(event) -> tuple[str, int]:
    return (("number", event.id) if isinstance(event, NumberThreadEvent)
            else ("conv", event.id))

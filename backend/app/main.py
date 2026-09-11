"""FastAPI app.

URL shapes deliberately mirror what was measured in GHL so routes map 1:1:
  GHL  /v2/location/<id>/contacts/smart_list/All
  ours /api/contacts?page=1&page_size=20
"""
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import AliasChoices, BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from . import auth, automations, crmlink, custom_fields, models, phone_match, softphone
from .db import DATABASE_URL, Base, engine, get_db
from .models import (
    ACTIVITY_TYPES,
    CONVERSATION_TYPES,
    ApiToken,
    Appointment,
    Calendar,
    Contact,
    ContactTag,
    Conversation,
    ConversationEvent,
    CustomFieldDef,
    CustomFieldPipeline,
    DeliveryStatus,
    Direction,
    EventType,
    Job,
    Opportunity,
    Pipeline,
    Role,
    SavedView,
    Stage,
    Tag,
    User,
)
from .phones import format_phone, phone_warning, store_phone
from .transport import get_transport

# The gate is applied ONCE, app-wide, rather than decorating 25 routes. Any route
# added later is authenticated by default, so the cost of forgetting is a 401 rather
# than a leak. `require_auth` exempts a short allowlist by path (health, login,
# refresh, logout). Note it does NOT cover /docs and /openapi.json — those are plain
# Starlette routes with no dependant, verified against fastapi 0.141.
app = FastAPI(title="GHL Clone API",
              dependencies=[Depends(auth.require_auth)])

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# The browser softphone (`/api/softphone/*`) lives in its own module: it is the only
# part of this app that talks OUT to the telephony project, and it holds the machine key
# that does so. One router, mounted here, so the app-level auth gate covers it like
# everything else -- see app/softphone.py.
app.include_router(softphone.router)

# Postgres schema belongs to Alembic (`uv run alembic upgrade head`) — one source of
# truth, so a model edit without a revision fails loudly instead of half-applying.
# SQLite still self-creates: that is the tests' throwaway database and the no-server
# bootstrap path, neither of which is worth a migration run.
if os.getenv("AUTO_CREATE_ALL",
             "1" if DATABASE_URL.startswith("sqlite") else "0") == "1":
    Base.metadata.create_all(bind=engine)


# A search box is not a pattern language. `%` and `_` are LIKE wildcards, so typing
# either into one matched every row instead of narrowing to nothing -- the term is
# parameterised, but the wildcards inside it are still read as wildcards. Escape them
# (and the escape character itself) so the user's characters mean themselves.
LIKE_ESCAPE = "\\"


def contains(q: str) -> str:
    """A LIKE pattern matching `q` literally, anywhere. Use with `escape=LIKE_ESCAPE`."""
    for ch in (LIKE_ESCAPE, "%", "_"):
        q = q.replace(ch, LIKE_ESCAPE + ch)
    return "%" + q + "%"


# NOTE and INTERNAL_COMMENT are the team talking to itself on a thread: recorded
# for the crew, never transmitted to the customer (see automations.INTERNAL_TYPES,
# which is why DND never suppresses them). As of 2026-09-10 they are STAFF-only on
# every path, read and write -- DECISIONS.md carries the reasoning.
#
# The rule lives in ONE predicate, applied at every door: the thread view, the
# cross-thread message search, the global palette search, and the composer. The
# alternative considered was enforcing it only in the new /api/search, which was
# rejected: a TECH would then fail to find a word in the palette and read that same
# note two clicks later in Conversations, which is the inconsistent-gate failure
# DECISIONS.md already complains about once.
INTERNAL_TYPES = automations.INTERNAL_TYPES


def _sees_internal(principal: auth.Principal) -> bool:
    return principal.role in (Role.ADMIN, Role.DISPATCHER)


def _refuse_internal(principal: auth.Principal, verb: str) -> None:
    """403 for an explicit request for internal notes.

    Explicit asks are refused rather than silently emptied, so `--type NOTE` from
    the CLI says why it returned nothing. An unfiltered read is narrowed instead:
    an inbox that 403s because one hidden row exists would be unusable.
    """
    raise HTTPException(403, "role %s may not %s internal notes" % (
        principal.role.value, verb))


# ---------- schemas ----------

class ContactOut(BaseModel):
    """Columns measured on GHL's Contacts grid: Contact name, Phone, Email,
    Business name, Created (EDT), Tags, Last activity (EDT)."""
    id: int
    name: str
    first_name: str
    last_name: str
    email: str | None
    phone: str | None
    # E.164 is what we store; it is not what a person reads. The grid renders
    # this, so formatting lives in one place instead of in every client.
    phone_display: str | None
    # Non-blocking: a number we could not parse was still saved as typed, and this
    # is the only place anything says so. None means it looks fine.
    phone_warning: str | None
    business_name: str | None
    created_at: datetime
    tags: list[str] = []
    last_activity: datetime | None = None


class Page(BaseModel):
    items: list
    total: int
    page: int
    page_size: int
    pages: int


class StageOut(BaseModel):
    id: int
    name: str
    position: int


class OpportunityOut(BaseModel):
    id: int
    title: str
    value_cents: int
    stage_id: int
    contact_name: str | None


class EventOut(BaseModel):
    id: int
    type: str
    direction: str
    occurred_at: datetime
    body: str | None
    subject: str | None
    duration_seconds: int | None
    recording_url: str | None
    # CALL only, and nullable: an outbound call we placed has no outcome yet, and
    # the feed does not always send one. The thread renders a null as "Call" rather
    # than claiming it completed.
    call_status: str | None = None
    delivery_status: str | None
    # Why the message is in that state, as a sentence. Null when the status speaks
    # for itself; set for a refusal or a carrier failure, which are exactly the
    # cases where the status alone does not tell the operator what to do next.
    delivery_detail: str | None = None


# ---------- contacts ----------

SORTABLE = {"name": Contact.first_name, "email": Contact.email,
            "phone": Contact.phone, "business_name": Contact.business_name,
            "created_at": Contact.created_at}


@app.get("/api/contacts", response_model=Page)
def list_contacts(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    q: str | None = None,
    sort: str = "created_at",
    order: Literal["asc", "desc"] = "desc",
    _: auth.Principal = auth.ANY_USER):
    stmt = select(Contact)
    if q:
        # contains() + escape=, not a bare "%s" pattern: typing a % into the box
        # used to match every row here while narrowing to nothing in the palette,
        # because only one of the two escaped it. One matching convention.
        like = contains(q)
        esc = LIKE_ESCAPE
        terms = [Contact.first_name.ilike(like, escape=esc),
                 Contact.last_name.ilike(like, escape=esc),
                 # Searching a full name is the obvious thing to type,
                 # and matching the columns separately never does it:
                 # "jane doe" is in neither first_name nor last_name.
                 # Renders as || on both Postgres and SQLite.
                 (Contact.first_name + " "
                  + Contact.last_name).ilike(like, escape=esc),
                 Contact.email.ilike(like, escape=esc),
                 Contact.phone.ilike(like, escape=esc),
                 Contact.business_name.ilike(like, escape=esc)]
        # A number typed the way a phone shows it must find the row whatever
        # shape that row is stored in — see app/phone_match.py. Added to the
        # ILIKE terms rather than replacing them: a business named "24/7
        # Roofing" is phone-shaped once the slash goes, and it must still be
        # findable by its own name.
        if phone_match.looks_like_phone(q):
            terms.append(phone_match.phone_clause(Contact.phone, q))
        stmt = stmt.where(or_(*terms))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    col = SORTABLE.get(sort, Contact.created_at)
    stmt = stmt.order_by(col.desc() if order == "desc" else col.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    stmt = stmt.options(selectinload(Contact.tags).selectinload(ContactTag.tag))
    rows = db.scalars(stmt).all()

    # Last activity = most recent event on this contact's conversation.
    ids = [r.id for r in rows]
    activity = {}
    if ids:
        for cid, last in db.execute(
            select(Conversation.contact_id, func.max(ConversationEvent.occurred_at))
            .join(ConversationEvent,
                  ConversationEvent.conversation_id == Conversation.id)
            .where(Conversation.contact_id.in_(ids))
            .group_by(Conversation.contact_id)).all():
            activity[cid] = last

    # Built explicitly rather than via model_validate: the ORM `tags` attribute
    # holds ContactTag rows, not strings, so attribute-mode validation fails for
    # any contact that actually has a tag.
    items = [
        ContactOut(
            id=r.id, name=r.name, first_name=r.first_name, last_name=r.last_name,
            email=r.email, phone=r.phone, phone_display=format_phone(r.phone),
            phone_warning=phone_warning(r.phone),
            business_name=r.business_name,
            created_at=r.created_at,
            tags=[ct.tag.name for ct in r.tags if ct.tag],
            last_activity=activity.get(r.id),
        ).model_dump()
        for r in rows
    ]

    return Page(
        items=items,
        total=total, page=page, page_size=page_size,
        pages=max(1, (total + page_size - 1) // page_size),
    )


def _contact_detail(c: Contact) -> dict:
    """Shape mirrors the measured Contact Details panel."""
    return {
        "id": c.id,
        "name": c.name,
        "first_name": c.first_name,
        "last_name": c.last_name,
        "email": c.email,
        "phone": c.phone,
        "phone_display": format_phone(c.phone),
        "phone_warning": phone_warning(c.phone),
        "business_name": c.business_name,
        "source": c.source,
        # Read-only, and deliberately not on `ContactPatch`. The Workiz import is
        # the only writer today (`app/workiz_import.py`); the measured Contact
        # Details panel has no address control and adding one is a change to the
        # surface parity is judged on, which is a separate decision. Exposed here
        # because an address the import writes and nothing can read would be worse
        # than either.
        "address_street": c.address_street,
        "address_city": c.address_city,
        "address_state": c.address_state,
        "address_postal_code": c.address_postal_code,
        "date_of_birth": c.date_of_birth,
        "contact_type": c.contact_type,
        "dnd": c.dnd,
        "created_by": c.created_by,
        "created_at": c.created_at,
        "owner_id": c.owner_id,
        "owner_name": c.owner.name if c.owner else None,
        "tags": [{"id": ct.tag.id, "name": ct.tag.name, "color": ct.tag.color}
                 for ct in c.tags if ct.tag],
        # Named, not counted: DELETE /api/contacts/{id} refuses with 409 while this
        # list is non-empty, and the Actions tab has to say *which* opportunities
        # are about to be detached before anyone confirms. Reading them back out of
        # the 409's prose would tie the panel to the wording of an error message.
        "opportunities": [{"id": o.id, "title": o.title} for o in c.opportunities],
        "custom_fields": c.custom_fields or {},
    }


@app.get("/api/contacts/{contact_id}")
def get_contact(contact_id: int, db: Session = Depends(get_db),
                _: auth.Principal = auth.ANY_USER):
    c = db.get(Contact, contact_id)
    if not c:
        raise HTTPException(404, "contact not found")
    return _contact_detail(c)


# Deliberately loose: one @, something either side, a dot in the domain. Enough to
# catch a typo or a pasted name; not an attempt to out-parse RFC 5322, which would
# reject addresses that work.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _blank_to_none(v: str | None) -> str | None:
    """Whitespace is not a contact detail.

    Without this, "   " satisfies the "needs a phone or an email" guard and stores a
    contact with no reachable channel that renders as a blank row.
    """
    if v is None:
        return None
    return v.strip() or None


def _check_email(v: str | None) -> str | None:
    if v is not None and not EMAIL_RE.match(v):
        raise ValueError("not a valid email address")
    return v


def _check_phone(v: str | None) -> str | None:
    """Store E.164 when the number parses, and what was typed when it does not.

    Deliberately *unlike* `_check_email`, which raises and so answers 422. The
    owner's decision (2026-09-10): a phone number can never block a save. Staff
    enter numbers with a customer in front of them, and refusing an unfamiliar
    shape at that moment loses the number entirely. `store_phone` cannot raise, so
    this validator cannot turn a save into a 422; `phone_warning` on the way back
    out is what tells anyone the number looked odd.

    Applies to writes only — nothing rewrites an existing row.
    """
    return store_phone(v)


class ContactPatch(BaseModel):
    """All optional — the panel saves one field at a time as it is edited.

    The max_lengths mirror the columns in models.py. Without them an over-long value
    reaches Postgres and comes back as StringDataRightTruncation, which FastAPI turns
    into a bare `500 Internal Server Error` with nothing naming the field. Declared
    here, the request is refused before the database is touched.
    """
    first_name: str | None = Field(None, max_length=120)
    last_name: str | None = Field(None, max_length=120)
    email: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=40)
    business_name: str | None = Field(None, max_length=200)
    source: str | None = Field(None, max_length=120)
    date_of_birth: str | None = Field(None, max_length=40)
    contact_type: str | None = Field(None, max_length=40)
    dnd: bool | None = None
    owner_id: int | None = None

    _strip = field_validator("first_name", "last_name", "email", "phone",
                             "business_name", "source", mode="after")(_blank_to_none)
    _email = field_validator("email", mode="after")(_check_email)
    _phone = field_validator("phone", mode="after")(_check_phone)


@app.patch("/api/contacts/{contact_id}")
def update_contact(contact_id: int, body: ContactPatch,
                   db: Session = Depends(get_db),
                   _: auth.Principal = auth.STAFF):
    c = db.get(Contact, contact_id)
    if not c:
        raise HTTPException(404, "contact not found")
    if body.owner_id is not None and not db.get(User, body.owner_id):
        raise HTTPException(400, "unknown owner_id")
    # exclude_unset so an omitted field is left alone rather than nulled.
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(c, k, v)
    db.commit()
    db.refresh(c)
    return _contact_detail(c)


class TagBody(BaseModel):
    name: str


@app.post("/api/contacts/{contact_id}/tags", status_code=201)
def add_tag(contact_id: int, body: TagBody, db: Session = Depends(get_db),
            _: auth.Principal = auth.STAFF):
    c = db.get(Contact, contact_id)
    if not c:
        raise HTTPException(404, "contact not found")
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "tag name is required")
    tag = db.scalar(select(Tag).where(Tag.name == name))
    if not tag:
        tag = Tag(name=name)
        db.add(tag)
        db.flush()
    if not any(ct.tag_id == tag.id for ct in c.tags):
        db.add(ContactTag(contact_id=c.id, tag_id=tag.id))
    db.commit()
    db.refresh(c)
    return _contact_detail(c)


@app.delete("/api/contacts/{contact_id}/tags/{tag_id}")
def remove_tag(contact_id: int, tag_id: int, db: Session = Depends(get_db),
               _: auth.Principal = auth.STAFF):
    c = db.get(Contact, contact_id)
    if not c:
        raise HTTPException(404, "contact not found")
    link = db.scalar(select(ContactTag).where(
        ContactTag.contact_id == contact_id, ContactTag.tag_id == tag_id))
    if link:
        db.delete(link)
        db.commit()
        db.refresh(c)
    return _contact_detail(c)


# ---------- opportunities ----------

@app.get("/api/pipelines")
def list_pipelines(db: Session = Depends(get_db),
                   _: auth.Principal = auth.ANY_USER):
    pipelines = db.scalars(
        select(Pipeline).options(selectinload(Pipeline.stages))).all()
    out = []
    for p in pipelines:
        stages = []
        for s in p.stages:
            rows = db.execute(
                select(func.count(Opportunity.id), func.coalesce(
                    func.sum(Opportunity.value_cents), 0))
                .where(Opportunity.stage_id == s.id)).one()
            stages.append({"id": s.id, "name": s.name, "position": s.position,
                           "count": rows[0], "value_cents": int(rows[1])})
        out.append({"id": p.id, "name": p.name, "stages": stages})
    return out


# ---------- pipeline structure (create / rename / reorder / delete) ----------
#
# There were no write endpoints for pipelines or stages at all until 2026-09-10;
# the structure came from the seed and was otherwise fixed. It is now editable,
# which is a deliberate divergence from the measured GHL capture — see the dated
# amendment in DECISIONS.md.
#
# Everything here is ADMIN. Renaming a stage changes a label four people navigate
# by and that the `ghl` CLI resolves against; deleting one removes a column of the
# board. That is not a dispatcher's call.
#
# Two rules do the safety work:
#
#   1. DELETE ONLY WHAT IS EMPTY. A stage holding opportunities cannot be deleted,
#      and the refusal says how many are in the way. There is no `force`. Cascading
#      would take real customer deals with it, and `custom_fields.owen_call_id` is
#      the telephony project's join key — CLAUDE.md warns specifically against
#      deleting opportunities as a side effect of tidying something else.
#   2. REORDERING MOVES COLUMNS, NEVER DEALS. Reorder writes `Stage.position` and
#      nothing else; no opportunity's `stage_id` is touched. The endpoint takes the
#      full stage list as a permutation, so "move this one left" cannot be
#      misapplied to a board that changed underneath the user.
#
# Names are NOT made unique. The measured pipeline has two distinct stages both
# called "Call Back" (DECISIONS.md), the `ghl` CLI exits 5 rather than guess
# between them, and quietly de-duplicating names here would break that.

PIPELINE_NAME_MAX = 160


class PipelineBody(BaseModel):
    name: str = Field(max_length=PIPELINE_NAME_MAX)


class StageBody(BaseModel):
    name: str = Field(max_length=PIPELINE_NAME_MAX)


class StageReorder(BaseModel):
    """The pipeline's stages in their new order — every one of them, exactly once."""
    stage_ids: list[int] = Field(min_length=1)


def _clean_structure_name(name: str | None, kind: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise HTTPException(400, "a %s needs a name" % kind)
    return cleaned


def _opportunity_count(db: Session, **where) -> int:
    stmt = select(func.count(Opportunity.id))
    for column, value in where.items():
        stmt = stmt.where(getattr(Opportunity, column) == value)
    return db.scalar(stmt) or 0


@app.post("/api/pipelines", status_code=201)
def create_pipeline(body: PipelineBody, db: Session = Depends(get_db),
                    _: auth.Principal = auth.ADMIN):
    """A new pipeline starts with no stages, so nothing can be filed in it yet.

    Deliberately not seeded with a default set of stages: guessing at a roofing
    pipeline's shape is exactly the kind of invention this project avoids, and an
    empty pipeline is honest about needing its columns named.
    """
    n = db.scalar(select(func.count(Pipeline.id))) or 0
    p = Pipeline(name=_clean_structure_name(body.name, "pipeline"), position=n)
    db.add(p)
    db.commit()
    db.refresh(p)
    return {"id": p.id, "name": p.name, "position": p.position, "stages": []}


@app.patch("/api/pipelines/{pipeline_id}")
def rename_pipeline(pipeline_id: int, body: PipelineBody,
                    db: Session = Depends(get_db),
                    _: auth.Principal = auth.ADMIN):
    p = db.get(Pipeline, pipeline_id)
    if not p:
        raise HTTPException(404, "pipeline not found")
    p.name = _clean_structure_name(body.name, "pipeline")
    db.commit()
    return {"id": p.id, "name": p.name}


@app.delete("/api/pipelines/{pipeline_id}")
def delete_pipeline(pipeline_id: int, db: Session = Depends(get_db),
                    _: auth.Principal = auth.ADMIN):
    """Only an EMPTY pipeline. No force, at any role.

    `Pipeline.stages` cascades delete-orphan, so allowing this while stages
    existed would silently take the columns — and every deal in them — with it.
    The refusal names what is in the way, the way the contact delete's 409 does.
    """
    p = db.get(Pipeline, pipeline_id)
    if not p:
        raise HTTPException(404, "pipeline not found")

    stages = len(p.stages)
    opps = _opportunity_count(db, pipeline_id=p.id)
    if stages or opps:
        raise HTTPException(409, (
            "%r still has %d stage%s and %d opportunit%s. Empty it first — a "
            "pipeline is never deleted with deals in it." % (
                p.name, stages, "" if stages == 1 else "s",
                opps, "y" if opps == 1 else "ies")))

    # Weak references: both columns are nullable and neither carries data of its
    # own. Detached rather than blocking the delete, and named in the response so
    # it is not a silent side effect.
    views = db.scalars(
        select(SavedView).where(SavedView.pipeline_id == p.id)).all()
    calendars = db.scalars(
        select(Calendar).where(Calendar.pipeline_id == p.id)).all()
    for v in views:
        v.pipeline_id = None
    for c in calendars:
        c.pipeline_id = None
    # A custom field attached to this pipeline loses the ATTACHMENT, never the
    # definition and never an answer: the field goes on existing elsewhere, and a
    # deal that was in this pipeline keeps everything recorded on it.
    links = db.scalars(select(CustomFieldPipeline).where(
        CustomFieldPipeline.pipeline_id == p.id)).all()
    detached_fields = sorted({link.field_id for link in links})
    for link in links:
        db.delete(link)

    db.delete(p)
    db.commit()
    return {"deleted": pipeline_id,
            "detached_saved_views": [v.id for v in views],
            "detached_calendars": [c.id for c in calendars],
            "detached_custom_fields": detached_fields}


@app.post("/api/pipelines/{pipeline_id}/stages", status_code=201)
def create_stage(pipeline_id: int, body: StageBody, db: Session = Depends(get_db),
                 _: auth.Principal = auth.ADMIN):
    """Appended at the end. A new stage is empty, so nothing moves."""
    p = db.get(Pipeline, pipeline_id)
    if not p:
        raise HTTPException(404, "pipeline not found")
    n = db.scalar(select(func.count(Stage.id))
                  .where(Stage.pipeline_id == p.id)) or 0
    s = Stage(pipeline_id=p.id, name=_clean_structure_name(body.name, "stage"),
              position=n)
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"id": s.id, "pipeline_id": s.pipeline_id, "name": s.name,
            "position": s.position, "count": 0, "value_cents": 0}


@app.patch("/api/stages/{stage_id}")
def rename_stage(stage_id: int, body: StageBody, db: Session = Depends(get_db),
                 _: auth.Principal = auth.ADMIN):
    """A rename, and ONLY a rename.

    There is no `pipeline_id` here on purpose: moving a stage to another pipeline
    would carry every opportunity in it across, and `PATCH /api/opportunities/{id}`
    refuses a cross-pipeline move for that reason. Names stay non-unique — two
    "Call Back" stages are the measured shape, and the CLI's exit 5 depends on it.
    """
    s = db.get(Stage, stage_id)
    if not s:
        raise HTTPException(404, "stage not found")
    s.name = _clean_structure_name(body.name, "stage")
    db.commit()
    return {"id": s.id, "pipeline_id": s.pipeline_id, "name": s.name,
            "position": s.position}


@app.delete("/api/stages/{stage_id}")
def delete_stage(stage_id: int, db: Session = Depends(get_db),
                 _: auth.Principal = auth.ADMIN):
    """Only an EMPTY stage. No force.

    A populated stage is refused with the count in the message, so the admin knows
    what to move where before trying again. The remaining stages are re-packed so
    positions stay 0..n-1 with no hole.
    """
    s = db.get(Stage, stage_id)
    if not s:
        raise HTTPException(404, "stage not found")

    held = _opportunity_count(db, stage_id=s.id)
    if held:
        raise HTTPException(409, (
            "%r still holds %d opportunit%s. Move %s to another stage first — "
            "deleting a stage never deletes the deals in it." % (
                s.name, held, "y" if held == 1 else "ies",
                "it" if held == 1 else "them")))

    pipeline_id = s.pipeline_id
    db.delete(s)
    db.flush()
    for i, remaining in enumerate(db.scalars(
            select(Stage).where(Stage.pipeline_id == pipeline_id)
            .order_by(Stage.position)).all()):
        remaining.position = i
    db.commit()
    return {"deleted": stage_id, "pipeline_id": pipeline_id}


@app.post("/api/pipelines/{pipeline_id}/stages/reorder")
def reorder_stages(pipeline_id: int, body: StageReorder,
                   db: Session = Depends(get_db),
                   _: auth.Principal = auth.ADMIN):
    """Re-order the columns. NO OPPORTUNITY MOVES.

    Only `Stage.position` is written; nothing touches `Opportunity.stage_id`. The
    request must name every stage of this pipeline exactly once, so a board that
    changed underneath the user is refused outright rather than half-applied — and
    a stage from another pipeline cannot be smuggled in.
    """
    p = db.get(Pipeline, pipeline_id)
    if not p:
        raise HTTPException(404, "pipeline not found")

    current = {s.id: s for s in p.stages}
    wanted = body.stage_ids
    if len(set(wanted)) != len(wanted) or set(wanted) != set(current):
        raise HTTPException(400, (
            "the new order must name every stage of this pipeline exactly once "
            "(%d stages: %s)" % (len(current),
                                 ", ".join(str(i) for i in sorted(current)))))

    for i, stage_id in enumerate(wanted):
        current[stage_id].position = i
    db.commit()
    return {"pipeline_id": p.id,
            "stages": [{"id": current[i].id, "name": current[i].name,
                        "position": current[i].position} for i in wanted]}


# ---------- custom field definitions ----------
#
# The owner defines the job questions himself: "How many stories?", "Where is the
# leak located?", "How old is the roof?". Five types, no multi-select (deferred on
# purpose), and NO required flag — an inbound call at 2am must still become a deal,
# and a required field would mean a missed lead.
#
# Definitions live here; the ANSWERS live where they always have, in the existing
# `Opportunity.custom_fields` JSON blob. There is no answers table and no data
# migration: this feature describes a blob that was already there.
#
# Everything that writes is ADMIN, matching the pipeline structure endpoints above
# — a field definition is configuration four people then have to answer. Reading is
# ANY_USER, because the opportunity form has to render the questions for whoever
# opens it, and the panel disables what a role may not use rather than 403ing on
# submit (d1f7c50, b943f4b).
#
# DELETE ARCHIVES. It never removes the row and never touches the blob, so the
# answers already recorded survive and stay readable on the deals that hold them.
# `POST .../restore` brings the field back.


class CustomFieldCreate(BaseModel):
    label: str = Field(max_length=custom_fields.LABEL_MAX)
    field_type: str
    options: list[str] | None = None
    pipeline_ids: list[int] = Field(default_factory=list)


class CustomFieldPatch(BaseModel):
    """No `field_type`, and no `key`, deliberately.

    Retyping a dropdown as a number would leave every answer already recorded
    failing its own field's validation, and the key is what those answers are
    filed under. Both are fixed at creation; the label, the options, the pipelines
    and the order are not.
    """
    label: str | None = Field(None, max_length=custom_fields.LABEL_MAX)
    options: list[str] | None = None
    pipeline_ids: list[int] | None = None


class CustomFieldReorder(BaseModel):
    """Every live field exactly once — the same permutation contract as stages."""
    field_ids: list[int] = Field(min_length=1)


def _check_pipelines(db: Session, ids: list[int] | None) -> list[int]:
    unique = sorted(set(ids or []))
    for pipeline_id in unique:
        if not db.get(Pipeline, pipeline_id):
            raise HTTPException(400, "unknown pipeline_id %d" % pipeline_id)
    return unique


def _get_field(db: Session, field_id: int) -> CustomFieldDef:
    d = db.get(CustomFieldDef, field_id)
    if not d:
        raise HTTPException(404, "custom field not found")
    return d


@app.get("/api/custom-fields")
def list_custom_fields(db: Session = Depends(get_db),
                       include_archived: bool = True,
                       pipeline_id: int | None = None,
                       _: auth.Principal = auth.ANY_USER):
    """Every definition, in display order.

    Archived ones are included by default because the opportunity form needs them:
    a deal holding an answer to an archived field still has to be able to LABEL it,
    and a list that hid them would render "roof_age: 14" as a bare key.
    """
    out = [custom_fields.describe(d)
           for d in custom_fields.load_defs(db, include_archived=include_archived)]
    if pipeline_id is not None:
        out = [d for d in out if pipeline_id in d["pipeline_ids"] and not d["archived"]]
    return out


@app.post("/api/custom-fields", status_code=201)
def create_custom_field(body: CustomFieldCreate, db: Session = Depends(get_db),
                        _: auth.Principal = auth.ADMIN):
    label = custom_fields.clean_label(body.label)
    field_type = custom_fields.clean_type(body.field_type)
    options = custom_fields.clean_options(field_type, body.options)
    # Refuses the `owen_` namespace and a key another field already holds. The
    # check is on the DERIVED key, so "Owen campaign" is refused too.
    key = custom_fields.claim_key(db, label)
    pipelines = _check_pipelines(db, body.pipeline_ids)

    n = db.scalar(select(func.count(CustomFieldDef.id))) or 0
    d = CustomFieldDef(key=key, label=label, field_type=field_type,
                       options=options, position=n, entity="opportunity")
    db.add(d)
    db.flush()
    custom_fields.attach(db, d, pipelines)
    db.commit()
    db.refresh(d)
    return custom_fields.describe(d)


@app.patch("/api/custom-fields/{field_id}")
def update_custom_field(field_id: int, body: CustomFieldPatch,
                        db: Session = Depends(get_db),
                        _: auth.Principal = auth.ADMIN):
    """Reword the question, change the choices, move it between pipelines.

    The key never moves with the label, so answers stay attached to the field they
    were given for. Detaching a pipeline HIDES the field on that pipeline's deals;
    it does not delete a single answer — see custom_fields.merge_answers.
    """
    d = _get_field(db, field_id)
    data = body.model_dump(exclude_unset=True)
    if "label" in data:
        d.label = custom_fields.clean_label(data["label"])
    if "options" in data:
        d.options = custom_fields.clean_options(d.field_type, data["options"])
    if "pipeline_ids" in data:
        custom_fields.attach(db, d, _check_pipelines(db, data["pipeline_ids"]))
    db.commit()
    db.refresh(d)
    return custom_fields.describe(d)


@app.delete("/api/custom-fields/{field_id}")
def archive_custom_field(field_id: int, db: Session = Depends(get_db),
                         _: auth.Principal = auth.ADMIN):
    """DELETE archives. It is not a soft delete waiting for a hard one.

    The answers in `Opportunity.custom_fields` are customer information somebody
    typed, and there is no endpoint anywhere that destroys them. Archiving takes
    the question off new deals and leaves every recorded answer readable on the
    deals that hold it; restore puts the question back.
    """
    d = _get_field(db, field_id)
    if d.archived_at is None:
        d.archived_at = models.utcnow()
        db.commit()
    return {"archived": d.id, "key": d.key,
            "note": "answers already recorded are kept and stay visible"}


@app.post("/api/custom-fields/{field_id}/restore")
def restore_custom_field(field_id: int, db: Session = Depends(get_db),
                         _: auth.Principal = auth.ADMIN):
    d = _get_field(db, field_id)
    d.archived_at = None
    db.commit()
    db.refresh(d)
    return custom_fields.describe(d)


@app.post("/api/custom-fields/reorder")
def reorder_custom_fields(body: CustomFieldReorder, db: Session = Depends(get_db),
                          _: auth.Principal = auth.ADMIN):
    """The whole live list as a permutation, for the reason the stage reorder is:
    a list that changed underneath the user is refused outright rather than
    half-applied. Archived fields keep the position they had."""
    live = custom_fields.load_defs(db, include_archived=False)
    if sorted(body.field_ids) != sorted(d.id for d in live):
        raise HTTPException(400, (
            "reorder takes every live custom field exactly once — expected %d "
            "ids, got %d" % (len(live), len(set(body.field_ids)))))
    by_id = {d.id: d for d in live}
    for position, field_id in enumerate(body.field_ids):
        by_id[field_id].position = position
    db.commit()
    return [custom_fields.describe(d)
            for d in custom_fields.load_defs(db, include_archived=False)]


@app.get("/api/opportunities")
def list_opportunities(
    db: Session = Depends(get_db),
    pipeline_id: int | None = None,
    q: str | None = None,
    status: str = "open",
    _: auth.Principal = auth.ANY_USER):
    """`status` mirrors GHL's measured default advanced filter: Status is any of Open.

    `pipeline_id` was required and is now optional. The board always sends one;
    omitting it lists across every pipeline, which is what the appointment dialog
    needs to offer "bind this booking to an open deal" without knowing, or caring,
    which board the deal is filed on.
    """
    stmt = (select(Opportunity)
            .options(selectinload(Opportunity.contact)))
    if pipeline_id is not None:
        stmt = stmt.where(Opportunity.pipeline_id == pipeline_id)
    if status != "all":
        stmt = stmt.where(Opportunity.status == status)
    if q:
        stmt = stmt.where(Opportunity.title.ilike(contains(q), escape=LIKE_ESCAPE))
    # `id` is the tiebreak, not decoration: `position` defaults to 0, so any rows
    # written before a re-pack can share one, and without it the board's order
    # changes between two identical refetches — which the optimistic drag would
    # then read as the server disagreeing with it.
    rows = db.scalars(stmt.order_by(Opportunity.position, Opportunity.id)).all()
    return [{"id": o.id, "title": o.title, "value_cents": o.value_cents,
             "stage_id": o.stage_id, "pipeline_id": o.pipeline_id,
             "status": o.status, "position": o.position,
             "contact_name": o.contact.name if o.contact else None,
             "business_name": o.contact.business_name if o.contact else None,
             "source": o.contact.source if o.contact else None,
             "updated_at": o.updated_at} for o in rows]


class OpportunityMove(BaseModel):
    # A negative position is not "the end", it is Python's negative slicing: -1 would
    # quietly file the card second-from-bottom. The board never sends one, so refuse
    # it rather than reorder the column in a way nobody asked for.
    stage_id: int
    position: int = Field(0, ge=0)


@app.patch("/api/opportunities/{opp_id}")
def move_opportunity(opp_id: int, body: OpportunityMove,
                     db: Session = Depends(get_db),
                     _: auth.Principal = auth.ANY_USER):
    """Drag a card between stages. Mutates OUR database only."""
    o = db.get(Opportunity, opp_id)
    if not o:
        raise HTTPException(404, "opportunity not found")
    stage = db.get(Stage, body.stage_id)
    if not stage or stage.pipeline_id != o.pipeline_id:
        raise HTTPException(400, "stage is not in this opportunity's pipeline")

    old_stage_id = o.stage_id
    # Re-pack positions within the destination stage so ordering stays stable.
    o.stage_id = body.stage_id
    o.position = body.position
    siblings = db.scalars(
        select(Opportunity)
        .where(Opportunity.stage_id == body.stage_id, Opportunity.id != o.id)
        .order_by(Opportunity.position, Opportunity.id)).all()
    ordered = [*siblings[:body.position], o, *siblings[body.position:]]
    for i, s in enumerate(ordered):
        s.position = i
    # ...and re-pack the stage it LEFT. Only the destination was packed before, so a
    # card dragged out of the middle left a hole (0, 2, 3). Nothing rendered wrong —
    # the board only reads the order — but the browser predicts the new positions
    # locally to move the card the instant it is dropped, and it can only do that
    # against a column it knows is contiguous.
    #
    # `id != o.id` is load-bearing, not tidiness: the session is autoflush=False, so
    # the card being dragged is still filed under its OLD stage in the database at
    # this point. Without it the query returns the card itself and the loop below
    # resets the position that was just chosen for it — every drop landed at the top
    # of the destination, which is the exact bug this change exists to fix.
    if old_stage_id != body.stage_id:
        left = db.scalars(
            select(Opportunity)
            .where(Opportunity.stage_id == old_stage_id, Opportunity.id != o.id)
            .order_by(Opportunity.position, Opportunity.id)).all()
        for i, left_over in enumerate(left):
            left_over.position = i
    # Rule 4: stage move -> text the customer.
    outcome = automations.on_opportunity_stage_changed(db, o, old_stage_id)
    db.commit()
    return {"id": o.id, "stage_id": o.stage_id, "position": o.position,
            "automation": outcome}


class ContactCreate(BaseModel):
    # max_lengths mirror the columns in models.py — see ContactPatch.
    first_name: str = Field("", max_length=120)
    last_name: str = Field("", max_length=120)
    email: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=40)
    business_name: str | None = Field(None, max_length=200)
    source: str | None = Field(None, max_length=120)

    _strip = field_validator("first_name", "last_name", "email", "phone",
                             "business_name", "source", mode="after")(_blank_to_none)
    _email = field_validator("email", mode="after")(_check_email)
    _phone = field_validator("phone", mode="after")(_check_phone)


@app.post("/api/contacts", status_code=201)
def create_contact(body: ContactCreate, db: Session = Depends(get_db),
                   _: auth.Principal = auth.STAFF):
    if not (body.phone or body.email):
        raise HTTPException(400, "a contact needs at least a phone or an email")
    c = Contact(**body.model_dump(), created_by="Manual")
    db.add(c)
    db.flush()
    # Rule 2: new lead -> notify the team.
    outcome = automations.on_contact_created(db, c)
    db.commit()
    db.refresh(c)
    return {**_contact_detail(c), "automation": outcome}


# The measured Call report groups calls by these five statuses (DECISIONS.md).
CALL_STATUSES = {"completed", "no-answer", "busy", "voicemail", "failed"}
# What a call with no status at all is reported as. NOT "completed": the column is
# nullable, the feed does not always fill it, and a blank is an unknown outcome.
UNKNOWN_CALL_STATUS = "unknown"


class EventIngest(BaseModel):
    """Ingest endpoint for the telephony project (owen-main).

    Inbound calls and messages are plain data we own; they do NOT go through
    MessageTransport, which governs outbound side effects only.

    `contact_id` is OPTIONAL as of 2026-09-11. It used to be required, and that is
    what made an inbound call from a stranger impossible to file: owen-main's
    `client.resolve_contact_id` searches our contacts, and when nothing matched it
    dropped the event rather than post one we would 404. A first-time caller — the
    new roofing lead the owner most wants — therefore reached nobody.

    Sending `from_number` instead (or as well) lets us do the matching here, where
    the contact can also be created. `caller_number` is accepted as a synonym
    because that is what owen-main calls the same field internally
    (`events.CallEventFacts.caller_number`), so whichever name the far side sends,
    it lands.
    """
    contact_id: int | None = None
    # The customer's number. Matched to a contact on the last ten digits, and a
    # contact is created when nothing matches.
    from_number: str | None = Field(
        default=None, validation_alias=AliasChoices("from_number", "caller_number"))
    type: Literal["SMS", "CALL", "EMAIL", "INTERNAL_COMMENT"]
    direction: Literal["INBOUND", "OUTBOUND"] = "INBOUND"
    body: str | None = None
    duration_seconds: int | None = None
    # CALL only. The Call report's "Call by status" donut is built from this, so a
    # feed that cannot send it leaves every ingested call with an unknown outcome.
    call_status: str | None = None
    recording_url: str | None = None
    provider_ref: str | None = None

    # owen-main's job payloads carry more than this (linkedid, outcome, winning
    # destination...) and are explicitly documented as forward-compatible. Ignoring
    # unknown keys rather than 422-ing on them means a newer owen-main deploy cannot
    # break ingest. This is Pydantic's default; stated so it is not "fixed" later.
    model_config = {"extra": "ignore"}


def _contact_by_number(db: Session, number: str) -> Contact | None:
    """The contact whose phone is this line, matched on the last ten digits.

    The identity rule is shared with the picker, the Contacts search and owen-main
    itself (DECISIONS.md, 2026-09-10) — two systems that disagree about whether two
    renderings are the same line would file a customer's call on the wrong timeline.

    Ordered by id so a database that already holds two contacts for one number (the
    duplicate guard warns rather than blocks, deliberately) resolves to the same one
    every time rather than to whichever the planner happened to return first.
    """
    if not phone_match.looks_like_phone(number):
        return None
    return db.scalars(
        select(Contact)
        .where(phone_match.phone_clause(Contact.phone, number))
        .order_by(Contact.id)
    ).first()


def _resolve_ingest_contact(db: Session, body: EventIngest) -> Contact:
    """The contact an ingested event belongs to, creating one if need be.

    Three paths, in order of how much the caller claims to know:

      1. an explicit `contact_id` — must exist, else 404. Unchanged: this is the
         path owen-main uses today and the one its own tests pin.
      2. a `from_number` that matches an existing contact on the last ten digits.
      3. a `from_number` that matches nothing — a contact is CREATED, named by the
         number, so a first-time caller becomes a lead instead of being dropped.
    """
    if body.contact_id is not None:
        contact = db.get(Contact, body.contact_id)
        if not contact:
            raise HTTPException(404, "contact not found")
        return contact

    number = (body.from_number or "").strip()
    if not number:
        raise HTTPException(
            422, "an event needs either contact_id or from_number")

    existing = _contact_by_number(db, number)
    if existing is not None:
        return existing

    # A stranger called. Create them rather than lose them — the owner's rule is
    # that a new roofing lead is never lost, and an unnamed contact carrying a real
    # number is recoverable while a dropped event is not.
    #
    # The display name IS the number, formatted, because that is the only true
    # thing we know about them. `store_phone` cannot raise (DECISIONS.md): a number
    # we could not parse is stored exactly as it arrived rather than refused.
    stored = store_phone(number)
    contact = Contact(
        first_name=format_phone(stored) or number,
        last_name="",
        phone=stored,
        source="Inbound call",
        created_by="owen-main",
        contact_type="Lead",
    )
    db.add(contact)
    db.flush()
    # Rule 2 — a contact that appeared from nowhere is exactly the case the
    # new-lead notification exists for, so the crew hears about it.
    automations.on_contact_created(db, contact)
    return contact


@app.post("/api/events", status_code=201)
def ingest_event(body: EventIngest, db: Session = Depends(get_db),
                 _: auth.Principal = auth.EVENTS_INGEST):
    contact = _resolve_ingest_contact(db, body)
    if body.call_status is not None:
        if body.type != "CALL":
            raise HTTPException(400, "call_status is only meaningful on a CALL")
        if body.call_status not in CALL_STATUSES:
            raise HTTPException(400, "unknown call_status %r — expected one of %s"
                                % (body.call_status, sorted(CALL_STATUSES)))

    conv = db.scalar(select(Conversation).where(
        Conversation.contact_id == contact.id))
    if conv is None:
        conv = Conversation(contact_id=contact.id)
        db.add(conv)
        db.flush()

    ev = ConversationEvent(
        conversation_id=conv.id,
        type=EventType[body.type],
        direction=Direction[body.direction],
        body=body.body,
        duration_seconds=body.duration_seconds,
        call_status=body.call_status,
        recording_url=body.recording_url,
        provider_ref=body.provider_ref,
    )
    db.add(ev)
    db.flush()
    conv.last_event_at = ev.occurred_at
    if body.direction == "INBOUND":
        conv.unread_count += 1

    # Rule 1: missed call -> auto text back.
    outcome = automations.on_inbound_call(db, ev)
    db.commit()
    return {"id": ev.id, "conversation_id": conv.id, "contact_id": contact.id,
            "automation": outcome}


# A BulkVS delivery receipt's vocabulary, mapped onto ours. `undelivered` and
# `blocked` are carrier words for "it did not arrive and it is not going to" —
# the owner's question is whether the text arrived, and for all three the answer is
# no, so they collapse onto FAILED with the carrier's own word kept in the detail.
# Taken from owen-main's `services/sms.OUTBOUND_STATUS_RANK`.
DELIVERY_RECEIPT_STATUSES = {
    "queued": DeliveryStatus.QUEUED,
    "sent": DeliveryStatus.SENT,
    "delivered": DeliveryStatus.DELIVERED,
    "failed": DeliveryStatus.FAILED,
    "undelivered": DeliveryStatus.FAILED,
    "blocked": DeliveryStatus.FAILED,
}


class DeliveryReceipt(BaseModel):
    """One carrier delivery receipt, relayed by owen-main.

    `provider_ref` is the id owen-main gave us when it accepted the message — its
    `message_id`, which we stored on the event. It is the only join key: the CRM
    never sees the BulkVS RefId.
    """
    provider_ref: str
    status: str
    # The carrier's failure text, when it gave one. Shown to the operator verbatim
    # beneath the message, because "failed" alone does not tell them whether to try
    # a different number or wait.
    detail: str | None = None

    model_config = {"extra": "ignore"}


@app.post("/api/events/delivery")
def ingest_delivery_receipt(body: DeliveryReceipt, db: Session = Depends(get_db),
                            _: auth.Principal = auth.EVENTS_INGEST):
    """Advance an outbound message's delivery state from a carrier receipt.

    **NOT WIRED ON THE FAR SIDE YET.** owen-main receives these on
    `/webhooks/bulkvs/message-status` and updates its OWN `messages` row; it does
    not relay them anywhere. Verified by reading it, not assumed — see the sentinel.
    This endpoint is the CRM half of that relay, and it is the half that could be
    built here; the forwarding call is a change to owen-main, which another agent
    owns.

    Forward-only, via `advance_delivery`: carriers re-send receipts and deliver
    them out of order, and a late "sent" must never walk a delivered message back.
    Applying that rule here rather than trusting the order they arrive in is the
    same choice owen-main makes about its own copy of the row.
    """
    key = (body.provider_ref or "").strip()
    if not key:
        raise HTTPException(422, "a delivery receipt needs a provider_ref")

    word = (body.status or "").strip().lower()
    if word not in DELIVERY_RECEIPT_STATUSES:
        raise HTTPException(
            400, "unknown delivery status %r — expected one of %s"
            % (body.status, sorted(DELIVERY_RECEIPT_STATUSES)))
    incoming = DELIVERY_RECEIPT_STATUSES[word]

    ev = db.scalars(
        select(ConversationEvent)
        .where(ConversationEvent.provider_ref == key,
               ConversationEvent.direction == Direction.OUTBOUND)
        .order_by(ConversationEvent.id.desc())
    ).first()
    if ev is None:
        raise HTTPException(404, "no outbound message with provider_ref %r" % key)

    before = ev.delivery_status
    ev.delivery_status = models.advance_delivery(before, incoming)
    # Keep the carrier's words only while they explain something. A receipt that
    # advances a message to DELIVERED clears a stale failure note rather than
    # leaving "carrier rejected" sitting under a message that plainly arrived.
    if ev.delivery_status is DeliveryStatus.FAILED:
        ev.delivery_detail = (body.detail or "").strip() or "the carrier could not deliver it"
    elif ev.delivery_status is DeliveryStatus.DELIVERED:
        ev.delivery_detail = None
    db.commit()

    return {
        "id": ev.id,
        "conversation_id": ev.conversation_id,
        "delivery_status": ev.delivery_status.value if ev.delivery_status else None,
        # False when a stale or out-of-order receipt was correctly ignored. The
        # relay needs to tell "we applied it" from "we already knew better".
        "advanced": ev.delivery_status is not before,
    }


class AppointmentCreate(BaseModel):
    title: str
    starts_at: datetime
    ends_at: datetime
    contact_id: int | None = None
    assigned_user_id: int | None = None
    # Was missing, so every appointment created through the API landed on no
    # calendar at all and never appeared under a calendar filter.
    calendar_id: int | None = None
    # The deal this visit is for. Optional in both directions: a booking made from
    # an opportunity arrives with it filled in, one made on the calendar can pick
    # an open deal or none at all. Never required — a call at 2am becomes a visit
    # before anybody has filed a deal for it.
    opportunity_id: int | None = None
    notes: str | None = None


def _clean_appointment_title(title: str) -> str:
    """`title: str` accepts "" and "   ", so an untitled booking used to be created
    happily and then rendered as an empty chip on the calendar — indistinguishable
    from a rendering bug. Rejected here rather than only in the browser, so the
    CLI and the telephony feed get the same answer, and shared with PATCH so an
    *edit* cannot blank a title the create path refuses to accept."""
    cleaned = title.strip()
    if not cleaned:
        raise HTTPException(400, "an appointment needs a title")
    return cleaned


@app.post("/api/appointments", status_code=201)
def create_appointment(body: AppointmentCreate, db: Session = Depends(get_db),
                       _: auth.Principal = auth.STAFF):
    title = _clean_appointment_title(body.title)
    if body.ends_at <= body.starts_at:
        raise HTTPException(400, "ends_at must be after starts_at")
    # A dangling id would be an IntegrityError rendered as a 500 in the dialog, the
    # same reason PATCH resolves its ids before writing.
    if body.opportunity_id is not None and not db.get(Opportunity, body.opportunity_id):
        raise HTTPException(404, "opportunity %s not found" % body.opportunity_id)
    a = Appointment(**{**body.model_dump(), "title": title})
    db.add(a)
    db.flush()
    # Rule 3: booked -> reminders at T-24h and T-1h.
    outcome = automations.on_appointment_booked(db, a)
    db.commit()
    db.refresh(a)
    return {"id": a.id, "title": a.title, "starts_at": a.starts_at,
            "ends_at": a.ends_at, "opportunity_id": a.opportunity_id,
            "automation": outcome}


def _opp_detail(o: Opportunity, db: Session | None = None) -> dict:
    """Shape mirrors GHL's measured opportunity detail screen.

    `db` is optional only so the callers that already hold one do not have to be
    rewritten; without it the linked appointments come back empty rather than
    wrong, and every caller in this file passes it.
    """
    appointments = []
    if db is not None:
        appointments = [
            {"id": a.id, "title": a.title, "starts_at": a.starts_at,
             "ends_at": a.ends_at, "status": a.status,
             "calendar_name": a.calendar.name if a.calendar else None}
            for a in db.scalars(
                select(Appointment)
                .options(selectinload(Appointment.calendar))
                .where(Appointment.opportunity_id == o.id)
                .order_by(Appointment.starts_at)).all()]
    return {
        "id": o.id,
        "title": o.title,
        "pipeline_id": o.pipeline_id,
        "stage_id": o.stage_id,
        "status": o.status,
        "value_cents": o.value_cents,
        "owner_id": o.owner_id,
        "owner_name": o.owner.name if o.owner else None,
        "business_name": o.business_name,
        "source": o.source,
        "expected_close_date": o.expected_close_date,
        "created_by": o.created_by,
        "created_at": o.created_at,
        "custom_fields": o.custom_fields or {},
        "contact_id": o.contact_id,
        "contact_name": o.contact.name if o.contact else None,
        "contact_email": o.contact.email if o.contact else None,
        "contact_phone": o.contact.phone if o.contact else None,
        # The visits booked for this deal, soonest first. A list rather than one
        # booking: a roof job is an inspection and then a repair, and hiding the
        # second one behind the first would be a lie about what is scheduled.
        "appointments": appointments,
    }


@app.get("/api/opportunities/{opp_id}")
def get_opportunity(opp_id: int, db: Session = Depends(get_db),
                    _: auth.Principal = auth.ANY_USER):
    o = db.get(Opportunity, opp_id)
    if not o:
        raise HTTPException(404, "opportunity not found")
    return _opp_detail(o, db)


# An opportunity name longer than this is refused before the database is touched,
# the same way ContactCreate/ContactPatch cap their fields. The column holds 255,
# so this is the tighter of the two limits and is what the UI is built against.
OPPORTUNITY_TITLE_MAX = 120


def _clean_opportunity_title(title: str | None) -> str:
    """The one required field on an opportunity, per the measured GHL form.

    Shared by create and the detail PATCH so the two cannot drift: a name you can
    save through one path but not the other is a bug waiting to happen. Returns the
    stripped name, so " Jane roof " is stored as "Jane roof" either way.
    """
    cleaned = (title or "").strip()
    if not cleaned:
        # "Opportunity name" is the only field GHL marks required (red *).
        raise HTTPException(400, "opportunity name is required")
    return cleaned


def _check_opportunity_value(value_cents: int | None) -> None:
    """Money is integer cents and never negative. Shared by create and PATCH."""
    if value_cents is not None and value_cents < 0:
        raise HTTPException(400, "value cannot be negative")


def _check_opportunity_contact(db: Session, contact_id: int | None) -> None:
    """A contact_id naming nobody would create an orphan opportunity - a card on
    the board whose contact link no panel can ever open. Shared by create and PATCH.
    """
    if contact_id is not None and not db.get(Contact, contact_id):
        raise HTTPException(400, "unknown contact_id")


class OpportunityPatch(BaseModel):
    """Measured GHL statuses: Open / Won / Lost / Abandoned."""
    title: str | None = Field(None, max_length=OPPORTUNITY_TITLE_MAX)
    stage_id: int | None = None
    status: Literal["open", "won", "lost", "abandoned"] | None = None
    value_cents: int | None = None
    owner_id: int | None = None
    business_name: str | None = None
    source: str | None = None
    expected_close_date: str | None = None
    custom_fields: dict | None = None


@app.patch("/api/opportunities/{opp_id}/detail")
def update_opportunity(opp_id: int, body: OpportunityPatch,
                       db: Session = Depends(get_db),
                       _: auth.Principal = auth.STAFF):
    o = db.get(Opportunity, opp_id)
    if not o:
        raise HTTPException(404, "opportunity not found")

    data = body.model_dump(exclude_unset=True)
    if "title" in data:
        data["title"] = _clean_opportunity_title(data["title"])
    if "contact_id" in data:
        _check_opportunity_contact(db, data["contact_id"])
    if "stage_id" in data:
        stage = db.get(Stage, data["stage_id"])
        if not stage or stage.pipeline_id != o.pipeline_id:
            raise HTTPException(400, "stage is not in this opportunity's pipeline")
    if ("owner_id" in data and data["owner_id"] is not None
            and not db.get(User, data["owner_id"])):
        raise HTTPException(400, "unknown owner_id")
    if "value_cents" in data:
        _check_opportunity_value(data["value_cents"])
    if data.get("custom_fields") is not None:
        # One place decides what the blob becomes, shared with the create path.
        # It keeps the `owen_call_id` guard this endpoint has always had (the
        # detail form posts the whole object back on every save, so an unchanged
        # echo is fine and only a real change is refused), validates every answer
        # against its definition's type, and — new — can no longer DROP a key:
        # not a reserved `owen_*` one, not an answer to a field this pipeline does
        # not ask, not an answer to an archived field.
        #
        # Which questions this deal is asked follows its PIPELINE, and no endpoint
        # moves a deal between pipelines (a cross-pipeline stage is refused above),
        # so `o.pipeline_id` is both the before and the after here.
        o.custom_fields = custom_fields.merge_answers(
            db, pipeline_id=o.pipeline_id,
            existing=o.custom_fields, incoming=data.pop("custom_fields"))

    old_stage_id = o.stage_id
    for k, v in data.items():
        setattr(o, k, v)
    db.flush()
    outcome = automations.on_opportunity_stage_changed(db, o, old_stage_id)
    db.commit()
    db.refresh(o)
    return {**_opp_detail(o, db), "automation": outcome}


class OpportunityCreate(BaseModel):
    """Create and edit share their rules - see _clean_opportunity_title."""
    title: str = Field(max_length=OPPORTUNITY_TITLE_MAX)
    pipeline_id: int
    stage_id: int
    contact_id: int | None = None
    value_cents: int = 0
    # The job questions, answered in the Add opportunity dialog. Validated by the
    # same code the detail form goes through, so a dropdown cannot be talked into
    # an answer it does not offer by using the other door.
    custom_fields: dict | None = None


@app.post("/api/opportunities", status_code=201)
def create_opportunity(body: OpportunityCreate, db: Session = Depends(get_db),
                       _: auth.Principal = auth.STAFF):
    title = _clean_opportunity_title(body.title)
    _check_opportunity_value(body.value_cents)
    _check_opportunity_contact(db, body.contact_id)
    stage = db.get(Stage, body.stage_id)
    if not stage or stage.pipeline_id != body.pipeline_id:
        raise HTTPException(400, "stage is not in that pipeline")
    answers = custom_fields.merge_answers(
        db, pipeline_id=body.pipeline_id, existing={}, incoming=body.custom_fields)
    n = db.scalar(select(func.count(Opportunity.id))
                  .where(Opportunity.stage_id == body.stage_id)) or 0
    o = Opportunity(title=title, pipeline_id=body.pipeline_id,
                    stage_id=body.stage_id, contact_id=body.contact_id,
                    value_cents=body.value_cents, position=n,
                    custom_fields=answers)
    db.add(o)
    db.commit()
    db.refresh(o)
    return {"id": o.id, "title": o.title, "stage_id": o.stage_id}


# ---------- bulk actions ----------
#
# Move a selection between stages, and assign a selection an owner. Both are the
# single-record write applied to many records, deliberately: a bulk move that took
# a shortcut past `on_opportunity_stage_changed` would silently stop texting
# customers the moment anyone used it on more than one card.
#
# THERE IS NO BULK DELETE, and its absence is a decision, not an omission.
# `Opportunity.custom_fields.owen_call_id` is the live join key to the telephony
# project, opportunities are never cascade-deleted from a contact for that reason,
# and a multi-select is the easiest way there is to lose real customer records in
# one click. The same call was already made for Contacts (DECISIONS.md, "Contact
# Details Actions tab"). `DELETE /api/opportunities/{id}` still exists, one at a
# time, ADMIN.

# An upper bound so one request cannot walk the whole table. The board shows one
# pipeline; the largest measured pipeline held 40 opportunities.
BULK_MAX = 500


class BulkIds(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=BULK_MAX)


class BulkStageMove(BulkIds):
    stage_id: int


class BulkOwnerAssign(BulkIds):
    owner_id: int | None = None


def _bulk_load(db: Session, ids: list[int]) -> list[Opportunity]:
    """Resolve every id, or refuse the whole request having written nothing.

    Partially applying a bulk action is the worst of the three outcomes: the user
    cannot tell which half landed, and re-running it is not safe. Duplicates in
    the selection collapse rather than being applied twice.
    """
    unique = list(dict.fromkeys(ids))
    rows = db.scalars(select(Opportunity).where(Opportunity.id.in_(unique))).all()
    found = {o.id: o for o in rows}
    missing = [str(i) for i in unique if i not in found]
    if missing:
        raise HTTPException(404, "no opportunity with id " + ", ".join(missing))
    return [found[i] for i in unique]


@app.post("/api/opportunities/bulk/stage")
def bulk_move_stage(body: BulkStageMove, db: Session = Depends(get_db),
                    _: auth.Principal = auth.ANY_USER):
    """Move a selection into one stage.

    ANY_USER, matching `PATCH /api/opportunities/{id}` — a TECH may move a deal
    between stages, they simply cannot edit it (CLAUDE.md). Selecting five cards
    must not need a role that dragging one does not.

    An opportunity ALREADY in the destination is reported as unchanged and its
    automation is not fired: rule 4 texts the customer, and "your job is now at
    Inspection" arriving because someone included the card in a selection is a
    message the customer should never have received.
    """
    stage = db.get(Stage, body.stage_id)
    if not stage:
        raise HTTPException(400, "unknown stage_id")

    opps = _bulk_load(db, body.ids)
    wrong = [str(o.id) for o in opps if o.pipeline_id != stage.pipeline_id]
    if wrong:
        raise HTTPException(
            400, "stage is not in the pipeline of opportunity " + ", ".join(wrong))

    changed = [o for o in opps if o.stage_id != stage.id]
    unchanged = [o.id for o in opps if o.stage_id == stage.id]
    was = {o.id: o.stage_id for o in changed}

    # Read the destination's current occupants BEFORE moving anyone in, so the
    # arrivals land after them in the order they were selected rather than
    # interleaving on whatever position they held in their old stage.
    settled = db.scalars(
        select(Opportunity)
        .where(Opportunity.stage_id == stage.id)
        .order_by(Opportunity.position)).all()
    for o in changed:
        o.stage_id = stage.id
    for i, o in enumerate([*settled, *changed]):
        o.position = i
    db.flush()

    # Rule 4, once per opportunity that actually changed stage.
    automation = {str(o.id): automations.on_opportunity_stage_changed(db, o, was[o.id])
                  for o in changed}
    db.commit()
    return {"stage_id": stage.id, "moved": [o.id for o in changed],
            "unchanged": unchanged, "automation": automation}


@app.post("/api/opportunities/bulk/owner")
def bulk_assign_owner(body: BulkOwnerAssign, db: Session = Depends(get_db),
                      _: auth.Principal = auth.STAFF):
    """Assign a selection an owner, or `owner_id: null` to unassign.

    STAFF, matching `PATCH /api/opportunities/{id}/detail`, which is where a single
    opportunity's owner is set. Changing an owner is editing the record, and a TECH
    cannot edit records.
    """
    if body.owner_id is not None and not db.get(User, body.owner_id):
        raise HTTPException(400, "unknown owner_id")
    opps = _bulk_load(db, body.ids)
    for o in opps:
        o.owner_id = body.owner_id
    db.commit()
    return {"owner_id": body.owner_id, "updated": [o.id for o in opps]}


# ---------- saved views (GHL's "smart lists") ----------
#
# A named filter set for the Opportunities board. OUR design: `+ List` and
# "Manage smart lists" were measured as labels and nothing behind them was ever
# opened on the live account (DECISIONS.md).
#
# The board's built-in "Open opportunities" is deliberately NOT a row in this
# table. It is the board's default state, so it is always there, cannot be
# deleted, and there is no seeded row to keep in step with the code.

def _saved_view_public(v: SavedView) -> dict:
    return {"id": v.id, "name": v.name, "pipeline_id": v.pipeline_id,
            "pipeline_name": v.pipeline.name if v.pipeline else None,
            "status": v.status, "q": v.q, "position": v.position,
            "created_by_id": v.created_by_id}


def _clean_view_name(name: str | None) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise HTTPException(400, "a saved view needs a name")
    return cleaned


def _check_view_pipeline(db: Session, pipeline_id: int | None) -> None:
    if pipeline_id is not None and not db.get(Pipeline, pipeline_id):
        raise HTTPException(400, "unknown pipeline_id")


class SavedViewCreate(BaseModel):
    name: str = Field(max_length=80)
    pipeline_id: int | None = None
    status: Literal["open", "won", "lost", "abandoned", "all"] = "open"
    q: str = Field("", max_length=200)


class SavedViewPatch(BaseModel):
    name: str | None = Field(None, max_length=80)
    pipeline_id: int | None = None
    status: Literal["open", "won", "lost", "abandoned", "all"] | None = None
    q: str | None = Field(None, max_length=200)
    position: int | None = None


@app.get("/api/saved-views")
def list_saved_views(db: Session = Depends(get_db),
                     _: auth.Principal = auth.ANY_USER):
    """Shared, not per-user: four people in one company, and "the list Owen made"
    is the useful thing. Everyone reads them."""
    rows = db.scalars(
        select(SavedView).options(selectinload(SavedView.pipeline))
        .order_by(SavedView.position, SavedView.id)).all()
    return [_saved_view_public(v) for v in rows]


@app.post("/api/saved-views", status_code=201)
def create_saved_view(body: SavedViewCreate, db: Session = Depends(get_db),
                      principal: auth.Principal = auth.STAFF):
    """STAFF: saving a view is a write, and it is shared with everyone.

    Duplicate names are allowed, deliberately. The pipeline this app was measured
    against holds two distinct stages both called "Call Back" and the whole
    codebase resolves by id rather than deduplicating names; a uniqueness rule
    here would be the only place that disagreed.
    """
    _check_view_pipeline(db, body.pipeline_id)
    n = db.scalar(select(func.count(SavedView.id))) or 0
    v = SavedView(name=_clean_view_name(body.name), pipeline_id=body.pipeline_id,
                  status=body.status, q=body.q.strip(), position=n,
                  created_by_id=principal.user_id)
    db.add(v)
    db.commit()
    db.refresh(v)
    return _saved_view_public(v)


@app.patch("/api/saved-views/{view_id}")
def update_saved_view(view_id: int, body: SavedViewPatch,
                      db: Session = Depends(get_db),
                      _: auth.Principal = auth.STAFF):
    v = db.get(SavedView, view_id)
    if not v:
        raise HTTPException(404, "saved view not found")
    data = body.model_dump(exclude_unset=True)
    if "name" in data:
        data["name"] = _clean_view_name(data["name"])
    if "pipeline_id" in data:
        _check_view_pipeline(db, data["pipeline_id"])
    if "q" in data and data["q"] is not None:
        data["q"] = data["q"].strip()
    for k, value in data.items():
        if value is not None or k == "pipeline_id":
            setattr(v, k, value)
    db.commit()
    db.refresh(v)
    return _saved_view_public(v)


@app.delete("/api/saved-views/{view_id}")
def delete_saved_view(view_id: int, db: Session = Depends(get_db),
                      _: auth.Principal = auth.ADMIN):
    """ADMIN, following this app's standing rule — everyone reads, staff write,
    admin deletes (CLAUDE.md). A saved view is a shared object: one person
    removing another's list is the kind of thing the rule exists for.
    """
    v = db.get(SavedView, view_id)
    if not v:
        raise HTTPException(404, "saved view not found")
    db.delete(v)
    db.commit()
    return {"deleted": view_id}


# ---------- conversations ----------

def _event_counts(db: Session, conv_id: int | None = None) -> dict[int, int]:
    """How many entries each thread holds, in ONE grouped query.

    The delete confirmation has to name what it is about to destroy — "Jane Doe
    and 12 messages" rather than a bare "are you sure" — so the number has to be
    on the row before anyone clicks. Counting per row would be a query per
    conversation on every poll of the inbox.

    It counts the WHOLE thread, deliberately, and is not narrowed by the thread
    view's `filter` or by the STAFF-only internal-note rule: a delete removes
    every event, so a count that quietly excluded the notes a TECH cannot see
    would under-report exactly what is being lost.
    """
    stmt = select(ConversationEvent.conversation_id, func.count())
    if conv_id is not None:
        stmt = stmt.where(ConversationEvent.conversation_id == conv_id)
    return dict(db.execute(stmt.group_by(ConversationEvent.conversation_id)).all())


@app.get("/api/conversations")
def list_conversations(
    db: Session = Depends(get_db),
    tab: Literal["unread", "all", "recent", "starred"] = "all",
    sort: str = "latest",
    assigned: Literal["all", "me"] = "all",
    q: str | None = None,
    principal: auth.Principal = auth.ANY_USER):
    """Tabs and sort options are the measured GHL set.

    Tabs:  Unread | All | Recent | Starred
    Sort:  Latest/Oldest - All Messages, Latest/Oldest - Manual Messages,
           Longest SLA Overdue, Next SLA Target
    SLA sorts are accepted but fall back to recency: this account has no SLA
    configured ("SLA has not been set. Go to Settings to configure."), so there is
    nothing measured to replicate yet.

    `assigned` and `q` are the Conversations icon rail — "Assigned to me" /
    "Team inbox", and the in-place search that narrows the inbox you are looking
    at (the ctrl+K palette is the one that finds anything anywhere).

    **All four narrow the same query and INTERSECT.** "My conversations, unread,
    matching Reyes" is a reasonable thing to ask for, and each control answers a
    different question — scope: whose, tab: what state, q: which contact, sort:
    in what order. Applying them anywhere but here would mean the Unread badge
    and the list it labels could be computed over different sets again.

    **`assigned` is `Contact.owner_id`, and there is deliberately no assignee on
    `Conversation`.** A thread is correspondence with a customer, and the
    customer already has an owner — the one shown in the contact panel. A second
    column would be a second answer to "whose is this" with nothing keeping the
    two in step, and no migration is worth that.

    Neither narrows a role's view: this endpoint is ANY_USER and returns the
    whole team inbox by default, exactly as before.
    """
    stmt = select(Conversation).options(selectinload(Conversation.contact))
    if tab == "unread":
        stmt = stmt.where(Conversation.unread_count > 0)
    elif tab == "starred":
        stmt = stmt.where(Conversation.starred.is_(True))

    if assigned == "me":
        # A contact with no owner belongs to nobody, so it is in the team inbox
        # and in no one's "assigned to me" — an IS NULL never equals a user id.
        stmt = stmt.join(Contact, Conversation.contact_id == Contact.id).where(
            Contact.owner_id == principal.user_id)

    if q and q.strip():
        # NAME and PHONE only, which is exactly what the row on screen shows.
        # Matching a field the row does not display (an email, a business name)
        # produces a result whose reason is invisible — the user sees a row that
        # plainly does not match what they typed. The palette already searches
        # those, and that is the control for "find anything anywhere".
        text = q.strip()
        like = contains(text)
        esc = LIKE_ESCAPE
        terms = [Contact.first_name.ilike(like, escape=esc),
                 Contact.last_name.ilike(like, escape=esc),
                 (Contact.first_name + " "
                  + Contact.last_name).ilike(like, escape=esc),
                 Contact.phone.ilike(like, escape=esc)]
        # A number typed the way a caller ID reads it has to find the row
        # whatever shape the number is stored in — app/phone_match.py.
        if phone_match.looks_like_phone(text):
            terms.append(phone_match.phone_clause(Contact.phone, text))
        # One join however many filters asked for it: joining twice is an error
        # on Postgres and a silent cross product waiting to happen.
        if assigned != "me":
            stmt = stmt.join(Contact, Conversation.contact_id == Contact.id)
        stmt = stmt.where(or_(*terms))

    oldest = sort.startswith("oldest")
    col = Conversation.last_event_at
    stmt = stmt.order_by(col.asc() if oldest else col.desc())
    rows = db.scalars(stmt).all()
    counts = _event_counts(db)
    # `contact_dnd` is here so the composer can disable Send with a reason rather
    # than let the operator type a message and discover it was suppressed. Same
    # precedent as the disabled Internal Comment row: never offer an enabled
    # control that cannot work.
    return [{"id": c.id, "contact_id": c.contact_id,
             "contact_name": c.contact.name if c.contact else None,
             "contact_phone": c.contact.phone if c.contact else None,
             "contact_dnd": bool(c.contact.dnd) if c.contact else False,
             "last_event_at": c.last_event_at, "unread_count": c.unread_count,
             "event_count": counts.get(c.id, 0),
             "starred": c.starred} for c in rows]


@app.get("/api/conversations/{conv_id}/events", response_model=list[EventOut])
def conversation_events(
    conv_id: int,
    db: Session = Depends(get_db),
    filter: str = "all",
    principal: auth.Principal = auth.ANY_USER):
    """`filter` mirrors GHL's measured "Filter messages" menu: all | conversations
    | activities | or a specific EventType name.

    Internal notes are dropped for a TECH whatever the filter says. Note
    CONVERSATION_TYPES contains INTERNAL_COMMENT, so the measured "Conversations"
    filter is one of the places that has to narrow.
    """
    internal_ok = _sees_internal(principal)
    stmt = (select(ConversationEvent)
            .where(ConversationEvent.conversation_id == conv_id))

    if filter == "conversations":
        types = set(CONVERSATION_TYPES)
        if not internal_ok:
            types -= INTERNAL_TYPES
        stmt = stmt.where(ConversationEvent.type.in_(types))
    elif filter == "activities":
        stmt = stmt.where(ConversationEvent.type.in_(ACTIVITY_TYPES))
    elif filter != "all":
        try:
            wanted = EventType[filter.upper()]
        except KeyError:
            raise HTTPException(400, "unknown filter %r" % filter) from None
        if wanted in INTERNAL_TYPES and not internal_ok:
            _refuse_internal(principal, "read")
        stmt = stmt.where(ConversationEvent.type == wanted)
    elif not internal_ok:
        stmt = stmt.where(ConversationEvent.type.not_in(INTERNAL_TYPES))

    rows = db.scalars(stmt.order_by(ConversationEvent.occurred_at)).all()
    return [EventOut(id=e.id, type=e.type.value, direction=e.direction.value,
                     occurred_at=e.occurred_at, body=e.body, subject=e.subject,
                     duration_seconds=e.duration_seconds,
                     recording_url=e.recording_url,
                     call_status=e.call_status,
                     delivery_status=e.delivery_status.value
                     if e.delivery_status else None,
                     delivery_detail=e.delivery_detail) for e in rows]


# ---------- appointments ----------

@app.get("/api/users")
def list_users(db: Session = Depends(get_db),
               principal: auth.Principal = auth.ANY_USER):
    """Every signed-in user can see the roster; only an ADMIN sees email addresses.

    Restricting the whole endpoint to ADMIN was considered and rejected: the owner
    dropdowns on the contact panel and the opportunity form both read this, so it
    would break those screens for a dispatcher. The original exposure being closed
    here is *unauthenticated* access to the staff list, not staff seeing each other.
    """
    rows = db.scalars(select(User).order_by(User.id)).all()
    is_admin = principal.role is Role.ADMIN
    return [{"id": u.id, "name": u.name, "role": u.role.value,
             "is_active": u.is_active,
             "email": u.email if is_admin else None}
            for u in rows]


@app.get("/api/calendars")
def list_calendars(db: Session = Depends(get_db),
                   _: auth.Principal = auth.ANY_USER):
    """Filter groups measured on GHL: Users and Calendars.
    `pipeline` is our addition — GHL has no pipeline filter here."""
    rows = db.scalars(
        select(Calendar).options(selectinload(Calendar.user),
                                 selectinload(Calendar.pipeline))
        .order_by(Calendar.id)).all()
    return [{"id": c.id, "name": c.name, "color": c.color,
             "user_id": c.user_id, "user_name": c.user.name if c.user else None,
             "pipeline_id": c.pipeline_id,
             "pipeline_name": c.pipeline.name if c.pipeline else None}
            for c in rows]


def _range_utc(dt: datetime | None) -> datetime | None:
    """Read one end of a dashboard range as UTC.

    A naive timestamp is taken as UTC rather than as the server's local time. The
    browser sends UTC and the CLI's `--since 30d` is UTC, so a naive value reaching
    here is always a caller that dropped the offset, never one meaning local noon.
    Left as local time it would move the window by the server's offset, which is the
    kind of drift nobody notices until a deal falls off the edge of "Last 7 days".
    """
    if dt is None:
        return None
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def _opportunities_in_range(db: Session, pipeline_id: int | None,
                            start: datetime | None, end: datetime | None):
    """Every opportunity the dashboard is allowed to count.

    The range filters on **creation date** — the owner's decision (2026-09-10).
    Not the stage-change date and not the won date: `updated_at` moves every time
    anyone touches a record, and there is no `won_at` column at all (see the Call
    report amendment in DECISIONS.md, which hit the same gap).

    No range means ALL TIME, deliberately. Defaulting to 30 days would empty this
    screen for a company whose deals were created in one August week, and a fix
    that reads as a regression is worse than the bug it fixes.
    """
    stmt = select(Opportunity)
    if pipeline_id:
        stmt = stmt.where(Opportunity.pipeline_id == pipeline_id)
    if start is not None:
        stmt = stmt.where(Opportunity.created_at >= _range_utc(start))
    if end is not None:
        stmt = stmt.where(Opportunity.created_at <= _range_utc(end))
    return db.scalars(stmt).all()


def _status_rollup(opps) -> dict:
    """Roll a set of opportunities up by status. THE dashboard arithmetic.

    `/api/dashboard` and `/api/forecast` both need these numbers, and a forecast
    that disagreed with the Dashboard about how many deals are open — or about the
    conversion rate — would just be a second, contradictory set of figures on the
    same data. They share this function so the two cannot drift.

    `conversion_rate` is a percentage rounded to two decimals, and it is the
    published one: the forecast weights its open money with exactly this value, so
    every number in the forecast can be re-derived by hand from the response.

    Two keys exist because the Dashboard cards used to invent them in the browser:

    * `value_by_status` — the Opportunity value card draws one bar per status. It
      had no per-status money to draw, so it hard-coded Lost at $0 and rendered
      Open as `total - won`, which is only true when nothing has been lost or
      abandoned. The bars were arithmetic, not data.
    * `conversion_rate_all` — `conversion_rate` is Won / (Won + Lost), i.e. of the
      deals that have been *decided*. Won / every opportunity is the other
      reasonable reading, so the card offers both rather than the API picking for
      the owner. Neither can exceed 100%.
    """
    by_status = {"won": 0, "open": 0, "lost": 0, "abandoned": 0}
    value_by_status = {"won": 0, "open": 0, "lost": 0, "abandoned": 0}
    total_value = won_value = 0
    for o in opps:
        by_status[o.status] = by_status.get(o.status, 0) + 1
        value_by_status[o.status] = value_by_status.get(o.status, 0) + o.value_cents
        total_value += o.value_cents
        if o.status == "won":
            won_value += o.value_cents

    decided = by_status["won"] + by_status["lost"]
    conversion = (by_status["won"] / decided * 100) if decided else 0.0
    conversion_all = (by_status["won"] / len(opps) * 100) if opps else 0.0
    return {
        "total": len(opps),
        "status": by_status,
        "total_value_cents": total_value,
        "won_value_cents": won_value,
        "value_by_status": value_by_status,
        "conversion_rate": round(conversion, 2),
        "conversion_rate_all": round(conversion_all, 2),
    }


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db), pipeline_id: int | None = None,
              start: datetime | None = None, end: datetime | None = None,
              _: auth.Principal = auth.STAFF):
    """Measured GHL dashboard cards: Opportunity status (Won/Open/Lost + total),
    Opportunity value (Total vs Won revenue), Conversion rate.

    `start`/`end` bound the opportunity's **creation** date; omitting both means
    all time. Every figure below obeys the same window — nothing on this endpoint
    is exempt from it.

    The arithmetic itself lives in `_status_rollup`, which `/api/forecast` also
    uses, so the forecast cannot publish a conversion rate the Dashboard disagrees
    with.
    """
    return _status_rollup(_opportunities_in_range(db, pipeline_id, start, end)) | {
        # Echoed so the browser and the CLI can show which window produced these
        # numbers rather than trusting the control that was last clicked.
        "range": {"start": _range_utc(start), "end": _range_utc(end)},
    }


@app.get("/api/dashboard/funnel")
def dashboard_funnel(db: Session = Depends(get_db), pipeline_id: int | None = None,
                     start: datetime | None = None, end: datetime | None = None,
                     _: auth.Principal = auth.ANY_USER):
    """The Funnel and Stage distribution cards: one pipeline, stage by stage.

    Both cards used to be fed by `GET /api/pipelines`, which counts every
    opportunity ever created and so ignored the date range entirely. They are fed
    from here instead, and this route keeps that endpoint's **ANY_USER** gate on
    purpose: per-stage counts and money are already TECH-visible through
    `/api/pipelines` and `/api/opportunities`, and DECISIONS.md (2026-09-09) records
    that as deliberate. Moving the funnel behind the STAFF gate on `/api/dashboard`
    would have quietly taken a card off a dispatched tech's screen.

    Three numbers per stage, and only the first is a plain count:

    * `count`    — opportunities sitting in this stage now. Matches the kanban.
    * `reached`  — how many got this far: `count` of this stage and every stage
      after it. This is INFERRED from where deals sit today, because the schema
      keeps no stage history — a deal in stage 4 is assumed to have passed through
      stages 0-3, and one lost at stage 2 still counts as having reached stage 2.
      That is the only reading the data supports; it is not a record of movement.
    * `cumulative_pct` — `reached / reached[0]`: the share of the pipeline's deals
      that got this far or further. It starts at 100% and can only fall, which is
      what makes it a funnel. It used to be `count / total`, which is a
      distribution, not a cumulative — it rose and fell down the column.
    * `next_step_pct` — `reached[i+1] / reached[i]`: of the deals that reached this
      stage, the share that went on to the next one. It cannot exceed 100%. It used
      to be `count[i+1] / count[i]`, comparing two occupancies rather than two
      populations, which is how a "conversion rate" of 400% got on screen.

    `null` where a figure has no meaning: the last stage has no next step, and a
    stage nothing reached has no denominator. The card renders those as `--`,
    matching the measured empty-field dash used elsewhere.
    """
    stmt = select(Pipeline).options(selectinload(Pipeline.stages))
    if pipeline_id:
        stmt = stmt.where(Pipeline.id == pipeline_id)
    pipeline = db.scalars(stmt.order_by(Pipeline.position, Pipeline.id)).first()
    if pipeline is None:
        # An unknown pipeline_id is a 404; an empty database simply has no funnel.
        if pipeline_id:
            raise HTTPException(404, "no pipeline %d" % pipeline_id)
        return {"pipeline_id": None, "pipeline_name": None, "total": 0, "stages": []}

    opps = _opportunities_in_range(db, pipeline.id, start, end)
    counts: dict[int, int] = {}
    values: dict[int, int] = {}
    for o in opps:
        counts[o.stage_id] = counts.get(o.stage_id, 0) + 1
        values[o.stage_id] = values.get(o.stage_id, 0) + o.value_cents

    ordered = sorted(pipeline.stages, key=lambda s: (s.position, s.id))
    tail = [counts.get(s.id, 0) for s in ordered]
    # reached[i] = everyone at stage i or beyond, walked backwards so it is one pass.
    reached = [0] * len(ordered)
    running = 0
    for i in range(len(ordered) - 1, -1, -1):
        running += tail[i]
        reached[i] = running

    total = reached[0] if reached else 0
    stages = []
    for i, s in enumerate(ordered):
        nxt = reached[i + 1] if i + 1 < len(reached) else None
        stages.append({
            "id": s.id, "name": s.name, "position": s.position,
            "count": tail[i], "value_cents": values.get(s.id, 0),
            "reached": reached[i],
            "cumulative_pct": round(reached[i] / total * 100, 2) if total else None,
            "next_step_pct": (round(nxt / reached[i] * 100, 2)
                              if nxt is not None and reached[i] else None),
        })
    return {"pipeline_id": pipeline.id, "pipeline_name": pipeline.name,
            "total": total, "stages": stages,
            "range": {"start": _range_utc(start), "end": _range_utc(end)}}


def _weighted(open_value_cents: int, conversion_rate: float) -> int:
    """`open_value_cents` at `conversion_rate` percent, in whole cents, half up.

    Kept in integers on purpose, the same reason `centsFromDollars` is: money is
    integer cents everywhere in this app and `round(v * rate / 100)` is a float
    round trip that both loses halves (banker's rounding) and depends on IEEE-754
    representation. `conversion_rate` carries two decimals, so it is exactly a
    whole number of hundredths of a percent — multiply by that instead.
    """
    hundredths = round(conversion_rate * 100)
    return (open_value_cents * hundredths + 5000) // 10000


@app.get("/api/forecast")
def forecast(pipeline_id: int, db: Session = Depends(get_db),
             _: auth.Principal = auth.STAFF):
    """Projected revenue by stage for one pipeline.

    OUR design — GHL's own Forecast tab was never opened on the live account, so
    there is no capture to match (DECISIONS.md). What it is NOT is a second
    opinion: the per-stage `count`/`value_cents` are the same figures
    `GET /api/pipelines` feeds the Dashboard funnel with (every status, not just
    open), and the rollup and `conversion_rate` come from `_status_rollup`, which
    is what `GET /api/dashboard` returns.

    The projection itself is deliberately one rule, applied uniformly:

        weighted = open value in the stage x the pipeline's conversion rate
        projected = money already won in the stage + weighted

    Per-stage win probabilities would be the richer model and this app cannot
    honestly compute them — nothing records stage history, so a won deal sits in
    whatever stage it was won in, and "the win rate of Inspection" would really be
    measuring where deals get marked won. One published rate that the user can see
    on the Dashboard beats a curve nobody can check.

    STAFF, matching `/api/dashboard`, whose aggregates this repeats.
    """
    p = db.get(Pipeline, pipeline_id)
    if not p:
        raise HTTPException(404, "pipeline not found")

    opps = list(db.scalars(
        select(Opportunity).where(Opportunity.pipeline_id == pipeline_id)).all())
    roll = _status_rollup(opps)
    rate = roll["conversion_rate"]

    by_stage: dict[int, list[Opportunity]] = {}
    for o in opps:
        by_stage.setdefault(o.stage_id, []).append(o)

    stages = []
    for s in p.stages:                      # relationship is ordered by position
        rows = by_stage.get(s.id, [])
        open_value = sum(o.value_cents for o in rows if o.status == "open")
        won_value = sum(o.value_cents for o in rows if o.status == "won")
        weighted = _weighted(open_value, rate)
        stages.append({
            "stage_id": s.id,
            "name": s.name,
            "position": s.position,
            # These two are exactly what /api/pipelines reports for the stage.
            "count": len(rows),
            "value_cents": sum(o.value_cents for o in rows),
            "open_count": sum(1 for o in rows if o.status == "open"),
            "open_value_cents": open_value,
            "won_count": sum(1 for o in rows if o.status == "won"),
            "won_value_cents": won_value,
            "weighted_value_cents": weighted,
            "projected_value_cents": won_value + weighted,
        })

    # Summed from the rows, not recomputed, so the column adds up to its own total.
    def col(key: str) -> int:
        return sum(s[key] for s in stages)

    return {
        "pipeline_id": p.id,
        "pipeline_name": p.name,
        "conversion_rate": rate,
        "status": roll["status"],
        "stages": stages,
        "totals": {
            "count": roll["total"],
            "value_cents": roll["total_value_cents"],
            "open_count": col("open_count"),
            "open_value_cents": col("open_value_cents"),
            "won_count": col("won_count"),
            "won_value_cents": roll["won_value_cents"],
            "weighted_value_cents": col("weighted_value_cents"),
            "projected_value_cents": col("projected_value_cents"),
        },
    }


@app.get("/api/appointments")
def list_appointments(
    db: Session = Depends(get_db),
    start: datetime | None = None,
    end: datetime | None = None,
    user_ids: str | None = None,
    calendar_ids: str | None = None,
    pipeline_ids: str | None = None,
    kind: Literal["all", "appointments", "blocked"] = "all",
    _: auth.Principal = auth.ANY_USER):
    """Range + filters mirror GHL's measured "Manage view" panel:
    View by type (All / Appointments / Blocked slots) and per-user filtering."""
    stmt = select(Appointment).options(selectinload(Appointment.contact),
                                selectinload(Appointment.calendar),
                                selectinload(Appointment.opportunity))
    if start:
        stmt = stmt.where(Appointment.ends_at >= start)
    if end:
        stmt = stmt.where(Appointment.starts_at <= end)
    if kind == "appointments":
        stmt = stmt.where(Appointment.status != "blocked")
    elif kind == "blocked":
        stmt = stmt.where(Appointment.status == "blocked")
    def _ids(raw):
        return [int(x) for x in (raw or "").split(",") if x.strip().isdigit()]

    if _ids(user_ids):
        stmt = stmt.where(Appointment.assigned_user_id.in_(_ids(user_ids)))
    if _ids(calendar_ids):
        stmt = stmt.where(Appointment.calendar_id.in_(_ids(calendar_ids)))
    if _ids(pipeline_ids):
        # OUR ADDITION: filter appointments by the pipeline their calendar belongs to.
        stmt = stmt.where(Appointment.calendar_id.in_(
            select(Calendar.id).where(Calendar.pipeline_id.in_(_ids(pipeline_ids)))))

    rows = db.scalars(stmt.order_by(Appointment.starts_at)).all()
    return [{"id": a.id, "title": a.title, "starts_at": a.starts_at,
             "ends_at": a.ends_at, "status": a.status,
             "assigned_user_id": a.assigned_user_id,
             "calendar_id": a.calendar_id,
             "calendar_name": a.calendar.name if a.calendar else None,
             "color": a.calendar.color if a.calendar else "#004eeb",
             "opportunity_id": a.opportunity_id,
             "opportunity_title": a.opportunity.title if a.opportunity else None,
             "contact_name": a.contact.name if a.contact else None} for a in rows]


# ---------- reporting ----------
#
# Measured from GHL's Reporting tabs. DELIBERATELY NOT IMPLEMENTED:
# "Google Ads", "Meta Ads (Facebook Ads) report" and "Local Marketing Audit" —
# all third-party integrations, excluded by request. "Attribution report"
# rendered empty on the live account, so there is nothing measured to copy.

@app.get("/api/reports/calls")
def report_calls(
    db: Session = Depends(get_db),
    start: datetime | None = None,
    end: datetime | None = None,
    direction: Literal["all", "INBOUND", "OUTBOUND"] = "INBOUND",
    _: auth.Principal = auth.STAFF):
    """Measured Call report: Incoming/Outgoing toggle, "Call by status",
    "First-time calls by status", avg + total duration, "Top call sources"
    (Source | Total calls | Won deals | Avg duration).

    Every figure is computed from ConversationEvent rows of type CALL, except
    "Won deals", which comes from Opportunity. The attribution is: a won
    opportunity belonging to a contact who called inside this window, credited to
    the source that contact's calls are listed under, counted once per
    opportunity. The schema records no date on which a deal was won (there is no
    `won_at`), so the deals are not themselves date-filtered — the window selects
    the callers, not the wins.
    """
    stmt = (select(ConversationEvent, Conversation, Contact)
            .join(Conversation, ConversationEvent.conversation_id == Conversation.id)
            .join(Contact, Conversation.contact_id == Contact.id)
            .where(ConversationEvent.type == EventType.CALL))
    if direction != "all":
        stmt = stmt.where(ConversationEvent.direction == Direction[direction])
    if start:
        stmt = stmt.where(ConversationEvent.occurred_at >= start)
    if end:
        stmt = stmt.where(ConversationEvent.occurred_at <= end)

    rows = db.execute(stmt).all()

    # "First-time" means new to us, not new to this month: a caller's earliest
    # call is looked up across ALL time, not just the reported window. Scoped to
    # the same direction, because Incoming/Outgoing scopes the whole report.
    earliest = (select(Conversation.contact_id.label("cid"),
                       func.min(ConversationEvent.occurred_at).label("first_at"))
                .join(Conversation,
                      ConversationEvent.conversation_id == Conversation.id)
                .where(ConversationEvent.type == EventType.CALL)
                .group_by(Conversation.contact_id))
    if direction != "all":
        earliest = earliest.where(ConversationEvent.direction == Direction[direction])
    first_call_at = dict(db.execute(earliest).all())

    # Won deals are attributed through the caller, so read them per contact and
    # bucket them below under the source that contact's calls are bucketed under.
    # Grouped in the database; the result is one row per contact with a win, which
    # for a single-tenant account is a handful of rows.
    won_by_contact = dict(db.execute(
        select(Opportunity.contact_id, func.count(Opportunity.id))
        .where(Opportunity.status == "won", Opportunity.contact_id.is_not(None))
        .group_by(Opportunity.contact_id)).all())

    by_status: dict[str, int] = {}
    first_by_status: dict[str, int] = {}
    seen_contacts: set[int] = set()
    attributed: set[int] = set()
    durations: list[int] = []
    first_durations: list[int] = []
    per_source: dict[str, dict] = {}

    for ev, conv, contact in sorted(rows, key=lambda r: r[0].occurred_at):
        # A blank status is unknown, not "completed". The telephony feed does not
        # always supply one, and calling an unknown call a completed one is the
        # difference between a missed lead and a served customer.
        status = ev.call_status or UNKNOWN_CALL_STATUS
        by_status[status] = by_status.get(status, 0) + 1

        d = ev.duration_seconds or 0
        durations.append(d)

        first_at = first_call_at.get(conv.contact_id)
        if conv.contact_id not in seen_contacts and first_at is not None \
                and ev.occurred_at <= first_at:
            seen_contacts.add(conv.contact_id)
            first_by_status[status] = first_by_status.get(status, 0) + 1
            first_durations.append(d)

        src = contact.source or "Unknown"
        s = per_source.setdefault(src, {"source": src, "calls": 0, "won": 0,
                                        "duration": 0})
        s["calls"] += 1
        s["duration"] += d
        # Only callers count, and each caller's deals count once however often
        # they rang. A won deal on a contact who never called is not a call
        # source's win — it belongs to whatever channel actually brought it in.
        if conv.contact_id not in attributed:
            attributed.add(conv.contact_id)
            s["won"] += won_by_contact.get(conv.contact_id, 0)

    def _avg(xs: list[int]) -> int:
        return round(sum(xs) / len(xs)) if xs else 0

    # Ties broken by name so the table does not reshuffle between refreshes.
    sources = sorted(per_source.values(), key=lambda s: (-s["calls"], s["source"]))
    for s in sources:
        s["avg_duration"] = round(s["duration"] / s["calls"]) if s["calls"] else 0

    return {
        "total_calls": len(rows),
        "by_status": by_status,
        "first_time_by_status": first_by_status,
        "avg_duration_seconds": _avg(durations),
        "total_duration_seconds": sum(durations),
        # The first-time card has its own duration strip; it used to be handed the
        # whole window's figures, which is a different number under the same label.
        "first_time_avg_duration_seconds": _avg(first_durations),
        "first_time_total_duration_seconds": sum(first_durations),
        "top_sources": sources[:10],
    }


@app.get("/api/reports/appointments")
def report_appointments(
    db: Session = Depends(get_db),
    start: datetime | None = None,
    end: datetime | None = None,
    calendar_ids: str | None = None,
    _: auth.Principal = auth.STAFF):
    """Measured Appointment report: status tiles (Booked, Confirmed, Cancelled,
    New, Showed, No-show, Invalid, Rescheduled) plus Channel / Source breakdown."""
    stmt = (select(Appointment, Contact)
            .join(Contact, Appointment.contact_id == Contact.id, isouter=True))
    if start:
        stmt = stmt.where(Appointment.starts_at >= start)
    if end:
        stmt = stmt.where(Appointment.starts_at <= end)
    ids = [int(x) for x in (calendar_ids or "").split(",") if x.strip().isdigit()]
    if ids:
        stmt = stmt.where(Appointment.calendar_id.in_(ids))

    rows = db.execute(stmt).all()

    TILES = ["booked", "confirmed", "cancelled", "new", "showed",
             "no-show", "invalid", "rescheduled"]
    tiles = dict.fromkeys(TILES, 0)
    by_source: dict[str, int] = {}
    by_calendar: dict[str, int] = {}
    outcomes: dict[str, int] = {}

    cal_names = {c.id: c.name for c in db.scalars(select(Calendar)).all()}
    # Measured on GHL: "Showed / No-show / Cancelled" roll up as Outcomes.
    OUTCOME_KEYS = {"showed", "no-show", "cancelled"}

    for appt, contact in rows:
        st = (appt.status or "").lower()
        if st in tiles:
            tiles[st] += 1
        if st in OUTCOME_KEYS:
            outcomes[st] = outcomes.get(st, 0) + 1
        src = (contact.source if contact else None) or "Unknown"
        by_source[src] = by_source.get(src, 0) + 1
        cal = cal_names.get(appt.calendar_id) or "Unassigned"
        by_calendar[cal] = by_calendar.get(cal, 0) + 1

    rank = lambda d, k: sorted(  # noqa: E731
        [{k: a, "count": b} for a, b in d.items()], key=lambda x: -x["count"])

    return {
        "total": len(rows),
        "tiles": tiles,
        "by_source": rank(by_source, "source"),
        "by_calendar": rank(by_calendar, "calendar")[:5],
        "outcomes": outcomes,
        # GHL buckets appointment channel as "Other" for calendar-created bookings.
        "by_channel": [{"channel": "Other", "count": len(rows)}] if rows else [],
    }


# ---------- appointment detail / edit / cancel ----------

APPOINTMENT_STATUSES = {"booked", "confirmed", "cancelled", "new", "showed",
                        "no-show", "invalid", "rescheduled", "blocked"}


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes even from timezone=True columns, so a value
    read back from the database cannot be compared with one just parsed from JSON
    without normalising first."""
    return None if dt is None else auth.as_aware(dt)


def _appointment_detail(a: Appointment) -> dict:
    return {"id": a.id, "title": a.title, "starts_at": a.starts_at,
            "ends_at": a.ends_at, "status": a.status, "notes": a.notes,
            "contact_id": a.contact_id,
            "contact_name": a.contact.name if a.contact else None,
            "calendar_id": a.calendar_id,
            "calendar_name": a.calendar.name if a.calendar else None,
            # Both directions of the link are readable: the deal lists its visits,
            # and the visit names its deal.
            "opportunity_id": a.opportunity_id,
            "opportunity_title": a.opportunity.title if a.opportunity else None,
            "assigned_user_id": a.assigned_user_id}


@app.get("/api/appointments/{appointment_id}")
def get_appointment(appointment_id: int, db: Session = Depends(get_db),
                    _: auth.Principal = auth.ANY_USER):
    a = db.get(Appointment, appointment_id)
    if not a:
        raise HTTPException(404, "appointment not found")
    return _appointment_detail(a)


class AppointmentPatch(BaseModel):
    title: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    status: str | None = None
    contact_id: int | None = None
    calendar_id: int | None = None
    assigned_user_id: int | None = None
    # Bind or unbind the deal. Deliberately NOT part of what reschedules anything:
    # only `starts_at` and the status touch the reminder queue (see below), and
    # linking a booking to a deal is not a change to when it happens.
    opportunity_id: int | None = None
    notes: str | None = None


def _drop_pending_reminders(db: Session, appointment_id: int) -> int:
    """Retire every reminder still queued for this appointment. Returns how many.

    Two things happen to each job, and both matter:

    * `status = "cancelled"` — not a delete. A reminder that was queued and then
      superseded is history worth keeping; the worker only ever claims `pending`.
    * `dedupe_key = None` — the key is RELEASED. `enqueue()` refuses a key that
      already exists *whatever its status*, and the key includes the start time
      (`appt_reminder:<id>:<starts_at>:<offset>`). So moving a booking Tuesday ->
      Friday -> back to Tuesday would hit the retired Tuesday key, `enqueue()`
      would return None, and the customer would silently get NO reminder — the
      exact failure the start time was put in the key to prevent, one move later.
      The key is an idempotency guard on live work, not a permanent record, so it
      is handed back when the work stops being live.

    `payload` is a plain JSON column with no mutation tracking, so it is
    reassigned rather than mutated in place — the released key is kept there so
    the trail still says which reminder this row was.
    """
    dropped = 0
    for job in db.scalars(select(Job).where(
            Job.type == "appointment_reminder", Job.status == "pending")).all():
        if job.payload.get("appointment_id") != appointment_id:
            continue
        job.status = "cancelled"
        if job.dedupe_key:
            job.payload = {**job.payload, "superseded_key": job.dedupe_key}
            job.dedupe_key = None
        dropped += 1
    db.flush()
    return dropped


@app.patch("/api/appointments/{appointment_id}")
def update_appointment(appointment_id: int, body: AppointmentPatch,
                       db: Session = Depends(get_db),
                       _: auth.Principal = auth.STAFF):
    """Edit a booking. Moving it, or switching it off and on, reschedules the
    reminders.

    Rescheduling is the interesting case: the T-24h and T-1h jobs were queued
    against the OLD time, so they must be dropped and re-queued, or the customer
    is reminded about a slot that no longer exists.

    Turning the status to `cancelled` is the same problem wearing a different
    hat. `_h_appointment_reminder` does re-check the status when it runs, so a
    stale job cannot actually send — but leaving it `pending` means
    `ghl jobs list --status pending` shows a reminder for an appointment that is
    off, and reviving the booking would then find its keys taken. The jobs are
    retired here instead, and re-queued if the booking comes back.
    """
    a = db.get(Appointment, appointment_id)
    if not a:
        raise HTTPException(404, "appointment not found")

    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in APPOINTMENT_STATUSES:
        raise HTTPException(400, "unknown status %r — expected one of %s" % (
            data["status"], sorted(APPOINTMENT_STATUSES)))
    # An edit must not be able to blank a title that the create path refuses.
    if "title" in data:
        data["title"] = _clean_appointment_title(data["title"] or "")
    # A dangling id is a 404 with a sentence, not an IntegrityError rendered as
    # "Something went wrong (500)." in the panel. The dialog picks from lists, so
    # this is really the CLI's and the telephony feed's answer.
    for field, model, what in (("contact_id", Contact, "contact"),
                               ("calendar_id", Calendar, "calendar"),
                               ("opportunity_id", Opportunity, "opportunity"),
                               ("assigned_user_id", User, "user")):
        if data.get(field) is not None and not db.get(model, data[field]):
            raise HTTPException(404, "%s %s not found" % (what, data[field]))

    old_start = _aware(a.starts_at)
    was_off = a.status in automations.NO_REMINDER_STATUSES
    for k, v in data.items():
        setattr(a, k, v)
    # Validate against the stored values, so patching only one end still checks.
    if _aware(a.ends_at) <= _aware(a.starts_at):
        raise HTTPException(400, "ends_at must be after starts_at")
    db.flush()

    moved = "starts_at" in data and _aware(a.starts_at) != old_start
    is_off = a.status in automations.NO_REMINDER_STATUSES

    outcome = "unchanged"
    if moved or is_off != was_off:
        # Always retire first, including on the revive path: it is what makes
        # "exactly one pending reminder per offset" true no matter how the
        # booking got here.
        _drop_pending_reminders(db, a.id)
        outcome = ("reminders cancelled" if is_off
                   else automations.on_appointment_booked(db, a))

    db.commit()
    db.refresh(a)
    return {**_appointment_detail(a), "automation": outcome}


@app.delete("/api/appointments/{appointment_id}")
def cancel_appointment(appointment_id: int, db: Session = Depends(get_db),
                       _: auth.Principal = auth.STAFF):
    """Cancel, not delete.

    The row survives: GHL's own Appointment report has a Cancelled tile, so a
    cancelled booking is a record, and throwing it away would lose the fact that
    the slot was ever taken.

    The reminders do NOT survive. `_h_appointment_reminder` re-checks the status
    at run time, so a leftover job could never send — but "could never send" and
    "is not queued" are different things to the dispatcher reading
    `ghl jobs list --status pending`, and only the second one is true here.
    """
    a = db.get(Appointment, appointment_id)
    if not a:
        raise HTTPException(404, "appointment not found")
    a.status = "cancelled"
    dropped = _drop_pending_reminders(db, a.id)
    db.commit()
    return {"id": a.id, "status": a.status, "reminders_cancelled": dropped}


# ---------- deletes ----------

@app.delete("/api/contacts/{contact_id}")
def delete_contact(contact_id: int, force: bool = False,
                   db: Session = Depends(get_db),
                   _: auth.Principal = auth.ADMIN):
    """Delete a contact and its conversation history.

    Opportunities are DETACHED, never deleted: their `custom_fields` carry
    `owen_call_id`, documented in DECISIONS.md as the join key to the telephony
    project's attribution history. Deleting them to tidy up a contact would break
    reporting that has nothing to do with this record.
    """
    c = db.get(Contact, contact_id)
    if not c:
        raise HTTPException(404, "contact not found")

    opp_ids = [o.id for o in db.scalars(
        select(Opportunity).where(Opportunity.contact_id == contact_id)).all()]
    if opp_ids and not force:
        raise HTTPException(409, "contact has %d opportunit%s (%s) — pass "
                                 "force=true to detach them and delete anyway"
                            % (len(opp_ids), "y" if len(opp_ids) == 1 else "ies",
                               ", ".join(str(i) for i in opp_ids)))

    for opp in db.scalars(select(Opportunity).where(
            Opportunity.contact_id == contact_id)).all():
        opp.contact_id = None
    # Conversations are not cascade-deleted from Contact, and conversations.
    # contact_id is NOT NULL, so this has to be explicit. Their events cascade.
    for conv in db.scalars(select(Conversation).where(
            Conversation.contact_id == contact_id)).all():
        db.delete(conv)
    db.flush()
    db.delete(c)            # ContactTag rows cascade
    db.commit()
    return {"deleted": contact_id, "detached_opportunities": opp_ids}


@app.delete("/api/conversations/{conv_id}")
def delete_conversation(conv_id: int, db: Session = Depends(get_db),
                        _: auth.Principal = auth.ADMIN):
    """Delete one conversation and its own events. NOTHING else.

    ADMIN only, like the other two deletes. This is customer correspondence —
    texts, calls, recordings and the crew's internal notes — and there is no soft
    delete anywhere in this codebase (see "Why Restore stays dead" in
    DECISIONS.md), so it is gone the moment this returns.

    The contact stays, and so does everything hanging off it. `delete_contact`
    above has to delete conversations explicitly because they are deliberately NOT
    cascaded from Contact; the same care applies in reverse, and more so — an
    opportunity carries `custom_fields.owen_call_id`, the telephony project's live
    join key, and an appointment is a slot somebody booked. Removing a thread is
    tidying a mailbox, not erasing a customer.

    Only `ConversationEvent` cascades, through `Conversation.events`'
    delete-orphan, and nothing else in the schema references a conversation.
    """
    conv = db.get(Conversation, conv_id)
    if not conv:
        raise HTTPException(404, "conversation not found")
    contact_id = conv.contact_id
    # Counted before the delete, because afterwards there is nothing to count and
    # the caller still needs to be told what it lost.
    events = _event_counts(db, conv_id).get(conv_id, 0)
    db.delete(conv)
    db.commit()
    return {"deleted": conv_id, "contact_id": contact_id,
            "events_deleted": events}


@app.delete("/api/opportunities/{opp_id}")
def delete_opportunity(opp_id: int, db: Session = Depends(get_db),
                       _: auth.Principal = auth.ADMIN):
    """Delete a deal. Its APPOINTMENTS are detached, never deleted.

    A booked visit is a promise to a customer: somebody is expecting a van on
    Tuesday, and tidying up a deal record must not quietly cancel it. This is the
    same call `delete_contact` already makes about opportunities, for the same
    reason — the relationship is a weak one and the column is nullable.

    It is also not optional. `appointments.opportunity_id` is a real foreign key,
    so on PostgreSQL deleting the row underneath it would raise rather than
    cascade; detaching first is what makes the delete work at all.
    """
    o = db.get(Opportunity, opp_id)
    if not o:
        raise HTTPException(404, "opportunity not found")
    booked = db.scalars(select(Appointment).where(
        Appointment.opportunity_id == opp_id)).all()
    for a in booked:
        a.opportunity_id = None
    db.flush()
    db.delete(o)
    db.commit()
    return {"deleted": opp_id, "detached_appointments": [a.id for a in booked]}


# ---------- global event search ----------
#
# The thread view answers "what happened with this contact". These answer "find me
# the call/message", across every thread at once — which is what a CLI needs and
# what /api/conversations/{id}/events cannot do.

def _search_events(db: Session, *, types: set[EventType],
                   start: datetime | None = None, end: datetime | None = None,
                   direction: str = "all", contact_id: int | None = None,
                   q: str | None = None, extra=None,
                   page: int = 1, page_size: int = 50,
                   order: str = "desc") -> tuple[list, int]:
    """Shared by /api/calls and /api/messages — same query, different projection."""
    stmt = (select(ConversationEvent, Contact)
            .join(Conversation,
                  Conversation.id == ConversationEvent.conversation_id)
            .join(Contact, Contact.id == Conversation.contact_id)
            .where(ConversationEvent.type.in_(types)))

    if start:
        stmt = stmt.where(ConversationEvent.occurred_at >= start)
    if end:
        stmt = stmt.where(ConversationEvent.occurred_at <= end)
    if direction != "all":
        stmt = stmt.where(ConversationEvent.direction == Direction[direction])
    if contact_id:
        stmt = stmt.where(Contact.id == contact_id)
    if q:
        # transcript as well as body: searching call transcripts is the whole
        # point of `ghl calls list --q`.
        like = contains(q)
        esc = LIKE_ESCAPE
        stmt = stmt.where(or_(ConversationEvent.body.ilike(like, escape=esc),
                              ConversationEvent.transcript.ilike(like, escape=esc),
                              ConversationEvent.subject.ilike(like, escape=esc)))
    for clause in (extra or []):
        stmt = stmt.where(clause)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    col = ConversationEvent.occurred_at
    stmt = stmt.order_by(col.asc() if order == "asc" else col.desc())
    rows = db.execute(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    return rows, total


def _paged(items: list, total: int, page: int, page_size: int) -> dict:
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size)}


@app.get("/api/calls")
def list_calls(
    db: Session = Depends(get_db),
    start: datetime | None = None,
    end: datetime | None = None,
    direction: Literal["all", "INBOUND", "OUTBOUND"] = "all",
    call_status: str | None = None,
    min_duration: int | None = None,
    max_duration: int | None = None,
    has_recording: bool | None = None,
    contact_id: int | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    order: Literal["asc", "desc"] = "desc",
    _: auth.Principal = auth.ANY_USER,
):
    """Search calls across every conversation.

    `q` matches the transcript as well as the body, so "find the call where they
    mentioned the skylight" works.
    """
    extra = []
    if call_status:
        wanted = {s.strip() for s in call_status.split(",") if s.strip()}
        unknown = wanted - CALL_STATUSES
        if unknown:
            raise HTTPException(400, "unknown call_status %r — expected one of %s"
                                % (min(unknown), sorted(CALL_STATUSES)))
        extra.append(ConversationEvent.call_status.in_(wanted))
    if min_duration is not None:
        extra.append(ConversationEvent.duration_seconds >= min_duration)
    if max_duration is not None:
        extra.append(ConversationEvent.duration_seconds <= max_duration)
    if has_recording is True:
        extra.append(ConversationEvent.recording_url.is_not(None))
    elif has_recording is False:
        extra.append(ConversationEvent.recording_url.is_(None))

    rows, total = _search_events(
        db, types={EventType.CALL}, start=start, end=end, direction=direction,
        contact_id=contact_id, q=q, extra=extra, page=page, page_size=page_size,
        order=order)

    return _paged([{
        "id": e.id, "conversation_id": e.conversation_id,
        "contact_id": c.id, "contact_name": c.name, "contact_phone": c.phone,
        "occurred_at": e.occurred_at, "direction": e.direction.value,
        "call_status": e.call_status, "duration_seconds": e.duration_seconds,
        "recording_url": e.recording_url, "transcript": e.transcript,
        "body": e.body,
    } for e, c in rows], total, page, page_size)


MESSAGE_TYPES = {EventType.SMS, EventType.EMAIL, EventType.NOTE,
                 EventType.INTERNAL_COMMENT, EventType.WHATSAPP}


@app.get("/api/messages")
def list_messages(
    db: Session = Depends(get_db),
    start: datetime | None = None,
    end: datetime | None = None,
    type: str = "all",
    direction: Literal["all", "INBOUND", "OUTBOUND"] = "all",
    delivery_status: str | None = None,
    contact_id: int | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    order: Literal["asc", "desc"] = "desc",
    principal: auth.Principal = auth.ANY_USER,
):
    """Search messages and notes across every conversation.

    Internal notes are STAFF-only: `type=all` narrows for a TECH, and asking for
    them by name is refused outright.
    """
    if type == "all":
        types = set(MESSAGE_TYPES)
    else:
        try:
            types = {EventType[type.upper()]}
        except KeyError:
            raise HTTPException(400, "unknown type %r — expected one of %s" % (
                type, sorted(t.value for t in MESSAGE_TYPES))) from None
        if types - MESSAGE_TYPES:
            raise HTTPException(400, "%r is not a message type" % type)

    if not _sees_internal(principal):
        if type != "all" and types & INTERNAL_TYPES:
            _refuse_internal(principal, "read")
        types -= INTERNAL_TYPES

    extra = []
    if delivery_status:
        # A bad value used to raise KeyError straight out of the enum lookup, which
        # FastAPI turns into a 500 — an unknown filter is the caller's mistake, not
        # ours, and the CLI's `--status` passes whatever it is given. Now it says
        # what the valid words are, the way every other filter on this route does.
        try:
            wanted = DeliveryStatus[delivery_status.upper()]
        except KeyError:
            raise HTTPException(
                400, "unknown delivery_status %r — expected one of %s"
                % (delivery_status, sorted(s.value for s in DeliveryStatus))) from None
        extra.append(ConversationEvent.delivery_status == wanted)

    rows, total = _search_events(
        db, types=types, start=start, end=end, direction=direction,
        contact_id=contact_id, q=q, extra=extra, page=page, page_size=page_size,
        order=order)

    return _paged([{
        "id": e.id, "conversation_id": e.conversation_id,
        "contact_id": c.id, "contact_name": c.name, "contact_phone": c.phone,
        "occurred_at": e.occurred_at, "type": e.type.value,
        "direction": e.direction.value, "subject": e.subject, "body": e.body,
        "delivery_status": e.delivery_status.value if e.delivery_status else None,
        "delivery_detail": e.delivery_detail,
    } for e, c in rows], total, page, page_size)


# ---------- global search (the ctrl+K palette) ----------
#
# ONE endpoint, deliberately, rather than the palette fanning out to /api/contacts,
# /api/opportunities and /api/messages on every debounced keystroke. Three requests
# per keystroke for a result set the user reads as a single list is the wrong shape,
# and it would put ranking, the per-group cap and the role rule in three different
# files with three chances to disagree.
#
# Scope is contacts, opportunities and messages. Call TRANSCRIPTS are deliberately
# out (owner's decision, DECISIONS.md 2026-09-10) even though `GET /api/calls?q=`
# already searches them: a recorded call is minutes of speech, so short queries hit
# nearly every one and bury the contact the user was actually reaching for.

SEARCH_GROUP_CAP = 5

# NOT EventType.CALL: see above. MESSAGE_TYPES minus nothing -- the internal ones
# are removed per-request, by role.
SEARCH_MESSAGE_TYPES = frozenset(MESSAGE_TYPES)


def _snippet(body: str | None, q: str, width: int = 90) -> str:
    """A window of `body` around the first case-insensitive hit on `q`.

    A thread found by something said in it is only useful if the row shows the
    thing that was said. Whitespace is collapsed first, because an email body
    arrives with newlines that would eat most of the width.
    """
    text = " ".join((body or "").split())
    if len(text) <= width:
        return text
    at = text.lower().find(q.lower())
    if at < 0:                                  # matched somewhere we do not show
        return text[:width].rstrip() + "…"
    start = max(0, at - width // 3)
    end = min(len(text), start + width)
    return ("…" if start else "") + text[start:end].strip() + (
        "…" if end < len(text) else "")


def _search_contacts(db: Session, term: str, limit: int) -> tuple[list, int]:
    """Name, email, phone -- the three things anyone types to find a person.

    `first_name + " " + last_name` is matched as well as the columns separately,
    because "jane doe" is in neither column on its own. Same reasoning, and the
    same rendering (`||` on both Postgres and SQLite), as GET /api/contacts.
    """
    like = contains(term)
    stmt = select(Contact).where(or_(
        Contact.first_name.ilike(like, escape=LIKE_ESCAPE),
        Contact.last_name.ilike(like, escape=LIKE_ESCAPE),
        (Contact.first_name + " " + Contact.last_name).ilike(
            like, escape=LIKE_ESCAPE),
        Contact.email.ilike(like, escape=LIKE_ESCAPE),
        Contact.phone.ilike(like, escape=LIKE_ESCAPE)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Contact.first_name, Contact.last_name, Contact.id)
        .limit(limit)).all()
    return [{"id": c.id, "name": c.name, "email": c.email, "phone": c.phone}
            for c in rows], total


def _search_opportunities(db: Session, term: str, limit: int) -> tuple[list, int]:
    """Title only, per the owner. Every status, not just open: a palette is how
    you go back to a deal you already won or lost, so `status` rides along and the
    row says which."""
    stmt = (select(Opportunity)
            .options(selectinload(Opportunity.stage),
                     selectinload(Opportunity.contact))
            .where(Opportunity.title.ilike(contains(term), escape=LIKE_ESCAPE)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Opportunity.updated_at.desc(), Opportunity.id.desc())
        .limit(limit)).all()
    return [{"id": o.id, "title": o.title, "status": o.status,
             "value_cents": o.value_cents,
             "pipeline_id": o.pipeline_id, "stage_id": o.stage_id,
             "stage_name": o.stage.name if o.stage else None,
             "contact_name": o.contact.name if o.contact else None}
            for o in rows], total


def _search_messages(db: Session, term: str, limit: int,
                     types: set[EventType]) -> tuple[list, int]:
    """Message BODIES, so a thread can be found by something said in it.

    The result identifies the thread it belongs to (`conversation_id` plus the
    contact), because finding the sentence is only half of what the user wants --
    the other half is being taken to the conversation it was said in.

    Subject lines are NOT matched: the owner asked for bodies. Cheap to add later.
    """
    stmt = (select(ConversationEvent, Contact)
            .join(Conversation,
                  Conversation.id == ConversationEvent.conversation_id)
            .join(Contact, Contact.id == Conversation.contact_id)
            .where(ConversationEvent.type.in_(types),
                   ConversationEvent.body.ilike(contains(term),
                                                escape=LIKE_ESCAPE)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(
        stmt.order_by(ConversationEvent.occurred_at.desc(),
                      ConversationEvent.id.desc())
        .limit(limit)).all()
    return [{"id": e.id, "conversation_id": e.conversation_id,
             "contact_id": c.id, "contact_name": c.name,
             "type": e.type.value, "direction": e.direction.value,
             "occurred_at": e.occurred_at,
             "snippet": _snippet(e.body, term)} for e, c in rows], total


@app.get("/api/search")
def search(
    db: Session = Depends(get_db),
    q: str = "",
    limit: int = Query(SEARCH_GROUP_CAP, ge=1, le=25),
    principal: auth.Principal = auth.ANY_USER,
):
    """Grouped, capped search across contacts, opportunities and messages.

    Every group is present in every response, empty ones included, so the palette
    renders a stable shape and "no results" is a property of the groups rather
    than of a missing key. `total` is the real number of matches and `truncated`
    says the list was cut -- the cap is reported, never silent.

    A blank `q` is not an error: the palette calls this as the box is being
    cleared. It returns the same empty shape rather than 422.

    Role: internal notes are STAFF-only (see INTERNAL_TYPES), so a TECH's message
    group is narrower. Everything else here is already ANY_USER-readable through
    the list endpoints, so search adds no reach.
    """
    term = q.strip()
    types = set(SEARCH_MESSAGE_TYPES)
    if not _sees_internal(principal):
        types -= INTERNAL_TYPES

    if term:
        found = [("contacts", "Contacts", _search_contacts(db, term, limit)),
                 ("opportunities", "Opportunities",
                  _search_opportunities(db, term, limit)),
                 ("messages", "Messages",
                  _search_messages(db, term, limit, types))]
    else:
        found = [(k, label, ([], 0)) for k, label in
                 (("contacts", "Contacts"), ("opportunities", "Opportunities"),
                  ("messages", "Messages"))]

    return {
        "q": term,
        "limit": limit,
        "total": sum(total for _, _, (_, total) in found),
        "groups": [{"type": key, "label": label, "items": items,
                    "total": total, "truncated": total > len(items)}
                   for key, label, (items, total) in found],
    }


# ---------- outbound messaging ----------

class MessageSend(BaseModel):
    body: str
    type: Literal["SMS", "EMAIL", "NOTE", "INTERNAL_COMMENT"] = "SMS"
    subject: str | None = None


def _send_to_contact(db: Session, contact: Contact, body: MessageSend,
                     principal: auth.Principal) -> dict:
    if not body.body.strip():
        raise HTTPException(400, "a message needs a body")
    # Writing a note a TECH cannot then read would be a worse bug than refusing
    # the write, so the read rule and the write rule are the same rule.
    if EventType[body.type] in INTERNAL_TYPES and not _sees_internal(principal):
        _refuse_internal(principal, "write")
    ev, reason = automations.send_outbound(
        db, contact, body.body, type_=EventType[body.type], subject=body.subject)
    db.commit()
    if ev is None:
        # 201 with suppressed=True, not an error: the request was well-formed and
        # the outcome is a business rule, matching how the existing routes report
        # {"automation": "suppressed: contact is on DND"}.
        return {"suppressed": True, "reason": reason, "id": None,
                "conversation_id": None}
    db.refresh(ev)
    return {"suppressed": False, "reason": reason, "id": ev.id,
            "conversation_id": ev.conversation_id, "type": ev.type.value,
            "direction": ev.direction.value, "occurred_at": ev.occurred_at,
            "body": ev.body, "subject": ev.subject,
            "delivery_status": (ev.delivery_status.value
                                if ev.delivery_status else None),
            "delivery_detail": ev.delivery_detail}


@app.post("/api/contacts/{contact_id}/messages", status_code=201)
def send_to_contact(contact_id: int, body: MessageSend,
                    db: Session = Depends(get_db),
                    principal: auth.Principal = auth.ANY_USER):
    """Send to a contact, creating the thread if there isn't one.

    Contact-first because that is how the CLI addresses people. While
    LoggingTransport is the only transport, this records the intent and
    transmits nothing — `delivery_status` comes back LOGGED_ONLY.
    """
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(404, "contact not found")
    return _send_to_contact(db, contact, body, principal)


@app.post("/api/conversations/{conv_id}/messages", status_code=201)
def send_to_conversation(conv_id: int, body: MessageSend,
                         db: Session = Depends(get_db),
                         principal: auth.Principal = auth.ANY_USER):
    """Send on an open thread — the shape the Conversations composer uses."""
    conv = db.get(Conversation, conv_id)
    if not conv:
        raise HTTPException(404, "conversation not found")
    contact = db.get(Contact, conv.contact_id)
    if not contact:
        raise HTTPException(404, "conversation has no contact")
    return _send_to_contact(db, contact, body, principal)


def _place_call(db: Session, contact: Contact) -> dict:
    """Ring the customer from the bound DID, via owen-main.

    There is no browser softphone and there does not need to be: owen-main rings an
    operator's phone first, then the customer, and bridges the two legs. The button
    in the thread header starts that; the conversation happens on real handsets.

    Refusals are answered 200 with `placed: false` and a sentence, not a 4xx. The
    caller is a person who pressed a button, the outcome is a business rule rather
    than a malformed request, and this matches how a suppressed send already
    reports itself.
    """
    if not contact.phone:
        return {"placed": False, "id": None,
                "reason": "This contact has no phone number to call."}
    # DND is a promise to the customer, and every other customer-facing path in
    # this app honours it. Making voice the silent exception would be a surprise
    # rather than a feature. Overrulable: the owner may well want a dispatcher to
    # be able to ring someone who has only opted out of texts.
    if contact.dnd:
        return {"placed": False, "id": None,
                "reason": "This contact is on Do Not Disturb."}
    if not crmlink.configured():
        return {"placed": False, "id": None,
                "reason": "Calling is not switched on — this CRM is not connected "
                          "to the phone system yet."}

    result = crmlink.place_call(to_number=contact.phone)
    if not result.ok:
        return {"placed": False, "id": None, "reason": result.reason}

    # The call is ringing. Record it so the thread shows it happened.
    #
    # `call_status` is left NULL on purpose. We know a call was PLACED; we do not
    # know how it ended, and the five statuses are all outcomes. The column is
    # nullable for exactly this and the Call report already renders a null as
    # "unknown" rather than inventing "completed".
    #
    # Nothing will fill it in later, either: owen-main reports call lifecycle only
    # for INBOUND calls on a bound DID (`integrations/crm/handler.py`), so an
    # outbound call it places on our behalf produces no follow-up event. That is a
    # gap on the far side, recorded in the sentinel, not something to paper over
    # here with a status we did not observe.
    linkedid = str((result.data or {}).get("linkedid") or "")
    conv = automations.thread_for(db, contact.id)
    ev = ConversationEvent(
        conversation_id=conv.id, type=EventType.CALL,
        direction=Direction.OUTBOUND, occurred_at=datetime.now(UTC),
        body="Outbound call placed from %s. Ringing an operator, then the customer."
             % crmlink.current().from_number,
        provider_ref=linkedid or None,
    )
    db.add(ev)
    conv.last_event_at = ev.occurred_at
    db.commit()
    db.refresh(ev)
    return {"placed": True, "id": ev.id, "conversation_id": conv.id,
            "reason": "Calling %s now — your phone will ring first."
                      % (format_phone(contact.phone) or contact.phone)}


@app.post("/api/contacts/{contact_id}/call")
def call_contact(contact_id: int, db: Session = Depends(get_db),
                 _: auth.Principal = auth.ANY_USER):
    """Place a call to a contact.

    ANY_USER: a TECH may send a customer a message (CLAUDE.md), and ringing the
    customer they are already texting is the same kind of act, not a wider one.
    """
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(404, "contact not found")
    return _place_call(db, contact)


@app.post("/api/conversations/{conv_id}/call")
def call_conversation(conv_id: int, db: Session = Depends(get_db),
                      _: auth.Principal = auth.ANY_USER):
    """The shape the thread header's phone button uses."""
    conv = db.get(Conversation, conv_id)
    if not conv:
        raise HTTPException(404, "conversation not found")
    contact = db.get(Contact, conv.contact_id)
    if not contact:
        raise HTTPException(404, "conversation has no contact")
    return _place_call(db, contact)


class ConversationPatch(BaseModel):
    read: bool | None = None
    starred: bool | None = None


@app.patch("/api/conversations/{conv_id}")
def update_conversation(conv_id: int, body: ConversationPatch,
                        db: Session = Depends(get_db),
                        _: auth.Principal = auth.ANY_USER):
    conv = db.get(Conversation, conv_id)
    if not conv:
        raise HTTPException(404, "conversation not found")
    data = body.model_dump(exclude_unset=True)
    if "read" in data:
        conv.unread_count = 0 if data["read"] else max(1, conv.unread_count)
    if "starred" in data:
        conv.starred = data["starred"]
    db.commit()
    db.refresh(conv)
    contact = db.get(Contact, conv.contact_id)
    return {"id": conv.id, "contact_id": conv.contact_id,
            "contact_name": contact.name if contact else None,
            "contact_phone": contact.phone if contact else None,
            "contact_dnd": bool(contact.dnd) if contact else False,
            "last_event_at": conv.last_event_at,
            "unread_count": conv.unread_count,
            # Same shape as a row from GET /api/conversations, so the browser can
            # drop this straight into the cached list without the row it patches
            # losing a field the list had.
            "event_count": _event_counts(db, conv.id).get(conv.id, 0),
            "starred": conv.starred}


# ---------- tags ----------

@app.get("/api/tags")
def list_tags(db: Session = Depends(get_db),
              _: auth.Principal = auth.ANY_USER):
    counts = dict(db.execute(
        select(ContactTag.tag_id, func.count()).group_by(ContactTag.tag_id)).all())
    rows = db.scalars(select(Tag).order_by(Tag.name)).all()
    return sorted(
        [{"id": t.id, "name": t.name, "color": t.color,
          "count": counts.get(t.id, 0)} for t in rows],
        key=lambda r: (-r["count"], r["name"]))


# ---------- jobs (queue visibility) ----------

@app.get("/api/jobs")
def list_jobs(db: Session = Depends(get_db),
              status: str | None = None, type: str | None = None,
              limit: int = Query(50, ge=1, le=500),
              _: auth.Principal = auth.ADMIN):
    """What the automations queued. Without this the only way to tell whether a
    rule fired is to read the worker's stdout."""
    stmt = select(Job)
    if status:
        stmt = stmt.where(Job.status == status)
    if type:
        stmt = stmt.where(Job.type == type)
    rows = db.scalars(stmt.order_by(Job.id.desc()).limit(limit)).all()
    return [{"id": j.id, "type": j.type, "status": j.status,
             "attempts": j.attempts, "max_attempts": j.max_attempts,
             "run_after": j.run_after, "last_error": j.last_error,
             "completed_at": j.completed_at, "created_at": j.created_at,
             "dedupe_key": j.dedupe_key, "payload": j.payload} for j in rows]


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: int, db: Session = Depends(get_db),
              _: auth.Principal = auth.ADMIN):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job.status == "done":
        raise HTTPException(400, "job already completed — retrying would repeat "
                                 "its side effect")
    job.status = "pending"
    job.run_after = datetime.now(UTC)
    job.last_error = None
    db.commit()
    return {"id": job.id, "status": job.status, "run_after": job.run_after}


# ---------- auth ----------
#
# Browser: short-lived JWT in an httpOnly cookie (LOCKED, DECISIONS.md).
# CLI:     a long-lived `ghl_pat_...` bearer token, revocable from the UI.
#
# Login is `def`, not `async def`, on purpose: scrypt blocks for ~700ms, and a sync
# route runs in the threadpool instead of stalling the event loop.

class LoginBody(BaseModel):
    email: str
    password: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class TokenCreate(BaseModel):
    name: str
    expires_in_days: int | None = None
    scopes: str = ""


class UserCreate(BaseModel):
    email: str
    name: str
    password: str
    role: Literal["ADMIN", "DISPATCHER", "TECH"] = "TECH"


class UserPatch(BaseModel):
    name: str | None = None
    role: Literal["ADMIN", "DISPATCHER", "TECH"] | None = None
    is_active: bool | None = None
    password: str | None = None


# Failed-login throttle. In-process and reset by a restart, which is proportionate
# for four users on one box — DECISIONS.md rules out Redis, and a table would be
# more moving parts than the risk warrants.
#
# Keyed on email, which is unauthenticated input, so the map is capped: without a
# bound anyone could grow it indefinitely by posting random addresses.
_LOGIN_FAILS: dict[str, tuple[int, datetime]] = {}
_MAX_FAILS = 10
_LOCKOUT = timedelta(minutes=15)
_MAX_TRACKED = 1000


def _user_public(u: User) -> dict:
    return {"id": u.id, "name": u.name, "email": u.email,
            "role": u.role.value, "is_active": u.is_active}


def _issue_session(response: Response, user: User) -> None:
    """Set the access, refresh and CSRF cookies."""
    secure = os.getenv("COOKIE_SECURE", "0") == "1"
    response.set_cookie(auth.COOKIE_ACCESS, auth.make_access_token(user),
                        httponly=True, samesite="lax", secure=secure,
                        max_age=int(auth.ACCESS_TTL.total_seconds()), path="/")
    # Path-scoped so the refresh token is not sent on ordinary API calls.
    response.set_cookie(auth.COOKIE_REFRESH, auth.make_refresh_token(user),
                        httponly=True, samesite="lax", secure=secure,
                        max_age=int(auth.REFRESH_TTL.total_seconds()),
                        path=auth.REFRESH_PATH)
    # Readable by JS on purpose — the frontend echoes it back in X-CSRF-Token.
    response.set_cookie(auth.COOKIE_CSRF, secrets.token_urlsafe(24),
                        httponly=False, samesite="lax", secure=secure,
                        max_age=int(auth.REFRESH_TTL.total_seconds()), path="/")


@app.post("/api/auth/login")
def login(body: LoginBody, response: Response, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    fails, until = _LOGIN_FAILS.get(email, (0, datetime.min.replace(tzinfo=UTC)))
    if fails >= _MAX_FAILS and datetime.now(UTC) < until:
        raise HTTPException(429, "too many failed attempts — try again later")

    user = db.scalar(select(User).where(func.lower(User.email) == email))
    if user is None:
        # Hash anyway, so an unknown email takes as long as a wrong password and
        # response time cannot be used to enumerate accounts.
        auth.waste_time_like_a_real_login()
        _note_login_failure(email)
        raise HTTPException(401, "invalid email or password")
    if not user.is_active:
        raise HTTPException(403, "this account is deactivated")
    if not auth.verify_password(body.password, user.password_hash):
        _note_login_failure(email)
        raise HTTPException(401, "invalid email or password")

    _LOGIN_FAILS.pop(email, None)
    _issue_session(response, user)
    return {"user": _user_public(user)}


def _note_login_failure(email: str) -> None:
    now = datetime.now(UTC)
    fails, until = _LOGIN_FAILS.get(email, (0, now))
    # Start a fresh count once the previous window has elapsed. Without this the
    # counter only ever grows, so after one lockout every single wrong password
    # re-locks the account for another 15 minutes — effectively one attempt per
    # quarter of an hour, forever, which is not what "10 attempts" means.
    if fails >= _MAX_FAILS and now >= until:
        fails = 0
    if len(_LOGIN_FAILS) >= _MAX_TRACKED and email not in _LOGIN_FAILS:
        for stale, (_, expiry) in list(_LOGIN_FAILS.items()):
            if now >= expiry:
                del _LOGIN_FAILS[stale]
        if len(_LOGIN_FAILS) >= _MAX_TRACKED:
            return          # nothing expired: stop growing rather than evict a live lock
    _LOGIN_FAILS[email] = (fails + 1, now + _LOCKOUT)


@app.post("/api/auth/refresh")
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    # This route is exempt from the gate (it must work while the access token is
    # expired), so it carries its own CSRF check — it is still a cookie-driven
    # state change and would otherwise be the one unprotected write.
    auth.check_csrf(request)
    raw = request.cookies.get(auth.COOKIE_REFRESH)
    if not raw:
        raise HTTPException(401, "no refresh token")
    payload = auth.decode_token(raw, "refresh")
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(401, "user is inactive")
    if payload.get("tv") != user.token_version:
        raise HTTPException(401, "session has been invalidated — log in again")
    # Rotate both, so a stolen refresh token has a bounded life.
    _issue_session(response, user)
    return {"user": _user_public(user)}


@app.post("/api/auth/logout")
def logout(response: Response):
    """Exempt from the gate: logging out must work even with an expired token."""
    response.delete_cookie(auth.COOKIE_ACCESS, path="/")
    response.delete_cookie(auth.COOKIE_REFRESH, path=auth.REFRESH_PATH)
    response.delete_cookie(auth.COOKIE_CSRF, path="/")
    return {"ok": True}


@app.post("/api/auth/logout-all")
def logout_all(response: Response, db: Session = Depends(get_db),
               principal: auth.Principal = auth.ANY_USER):
    """Invalidate every session and JWT for this user, everywhere.

    Does not revoke API tokens — those are listed and revoked individually, so
    "log out of my browsers" does not silently break the CLI or the telephony feed.
    """
    user = db.get(User, principal.user_id)
    user.token_version += 1
    db.commit()
    return logout(response)


@app.get("/api/auth/me")
def me(db: Session = Depends(get_db), principal: auth.Principal = auth.ANY_USER):
    user = db.get(User, principal.user_id)
    token = db.get(ApiToken, principal.token_id) if principal.token_id else None
    return {"user": _user_public(user), "kind": principal.kind,
            "scopes": sorted(principal.scopes),
            "token": {"id": token.id, "name": token.name,
                      "prefix": token.prefix} if token else None}


@app.post("/api/auth/password")
def change_password(body: PasswordChange, response: Response,
                    db: Session = Depends(get_db),
                    principal: auth.Principal = auth.ANY_USER):
    user = db.get(User, principal.user_id)
    if not auth.verify_password(body.current_password, user.password_hash):
        raise HTTPException(403, "current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(400, "password must be at least 8 characters")

    user.password_hash = auth.hash_password(body.new_password)
    # Everything issued under the old password stops working immediately.
    user.token_version += 1
    db.commit()
    db.refresh(user)
    _issue_session(response, user)          # keep the caller logged in
    return {"ok": True, "user": _user_public(user)}


@app.post("/api/auth/tokens", status_code=201)
def create_token(body: TokenCreate, db: Session = Depends(get_db),
                 principal: auth.Principal = auth.ANY_USER):
    """Mint a personal access token. The secret is returned ONCE and never again."""
    if not body.name.strip():
        raise HTTPException(400, "a token needs a name")
    user = db.get(User, principal.user_id)
    plain, token = auth.mint_api_token(
        user, name=body.name.strip(), scopes=body.scopes,
        expires_in_days=body.expires_in_days)
    db.add(token)
    db.commit()
    db.refresh(token)
    return {**_token_public(token), "token": plain}


def _token_public(t: ApiToken) -> dict:
    return {"id": t.id, "name": t.name, "prefix": t.prefix,
            "scopes": t.scopes, "created_at": t.created_at,
            "last_used_at": t.last_used_at, "expires_at": t.expires_at,
            "revoked_at": t.revoked_at}


@app.get("/api/auth/tokens")
def list_tokens(db: Session = Depends(get_db),
                principal: auth.Principal = auth.ANY_USER,
                user_id: int | None = None):
    """Your own tokens. ADMIN may pass `user_id` to inspect someone else's."""
    target = principal.user_id
    if user_id is not None and user_id != principal.user_id:
        if principal.role is not Role.ADMIN:
            raise HTTPException(403, "only an admin may list another user's tokens")
        target = user_id
    rows = db.scalars(select(ApiToken).where(ApiToken.user_id == target)
                      .order_by(ApiToken.id.desc())).all()
    return [_token_public(t) for t in rows]


@app.delete("/api/auth/tokens/{token_id}")
def revoke_token(token_id: int, db: Session = Depends(get_db),
                 principal: auth.Principal = auth.ANY_USER,
                 user_id: int | None = None):
    """Revoke a token: yours by id, someone else's only if you name the owner.

    Revocation is irreversible — only a sha256 is stored — and `GET /api/auth/tokens`
    lists only your own, so an ADMIN revoking by bare id is firing at a target they
    cannot see. That is how the live telephony credential was destroyed
    (DECISIONS.md). An ADMIN may still revoke another user's token, but has to say
    whose, the same way `list_tokens` already makes them.
    """
    token = db.get(ApiToken, token_id)
    # 404 rather than 403 for someone else's token — do not confirm it exists.
    if token is None:
        raise HTTPException(404, "token not found")
    if token.user_id != principal.user_id and not (
            principal.role is Role.ADMIN and user_id == token.user_id):
        raise HTTPException(404, "token not found")
    if token.revoked_at is None:
        token.revoked_at = datetime.now(UTC)
        db.commit()
    return {"ok": True, "id": token_id}


@app.post("/api/users", status_code=201)
def create_user(body: UserCreate, db: Session = Depends(get_db),
                _: auth.Principal = auth.ADMIN):
    email = body.email.strip().lower()
    if db.scalar(select(User).where(func.lower(User.email) == email)):
        raise HTTPException(400, "a user with that email already exists")
    if len(body.password) < 8:
        raise HTTPException(400, "password must be at least 8 characters")
    u = User(email=email, name=body.name, role=Role[body.role],
             password_hash=auth.hash_password(body.password))
    db.add(u)
    db.commit()
    db.refresh(u)
    return _user_public(u)


@app.patch("/api/users/{user_id}")
def update_user(user_id: int, body: UserPatch, db: Session = Depends(get_db),
                _: auth.Principal = auth.ADMIN):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "user not found")
    data = body.model_dump(exclude_unset=True)

    password = data.pop("password", None)
    if password is not None:
        if len(password) < 8:
            raise HTTPException(400, "password must be at least 8 characters")
        u.password_hash = auth.hash_password(password)
    if "role" in data:
        data["role"] = Role[data["role"]]
    for k, v in data.items():
        setattr(u, k, v)

    # A demotion or deactivation must take effect now, not in up to 15 minutes.
    if password is not None or "role" in data or "is_active" in data:
        u.token_version += 1
    db.commit()
    db.refresh(u)
    return _user_public(u)


@app.get("/api/health")
def health():
    """Unauthenticated (auth.EXEMPT), so it stays deliberately dull.

    `transport` and `crm_link` answer "is this thing armed?" — the question worth
    being able to ask from outside after a deploy, given that the difference
    between LoggingTransport and CrmLinkTransport is the difference between a
    recorded intent and a real text message. Neither field names a URL or a key.
    """
    return {"ok": True, "transport": type(get_transport()).__name__,
            "crm_link": crmlink.configured()}

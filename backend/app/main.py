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
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, object_session, selectinload

from . import (
    ahs_jobs,
    assigned_access,
    attachments,
    auth,
    automations,
    companycam,
    companycam_api,
    connection_status,
    crmlink,
    custom_fields,
    dlr,
    media_api,
    message_media,
    models,
    number_threads,
    openphone,
    opportunity_workspace,
    phone_match,
    pipeline_access,
    softphone,
)
from .ai import api as ai_api
from .ai import engine as ai_engine
from .ai import triggers as ai_triggers
from .db import DATABASE_URL, Base, engine, get_db
from .models import (
    ACTIVITY_TYPES,
    CONVERSATION_TYPES,
    ApiToken,
    Appointment,
    BlockedTime,
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
    NumberThread,
    NumberThreadEvent,
    Opportunity,
    Pipeline,
    PipelinePermission,
    Role,
    SavedView,
    Stage,
    Tag,
    User,
)
from .phones import format_phone, phone_warning, store_phone
from .transport import get_transport
from .zuper import api as zuper_api

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
# The OpenPhone mirror's one CRM-side route: it streams a mirrored call's audio
# through owen-main so the OpenPhone key never reaches the browser. Ingest needs
# no code here -- owen-main posts mirrored events to /api/events like any other
# telephony feed. See app/openphone.py.
app.include_router(openphone.router)
# The top-bar status dot (2026-09-14): one GET that asks owen-main how the link and
# the Quo sync are, server-side, cached 30s. See app/connection_status.py.
app.include_router(connection_status.router)
# The opportunity modal's tasks, notes and custom-field tabs (2026-09-13). One
# router, under the same app-level gate — see app/opportunity_workspace.py.
app.include_router(opportunity_workspace.router)
# CompanyCam job photos (2026-09-14): the Photos tab, the contact panel's projects, the
# image relay and the admin page. See app/companycam.py and app/companycam_api.py.
app.include_router(companycam_api.router)
# AI Agents, phase 1 (2026-09-15): agents, knowledge, logs, suggestions, connections and the
# alert bell, under /api/ai. STAFF-and-unrestricted to open, ADMIN to change. See app/ai/.
app.include_router(ai_api.router)
# Pictures on a text message (2026-09-16): the bytes a signed-in browser reads, and the
# upload/remove/retry the composer and the thread use. See app/media_api.py.
app.include_router(media_api.router)
# Zuper two-way sync (2026-09-16): Settings → Zuper, the money panels, Zuper's job photos and
# the webhook (the one route here on auth.EXEMPT — it has its own token). See app/zuper/.
app.include_router(zuper_api.router)

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


def _opportunity_text_match(term: str):
    """What the board search and the ctrl+K palette match a card on: its title, or
    its job's street or city (2026-09-14). One clause, so the two cannot drift. It
    narrows a query; it never widens one past `pipeline_access`."""
    like = contains(term)
    return or_(Opportunity.title.ilike(like, escape=LIKE_ESCAPE),
               Opportunity.address_street.ilike(like, escape=LIKE_ESCAPE),
               Opportunity.address_city.ilike(like, escape=LIKE_ESCAPE))


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


# The predicate itself lives in auth.py since 2026-09-13, so the opportunity
# notes in app/opportunity_workspace.py answer to the very same function.
_sees_internal = auth.sees_internal


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
    # WHICH phone system and WHICH line carried this event. A thread can hold both
    # BulkVS and mirrored OpenPhone events, and an operator who cannot tell them
    # apart cannot tell which number the customer knows them by — which is the
    # whole question when deciding whether to reply here or call back.
    #
    # Null renders NO chip rather than a default. Every row written before the
    # mirror existed has no observed source, and labelling them "BulkVS" would be
    # inventing a fact to make the UI tidier.
    source_system: str | None = None
    source_number: str | None = None
    # What was said on a call, when the far side transcribed it (Quo does).
    transcript: str | None = None
    # "AI: <agent name>" when an AI agent wrote this event (2026-09-15), else null.
    ai_author: str | None = None
    # The pictures on this message (2026-09-16), in the order the carrier sent them.
    # ALWAYS a list, empty for the overwhelming majority of rows — a null would make every
    # caller write the same `?? []`, and the thread renders nothing for an empty one.
    # Each entry carries an id the browser can ask THIS server for and nothing else: no
    # carrier URL, no owen-main locator, no path on disk. See app/message_media.py.
    attachments: list[dict] = []


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
    principal: auth.Principal = auth.ANY_USER):
    # "Only assigned data": the contacts of the reader's jobs, and no others — in the
    # rows and in `total` alike.
    stmt = assigned_access.contacts(select(Contact),
                                    assigned_access.scope(db, principal))
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


def _contact_detail(c: Contact, s: assigned_access.Scope) -> dict:
    """Shape mirrors the measured Contact Details panel.

    `s` is REQUIRED, with no default, on purpose: this payload lists the contact's
    opportunities and appointments, and a caller that forgot to pass what the reader
    may see would leak deal titles from pipelines they cannot access
    (pipeline_access.py) or from jobs that are not theirs (assigned_access.py).
    """
    theirs = {a.id for a in c.appointments}
    if s.restricted:
        # Only the visits on the reader's own calendar, as the Calendars page shows.
        theirs = set(object_session(c).scalars(select(Appointment.id).where(
            Appointment.contact_id == c.id,
            assigned_access.appointment_is_theirs(s.user_id))).all())
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
        "opportunities": [{"id": o.id, "title": o.title} for o in c.opportunities
                          if s.sees_job(o)],
        # Same reason, added 2026-09-11: the delete refuses over appointments too,
        # and the panel has to say which bookings are about to lose their customer
        # before anyone confirms.
        "appointments": [{"id": a.id, "title": a.title, "starts_at": _aware(a.starts_at),
                          "status": a.status, "location": a.location}
                         for a in c.appointments if a.id in theirs],
        "custom_fields": c.custom_fields or {},
    }


@app.get("/api/contacts/{contact_id}")
def get_contact(contact_id: int, db: Session = Depends(get_db),
                principal: auth.Principal = auth.ANY_USER):
    c = assigned_access.get_contact(db, principal, contact_id)
    return _contact_detail(c, assigned_access.scope(db, principal))


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
                   principal: auth.Principal = auth.STAFF):
    c = assigned_access.get_contact(db, principal, contact_id)
    if body.owner_id is not None and not db.get(User, body.owner_id):
        raise HTTPException(400, "unknown owner_id")
    # exclude_unset so an omitted field is left alone rather than nulled.
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(c, k, v)
    db.commit()
    db.refresh(c)
    # A phone edited to a number that has a number-only thread adopts it, in the
    # commit above (number_threads.adopt_on_flush); this only reports it.
    return {**_contact_detail(c, assigned_access.scope(db, principal)),
            **_adopted(db, c)}


class TagBody(BaseModel):
    name: str


@app.post("/api/contacts/{contact_id}/tags", status_code=201)
def add_tag(contact_id: int, body: TagBody, db: Session = Depends(get_db),
            principal: auth.Principal = auth.STAFF):
    c = assigned_access.get_contact(db, principal, contact_id)
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
    return _contact_detail(c, assigned_access.scope(db, principal))


@app.delete("/api/contacts/{contact_id}/tags/{tag_id}")
def remove_tag(contact_id: int, tag_id: int, db: Session = Depends(get_db),
               principal: auth.Principal = auth.STAFF):
    c = assigned_access.get_contact(db, principal, contact_id)
    link = db.scalar(select(ContactTag).where(
        ContactTag.contact_id == contact_id, ContactTag.tag_id == tag_id))
    if link:
        db.delete(link)
        db.commit()
        db.refresh(c)
    return _contact_detail(c, assigned_access.scope(db, principal))


# ---------- opportunities ----------

def _pipeline_public(db: Session, p: Pipeline, s: assigned_access.Scope) -> dict:
    """One pipeline as every screen reads it: the board, the Pipelines list, the
    modal, the Dashboard selectors and the `ghl` CLI.

    `count`/`value_cents` per stage are every deal in the stage, every status —
    the figures the Dashboard funnel and the Forecast have always quoted. For a
    reader with "Only assigned data" on they are THEIR jobs in the stage, so a
    column header cannot say how much money sits in other people's cards.
    """
    totals = {
        stage_id: (n, int(v)) for stage_id, n, v in db.execute(
            assigned_access.opportunities(
                select(Opportunity.stage_id, func.count(Opportunity.id),
                       func.coalesce(func.sum(Opportunity.value_cents), 0))
                .where(Opportunity.pipeline_id == p.id), s)
            .group_by(Opportunity.stage_id)).all()}
    stages = []
    for s in sorted(p.stages, key=lambda s: (s.position, s.id)):
        n, v = totals.get(s.id, (0, 0))
        stages.append({"id": s.id, "name": s.name, "position": s.position,
                       "count": n, "value_cents": v,
                       "color": s.color, "probability": s.probability,
                       "show_in_funnel": s.show_in_funnel,
                       "show_in_pie": s.show_in_pie})
    return {"id": p.id, "name": p.name, "position": p.position,
            "color_mode": p.color_mode,
            "use_opportunity_probability": p.use_opportunity_probability,
            "updated_at": p.updated_at,
            "stages": stages}


def _ordered_pipelines(db: Session) -> list[Pipeline]:
    """Every pipeline in display order. `id` breaks ties, because every pipeline
    written before positions were maintained may share position 0."""
    return list(db.scalars(
        select(Pipeline).options(selectinload(Pipeline.stages))
        .order_by(Pipeline.position, Pipeline.id)).all())


@app.get("/api/pipelines")
def list_pipelines(db: Session = Depends(get_db),
                   principal: auth.Principal = auth.ANY_USER):
    """In `position` order, which the Pipelines tab's drag sets and the board's
    selector follows. A pipeline the caller may not access is simply absent."""
    s = assigned_access.scope(db, principal)
    return [_pipeline_public(db, p, s) for p in _ordered_pipelines(db)
            if p.id not in s.hidden]


# ---------- pipeline structure ----------
#
# Editable since 2026-09-10; rebuilt on 2026-09-13 as GoHighLevel's Pipelines tab,
# its Create/Edit modal and its row menu (DECISIONS.md, both dated notes).
#
# ROLES. DISPATCHER may create, edit, duplicate and reorder. ADMIN alone may
# delete a stage or a pipeline and manage who can access one. TECH changes
# nothing here. A pipeline a user cannot access answers 404 on every route below
# (pipeline_access.py) — never 403, which would confirm it exists.
#
# DELETING, by the owner's override of 2026-09-13. A stage or pipeline that holds
# deals CAN now be deleted, GoHighLevel's way — BUT NO DEAL IS EVER DELETED:
#
#   * the caller names where the deals go (a stage of the same pipeline for a
#     stage; a stage of another pipeline for a pipeline) and they are MOVED there
#     first. Without a destination the delete is refused 409 and names the count.
#   * a structural move writes `pipeline_id`, `stage_id` and the in-column rank
#     and NOTHING else: `custom_fields` — including `owen_call_id`, the telephony
#     project's live join key — and `updated_at` are left byte-identical.
#   * a structural move FIRES NO AUTOMATION. The drag path texts the customer
#     through rule 4; emptying a 255-deal stage must not queue 255 of those.
#   * it is ONE transaction. Nothing is committed until the end, so a failure
#     part-way moves nothing and deletes nothing.
#
# Reordering still moves columns, never deals. Stage names are still NOT unique
# (the measured pipeline has two "Call Back" stages and the CLI exits 5 between
# them); PIPELINE names are unique, case-insensitively, which is what the modal's
# helper text promises.

PIPELINE_NAME_MAX = 160
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")

# The palette a new stage is painted from, by position. Distinct at a glance,
# readable as a dot and as a 12%-alpha tint, and in the Untitled UI family the
# rest of the app's colours come from.
STAGE_PALETTE = ("#2E90FA", "#12B76A", "#F79009", "#7A5AF8", "#F04438",
                 "#06AED4", "#EE46BC", "#667085", "#EAAA08", "#15B79E")


def default_stage_color(position: int) -> str:
    return STAGE_PALETTE[position % len(STAGE_PALETTE)]


def _check_color(v: str | None) -> str | None:
    if v is not None and not HEX_COLOR.match(v):
        raise ValueError("a stage colour is #RRGGBB")
    return v.upper() if v else v


class StageFields(BaseModel):
    """The per-stage settings the modal edits. All optional on a PATCH."""
    color: str | None = None
    probability: int | None = Field(None, ge=0, le=100)
    show_in_funnel: bool | None = None
    show_in_pie: bool | None = None

    _color = field_validator("color", mode="after")(_check_color)


class StageBody(StageFields):
    name: str = Field(max_length=PIPELINE_NAME_MAX)


class StagePatch(StageFields):
    """No `pipeline_id`, on purpose: moving a stage to another pipeline would carry
    every deal in it across, which a deal PATCH refuses as a cross-pipeline move."""
    name: str | None = Field(None, max_length=PIPELINE_NAME_MAX)


class StageIn(StageFields):
    """One row of the modal's stage list. `id` names an existing stage; without
    one it is a new stage."""
    id: int | None = None
    name: str = Field(max_length=PIPELINE_NAME_MAX)


class PipelineBody(BaseModel):
    name: str = Field(max_length=PIPELINE_NAME_MAX)
    use_opportunity_probability: bool = False
    color_mode: Literal["none", "dot", "tint"] = "none"
    # Optional so `ghl`/curl can still create an empty pipeline by name alone.
    stages: list[StageIn] = Field(default_factory=list)


class PipelinePatch(BaseModel):
    name: str | None = Field(None, max_length=PIPELINE_NAME_MAX)
    use_opportunity_probability: bool | None = None
    color_mode: Literal["none", "dot", "tint"] | None = None
    # The WHOLE stage list in its new order, when present. An existing stage left
    # out is deleted — ADMIN only — and if it holds deals `stage_moves` must name
    # the stage (kept, in this pipeline) that receives them.
    stages: list[StageIn] | None = None
    stage_moves: dict[int, int] = Field(default_factory=dict)


class StageReorder(BaseModel):
    """The pipeline's stages in their new order — every one of them, exactly once."""
    stage_ids: list[int] = Field(min_length=1)


class PipelineReorder(BaseModel):
    """Every pipeline the caller can see, exactly once, in the new order."""
    pipeline_ids: list[int] = Field(min_length=1)


class PipelinePermissions(BaseModel):
    """Nobody listed = everyone may access the pipeline."""
    user_ids: list[int] = Field(default_factory=list)


def _clean_structure_name(name: str | None, kind: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise HTTPException(400, "a %s needs a name" % kind)
    return cleaned


def _unique_pipeline_name(db: Session, name: str, exclude_id: int | None = None) -> str:
    """Pipeline names are unique, case-insensitively — the modal says "use a
    unique, descriptive name". Checked across EVERY pipeline, including ones the
    caller cannot see: two boards called "Retail" is the ambiguity this prevents."""
    cleaned = _clean_structure_name(name, "pipeline")
    clash = select(Pipeline.id).where(func.lower(Pipeline.name) == cleaned.lower())
    if exclude_id is not None:
        clash = clash.where(Pipeline.id != exclude_id)
    if db.scalar(clash) is not None:
        raise HTTPException(409, "a pipeline called %r already exists — pipeline "
                                 "names must be unique" % cleaned)
    return cleaned


def _opportunity_count(db: Session, **where) -> int:
    stmt = select(func.count(Opportunity.id))
    for column, value in where.items():
        stmt = stmt.where(getattr(Opportunity, column) == value)
    return db.scalar(stmt) or 0


def _touch(p: Pipeline) -> None:
    p.updated_at = datetime.now(UTC)


def _apply_stage_fields(s: Stage, fields: dict) -> None:
    for key in ("color", "probability", "show_in_funnel", "show_in_pie"):
        if key in fields and (fields[key] is not None or key in ("color", "probability")):
            setattr(s, key, fields[key])


def _new_stage(pipeline_id: int, position: int, body: StageFields, name: str) -> Stage:
    data = body.model_dump(exclude_unset=True)
    s = Stage(pipeline_id=pipeline_id, name=_clean_structure_name(name, "stage"),
              position=position, show_in_funnel=True, show_in_pie=True,
              color=default_stage_color(position))
    _apply_stage_fields(s, data)
    if s.color is None:
        s.color = default_stage_color(position)
    return s


def _structural_move(db: Session, where, destination: Stage) -> list[int]:
    """Move every deal matching `where` into `destination`. NO automation.

    Deliberately NOT `move_opportunity` in a loop, and deliberately a Core UPDATE
    rather than ORM attribute writes:

    * `move_opportunity` calls `automations.on_opportunity_stage_changed`, which
      queues a customer text per deal. A column being deleted is not news to the
      customer.
    * the UPDATE names exactly the columns that change. `updated_at` is set to
      itself, which suppresses the column's `onupdate`, so the deal's own
      last-changed time — and `custom_fields`, which is never mentioned — come out
      byte-identical.

    Arrivals keep their relative order and land after whatever the destination
    already holds, so no two cards in the column share a rank.
    """
    moving = db.execute(
        select(Opportunity.id).join(Stage, Stage.id == Opportunity.stage_id,
                                    isouter=True)
        .where(where)
        .order_by(Stage.position, Opportunity.position, Opportunity.id)).scalars().all()
    if not moving:
        return []
    start = db.scalar(select(func.max(Opportunity.position))
                      .where(Opportunity.stage_id == destination.id,
                             Opportunity.id.not_in(moving)))
    start = 0 if start is None else start + 1
    for rank, opp_id in enumerate(moving):
        db.execute(
            update(Opportunity).where(Opportunity.id == opp_id)
            .values(pipeline_id=destination.pipeline_id, stage_id=destination.id,
                    position=start + rank, updated_at=Opportunity.updated_at)
            .execution_options(synchronize_session=False))
    return list(moving)


def _repack_stages(db: Session, pipeline_id: int) -> None:
    db.flush()
    for i, remaining in enumerate(db.scalars(
            select(Stage).where(Stage.pipeline_id == pipeline_id)
            .order_by(Stage.position, Stage.id)).all()):
        remaining.position = i


def _delete_stage_moving_deals(db: Session, principal: auth.Principal, s: Stage,
                               move_to_stage_id: int | None) -> list[int]:
    """Delete one stage, moving its deals first. Does NOT commit."""
    held = _opportunity_count(db, stage_id=s.id)
    if held and move_to_stage_id is None:
        raise HTTPException(409, (
            "%r still holds %d opportunit%s. Choose a stage of this pipeline to "
            "move %s to — deleting a stage never deletes the deals in it." % (
                s.name, held, "y" if held == 1 else "ies",
                "it" if held == 1 else "them")))
    moved: list[int] = []
    if held:
        dest = db.get(Stage, move_to_stage_id)
        if (dest is None or dest.pipeline_id != s.pipeline_id or dest.id == s.id):
            raise HTTPException(400, "the deals must move to another stage of the "
                                     "same pipeline")
        moved = _structural_move(db, Opportunity.stage_id == s.id, dest)
    db.delete(s)
    return moved


@app.post("/api/pipelines", status_code=201)
def create_pipeline(body: PipelineBody, db: Session = Depends(get_db),
                    principal: auth.Principal = auth.STAFF):
    """Create a pipeline with every setting the modal shows, in one request.

    Without `stages` a pipeline still starts EMPTY — the API does not guess at a
    roofing pipeline's shape. The browser's modal is what pre-fills GoHighLevel's
    four starter rows, visibly and editably, before anything is sent.
    """
    name = _unique_pipeline_name(db, body.name)
    if any(st.id is not None for st in body.stages):
        raise HTTPException(400, "a new pipeline's stages cannot carry ids")
    last = db.scalar(select(func.max(Pipeline.position)))
    p = Pipeline(name=name, position=0 if last is None else last + 1,
                 color_mode=body.color_mode,
                 use_opportunity_probability=body.use_opportunity_probability)
    _touch(p)
    db.add(p)
    db.flush()
    for i, st in enumerate(body.stages):
        db.add(_new_stage(p.id, i, st, st.name))
    db.commit()
    db.refresh(p)
    return _pipeline_public(db, p, assigned_access.scope(db, principal))


@app.patch("/api/pipelines/{pipeline_id}")
def update_pipeline(pipeline_id: int, body: PipelinePatch,
                    db: Session = Depends(get_db),
                    principal: auth.Principal = auth.STAFF):
    """Edit a pipeline: the modal's Update. Every change lands, or none does.

    Everything is validated before anything is written, and nothing is committed
    until the end, so a stage list that fails half-way leaves the pipeline as it
    was — including the deals of any stage it was about to delete.
    """
    p = pipeline_access.get_pipeline(db, principal, pipeline_id)
    data = body.model_dump(exclude_unset=True)

    if "name" in data:
        p.name = _unique_pipeline_name(db, data["name"], exclude_id=p.id)
    if data.get("color_mode") is not None:
        p.color_mode = data["color_mode"]
    if data.get("use_opportunity_probability") is not None:
        p.use_opportunity_probability = data["use_opportunity_probability"]

    moved: dict[str, list[int]] = {}
    if body.stages is not None:
        current = {s.id: s for s in p.stages}
        named = [st.id for st in body.stages if st.id is not None]
        if len(set(named)) != len(named):
            raise HTTPException(400, "a stage appears twice in the new list")
        foreign = [i for i in named if i not in current]
        if foreign:
            raise HTTPException(400, "stage %s is not in this pipeline" % foreign[0])
        if not body.stages:
            raise HTTPException(400, "a pipeline keeps at least one stage — delete "
                                     "the pipeline instead")
        removed = [s for sid, s in current.items() if sid not in set(named)]
        if removed and principal.role is not Role.ADMIN:
            raise HTTPException(403, "only an admin can delete a stage")
        for s in removed:
            dest = body.stage_moves.get(s.id)
            if dest is not None and dest not in set(named):
                raise HTTPException(400, "the deals of %r must move to a stage "
                                         "this pipeline keeps" % s.name)
        # Validate every name before writing anything.
        for st in body.stages:
            _clean_structure_name(st.name, "stage")

        for s in removed:
            moved[str(s.id)] = _delete_stage_moving_deals(
                db, principal, s, body.stage_moves.get(s.id))
        db.flush()
        for position, st in enumerate(body.stages):
            if st.id is None:
                db.add(_new_stage(p.id, position, st, st.name))
                continue
            s = current[st.id]
            s.name = _clean_structure_name(st.name, "stage")
            s.position = position
            _apply_stage_fields(s, st.model_dump(exclude_unset=True))

    _touch(p)
    db.commit()
    db.refresh(p)
    return {**_pipeline_public(db, p, assigned_access.scope(db, principal)),
            "moved": moved}


@app.post("/api/pipelines/reorder")
def reorder_pipelines(body: PipelineReorder, db: Session = Depends(get_db),
                      principal: auth.Principal = auth.STAFF):
    """The Pipelines table's drag and "Move to position". The board's selector
    follows this order.

    The body is every pipeline the CALLER CAN SEE, exactly once. Pipelines they
    cannot see keep the slots they hold, so a dispatcher can reorder their own
    list without being told — by a refusal naming ids — that others exist.
    """
    hidden = pipeline_access.hidden_pipeline_ids(db, principal)
    everything = _ordered_pipelines(db)
    visible = [p.id for p in everything if p.id not in hidden]
    wanted = body.pipeline_ids
    if len(set(wanted)) != len(wanted) or set(wanted) != set(visible):
        raise HTTPException(400, "the new order must name every pipeline exactly "
                                 "once (%d pipelines)" % len(visible))
    slots = [i for i, p in enumerate(everything) if p.id not in hidden]
    order = [p.id for p in everything]
    for slot, pid in zip(slots, wanted, strict=True):
        order[slot] = pid
    by_id = {p.id: p for p in everything}
    for i, pid in enumerate(order):
        by_id[pid].position = i
    db.commit()
    s = assigned_access.scope(db, principal)
    return [_pipeline_public(db, by_id[pid], s) for pid in order if pid not in hidden]


@app.post("/api/pipelines/{pipeline_id}/duplicate", status_code=201)
def duplicate_pipeline(pipeline_id: int, db: Session = Depends(get_db),
                       principal: auth.Principal = auth.STAFF):
    """"<name> (copy)": every setting and every stage, NEVER a deal.

    Copied: the name (suffixed), both settings, each stage's name, order,
    probability, report flags and colour, the custom-field attachments — so the
    job questions follow — and the access list. The access list is copied
    because leaving it out would turn a duplicate of a restricted pipeline into
    one everybody can open.
    """
    src = pipeline_access.get_pipeline(db, principal, pipeline_id)
    base = "%s (copy)" % src.name
    name, n = base, 1
    while db.scalar(select(Pipeline.id).where(func.lower(Pipeline.name) == name.lower())):
        n += 1
        name = "%s (copy %d)" % (src.name, n)
    name = name[:PIPELINE_NAME_MAX]
    last = db.scalar(select(func.max(Pipeline.position)))
    copy = Pipeline(name=name, position=0 if last is None else last + 1,
                    color_mode=src.color_mode,
                    use_opportunity_probability=src.use_opportunity_probability)
    _touch(copy)
    db.add(copy)
    db.flush()
    for s in sorted(src.stages, key=lambda s: (s.position, s.id)):
        db.add(Stage(pipeline_id=copy.id, name=s.name, position=s.position,
                     color=s.color, probability=s.probability,
                     show_in_funnel=s.show_in_funnel, show_in_pie=s.show_in_pie))
    for link in db.scalars(select(CustomFieldPipeline).where(
            CustomFieldPipeline.pipeline_id == src.id)).all():
        db.add(CustomFieldPipeline(field_id=link.field_id, pipeline_id=copy.id))
    for grant in db.scalars(select(PipelinePermission).where(
            PipelinePermission.pipeline_id == src.id)).all():
        db.add(PipelinePermission(pipeline_id=copy.id, user_id=grant.user_id))
    db.commit()
    db.refresh(copy)
    return _pipeline_public(db, copy, assigned_access.scope(db, principal))


@app.get("/api/pipelines/{pipeline_id}/permissions")
def get_pipeline_permissions(pipeline_id: int, db: Session = Depends(get_db),
                             principal: auth.Principal = auth.ADMIN):
    p = pipeline_access.get_pipeline(db, principal, pipeline_id)
    ids = sorted(db.scalars(select(PipelinePermission.user_id).where(
        PipelinePermission.pipeline_id == p.id)).all())
    return {"pipeline_id": p.id, "user_ids": ids, "everyone": not ids}


@app.put("/api/pipelines/{pipeline_id}/permissions")
def set_pipeline_permissions(pipeline_id: int, body: PipelinePermissions,
                             db: Session = Depends(get_db),
                             principal: auth.Principal = auth.ADMIN):
    """Replace the access list. ADMIN. An empty list opens the pipeline to all."""
    p = pipeline_access.get_pipeline(db, principal, pipeline_id)
    wanted = sorted(set(body.user_ids))
    for user_id in wanted:
        if db.get(User, user_id) is None:
            raise HTTPException(400, "unknown user_id %d" % user_id)
    for grant in db.scalars(select(PipelinePermission).where(
            PipelinePermission.pipeline_id == p.id)).all():
        db.delete(grant)
    db.flush()
    for user_id in wanted:
        db.add(PipelinePermission(pipeline_id=p.id, user_id=user_id))
    _touch(p)
    db.commit()
    return {"pipeline_id": p.id, "user_ids": wanted, "everyone": not wanted}


@app.delete("/api/pipelines/{pipeline_id}")
def delete_pipeline(pipeline_id: int, move_to_stage_id: int | None = None,
                    db: Session = Depends(get_db),
                    principal: auth.Principal = auth.ADMIN):
    """Delete a pipeline. Its deals MOVE to `move_to_stage_id`; none is deleted.

    A pipeline holding no deals deletes outright, stages and all — the browser
    asks first. One holding deals needs a destination stage in ANOTHER pipeline
    the caller can access, and is refused 409, naming the count, without one.

    What pointed at the pipeline:
      * saved views and calendars are DETACHED (`pipeline_id = NULL`), as they
        always were for an empty pipeline. A view with no pipeline re-filters
        whichever board is open, so the board does not break — and a view named
        "Open AHS" silently re-pointed at Retail would be a list that lies.
      * custom-field attachments are removed; the definitions and every recorded
        answer stay.
      * the access list goes with it.
    """
    p = pipeline_access.get_pipeline(db, principal, pipeline_id)
    stages = len(p.stages)
    opps = _opportunity_count(db, pipeline_id=p.id)
    if opps and move_to_stage_id is None:
        raise HTTPException(409, (
            "%r still has %d stage%s and %d opportunit%s. Choose a pipeline and "
            "stage to move them to — a pipeline is never deleted with its deals." % (
                p.name, stages, "" if stages == 1 else "s",
                opps, "y" if opps == 1 else "ies")))

    moved: list[int] = []
    if opps:
        dest = db.get(Stage, move_to_stage_id)
        if (dest is None or dest.pipeline_id == p.id
                or not pipeline_access.can_see(db, principal, dest.pipeline_id)):
            raise HTTPException(400, "the deals must move to a stage of another "
                                     "pipeline")
        moved = _structural_move(db, Opportunity.pipeline_id == p.id, dest)
        _touch(db.get(Pipeline, dest.pipeline_id))

    views = db.scalars(
        select(SavedView).where(SavedView.pipeline_id == p.id)).all()
    calendars = db.scalars(
        select(Calendar).where(Calendar.pipeline_id == p.id)).all()
    for v in views:
        v.pipeline_id = None
    for c in calendars:
        c.pipeline_id = None
    links = db.scalars(select(CustomFieldPipeline).where(
        CustomFieldPipeline.pipeline_id == p.id)).all()
    detached_fields = sorted({link.field_id for link in links})
    for link in links:
        db.delete(link)
    for grant in db.scalars(select(PipelinePermission).where(
            PipelinePermission.pipeline_id == p.id)).all():
        db.delete(grant)
    db.flush()
    _finish_pipeline_delete(db, p)
    db.commit()
    return {"deleted": pipeline_id,
            "moved_opportunities": moved,
            "detached_saved_views": [v.id for v in views],
            "detached_calendars": [c.id for c in calendars],
            "detached_custom_fields": detached_fields}


def _finish_pipeline_delete(db: Session, p: Pipeline) -> None:
    """The last step, and a guard: if a single deal is still filed here, stop.

    `Pipeline.stages` cascades delete-orphan, so reaching the delete with a deal
    still in one of its stages would orphan it. Raising here rolls back the whole
    request, moves included.
    """
    if _opportunity_count(db, pipeline_id=p.id):
        raise RuntimeError("refusing to delete pipeline %d: deals are still filed "
                           "in it" % p.id)
    db.delete(p)
    db.flush()


@app.post("/api/pipelines/{pipeline_id}/stages", status_code=201)
def create_stage(pipeline_id: int, body: StageBody, db: Session = Depends(get_db),
                 principal: auth.Principal = auth.STAFF):
    """Appended at the end. A new stage is empty, so nothing moves."""
    p = pipeline_access.get_pipeline(db, principal, pipeline_id)
    n = db.scalar(select(func.count(Stage.id))
                  .where(Stage.pipeline_id == p.id)) or 0
    s = _new_stage(p.id, n, body, body.name)
    db.add(s)
    _touch(p)
    db.commit()
    db.refresh(s)
    return {"id": s.id, "pipeline_id": s.pipeline_id, "name": s.name,
            "position": s.position, "count": 0, "value_cents": 0,
            "color": s.color, "probability": s.probability,
            "show_in_funnel": s.show_in_funnel, "show_in_pie": s.show_in_pie}


@app.patch("/api/stages/{stage_id}")
def update_stage(stage_id: int, body: StagePatch, db: Session = Depends(get_db),
                 principal: auth.Principal = auth.STAFF):
    """Rename a stage or change its settings. Never its pipeline.

    Names stay non-unique — two "Call Back" stages are the measured shape, and the
    CLI's exit 5 depends on it.
    """
    s = pipeline_access.get_stage(db, principal, stage_id)
    data = body.model_dump(exclude_unset=True)
    if "name" in data:
        s.name = _clean_structure_name(data["name"], "stage")
    _apply_stage_fields(s, data)
    _touch(s.pipeline)
    db.commit()
    return {"id": s.id, "pipeline_id": s.pipeline_id, "name": s.name,
            "position": s.position, "color": s.color,
            "probability": s.probability, "show_in_funnel": s.show_in_funnel,
            "show_in_pie": s.show_in_pie}


@app.delete("/api/stages/{stage_id}")
def delete_stage(stage_id: int, move_to_stage_id: int | None = None,
                 db: Session = Depends(get_db),
                 principal: auth.Principal = auth.ADMIN):
    """Delete a stage. Its deals MOVE to `move_to_stage_id` first; none is deleted.

    An empty stage deletes outright (the browser confirms first). A populated one
    without a destination is refused 409 with the count. The destination must be
    another stage of the SAME pipeline. The rest are re-packed 0..n-1.
    """
    s = pipeline_access.get_stage(db, principal, stage_id)
    pipeline_id = s.pipeline_id
    moved = _delete_stage_moving_deals(db, principal, s, move_to_stage_id)
    _repack_stages(db, pipeline_id)
    _touch(db.get(Pipeline, pipeline_id))
    db.commit()
    return {"deleted": stage_id, "pipeline_id": pipeline_id,
            "moved_opportunities": moved}


@app.post("/api/pipelines/{pipeline_id}/stages/reorder")
def reorder_stages(pipeline_id: int, body: StageReorder,
                   db: Session = Depends(get_db),
                   principal: auth.Principal = auth.STAFF):
    """Re-order the columns. NO OPPORTUNITY MOVES.

    Only `Stage.position` is written; nothing touches `Opportunity.stage_id`. The
    request must name every stage of this pipeline exactly once, so a board that
    changed underneath the user is refused outright rather than half-applied — and
    a stage from another pipeline cannot be smuggled in.
    """
    p = pipeline_access.get_pipeline(db, principal, pipeline_id)

    current = {s.id: s for s in p.stages}
    wanted = body.stage_ids
    if len(set(wanted)) != len(wanted) or set(wanted) != set(current):
        raise HTTPException(400, (
            "the new order must name every stage of this pipeline exactly once "
            "(%d stages: %s)" % (len(current),
                                 ", ".join(str(i) for i in sorted(current)))))

    for i, stage_id in enumerate(wanted):
        current[stage_id].position = i
    _touch(p)
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
    # The modal tab it is drawn under; None = Opportunity details.
    group_id: int | None = None
    # The Checklist's settings (2026-09-14), all optional: what to say, what real
    # value a yes/no shows beside its tick, which dropdown choices open a details box.
    script: str | None = None
    linked_field: str | None = None
    details_when: list[str] | None = None


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
    # Moving a field between tabs. Send null to put it back under Opportunity
    # details. Moves no answer: the key, and so every value, stays where it was.
    group_id: int | None = None
    # Settings, not answers: changing or clearing one (null / "" / []) touches no
    # recorded answer, a details answer included.
    script: str | None = None
    linked_field: str | None = None
    details_when: list[str] | None = None


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
                       principal: auth.Principal = auth.ANY_USER):
    """Every definition, in display order.

    Archived ones are included by default because the opportunity form needs them:
    a deal holding an answer to an archived field still has to be able to LABEL it,
    and a list that hid them would render "roof_age: 14" as a bare key.

    A pipeline the caller cannot access is left out of every `pipeline_ids`, so the
    list does not name it. Only an ADMIN can write a definition, and an ADMIN sees
    every pipeline, so nothing here can save a narrowed list back over a full one.
    """
    hidden = pipeline_access.hidden_pipeline_ids(db, principal)
    out = [custom_fields.describe(d)
           for d in custom_fields.load_defs(db, include_archived=include_archived)]
    if hidden:
        out = [{**d, "pipeline_ids": [i for i in d["pipeline_ids"] if i not in hidden]}
               for d in out]
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
    group_id = opportunity_workspace.check_group(db, body.group_id)

    script = custom_fields.clean_script(body.script)
    linked = custom_fields.clean_linked(field_type, body.linked_field)
    details_when = custom_fields.clean_details_when(field_type, options, body.details_when)

    n = db.scalar(select(func.count(CustomFieldDef.id))) or 0
    d = CustomFieldDef(key=key, label=label, field_type=field_type,
                       options=options, position=n, entity="opportunity",
                       group_id=group_id, script=script, linked_field=linked,
                       details_when=details_when or None)
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
        if "details_when" not in data and d.details_when:
            # A retired choice can no longer be chosen, so it can no longer open the
            # box. Pruned rather than refused: the answers already given are
            # untouched either way, and this is configuration, not an answer.
            d.details_when = [o for o in d.details_when if o in d.options] or None
    if "script" in data:
        d.script = custom_fields.clean_script(data["script"])
    if "linked_field" in data:
        d.linked_field = custom_fields.clean_linked(d.field_type, data["linked_field"])
    if "details_when" in data:
        d.details_when = custom_fields.clean_details_when(
            d.field_type, list(d.options or []), data["details_when"]) or None
    if "pipeline_ids" in data:
        custom_fields.attach(db, d, _check_pipelines(db, data["pipeline_ids"]))
    if "group_id" in data:
        d.group_id = opportunity_workspace.check_group(db, data["group_id"])
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
    principal: auth.Principal = auth.ANY_USER):
    """`status` mirrors GHL's measured default advanced filter: Status is any of Open.

    `pipeline_id` was required and is now optional. The board always sends one;
    omitting it lists across every pipeline, which is what the appointment dialog
    needs to offer "bind this booking to an open deal" without knowing, or caring,
    which board the deal is filed on.

    Deals in a pipeline the caller cannot access are never listed. Naming such a
    pipeline returns `[]`, exactly what naming one that does not exist returns. With
    "Only assigned data" on, only the caller's own jobs are listed (assigned_access).
    """
    stmt = assigned_access.opportunities(
        select(Opportunity).options(selectinload(Opportunity.contact),
                                    selectinload(Opportunity.owner)),
        assigned_access.scope(db, principal))
    if pipeline_id is not None:
        stmt = stmt.where(Opportunity.pipeline_id == pipeline_id)
    if status != "all":
        stmt = stmt.where(Opportunity.status == status)
    if q:
        # The board's search box: the title, or the job's street or city
        # (2026-09-14) — "which card is the one on Palm Ave" is a real question.
        stmt = stmt.where(_opportunity_text_match(q))
    # `id` is the tiebreak, not decoration: `position` defaults to 0, so any rows
    # written before a re-pack can share one, and without it the board's order
    # changes between two identical refetches — which the optimistic drag would
    # then read as the server disagreeing with it.
    rows = db.scalars(stmt.order_by(Opportunity.position, Opportunity.id)).all()
    # The board card's icon row (2026-09-13): tag names, open tasks and — for
    # STAFF only — the note count and first lines. A TECH's rows carry no note
    # key at all; see opportunity_workspace.card_extras.
    extras = opportunity_workspace.card_extras(db, list(rows), principal)
    return [{"id": o.id, "title": o.title, "value_cents": o.value_cents,
             "stage_id": o.stage_id, "pipeline_id": o.pipeline_id,
             "status": o.status, "position": o.position,
             "contact_id": o.contact_id,
             "contact_name": o.contact.name if o.contact else None,
             "business_name": o.contact.business_name if o.contact else None,
             "source": o.contact.source if o.contact else None,
             "probability": o.probability,
             **_opp_address(o),
             "owner_id": o.owner_id,
             "owner_name": o.owner.name if o.owner else None,
             "updated_at": o.updated_at, **extras[o.id]} for o in rows]


class OpportunityMove(BaseModel):
    # A negative position is not "the end", it is Python's negative slicing: -1 would
    # quietly file the card second-from-bottom. The board never sends one, so refuse
    # it rather than reorder the column in a way nobody asked for.
    stage_id: int
    position: int = Field(0, ge=0)


@app.patch("/api/opportunities/{opp_id}")
def move_opportunity(opp_id: int, body: OpportunityMove,
                     db: Session = Depends(get_db),
                     principal: auth.Principal = auth.ANY_USER):
    """Drag a card between stages. Mutates OUR database only."""
    o = pipeline_access.get_opportunity(db, principal, opp_id)
    result = move_to_stage(db, principal, o, body.stage_id, body.position)
    db.commit()
    return result


def move_to_stage(db: Session, principal: auth.Principal, o: Opportunity, stage_id: int,
                  position: int) -> dict:
    """The drag, as a service: the board's route and an AI agent's "move stage" action
    run exactly this (2026-09-15). The caller resolved `o` through pipeline_access and
    commits. Fires rule 4 like any stage move."""
    body = OpportunityMove(stage_id=stage_id, position=position)
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
    at = body.position
    s = assigned_access.scope(db, principal)
    if s.restricted:
        # The caller's board shows only their own cards, so the position they dropped
        # at counts THEIR cards. Land just before the card they dropped above (or
        # after the last one they can see), so other people's cards keep their order.
        mine = [i for i, sib in enumerate(siblings) if s.sees_job(sib)]
        at = (mine[body.position] if body.position < len(mine)
              else (mine[-1] + 1 if mine else len(siblings)))
    ordered = [*siblings[:at], o, *siblings[at:]]
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
                   principal: auth.Principal = auth.STAFF):
    if not (body.phone or body.email):
        raise HTTPException(400, "a contact needs at least a phone or an email")
    c = Contact(**body.model_dump(), created_by="Manual")
    db.add(c)
    db.flush()
    # Rule 2: new lead -> notify the team.
    outcome = automations.on_contact_created(db, c)
    db.commit()
    db.refresh(c)
    return {**_contact_detail(c, assigned_access.scope(db, principal)),
            "automation": outcome, **_adopted(db, c)}


def _adopted(db: Session, contact: Contact) -> dict:
    """What the flush that just saved `contact` adopted, if anything.

    The adoption itself happens in `number_threads.adopt_on_flush`, whatever route
    wrote the contact; this only reports it, so "Add as contact" can open the
    contact's thread — which now holds the number's whole history — instead of the
    number thread that no longer exists.
    """
    found = [r for r in db.info.pop("adopted_number_threads", [])
             if r.get("contact") is contact]
    if not found:
        return {"adopted_number_thread": None}
    conv = db.scalar(select(Conversation).where(Conversation.contact_id == contact.id))
    r = found[0]
    return {"adopted_number_thread": {
        "number_thread_id": r["number_thread_id"], "events_moved": r["moved"],
        "duplicates_skipped": r["skipped_duplicates"],
        "conversation_id": conv.id if conv else None}}


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

    # --- mirrored feeds (2026-09-11) ----------------------------------------
    # All optional, all defaulting to None, so a body that omits them is exactly
    # the body owen-main's BulkVS path has always sent.
    #
    # WHEN IT HAPPENED, as opposed to when we heard about it. Until the OpenPhone
    # mirror every ingested event WAS "just now" and this was safe to leave to the
    # column default. A 30-day backfill is the first feed that can legitimately
    # report the past, and without this it would land as a month of history all
    # dated today, in poll order — the opposite of the one-timeline-per-customer
    # the mirror exists to produce. Back-dating also feeds `automations.is_fresh`,
    # which is what stops rule 1 texting a month of past callers.
    occurred_at: datetime | None = None
    # OPT-IN idempotency. Unique; a second ingest carrying a key we already hold
    # returns the row that exists instead of writing another. Deliberately NOT
    # `provider_ref`, which the call path sends identically on all three lifecycle
    # phases and so cannot be unique. See the model and the migration.
    dedupe_key: str | None = None
    # Which system and which line carried this event, for a thread that now holds
    # two phone systems at once.
    source_system: str | None = None
    source_number: str | None = None
    # OpenPhone has already transcribed its calls, so carrying the text costs a
    # field and no STT spend. The column has existed since the baseline.
    transcript: str | None = None
    # The name Quo's own contact book gives the customer's number, when it has one
    # (2026-09-13). Shown on a number-only thread labelled "from Quo". It creates
    # nothing and never renames an existing contact.
    source_contact_name: str | None = Field(default=None, max_length=200)
    # A call's summary, as Quo wrote it. On a FIRST delivery it is already inside
    # `body`; it is carried separately so a summary Quo finishes after the call was
    # mirrored can be added to that one row (see `_enrich`).
    summary: str | None = None
    # How many pictures came with a text (2026-09-16). owen-main has stored an inbound
    # MMS's media since before this CRM could show one; this is the field that says there
    # are some, and `app/message_media.py` then asks owen-main for each by index. NO media
    # URL is ever carried here: a carrier link is a credential-free URL to a customer's
    # photograph, and the only system that should ever hold one is the one that already
    # holds the carrier's key.
    num_media: int = 0

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


def _resolve_ingest_contact(db: Session, body: EventIngest) -> Contact | None:
    """The contact an ingested event belongs to, or None for a number nobody holds.

      1. an explicit `contact_id` — must exist, else 404. Unchanged: this is the
         path owen-main uses today and the one its own tests pin.
      2. a `from_number` that matches an existing contact on the last ten digits.
      3. a `from_number` that matches nothing — **None**. The caller files the
         event on that number's thread (app/number_threads.py).

    AMENDED 2026-09-13. Path 3 used to CREATE a contact named by the number and fire
    the new-lead notification. The owner reversed that: "if its a number that is
    not registered as a contact, it should not be saved as contact". Spam callers
    were becoming contacts. Nothing is created and nobody is notified; the number
    still shows in the inbox, unread, with its whole conversation.
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
    if not number_threads.phone_key(number):
        # Too short to be a line anyone can be called back on. Filing it under a
        # fragment would let it collide with every number ending in those digits.
        raise HTTPException(
            422, "from_number %r is not a complete phone number" % number)
    return None


def _duplicate_event(db: Session, key: str):
    """The event already holding `key`, on either kind of thread, or None."""
    seen = db.scalar(select(ConversationEvent)
                     .where(ConversationEvent.dedupe_key == key))
    if seen is not None:
        return seen
    return db.scalar(select(NumberThreadEvent)
                     .where(NumberThreadEvent.dedupe_key == key))


# Fields a later delivery of the SAME object may fill in when the first one could
# not carry them. A Quo call reaches us on `call.completed` before its recording,
# transcript and summary exist; those arrive as separate webhook events for the same
# call and must land on the one row rather than a second (2026-09-13). Only BLANKS
# are filled — nothing already recorded is ever overwritten by a repeat delivery.
ENRICHABLE_FIELDS = ("recording_url", "transcript", "call_status", "duration_seconds",
                     "source_number")


def _enrich(seen, body: EventIngest) -> list[str]:
    filled = []
    for name in ENRICHABLE_FIELDS:
        new = getattr(body, name)
        if new in (None, "") or getattr(seen, name) not in (None, ""):
            continue
        if name == "call_status" and seen.type is not EventType.CALL:
            continue
        setattr(seen, name, new)
        filled.append(name)
    # The one field that is ADDED TO rather than filled: a summary is appended to the
    # call's sentence once. Containment is the idempotency check — a second delivery
    # of the same summary finds it already there and changes nothing.
    summary = (body.summary or "").strip()
    if summary and seen.type is EventType.CALL and summary not in (seen.body or ""):
        seen.body = ((seen.body or "").rstrip() + " Summary: " + summary).strip()
        filled.append("summary")
    return filled


def _plan_pictures(db: Session, ev, body: EventIngest) -> int:
    """Make the PENDING attachment rows for an inbound MMS and queue their fetch.

    Returns how many pictures are coming — the ingest answers it as `attachments_expected`,
    a COUNT and not the list a send route's `attachments` is, which is why it has its own
    name. It is worth answering at all because — owen-main logs
    it, and "the CRM took the text but not the photo" is otherwise invisible from that end.

    Deliberately NOT part of the transaction's success condition: the rows are added to the
    same commit as the event (a text and its pictures are one thing), but the BYTES are
    fetched later by the worker. See app/message_media.py for why that is a job and not an
    inline carrier round-trip.
    """
    if body.direction != "INBOUND" or body.type != "SMS" or body.num_media <= 0:
        return 0
    rows = message_media.plan_inbound(db, ev, body.provider_ref, body.num_media)
    message_media.enqueue_fetch(db, rows)
    return len(rows)


def _ingest_to_number(db: Session, body: EventIngest, key: str) -> dict:
    """File an event from a number no contact holds. Creates no contact, no deal,
    no job. See app/number_threads.py."""
    thread, _ = number_threads.thread_for_number(db, body.from_number or "",
                                                 at=body.occurred_at)
    name = (body.source_contact_name or "").strip()
    if name:
        thread.quo_name = name
    ev = NumberThreadEvent(
        number_thread_id=thread.id,
        type=EventType[body.type],
        direction=Direction[body.direction],
        body=body.body,
        duration_seconds=body.duration_seconds,
        call_status=body.call_status,
        recording_url=body.recording_url,
        provider_ref=body.provider_ref,
        dedupe_key=key or None,
        source_system=body.source_system,
        source_number=body.source_number,
        transcript=body.transcript,
    )
    if body.occurred_at is not None:
        ev.occurred_at = body.occurred_at
    db.add(ev)
    db.flush()
    previous = automations.as_utc(thread.last_event_at)
    occurred = automations.as_utc(ev.occurred_at)
    if previous is None or occurred > previous:
        thread.last_event_at = ev.occurred_at
    if body.direction == "INBOUND" and automations.is_fresh(ev.occurred_at):
        thread.unread_count += 1
    pictures = _plan_pictures(db, ev, body)
    db.commit()
    return {"id": ev.id, "conversation_id": None, "number_thread_id": thread.id,
            "contact_id": None, "attachments_expected": pictures,
            "automation": "none: not a contact — filed on a number-only thread"}


@app.post("/api/events", status_code=201)
def ingest_event(body: EventIngest, db: Session = Depends(get_db),
                 _: auth.Principal = auth.EVENTS_INGEST):
    # IDEMPOTENCY, and it comes first — before the contact is resolved or created.
    #
    # A feed that may deliver the same object twice sends a `dedupe_key`. Two
    # things produce that here and neither is exotic: the OpenPhone mirror's poll
    # re-reads its whole window every tick, and owen-main's `crm_report` job is
    # retried on a timeout — including a timeout that happened AFTER this endpoint
    # committed and before its 201 got home. The second case is why the check
    # cannot live on the far side: owen-main genuinely does not know whether the
    # first attempt landed.
    #
    # Returning the EXISTING row rather than 409-ing is deliberate. To the caller a
    # repeat delivery succeeded — the event is on the thread, which is all it
    # wanted — and a 4xx would dead-letter a `crm_report` job after five attempts
    # for having done its job correctly.
    #
    # Resolving the contact first would be worse than wasteful: `_resolve_ingest_
    # contact` CREATES a contact for an unrecognised number and fires the new-lead
    # automation, so a duplicate delivery would notify the crew about a lead that
    # was already on file.
    key = (body.dedupe_key or "").strip()
    if key:
        seen = _duplicate_event(db, key)
        if seen is not None:
            filled = _enrich(seen, body)
            if filled:
                db.commit()
            if isinstance(seen, NumberThreadEvent):
                return {"id": seen.id, "conversation_id": None,
                        "number_thread_id": seen.number_thread_id,
                        "contact_id": None, "enriched": filled,
                        "automation": "duplicate: already ingested"}
            conv_existing = db.get(Conversation, seen.conversation_id)
            return {"id": seen.id, "conversation_id": seen.conversation_id,
                    "contact_id": conv_existing.contact_id if conv_existing else None,
                    "enriched": filled,
                    "automation": "duplicate: already ingested"}

    # A CARRIER DELIVERY RECEIPT IS NOT A MESSAGE (2026-09-16). BulkVS posts these to the
    # same webhook as an inbound text, and before owen-main learned to tell them apart they
    # were relayed here and filed on a customer's thread as words the customer had written.
    #
    # owen-main is where that is fixed. This is the CRM's own guard, and it is worth having
    # because the two systems DEPLOY SEPARATELY: for however long an older owen-main is
    # running, every receipt it relays would otherwise land in a conversation. Answered as a
    # success with a reason rather than a 4xx — the relay job did its job, and dead-lettering
    # it after five attempts for correctly delivering something we do not want would only
    # lose the log line that says so.
    if (body.type == "SMS" and body.direction == "INBOUND"
            and dlr.looks_like_receipt(body.body)):
        return {"id": None, "conversation_id": None, "contact_id": None,
                "number_thread_id": None, "attachments_expected": 0,
                "automation": dlr.REFUSED}

    if body.call_status is not None:
        if body.type != "CALL":
            raise HTTPException(400, "call_status is only meaningful on a CALL")
        if body.call_status not in CALL_STATUSES:
            raise HTTPException(400, "unknown call_status %r — expected one of %s"
                                % (body.call_status, sorted(CALL_STATUSES)))

    contact = _resolve_ingest_contact(db, body)
    if contact is None:
        return _ingest_to_number(db, body, key)

    conv = db.scalar(select(Conversation).where(
        Conversation.contact_id == contact.id))
    if conv is None:
        # Dated by the event that opens it, not by the ingest clock: a thread first
        # seen through a back-dated event must not sort above one active since.
        conv = Conversation(contact_id=contact.id)
        if body.occurred_at is not None:
            conv.last_event_at = body.occurred_at
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
        dedupe_key=key or None,
        source_system=body.source_system,
        source_number=body.source_number,
        transcript=body.transcript,
    )
    # Omitted means "now", which is what every caller before the mirror meant and
    # what the column default already does. Set means the feed observed the real
    # time, and the thread must show it there.
    if body.occurred_at is not None:
        ev.occurred_at = body.occurred_at
    db.add(ev)
    db.flush()

    # FORWARD-ONLY, for the same reason the delivery ladder is. `last_event_at`
    # orders the inbox, so a backfilled three-week-old call must not drag a thread
    # that was active this morning back down the list. A live event is newer than
    # whatever is there and still wins.
    fresh = automations.is_fresh(ev.occurred_at)
    # Both sides normalised to aware UTC before comparing: SQLite round-trips a
    # timezone-aware column as NAIVE and Postgres does not, so an un-normalised
    # comparison raises on the tests and silently works in production.
    previous = automations.as_utc(conv.last_event_at)
    occurred = automations.as_utc(ev.occurred_at)
    if previous is None or occurred > previous:
        conv.last_event_at = ev.occurred_at
    # "Unread" means something arrived that nobody has seen. A mirrored text from
    # three weeks ago was seen — in OpenPhone, which is where it was answered — so
    # counting it would hand the operator a badge of 200 for correspondence that is
    # already handled, and train them to clear the badge without reading it.
    if body.direction == "INBOUND" and fresh:
        conv.unread_count += 1

    # Rule 1 (missed call -> auto text back) is DISABLED as of 2026-09-13 and
    # answers with the reason; it is still asked so the response says so.
    outcome = automations.on_inbound_call(db, ev)
    # AI agents (2026-09-15): an unanswered call or an inbound text on a CONTACT's thread.
    # A number no contact holds returned above and never reaches this line.
    if fresh:
        ai_triggers.inbound_event(db, contact, conv, ev)
    pictures = _plan_pictures(db, ev, body)
    db.commit()
    return {"id": ev.id, "conversation_id": conv.id, "contact_id": contact.id,
            "attachments_expected": pictures, "automation": outcome}


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
        # A text sent from a number-only thread is receipted the same way.
        ev = db.scalars(
            select(NumberThreadEvent)
            .where(NumberThreadEvent.provider_ref == key,
                   NumberThreadEvent.direction == Direction.OUTBOUND)
            .order_by(NumberThreadEvent.id.desc())
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
        "conversation_id": getattr(ev, "conversation_id", None),
        "number_thread_id": getattr(ev, "number_thread_id", None),
        "delivery_status": ev.delivery_status.value if ev.delivery_status else None,
        # False when a stale or out-of-order receipt was correctly ignored. The
        # relay needs to tell "we applied it" from "we already knew better".
        "advanced": ev.delivery_status is not before,
    }


# ---------- American Home Shield work orders (2026-09-14) ----------
#
# owen-main posts every AHS work order it parses out of the Dispatch mailbox here, as
# its own queued job beside (never instead of) its GoHighLevel relay. The logic is in
# app/ahs_jobs.py; these two routes are the transaction. Same machine token and the
# same `events:write` scope as `/api/events`: the same account on the same machine,
# delivering the same kind of feed, and a second scope would only be a second token
# for the owner to mint and rotate. Per-pipeline access: the AHS board must be
# visible to the token's user (`pipeline_access.can_see`), or the request is refused
# as if the board did not exist.

@app.post("/api/ahs-jobs", status_code=201)
def ingest_ahs_job(body: ahs_jobs.AhsJobIn, response: Response,
                   db: Session = Depends(get_db),
                   principal: auth.Principal = auth.EVENTS_INGEST):
    """One AHS work order -> one contact (matched or created), one open card in
    Dream Team Roofing AHS / New Lead, and its work order as a note. A repeat
    `ahs_job_id` answers 200 with the card that exists and writes nothing."""
    code, out = ahs_jobs.deliver_job(db, body, principal)
    if code == 201:
        # The work order's card gets its CompanyCam project, like a card made by hand.
        card = db.get(Opportunity, out["opportunity"]["id"])
        companycam.request_project(db, card, "ahs_email")
        db.commit()
    response.status_code = code
    return out


@app.post("/api/ahs-jobs/cancellations")
def ingest_ahs_cancellation(body: ahs_jobs.AhsCancellationIn,
                            db: Session = Depends(get_db),
                            principal: auth.Principal = auth.EVENTS_INGEST):
    """Note the cancellation on that job's card and leave the card open. No card
    for the job answers `outcome: no_card` and writes nothing."""
    out = ahs_jobs.deliver_cancellation(db, body, principal)
    if out["outcome"] == "noted":
        db.commit()
    return out


class AppointmentCreate(BaseModel):
    title: str
    starts_at: datetime
    ends_at: datetime
    contact_id: int | None = None
    # Not in GoHighLevel's Book appointment modal (2026-09-13): the assignee is the
    # selected calendar's user. Still accepted, so `ghl appts create --user` and
    # the telephony feed keep working; an explicit assignee wins over the calendar's.
    assigned_user_id: int | None = None
    # Was missing, so every appointment created through the API landed on no
    # calendar at all and never appeared under a calendar filter.
    calendar_id: int | None = None
    # The deal this visit is for. Optional in both directions: a booking made from
    # an opportunity arrives with it filled in, one made on the calendar can pick
    # an open deal or none at all. Never required — a call at 2am becomes a visit
    # before anybody has filed a deal for it.
    opportunity_id: int | None = None
    # The modal's STAFF-only "Internal notes". The route is STAFF already.
    notes: str | None = None
    # "Add description" — visible to every role.
    description: str | None = None
    # "Meeting location". `calendar_default` resolves to the contact's property
    # address; `custom` takes `location` as typed; absent stores `location` as sent.
    location_kind: Literal["calendar_default", "custom"] | None = None
    location: str | None = None
    # The footer's "Status :" dropdown. Was not accepted before 2026-09-13, when the
    # dialog showed it disabled; the owner's GoHighLevel modal books with a status.
    status: str = "confirmed"
    # Booking over blocked off time on the same calendar is refused with 409 until
    # the booker has seen it and confirms by sending this.
    allow_blocked_time: bool = False


def _check_appointment_opportunity(db: Session, principal: auth.Principal,
                                   opp_id: int | None) -> None:
    """A deal the caller cannot access is answered exactly like one that does not
    exist — a booking must not be a way to probe for a hidden deal's id, whether it
    is hidden by its pipeline or by "Only assigned data"."""
    if opp_id is None:
        return
    o = db.get(Opportunity, opp_id)
    if o is None or not assigned_access.scope(db, principal).sees_job(o):
        raise HTTPException(404, "opportunity %s not found" % opp_id)


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


# GoHighLevel prefills "Appointment title" with `{{contact.name}}`. The variable is
# resolved when the booking is SAVED and the resolved text is what is stored, so a
# calendar chip, a reminder and the CLI all read a name rather than braces, and
# renaming the contact later does not rename a visit already booked.
TITLE_VARIABLES = {
    "contact.name": lambda c: c.name,
    "contact.first_name": lambda c: c.first_name,
    "contact.last_name": lambda c: c.last_name,
    "contact.email": lambda c: c.email or "",
    "contact.phone": lambda c: format_phone(c.phone) if c.phone else "",
}
_TEMPLATE_VARIABLE = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")


def _resolve_appointment_title(title: str, contact: Contact | None) -> str:
    """Resolve every `{{...}}` in a title, then clean it like any other title.

    An unknown variable is refused rather than stored verbatim: braces on a
    customer's calendar chip read as a bug. A contact variable with no contact is
    refused with the reason, rather than resolved to an empty title."""
    def sub(m: re.Match) -> str:
        name = m.group(1).strip()
        resolve = TITLE_VARIABLES.get(name.lower())
        if resolve is None:
            raise HTTPException(400, "the title uses {{%s}}, which is not a variable "
                                "this app knows — {{contact.name}} is" % name)
        if contact is None:
            raise HTTPException(400, "the title uses {{%s}}, so select a contact "
                                "first" % name)
        return resolve(contact) or ""
    return _clean_appointment_title(_TEMPLATE_VARIABLE.sub(sub, title or ""))


def contact_address(c: Contact | Opportunity | None) -> str | None:
    """The contact's saved property address on one line — "Calendar default" in
    the Meeting location control. `None` when nothing is on file."""
    if c is None:
        return None
    region = " ".join(p.strip() for p in (c.address_state, c.address_postal_code)
                      if p and p.strip())
    parts = [p.strip() for p in (c.address_street, c.address_city, region)
             if p and p.strip()]
    return ", ".join(parts) or None


def _resolve_location(kind: str | None, typed: str | None,
                      contact: Contact | None,
                      opportunity: Opportunity | None = None) -> str | None:
    """"Calendar default" is the JOB's address when the booking is linked to a card
    that has one (2026-09-14), else the contact's — a customer with six roofs is
    visited at the roof the deal is for. `contact_address` formats either: it reads
    the same four attribute names off both."""
    if kind == "calendar_default":
        return contact_address(opportunity) or contact_address(contact)
    cleaned = (typed or "").strip() or None
    if kind == "custom" and cleaned is None:
        raise HTTPException(400, "a custom meeting location needs an address")
    return cleaned


def _utc(dt: datetime) -> datetime:
    """Normalise an incoming time to UTC before it is stored or compared.

    SQLite keeps a datetime's wall-clock digits and drops its offset, so
    `13:30-04:00` stored as-is reads back as 13:30 UTC — four hours early. The
    browser always sends UTC, which is why this never showed; the CLI and a test
    sending an Eastern offset would not. PostgreSQL is unaffected either way."""
    return auth.as_aware(dt).astimezone(UTC)


def _blocked_overlaps(db: Session, calendar_id: int | None, starts: datetime,
                      ends: datetime) -> list[BlockedTime]:
    """Blocked off time on the same calendar that this range overlaps. Touching
    ends do not overlap: a visit from 3:00 may follow a block ending at 3:00."""
    if calendar_id is None:
        return []
    return list(db.scalars(select(BlockedTime).where(
        BlockedTime.calendar_id == calendar_id,
        BlockedTime.starts_at < _utc(ends),
        BlockedTime.ends_at > _utc(starts)).order_by(BlockedTime.starts_at)).all())


def _refuse_blocked_overlap(db: Session, calendar_id: int | None, starts: datetime,
                            ends: datetime, allowed: bool) -> None:
    """409 until the booker has confirmed. Raised BEFORE anything is written."""
    if allowed:
        return
    hits = _blocked_overlaps(db, calendar_id, starts, ends)
    if hits:
        cal = db.get(Calendar, calendar_id)
        raise HTTPException(409, "This time overlaps blocked off time on %s: %s. "
                            "Book it anyway?" % (
                                cal.name if cal else "this calendar",
                                ", ".join('"%s"' % b.title for b in hits)))


def _check_new_status(status: str) -> str:
    if status not in APPOINTMENT_STATUSES:
        raise HTTPException(400, "unknown status %r — expected one of %s" % (
            status, sorted(APPOINTMENT_STATUSES)))
    return status


@app.post("/api/appointments", status_code=201)
def create_appointment(body: AppointmentCreate, db: Session = Depends(get_db),
                       principal: auth.Principal = auth.STAFF):
    a, outcome = book_appointment(db, principal, body)
    db.commit()
    db.refresh(a)
    return {"id": a.id, "title": a.title, "starts_at": _aware(a.starts_at),
            "ends_at": _aware(a.ends_at), "opportunity_id": a.opportunity_id,
            "assigned_user_id": a.assigned_user_id, "status": a.status,
            "description": a.description, "location": a.location,
            "automation": outcome}


def book_appointment(db: Session, principal: auth.Principal, body: AppointmentCreate,
                     *, ai_agent_id: int | None = None) -> tuple[Appointment, str]:
    """Book a visit, as a service: the Book appointment modal's route and an AI agent's
    "book appointment" action run exactly this (2026-09-15). Every check the route had is
    here; the caller commits."""
    contact = None
    if body.contact_id is not None:
        contact = db.get(Contact, body.contact_id)
        if contact is None or not assigned_access.scope(db, principal).sees_contact(
                contact.id):
            raise HTTPException(404, "contact %s not found" % body.contact_id)
    calendar = None
    if body.calendar_id is not None:
        calendar = db.get(Calendar, body.calendar_id)
        if calendar is None:
            raise HTTPException(404, "calendar %s not found" % body.calendar_id)
    title = _resolve_appointment_title(body.title, contact)
    status = _check_new_status(body.status)
    if body.ends_at <= body.starts_at:
        raise HTTPException(400, "ends_at must be after starts_at")
    # A dangling id would be an IntegrityError rendered as a 500 in the dialog, the
    # same reason PATCH resolves its ids before writing.
    _check_appointment_opportunity(db, principal, body.opportunity_id)
    _refuse_blocked_overlap(db, body.calendar_id, body.starts_at, body.ends_at,
                            body.allow_blocked_time)
    a = Appointment(
        title=title, starts_at=_utc(body.starts_at), ends_at=_utc(body.ends_at),
        contact_id=body.contact_id, calendar_id=body.calendar_id,
        opportunity_id=body.opportunity_id, status=status,
        # GoHighLevel: the calendar's user is the assignee.
        assigned_user_id=(body.assigned_user_id if body.assigned_user_id is not None
                          else calendar.user_id if calendar else None),
        notes=(body.notes or "").strip() or None,
        description=(body.description or "").strip() or None,
        location=_resolve_location(
            body.location_kind, body.location, contact,
            db.get(Opportunity, body.opportunity_id)
            if body.opportunity_id is not None else None))
    a.ai_agent_id = ai_agent_id
    db.add(a)
    db.flush()
    # Rule 3: booked -> reminders at T-24h and T-1h. Unchanged by the new modal.
    outcome = automations.on_appointment_booked(db, a)
    ai_triggers.appointment_changed(db, a, "appointment_booked")
    return a, outcome


def _opp_detail(o: Opportunity, db: Session | None = None) -> dict:
    """Shape mirrors GHL's measured opportunity detail screen.

    `db` is optional only so the callers that already hold one do not have to be
    rewritten; without it the linked appointments come back empty rather than
    wrong, and every caller in this file passes it.
    """
    appointments = []
    if db is not None:
        appointments = [
            {"id": a.id, "title": a.title, "starts_at": _aware(a.starts_at),
             "ends_at": _aware(a.ends_at), "status": a.status,
             "calendar_name": a.calendar.name if a.calendar else None,
             "location": a.location}
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
        # 0-100 or null. Read by the Forecast only when the pipeline uses
        # opportunity-level probability.
        "probability": o.probability,
        # The JOB's address (2026-09-14), not the contact's. All four null when the
        # card has none; the modal then offers the contact's as a greyed fallback.
        **_opp_address(o),
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
        # The modal (2026-09-13). Followers are user accounts; additional contacts
        # are the deal's other people, never the primary one; tags are the PRIMARY
        # CONTACT's, because an opportunity has none of its own (DECISIONS.md).
        "followers": (opportunity_workspace.followers_of(db, o.id)
                      if db is not None else []),
        "additional_contacts": (opportunity_workspace.additional_contacts_of(db, o.id)
                                if db is not None else []),
        "contact_tags": ([{"id": t.tag.id, "name": t.tag.name, "color": t.tag.color}
                          for t in sorted(o.contact.tags, key=lambda t: t.tag.name)]
                         if o.contact else []),
        "conversation_id": (db.scalar(select(Conversation.id).where(
            Conversation.contact_id == o.contact_id)) if db is not None
            and o.contact_id is not None else None),
    }


@app.get("/api/opportunities/{opp_id}")
def get_opportunity(opp_id: int, db: Session = Depends(get_db),
                    principal: auth.Principal = auth.ANY_USER):
    # 404, not 403, for a deal in a pipeline the caller cannot access.
    return _opp_detail(pipeline_access.get_opportunity(db, principal, opp_id), db)


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


# The job's address on a card (2026-09-14). The max_lengths mirror the columns, as
# ContactPatch's do, so an over-long value is a 422 naming the field rather than a
# truncation error rendered as a bare 500; whitespace is stored as nothing.
OPPORTUNITY_ADDRESS = ("address_street", "address_city", "address_state",
                       "address_postal_code")


def _opp_address(o: Opportunity) -> dict:
    return {k: getattr(o, k) for k in OPPORTUNITY_ADDRESS}


class OpportunityPatch(BaseModel):
    """Measured GHL statuses: Open / Won / Lost / Abandoned.

    2026-09-13, for the modal: the primary contact, the PIPELINE (a move must name
    a stage in the new pipeline), followers and additional contacts. The two lists
    REPLACE the set they name, and are left alone when not sent.
    """
    title: str | None = Field(None, max_length=OPPORTUNITY_TITLE_MAX)
    contact_id: int | None = None
    pipeline_id: int | None = None
    follower_ids: list[int] | None = None
    additional_contact_ids: list[int] | None = None
    stage_id: int | None = None
    status: Literal["open", "won", "lost", "abandoned"] | None = None
    value_cents: int | None = None
    owner_id: int | None = None
    business_name: str | None = None
    source: str | None = None
    expected_close_date: str | None = None
    # The deal's own win probability, 0-100, or null to clear it. Stored whatever
    # the pipeline's setting; the Forecast only reads it when the pipeline uses
    # opportunity-level probability.
    probability: int | None = Field(None, ge=0, le=100)
    custom_fields: dict | None = None
    # The job's address, one field at a time; null or blank clears that field.
    address_street: str | None = Field(None, max_length=255)
    address_city: str | None = Field(None, max_length=120)
    address_state: str | None = Field(None, max_length=80)
    address_postal_code: str | None = Field(None, max_length=20)

    _address = field_validator(*OPPORTUNITY_ADDRESS, mode="after")(_blank_to_none)


# What a TECH with "Only assigned data" on may send to the detail PATCH, on their own
# job (2026-09-15): the answers to its questions — the Checklist's ticks included —
# and its stage. Not the title, value, status, pipeline, owner, followers, source,
# business name, address or contacts; the card's address beside a "verified" tick is
# the address, so it is refused here like any other address edit.
TECH_JOB_FIELDS = {"custom_fields", "stage_id"}


@app.patch("/api/opportunities/{opp_id}/detail")
def update_opportunity(opp_id: int, body: OpportunityPatch,
                       db: Session = Depends(get_db),
                       principal: auth.Principal = auth.ANY_USER):
    """STAFF edit a deal. A TECH may only answer its questions and move its stage, and
    only on their own job with "Only assigned data" on; every other TECH request is
    refused exactly as the STAFF gate this route had refused it."""
    data = body.model_dump(exclude_unset=True)
    if principal.role is Role.TECH:
        if not assigned_access.tech_on_own_job(principal):
            raise HTTPException(403, "role TECH may not do this")
        # Resolved FIRST, so a job that is not theirs is the same 404 as a missing one
        # whatever the body holds.
        pipeline_access.get_opportunity(db, principal, opp_id)
        refused = sorted(set(data) - TECH_JOB_FIELDS)
        if refused:
            raise HTTPException(403, "a technician can answer this job's questions and "
                                     "move its stage, but not change %s"
                                % ", ".join(refused))
    o = pipeline_access.get_opportunity(db, principal, opp_id)

    if "title" in data:
        data["title"] = _clean_opportunity_title(data["title"])
    if "contact_id" in data:
        # GoHighLevel marks the primary contact required. A deal that already has
        # none keeps working (nothing here asks for one), but an edit cannot take
        # the contact away.
        if data["contact_id"] is None:
            raise HTTPException(400, "primary contact is required")
        _check_opportunity_contact(db, data["contact_id"])
    old_pipeline_id = o.pipeline_id
    new_pipeline_id = data.pop("pipeline_id", None) or o.pipeline_id
    if new_pipeline_id != o.pipeline_id:
        # A pipeline the caller cannot access answers exactly as one that does not
        # exist (pipeline_access.py): the move is refused and existence not leaked.
        if (not db.get(Pipeline, new_pipeline_id)
                or not pipeline_access.can_see(db, principal, new_pipeline_id)):
            raise HTTPException(400, "unknown pipeline_id")
        # Every stage belongs to one pipeline, so a deal moved without a stage
        # would sit in a column of the board it just left.
        if "stage_id" not in data:
            raise HTTPException(400, "moving to another pipeline needs a stage in "
                                     "that pipeline")
    if "stage_id" in data:
        stage = db.get(Stage, data["stage_id"])
        if not stage or stage.pipeline_id != new_pipeline_id:
            raise HTTPException(400, "stage is not in this opportunity's pipeline")
    if ("owner_id" in data and data["owner_id"] is not None
            and not db.get(User, data["owner_id"])):
        raise HTTPException(400, "unknown owner_id")
    if "value_cents" in data:
        _check_opportunity_value(data["value_cents"])
    follower_ids = data.pop("follower_ids", None)
    additional_ids = data.pop("additional_contact_ids", None)
    if additional_ids is not None:
        # Validated BEFORE anything is written, so an 11th contact refuses the
        # whole save rather than landing half of it.
        opportunity_workspace.set_additional_contacts(
            db, o, additional_ids, data.get("contact_id", o.contact_id))
    elif "contact_id" in data and data["contact_id"] is not None:
        # The new primary contact may already be one of the additional ones; it
        # cannot be both.
        for link in db.scalars(select(models.OpportunityContact).where(
                models.OpportunityContact.opportunity_id == o.id,
                models.OpportunityContact.contact_id == data["contact_id"])).all():
            db.delete(link)
    if follower_ids is not None:
        opportunity_workspace.set_followers(db, o, follower_ids)
    if data.get("custom_fields") is not None:
        # One place decides what the blob becomes, shared with the create path.
        # It keeps the `owen_call_id` guard this endpoint has always had (the
        # detail form posts the whole object back on every save, so an unchanged
        # echo is fine and only a real change is refused), validates every answer
        # against its definition's type, and — new — can no longer DROP a key:
        # not a reserved `owen_*` one, not an answer to a field this pipeline does
        # not ask, not an answer to an archived field.
        #
        # Which questions this deal is asked follows its PIPELINE — the one it is
        # moving TO when this save moves it. An answer to a question the new
        # pipeline does not ask is kept untouched by merge_answers, as always.
        answer_questions(db, o, data.pop("custom_fields"), pipeline_id=new_pipeline_id)
    data.pop("custom_fields", None)

    old_stage_id = o.stage_id
    had_street = bool((o.address_street or "").strip())
    for k, v in data.items():
        setattr(o, k, v)
    if new_pipeline_id != old_pipeline_id:
        o.pipeline_id = new_pipeline_id
        # Filed at the bottom of its new column, and the column it left re-packed,
        # the same contiguity the board's drag relies on.
        o.position = db.scalar(select(func.count(Opportunity.id)).where(
            Opportunity.stage_id == o.stage_id, Opportunity.id != o.id)) or 0
        for i, left in enumerate(db.scalars(
                select(Opportunity)
                .where(Opportunity.stage_id == old_stage_id, Opportunity.id != o.id)
                .order_by(Opportunity.position, Opportunity.id)).all()):
            left.position = i
    db.flush()
    if not had_street:
        # The first address saved on a card gets its CompanyCam project.
        companycam.request_project(db, o, "address_saved")
    outcome = automations.on_opportunity_stage_changed(db, o, old_stage_id)
    db.commit()
    db.refresh(o)
    return {**_opp_detail(o, db), "automation": outcome}


def answer_questions(db: Session, o: Opportunity, answers: dict, *,
                     pipeline_id: int | None = None) -> None:
    """Record answers to a deal's questions, as a service (2026-09-15): the detail PATCH
    and an AI agent's "fill checklist answers" action. `merge_answers` is the one place
    that validates an answer against its definition and keeps every reserved key."""
    o.custom_fields = custom_fields.merge_answers(
        db, pipeline_id=pipeline_id or o.pipeline_id, existing=o.custom_fields,
        incoming=answers)


class OpportunityCreate(BaseModel):
    """Create and edit share their rules - see _clean_opportunity_title."""
    title: str = Field(max_length=OPPORTUNITY_TITLE_MAX)
    pipeline_id: int
    stage_id: int
    contact_id: int | None = None
    value_cents: int = 0
    probability: int | None = Field(None, ge=0, le=100)
    # GoHighLevel's Add new opportunity modal (2026-09-14) files the whole card in
    # one submit. All optional, so the machine paths (AHS emails, the Workiz import,
    # the CLI) are unaffected; the modal is what requires a contact, not the API.
    status: Literal["open", "won", "lost", "abandoned"] = "open"
    owner_id: int | None = None
    follower_ids: list[int] | None = None
    business_name: str | None = Field(None, max_length=200)
    source: str | None = Field(None, max_length=120)
    # The job questions, answered in the Add opportunity dialog. Validated by the
    # same code the detail form goes through, so a dropdown cannot be talked into
    # an answer it does not offer by using the other door.
    custom_fields: dict | None = None
    address_street: str | None = Field(None, max_length=255)
    address_city: str | None = Field(None, max_length=120)
    address_state: str | None = Field(None, max_length=80)
    address_postal_code: str | None = Field(None, max_length=20)

    _address = field_validator(*OPPORTUNITY_ADDRESS, mode="after")(_blank_to_none)


@app.post("/api/opportunities", status_code=201)
def create_opportunity(body: OpportunityCreate, db: Session = Depends(get_db),
                       principal: auth.Principal = auth.STAFF):
    title = _clean_opportunity_title(body.title)
    _check_opportunity_value(body.value_cents)
    _check_opportunity_contact(db, body.contact_id)
    stage = db.get(Stage, body.stage_id)
    # A pipeline the caller cannot access gets the same answer as a wrong pair.
    if (not stage or stage.pipeline_id != body.pipeline_id
            or not pipeline_access.can_see(db, principal, body.pipeline_id)):
        raise HTTPException(400, "stage is not in that pipeline")
    if body.owner_id is not None and not db.get(User, body.owner_id):
        raise HTTPException(400, "unknown owner_id")
    answers = custom_fields.merge_answers(
        db, pipeline_id=body.pipeline_id, existing={}, incoming=body.custom_fields)
    n = db.scalar(select(func.count(Opportunity.id))
                  .where(Opportunity.stage_id == body.stage_id)) or 0
    o = Opportunity(title=title, pipeline_id=body.pipeline_id,
                    stage_id=body.stage_id, contact_id=body.contact_id,
                    value_cents=body.value_cents, position=n,
                    probability=body.probability, custom_fields=answers,
                    status=body.status, owner_id=body.owner_id,
                    business_name=(body.business_name or "").strip() or None,
                    source=(body.source or "").strip() or None,
                    **{k: getattr(body, k) for k in OPPORTUNITY_ADDRESS})
    db.add(o)
    db.flush()
    if body.follower_ids:
        # Refused whole (400, nothing committed) when a follower does not exist.
        opportunity_workspace.set_followers(db, o, body.follower_ids)
    # A card made in the CRM with an address gets its CompanyCam project (the
    # worker searches first and links an existing one). No-op without a token.
    companycam.request_project(db, o, "created")
    db.commit()
    db.refresh(o)
    return {"id": o.id, "title": o.title, "stage_id": o.stage_id, **_opp_address(o)}


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


def _bulk_load(db: Session, principal: auth.Principal,
               ids: list[int]) -> list[Opportunity]:
    """Resolve every id, or refuse the whole request having written nothing.

    Partially applying a bulk action is the worst of the three outcomes: the user
    cannot tell which half landed, and re-running it is not safe. Duplicates in
    the selection collapse rather than being applied twice.

    A deal in a pipeline the caller cannot access resolves as MISSING, in the same
    404 sentence, so a selection cannot be used to probe for one.
    """
    unique = list(dict.fromkeys(ids))
    # A deal that is not one of a restricted caller's jobs resolves as missing too.
    rows = db.scalars(assigned_access.opportunities(
        select(Opportunity).where(Opportunity.id.in_(unique)),
        assigned_access.scope(db, principal))).all()
    found = {o.id: o for o in rows}
    missing = [str(i) for i in unique if i not in found]
    if missing:
        raise HTTPException(404, "no opportunity with id " + ", ".join(missing))
    return [found[i] for i in unique]


@app.post("/api/opportunities/bulk/stage")
def bulk_move_stage(body: BulkStageMove, db: Session = Depends(get_db),
                    principal: auth.Principal = auth.ANY_USER):
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
    if not stage or not pipeline_access.can_see(db, principal, stage.pipeline_id):
        raise HTTPException(400, "unknown stage_id")

    opps = _bulk_load(db, principal, body.ids)
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
                      principal: auth.Principal = auth.STAFF):
    """Assign a selection an owner, or `owner_id: null` to unassign.

    STAFF, matching `PATCH /api/opportunities/{id}/detail`, which is where a single
    opportunity's owner is set. Changing an owner is editing the record, and a TECH
    cannot edit records.
    """
    if body.owner_id is not None and not db.get(User, body.owner_id):
        raise HTTPException(400, "unknown owner_id")
    opps = _bulk_load(db, principal, body.ids)
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


def _check_view_pipeline(db: Session, principal: auth.Principal,
                         pipeline_id: int | None) -> None:
    # A pipeline the caller cannot access is as unknown as one that does not exist.
    if pipeline_id is not None and (
            not db.get(Pipeline, pipeline_id)
            or not pipeline_access.can_see(db, principal, pipeline_id)):
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
                     principal: auth.Principal = auth.ANY_USER):
    """Shared, not per-user: four people in one company, and "the list Owen made"
    is the useful thing. Everyone reads them.

    Except a view filed on a pipeline the reader cannot access: it would name that
    pipeline, and applying it would open a board they may not see. It is left out.
    """
    hidden = pipeline_access.hidden_pipeline_ids(db, principal)
    rows = db.scalars(
        select(SavedView).options(selectinload(SavedView.pipeline))
        .order_by(SavedView.position, SavedView.id)).all()
    return [_saved_view_public(v) for v in rows if v.pipeline_id not in hidden]


@app.post("/api/saved-views", status_code=201)
def create_saved_view(body: SavedViewCreate, db: Session = Depends(get_db),
                      principal: auth.Principal = auth.STAFF):
    """STAFF: saving a view is a write, and it is shared with everyone.

    Duplicate names are allowed, deliberately. The pipeline this app was measured
    against holds two distinct stages both called "Call Back" and the whole
    codebase resolves by id rather than deduplicating names; a uniqueness rule
    here would be the only place that disagreed.
    """
    _check_view_pipeline(db, principal, body.pipeline_id)
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
                      principal: auth.Principal = auth.STAFF):
    v = db.get(SavedView, view_id)
    if not v or not pipeline_access.can_see(db, principal, v.pipeline_id):
        raise HTTPException(404, "saved view not found")
    data = body.model_dump(exclude_unset=True)
    if "name" in data:
        data["name"] = _clean_view_name(data["name"])
    if "pipeline_id" in data:
        _check_view_pipeline(db, principal, data["pipeline_id"])
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
    # "Only assigned data": the threads of the contacts on the caller's jobs, and no
    # number-only thread at all (below). Every other control narrows WITHIN that, so
    # the Unread badge is still counted over exactly the list it labels.
    s = assigned_access.scope(db, principal)
    stmt = assigned_access.conversations(
        select(Conversation).options(selectinload(Conversation.contact)), s)
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
    out = [_conversation_row(c, counts.get(c.id, 0)) for c in rows]

    # NUMBER-ONLY THREADS (2026-09-13), mixed in by last activity. Every narrowing
    # control above applies to them by the same rule, so the Unread badge and the
    # list it labels are still computed over one set:
    #   tab      unread_count > 0 / starred — the same two columns;
    #   assigned "me" is the contact's owner, and a number has no owner, so it is in
    #            the team inbox and in nobody's own — like a contact nobody owns;
    #   q        the number (by the shared digits rule) and Quo's name, which is
    #            what the row shows in place of a contact name.
    if assigned != "me" and not s.restricted:
        out.extend(_number_thread_rows(db, tab=tab, q=q))
        out.sort(key=lambda r: automations.as_utc(r["last_event_at"]),
                 reverse=not oldest)
    return out


def _conversation_row(c: Conversation, event_count: int) -> dict:
    """One contact thread as the inbox list renders it."""
    phone = c.contact.phone if c.contact else None
    return {"id": c.id, "key": "c%d" % c.id, "kind": "contact",
            "contact_id": c.contact_id, "number_thread_id": None,
            "contact_name": c.contact.name if c.contact else None,
            "contact_phone": phone, "phone_display": format_phone(phone),
            "contact_dnd": bool(c.contact.dnd) if c.contact else False,
            "quo_name": None,
            "last_event_at": c.last_event_at, "unread_count": c.unread_count,
            "event_count": event_count,
            "starred": c.starred}


def _number_event_counts(db: Session, thread_id: int | None = None) -> dict[int, int]:
    """`_event_counts` for number-only threads: the WHOLE thread, notes included."""
    stmt = select(NumberThreadEvent.number_thread_id, func.count())
    if thread_id is not None:
        stmt = stmt.where(NumberThreadEvent.number_thread_id == thread_id)
    return dict(db.execute(stmt.group_by(NumberThreadEvent.number_thread_id)).all())


def _number_thread_row(t: NumberThread, event_count: int) -> dict:
    """One number-only thread, in the SAME shape as a contact thread's row, so the
    list, the badge arithmetic and the CLI read both kinds with one set of keys.

    `kind` and `key` are what tell them apart: ids come from two tables and can
    collide, so the browser selects by `key` ("c12" / "n3"), never by `id` alone.
    `contact_id` and `contact_name` are None — this is not a contact, and nothing
    here pretends otherwise.
    """
    return {"id": t.id, "key": "n%d" % t.id, "kind": "number",
            "contact_id": None, "number_thread_id": t.id,
            "contact_name": None, "contact_phone": t.phone,
            "phone_display": format_phone(t.phone) or t.phone,
            "contact_dnd": False, "quo_name": t.quo_name,
            "last_event_at": t.last_event_at, "unread_count": t.unread_count,
            "event_count": event_count, "starred": t.starred}


def _number_thread_rows(db: Session, *, tab: str = "all", q: str | None = None) -> list:
    stmt = select(NumberThread)
    if tab == "unread":
        stmt = stmt.where(NumberThread.unread_count > 0)
    elif tab == "starred":
        stmt = stmt.where(NumberThread.starred.is_(True))
    if q and q.strip():
        text = q.strip()
        terms = [NumberThread.quo_name.ilike(contains(text), escape=LIKE_ESCAPE),
                 NumberThread.phone.ilike(contains(text), escape=LIKE_ESCAPE)]
        if phone_match.looks_like_phone(text):
            terms.append(phone_match.phone_clause(NumberThread.phone, text))
        stmt = stmt.where(or_(*terms))
    counts = _number_event_counts(db)
    return [_number_thread_row(t, counts.get(t.id, 0))
            for t in db.scalars(stmt).all()]


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
    assigned_access.get_conversation(db, principal, conv_id)
    return _thread_events(ConversationEvent,
                          ConversationEvent.conversation_id == conv_id,
                          db, filter, principal)


def _thread_events(model, parent_clause, db: Session, filter: str,
                   principal: auth.Principal) -> list[EventOut]:
    """The thread view for either kind of thread — ONE filter implementation, so a
    number-only thread cannot quietly disagree with a contact thread about what
    "Conversations" means or who may read a note."""
    internal_ok = _sees_internal(principal)
    stmt = select(model).where(parent_clause)

    if filter == "conversations":
        types = set(CONVERSATION_TYPES)
        if not internal_ok:
            types -= INTERNAL_TYPES
        stmt = stmt.where(model.type.in_(types))
    elif filter == "activities":
        stmt = stmt.where(model.type.in_(ACTIVITY_TYPES))
    elif filter != "all":
        try:
            wanted = EventType[filter.upper()]
        except KeyError:
            raise HTTPException(400, "unknown filter %r" % filter) from None
        if wanted in INTERNAL_TYPES and not internal_ok:
            _refuse_internal(principal, "read")
        stmt = stmt.where(model.type == wanted)
    elif not internal_ok:
        stmt = stmt.where(model.type.not_in(INTERNAL_TYPES))

    rows = db.scalars(stmt.order_by(model.occurred_at, model.id)).all()
    authors = {aid: ai_engine.ai_author(db, aid)
               for aid in {e.ai_agent_id for e in rows if e.ai_agent_id}}
    # One query for the whole page rather than one per bubble: a thread of 300 texts with
    # two pictures on it should cost two queries, not 301.
    pictures = message_media.by_event(db, list(rows))
    return [EventOut(id=e.id, type=e.type.value, direction=e.direction.value,
                     attachments=pictures.get(message_media.event_key(e), []),
                     ai_author=authors.get(e.ai_agent_id),
                     occurred_at=e.occurred_at, body=e.body, subject=e.subject,
                     duration_seconds=e.duration_seconds,
                     recording_url=e.recording_url,
                     call_status=e.call_status,
                     delivery_status=e.delivery_status.value
                     if e.delivery_status else None,
                     delivery_detail=e.delivery_detail,
                     transcript=e.transcript,
                     source_system=e.source_system,
                     source_number=e.source_number) for e in rows]


# ---------- appointments ----------

@app.get("/api/users")
def list_users(db: Session = Depends(get_db),
               q: str | None = None,
               role: Literal["ADMIN", "DISPATCHER", "TECH"] | None = None,
               principal: auth.Principal = auth.ANY_USER):
    """Every signed-in user can see the roster; only an ADMIN sees email addresses.

    Restricting the whole endpoint to ADMIN was considered and rejected: the owner
    dropdowns on the contact panel and the opportunity form both read this, so it
    would break those screens for a dispatcher. The original exposure being closed
    here is *unauthenticated* access to the staff list, not staff seeing each other.
    """
    stmt = select(User).order_by(User.id)
    is_admin = principal.role is Role.ADMIN
    if is_admin and role:
        stmt = stmt.where(User.role == Role[role])
    if is_admin and q and q.strip():
        # My Staff's search box: name, email, phone or id — what GoHighLevel's says.
        text = q.strip()
        terms = [User.name.ilike(contains(text), escape=LIKE_ESCAPE),
                 User.email.ilike(contains(text), escape=LIKE_ESCAPE),
                 User.phone.ilike(contains(text), escape=LIKE_ESCAPE)]
        if text.isdigit() and len(text) < 7:
            # A short number is an id — the ids shown under each email — not a
            # fragment of every phone number that happens to contain that digit.
            terms = [User.id == int(text)]
        elif phone_match.looks_like_phone(text):
            terms.append(phone_match.phone_clause(User.phone, text))
        stmt = stmt.where(or_(*terms))
    rows = db.scalars(stmt).all()
    if not is_admin:
        return [{"id": u.id, "name": u.name, "role": u.role.value,
                 "is_active": u.is_active, "email": None, "only_assigned_data": None}
                for u in rows]
    # My Staff reads this: everything an admin manages. Email, phone and who is
    # restricted are ADMIN-only, like email always was.
    return [_user_public(u) for u in rows]


@app.get("/api/calendars")
def list_calendars(db: Session = Depends(get_db),
                   principal: auth.Principal = auth.ANY_USER):
    """Filter groups measured on GHL: Users and Calendars.
    `pipeline` is our addition — GHL has no pipeline filter here.

    The calendar itself is not a pipeline's and stays listed; only the link to a
    pipeline the caller cannot access is blanked, so it is not named.
    """
    s = assigned_access.scope(db, principal)
    hidden = s.hidden
    rows = db.scalars(assigned_access.calendars(
        select(Calendar).options(selectinload(Calendar.user),
                                 selectinload(Calendar.pipeline)), s)
        .order_by(Calendar.id)).all()
    return [{"id": c.id, "name": c.name, "color": c.color,
             "user_id": c.user_id, "user_name": c.user.name if c.user else None,
             "pipeline_id": None if c.pipeline_id in hidden else c.pipeline_id,
             "pipeline_name": (c.pipeline.name if c.pipeline
                               and c.pipeline_id not in hidden else None)}
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
                            start: datetime | None, end: datetime | None,
                            hidden: set[int]):
    """Every opportunity the dashboard is allowed to count.

    The range filters on **creation date** — the owner's decision (2026-09-10).
    Not the stage-change date and not the won date: `updated_at` moves every time
    anyone touches a record, and there is no `won_at` column at all (see the Call
    report amendment in DECISIONS.md, which hit the same gap).

    No range means ALL TIME, deliberately. Defaulting to 30 days would empty this
    screen for a company whose deals were created in one August week, and a fix
    that reads as a regression is worse than the bug it fixes.

    `hidden` is required: deals in a pipeline the reader cannot access are never
    counted, so no figure on the Dashboard is computed over them.
    """
    stmt = pipeline_access.visible_opportunities(select(Opportunity), hidden)
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
              principal: auth.Principal = assigned_access.REPORTING):
    """Measured GHL dashboard cards: Opportunity status (Won/Open/Lost + total),
    Opportunity value (Total vs Won revenue), Conversion rate.

    `start`/`end` bound the opportunity's **creation** date; omitting both means
    all time. Every figure below obeys the same window — nothing on this endpoint
    is exempt from it.

    The arithmetic itself lives in `_status_rollup`, which `/api/forecast` also
    uses, so the forecast cannot publish a conversion rate the Dashboard disagrees
    with.
    """
    assigned_access.refuse_reporting(principal)
    hidden = pipeline_access.hidden_pipeline_ids(db, principal)
    return _status_rollup(
            _opportunities_in_range(db, pipeline_id, start, end, hidden)) | {
        # Echoed so the browser and the CLI can show which window produced these
        # numbers rather than trusting the control that was last clicked.
        "range": {"start": _range_utc(start), "end": _range_utc(end)},
    }


@app.get("/api/dashboard/funnel")
def dashboard_funnel(db: Session = Depends(get_db), pipeline_id: int | None = None,
                     start: datetime | None = None, end: datetime | None = None,
                     principal: auth.Principal = auth.ANY_USER):
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

    **"Show in reports"** (the pipeline modal, 2026-09-13) decides which stages
    each card draws, and the two switches are independent:

    * `stages` — the FUNNEL — holds only stages with `show_in_funnel`. A hidden
      stage is left out of the walk entirely, its deals with it, so `reached`,
      `total` and both percentages are the funnel of the stages that are shown.
    * `distribution` — the PIE — holds only stages with `show_in_pie`, and
      `distribution_total` is the sum of exactly those slices, so the number in
      the middle of the donut is the donut.

    A pipeline the caller cannot access is answered like one that does not exist.
    With "Only assigned data" on, the card refuses like the rest of the Dashboard.
    """
    assigned_access.refuse_reporting(principal)
    hidden = pipeline_access.hidden_pipeline_ids(db, principal)
    stmt = select(Pipeline).options(selectinload(Pipeline.stages))
    if pipeline_id:
        stmt = stmt.where(Pipeline.id == pipeline_id)
    if hidden:
        stmt = stmt.where(Pipeline.id.not_in(hidden))
    pipeline = db.scalars(stmt.order_by(Pipeline.position, Pipeline.id)).first()
    if pipeline is None:
        # An unknown pipeline_id is a 404; an empty database simply has no funnel.
        if pipeline_id:
            raise HTTPException(404, "no pipeline %d" % pipeline_id)
        return {"pipeline_id": None, "pipeline_name": None, "total": 0, "stages": [],
                "distribution": [], "distribution_total": 0}

    opps = _opportunities_in_range(db, pipeline.id, start, end, hidden)
    counts: dict[int, int] = {}
    values: dict[int, int] = {}
    for o in opps:
        counts[o.stage_id] = counts.get(o.stage_id, 0) + 1
        values[o.stage_id] = values.get(o.stage_id, 0) + o.value_cents

    in_order = sorted(pipeline.stages, key=lambda s: (s.position, s.id))
    ordered = [s for s in in_order if s.show_in_funnel]
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
    distribution = [{"id": s.id, "name": s.name, "position": s.position,
                     "color": s.color, "count": counts.get(s.id, 0),
                     "value_cents": values.get(s.id, 0)}
                    for s in in_order if s.show_in_pie]
    return {"pipeline_id": pipeline.id, "pipeline_name": pipeline.name,
            "total": total, "stages": stages,
            "distribution": distribution,
            "distribution_total": sum(d["count"] for d in distribution),
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
             principal: auth.Principal = assigned_access.REPORTING):
    """Projected revenue by stage for one pipeline.

    OUR design — GHL's own Forecast tab was never opened on the live account, so
    there is no capture to match (DECISIONS.md). What it is NOT is a second
    opinion: the per-stage `count`/`value_cents` are the same figures
    `GET /api/pipelines` feeds the Dashboard funnel with (every status, not just
    open), and the rollup and `conversion_rate` come from `_status_rollup`, which
    is what `GET /api/dashboard` returns.

        weighted  = open value x the probability that applies
        projected = money already won in the stage + weighted

    WHICH probability applies — the owner's pipeline modal, 2026-09-13, which
    supersedes the single-rate rule recorded on 2026-09-10:

    1. The pipeline uses opportunity-level probability: each OPEN deal is weighted
       at its own `probability`, falling back to its stage's when it has none.
    2. Otherwise: the stage's `probability`, applied to the stage's open value.
    3. Whatever is still unset — every stage that existed before probabilities did
       — is weighted at the pipeline's conversion rate, which is exactly what this
       endpoint did before. A pipeline nobody has edited forecasts as it always has.

    Each stage row says which applied (`weighting`: "opportunity", "stage" or
    "conversion_rate") and quotes the stage's probability, so every weighted figure
    can be re-derived by hand from the response. Integer cents, half up, per deal
    in case 1 and per stage otherwise.

    STAFF, matching `/api/dashboard`, whose aggregates this repeats. A pipeline
    the caller cannot access is a 404 like one that does not exist.
    """
    assigned_access.refuse_reporting(principal)
    p = pipeline_access.get_pipeline(db, principal, pipeline_id)

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
        open_rows = [o for o in rows if o.status == "open"]
        open_value = sum(o.value_cents for o in open_rows)
        won_value = sum(o.value_cents for o in rows if o.status == "won")
        if p.use_opportunity_probability:
            weighting = "opportunity"
            weighted = sum(
                _weighted(o.value_cents,
                          o.probability if o.probability is not None
                          else s.probability if s.probability is not None
                          else rate)
                for o in open_rows)
        elif s.probability is not None:
            weighting = "stage"
            weighted = _weighted(open_value, s.probability)
        else:
            weighting = "conversion_rate"
            weighted = _weighted(open_value, rate)
        stages.append({
            "stage_id": s.id,
            "name": s.name,
            "position": s.position,
            # These two are exactly what /api/pipelines reports for the stage.
            "count": len(rows),
            "value_cents": sum(o.value_cents for o in rows),
            "open_count": len(open_rows),
            "open_value_cents": open_value,
            "won_count": sum(1 for o in rows if o.status == "won"),
            "won_value_cents": won_value,
            "probability": s.probability,
            "weighting": weighting,
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
        "use_opportunity_probability": p.use_opportunity_probability,
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
    principal: auth.Principal = auth.ANY_USER):
    """Range + filters mirror GHL's measured "Manage view" panel:
    View by type (All / Appointments / Blocked slots) and per-user filtering."""
    s = assigned_access.scope(db, principal)
    # "Only assigned data": the visits on the caller's own calendar or assigned to them.
    stmt = assigned_access.appointments(
        select(Appointment).options(selectinload(Appointment.contact),
                                    selectinload(Appointment.calendar),
                                    selectinload(Appointment.opportunity)), s)
    if start:
        stmt = stmt.where(Appointment.ends_at >= _utc(start))
    if end:
        stmt = stmt.where(Appointment.starts_at <= _utc(end))
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
    # A booking stays on the calendar whatever deal it is for — it is a visit, not
    # a deal. Only the link to a deal in a pipeline the reader cannot access is
    # blanked, so the deal's title does not leak through the calendar.
    return [{"id": a.id, "title": a.title, "starts_at": _aware(a.starts_at),
             "ends_at": _aware(a.ends_at), "status": a.status,
             "assigned_user_id": a.assigned_user_id,
             "calendar_id": a.calendar_id,
             "calendar_name": a.calendar.name if a.calendar else None,
             "color": a.calendar.color if a.calendar else "#004eeb",
             **_appointment_deal(a, s),
             "location": a.location,
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
    principal: auth.Principal = assigned_access.REPORTING):
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
    assigned_access.refuse_reporting(principal)
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
    # A won deal in a pipeline the reader cannot access is not counted.
    won_by_contact = dict(db.execute(pipeline_access.visible_opportunities(
        select(Opportunity.contact_id, func.count(Opportunity.id))
        .where(Opportunity.status == "won", Opportunity.contact_id.is_not(None)),
        pipeline_access.hidden_pipeline_ids(db, principal))
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
    principal: auth.Principal = assigned_access.REPORTING):
    """Measured Appointment report: status tiles (Booked, Confirmed, Cancelled,
    New, Showed, No-show, Invalid, Rescheduled) plus Channel / Source breakdown."""
    assigned_access.refuse_reporting(principal)
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


def _appointment_deal(a: Appointment, s: assigned_access.Scope) -> dict:
    """The deal a booking is for, or nothing if the reader may not see that deal —
    a pipeline they cannot access, or (Only assigned data) a job that is not theirs,
    which a CANCELLED visit on their calendar does not make it."""
    if not s.sees_job(a.opportunity):
        return {"opportunity_id": None, "opportunity_title": None,
                "opportunity_address": None, "workiz_job_id": None}
    o = a.opportunity
    # What a calendar block shows under the time when it is tall enough (2026-09-15):
    # the card's street and city, and the Workiz Job # the card was imported from.
    # Blanked with the title above, so neither leaks from a deal the reader cannot see.
    address = ", ".join(p for p in (o.address_street, o.address_city) if p) if o else ""
    return {"opportunity_id": a.opportunity_id,
            "opportunity_title": o.title if o else None,
            "opportunity_address": address or None,
            "workiz_job_id": ((o.custom_fields or {}).get("workiz_id") or None) if o else None}


def _appointment_detail(a: Appointment, s: assigned_access.Scope,
                        principal: auth.Principal) -> dict:
    sees_notes = auth.sees_internal(principal)
    return {"id": a.id, "title": a.title, "starts_at": _aware(a.starts_at),
            "ends_at": _aware(a.ends_at), "status": a.status,
            # `notes` is the Book appointment modal's "Internal notes" — STAFF-only
            # like every internal note in this app (2026-09-10), through the same
            # predicate. A TECH reads `description` and `location` instead.
            "notes": a.notes if sees_notes else None,
            "notes_visible": sees_notes,
            "description": a.description,
            "location": a.location,
            "contact_id": a.contact_id,
            "contact_name": a.contact.name if a.contact else None,
            "calendar_id": a.calendar_id,
            "calendar_name": a.calendar.name if a.calendar else None,
            # Both directions of the link are readable: the deal lists its visits,
            # and the visit names its deal — when the reader may see the deal.
            **_appointment_deal(a, s),
            "assigned_user_id": a.assigned_user_id}


@app.get("/api/appointments/{appointment_id}")
def get_appointment(appointment_id: int, db: Session = Depends(get_db),
                    principal: auth.Principal = auth.ANY_USER):
    a = assigned_access.get_appointment(db, principal, appointment_id)
    return _appointment_detail(a, assigned_access.scope(db, principal), principal)


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
    description: str | None = None
    # Same meaning as on create. Absent leaves the stored location alone.
    location_kind: Literal["calendar_default", "custom"] | None = None
    location: str | None = None
    allow_blocked_time: bool = False


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
                       principal: auth.Principal = auth.STAFF):
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
    a = assigned_access.get_appointment(db, principal, appointment_id)
    outcome = edit_appointment(db, principal, a, body)
    db.commit()
    db.refresh(a)
    return {**_appointment_detail(a, assigned_access.scope(db, principal), principal),
            "automation": outcome}


def edit_appointment(db: Session, principal: auth.Principal, a: Appointment,
                     body: AppointmentPatch, *, ai_agent_id: int | None = None) -> str:
    """Edit / reschedule a visit, as a service (2026-09-15): the appointment panel's route
    and an AI agent's "reschedule appointment" action run exactly this, reminders and
    all. The caller resolved `a` and commits."""
    data = body.model_dump(exclude_unset=True)
    allow_blocked = data.pop("allow_blocked_time", False)
    location_kind = data.pop("location_kind", None)
    if "status" in data and data["status"] not in APPOINTMENT_STATUSES:
        raise HTTPException(400, "unknown status %r — expected one of %s" % (
            data["status"], sorted(APPOINTMENT_STATUSES)))
    for when in ("starts_at", "ends_at"):
        if data.get(when) is not None:
            data[when] = _utc(data[when])
    # A dangling id is a 404 with a sentence, not an IntegrityError rendered as
    # "Something went wrong (500)." in the panel. The dialog picks from lists, so
    # this is really the CLI's and the telephony feed's answer.
    for field, model, what in (("contact_id", Contact, "contact"),
                               ("calendar_id", Calendar, "calendar"),
                               ("opportunity_id", Opportunity, "opportunity"),
                               ("assigned_user_id", User, "user")):
        if data.get(field) is not None and not db.get(model, data[field]):
            raise HTTPException(404, "%s %s not found" % (what, data[field]))
    _check_appointment_opportunity(db, principal, data.get("opportunity_id"))

    contact = (db.get(Contact, data["contact_id"]) if data.get("contact_id") is not None
               else None if "contact_id" in data else a.contact)
    # An edit must not be able to blank a title that the create path refuses, and
    # a template variable resolves against the contact the booking will have.
    if "title" in data:
        data["title"] = _resolve_appointment_title(data["title"] or "", contact)
    for text in ("notes", "description"):
        if text in data:
            data[text] = (data[text] or "").strip() or None
    if location_kind is not None or "location" in data:
        opp_id = data.get("opportunity_id", a.opportunity_id)
        deal = db.get(Opportunity, opp_id) if opp_id is not None else None
        if deal is not None and not assigned_access.scope(db, principal).sees_job(deal):
            # A deal in a pipeline this user cannot access lends the booking nothing:
            # its address must not surface through the calendar.
            deal = None
        data["location"] = _resolve_location(location_kind, data.get("location"),
                                             contact, deal)
    # GoHighLevel: the calendar's user is the assignee. Moving a booking to another
    # calendar hands it to that calendar's user unless an assignee was sent too.
    if data.get("calendar_id") is not None and "assigned_user_id" not in data:
        new_calendar = db.get(Calendar, data["calendar_id"])
        if new_calendar.user_id is not None:
            data["assigned_user_id"] = new_calendar.user_id

    new_calendar_id = data.get("calendar_id", a.calendar_id)
    new_starts = data.get("starts_at") or a.starts_at
    new_ends = data.get("ends_at") or a.ends_at
    new_status = data.get("status", a.status)
    if (("starts_at" in data or "ends_at" in data or "calendar_id" in data)
            and new_status not in automations.NO_REMINDER_STATUSES
            and _aware(new_ends) > _aware(new_starts)):
        # Checked before a single attribute is set, so a refusal writes nothing.
        _refuse_blocked_overlap(db, new_calendar_id, _aware(new_starts),
                                _aware(new_ends), allow_blocked)

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
    if ai_agent_id is not None:
        a.ai_agent_id = ai_agent_id
    if is_off and not was_off and a.status == "cancelled":
        ai_triggers.appointment_changed(db, a, "appointment_cancelled")
    elif moved and not is_off:
        ai_triggers.appointment_changed(db, a, "appointment_rescheduled")
    return outcome


@app.delete("/api/appointments/{appointment_id}")
def cancel_appointment(appointment_id: int, db: Session = Depends(get_db),
                       principal: auth.Principal = auth.STAFF):
    """Cancel, not delete.

    The row survives: GHL's own Appointment report has a Cancelled tile, so a
    cancelled booking is a record, and throwing it away would lose the fact that
    the slot was ever taken.

    The verb and the route say "delete" and the outcome is a status change, so the
    body says so out loud: `deleted: false` alongside `status: "cancelled"`. A
    caller reading a 200 off a DELETE should not have to know this docstring
    exists to find out the row is still there. Nothing here hard-deletes an
    appointment — there is no endpoint that does, deliberately.

    The reminders do NOT survive. `_h_appointment_reminder` re-checks the status
    at run time, so a leftover job could never send — but "could never send" and
    "is not queued" are different things to the dispatcher reading
    `ghl jobs list --status pending`, and only the second one is true here.
    """
    a = assigned_access.get_appointment(db, principal, appointment_id)
    dropped = cancel_appointment_record(db, a)
    db.commit()
    return {"id": a.id, "status": a.status, "deleted": False,
            "reminders_cancelled": dropped}


def cancel_appointment_record(db: Session, a: Appointment, *,
                              ai_agent_id: int | None = None) -> int:
    """Cancel a visit, as a service (2026-09-15): the route and an AI agent's "cancel
    appointment" action. The row survives; its reminders are retired and their keys
    released. Returns how many reminders were withdrawn. The caller commits."""
    was_cancelled = a.status == "cancelled"
    a.status = "cancelled"
    if ai_agent_id is not None:
        a.ai_agent_id = ai_agent_id
    dropped = _drop_pending_reminders(db, a.id)
    if not was_cancelled:
        ai_triggers.appointment_changed(db, a, "appointment_cancelled")
    return dropped


# ---------- blocked off time ----------
#
# GoHighLevel's "Blocked off time" tab of the Book appointment modal (2026-09-13).
# A range on a calendar that is not a booking. It sends nothing and enqueues no
# job — no route below imports the queue or calls an automation. Everyone reads
# (a tech needs to know the crew is off), staff write. A delete is a real delete:
# a block is the team's own schedule, not a customer record, the same call
# DECISIONS.md makes for an opportunity's tasks.


class BlockedTimeIn(BaseModel):
    title: str
    calendar_id: int
    starts_at: datetime
    ends_at: datetime
    notes: str | None = None


class BlockedTimePatch(BaseModel):
    title: str | None = None
    calendar_id: int | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    notes: str | None = None


def _blocked_row(b: BlockedTime) -> dict:
    return {"id": b.id, "title": b.title, "calendar_id": b.calendar_id,
            "calendar_name": b.calendar.name if b.calendar else None,
            "color": b.calendar.color if b.calendar else "#004eeb",
            "starts_at": _aware(b.starts_at), "ends_at": _aware(b.ends_at), "notes": b.notes}


def _clean_blocked_title(title: str) -> str:
    cleaned = (title or "").strip()
    if not cleaned:
        raise HTTPException(400, "blocked off time needs a title")
    return cleaned


@app.get("/api/blocked-times")
def list_blocked_times(
    db: Session = Depends(get_db),
    start: datetime | None = None,
    end: datetime | None = None,
    calendar_ids: str | None = None,
    user_ids: str | None = None,
    principal: auth.Principal = auth.ANY_USER):
    """Everything overlapping the window — the same shape of range query the
    calendar grid asks `/api/appointments` for. `user_ids` selects the blocks on
    those users' calendars, which is what the Users filter means for a block.
    With "Only assigned data" on, only the blocks on the caller's own calendars."""
    stmt = assigned_access.blocked_times(
        select(BlockedTime).options(selectinload(BlockedTime.calendar)),
        assigned_access.scope(db, principal))
    if start:
        stmt = stmt.where(BlockedTime.ends_at >= _utc(start))
    if end:
        stmt = stmt.where(BlockedTime.starts_at <= _utc(end))

    def _ids(raw):
        return [int(x) for x in (raw or "").split(",") if x.strip().isdigit()]

    if _ids(calendar_ids):
        stmt = stmt.where(BlockedTime.calendar_id.in_(_ids(calendar_ids)))
    if _ids(user_ids):
        stmt = stmt.where(BlockedTime.calendar_id.in_(
            select(Calendar.id).where(Calendar.user_id.in_(_ids(user_ids)))))
    return [_blocked_row(b) for b in db.scalars(stmt.order_by(BlockedTime.starts_at))]


@app.get("/api/blocked-times/{blocked_id}")
def get_blocked_time(blocked_id: int, db: Session = Depends(get_db),
                     principal: auth.Principal = auth.ANY_USER):
    return _blocked_row(assigned_access.get_blocked_time(db, principal, blocked_id))


@app.post("/api/blocked-times", status_code=201)
def create_blocked_time(body: BlockedTimeIn, db: Session = Depends(get_db),
                        principal: auth.Principal = auth.STAFF):
    title = _clean_blocked_title(body.title)
    if db.get(Calendar, body.calendar_id) is None:
        raise HTTPException(404, "calendar %s not found" % body.calendar_id)
    if body.ends_at <= body.starts_at:
        raise HTTPException(400, "ends_at must be after starts_at")
    b = BlockedTime(title=title, calendar_id=body.calendar_id,
                    starts_at=_utc(body.starts_at), ends_at=_utc(body.ends_at),
                    notes=(body.notes or "").strip() or None,
                    created_by_id=principal.user_id)
    db.add(b)
    db.commit()
    db.refresh(b)
    return _blocked_row(b)


@app.patch("/api/blocked-times/{blocked_id}")
def update_blocked_time(blocked_id: int, body: BlockedTimePatch,
                        db: Session = Depends(get_db),
                        principal: auth.Principal = auth.STAFF):
    b = assigned_access.get_blocked_time(db, principal, blocked_id)
    data = body.model_dump(exclude_unset=True)
    if "title" in data:
        data["title"] = _clean_blocked_title(data["title"] or "")
    if "calendar_id" in data and (
            data["calendar_id"] is None or db.get(Calendar, data["calendar_id"]) is None):
        raise HTTPException(404, "calendar %s not found" % data["calendar_id"])
    for when in ("starts_at", "ends_at"):
        if when in data:
            if data[when] is None:
                raise HTTPException(400, "%s cannot be empty" % when)
            data[when] = _utc(data[when])
    if "notes" in data:
        data["notes"] = (data["notes"] or "").strip() or None
    starts = _aware(data.get("starts_at", b.starts_at))
    ends = _aware(data.get("ends_at", b.ends_at))
    # Checked before anything is set, so a refusal writes nothing.
    if ends <= starts:
        raise HTTPException(400, "ends_at must be after starts_at")
    for k, v in data.items():
        setattr(b, k, v)
    db.commit()
    db.refresh(b)
    return _blocked_row(b)


@app.delete("/api/blocked-times/{blocked_id}")
def delete_blocked_time(blocked_id: int, db: Session = Depends(get_db),
                        principal: auth.Principal = auth.STAFF):
    b = assigned_access.get_blocked_time(db, principal, blocked_id)
    db.delete(b)
    db.commit()
    return {"deleted": blocked_id}


# ---------- deletes ----------


def _held_clause(ids: list[int], singular: str, plural: str) -> str:
    """Render a blocker as `2 opportunities (3, 7)`. Empty list, empty string.

    The ids are in the sentence because the confirmation the user actually sees is
    built from them: the Contact Details panel turns this 409 into "these will be
    detached", and the CLI prints it verbatim.
    """
    if not ids:
        return ""
    return "%d %s (%s)" % (len(ids), singular if len(ids) == 1 else plural,
                           ", ".join(str(i) for i in ids))


@app.delete("/api/contacts/{contact_id}")
def delete_contact(contact_id: int, force: bool = False,
                   db: Session = Depends(get_db),
                   _: auth.Principal = auth.ADMIN):
    """Delete a contact and its conversation history.

    Nothing that carries meaning of its own is destroyed here. Two kinds of row
    hang off a contact, and both OUTLIVE it, detached rather than deleted:

    * Opportunities — their `custom_fields` carry `owen_call_id`, documented in
      DECISIONS.md as the join key to the telephony project's attribution
      history. Deleting them to tidy up a contact would break reporting that has
      nothing to do with this record.
    * Appointments — a booking is the record that a slot was taken, which is the
      same reason `DELETE /api/appointments/{id}` cancels instead of deleting.
      This one is also not optional: `appointments.contact_id` is a real foreign
      key, so a contact left with an appointment pointing at it does not delete
      at all. Postgres raises ForeignKeyViolation and the caller gets a raw 500
      — reproduced on production while clearing demo data, and the bug this
      shape fixes.

    Conversations ARE deleted: nothing outside this record joins to them,
    `conversations.contact_id` is NOT NULL, and nothing cascades them, so it has
    to be explicit. Their events cascade.

    Both detaches need `force=true`, and the refusal names every row in the way.
    An appointment is a commitment to a customer — somebody may still be driving
    out to it — so severing one as a side effect of tidying a contact is a
    deliberate act, not a default.
    """
    c = db.get(Contact, contact_id)
    if not c:
        raise HTTPException(404, "contact not found")

    opps = db.scalars(select(Opportunity).where(
        Opportunity.contact_id == contact_id)).all()
    appts = db.scalars(select(Appointment).where(
        Appointment.contact_id == contact_id)).all()
    opp_ids = [o.id for o in opps]
    appt_ids = [a.id for a in appts]

    if (opp_ids or appt_ids) and not force:
        # One refusal naming both, rather than one per kind: an admin who clears
        # the opportunities only to be refused again over the appointments has
        # been made to play whack-a-mole with a confirmation dialog.
        held = [clause for clause in (
            _held_clause(opp_ids, "opportunity", "opportunities"),
            _held_clause(appt_ids, "appointment", "appointments")) if clause]
        raise HTTPException(409, "contact has %s — pass force=true to detach "
                                 "them and delete anyway" % " and ".join(held))

    for opp in opps:
        opp.contact_id = None
    reminders = 0
    for appt in appts:
        appt.contact_id = None
        # The reminder handler already skips an appointment whose contact has
        # gone (`_can_message(None)`), so nothing could be sent — but a job that
        # can never run has no business sitting in `ghl jobs list --status
        # pending`. Same distinction `cancel_appointment` draws.
        reminders += _drop_pending_reminders(db, appt.id)
    for conv in db.scalars(select(Conversation).where(
            Conversation.contact_id == contact_id)).all():
        db.delete(conv)
    # Off every deal it was an ADDITIONAL contact on (a link row, nothing else),
    # and its tasks detached rather than deleted — they belong to their deal.
    removed_from = opportunity_workspace.release_contact(db, contact_id)
    db.flush()
    db.delete(c)            # ContactTag rows cascade
    db.commit()
    return {"deleted": contact_id, "detached_opportunities": opp_ids,
            "detached_appointments": appt_ids,
            "removed_from_opportunities": removed_from,
            "reminders_cancelled": reminders}


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
                       principal: auth.Principal = auth.ADMIN):
    """Delete a deal. Its APPOINTMENTS are detached, never deleted.

    A booked visit is a promise to a customer: somebody is expecting a van on
    Tuesday, and tidying up a deal record must not quietly cancel it. This is the
    same call `delete_contact` already makes about opportunities, for the same
    reason — the relationship is a weak one and the column is nullable.

    It is also not optional. `appointments.opportunity_id` is a real foreign key,
    so on PostgreSQL deleting the row underneath it would raise rather than
    cascade; detaching first is what makes the delete work at all.
    """
    # An ADMIN sees every pipeline today; asked anyway, so this route is on the
    # same path as every other read-by-id should that rule ever change.
    o = pipeline_access.get_opportunity(db, principal, opp_id)
    booked = db.scalars(select(Appointment).where(
        Appointment.opportunity_id == opp_id)).all()
    for a in booked:
        a.opportunity_id = None
    db.flush()
    # The deal's OWN notes and tasks go with it, and its follower and
    # additional-contact links; the counts are returned so nothing goes silently.
    # Nothing on the contact's thread is touched.
    gone = opportunity_workspace.remove_opportunity_records(db, opp_id)
    db.delete(o)
    db.commit()
    return {"deleted": opp_id, "detached_appointments": [a.id for a in booked],
            **gone}


# ---------- global event search ----------
#
# The thread view answers "what happened with this contact". These answer "find me
# the call/message", across every thread at once — which is what a CLI needs and
# what /api/conversations/{id}/events cannot do.

def _search_events(db: Session, s: assigned_access.Scope, *, types: set[EventType],
                   start: datetime | None = None, end: datetime | None = None,
                   direction: str = "all", contact_id: int | None = None,
                   q: str | None = None, extra=None,
                   page: int = 1, page_size: int = 50,
                   order: str = "desc") -> tuple[list, int]:
    """Shared by /api/calls and /api/messages — same query, different projection.
    `s` is required: with "Only assigned data" on, only the threads of the contacts
    on the caller's jobs are searched, in the rows and in `total` alike."""
    stmt = assigned_access.contacts(
        select(ConversationEvent, Contact)
        .join(Conversation,
              Conversation.id == ConversationEvent.conversation_id)
        .join(Contact, Contact.id == Conversation.contact_id)
        .where(ConversationEvent.type.in_(types)), s)

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
    principal: auth.Principal = auth.ANY_USER,
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
        db, assigned_access.scope(db, principal), types={EventType.CALL},
        start=start, end=end, direction=direction,
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
        db, assigned_access.scope(db, principal), types=types,
        start=start, end=end, direction=direction,
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


def _search_contacts(db: Session, term: str, limit: int,
                     s: assigned_access.Scope) -> tuple[list, int]:
    """Name, email, phone -- the three things anyone types to find a person.

    `first_name + " " + last_name` is matched as well as the columns separately,
    because "jane doe" is in neither column on its own. Same reasoning, and the
    same rendering (`||` on both Postgres and SQLite), as GET /api/contacts.
    """
    like = contains(term)
    stmt = assigned_access.contacts(select(Contact).where(or_(
        Contact.first_name.ilike(like, escape=LIKE_ESCAPE),
        Contact.last_name.ilike(like, escape=LIKE_ESCAPE),
        (Contact.first_name + " " + Contact.last_name).ilike(
            like, escape=LIKE_ESCAPE),
        Contact.email.ilike(like, escape=LIKE_ESCAPE),
        Contact.phone.ilike(like, escape=LIKE_ESCAPE))), s)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Contact.first_name, Contact.last_name, Contact.id)
        .limit(limit)).all()
    return [{"id": c.id, "name": c.name, "email": c.email, "phone": c.phone}
            for c in rows], total


def _search_opportunities(db: Session, term: str, limit: int,
                          s: assigned_access.Scope) -> tuple[list, int]:
    """Title, or the job's street or city (2026-09-14). Every status, not just
    open: a palette is how you go back to a deal you already won or lost, so
    `status` rides along and the row says which.

    Deals in a pipeline the reader cannot access are neither listed nor counted in
    `total` — a total of 3 above two rows would say a third exists."""
    stmt = assigned_access.opportunities(
        select(Opportunity)
        .options(selectinload(Opportunity.stage),
                 selectinload(Opportunity.contact))
        .where(_opportunity_text_match(term)), s)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Opportunity.updated_at.desc(), Opportunity.id.desc())
        .limit(limit)).all()
    return [{"id": o.id, "title": o.title, "status": o.status,
             "value_cents": o.value_cents,
             "pipeline_id": o.pipeline_id, "stage_id": o.stage_id,
             "stage_name": o.stage.name if o.stage else None,
             "contact_name": o.contact.name if o.contact else None,
             "address_street": o.address_street, "address_city": o.address_city}
            for o in rows], total


def _search_messages(db: Session, term: str, limit: int,
                     types: set[EventType], s: assigned_access.Scope) -> tuple[list, int]:
    """Message BODIES, so a thread can be found by something said in it.

    The result identifies the thread it belongs to (`conversation_id` plus the
    contact), because finding the sentence is only half of what the user wants --
    the other half is being taken to the conversation it was said in.

    Subject lines are NOT matched: the owner asked for bodies. Cheap to add later.
    """
    stmt = assigned_access.contacts(
        select(ConversationEvent, Contact)
        .join(Conversation,
              Conversation.id == ConversationEvent.conversation_id)
        .join(Contact, Contact.id == Conversation.contact_id)
        .where(ConversationEvent.type.in_(types),
               ConversationEvent.body.ilike(contains(term), escape=LIKE_ESCAPE)), s)
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
        # One scope for all three groups: with "Only assigned data" on, a restricted
        # user finds only their jobs, their jobs' contacts and those contacts'
        # threads — in the items AND in every `total`.
        s = assigned_access.scope(db, principal)
        found = [("contacts", "Contacts", _search_contacts(db, term, limit, s)),
                 ("opportunities", "Opportunities",
                  _search_opportunities(db, term, limit, s)),
                 ("messages", "Messages",
                  _search_messages(db, term, limit, types, s))]
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
    # Draft pictures from `POST /api/attachments`, in the order they should be sent
    # (2026-09-16). Ids, not bytes: the composer uploads each picture as it is picked so it
    # can be previewed and removed before anything is sent, and so a 4 MB photo is not
    # re-uploaded every time the operator edits the sentence.
    attachment_ids: list[int] = []


def _pictures_for(db: Session, body: MessageSend,
                  principal: auth.Principal) -> list:
    """The draft attachments a send names, refusing anything a picture cannot go on.

    A picture is an SMS thing. An email has no picture path in this product and an
    internal note is not sent anywhere, so both refuse rather than silently dropping what
    the operator attached — a note that swallowed a photograph would be the kind of quiet
    loss nobody notices until the job is finished.
    """
    if not body.attachment_ids:
        return []
    if body.type != "SMS":
        raise HTTPException(400, "Pictures can only be sent on a text message.")
    return message_media.take_drafts(db, principal, body.attachment_ids)


def _send_to_contact(db: Session, contact: Contact, body: MessageSend,
                     principal: auth.Principal) -> dict:
    pictures = _pictures_for(db, body, principal)
    # A picture with no words IS a message — the customer sent one, and the reply is often
    # one back. Only a text with neither is empty.
    if not body.body.strip() and not pictures:
        raise HTTPException(400, "a message needs a body")
    # Writing a note a TECH cannot then read would be a worse bug than refusing
    # the write, so the read rule and the write rule are the same rule.
    if EventType[body.type] in INTERNAL_TYPES and not _sees_internal(principal):
        _refuse_internal(principal, "write")
    ev, reason = automations.send_outbound(
        db, contact, body.body, type_=EventType[body.type], subject=body.subject,
        pictures=pictures)
    if ev is not None and EventType[body.type] not in INTERNAL_TYPES:
        # A person answered the customer: an agent set to sleep on a staff reply stops on
        # this conversation, and its pending follow-up is cancelled (2026-09-15).
        ai_triggers.staff_replied(db, contact.id, "a staff member sent a message")
    db.commit()
    if ev is None:
        # 201 with suppressed=True, not an error: the request was well-formed and
        # the outcome is a business rule, matching how the existing routes report
        # {"automation": "suppressed: contact is on DND"}.
        #
        # The DRAFTS ARE LEFT ALONE. A suppressed send wrote no message, so the pictures
        # are still attached in the composer and the operator can fix the number and press
        # send again without picking them a second time.
        return {"suppressed": True, "reason": reason, "id": None,
                "conversation_id": None, "attachments": []}
    message_media.attach_sent(db, ev, pictures)
    db.commit()
    db.refresh(ev)
    return {"suppressed": False, "reason": reason, "id": ev.id,
            "conversation_id": ev.conversation_id, "type": ev.type.value,
            "direction": ev.direction.value, "occurred_at": ev.occurred_at,
            "body": ev.body, "subject": ev.subject,
            "attachments": [message_media.public(a) for a in pictures],
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
    contact = assigned_access.get_contact(db, principal, contact_id)
    return _send_to_contact(db, contact, body, principal)


@app.post("/api/conversations/{conv_id}/messages", status_code=201)
def send_to_conversation(conv_id: int, body: MessageSend,
                         db: Session = Depends(get_db),
                         principal: auth.Principal = auth.ANY_USER):
    """Send on an open thread — the shape the Conversations composer uses."""
    conv = assigned_access.get_conversation(db, principal, conv_id)
    contact = db.get(Contact, conv.contact_id)
    if not contact:
        raise HTTPException(404, "conversation has no contact")
    return _send_to_contact(db, contact, body, principal)


class CallOptions(BaseModel):
    """The optional body of every "place a call" route (2026-09-14).

    `ring_browser`: ring the SIGNED-IN USER'S OWN browser phone first, instead of the
    binding's default operator. The browser sends it only while its phone is
    registered, so the leg owen-main rings first is the tab that pressed Call, and
    that tab answers it by itself (`frontend/src/lib/outboundIntent.ts`).

    It is a flag, not an operator name. Who gets rung is the principal's own email —
    the identity `/api/softphone/credentials` registers that browser as — and never a
    value the client chose: naming somebody else's operator would ring their desk
    with a call they did not place.
    """

    ring_browser: bool = False


def _operator_for(principal: auth.Principal, opts: CallOptions | None) -> str | None:
    """owen-main slugs whatever it is given with the same `operator_slug` its
    credential minting uses, so the email rings exactly the endpoint the browser
    registered as. None keeps owen-main's default (the binding's operator)."""
    if opts is not None and opts.ring_browser and principal.email:
        return principal.email
    return None


def _dial(number: str, operator: str | None = None
          ) -> tuple[dict | None, crmlink.LinkResult | None]:
    """Ask owen-main to ring `number`. `(refusal, None)` or `(None, result)`.

    The ONE dialling path, shared by a contact, a number-only thread and the
    Conversations dialer, so calling a number nobody has saved passes exactly the
    gates calling a contact does: the CRM link must be armed here, and owen-main
    then applies its own kill switch, `CRM_LINK_ALLOWLIST` (empty allows nothing),
    the bound-DID check and its block list. Nothing on this side widens or skips
    any of them.
    """
    if not crmlink.configured():
        return {"placed": False, "id": None,
                "reason": "Calling is not switched on — this CRM is not connected "
                          "to the phone system yet."}, None
    result = crmlink.place_call(to_number=number, operator=operator)
    if not result.ok:
        return {"placed": False, "id": None, "reason": result.reason}, None
    return None, result


def _place_call(db: Session, contact: Contact, operator: str | None = None) -> dict:
    """Ring the customer from the bound DID, via owen-main.

    owen-main rings an operator's phone first, then the customer, and bridges the two
    legs. With `operator` set (the caller's own browser phone, `CallOptions`) the
    first leg rings the tab that pressed Call, which answers it by itself; without it,
    owen-main rings the binding's default operator exactly as it always has.

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
    refused, result = _dial(contact.phone, operator)
    if refused:
        return refused

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
        body=_placed_body(),
        provider_ref=linkedid or None,
    )
    db.add(ev)
    conv.last_event_at = ev.occurred_at
    ai_triggers.staff_replied(db, contact.id, "a staff member called the customer")
    db.commit()
    db.refresh(ev)
    return {"placed": True, "id": ev.id, "conversation_id": conv.id,
            "reason": "Calling %s now — your phone will ring first."
                      % (format_phone(contact.phone) or contact.phone)}


# The outbound-call row's body. One sentence for every path that places a call.
def _placed_body() -> str:
    return ("Outbound call placed from %s. Ringing an operator, then the customer."
            % crmlink.current().from_number)


@app.post("/api/contacts/{contact_id}/call")
def call_contact(contact_id: int, body: CallOptions | None = None,
                 db: Session = Depends(get_db),
                 principal: auth.Principal = auth.ANY_USER):
    """Place a call to a contact.

    ANY_USER: a TECH may send a customer a message (CLAUDE.md), and ringing the
    customer they are already texting is the same kind of act, not a wider one.
    """
    contact = assigned_access.get_contact(db, principal, contact_id)
    return _place_call(db, contact, _operator_for(principal, body))


@app.post("/api/conversations/{conv_id}/call")
def call_conversation(conv_id: int, body: CallOptions | None = None,
                      db: Session = Depends(get_db),
                      principal: auth.Principal = auth.ANY_USER):
    """The shape the thread header's phone button uses."""
    conv = assigned_access.get_conversation(db, principal, conv_id)
    contact = db.get(Contact, conv.contact_id)
    if not contact:
        raise HTTPException(404, "conversation has no contact")
    return _place_call(db, contact, _operator_for(principal, body))


class DialIn(CallOptions):
    number: str = Field(max_length=40)


# Only North American numbers are dialled from the CRM: the dialer formats and
# validates a 10-digit NANP number, and the bound DID is a US BulkVS line. owen-main's
# allowlist still has the last word on which of those may actually be rung.
_DIALABLE_CHARS = re.compile(r"^[\d\s().+-]*$")


def dial_problem(raw: str) -> tuple[str | None, str | None]:
    """`(e164, None)` for a number the dialer may ring, else `(None, sentence)`.

    The same rules, in the same words, as `dialProblem` in `frontend/src/lib/dialPad.ts`
    — the browser explains before Call is pressed, this is the gate. Not
    `phones.normalize_phone`: its sentences are about SAVING a contact's number
    ("extensions are not stored...") and it accepts every country.
    """
    return _number_problem(raw, "call")


def text_problem(raw: str) -> tuple[str | None, str | None]:
    """`dial_problem`'s rules for "New message" (2026-09-15), in words about texting.
    The browser's twin is `textProblem` in `frontend/src/lib/dialPad.ts`."""
    return _number_problem(raw, "text")


def _number_problem(raw: str, verb: str) -> tuple[str | None, str | None]:
    called = "called" if verb == "call" else "texted"
    text = (raw or "").strip()
    if not text:
        return None, "Enter a number to %s — 10 digits, area code first." % verb
    if not _DIALABLE_CHARS.match(text):
        if verb == "call":
            return None, ("A number to call has only digits. Star and pound are for menus "
                          "once the call connects.")
        return None, "A number to text has only digits."
    digits = re.sub(r"\D", "", text)
    if text.startswith("+") and not digits.startswith("1"):
        return None, ("Only US and Canadian numbers can be %s from here — "
                      "enter 10 digits, area code first." % called)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return None, ("Only US and Canadian numbers can be %s from here — "
                      "enter 10 digits, area code first." % called
                      if len(digits) > 10 else
                      "That number is too short — enter all 10 digits, area code first.")
    if digits[0] in "01" or digits[3] in "01":
        return None, ("That is not a valid US number — an area code and an exchange "
                      "cannot start with 0 or 1.")
    if number_threads.phone_key(crmlink.current().from_number) == digits:
        return None, "That is this CRM's own number — it cannot %s itself." % verb
    return "+1" + digits, None


ONLY_THEIR_CUSTOMERS = ("With “Only assigned data” on you can call the customers on your own "
                        "jobs, and this number is not one of them.")


@app.post("/api/calls/dial")
def dial_number(body: DialIn, db: Session = Depends(get_db),
                principal: auth.Principal = auth.ANY_USER):
    """Call ANY number — the Conversations page's "Call a number" dialer (2026-09-14).

    Not a second dialling path. The number is normalised, then:

      * a CONTACT holds it (last ten digits, lowest id — `contact_holding`): this is
        `POST /api/contacts/{id}/call`, byte for byte, DND refusal included;
      * nobody holds it: `_dial`, and only once owen-main has ACCEPTED the call is its
        number-only thread found or created and the call logged there, exactly as
        `POST /api/number-threads/{id}/call` logs one. No contact is ever created.

    Every refusal is a 200 with `placed: false` and a sentence, like the routes it
    reuses, and writes nothing — not even an empty thread for a number that was never
    rung.
    """
    number, problem = dial_problem(body.number)
    if problem:
        return {"placed": False, "id": None, "number": None, "reason": problem}

    operator = _operator_for(principal, body)
    contact = number_threads.contact_holding(db, number)
    s = assigned_access.scope(db, principal)
    if s.restricted and (contact is None or not s.sees_contact(contact.id)):
        # "Only assigned data" (2026-09-15): the dialer rings only the customers on the
        # caller's own jobs. The same sentence whether or not someone else's contact
        # holds the number, so it cannot be used to find out; nothing is written.
        return {"placed": False, "id": None, "number": number, "contact_id": None,
                "contact_name": None, "number_thread_id": None,
                "reason": ONLY_THEIR_CUSTOMERS}
    if contact is not None:
        out = _place_call(db, contact, operator)
        return {**out, "number": number, "contact_id": contact.id,
                "contact_name": contact.name or None, "number_thread_id": None}

    refused, result = _dial(number, operator)
    if refused:
        return {**refused, "number": number, "contact_id": None,
                "contact_name": None, "number_thread_id": None}
    thread, _created = number_threads.thread_for_number(db, number)
    ev = _log_number_call(db, thread, result)
    return {"placed": True, "id": ev.id, "number": number, "contact_id": None,
            "contact_name": None, "conversation_id": None,
            "number_thread_id": thread.id,
            "reason": "Calling %s now — your phone will ring first."
                      % (format_phone(number) or number)}


ONLY_THEIR_CUSTOMERS_TEXT = ("With “Only assigned data” on you can text the customers on your "
                             "own jobs, and this number is not one of them.")


class NewMessageIn(BaseModel):
    """"New message" to any number. Deliberately no `from_number`: every text leaves on
    the bound BulkVS DID, and Quo (OpenPhone) is never a sender (2026-09-11). A client
    that sends one anyway has it ignored, like any unknown field."""
    number: str = Field(max_length=40)
    body: str = Field(max_length=1600)
    # Draft pictures, exactly as the thread composer sends them (2026-09-16).
    attachment_ids: list[int] = []


@app.post("/api/messages/new")
def new_message(body: NewMessageIn, db: Session = Depends(get_db),
                principal: auth.Principal = auth.ANY_USER):
    """Text ANY number — the Conversations page's "New message" (2026-09-15).

    Not a second send path; the twin of `POST /api/calls/dial`. The number is
    normalised by `text_problem`, then:

      * a CONTACT holds it (last ten digits, lowest id): this is
        `POST /api/contacts/{id}/messages`, DND suppression included, on their thread;
      * nobody holds it: its NUMBER-ONLY thread is found or created and the text goes
        through `send_outbound_to_number`, exactly as the thread's own composer sends.
        No contact is ever created.

    Refusals (a bad number, "Only assigned data") are a 200 with `recorded: false` and a
    sentence, and write nothing — not even an empty thread. A send the phone system
    refuses or cannot take IS recorded on the thread, like any composer send.
    """
    pictures = message_media.take_drafts(db, principal, body.attachment_ids)
    if not body.body.strip() and not pictures:
        raise HTTPException(400, "a message needs a body")
    none = {"recorded": False, "id": None, "kind": None, "key": None, "contact_id": None,
            "conversation_id": None, "number_thread_id": None, "delivery_status": None,
            "delivery_detail": None, "attachments": []}
    number, problem = text_problem(body.number)
    if problem:
        return {**none, "number": None, "reason": problem}

    contact = number_threads.contact_holding(db, number)
    s = assigned_access.scope(db, principal)
    if s.restricted and (contact is None or not s.sees_contact(contact.id)):
        # The same sentence whether or not someone else's contact holds the number, so
        # it cannot be used to find out who does; nothing is written.
        return {**none, "number": number, "reason": ONLY_THEIR_CUSTOMERS_TEXT}

    if contact is not None:
        out = _send_to_contact(db, contact,
                               MessageSend(body=body.body, type="SMS",
                                           attachment_ids=body.attachment_ids),
                               principal)
        conv_id = out.get("conversation_id")
        if conv_id is None:
            # Suppressed (DND, or no phone): nothing was written, so nothing to open —
            # except the thread the contact may already have.
            existing = db.scalar(select(Conversation.id).where(
                Conversation.contact_id == contact.id))
            conv_id = existing
        return {**out, "recorded": not out["suppressed"], "number": number, "kind": "contact",
                "key": ("c%d" % conv_id) if conv_id is not None else None,
                "contact_id": contact.id, "contact_name": contact.name or None,
                "conversation_id": conv_id, "number_thread_id": None}

    thread, created = number_threads.thread_for_number(db, number)
    ev, reason = automations.send_outbound_to_number(db, thread, body.body,
                                                     pictures=pictures)
    if ev is None:
        if created:
            db.delete(thread)
        db.commit()
        return {**none, "number": number, "reason": reason, "suppressed": True}
    message_media.attach_sent(db, ev, pictures)
    db.commit()
    db.refresh(ev)
    return {"recorded": True, "suppressed": False, "reason": reason, "id": ev.id,
            "number": number, "kind": "number", "key": "n%d" % thread.id,
            "contact_id": None, "contact_name": None, "conversation_id": None,
            "number_thread_id": thread.id, "thread_created": created,
            "type": ev.type.value, "direction": ev.direction.value,
            "occurred_at": ev.occurred_at, "body": ev.body,
            "attachments": [message_media.public(a) for a in pictures],
            "delivery_status": ev.delivery_status.value if ev.delivery_status else None,
            "delivery_detail": ev.delivery_detail}


@app.get("/api/automations")
def list_automations(principal: auth.Principal = auth.ANY_USER):
    """The four hard-coded rules and whether each is on, with the reason in words
    (2026-09-15). Read-only: there is no switch to flip from the browser, because the
    owner's decision is that nothing texts a customer by itself."""
    return {"rules": automations.rules()}


class ConversationPatch(BaseModel):
    read: bool | None = None
    starred: bool | None = None


@app.patch("/api/conversations/{conv_id}")
def update_conversation(conv_id: int, body: ConversationPatch,
                        db: Session = Depends(get_db),
                        principal: auth.Principal = auth.ANY_USER):
    conv = assigned_access.get_conversation(db, principal, conv_id)
    data = body.model_dump(exclude_unset=True)
    if "read" in data:
        conv.unread_count = 0 if data["read"] else max(1, conv.unread_count)
    if "starred" in data:
        conv.starred = data["starred"]
    db.commit()
    db.refresh(conv)
    # Same shape as a row from GET /api/conversations, so the browser can drop this
    # straight into the cached list without the row it patches losing a field.
    return _conversation_row(conv, _event_counts(db, conv.id).get(conv.id, 0))


# ---------- number-only threads (2026-09-13) ----------
#
# The routes a contact thread has, for a thread that belongs to a number nobody has
# saved (app/number_threads.py). Each one applies the SAME rule its contact-thread
# twin applies — the same role gate, the same internal-note predicate, the same
# transport and the same dialling path — because the two kinds of thread share one
# inbox and an operator must not be able to tell them apart by what they are allowed
# to do. What differs is only what cannot exist: there is no contact, so there is no
# DND to honour and no email to send.

def _number_thread(db: Session, principal: auth.Principal, thread_id: int) -> NumberThread:
    """404 for a missing thread — and for any number thread at all when the caller has
    "Only assigned data" on: a number nobody has saved is nobody's job."""
    assigned_access.refuse_number_threads(principal)
    t = db.get(NumberThread, thread_id)
    if not t:
        raise HTTPException(404, "number thread not found")
    return t


@app.get("/api/number-threads/{thread_id}")
def get_number_thread(thread_id: int, db: Session = Depends(get_db),
                      principal: auth.Principal = auth.ANY_USER):
    t = _number_thread(db, principal, thread_id)
    return _number_thread_row(t, _number_event_counts(db, t.id).get(t.id, 0))


@app.get("/api/number-threads/{thread_id}/events", response_model=list[EventOut])
def number_thread_events(thread_id: int, db: Session = Depends(get_db),
                         filter: str = "all",
                         principal: auth.Principal = auth.ANY_USER):
    """The thread view. Internal notes follow the STAFF-only rule exactly as on a
    contact thread — the same function builds both."""
    _number_thread(db, principal, thread_id)
    return _thread_events(NumberThreadEvent,
                          NumberThreadEvent.number_thread_id == thread_id,
                          db, filter, principal)


@app.patch("/api/number-threads/{thread_id}")
def update_number_thread(thread_id: int, body: ConversationPatch,
                         db: Session = Depends(get_db),
                         principal: auth.Principal = auth.ANY_USER):
    """Read / unread / star — `PATCH /api/conversations/{id}`'s rule, verbatim."""
    t = _number_thread(db, principal, thread_id)
    data = body.model_dump(exclude_unset=True)
    if "read" in data:
        t.unread_count = 0 if data["read"] else max(1, t.unread_count)
    if "starred" in data:
        t.starred = data["starred"]
    db.commit()
    db.refresh(t)
    return _number_thread_row(t, _number_event_counts(db, t.id).get(t.id, 0))


@app.delete("/api/number-threads/{thread_id}")
def delete_number_thread(thread_id: int, db: Session = Depends(get_db),
                         principal: auth.Principal = auth.ADMIN):
    """Delete the thread and its events. ADMIN, like a contact thread's delete.
    There is no contact to keep, so there is nothing else to leave alone."""
    t = _number_thread(db, principal, thread_id)
    events = _number_event_counts(db, t.id).get(t.id, 0)
    phone = t.phone
    db.delete(t)
    db.commit()
    return {"deleted": thread_id, "phone": phone, "events_deleted": events}


@app.post("/api/number-threads/{thread_id}/messages", status_code=201)
def send_to_number_thread(thread_id: int, body: MessageSend,
                          db: Session = Depends(get_db),
                          principal: auth.Principal = auth.ANY_USER):
    """Text the number, or note on its thread — through the SAME transport a contact
    text uses, so it is logged, refused or queued exactly as that would be."""
    t = _number_thread(db, principal, thread_id)
    pictures = _pictures_for(db, body, principal)
    if not body.body.strip() and not pictures:
        raise HTTPException(400, "a message needs a body")
    type_ = EventType[body.type]
    if type_ in INTERNAL_TYPES and not _sees_internal(principal):
        _refuse_internal(principal, "write")
    ev, reason = automations.send_outbound_to_number(db, t, body.body, type_=type_,
                                                     pictures=pictures)
    if ev is None:
        db.commit()
        return {"suppressed": True, "reason": reason, "id": None,
                "conversation_id": None, "number_thread_id": t.id, "attachments": []}
    message_media.attach_sent(db, ev, pictures)
    db.commit()
    db.refresh(ev)
    return {"suppressed": False, "reason": reason, "id": ev.id,
            "conversation_id": None, "number_thread_id": t.id,
            "type": ev.type.value, "direction": ev.direction.value,
            "occurred_at": ev.occurred_at, "body": ev.body,
            "attachments": [message_media.public(a) for a in pictures],
            "delivery_status": (ev.delivery_status.value
                                if ev.delivery_status else None),
            "delivery_detail": ev.delivery_detail}


def _log_number_call(db: Session, t: NumberThread,
                     result: crmlink.LinkResult) -> NumberThreadEvent:
    """Record an outbound call owen-main accepted, on a number-only thread."""
    linkedid = str((result.data or {}).get("linkedid") or "")
    ev = NumberThreadEvent(
        number_thread_id=t.id, type=EventType.CALL, direction=Direction.OUTBOUND,
        occurred_at=datetime.now(UTC),
        body=_placed_body(),
        provider_ref=linkedid or None,
        source_system=automations.SENT_SOURCE_SYSTEM,
        source_number=crmlink.current().from_number,
    )
    db.add(ev)
    t.last_event_at = ev.occurred_at
    db.commit()
    db.refresh(ev)
    return ev


@app.post("/api/number-threads/{thread_id}/call")
def call_number_thread(thread_id: int, body: CallOptions | None = None,
                       db: Session = Depends(get_db),
                       principal: auth.Principal = auth.ANY_USER):
    """Ring the number from the bound DID, through the same `_dial` a contact call
    uses — owen-main's allowlist and block list apply unchanged."""
    t = _number_thread(db, principal, thread_id)
    refused, result = _dial(t.phone, _operator_for(principal, body))
    if refused:
        return refused
    ev = _log_number_call(db, t, result)
    return {"placed": True, "id": ev.id, "number_thread_id": t.id,
            "reason": "Calling %s now — your phone will ring first."
                      % (format_phone(t.phone) or t.phone)}


# ---------- tags ----------

@app.get("/api/tags")
def list_tags(db: Session = Depends(get_db),
              principal: auth.Principal = auth.ANY_USER):
    # With "Only assigned data" on, a count is of the caller's own contacts: how many
    # people carry a tag across the whole book is not theirs to know.
    counts = dict(db.execute(assigned_access.contacts(
        select(ContactTag.tag_id, func.count()), assigned_access.scope(db, principal),
        ContactTag.contact_id).group_by(ContactTag.tag_id)).all())
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
    email: str = Field(max_length=255)
    name: str = Field(max_length=120)
    password: str
    role: Literal["ADMIN", "DISPATCHER", "TECH"] = "TECH"
    # GoHighLevel's "Only assigned data". Omitted: ON for a TECH, OFF for anyone else —
    # the owner's default (2026-09-15). Never ON for an ADMIN.
    only_assigned_data: bool | None = None
    phone: str | None = Field(None, max_length=40)

    _phone = field_validator("phone", mode="after")(
        lambda v: store_phone(_blank_to_none(v)))


class UserPatch(BaseModel):
    name: str | None = Field(None, max_length=120)
    email: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=40)
    role: Literal["ADMIN", "DISPATCHER", "TECH"] | None = None
    is_active: bool | None = None
    password: str | None = None
    only_assigned_data: bool | None = None

    _phone = field_validator("phone", mode="after")(
        lambda v: store_phone(_blank_to_none(v)))


ADMIN_NEVER_RESTRICTED = ("an admin always sees everything — “Only assigned data” "
                          "applies to dispatchers and technicians")
MACHINE_NO_LOGIN = ("this is a machine account (an API token with no password, e.g. the "
                    "telephony feed) — it can never be given a password to sign in with")


def is_machine_account(u: User) -> bool:
    """No password: it exists to own an API token (`app.bootstrap --machine`). A human
    with no password yet is set up with `app.bootstrap set-password`, not My Staff."""
    return not u.password_hash


def _clean_staff_name(name: str | None) -> str:
    cleaned = " ".join((name or "").split())
    if not cleaned:
        raise HTTPException(400, "a user needs a name")
    return cleaned


def _clean_staff_email(db: Session, email: str | None, exclude_id: int | None = None) -> str:
    cleaned = (email or "").strip().lower()
    if not EMAIL_RE.match(cleaned):
        raise HTTPException(400, "not a valid email address")
    clash = select(User.id).where(func.lower(User.email) == cleaned)
    if exclude_id is not None:
        clash = clash.where(User.id != exclude_id)
    if db.scalar(clash) is not None:
        raise HTTPException(400, "a user with that email already exists")
    return cleaned


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
            "role": u.role.value, "is_active": u.is_active,
            "only_assigned_data": bool(u.only_assigned_data),
            "phone": u.phone, "phone_display": format_phone(u.phone),
            "must_change_password": bool(u.must_change_password),
            "machine": is_machine_account(u)}


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
    if body.new_password == body.current_password:
        raise HTTPException(400, "choose a password different from the current one")

    user.password_hash = auth.hash_password(body.new_password)
    # The admin-set password is gone, so the forced change is done (My Staff).
    user.must_change_password = False
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


def _technician_calendar(db: Session, u: User) -> dict:
    """A new TECHNICIAN's own calendar (My Staff, 2026-09-15), so a visit can be booked
    on it and "Only assigned data" has a calendar to show them.

    * Never a second one: a user who already owns a calendar keeps it and gets none.
    * Named "<Name>". If a calendar of that exact name already exists — someone else's,
      or nobody's — it is left completely alone (handing it to the new user would give
      them every visit already booked on it) and the new one is "<Name> (2)", "(3)"...
    """
    owned = db.scalar(select(Calendar).where(Calendar.user_id == u.id))
    if owned is not None:
        return {"id": owned.id, "name": owned.name, "created": False}
    name, n = u.name, 1
    while db.scalar(select(Calendar.id).where(func.lower(Calendar.name) == name.lower())):
        n += 1
        name = "%s (%d)" % (u.name, n)
    cal = Calendar(name=name[:160], user_id=u.id)
    db.add(cal)
    db.flush()
    return {"id": cal.id, "name": cal.name, "created": True,
            "renamed_from": u.name if name != u.name else None}


def _active_admins(db: Session) -> int:
    """Admins who can sign in and manage staff: active, and not machine accounts (no
    password) — a token-only ADMIN cannot open My Staff to put things right."""
    return db.scalar(select(func.count(User.id)).where(
        User.role == Role.ADMIN, User.is_active.is_(True),
        User.password_hash != "")) or 0


@app.post("/api/users", status_code=201)
def create_user(body: UserCreate, db: Session = Depends(get_db),
                _: auth.Principal = auth.ADMIN):
    """My Staff's "+ Add User". The admin types a FIRST password; the user must replace
    it at their first sign-in (`must_change_password`). No email invitation is sent.
    A technician gets their own calendar in the same transaction."""
    email = _clean_staff_email(db, body.email)
    name = _clean_staff_name(body.name)
    if len(body.password) < 8:
        raise HTTPException(400, "password must be at least 8 characters")
    restricted = (body.role == "TECH" if body.only_assigned_data is None
                  else body.only_assigned_data)
    if restricted and body.role == "ADMIN":
        raise HTTPException(400, ADMIN_NEVER_RESTRICTED)
    u = User(email=email, name=name, role=Role[body.role],
             password_hash=auth.hash_password(body.password),
             only_assigned_data=restricted, phone=body.phone,
             must_change_password=True)
    db.add(u)
    db.flush()
    calendar = _technician_calendar(db, u) if u.role is Role.TECH else None
    db.commit()
    db.refresh(u)
    return {**_user_public(u), "calendar": calendar}


@app.patch("/api/users/{user_id}")
def update_user(user_id: int, body: UserPatch, db: Session = Depends(get_db),
                principal: auth.Principal = auth.ADMIN):
    """My Staff's Edit, Deactivate and Reactivate. Nothing here deletes a user.

    * Deactivating ends every browser session now (token_version) and revokes every
      API token a PERSON holds; their name stays on every job, note and task.
      A MACHINE account's tokens are refused while it is inactive but not destroyed —
      a token's secret is shown once, and destroying the telephony feed's is how the
      live credential was lost before (DECISIONS.md).
    * An admin cannot deactivate or demote themselves, and the last active ADMIN can
      never be deactivated or demoted.
    * A password set here is a RESET: the user must choose their own at next sign-in.
      A machine account can never be given one.
    """
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "user not found")
    data = body.model_dump(exclude_unset=True)
    for key in ("name", "email", "role", "is_active", "only_assigned_data"):
        if key in data and data[key] is None:
            data.pop(key)

    if "name" in data:
        data["name"] = _clean_staff_name(data["name"])
    if "email" in data:
        data["email"] = _clean_staff_email(db, data["email"], exclude_id=u.id)
    password = data.pop("password", None)
    if password is not None:
        if is_machine_account(u):
            raise HTTPException(400, MACHINE_NO_LOGIN)
        if len(password) < 8:
            raise HTTPException(400, "password must be at least 8 characters")
    if "role" in data:
        data["role"] = Role[data["role"]]
    new_role = data.get("role", u.role)
    deactivating = data.get("is_active") is False and u.is_active
    demoting = u.role is Role.ADMIN and new_role is not Role.ADMIN

    # Checked before anything is written, so a refusal changes nothing.
    if u.id == principal.user_id and deactivating:
        raise HTTPException(400, "you cannot deactivate your own account")
    if u.id == principal.user_id and demoting:
        raise HTTPException(400, "you cannot change your own role away from admin")
    if ((deactivating or demoting) and u.role is Role.ADMIN and u.is_active
            and not is_machine_account(u) and _active_admins(db) <= 1):
        raise HTTPException(409, "%s is the last active admin — make someone else an "
                                 "admin first" % u.name)
    if new_role is Role.ADMIN:
        if data.get("only_assigned_data"):
            raise HTTPException(400, ADMIN_NEVER_RESTRICTED)
        # Promoted to ADMIN: the switch means nothing there, so it is cleared rather
        # than left to silently re-apply on a later demotion.
        data["only_assigned_data"] = False

    if password is not None:
        u.password_hash = auth.hash_password(password)
        u.must_change_password = True
    for k, v in data.items():
        setattr(u, k, v)
    if deactivating and not is_machine_account(u):
        now = datetime.now(UTC)
        for token in db.scalars(select(ApiToken).where(
                ApiToken.user_id == u.id, ApiToken.revoked_at.is_(None))).all():
            token.revoked_at = now

    # A demotion or deactivation must take effect now, not in up to 15 minutes.
    # "Only assigned data" is read per request (auth.Principal), so it needs no bump.
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

    `media_writable` (2026-09-16) is the same kind of question for pictures: the volume
    that holds them is the one thing in this stack a deploy can silently get wrong, and
    the symptom — every inbound photograph reading "Picture unavailable" a week later —
    looks like a carrier problem rather than a missing mount. It reports whether the
    directory can be written, and deliberately NOT where it is: a path is a fact about
    the host, and this route is unauthenticated.
    """
    return {"ok": True, "transport": type(get_transport()).__name__,
            "crm_link": crmlink.configured(),
            "media_writable": _media_writable()}


def _media_writable() -> bool:
    """Can we actually put a picture on disk? Asked by writing, because a directory that
    exists and is read-only fails in exactly the way that matters and `os.access` on a
    docker volume has lied about it before."""
    try:
        root = attachments.media_root()
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".writable"
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False

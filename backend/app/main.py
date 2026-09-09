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
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from . import auth, automations
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
    DeliveryStatus,
    Direction,
    EventType,
    Job,
    Opportunity,
    Pipeline,
    Role,
    Stage,
    Tag,
    User,
)
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
    delivery_status: str | None


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
        like = "%%%s%%" % q
        stmt = stmt.where(or_(Contact.first_name.ilike(like),
                              Contact.last_name.ilike(like),
                              # Searching a full name is the obvious thing to type,
                              # and matching the columns separately never does it:
                              # "jane doe" is in neither first_name nor last_name.
                              # Renders as || on both Postgres and SQLite.
                              (Contact.first_name + " "
                               + Contact.last_name).ilike(like),
                              Contact.email.ilike(like),
                              Contact.phone.ilike(like),
                              Contact.business_name.ilike(like)))

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
            email=r.email, phone=r.phone, business_name=r.business_name,
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
        "business_name": c.business_name,
        "source": c.source,
        "date_of_birth": c.date_of_birth,
        "contact_type": c.contact_type,
        "dnd": c.dnd,
        "created_by": c.created_by,
        "created_at": c.created_at,
        "owner_id": c.owner_id,
        "owner_name": c.owner.name if c.owner else None,
        "tags": [{"id": ct.tag.id, "name": ct.tag.name, "color": ct.tag.color}
                 for ct in c.tags if ct.tag],
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


@app.get("/api/opportunities")
def list_opportunities(
    pipeline_id: int,
    db: Session = Depends(get_db),
    q: str | None = None,
    status: str = "open",
    _: auth.Principal = auth.ANY_USER):
    """`status` mirrors GHL's measured default advanced filter: Status is any of Open."""
    stmt = (select(Opportunity)
            .options(selectinload(Opportunity.contact))
            .where(Opportunity.pipeline_id == pipeline_id))
    if status != "all":
        stmt = stmt.where(Opportunity.status == status)
    if q:
        stmt = stmt.where(Opportunity.title.ilike(contains(q), escape=LIKE_ESCAPE))
    rows = db.scalars(stmt.order_by(Opportunity.position)).all()
    return [{"id": o.id, "title": o.title, "value_cents": o.value_cents,
             "stage_id": o.stage_id, "status": o.status,
             "contact_name": o.contact.name if o.contact else None,
             "business_name": o.contact.business_name if o.contact else None,
             "source": o.contact.source if o.contact else None,
             "updated_at": o.updated_at} for o in rows]


class OpportunityMove(BaseModel):
    stage_id: int
    position: int = 0


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
        .order_by(Opportunity.position)).all()
    ordered = [*siblings[:body.position], o, *siblings[body.position:]]
    for i, s in enumerate(ordered):
        s.position = i
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
    """Ingest endpoint for the telephony project.

    Inbound calls and messages are plain data we own; they do NOT go through
    MessageTransport, which governs outbound side effects only.
    """
    contact_id: int
    type: Literal["SMS", "CALL", "EMAIL", "INTERNAL_COMMENT"]
    direction: Literal["INBOUND", "OUTBOUND"] = "INBOUND"
    body: str | None = None
    duration_seconds: int | None = None
    # CALL only. The Call report's "Call by status" donut is built from this, so a
    # feed that cannot send it leaves every ingested call with an unknown outcome.
    call_status: str | None = None
    recording_url: str | None = None
    provider_ref: str | None = None


@app.post("/api/events", status_code=201)
def ingest_event(body: EventIngest, db: Session = Depends(get_db),
                 _: auth.Principal = auth.EVENTS_INGEST):
    contact = db.get(Contact, body.contact_id)
    if not contact:
        raise HTTPException(404, "contact not found")
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
    return {"id": ev.id, "conversation_id": conv.id, "automation": outcome}


class AppointmentCreate(BaseModel):
    title: str
    starts_at: datetime
    ends_at: datetime
    contact_id: int | None = None
    assigned_user_id: int | None = None
    # Was missing, so every appointment created through the API landed on no
    # calendar at all and never appeared under a calendar filter.
    calendar_id: int | None = None
    notes: str | None = None


@app.post("/api/appointments", status_code=201)
def create_appointment(body: AppointmentCreate, db: Session = Depends(get_db),
                       _: auth.Principal = auth.STAFF):
    if body.ends_at <= body.starts_at:
        raise HTTPException(400, "ends_at must be after starts_at")
    a = Appointment(**body.model_dump())
    db.add(a)
    db.flush()
    # Rule 3: booked -> reminders at T-24h and T-1h.
    outcome = automations.on_appointment_booked(db, a)
    db.commit()
    db.refresh(a)
    return {"id": a.id, "title": a.title, "starts_at": a.starts_at,
            "ends_at": a.ends_at, "automation": outcome}


def _opp_detail(o: Opportunity) -> dict:
    """Shape mirrors GHL's measured opportunity detail screen."""
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
    }


@app.get("/api/opportunities/{opp_id}")
def get_opportunity(opp_id: int, db: Session = Depends(get_db),
                    _: auth.Principal = auth.ANY_USER):
    o = db.get(Opportunity, opp_id)
    if not o:
        raise HTTPException(404, "opportunity not found")
    return _opp_detail(o)


class OpportunityPatch(BaseModel):
    """Measured GHL statuses: Open / Won / Lost / Abandoned."""
    title: str | None = None
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
    if "title" in data and not (data["title"] or "").strip():
        # "Opportunity name" is the only field GHL marks required (red *).
        raise HTTPException(400, "opportunity name is required")
    if "stage_id" in data:
        stage = db.get(Stage, data["stage_id"])
        if not stage or stage.pipeline_id != o.pipeline_id:
            raise HTTPException(400, "stage is not in this opportunity's pipeline")
    if ("owner_id" in data and data["owner_id"] is not None
            and not db.get(User, data["owner_id"])):
        raise HTTPException(400, "unknown owner_id")
    if "value_cents" in data and data["value_cents"] is not None \
            and data["value_cents"] < 0:
        raise HTTPException(400, "value cannot be negative")
    if data.get("custom_fields") is not None:
        # `owen_call_id` is the join key to the telephony project (DECISIONS.md);
        # rewriting it breaks attribution history for calls this form knows nothing
        # about. Nothing legitimate sets it here — telephony ingests through
        # POST /api/events. The detail form posts the whole custom_fields object
        # back on every save, so an unchanged echo is fine and only a real change
        # is refused. Setting it where there was none is still allowed.
        was = (o.custom_fields or {}).get("owen_call_id")
        now = data["custom_fields"].get("owen_call_id")
        if was is not None and now != was:
            raise HTTPException(
                400, "owen_call_id is the telephony join key and cannot be changed here")

    old_stage_id = o.stage_id
    for k, v in data.items():
        setattr(o, k, v)
    db.flush()
    outcome = automations.on_opportunity_stage_changed(db, o, old_stage_id)
    db.commit()
    db.refresh(o)
    return {**_opp_detail(o), "automation": outcome}


class OpportunityCreate(BaseModel):
    title: str
    pipeline_id: int
    stage_id: int
    contact_id: int | None = None
    value_cents: int = 0


@app.post("/api/opportunities", status_code=201)
def create_opportunity(body: OpportunityCreate, db: Session = Depends(get_db),
                       _: auth.Principal = auth.STAFF):
    stage = db.get(Stage, body.stage_id)
    if not stage or stage.pipeline_id != body.pipeline_id:
        raise HTTPException(400, "stage is not in that pipeline")
    n = db.scalar(select(func.count(Opportunity.id))
                  .where(Opportunity.stage_id == body.stage_id)) or 0
    o = Opportunity(title=body.title, pipeline_id=body.pipeline_id,
                    stage_id=body.stage_id, contact_id=body.contact_id,
                    value_cents=body.value_cents, position=n)
    db.add(o)
    db.commit()
    db.refresh(o)
    return {"id": o.id, "title": o.title, "stage_id": o.stage_id}


# ---------- conversations ----------

@app.get("/api/conversations")
def list_conversations(
    db: Session = Depends(get_db),
    tab: Literal["unread", "all", "recent", "starred"] = "all",
    sort: str = "latest",
    _: auth.Principal = auth.ANY_USER):
    """Tabs and sort options are the measured GHL set.

    Tabs:  Unread | All | Recent | Starred
    Sort:  Latest/Oldest - All Messages, Latest/Oldest - Manual Messages,
           Longest SLA Overdue, Next SLA Target
    SLA sorts are accepted but fall back to recency: this account has no SLA
    configured ("SLA has not been set. Go to Settings to configure."), so there is
    nothing measured to replicate yet.
    """
    stmt = select(Conversation).options(selectinload(Conversation.contact))
    if tab == "unread":
        stmt = stmt.where(Conversation.unread_count > 0)
    elif tab == "starred":
        stmt = stmt.where(Conversation.starred.is_(True))

    oldest = sort.startswith("oldest")
    col = Conversation.last_event_at
    stmt = stmt.order_by(col.asc() if oldest else col.desc())
    rows = db.scalars(stmt).all()
    return [{"id": c.id, "contact_id": c.contact_id,
             "contact_name": c.contact.name if c.contact else None,
             "contact_phone": c.contact.phone if c.contact else None,
             "last_event_at": c.last_event_at, "unread_count": c.unread_count,
             "starred": c.starred} for c in rows]


@app.get("/api/conversations/{conv_id}/events", response_model=list[EventOut])
def conversation_events(
    conv_id: int,
    db: Session = Depends(get_db),
    filter: str = "all",
    _: auth.Principal = auth.ANY_USER):
    """`filter` mirrors GHL's measured "Filter messages" menu: all | conversations
    | activities | or a specific EventType name."""
    stmt = (select(ConversationEvent)
            .where(ConversationEvent.conversation_id == conv_id))

    if filter == "conversations":
        stmt = stmt.where(ConversationEvent.type.in_(CONVERSATION_TYPES))
    elif filter == "activities":
        stmt = stmt.where(ConversationEvent.type.in_(ACTIVITY_TYPES))
    elif filter != "all":
        try:
            stmt = stmt.where(ConversationEvent.type == EventType[filter.upper()])
        except KeyError:
            raise HTTPException(400, "unknown filter %r" % filter) from None

    rows = db.scalars(stmt.order_by(ConversationEvent.occurred_at)).all()
    return [EventOut(id=e.id, type=e.type.value, direction=e.direction.value,
                     occurred_at=e.occurred_at, body=e.body, subject=e.subject,
                     duration_seconds=e.duration_seconds,
                     recording_url=e.recording_url,
                     delivery_status=e.delivery_status.value
                     if e.delivery_status else None) for e in rows]


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


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db), pipeline_id: int | None = None,
              _: auth.Principal = auth.STAFF):
    """Measured GHL dashboard cards: Opportunity status (Won/Open/Lost + total),
    Opportunity value (Total vs Won revenue), Conversion rate."""
    stmt = select(Opportunity)
    if pipeline_id:
        stmt = stmt.where(Opportunity.pipeline_id == pipeline_id)
    opps = db.scalars(stmt).all()

    by_status = {"won": 0, "open": 0, "lost": 0, "abandoned": 0}
    total_value = won_value = 0
    for o in opps:
        by_status[o.status] = by_status.get(o.status, 0) + 1
        total_value += o.value_cents
        if o.status == "won":
            won_value += o.value_cents

    decided = by_status["won"] + by_status["lost"]
    conversion = (by_status["won"] / decided * 100) if decided else 0.0
    return {
        "total": len(opps),
        "status": by_status,
        "total_value_cents": total_value,
        "won_value_cents": won_value,
        "conversion_rate": round(conversion, 2),
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
                                selectinload(Appointment.calendar))
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
    notes: str | None = None


@app.patch("/api/appointments/{appointment_id}")
def update_appointment(appointment_id: int, body: AppointmentPatch,
                       db: Session = Depends(get_db),
                       _: auth.Principal = auth.STAFF):
    """Edit a booking. Moving it reschedules the reminders.

    Rescheduling is the interesting case: the T-24h and T-1h jobs were queued
    against the OLD time, so they must be dropped and re-queued, or the customer
    is reminded about a slot that no longer exists.
    """
    a = db.get(Appointment, appointment_id)
    if not a:
        raise HTTPException(404, "appointment not found")

    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in APPOINTMENT_STATUSES:
        raise HTTPException(400, "unknown status %r — expected one of %s" % (
            data["status"], sorted(APPOINTMENT_STATUSES)))

    old_start = _aware(a.starts_at)
    for k, v in data.items():
        setattr(a, k, v)
    # Validate against the stored values, so patching only one end still checks.
    if _aware(a.ends_at) <= _aware(a.starts_at):
        raise HTTPException(400, "ends_at must be after starts_at")
    db.flush()

    outcome = "unchanged"
    if "starts_at" in data and _aware(a.starts_at) != old_start:
        # Drop reminders that have not run yet; the ones already sent stay as
        # history. Cancelled rather than deleted so the trail survives.
        pending = db.scalars(select(Job).where(
            Job.type == "appointment_reminder", Job.status == "pending")).all()
        for job in pending:
            if job.payload.get("appointment_id") == a.id:
                job.status = "cancelled"
        db.flush()
        outcome = automations.on_appointment_booked(db, a)

    db.commit()
    db.refresh(a)
    return {**_appointment_detail(a), "automation": outcome}


@app.delete("/api/appointments/{appointment_id}")
def cancel_appointment(appointment_id: int, db: Session = Depends(get_db),
                       _: auth.Principal = auth.STAFF):
    """Cancel, not delete.

    `_h_appointment_reminder` already re-checks status when the job runs, so a
    cancelled appointment suppresses its own pending reminders. Deleting the row
    would throw away the history for no benefit.
    """
    a = db.get(Appointment, appointment_id)
    if not a:
        raise HTTPException(404, "appointment not found")
    a.status = "cancelled"
    db.commit()
    return {"id": a.id, "status": a.status}


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


@app.delete("/api/opportunities/{opp_id}")
def delete_opportunity(opp_id: int, db: Session = Depends(get_db),
                       _: auth.Principal = auth.ADMIN):
    o = db.get(Opportunity, opp_id)
    if not o:
        raise HTTPException(404, "opportunity not found")
    db.delete(o)
    db.commit()
    return {"deleted": opp_id}


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
        like = "%%%s%%" % q
        # transcript as well as body: searching call transcripts is the whole
        # point of `ghl calls list --q`.
        stmt = stmt.where(or_(ConversationEvent.body.ilike(like),
                              ConversationEvent.transcript.ilike(like),
                              ConversationEvent.subject.ilike(like)))
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
    _: auth.Principal = auth.ANY_USER,
):
    """Search messages and notes across every conversation."""
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

    extra = []
    if delivery_status:
        extra.append(ConversationEvent.delivery_status
                     == DeliveryStatus[delivery_status.upper()])

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
    } for e, c in rows], total, page, page_size)


# ---------- outbound messaging ----------

class MessageSend(BaseModel):
    body: str
    type: Literal["SMS", "EMAIL", "NOTE", "INTERNAL_COMMENT"] = "SMS"
    subject: str | None = None


def _send_to_contact(db: Session, contact: Contact, body: MessageSend) -> dict:
    if not body.body.strip():
        raise HTTPException(400, "a message needs a body")
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
                                if ev.delivery_status else None)}


@app.post("/api/contacts/{contact_id}/messages", status_code=201)
def send_to_contact(contact_id: int, body: MessageSend,
                    db: Session = Depends(get_db),
                    _: auth.Principal = auth.ANY_USER):
    """Send to a contact, creating the thread if there isn't one.

    Contact-first because that is how the CLI addresses people. While
    LoggingTransport is the only transport, this records the intent and
    transmits nothing — `delivery_status` comes back LOGGED_ONLY.
    """
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(404, "contact not found")
    return _send_to_contact(db, contact, body)


@app.post("/api/conversations/{conv_id}/messages", status_code=201)
def send_to_conversation(conv_id: int, body: MessageSend,
                         db: Session = Depends(get_db),
                         _: auth.Principal = auth.ANY_USER):
    """Send on an open thread — the shape the Conversations composer uses."""
    conv = db.get(Conversation, conv_id)
    if not conv:
        raise HTTPException(404, "conversation not found")
    contact = db.get(Contact, conv.contact_id)
    if not contact:
        raise HTTPException(404, "conversation has no contact")
    return _send_to_contact(db, contact, body)


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
            "last_event_at": conv.last_event_at,
            "unread_count": conv.unread_count, "starred": conv.starred}


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
    return {"ok": True, "transport": type(get_transport()).__name__}

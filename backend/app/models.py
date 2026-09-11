"""Domain model.

Shape is driven by what was measured in the live GHL account (see DECISIONS.md):

* A conversation timeline interleaves MESSAGES and CALLS as sibling event types.
  Measured: GHL's Conversations shows inbound call records with audio playback in
  the same thread as SMS. Calls are not a separate feature bolted on later.
* Opportunities sit in exactly one stage of exactly one pipeline.
  Measured pipeline: "Dream Team Roofing AHS", 5 stages, home-warranty shaped.
"""
import enum
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

# JSONB on Postgres (indexable, the reason custom fields are JSON at all);
# plain JSON on SQLite so the dev bootstrap still works.
JSONType = JSON().with_variant(JSONB, "postgresql")

from .db import Base  # noqa: E402  (must follow JSONType: the models below use it)


def utcnow():
    return datetime.now(UTC)


class Role(str, enum.Enum):
    ADMIN = "ADMIN"
    DISPATCHER = "DISPATCHER"
    TECH = "TECH"


class EventType(str, enum.Enum):
    """Thread timeline entry types.

    Measured from GHL's "Filter messages" dropdown (captures/conversations/menus.json),
    which proves the thread is a mixed ACTIVITY timeline, not a message log:
    appointments, opportunity changes and invoices render inline beside SMS and calls.

    GHL groups these as Conversations vs Activities - see IS_ACTIVITY below.
    Types we do not implement in v1 (WhatsApp, AI Action Logs, SLA) are listed so the
    filter UI matches what was measured, rather than quietly showing a shorter menu.
    """
    SMS = "SMS"
    EMAIL = "EMAIL"
    CALL = "CALL"
    NOTE = "NOTE"
    INTERNAL_COMMENT = "INTERNAL_COMMENT"
    WHATSAPP = "WHATSAPP"
    CONTACT = "CONTACT"
    APPOINTMENT = "APPOINTMENT"
    OPPORTUNITY = "OPPORTUNITY"
    PAYMENT = "PAYMENT"
    INVOICE = "INVOICE"


# "Conversations" vs "Activities" split, per the measured filter menu.
CONVERSATION_TYPES = {EventType.SMS, EventType.EMAIL, EventType.CALL,
                      EventType.WHATSAPP, EventType.INTERNAL_COMMENT}
ACTIVITY_TYPES = {EventType.CONTACT, EventType.APPOINTMENT,
                  EventType.OPPORTUNITY, EventType.PAYMENT, EventType.INVOICE}


class Direction(str, enum.Enum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class DeliveryStatus(str, enum.Enum):
    """What happened to an outbound message. The owner's question is "did the text
    arrive or not", so these have to stay distinguishable all the way to the screen.

    The live ladder, driven by the BulkVS delivery receipt owen-main relays:

        QUEUED -> SENT -> DELIVERED
                       -> FAILED

    and two states that are not on it:

      * REFUSED — it never left, on purpose, and we know why: SMS is dark pending
        10DLC approval, the destination is not allowlisted, the contact opted out.
        Deliberately NOT folded into FAILED: nothing is broken, and a refusal will
        not fix itself on a retry the way a failure might.
      * LOGGED_ONLY — recorded by `LoggingTransport` and never transmitted. Kept
        distinct and visible so a stubbed message and a real one can never look the
        same in the same thread.

    PENDING predates all of this and is left in place; nothing writes it.
    """
    PENDING = "PENDING"
    # Accepted by owen-main and queued for the carrier. Not yet sent.
    QUEUED = "QUEUED"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    # Refused before it left, with a reason in `delivery_detail`.
    REFUSED = "REFUSED"
    # Outbound while only LoggingTransport is wired: recorded, never transmitted.
    LOGGED_ONLY = "LOGGED_ONLY"


# The delivery ladder is FORWARD-ONLY. Carriers re-deliver receipts and deliver them
# out of order, so a late "sent" must never walk a message back from "delivered".
# Mirrors owen-main's `services/sms.OUTBOUND_STATUS_RANK`, which applies the same
# rule to its own copy of the row — two systems ranking the same ladder differently
# would disagree about what a message's final state was.
DELIVERY_RANK = {
    DeliveryStatus.QUEUED: 1,
    DeliveryStatus.SENT: 2,
    DeliveryStatus.DELIVERED: 3,
    DeliveryStatus.FAILED: 3,
}


def advance_delivery(current: "DeliveryStatus | None",
                     new: "DeliveryStatus") -> "DeliveryStatus | None":
    """The status a row should hold after seeing `new`. Keeps `current` unless
    `new` ranks strictly higher, so DELIVERED and FAILED are terminal.

    A status off the ladder (LOGGED_ONLY, REFUSED, PENDING) ranks 0, so a receipt
    can still advance one — that matters: a row written REFUSED by a transport that
    was wrong about the far side should be correctable by the truth from the
    carrier. What cannot happen is a downgrade.
    """
    if DELIVERY_RANK.get(new, 0) > DELIVERY_RANK.get(current, 0):
        return new
    return current


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    # scrypt$n$r$p$<salt_b64>$<hash_b64> — see app.auth. Empty means "no password
    # set", which must never verify: the seeded users start this way.
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.ADMIN)

    # Bumping this invalidates every outstanding JWT for the user, so a password
    # change or a role change takes effect immediately rather than after the
    # 15-minute access-token lifetime.
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Departing staff are deactivated, not deleted: users.id is referenced by
    # contacts.owner_id, opportunities.owner_id, appointments.assigned_user_id and
    # calendars.user_id, none of which cascade.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApiToken(Base):
    """A long-lived personal access token, used by the `ghl` CLI.

    The browser uses short-lived JWTs in httpOnly cookies (DECISIONS.md); a CLI
    cannot hold a cookie session, so it authenticates with one of these instead.

    `token_hash` is a plain sha256 of the secret, deliberately NOT a slow KDF: the
    token is 256 bits of `secrets.token_urlsafe`, so it is not dictionary-attackable
    and a KDF would only add latency to every single request.
    """
    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # First few characters of the secret, for display only. Never used to
    # authenticate — it exists so a token can be identified in a list.
    prefix: Mapped[str] = mapped_column(String(24), index=True)
    # Space-separated. Empty = whatever the owner's role allows. Non-empty is a
    # strict narrowing, so a scoped token can never exceed its user's role. The
    # telephony project's ingest token carries exactly "events:write".
    scopes: Mapped[str] = mapped_column(String(255), default="", server_default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship()


class Contact(Base):
    __tablename__ = "contacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String(120), default="")
    last_name: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str | None] = mapped_column(String(255), index=True)
    phone: Mapped[str | None] = mapped_column(String(40), index=True)
    business_name: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str | None] = mapped_column(String(120))

    # Where the roof is. Added 2026-09-11 for the Workiz import, which is the first
    # thing that ever had an address to put anywhere: 856 real client records, every
    # one of them carrying one, and a roofing CRM that cannot say where the job is
    # is missing the field the work is organised around.
    #
    # Four columns rather than one blob because the two exports disagree about the
    # shape: `workiz_jobs.csv` already has City / State / Zip code as separate
    # columns, while `workiz_clients.csv` has a single combined string. Splitting is
    # the only way those two can agree on one record, and a city column is what
    # makes "everything in Palmetto this week" answerable later.
    #
    # All four are NULLABLE and nothing backfills them. Production holds real
    # contacts (CLAUDE.md) which simply have no address, and that is not an error —
    # exactly the stance `phone` takes. Anything the importer cannot confidently
    # split goes into `address_street` whole rather than being dropped.
    address_street: Mapped[str | None] = mapped_column(String(255))
    address_city: Mapped[str | None] = mapped_column(String(120))
    address_state: Mapped[str | None] = mapped_column(String(80))
    address_postal_code: Mapped[str | None] = mapped_column(String(20))

    # Fields the measured Contact Details panel renders (captures/conversations):
    # Owner, Followers, Tags, First/Last name, Email, Phone, Date of birth,
    # Contact source, Contact type (default "Lead"), DND, "Created by:".
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    date_of_birth: Mapped[str | None] = mapped_column(String(40))
    contact_type: Mapped[str] = mapped_column(String(40), default="Lead")
    dnd: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(80))
    # GHL custom fields are user-defined per location; JSON keeps them without
    # a migration per field. Becomes JSONB on Postgres.
    custom_fields: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    tags: Mapped[list["ContactTag"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan")
    opportunities: Mapped[list["Opportunity"]] = relationship(back_populates="contact")
    # No cascade, for the same reason opportunities have none: DELETE
    # /api/contacts detaches both and deletes neither. The relationship exists so
    # the detail payload can NAME the appointments the delete would sever —
    # `appointments.contact_id` is a real foreign key, and a contact that still
    # holds one cannot be deleted at all (see `delete_contact`).
    appointments: Mapped[list["Appointment"]] = relationship(
        back_populates="contact", order_by="Appointment.starts_at")
    owner: Mapped["User | None"] = relationship()

    @property
    def name(self) -> str:
        return (self.first_name + " " + self.last_name).strip()


class Tag(Base):
    __tablename__ = "tags"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    color: Mapped[str] = mapped_column(String(20), default="#eeeeee")


class ContactTag(Base):
    __tablename__ = "contact_tags"
    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id"), index=True)
    contact: Mapped[Contact] = relationship(back_populates="tags")
    tag: Mapped[Tag] = relationship()


class Pipeline(Base):
    __tablename__ = "pipelines"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    position: Mapped[int] = mapped_column(Integer, default=0)
    stages: Mapped[list["Stage"]] = relationship(
        back_populates="pipeline", cascade="all, delete-orphan",
        order_by="Stage.position")


class Stage(Base):
    __tablename__ = "stages"
    id: Mapped[int] = mapped_column(primary_key=True)
    pipeline_id: Mapped[int] = mapped_column(ForeignKey("pipelines.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    position: Mapped[int] = mapped_column(Integer, default=0)
    pipeline: Mapped[Pipeline] = relationship(back_populates="stages")


class Opportunity(Base):
    __tablename__ = "opportunities"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), index=True)
    pipeline_id: Mapped[int] = mapped_column(ForeignKey("pipelines.id"), index=True)
    stage_id: Mapped[int] = mapped_column(ForeignKey("stages.id"), index=True)
    # Money as integer cents; never float.
    value_cents: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(40), default="open")
    position: Mapped[int] = mapped_column(Integer, default=0)

    # Fields measured on GHL's opportunity detail screen
    # (captures/opportunities/detail_modal.json).
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    business_name: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str | None] = mapped_column(String(120))
    expected_close_date: Mapped[str | None] = mapped_column(String(40))
    created_by: Mapped[str | None] = mapped_column(String(80))

    # The live account already carries owen_* custom fields written by the
    # telephony project ("from OWEN"), with owen_call_id documented as the
    # join key. Keeping them as JSONB preserves that contract without a
    # migration per field. See DECISIONS.md.
    custom_fields: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    contact: Mapped[Contact | None] = relationship(back_populates="opportunities")
    stage: Mapped[Stage] = relationship()
    pipeline: Mapped[Pipeline] = relationship()
    owner: Mapped["User | None"] = relationship()


class SavedView(Base):
    """A named filter set for the Opportunities board. GHL calls these smart lists.

    OUR design — the `+ List` row and "Manage smart lists" were measured as labels
    only, and no saved-list screen was ever opened on the live account
    (DECISIONS.md, 2026-09-10).

    It holds exactly the filters the board actually has, and nothing speculative:
    a pipeline, the status filter and the search term. `pipeline_id` is nullable
    on purpose — a view that only says "Won, matching 'skylight'" applies to
    whichever pipeline is open, while one that names a pipeline switches to it.

    Views are shared, not per-user, because this is a four-person single-tenant
    CRM and "the list Owen made" is the useful thing. `created_by_id` records who
    made it; it is not an ownership check.

    The board's built-in "Open opportunities" is NOT a row here. It is the default
    state of the board and always available, so there is nothing to delete and no
    seed to keep in step.
    """
    __tablename__ = "saved_views"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    pipeline_id: Mapped[int | None] = mapped_column(ForeignKey("pipelines.id"))
    # Mirrors the board's own filter: open | won | lost | abandoned | all.
    status: Mapped[str] = mapped_column(String(20), default="open",
                                        server_default="open")
    q: Mapped[str] = mapped_column(String(200), default="", server_default="")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow)

    pipeline: Mapped["Pipeline | None"] = relationship()


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    last_event_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True)
    unread_count: Mapped[int] = mapped_column(Integer, default=0)
    starred: Mapped[bool] = mapped_column(Boolean, default=False)

    contact: Mapped[Contact] = relationship()
    events: Mapped[list["ConversationEvent"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan",
        order_by="ConversationEvent.occurred_at")


class ConversationEvent(Base):
    """One entry on a thread timeline. SMS, email, call and note are siblings."""
    __tablename__ = "conversation_events"
    # The global call/message search (GET /api/calls, /api/messages) filters on type
    # and orders by recency across every thread at once, which the per-conversation
    # indexes cannot serve.
    __table_args__ = (
        Index("ix_conversation_events_type_occurred", "type", "occurred_at"),
        # The idempotency guarantee for mirrored feeds, declared as a UNIQUE INDEX and
        # NOT as `unique=True` on the column.
        #
        # Both enforce the same thing on both backends, but they are different OBJECTS to
        # Alembic: `unique=True` on a column renders a UniqueConstraint, while the
        # migration adds a unique Index. Declaring one and creating the other makes
        # `uv run alembic check` — the drift gate CLAUDE.md documents — report a phantom
        # drop-and-recreate forever, and hands the next `--autogenerate` a spurious
        # constraint change inside some unrelated migration.
        #
        # The INDEX is the side that moved to match, because the migration ADDs this
        # column to an existing table: `ALTER TABLE ... ADD CONSTRAINT` needs batch mode
        # on SQLite, while a `CREATE UNIQUE INDEX` works unchanged on SQLite and Postgres
        # alike. `Job.dedupe_key` next door keeps `unique=True` and is right to — it is
        # created inside its CREATE TABLE, where a UniqueConstraint costs nothing.
        #
        # Named `uq_` rather than `ix_` deliberately: it is an index, but what a reader
        # needs to know first is that it makes the column unique.
        Index("uq_conversation_events_dedupe_key", "dedupe_key", unique=True),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id"), index=True)
    type: Mapped[EventType] = mapped_column(Enum(EventType))
    direction: Mapped[Direction] = mapped_column(Enum(Direction))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True)

    body: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(String(255))

    # CALL only. Populated by the telephony project writing into this schema.
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    # Measured on GHL's Call report: calls are grouped "by status".
    # completed | no-answer | busy | voicemail | failed
    call_status: Mapped[str | None] = mapped_column(String(30))
    recording_url: Mapped[str | None] = mapped_column(String(500))
    transcript: Mapped[str | None] = mapped_column(Text)

    delivery_status: Mapped[DeliveryStatus | None] = mapped_column(Enum(DeliveryStatus))
    # Why a message is in the state it is in, as a sentence for a person: the
    # refusal owen-main gave, or the carrier's failure reason. Null when the status
    # speaks for itself. The status is what the UI branches on; this is what it
    # prints beside it, so an unanticipated reason still reaches the operator.
    delivery_detail: Mapped[str | None] = mapped_column(Text)
    # The far side's id for this message — owen-main's `message_id`. This is the
    # join key a delivery receipt arrives with, which is why it is indexed:
    # POST /api/events/delivery looks a row up by it on every receipt.
    #
    # NOT unique, and it must not become so: the BulkVS call path deliberately
    # sends the SAME provider_ref on all three lifecycle phases ("calls.id, on
    # EVERY phase — the join key must not depend on which event survived",
    # owen-main integrations/crm/events.py). Deduplicating on it would collapse
    # started/answered/ended into one row and break the live path.
    provider_ref: Mapped[str | None] = mapped_column(String(120), index=True)

    # --- mirrored feeds (2026-09-11) ------------------------------------------
    # An OPT-IN idempotency key. UNIQUE, so a feed that may deliver the same
    # object twice — a poll that re-reads its window, or a job retried after the
    # response to a successful POST was lost — lands on ONE row.
    #
    # Separate from `provider_ref` precisely because that column cannot carry
    # this meaning (see above). NULL for everything that does not opt in, which
    # is every row written before this column existed and every row the BulkVS
    # path writes today; Postgres and SQLite both allow many NULLs in a unique
    # index, so the constraint costs those rows nothing.
    #
    # The uniqueness lives in `__table_args__` as a unique Index, not here as
    # `unique=True` — see the note there for why the two are not interchangeable.
    dedupe_key: Mapped[str | None] = mapped_column(String(200))

    # WHICH system and WHICH line carried this event: "OpenPhone" / "+19417247244".
    # A thread can now hold events from two phone systems at once, and an operator
    # who cannot tell them apart cannot tell which number the customer knows them
    # by. NULL means "not recorded" and renders no chip — never a guess, because
    # every row that predates this column has no observed source.
    source_system: Mapped[str | None] = mapped_column(String(40))
    source_number: Mapped[str | None] = mapped_column(String(40))

    conversation: Mapped[Conversation] = relationship(back_populates="events")


class Job(Base):
    """Durable queue. Postgres-backed, no Redis (DECISIONS.md).

    Claimed with SELECT ... FOR UPDATE SKIP LOCKED so multiple workers can drain
    it without double-running a job.
    """
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    # Set once a job has run to completion, so a retry can never duplicate a send.
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    # Idempotency: one job per (type, key). Prevents a duplicate reminder when the
    # same appointment is saved twice.
    dedupe_key: Mapped[str | None] = mapped_column(String(200), unique=True)


class Calendar(Base):
    """A bookable calendar.

    Measured on GHL: the Calendars filter group lists per-user / per-resource
    calendars ("Luis Candialies's Personal Calendar", "Workiz Jobs (imported)").

    `pipeline_id` is OUR ADDITION — GHL has no pipeline filter on calendars.
    Requested deliberately so appointments can be filtered by the pipeline they
    belong to. Recorded as a deviation in DECISIONS.md.
    """
    __tablename__ = "calendars"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    pipeline_id: Mapped[int | None] = mapped_column(ForeignKey("pipelines.id"))
    color: Mapped[str] = mapped_column(String(20), default="#004eeb")

    user: Mapped["User | None"] = relationship()
    pipeline: Mapped["Pipeline | None"] = relationship()


class Appointment(Base):
    __tablename__ = "appointments"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    calendar_id: Mapped[int | None] = mapped_column(ForeignKey("calendars.id"), index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), index=True)
    assigned_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(40), default="confirmed")
    notes: Mapped[str | None] = mapped_column(Text)
    # The deal this visit is for. NULLABLE and deliberately NOT cascaded: deleting
    # an opportunity detaches the booking and leaves it standing, exactly as
    # deleting a contact detaches its opportunities. A booked visit is a promise to
    # a customer and must survive a deal record being tidied up.
    opportunity_id: Mapped[int | None] = mapped_column(
        ForeignKey("opportunities.id"), index=True)

    contact: Mapped[Contact | None] = relationship(back_populates="appointments")
    calendar: Mapped["Calendar | None"] = relationship()
    opportunity: Mapped["Opportunity | None"] = relationship()


class CustomFieldDef(Base):
    """A job question the owner defines himself: "How many stories?", "Where is
    the leak located?", "What type of roof?".

    Three decisions are baked into the shape of this table, all the owner's:

    * **It describes, it does not store.** The answers stay in the existing
      `Opportunity.custom_fields` JSON blob, keyed by `key`. This table says a
      field exists, what type it is and what its options are; nothing here holds a
      customer's answer, so nothing here can destroy one.
    * **It attaches to OPPORTUNITIES, not contacts.** "How old is the roof" is a
      fact about this job — the same customer calling back next year gets their own
      answers. `entity` exists so contact-level fields could be added later without
      a second table; today it is always "opportunity" and nothing writes anything
      else.
    * **A definition can attach to SEVERAL pipelines** (`CustomFieldPipeline`), so
      "Roof type" is defined once and answers stay comparable across pipelines,
      while "AHS claim number" is attached to the warranty pipeline alone.

    There is deliberately **no `required` flag**. An inbound call at 2am must still
    become a deal; a required field would mean a missed lead.

    Deleting is ARCHIVING (`archived_at`). An archived field stops appearing on new
    deals, the answers already recorded survive in the blob and stay readable on the
    deals that hold them, and un-archiving brings it back.
    """
    __tablename__ = "custom_field_defs"

    # The five types the owner asked for. No multi-select: deliberately deferred.
    TEXT = "text"
    NUMBER = "number"
    DROPDOWN = "dropdown"
    DATE = "date"
    BOOLEAN = "boolean"
    TYPES = (TEXT, NUMBER, DROPDOWN, DATE, BOOLEAN)

    id: Mapped[int] = mapped_column(primary_key=True)
    # The key inside `Opportunity.custom_fields`. Derived from the label ONCE, at
    # creation, and then immutable: it is what every recorded answer is filed
    # under, so changing it would orphan every answer already given.
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(160))
    field_type: Mapped[str] = mapped_column(String(20))
    # DROPDOWN only: the list of allowed answers, in the order they are offered.
    options: Mapped[list] = mapped_column(JSONType, default=list)
    entity: Mapped[str] = mapped_column(String(20), default="opportunity",
                                        server_default="opportunity")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Set = archived. Never deleted, because the answers are not ours to destroy.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow)

    pipelines: Mapped[list["CustomFieldPipeline"]] = relationship(
        back_populates="field", cascade="all, delete-orphan")


class CustomFieldPipeline(Base):
    """Which pipelines a definition is asked on. One row per attachment.

    A field attached to NO pipeline appears on no deal — attachment is explicit,
    never inferred from an empty set, so detaching the last pipeline cannot
    silently turn a narrow field into a global one.
    """
    __tablename__ = "custom_field_pipelines"
    __table_args__ = (UniqueConstraint("field_id", "pipeline_id",
                                       name="uq_custom_field_pipeline"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    field_id: Mapped[int] = mapped_column(
        ForeignKey("custom_field_defs.id"), index=True)
    pipeline_id: Mapped[int] = mapped_column(
        ForeignKey("pipelines.id"), index=True)

    field: Mapped[CustomFieldDef] = relationship(back_populates="pipelines")

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
    false,
    func,
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
    # GoHighLevel's "Only assigned data" (2026-09-15). On: the user sees only their own
    # jobs and what hangs off them — see app/assigned_access.py. The API turns it on for
    # a user CREATED as a TECH; every user that existed before the column is false,
    # which is exactly their access before it. Ignored for an ADMIN, who always sees all.
    only_assigned_data: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false())
    # My Staff (2026-09-15). Shown in the staff table; stored as `store_phone` stores a
    # contact's. NULL = none recorded.
    phone: Mapped[str | None] = mapped_column(String(40))
    # Set when an ADMIN creates the user or resets their password: until they choose
    # their own, the API answers nothing but "change your password" (auth.require_auth).
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false())

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
    # The three display modes GoHighLevel's "Set pipeline display colors" offers.
    COLOR_NONE = "none"
    COLOR_DOT = "dot"
    COLOR_TINT = "tint"
    COLOR_MODES = (COLOR_NONE, COLOR_DOT, COLOR_TINT)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    position: Mapped[int] = mapped_column(Integer, default=0)
    # --- the pipeline modal (2026-09-13) --------------------------------------
    # How the board draws a stage's colour in its column header. "none" is what
    # every pipeline did before this column existed, and is its server default.
    color_mode: Mapped[str] = mapped_column(String(20), default=COLOR_NONE,
                                            server_default=COLOR_NONE)
    # Off: a deal's probability is its stage's. On: each deal carries its own
    # (`Opportunity.probability`). Only the Forecast reads it.
    use_opportunity_probability: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false())
    # NULL for every pipeline that predates the column: nothing recorded when it
    # was last changed, and the list says "—" rather than inventing a date.
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    stages: Mapped[list["Stage"]] = relationship(
        back_populates="pipeline", cascade="all, delete-orphan",
        order_by="Stage.position")


class Stage(Base):
    __tablename__ = "stages"
    id: Mapped[int] = mapped_column(primary_key=True)
    pipeline_id: Mapped[int] = mapped_column(ForeignKey("pipelines.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    position: Mapped[int] = mapped_column(Integer, default=0)
    # --- the pipeline modal (2026-09-13) --------------------------------------
    # "#RRGGBB", or NULL for a stage created before stages had colours. Only drawn
    # when the pipeline's `color_mode` is dot or tint.
    color: Mapped[str | None] = mapped_column(String(20))
    # 0-100, or NULL = "no probability set", which the Forecast weights at the
    # pipeline's conversion rate exactly as it did before this column existed.
    probability: Mapped[int | None] = mapped_column(Integer)
    # GoHighLevel's "Show in reports": the funnel icon and the pie icon, two
    # independent switches. A hidden stage still holds its deals and still shows
    # on the board; it only drops out of that report.
    show_in_funnel: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true())
    show_in_pie: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true())
    pipeline: Mapped[Pipeline] = relationship(back_populates="stages")


class PipelinePermission(Base):
    """Who may access a pipeline — GoHighLevel's "Manage permissions".

    One row per (pipeline, user) that is ALLOWED. A pipeline with NO rows is open
    to everyone, which is what every pipeline was before this table existed. An
    ADMIN always has access whatever the rows say. This decides whether a user
    can SEE the pipeline and its deals at all; their role still decides what they
    may do with them. Enforced in `app/pipeline_access.py`.
    """
    __tablename__ = "pipeline_permissions"
    __table_args__ = (UniqueConstraint("pipeline_id", "user_id",
                                       name="uq_pipeline_permission"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pipeline_id: Mapped[int] = mapped_column(ForeignKey("pipelines.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)


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
    # 0-100, or NULL. Read by the Forecast ONLY when the pipeline has "Use
    # opportunity-level probability" switched on; otherwise the stage's applies.
    probability: Mapped[int | None] = mapped_column(Integer)
    # The address of the JOB — the property being worked on (2026-09-14). A customer
    # with six roofs is one contact and six cards, so the contact's address cannot
    # say which roof a card is. Typed like the contact's four columns. Null means
    # "none on the card"; nothing falls back to the contact's address at rest.
    address_street: Mapped[str | None] = mapped_column(String(255))
    address_city: Mapped[str | None] = mapped_column(String(120))
    address_state: Mapped[str | None] = mapped_column(String(80))
    address_postal_code: Mapped[str | None] = mapped_column(String(20))

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
    # The AI agent that wrote this event (2026-09-15), shown as "AI: <agent name>". NULL for
    # everything a person, an automation or a feed wrote. A plain integer, not a foreign
    # key: the column is ADDED to a live table, and an agent is archived, never deleted.
    ai_agent_id: Mapped[int | None] = mapped_column(Integer)

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
    # GoHighLevel's "Add description" (2026-09-13). Visible to every role, unlike
    # `notes`, which is the modal's STAFF-only "Internal notes".
    description: Mapped[str | None] = mapped_column(Text)
    # "Meeting location", stored as the RESOLVED text: "Calendar default" is the
    # contact's property address at the moment of booking, "Custom" is what was
    # typed. Resolved rather than referenced, so editing the contact's address
    # later does not move a visit that was already booked somewhere.
    location: Mapped[str | None] = mapped_column(Text)
    # The AI agent that booked, rescheduled or cancelled this visit last (2026-09-15).
    ai_agent_id: Mapped[int | None] = mapped_column(Integer)

    contact: Mapped[Contact | None] = relationship(back_populates="appointments")
    calendar: Mapped["Calendar | None"] = relationship()
    opportunity: Mapped["Opportunity | None"] = relationship()


class BlockedTime(Base):
    """GoHighLevel's "Blocked off time" (2026-09-13): a range on a calendar that is
    not an appointment — a crew day off, a supplier run.

    It is its own table rather than an appointment with `status = "blocked"`,
    because it has no contact, no reminders and no report tile. It never sends
    anything and enqueues no job. Booking an appointment over one is allowed, but
    only after the booker has been told and confirms.
    """
    __tablename__ = "blocked_times"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    calendar_id: Mapped[int] = mapped_column(ForeignKey("calendars.id"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now())

    calendar: Mapped["Calendar"] = relationship()


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

    # The five types the owner asked for, and PARAGRAPH (multi-line text) for the
    # call Checklist (2026-09-14). No multi-select: deliberately deferred.
    TEXT = "text"
    NUMBER = "number"
    DROPDOWN = "dropdown"
    DATE = "date"
    BOOLEAN = "boolean"
    PARAGRAPH = "paragraph"
    TYPES = (TEXT, NUMBER, DROPDOWN, DATE, BOOLEAN, PARAGRAPH)

    # What a yes/no question may show beside its tick (2026-09-14). The value shown
    # is the REAL record's, edited in place — never a copy kept in the answers.
    LINK_CONTACT_EMAIL = "contact_email"
    LINK_OPPORTUNITY_ADDRESS = "opportunity_address"
    LINKS = (LINK_CONTACT_EMAIL, LINK_OPPORTUNITY_ADDRESS)

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
    # The modal tab this field is drawn under (2026-09-13). NULL = no group, which
    # draws it under "Opportunity details". A field belongs to AT MOST one group;
    # deleting a group sets this back to NULL and touches no answer.
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("custom_field_groups.id"), index=True)
    # The call Checklist's per-question settings (2026-09-14). All three NULL on
    # every field that existed before, which is exactly "no setting".
    #   script        what the dispatcher says, drawn under the question. Any type.
    #   linked_field  BOOLEAN only: one of LINKS, shown and edited beside the tick.
    #   details_when  DROPDOWN only: the options that open a details box. Its answer
    #                 is stored beside the main one, under "<key>__details".
    script: Mapped[str | None] = mapped_column(Text)
    linked_field: Mapped[str | None] = mapped_column(String(40))
    details_when: Mapped[list | None] = mapped_column(JSONType)

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


class CustomFieldGroup(Base):
    """A named tab of custom fields in the opportunity modal (2026-09-13).

    GoHighLevel's "Roof Inspection", "Photo Checklist" and "Customer Journey
    Checklist" tabs are groups the owner made himself. A group is a name and a
    position and nothing else: it applies to EVERY pipeline, while the fields in
    it keep their own per-pipeline attachment. It holds no answer, so deleting one
    cannot lose an answer — its fields simply fall back to Opportunity details.
    """
    __tablename__ = "custom_field_groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow,
        server_default=func.now())


class OpportunityTask(Base):
    """A to-do on a deal (2026-09-13). Attached to the opportunity AND to its
    contact, so a customer's tasks stay findable if the deal is later detached.

    A task notifies NOBODY. It enqueues no job and sends nothing — there is no
    reminder, no due-date text and no assignee notification, by the owner's rule.
    `completed_at` set means done; clearing it reopens the task.
    """
    __tablename__ = "opportunity_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id"), index=True)
    contact_id: Mapped[int | None] = mapped_column(
        ForeignKey("contacts.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow,
        server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow,
        server_default=func.now())
    # AI Agents (2026-09-15). "normal" for every task that existed before; an escalation
    # makes an "urgent" one. The agent that created a task, shown "AI: <agent name>".
    priority: Mapped[str] = mapped_column(String(20), default="normal",
                                          server_default="normal")
    ai_agent_id: Mapped[int | None] = mapped_column(Integer)

    assignee: Mapped["User | None"] = relationship(foreign_keys=[assigned_user_id])


class OpportunityNote(Base):
    """A staff note on ONE deal (2026-09-13). A customer with three jobs has three
    note lists. STAFF-only on every path, exactly like a NOTE event on a thread
    (DECISIONS.md, 2026-09-10): a TECH cannot read, count or write one."""
    __tablename__ = "opportunity_notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow,
        server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow,
        server_default=func.now())
    # The AI agent that wrote this note (2026-09-15); `created_by_id` is then NULL.
    ai_agent_id: Mapped[int | None] = mapped_column(Integer)

    author: Mapped["User | None"] = relationship()


class OpportunityFollower(Base):
    """A staff user following a deal. A link row and nothing else."""
    __tablename__ = "opportunity_followers"
    __table_args__ = (UniqueConstraint("opportunity_id", "user_id",
                                       name="uq_opportunity_follower"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)


class OpportunityContact(Base):
    """An ADDITIONAL contact on a deal — the spouse, the property manager. The
    primary contact stays `opportunities.contact_id`. At most ten per deal
    (GoHighLevel's "Additional contacts (Max: 10)"), enforced by the API."""
    __tablename__ = "opportunity_contacts"
    __table_args__ = (UniqueConstraint("opportunity_id", "contact_id",
                                       name="uq_opportunity_contact"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)

    contact: Mapped["Contact"] = relationship()


# ---------- number-only threads (2026-09-13) ----------
#
# The owner's rule: "if its a number that is not registered as a contact, it should
# not be saved as contact but the numbers with the conversation must display
# anyways". A call or text from a number no contact holds creates NO Contact and NO
# Opportunity; it lands on a thread that belongs to the NUMBER.
#
# Why two NEW tables rather than a flag on the existing two. `conversations.contact_id`
# and `conversation_events.conversation_id` are both NOT NULL, and the standing
# migration rule is CREATE TABLE / ADD COLUMN only — no ALTER of an existing column.
# So a number-only thread cannot be a `conversations` row (it has no contact to point
# at) and its events cannot be `conversation_events` rows (they have no conversation
# to point at). Everything else follows from that:
#
#   * `number_threads` carries exactly the thread state a conversation carries —
#     unread_count, starred, last_event_at — plus the number itself and the name
#     Quo's own contact book gives it, which is display-only and creates nothing.
#   * `number_thread_events` is `conversation_events` column for column, with the
#     parent swapped. `test_number_threads.py` pins the two column sets equal, so a
#     column added to one and forgotten on the other fails a test instead of being
#     silently dropped when a thread is adopted.
#   * ADOPTION moves every event into `conversation_events` and deletes the number
#     thread, in the same transaction that gave the number a contact
#     (`number_threads.adopt_on_flush`). A number-only thread is a waiting room, not
#     a second kind of history.

class NumberThread(Base):
    """A thread for a phone number that no contact holds. See the block above."""
    __tablename__ = "number_threads"
    id: Mapped[int] = mapped_column(primary_key=True)
    # As `store_phone` stores it: E.164 when it parses, verbatim when it does not.
    phone: Mapped[str] = mapped_column(String(40))
    # The last ten digits — the identity rule shared with contacts, the picker, the
    # CRM link and owen-main. UNIQUE: one number, one thread.
    phone_key: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    # The name Quo's (OpenPhone's) own contact book has for this number, when it has
    # one. Shown labelled "from Quo". Never used to create anything here.
    quo_name: Mapped[str | None] = mapped_column(String(200))
    last_event_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True)
    unread_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    starred: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now())

    events: Mapped[list["NumberThreadEvent"]] = relationship(
        back_populates="thread", cascade="all, delete-orphan",
        order_by="NumberThreadEvent.occurred_at")


class NumberThreadEvent(Base):
    """One entry on a number-only thread: `ConversationEvent`, parent swapped."""
    __tablename__ = "number_thread_events"
    __table_args__ = (
        Index("ix_number_thread_events_type_occurred", "type", "occurred_at"),
        # The same idempotency guarantee, declared the same way (a unique INDEX, see
        # ConversationEvent). Uniqueness ACROSS the two tables is the ingest's job:
        # `POST /api/events` looks a key up in both before writing either, and
        # adoption skips an event whose key the contact's thread already holds.
        Index("uq_number_thread_events_dedupe_key", "dedupe_key", unique=True),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    number_thread_id: Mapped[int] = mapped_column(
        ForeignKey("number_threads.id"), index=True)
    type: Mapped[EventType] = mapped_column(Enum(EventType))
    direction: Mapped[Direction] = mapped_column(Enum(Direction))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True)
    body: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(String(255))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    call_status: Mapped[str | None] = mapped_column(String(30))
    recording_url: Mapped[str | None] = mapped_column(String(500))
    transcript: Mapped[str | None] = mapped_column(Text)
    delivery_status: Mapped[DeliveryStatus | None] = mapped_column(Enum(DeliveryStatus))
    delivery_detail: Mapped[str | None] = mapped_column(Text)
    provider_ref: Mapped[str | None] = mapped_column(String(120), index=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    source_system: Mapped[str | None] = mapped_column(String(40))
    source_number: Mapped[str | None] = mapped_column(String(40))
    # Column for column with ConversationEvent (test_number_threads pins it). An agent never
    # writes to a number-only thread — it never acts without a contact — so this stays NULL.
    ai_agent_id: Mapped[int | None] = mapped_column(Integer)

    thread: Mapped[NumberThread] = relationship(back_populates="events")


# The columns an event carries that are not its identity or its parent. Adoption and
# conversion copy exactly these, in both directions.
EVENT_PAYLOAD_COLUMNS = (
    "type", "direction", "occurred_at", "body", "subject", "duration_seconds",
    "call_status", "recording_url", "transcript", "delivery_status",
    "delivery_detail", "provider_ref", "dedupe_key", "source_system",
    "source_number", "ai_agent_id",
)


# ---- CompanyCam (2026-09-14) ---------------------------------------------------------
#
# Job photos stay in CompanyCam. What this CRM keeps is only WHICH project belongs to
# which card, how that was decided, what the hourly check last did, and what a human
# still has to look at. No photo, no photo URL and no token is ever stored. See
# app/companycam.py and DECISIONS.md (2026-09-14, CompanyCam).

class CompanyCamLink(Base):
    """One card <-> one CompanyCam project. A project can sit on several cards (a
    repeat customer at one address) and a card can hold several projects."""
    __tablename__ = "companycam_links"
    __table_args__ = (
        UniqueConstraint("opportunity_id", "project_id", name="uq_companycam_link"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    # CASCADE at the database: deleting a deal must never be refused over a photo link,
    # and the link means nothing without its card. Nothing in CompanyCam is touched.
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), index=True)
    # CompanyCam's own id, as text: it is theirs, and nothing here does arithmetic on it.
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    # workiz_job | address | manual | created
    method: Mapped[str] = mapped_column(String(20))
    # The project's name when it was linked, so the contact panel and the review page
    # can say which project this is without a round trip. Display only.
    project_name: Mapped[str | None] = mapped_column(String(255))
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now())
    linked_by: Mapped[str | None] = mapped_column(String(200))
    # An ADMIN's unlink. The row is kept so the address rule never links the same
    # project back onto this card on the next sweep; every read skips it.
    unlinked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CompanyCamReviewItem(Base):
    """A project that matched a card by customer NAME only. Never linked on that
    evidence; listed for an admin, who may link it by hand or dismiss it."""
    __tablename__ = "companycam_review_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    project_name: Mapped[str | None] = mapped_column(String(255))
    project_address: Mapped[str | None] = mapped_column(String(500))
    candidate_opportunity_ids: Mapped[list] = mapped_column(JSONType, default=list,
                                                           server_default="[]")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now())
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CompanyCamSyncState(Base):
    """The hourly check's heartbeat. One row (id 1), readable by the operator through
    `python -m app.companycam_link status` and `GET /api/companycam/status`."""
    __tablename__ = "companycam_sync_state"
    id: Mapped[int] = mapped_column(primary_key=True)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_full_sweep_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Unix seconds: projects updated at or after this are re-examined next hour.
    modified_cursor: Mapped[int | None] = mapped_column(Integer)
    last_counts: Mapped[dict | None] = mapped_column(JSONType)
    last_error: Mapped[str | None] = mapped_column(Text)


class CompanyCamProjectRequest(Base):
    """A card that should get a CompanyCam project: created by hand or by an AHS
    email with an address, or given its first address. The worker searches
    CompanyCam first and links what is already there; it creates only when nothing
    is. UNIQUE per card: one card can never ask twice."""
    __tablename__ = "companycam_project_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(
        ForeignKey("opportunities.id", ondelete="CASCADE"), unique=True, index=True)
    # created (Add opportunity / API) | address_saved | ahs_email
    origin: Mapped[str] = mapped_column(String(20))
    # pending | creating | linked | created | skipped | failed
    state: Mapped[str] = mapped_column(String(20), default="pending",
                                       server_default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    project_id: Mapped[str | None] = mapped_column(String(40))
    last_error: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---- AI Agents, phase 1: the foundation (2026-09-15) -------------------------------------
#
# GoHighLevel's AI Agents module, built so a text follow-up (phase 2), a voice receptionist
# on owen-main (phase 3) and outbound (phase 4) are configuration plus small additions. See
# app/ai/ and DECISIONS.md (2026-09-15, AI Agents).
#
# Two rules shape every table below:
#
#   * A run, a suggestion or a log line refers to a contact, a deal, a conversation or an
#     appointment by a PLAIN INTEGER, never a foreign key. Logs are kept indefinitely and a
#     contact can be deleted (DELETE /api/contacts detaches or deletes what points at it,
#     and must not learn about a new table to keep working). A deleted subject leaves a log
#     that still reads — the label is kept beside the id.
#   * Nothing here holds a provider API key in clear. `ai_connections.api_key_encrypted` is
#     a Fernet token under AI_SECRETS_KEY; only the last four characters are kept readable.

class AiSettings(Base):
    """One row (id 1): the global "Pause all AI agents" switch and the on-call phone an
    emergency escalation texts. No row = not paused, no on-call phone."""
    __tablename__ = "ai_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    on_call_phone: Mapped[str | None] = mapped_column(String(40))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class AiConnection(Base):
    """An AI provider account: Anthropic, OpenAI, or any OpenAI-compatible server."""
    __tablename__ = "ai_connections"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    COMPATIBLE = "openai_compatible"
    PROVIDERS = (ANTHROPIC, OPENAI, COMPATIBLE)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(30))
    # OpenAI-compatible only. NULL = the provider's own endpoint.
    base_url: Mapped[str | None] = mapped_column(String(500))
    api_key_encrypted: Mapped[str] = mapped_column(Text)
    api_key_last4: Mapped[str] = mapped_column(String(8), default="", server_default="")
    default_model: Mapped[str] = mapped_column(String(200))
    # US dollars per 1M tokens, as MICRO-dollars (5.00 -> 5_000_000) so money is never a
    # float. NULL = price unknown, and a run's cost is then unknown rather than zero.
    price_input_micros: Mapped[int | None] = mapped_column(Integer)
    price_output_micros: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class AiFolder(Base):
    __tablename__ = "ai_folders"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now())


class AiAgent(Base):
    """An agent. What it IS lives in `draft` (edited freely) and in its published
    versions (frozen). What it is DOING — its mode — is a column, because switching an
    agent off must never need a publish."""
    __tablename__ = "ai_agents"
    OFF = "off"
    SUGGEST = "suggest"
    AUTO = "auto"
    MODES = (OFF, SUGGEST, AUTO)
    TEXT = "text"
    VOICE = "voice"
    CHANNELS = (TEXT, VOICE)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    folder_id: Mapped[int | None] = mapped_column(ForeignKey("ai_folders.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20), default=TEXT, server_default=TEXT)
    description: Mapped[str | None] = mapped_column(Text)
    # Every agent starts Off, and nothing but an explicit change turns one on.
    mode: Mapped[str] = mapped_column(String(20), default=OFF, server_default=OFF)
    draft: Mapped[dict] = mapped_column(JSONType, default=dict)
    published_version_id: Mapped[int | None] = mapped_column(Integer)
    draft_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # Deleting an agent archives it: its runs, suggestions and versions still read.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AiAgentVersion(Base):
    """An IMMUTABLE published config — the shape owen-main's `agent_versions` keeps, so a
    voice agent's version can be pushed there as-is in phase 3. Nothing updates a row."""
    __tablename__ = "ai_agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version", name="uq_ai_agent_version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("ai_agents.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict] = mapped_column(JSONType)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now())
    published_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class AiKnowledgeBase(Base):
    __tablename__ = "ai_knowledge_bases"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AiKbItem(Base):
    """One FAQ, article or file. A file keeps its EXTRACTED TEXT, name, type and size —
    not its bytes: this CRM has no file store (CompanyCam photos stay in CompanyCam)."""
    __tablename__ = "ai_kb_items"
    FAQ = "faq"
    ARTICLE = "article"
    FILE = "file"
    KINDS = (FAQ, ARTICLE, FILE)

    id: Mapped[int] = mapped_column(primary_key=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("ai_knowledge_bases.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    # FAQ: the question. Article: its title. File: the file name.
    title: Mapped[str] = mapped_column(String(500))
    # FAQ: the answer. Article: its text. File: the text extracted from it.
    body: Mapped[str] = mapped_column(Text, default="")
    content_type: Mapped[str | None] = mapped_column(String(120))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class AiKbChunk(Base):
    """A searchable piece of an item. `terms` is the normalised word list the lexical
    retriever matches (" roof leak warranty "), so search works the same on SQLite and
    Postgres without an extension. An embeddings retriever can add its own table later."""
    __tablename__ = "ai_kb_chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("ai_kb_items.id", ondelete="CASCADE"), index=True)
    kb_id: Mapped[int] = mapped_column(Integer, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    text: Mapped[str] = mapped_column(Text)
    terms: Mapped[str] = mapped_column(Text, default="", server_default="")


class AiKnowledgeGap(Base):
    """A question an agent could not answer from its knowledge, deduplicated."""
    __tablename__ = "ai_knowledge_gaps"
    OPEN = "open"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_key: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default=OPEN, server_default=OPEN,
                                        index=True)
    count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    agent_id: Mapped[int | None] = mapped_column(Integer)
    last_run_id: Mapped[int | None] = mapped_column(Integer)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now())
    resolved_item_id: Mapped[int | None] = mapped_column(Integer)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class AiRun(Base):
    """One time an agent was triggered — including the times it decided not to act."""
    __tablename__ = "ai_runs"
    __table_args__ = (Index("ix_ai_runs_agent_created", "agent_id", "created_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    agent_name: Mapped[str] = mapped_column(String(120), default="", server_default="")
    version_id: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[int | None] = mapped_column(Integer)
    # missed_call | inbound_text | stage_entered | appointment_booked |
    # appointment_rescheduled | appointment_cancelled | manual | test
    trigger: Mapped[str] = mapped_column(String(40))
    trigger_ref: Mapped[str | None] = mapped_column(String(120))
    contact_id: Mapped[int | None] = mapped_column(Integer, index=True)
    opportunity_id: Mapped[int | None] = mapped_column(Integer, index=True)
    appointment_id: Mapped[int | None] = mapped_column(Integer)
    conversation_id: Mapped[int | None] = mapped_column(Integer)
    subject_label: Mapped[str | None] = mapped_column(String(300))
    mode: Mapped[str] = mapped_column(String(20))
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # queued | running | completed | escalated | refused | error | skipped | cancelled
    outcome: Mapped[str] = mapped_column(String(20), default="queued",
                                         server_default="queued", index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    connection_id: Mapped[int | None] = mapped_column(Integer)
    provider: Mapped[str | None] = mapped_column(String(30))
    model: Mapped[str | None] = mapped_column(String(200))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Micro-dollars. NULL = unknown (no price on the connection), never "free".
    cost_micros: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    run_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True)
    created_by_id: Mapped[int | None] = mapped_column(Integer)

    steps: Mapped[list["AiRunStep"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="AiRunStep.position")


class AiRunStep(Base):
    """One line of a run's transcript: the prompt, a message, a tool call, its result, an
    action taken / suggested / refused / that a test run WOULD have taken."""
    __tablename__ = "ai_run_steps"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("ai_runs.id", ondelete="CASCADE"),
                                        index=True)
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # system | user | assistant | tool_call | tool_result | action | note
    kind: Mapped[str] = mapped_column(String(20))
    text: Mapped[str | None] = mapped_column(Text)
    tool_name: Mapped[str | None] = mapped_column(String(60))
    tool_call_id: Mapped[str | None] = mapped_column(String(120))
    data: Mapped[dict | None] = mapped_column(JSONType)
    # executed | suggested | refused | would — for kind "action"
    action_status: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now())

    run: Mapped[AiRun] = relationship(back_populates="steps")


class AiSuggestion(Base):
    """A write a Suggest-mode agent wanted to make, waiting for a person."""
    __tablename__ = "ai_suggestions"
    PENDING = "pending"
    APPROVED = "approved"
    DISMISSED = "dismissed"
    FAILED = "failed"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, index=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(60))
    args: Mapped[dict] = mapped_column(JSONType, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    contact_id: Mapped[int | None] = mapped_column(Integer, index=True)
    opportunity_id: Mapped[int | None] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(20), default=PENDING,
                                        server_default=PENDING, index=True)
    result: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class AiAgentThread(Base):
    """An agent's state on one customer's conversation: how many texts it has sent there
    (max messages) and whether a staff reply put it to sleep."""
    __tablename__ = "ai_agent_threads"
    __table_args__ = (UniqueConstraint("agent_id", "contact_id", name="uq_ai_agent_thread"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(Integer, index=True)
    contact_id: Mapped[int] = mapped_column(Integer, index=True)
    messages_sent: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    asleep_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    asleep_reason: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AiTemplate(Base):
    """A saved agent config to start new agents from. None ship with the product."""
    __tablename__ = "ai_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(20), default="text", server_default="text")
    config: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now())
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class AiAlert(Base):
    """An in-app alert — the bell. This CRM had no notification mechanism, so this is a
    minimal one: a row per recipient, read or not. Only escalations write one today."""
    __tablename__ = "ai_alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str | None] = mapped_column(Text)
    urgent: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    run_id: Mapped[int | None] = mapped_column(Integer)
    agent_id: Mapped[int | None] = mapped_column(Integer)
    contact_id: Mapped[int | None] = mapped_column(Integer)
    opportunity_id: Mapped[int | None] = mapped_column(Integer)
    task_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---- picture messages (MMS), 2026-09-16 ----------------------------------------------
#
# The CRM keeps its OWN copy of every picture, unlike a mirrored call recording, which is
# streamed from owen-main every time it is played. The reason is the difference between the
# two sources: OpenPhone holds a recording for as long as the account exists, while a
# CARRIER MMS media link EXPIRES — days, sometimes hours. A thread that relayed the bytes on
# demand would show the roof for a week and a broken picture for ever after, which is the
# opposite of what a job record is for.
#
# THE BYTES ARE NOT IN THIS TABLE. They live on disk under `MEDIA_ROOT`, content-addressed
# by sha256, and this row records where. Postgres would carry a few gigabytes of JPEG in the
# WAL, in every base backup and in every `pg_dump` the operator takes to look at 800
# contacts. `app/attachments.py` owns the directory; nothing else reads or writes it.

class MessageAttachment(Base):
    """One picture on one message, on either kind of thread.

    ONE table for both thread kinds rather than the parallel pair `ConversationEvent` /
    `NumberThreadEvent` use. Those two are parallel because they are whole timelines with
    their own routes, filters and permissions; an attachment has none of that — it is a file
    and a status — and two tables would mean two of every query, two of every cap check and
    two chances for them to disagree about what "stored" means.

    Exactly one of the two parent columns is set, and BOTH are null for a DRAFT: a picture
    the composer has uploaded and nobody has sent yet. A draft becomes an outbound
    attachment when the send succeeds, and is deleted when the operator removes it.
    """
    __tablename__ = "message_attachments"
    __table_args__ = (
        # Idempotency for a re-delivered inbound MMS: owen-main hands us the same message
        # twice, `POST /api/events` returns the existing event, and this index means the
        # second attempt to create its pictures writes nothing rather than doubling them.
        # NULLs are distinct in a unique index on both backends, so every draft (both
        # parents NULL) is still its own row.
        Index("uq_message_attachments_conv_event_position",
              "conversation_event_id", "position", unique=True),
        Index("uq_message_attachments_number_event_position",
              "number_thread_event_id", "position", unique=True),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversation_events.id"), index=True)
    number_thread_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("number_thread_events.id"), index=True)
    # 0-based, and the order the carrier sent them in. What the viewer's next/previous walks.
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    direction: Mapped[Direction] = mapped_column(
        Enum(Direction), default=Direction.INBOUND, server_default="INBOUND")

    # PENDING -> STORED, or FAILED / REFUSED. A draft the composer holds is DRAFT.
    #   PENDING  the text has landed, the picture has not been fetched yet
    #   STORED   the bytes are on disk under `storage_path`
    #   FAILED   the fetch did not work and may on a retry (a timeout, owen-main down)
    #   REFUSED  it will never work: too big, or not an image. `detail` says which.
    # Plain strings rather than an Enum: a status added later must not need an ALTER TYPE
    # against a live Postgres, which is exactly what the owner's migration rule forbids.
    status: Mapped[str] = mapped_column(String(20), default="PENDING",
                                        server_default="PENDING")
    # A sentence for a person, never a wire body: "That picture is 8.2 MB — the limit is 5 MB."
    detail: Mapped[str | None] = mapped_column(Text)

    # WHERE owen-main can be asked for this picture: "<owen message id>/<index>". The CRM
    # never holds a carrier URL — this is a locator on the link, resolved by
    # `crmlink.fetch_message_media`. Null on an outbound picture, which we already have.
    source_ref: Mapped[str | None] = mapped_column(String(200), index=True)

    content_type: Mapped[str | None] = mapped_column(String(80))
    byte_size: Mapped[int | None] = mapped_column(Integer)
    # The file's identity AND its name on disk: `<sha[:2]>/<sha>` under MEDIA_ROOT. Two
    # messages carrying the same picture share one file, and deleting one must not orphan
    # the other — which is why deletion counts the rows holding a sha before unlinking.
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    storage_path: Mapped[str | None] = mapped_column(String(200))
    # What to call it in a download. The carrier rarely sends one; then it is derived from
    # the type ("photo-3.jpg"), never invented from the customer's words.
    filename: Mapped[str | None] = mapped_column(String(200))

    # Who uploaded an OUTBOUND picture. Null on everything inbound.
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


ATTACHMENT_PENDING = "PENDING"
ATTACHMENT_STORED = "STORED"
ATTACHMENT_FAILED = "FAILED"
ATTACHMENT_REFUSED = "REFUSED"
ATTACHMENT_DRAFT = "DRAFT"

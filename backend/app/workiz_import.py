"""Import the real Workiz export: 856 clients and 389 jobs.

    uv run python -m app.workiz_import                 # DRY RUN. Writes nothing.
    uv run python -m app.workiz_import --commit        # ...and this one writes.

The owner is migrating off Workiz, and this is how his actual business data arrives
in the CRM. It runs against whatever `DATABASE_URL` points at, exactly like
`app.bootstrap`, and it is a module rather than a `ghl` CLI command **on purpose**:

  * The `ghl` CLI talks to the API over HTTP, and every write endpoint it would use
    fires an automation. `POST /api/appointments` schedules T-24h and T-1h reminder
    texts; `POST /api/contacts` notifies the team; a stage change texts the
    customer. An import that went through the front door would queue hundreds of
    messages about jobs finished months ago. See "No reminders, ever" below.
  * Merging duplicates and choosing a surviving record are not operations the API
    has, and inventing endpoints for a one-off migration would leave them behind
    afterwards.

## No reminders, ever

This is the single most dangerous thing in this file, and it is guarded three ways
rather than once:

1. **This module does not import `app.automations` or `app.queue`.** There is no
   code path from here to `enqueue()`. That is why `_thread_for` below is four
   lines of its own rather than `automations.thread_for` — the duplication is the
   price of the guarantee, and it is a deliberate purchase.
2. **The `jobs` table is counted before and after, inside the transaction.** If the
   count moves by so much as one row the whole import is ROLLED BACK and the
   command exits non-zero. That holds even if something underneath this file starts
   enqueueing on its own one day, which an import check cannot.
3. **Appointments are written through the ORM**, never through
   `POST /api/appointments`, which is the only thing that calls
   `on_appointment_booked`. There are no SQLAlchemy event listeners anywhere in
   `app/` for one to hide behind.

`test_workiz_import.py` asserts all three as behaviour: zero rows in `jobs` after a
full import that includes a future-dated appointment.

## Idempotency: one custom field

`workiz_id` — the `Client #` on a contact, the `Job #` on an opportunity. Re-running
finds the record by it and UPDATES rather than creating a second one. `workiz_*` is
a reserved, read-only namespace alongside `owen_*` (see `custom_fields.py`), so the
API cannot let anybody edit the key the next import depends on. A record created in
the CRM by hand has no `workiz_id`; that is expected and is never an error.

## What it will not do

It never deletes a contact, an opportunity or an appointment, and it never deletes a
stage that holds deals. `custom_fields.owen_call_id` is a live join key into the
telephony project (CLAUDE.md), so nothing here may take a deal out from under it.
"""
import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from . import custom_fields
from .db import SessionLocal, engine
from .models import (
    Appointment,
    Calendar,
    Contact,
    Conversation,
    ConversationEvent,
    CustomFieldDef,
    CustomFieldPipeline,
    Direction,
    EventType,
    Job,
    Opportunity,
    Pipeline,
    Stage,
)
from .phones import store_phone

# Workiz timestamps carry no offset. The business is in Bradenton FL and every job
# in the file is a van driving to a Florida address, so the wall-clock times in the
# export are Eastern. Reading them as UTC would move every appointment five hours
# and put a 4pm visit on the wrong side of the day.
WORKIZ_TZ = ZoneInfo("America/New_York")
# "Fri Jan 03, 2025 10:30 am" — the one shape in both exports, measured across all
# three datetime columns of all 389 rows.
WORKIZ_DATETIME = "%a %b %d, %Y %I:%M %p"

# The idempotency key, in the reserved namespace `custom_fields.py` guards.
WORKIZ_ID = custom_fields.IMPORT_PREFIX + "id"
# Every Workiz client number that folded into this contact, the surviving one
# included. Written so a re-run can still recognise a record whose base changed, and
# so a human can trace a merged contact back to the export.
WORKIZ_MERGED = custom_fields.IMPORT_PREFIX + "merged_ids"

# ---------------------------------------------------------------- pipelines

AHS = "Dream Team Roofing AHS"
RETAIL = "Retail"

# The AHS board as measured on the live GHL account (DECISIONS.md), minus the
# duplicates `fix_stage_names` takes out. Only used when the pipeline does not exist
# at all — on the owner's database it does, and its stages are left alone.
AHS_STAGES = [
    "New Lead",
    "Inspection",
    "Request the Approval (AHS)",
    "Approved- Repair Schedule",
    "Repair in Process",
    "Submit The Invoice",
    "Call Back",
    "AHS Upgrades",
]
# The owner's retail workflow, in his order.
RETAIL_STAGES = [
    "New Lead",
    "Inspection / Estimate",
    "Estimate Sent",
    "Follow Up",
    "Scheduled",
    "Repair in Process",
    "Invoice",
]

# Near-duplicate columns to collapse, as `(goes away, survives)`. Distinct from the
# exact-name duplicates handled generically below: "Submit Invoices" and "Submit The
# Invoice" are two spellings of one step, the CLI resolves both without complaining,
# and nothing but a human can know they mean the same thing. This list is that human
# judgement written down, and removing an entry from it is a one-line veto.
NEAR_DUPLICATE_STAGES = [("Submit Invoices", "Submit The Invoice")]

# ---------------------------------------------------------------- statuses

# A status is mapped to a MEANING, and the meaning is then spelled in the vocabulary
# of whichever pipeline the job lands on. Two steps rather than one because the two
# boards do not share a single stage name past "New Lead", and `Source` decides the
# board independently of `Status` — measured, there are six real rows whose status
# has no column on the board their source sends them to (four AHS-sourced
# "Pending (Estimate Follow Up)" and two retail-sourced "In Progress (Request
# Approval)"). A flat status->stage table cannot file those at all.
NEW_LEAD = "new_lead"
INSPECTION = "inspection"
ESTIMATE_SENT = "estimate_sent"
FOLLOW_UP = "follow_up"
SCHEDULED = "scheduled"
CALL_BACK = "call_back"
INVOICE = "invoice"

STAGE_FOR = {
    AHS: {
        NEW_LEAD: "New Lead",
        INSPECTION: "Inspection",
        # An AHS estimate goes to the warranty company for approval — that IS the
        # "estimate sent" step on this board.
        ESTIMATE_SENT: "Request the Approval (AHS)",
        # Chasing a decision on an estimate is a callback. JUDGEMENT CALL: four real
        # rows. The alternative reading files them under "Request the Approval
        # (AHS)"; both are defensible and neither is in the export.
        FOLLOW_UP: "Call Back",
        SCHEDULED: "Approved- Repair Schedule",
        CALL_BACK: "Call Back",
        INVOICE: "Submit The Invoice",
    },
    RETAIL: {
        NEW_LEAD: "New Lead",
        INSPECTION: "Inspection / Estimate",
        ESTIMATE_SENT: "Estimate Sent",
        FOLLOW_UP: "Follow Up",
        SCHEDULED: "Scheduled",
        # Retail has no callback column; chasing a customer is a follow-up.
        CALL_BACK: "Follow Up",
        INVOICE: "Invoice",
    },
}

OPEN, WON = "open", "won"

# Keyed by the status with ALL whitespace removed and lowercased, so the trailing
# spaces inside "In Progress (Request Approval  )" and any casing drift in the raw
# data land on the same entry. The key is never displayed; the raw value is what
# goes in the report.
STATUS_MAP = {
    "submitted": (NEW_LEAD, OPEN),
    "inprogress(inspections)": (INSPECTION, OPEN),
    "inprogress(requestapproval)": (ESTIMATE_SENT, OPEN),
    "inprogress(repairschedule)": (SCHEDULED, OPEN),
    "inprogress(callback)": (CALL_BACK, OPEN),
    "pending(collectbalance)": (INVOICE, OPEN),
    "pending(estimatefollowup)": (FOLLOW_UP, OPEN),
    "pending(newroofestimate)": (ESTIMATE_SENT, OPEN),
    # Work done, money not collected. OPEN, not won — the owner's distinction, and
    # the reason this row exists separately from "Done" at all.
    "donependingapproval": (INVOICE, OPEN),
    "done": (INVOICE, WON),
}
# Imported as nothing at all. Not a skip-with-a-reason: a cancelled job is a job
# that did not happen, and 49 dead cards on a board nobody asked for is worse than
# not having them.
DROPPED_STATUSES = {"canceled", "cancelled"}

# ---------------------------------------------------------------- job type

JOB_TYPE_LABEL = "Job type"
JOB_TYPE_KEY = custom_fields.slug_for(JOB_TYPE_LABEL)
# The measured set, in the order the export's own frequencies put them, so the most
# common answer is the first one offered. Anything in the file that is not here is
# appended and reported rather than refused — a dropdown that cannot express a value
# the source holds would silently drop it.
JOB_TYPES = [
    "Active Leak Repair",
    "Roof Repair",
    "Inspection - Estimate",
    "New Roof Replacement",
    "New Gutters",
    "Tile Roof Repair",
    "Solar",
    "Shingle Roof Repair",
]

# GHL's own measured calendar list carries one of these (DECISIONS.md). Reusing the
# name rather than inventing one keeps the imported visits in the place the owner
# already recognises.
IMPORT_CALENDAR = "Workiz Jobs (imported)"

# A job with no end time, or an end that is not after its start, gets this much.
DEFAULT_VISIT = timedelta(hours=2)

MERGE_NOTE_MARKER = "[Workiz import] Merged duplicate client records."

# Matching two numbers on their last ten digits is the identity rule the picker, the
# contacts search and the telephony project all already use (DECISIONS.md). Anything
# shorter is not a number we can identify anybody by.
PHONE_DIGITS = 10


# ================================================================ parsing helpers


def last10(raw: str | None) -> str | None:
    """The last ten digits, or None. The shared identity rule for a phone number."""
    digits = re.sub(r"\D", "", raw or "")
    return digits[-PHONE_DIGITS:] if len(digits) >= PHONE_DIGITS else None


def clean(value: str | None) -> str:
    return (value or "").strip()


def status_key(raw: str | None) -> str:
    """Case-insensitive, whitespace-insensitive. The raw data has both problems."""
    return re.sub(r"\s+", "", raw or "").lower()


def split_name(full: str) -> tuple[str, str]:
    """"Jane Q Public" -> ("Jane Q", "Public"). One word is all first name.

    A surname is the LAST word, not the second one: the export holds middle names,
    and "Jane Q" as a first name is a smaller error than "Q" as a surname.
    """
    parts = clean(full).split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return " ".join(parts[:-1]), parts[-1]


def parse_money(raw: str | None) -> int:
    """Dollars as written -> integer cents, with no float anywhere in the path.

    Raises ValueError, which the caller turns into a skipped row.
    """
    text = clean(raw).replace("$", "").replace(",", "")
    if not text:
        return 0
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ValueError("%r is not an amount" % clean(raw)) from None
    if not amount.is_finite():
        raise ValueError("%r is not an amount" % clean(raw))
    return int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))


def parse_dt(raw: str | None) -> datetime | None:
    """Eastern wall clock -> an aware UTC datetime. None for a blank."""
    text = clean(raw)
    if not text:
        return None
    try:
        naive = datetime.strptime(text, WORKIZ_DATETIME)
    except ValueError:
        raise ValueError("%r is not a date we can read" % text) from None
    return naive.replace(tzinfo=WORKIZ_TZ).astimezone(UTC)


_ZIP = re.compile(r"(\d{5})\s*(?:-\s*(\d{4}))?$")

# US states, by full name and by postal abbreviation, so the token in front of a ZIP
# can be told apart from a city name rather than assumed to be one. Measured need:
# the clients file writes "<street>, Florida 34203" — the combined string carries the
# STATE where a city would go, 754 times — and a handful of out-of-state customers
# write their own state the same way. Without this list every one of those lands in
# `address_city`, which then reads "Florida" on 754 contacts.
_STATE_NAMES = (
    "Alabama AL|Alaska AK|Arizona AZ|Arkansas AR|California CA|Colorado CO|"
    "Connecticut CT|Delaware DE|District of Columbia DC|Florida FL|Georgia GA|"
    "Hawaii HI|Idaho ID|Illinois IL|Indiana IN|Iowa IA|Kansas KS|Kentucky KY|"
    "Louisiana LA|Maine ME|Maryland MD|Massachusetts MA|Michigan MI|Minnesota MN|"
    "Mississippi MS|Missouri MO|Montana MT|Nebraska NE|Nevada NV|New Hampshire NH|"
    "New Jersey NJ|New Mexico NM|New York NY|North Carolina NC|North Dakota ND|"
    "Ohio OH|Oklahoma OK|Oregon OR|Pennsylvania PA|Puerto Rico PR|Rhode Island RI|"
    "South Carolina SC|South Dakota SD|Tennessee TN|Texas TX|Utah UT|Vermont VT|"
    "Virginia VA|Washington WA|West Virginia WV|Wisconsin WI|Wyoming WY"
)
# Upper-cased lookup -> the spelling we store. A record that wrote "FL" keeps "FL";
# one that wrote "Florida" keeps "Florida". Nothing is rewritten into a house style:
# the export is the customer's own address and re-spelling it is not this importer's
# job.
_STATES: dict[str, str] = {}
for _entry in _STATE_NAMES.split("|"):
    _name, _abbr = _entry.rsplit(" ", 1)
    _STATES[_name.upper()] = _name
    _STATES[_abbr] = _abbr

# "<anything> FL 34203" / "<anything> Florida 34203-1234" — the one shape worth
# pulling apart in an address that has no commas at all. Anchored on a state token
# so a street that merely ends in five digits ("PO Box 34208") is left whole.
_TRAILING_STATE_ZIP = re.compile(
    r"[\s,]+(%s)\s+(\d{5})(?:\s*-\s*(\d{4}))?$"
    % "|".join(sorted((re.escape(k) for k in _STATES), key=len, reverse=True)),
    re.IGNORECASE)


def parse_address(raw: str | None) -> dict:
    """Split the clients file's one combined string into four columns.

    Measured shapes, in the order they occur: `"<street>, <City> <ZIP>"` (751 rows),
    `"<street> ,  "` with nothing after the comma (55), `"<street>, <City>, <ST>
    <ZIP>"` (35), and a handful of one-offs.

    The rule that makes this safe: **anything not confidently identified stays in
    `street`.** A city column that is sometimes half a street name is worse than a
    street column that is sometimes the whole address, and the export is a customer's
    home — losing part of it to a tidy parse is the one outcome worth avoiding.
    """
    out = {"address_street": None, "address_city": None,
           "address_state": None, "address_postal_code": None}
    text = clean(raw)
    # Trailing "," / " ,  " means "no city was recorded", not an empty final field.
    while text.endswith(",") or text.endswith(" "):
        text = text.rstrip().rstrip(",").rstrip()
    if not text:
        return out

    parts = [p.strip() for p in text.split(",")]
    parts = [p for p in parts if p]
    if len(parts) == 1:
        # No commas to split on. Take a trailing "<state> <zip>" if one is there and
        # leave everything else in `street` — including the city, which cannot be
        # told from the rest of the street without guessing where it starts.
        whole = parts[0]
        m = _TRAILING_STATE_ZIP.search(whole)
        if m:
            out["address_state"] = _STATES[m.group(1).upper()]
            out["address_postal_code"] = m.group(2) + (
                "-" + m.group(3) if m.group(3) else "")
            whole = whole[:m.start()].strip().rstrip(",").strip()
        out["address_street"] = whole[:255] or None
        return out

    tail = parts[-1]
    zip_match = _ZIP.search(tail)
    if zip_match:
        out["address_postal_code"] = (
            zip_match.group(1) + ("-" + zip_match.group(2) if zip_match.group(2) else ""))
        tail = tail[:zip_match.start()].strip().rstrip(",").strip()

    if tail.upper() in _STATES:
        out["address_state"] = _STATES[tail.upper()]  # keeps the export's spelling
        # "street, City, FL 34203" — the city is the part before the state.
        if len(parts) >= 3:
            out["address_city"] = parts[-2][:120]
            street = ", ".join(parts[:-2])
        else:
            street = ", ".join(parts[:-1])
    elif tail:
        out["address_city"] = tail[:120]
        street = ", ".join(parts[:-1])
    else:
        # The last part was nothing but a ZIP.
        street = ", ".join(parts[:-1])

    out["address_street"] = (street or None) and street[:255]
    return out


def address_from_job(row: dict) -> dict:
    """The jobs file already has the four columns; it just needs the same shapes."""
    state = clean(row.get("State"))
    return {
        "address_street": clean(row.get("Address"))[:255] or None,
        "address_city": clean(row.get("City"))[:120] or None,
        "address_state": (_STATES.get(state.upper(), state) or None) and
                         _STATES.get(state.upper(), state)[:80],
        "address_postal_code": clean(row.get("Zip code"))[:20] or None,
    }


def pipeline_for(source: str | None) -> str:
    """The owner's routing rule, and the whole of it: AHS or everything else."""
    return AHS if clean(source).upper() == "AHS" else RETAIL


# ================================================================ the plan
#
# The plan is PURE DATA computed from the CSVs and a read-only snapshot of the
# database. Nothing in `build_plan` writes, flushes or adds, which is what makes
# "--dry-run writes absolutely nothing" a property of the shape of this file rather
# than a promise about its behaviour.


@dataclass
class Skip:
    where: str          # "jobs" / "clients"
    ident: str          # Job # or Client #, never a customer's name
    reason: str


@dataclass
class ContactPlan:
    workiz_id: str
    merged_ids: list[str]
    first_name: str
    last_name: str
    email: str | None
    phone: str | None
    source: str | None
    contact_type: str
    address: dict
    note: str | None
    existing_id: int | None = None


@dataclass
class OppPlan:
    workiz_id: str
    client_workiz_id: str
    title: str
    pipeline: str
    stage: str
    status: str
    value_cents: int
    source: str | None
    job_type: str | None
    created_at: datetime | None
    existing_id: int | None = None


@dataclass
class ApptPlan:
    job_workiz_id: str
    title: str
    starts_at: datetime
    ends_at: datetime


@dataclass
class StageFix:
    action: str         # "remove" / "rename"
    pipeline: str
    name: str
    detail: str
    stage_id: int | None = None
    new_name: str | None = None


@dataclass
class Plan:
    # What was read, so every number below can be checked against the export
    # without opening it.
    client_rows: int = 0
    job_rows: int = 0
    dropped_cancelled: int = 0
    pipelines_to_create: list[str] = field(default_factory=list)
    stages_to_create: list[tuple[str, str]] = field(default_factory=list)
    stage_fixes: list[StageFix] = field(default_factory=list)
    job_type_options: list[str] = field(default_factory=list)
    job_type_action: str = "unchanged"
    calendar_action: str = "unchanged"
    contacts: list[ContactPlan] = field(default_factory=list)
    opportunities: list[OppPlan] = field(default_factory=list)
    appointments: list[ApptPlan] = field(default_factory=list)
    past_appointments: int = 0
    skipped: list[Skip] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- reading files


def read_csv(path: str) -> list[dict]:
    # utf-8-sig: the jobs export is UTF-8 and Workiz writes a BOM, which would
    # otherwise make the first column name "﻿Job #" and every lookup miss.
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------- merging


def _completeness(row: dict) -> int:
    return sum(1 for key in ("Name", "Email", "Address", "Phone", "Ad Source")
               if clean(row.get(key)))


def merge_groups(clients: list[dict]) -> list[list[dict]]:
    """Group client rows that are the same person, most complete first.

    Grouped on the LAST TEN DIGITS of the phone — the identity rule the picker, the
    contacts search and the telephony project already share (DECISIONS.md). A row
    whose phone has fewer than ten digits is its own group: an empty match key would
    otherwise collapse every unusable number into one contact, which is how four
    unrelated customers become one.

    Inside a group the most complete record leads, ties broken by `Client #` so two
    runs over the same file produce the same base.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in clients:
        key = last10(row.get("Phone"))
        groups["p:" + key if key else "c:" + clean(row.get("Client #"))].append(row)
    ordered = []
    for key in sorted(groups):
        rows = sorted(groups[key],
                      key=lambda r: (-_completeness(r), clean(r.get("Client #"))))
        ordered.append(rows)
    return ordered


def merge_note(base: dict, others: list[dict]) -> str | None:
    """Everything the merge did not keep, as a sentence a person can act on.

    Nothing is discarded silently: a name, email or address on a losing record that
    differs from the surviving one is written out in full, with the Workiz client
    number it came from, so all of them can be reviewed by hand afterwards.
    """
    lines = []
    for row in others:
        kept = []
        for label, key, survivor in (("name", "Name", clean(base.get("Name"))),
                                     ("email", "Email", clean(base.get("Email"))),
                                     ("address", "Address", clean(base.get("Address")))):
            value = clean(row.get(key))
            if value and value.casefold() != survivor.casefold():
                kept.append("%s: %s" % (label, value))
        lines.append("  Client #%s%s" % (
            clean(row.get("Client #")),
            (" — " + "; ".join(kept)) if kept else " — no differing details"))
    if not lines:
        return None
    return "\n".join([
        MERGE_NOTE_MARKER,
        "These records shared a phone number and were combined into this contact.",
        "Kept: Client #%s. Nothing below was deleted from the export — it is "
        "recorded here so it can be reviewed." % clean(base.get("Client #")),
        "",
        *lines,
    ])


# ---------------------------------------------------------------- the builder


def build_plan(db: Session, clients: list[dict], jobs: list[dict]) -> Plan:
    """What the import WOULD do. Reads the database; writes nothing to it."""
    plan = Plan(client_rows=len(clients), job_rows=len(jobs))

    # --- existing rows, read once into dicts ---------------------------------
    # A `custom_fields->>'workiz_id'` filter would need different SQL on SQLite and
    # PostgreSQL, and the tests must get the same answer as production. At this size
    # — a few hundred rows in a single-tenant CRM — reading them is cheaper than
    # maintaining two queries.
    existing_contacts = {}
    for c in db.scalars(select(Contact)).all():
        wid = (c.custom_fields or {}).get(WORKIZ_ID)
        if wid:
            existing_contacts.setdefault(str(wid), []).append(c)
        for merged in (c.custom_fields or {}).get(WORKIZ_MERGED) or []:
            existing_contacts.setdefault(str(merged), []).append(c)
    existing_opps = {}
    for o in db.scalars(select(Opportunity)).all():
        wid = (o.custom_fields or {}).get(WORKIZ_ID)
        if wid:
            existing_opps.setdefault(str(wid), []).append(o)

    pipelines = {p.name: p for p in db.scalars(select(Pipeline)).all()}

    # --- pipelines and their stages ------------------------------------------
    for name, stages in ((AHS, AHS_STAGES), (RETAIL, RETAIL_STAGES)):
        p = pipelines.get(name)
        if p is None:
            plan.pipelines_to_create.append(name)
            plan.stages_to_create.extend((name, s) for s in stages)
            continue
        have = {s.name for s in p.stages}
        # Only the stages the routing table actually needs are added to a pipeline
        # that already exists. The owner's board is his configuration; an import is
        # not the place to impose a column list on it.
        for stage_name in stages:
            if stage_name not in have and stage_name in STAGE_FOR[name].values():
                plan.stages_to_create.append((name, stage_name))

    plan.stage_fixes = plan_stage_fixes(db, pipelines.get(AHS))

    # --- the Job type dropdown ------------------------------------------------
    found_types = []
    for row in jobs:
        t = clean(row.get("Type"))
        if t and t not in JOB_TYPES and t not in found_types:
            found_types.append(t)
    plan.job_type_options = JOB_TYPES + found_types
    if found_types:
        plan.notes.append(
            "Job type: %d value(s) in the export are not in the measured set and "
            "were added as options: %s" % (len(found_types), ", ".join(found_types)))
    existing_def = db.scalar(select(CustomFieldDef).where(
        CustomFieldDef.key == JOB_TYPE_KEY))
    plan.job_type_action = "unchanged" if (
        existing_def is not None
        and list(existing_def.options or []) == plan.job_type_options) else (
        "update" if existing_def is not None else "create")

    if db.scalar(select(Calendar).where(Calendar.name == IMPORT_CALENDAR)) is None:
        plan.calendar_action = "create"

    # --- contacts -------------------------------------------------------------
    groups = merge_groups(clients)
    # Which merged contact a job belongs to, by phone and by name. Phone wins where
    # they disagree: it is the identity rule everything else in this codebase uses,
    # and two people really do share a name.
    by_phone: dict[str, str] = {}
    by_name: dict[str, list[str]] = defaultdict(list)
    group_for: dict[str, list[dict]] = {}
    for rows in groups:
        base = rows[0]
        wid = clean(base.get("Client #"))
        group_for[wid] = rows
        for row in rows:
            key = last10(row.get("Phone"))
            if key:
                by_phone.setdefault(key, wid)
            name = clean(row.get("Name")).casefold()
            if name and wid not in by_name[name]:
                by_name[name].append(wid)

    # --- jobs, resolved to a contact -----------------------------------------
    # `jobs_for` holds the jobs that become opportunities. `has_any_job` is wider on
    # purpose: it counts EVERY row in the file, cancelled ones included, because it
    # decides `contact_type`. Somebody who booked a job and then cancelled it is
    # still a customer the business has dealt with, not a lead nobody has spoken to,
    # and the measured split the owner gave (331 Customer / 490 Lead) is counted
    # that way too.
    jobs_for: dict[str, list[dict]] = defaultdict(list)
    has_any_job: set[str] = set()
    seen_job_ids: set[str] = set()
    for row in jobs:
        job_id = clean(row.get("Job #"))
        if not job_id:
            plan.skipped.append(Skip("jobs", "<no Job #>", "the row has no Job #"))
            continue
        if job_id in seen_job_ids:
            plan.skipped.append(Skip("jobs", job_id, "duplicate Job # in the export"))
            continue
        seen_job_ids.add(job_id)

        raw_status = clean(row.get("Status"))
        key = status_key(raw_status)
        dropped = key in DROPPED_STATUSES
        if not dropped and key not in STATUS_MAP:
            plan.skipped.append(Skip(
                "jobs", job_id,
                "status %r is not in the routing table — not guessed" % raw_status))
            continue

        # Resolve the owner BEFORE dropping a cancelled row: a cancelled job still
        # decides whether its client is a Customer or a Lead.
        phone_key = last10(row.get("Phone"))
        name_key = clean(row.get("Client")).casefold()
        owner = by_phone.get(phone_key) if phone_key else None
        named = by_name.get(name_key) or []
        if owner is None:
            if len(named) == 1:
                owner = named[0]
            elif len(named) > 1:
                if not dropped:
                    plan.skipped.append(Skip(
                        "jobs", job_id,
                        "its client name matches %d client records and its phone "
                        "matches none — not guessed" % len(named)))
                continue
            else:
                if not dropped:
                    plan.skipped.append(Skip(
                        "jobs", job_id,
                        "no client record matches its phone or its name"))
                continue
        elif named and owner not in named and not dropped:
            plan.conflicts.append(
                "Job %s: phone and name point at different client records; the "
                "phone won (the identity rule everything else here uses)." % job_id)

        has_any_job.add(owner)
        if dropped:
            plan.dropped_cancelled += 1
        else:
            jobs_for[owner].append(row)

    # --- build the contact plans ---------------------------------------------
    for rows in groups:
        base = rows[0]
        wid = clean(base.get("Client #"))
        if not wid:
            plan.skipped.append(Skip("clients", "<no Client #>",
                                     "the row has no Client #"))
            continue
        others = rows[1:]

        def pick(key: str, _rows=rows) -> str:
            """The base's value, or the first non-empty one behind it."""
            for row in _rows:
                value = clean(row.get(key))
                if value:
                    return value
            return ""

        first, last = split_name(pick("Name"))
        address = parse_address(pick("Address"))
        # Blank-fill only, from this contact's earliest job: the jobs file has City
        # / State / Zip as real columns and the clients file often does not. It
        # never overrides a value the client record already carries.
        if not address["address_city"]:
            for job in sorted(jobs_for.get(wid, []),
                              key=lambda r: clean(r.get("Job Created"))):
                from_job = address_from_job(job)
                if from_job["address_city"]:
                    for k, v in from_job.items():
                        if not address[k] and k != "address_street":
                            address[k] = v
                    break

        source = pick("Ad Source")
        if not source:
            for job in jobs_for.get(wid, []):
                if clean(job.get("Source")):
                    source = clean(job.get("Source"))
                    break

        existing = existing_contacts.get(wid) or []
        for merged in (clean(r.get("Client #")) for r in others):
            existing = existing or existing_contacts.get(merged) or []
        unique = {c.id: c for c in existing}
        if len(unique) > 1:
            plan.conflicts.append(
                "Client #%s already matches %d contacts (ids %s). The lowest id is "
                "updated; the others are LEFT ALONE for a human — this importer "
                "never deletes a contact." % (
                    wid, len(unique), ", ".join(str(i) for i in sorted(unique))))

        plan.contacts.append(ContactPlan(
            workiz_id=wid,
            merged_ids=sorted(clean(r.get("Client #")) for r in rows),
            first_name=first[:120],
            last_name=last[:120],
            email=(pick("Email")[:255] or None),
            phone=store_phone(pick("Phone")),
            source=(source[:120] or None),
            contact_type="Customer" if wid in has_any_job else "Lead",
            address=address,
            note=merge_note(base, others),
            existing_id=min(unique) if unique else None,
        ))

    # --- opportunities and appointments --------------------------------------
    now = datetime.now(UTC)
    for client_wid, rows in jobs_for.items():
        for row in sorted(rows, key=lambda r: clean(r.get("Job #"))):
            job_id = clean(row.get("Job #"))
            meaning, opp_status = STATUS_MAP[status_key(row.get("Status"))]
            pipeline = pipeline_for(row.get("Source"))
            stage = STAGE_FOR[pipeline][meaning]
            try:
                value_cents = parse_money(row.get("Total"))
                created_at = parse_dt(row.get("Job Created"))
                starts_at = parse_dt(row.get("Scheduled"))
                ends_at = parse_dt(row.get("End"))
            except ValueError as e:
                plan.skipped.append(Skip("jobs", job_id, str(e)))
                continue

            name = clean(row.get("Job name"))
            if not name:
                # The measured GHL convention is "<NAME> - <something>".
                name = "%s - %s" % (clean(row.get("Client")) or "Workiz job",
                                    clean(row.get("Type")) or "Job")
            job_type = clean(row.get("Type")) or None

            existing = existing_opps.get(job_id) or []
            if len({o.id for o in existing}) > 1:
                plan.conflicts.append(
                    "Job %s already matches %d opportunities; the lowest id is "
                    "updated and the rest are left for a human — this importer "
                    "never deletes a deal (owen_call_id, CLAUDE.md)." % (
                        job_id, len({o.id for o in existing})))

            plan.opportunities.append(OppPlan(
                workiz_id=job_id,
                client_workiz_id=client_wid,
                title=name[:255],
                pipeline=pipeline,
                stage=stage,
                status=opp_status,
                value_cents=value_cents,
                source=(clean(row.get("Source"))[:120] or None),
                job_type=job_type,
                created_at=created_at,
                existing_id=min(o.id for o in existing) if existing else None,
            ))

            if starts_at is None:
                continue
            if starts_at <= now:
                # The whole point: an import must not book, or remind anybody about,
                # a visit that already happened.
                plan.past_appointments += 1
                continue
            if ends_at is None or ends_at <= starts_at:
                ends_at = starts_at + DEFAULT_VISIT
            plan.appointments.append(ApptPlan(
                job_workiz_id=job_id, title=name[:255],
                starts_at=starts_at, ends_at=ends_at))

    return plan


def plan_stage_fixes(db: Session, ahs: Pipeline | None) -> list[StageFix]:
    """Make every stage name on the AHS board unique, without moving a single deal.

    Two shapes, and they are handled differently on purpose:

    * **Exact duplicates.** The board carries two stages both named "Call Back", and
      `resolve.pick` in the CLI exits 5 (ambiguous) rather than guessing between
      them, so `ghl opps move ... --stage "Call Back"` cannot be run at all today.
      The copy holding the most deals survives; an empty duplicate is removed, and
      one that holds deals is RENAMED instead. Nothing here ever moves or deletes an
      opportunity — `custom_fields.owen_call_id` is a live join key into the
      telephony project (CLAUDE.md).
    * **Near duplicates**, listed in `NEAR_DUPLICATE_STAGES`. "Submit Invoices" and
      "Submit The Invoice" are two spellings of one step; the CLI resolves both
      without complaining, so this is a judgement call rather than a defect, and it
      only ever applies to an EMPTY column.
    """
    if ahs is None:
        return []
    counts = dict(db.execute(
        select(Opportunity.stage_id, func.count(Opportunity.id))
        .group_by(Opportunity.stage_id)).all())
    fixes: list[StageFix] = []

    by_name: dict[str, list[Stage]] = defaultdict(list)
    for s in sorted(ahs.stages, key=lambda s: (s.position, s.id)):
        by_name[s.name].append(s)

    for stages in by_name.values():
        if len(stages) < 2:
            continue
        # Most deals wins; then the leftmost column; then the lowest id.
        survivor = min(stages,
                       key=lambda s: (-counts.get(s.id, 0), s.position, s.id))
        for n, s in enumerate(st for st in stages if st.id != survivor.id):
            held = counts.get(s.id, 0)
            if held:
                fixes.append(StageFix(
                    "rename", AHS, s.name,
                    "holds %d opportunit%s, so it is renamed rather than removed — "
                    "nothing here moves a deal" % (held, "y" if held == 1 else "ies"),
                    stage_id=s.id, new_name="%s (%d)" % (s.name, n + 2)))
            else:
                fixes.append(StageFix(
                    "remove", AHS, s.name,
                    "an exact duplicate of the column at position %d, and empty — "
                    "the CLI exits 5 (ambiguous) while both exist" % survivor.position,
                    stage_id=s.id))

    live = {s.name: s for s in ahs.stages}
    for gone, survives in NEAR_DUPLICATE_STAGES:
        s = live.get(gone)
        if s is None or survives not in live:
            continue
        if counts.get(s.id, 0):
            fixes.append(StageFix(
                "keep", AHS, gone,
                "a near-duplicate of %r, but it holds %d opportunit%s — left alone, "
                "because moving deals is not this importer's decision" % (
                    survives, counts[s.id], "y" if counts[s.id] == 1 else "ies"),
                stage_id=s.id))
        else:
            fixes.append(StageFix(
                "remove", AHS, gone,
                "empty, and a near-duplicate of %r (judgement call — see "
                "NEAR_DUPLICATE_STAGES)" % survives, stage_id=s.id))
    return fixes


# ================================================================ applying


def _as_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _set(obj, attr: str, value) -> None:
    """Assign only when it changes.

    This is what makes a second run a no-op at the SQL level rather than merely at
    the row level: SQLAlchemy emits no UPDATE for an unchanged object, so
    `updated_at` (which has an `onupdate`) does not churn either.

    The datetime branch exists because SQLite hands back a NAIVE datetime for a
    `DateTime(timezone=True)` column while PostgreSQL hands back an aware one. Both
    are the same instant, and comparing them raw would make every re-import rewrite
    every row on SQLite — where the tests and the bootstrap live — while looking
    perfectly idempotent on the database that actually matters. That is precisely
    the kind of difference a test suite must not have.
    """
    current = getattr(obj, attr)
    if (isinstance(current, datetime) and isinstance(value, datetime)
            and (current.tzinfo is None) != (value.tzinfo is None)
            and _as_utc(current) == _as_utc(value)):
        return
    if current != value:
        setattr(obj, attr, value)


def _thread_for(db: Session, contact_id: int) -> Conversation:
    """The contact's thread, created on first use.

    Deliberately NOT `automations.thread_for`, which is four identical lines: this
    module must not import `app.automations`, so that there is no code path at all
    from an import to `enqueue()`. See the module docstring.
    """
    conv = db.scalar(select(Conversation).where(
        Conversation.contact_id == contact_id))
    if conv is None:
        conv = Conversation(contact_id=contact_id, last_event_at=datetime.now(UTC))
        db.add(conv)
        db.flush()
    return conv


def _write_merge_note(db: Session, contact: Contact, body: str) -> None:
    """One note per merged contact, rewritten in place on a re-run.

    A NOTE is STAFF-only on every path (DECISIONS.md, 2026-09-10), which is the
    right visibility for a block of text holding a customer's other names and
    addresses.
    """
    conv = _thread_for(db, contact.id)
    existing = None
    for ev in db.scalars(select(ConversationEvent).where(
            ConversationEvent.conversation_id == conv.id,
            ConversationEvent.type == EventType.NOTE)).all():
        if (ev.body or "").startswith(MERGE_NOTE_MARKER):
            existing = ev
            break
    if existing is not None:
        _set(existing, "body", body)
        return
    db.add(ConversationEvent(
        conversation_id=conv.id, type=EventType.NOTE, direction=Direction.OUTBOUND,
        occurred_at=datetime.now(UTC), body=body))


def apply_plan(db: Session, plan: Plan) -> dict:
    """Write the plan. The caller owns the transaction and the jobs-table guard."""
    counts = Counter()

    # --- pipelines and stages -------------------------------------------------
    pipelines = {p.name: p for p in db.scalars(select(Pipeline)).all()}
    for name in plan.pipelines_to_create:
        p = Pipeline(name=name, position=len(pipelines))
        db.add(p)
        db.flush()
        pipelines[name] = p
        counts["pipelines_created"] += 1
    for pipeline_name, stage_name in plan.stages_to_create:
        p = pipelines[pipeline_name]
        n = db.scalar(select(func.count(Stage.id)).where(
            Stage.pipeline_id == p.id)) or 0
        db.add(Stage(pipeline_id=p.id, name=stage_name, position=n))
        counts["stages_created"] += 1
    db.flush()

    for fix in plan.stage_fixes:
        if fix.action == "remove":
            stage = db.get(Stage, fix.stage_id)
            # Re-checked here, not only when the plan was built: between a dry run
            # and the real one somebody may have dropped a card on this column.
            held = db.scalar(select(func.count(Opportunity.id)).where(
                Opportunity.stage_id == fix.stage_id)) or 0
            if stage is not None and held == 0:
                db.delete(stage)
                counts["stages_removed"] += 1
        elif fix.action == "rename":
            stage = db.get(Stage, fix.stage_id)
            if stage is not None:
                _set(stage, "name", fix.new_name)
                counts["stages_renamed"] += 1
    db.flush()

    stages = {}
    for p in db.scalars(select(Pipeline)).all():
        for s in p.stages:
            stages.setdefault((p.name, s.name), s)

    # --- the Job type dropdown ------------------------------------------------
    d = db.scalar(select(CustomFieldDef).where(CustomFieldDef.key == JOB_TYPE_KEY))
    if d is None:
        n = db.scalar(select(func.count(CustomFieldDef.id))) or 0
        d = CustomFieldDef(key=JOB_TYPE_KEY, label=JOB_TYPE_LABEL,
                           field_type=CustomFieldDef.DROPDOWN,
                           options=list(plan.job_type_options), position=n,
                           entity="opportunity")
        db.add(d)
        db.flush()
        counts["custom_fields_created"] += 1
    else:
        # Options are a UNION, never a replacement: removing one would make every
        # deal already holding that answer fail its own field's validation the next
        # time somebody opened the form.
        merged = list(d.options or [])
        for option in plan.job_type_options:
            if option not in merged:
                merged.append(option)
        if merged != list(d.options or []):
            _set(d, "options", merged)
            counts["custom_fields_updated"] += 1
    for pipeline_name in (AHS, RETAIL):
        p = pipelines.get(pipeline_name) or db.scalar(
            select(Pipeline).where(Pipeline.name == pipeline_name))
        if p is None:
            continue
        link = db.scalar(select(CustomFieldPipeline).where(
            CustomFieldPipeline.field_id == d.id,
            CustomFieldPipeline.pipeline_id == p.id))
        if link is None:
            db.add(CustomFieldPipeline(field_id=d.id, pipeline_id=p.id))
    db.flush()

    calendar = db.scalar(select(Calendar).where(Calendar.name == IMPORT_CALENDAR))
    if calendar is None:
        calendar = Calendar(name=IMPORT_CALENDAR)
        db.add(calendar)
        db.flush()
        counts["calendars_created"] += 1

    # --- contacts -------------------------------------------------------------
    contacts: dict[str, Contact] = {}
    for cp in plan.contacts:
        c = db.get(Contact, cp.existing_id) if cp.existing_id else None
        if c is None:
            c = Contact(created_by="Workiz import")
            db.add(c)
            counts["contacts_created"] += 1
        else:
            counts["contacts_updated"] += 1
        _set(c, "first_name", cp.first_name)
        _set(c, "last_name", cp.last_name)
        _set(c, "email", cp.email)
        _set(c, "phone", cp.phone)
        _set(c, "source", cp.source)
        _set(c, "contact_type", cp.contact_type)
        for key, value in cp.address.items():
            _set(c, key, value)
        blob = dict(c.custom_fields or {})
        blob[WORKIZ_ID] = cp.workiz_id
        blob[WORKIZ_MERGED] = cp.merged_ids
        _set(c, "custom_fields", blob)
        db.flush()
        contacts[cp.workiz_id] = c
        if cp.note:
            _write_merge_note(db, c, cp.note)
            counts["merge_notes"] += 1
    db.flush()

    # --- opportunities --------------------------------------------------------
    positions = Counter()
    opportunities: dict[str, Opportunity] = {}
    for op in plan.opportunities:
        stage = stages.get((op.pipeline, op.stage))
        if stage is None:
            raise RuntimeError(
                "stage %r is missing from pipeline %r — the plan and the database "
                "disagree, so nothing was written" % (op.stage, op.pipeline))
        contact = contacts.get(op.client_workiz_id)
        o = db.get(Opportunity, op.existing_id) if op.existing_id else None
        if o is None:
            o = Opportunity(pipeline_id=stage.pipeline_id, stage_id=stage.id,
                            position=positions[stage.id])
            positions[stage.id] += 1
            db.add(o)
            counts["opportunities_created"] += 1
        else:
            counts["opportunities_updated"] += 1
            _set(o, "pipeline_id", stage.pipeline_id)
            _set(o, "stage_id", stage.id)
        _set(o, "title", op.title)
        _set(o, "contact_id", contact.id if contact else None)
        _set(o, "value_cents", op.value_cents)
        _set(o, "status", op.status)
        _set(o, "source", op.source)
        _set(o, "created_by", "Workiz import")
        if op.created_at is not None:
            # The board and the dashboard filter on creation date. Without this,
            # 340 deals spanning two years all look like they were created today.
            _set(o, "created_at", op.created_at)
        blob = dict(o.custom_fields or {})
        blob[WORKIZ_ID] = op.workiz_id
        if op.job_type:
            blob[JOB_TYPE_KEY] = op.job_type
        _set(o, "custom_fields", blob)
        db.flush()
        opportunities[op.workiz_id] = o
    db.flush()

    # --- appointments ---------------------------------------------------------
    for ap in plan.appointments:
        o = opportunities.get(ap.job_workiz_id)
        if o is None:
            continue
        a = db.scalar(select(Appointment).where(
            Appointment.opportunity_id == o.id,
            Appointment.calendar_id == calendar.id))
        if a is None:
            a = Appointment(opportunity_id=o.id, calendar_id=calendar.id)
            db.add(a)
            counts["appointments_created"] += 1
        else:
            counts["appointments_updated"] += 1
        _set(a, "title", ap.title)
        _set(a, "contact_id", o.contact_id)
        _set(a, "starts_at", ap.starts_at)
        _set(a, "ends_at", ap.ends_at)
        _set(a, "status", "confirmed")
    db.flush()
    return dict(counts)


# ================================================================ preflight


REQUIRED_SCHEMA = {
    "contacts": ("address_street", "address_city", "address_state",
                 "address_postal_code"),
    "appointments": ("opportunity_id",),
    "custom_field_defs": (),
    "custom_field_pipelines": (),
}


def preflight(bind) -> list[str]:
    """What is missing from the database, as sentences.

    Two migrations have to be in place before this can run: the one that adds the
    address columns, and `feature/opportunity-fields`' own, which creates the custom
    field tables and `appointments.opportunity_id`. Saying so is much better than
    the OperationalError somewhere in the middle of writing 800 contacts.
    """
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    problems = []
    for table, columns in REQUIRED_SCHEMA.items():
        if table not in tables:
            problems.append(
                "table %r does not exist — run `alembic upgrade head`" % table)
            continue
        have = {c["name"] for c in insp.get_columns(table)}
        for column in columns:
            if column not in have:
                problems.append("%s.%s does not exist — run `alembic upgrade head`"
                                % (table, column))
    return problems


# ================================================================ the report


def _plural(n: int, word: str) -> str:
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def render(plan: Plan, counts: dict | None, *, committed: bool) -> str:
    """The summary a human checks against the source numbers.

    Deliberately free of customer data: counts, Workiz record numbers, stage names.
    No name, phone, email or address appears anywhere in it — the report gets pasted
    into tickets and chat windows, and the export does not.
    """
    out = []
    say = out.append
    say("=" * 72)
    say("WORKIZ IMPORT — %s" % ("COMMITTED" if committed else "DRY RUN (nothing written)"))
    say("=" * 72)

    say("")
    say("Read from the export")
    say("  %-6d client rows" % plan.client_rows)
    say("  %-6d job rows" % plan.job_rows)
    say("  %-6d of those cancelled — imported as nothing at all" % plan.dropped_cancelled)
    say("  %-6d job rows skipped with a reason (listed below)" % sum(
        1 for skip in plan.skipped if skip.where == "jobs"))
    say("  %-6d job rows become opportunities" % len(plan.opportunities))

    say("")
    say("Pipelines")
    for name in plan.pipelines_to_create:
        say("  create pipeline  %s" % name)
    for pipeline_name, stage_name in plan.stages_to_create:
        say("  create stage     %s / %s" % (pipeline_name, stage_name))
    if not plan.pipelines_to_create and not plan.stages_to_create:
        say("  both pipelines and every stage the routing needs already exist")

    say("")
    say("Stage-name duplicates on %s" % AHS)
    if not plan.stage_fixes:
        say("  none — every stage name is already unique")
    for fix in plan.stage_fixes:
        say("  %-7s %-28s %s" % (
            fix.action.upper(),
            fix.name + (" -> " + fix.new_name if fix.new_name else ""),
            fix.detail))

    say("")
    say("Custom field")
    say("  %-8s %s (dropdown, %s)" % (
        plan.job_type_action, JOB_TYPE_KEY, _plural(len(plan.job_type_options),
                                                    "option")))
    say("  %-8s calendar %r" % (plan.calendar_action, IMPORT_CALENDAR))

    say("")
    say("Contacts")
    created = sum(1 for c in plan.contacts if c.existing_id is None)
    merged = [c for c in plan.contacts if len(c.merged_ids) > 1]
    collapsed = sum(len(c.merged_ids) - 1 for c in merged)
    types = Counter(c.contact_type for c in plan.contacts)
    say("  %-6d contacts in total" % len(plan.contacts))
    say("  %-6d to create" % created)
    say("  %-6d to update (already carry a %s)" % (len(plan.contacts) - created,
                                                   WORKIZ_ID))
    say("  %-6d of them merged: %d client records sharing %d phone numbers, so %d "
        "rows collapse" % (len(merged), collapsed + len(merged), len(merged),
                           collapsed))
    say("  %-6d Customer (has at least one job)" % types.get("Customer", 0))
    say("  %-6d Lead (no job)" % types.get("Lead", 0))
    say("  %-6d merge notes to write" % len(merged))

    say("")
    say("Opportunities")
    created = sum(1 for o in plan.opportunities if o.existing_id is None)
    say("  %-6d in total (%d to create, %d to update)"
        % (len(plan.opportunities), created, len(plan.opportunities) - created))
    per = Counter((o.pipeline, o.stage, o.status) for o in plan.opportunities)
    for (pipeline_name, stage_name, status) in sorted(per):
        say("    %-26s %-28s %-5s %d"
            % (pipeline_name, stage_name, status, per[(pipeline_name, stage_name, status)]))
    by_pipeline = Counter(o.pipeline for o in plan.opportunities)
    for name in sorted(by_pipeline):
        say("  %-26s %d" % (name, by_pipeline[name]))
    total = sum(o.value_cents for o in plan.opportunities)
    say("  value %d cents ($%s)" % (total, format(Decimal(total) / 100, ",.2f")))

    say("")
    say("Appointments")
    say("  %-6d to create or update — every one is in the FUTURE" % len(plan.appointments))
    say("  %-6d jobs scheduled in the past: NO appointment, and no reminder"
        % plan.past_appointments)
    say("  reminders scheduled: 0, always. This importer cannot enqueue a job.")

    say("")
    say("Skipped rows (%d)" % len(plan.skipped))
    reasons = Counter(s.reason for s in plan.skipped)
    for reason, n in reasons.most_common():
        say("  %-4d %s" % (n, reason))
    for s in plan.skipped:
        say("    %-8s %-10s %s" % (s.where, s.ident, s.reason))

    if plan.conflicts:
        say("")
        say("Conflicts a human should look at (%d)" % len(plan.conflicts))
        for c in plan.conflicts:
            say("  - %s" % c)

    if plan.notes:
        say("")
        say("Notes")
        for n in plan.notes:
            say("  - %s" % n)

    if counts is not None:
        say("")
        say("Written")
        for key in sorted(counts):
            say("  %-26s %d" % (key, counts[key]))

    say("")
    say("=" * 72)
    if not committed:
        say("DRY RUN. Nothing above was written. Re-run with --commit to apply.")
        say("=" * 72)
    return "\n".join(out)


def as_json(plan: Plan, counts: dict | None) -> dict:
    """The same numbers, for a machine. Still carries no customer data."""
    return {
        "pipelines_to_create": plan.pipelines_to_create,
        "stages_to_create": [list(s) for s in plan.stages_to_create],
        "stage_fixes": [{"action": f.action, "stage": f.name,
                         "new_name": f.new_name, "why": f.detail}
                        for f in plan.stage_fixes],
        "contacts": {
            "total": len(plan.contacts),
            "create": sum(1 for c in plan.contacts if c.existing_id is None),
            "update": sum(1 for c in plan.contacts if c.existing_id is not None),
            "merged": sum(1 for c in plan.contacts if len(c.merged_ids) > 1),
            "records_collapsed": sum(len(c.merged_ids) - 1 for c in plan.contacts),
            "by_type": dict(Counter(c.contact_type for c in plan.contacts)),
        },
        "opportunities": {
            "total": len(plan.opportunities),
            "create": sum(1 for o in plan.opportunities if o.existing_id is None),
            "by_pipeline": dict(Counter(o.pipeline for o in plan.opportunities)),
            "by_stage": {"%s / %s" % k: v for k, v in
                         Counter((o.pipeline, o.stage)
                                 for o in plan.opportunities).items()},
            "by_status": dict(Counter(o.status for o in plan.opportunities)),
            "value_cents": sum(o.value_cents for o in plan.opportunities),
        },
        "appointments": {"create": len(plan.appointments),
                         "past_not_created": plan.past_appointments,
                         "reminders_enqueued": 0},
        "skipped": [{"file": s.where, "id": s.ident, "reason": s.reason}
                    for s in plan.skipped],
        "conflicts": plan.conflicts,
        "notes": plan.notes,
        "written": counts,
    }


# ================================================================ entry point


DEFAULT_CLIENTS = os.path.join(os.path.expanduser("~"), "workiz",
                               "workiz_clients.csv")
DEFAULT_JOBS = os.path.join(os.path.expanduser("~"), "workiz", "workiz_jobs.csv")


def run(clients_path: str, jobs_path: str, *, commit: bool,
        stream=sys.stdout, as_json_output: bool = False) -> int:
    problems = preflight(engine)
    if problems:
        for p in problems:
            print("PREFLIGHT: %s" % p, file=sys.stderr)
        return 6

    try:
        clients = read_csv(clients_path)
        jobs = read_csv(jobs_path)
    except OSError as e:
        print("cannot read the export: %s" % e, file=sys.stderr)
        return 4

    with SessionLocal() as db:
        # The reminder guard. Counted inside the same transaction the import runs
        # in, so a job row appearing by any route at all takes the whole import
        # down with it rather than texting a customer about a roof we fixed in
        # March. See the module docstring.
        jobs_before = db.scalar(select(func.count(Job.id))) or 0

        plan = build_plan(db, clients, jobs)
        counts = None
        if commit:
            try:
                counts = apply_plan(db, plan)
            except Exception:
                db.rollback()
                raise
            jobs_after = db.scalar(select(func.count(Job.id))) or 0
            if jobs_after != jobs_before:
                db.rollback()
                print("ABORTED: the import would have queued %d background job(s). "
                      "Nothing was written. An import must never cause a message to "
                      "a customer." % (jobs_after - jobs_before), file=sys.stderr)
                return 8
            db.commit()
        else:
            # Belt and braces. `build_plan` adds nothing, but a session that read
            # rows is rolled back rather than merely dropped, so a stray autoflush
            # could not reach the database even in principle.
            db.rollback()

        if as_json_output:
            print(json.dumps(as_json(plan, counts), indent=2, default=str),
                  file=stream)
        else:
            print(render(plan, counts, committed=commit), file=stream)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="app.workiz_import",
        description="Import the Workiz export. DRY RUN unless --commit is given.")
    p.add_argument("--clients", default=DEFAULT_CLIENTS,
                   help="path to workiz_clients.csv (default: %(default)s)")
    p.add_argument("--jobs", default=DEFAULT_JOBS,
                   help="path to workiz_jobs.csv (default: %(default)s)")
    p.add_argument("--dry-run", action="store_true", default=True,
                   help="the default; print what would happen and write nothing")
    p.add_argument("--commit", action="store_true",
                   help="actually write. Without this the database is not touched.")
    p.add_argument("--json", action="store_true", dest="as_json",
                   help="print the summary as JSON instead of text")
    args = p.parse_args(argv)
    return run(args.clients, args.jobs, commit=args.commit,
               as_json_output=args.as_json)


if __name__ == "__main__":
    sys.exit(main())

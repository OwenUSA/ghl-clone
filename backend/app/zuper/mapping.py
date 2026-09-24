"""What each record looks like to the sync, on both sides, and who owns which field.

A *view* is a flat dict of the fields that are synced, spelled the same way whichever side
it was read from — so a CRM view and a Zuper view of the same record can be compared field
by field, hashed, and merged against the `base` the two last agreed on. Nothing here talks
to Zuper or writes anything; `engine.py` does.

Zuper field names below are from Zuper's public reference and are UNVERIFIED against the
live account (DECISIONS.md, 2026-09-16, lists each and how the first read-only probe checks
it). They are read defensively: a missing key reads as empty, never as an error.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Appointment,
    CompanyCamLink,
    Contact,
    CustomFieldDef,
    Opportunity,
    OpportunityNote,
    OpportunityTask,
    User,
    ZuperDocument,
    ZuperMapping,
)
from . import config

SYNC_CREATED_BY = "Zuper sync"

# ------------------------------------------------------------------------ the owner's lists

AHS_PIPELINE = "Dream Team Roofing AHS"
RETAIL_PIPELINE = "Retail"
# Pipeline name -> the Zuper job category the setup command creates for it.
CATEGORIES = {AHS_PIPELINE: "AHS", RETAIL_PIPELINE: "Retail"}
# Zuper categories the CRM only COPIES (2026-09-24): the owner's six regional pipelines, made
# in Zuper with Retail's statuses. `python -m app.zuper.mirror` creates a CRM pipeline of the
# same name for each; setup, the day-one load and Send to Zuper never touch them.
MIRROR_ONLY_CATEGORIES = {name: name for name in (
    "Miami Retail Repair", "Miami Retail Roof Replacement", "Miami Gutters",
    "Sarasota Repairs", "Sarasota Roof Replacement", "Sarasota Gutters")}


def mirrored_categories() -> dict[str, str]:
    """Every CRM pipeline -> Zuper category pair the one-way mirror lines up."""
    return {**CATEGORIES, **MIRROR_ONLY_CATEGORIES}

LEAD_TAG = "Lead"

# CRM lead source -> Zuper lead source name, where the spelling differs (the owner's list).
SOURCE_NAMES = {
    "Existig Customer": "Existing Customer",
    "CL- ADS": "Craigslist Ads",
    "FB": "Facebook",
    "yelp": "Yelp",
    "Sarasota GMB location": "Sarasota GMB",
}
ZUPER_LEAD_SOURCES = ["AHS", "Google", "Existing Customer", "Craigslist Ads", "DTR Website",
                      "AI", "Facebook", "Yelp", "Sarasota GMB", "B2B - Helios"]

CUSTOMER_FIELDS = ["CRM Contact ID", "CRM Link"]
JOB_CRM_FIELDS = ["CRM Opportunity ID", "CRM Link", "Technicians", "Workiz Job #",
                  "AHS Job ID", "CompanyCam Project", "Job Type"]

# The call Checklist: CRM question key -> the exact Zuper label (zuper-checklist.md, D).
# Keyed by the CRM KEY, which never changes, so the owner rewording a question in the CRM
# does not break the pairing.
CHECKLIST = [
    ("checklist_service_address_verified", "Service address verified"),
    ("checklist_second_phone", "Second phone number"),
    ("checklist_email_verified", "Email verified"),
    ("checklist_whats_happening", "What's happening there?"),
    ("checklist_leak_count", "How many leaks?"),
    ("checklist_leak_location", "Where is the leak located inside the house?"),
    ("checklist_roof_age", "How old is the roof?"),
    ("checklist_previous_repair", "Any previous repair?"),
    ("checklist_previous_repair__details", "Any previous repair? - details"),
    ("checklist_roof_material", "Roof material"),
    ("checklist_stories", "How many stories?"),
    ("checklist_occupancy", "Does the customer live there, or are there tenants?"),
    ("checklist_leak_started", "When did the leak start?"),
    ("checklist_first_time_ahs", "First time using AHS for roofing?"),
    ("checklist_explained_ahs_process", "Explained the AHS repair process"),
    ("checklist_introduced_antonio", "Introduced Antonio"),
    ("checklist_preferred_inspection", "Preferred inspection day & time"),
]
CHECKLIST_LABELS = dict(CHECKLIST)

# Zuper field types as the setup check expects them (label -> (type words, options)).
# "Single item" is a one-choice list; the type names Zuper reports are UNVERIFIED, so the
# check accepts any of the listed spellings.
SINGLE_LINE = ("SINGLE_LINE", "TEXT", "SINGLE LINE", "SINGLE_LINE_TEXT")
MULTI_LINE = ("MULTI_LINE", "TEXTAREA", "MULTI LINE", "MULTI_LINE_TEXT", "PARAGRAPH")
NUMBER = ("NUMBER", "NUMERIC", "DECIMAL", "INTEGER")
SINGLE_ITEM = ("SINGLE_ITEM", "DROPDOWN", "SINGLE_SELECT", "RADIO", "SINGLE ITEM", "SELECT")
YES_NO = ["Yes", "No"]
CHECKLIST_TYPES: dict[str, tuple[tuple[str, ...], list[str]]] = {
    "Service address verified": (SINGLE_ITEM, YES_NO),
    "Second phone number": (SINGLE_LINE, []),
    "Email verified": (SINGLE_ITEM, YES_NO),
    "What's happening there?": (MULTI_LINE, []),
    "How many leaks?": (NUMBER, []),
    "Where is the leak located inside the house?": (SINGLE_LINE, []),
    "How old is the roof?": (SINGLE_ITEM, ["Under 5 yrs", "5-10 yrs", "10-15 yrs",
                                           "15-20 yrs", "20+ yrs", "Unknown"]),
    "Any previous repair?": (SINGLE_ITEM, ["Yes", "No", "Unknown"]),
    "Any previous repair? - details": (MULTI_LINE, []),
    "Roof material": (SINGLE_ITEM, ["Shingle", "Tile", "Flat (single-ply TPO)", "Metal",
                                    "Other"]),
    "How many stories?": (SINGLE_ITEM, ["1", "2", "3+"]),
    "Does the customer live there, or are there tenants?": (
        SINGLE_ITEM, ["Owner lives there", "Tenants", "Vacant"]),
    "When did the leak start?": (SINGLE_LINE, []),
    "First time using AHS for roofing?": (SINGLE_ITEM, YES_NO),
    "Explained the AHS repair process": (SINGLE_ITEM, YES_NO),
    "Introduced Antonio": (SINGLE_ITEM, YES_NO),
    "Preferred inspection day & time": (SINGLE_LINE, []),
}

WORKIZ_ID = "workiz_id"
WORKIZ_TECH = "workiz_tech"
WORKIZ_APPOINTMENT = "workiz_appointment"
AHS_JOB_ID = "ahs_job_id"
JOB_TYPE_KEY = "job_type"


# ------------------------------------------------------------------------ small helpers

def digest(view: dict) -> str:
    return hashlib.sha256(json.dumps(view, sort_keys=True, default=str,
                                     ensure_ascii=False).encode()).hexdigest()


def text(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def last10(raw: Any) -> str | None:
    digits = re.sub(r"\D", "", str(raw or ""))
    return digits[-10:] if len(digits) >= 10 else (digits or None)


def aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def iso_minute(dt: datetime | None) -> str | None:
    """An instant as UTC ISO to the minute — the precision both calendars keep."""
    dt = aware(dt)
    if dt is None:
        return None
    return dt.astimezone(UTC).replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    raw = value.strip().replace(" ", "T", 1) if "T" not in value else value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def canonical_source(name: str | None) -> str | None:
    name = text(name)
    return SOURCE_NAMES.get(name, name) if name else None


def dash(value: str) -> str:
    """The CRM's Checklist options use en dashes; Zuper's use hyphens ("5-10 yrs")."""
    return value.replace(chr(0x2013), "-").replace(chr(0x2014), "-")


def crm_link(kind: str, record_id: int) -> str:
    base = config.crm_base_url()
    if kind == "contact":
        return "%s/contacts?contact=%d" % (base, record_id)
    return "%s/opportunities?opportunity=%d" % (base, record_id)


def custom_values(record: dict) -> dict[str, Any]:
    """Zuper custom fields as {label: value}. Zuper sends a list of {label, value, ...}."""
    out: dict[str, Any] = {}
    fields = record.get("custom_fields") or []
    if isinstance(fields, dict):
        return {str(k): v for k, v in fields.items()}
    for f in fields:
        if isinstance(f, dict) and f.get("label"):
            out[str(f["label"])] = f.get("value")
    return out


def custom_list(values: dict[str, Any]) -> list[dict]:
    return [{"label": label, "value": "" if value is None else str(value)}
            for label, value in values.items()]


def uid(record: dict, *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, dict):
            value = value.get(key) or value.get("uid")
        if isinstance(value, str) and value:
            return value
    return None


def mapping_for(db: Session, crm_type: str, crm_id: int) -> ZuperMapping | None:
    with db.no_autoflush:
        return db.scalar(select(ZuperMapping).where(ZuperMapping.crm_type == crm_type,
                                                    ZuperMapping.crm_id == crm_id))


def mapping_by_uid(db: Session, zuper_type: str, zuper_uid: str) -> ZuperMapping | None:
    with db.no_autoflush:
        return db.scalar(select(ZuperMapping).where(ZuperMapping.zuper_type == zuper_type,
                                                    ZuperMapping.zuper_uid == zuper_uid))


# ------------------------------------------------------------------------ contacts

CONTACT_FIELDS = ["first_name", "last_name", "email", "phone", "company", "street", "city",
                  "state", "zip", "source"]


def contact_view(db: Session, c: Contact) -> dict:
    return {
        "first_name": text(c.first_name), "last_name": text(c.last_name),
        "email": text(c.email), "phone": last10(c.phone), "company": text(c.business_name),
        "street": text(c.address_street), "city": text(c.address_city),
        "state": text(c.address_state), "zip": text(c.address_postal_code),
        "source": canonical_source(c.source),
    }


def customer_view(record: dict, sources_by_uid: dict[str, str]) -> dict:
    phones = record.get("customer_contact_no") or {}
    address = record.get("customer_address") or {}
    if isinstance(address, list):
        address = address[0] if address else {}
    source = record.get("customer_source") or record.get("source")
    source_name = None
    if isinstance(source, dict):
        source_name = source.get("source_name") or source.get("name")
        source = source.get("source_uid") or source.get("uid")
    if not source_name:
        source_uid = record.get("source_uid") or (source if isinstance(source, str) else None)
        source_name = sources_by_uid.get(source_uid or "")
    return {
        "first_name": text(record.get("customer_first_name")),
        "last_name": text(record.get("customer_last_name")),
        "email": text(record.get("customer_email")),
        "phone": last10(phones.get("mobile") if isinstance(phones, dict) else None),
        "company": text(record.get("customer_company_name")),
        "street": text(address.get("street")), "city": text(address.get("city")),
        "state": text(address.get("state")), "zip": text(address.get("zip_code")),
        "source": text(source_name),
    }


def e164(ten: str | None) -> str | None:
    if not ten:
        return None
    return "+1" + ten if len(ten) == 10 else ten


def customer_payload(c: Contact, view: dict, *, sources_by_name: dict[str, str],
                     existing_tags: list[str] | None = None) -> dict:
    # Tags are Zuper's own; the sync sends back whatever the customer already carries. (Leads
    # never reach Zuper since 2026-09-16, so there is no "Lead" tag to maintain.)
    tags = list(existing_tags or [])
    body: dict[str, Any] = {
        # Zuper requires a first name; a contact with none is sent its last name, else "—".
        "customer_first_name": view.get("first_name") or view.get("last_name") or "—",
        "customer_last_name": view.get("last_name") or "",
        "customer_email": view.get("email") or "",
        "customer_contact_no": {"mobile": e164(view.get("phone")) or ""},
        "customer_company_name": view.get("company") or "",
        "customer_address": {"street": view.get("street") or "", "city": view.get("city") or "",
                             "state": view.get("state") or "",
                             "zip_code": view.get("zip") or "", "country": "US"},
        "customer_tags": tags,
        "custom_fields": custom_list({"CRM Contact ID": str(c.id),
                                      "CRM Link": crm_link("contact", c.id)}),
    }
    source_uid = sources_by_name.get(view.get("source") or "")
    if source_uid:
        body["source_uid"] = source_uid
    return body


# ------------------------------------------------------------------------ opportunities

JOB_ADDRESS = ["street", "city", "state", "zip"]


def is_workiz_card(o: Opportunity) -> bool:
    return bool((o.custom_fields or {}).get(WORKIZ_ID))


def technicians(db: Session, o: Opportunity) -> str | None:
    """The card's technicians: Workiz's names, then the users its visits are assigned to."""
    names: list[str] = []
    raw = (o.custom_fields or {}).get(WORKIZ_TECH)
    if isinstance(raw, list):
        names.extend(str(n).strip() for n in raw if str(n).strip())
    elif isinstance(raw, str) and raw.strip():
        names.extend(n.strip() for n in raw.split(",") if n.strip())
    with db.no_autoflush:
        assigned = db.scalars(select(User.name).join(
            Appointment, Appointment.assigned_user_id == User.id).where(
            Appointment.opportunity_id == o.id, Appointment.status != "cancelled")
            .order_by(Appointment.starts_at)).all()
    for name in assigned:
        if name and name not in names:
            names.append(name)
    return ", ".join(names) or None


def companycam_url(db: Session, o: Opportunity) -> str | None:
    with db.no_autoflush:
        project = db.scalar(select(CompanyCamLink.project_id).where(
            CompanyCamLink.opportunity_id == o.id, CompanyCamLink.unlinked_at.is_(None))
            .order_by(CompanyCamLink.id))
    return "https://app.companycam.com/projects/%s" % project if project else None


def _checklist_defs(db: Session) -> dict[str, CustomFieldDef]:
    keys = [k for k, _ in CHECKLIST if not k.endswith("__details")]
    with db.no_autoflush:
        return {d.key: d for d in db.scalars(select(CustomFieldDef).where(
            CustomFieldDef.key.in_(keys))).all()}


def checklist_to_zuper(d: CustomFieldDef | None, key: str, value: Any) -> str | None:
    """A CRM answer spelled the way the Zuper field holds it."""
    if value is None or value == "":
        return None
    if d is not None and d.field_type == CustomFieldDef.BOOLEAN and not key.endswith("__details"):
        if isinstance(value, bool):
            return "Yes" if value else "No"
        return "Yes" if str(value).strip().lower() in {"true", "yes", "1", "on"} else "No"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return dash(str(value)).strip() or None


def checklist_to_crm(d: CustomFieldDef | None, key: str, value: Any) -> tuple[bool, Any]:
    """(ok, answer) — a Zuper value as the CRM question stores it; ok False when the CRM
    question cannot hold it (an option it does not offer)."""
    if value is None or str(value).strip() == "":
        return True, None
    raw = str(value).strip()
    if key.endswith("__details") or d is None:
        return True, raw
    if d.field_type == CustomFieldDef.BOOLEAN:
        if raw.lower() in {"yes", "true"}:
            return True, True
        if raw.lower() in {"no", "false"}:
            return True, False
        return False, None
    if d.field_type == CustomFieldDef.NUMBER:
        try:
            number = float(raw)
        except ValueError:
            return False, None
        return True, int(number) if number.is_integer() else number
    if d.field_type == CustomFieldDef.DROPDOWN:
        for option in d.options or []:
            if dash(option) == dash(raw):
                return True, option
        return False, None
    return True, raw


def documents_value(db: Session, job_uid: str | None) -> int | None:
    """The job value Zuper's documents set: the invoices' total when there is an invoice,
    else the approved quotes' total, else None (no document yet: the CRM's value stands)."""
    if not job_uid:
        return None
    with db.no_autoflush:
        docs = db.scalars(select(ZuperDocument).where(ZuperDocument.job_uid == job_uid,
                                                      ZuperDocument.removed_in_zuper.is_(False))).all()
    invoices = [d for d in docs if d.kind == ZuperDocument.INVOICE and d.total_cents is not None
                and (d.status or "").upper() not in INVOICE_VOID]
    if invoices:
        return sum(d.total_cents for d in invoices)
    approved = [d for d in docs if d.kind == ZuperDocument.QUOTE and d.total_cents is not None
                and (d.status or "").upper() == "APPROVED"]
    if approved:
        return sum(d.total_cents for d in approved)
    return None


INVOICE_VOID = {"CANCELED", "CANCELLED", "ARCHIVED", "BAD_DEBT", "DRAFT"}


def opportunity_view(db: Session, o: Opportunity, job_uid: str | None) -> dict:
    blob = o.custom_fields or {}
    defs = _checklist_defs(db)
    view: dict[str, Any] = {
        "title": text(o.title), "stage": o.stage_id,
        "street": text(o.address_street), "city": text(o.address_city),
        "state": text(o.address_state), "zip": text(o.address_postal_code),
    }
    with db.no_autoflush:
        owner = db.get(User, o.owner_id) if o.owner_id else None
    view["owner_email"] = (owner.email.lower() if owner and owner.email else None)
    for key, label in CHECKLIST:
        base_key = key.removesuffix("__details")
        view["cl:" + label] = checklist_to_zuper(defs.get(base_key), key, blob.get(key))
    wid = blob.get(WORKIZ_ID)
    view.update({
        "crm:CRM Opportunity ID": str(o.id),
        "crm:CRM Link": crm_link("opportunity", o.id),
        "crm:Technicians": technicians(db, o),
        "crm:Workiz Job #": text(wid),
        "crm:AHS Job ID": text(blob.get(AHS_JOB_ID)),
        "crm:CompanyCam Project": companycam_url(db, o),
        "crm:Job Type": text(blob.get(JOB_TYPE_KEY)),
    })
    value = documents_value(db, job_uid)
    if value is not None:
        view["value"] = o.value_cents
    return view


def stage_for_status(db: Session, status_uid: str | None) -> int | None:
    if not status_uid:
        return None
    m = mapping_by_uid(db, "status", status_uid)
    return m.crm_id if m else None


def job_status_uid(record: dict) -> str | None:
    status = record.get("current_job_status") or record.get("job_status") or {}
    if isinstance(status, list):
        status = status[-1] if status else {}
    if isinstance(status, dict):
        return status.get("status_uid") or status.get("uid")
    return status if isinstance(status, str) else None


def job_category_uid(record: dict) -> str | None:
    category = record.get("job_category")
    if isinstance(category, dict):
        return category.get("category_uid") or category.get("uid")
    return category if isinstance(category, str) else None


def job_view(db: Session, record: dict, job_uid: str | None) -> dict:
    address = record.get("customer_address") or record.get("job_address") or {}
    if isinstance(address, list):
        address = address[0] if address else {}
    values = custom_values(record)
    assigned = record.get("assigned_to") or []
    email = None
    for a in assigned if isinstance(assigned, list) else []:
        user = a.get("user") if isinstance(a, dict) and isinstance(a.get("user"), dict) else a
        if isinstance(user, dict) and user.get("email"):
            email = str(user["email"]).lower()
            break
    view: dict[str, Any] = {
        "title": text(record.get("job_title")),
        "stage": stage_for_status(db, job_status_uid(record)),
        "street": text(address.get("street")), "city": text(address.get("city")),
        "state": text(address.get("state")), "zip": text(address.get("zip_code")),
        "owner_email": email,
    }
    for _key, label in CHECKLIST:
        v = values.get(label)
        view["cl:" + label] = dash(str(v)).strip() or None if v not in (None, "") else None
    for label in JOB_CRM_FIELDS:
        view["crm:" + label] = text(values.get(label))
    value = documents_value(db, job_uid)
    if value is not None:
        view["value"] = value
    return view


def assigned_user_uids(record: dict) -> list[str]:
    out = []
    for a in record.get("assigned_to") or []:
        if not isinstance(a, dict):
            continue
        user = a.get("user") if isinstance(a.get("user"), dict) else a
        value = user.get("user_uid") or user.get("uid")
        if value:
            out.append(str(value))
    return out


def job_payload(view: dict, *, category_uid: str | None, customer_uid: str | None,
                assigned_user_uid: str | None) -> dict:
    values = {label: view.get("cl:" + label) for _, label in CHECKLIST}
    values.update({label: view.get("crm:" + label) for label in JOB_CRM_FIELDS})
    body: dict[str, Any] = {
        "job_title": view.get("title") or "Untitled job",
        "job_priority": "MEDIUM",
        "customer_address": {"street": view.get("street") or "", "city": view.get("city") or "",
                             "state": view.get("state") or "",
                             "zip_code": view.get("zip") or "", "country": "US"},
        "custom_fields": custom_list(values),
        "assigned_to": [{"user_uid": assigned_user_uid}] if assigned_user_uid else [],
    }
    if category_uid:
        body["job_category"] = category_uid
    if customer_uid:
        body["customer"] = customer_uid
    return body


# ------------------------------------------------------------------------ appointments

def appointment_view(a: Appointment) -> dict:
    return {"title": text(a.title), "starts_at": iso_minute(a.starts_at),
            "ends_at": iso_minute(a.ends_at), "cancelled": a.status == "cancelled"}


def zuper_appointment_view(record: dict) -> dict:
    start = parse_time(record.get("start_time") or record.get("scheduled_start_time"))
    end = parse_time(record.get("end_time") or record.get("scheduled_end_time"))
    status = str(record.get("appointment_status") or record.get("status") or "").upper()
    return {"title": text(record.get("appointment_title") or record.get("title")),
            "starts_at": iso_minute(start), "ends_at": iso_minute(end),
            "cancelled": status in {"CANCELED", "CANCELLED"} or bool(record.get("is_deleted"))}


def appointment_payload(view: dict, job_uid: str) -> dict:
    return {"job_uid": job_uid, "appointment_title": view.get("title") or "Visit",
            "start_time": view.get("starts_at"), "end_time": view.get("ends_at")}


def is_workiz_appointment(db: Session, a: Appointment) -> bool:
    if a.opportunity_id is None:
        return False
    with db.no_autoflush:
        o = db.get(Opportunity, a.opportunity_id)
    record = (o.custom_fields or {}).get(WORKIZ_APPOINTMENT) if o else None
    return isinstance(record, dict) and record.get("id") == a.id


# ------------------------------------------------------------------------ notes and tasks

def note_view(n: OpportunityNote) -> dict:
    return {"body": text(n.body)}


def zuper_note_view(record: dict) -> dict:
    return {"body": text(record.get("note") or record.get("note_text") or record.get("body"))}


def note_payload(view: dict) -> dict:
    # Every CRM opportunity note is staff-only, so it stays private in Zuper.
    return {"note": view.get("body") or "", "is_private": True}


def task_view(t: OpportunityTask) -> dict:
    return {"title": text(t.title), "description": text(t.description),
            "done": t.completed_at is not None, "due_at": iso_minute(t.due_at)}


def zuper_task_view(record: dict) -> dict:
    status = str(record.get("service_task_status") or record.get("status") or "").upper()
    done = bool(record.get("is_completed")) or status in {"COMPLETED", "DONE"}
    return {"title": text(record.get("service_task_title") or record.get("title")),
            "description": text(record.get("service_task_description")
                                or record.get("description")),
            "done": done,
            "due_at": iso_minute(parse_time(record.get("due_date") or record.get("due_at")))}


def task_payload(view: dict) -> dict:
    return {"service_task_title": view.get("title") or "Task",
            "service_task_description": view.get("description") or "",
            "service_task_status": "COMPLETED" if view.get("done") else "NEW",
            "due_date": view.get("due_at")}

"""Owen AI's new leads: a customer + a job in Zuper, approved by the owner (2026-11-19).

USER REQUEST (2026-11-19, a scoped lift of "Zuper is read-only"): Owen pastes leads into the
chat (a web form, a call summary); Owen AI turns each into a NEW customer and a job on a board
(Retail by default), with the lead's message as a private job note — the way the three leads
of 2026-11-19 (#864-#866) were created by hand. Owen sees each one on the Owen AI page
(Proposals tab, "New customers") with Approve / Reject.

  * `propose_new_lead` (the tool Claude calls) validates the lead strictly, READS Zuper (the
    board and its first column, customers with the same email or name) and the CRM (contacts
    with the same phone, linked to a Zuper customer), and stores a PENDING row. It writes
    nothing to Zuper. A possible duplicate is refused until Claude names the existing customer
    (`customer_uid`) or Owen says it is a different person (`new_customer: true`).
  * `approve` (POST /api/owen-ai/leads/{id}/approve, the owner only) claims the row pending ->
    creating in one UPDATE (a double click cannot create two), checks the server gate and the
    registry BEFORE any request, then inside `client.owen_ai_lead_create()` sends at most
    POST /customers_new, POST /jobs (no status, visit or technician: the job lands in the
    board's first column, as an emailed AHS work order does) and POST /jobs/{uid}/note, and
    reads the job back. Anything wrong -> failed, saying what already exists in Zuper. Never
    retried automatically. It runs inside the request, like proposals.approve, for the same
    reasons (three writes, the owner sees the outcome at once, the atomic claim is the guard).
  * Nothing here texts, emails, schedules or assigns. The note body is the WRAPPED shape the
    official reference documents ({"note": {...}}) — the flat one answers 200 and stores an
    empty note (2026-11-09).
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import Contact, OwenAiLeadDraft, OwenAiMessage, OwenAiThread, ZuperMapping
from ..registry import service as registry
from ..zuper import client as zc
from ..zuper import config as zuper_config
from . import config, dossier
from .proposals import SpecError, current_thread

ACTION = "zuper_create"              # registry action on the owen_ai entry
DEFAULT_BOARD = "Retail"
STATUSES = ("pending", "creating", "created", "rejected", "failed")
NOTE_MAX = 6000
WAITING = ("Waiting for Owen to approve it on the Proposals tab (New customers) — nothing was "
           "created in Zuper yet.")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
TOP_KEYS = {"first_name", "last_name", "phone", "email", "address", "board", "title", "note",
            "customer_uid", "new_customer"}
ADDRESS_KEYS = {"street", "city", "state", "zip"}


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def last10(raw: Any) -> str | None:
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def _str(d: dict, key: str, problems: list[str], max_len: int, required: bool = False) -> str:
    v = d.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            problems.append("%s is required" % key)
        return ""
    if not isinstance(v, str):
        problems.append("%s must be text" % key)
        return ""
    if len(v.strip()) > max_len:
        problems.append("%s is longer than %d characters" % (key, max_len))
        return ""
    return v.strip()


def validate(spec: Any) -> dict:
    """The lead Claude sent -> a clean dict, or SpecError listing every problem."""
    if not isinstance(spec, dict):
        raise SpecError(["the lead must be an object"])
    problems: list[str] = []
    extra = set(spec) - TOP_KEYS
    if extra:
        problems.append("unknown keys: %s" % ", ".join(sorted(extra)))
    first = _str(spec, "first_name", problems, 80, required=True)
    last = _str(spec, "last_name", problems, 80)
    phone_raw = spec.get("phone")
    phone = last10(phone_raw)
    if phone is None:
        problems.append("phone must be a 10-digit US number (got %r)" % (phone_raw,))
    email = _str(spec, "email", problems, 200).lower()
    if email and not _EMAIL.match(email):
        problems.append("email %r does not look like an email address" % email)
    a = spec.get("address") or {}
    addr = {"street": "", "city": "", "state": "FL", "zip": ""}
    if not isinstance(a, dict):
        problems.append("address must be an object {street, city, state, zip}")
    else:
        bad = set(a) - ADDRESS_KEYS
        if bad:
            problems.append("unknown address keys: %s" % ", ".join(sorted(bad)))
        for k in ADDRESS_KEYS:
            v = a.get(k)
            if v is not None and not isinstance(v, str):
                problems.append("address.%s must be text" % k)
            elif v and v.strip():
                addr[k] = v.strip()[:200]
        if addr["zip"] and not re.fullmatch(r"\d{5}", addr["zip"]):
            problems.append("address.zip must be 5 digits")
        if not (addr["zip"] or addr["city"]):
            problems.append("address needs at least a zip or a city")
    board = _str(spec, "board", problems, 120) or DEFAULT_BOARD
    title = _str(spec, "title", problems, 200)
    note = _str(spec, "note", problems, NOTE_MAX, required=True)
    customer_uid = _str(spec, "customer_uid", problems, 64) or None
    new_customer = spec.get("new_customer", False)
    if not isinstance(new_customer, bool):
        problems.append("new_customer must be true or false")
    if customer_uid and new_customer:
        problems.append("give customer_uid OR new_customer, not both")
    if problems:
        raise SpecError(problems)
    name = (first + " " + last).strip()
    return {"first_name": first, "last_name": last, "name": name, "phone": phone,
            "email": email, "address": addr, "board": board,
            "title": title or default_title(name, board), "note": note,
            "customer_uid": customer_uid, "new_customer": bool(new_customer)}


def default_title(name: str, board: str) -> str:
    """The team's own convention on Retail: "<Customer name> Retail"."""
    return ("%s Retail" % name if board.strip().lower() == "retail"
            else "%s - %s" % (name, board))


# ------------------------------------------------------------------------------ the reads

def _board(r: dossier.Reader, name: str) -> dict:
    cats = zc.rows_of(r.get("/jobs/category"))
    want = name.strip().lower()
    cat = next((c for c in cats if str(c.get("category_name") or "").strip().lower() == want),
               None)
    if cat is None:
        raise SpecError(["no Zuper board named %r; boards: %s" % (
            name, ", ".join(sorted(str(c.get("category_name")) for c in cats)))])
    statuses = zc.rows_of(r.get("/jobs/status/" + cat["category_uid"]))
    first = next((s for s in statuses if str(s.get("status_type") or "").upper() == "NEW"),
                 None)
    if first is None:
        # A job created with no status lands in the board's NEW column; with none it shows in
        # no column at all (#720-#724 sat invisible for a day, 2026-09-30).
        raise SpecError(["the %s board has no NEW column, so a new job would show in no "
                         "column; pick another board" % cat.get("category_name")])
    return {"uid": cat["category_uid"], "name": cat.get("category_name"),
            "first_column": first.get("status_name")}


def _cust(c: dict) -> dict:
    phones = c.get("customer_contact_no") or {}
    return {"customer_uid": c.get("customer_uid"),
            "name": " ".join(x for x in (c.get("customer_first_name"),
                                         c.get("customer_last_name")) if x),
            "email": c.get("customer_email"),
            "phone": next((last10(v) for v in (phones.values() if isinstance(phones, dict)
                                               else []) if last10(v)), None)}


def duplicates(db: Session, r: dossier.Reader, lead: dict) -> list[dict]:
    """Zuper customers that may be this person: same email, same full name or same phone (Zuper's
    keyword search matches all three for a customer whose mobile is stored as +1..., measured
    2026-11-19 — but not for every older record, so the CRM is asked too), or linked in the CRM
    to a contact with the same phone. Deleted customers are left out."""
    found: dict[str, dict] = {}

    def add(c: dict, why: str) -> None:
        if not isinstance(c, dict) or not c.get("customer_uid") or c.get("is_deleted"):
            return
        row = found.setdefault(c["customer_uid"], {**_cust(c), "why": []})
        if why not in row["why"]:
            row["why"].append(why)

    for kw, why in ((lead["email"], "same email"), (lead["name"], "same name"),
                    (lead["phone"], "same phone")):
        if not kw:
            continue
        for c in zc.rows_of(r.get("/customers", {"filter.keyword": kw, "count": 20,
                                                 "page": 1})):
            if why == "same email" and str(c.get("customer_email") or "").lower() != kw:
                continue
            if why == "same name" and _cust(c)["name"].strip().lower() != kw.lower():
                continue
            if why == "same phone" and _cust(c)["phone"] != kw:
                continue
            add(c, why)
    for c in found.values():
        if c["phone"] == lead["phone"] and "same phone" not in c["why"]:
            c["why"].append("same phone")
    tail = lead["phone"][-4:]
    for contact in db.scalars(select(Contact).where(Contact.phone.like("%" + tail))):
        if last10(contact.phone) != lead["phone"]:
            continue
        m = db.scalar(select(ZuperMapping).where(ZuperMapping.crm_type == "contact",
                                                 ZuperMapping.crm_id == contact.id,
                                                 ZuperMapping.zuper_type == "customer"))
        if m is None or not m.zuper_uid:
            continue
        if m.zuper_uid in found:
            if "same phone" not in found[m.zuper_uid]["why"]:
                found[m.zuper_uid]["why"].append("same phone")
            continue
        try:
            add(zc.data_of(r.get("/customers/" + m.zuper_uid)), "same phone")
        except zc.ZuperError:
            continue
    return list(found.values())


# ------------------------------------------------------------------------------ the tool

def prepare(db: Session, spec_in: Any, thread_id: int | None = None,
            reader: dossier.Reader | None = None) -> OwenAiLeadDraft:
    """Validate, read Zuper and the CRM, store a PENDING row. Raises SpecError or ZuperError.
    Writes nothing to Zuper."""
    lead = validate(spec_in)
    r = reader or dossier.Reader()
    board = _board(r, lead["board"])
    existing = None
    dupes: list[dict] = []
    if lead["customer_uid"]:
        try:
            c = zc.data_of(r.get("/customers/" + lead["customer_uid"]))
        except zc.ZuperError as exc:
            if exc.kind == "not_found":
                raise SpecError(["no Zuper customer %s" % lead["customer_uid"]]) from None
            raise
        if not isinstance(c, dict) or c.get("is_deleted"):
            raise SpecError(["Zuper customer %s is deleted" % lead["customer_uid"]])
        existing = _cust(c)
    else:
        dupes = duplicates(db, r, lead)
        if dupes and not lead["new_customer"]:
            raise SpecError(["Zuper may already have this customer: %s. Ask Owen which it is, "
                             "then call again with customer_uid (a new job for that "
                             "customer) or new_customer: true (a different person)." % "; ".join(
                                 "%s %s (%s; %s)" % (d["customer_uid"], d["name"] or "?",
                                                     ", ".join(d["why"]),
                                                     d["email"] or "no email")
                                 for d in dupes)])
    # A newer lead for the same phone replaces one still waiting.
    for old in db.scalars(select(OwenAiLeadDraft).where(OwenAiLeadDraft.status == "pending")):
        if (old.spec or {}).get("lead", {}).get("phone") == lead["phone"]:
            old.status, old.decided_at, old.decided_by = "rejected", _now(), "Owen AI"
            old.error = "Replaced by a newer draft."
    row = OwenAiLeadDraft(
        customer_name=(existing or {}).get("name") or lead["name"], board=board["name"],
        category_uid=board["uid"], job_title=lead["title"],
        spec={"input": spec_in if isinstance(spec_in, dict) else {}, "lead": lead,
              "first_column": board["first_column"], "existing_customer": existing,
              "duplicates": dupes},
        status="pending", customer_uid=lead["customer_uid"], thread_id=thread_id,
        created_at=_now())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _session():
    from ..db import SessionLocal
    return SessionLocal()


def summary(row: OwenAiLeadDraft) -> str:
    lead = (row.spec or {}).get("lead") or {}
    a = lead.get("address") or {}
    where = ", ".join(x for x in (a.get("street"), a.get("city"),
                                  " ".join(x for x in (a.get("state"), a.get("zip")) if x)) if x)
    who = ("existing customer %s (%s)" % (row.customer_name, row.customer_uid)
           if row.customer_uid else "NEW customer %s" % row.customer_name)
    return ("Lead #%d: %s, phone %s%s, %s. Job \"%s\" on %s / %s, no visit, no technician; the "
            "message as a private note (%d characters)." % (
                row.id, who, lead.get("phone"),
                ", " + lead["email"] if lead.get("email") else ", no email", where or "no address",
                row.job_title, row.board, (row.spec or {}).get("first_column"),
                len(lead.get("note") or "")))


def tool_propose(args: dict) -> str:
    spec = args.get("lead") if isinstance(args, dict) and "lead" in args else args
    db = _session()
    try:
        row = prepare(db, spec, current_thread())
    except SpecError as exc:
        return ("ERROR: the lead was not stored — fix these and call again:\n"
                + "\n".join("- " + p for p in exc.problems))
    except zc.ZuperError as exc:
        return "ERROR from Zuper: %s" % zc.sentence(exc)
    finally:
        db.close()
    return summary(row) + "\n" + WAITING


def input_schema() -> dict:
    return {"type": "object", "properties": {
        "first_name": {"type": "string"},
        "last_name": {"type": "string", "description": "may be an initial ('B') or empty"},
        "phone": {"type": "string", "description": "the customer's phone, any format, 10 digits"},
        "email": {"type": "string", "description": "omit when the lead has none"},
        "address": {"type": "object", "properties": {
            "street": {"type": "string"}, "city": {"type": "string"},
            "state": {"type": "string", "description": "default FL"},
            "zip": {"type": "string"}},
            "description": "the SERVICE address (where the roof is) — never the office address "
                           "a web form adds; at least a zip or a city"},
        "board": {"type": "string", "description": "Zuper board name, default 'Retail'"},
        "title": {"type": "string", "description": "default '<First Last> Retail' (the team's "
                                                   "convention)"},
        "note": {"type": "string", "description": "the lead as Owen gave it: source, service, "
                                                  "the customer's message / the call summary, "
                                                  "urgency, follow-up owed. Plain text."},
        "customer_uid": {"type": "string", "description": "an EXISTING Zuper customer to put "
                                                          "the new job on (after Owen says so)"},
        "new_customer": {"type": "boolean", "description": "true only when Owen says a possible "
                                                           "duplicate is a different person"}},
        "required": ["first_name", "phone", "note"]}


DESCRIPTION = (
    "PREPARE one new lead for Owen to APPROVE: a NEW Zuper customer and a job on a board "
    "(default Retail), with the lead's message as a private job note. One call per customer. "
    "It writes NOTHING to Zuper: it stores a pending lead. Only Owen's Approve on the Proposals "
    "tab (New customers) creates the customer, the job (in the board's first column, e.g. New "
    "Lead — no visit, no technician, nothing sent to the customer) and the note. It checks for "
    "an existing customer (same email, same full name, or a CRM contact with the same phone): "
    "on a possible duplicate it refuses and lists them — ask Owen, then call again with "
    "customer_uid (new job for that customer) or new_customer: true. Use the SERVICE address, "
    "not the office address web forms add. Never invent a phone, email or address; leave out "
    "what the lead does not give.")

SPECS = (("propose_new_lead", DESCRIPTION, input_schema(), tool_propose),)


# ------------------------------------------------------------------------------ approve / reject

class Refused(Exception):
    """Approve refused before anything was claimed or sent (409, the row stays pending)."""


def refusal(db: Session) -> str | None:
    if not zuper_config.owen_ai_leads_enabled():
        return zuper_config.OWEN_AI_LEADS_OFF_SENTENCE
    return registry.level_refusal(db, config.KEY, ACTION)


def _note(db: Session, row: OwenAiLeadDraft, text: str) -> None:
    th = db.get(OwenAiThread, row.thread_id) if row.thread_id else None
    if th is None:
        return
    db.add(OwenAiMessage(thread_id=row.thread_id, role="system", content=text, status="done",
                         created_at=_now(), finished_at=_now()))
    th.updated_at = _now()


def _address(a: dict) -> dict:
    return {"street": a.get("street") or "", "city": a.get("city") or "",
            "state": a.get("state") or "FL", "zip_code": a.get("zip") or "", "country": "US"}


def customer_body(row: OwenAiLeadDraft) -> dict:
    lead = row.spec["lead"]
    c = {"customer_first_name": lead["first_name"], "customer_last_name": lead["last_name"],
         "customer_contact_no": {"mobile": "+1" + lead["phone"]},
         "customer_address": _address(lead["address"])}
    if lead.get("email"):
        c["customer_email"] = lead["email"]
    return {"customer": c}


def job_body(row: OwenAiLeadDraft, customer_uid: str) -> dict:
    lead = row.spec["lead"]
    return {"job": {"job_title": row.job_title, "job_priority": "MEDIUM",
                    "job_category": row.category_uid, "customer_uid": customer_uid,
                    "customer_address": _address(lead["address"]),
                    "due_date": _now().replace(second=0, microsecond=0).strftime(
                        "%Y-%m-%dT%H:%M:%SZ")}}


def note_body(row: OwenAiLeadDraft) -> dict:
    return {"note": {"note": row.spec["lead"]["note"], "is_private": True}}


def check_readback(job: dict, notes: list[dict], row: OwenAiLeadDraft) -> list[str]:
    problems = []
    cat = job.get("job_category")
    cat_uid = cat.get("category_uid") if isinstance(cat, dict) else cat
    if cat_uid != row.category_uid:
        problems.append("it is on another board")
    cur = job.get("current_job_status") or {}
    if str(cur.get("status_type") or "").upper() != "NEW":
        problems.append("it is in %s, not the board's first column" % (
            cur.get("status_name") or "no column"))
    if job.get("is_scheduled"):
        problems.append("it has a visit")
    if job.get("is_assigned"):
        problems.append("someone is assigned")
    cust = job.get("customer")
    cust_uid = cust.get("customer_uid") if isinstance(cust, dict) else cust
    if cust_uid and cust_uid != row.customer_uid:
        problems.append("it belongs to another customer")
    if not any(str(n.get("note") or "").strip() for n in notes if isinstance(n, dict)):
        problems.append("the note is empty")
    return problems


def approve(db: Session, row_id: int, who: str) -> OwenAiLeadDraft:
    row = db.get(OwenAiLeadDraft, row_id)
    if row is None:
        raise LookupError("no such lead")
    if row.status != "pending":
        raise Refused("This lead is already %s." % row.status)
    why = refusal(db)
    if why:
        raise Refused(why)
    took = db.execute(update(OwenAiLeadDraft).where(
        OwenAiLeadDraft.id == row.id, OwenAiLeadDraft.status == "pending").values(
        status="creating", decided_at=_now(), decided_by=(who or "")[:200])).rowcount
    db.commit()
    if not took:
        db.refresh(row)
        raise Refused("This lead is already %s." % row.status)
    db.refresh(row)
    made: list[str] = []
    try:
        with zc.owen_ai_lead_create(customer_uid=row.customer_uid):
            if not row.customer_uid:
                uid = zc.uid_of(zc.request("POST", "/customers_new", body=customer_body(row)),
                                "customer_uid")
                zc.owen_ai_lead_created("customer", uid)
                row.customer_uid = uid
                made.append("customer %s" % uid)
                db.commit()
            job_uid = zc.uid_of(zc.request("POST", "/jobs", body=job_body(row, row.customer_uid)),
                                "job_uid")
            zc.owen_ai_lead_created("job", job_uid)
            row.job_uid = job_uid
            made.append("job %s" % job_uid)
            db.commit()
            zc.request("POST", "/jobs/%s/note" % job_uid, body=note_body(row))
            made.append("the note")
            job = zc.data_of(zc.request("GET", "/jobs/" + job_uid))
            job = job if isinstance(job, dict) else {}
            notes = zc.rows_of(zc.request("GET", "/jobs/%s/note" % job_uid))
        row.job_number = str(job.get("work_order_number") or "") or None
        problems = check_readback(job, notes, row)
        if problems:
            row.status = "failed"
            row.error = ("Job #%s was created in Zuper, but the read-back does not match: %s. "
                         "Check it in Zuper before doing anything else." % (
                             row.job_number or job_uid, "; ".join(problems)))
        else:
            row.status, row.error = "created", None
    except zc.ZuperError as exc:
        row.status = "failed"
        row.error = zc.sentence(exc) + (" %s" % exc.detail if exc.kind == "refused" and
                                        exc.detail else "") + (
            " Already in Zuper: %s — check before trying again." % ", ".join(made)
            if made else " Nothing was created in Zuper.")
    except Exception as exc:  # noqa: BLE001 - a bug must still leave the row failed, not creating
        row.status = "failed"
        row.error = "Internal error (%s).%s" % (type(exc).__name__, (
            " Already in Zuper: %s." % ", ".join(made)) if made else "")
    label = "Lead #%d (%s)" % (row.id, row.customer_name)
    if row.status == "created":
        _note(db, row, "%s was APPROVED by %s and created in Zuper: job #%s \"%s\" on %s / %s%s, "
                       "with the note. Nothing was sent to the customer." % (
                           label, who, row.job_number, row.job_title, row.board,
                           (row.spec or {}).get("first_column"),
                           "" if (row.spec or {}).get("existing_customer")
                           else ", new customer"))
    else:
        _note(db, row, "%s was approved by %s but FAILED in Zuper: %s" % (label, who, row.error))
    db.commit()
    db.refresh(row)
    return row


def reject(db: Session, row_id: int, who: str, why: str) -> OwenAiLeadDraft:
    row = db.get(OwenAiLeadDraft, row_id)
    if row is None:
        raise LookupError("no such lead")
    took = db.execute(update(OwenAiLeadDraft).where(
        OwenAiLeadDraft.id == row_id,
        OwenAiLeadDraft.status.in_(("pending", "failed"))).values(
        status="rejected", decided_at=_now(), decided_by=(who or "")[:200],
        error=(why or "").strip()[:1000] or None)).rowcount
    db.commit()
    db.refresh(row)
    if not took:
        raise Refused("This lead is already %s." % row.status)
    _note(db, row, "Lead #%d (%s) was REJECTED by %s: %s. Nothing was created in Zuper." % (
        row.id, row.customer_name, who, (why or "").strip() or "no reason given"))
    db.commit()
    return row


# ------------------------------------------------------------------------------ the API's view

def out(row: OwenAiLeadDraft) -> dict:
    spec = row.spec or {}
    lead = spec.get("lead") or {}
    tpl = zuper_config.job_url_template()
    return {"id": row.id, "status": row.status, "customer_name": row.customer_name,
            "first_name": lead.get("first_name"), "last_name": lead.get("last_name"),
            "phone": lead.get("phone"), "email": lead.get("email"),
            "address": lead.get("address") or {}, "note": lead.get("note"),
            "board": row.board, "first_column": spec.get("first_column"),
            "job_title": row.job_title, "existing_customer": spec.get("existing_customer"),
            "duplicates": spec.get("duplicates") or [],
            "customer_uid": row.customer_uid, "job_uid": row.job_uid,
            "job_number": row.job_number,
            "job_url": tpl.format(uid=row.job_uid) if tpl and row.job_uid else None,
            "error": row.error, "thread_id": row.thread_id,
            "created_at": _iso(row.created_at), "decided_at": _iso(row.decided_at),
            "decided_by": row.decided_by}

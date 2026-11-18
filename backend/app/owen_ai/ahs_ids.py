"""Which AHS dispatch is this Zuper job? (2026-11-18)

GOOD's price on an AHS job is what we submitted in Frontdoor, and Frontdoor is read by the AHS
dispatch number. Most Zuper jobs carry it in the "AHS Job ID" field, but not all: job #768's field
was empty, so its proposal was written without the portal price and came out $1,700 too low. The
number was on the customer's AHS work-order email card in the CRM all along.

`candidates()` lists every dispatch number this job can be tied to, with where each came from, in
this order: the job's "AHS Job ID" field; an 8-10 digit number in the job title; the CRM's AHS
email cards (`custom_fields.ahs_job_id`) whose contact has the job customer's phone (last ten
digits) or exact first + last name. `single()` is the one to use without asking: the field when it
is set, else exactly one candidate. Several candidates (a customer AHS sent us twice) are never
picked here: the caller reads each in Frontdoor and says which one it used, or asks.

Reads only: the CRM database and the Zuper job record already in hand.
"""
from __future__ import annotations

import re
from typing import Any

FIELD_LABEL = "AHS Job ID"
_ID = re.compile(r"(?<!\d)(\d{8,10})(?!\d)")


def _digits(v: Any) -> str:
    d = re.sub(r"\D", "", str(v or ""))
    return d[-10:] if len(d) >= 10 else ""


def field_id(rec: dict) -> str | None:
    """The job's own "AHS Job ID" field, when it holds a dispatch number."""
    for f in rec.get("custom_fields") or []:
        if isinstance(f, dict) and str(f.get("label") or "").strip().lower() == FIELD_LABEL.lower():
            m = _ID.search(str(f.get("value") or ""))
            if m:
                return m.group(1)
    return None


def _customer(rec: dict) -> tuple[set[str], tuple[str, str]]:
    c = rec.get("customer") if isinstance(rec.get("customer"), dict) else {}
    nums = c.get("customer_contact_no") if isinstance(c.get("customer_contact_no"), dict) else {}
    phones = {_digits(v) for v in nums.values()} - {""}
    name = (str(c.get("customer_first_name") or "").strip().lower(),
            str(c.get("customer_last_name") or "").strip().lower())
    return phones, name


def candidates(rec: dict, db: Any = None) -> list[dict]:
    """[{dispatch, source}] for this Zuper job record, each number once, best source first."""
    out: list[dict] = []

    def add(num: str | None, source: str) -> None:
        if num and num not in {x["dispatch"] for x in out}:
            out.append({"dispatch": num, "source": source})

    add(field_id(rec), "the job's AHS Job ID field")
    m = _ID.search(str(rec.get("job_title") or ""))
    add(m.group(1) if m else None, "the job title")
    phones, (first, last) = _customer(rec)
    if not phones and not (first and last):
        return out
    from ..db import SessionLocal
    from ..models import Contact, Opportunity
    own = db is None
    s = SessionLocal() if own else db
    try:
        rows = (s.query(Opportunity, Contact).join(Contact, Contact.id == Opportunity.contact_id)
                .filter(Opportunity.custom_fields.isnot(None)).all())
        found = []
        for opp, ct in rows:
            num = _ID.search(str((opp.custom_fields or {}).get("ahs_job_id") or ""))
            if not num:
                continue
            by_phone = _digits(ct.phone) in phones if _digits(ct.phone) else False
            full = (str(ct.first_name or "").strip().lower(),
                    str(ct.last_name or "").strip().lower())
            by_name = bool(first and last) and full == (first, last)
            if by_phone or by_name:
                found.append((opp.created_at, num.group(1), "phone" if by_phone else "name"))
        for _, num, how in sorted(found, key=lambda x: str(x[0] or ""), reverse=True):
            add(num, "the CRM's AHS email card (same customer %s)" % how)
    finally:
        if own:
            s.close()
    return out


def single(rec: dict, db: Any = None) -> str | None:
    """The dispatch to read without asking: the field, else the only candidate; None when there
    is none or several."""
    own = field_id(rec)
    if own:
        return own
    c = candidates(rec, db)
    return c[0]["dispatch"] if len(c) == 1 else None

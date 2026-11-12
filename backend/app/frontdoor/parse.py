"""The cells `client.py` read out of the portal -> plain dicts. PURE: no browser, no network.

What arrives here has already had the customer's name, phone and address left behind in the
browser (client.EXTRACT skips those cells). A history note can still mention a person, so notes
never leave this module: only the AHS figures found in them do (AUTHO #, NCC $, Net Total $).
Money is a float; a figure that is not on the page is None, never 0.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
MONEY = re.compile(r"-?\$\s?-?[\d,]+(?:\.\d+)?")


def money(text: str | None) -> float | None:
    m = MONEY.search(text or "")
    if not m:
        return None
    raw = m.group(0)
    try:
        value = float(re.sub(r"[^\d.]", "", raw))
    except ValueError:
        return None
    return -value if raw.count("-") else value


def day(text: str | None) -> str | None:
    """'11/12/2026' (anywhere in the text) -> '2026-11-12'."""
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text or "")
    if not m:
        return None
    try:
        return datetime(int(m.group(3)), int(m.group(1)), int(m.group(2))).date().isoformat()
    except ValueError:
        return None


def moment(text: str | None) -> str | None:
    """'11/12/2026 01:50 PM GMT-4' or '11/10/2026 at 05:03 PM' -> ISO 8601, New York time.

    The history table stamps its own offset ("GMT-4" on 2026-11-12, when New York was on EST:
    the same event read 12:50 PM in the Authorization panel), so a stated offset is honoured and
    converted; a time without one (the Authorization panel's) is New York time."""
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\D{1,6}(\d{1,2}):(\d{2})\s*([AP]M)"
                  r"(?:\s*GMT\s*([+-]\d{1,2})(?::?(\d{2}))?)?", text or "", re.IGNORECASE)
    if not m:
        return day(text)
    mo, d, y, h, mi, ap, off_h, off_m = m.groups()
    h = int(h) % 12 + (12 if ap.upper() == "PM" else 0)
    try:
        if off_h is not None:
            sign = -1 if off_h.startswith("-") else 1
            tz = timezone(sign * timedelta(hours=abs(int(off_h)), minutes=int(off_m or 0)))
            return datetime(int(y), int(mo), int(d), h, int(mi), tzinfo=tz) \
                .astimezone(NY).isoformat()
        return datetime(int(y), int(mo), int(d), h, int(mi), tzinfo=NY).isoformat()
    except ValueError:
        return None


def clean(text: str | None) -> str | None:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t or None


# ------------------------------------------------------------------------------ dispatch list

def dispatch_row(raw: dict) -> dict | None:
    """A row of the Dispatches list. `raw` = {"cells": {suffix: text}, "items": [...],
    "href": "/dispatches/<id>?contractID=..&workorderID=.."}."""
    cells = raw.get("cells") or {}
    dispatch_id = clean(cells.get("DispatchID"))
    if not dispatch_id or not dispatch_id.isdigit():
        return None
    q = parse_qs(urlsplit(raw.get("href") or "").query)
    return {
        "dispatch_id": dispatch_id,
        "date": day(cells.get("Date")),
        "brand": clean(cells.get("WarrantyBrand")),
        "items": [x for x in (clean(i) for i in raw.get("items") or []) if x],
        "status_group": clean(cells.get("StatusGroup")),
        "status": clean(cells.get("Status")),
        "contract_id": (q.get("contractID") or [None])[0],
        "work_order_id": (q.get("workorderID") or [None])[0],
    }


# ---------------------------------------------------------------------------- dispatch detail

AUTHO_NO = re.compile(r"AUTHO\s*#?\s*:?\s*([A-Za-z0-9][A-Za-z0-9-]{2,39})", re.IGNORECASE)
NCC = re.compile(r"\bNCC\s*\$\s?([\d,]+(?:\.\d+)?)", re.IGNORECASE)
NET = re.compile(r"\bNet\s+Total\s*\$\s?([\d,]+(?:\.\d+)?)", re.IGNORECASE)
AUTHO_EVENT = re.compile(r"autho|ncc", re.IGNORECASE)


def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _after(lines: list[str], label: str) -> str | None:
    for i, line in enumerate(lines):
        if line.strip().lower() == label.lower():
            for nxt in lines[i + 1:i + 3]:
                if nxt.strip():
                    return nxt.strip()
    return None


def authorization(text: str | None) -> dict | None:
    """The detail page's Authorization panel:

        Authorization / Roof / Customer Owes / $675.00 / Authorized To Contractor / $1125.00 /
        Total / $1800.00 / Authorized: / 11/10/2026 at 05:03 PM ... / by KMCKERN

    -> {"requested": True, "trade", "customer_owes", "authorized_to_contractor", "total",
    "authorized_at", "authorized_by"}. "No Authorization Requested" -> {"requested": False}.
    None = no panel on the page at all."""
    if not text or not text.strip():
        return None
    if re.search(r"no authorization requested", text, re.IGNORECASE):
        return {"requested": False}
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    labels = {"customer owes", "authorized to contractor", "total", "authorized:", "authorization"}
    trade = lines[1] if len(lines) > 1 and lines[0].lower() == "authorization" and \
        lines[1].lower() not in labels and "$" not in lines[1] else None
    when = _after(lines, "Authorized:")
    by = next((x[3:].strip() for x in lines if x.lower().startswith("by ")), None)
    return {
        "requested": True,
        "trade": trade,
        "customer_owes": money(_after(lines, "Customer Owes")),
        "authorized_to_contractor": money(_after(lines, "Authorized To Contractor")),
        "total": money(_after(lines, "Total")),
        "authorized_at": moment(when),
        "authorized_by": by,
    }


def ahs_figures(notes: list[str]) -> dict:
    """AUTHO numbers and the NCC / Net Total amounts AHS wrote in history notes. Nothing else of
    a note is kept."""
    autho: list[str] = []
    ncc: list[float] = []
    net: list[float] = []
    for note in notes:
        for m in AUTHO_NO.finditer(note or ""):
            if m.group(1).upper() not in autho:
                autho.append(m.group(1).upper())
        ncc += [v for v in (_num(x) for x in NCC.findall(note or "")) if v is not None]
        net += [v for v in (_num(x) for x in NET.findall(note or "")) if v is not None]
    return {"autho_numbers": autho, "ncc_amounts": ncc, "net_totals": net}


def history_rows(rows: list[list[str]]) -> list[dict]:
    """[[date, update type, reported by, item, note], ...] -> events, newest first as shown,
    WITHOUT the note text."""
    out = []
    for r in rows:
        if len(r) < 2 or not moment(r[0]):
            continue
        out.append({"at": moment(r[0]), "update": clean(r[1]),
                    "by": clean(r[2]) if len(r) > 2 else None,
                    "item": clean(r[3]) if len(r) > 3 else None})
    return out


def dispatch_detail(raw: dict) -> dict:
    """`raw` = {"dispatch_id", "cells": {suffix: text}, "items": [...], "autho_text": str|None,
    "history": [[date, type, by, item, note], ...]}."""
    cells = raw.get("cells") or {}
    history = history_rows(raw.get("history") or [])
    figures = ahs_figures([r[4] for r in raw.get("history") or [] if len(r) > 4])
    latest_autho = next((h for h in history if AUTHO_EVENT.search(h.get("update") or "")), None)
    return {
        "dispatch_id": str(raw.get("dispatch_id") or ""),
        "status": clean(cells.get("DispatchStatusText")),
        "invoice_status": clean(cells.get("DispatchInvoiceStatusText")),
        "type": clean(cells.get("DispatchTypeText")),
        "trade": clean(cells.get("DispatchTradeText")),
        "urgency": clean(cells.get("DispatchUrgencyText")),
        "warranty": clean(cells.get("DispatchWarrantyText")),
        "residence_type": clean(cells.get("DwellingTypeDescriptionText")),
        "plan_effective": day(cells.get("ContractEffectiveDateText")),
        "plan_expires": day(cells.get("ContractExpiryDateText")),
        "items": [x for x in (clean(i) for i in raw.get("items") or []) if x],
        "authorization": authorization(raw.get("autho_text")),
        "authorization_update": latest_autho,
        **figures,
        "history": history,
    }


# ------------------------------------------------------------------------------- invoices

TABS = {"ready": "Ready To Invoice", "draft": "Draft Invoices", "submitted": "Submitted Invoices"}


def invoice_row(tab: str, cells: dict) -> dict | None:
    """A row of one Invoices tab. The cells' suffixes differ per tab (Ready: createOn,
    DispatchItems; Submitted: submittedOn, legacyItemName, Status, TotalAmount,
    TotalAmountPaid), so every known one is looked for."""
    dispatch_id = clean(cells.get("DispatchLink"))
    if not dispatch_id or not dispatch_id.isdigit():
        return None
    # "574" (the id alone, measured 2026-11-12) or "574 (Roof Leaks $1,125.00)".
    inv = clean(cells.get("legacyItemName") or cells.get("InvoiceIdAndItem"))
    m = re.match(r"^(\S+)\s*(?:\((.*)\))?$", inv or "")
    return {
        "tab": tab,
        "dispatch_id": dispatch_id,
        "created": day(cells.get("createOn") or cells.get("createdOn")),
        "submitted": day(cells.get("submittedOn")),
        "invoice_id": m.group(1) if m else None,
        "invoice_item": clean(m.group(2)) if m else inv,
        "items": clean(cells.get("DispatchItems")),
        "status": clean(cells.get("Status")),
        "total": money(cells.get("TotalAmount") or cells.get("Total")),
        "paid": money(cells.get("TotalAmountPaid")),
    }

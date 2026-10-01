"""The KPI Excel for AHS - Inspection, AHS - Repair & Review and Retail (2026-09-30).

One dated file per night, never overwritten, on owen-main only (the owner: no cloud sheets).
Built from what the CRM already holds — Zuper's history (`app/zuper/history.py`) — plus the
Workiz and AHS exports the owner copied to a private folder for the months before Zuper.
Reads only; writes nothing but the file.

The owner's definitions (Claude memory `kpi-reporting-decisions`):
  upsell    the job closed for MORE than the AHS invoice; the difference is what the customer
            paid. Before Zuper the tier (Good/Better/Best) is unknown.
  won AHS   AHS paid. Every row of the AHS vendor export counts as paid.
  Retail    sold = reached Scheduled; won = Paid.
  callback  re-work / warranty only (Job Type "Callback/Warranty" or the Callback tag) —
            NOT the "Call Back" column.
  time in a column counts from 2026-09-26: Zuper's history before that is load and bulk moves.

The sheets carry job numbers only — no customer names, phones, emails or addresses.

  uv run python -m app.kpi_report                    # build now (from backend/)
  uv run python -m app.kpi_report --input DIR --out DIR
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import ZuperRecordVersion, ZuperStatusHistory

log = logging.getLogger("kpi_report")

INPUT_DIR = Path(os.getenv("KPI_INPUT_DIR", "/var/lib/ghl-clone/kpi/input"))
OUTPUT_DIR = Path(os.getenv("KPI_OUTPUT_DIR", "/var/lib/ghl-clone/kpi/reports"))

BOARDS = ("AHS - Inspection", "AHS - Repair & Review", "Retail")
TIMING_FROM = datetime(2026, 9, 26, 4, 0, tzinfo=UTC)      # 2026-09-26 00:00 New York

# The questions the team adds in Zuper (docs/ZUPER-KPI-SETUP.md). Found by exact label.
Q_AHS_AUTHORIZED = "AHS authorized ($)"
Q_CUSTOMER_CHOSE = "Customer chose"
Q_CUSTOMER_PAID = "Customer paid ($)"
Q_OPTION_CHOSEN = "Option chosen"
Q_SOLD_PRICE = "Sold price ($)"
Q_CANCEL_REASON = "Cancel reason"
Q_CANCEL_NOTE = "Cancel note"
F_ORIGINAL_JOB = "Original job #"
QUESTIONS = (Q_AHS_AUTHORIZED, Q_CUSTOMER_CHOSE, Q_CUSTOMER_PAID, Q_OPTION_CHOSEN,
             Q_SOLD_PRICE, Q_CANCEL_REASON, Q_CANCEL_NOTE)


# ---------------------------------------------------------------------------- small helpers

def money(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    for fmt in ("%a %b %d, %Y %I:%M %p", "%a %b %d, %Y", "%m/%d/%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raw = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def month(dt: datetime | date | None) -> str:
    return dt.strftime("%Y-%m") if dt else "(no date)"


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def custom_field(record: dict, label: str) -> Any:
    for f in record.get("custom_fields") or []:
        if isinstance(f, dict) and f.get("label") == label:
            return f.get("value")
    return None


# ---------------------------------------------------------------------------- Workiz + AHS

SUFFIXES = {"STREET", "ST", "AVENUE", "AVE", "AV", "ROAD", "RD", "DRIVE", "DR", "LANE", "LN",
            "COURT", "CT", "CIRCLE", "CIR", "BOULEVARD", "BLVD", "PLACE", "PL", "TERRACE",
            "TER", "TERR", "WAY", "TRAIL", "TRL", "PARKWAY", "PKWY", "HIGHWAY", "HWY", "COVE",
            "CV", "LOOP", "RUN", "PATH", "PT", "POINT", "PLZ", "PLAZA", "SQ", "XING", "ROW",
            "GLN", "GLEN", "BND", "BEND", "CRES"}
DIRECTIONS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW", "NORTH", "SOUTH", "EAST", "WEST",
              "NORTHEAST", "NORTHWEST", "SOUTHEAST", "SOUTHWEST"}
ORDINALS = {"FIRST": "1ST", "SECOND": "2ND", "THIRD": "3RD", "FOURTH": "4TH", "FIFTH": "5TH"}


def norm_address(street: str | None) -> tuple[str | None, str | None]:
    """(house number, first two street words) — unit, suffix and direction dropped."""
    if not isinstance(street, str):
        return None, None
    s = re.sub(r"^\s*(\d+[A-Z]?)\s*,", r"\1 ", street.upper()).split(",")[0]
    s = re.sub(r"\b(APT|UNIT|STE|SUITE|LOT|BLDG|#)\s*\S+.*$", "", s).replace("#", " ")
    words = re.sub(r"[^A-Z0-9 ]", " ", s).split()
    if not words:
        return None, None
    num = words[0] if re.match(r"^\d+[A-Z]?$", words[0]) else None
    rest = [ORDINALS.get(w, w) for w in words[1:] if w not in SUFFIXES and w not in DIRECTIONS]
    rest = [re.sub(r"^(\d+)(ST|ND|RD|TH)$", r"\1", w) for w in rest]
    return num, " ".join(rest[:2])


def _zip(zip_col: Any, address: Any = None) -> str | None:
    """The ZIP column, else a ZIP at the END of the address — never the house number."""
    m = re.search(r"(\d{5})", zip_col) if isinstance(zip_col, str) else None
    if m is None and isinstance(address, str):
        m = re.search(r"(\d{5})\s*(?:,?\s*USA)?\s*$", address)
    return m.group(1) if m else None


def _export_order(path: Path) -> tuple:
    m = re.search(r"(\d+)(_new)?\.csv$", path.name)
    return (0, int(m.group(1)), 1 if m.group(2) else 0) if m else (1, 0, 0)


@dataclass
class WorkizJob:
    number: str
    is_ahs: bool
    status: str
    type: str
    tech: str
    tags: str
    total: float
    created: datetime | None
    num: str | None
    street: str | None
    zip: str | None


def load_workiz_jobs(input_dir: Path) -> dict[str, WorkizJob]:
    """Every Workiz jobs export, applied in export order: a later export's values win."""
    files = sorted((p for p in input_dir.glob("*.csv")
                    if re.search(r"workiz[-_]jobs", p.name, re.IGNORECASE)), key=_export_order)
    jobs: dict[str, WorkizJob] = {}
    for path in files:
        for r in read_csv(path):
            number = (r.get("Job #") or "").strip()
            if not number:
                continue
            tags = r.get("Tags") or ""
            num, street = norm_address(r.get("Address"))
            jobs[number] = WorkizJob(
                number=number, status=(r.get("Status") or "").strip(),
                is_ahs=(r.get("Source") == "AHS") or bool(re.search(r"\bAHS\b", tags)),
                type=(r.get("Type") or "").strip(), tech=(r.get("Tech") or "").strip(),
                tags=tags, total=money(r.get("Total")) or 0.0,
                created=parse_dt(r.get("Job Created")), num=num, street=street,
                zip=_zip(r.get("Zip code"), r.get("Address")))
    return jobs


def load_workiz_invoices(input_dir: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for path in sorted(input_dir.glob("workiz-invoices*.csv")):
        for r in read_csv(path):
            job = (r.get("Job") or "").strip()
            if job:
                out[job] = {"total": money(r.get("Total Amount")) or 0.0,
                            "due": money(r.get("Amount Due")) or 0.0,
                            "status": (r.get("Status") or "").split(" - ")[0].strip(),
                            "created": parse_dt(r.get("Created"))}
    return out


@dataclass
class Dispatch:
    dispatch_id: str
    covered: float
    submitted: datetime | None
    status: str
    num: str | None
    street: str | None
    zip: str | None


def load_ahs(input_dir: Path) -> dict[str, Dispatch]:
    """The AHS vendor portal export(s): one row per invoice line, grouped by dispatch."""
    out: dict[str, Dispatch] = {}
    for path in sorted(input_dir.glob("submitted_invoices_vendor_*.csv")):
        for r in read_csv(path):
            did = (r.get("Dispatch ID") or "").strip()
            if not did:
                continue
            sub = parse_dt(r.get("Invoice Submitted"))
            num, street = norm_address(r.get("Street"))
            prev = out.get(did)
            if prev is not None and prev.submitted and sub and prev.submitted <= sub:
                continue                       # keep the earliest submit per dispatch
            out[did] = Dispatch(did, money(r.get("Dispatch Total")) or 0.0, sub,
                                (r.get("Status") or "").strip(), num, street,
                                _zip(r.get("ZIP")))
    return out


@dataclass
class Match:
    dispatch: Dispatch
    job: WorkizJob
    method: str


MATCH_RULES = ("address + ZIP", "address", "number + ZIP + street word")


def same_place(method: str, d: Dispatch, j: WorkizJob) -> bool:
    if j.num != d.num:
        return False
    if method == "address + ZIP":
        return j.street == d.street and j.zip == d.zip
    if method == "address":
        return j.street == d.street
    return (j.zip == d.zip and bool(j.street) and bool(d.street)
            and j.street.split()[0] == d.street.split()[0])


def match_dispatches(dispatches: dict[str, Dispatch],
                     jobs: dict[str, WorkizJob]) -> list[Match]:
    """Each AHS dispatch to the Workiz job at the same address: address + ZIP, else address,
    else house number + ZIP + first street word; created at most 3 days after the submit;
    nearest in time first, one dispatch per job (a second dispatch may share a job only when
    nothing else is left)."""
    cands = []
    for d in dispatches.values():
        if not d.num:
            continue
        for method in MATCH_RULES:
            found = [j for j in jobs.values() if same_place(method, d, j)]
            if found:
                for j in found:
                    if d.submitted and j.created:
                        gap = (d.submitted - j.created.replace(tzinfo=None)).days
                        if gap >= -3:
                            cands.append((gap, d.dispatch_id, j.number, method))
                break
    cands.sort()
    used_jobs: set[str] = set()
    chosen: dict[str, tuple[str, str]] = {}
    for _gap, did, number, method in cands:
        if did not in chosen and number not in used_jobs:
            chosen[did] = (number, method)
            used_jobs.add(number)
    for _gap, did, number, method in cands:
        if did not in chosen:
            chosen[did] = (number, method + " (shared job)")
    return [Match(dispatches[d], jobs[n], m) for d, (n, m) in chosen.items()]


@dataclass
class UpsellRow:
    job: str
    created: datetime | None
    tech: str
    type: str
    dispatch: str
    covered: float
    closed: float
    closed_from: str
    difference: float
    outcome: str
    method: str


def upsells(matches: list[Match], invoices: dict[str, dict]) -> list[UpsellRow]:
    rows = []
    for m in matches:
        inv = invoices.get(m.job.number)
        closed, source = (inv["total"], "Workiz invoice") if inv else (m.job.total, "job total")
        if closed <= 0:
            outcome = "not closed ($0)"
        elif abs(closed - m.dispatch.covered) < 0.01:
            outcome = "AHS only"
        elif closed > m.dispatch.covered:
            outcome = "UPSELL"
        else:
            outcome = "closed lower"
        rows.append(UpsellRow(m.job.number, m.job.created, m.job.tech or "(none)", m.job.type,
                              m.dispatch.dispatch_id, m.dispatch.covered, closed, source,
                              round(closed - m.dispatch.covered, 2), outcome, m.method))
    rows.sort(key=lambda r: (r.created or datetime.min, r.job))
    return rows


# ---------------------------------------------------------------------------- Zuper (CRM copy)

def latest_jobs(db: Session) -> list[dict]:
    newest = (select(func.max(ZuperRecordVersion.id)).where(ZuperRecordVersion.module == "job")
              .group_by(ZuperRecordVersion.zuper_uid))
    return [r for r in db.scalars(select(ZuperRecordVersion.record)
                                  .where(ZuperRecordVersion.id.in_(newest))).all()
            if isinstance(r, dict) and not r.get("is_deleted")]


def board_of(job: dict) -> str | None:
    cat = job.get("job_category") or {}
    return cat.get("category_name") if isinstance(cat, dict) else None


def answers(job: dict) -> dict[str, Any]:
    """Each KPI answer: the job field of the same name first — every question copies into one
    (section "KPI", 2026-09-30), and a correction is made there — else the latest answer in the
    job's stage checklists. The status history keeps what was entered at the move either way."""
    out: dict[str, Any] = {}
    for entry in job.get("job_status") or []:
        for item in (entry or {}).get("checklist") or []:
            q = (item or {}).get("question")
            if q in QUESTIONS and item.get("answer") not in (None, "", []):
                out[q] = item.get("answer")
    for q in QUESTIONS:
        value = custom_field(job, q)
        if value not in (None, "", []):
            out[q] = value
    return out


def is_callback(job: dict) -> bool:
    kind = str(custom_field(job, "Job Type") or "")
    tags = " ".join(str(t) for t in job.get("job_tags") or [])
    return "callback" in kind.lower() or "callback" in tags.lower()


def moves_by_job(db: Session) -> dict[str, list[ZuperStatusHistory]]:
    out: dict[str, list[ZuperStatusHistory]] = defaultdict(list)
    for m in db.scalars(select(ZuperStatusHistory).order_by(ZuperStatusHistory.changed_at)).all():
        out[m.job_uid].append(m)
    return out


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


# ---------------------------------------------------------------------------- money and commissions

# Line items on a job (2026-10-01): what AHS paid vs what the customer paid on top.
AHS_ITEMS = ("AHS-LEAK-REPAIR", "AHS-TRIP", "AHS-ADDL-LEAK")
UPGRADE_TIERS = {"UPG-GOOD": "Good", "UPG-BETTER": "Better", "UPG-BEST": "Best",
                 "UPG-UNKNOWN": "not recorded"}

# The commission rule, kept OUT of the code so it can change without a deploy: a CSV named
# `commission-rules.csv` in the input folder (technician,item,kind,value,from) replaces these.
# `item` may end in * ("UPG-*"); `kind` is flat (per unit) or percent (of the line); a rule
# applies to jobs dated on or after `from`, the latest one winning — an old period keeps its rule.
RULES_FILE = "commission-rules.csv"
DEFAULT_RULES = [
    {"technician": "Antonio Brown", "item": item, "kind": kind, "value": value,
     "from": "2026-01-01"}
    for item, kind, value in (("AHS-LEAK-REPAIR", "flat", 350), ("AHS-TRIP", "flat", 50),
                              ("AHS-ADDL-LEAK", "flat", 350), ("UPG-*", "percent", 50))
]


def load_rules(input_dir: Path) -> tuple[list[dict], str]:
    path = input_dir / RULES_FILE
    if not path.exists():
        return DEFAULT_RULES, "built-in (no %s on the server)" % RULES_FILE
    rules = []
    for r in read_csv(path):
        try:
            rules.append({"technician": (r.get("technician") or "").strip(),
                          "item": (r.get("item") or "").strip(),
                          "kind": (r.get("kind") or "").strip().lower(),
                          "value": float(r.get("value") or 0),
                          "from": (r.get("from") or "2000-01-01").strip()})
        except ValueError:
            continue
    return rules, RULES_FILE


def _rule_for(rules: list[dict], tech: str, code: str, day: str) -> dict | None:
    from fnmatch import fnmatch
    fits = [r for r in rules if r["technician"] in (tech, "*") and fnmatch(code, r["item"])
            and r["from"] <= day]
    return max(fits, key=lambda r: r["from"]) if fits else None


def line_items(job: dict) -> list[dict]:
    return [p for p in job.get("products") or [] if isinstance(p, dict)]


def expected_commission(job: dict, rules: list[dict]) -> float | None:
    tech = str(custom_field(job, "Technician") or "").strip()
    day = str(job.get("scheduled_start_time") or job.get("created_at") or "")[:10]
    total, any_rule = 0.0, False
    for p in line_items(job):
        rule = _rule_for(rules, tech, str(p.get("product_id") or ""), day)
        if rule is None:
            continue
        any_rule = True
        qty = float(p.get("quantity") or 1)
        line_total = money(p.get("total")) or (money(p.get("price")) or 0) * qty
        total += rule["value"] * qty if rule["kind"] == "flat" else line_total * rule["value"] / 100
    return round(total, 2) if any_rule else None


def week_of(day: datetime | None) -> str:
    if day is None:
        return "(no date)"
    monday = day.date() - timedelta(days=day.weekday())
    return monday.isoformat()


def latest_records(db: Session, module: str) -> list[dict]:
    newest = (select(func.max(ZuperRecordVersion.id)).where(ZuperRecordVersion.module == module)
              .group_by(ZuperRecordVersion.zuper_uid))
    return [r for r in db.scalars(select(ZuperRecordVersion.record)
                                  .where(ZuperRecordVersion.id.in_(newest))).all()
            if isinstance(r, dict) and not r.get("is_deleted")]


def _person_name(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(p for p in (value.get("first_name"), value.get("last_name")) if p) or "?"
    return str(value or "?")


def pay_sheet_periods(input_dir: Path) -> list[list]:
    """Antonio's pay sheet (antonio-commissions.xlsx), one row per pay period: AHS jobs, extras and
    his pay. Two identical tabs are counted once: the one whose job dates fall inside the period
    its name gives is the real one, the other is reported as a copy (the 2026-10-01 file had the
    Aug 1-15 jobs pasted into the Jul 15 - Aug 1 tab as well)."""
    path = input_dir / "antonio-commissions.xlsx"
    if not path.exists():
        return []
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True, read_only=True)
    tabs = []
    for ws in wb.worksheets:
        title = ws.title.strip()
        jobs_rows, extras, in_extras = [], [], False
        for r in ws.iter_rows(values_only=True):
            if not r:
                continue
            if isinstance(r[0], datetime):
                jobs_rows.append(r)
            elif str(r[0]).strip().lower() == "type":
                in_extras = True
            elif in_extras and isinstance(r[0], str) and r[0].strip() and len(r) > 5 \
                    and isinstance(r[4], int | float):
                extras.append(r)
        bounds = re.findall(r"(\d{8})", title)
        try:
            start, end = (datetime.strptime(b, "%m%d%Y") for b in (bounds[0], bounds[-1]))
            margin = timedelta(days=3)
            fits = all(start - margin <= r[0] <= end + margin for r in jobs_rows)
        except (IndexError, ValueError):
            fits = True
        sig = tuple(sorted((str(r[0])[:10], str(r[4]), str(r[5])) for r in jobs_rows))
        tabs.append((title, jobs_rows, extras, sig, fits))
    real: dict[tuple, tuple[str, bool]] = {}          # contents -> (the real tab, its dates fit)
    for title, _rows, _extras, sig, fits in tabs:
        if sig and (sig not in real or (fits and not real[sig][1])):
            real[sig] = (title, fits)
    real = {sig: title for sig, (title, _fits) in real.items()}
    out = []
    for title, jobs_rows, extras, sig, _fits in tabs:
        if sig and real.get(sig) != title:
            note = "copy of tab %s - not counted" % real[sig]
            out.append([title, None, None, None, None, None, note])
            continue
        jt = sum(money(r[4]) or 0 for r in jobs_rows)
        jc = sum(money(r[5]) or 0 for r in jobs_rows)
        es = sum(money(r[4]) or 0 for r in extras)
        ec = sum(money(r[5]) or 0 for r in extras if len(r) > 5)
        out.append([title, len(jobs_rows), jt, len(extras), es, jc + ec, ""])
    return out


# ---------------------------------------------------------------------------- the workbook

MONEY_FMT = '"$"#,##0.00'


def _sheet(wb, title: str, headers: list[str], rows: list[list], widths: dict | None = None,
           money_cols: tuple[int, ...] = ()):
    from openpyxl.styles import Font, PatternFill
    ws = wb.create_sheet(title)
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E78")
    for r in rows:
        ws.append([v.replace(tzinfo=None) if isinstance(v, datetime) and v.tzinfo else v
                   for v in r])
    for col in money_cols:
        for row in ws.iter_rows(min_row=2, min_col=col, max_col=col):
            for c in row:
                c.number_format = MONEY_FMT
    ws.freeze_panes = "A2"
    for i, h in enumerate(headers, start=1):
        letter = ws.cell(row=1, column=i).column_letter
        ws.column_dimensions[letter].width = (widths or {}).get(h, max(12, min(40, len(h) + 4)))
    return ws


def build(db: Session, input_dir: Path, now: datetime | None = None):
    """The workbook (openpyxl) and a small summary of what went in."""
    from openpyxl import Workbook
    now = now or datetime.now(UTC)
    wb = Workbook()
    wb.remove(wb.active)

    jobs = [j for j in latest_jobs(db) if board_of(j) in BOARDS]
    by_job = moves_by_job(db)
    wjobs = load_workiz_jobs(input_dir) if input_dir.exists() else {}
    winv = load_workiz_invoices(input_dir) if input_dir.exists() else {}
    ahs = load_ahs(input_dir) if input_dir.exists() else {}
    up = upsells(match_dispatches(ahs, wjobs), winv)

    # ---- Summary
    summary: list[list] = [["Built", now.astimezone().strftime("%Y-%m-%d %H:%M"), ""], ["", "", ""]]
    summary.append(["ZUPER, NOW (from the CRM's copy)", "", ""])
    for b in BOARDS:
        mine = [j for j in jobs if board_of(j) == b]
        types = defaultdict(int)
        for j in mine:
            types[str((j.get("current_job_status") or {}).get("status_type") or "?")] += 1
        closed = types.get("PAID", 0) + types.get("COMPLETED", 0)
        summary += [[b, "jobs", len(mine)],
                    [b, "closed (Paid / Completed)", closed],
                    [b, "cancelled", types.get("CANCELED", 0)],
                    [b, "open", len(mine) - closed - types.get("CANCELED", 0)],
                    [b, "callbacks (re-work / warranty)", sum(is_callback(j) for j in mine)]]
    ahs_now = [j for j in jobs if board_of(j) == "AHS - Repair & Review"]
    chose = [answers(j) for j in ahs_now]
    chose = [a for a in chose if Q_CUSTOMER_CHOSE in a]
    summary += [["AHS - Repair & Review",
                 "upsells recorded in Zuper (\"Customer chose\" not AHS only)",
                 sum(str(a[Q_CUSTOMER_CHOSE]).strip().lower() != "ahs only" for a in chose)],
                ["AHS - Repair & Review", "customer paid, recorded in Zuper ($)",
                 sum(money(a.get(Q_CUSTOMER_PAID)) or 0 for a in chose)],
                ["", "", ""]]
    considered = [r for r in up if r.outcome != "not closed ($0)"]
    ups = [r for r in considered if r.outcome == "UPSELL"]
    summary += [["BEFORE ZUPER (Workiz + AHS files)", "", ""],
                ["AHS", "AHS dispatches in the files", len(ahs)],
                ["AHS", "matched to a Workiz job", len(up)],
                ["AHS", "matched and closed for more than $0", len(considered)],
                ["AHS", "UPSELLS (closed > AHS invoice)", len(ups)],
                ["AHS", "upsell rate", f"{len(ups) / len(considered):.0%}" if considered else "-"],
                ["AHS", "customer paid on upsells ($)", round(sum(r.difference for r in ups), 2)],
                ["AHS", "AHS paid on the matched jobs ($) — every AHS invoice counts as paid",
                 round(sum(r.covered for r in considered), 2)]]
    ws = _sheet(wb, "Summary", ["Board", "Measure", "Value"], summary,
                {"Board": 38, "Measure": 62, "Value": 18})
    for row in ws.iter_rows(min_row=2):
        if isinstance(row[2].value, float):
            row[2].number_format = MONEY_FMT

    # ---- AHS upsells (per job, per month, per tech)
    _sheet(wb, "AHS upsells", ["Workiz job #", "Job created", "Tech", "Type", "AHS dispatch",
                               "AHS paid", "Closed for", "Closed from", "Customer paid (diff)",
                               "Outcome", "How matched"],
           [[r.job, r.created, r.tech, r.type, r.dispatch, r.covered, r.closed, r.closed_from,
             r.difference, r.outcome, r.method] for r in up],
           {"Job created": 18, "Type": 22, "How matched": 30}, money_cols=(6, 7, 9))
    for title, key in (("Upsells by month", lambda r: month(r.created)),
                       ("Upsells by tech", lambda r: r.tech)):
        groups: dict[str, list[UpsellRow]] = defaultdict(list)
        for r in considered:
            groups[key(r)].append(r)
        rows = []
        for k in sorted(groups):
            g = groups[k]
            u = [r for r in g if r.outcome == "UPSELL"]
            rows.append([k, len(g), len(u), f"{len(u) / len(g):.0%}",
                         round(sum(r.difference for r in u), 2),
                         round(statistics.median([r.difference for r in u]), 2) if u else None])
        _sheet(wb, title, ["Month" if "month" in title else "Tech", "AHS jobs closed",
                           "Upsells", "Rate", "Customer paid ($)", "Median upsell ($)"], rows,
               money_cols=(5, 6))

    # ---- Workiz monthly (before Zuper)
    monthly: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for j in wjobs.values():
        seg = "AHS" if j.is_ahs else "Retail"
        m = monthly[(month(j.created), seg)]
        m["created"] += 1
        m["done"] += j.status == "Done"
        m["cancelled"] += j.status == "Canceled"
        m["callbacks"] += "callback" in j.tags.lower()
        m["done $"] += j.total if j.status == "Done" else 0
    _sheet(wb, "Workiz by month", ["Month", "AHS / Retail", "Jobs created", "Done",
                                   "Cancelled", "Callbacks", "Done total ($)"],
           [[k[0], k[1], int(v["created"]), int(v["done"]), int(v["cancelled"]),
             int(v["callbacks"]), round(v["done $"], 2)] for k, v in sorted(monthly.items())],
           money_cols=(7,))

    # ---- Jobs now
    rows = []
    for j in sorted(jobs, key=lambda x: (board_of(x) or "", str(x.get("work_order_number")))):
        cur = j.get("current_job_status") or {}
        hist = by_job.get(j.get("job_uid"), [])
        since = _aware(hist[-1].changed_at) if hist else None
        a = answers(j)
        rows.append([
            j.get("work_order_number"), custom_field(j, "Workiz Job #"), board_of(j),
            cur.get("status_name"), cur.get("status_type"), parse_dt(j.get("created_at")),
            custom_field(j, "Technician"), money(j.get("job_total")),
            round((now - since).total_seconds() / 86400, 1) if since else None,
            "yes" if is_callback(j) else "", custom_field(j, F_ORIGINAL_JOB),
            a.get(Q_AHS_AUTHORIZED), a.get(Q_CUSTOMER_CHOSE), a.get(Q_CUSTOMER_PAID),
            a.get(Q_OPTION_CHOSEN), a.get(Q_SOLD_PRICE), a.get(Q_CANCEL_REASON),
            a.get(Q_CANCEL_NOTE)])
    _sheet(wb, "Jobs now", ["Job #", "Workiz job #", "Board", "Column", "Status type",
                            "Created in Zuper", "Technician", "Job total", "Days in column",
                            "Callback", F_ORIGINAL_JOB, Q_AHS_AUTHORIZED, Q_CUSTOMER_CHOSE,
                            Q_CUSTOMER_PAID, Q_OPTION_CHOSEN, Q_SOLD_PRICE, Q_CANCEL_REASON,
                            Q_CANCEL_NOTE], rows,
           {"Board": 24, "Column": 28, "Created in Zuper": 18}, money_cols=(8,))

    # ---- Income per job (line items) and commissions
    rules, rules_from = load_rules(input_dir) if input_dir.exists() else (DEFAULT_RULES, "built-in")
    commissions = latest_records(db, "commission")
    comm_by_job: dict[str, float] = defaultdict(float)
    for c in commissions:
        job_uid = str(c.get("job_uid") or (c.get("job") or {}).get("job_uid"))
        comm_by_job[job_uid] += money(c.get("commission_amount")) or 0
    income_rows = []
    for j in sorted(jobs, key=lambda x: (board_of(x) or "", str(x.get("work_order_number")))):
        items = line_items(j)
        if not items and not money(j.get("job_total")):
            continue
        ahs_paid = sum(money(p.get("total")) or 0 for p in items
                       if p.get("product_id") in AHS_ITEMS)
        upg = [p for p in items if p.get("product_id") in UPGRADE_TIERS]
        cust = sum(money(p.get("total")) or 0 for p in upg)
        tiers = ", ".join(sorted({UPGRADE_TIERS[p["product_id"]] for p in upg}))
        a = answers(j)
        actual = comm_by_job.get(j.get("job_uid"))
        expected = expected_commission(j, rules)
        income_rows.append([
            j.get("work_order_number"), board_of(j),
            (j.get("current_job_status") or {}).get("status_name"),
            custom_field(j, "Technician"), money(j.get("job_total")), ahs_paid or None,
            cust or None,
            tiers or ("AHS only" if items and ahs_paid else ""), len(items),
            a.get(Q_CUSTOMER_CHOSE), money(a.get(Q_CUSTOMER_PAID)), money(a.get(Q_SOLD_PRICE)),
            actual, expected,
            round(actual - expected, 2) if expected is not None and actual is not None
            else None])
    _sheet(wb, "Income per job", ["Job #", "Board", "Column", "Technician", "Job total", "AHS paid",
                                  "Customer paid (upgrade)", "Upgrade tier", "Line items",
                                  Q_CUSTOMER_CHOSE, Q_CUSTOMER_PAID, Q_SOLD_PRICE,
                                  "Commission in Zuper", "Expected commission (rule)",
                                  "Difference (Zuper - rule)"], income_rows,
           {"Board": 24, "Column": 26, "Technician": 16}, money_cols=(5, 6, 7, 11, 12, 13, 14, 15))

    comm_rows = []
    weekly: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0, 0.0, 0.0])
    for c in sorted(commissions, key=lambda x: str(x.get("commission_date") or "")):
        day = parse_dt(str(c.get("commission_date") or "")[:10])
        tech = _person_name(c.get("assigned_to"))
        amount = money(c.get("commission_amount")) or 0
        paid = c.get("payout_status") == "PAID"
        payouts = c.get("payout_details") or []
        paid_on = (payouts[-1] or {}).get("payout_date") if payouts else None
        comm_rows.append([(c.get("job") or {}).get("work_order_number"), tech, amount, day,
                          week_of(day),
                          c.get("payout_status"), str(paid_on or "")[:10], c.get("description")])
        w = weekly[(tech, week_of(day))]
        w[0] += 1
        w[1] += amount
        w[2] += amount if paid else 0
    _sheet(wb, "Commissions", ["Job #", "Technician", "Amount", "Commission date",
                               "Week of (Monday)", "Payout status", "Paid on", "Description"],
           comm_rows,
           {"Description": 46, "Commission date": 18}, money_cols=(3,))
    _sheet(wb, "Commissions by week", ["Technician", "Week of (Monday)", "Commissions", "Total",
                                       "Paid", "Still owed"],
           [[t, wk, int(v[0]), round(v[1], 2), round(v[2], 2), round(v[1] - v[2], 2)]
            for (t, wk), v in sorted(weekly.items())], money_cols=(4, 5, 6))
    periods = pay_sheet_periods(input_dir) if input_dir.exists() else []
    if periods:
        _sheet(wb, "Antonio pay sheets", ["Pay period (tab)", "AHS jobs", "AHS $", "Extras",
                                          "Extras sold $", "Antonio pay", "Note"], periods,
               {"Pay period (tab)": 24, "Note": 40}, money_cols=(3, 5, 6))

    # ---- New jobs per month from Zuper (loaded Workiz jobs kept apart)
    new_jobs: dict[tuple[str, str, str], int] = defaultdict(int)
    for j in jobs:
        origin = "loaded from Workiz" if custom_field(j, "Workiz Job #") else "made in Zuper"
        new_jobs[(month(parse_dt(str(j.get("created_at") or ""))), board_of(j) or "?", origin)] += 1
    _sheet(wb, "New jobs by month", ["Month (created in Zuper)", "Board", "Origin", "Jobs"],
           [[k[0], k[1], k[2], v] for k, v in sorted(new_jobs.items())],
           {"Board": 24, "Origin": 20})

    upg_jobs = [r for r in income_rows if r[6]]
    comm_total = sum(money(c.get("commission_amount")) or 0 for c in commissions)
    comm_owed = sum(money(c.get("commission_amount")) or 0 for c in commissions
                    if c.get("payout_status") != "PAID")
    summary_tail = [["", "", ""], ["MONEY ON JOBS (Zuper line items)", "", ""],
                    ["AHS", "jobs with line items", sum(1 for r in income_rows if r[8])],
                    ["AHS", "AHS paid on those jobs ($)",
                     round(sum(r[5] or 0 for r in income_rows), 2)],
                    ["AHS", "jobs with a customer upgrade", len(upg_jobs)],
                    ["AHS", "customers paid on upgrades ($)",
                     round(sum(r[6] or 0 for r in upg_jobs), 2)],
                    ["", "", ""], ["COMMISSIONS (Zuper)", "", ""],
                    ["All", "commissions recorded", len(commissions)],
                    ["All", "total ($)", round(comm_total, 2)],
                    ["All", "still owed ($)", round(comm_owed, 2)],
                    ["All", "commission rule from", rules_from]]
    for row in summary_tail:
        wb["Summary"].append(row)
        if isinstance(row[2], float):
            wb["Summary"].cell(row=wb["Summary"].max_row, column=3).number_format = MONEY_FMT

    # ---- Time in column (moves since TIMING_FROM) + the moves themselves
    board_uids = {}
    for j in jobs:
        cat = j.get("job_category") or {}
        board_uids[cat.get("category_uid")] = cat.get("category_name")
    spans: dict[tuple[str, str], list[float]] = defaultdict(list)
    sitting: dict[tuple[str, str], list[float]] = defaultdict(list)
    move_rows = []
    job_no = {j.get("job_uid"): j.get("work_order_number") for j in jobs}
    for uid, hist in by_job.items():
        if uid not in job_no:
            continue
        for i, m in enumerate(hist):
            at = _aware(m.changed_at)
            if at is None or at < TIMING_FROM:
                continue
            board = board_uids.get(m.category_uid) or m.category_name or "?"
            nxt = _aware(hist[i + 1].changed_at) if i + 1 < len(hist) else None
            hours = ((nxt or now) - at).total_seconds() / 3600
            (spans if nxt else sitting)[(board, m.status_name or "?")].append(hours)
            move_rows.append([job_no[uid], board, m.status_name, at, m.done_by_name,
                              round(hours, 1), "" if nxt else "still there"])
    rows = []
    for key in sorted(set(spans) | set(sitting)):
        done, now_in = spans.get(key, []), sitting.get(key, [])
        rows.append([key[0], key[1], len(done),
                     round(statistics.median(done), 1) if done else None,
                     round(statistics.mean(done), 1) if done else None,
                     round(max(done), 1) if done else None, len(now_in),
                     round(max(now_in) / 24, 1) if now_in else None])
    rows.sort(key=lambda r: -(r[3] or 0))
    _sheet(wb, "Time in column", ["Board", "Column", "Jobs that left it", "Median hours",
                                  "Average hours", "Longest hours", "Jobs in it now",
                                  "Longest wait now (days)"], rows,
           {"Board": 24, "Column": 30})
    move_rows.sort(key=lambda r: r[3], reverse=True)
    _sheet(wb, "Moves", ["Job #", "Board", "Column", "Moved at (UTC)", "By", "Hours in column",
                         "Note"], move_rows, {"Moved at (UTC)": 20, "Column": 30, "Board": 24})

    # ---- Read me
    moves_stored = sum(len(v) for v in by_job.values())
    readme = [
        ["What this is", ("KPIs for AHS - Inspection, AHS - Repair & Review and Retail. Built "
                          "nightly on the CRM server; one file per day, never overwritten.")],
        ["Upsell", ("A job closed for MORE than the AHS invoice. The difference is what the "
                    "customer paid. Before Zuper the tier (Good/Better/Best) was not recorded.")],
        ["AHS paid", "Every invoice in the AHS vendor export counts as paid (owner, 2026-09-30)."],
        ["Callback", ("Re-work / warranty only (Job Type Callback/Warranty or the Callback "
                      "tag), not the 'Call Back' column.")],
        ["Time in column", ("Counted from 2026-09-26. Zuper's history before that is the "
                            "2026-09-17 load and bulk moves. 'By' shows Owen for moves made "
                            "through the API key.")],
        ["From now on", ("Upsell tier, customer paid, AHS authorized, Retail option and cancel "
                         "reason come from the questions in docs/ZUPER-KPI-SETUP.md — empty "
                         "until the team answers them in Zuper.")],
        ["Matching", ("AHS dispatches are matched to Workiz jobs by address (house number, "
                      "street, ZIP) and the nearest date; 'How matched' says which rule found "
                      "each one.")],
        ["Income per job", ("From the job's line items in Zuper: AHS paid = AHS - Leak Repair "
                            "/ Trip / Additional Leak; customer paid = Customer Upgrade "
                            "(Good/Better/Best/not recorded).")],
        ["Commissions", ("Every commission recorded in Zuper, by week and technician, paid or "
                         "owed. 'Expected commission' applies the rule in commission-rules.csv "
                         "on the server (dated, so a new rule never rewrites an old period); a "
                         "difference flags a missing or wrong commission.")],
        ["Privacy", "Job numbers only: no customer names, phones, emails or addresses."],
        ["Sources", (f"Zuper jobs: {len(jobs)} · moves stored: {moves_stored} · Workiz jobs: "
                     f"{len(wjobs)} · Workiz invoices: {len(winv)} · AHS dispatches: {len(ahs)}")],
    ]
    _sheet(wb, "Read me", ["Topic", "Explanation"], readme, {"Topic": 18, "Explanation": 120})
    wb.move_sheet("Read me", offset=-len(wb.sheetnames) + 1)
    wb.active = 1
    return wb, {"zuper_jobs": len(jobs), "workiz_jobs": len(wjobs), "ahs_dispatches": len(ahs),
                "matched": len(up), "upsells": len(ups),
                "upsell_customer_paid": round(sum(r.difference for r in ups), 2)}


def write(db: Session, input_dir: Path = INPUT_DIR, output_dir: Path = OUTPUT_DIR,
          now: datetime | None = None) -> tuple[Path, dict]:
    """Build and save today's file. An existing file is never overwritten."""
    now = now or datetime.now(UTC)
    wb, info = build(db, input_dir, now)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.astimezone().strftime("%Y-%m-%d")
    path = output_dir / f"KPI-{stamp}.xlsx"
    if path.exists():
        path = output_dir / f"KPI-{stamp}-{now.astimezone().strftime('%H%M%S')}.xlsx"
    wb.save(path)
    return path, info


def nightly(db: Session, now: datetime | None = None) -> None:
    """Called by the worker after the history pass. Never raises into it."""
    try:
        path, info = write(db, now=now)
        log.info("kpi report written: %s %s", path, info)
    except Exception:
        log.exception("kpi report not written")


def main(argv: list[str] | None = None) -> int:
    from .db import SessionLocal
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", type=Path, default=INPUT_DIR)
    ap.add_argument("--out", type=Path, default=OUTPUT_DIR)
    args = ap.parse_args(argv)
    with SessionLocal() as db:
        path, info = write(db, args.input, args.out)
    print(path)
    print(info)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

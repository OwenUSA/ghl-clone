"""Compare an uploaded spreadsheet with Zuper, row by row (2026-10-01).

PURE: the parsed sheets (sheets.parse), the Dispatch page's copy of Zuper's jobs, the boards
hidden from the reader and `now` in; one result per row out. The AI explains the result; it does
not do the matching — the same file always gives the same answer. Pinned by
tests/test_dispatch_compare.py.

Columns are found by their header (any language-ish spelling the office uses): a job number
("Job #", "WO", "Zuper WO", "Work order"), a customer ("Customer", "Name", "Client"), a phone,
an address, a city, a status ("Status", "Stage"). A row is matched to Zuper, in this order:
  1. a job number in the row that is a Zuper job number;
  2. a phone in the row that a Zuper customer holds;
  3. the customer's name (all words, or first + last);
  4. the street address (house number + street name).
Then it says what Zuper shows now and what differs from the row.
"""
from __future__ import annotations

import re
from datetime import datetime

from . import config as c
from .comms import digits

JOB_HEADERS = ("job #", "job#", "job no", "job number", "zuper wo", "work order", "wo",
               "zuper #", "zuper job")
NAME_HEADERS = ("customer", "client", "name", "homeowner")
PHONE_HEADERS = ("phone", "mobile", "cell", "tel")
ADDRESS_HEADERS = ("address", "street")
CITY_HEADERS = ("city",)
STATUS_HEADERS = ("status", "stage")
STREET_NOISE = {"st", "ave", "rd", "dr", "ln", "ct", "blvd", "way", "ter", "pl", "cir", "sw",
                "nw", "ne", "se", "n", "s", "e", "w", "fl", "street", "avenue", "road",
                "drive", "lane", "court", "unit", "apt"}


def words(s) -> list[str]:
    return re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower()).split()


def addr_key(a) -> tuple[str, str] | None:
    t = words(a)
    if not t or not t[0].isdigit():
        return None
    rest = [w for w in t[1:] if not w.isdigit() and w not in STREET_NOISE]
    return (t[0], rest[0] if rest else "")


def _find(columns: list[str], wanted: tuple[str, ...], exact_short: bool = True) -> str | None:
    """The first column whose header matches. Short keys ("wo", "tel") must be the whole
    header so "Work / note" is never taken for a work order."""
    for col in columns:
        h = " ".join(words(col))
        raw = " ".join(str(col).lower().split())       # keeps "#": "job #"
        for key in wanted:
            if (len(key) <= 3 and exact_short and h == key) or (
                    len(key) > 3 and (key in h or key in raw)):
                return col
    return None


def columns_of(columns: list[str]) -> dict[str, str | None]:
    job = _find(columns, JOB_HEADERS)
    return {"job": job, "name": _find(columns, NAME_HEADERS),
            "phone": _find(columns, PHONE_HEADERS),
            "address": _find(columns, ADDRESS_HEADERS),
            "city": _find(columns, CITY_HEADERS),
            "status": _find(columns, STATUS_HEADERS)}


def _same_stage(file_status: str, zuper: str | None) -> bool:
    """Spacing and dashes do not make a stage different: "Callback" is "Call Back", and
    "Callback - Needs to Schedule" is still Call Back (live list, 2026-10-01)."""
    a, b = "".join(words(file_status)), "".join(words(zuper))
    return bool(a and b) and (a == b or a in b or b in a)


def names_in(cell: str) -> list[str]:
    """The customer names in a cell: "(Northlake Dr)" notes dropped, "A / B" and "A & B" split
    into two people (live list, 2026-10-01)."""
    cell = re.sub(r"\([^)]*\)", " ", cell or "")
    return [" ".join(words(x)) for x in re.split(r"\s*(?:/|&|\+| and )\s*", cell)
            if words(x)]


def _when(dt) -> str:
    dt = c.aware(dt)
    if not dt:
        return ""
    t = c.local(dt)
    return "%s %d, %s" % (t.strftime("%a %b"), t.day, t.strftime("%I:%M %p").lstrip("0"))


def compare(sheets: list[dict], jobs, *, hidden: set[str], now: datetime) -> dict:
    visible = [j for j in jobs if not (j.board and j.board in hidden)]
    by_number = {str(j.job_number): j for j in visible if j.job_number}
    by_phone: dict[str, list] = {}
    by_name: dict[str, list] = {}
    by_addr: dict[tuple, list] = {}
    for j in visible:
        for p in j.phones or []:
            by_phone.setdefault(p, []).append(j)
        n = " ".join(words(j.customer_name))
        if n:
            by_name.setdefault(n, []).append(j)
        k = addr_key(j.address)
        if k:
            by_addr.setdefault(k, []).append(j)

    def by_first_last(name: str) -> list:
        t = name.split()
        if len(t) < 2:
            return []
        return [j for key, js in by_name.items() for j in js
                if key.split()[:1] == t[:1] and key.split()[-1:] == t[-1:]]

    results = []
    skipped = 0
    for sheet in sheets:
        cols = columns_of(sheet["columns"])
        for r in sheet["rows"]:
            v = r["values"]
            name = v.get(cols["name"] or "", "")
            # A row with no customer, job number, phone or address is a heading, a day
            # separator or a total — not a customer (live list, 2026-10-01).
            if not any(v.get(cols[k] or "") for k in ("name", "job", "phone", "address")):
                skipped += 1
                continue
            found, how = [], []
            for num in re.findall(r"\d{2,6}", v.get(cols["job"] or "", "")):
                if num in by_number:
                    found.append(by_number[num])
                    how.append("job #")
            if not found and cols["phone"]:
                for p in {digits(x) for x in re.split(r"[,/;]", v.get(cols["phone"], ""))}:
                    if p and p in by_phone:
                        found += by_phone[p]
                        how.append("phone")
            if not found and name:
                for key in names_in(name):
                    match = by_name.get(key) or by_first_last(key)
                    if match:
                        found += match
                        how.append("name")
            if not found and cols["address"]:
                k = addr_key(v.get(cols["address"]))
                if k and k in by_addr:
                    found += by_addr[k]
                    how.append("address")
            uniq = list({j.job_uid: j for j in found}.values())
            row = {"sheet": sheet["name"], "row": r["row"], "customer": name,
                   "file_status": v.get(cols["status"] or "", ""), "matched_by": "+".join(
                       dict.fromkeys(how)), "job_number": None, "board": None,
                   "zuper_stage": None, "stage_since": None, "visit": None,
                   "assigned": None, "other_jobs": [], "differences": [], "result": ""}
            if not uniq:
                row["result"] = "not in Zuper"
                row["differences"].append(
                    "No Zuper job found by job number, phone, name or address.")
                results.append(row)
                continue
            live = [j for j in uniq if j.is_open] or uniq
            j = max(live, key=lambda x: c.aware(x.zuper_created_at) or now)
            row.update({
                "job_number": j.job_number, "board": j.board, "zuper_stage": j.status,
                "stage_since": _when(j.status_since),
                "visit": _when(j.scheduled_start) if c.aware(j.scheduled_start) and
                c.aware(j.scheduled_start) >= now else "",
                "assigned": ", ".join(j.assigned or []) or (j.technician or ""),
                "other_jobs": ["#%s %s" % (o.job_number, o.status) for o in uniq if o is not j]})
            diffs = row["differences"]
            if row["file_status"] and not _same_stage(row["file_status"], j.status):
                diffs.append("Stage: the file says “%s”, Zuper says “%s”." % (
                    row["file_status"], j.status))
            if cols["address"] and v.get(cols["address"]):
                fk, zk = addr_key(v[cols["address"]]), addr_key(j.address)
                if fk and zk and fk != zk:
                    diffs.append("Address: the file says “%s”, Zuper says “%s”." % (
                        v[cols["address"]], j.address))
            if cols["phone"] and v.get(cols["phone"]):
                p = digits(v[cols["phone"]])
                if p and j.phones and p not in j.phones:
                    diffs.append("Phone %s is not on the Zuper customer." % p)
            if not j.is_open:
                diffs.append("The job is closed in Zuper (%s)." % j.status)
            if len({x.customer_name for x in uniq}) > 1:
                diffs.append("More than one Zuper customer matched — check which is right.")
            row["result"] = "different" if diffs else "same"
            results.append(row)
    counts = {"rows": len(results),
              "matched": sum(1 for x in results if x["result"] != "not in Zuper"),
              "not_in_zuper": sum(1 for x in results if x["result"] == "not in Zuper"),
              "different": sum(1 for x in results if x["result"] == "different"),
              "same": sum(1 for x in results if x["result"] == "same"),
              "not_a_customer_row": skipped}
    return {"counts": counts, "columns_used": {s["name"]: columns_of(s["columns"])
                                               for s in sheets}, "rows": results}

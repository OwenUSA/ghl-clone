"""Owen AI's compact read tools: one job's dossier and one board's snapshot (2026-11-03).

Why: raw Zuper answers are huge (one job record is ~190k characters, mostly checklist answers
repeated on every stage move), so "review job 701" cost Claude 30+ tool turns of spilling and
grepping. These functions fetch the few records a review needs and keep only what a person
reads: stages and who moved them, filled answers, prices per option, money owed, and one
merged timeline of Zuper Connect and Quo calls and texts. Deterministic Python, no model.

READ-ONLY by construction: Zuper only inside `client.owen_ai_read()` (GET only, every other
method refused before a connection) and `client.connect_read` (its five listed reads); Quo only
through `quo.get` (GET on its listed paths). The parsing functions are pure (fixture JSON in,
dict out) so tests feed them captured shapes; `job_dossier` / `board_snapshot` / `open_jobs`
do the fetching.

Shapes verified read-only against the live account on 2026-11-03:
  GET  /jobs?filter.keyword=<n>        rows with work_order_number (keyword also hits phones)
  GET  /jobs/{uid}                     job_status[] = every move, with done_by, time_on_status
                                       (minutes) and checklist[{question, answer, type}]
  GET  /jobs/{uid}/note                rows; the `note` text can be missing (said so per note)
  GET  /estimate?filter.job=<uid>      list rows have NO option totals or line items ->
  GET  /estimate/{uid}                 proposal_options[{option_name, is_accepted, total,
                                       deposit, line_items[{product_id, name, quantity,
                                       unit_price, total, plain_text_description}]}],
                                       sent_date, accepted_date, deposit{total, status}
  GET  /invoice?filter.job=<uid>       invoice_no, invoice_status, total, amount_due,
                                       amount_paid, due_date, paid_date, payment_history
  Connect POST /message/conversations {filters: {status: "", keyword: <10 digits>}} matches
       the number; POST /messages (one conversation, newest first); POST /calls
       {filters: {number: [...]}} is an EXACT string match and Zuper stores both "+1XXXXXXXXXX"
       and "1XXXXXXXXXX", so both forms are sent; who called is from/to.user.user_name.
  Quo  GET /calls and /messages need phoneNumberId + ONE participant; /call-summaries/{id}.
"""
from __future__ import annotations

import contextlib
import json
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..dispatch.comms import NOT_ANSWERED, digits
from ..dispatch.reader import details_summary, note_text
from ..zuper import client as zc
from ..zuper.mapping import parse_time
from . import quo

TZ = ZoneInfo("America/New_York")
SIMPLE_FLOW = "AHS Simple Flow"
SIMPLE_FLOW_UID = "2fbc07ec-94e0-43ca-98d5-c902f12b59e5"
# A job in one of these is not open. Status TYPES first; the names cover older boards' columns.
CLOSED_TYPES = {"COMPLETED", "CANCELED", "CANCELLED", "CLOSED", "LOST", "PAID"}
CLOSED_NAMES = {"paid", "cancelled", "canceled", "estimate declined", "review received",
                "job completed"}

MAX_CHARS = 12_000            # a dossier is trimmed to fit this
DAYS = 60                     # conversation window
TIMELINE_MAX = 60
TEXT_MAX = 300
SUMMARY_MAX = 400
DESC_MAX = 200
PROPOSALS_MAX = 5             # proposal details read per job (newest first)
CONVERSATIONS_MAX = 3         # Zuper Connect conversations read per job
ZUPER_SUMMARIES_MAX = 3       # Connect call details (summaries) read per job
QUO_SUMMARIES_MAX = 6         # Quo call summaries read per job
CONTACT_DAYS = 30             # board snapshot: how far back "last contact" looks
CONTACT_PAGES = 4             # board snapshot: Connect inbox pages read (25 each)
_QUO_CACHE_SECONDS = 3600
# A text's delivery status is noise unless it failed (Connect shows every outgoing as QUEUED).
FAILED_TEXT = {"FAILED", "UNDELIVERED", "REJECTED", "ERROR", "failed", "undelivered"}
# Zuper names its workflow texts' sender "Zuper Admin" — an automation, not a person.
AUTOMATION_NAMES = {"Zuper Admin": "Zuper automation"}


# ------------------------------------------------------------------------------ small helpers

def et(value: Any) -> str | None:
    """A Zuper/Quo timestamp as 'YYYY-MM-DD HH:MM' in America/New_York."""
    dt = value if isinstance(value, datetime) else parse_time(value)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M")


def e164(value: Any) -> str | None:
    d = digits(value)
    return "+1" + d if d else None


def short(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text:
        return None
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def plain(value: Any) -> str | None:
    """HTML (Zuper's editor) as plain words."""
    return note_text(value) if isinstance(value, str) else None


def person(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    name = " ".join(x.strip() for x in (value.get("first_name"), value.get("last_name"))
                    if isinstance(x, str) and x.strip()) or value.get("user_name") or None
    return AUTOMATION_NAMES.get(name, name) if name else None


def money(value: Any) -> float | int | None:
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) else round(f, 2)


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _answer(value: Any) -> str | None:
    """A checklist / custom-field answer as text; photo lists as a count."""
    if isinstance(value, list):
        urls = [v for v in value if isinstance(v, str) and v.startswith("http")]
        others = [str(v) for v in value if v not in (None, "") and v not in urls
                  and not isinstance(v, dict)]
        dicts = [v for v in value if isinstance(v, dict)]
        files = len(urls) + sum(1 for d in dicts if any(
            isinstance(x, str) and x.startswith("http") for x in d.values()))
        parts = (["%d file%s" % (files, "" if files == 1 else "s")] if files else []) + others
        return ", ".join(parts) or None
    if isinstance(value, dict):
        return None
    if isinstance(value, bool):
        return "Yes" if value else "No"
    text = plain(value) if isinstance(value, str) and "<" in value else value
    if isinstance(text, str) and text.startswith("http"):
        return "1 file"
    return short(text, DESC_MAX)


def is_open(row: dict) -> bool:
    cur = _dict(row.get("current_job_status"))
    if row.get("is_deleted"):
        return False
    return (str(cur.get("status_type") or "").upper() not in CLOSED_TYPES
            and str(cur.get("status_name") or "").strip().lower() not in CLOSED_NAMES)


def moves_of(rec: dict) -> list[dict]:
    return sorted((e for e in _list(rec.get("job_status")) if isinstance(e, dict)),
                  key=lambda e: str(e.get("created_at") or ""))


# ------------------------------------------------------------------------------ pure parsing

def parse_job(rec: dict) -> dict:
    """The whole job record (GET /jobs/{uid}) -> what a review reads."""
    cur = _dict(rec.get("current_job_status"))
    moves = moves_of(rec)
    cust = _dict(rec.get("customer"))
    addr = _dict(rec.get("customer_address"))
    nums = _dict(cust.get("customer_contact_no"))
    phones: list[str] = []
    for v in [nums.get("mobile"), nums.get("home"), nums.get("work"), addr.get("phone_number"),
              _dict(_dict(rec.get("property")).get("property_address")).get("phone_number")]:
        p = e164(v)
        if p and p not in phones:
            phones.append(p)
    history = [{"stage": e.get("status_name"), "at": et(e.get("created_at")),
                "by": person(e.get("done_by"))} for e in moves]
    checklist: dict[str, dict[str, str]] = {}
    for e in moves:
        for q in _list(e.get("checklist")):
            if not isinstance(q, dict) or (q.get("type") or "").upper() == "HEADER":
                continue
            answer = _answer(q.get("answer"))
            question = short(q.get("question"), 90)
            if answer and question:
                checklist.setdefault(str(e.get("status_name") or "?"), {})[question] = answer
    fields: dict[str, str] = {}
    for f in _list(rec.get("custom_fields")):
        if not isinstance(f, dict) or f.get("hide_field"):
            continue
        value = _answer(f.get("value"))
        label = short(f.get("label"), 80)
        if value and label and label not in fields:
            fields[label] = value
    lines = [{"code": p.get("product_id"), "name": short(p.get("product_name"), 120),
              "qty": money(p.get("quantity")), "price": money(p.get("price")),
              "total": money(p.get("total"))}
             for p in _list(rec.get("products")) if isinstance(p, dict)]
    category = rec.get("job_category")
    board = category.get("category_name") if isinstance(category, dict) else None
    board = board or _dict(cur.get("category")).get("category_name")
    return {
        "uid": rec.get("job_uid"),
        "number": str(rec.get("work_order_number") or "") or None,
        "title": short(rec.get("job_title"), 150),
        "board": board,
        "stage": cur.get("status_name") or (moves[-1].get("status_name") if moves else None),
        "stage_type": cur.get("status_type"),
        "stage_since": history[-1]["at"] if history else None,
        "created": et(rec.get("created_at")),
        "scheduled": [et(rec.get("scheduled_start_time")), et(rec.get("scheduled_end_time"))],
        "assigned": [n for n in (person(_dict(a).get("user")) for a in
                                 _list(rec.get("assigned_to"))) if n],
        "address": ", ".join(x for x in (addr.get("street"), addr.get("city"), addr.get("state"))
                             if isinstance(x, str) and x.strip()) or None,
        "customer": {
            "uid": cust.get("customer_uid"),
            "first_name": cust.get("customer_first_name"),
            "last_name": cust.get("customer_last_name"),
            "company": cust.get("customer_company_name") or None,
            "phones": phones,
            "has_email": bool(cust.get("customer_email") or addr.get("email")),
        },
        "job_total": money(rec.get("job_total")),
        "line_items": lines,
        "stage_history": history,
        "checklist": checklist,
        "custom_fields": fields,
    }


def parse_notes(rows: list[dict]) -> list[dict]:
    out = []
    for n in rows:
        if not isinstance(n, dict) or n.get("is_deleted"):
            continue
        text = plain(n.get("note"))
        files = len(_list(n.get("attachments")))
        out.append({"at": et(n.get("created_at")), "by": person(n.get("created_by")),
                    "type": n.get("note_type"),
                    "text": short(text, TEXT_MAX) if text else
                    ("[%d file%s]" % (files, "" if files == 1 else "s") if files else
                     "[no text in the API answer]"),
                    **({"private": True} if n.get("is_private") else {})})
    out.sort(key=lambda n: n["at"] or "", reverse=True)
    return out


def proposal_number(rec: dict) -> str | None:
    no = rec.get("estimate_no")
    if no in (None, ""):
        return None
    return "%s%s" % (rec.get("prefix") or "", no)


def parse_proposal(rec: dict) -> dict:
    """A proposal: the list row or, better, the whole record (GET /estimate/{uid})."""
    options = []
    chosen = None
    for o in _list(rec.get("proposal_options")):
        if not isinstance(o, dict):
            continue
        if o.get("is_accepted"):
            chosen = o.get("option_name")
        lines = []
        for li in _list(o.get("line_items")):
            if not isinstance(li, dict):
                continue
            desc = li.get("plain_text_description") or plain(li.get("description"))
            lines.append({"code": li.get("product_id"),
                          "name": short(li.get("name") or li.get("product_name"), 120),
                          "qty": money(li.get("quantity")),
                          "price": money(li.get("unit_price") if li.get("unit_price")
                                         is not None else li.get("price")),
                          "total": money(li.get("total")),
                          "desc": short(desc, DESC_MAX)})
        opt = {"name": o.get("option_name"), "total": money(o.get("total")),
               "deposit": money(o.get("deposit"))}
        if o.get("is_accepted"):
            opt["accepted"] = True
        if o.get("is_recommended"):
            opt["recommended"] = True
        if o.get("is_hidden"):
            opt["hidden"] = True
        if "line_items" in o:
            opt["lines"] = lines
        options.append(opt)
    dep = _dict(rec.get("deposit"))
    return {
        "number": proposal_number(rec), "uid": rec.get("estimate_uid"),
        "status": rec.get("estimate_status"), "title": short(rec.get("proposal_title"), 120),
        "total": money(rec.get("total")), "created": et(rec.get("created_at")),
        "sent": et(rec.get("sent_date")), "accepted": et(rec.get("accepted_date")),
        "expires": et(rec.get("expiry_date")), "viewed": rec.get("customer_viewed"),
        "deposit": ({"total": money(dep.get("total")), "status": dep.get("status")}
                    if dep else None),
        "chosen": chosen, "options": options,
    }


def parse_invoice(rec: dict) -> dict:
    pays = []
    for p in _list(rec.get("payment_history")):
        if isinstance(p, dict):
            pays.append({"at": et(p.get("payment_date") or p.get("created_at")),
                         "amount": money(p.get("amount") or p.get("payment_amount")),
                         "method": p.get("payment_mode") or p.get("payment_method")
                         or p.get("payment_type")})
    no = rec.get("invoice_no")
    return {"number": "%s%s" % (rec.get("prefix") or "", no) if no not in (None, "") else None,
            "uid": rec.get("invoice_uid"), "status": rec.get("invoice_status"),
            "total": money(rec.get("total")), "due": money(rec.get("amount_due")),
            "paid": money(rec.get("amount_paid")), "is_paid": rec.get("is_paid"),
            "due_date": et(rec.get("due_date")), "paid_date": et(rec.get("paid_date")),
            "payments": pays}


def _since_ok(at: datetime | None, since: datetime | None) -> bool:
    return at is not None and (since is None or at >= since)


def zuper_texts(rows: list[dict], since: datetime | None = None,
                calls_too: bool = False) -> list[dict]:
    """POST /messages rows (one Connect conversation) -> timeline entries. CALL rows are
    skipped unless `calls_too` (the call search names who called; these rows do not)."""
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        kind = str(r.get("message_type") or "").upper()
        at = parse_time(r.get("created_at"))
        if not _since_ok(at, since):
            continue
        outgoing = str(r.get("direction") or "").upper() == "OUTGOING"
        if kind in ("SMS", "MMS"):
            body = r.get("message") or ("[picture]" if kind == "MMS" or r.get("media") else "")
            out.append({"_at": at, "ts": et(at), "ch": "zuper_text",
                        "dir": "out" if outgoing else "in",
                        "who": person(r.get("user")) if outgoing else None,
                        "text": short(body, TEXT_MAX),
                        "failed": True if r.get("status") in FAILED_TEXT else None})
        elif kind == "CALL" and calls_too:
            call = _dict(r.get("call"))
            out.append(_zuper_call_entry(at, call, outgoing, None, r.get("call") or {}))
    return out


def _zuper_call_entry(at, call: dict, outgoing: bool, who: str | None, raw: dict,
                      summary: str | None = None) -> dict:
    status = str(call.get("status") or "").upper()
    secs = call.get("duration") if isinstance(call.get("duration"), int) else 0
    answered = status == "COMPLETED" and secs > 0
    entry = {"_at": at, "ts": et(at), "ch": "zuper_call", "dir": "out" if outgoing else "in",
             "who": who, "secs": secs, "status": status or None, "answered": answered}
    if not outgoing and status in NOT_ANSWERED:
        entry["missed"] = True
    summary = summary or details_summary({"call_recordings": _list(raw.get("call_recordings"))})
    if summary:
        entry["summary"] = short(summary, SUMMARY_MAX)
    return entry


def zuper_calls(rows: list[dict], since: datetime | None = None,
                summaries: dict[str, str] | None = None) -> list[dict]:
    """POST /calls search rows -> timeline entries."""
    out = []
    for c in rows:
        if not isinstance(c, dict):
            continue
        at = parse_time(c.get("created_at"))
        if not _since_ok(at, since):
            continue
        outgoing = str(c.get("direction") or "").upper() == "OUTGOING"
        staff = _dict(c.get("from") if outgoing else c.get("to"))
        who = _dict(staff.get("user")).get("user_name")
        out.append(_zuper_call_entry(at, c, outgoing, who, c,
                                     (summaries or {}).get(str(c.get("call_uid")))))
    return out


def quo_calls(rows: list[dict], since: datetime | None = None,
              summaries: dict[str, str] | None = None,
              users: dict[str, str] | None = None) -> list[dict]:
    out = []
    for c in rows:
        if not isinstance(c, dict):
            continue
        at = parse_time(c.get("createdAt"))
        if not _since_ok(at, since):
            continue
        outgoing = c.get("direction") == "outgoing"
        status = str(c.get("status") or "")
        answered = bool(c.get("answeredAt")) or (status == "completed" and
                                                 (c.get("duration") or 0) > 0)
        staff = c.get("userId") if outgoing else c.get("answeredBy")
        entry = {"_at": at, "ts": et(at), "ch": "quo_call", "dir": "out" if outgoing else "in",
                 "who": (users or {}).get(str(staff or "")),
                 "secs": int(c.get("duration") or 0), "status": status or None,
                 "answered": answered}
        if not outgoing and (not answered or status in ("missed", "no-answer", "abandoned")):
            entry["missed"] = True
        if c.get("aiHandled"):
            entry["ai"] = True
        s = (summaries or {}).get(str(c.get("id")))
        if s:
            entry["summary"] = short(s, SUMMARY_MAX)
        entry["_id"] = c.get("id")
        out.append(entry)
    return out


def quo_texts(rows: list[dict], since: datetime | None = None,
              users: dict[str, str] | None = None) -> list[dict]:
    out = []
    for m in rows:
        if not isinstance(m, dict):
            continue
        at = parse_time(m.get("createdAt"))
        if not _since_ok(at, since):
            continue
        outgoing = m.get("direction") == "outgoing"
        body = m.get("text") or ("[picture]" if m.get("media") else "")
        out.append({"_at": at, "ts": et(at), "ch": "quo_text", "dir": "out" if outgoing else "in",
                    "who": (users or {}).get(str(m.get("userId") or "")) if outgoing else None,
                    "text": short(body, TEXT_MAX),
                    "failed": True if m.get("status") in FAILED_TEXT else None})
    return out


def quo_summary_text(payload: Any) -> str | None:
    data = _dict(_dict(payload).get("data"))
    if data.get("status") != "completed":
        return None
    parts = [str(s) for s in _list(data.get("summary")) if s]
    steps = [str(s) for s in _list(data.get("nextSteps")) if s]
    text = " ".join(parts)
    if steps:
        text += " Next: " + "; ".join(steps)
    return text.strip() or None


def timeline(entries: list[dict], cap: int = TIMELINE_MAX) -> list[dict]:
    """Newest first, capped; private keys (_at, _id) and empty values dropped."""
    entries = sorted(entries, key=lambda e: e.get("_at") or datetime.min.replace(tzinfo=UTC),
                     reverse=True)[:cap]
    return [{k: v for k, v in e.items() if not k.startswith("_") and v not in (None, "")}
            for e in entries]


def size(payload: Any) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str))


def fit(d: dict, max_chars: int = MAX_CHARS) -> dict:
    """Trim a dossier, least useful first, until it fits. Each step taken is listed in
    `trimmed` so Claude knows what to fetch raw if it needs it."""
    trimmed: list[str] = []
    d["trimmed"] = trimmed              # counted while trimming; removed again when empty

    def over() -> bool:
        return size(d) > max_chars

    def cut_text(entries, key, limit):
        for e in entries:
            if isinstance(e.get(key), str) and len(e[key]) > limit:
                e[key] = e[key][:limit - 1] + "…"

    steps: list[tuple[str, Callable[[], None]]] = [
        ("timeline texts to 150 chars, summaries to 250",
         lambda: (cut_text(d.get("conversations", []), "text", 150),
                  cut_text(d.get("conversations", []), "summary", 250))),
        ("proposal line descriptions to 100 chars", lambda: [
            cut_text(o.get("lines", []), "desc", 100)
            for p in d.get("proposals", []) for o in p.get("options", [])]),
        ("notes to 150 chars", lambda: cut_text(d.get("notes", []), "text", 150)),
        ("custom field values to 60 chars", lambda: d.get("job", {}).__setitem__(
            "custom_fields", {k: (v if len(v) <= 60 else v[:59] + "…")
                              for k, v in d.get("job", {}).get("custom_fields", {}).items()})),
        ("checklist answers to 80 chars", lambda: d.get("job", {}).__setitem__(
            "checklist", {s: {q: (a if len(a) <= 80 else a[:79] + "…") for q, a in qa.items()}
                          for s, qa in d.get("job", {}).get("checklist", {}).items()})),
        ("stage history to the last 15 moves", lambda: d.get("job", {}).__setitem__(
            "stage_history", d.get("job", {}).get("stage_history", [])[-15:])),
        ("proposal line descriptions dropped", lambda: [
            o.get("lines") and [li.pop("desc", None) for li in o["lines"]]
            for p in d.get("proposals", []) for o in p.get("options", [])]),
        ("timeline to 30 entries", lambda: d.__setitem__(
            "conversations", d.get("conversations", [])[:30])),
        ("notes to the newest 5", lambda: d.__setitem__("notes", d.get("notes", [])[:5])),
        ("timeline to 15 entries", lambda: d.__setitem__(
            "conversations", d.get("conversations", [])[:15])),
        ("checklist dropped except the current stage", lambda: d.get("job", {}).__setitem__(
            "checklist", {s: qa for s, qa in d.get("job", {}).get("checklist", {}).items()
                          if s == d.get("job", {}).get("stage")})),
        ("custom fields dropped", lambda: d.get("job", {}).pop("custom_fields", None)),
        ("checklist dropped", lambda: d.get("job", {}).pop("checklist", None)),
        ("timeline to 8 entries", lambda: d.__setitem__(
            "conversations", d.get("conversations", [])[:8])),
        ("proposal line items dropped", lambda: [
            o.pop("lines", None) for p in d.get("proposals", []) for o in p.get("options", [])]),
    ]
    for label, step in steps:
        if not over():
            break
        step()
        trimmed.append(label)
    if not trimmed:
        d.pop("trimmed")
    return d


# ------------------------------------------------------------------------------ fetching

class Reader:
    """Counts and makes every request a tool makes. Zuper GETs inside `owen_ai_read`."""

    def __init__(self) -> None:
        self.counts = {"zuper": 0, "connect": 0, "quo": 0}

    def get(self, path: str, params: dict | None = None) -> Any:
        self.counts["zuper"] += 1
        with zc.owen_ai_read():
            return zc.request("GET", path, params=params)

    def connect(self, method: str, path: str, body: dict | None = None) -> Any:
        self.counts["connect"] += 1
        return zc.connect_read(method, path, body=body)

    def quo(self, path: str, params: dict | None = None) -> Any:
        self.counts["quo"] += 1
        return quo.get(path, params)


_QUO_CACHE: dict[str, tuple[float, Any]] = {}


def _quo_cached(r: Reader, path: str, params: dict | None = None) -> Any:
    hit = _QUO_CACHE.get(path)
    if hit and time.monotonic() - hit[0] < _QUO_CACHE_SECONDS:
        return hit[1]
    value = r.quo(path, params)
    _QUO_CACHE[path] = (time.monotonic(), value)
    return value


def _why(exc: Exception) -> str:
    if isinstance(exc, zc.ZuperError):
        return "unavailable: " + zc.sentence(exc)
    return "unavailable: %s" % (str(exc)[:200] or type(exc).__name__)


def _is_uid(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F-]{20,}", value))


def find_job(r: Reader, job: str) -> dict:
    """A job number ('701', '#701') or job uid -> the whole job record."""
    key = str(job or "").strip().lstrip("#").strip()
    if not key:
        raise ValueError("give a job number like 701 or a job uid")
    if _is_uid(key):
        return zc.data_of(r.get("/jobs/" + key))
    rows = zc.rows_of(r.get("/jobs", {"filter.keyword": key, "count": 20, "page": 1}))
    found = [x for x in rows if str(x.get("work_order_number")) == key]
    found.sort(key=lambda x: bool(x.get("is_deleted")))
    if not found:
        raise LookupError("no Zuper job #%s" % key)
    return zc.data_of(r.get("/jobs/" + str(found[0]["job_uid"])))


def _zuper_conversations(r: Reader, phones: list[str], since: datetime,
                         flags: dict) -> list[dict]:
    entries: list[dict] = []
    try:
        convs: list[str] = []
        for p in phones:
            ten = digits(p)
            rows = zc.data_of(r.connect("POST", "/message/conversations", {
                "limit": 10, "page": 1, "filters": {"status": "", "keyword": ten}}))
            for c in _list(rows):
                cn = _dict(_dict(c).get("customer_number"))
                at = parse_time(c.get("recent_conversation_at"))
                if digits(cn.get("number")) == ten and c.get("conversation_uid") \
                        and c["conversation_uid"] not in convs and (at is None or at >= since):
                    convs.append(c["conversation_uid"])
        for uid in convs[:CONVERSATIONS_MAX]:
            rows = zc.data_of(r.connect("POST", "/messages", {
                "filters": {"conversation_uid": uid, "message_type": ["SMS", "MMS", "CALL"]},
                "limit": 50, "page": 1}))
            entries += zuper_texts(_list(rows), since)
        flags["zuper_texts"] = "ok (%d conversation%s)" % (len(convs), "" if len(convs) == 1
                                                           else "s") if convs else "none"
        if len(convs) > CONVERSATIONS_MAX:
            flags["zuper_texts"] += ", read the newest %d" % CONVERSATIONS_MAX
    except Exception as exc:                       # noqa: BLE001 — one source, said in flags
        flags["zuper_texts"] = _why(exc)
    try:
        numbers = [f for p in phones for f in ("+1" + digits(p), "1" + digits(p))]
        rows = _list(zc.data_of(r.connect("POST", "/calls", {
            "page": 1, "limit": 50, "sort": "DESC", "sortBy": "created_at",
            "filters": {"current_user": False, "number": numbers}})))
        recent = [c for c in rows if _since_ok(parse_time(_dict(c).get("created_at")), since)]
        summaries: dict[str, str] = {}
        for c in [c for c in recent if c.get("is_recorded")][:ZUPER_SUMMARIES_MAX]:
            try:
                text = details_summary(r.connect("GET", "/calls/%s/details" % c["call_uid"]))
            except zc.ZuperError:
                continue
            if text:
                summaries[str(c["call_uid"])] = text
        entries += zuper_calls(recent, since, summaries)
        flags["zuper_calls"] = "ok" if recent else "none"
    except Exception as exc:                       # noqa: BLE001
        flags["zuper_calls"] = _why(exc)
    return entries


def _quo_conversations(r: Reader, phones: list[str], since: datetime, flags: dict) -> list:
    entries: list[dict] = []
    try:
        lines = [x.get("id") for x in _list(zc.data_of(_quo_cached(r, "/phone-numbers")))
                 if isinstance(x, dict) and x.get("id")]
        users: dict[str, str] = {}
        with contextlib.suppress(quo.QuoError):
            users = {u["id"]: " ".join(x for x in (u.get("firstName"), u.get("lastName")) if x)
                     for u in _list(zc.data_of(_quo_cached(r, "/users", {"maxResults": 50})))
                     if isinstance(u, dict) and u.get("id")}
        after = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        calls: list[dict] = []
        texts: list[dict] = []
        for pn in lines:
            for p in phones:
                base = {"phoneNumberId": pn, "participants": [p], "maxResults": 100,
                        "createdAfter": after}
                calls += _list(_dict(r.quo("/calls", base)).get("data"))
                texts += _list(_dict(r.quo("/messages", base)).get("data"))
        entries_calls = quo_calls(calls, since, None, users)
        summaries: dict[str, str] = {}
        wanted = sorted((e for e in entries_calls if e.get("answered") and e.get("secs", 0) >= 20),
                        key=lambda e: e["_at"], reverse=True)[:QUO_SUMMARIES_MAX]
        for e in wanted:
            try:
                text = quo_summary_text(r.quo("/call-summaries/%s" % e["_id"]))
            except quo.QuoError:
                continue
            if text:
                summaries[str(e["_id"])] = text
        entries += quo_calls(calls, since, summaries, users) + quo_texts(texts, since, users)
        flags["quo"] = "ok" if calls or texts else "none"
    except Exception as exc:                       # noqa: BLE001
        flags["quo"] = _why(exc)
    return entries


def dossier(job: str, now: datetime | None = None, max_chars: int = MAX_CHARS) -> dict:
    """Everything one job review needs, compact. Raises only when the job itself cannot be
    read; every other source that fails is named in `sources`."""
    started = time.monotonic()
    now = now or datetime.now(UTC)
    since = now - timedelta(days=DAYS)
    r = Reader()
    rec = find_job(r, job)
    if not isinstance(rec, dict) or not rec.get("job_uid"):
        raise LookupError("Zuper answered without a job for %r" % job)
    j = parse_job(rec)
    uid = j["uid"]
    flags: dict[str, str] = {"stage_history": "ok" if j["stage_history"] else "none",
                             "checklist": "ok" if j["checklist"] else "none"}
    out: dict[str, Any] = {"tz": "America/New_York", "job": j}

    try:
        notes = parse_notes(zc.rows_of(r.get("/jobs/%s/note" % uid, {"count": 100})))
        out["notes"] = notes
        missing = sum(1 for n in notes if n["text"] == "[no text in the API answer]")
        flags["notes"] = ("ok" if notes else "none") + (
            ", %d without text in the API" % missing if missing else "")
    except Exception as exc:                       # noqa: BLE001
        flags["notes"] = _why(exc)

    try:
        rows = [x for x in zc.rows_of(r.get("/estimate", {"filter.job": uid, "count": 50,
                                                          "page": 1}))
                if not x.get("is_deleted")]
        rows.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        props = []
        for i, row in enumerate(rows):
            if i < PROPOSALS_MAX:
                with contextlib.suppress(zc.ZuperError):
                    row = zc.data_of(r.get("/estimate/" + str(row["estimate_uid"])))
            props.append(parse_proposal(row))
        out["proposals"] = props
        flags["proposals"] = "ok" if props else "none"
        if len(rows) > PROPOSALS_MAX:
            flags["proposals"] += ", options of the newest %d only" % PROPOSALS_MAX
    except Exception as exc:                       # noqa: BLE001
        flags["proposals"] = _why(exc)

    try:
        inv = [parse_invoice(x) for x in zc.rows_of(r.get("/invoice", {
            "filter.job": uid, "count": 50, "page": 1})) if not x.get("is_deleted")]
        out["invoices"] = inv
        flags["invoices"] = "ok" if inv else "none"
    except Exception as exc:                       # noqa: BLE001
        flags["invoices"] = _why(exc)

    phones = j["customer"]["phones"]
    if phones:
        entries = _zuper_conversations(r, phones, since, flags) \
            + _quo_conversations(r, phones, since, flags)
        out["conversations"] = timeline(entries)
    else:
        out["conversations"] = []
        flags["zuper_texts"] = flags["zuper_calls"] = flags["quo"] = "no customer phone"
    out["sources"] = flags
    out["requests"] = dict(r.counts, seconds=round(time.monotonic() - started, 1))
    return fit(out, max_chars)


# ------------------------------------------------------------------------------ boards

def board_uid(r: Reader, board: str) -> tuple[str, str]:
    name = (board or SIMPLE_FLOW).strip()
    if name.lower() == SIMPLE_FLOW.lower() or name == SIMPLE_FLOW_UID:
        return SIMPLE_FLOW_UID, SIMPLE_FLOW
    if _is_uid(name):
        return name, name
    cats = zc.rows_of(r.get("/jobs/category"))
    for exact in (True, False):
        for c in cats:
            cname = str(c.get("category_name") or "")
            if (cname.lower() == name.lower()) if exact else (name.lower() in cname.lower()):
                return str(c.get("category_uid")), cname
    raise LookupError("no Zuper board named %r (boards: %s)" % (
        board, ", ".join(sorted(str(c.get("category_name")) for c in cats))))


def board_row(row: dict, now: datetime) -> dict:
    """A job LIST row -> one open-job line."""
    cur = _dict(row.get("current_job_status"))
    moves = moves_of(row)
    since = parse_time(moves[-1].get("created_at")) if moves else None
    cust = _dict(row.get("customer"))
    nums = _dict(cust.get("customer_contact_no"))
    return {
        "uid": row.get("job_uid"),
        "number": str(row.get("work_order_number") or ""),
        "stage": cur.get("status_name") or (moves[-1].get("status_name") if moves else None),
        "stage_type": cur.get("status_type"),
        "days_in_stage": (now - since).days if since else None,
        "stage_since": et(since),
        "scheduled": et(row.get("scheduled_start_time")),
        "assigned": [n for n in (person(_dict(a).get("user")) for a in
                                 _list(row.get("assigned_to"))) if n],
        "first_name": short(cust.get("customer_first_name"), 40),
        "customer_uid": cust.get("customer_uid"),
        "phones": sorted({digits(v) for v in nums.values() if digits(v)}),
        "title": short(row.get("job_title"), 80),
    }


def open_jobs(board: str = SIMPLE_FLOW, now: datetime | None = None) -> list[dict]:
    """Every OPEN job on a board (uid, number without '#', stage, ...). Raises on any Zuper
    failure — never a partial list (the scheduled sweep relies on that)."""
    return _open_jobs(Reader(), board, now or datetime.now(UTC))[1]


def _open_jobs(r: Reader, board: str, now: datetime) -> tuple[str, list[dict]]:
    uid, name = board_uid(r, board)
    rows: list[dict] = []
    for page in range(1, zc.MAX_PAGES + 1):
        payload = r.get("/jobs", {"filter.category": uid, "count": 100, "page": page})
        got = zc.rows_of(payload)
        rows += got
        total = payload.get("total_pages") if isinstance(payload, dict) else None
        if not got or (isinstance(total, int) and page >= total) or \
                (len(got) < 100 and not isinstance(total, int)):
            break
    return name, [board_row(x, now) for x in rows if is_open(x)]


def latest_proposals(rows: list[dict]) -> dict[str, dict]:
    """Proposal list rows -> job uid -> the newest live proposal (number, status, chosen)."""
    out: dict[str, dict] = {}
    for p in sorted(rows, key=lambda x: str(x.get("created_at") or "")):
        if not isinstance(p, dict) or p.get("is_deleted"):
            continue
        job = p.get("job")
        juid = job.get("job_uid") if isinstance(job, dict) else job
        if not juid:
            continue
        chosen = next((o.get("option_name") for o in _list(p.get("proposal_options"))
                       if isinstance(o, dict) and o.get("is_accepted")), None)
        out[str(juid)] = {"number": proposal_number(p), "status": p.get("estimate_status"),
                          **({"chosen": chosen} if chosen else {})}
    return out


def last_contacts(zuper_convs: list[dict], quo_convs: list[dict]) -> dict[str, dict]:
    """10-digit phone -> the newest contact seen in either inbox (ts, channel, direction)."""
    out: dict[str, dict] = {}

    def take(phone, at, channel, direction):
        key = digits(phone)
        if key and at and (key not in out or at > out[key]["_at"]):
            out[key] = {"_at": at, "ts": et(at), "ch": channel,
                        **({"dir": direction} if direction else {})}

    for c in zuper_convs:
        if not isinstance(c, dict):
            continue
        rm = _dict(c.get("recent_message"))
        d = str(rm.get("direction") or "").upper()
        take(_dict(c.get("customer_number")).get("number"),
             parse_time(c.get("recent_conversation_at")), "zuper",
             "out" if d == "OUTGOING" else "in" if d == "INCOMING" else None)
    for c in quo_convs:
        if isinstance(c, dict):
            for p in _list(c.get("participants")):
                take(p, parse_time(c.get("lastActivityAt")), "quo", None)
    return out


def snapshot(board: str = SIMPLE_FLOW, now: datetime | None = None) -> dict:
    started = time.monotonic()
    now = now or datetime.now(UTC)
    r = Reader()
    name, jobs = _open_jobs(r, board, now)
    sources: dict[str, str] = {}
    props: dict[str, dict] = {}
    try:
        rows: list[dict] = []
        for page in range(1, 11):
            payload = r.get("/estimate", {"count": 100, "page": page})
            got = zc.rows_of(payload)
            rows += got
            total = payload.get("total_pages") if isinstance(payload, dict) else None
            if not got or (isinstance(total, int) and page >= total) or len(got) < 100:
                break
        props = latest_proposals(rows)
        sources["proposals"] = "ok"
    except Exception as exc:                       # noqa: BLE001
        sources["proposals"] = _why(exc)
    cutoff = now - timedelta(days=CONTACT_DAYS)
    zconvs: list[dict] = []
    try:
        for page in range(1, CONTACT_PAGES + 1):
            got = _list(zc.data_of(r.connect("POST", "/message/conversations", {
                "limit": 25, "page": page, "filters": {"status": ""}})))
            zconvs += got
            last = parse_time(_dict(got[-1]).get("recent_conversation_at")) if got else None
            if not got or (last and last < cutoff):
                break
        sources["zuper_inbox"] = "ok (newest %d conversations)" % len(zconvs)
    except Exception as exc:                       # noqa: BLE001
        sources["zuper_inbox"] = _why(exc)
    qconvs: list[dict] = []
    try:
        after = cutoff.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        for pn in [x.get("id") for x in _list(zc.data_of(_quo_cached(r, "/phone-numbers")))
                   if isinstance(x, dict)]:
            qconvs += _list(_dict(r.quo("/conversations", {
                "phoneNumbers": [pn], "updatedAfter": after, "maxResults": 100})).get("data"))
        sources["quo_inbox"] = "ok"
    except Exception as exc:                       # noqa: BLE001
        sources["quo_inbox"] = _why(exc)
    contacts = last_contacts(zconvs, qconvs)

    cols = ["number", "uid", "stage", "days_in_stage", "scheduled", "assigned", "first_name",
            "last_contact", "proposal"]
    out_rows = []
    by_stage: dict[str, int] = {}
    for j in sorted(jobs, key=lambda j: (_stage_order(j), -(j["days_in_stage"] or 0))):
        by_stage[j["stage"]] = by_stage.get(j["stage"], 0) + 1
        seen = [contacts[p] for p in j["phones"] if p in contacts]
        lc = max(seen, key=lambda c: c["_at"]) if seen else None
        p = props.get(str(j["uid"]))
        out_rows.append([
            j["number"], j["uid"], j["stage"], j["days_in_stage"], j["scheduled"],
            ", ".join(j["assigned"]) or None, j["first_name"],
            " ".join(x for x in (lc["ts"], lc["ch"], lc.get("dir")) if x) if lc else None,
            " ".join(x for x in (p["number"], p["status"], p.get("chosen")) if x) if p else None,
        ])
    return {
        "board": name, "tz": "America/New_York", "as_of": et(now), "open_jobs": len(jobs),
        "per_stage": by_stage, "columns": cols, "rows": out_rows,
        "notes": ["last_contact = newest activity in the Zuper Connect inbox (last %d days, "
                  "newest %d conversations) or the Quo inbox for the customer's number; empty "
                  "= none seen there. Quo gives no direction." % (CONTACT_DAYS, len(zconvs))],
        "sources": sources,
        "requests": dict(r.counts, seconds=round(time.monotonic() - started, 1)),
    }


_TYPE_ORDER = ["NEW", "LEAD", "SCHEDULED", "ON_MY_WAY", "ESTIMATING", "SOLD", "PRODUCTION",
               "STARTED", "ON_HOLD", "FOLLOW_UP", "INVOICING", "OTHER"]


def _stage_order(j: dict) -> tuple[int, str]:
    t = str(j.get("stage_type") or "").upper()
    return (_TYPE_ORDER.index(t) if t in _TYPE_ORDER else len(_TYPE_ORDER), j.get("stage") or "")


# ------------------------------------------------------------------------------ tool entry points

def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def job_dossier(job: str) -> str:
    try:
        return _dump(dossier(job))
    except (LookupError, ValueError) as exc:
        return "ERROR: %s" % exc
    except zc.ZuperError as exc:
        return "ERROR from Zuper: %s" % zc.sentence(exc)


def board_snapshot(board: str = SIMPLE_FLOW) -> str:
    try:
        return _dump(snapshot(board))
    except (LookupError, ValueError) as exc:
        return "ERROR: %s" % exc
    except zc.ZuperError as exc:
        return "ERROR from Zuper: %s" % zc.sentence(exc)

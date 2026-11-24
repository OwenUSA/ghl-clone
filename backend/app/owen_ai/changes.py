"""Owen AI changes Zuper when Owen says so in the chat (2026-11-21, the owner's lift).

USER REQUEST (2026-11-21, "Yes, Owen approved"): Owen AI should advise what to change in
Zuper from the customers' calls and texts, and when Owen tells it in the chat to do it, do it —
no other approval. What it may change:

  move         a job to another column ON ITS OWN BOARD — any column, closing ones included,
               with that column's questions answered from the chat (never to another board)
  schedule     the visit's start and end (the dispatch board)
  technicians  who is assigned (the dispatch board) and the job's Technician field
  job_field    one of the job's own fields
  job_address  the job's service address
  customer     the customer's names, email, phones and address
  note         a private note on the job, nobody notified
  pictures     pictures Owen dropped in this chat, ADDED to the job's gallery (never replaced,
               moved or deleted)

How "do it" is enforced — in code, not only in the prompt:

  * `propose_zuper_changes` READS Zuper (the job, its board's columns and the target column's
    questions, users and teams, the customer) and stores ONE pending change list per chat (a
    newer one replaces it). It writes nothing to Zuper. Each change carries its evidence.
  * `apply_zuper_changes` makes them only while answering a message OWEN TYPED (kind "chat") in
    the SAME chat, LATER than the message the list was proposed under, with `owen_said` = words
    from that very message — so a list can never be proposed and applied in one answer, nor by
    a sub-chat's instruction, an audit, or a customer's text. 24 hours, then it expires.
  * Then the server gate (OWEN_AI_ZUPER_CHANGES_ENABLED), the registry's owen_ai "zuper_change"
    level, and a safety read (no customer notification rule active, no customer feedback
    requests, Zuper's system texts only from (954) 420-7373) — all before any request.
  * The list is claimed pending -> applying in one UPDATE (a second "do it" applies nothing).
    Each change re-reads Zuper first and is SKIPPED when the record changed since it was
    proposed; then its requests go through `client.owen_ai_change` (only that change's writes,
    this job / customer only, no DELETE, nothing that sends); then it is READ BACK. Nothing is
    ever retried: a write whose outcome is unclear may have landed. Zuper shows these writes "by
    Owen" (the API key's user); every remark and note says Owen AI.
"""
from __future__ import annotations

import contextlib
import contextvars
import copy
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from ..models import OwenAiChangeSet, OwenAiMessage, OwenAiUpload
from ..registry import service as registry
from ..zuper import client as zc
from ..zuper import config as zuper_config
from ..zuper import mapping
from . import config, dossier, uploads
from .proposals import SpecError, current_thread

NY = ZoneInfo("America/New_York")
ACTION = "zuper_change"                 # registry action on the owen_ai entry
KINDS = zc.CHANGE_KINDS
MAX_CHANGES = 15
MAX_PICTURES = uploads.MAX_PER_MESSAGE
EXPIRE = timedelta(hours=24)
ZUPER_TIME = "%Y-%m-%d %H:%M:%S"
TIME_SLACK = timedelta(seconds=60)
TECHNICIAN_FIELD = "Technician"
NOTE_PREFIX = "Owen AI: "
REMARKS = "Moved by Owen AI after Owen said so in the Owen AI chat."
SYSTEM_NUMBER = "19544207373"           # the only sender Zuper's system texts may use
MEDIA_TYPES = frozenset({"IMAGE", "MULTI_IMAGE", "VIDEO", "FILE", "MULTI_FILE", "MULTI_MEDIA",
                         "SIGNATURE", "AUDIO"})
CHOICE_TYPES = frozenset({"RADIO", "SINGLE_ITEM", "MULTI_ITEM", "CHECKBOX", "DROPDOWN"})
CLOSING_TYPES = frozenset({"PAID", "CANCELED", "CANCELLED", "LOST"})
FIELD_META = ("group_uid", "group_name", "type", "hide_to_fe", "hide_field", "read_only")
WAITING = ("Nothing was changed in Zuper yet. Show Owen the list and wait for HIS answer: only "
           "a later message of his in this chat saying to do it lets apply_zuper_changes make it.")

COMMON_KEYS = {"kind", "job", "evidence"}
KIND_KEYS = {
    "move": {"to", "answers"},
    "schedule": {"start", "end", "visit"},
    "technicians": {"technicians"},
    "job_field": {"field", "value"},
    "job_address": {"address"},
    "customer": {"customer_uid", "first_name", "last_name", "email", "mobile", "home", "work",
                 "address"},
    "note": {"text"},
    "pictures": {"uploads"},
}


class Refused(Exception):
    """apply_zuper_changes refused before anything was claimed or sent."""


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _iso(dt):
    dt = _aware(dt)
    return dt.isoformat() if dt else None


def norm(value: Any) -> str:
    return " ".join(str("" if value is None else value).lower().split())


# ------------------------------------------------------------------- the message being answered

_ANSWERING: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "owen_ai_answering_message", default=None)


@contextlib.contextmanager
def answering_message(m: OwenAiMessage):
    """The runner wraps one answer in it: which message Claude is answering, of what kind, and
    Owen's words — the only words that can say "do it"."""
    token = _ANSWERING.set({"id": m.id, "thread_id": m.thread_id, "kind": m.kind or "chat",
                            "content": m.content or ""})
    try:
        yield
    finally:
        _ANSWERING.reset(token)


def current_message() -> dict | None:
    return _ANSWERING.get()


# ------------------------------------------------------------------------------- small readers

def _session():
    from ..db import SessionLocal
    return SessionLocal()


def _customer_of(job: dict) -> tuple[str | None, str]:
    c = job.get("customer") if isinstance(job.get("customer"), dict) else {}
    name = " ".join(x for x in (c.get("customer_first_name"), c.get("customer_last_name")) if x)
    return c.get("customer_uid"), name


def _statuses(r, category_uid: str) -> list[dict]:
    d = zc.data_of(r.get("/jobs/status/" + category_uid))
    rows = d.get("job_statuses") if isinstance(d, dict) else d
    return [s for s in rows or [] if isinstance(s, dict) and s.get("status_uid")]


def _questions(r, category_uid: str, status_uid: str) -> list[dict]:
    rows = zc.rows_of(r.get("/settings/checklist", {"category_uid": category_uid,
                                                     "job_status_uid": status_uid}))
    out = []
    for q in rows:
        if not isinstance(q, dict) or q.get("is_deleted") or not q.get("field_name"):
            continue
        out.append({"uid": q.get("checklist_uid"), "question": str(q["field_name"]).strip(),
                    "type": str(q.get("field_type") or "SINGLE_LINE").upper(),
                    "required": bool(q.get("is_required")),
                    "options": _options(q.get("field_options")),
                    "dependent_on": q.get("dependent_on") if q.get("is_dependent") else None,
                    "dependent_options": _options(q.get("dependent_options"))})
    return out


def _options(raw: Any) -> list[str]:
    out = []
    for o in raw or [] if isinstance(raw, list) else []:
        if isinstance(o, str):
            out.append(o)
        elif isinstance(o, dict):
            v = o.get("label") or o.get("value") or o.get("option") or o.get("name")
            if v:
                out.append(str(v))
    return out


def _fields(job: dict) -> list[dict]:
    return [f for f in job.get("custom_fields") or [] if isinstance(f, dict) and f.get("label")]


def _field(job: dict, label: str) -> dict | None:
    exact = [f for f in _fields(job) if f["label"] == label]
    if exact:
        return exact[0]
    loose = [f for f in _fields(job) if norm(f["label"]) == norm(label)]
    return loose[0] if len(loose) == 1 else None


def _field_value(job: dict, label: str) -> str:
    f = _field(job, label)
    return "" if f is None or f.get("value") is None else str(f.get("value"))


def _assigned(job: dict) -> list[dict]:
    out = []
    for a in job.get("assigned_to") or []:
        u = a.get("user") if isinstance(a, dict) else None
        if isinstance(u, dict) and u.get("user_uid"):
            t = a.get("team") if isinstance(a.get("team"), dict) else {}
            out.append({"user_uid": u["user_uid"], "team_uid": t.get("team_uid"),
                        "name": " ".join(x for x in (u.get("first_name"), u.get("last_name"))
                                         if x)})
    return out


def _address(a: Any) -> dict:
    a = a if isinstance(a, dict) else {}
    return {"street": a.get("street") or "", "city": a.get("city") or "",
            "state": a.get("state") or "", "zip_code": str(a.get("zip_code") or "")}


def _country(a: Any) -> str:
    """The record's own country, as Zuper wrote it ("USA" on this account, measured on #701
    2026-11-21); "US" only when it has none. Sending "US" over "USA" changed the record."""
    c = a.get("country") if isinstance(a, dict) else None
    return c.strip() if isinstance(c, str) and c.strip() else "US"


def _addr_text(a: dict) -> str:
    return ", ".join(x for x in (a.get("street"), a.get("city"),
                                 " ".join(x for x in (a.get("state"), a.get("zip_code")) if x))
                     if x) or "none"


def _last10(raw: Any) -> str | None:
    d = re.sub(r"\D", "", str(raw or ""))
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d if len(d) == 10 else None


def _phone_text(raw: Any) -> str:
    d = _last10(raw)
    return "(%s) %s-%s" % (d[:3], d[3:6], d[6:]) if d else (str(raw) if raw else "none")


def _when(start: datetime | None, end: datetime | None) -> str:
    """"Mon Nov 23, 9:00 AM to 11:00 AM"; an end on another day names its day too (a job whose
    visits span days read "Thu Nov 12, 11:00 AM to 9:30 AM" — wrong — on 2026-11-23)."""
    if not start:
        return "no visit"
    s = start.astimezone(NY)
    text = s.strftime("%a %b %d, %I:%M %p").replace(" 0", " ")
    if end:
        e = end.astimezone(NY)
        text += " to " + (e.strftime("%I:%M %p").lstrip("0") if e.date() == s.date()
                          else e.strftime("%a %b %d, %I:%M %p").replace(" 0", " "))
    return text


def _appointments(job: dict) -> list[dict]:
    """A job's visits. Zuper keeps them in `job_appointments.appointments` (seen on #777,
    2026-11-23: two visits); a job with more than one needs the visit's appointment_uid to
    move it ("appointment_uid is required when job has multiple appointments")."""
    raw = job.get("job_appointments")
    rows = raw.get("appointments") if isinstance(raw, dict) else raw
    out = []
    for a in rows or [] if isinstance(rows, list) else []:
        if isinstance(a, dict) and a.get("appointment_uid"):
            out.append({"uid": a["appointment_uid"],
                        "start": mapping.parse_time(a.get("scheduled_start_time")),
                        "end": mapping.parse_time(a.get("scheduled_end_time")),
                        "status": a.get("appointment_status")})
    return sorted(out, key=lambda a: a["start"] or datetime.max.replace(tzinfo=UTC))


def _local_text(dt: datetime | None) -> str:
    return dt.astimezone(NY).strftime("%Y-%m-%d %H:%M") if dt else "no time"


def _parse_local(value: Any, what: str, problems: list[str]) -> datetime | None:
    m = re.fullmatch(r"\s*(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})\s*", str(value or ""))
    if not m:
        problems.append("%s must be 'YYYY-MM-DD HH:MM' in Florida time (got %r)" % (what, value))
        return None
    y, mo, d, h, mi = (int(x) for x in m.groups())
    try:
        return datetime(y, mo, d, h, mi, tzinfo=NY)
    except ValueError:
        problems.append("%s is not a real date/time" % what)
        return None


# ---------------------------------------------------------------------------- validation

def _text(d: dict, key: str, problems: list[str], max_len: int, required=False) -> str | None:
    v = d.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            problems.append("%s is required" % key)
        return None
    if not isinstance(v, str):
        problems.append("%s must be text" % key)
        return None
    if len(v.strip()) > max_len:
        problems.append("%s is longer than %d characters" % (key, max_len))
        return None
    return v.strip()


def validate(raw: Any, n: int) -> dict:
    """One change as Claude sent it -> clean dict, or SpecError naming change n."""
    p: list[str] = []
    if not isinstance(raw, dict):
        raise SpecError(["change %d must be an object" % n])
    kind = raw.get("kind")
    if kind not in KINDS:
        raise SpecError(["change %d: kind must be one of %s" % (n, ", ".join(KINDS))])
    extra = set(raw) - COMMON_KEYS - KIND_KEYS[kind]
    if extra:
        p.append("unknown keys for %s: %s" % (kind, ", ".join(sorted(extra))))
    out: dict[str, Any] = {"kind": kind,
                           "evidence": _text(raw, "evidence", p, 1500, required=True)}
    job = raw.get("job")
    out["job"] = str(job).strip().lstrip("#").strip() if job not in (None, "") else None
    if kind != "customer" and not out["job"]:
        p.append("job is required (a job number like 812, or its uid)")
    if kind == "move":
        out["to"] = _text(raw, "to", p, 120, required=True)
        ans = raw.get("answers") or {}
        if not isinstance(ans, dict) or not all(
                isinstance(k, str) and isinstance(v, (str, int, float, list))
                for k, v in ans.items()):
            p.append("answers must be an object {question: answer}")
            ans = {}
        out["answers"] = {k.strip(): (", ".join(str(x) for x in v) if isinstance(v, list)
                                      else str(v)).strip() for k, v in ans.items()}
    elif kind == "schedule":
        out["start"] = _parse_local(raw.get("start"), "start", p)
        out["end"] = _parse_local(raw.get("end"), "end", p)
        out["visit"] = _parse_local(raw.get("visit"), "visit", p) if raw.get("visit") else None
        if out["start"] and out["end"]:
            if out["end"] <= out["start"]:
                p.append("the visit ends before it starts")
            elif out["end"] < _now():
                p.append("the visit is in the past")
    elif kind == "technicians":
        t = raw.get("technicians")
        if not isinstance(t, list) or not all(isinstance(x, str) and x.strip() for x in t) \
                or len(t) > 4:
            p.append("technicians must be a list of up to 4 names ([] takes everyone off)")
            t = []
        out["technicians"] = [x.strip() for x in t]
    elif kind == "job_field":
        out["field"] = _text(raw, "field", p, 120, required=True)
        v = raw.get("value")
        if not isinstance(v, (str, int, float)):
            p.append("value must be text (\"\" clears the field)")
            v = ""
        out["value"] = str(v).strip()[:2000]
    elif kind == "job_address":
        out["address"] = _address_in(raw.get("address"), p, required=True)
    elif kind == "customer":
        out["customer_uid"] = _text(raw, "customer_uid", p, 64)
        if not out["job"] and not out["customer_uid"]:
            p.append("give the job (its customer) or customer_uid")
        for k in ("first_name", "last_name"):
            out[k] = _text(raw, k, p, 80)
        email = _text(raw, "email", p, 200)
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", email):
            p.append("email %r does not look like an email address" % email)
        out["email"] = email.lower() if email else None
        for k in ("mobile", "home", "work"):
            v = raw.get(k)
            if v in (None, ""):
                out[k] = None
            elif _last10(v) is None:
                p.append("%s must be a 10-digit US number (got %r)" % (k, v))
                out[k] = None
            else:
                out[k] = _last10(v)
        out["address"] = _address_in(raw.get("address"), p) if raw.get("address") else None
        if not any(out.get(k) for k in ("first_name", "last_name", "email", "mobile", "home",
                                        "work", "address")):
            p.append("say what changes on the customer")
    elif kind == "note":
        out["text"] = _text(raw, "text", p, 4000, required=True)
    elif kind == "pictures":
        ids = raw.get("uploads")
        if not isinstance(ids, list) or not ids or len(ids) > MAX_PICTURES or not all(
                isinstance(i, int) and not isinstance(i, bool) for i in ids):
            p.append("uploads must be a list of 1-%d upload ids" % MAX_PICTURES)
            ids = []
        out["uploads"] = list(dict.fromkeys(ids))
    if p:
        raise SpecError(["change %d (%s): %s" % (n, kind, x) for x in p])
    return out


def _address_in(a: Any, p: list[str], required: bool = False) -> dict | None:
    if not isinstance(a, dict):
        if required:
            p.append("address must be {street, city, state, zip}")
        return None
    bad = set(a) - {"street", "city", "state", "zip"}
    if bad:
        p.append("unknown address keys: %s" % ", ".join(sorted(bad)))
    out = {k: str(a.get(k) or "").strip()[:200] for k in ("street", "city", "state", "zip")}
    out["state"] = (out["state"] or "FL").upper()
    if not (out["street"] and out["city"] and re.fullmatch(r"\d{5}", out["zip"])):
        p.append("an address needs street, city and a 5-digit zip")
    return {"street": out["street"], "city": out["city"], "state": out["state"],
            "zip_code": out["zip"]}


# ------------------------------------------------------------------------- prepare (reads only)

def _base(r, spec: dict) -> dict:
    try:
        job = dossier.find_job(r, spec["job"])
    except (LookupError, ValueError) as exc:
        raise SpecError([str(exc)]) from None
    if not isinstance(job, dict) or not job.get("job_uid"):
        raise SpecError(["no Zuper job %s" % spec["job"]])
    if job.get("is_deleted"):
        raise SpecError(["job #%s is deleted in Zuper" % job.get("work_order_number")])
    cust_uid, cust_name = _customer_of(job)
    return {"job": job, "job_uid": job["job_uid"],
            "job_number": str(job.get("work_order_number") or "") or None,
            "customer_uid": cust_uid, "customer_name": cust_name}


def _prep_move(r, spec: dict, b: dict) -> dict:
    job = b["job"]
    cat = job.get("job_category") if isinstance(job.get("job_category"), dict) else {}
    cur = job.get("current_job_status") if isinstance(job.get("current_job_status"), dict) else {}
    if not cat.get("category_uid"):
        raise SpecError(["Zuper does not say which board job #%s is on" % b["job_number"]])
    cols = _statuses(r, cat["category_uid"])
    target = [s for s in cols if norm(s.get("status_name")) == norm(spec["to"])]
    if len(target) != 1:
        raise SpecError(["%r is not a column of %s (job #%s's own board; it never moves to "
                         "another board). Its columns: %s" % (
                             spec["to"], cat.get("category_name"), b["job_number"],
                             ", ".join(str(s.get("status_name")) for s in cols))])
    t = target[0]
    if t["status_uid"] == cur.get("status_uid"):
        raise SpecError(["job #%s is already in %s" % (b["job_number"], t.get("status_name"))])
    if str(t.get("status_type") or "").upper() == "CLOSED":
        raise SpecError(["%s is a CLOSED-type column: Zuper then refuses every later move. "
                         "Move it in Zuper if that is really wanted." % t.get("status_name")])
    qs = _questions(r, cat["category_uid"], t["status_uid"])
    given = dict(spec["answers"])
    by_q = {norm(q["question"]): q for q in qs}
    unknown = [k for k in given if norm(k) not in by_q]
    if unknown:
        raise SpecError(["%s has no question %s. Its questions: %s" % (
            t.get("status_name"), ", ".join(repr(k) for k in unknown),
            "; ".join(_q_text(q) for q in qs) or "none")])
    answers = {by_q[norm(k)]["question"]: v for k, v in given.items() if v != ""}
    problems, checklist = [], []
    for q in qs:
        a = answers.get(q["question"])
        if q["type"] in MEDIA_TYPES:
            if q["required"] and _needed(q, qs, answers):
                problems.append("%s asks for a %s (%r): Owen AI cannot answer that — move #%s "
                                "in Zuper" % (t.get("status_name"), q["type"].lower(),
                                              q["question"], b["job_number"]))
            elif a:
                problems.append("%r takes a %s, not text" % (q["question"], q["type"].lower()))
            continue
        if a is None:
            if q["required"] and _needed(q, qs, answers):
                problems.append("required: " + _q_text(q))
            continue
        if q["type"] in CHOICE_TYPES and q["options"]:
            picked = [o for o in q["options"] for part in re.split(r"\s*,\s*", a)
                      if norm(o) == norm(part)] if q["type"] in ("MULTI_ITEM", "CHECKBOX") \
                else [o for o in q["options"] if norm(o) == norm(a)]
            if not picked:
                problems.append("%r must be one of: %s (got %r)" % (
                    q["question"], ", ".join(q["options"]), a))
                continue
            a = ", ".join(dict.fromkeys(picked))
        checklist.append({"question": q["question"], "answer": a, "type": q["type"]})
    if problems:
        raise SpecError(["moving #%s to %s: %s. Ask Owen for the answers, then propose again."
                         % (b["job_number"], t.get("status_name"), x) for x in problems])
    ttype = str(t.get("status_type") or "").upper()
    ctype = str(cur.get("status_type") or "").upper()
    warn = []
    if ttype in CLOSING_TYPES:
        warn.append("this column CLOSES the job (%s)" % ttype.lower())
    if ctype in CLOSING_TYPES:
        warn.append("it reopens a closed job")
    return {"title": "Column: %s → %s%s" % (cur.get("status_name") or "none", t["status_name"],
                                             "" if not checklist else " (answers: %s)" % "; ".join(
                                                 "%s = %s" % (c["question"], c["answer"])
                                                 for c in checklist)),
            "warning": "; ".join(warn) or None,
            "before": {"column": cur.get("status_name"), "status_uid": cur.get("status_uid"),
                       "category_uid": cat["category_uid"]},
            "after": {"column": t["status_name"], "answers": {c["question"]: c["answer"]
                                                              for c in checklist}},
            "do": {"status_uid": t["status_uid"], "status_name": t["status_name"],
                   "checklist": checklist}}


def _q_text(q: dict) -> str:
    return "%r (%s%s%s)" % (q["question"], q["type"].lower(),
                            ", required" if q["required"] else "",
                            ": " + " / ".join(q["options"]) if q["options"] else "")


def _needed(q: dict, qs: list[dict], answers: dict) -> bool:
    """A required question that only shows for some answer of another one is required only
    when that answer was given."""
    if not q["dependent_on"]:
        return True
    parent = next((x for x in qs if x["uid"] == q["dependent_on"]), None)
    if parent is None or not q["dependent_options"]:
        return True
    given = answers.get(parent["question"])
    return given is not None and norm(given) in {norm(o) for o in q["dependent_options"]}


def _prep_schedule(r, spec: dict, b: dict) -> dict:
    job = b["job"]
    s1, e1 = spec["start"].astimezone(UTC), spec["end"].astimezone(UTC)
    visits = _appointments(job)
    appt, label = None, "Visit"
    if len(visits) > 1:
        # Several visits: Owen AI must say WHICH one moves, by its current start.
        listing = "; ".join("%s (%s)" % (_local_text(v["start"]), _when(v["start"], v["end"]))
                            for v in visits)
        if spec.get("visit") is None:
            raise SpecError(["job #%s has %d visits — say which one moves with `visit` = its "
                             "current start: %s" % (b["job_number"], len(visits), listing)])
        hit = [v for v in visits if v["start"] and abs(v["start"] - spec["visit"]) <= TIME_SLACK]
        if len(hit) != 1:
            raise SpecError(["job #%s has no visit starting %s; its visits: %s" % (
                b["job_number"], _local_text(spec["visit"]), listing)])
        appt = hit[0]
        s0, e0 = appt["start"], appt["end"]
        label = "Visit %d of %d" % (visits.index(appt) + 1, len(visits))
    else:
        s0 = mapping.parse_time(job.get("scheduled_start_time"))
        e0 = mapping.parse_time(job.get("scheduled_end_time"))
    if s0 and e0 and abs(s0 - s1) <= TIME_SLACK and abs(e0 - e1) <= TIME_SLACK:
        raise SpecError(["job #%s is already booked %s" % (b["job_number"], _when(s0, e0))])
    do = {"start": s1.strftime(ZUPER_TIME), "end": e1.strftime(ZUPER_TIME)}
    if appt:
        do["appointment_uid"] = appt["uid"]
    return {"title": "%s: %s → %s" % (label, _when(s0, e0), _when(s1, e1)), "warning": None,
            "before": {"start": _iso(s0), "end": _iso(e0),
                       "appointment_uid": appt["uid"] if appt else None,
                       "others": {v["uid"]: [_iso(v["start"]), _iso(v["end"])]
                                  for v in visits if v is not appt}},
            "after": {"start": s1.isoformat(), "end": e1.isoformat()},
            "do": do}


def _users(r) -> list[dict]:
    out = []
    for u in zc.rows_of(r.get("/user/all")):
        if isinstance(u, dict) and u.get("user_uid") and u.get("is_active", True) \
                and not u.get("is_deleted"):
            out.append({"user_uid": u["user_uid"],
                        "name": " ".join(x for x in (u.get("first_name"), u.get("last_name"))
                                         if x),
                        "first": str(u.get("first_name") or "")})
    return out


def _team_of(r, user_uid: str, job: dict) -> str | None:
    for a in _assigned(job):
        if a["user_uid"] == user_uid and a["team_uid"]:
            return a["team_uid"]
    for t in zc.rows_of(r.get("/teams/summary")):
        if not isinstance(t, dict) or not t.get("team_uid") or t.get("is_active") is False:
            continue
        d = zc.data_of(r.get("/team/" + t["team_uid"]))
        members = d.get("users") if isinstance(d, dict) else None
        if any(isinstance(u, dict) and u.get("user_uid") == user_uid for u in members or []):
            return t["team_uid"]
    return None


def _prep_technicians(r, spec: dict, b: dict) -> dict:
    job = b["job"]
    users = _users(r)
    wanted = []
    for name in spec["technicians"]:
        hit = [u for u in users if norm(u["name"]) == norm(name)] or \
              [u for u in users if norm(u["first"]) == norm(name)]
        if len(hit) != 1:
            raise SpecError(["no single Zuper user called %r; users: %s" % (
                name, ", ".join(u["name"] for u in users))])
        if hit[0] not in wanted:
            wanted.append(hit[0])
    now = _assigned(job)
    now_uids = {a["user_uid"] for a in now}
    want_uids = [u["user_uid"] for u in wanted]
    add = []
    for u in wanted:
        if u["user_uid"] in now_uids:
            continue
        team = _team_of(r, u["user_uid"], job)
        if not team:
            raise SpecError(["%s is in no Zuper team, so Zuper cannot assign them" % u["name"]])
        add.append({"user_uid": u["user_uid"], "team_uid": team, "name": u["name"]})
    remove = [a for a in now if a["user_uid"] not in want_uids]
    for a in remove:
        if not a["team_uid"]:
            raise SpecError(["Zuper does not say which team %s is assigned through" % a["name"]])
    tf = _field(job, TECHNICIAN_FIELD)
    field = None
    new_value = wanted[0]["name"] if wanted else ""
    old_value = "" if tf is None else str(tf.get("value") or "")
    if tf is not None and norm(old_value) != norm(new_value):
        if tf.get("read_only") is True:
            raise SpecError(["Zuper marks the Technician field read-only on #%s" % b["job_number"]])
        field = {"label": tf["label"], "value": new_value,
                 **{k: tf[k] for k in FIELD_META if k in tf}}
    if not add and not remove and field is None:
        raise SpecError(["job #%s already has %s" % (
            b["job_number"], ", ".join(u["name"] for u in wanted) or "nobody assigned")])
    names_now = [a["name"] for a in now]
    names_after = [u["name"] for u in wanted]
    title = "Technicians: %s → %s" % (", ".join(names_now) or "nobody",
                                      ", ".join(names_after) or "nobody")
    if tf is not None:
        title += "; Technician field: %s → %s" % (old_value or "empty",
                                                  (field or {}).get("value", old_value) or "empty")
    return {"title": title, "warning": None,
            "before": {"technicians": names_now, "user_uids": sorted(now_uids),
                       "technician_field": old_value},
            "after": {"technicians": names_after, "user_uids": sorted(want_uids),
                      "technician_field": (field or {}).get("value", old_value)},
            "do": {"assign": add, "unassign": remove, "field": field}}


def _prep_job_field(r, spec: dict, b: dict) -> dict:
    job = b["job"]
    f = _field(job, spec["field"])
    if f is None:
        raise SpecError(["job #%s has no field %r. Its fields: %s" % (
            b["job_number"], spec["field"], ", ".join(x["label"] for x in _fields(job)))])
    if f.get("read_only") is True:
        raise SpecError(["Zuper marks %r read-only" % f["label"]])
    if norm(f["label"]) == norm(TECHNICIAN_FIELD):
        raise SpecError(["use a `technicians` change for the technician (it assigns them too)"])
    old = "" if f.get("value") is None else str(f.get("value"))
    if norm(old) == norm(spec["value"]):
        raise SpecError(["%r on #%s already reads %r" % (f["label"], b["job_number"], old)])
    return {"title": "Field %s: %s → %s" % (f["label"], old or "empty", spec["value"] or "empty"),
            "warning": None, "before": {"field": f["label"], "value": old},
            "after": {"field": f["label"], "value": spec["value"]},
            "do": {"field": {"label": f["label"], "value": spec["value"],
                             **{k: f[k] for k in FIELD_META if k in f}}}}


def _prep_job_address(r, spec: dict, b: dict) -> dict:
    old = _address(b["job"].get("customer_address"))
    new = spec["address"]
    if all(norm(old[k]) == norm(new[k]) for k in ("street", "city", "zip_code")):
        raise SpecError(["job #%s's address already reads %s" % (b["job_number"], _addr_text(old))])
    return {"title": "Job address: %s → %s" % (_addr_text(old), _addr_text(new)),
            "warning": "routes use the job's map position; check it moved",
            "before": {"address": old}, "after": {"address": new},
            "do": {"address": {**new,
                               "country": _country(b["job"].get("customer_address"))}}}


CUSTOMER_LABELS = {"customer_first_name": "first name", "customer_last_name": "last name",
                   "customer_email": "email"}


def _customer_now(c: dict) -> dict:
    phones = c.get("customer_contact_no") if isinstance(c.get("customer_contact_no"), dict) else {}
    return {"customer_first_name": c.get("customer_first_name") or "",
            "customer_last_name": c.get("customer_last_name") or "",
            "customer_email": c.get("customer_email") or "",
            "phones": {k: str(v) for k, v in phones.items() if isinstance(v, str) and v},
            "address": _address(c.get("customer_address"))}


def _prep_customer(r, spec: dict, b: dict | None) -> dict:
    uid = spec.get("customer_uid") or (b or {}).get("customer_uid")
    if not uid:
        raise SpecError(["Zuper does not say whose job #%s is" % (b or {}).get("job_number")])
    try:
        c = zc.data_of(r.get("/customers/" + uid))
    except zc.ZuperError as exc:
        if exc.kind == "not_found":
            raise SpecError(["no Zuper customer %s" % uid]) from None
        raise
    if not isinstance(c, dict) or c.get("is_deleted"):
        raise SpecError(["Zuper customer %s is deleted" % uid])
    now = _customer_now(c)
    body: dict[str, Any] = {}
    parts = []
    for key, src in (("customer_first_name", "first_name"), ("customer_last_name", "last_name"),
                     ("customer_email", "email")):
        v = spec.get(src)
        if v and norm(v) != norm(now[key]):
            body[key] = v
            parts.append("%s %s → %s" % (CUSTOMER_LABELS[key], now[key] or "empty", v))
    phones = dict(now["phones"])
    changed_phone = False
    for k in ("mobile", "home", "work"):
        v = spec.get(k)
        if v and _last10(phones.get(k)) != v:
            parts.append("%s %s → %s" % (k, _phone_text(phones.get(k)), _phone_text(v)))
            phones[k] = "+1" + v
            changed_phone = True
    if changed_phone:
        body["customer_contact_no"] = phones
    if spec.get("address"):
        new = spec["address"]
        if not all(norm(now["address"][k]) == norm(new[k]) for k in ("street", "city",
                                                                     "zip_code")):
            body["customer_address"] = {**new, "country": _country(c.get("customer_address"))}
            parts.append("address %s → %s" % (_addr_text(now["address"]), _addr_text(new)))
    if not body:
        raise SpecError(["customer %s already has those details" % (
            " ".join(x for x in (now["customer_first_name"], now["customer_last_name"]) if x)
            or uid)])
    name = " ".join(x for x in (now["customer_first_name"], now["customer_last_name"]) if x)
    return {"title": "Customer %s: %s" % (name or uid, "; ".join(parts)), "warning": None,
            "customer_uid": uid, "customer_name": name,
            "before": now, "after": dict(body),
            "do": {"customer": body}}


GALLERY_PAGE = 200          # the web app's gallery read, verified live 2026-11-19 (#803)


def _gallery(r, job_uid: str) -> list[dict]:
    d = zc.data_of(r.get("/attachments/group", {
        "group_by": "created_by", "sort": "ASC", "sort_by": "created_at",
        "filter.module": "JOB", "filter.module_uid": job_uid, "filter.type": "IMAGE,VIDEO",
        "page": 1, "limit": GALLERY_PAGE}))
    out = []
    for grp in d if isinstance(d, list) else []:
        for a in (grp.get("attachments") if isinstance(grp, dict) else None) or []:
            if isinstance(a, dict) and a.get("attachment_uid"):
                out.append(a)
    return out


def _prep_pictures(db: Session, r, spec: dict, b: dict, thread_id: int) -> dict:
    rows = {u.id: u for u in db.scalars(select(OwenAiUpload).where(
        OwenAiUpload.id.in_(spec["uploads"])))}
    bad = [i for i in spec["uploads"] if i not in rows or rows[i].thread_id != thread_id
           or rows[i].message_id is None]
    if bad:
        raise SpecError(["upload%s %s %s not a picture Owen sent in this chat" % (
            "" if len(bad) == 1 else "s", ", ".join(map(str, bad)),
            "is" if len(bad) == 1 else "are")])
    names = [rows[i].file_name for i in spec["uploads"]]
    count = len(_gallery(r, b["job_uid"]))
    return {"title": "Gallery: add %d picture%s (%s)" % (
        len(names), "" if len(names) == 1 else "s", ", ".join(names)),
            "warning": None, "before": {"gallery": count},
            "after": {"gallery": count + len(names)},
            "do": {"uploads": spec["uploads"], "names": names}}


def prepare(db: Session, raw_changes: Any, why: str | None, thread_id: int,
            message_id: int | None, reader=None) -> OwenAiChangeSet:
    """Validate, read Zuper, store ONE pending list for this chat. Raises SpecError or
    ZuperError. Writes nothing to Zuper."""
    if not isinstance(raw_changes, list) or not raw_changes:
        raise SpecError(["changes must be a list of 1-%d changes" % MAX_CHANGES])
    if len(raw_changes) > MAX_CHANGES:
        raise SpecError(["at most %d changes in one list" % MAX_CHANGES])
    specs, problems = [], []
    for n, raw in enumerate(raw_changes, 1):
        try:
            specs.append(validate(raw, n))
        except SpecError as exc:
            problems += exc.problems
    if problems:
        raise SpecError(problems)
    r = reader or dossier.Reader()
    jobs: dict[str, dict] = {}
    out = []
    for n, spec in enumerate(specs, 1):
        try:
            b = None
            if spec["job"]:
                b = jobs.get(spec["job"]) or _base(r, spec)
                jobs[spec["job"]] = b
            kind = spec["kind"]
            if kind == "move":
                c = _prep_move(r, spec, b)
            elif kind == "schedule":
                c = _prep_schedule(r, spec, b)
            elif kind == "technicians":
                c = _prep_technicians(r, spec, b)
            elif kind == "job_field":
                c = _prep_job_field(r, spec, b)
            elif kind == "job_address":
                c = _prep_job_address(r, spec, b)
            elif kind == "customer":
                c = _prep_customer(r, spec, b)
            elif kind == "note":
                c = {"title": "Private note: “%s”" % _cut(spec["text"], 120), "warning": None,
                     "before": {}, "after": {"text": NOTE_PREFIX + spec["text"]},
                     "do": {"text": NOTE_PREFIX + spec["text"]}}
            else:
                c = _prep_pictures(db, r, spec, b, thread_id)
        except SpecError as exc:
            problems += ["change %d (%s): %s" % (n, spec["kind"], x) for x in exc.problems]
            continue
        out.append({"n": n, "kind": spec["kind"],
                    "job_uid": (b or {}).get("job_uid"), "job_number": (b or {}).get("job_number"),
                    "customer_uid": c.pop("customer_uid", None) or (b or {}).get("customer_uid"),
                    "customer_name": c.pop("customer_name", None) or (b or {}).get(
                        "customer_name"),
                    "evidence": spec["evidence"], "status": "pending", "result": None, **c})
    if problems:
        raise SpecError(problems)
    for old in db.scalars(select(OwenAiChangeSet).where(OwenAiChangeSet.thread_id == thread_id,
                                                        OwenAiChangeSet.status == "pending")):
        old.status = "superseded"
    row = OwenAiChangeSet(thread_id=thread_id, proposed_message_id=message_id, status="pending",
                          why=(why or "").strip()[:2000] or None, changes=out,
                          created_at=_now())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _cut(s: str, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


def line(c: dict) -> str:
    who = "#%s %s" % (c.get("job_number") or "?", c.get("customer_name") or "") if c.get(
        "job_number") else (c.get("customer_name") or c.get("customer_uid") or "")
    return "%d. %s — %s%s" % (c["n"], who.strip(), c["title"],
                              " ⚠ %s" % c["warning"] if c.get("warning") else "")


def summary(row: OwenAiChangeSet) -> str:
    lines = ["Change list #%d — %d change%s, waiting for Owen's OK:" % (
        row.id, len(row.changes), "" if len(row.changes) == 1 else "s")]
    for c in row.changes:
        lines.append(line(c))
        lines.append("   because: %s" % _cut(c.get("evidence"), 300))
    return "\n".join(lines)


# ------------------------------------------------------------------------------- the tools

def tool_propose(args: dict) -> str:
    ctx = current_message()
    thread_id = current_thread()
    if ctx is not None and ctx["kind"] == "audit":
        return ("ERROR: a scheduled audit does not propose changes (nobody can answer it there). "
                "Record the problem with report_issue and put the change Zuper needs in its "
                "action; Owen asks for it in his chat.")
    if ctx is None or thread_id is None:
        return "ERROR: changes are proposed only inside an Owen AI chat."
    db = _session()
    try:
        row = prepare(db, (args or {}).get("changes"), (args or {}).get("why"), thread_id,
                      ctx["id"])
    except SpecError as exc:
        return ("ERROR: nothing was stored — fix these and call again:\n"
                + "\n".join("- " + p for p in exc.problems))
    except zc.ZuperError as exc:
        return "ERROR from Zuper: %s" % zc.sentence(exc)
    finally:
        db.close()
    gate = None if zuper_config.owen_ai_changes_enabled() \
        else zuper_config.OWEN_AI_CHANGES_OFF_SENTENCE
    return summary(row) + "\n" + WAITING + ("\nNote: %s" % gate if gate else "")


def tool_apply(args: dict) -> str:
    args = args or {}
    db = _session()
    try:
        row = apply(db, args.get("change_list"), args.get("items"), args.get("owen_said"))
    except Refused as exc:
        return "ERROR: nothing was changed in Zuper — %s" % exc
    finally:
        db.close()
    return result_text(row)


def result_text(row: OwenAiChangeSet) -> str:
    mark = {"applied": "DONE", "failed": "FAILED", "skipped": "SKIPPED", "not_asked": "NOT DONE"}
    lines = ["Change list #%d: %s." % (row.id, row.status)]
    for c in row.changes:
        lines.append("%s — %s: %s" % (line(c), mark.get(c["status"], c["status"]),
                                      c.get("result") or ""))
    return "\n".join(lines)


PROPOSE_SCHEMA = {"type": "object", "properties": {
    "changes": {"type": "array", "minItems": 1, "maxItems": MAX_CHANGES, "items": {
        "type": "object", "properties": {
            "kind": {"type": "string", "enum": list(KINDS)},
            "job": {"type": "string", "description": "job number (812) or job uid"},
            "evidence": {"type": "string", "description": "why: the customer's own words with "
                         "date and source (call / text / note), or 'Owen asked in the chat'"},
            "to": {"type": "string", "description": "move: the column name on the job's board"},
            "answers": {"type": "object", "description": "move: {question: answer} for the "
                        "column's questions (required ones must be given)"},
            "start": {"type": "string", "description": "schedule: 'YYYY-MM-DD HH:MM' Florida"},
            "end": {"type": "string", "description": "schedule: 'YYYY-MM-DD HH:MM' Florida"},
            "visit": {"type": "string", "description": "schedule, only for a job with several "
                      "visits: the CURRENT start ('YYYY-MM-DD HH:MM' Florida) of the visit that "
                      "moves — the error lists them"},
            "technicians": {"type": "array", "items": {"type": "string"},
                            "description": "technicians: who should be on the job after the "
                                           "change (the first one also goes in the Technician "
                                           "field); [] takes everyone off"},
            "field": {"type": "string", "description": "job_field: the field's exact label"},
            "value": {"type": "string", "description": "job_field: the new value"},
            "address": {"type": "object", "properties": {
                "street": {"type": "string"}, "city": {"type": "string"},
                "state": {"type": "string"}, "zip": {"type": "string"}},
                "description": "job_address / customer: the full new address"},
            "customer_uid": {"type": "string"},
            "first_name": {"type": "string"}, "last_name": {"type": "string"},
            "email": {"type": "string"}, "mobile": {"type": "string"},
            "home": {"type": "string"}, "work": {"type": "string"},
            "text": {"type": "string", "description": "note: the note (private)"},
            "uploads": {"type": "array", "items": {"type": "integer"},
                        "description": "pictures: upload ids Owen sent in this chat"}},
        "required": ["kind", "evidence"]}},
    "why": {"type": "string", "description": "one line: what this list fixes"}},
    "required": ["changes"]}

PROPOSE_DESCRIPTION = (
    "PROPOSE changes to make in Zuper, for Owen to OK in the chat: move a job to another column "
    "of its OWN board (any column; give the column's questions in `answers`), the visit time "
    "(`schedule`), who is assigned (`technicians`), one job field, the job's service address, the "
    "customer's names / email / phones / address (`customer`), a private note, or pictures Owen "
    "sent in this chat added to the job's gallery. ONE call with every change Zuper needs (it "
    "replaces any list still waiting in this chat). It reads Zuper and stores the list; it "
    "CHANGES NOTHING. Each change needs its evidence (the customer's words, date, source). On an "
    "ERROR (a column question unanswered, a name not found) ask Owen, then call again. Then show "
    "Owen the numbered list it returns and END your answer — only his next message can say do it.")

APPLY_SCHEMA = {"type": "object", "properties": {
    "change_list": {"type": "integer", "description": "the list's number (Change list #N)"},
    "items": {"type": "array", "items": {"type": "integer"},
              "description": "only these numbers of the list, when Owen OK'd only some; "
                             "omit = all"},
    "owen_said": {"type": "string", "description": "Owen's exact words from his message that "
                  "OK'd it (copied, not paraphrased)"}},
    "required": ["change_list", "owen_said"]}

APPLY_DESCRIPTION = (
    "MAKE a proposed change list in Zuper — ONLY when Owen's message you are answering clearly "
    "tells you to do it (\"do it\", \"yes\", \"go ahead\", \"hazlo\", \"do 1 and 3\"). Pass his "
    "exact words in `owen_said`, and `items` when he OK'd only some. If he asked for something "
    "DIFFERENT (another day, another column), do not apply: propose again. It refuses when Owen "
    "has not answered since the list was proposed. Each change is re-checked, made, and read "
    "back; report each DONE / FAILED / SKIPPED line to Owen exactly — never say a change was made "
    "unless it says DONE. Nothing is retried.")

SPECS = (("propose_zuper_changes", PROPOSE_DESCRIPTION, PROPOSE_SCHEMA, tool_propose),
         ("apply_zuper_changes", APPLY_DESCRIPTION, APPLY_SCHEMA, tool_apply))


# ------------------------------------------------------------------------------- apply

def safety_refusal() -> str | None:
    """Reads only. Something in Zuper that could message a customer when a job moves or
    changes: a customer notification rule switched on, customer feedback requests on, or
    Zuper's system texts going out from any number but (954) 420-7373."""
    with zc.owen_ai_read():
        cfg = zc.data_of(zc.request("GET", "/company/config"))
        cfg = cfg if isinstance(cfg, dict) else {}
        if (cfg.get("jobs") or {}).get("enable_customer_feedback"):
            return ("Zuper's customer feedback requests are ON (company settings, jobs), so "
                    "moving a job could message the customer")
        zcn = cfg.get("zuper_connect") or {}
        number = re.sub(r"\D", "", str(zcn.get("system_notification_number") or ""))
        if zcn.get("system_notification_enabled") and number != SYSTEM_NUMBER:
            return ("Zuper's system texts are ON from %s, not (954) 420-7373" % (number or
                                                                                 "no number"))
        active = []
        for page in range(1, 6):
            rows = zc.rows_of(zc.request("GET", "/customer_notification",
                                         params={"page": page, "count": 100}))
            active += [str(x.get("notification_name") or "?") for x in rows
                       if isinstance(x, dict) and x.get("is_active")]
            if len(rows) < 100:
                break
        if active:
            return ("Zuper has customer notification rules switched ON (%s), so a change could "
                    "message the customer" % ", ".join(active[:5]))
    return None


def refusal(db: Session) -> str | None:
    if not zuper_config.owen_ai_changes_enabled():
        return zuper_config.OWEN_AI_CHANGES_OFF_SENTENCE
    if not zuper_config.env_enabled():
        return zuper_config.OFF_SENTENCE
    if not zuper_config.key_set():
        return zuper_config.NO_KEY_SENTENCE
    return registry.level_refusal(db, config.KEY, ACTION)


def apply(db: Session, set_id: Any, items: Any, owen_said: Any,
          now: datetime | None = None) -> OwenAiChangeSet:
    now = now or _now()
    ctx = current_message()
    if ctx is None or ctx["kind"] != "chat":
        raise Refused("only a message Owen typed in this chat can say to do it.")
    if not isinstance(set_id, int) or isinstance(set_id, bool):
        raise Refused("change_list must be the list's number.")
    row = db.get(OwenAiChangeSet, set_id)
    if row is None or row.thread_id != ctx["thread_id"]:
        raise Refused("change list #%s is not in this chat." % set_id)
    if row.status != "pending":
        raise Refused("change list #%d is already %s." % (row.id, row.status))
    if _aware(row.created_at) + EXPIRE < now:
        row.status = "expired"
        db.commit()
        raise Refused("change list #%d is more than %d hours old; read Zuper again and propose "
                      "it again." % (row.id, EXPIRE.total_seconds() // 3600))
    if row.proposed_message_id is not None and ctx["id"] <= row.proposed_message_id:
        raise Refused("Owen has not answered since change list #%d was proposed. Show him the "
                      "list and wait for his reply." % row.id)
    said = norm(owen_said)
    if len(said) < 2 or said not in norm(ctx["content"]):
        raise Refused("owen_said must be Owen's own words, copied from the message you are "
                      "answering.")
    numbers = [c["n"] for c in row.changes]
    if items is None or items == []:
        chosen = numbers
    elif isinstance(items, list) and all(isinstance(i, int) and not isinstance(i, bool)
                                         for i in items):
        chosen = sorted(set(items))
        bad = [i for i in chosen if i not in numbers]
        if bad:
            raise Refused("change list #%d has no change %s." % (row.id, ", ".join(map(str, bad))))
    else:
        raise Refused("items must be a list of the change numbers.")
    why = refusal(db)
    if why:
        raise Refused(why)
    try:
        why = safety_refusal()
    except zc.ZuperError as exc:
        raise Refused("the safety check could not read Zuper (%s)." % zc.sentence(exc)) from None
    if why:
        raise Refused(why + ". Ask the office to check Zuper's settings first.")
    took = db.execute(update(OwenAiChangeSet).where(
        OwenAiChangeSet.id == row.id, OwenAiChangeSet.status == "pending").values(
        status="applying", applied_message_id=ctx["id"], owen_said=str(owen_said)[:2000],
        applied_at=now)).rowcount
    db.commit()
    if not took:
        db.refresh(row)
        raise Refused("change list #%d is already %s." % (row.id, row.status))
    db.refresh(row)
    changes = copy.deepcopy(row.changes)
    for c in changes:
        if c["n"] not in chosen:
            c["status"], c["result"] = "not_asked", "Owen did not OK this one."
    row.changes = changes
    flag_modified(row, "changes")
    db.commit()
    for c in changes:
        if c["n"] not in chosen:
            continue
        c["status"], c["result"] = "applying", None
        row.changes = copy.deepcopy(changes)
        flag_modified(row, "changes")
        db.commit()
        try:
            status, sentence = run_change(db, c)
        except zc.ZuperError as exc:
            status, sentence = "failed", ("Zuper did not take it (%s)%s. Check it in Zuper; "
                                          "nothing was retried." % (
                                              zc.sentence(exc),
                                              ": " + exc.detail if exc.detail else ""))
        except Exception as exc:  # noqa: BLE001 - a bug must still leave a sentence, not "applying"
            status, sentence = "failed", ("Internal error (%s) — check it in Zuper; nothing was "
                                          "retried." % type(exc).__name__)
        c["status"], c["result"], c["done_at"] = status, sentence, _now().isoformat()
        row.changes = copy.deepcopy(changes)
        flag_modified(row, "changes")
        db.commit()
    done = [c for c in changes if c["n"] in chosen]
    ok = sum(1 for c in done if c["status"] == "applied")
    row.status = "applied" if ok == len(done) else ("partial" if ok else "failed")
    db.commit()
    db.refresh(row)
    return row


def recover(db: Session) -> int:
    """A list left APPLYING by a crash or a deploy: failed, said plainly, never retried."""
    n = 0
    for row in db.scalars(select(OwenAiChangeSet).where(OwenAiChangeSet.status == "applying")):
        changes = copy.deepcopy(row.changes)
        for c in changes:
            if c.get("status") in ("pending", "applying"):
                c["status"] = "failed"
                c["result"] = ("Owen AI restarted while making it — it may or may not have "
                               "landed. Check it in Zuper; nothing was retried.")
        row.changes = changes
        flag_modified(row, "changes")
        row.status = "failed"
        n += 1
    db.commit()
    return n


# ------------------------------------------------------------------- one change, then read back

def _job(uid: str) -> dict:
    d = zc.data_of(zc.request("GET", "/jobs/" + uid))
    return d if isinstance(d, dict) else {}


def _others(job: dict, label: str | None) -> dict:
    return {f["label"]: norm(f.get("value")) for f in _fields(job) if f["label"] != label}


def run_change(db: Session, c: dict) -> tuple[str, str]:
    """(status, sentence). Re-reads first: a record changed since the proposal is SKIPPED."""
    kind, do, before = c["kind"], c["do"], c["before"]
    job_uid = c.get("job_uid")
    if kind == "move":
        with zc.owen_ai_change("move", job_uid, status_uid=do["status_uid"]):
            j = _job(job_uid)
            cur = j.get("current_job_status") or {}
            cat = j.get("job_category") or {}
            if cur.get("status_uid") != before["status_uid"] or \
                    cat.get("category_uid") != before["category_uid"]:
                return "skipped", "it is in %s now, not %s — nothing was moved." % (
                    cur.get("status_name") or "another column", before["column"])
            body: dict[str, Any] = {"status_uid": do["status_uid"], "remarks": REMARKS}
            if do["checklist"]:
                body = {"job_uid": job_uid, "status_uid": do["status_uid"],
                        "status_name": do["status_name"], "customer_signature": "",
                        "remarks": REMARKS, "checklist": do["checklist"]}
            zc.request("PUT", "/jobs/%s/status" % job_uid, body=body)
            after = _job(job_uid)
        cur = after.get("current_job_status") or {}
        if cur.get("status_uid") == do["status_uid"]:
            return "applied", "moved to %s." % do["status_name"]
        if any(h.get("status_uid") == do["status_uid"]
               or norm(h.get("status_name")) == norm(do["status_name"])
               for h in dossier.moves_of(after)[-3:]):
            return "applied", ("moved to %s; Zuper then moved it on to %s (one of its own "
                               "workflows)." % (do["status_name"], cur.get("status_name")))
        return "failed", ("Zuper answered OK but the job reads %s. Check it in Zuper; nothing "
                          "was retried." % (cur.get("status_name") or "no column"))
    if kind == "schedule":
        appt_uid = do.get("appointment_uid")

        def visit_of(job: dict) -> tuple[datetime | None, datetime | None]:
            if not appt_uid:
                return (mapping.parse_time(job.get("scheduled_start_time")),
                        mapping.parse_time(job.get("scheduled_end_time")))
            v = next((a for a in _appointments(job) if a["uid"] == appt_uid), None)
            return (v["start"], v["end"]) if v else (None, None)

        def others_of(job: dict) -> dict:
            return {v["uid"]: [_iso(v["start"]), _iso(v["end"])]
                    for v in _appointments(job) if v["uid"] != appt_uid}

        with zc.owen_ai_change("schedule", job_uid, appointment_uid=appt_uid):
            j = _job(job_uid)
            s0, e0 = visit_of(j)
            if _iso(s0) != before["start"]:
                return "skipped", "the visit is %s now, not as proposed — nothing changed." % (
                    _when(s0, e0))
            if appt_uid is None and len(_appointments(j)) > 1:
                return "skipped", ("the job has %d visits now; propose it again naming which "
                                   "one moves — nothing changed." % len(_appointments(j)))
            row = {"scheduled_start_time": do["start"], "scheduled_end_time": do["end"],
                   "type": "SCHEDULE"}
            if appt_uid:
                row["appointment_uid"] = appt_uid
            zc.request("PUT", "/jobs/%s/update" % job_uid, body={"job": [row]})
            after = _job(job_uid)
        s1, e1 = visit_of(after)
        want_s = datetime.fromisoformat(c["after"]["start"])
        want_e = datetime.fromisoformat(c["after"]["end"])
        if not (s1 and e1 and abs(s1 - want_s) <= TIME_SLACK and abs(e1 - want_e) <= TIME_SLACK):
            return "failed", ("Zuper answered OK but the visit reads %s. Check it in Zuper; "
                              "nothing was retried." % _when(s1, e1))
        moved = [u for u, t in (before.get("others") or {}).items() if others_of(after).get(u) != t]
        if moved:
            return "failed", ("the visit is booked %s, but %d other visit%s of the job changed "
                              "too — check the job in Zuper now." % (
                                  _when(s1, e1), len(moved), "" if len(moved) == 1 else "s"))
        return "applied", "booked %s." % _when(s1, e1)
    if kind == "technicians":
        field = do.get("field")
        with zc.owen_ai_change("technicians", job_uid,
                               label=field["label"] if field else None,
                               assign=tuple(a["user_uid"] for a in do["assign"]),
                               unassign=tuple(a["user_uid"] for a in do["unassign"])):
            j = _job(job_uid)
            now_uids = sorted(a["user_uid"] for a in _assigned(j))
            tf = _field_value(j, TECHNICIAN_FIELD)
            if now_uids != before["user_uids"] or norm(tf) != norm(before["technician_field"]):
                return "skipped", ("the technicians changed in Zuper since (now %s) — nothing "
                                   "changed." % (", ".join(a["name"] for a in _assigned(j))
                                                 or "nobody"))
            start_col = (j.get("current_job_status") or {}).get("status_name")
            for a in do["assign"]:
                zc.request("POST", "/jobs/assign", body={
                    "job_uid": job_uid, "type": "ASSIGN",
                    "users": [{"user_uid": a["user_uid"], "team_uid": a["team_uid"]}]})
            for a in do["unassign"]:
                zc.request("PUT", "/jobs/%s/update" % job_uid, body={"job": [{
                    "user_uid": a["user_uid"], "team_uid": a["team_uid"], "type": "UNASSIGN"}]})
            if field:
                zc.request("PATCH", "/custom_fields", body={
                    "module_name": "JOB", "module_uid": job_uid, "custom_fields": [field]})
            after = _job(job_uid)
        got = sorted(a["user_uid"] for a in _assigned(after))
        wrong = []
        if got != c["after"]["user_uids"]:
            wrong.append("assigned reads %s" % (", ".join(a["name"] for a in _assigned(after))
                                                or "nobody"))
        if field and norm(_field_value(after, TECHNICIAN_FIELD)) != norm(field["value"]):
            wrong.append("the Technician field reads %r" % _field_value(after, TECHNICIAN_FIELD))
        if wrong:
            return "failed", "Zuper answered OK but %s. Check it in Zuper; nothing was retried." % (
                "; ".join(wrong))
        moved = (after.get("current_job_status") or {}).get("status_name")
        return "applied", "%s on the job%s." % (
            ", ".join(c["after"]["technicians"]) or "nobody",
            "; Zuper then moved it to %s (its own workflow)" % moved
            if moved and moved != start_col else "")
    if kind == "job_field":
        f = do["field"]
        with zc.owen_ai_change("job_field", job_uid, label=f["label"]):
            j = _job(job_uid)
            if norm(_field_value(j, f["label"])) != norm(before["value"]):
                return "skipped", "%s reads %r now — nothing changed." % (
                    f["label"], _field_value(j, f["label"]))
            zc.request("PATCH", "/custom_fields", body={
                "module_name": "JOB", "module_uid": job_uid, "custom_fields": [f]})
            after = _job(job_uid)
        if norm(_field_value(after, f["label"])) != norm(f["value"]):
            return "failed", ("Zuper answered OK but %s reads %r. Check it in Zuper; nothing was "
                              "retried." % (f["label"], _field_value(after, f["label"])))
        lost = [k for k, v in _others(j, f["label"]).items()
                if _others(after, f["label"]).get(k) != v]
        if lost:
            return "failed", ("%s is set, but other fields changed too (%s) — check the job in "
                              "Zuper now." % (f["label"], ", ".join(lost[:6])))
        return "applied", "%s is now %r." % (f["label"], f["value"])
    if kind == "job_address":
        with zc.owen_ai_change("job_address", job_uid):
            j = _job(job_uid)
            if any(norm(_address(j.get("customer_address"))[k]) != norm(before["address"][k])
                   for k in ("street", "city", "zip_code")):
                return "skipped", "the address reads %s now — nothing changed." % _addr_text(
                    _address(j.get("customer_address")))
            old_geo = (j.get("customer_address") or {}).get("geo_cordinates")
            zc.request("PUT", "/jobs", body={"job": {"job_uid": job_uid,
                                                     "customer_address": do["address"]}})
            after = _job(job_uid)
        a = _address(after.get("customer_address"))
        if any(norm(a[k]) != norm(do["address"][k]) for k in ("street", "city", "zip_code")):
            return "failed", ("Zuper answered OK but the address reads %s. Check it in Zuper; "
                              "nothing was retried." % _addr_text(a))
        geo = (after.get("customer_address") or {}).get("geo_cordinates")
        return "applied", "the address is %s%s." % (
            _addr_text(a), "" if geo != old_geo or not old_geo else
            " — but Zuper kept the OLD map position; fix the pin in Zuper before routing")
    if kind == "customer":
        uid = c["customer_uid"]
        body = do["customer"]
        with zc.owen_ai_change("customer", customer_uid=uid):
            cust = zc.data_of(zc.request("GET", "/customers/" + uid))
            now_c = _customer_now(cust if isinstance(cust, dict) else {})
            if now_c != before:
                return "skipped", "the customer changed in Zuper since — nothing changed."
            zc.request("PUT", "/customers/" + uid, body={"customer": body})
            after_raw = zc.data_of(zc.request("GET", "/customers/" + uid))
        after = _customer_now(after_raw if isinstance(after_raw, dict) else {})
        wrong = []
        for k in ("customer_first_name", "customer_last_name", "customer_email"):
            want = body.get(k, before[k])
            if norm(after[k]) != norm(want):
                wrong.append("%s reads %r" % (CUSTOMER_LABELS[k], after[k]))
        want_phones = {k: _last10(v) for k, v in body.get("customer_contact_no",
                                                          before["phones"]).items()}
        got_phones = {k: _last10(v) for k, v in after["phones"].items()}
        if {k: v for k, v in got_phones.items() if v} != {k: v for k, v in want_phones.items()
                                                          if v}:
            wrong.append("phones read %s" % ", ".join(
                "%s %s" % (k, _phone_text(v)) for k, v in after["phones"].items()) or "none")
        want_addr = body.get("customer_address", before["address"])
        if any(norm(after["address"][k]) != norm(want_addr.get(k)) for k in
               ("street", "city", "zip_code")):
            wrong.append("the address reads %s" % _addr_text(after["address"]))
        if wrong:
            return "failed", ("Zuper answered OK but %s. Check the customer in Zuper; nothing "
                              "was retried." % "; ".join(wrong))
        return "applied", "the customer's details are updated."
    if kind == "note":
        text = do["text"]
        with zc.owen_ai_change("note", job_uid):
            zc.request("POST", "/jobs/%s/note" % job_uid, body={"note": {
                "note": text, "is_private": True, "notify_users": False}})
            notes = zc.rows_of(zc.request("GET", "/jobs/%s/note" % job_uid))
        if any(norm(text) in norm(re.sub(r"<[^>]+>", " ", str(n.get("note") or "")))
               for n in notes if isinstance(n, dict) and not n.get("is_deleted")):
            return "applied", "the private note is on the job."
        return "failed", ("Zuper answered OK but the note is not on the job. Check it in Zuper; "
                          "nothing was retried (a second try could add it twice).")
    if kind == "pictures":
        rows = [db.get(OwenAiUpload, i) for i in do["uploads"]]
        if any(r is None for r in rows):
            return "skipped", "a picture is no longer kept here — nothing was uploaded."
        files = [(r.file_name, uploads.read(r), r.content_type) for r in rows]
        new_uids: list[str] = []
        r_ = _Plain()
        with zc.owen_ai_change("pictures", job_uid, uploads=len(files)):
            before_uids = {a["attachment_uid"] for a in _gallery(r_, job_uid)}
            for name, data, mime in files:
                got = zc.data_of(zc.request(
                    "POST", "/misc/upload", files={"attachment": (name, data, mime)},
                    form={"attachment_type": "ATTACHMENT", "module": "JOB",
                          "module_uid": job_uid}))
                uid = got.get("attachment_uid") if isinstance(got, dict) else None
                if not uid:
                    return "failed", ("Zuper did not accept %s (%d of %d were added). Check the "
                                      "gallery; nothing was retried." % (
                                          name, len(new_uids), len(files)))
                new_uids.append(uid)
            after_uids = {a["attachment_uid"] for a in _gallery(r_, job_uid)}
        missing = [u for u in new_uids if u not in after_uids]
        # One page of the gallery is read; past it, "earlier ones kept" cannot be checked.
        lost = before_uids - after_uids if len(after_uids) < GALLERY_PAGE else set()
        if (missing and len(after_uids) < GALLERY_PAGE) or lost:
            return "failed", ("the gallery does not read as expected (%d new not shown, %d "
                              "earlier missing). Check it in Zuper." % (len(missing), len(lost)))
        return "applied", "%d picture%s added to the gallery (%d → %d)." % (
            len(new_uids), "" if len(new_uids) == 1 else "s", len(before_uids), len(after_uids))
    return "failed", "unknown change kind %r" % kind


class _Plain:
    """A reader for inside a change scope: plain GETs (the scope allows them)."""

    def get(self, path: str, params: dict | None = None) -> Any:
        return zc.request("GET", path, params=params)


# ------------------------------------------------------------------------------- the API's view

def shown_status(row: OwenAiChangeSet, now: datetime | None = None) -> str:
    """A pending list past its 24 hours reads expired: it can no longer be applied (the row
    itself is marked only when someone tries, 2026-11-23 — list #5 read "waiting" for days)."""
    if row.status == "pending" and row.created_at and \
            _aware(row.created_at) + EXPIRE < (now or _now()):
        return "expired"
    return row.status


def out(row: OwenAiChangeSet) -> dict:
    tpl = zuper_config.job_url_template()
    return {"id": row.id, "thread_id": row.thread_id, "status": shown_status(row),
            "why": row.why,
            "proposed_message_id": row.proposed_message_id,
            "applied_message_id": row.applied_message_id, "owen_said": row.owen_said,
            "created_at": _iso(row.created_at), "applied_at": _iso(row.applied_at),
            "expires_at": _iso(_aware(row.created_at) + EXPIRE) if row.created_at else None,
            "changes": [{"n": c["n"], "kind": c["kind"], "title": c["title"],
                         "warning": c.get("warning"), "job_number": c.get("job_number"),
                         "customer_name": c.get("customer_name"), "evidence": c.get("evidence"),
                         "status": c.get("status"), "result": c.get("result"),
                         "job_url": tpl.format(uid=c["job_uid"]) if tpl and c.get("job_uid")
                         else None}
                        for c in row.changes or []]}


def for_thread(db: Session, thread_id: int) -> list[OwenAiChangeSet]:
    return list(db.scalars(select(OwenAiChangeSet).where(OwenAiChangeSet.thread_id == thread_id)
                           .order_by(OwenAiChangeSet.id)))


def pending_notes(db: Session, thread_id: int, now: datetime | None = None) -> list[str]:
    """For the prompt: the list still waiting in this chat, so a fresh session knows it."""
    now = now or _now()
    rows = [r for r in for_thread(db, thread_id) if r.status == "pending"
            and _aware(r.created_at) + EXPIRE >= now]
    return [summary(r) for r in rows[-1:]]

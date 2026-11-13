"""The customer review — every open customer on a board, gathered in the BACKGROUND (2026-11-13).

Why: "review all ~70 pending customers with their texts, calls and AHS authorization" cannot fit
one chat answer (15 minutes; a stopped answer keeps nothing). So Owen AI's tool
`start_customer_review` starts this and answers at once; the gathering runs on a thread in the
owen_ai runner, saving as it goes:

  1. Zuper: the board's open jobs, then `dossier.dossier()` per job (job, stage history, notes,
     proposals, invoices, 60 days of Zuper Connect + Quo texts and calls with call summaries) ->
     `customers/<job number>.json`. A job already saved is skipped, so a stopped review resumes.
  2. Frontdoor (when reading is on): every job's "AHS Job ID" read in ONE browser session
     (`frontdoor.service.dispatch_many`) -> added to the same files.
  3. `review.xlsx`: Customers (one row each, with what needs attention), History (every stage
     move, note, text and call), Frontdoor.
  4. A message is queued in the chat that started it, so Owen AI reads the result in pages of
     ten (`customer_review_read`) and writes Owen the list, without anyone asking again.

Reads only, like every Owen AI tool: Zuper / Connect / Quo GETs (and Connect's search POSTs),
Frontdoor through its guard. Files live beside the reports (`config.report_dir().parent /
"reviews"`, the owen_ai_home volume); the api serves review.xlsx to the owner only.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import config, dossier

log = logging.getLogger("owen_ai")

MAX_CHARS = 60_000            # a saved dossier keeps far more than the chat's 12 000
PAGE = 10                     # customers per customer_review_read page
DIGEST_EVENTS = 6             # newest conversation entries in a page digest
STALE_SECONDS = 10 * 60       # a running review with no heartbeat for this long has stopped
KEEP_DAYS = 30
NO_CONTACT_DAYS = 3
STUCK_DAYS = 7
URL_PREFIX = "/api/owen-ai/reviews/"
ID = re.compile(r"^R\d{8}-\d{4}-[0-9a-f]{4}$")

_THREADS: dict[str, threading.Thread] = {}


def root() -> Path:
    return config.report_dir().parent / "reviews"


def _dir(review_id: str) -> Path | None:
    if not ID.match(review_id or ""):
        return None
    d = root() / review_id
    return d if d.is_dir() else None


def _now() -> datetime:
    return datetime.now(UTC)


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(path: Path, data: Any) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)


def status(review_id: str | None = None) -> dict | None:
    """The review's status.json (the newest review when no id), with `state` "stopped" for a
    running one whose heartbeat went quiet (the container restarted)."""
    if review_id is None:
        ids = sorted((p.name for p in root().glob("R*") if ID.match(p.name)), reverse=True) \
            if root().is_dir() else []
        if not ids:
            return None
        review_id = ids[0]
    d = _dir(review_id)
    s = _read(d / "status.json") if d else None
    if not isinstance(s, dict):
        return None
    beat = s.get("heartbeat")
    if s.get("state") == "running" and beat and review_id not in _THREADS and \
            (_now() - datetime.fromisoformat(beat)).total_seconds() > STALE_SECONDS:
        s["state"] = "stopped"
    return s


def _save_status(d: Path, s: dict, **changes) -> dict:
    s.update(changes, heartbeat=_now().isoformat())
    _write(d / "status.json", s)
    return s


# ------------------------------------------------------------------------------ start

def start(board: str = dossier.SIMPLE_FLOW, with_frontdoor: bool = True,
          thread_id: int | None = None, resume: str | None = None) -> dict:
    """Start (or resume) a review; returns its status at once. One review runs at a time."""
    for rid, t in list(_THREADS.items()):
        if t.is_alive():
            return {**(status(rid) or {"id": rid}), "already_running": True}
        _THREADS.pop(rid, None)
    if resume:
        d = _dir(resume)
        s = status(resume) if d else None
        if s is None:
            raise LookupError("no customer review %s" % resume)
        if s.get("state") == "done":
            return s
    else:
        root().mkdir(parents=True, exist_ok=True)
        _prune()
        rid = "R%s-%s" % (datetime.now(dossier.TZ).strftime("%Y%m%d-%H%M"), uuid.uuid4().hex[:4])
        d = root() / rid
        (d / "customers").mkdir(parents=True)
        s = {"id": rid, "board": board, "with_frontdoor": bool(with_frontdoor),
             "thread_id": thread_id, "created_at": _now().isoformat()}
    s = _save_status(d, s, state="running", phase="starting", error=None)
    t = threading.Thread(target=_run, args=(d, s), name="owen-ai-review-" + s["id"], daemon=True)
    _THREADS[s["id"]] = t
    t.start()
    return s


def _prune() -> None:
    cut = time.time() - KEEP_DAYS * 86400
    for p in root().glob("R*"):
        if ID.match(p.name) and p.is_dir() and p.stat().st_mtime < cut:
            import shutil
            shutil.rmtree(p, ignore_errors=True)


def _run(d: Path, s: dict) -> None:
    try:
        gather(d, s)
        _save_status(d, s, phase="excel")
        write_excel(d)
        _save_status(d, s, state="done", phase="done", finished_at=_now().isoformat())
        log.info("owen_ai: customer review %s done (%s customers)", s["id"], s.get("total"))
    except Exception as exc:  # a failed review is a status, never a dead runner
        log.exception("owen_ai: customer review %s failed", s["id"])
        _save_status(d, s, state="failed", error=_sentence(exc))
    finally:
        _THREADS.pop(s["id"], None)
        notify(s)


def _sentence(exc: BaseException) -> str:
    from ..frontdoor import service as frontdoor
    return frontdoor.sentence(exc) if "frontdoor" in type(exc).__module__ else \
        "%s: %s" % (type(exc).__name__, str(exc)[:300])


# ------------------------------------------------------------------------------ gathering

def ahs_job_id(d: dict) -> str | None:
    fields = ((d.get("job") or {}).get("custom_fields") or {})
    v = next((str(v) for k, v in fields.items() if k.strip().lower() == "ahs job id"), None)
    m = re.search(r"\d{6,10}", v or "")
    return m.group(0) if m else None


def gather(d: Path, s: dict, dossier_fn=None, open_jobs_fn=None, frontdoor_fn=None) -> None:
    """Steps 1 and 2. The three functions are parameters so tests run it with no network."""
    dossier_fn = dossier_fn or (lambda uid: dossier.dossier(uid, max_chars=MAX_CHARS))
    open_jobs_fn = open_jobs_fn or dossier.open_jobs
    cdir = d / "customers"
    jobs = open_jobs_fn(s["board"])
    _write(d / "jobs.json", jobs)
    _save_status(d, s, phase="zuper", total=len(jobs), done=0, failed=0)
    failed = 0
    for done, j in enumerate(jobs, 1):
        f = cdir / ("%s.json" % (j.get("number") or j["uid"]))
        if not f.is_file():
            try:
                _write(f, {"row": j, "dossier": dossier_fn(j["uid"])})
            except Exception as exc:  # noqa: BLE001 — one job's failure is recorded, not fatal
                _write(f, {"row": j, "error": _sentence(exc)})
        rec = _read(f) or {}
        failed += 1 if rec.get("error") else 0
        _save_status(d, s, done=done, failed=failed, current=j.get("number"))
    if not s.get("with_frontdoor"):
        return
    from ..frontdoor import config as fd_config
    if fd_config.refusal() or (frontdoor_fn is None and _frontdoor_refusal()):
        _save_status(d, s, frontdoor=fd_config.refusal() or _frontdoor_refusal())
        return
    want: dict[str, Path] = {}
    for f in sorted(cdir.glob("*.json")):
        rec = _read(f) or {}
        aid = ahs_job_id(rec.get("dossier") or {})
        if aid and "frontdoor" not in rec:
            want[aid] = f
    _save_status(d, s, phase="frontdoor", frontdoor_total=len(want), frontdoor_done=0)
    if not want:
        return
    counter = {"n": 0}

    def each(aid: str, result: dict) -> None:
        f = want[aid]
        rec = _read(f) or {}
        rec["frontdoor"] = result
        _write(f, rec)
        counter["n"] += 1
        _save_status(d, s, frontdoor_done=counter["n"], current=aid)

    if frontdoor_fn is None:
        from ..frontdoor import service as frontdoor
        frontdoor_fn = frontdoor.dispatch_many
    try:
        frontdoor_fn(list(want), each)
    except Exception as exc:  # noqa: BLE001 — Zuper's part is saved; the Excel still gets written
        _save_status(d, s, frontdoor="stopped: %s" % _sentence(exc))


def _frontdoor_refusal() -> str | None:
    from . import report
    return report.frontdoor_refusal()


# ------------------------------------------------------------------------------ digests

def _at(entry: dict) -> datetime | None:
    """A dossier entry's "ts" ('YYYY-MM-DD HH:MM', New York) as an aware datetime."""
    try:
        dt = datetime.fromisoformat(str(entry.get("ts") or ""))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=dossier.TZ)


def _entry_line(e: dict) -> str:
    ch = {"zuper_text": "Zuper text", "zuper_call": "Zuper call", "quo_text": "Quo text",
          "quo_call": "Quo call"}.get(e.get("ch"), e.get("ch") or "?")
    who = "customer" if e.get("dir") == "in" else (e.get("who") or "office")
    bits = ["%s %s %s" % (str(e.get("ts") or "")[:16], ch,
                          "from " + who if e.get("dir") == "in" else "by " + who)]
    if "call" in (e.get("ch") or ""):
        bits.append("answered %ss" % e.get("secs") if e.get("answered") else
                    "MISSED" if e.get("missed") else "not answered")
    text = e.get("text") or e.get("summary")
    if text:
        bits.append("“%s”" % str(text)[:220])
    if e.get("failed"):
        bits.append("FAILED")
    return " · ".join(b for b in bits if b)


def attention(rec: dict, now: datetime) -> list[str]:
    """Plain rules, so Owen sees why a customer is on the list. Owen AI adds its judgment."""
    out: list[str] = []
    if rec.get("error"):
        return ["Could not read this job: %s" % rec["error"]]
    dz = rec.get("dossier") or {}
    row = rec.get("row") or {}
    conv = [e for e in dz.get("conversations") or [] if isinstance(e, dict)]
    newest = max(conv, key=lambda e: _at(e) or datetime.min.replace(tzinfo=UTC), default=None)
    if newest is None:
        out.append("No text or call with the customer in %d days" % dossier.DAYS)
    else:
        at = _at(newest)
        days = (now - at).days if at else None
        if newest.get("dir") == "in" and (newest.get("ch", "").endswith("text") or
                                          newest.get("missed") or newest.get("answered") is False):
            out.append("The customer's %s is the last word (%s) — not answered yet" % (
                "text" if newest.get("ch", "").endswith("text") else "missed call",
                str(newest.get("ts"))[:10]))
        elif days is not None and days >= NO_CONTACT_DAYS:
            out.append("No contact for %d days" % days)
        if any(e.get("failed") for e in conv[:5]):
            out.append("A recent text FAILED to deliver")
    if (row.get("days_in_stage") or 0) >= STUCK_DAYS:
        out.append("In “%s” for %d days" % (row.get("stage"), row["days_in_stage"]))
    fd = rec.get("frontdoor") or {}
    a = (fd.get("authorization") or {}) if isinstance(fd, dict) else {}
    props = dz.get("proposals") or []
    if a.get("requested") and a.get("authorized_to_contractor") and not props:
        out.append("AHS authorized $%s on Frontdoor but the job has no proposal" %
                   _money(a["authorized_to_contractor"]))
    if fd.get("error"):
        out.append("Frontdoor: %s" % fd["error"])
    board = ((dz.get("job") or {}).get("board") or "").lower()
    if "ahs" in board and not ahs_job_id(dz):
        out.append("No AHS Job ID on the job (Frontdoor cannot be checked)")
    return out


def _money(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "?"
    return ("{:,.0f}" if v == int(v) else "{:,.2f}").format(v)


def digest(rec: dict, now: datetime) -> dict:
    """One customer, compact (~1-2k characters): what Owen AI reads to write the list."""
    row = rec.get("row") or {}
    dz = rec.get("dossier") or {}
    j = dz.get("job") or {}
    cust = j.get("customer") or {}
    conv = sorted((e for e in dz.get("conversations") or [] if isinstance(e, dict)),
                  key=lambda e: _at(e) or datetime.min.replace(tzinfo=UTC), reverse=True)
    props = dz.get("proposals") or []
    fd = rec.get("frontdoor") if isinstance(rec.get("frontdoor"), dict) else None
    a = (fd or {}).get("authorization") or {}
    notes = sorted((n for n in dz.get("notes") or [] if isinstance(n, dict)),
                   key=lambda n: str(n.get("at") or ""), reverse=True)
    return {
        "job": row.get("number"), "uid": row.get("uid"),
        "customer": " ".join(x for x in (cust.get("first_name"), cust.get("last_name")) if x)
        or row.get("first_name"),
        "stage": row.get("stage"), "days_in_stage": row.get("days_in_stage"),
        "assigned": row.get("assigned"), "scheduled": row.get("scheduled"),
        "last_stage_moves": list(j.get("stage_history") or [])[-3:],
        "last_note": ({"at": notes[0].get("at"), "by": notes[0].get("by"),
                       "text": str(notes[0].get("text") or "")[:240]} if notes else None),
        "proposal": ({"number": props[0].get("number"), "status": props[0].get("status"),
                      "chosen": props[0].get("chosen")} if props else None),
        "conversation": [_entry_line(e) for e in conv[:DIGEST_EVENTS]],
        "conversation_count_60d": len(conv),
        "frontdoor": ({"dispatch": fd.get("dispatch_id"), "status": fd.get("status"),
                       "authorized_to_us": a.get("authorized_to_contractor"),
                       "customer_owes": a.get("customer_owes"),
                       "authorized_at": a.get("authorized_at"),
                       "requested": a.get("requested"), "error": fd.get("error")}
                      if fd else None),
        "attention": attention(rec, now),
        **({"error": rec["error"]} if rec.get("error") else {}),
    }


def customers(review_id: str) -> list[dict]:
    d = _dir(review_id)
    if d is None:
        raise LookupError("no customer review %s" % review_id)
    order = {str(j.get("number") or j.get("uid")): i
             for i, j in enumerate(_read(d / "jobs.json") or [])}
    files = sorted((d / "customers").glob("*.json"),
                   key=lambda p: order.get(p.stem, 10**6))
    return [r for r in (_read(f) for f in files) if isinstance(r, dict)]


def read(review_id: str | None = None, start_at: int = 0, count: int = PAGE,
         only_attention: bool = False) -> dict:
    s = status(review_id)
    if s is None:
        raise LookupError("no customer review %s" % (review_id or "yet"))
    now = _now()
    digests = [digest(r, now) for r in customers(s["id"])]
    if only_attention:
        digests = [x for x in digests if x["attention"]]
    count = max(1, min(int(count or PAGE), 20))
    start_at = max(0, int(start_at or 0))
    page = digests[start_at:start_at + count]
    nxt = start_at + count if start_at + count < len(digests) else None
    return {"review": _public(s), "customers_total": len(digests), "from": start_at,
            "customers": page, "next_start": nxt,
            "excel": URL_PREFIX + s["id"] + "/review.xlsx" if s.get("state") == "done" else None}


def _public(s: dict) -> dict:
    keep = ("id", "board", "state", "phase", "total", "done", "failed", "frontdoor",
            "frontdoor_total", "frontdoor_done", "created_at", "finished_at", "error",
            "with_frontdoor")
    return {k: s[k] for k in keep if s.get(k) is not None}


# ------------------------------------------------------------------------------ Excel

def write_excel(d: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    now = _now()
    recs = customers(d.name)
    wb = Workbook()
    ws = wb.active
    ws.title = "Customers"
    head = ["Job #", "Customer", "Stage", "Days in stage", "Assigned", "Visit", "Last contact",
            "Last contact channel", "Texts+calls (60 d)", "Proposal", "AHS dispatch",
            "Frontdoor status", "AHS authorized to us", "Customer owes (AHS)", "Needs attention"]
    ws.append(head)
    hist = wb.create_sheet("History")
    hist.append(["Job #", "Customer", "When (New York)", "Source", "Direction", "Who",
                 "What"])
    fdws = wb.create_sheet("Frontdoor")
    fdws.append(["Job #", "AHS dispatch", "Status", "Invoice status", "Authorized to us",
                 "Customer owes", "AHS total", "Authorized at", "Authorized by", "AUTHO #",
                 "Error"])
    for rec in recs:
        g = digest(rec, now)
        dz = rec.get("dossier") or {}
        conv = sorted((e for e in dz.get("conversations") or [] if isinstance(e, dict)),
                      key=lambda e: _at(e) or datetime.min.replace(tzinfo=UTC), reverse=True)
        last = conv[0] if conv else {}
        fd = g["frontdoor"] or {}
        ws.append([g["job"], g["customer"], g["stage"], g["days_in_stage"],
                   ", ".join(g["assigned"] or []), g["scheduled"],
                   str(last.get("ts") or "")[:16], last.get("ch"),
                   g["conversation_count_60d"],
                   " ".join(str(x) for x in (g["proposal"] or {}).values() if x),
                   fd.get("dispatch"), fd.get("status"), fd.get("authorized_to_us"),
                   fd.get("customer_owes"), "\n".join(g["attention"])])
        j = dz.get("job") or {}
        for h in j.get("stage_history") or []:
            hist.append([g["job"], g["customer"], str(h.get("at") or "")[:16],
                         "Zuper stage", None, h.get("by"), "Moved to %s" % h.get("stage")])
        for n in dz.get("notes") or []:
            if isinstance(n, dict):
                hist.append([g["job"], g["customer"], str(n.get("at") or "")[:16],
                             "Zuper note", None, n.get("by"), n.get("text")])
        for e in conv:
            hist.append([g["job"], g["customer"], str(e.get("ts") or "")[:16],
                         e.get("ch"), e.get("dir"), e.get("who"), _entry_line(e)])
        raw = rec.get("frontdoor") if isinstance(rec.get("frontdoor"), dict) else None
        if raw:
            a = raw.get("authorization") or {}
            fdws.append([g["job"], raw.get("dispatch_id"), raw.get("status"),
                         raw.get("invoice_status"), a.get("authorized_to_contractor"),
                         a.get("customer_owes"), a.get("total"), a.get("authorized_at"),
                         a.get("authorized_by"), ", ".join(raw.get("autho_numbers") or []),
                         raw.get("error")])
            for h in raw.get("history") or []:
                hist.append([g["job"], g["customer"], str(h.get("at") or "")[:16].replace(
                    "T", " "), "Frontdoor", None, h.get("by"), h.get("update")])
    for sheet, widths in ((ws, [8, 24, 26, 8, 18, 18, 17, 12, 9, 22, 12, 30, 12, 12, 60]),
                          (hist, [8, 24, 17, 12, 9, 22, 100]),
                          (fdws, [8, 12, 34, 30, 12, 12, 12, 22, 16, 14, 30])):
        for i, w in enumerate(widths):
            sheet.column_dimensions[chr(65 + i)].width = w
        for c in sheet[1]:
            c.font = Font(bold=True)
        sheet.freeze_panes = "A2"
    for row in ws.iter_rows(min_row=2):
        row[-1].alignment = Alignment(wrap_text=True, vertical="top")
    out = d / "review.xlsx"
    wb.save(out)
    return out


def file_path(review_id: str, name: str) -> Path | None:
    d = _dir(review_id)
    if d is None or name != "review.xlsx":
        return None
    p = (d / name).resolve()
    return p if p.is_file() and p.parent == d.resolve() else None


# ------------------------------------------------------------------------------ the follow-up

FOLLOW_UP = ("[Automatic — the customer review {id} you started has {how}.] Read it with "
             "customer_review_read (review_id {id}), page by page until next_start is null, and "
             "write Owen the list: for each customer that needs attention, one line with job #, "
             "name, stage, the last contact and what to do next, grouped by urgency; then one "
             "line on how many need nothing. Give him the Excel link: {excel}.")


def notify(s: dict) -> None:
    """Queue a message in the chat that started the review, so Owen AI writes the list."""
    if not s.get("thread_id"):
        return
    try:
        from ..db import SessionLocal
        from ..models import OwenAiMessage, OwenAiThread
        with SessionLocal() as db:
            if db.get(OwenAiThread, s["thread_id"]) is None:
                return
            how = "finished" if s.get("state") == "done" else \
                "stopped (%s); read what it gathered" % (s.get("error") or s.get("state"))
            db.add(OwenAiMessage(thread_id=s["thread_id"], role="user", kind="review",
                                 status="queued", content=FOLLOW_UP.format(
                                     id=s["id"], how=how,
                                     excel=URL_PREFIX + s["id"] + "/review.xlsx")))
            db.commit()
    except Exception:  # the review itself is saved; only the nudge is lost
        log.exception("owen_ai: could not queue the follow-up for review %s", s.get("id"))


# ------------------------------------------------------------------------------ tool text

def started_text(s: dict) -> str:
    if s.get("already_running"):
        return ("A customer review is already running (%s: %s of %s customers). It will post "
                "the list in this chat when it finishes." % (s.get("id"), s.get("done", 0),
                                                              s.get("total", "?")))
    return json.dumps({"started": _public(s), "note": (
        "Gathering runs in the background (about 10-20 seconds per customer, plus about 10 "
        "seconds per AHS dispatch on Frontdoor). When it finishes, a message arrives in this "
        "chat and you write the list then. Tell Owen it started and roughly how long; do not "
        "wait for it.")})


def eta(s: dict) -> str | None:
    """A rough 'about N minutes left' from the pace so far."""
    try:
        started = datetime.fromisoformat(s["created_at"])
        done, total = int(s.get("done") or 0), int(s.get("total") or 0)
    except (KeyError, ValueError, TypeError):
        return None
    if not done or not total or s.get("phase") != "zuper":
        return None
    per = (_now() - started) / done
    left = per * (total - done) + timedelta(seconds=10 * total // 2)
    return "about %d minutes" % max(1, round(left.total_seconds() / 60))

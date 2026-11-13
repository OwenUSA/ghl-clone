"""The tools Owen AI's Claude may call — Phase 1: reads only (2026-11-02).

Each tool is a plain sync function (`job_dossier`, `board_snapshot` — compact answers built in
`dossier.py`, the ones Claude should reach for first; `job_photos` in `photos.py`, which saves a
job's pictures for Claude's Read tool — and the raw `zuper_get`, `zuper_connect`,
`quo_get`, whose answers lose their null / empty values before any cut) returning text, so
tests call them directly; `server()` wraps them as an in-process MCP server for the Claude Agent
SDK (imported lazily — the API container never needs the SDK). Claude sees them as
`mcp__owen__<name>`.

Nothing here writes to Zuper or contacts anyone (alerts and proposal drafts go to the CRM's
database — a draft reaches Zuper only through the owner's Approve, `proposals.approve`;
`make_inspection_report` saves an HTML/PDF draft in the reports folder for Owen): Zuper goes
through `client.owen_ai_read()` (GET only, denylist applies),
Zuper Connect through `client.connect_read` (its five reads), Quo through `quo.get` (GET only),
Frontdoor (AHS's contractor portal, 2026-11-12) through `app.frontdoor` (a guarded browser that
only reads; off unless FRONTDOOR_READ_ENABLED).
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any

from ..frontdoor import service as frontdoor
from ..zuper import client as zc
from . import (
    alerts,
    companycam_photos,
    config,
    dossier,
    photos,
    pictures,
    proposals,
    quo,
    report,
    report_attach,
    review,
    saved_reports,
)

SERVER_NAME = "owen"


def compact(value: Any, depth: int = 0) -> Any:
    """Drop null and empty values ("", [], {}) recursively; 0 and false stay. At the top level
    only nulls go, so an empty answer still reads as one ({"data": []})."""
    def empty(v: Any, keep_containers: bool = False) -> bool:
        if v is None or v == "":
            return True
        return not keep_containers and isinstance(v, (list, dict)) and not v

    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            v = compact(v, depth + 1)
            if not empty(v, keep_containers=depth == 0):
                out[k] = v
        return out
    if isinstance(value, list):
        return [x for x in (compact(v, depth + 1) for v in value) if not empty(x)]
    return value


def _text(payload: Any) -> str:
    out = json.dumps(compact(payload), ensure_ascii=False, default=str)
    if len(out) > config.TOOL_RESULT_MAX_CHARS:
        cut = config.TOOL_RESULT_MAX_CHARS
        return (out[:cut] + "\n...[cut: the answer was %d characters; ask for fewer rows "
                "(count/limit) or a single record]" % len(out))
    return out


def zuper_get(path: str, params: dict | None = None) -> str:
    try:
        with zc.owen_ai_read():
            return _text(zc.request("GET", path, params=params or None))
    except zc.ZuperError as exc:
        detail = " (%s)" % exc.detail if exc.kind == "not_found" and exc.detail else ""
        return "ERROR from Zuper: %s%s" % (zc.sentence(exc), detail)


CONNECT = {
    # what: (method, path template, needs id)
    "calls": ("POST", "/calls", False),
    "call": ("GET", "/calls/{id}/details", True),
    "conversations": ("POST", "/message/conversations", False),
    "conversation": ("GET", "/message/conversation/{id}", True),
    "messages": ("POST", "/messages", False),
}


def zuper_connect(what: str, id: str | None = None, body: dict | None = None) -> str:
    if what not in CONNECT:
        return "ERROR: `what` must be one of %s" % ", ".join(CONNECT)
    method, template, needs_id = CONNECT[what]
    if needs_id and not id:
        return "ERROR: `%s` needs `id`" % what
    try:
        return _text(zc.connect_read(method, template.format(id=id), body=body if method ==
                                     "POST" else None))
    except zc.ZuperError as exc:
        return "ERROR from Zuper Connect: %s" % zc.sentence(exc)


def quo_get(path: str, params: dict | None = None) -> str:
    try:
        return _text(quo.get(path, params))
    except quo.QuoError as exc:
        return "ERROR from Quo: %s" % exc


def _since(value) -> date | None:
    if not value:
        return None
    return date.fromisoformat(str(value)[:10])


def frontdoor_read(what: str, dispatch: str | None = None, since: str | None = None,
                   fresh: bool = False) -> str:
    """AHS's contractor portal, read-only (app/frontdoor). Off unless FRONTDOOR_READ_ENABLED and
    the registry lets Owen AI read it."""
    why = report.frontdoor_refusal()
    if why:
        return "ERROR: %s" % why
    try:
        if what == "dispatch":
            return _text(frontdoor.dispatch(dispatch, fresh=fresh))
        if what == "dispatches":
            return _text(frontdoor.dispatches(_since(since), fresh=fresh))
        if what == "invoices":
            return _text(frontdoor.invoices(_since(since), fresh=fresh))
        return "ERROR: unknown Frontdoor read %r" % what
    except Exception as exc:  # noqa: BLE001 — a sentence, never a page's call log
        return "ERROR from Frontdoor: %s" % frontdoor.sentence(exc)


def start_customer_review(board: str | None = None, with_frontdoor: bool = True,
                          resume: str | None = None) -> str:
    """Starts the background customer review (review.py) and answers at once."""
    try:
        s = review.start(board or dossier.SIMPLE_FLOW, with_frontdoor,
                         proposals.CURRENT_THREAD, resume)
    except Exception as exc:  # noqa: BLE001
        return "ERROR: could not start the customer review: %s" % exc
    return review.started_text(s)


def customer_review_read(review_id: str | None = None, start: int = 0, count: int = 10,
                         only_attention: bool = False) -> str:
    try:
        out = review.read(review_id or None, start, count, only_attention)
    except LookupError as exc:
        return "ERROR: %s" % exc
    s = out["review"]
    if s.get("state") == "running":
        left = review.eta(s)
        out["note"] = "Still gathering (%s); what is shown is what is saved so far.%s" % (
            s.get("phase"), " Done in %s." % left if left else "")
    return _text(out)


# ------------------------------------------------------------------------- the MCP server

SPECS = (
    ("job_dossier",
     ("USE THIS FIRST for any question about one job (review, status, what was quoted, who "
      "talked to the customer). `job` = job number (701 or #701) or job uid. One call returns "
      "compact JSON: the job (board, stage + since, full stage history with who moved it, "
      "schedule, assignees, address, customer + phones, filled custom fields, filled "
      "checklist answers per stage, line items), its notes, every proposal with each option's "
      "total, deposit and line items (code, short description) and the chosen option, invoices "
      "and payments, and ONE timeline (newest first, last 60 days) of Zuper Connect texts and "
      "calls and Quo calls and texts with that customer's numbers, with call summaries. "
      "Times are America/New_York. `sources` says which part was unavailable; `trimmed` lists "
      "what was shortened to fit. Use zuper_get / quo_get only for something this lacks."),
     {"type": "object", "properties": {"job": {"type": "string"}}, "required": ["job"]},
     lambda a: dossier.job_dossier(str(a["job"]))),
    ("board_snapshot",
     ("USE THIS FIRST for questions about a whole board (what is open, what is stuck, who has "
      "not been contacted). `board` = board name, default \"AHS Simple Flow\". Returns every "
      "OPEN job as a table (columns + rows): number, uid, stage, days in stage, scheduled "
      "start, assigned, customer first name, last customer contact (Zuper Connect / Quo inbox, "
      "last 30 days), newest proposal (DTR-N status chosen option), plus a count per stage. "
      "Then call job_dossier on the jobs that need a closer look."),
     {"type": "object", "properties": {"board": {"type": "string"}}},
     lambda a: dossier.board_snapshot(str(a.get("board") or dossier.SIMPLE_FLOW))),
    ("job_photos",
     ("LOOK at one job's pictures (read-only) — when reviewing or drafting a proposal, or "
      "checking what the technician found or did. `job` = job number or uid. `which` = "
      "\"inspection\" (default: photos up to the first proposal, nearest the inspection, "
      "leak / failure-point / roof albums first), \"repair\" (after the proposal was "
      "accepted) or \"all\"; `max_photos` (default 24, at most 40); `since` = a date "
      "(YYYY-MM-DD) to ignore older photos. Gathers notes, albums and checklist photo answers, "
      "saves each picked picture as a 1024 px JPEG and returns JSON: counts and per photo its "
      "absolute local `path`, time (America/New_York), source, album, who and the note text. "
      "THEN use the Read tool on each `path` to see the image — the JSON alone does not show "
      "the pictures. A second call reuses the files already saved."),
     {"type": "object", "properties": {"job": {"type": "string"},
                                       "max_photos": {"type": "integer"},
                                       "since": {"type": "string"},
                                       "which": {"type": "string",
                                                 "enum": list(photos.WHICH)}},
      "required": ["job"]},
     lambda a: photos.job_photos(str(a["job"]), a.get("max_photos") or photos.MAX_PHOTOS,
                                 a.get("since"), a.get("which") or "inspection")),
    ("zuper_get",
     ("GET any Zuper REST path (read-only). `path` like /jobs/{uid}, /jobs?filter.keyword=701, "
     "/estimate?count=20, /invoice_estimate/proposal_template?count=50, /jobs/category. "
     "`params` = query string object. Writes are impossible. "
     "See knowledge/zuper/api-reference.md."),
     {"type": "object", "properties": {"path": {"type": "string"},
                                       "params": {"type": "object"}},
      "required": ["path"]},
     lambda a: zuper_get(a["path"], a.get("params"))),
    ("zuper_connect",
     ("Read Zuper Connect (Zuper's phone/text inbox). what = calls | call | conversations | "
     "conversation | messages. `id` for call/conversation; `body` = the search filters for the "
     "POST searches (see knowledge/zuper/api-reference.md, Zuper Connect). Read-only."),
     {"type": "object", "properties": {"what": {"type": "string"}, "id": {"type": "string"},
                                       "body": {"type": "object"}},
      "required": ["what"]},
     lambda a: zuper_connect(a["what"], a.get("id"), a.get("body"))),
    ("quo_get",
     ("GET the Quo (OpenPhone) API, read-only. `path` like /phone-numbers, /calls, "
     "/call-transcripts/{callId}, /call-summaries/{callId}, /messages, /conversations, /contacts. "
     "/calls and /messages need params phoneNumberId and participants (list of E.164 numbers). "
     "See knowledge/quo/api-reference.md."),
     {"type": "object", "properties": {"path": {"type": "string"},
                                       "params": {"type": "object"}},
      "required": ["path"]},
     lambda a: quo_get(a["path"], a.get("params"))),
    ("start_customer_review",
     ("USE THIS for any question about MANY customers at once (\"all pending customers\", "
      "\"every job on the board with its texts, calls and AHS authorization\"). Never call "
      "job_dossier or frontdoor_dispatch in a loop for that — it cannot finish in one answer. "
      "Starts a BACKGROUND review of every open job on `board` (default \"AHS Simple Flow\"): "
      "per job the job_dossier data (stage history, notes, proposals, invoices, 60 days of Zuper "
      "Connect + Quo texts and calls with call summaries) and, with `with_frontdoor` (default "
      "true), the Frontdoor dispatch and authorization; then an Excel. Answers at once. When it "
      "finishes a message arrives in THIS chat and you write the list then. `resume` = the id "
      "of a stopped review to continue it."),
     {"type": "object", "properties": {"board": {"type": "string"},
                                       "with_frontdoor": {"type": "boolean"},
                                       "resume": {"type": "string"}}},
     lambda a: start_customer_review(a.get("board"), a.get("with_frontdoor", True),
                                     a.get("resume"))),
    ("customer_review_read",
     ("Read a customer review (start_customer_review) in pages: `start` (0, then next_start), "
      "`count` (default 10, at most 20), `only_attention` = only customers with something to "
      "address. Each customer: job, name, stage + days, last stage moves, last note, proposal, "
      "the newest texts/calls (with call summaries), Frontdoor (authorized to us, customer "
      "owes), and `attention` (rule-based reasons). Also the review's state and the Excel link "
      "(give Owen the link). No `review_id` = the newest review."),
     {"type": "object", "properties": {"review_id": {"type": "string"},
                                       "start": {"type": "integer"},
                                       "count": {"type": "integer"},
                                       "only_attention": {"type": "boolean"}}},
     lambda a: customer_review_read(a.get("review_id"), a.get("start") or 0,
                                    a.get("count") or 10, bool(a.get("only_attention")))),
    ("frontdoor_dispatch",
     ("READ one AHS dispatch in Frontdoor, AHS's contractor portal (read-only; ~30 s, cached 15 "
      "min, `fresh`: true re-reads). `dispatch` = the AHS dispatch number = the Zuper job's "
      "\"AHS Job ID\" custom field (job_dossier shows it). Returns the dispatch status, invoice "
      "status, items, plan dates, `authorization` (authorized_to_contractor = what AHS pays us, "
      "customer_owes = the customer's share on AHS's own estimate, total, authorized_at, "
      "authorized_by; requested=false = no authorization asked yet), AUTHO numbers and NCC / "
      "Net Total amounts found in AHS's notes, and the history (update, who, when; no note "
      "text). No customer name, phone or address — use job_dossier for those."),
     {"type": "object", "properties": {"dispatch": {"type": "string"},
                                       "fresh": {"type": "boolean"}},
      "required": ["dispatch"]},
     lambda a: frontdoor_read("dispatch", dispatch=str(a["dispatch"]),
                              fresh=bool(a.get("fresh")))),
    ("frontdoor_dispatches",
     ("READ the Frontdoor dispatch list (read-only): every AHS dispatch dated on or after "
      "`since` (YYYY-MM-DD, default 30 days ago), with date, brand, items, status group "
      "(New / In Progress / Completed / Cancelled) and status. Then frontdoor_dispatch for the "
      "money on one of them."),
     {"type": "object", "properties": {"since": {"type": "string"},
                                       "fresh": {"type": "boolean"}}},
     lambda a: frontdoor_read("dispatches", since=a.get("since"), fresh=bool(a.get("fresh")))),
    ("frontdoor_invoices",
     ("READ the Frontdoor Invoices page (read-only): the Ready To Invoice, Draft Invoices and "
      "Submitted Invoices tabs, per row the dispatch number, dates, invoice id, status, total "
      "and paid total. `since` (YYYY-MM-DD) drops older rows."),
     {"type": "object", "properties": {"since": {"type": "string"},
                                       "fresh": {"type": "boolean"}}},
     lambda a: frontdoor_read("invoices", since=a.get("since"), fresh=bool(a.get("fresh")))),
)

# Owen's alerts (2026-11-03): the only thing Claude writes, and only in the CRM's database.
SPECS = SPECS + alerts.SPECS
# The customer inspection report (2026-11-07): an HTML/PDF draft saved for Owen to download;
# its facts (customer, address, date, prices) are read from Zuper by the tool, not typed.
# Since 2026-11-12 it is SAVED with versions (saved_reports.py): Owen can change anything on the
# page, and Owen AI reads it back (get_) and saves a new version (update_) — never sent.
SPECS = SPECS + saved_reports.SPECS
# CompanyCam's photos of the job (2026-11-12), the technician's marked copies included — read
# through app/companycam.py, GET only.
SPECS = SPECS + companycam_photos.SPECS
# Proposal drafts (2026-11-09): stored PENDING in the CRM; Zuper is written only when the owner
# approves one on the Proposals tab (proposals.approve), never by this tool.
SPECS = SPECS + proposals.SPECS
# Step pictures on a CREATED proposal (2026-11-09): only when Owen asks; generate talks to OpenAI
# only, attach goes through client.owen_ai_proposal_pictures (option_image only, read back).
SPECS = SPECS + pictures.SPECS
# The saved report's PDF on its DRAFT proposal (2026-11-12): only when Owen asks; two writes
# through client.owen_ai_report_attach (upload, attach), read back, never sent.
SPECS = SPECS + report_attach.SPECS

TOOL_NAMES = tuple("mcp__%s__%s" % (SERVER_NAME, name) for name, *_ in SPECS)


def server():
    """The SDK MCP server config. Imported lazily so nothing but the runner loads the SDK."""
    from claude_agent_sdk import create_sdk_mcp_server, tool

    def wrap(name, description, schema, fn):
        @tool(name, description, schema)
        async def run(args: dict) -> dict:
            text = await asyncio.to_thread(fn, args)
            return {"content": [{"type": "text", "text": text}],
                    **({"is_error": True} if text.startswith("ERROR") else {})}
        return run

    return create_sdk_mcp_server(SERVER_NAME, tools=[wrap(*s) for s in SPECS])

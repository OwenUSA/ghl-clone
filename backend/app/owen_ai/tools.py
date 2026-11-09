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
Zuper Connect through `client.connect_read` (its five reads), Quo through `quo.get` (GET only).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from ..zuper import client as zc
from . import alerts, config, dossier, photos, proposals, quo, report

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
)

# Owen's alerts (2026-11-03): the only thing Claude writes, and only in the CRM's database.
SPECS = SPECS + alerts.SPECS
# The customer inspection report (2026-11-07): an HTML/PDF draft saved for Owen to download;
# its facts (customer, address, date, prices) are read from Zuper by the tool, not typed.
SPECS = SPECS + report.SPECS
# Proposal drafts (2026-11-09): stored PENDING in the CRM; Zuper is written only when the owner
# approves one on the Proposals tab (proposals.approve), never by this tool.
SPECS = SPECS + proposals.SPECS

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

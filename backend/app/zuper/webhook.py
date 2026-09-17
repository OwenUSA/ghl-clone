"""Register the Zuper webhooks that feed this CRM (owner decision, 2026-09-17).

    uv run python -m app.zuper.webhook             # DRY RUN: lists Zuper's webhooks and what
                                                   # it WOULD create. Writes nothing.
    uv run python -m app.zuper.webhook --commit    # creates the missing ones
    uv run python -m app.zuper.webhook --json      # the same report, for jq

Every webhook posts JSON to <ZUPER_CRM_BASE_URL>/api/zuper/webhook, authenticated by the
shared token ZUPER_WEBHOOK_TOKEN (webhooks.py): sent as the custom header X-Webhook-Token
(`TOKEN_IN = "header"`), or as ?token= on the URL (`TOKEN_IN = "url"`) if Zuper turns out not
to keep a custom header. The token is never printed.

**Never a second webhook** for the same module, event and URL. What exists is known two ways:
Zuper's own list (`LIST_PATHS`, tried in turn — the first live setup check could not read the
documented one), and the CRM's record of every webhook it registered (a `zuper_mappings` row,
crm_type "webhook", committed as "creating" BEFORE the request, like the initial load). A
create Zuper REFUSED made nothing, so its row is dropped and a corrected run tries again; a
create whose outcome is unknown (an outage, an unreadable answer) is never retried — it is
listed for a person to check in Zuper's screen. The sync never edits or deletes a webhook.

**UNVERIFIED** (zuper-research.md): the module / event names and the create body's header
field. They are the constants `EVENTS` and `HEADERS_FIELD`, so a wrong guess is corrected in
one line. The first refusal STOPS the command with Zuper's own message.
"""
from __future__ import annotations

import argparse
import json
import sys
import zlib
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import ZuperMapping
from . import client, config, zapi
from .client import ZuperError

WEBHOOK_PATH = "/api/zuper/webhook"
TOKEN_HEADER = "X-Webhook-Token"
TOKEN_IN = "header"                 # "header" | "url"
HEADERS_FIELD = "headers"           # the create body's custom-header list (UNVERIFIED)
NAME_PREFIX = "CRM sync"
CRM_TYPE = "webhook"
LIST_PATHS = ("webhooks", "webhook_list")

# (webhook_module, webhook_event) for every record kind the sync consumes: job, customer,
# appointment, note, service task, estimate / quote, invoice. UNVERIFIED names.
EVENTS: list[tuple[str, str]] = [
    ("JOB", "JOB_NEW"), ("JOB", "JOB_UPDATE"), ("JOB", "JOB_STATUS_UPDATE"),
    ("JOB", "JOB_DELETE"),
    ("CUSTOMER", "CUSTOMER_NEW"), ("CUSTOMER", "CUSTOMER_UPDATE"),
    ("CUSTOMER", "CUSTOMER_DELETE"),
    ("APPOINTMENT", "APPOINTMENT_NEW"), ("APPOINTMENT", "APPOINTMENT_UPDATE"),
    ("APPOINTMENT", "APPOINTMENT_DELETE"),
    ("NOTE", "NOTE_NEW"), ("NOTE", "NOTE_UPDATE"), ("NOTE", "NOTE_DELETE"),
    ("SERVICE_TASK", "SERVICE_TASK_NEW"), ("SERVICE_TASK", "SERVICE_TASK_UPDATE"),
    ("SERVICE_TASK", "SERVICE_TASK_DELETE"),
    ("ESTIMATE", "ESTIMATE_NEW"), ("ESTIMATE", "ESTIMATE_UPDATE"),
    ("ESTIMATE", "ESTIMATE_DELETE"),
    ("INVOICE", "INVOICE_NEW"), ("INVOICE", "INVOICE_UPDATE"), ("INVOICE", "INVOICE_DELETE"),
]

NO_TOKEN_SENTENCE = ("No webhook token is configured (ZUPER_WEBHOOK_TOKEN), so a webhook could "
                     "not authenticate; nothing was registered.")


def target_url() -> str:
    return config.crm_base_url().rstrip("/") + WEBHOOK_PATH


def record_id(module: str, event: str) -> int:
    """A stable integer for (module, event): the `crm_id` of its mapping row."""
    return zlib.crc32(("%s:%s" % (module.upper(), event.upper())).encode()) & 0x7FFFFFFF


def _bare(url: str) -> str:
    parts = urlsplit(url or "")
    return "%s://%s%s" % (parts.scheme, parts.netloc, parts.path.rstrip("/"))


def _field(rec: dict, *keys: str) -> str:
    for key in keys:
        value = rec.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _headers(rec: dict) -> dict[str, str] | None:
    """The webhook's custom headers as {lower-case name: value}, or None when Zuper did not
    report any header field at all (then whether a header is kept cannot be told)."""
    for key in (HEADERS_FIELD, "headers", "custom_headers", "webhook_headers"):
        if key in rec:
            raw = rec[key]
            break
    else:
        return None
    if isinstance(raw, dict):
        raw = [{"key": k, "value": v} for k, v in raw.items()]
    out: dict[str, str] = {}
    for h in raw if isinstance(raw, list) else []:
        if isinstance(h, dict):
            name = h.get("key") or h.get("name") or h.get("header")
            if name:
                out[str(name).strip().lower()] = str(h.get("value") or "").strip()
    return out


def authenticates(rec: dict) -> bool | None:
    """True / False, or None when Zuper's listing does not show enough to tell."""
    token = config.webhook_token()
    query = parse_qs(urlsplit(_field(rec, "webhook_url", "url")).query).get("token")
    if query and query[-1] == token:
        return True
    headers = _headers(rec)
    if headers is None:
        return None
    return headers.get(TOKEN_HEADER.lower()) == token


def create_body(module: str, event: str) -> dict:
    url = target_url()
    body: dict[str, Any] = {
        "webhook_name": "%s %s" % (NAME_PREFIX, event.lower()), "webhook_event": event,
        "webhook_url": url, "content_type": "application/json", "request_method": "POST",
        "webhook_module": module}
    if TOKEN_IN == "url":
        body["webhook_url"] = "%s?token=%s" % (url, config.webhook_token())
    else:
        body[HEADERS_FIELD] = [{"key": TOKEN_HEADER, "value": config.webhook_token()}]
    return body


def list_zuper() -> tuple[list[dict] | None, list[str]]:
    """Zuper's webhooks from the first list endpoint that answers readably, or None and
    why each one did not (kind and HTTP status only)."""
    why = []
    for name in LIST_PATHS:
        try:
            with client.read_only():
                return zapi.webhooks(name), []
        except ZuperError as exc:
            if exc.kind in ("unavailable", "rate_limited", "unauthorized", "off", "no_key"):
                raise
            why.append("GET %s: %s%s" % (client.path(name), exc.kind,
                                         " (HTTP %d)" % exc.status if exc.status else ""))
    return None, why


def recorded(db: Session) -> dict[int, ZuperMapping]:
    return {m.crm_id: m for m in db.scalars(select(ZuperMapping).where(
        ZuperMapping.crm_type == CRM_TYPE)).all()}


def run(db: Session, *, commit: bool) -> tuple[int, dict]:
    report: dict = {"mode": "commit" if commit else "dry-run", "url": target_url(),
                    "token_in": TOKEN_IN, "listed": None, "list_problems": [],
                    "existing": [], "items": [], "problems": []}
    if not config.key_set():
        report["refused"] = config.NO_KEY_SENTENCE
        return 2, report
    if not config.webhook_token():
        report["refused"] = NO_TOKEN_SENTENCE
        return 2, report
    ours = _bare(target_url())
    with client.operator_mode():
        try:
            rows, report["list_problems"] = list_zuper()
        except ZuperError as exc:
            report["stopped"] = "Zuper did not list its webhooks: " + client.sentence(exc)
            return 2, report
        report["listed"] = rows is not None
        listed: dict[tuple[str, str], dict] = {}
        for rec in rows or []:
            module = _field(rec, "webhook_module", "module").upper()
            event = _field(rec, "webhook_event", "event").upper()
            mine = _bare(_field(rec, "webhook_url", "url")) == ours
            report["existing"].append({
                "uid": _field(rec, "webhook_uid", "uid"), "module": module, "event": event,
                "url": _bare(_field(rec, "webhook_url", "url")), "points_here": mine,
                "active": rec.get("is_active", rec.get("status"))})
            if mine:
                listed.setdefault((module, event), rec)
        mine_recorded = recorded(db)
        for module, event in EVENTS:
            entry: dict = {"module": module, "event": event}
            report["items"].append(entry)
            rec = listed.get((module.upper(), event.upper()))
            m = mine_recorded.get(record_id(module, event))
            if rec is not None:
                entry["action"] = "exists"
                entry["authenticates"] = authenticates(rec)
                if entry["authenticates"] is False:
                    report["problems"].append(
                        "The %s / %s webhook (uid %s) points here but does not carry the "
                        "configured token; delete it in Zuper (Settings > Developer Hub > "
                        "Webhooks) and run again." % (
                            module, event, _field(rec, "webhook_uid", "uid") or "?"))
                if commit and (m is None or m.state != "linked"):
                    _record(db, module, event, m, "linked",
                            _field(rec, "webhook_uid", "uid") or None)
                continue
            if m is not None and m.state == "linked":
                entry["action"] = "registered"          # by this CRM; Zuper did not list it
                if report["listed"]:
                    report["problems"].append(
                        "The CRM registered the %s / %s webhook (uid %s) but Zuper no longer "
                        "lists it; check Settings > Developer Hub > Webhooks." % (
                            module, event, m.zuper_uid or "?"))
                continue
            if m is not None and m.state == "creating":
                entry["action"] = "unknown"
                report["problems"].append(
                    "Registering the %s / %s webhook was interrupted before Zuper's answer "
                    "was read; check Settings > Developer Hub > Webhooks for a webhook "
                    "named “%s %s” (create it there if missing), then tell the operator. "
                    "It is not retried, so it is never made twice." % (
                        module, event, NAME_PREFIX, event.lower()))
                continue
            entry["action"] = "create"
            if not commit:
                continue
            m = _record(db, module, event, m, "creating", None)
            try:
                payload = zapi.create_webhook(create_body(module, event))
            except ZuperError as exc:
                if exc.kind == "rejected":
                    db.delete(m)                         # refused: nothing was made
                    db.commit()
                    entry["action"] = "refused"
                else:
                    entry["action"] = "unknown"
                report["stopped"] = ("Zuper did not accept the %s / %s webhook, so nothing "
                                     "after it was tried: %s" % (module, event,
                                                                 client.sentence(exc)))
                break
            uid = _uid(payload)
            _record(db, module, event, m, "linked", uid)
            entry["action"] = "created"
            entry["uid"] = uid
        if commit and report["listed"] and any(
                i.get("action") == "created" for i in report["items"]):
            _check_created(report, ours)
    if report.get("stopped"):
        return 2, report
    return (1 if report["problems"] else 0), report


def _uid(payload: Any) -> str | None:
    try:
        return client.uid_of(payload, "webhook_uid")
    except ZuperError:
        return None                                    # made; its uid is just not echoed


def _record(db: Session, module: str, event: str, m: ZuperMapping | None, state: str,
            uid: str | None) -> ZuperMapping:
    if m is None:
        m = ZuperMapping(crm_type=CRM_TYPE, crm_id=record_id(module, event),
                         zuper_type=CRM_TYPE)
        db.add(m)
    m.state, m.zuper_uid = state, uid
    m.base = {"module": module, "event": event, "url": target_url(), "token_in": TOKEN_IN}
    m.updated_at = datetime.now(UTC)
    db.commit()
    return m


def _check_created(report: dict, ours: str) -> None:
    """Read the list back: did Zuper keep what was sent, and will it authenticate?"""
    rows, why = list_zuper()
    if rows is None:
        report["problems"].append("Zuper did not list its webhooks after creating them (%s)."
                                  % "; ".join(why))
        return
    found = {(_field(r, "webhook_module", "module").upper(),
              _field(r, "webhook_event", "event").upper()): r
             for r in rows if _bare(_field(r, "webhook_url", "url")) == ours}
    for entry in report["items"]:
        if entry.get("action") != "created":
            continue
        rec = found.get((entry["module"].upper(), entry["event"].upper()))
        if rec is None:
            report["problems"].append("Zuper accepted the %s / %s webhook but does not list "
                                      "it." % (entry["module"], entry["event"]))
            continue
        entry["authenticates"] = authenticates(rec)
        if entry["authenticates"] is False:
            report["problems"].append(
                "Zuper did not keep the %s header on the %s / %s webhook, so its deliveries "
                "would be refused; switch TOKEN_IN to \"url\" in app/zuper/webhook.py, delete "
                "these webhooks in Zuper and run again." % (TOKEN_HEADER, entry["module"],
                                                           entry["event"]))


def setup_state(db: Session) -> tuple[str, str]:
    """For the setup check: ("pass" | "fail" | "confirm_by_hand", sentence)."""
    rows, why = list_zuper()
    url = target_url()
    if rows is not None:
        ours = [w for w in rows if _bare(_field(w, "webhook_url", "url")) == _bare(url)]
        if ours:
            return "pass", "%d webhook(s) post to this CRM." % len(ours)
        return "fail", "No webhook posts to %s yet (python -m app.zuper.webhook)." % url
    mine = recorded(db)
    linked = [e for e in EVENTS if (m := mine.get(record_id(*e))) and m.state == "linked"]
    if len(linked) == len(EVENTS):
        return "pass", ("All %d webhooks were registered by this CRM (Zuper does not list "
                        "webhooks through its API: %s)." % (len(EVENTS), "; ".join(why)))
    if not linked:
        return "confirm_by_hand", ("Zuper did not list its webhooks (%s); register them with "
                                   "python -m app.zuper.webhook, or confirm one posts to %s "
                                   "with the X-Webhook-Token header." % ("; ".join(why), url))
    return "fail", ("%d of %d webhooks are registered; run python -m app.zuper.webhook."
                    % (len(linked), len(EVENTS)))


def render(report: dict) -> str:
    dry = report["mode"] == "dry-run"
    lines = ["MODE: %s" % ("DRY RUN — nothing was written in Zuper" if dry else "COMMIT"),
             "target: %s (token in %s)" % (report["url"], report["token_in"])]
    if report.get("refused"):
        lines.append("REFUSED: " + report["refused"])
    for p in report.get("list_problems") or []:
        lines.append("could not list: " + p)
    if report.get("listed"):
        lines.append("existing webhooks in Zuper: %d" % len(report["existing"]))
    for e in report["existing"]:
        lines.append("  %s %s / %s -> %s%s (active: %s)" % (
            e["uid"] or "?", e["module"] or "?", e["event"] or "?", e["url"] or "?",
            " [this CRM]" if e["points_here"] else "", e["active"]))
    words = {True: "yes", False: "NO", None: "cannot tell"}
    for i in report["items"]:
        action = i.get("action", "—")
        if dry and action == "create":
            action = "would create"
        auth = (", authenticates: %s" % words[i["authenticates"]]
                if "authenticates" in i else "")
        lines.append("  %s / %s: %s%s" % (i["module"], i["event"], action, auth))
    for p in report["problems"]:
        lines.append("PROBLEM: " + p)
    if report.get("stopped"):
        lines.append("STOPPED: " + report["stopped"])
    created = sum(i.get("action") == "created" for i in report["items"])
    lines.append("result: %s" % (
        "refused" if report.get("refused") else "stopped" if report.get("stopped")
        else "%d created, %d problem(s)" % (created, len(report["problems"]))))
    return "\n".join(lines)


def redact(text: str) -> str:
    """Zuper's messages may echo what was sent; the token never reaches the output."""
    token = config.webhook_token()
    return text.replace(token, "[token]") if token else text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--commit", action="store_true", help="create what is missing")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    db = SessionLocal()
    try:
        code, report = run(db, commit=args.commit)
    finally:
        db.close()
    print(redact(json.dumps(report, indent=2, default=str) if args.json else render(report)))
    return code


if __name__ == "__main__":
    sys.exit(main())

"""Register the Zuper webhooks that feed this CRM (owner decision, 2026-09-17).

    uv run python -m app.zuper.webhook             # DRY RUN: lists Zuper's webhooks and what
                                                   # it WOULD create. Writes nothing.
    uv run python -m app.zuper.webhook --commit    # creates the missing ones
    uv run python -m app.zuper.webhook --json      # the same report, for jq

Every webhook posts JSON to <ZUPER_CRM_BASE_URL>/api/zuper/webhook, authenticated by the
shared token ZUPER_WEBHOOK_TOKEN (webhooks.py): sent as the custom header X-Webhook-Token
(`TOKEN_IN = "header"`), or as ?token= on the URL (`TOKEN_IN = "url"`) if Zuper turns out not
to keep a custom header. The token is never printed: URLs are shown without their query.

**One webhook per (module, event), never a second one** for the same module, event and URL
(query ignored). An existing one that would not authenticate (its header or ?token= is not
the configured token) is listed as a problem to fix in Zuper's screen — the sync neither
edits nor deletes Zuper's webhooks.

**UNVERIFIED** (zuper-research.md): Zuper documents neither the module / event names nor
the create body's header field. They are the constants `EVENTS` and `HEADERS_FIELD`, so a
wrong guess is corrected in one line. The first refusal STOPS the command with Zuper's own
message, and nothing after it is tried.
"""
from __future__ import annotations

import argparse
import json
import sys
from urllib.parse import parse_qs, urlsplit

from . import client, config, zapi
from .client import ZuperError

WEBHOOK_PATH = "/api/zuper/webhook"
TOKEN_HEADER = "X-Webhook-Token"
TOKEN_IN = "header"                 # "header" | "url"
HEADERS_FIELD = "headers"           # the create body's custom-header list (UNVERIFIED)
NAME_PREFIX = "CRM sync"

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
    raw = None
    for key in (HEADERS_FIELD, "headers", "custom_headers", "webhook_headers"):
        if key in rec:
            raw = rec[key]
            break
    else:
        return None
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        raw = [{"key": k, "value": v} for k, v in raw.items()]
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
    body = {"webhook_name": "%s %s" % (NAME_PREFIX, event.lower()), "webhook_event": event,
            "webhook_url": url, "content_type": "application/json",
            "request_method": "POST", "webhook_module": module}
    if TOKEN_IN == "url":
        body["webhook_url"] = "%s?token=%s" % (url, config.webhook_token())
    else:
        body[HEADERS_FIELD] = [{"key": TOKEN_HEADER, "value": config.webhook_token()}]
    return body


def run(*, commit: bool) -> tuple[int, dict]:
    report: dict = {"mode": "commit" if commit else "dry-run", "url": target_url(),
                    "token_in": TOKEN_IN, "existing": [], "items": [], "problems": []}
    if not config.key_set():
        report["refused"] = config.NO_KEY_SENTENCE
        return 2, report
    if not config.webhook_token():
        report["refused"] = NO_TOKEN_SENTENCE
        return 2, report
    ours = _bare(target_url())
    with client.operator_mode():
        try:
            with client.read_only():
                rows = zapi.webhooks()
        except ZuperError as exc:
            report["stopped"] = "Zuper did not list its webhooks: " + client.sentence(exc)
            return 2, report
        have: dict[tuple[str, str], dict] = {}
        for rec in rows:
            module = _field(rec, "webhook_module", "module").upper()
            event = _field(rec, "webhook_event", "event").upper()
            mine = _bare(_field(rec, "webhook_url", "url")) == ours
            report["existing"].append({
                "uid": _field(rec, "webhook_uid", "uid"), "module": module, "event": event,
                "url": _bare(_field(rec, "webhook_url", "url")), "points_here": mine,
                "active": rec.get("is_active", rec.get("status"))})
            if mine:
                have.setdefault((module, event), rec)
        for module, event in EVENTS:
            entry = {"module": module, "event": event}
            report["items"].append(entry)
            rec = have.get((module.upper(), event.upper()))
            if rec is not None:
                ok = authenticates(rec)
                entry["action"] = "exists"
                entry["authenticates"] = ok
                if ok is False:
                    report["problems"].append(
                        "The %s / %s webhook (uid %s) points here but does not carry the "
                        "configured token; delete it in Zuper (Settings > Developer Hub > "
                        "Webhooks) and run again." % (module, event,
                                                      _field(rec, "webhook_uid", "uid") or "?"))
                continue
            entry["action"] = "create"
            if not commit:
                continue
            try:
                zapi.create_webhook(create_body(module, event))
            except ZuperError as exc:
                entry["action"] = "refused"
                report["stopped"] = ("Zuper did not accept the %s / %s webhook, so nothing "
                                     "after it was tried: %s" % (module, event,
                                                                 client.sentence(exc)))
                break
            entry["action"] = "created"
        if commit and any(i["action"] == "created" for i in report["items"]):
            _check_created(report, ours)
    if report.get("stopped"):
        return 2, report
    return (1 if report["problems"] else 0), report


def _check_created(report: dict, ours: str) -> None:
    """Read the list back: did Zuper keep what was sent, and will it authenticate?"""
    try:
        with client.read_only():
            rows = zapi.webhooks()
    except ZuperError as exc:
        report["problems"].append("Zuper did not list its webhooks after creating them: "
                                  + client.sentence(exc))
        return
    found = {(_field(r, "webhook_module", "module").upper(),
              _field(r, "webhook_event", "event").upper()): r
             for r in rows if _bare(_field(r, "webhook_url", "url")) == ours}
    for entry in report["items"]:
        if entry["action"] != "created":
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


def render(report: dict) -> str:
    dry = report["mode"] == "dry-run"
    lines = ["MODE: %s" % ("DRY RUN — nothing was written in Zuper" if dry else "COMMIT"),
             "target: %s (token in %s)" % (report["url"], report["token_in"])]
    if report.get("refused"):
        lines.append("REFUSED: " + report["refused"])
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--commit", action="store_true", help="create what is missing")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    code, report = run(commit=args.commit)
    print(redact(json.dumps(report, indent=2, default=str) if args.json else render(report)))
    return code


def redact(text: str) -> str:
    """Zuper's messages may echo what was sent; the token never reaches the output."""
    token = config.webhook_token()
    return text.replace(token, "[token]") if token else text


if __name__ == "__main__":
    sys.exit(main())

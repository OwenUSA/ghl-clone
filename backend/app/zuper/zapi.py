"""One function per Zuper endpoint the sync uses. Request bodies are wrapped the way Zuper's
reference shows ({"customer": {...}}, {"job": {...}}); every shape here is UNVERIFIED against
the live account and is the first thing the read-only probe checks (DECISIONS.md, 2026-09-16).
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from .client import (
    ZuperError,
    data_of,
    iter_all,
    path,
    request,
    rows_of,
    shape,
    total_of,
    uid_of,
)


def _record(payload: Any) -> dict:
    data = data_of(payload)
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        raise ZuperError("bad_response", "expected a record")
    return data


def is_deleted(record: dict | None) -> bool:
    return record is None or bool(record.get("is_deleted"))


# ------------------------------------------------------------------ users

def me() -> dict:
    return _record(request("GET", path("me")))


def users() -> list[dict]:
    return rows_of(request("GET", path("users")))


def user_email(user: dict) -> str | None:
    email = user.get("email") or user.get("user_email")
    return str(email).strip().lower() if email else None


def user_uid(user: dict) -> str | None:
    value = user.get("user_uid") or user.get("uid")
    return str(value) if value else None


# ------------------------------------------------------------------ customers

def customers(params: dict | None = None) -> Iterator[dict]:
    return iter_all(path("customers"), params)


def customer(uid: str) -> dict:
    return _record(request("GET", path("customer", uid=uid)))


def create_customer(body: dict) -> str:
    return uid_of(request("POST", path("customer_create"), body={"customer": body}),
                  "customer_uid")


def update_customer(uid: str, body: dict) -> None:
    request("PUT", path("customer", uid=uid), body={"customer": body})


def delete_customer(uid: str) -> None:
    request("DELETE", path("customer", uid=uid))


def recover_customer(uid: str) -> None:
    request("POST", path("customer_recover", uid=uid))


def find_customers_by_crm_id(crm_id: int) -> list[dict]:
    """Customers whose "CRM Contact ID" is this contact. The keyword filter narrows the
    list; the custom field is checked here, so a phone number containing the id is never
    taken for a match."""
    from .mapping import custom_values
    found = []
    for rec in customers({"filter.keyword": str(crm_id)}):
        if str(custom_values(rec).get("CRM Contact ID") or "").strip() == str(crm_id):
            found.append(rec)
    return found


# ------------------------------------------------------------------ categories and statuses

def categories() -> list[dict]:
    return rows_of(request("GET", path("categories")))


# Which body shape Zuper accepted for a create whose shape is UNVERIFIED, by kind — shown by
# the setup report so the first live run tells which one is right.
ACCEPTED_SHAPES: dict[str, str] = {}


def first_accepted(kind: str, method: str, api_path: str,
                   shapes: list[tuple[str, dict]]) -> Any:
    """Send each candidate body in turn until Zuper accepts one; return Zuper's answer.

    Only a REJECTION (Zuper's 4xx or {"type": "error"}) moves on to the next shape: a
    refused request changed nothing. Anything else — an outage, an unreadable answer —
    is raised at once, so nothing is ever made twice. The shape Zuper accepted is tried
    first from then on. All refused: one error carrying Zuper's message for every shape."""
    known = ACCEPTED_SHAPES.get(kind)
    ordered = sorted(shapes, key=lambda sh: sh[0] != known)
    refused = []
    for name, body in ordered:
        try:
            payload = request(method, api_path, body=body)
        except ZuperError as exc:
            if exc.kind != "rejected":
                raise
            refused.append("%s → %s" % (name, exc.detail or "rejected"))
            continue
        ACCEPTED_SHAPES[kind] = name
        return payload
    raise ZuperError("rejected", "Zuper refused every %s body tried: %s" % (
        kind, "; ".join(refused)))


def create_first_accepted(kind: str, api_path: str, shapes: list[tuple[str, dict]],
                          key: str) -> str:
    """`first_accepted` for a create: the new uid (an unreadable answer is never retried)."""
    return uid_of(first_accepted(kind, "POST", api_path, shapes), key)


def create_category(name: str) -> str:
    # 2026-09-17, live: {"job_category": {"category_name": …}} → "Category Name Missing".
    fields = {"category_name": name}
    return create_first_accepted("category", path("categories"), [
        ("flat", dict(fields)), ("category", {"category": dict(fields)}),
        ("job_category", {"job_category": dict(fields)})], "category_uid")


def statuses(category_uid: str) -> list[dict]:
    return rows_of(request("GET", path("statuses", category_uid=category_uid)))


def status_sources(category_uid: str) -> tuple[list[dict] | None, list[str]]:
    """Every status Zuper shows for a category (UNVERIFIED where): GET /jobs/status_new/{uid},
    GET /jobs/status/{uid}, and the `job_statuses` inside the category record. Live
    2026-09-17: the first answers 404, the second is not a list, the category record carries
    `job_statuses`. Merged by uid; a record without a readable uid is kept, so the caller can
    refuse to guess. None only when no source could be read. The notes say what each source
    gave (counts and field names only)."""
    rows, notes, _ = _status_sources(category_uid)
    return rows, notes


def _status_sources(category_uid: str) -> tuple[list[dict] | None, list[str], bool]:
    merged: list[dict] = []
    seen: set[str] = set()
    notes: list[str] = []
    readable = False
    reliable = False

    def take(rows: list[dict]) -> None:
        for r in rows:
            uid = status_uid(r)
            if uid and uid in seen:
                continue
            if uid:
                seen.add(uid)
            merged.append(r)

    for name in ("status_create", "statuses"):
        api_path = path(name, category_uid=category_uid)
        try:
            rows = rows_of(request("GET", api_path))
        except ZuperError as exc:
            if exc.kind not in ("not_found", "bad_response", "rejected", "refused"):
                raise
            notes.append("GET %s: %s%s%s" % (
                api_path, exc.kind, " (HTTP %d)" % exc.status if exc.status else "",
                " %s" % exc.detail if exc.kind == "bad_response" else ""))
            continue
        readable = True
        # GET /jobs/status/{c} is Zuper's own status list for the category (live: it answers
        # {data: {job_statuses: [...]}} with status_uid and status_name) — a status missing
        # from it does not exist.
        reliable = reliable or name == "statuses"
        notes.append("GET %s: %d%s" % (api_path, len(rows), (": " + ", ".join(
            "“%s”" % (status_name(r) or "?") for r in rows[:30])) if rows else ""))
        take(rows)
    records = categories()
    others_with_statuses = sum(
        1 for rec in records if category_uid_of(rec) != category_uid
        and isinstance(rec.get("job_statuses"), list) and rec["job_statuses"])
    for rec in records:
        if category_uid_of(rec) != category_uid:
            continue
        notes.append("category record fields: %s" % ", ".join(sorted(rec.keys())[:30]))
        for key in ("job_statuses", "statuses", "category_statuses", "job_status"):
            if isinstance(rec.get(key), list):
                readable = True
                reliable = reliable or (key == "job_statuses" and others_with_statuses > 0)
                rows = [r for r in rec[key] if isinstance(r, dict)]
                notes.append("category record %s: %d%s" % (key, len(rows), (
                    " (fields: %s)" % ", ".join(sorted(rows[0].keys())[:20])) if rows else ""))
                take(rows)
    notes.append("other categories showing statuses in their record: %d" % others_with_statuses)
    # The category list is Zuper's own record of each category's statuses. It is trusted to
    # show a status that exists when it shows statuses for other categories too.
    return (merged if readable else None), notes, reliable


def statuses_reliably(category_uid: str) -> tuple[list[dict] | None, list[str], bool]:
    """(statuses, notes, reliable): reliable = a status missing from the list does not exist."""
    return _status_sources(category_uid)


STATUS_COLOR = "#1E88E5"


def status_create_candidates(category_uid: str, name: str,
                             status_type: str) -> list[tuple[str, str, str, Any]]:
    """(label, method, path, body) — every way a status might be created (UNVERIFIED). Live
    2026-09-17: POST /jobs/status_new/{c} {"status_name", "status_type"} answered
    {type, message} and nothing appeared. The UI requires a colour."""
    one = {"status_name": name, "status_type": status_type, "status_color": STATUS_COLOR}
    new = path("status_create", category_uid=category_uid)
    return [
        ("status_new flat", "POST", new, dict(one)),
        ("status_new job_status", "POST", new, {"job_status": dict(one)}),
        ("status_new job_statuses[]", "POST", new, {"job_statuses": [dict(one)]}),
        ("status_new [list]", "POST", new, [dict(one)]),
        ("jobs/status flat", "POST", path("status_create_plain"),
         {**one, "category_uid": category_uid}),
        ("jobs/status job_status", "POST", path("status_create_plain"),
         {"job_status": {**one, "category_uid": category_uid}}),
    ]


def statuses_or_none(category_uid: str) -> list[dict] | None:
    return status_sources(category_uid)[0]


def create_status(category_uid: str, name: str, status_type: str,
                  taken: set | frozenset = frozenset()) -> str:
    """Create a status and return its uid, VERIFIED: an answer without a uid is only believed
    when the status then shows in Zuper's own category record. Each candidate request is
    tried in turn (the one that worked before first) and moves on only when Zuper refused it,
    or when it said OK but nothing appeared AND Zuper's list is reliable — so a status is
    never made twice. When the list cannot be trusted, it stops. Every attempt's outcome,
    with Zuper's own message, is in the error."""
    notes: list[str] = []
    known = ACCEPTED_SHAPES.get("status")
    candidates = sorted(status_create_candidates(category_uid, name, status_type),
                        key=lambda c: c[0] != known)
    for label, method, api_path, body in candidates:
        try:
            payload = request(method, api_path, body=body)
        except ZuperError as exc:
            if exc.kind not in ("rejected", "not_found"):
                raise
            notes.append("%s: refused (%s)" % (label, exc.detail or exc.kind))
            continue
        try:
            uid = uid_of(payload, "status_uid", "job_status_uid")
        except ZuperError:
            uid = None
        if uid:
            ACCEPTED_SHAPES["status"] = label
            return uid
        rows, source_notes, reliable = _status_sources(category_uid)
        found = [status_uid(r) for r in rows or [] if status_name(r) == name
                 and status_uid(r) and status_uid(r) not in taken]
        message = payload.get("message") if isinstance(payload, dict) else None
        said = "“%s”" % str(message)[:200] if message else shape(payload)
        if len(found) == 1:
            ACCEPTED_SHAPES["status"] = label
            return str(found[0])
        if len(found) > 1:
            raise ZuperError("bad_response", "%s: answered %s and “%s” now shows %d times; "
                             "not mapped." % (label, said, name, len(found)))
        notes.append("%s: answered %s, but no status “%s” appeared" % (label, said, name))
        if not reliable:
            raise ZuperError("bad_response", "Zuper said OK to a status create but the sync "
                             "cannot tell whether it made “%s” (its lists are not reliable: "
                             "%s). Stopped so it is never made twice. Attempts: %s." % (
                                 name, "; ".join(source_notes), "; ".join(notes)))
    raise ZuperError("rejected", "No way of creating the status “%s” worked. Attempts: %s."
                     % (name, "; ".join(notes)))


def rename_status(category_uid: str, status_uid: str, name: str) -> None:
    request("PUT", path("status_update", category_uid=category_uid, status_uid=status_uid),
            body={"job_status": {"status_name": name}})


def category_uid_of(rec: dict) -> str | None:
    return category_uid(rec)


def category_uid(rec: dict) -> str | None:
    value = rec.get("category_uid") or rec.get("uid")
    return str(value) if value else None


def category_name(rec: dict) -> str | None:
    return rec.get("category_name") or rec.get("name")


def status_uid(rec: dict) -> str | None:
    value = rec.get("status_uid") or rec.get("job_status_uid") or rec.get("uid")
    return str(value) if value else None


def status_name(rec: dict) -> str | None:
    for key in ("status_name", "job_status_name", "name"):
        value = rec.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# ------------------------------------------------------------------ jobs

def jobs(params: dict | None = None) -> Iterator[dict]:
    return iter_all(path("jobs"), params)


def job(uid: str) -> dict:
    return _record(request("GET", path("job", uid=uid)))


def create_job(body: dict) -> str:
    return uid_of(request("POST", path("jobs"), body={"job": body}), "job_uid")


def update_job(uid: str, body: dict) -> None:
    request("PUT", path("jobs"), body={"job": {**body, "job_uid": uid}})


def delete_job(uid: str) -> None:
    request("DELETE", path("job_delete", uid=uid))


def recover_job(uid: str) -> None:
    request("POST", path("job_recover", uid=uid))


def _status_shapes(uid: str, status: str) -> list[tuple[str, dict]]:
    # UNVERIFIED body; a refused move changed nothing, so the next shape is safe to try.
    return [("flat", {"job_uid": uid, "status_uid": status}),
            ("job_status", {"job_status": {"status_uid": status}}),
            ("job", {"job": {"job_uid": uid, "status_uid": status}})]


def set_job_status(uid: str, status: str) -> None:
    first_accepted("job_status", "PUT", path("job_status", uid=uid),
                   _status_shapes(uid, status))


def rollback_job_status(uid: str, status: str) -> None:
    """Zuper only moves a job BACK through its statuses with the rollback endpoint."""
    first_accepted("job_status_rollback", "PUT", path("job_status_rollback", uid=uid),
                   _status_shapes(uid, status))


def find_jobs_by_crm_id(crm_id: int) -> list[dict]:
    from .mapping import custom_values
    found = []
    for rec in jobs({"filter.keyword": str(crm_id)}):
        if str(custom_values(rec).get("CRM Opportunity ID") or "").strip() == str(crm_id):
            found.append(rec)
    return found


# ------------------------------------------------------------------ notes and service tasks

def job_notes(job_uid: str) -> list[dict]:
    return rows_of(request("GET", path("job_notes", uid=job_uid)))


def create_note(job_uid: str, body: dict) -> str:
    return create_first_accepted("note", path("job_notes", uid=job_uid), [
        ("flat", dict(body)), ("note", {"note": dict(body)})], "note_uid")


def update_note(job_uid: str, note_uid: str, body: dict) -> None:
    request("PUT", path("job_note", uid=job_uid, note_uid=note_uid), body=body)


def delete_note(job_uid: str, note_uid: str) -> None:
    request("DELETE", path("job_note", uid=job_uid, note_uid=note_uid))


def job_tasks(job_uid: str) -> list[dict]:
    return rows_of(request("GET", path("job_service_tasks", uid=job_uid)))


def create_task(job_uid: str, body: dict) -> str:
    return uid_of(request("POST", path("job_service_tasks", uid=job_uid),
                          body={"service_task": body}), "service_task_uid")


def update_task(job_uid: str, task_uid: str, body: dict) -> None:
    request("PUT", path("job_service_task", uid=job_uid, task_uid=task_uid),
            body={"service_task": body})


def delete_task(job_uid: str, task_uid: str) -> None:
    request("DELETE", path("job_service_task", uid=job_uid, task_uid=task_uid))


def note_uid(rec: dict) -> str | None:
    value = rec.get("note_uid") or rec.get("uid")
    return str(value) if value else None


def task_uid(rec: dict) -> str | None:
    value = rec.get("service_task_uid") or rec.get("uid")
    return str(value) if value else None


# ------------------------------------------------------------------ appointments

def appointments(params: dict | None = None) -> Iterator[dict]:
    return iter_all(path("appointments"), params)


def job_appointments(job_uid: str) -> list[dict]:
    return list(appointments({"filter.job_uid": job_uid}))


def appointment(uid: str) -> dict:
    return _record(request("GET", path("appointment", uid=uid)))


def create_appointment(body: dict) -> str:
    return create_first_accepted("appointment", path("appointments"), [
        ("appointment", {"appointment": dict(body)}), ("flat", dict(body))],
        "appointment_uid")


def update_appointment(uid: str, body: dict) -> None:
    request("PUT", path("appointment", uid=uid), body={"appointment": body})


def delete_appointment(uid: str) -> None:
    request("DELETE", path("appointment", uid=uid))


def appointment_uid(rec: dict) -> str | None:
    value = rec.get("appointment_uid") or rec.get("uid")
    return str(value) if value else None


def appointment_job_uid(rec: dict) -> str | None:
    value = rec.get("job_uid") or rec.get("job")
    if isinstance(value, dict):
        value = value.get("job_uid")
    return str(value) if value else None


# ------------------------------------------------------------------ quotes and invoices (read only)

def estimates(params: dict | None = None) -> Iterator[dict]:
    return iter_all(path("estimates"), params)


def estimate(uid: str) -> dict:
    return _record(request("GET", path("estimate", uid=uid)))


def invoices(params: dict | None = None) -> Iterator[dict]:
    return iter_all(path("invoices"), params)


def invoice(uid: str) -> dict:
    return _record(request("GET", path("invoice", uid=uid)))


# ------------------------------------------------------------------ attachments

def job_attachments(job_uid: str) -> list[dict]:
    """A job's own files. `filter.module_uid` is what narrows them: WITHOUT it the live
    endpoint answers the whole account's attachments (537 of them, 2026-09-24), so the
    filter is not optional - it is the difference between this job and everyone's."""
    return rows_of(request("GET", path("job_attachments"),
                           params={"filter.module_uid": job_uid, "page": 1, "count": 100}))


# ------------------------------------------------------------------ settings the setup check reads

def custom_field_defs(module: str) -> list[dict]:
    return rows_of(request("GET", path("custom_fields"), params={"module": module}))


def lead_sources() -> list[dict]:
    return rows_of(request("GET", path("lead_sources")))


def webhooks(path_name: str = "webhooks") -> list[dict]:
    return rows_of(request("GET", path(path_name)))


def create_webhook(web_hook: dict) -> Any:
    """POST /webhook {"web_hook": {...}} — the body is built by app/zuper/webhook.py."""
    return request("POST", path("webhook_create"), body={"web_hook": web_hook})


def total(path_name: str, params: dict | None = None) -> int | None:
    payload = request("GET", path(path_name), params={**(params or {}), "page": 1, "count": 1})
    found = total_of(payload)
    if found is None:
        try:
            found = len(rows_of(payload))
        except ZuperError:
            return None
    return found


def compact(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)[:500]

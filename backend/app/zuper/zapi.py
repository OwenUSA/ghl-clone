"""One function per Zuper endpoint the sync uses. Request bodies are wrapped the way Zuper's
reference shows ({"customer": {...}}, {"job": {...}}); every shape here is UNVERIFIED against
the live account and is the first thing the read-only probe checks (DECISIONS.md, 2026-09-16).
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from .client import ZuperError, data_of, iter_all, path, request, rows_of, total_of, uid_of


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


def statuses_or_none(category_uid: str) -> list[dict] | None:
    """The category's statuses, or None when Zuper will not say. The operator's first read
    found GET /jobs/status empty (2026-09-16), so when the per-category list is not readable
    the statuses embedded in the category record are used, if it carries any."""
    try:
        return statuses(category_uid)
    except ZuperError as exc:
        if exc.kind not in ("not_found", "bad_response", "rejected"):
            raise
    for rec in categories():
        if category_uid_of(rec) == category_uid:
            for key in ("job_statuses", "statuses", "category_statuses", "job_status"):
                if isinstance(rec.get(key), list):
                    return [r for r in rec[key] if isinstance(r, dict)]
    return None


def create_status(category_uid: str, name: str, status_type: str) -> str:
    fields = {"status_name": name, "status_type": status_type}
    return create_first_accepted("status", path("status_create", category_uid=category_uid), [
        ("flat", dict(fields)), ("job_status", {"job_status": dict(fields)}),
        ("status", {"status": dict(fields)})], "status_uid")


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
    value = rec.get("status_uid") or rec.get("uid")
    return str(value) if value else None


def status_name(rec: dict) -> str | None:
    return rec.get("status_name") or rec.get("name")


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
    return rows_of(request("GET", path("job_attachments", uid=job_uid)))


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

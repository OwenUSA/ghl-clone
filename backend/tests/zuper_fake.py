"""An in-memory Zuper at the HTTP boundary, for tests and the headless-browser drive.

    fake = FakeZuper()
    client.TRANSPORT = fake.transport()          # or the `zuper` fixture in tests/zuper_support

It answers the endpoints `app/zuper/client.PATHS` names, in the shapes `app/zuper/zapi.py`
reads — which are Zuper's documented shapes as far as the research file could tell, and
UNVERIFIED against the live account. It records every request, and REFUSES (records a
violation and answers 418) any request the client's denylist names, so a denylisted call
that somehow got past the client would still fail the test that made it.

Helpers act "as a person in Zuper" (`edit_job`, `add_invoice`, `delete_job`, …): they change
the record and bump its `updated_at` the way a real edit would, without going through the
client, so the sync sees them only by webhook or sweep.
"""
from __future__ import annotations

import itertools
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

import httpx
from app.zuper import client

SYNC_USER = {"user_uid": "u-sync", "first_name": "CRM", "last_name": "Sync",
             "email": "crm-sync@example.test", "role": "Admin"}
OFFICE_USER = {"user_uid": "u-owner", "first_name": "Owen", "last_name": "Owner",
               "email": "owner@example.test", "role": "Admin"}


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Seen:
    method: str
    path: str
    params: dict
    body: Any


@dataclass
class FakeZuper:
    customers: dict[str, dict] = field(default_factory=dict)
    jobs: dict[str, dict] = field(default_factory=dict)
    categories: dict[str, dict] = field(default_factory=dict)
    statuses: dict[str, list[dict]] = field(default_factory=dict)
    notes: dict[str, list[dict]] = field(default_factory=dict)
    tasks: dict[str, list[dict]] = field(default_factory=dict)
    appointments: dict[str, dict] = field(default_factory=dict)
    estimates: dict[str, dict] = field(default_factory=dict)
    invoices: dict[str, dict] = field(default_factory=dict)
    attachments: dict[str, list[dict]] = field(default_factory=dict)
    files: dict[str, tuple[bytes, str]] = field(default_factory=dict)
    webhooks: list[dict] = field(default_factory=list)
    # POST /webhook: the (module, event) pairs Zuper accepts (None = any), and whether it
    # keeps the custom headers it was sent.
    webhook_events: set[tuple[str, str]] | None = None
    keeps_webhook_headers: bool = True
    # Where GET shows a category's statuses: "status" = /jobs/status/{c}, "status_new" =
    # /jobs/status_new/{c}. A path not in the set answers an empty list (as live 09-17).
    statuses_listed_at: set[str] = field(default_factory=lambda: {"status"})
    status_create_echoes_uid: bool = True
    status_list_wrapped: bool = False    # live: {data: {_id, job_statuses: [...]}}
    status_list_readable: bool = True    # False: GET /jobs/status/{c} answers no list
    # Live-like status creates: the only body form that really creates ("flat", "job_status",
    # "job_statuses", "list", "plain"; None = flat and job_status both work, as before). Any
    # other form answers {type, message} and makes nothing (live, 2026-09-17).
    status_create_form: str | None = None
    category_embeds_statuses: bool = False       # GET /jobs/category carries job_statuses
    template_category_with_statuses: bool = False
    webhooks_listable: bool = True          # False: both list endpoints answer 404 (live, 09-17)
    users: list[dict] = field(default_factory=lambda: [dict(SYNC_USER), dict(OFFICE_USER)])
    me: dict = field(default_factory=lambda: dict(SYNC_USER))
    # Settings the check reads. None = Zuper answers 404 (the "confirm by hand" path).
    custom_field_defs: dict[str, list[dict]] | None = None
    lead_sources: list[dict] | None = None
    requests: list[Seen] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    company_name: str = "Dream Team Roofing"
    dc_api_url: str = "https://us-east-1.zuperpro.com"
    # Behaviour switches.
    honour_updated_filter: bool = True
    throttle: int = 0                       # the next N requests answer 429
    fail_next: dict[str, int] = field(default_factory=dict)   # "METHOD path-regex" -> count
    clock: datetime = field(default_factory=lambda: datetime.now(UTC).replace(microsecond=0))
    _ids: Any = field(default_factory=lambda: itertools.count(1))

    # ------------------------------------------------------------------ plumbing

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def install(self) -> FakeZuper:
        client.TRANSPORT = self.transport()
        return self

    def uid(self, prefix: str) -> str:
        return "%s-%d" % (prefix, next(self._ids))

    def tick(self) -> str:
        self.clock += timedelta(seconds=1)
        return iso(self.clock)

    def writes(self) -> list[Seen]:
        return [r for r in self.requests if r.method != "GET"]

    def calls(self, method: str, pattern: str) -> list[Seen]:
        return [r for r in self.requests if r.method == method and re.search(pattern, r.path)]

    @staticmethod
    def ok(data: Any = None, **extra) -> httpx.Response:
        body = {"type": "success", **extra}
        if data is not None:
            body["data"] = data
        return httpx.Response(200, json=body)

    @staticmethod
    def not_found() -> httpx.Response:
        return httpx.Response(404, json={"type": "error", "title": "Not found",
                                         "message": "Record not found"})

    @staticmethod
    def error(message: str, status: int = 400) -> httpx.Response:
        return httpx.Response(status, json={"type": "error", "title": "Error",
                                            "message": message})

    def page(self, rows: list[dict], params: dict) -> httpx.Response:
        page = int(params.get("page", 1))
        count = int(params.get("count", 100))
        chunk = rows[(page - 1) * count: page * count]
        pages = max(1, -(-len(rows) // count))
        return self.ok(chunk, total_records=len(rows), total_pages=pages, current_page=page)

    def filtered(self, rows: list[dict], params: dict) -> list[dict]:
        deleted = params.get("filter.is_deleted") == "true"
        out = [r for r in rows if bool(r.get("is_deleted")) == deleted]
        since = params.get("filter.updated_at_from")
        if since and self.honour_updated_filter:
            moment = datetime.fromisoformat(since)
            out = [r for r in out if datetime.fromisoformat(r["updated_at"]) >= moment]
        keyword = params.get("filter.keyword")
        if keyword:
            out = [r for r in out if keyword in json.dumps(r)]
        job_uid = params.get("filter.job_uid")
        if job_uid:
            out = [r for r in out if r.get("job_uid") == job_uid]
        return out

    # ------------------------------------------------------------------ the handler

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "accounts.zuperpro.com":
            body = json.loads(request.content or b"{}")
            self.requests.append(Seen(request.method, "ACCOUNTS " + request.url.path, {}, body))
            if request.headers.get("x-api-key"):
                self.violations.append("the API key was sent to the region lookup")
            if body.get("company_name") != self.company_name:
                return self.not_found()
            return self.ok({"dc_api_url": self.dc_api_url})
        if request.url.host.endswith("zuperpro.com") and not request.url.path.startswith("/api"):
            return self.file(request)
        path = request.url.path.removeprefix("/api")
        params = {k: v[-1] for k, v in parse_qs(request.url.query.decode()).items()}
        body = json.loads(request.content) if request.content else None
        self.requests.append(Seen(request.method, path, params, body))
        if client.denied(request.method, path):
            self.violations.append("%s %s" % (request.method, path))
            return httpx.Response(418, json={"type": "error", "message": "denylisted"})
        if request.headers.get("x-api-key") != "zk_test":
            return self.error("bad key", 401)
        if self.throttle > 0:
            self.throttle -= 1
            return httpx.Response(429, headers={"Retry-After": "1"},
                                  json={"type": "error", "message": "Too many requests"})
        for key, n in list(self.fail_next.items()):
            method, pattern = key.split(" ", 1)
            if n > 0 and method == request.method and re.search(pattern, path):
                self.fail_next[key] = n - 1
                return httpx.Response(503, json={"type": "error", "message": "down"})
        m = request.method
        for rx, fn in self.routes():
            match = re.fullmatch(rx, path)
            if match and fn[0] == m:
                return fn[1](params, body, *match.groups())
        return self.not_found()

    def routes(self):
        return [
            (r"/user", ("GET", lambda p, b: self.ok(self.me))),
            (r"/user/all", ("GET", lambda p, b: self.ok(self.users))),
            (r"/customers", ("GET", lambda p, b: self.page(
                self.filtered(list(self.customers.values()), p), p))),
            (r"/customers_new", ("POST", self.create_customer)),
            (r"/customers/([^/]+)", ("GET", lambda p, b, u: self.get(self.customers, u))),
            (r"/customers/([^/]+)", ("PUT", self.update_customer)),
            (r"/customers/([^/]+)", ("DELETE", lambda p, b, u: self.soft_delete(
                self.customers, u))),
            (r"/customers/([^/]+)/recover", ("POST", lambda p, b, u: self.recover(
                self.customers, u))),
            (r"/jobs/category", ("GET", lambda p, b: self.ok(self.category_rows()))),
            (r"/jobs/category", ("POST", self.create_category)),
            (r"/jobs/status/([^/]+)", ("GET", lambda p, b, c: self.status_list(c))),
            (r"/jobs/status_new/([^/]+)", ("GET", lambda p, b, c: self.ok(
                self.statuses.get(c, []) if "status_new" in self.statuses_listed_at else []))),
            (r"/jobs/status_new/([^/]+)", ("POST", self.create_status)),
            (r"/jobs/status", ("POST", lambda p, b: self.create_status(
                p, b, (b.get("job_status") or b).get("category_uid") if isinstance(b, dict)
                else None, plain=True))),
            (r"/jobs/status/([^/]+)/([^/]+)", ("PUT", self.rename_status)),
            (r"/jobs", ("GET", lambda p, b: self.page(
                self.filtered(list(self.jobs.values()), p), p))),
            (r"/jobs", ("POST", self.create_job)),
            (r"/jobs", ("PUT", self.update_job)),
            (r"/jobs/([^/]+)", ("GET", lambda p, b, u: self.get(self.jobs, u))),
            (r"/jobs/([^/]+)/delete", ("DELETE", lambda p, b, u: self.soft_delete(self.jobs, u))),
            (r"/jobs/([^/]+)/recover", ("POST", lambda p, b, u: self.recover(self.jobs, u))),
            (r"/jobs/([^/]+)/status", ("PUT", lambda p, b, u: self.move(u, b, back=False))),
            (r"/jobs/([^/]+)/status/rollback", ("PUT", lambda p, b, u: self.move(u, b, back=True))),
            (r"/jobs/([^/]+)/note", ("GET", lambda p, b, u: self.children(self.notes, u))),
            (r"/jobs/([^/]+)/note", ("POST", self.create_note)),
            (r"/jobs/([^/]+)/note/([^/]+)", ("PUT", self.update_note)),
            (r"/jobs/([^/]+)/note/([^/]+)", ("DELETE", lambda p, b, j, n: self.drop_child(
                self.notes, j, n, "note_uid"))),
            (r"/jobs/([^/]+)/service_tasks", ("GET", lambda p, b, u: self.children(self.tasks, u))),
            (r"/jobs/([^/]+)/service_tasks", ("POST", self.create_task)),
            (r"/jobs/([^/]+)/service_tasks/([^/]+)", ("PUT", self.update_task)),
            (r"/jobs/([^/]+)/service_tasks/([^/]+)", ("DELETE", lambda p, b, j, t: self.drop_child(
                self.tasks, j, t, "service_task_uid"))),
            (r"/jobs/([^/]+)/attachments", ("GET", lambda p, b, u: self.ok(
                self.attachments.get(u, [])))),
            (r"/appointments", ("GET", lambda p, b: self.page(
                self.filtered(list(self.appointments.values()), p), p))),
            (r"/appointments", ("POST", self.create_appointment)),
            (r"/appointments/([^/]+)", ("GET", lambda p, b, u: self.get(self.appointments, u))),
            (r"/appointments/([^/]+)", ("PUT", self.update_appointment)),
            (r"/appointments/([^/]+)", ("DELETE", lambda p, b, u: self.hard_delete(
                self.appointments, u))),
            (r"/estimate", ("GET", lambda p, b: self.page(
                self.filtered(list(self.estimates.values()), p), p))),
            (r"/estimate/([^/]+)", ("GET", lambda p, b, u: self.get(self.estimates, u))),
            (r"/invoice", ("GET", lambda p, b: self.page(
                self.filtered(list(self.invoices.values()), p), p))),
            (r"/invoice/([^/]+)", ("GET", lambda p, b, u: self.get(self.invoices, u))),
            (r"/settings/custom_fields", ("GET", lambda p, b: self.not_found()
                                          if self.custom_field_defs is None else self.ok(
                self.custom_field_defs.get(p.get("module", ""), [])))),
            (r"/settings/lead_sources", ("GET", lambda p, b: self.not_found()
                                         if self.lead_sources is None else self.ok(
                self.lead_sources))),
            (r"/service/notifications/webhook", ("GET", lambda p, b: self.ok(self.webhooks)
                                                 if self.webhooks_listable
                                                 else self.not_found())),
            (r"/webhook", ("POST", self.create_webhook)),
        ]

    # ------------------------------------------------------------------ records

    def get(self, store: dict, uid: str) -> httpx.Response:
        rec = store.get(uid)
        if rec is None:
            return self.not_found()
        return self.ok(rec)

    def soft_delete(self, store: dict, uid: str) -> httpx.Response:
        rec = store.get(uid)
        if rec is None or rec.get("is_deleted"):
            return self.not_found()
        rec["is_deleted"] = True
        rec["updated_at"] = self.tick()
        return self.ok(message="deleted")

    def recover(self, store: dict, uid: str) -> httpx.Response:
        rec = store.get(uid)
        if rec is None:
            return self.not_found()
        rec["is_deleted"] = False
        rec["updated_at"] = self.tick()
        return self.ok(message="recovered")

    def hard_delete(self, store: dict, uid: str) -> httpx.Response:
        if store.pop(uid, None) is None:
            return self.not_found()
        return self.ok(message="deleted")

    def create_customer(self, params, body) -> httpx.Response:
        data = dict(body["customer"])
        uid = self.uid("cus")
        data.update(customer_uid=uid, created_at=self.tick(), updated_at=iso(self.clock),
                    is_deleted=False, updated_by={"user_uid": "u-sync"})
        self.customers[uid] = data
        return self.ok({"customer_uid": uid})

    def update_customer(self, params, body, uid) -> httpx.Response:
        rec = self.customers.get(uid)
        if rec is None:
            return self.not_found()
        rec.update(body["customer"])
        rec["updated_at"] = self.tick()
        rec["updated_by"] = {"user_uid": "u-sync"}
        return self.ok(message="updated")

    def create_webhook(self, params, body) -> httpx.Response:
        hook = dict(body["web_hook"])
        pair = (hook.get("webhook_module"), hook.get("webhook_event"))
        if self.webhook_events is not None and pair not in self.webhook_events:
            return self.error("Invalid webhook_event %s for module %s" % (pair[1], pair[0]))
        if not self.keeps_webhook_headers:
            hook.pop("headers", None)
        uid = self.uid("whk")
        hook.update(webhook_uid=uid, is_active=True)
        self.webhooks.append(hook)
        return self.ok({"webhook_uid": uid})

    def create_category(self, params, body) -> httpx.Response:
        # Live Zuper (2026-09-17) refused the wrapped body with "Category Name Missing".
        if not body.get("category_name"):
            return self.error("Category Name Missing")
        uid = self.uid("cat")
        self.categories[uid] = {"category_uid": uid, "category_name": body["category_name"]}
        self.statuses[uid] = []
        return self.ok({"category_uid": uid})

    def status_list(self, cat: str) -> httpx.Response:
        rows = self.statuses.get(cat, []) if "status" in self.statuses_listed_at else []
        if not self.status_list_readable:
            return self.ok({"_id": "x"})
        if self.status_list_wrapped:
            return self.ok({"_id": "x", "job_statuses": rows})
        return self.ok(rows)

    def category_rows(self) -> list[dict]:
        rows = []
        if self.template_category_with_statuses:
            rows.append({"category_uid": "cat-template", "category_name": "Roof Inspection",
                         "job_statuses": [{"status_uid": "st-t1", "status_name": "New"}]})
        for c in self.categories.values():
            row = dict(c)
            if self.category_embeds_statuses:
                row["job_statuses"] = [dict(x) for x in self.statuses.get(c["category_uid"], [])]
            rows.append(row)
        return rows

    def create_status(self, params, body, cat, plain: bool = False) -> httpx.Response:
        form = ("plain" if plain else "list" if isinstance(body, list)
                else "job_statuses" if "job_statuses" in body
                else "job_status" if "job_status" in body else "flat")
        if self.status_create_form is not None and form != self.status_create_form:
            return self.ok(message="Job status updated successfully")      # and makes nothing
        if form == "list":
            fields = body[0]
        elif form == "job_statuses":
            fields = body["job_statuses"][0]
        else:
            fields = body.get("job_status") or body
        if cat not in self.categories:
            return self.not_found()
        if not fields.get("status_name"):
            return self.error("Status Name Missing")
        uid = self.uid("st")
        self.statuses.setdefault(cat, []).append(
            {"status_uid": uid, "status_name": fields["status_name"],
             "status_type": fields.get("status_type")})
        if not self.status_create_echoes_uid:
            return self.ok(message="Status created")
        return self.ok({"status_uid": uid})

    def rename_status(self, params, body, cat, status) -> httpx.Response:
        for s in self.statuses.get(cat, []):
            if s["status_uid"] == status:
                s["status_name"] = body["job_status"]["status_name"]
                return self.ok(message="updated")
        return self.not_found()

    def _job_status(self, job: dict, status_uid: str) -> None:
        cat = job["job_category"]["category_uid"]
        name = next((s["status_name"] for s in self.statuses.get(cat, [])
                     if s["status_uid"] == status_uid), None)
        job["current_job_status"] = {"status_uid": status_uid, "status_name": name}

    def create_job(self, params, body) -> httpx.Response:
        data = dict(body["job"])
        uid = self.uid("job")
        cat = data.pop("job_category", None)
        customer = data.pop("customer", None)
        data.update(job_uid=uid, job_category={"category_uid": cat},
                    customer={"customer_uid": customer} if customer else None,
                    created_at=self.tick(), updated_at=iso(self.clock), is_deleted=False,
                    updated_by={"user_uid": "u-sync"})
        statuses = self.statuses.get(cat or "", [])
        if statuses:
            self._job_status(data, statuses[0]["status_uid"])
        self.jobs[uid] = data
        return self.ok({"job_uid": uid})

    def update_job(self, params, body) -> httpx.Response:
        data = dict(body["job"])
        uid = data.pop("job_uid")
        job = self.jobs.get(uid)
        if job is None:
            return self.not_found()
        if "job_category" in data:
            job["job_category"] = {"category_uid": data.pop("job_category")}
        if "customer" in data:
            customer = data.pop("customer")
            job["customer"] = {"customer_uid": customer} if customer else None
        job.update(data)
        job["updated_at"] = self.tick()
        job["updated_by"] = {"user_uid": "u-sync"}
        return self.ok(message="updated")

    def move(self, uid: str, body: dict, *, back: bool) -> httpx.Response:
        job = self.jobs.get(uid)
        if job is None:
            return self.not_found()
        order = [s["status_uid"] for s in self.statuses.get(
            job["job_category"]["category_uid"], [])]
        target = body.get("status_uid")
        if target not in order:
            return self.error("unknown status")
        current = (job.get("current_job_status") or {}).get("status_uid")
        if current in order:
            if back and order.index(target) > order.index(current):
                return self.error("rollback only moves backwards")
            if not back and order.index(target) < order.index(current):
                return self.error("use the rollback endpoint to move a job backwards")
        self._job_status(job, target)
        job["updated_at"] = self.tick()
        job["updated_by"] = {"user_uid": "u-sync"}
        return self.ok(message="status updated")

    def children(self, store: dict, job_uid: str) -> httpx.Response:
        if job_uid not in self.jobs:
            return self.not_found()
        return self.ok(store.get(job_uid, []))

    def drop_child(self, store: dict, job_uid: str, child_uid: str, key: str) -> httpx.Response:
        rows = store.get(job_uid, [])
        kept = [r for r in rows if r[key] != child_uid]
        if len(kept) == len(rows):
            return self.not_found()
        store[job_uid] = kept
        return self.ok(message="deleted")

    def create_note(self, params, body, job_uid) -> httpx.Response:
        if job_uid not in self.jobs:
            return self.not_found()
        uid = self.uid("note")
        self.notes.setdefault(job_uid, []).append({
            "note_uid": uid, "note": body["note"], "is_private": body.get("is_private"),
            "created_at": self.tick(), "updated_at": iso(self.clock)})
        return self.ok({"note_uid": uid})

    def update_note(self, params, body, job_uid, note_uid) -> httpx.Response:
        for n in self.notes.get(job_uid, []):
            if n["note_uid"] == note_uid:
                n.update(note=body["note"], updated_at=self.tick())
                return self.ok(message="updated")
        return self.not_found()

    def create_task(self, params, body, job_uid) -> httpx.Response:
        if job_uid not in self.jobs:
            return self.not_found()
        uid = self.uid("task")
        self.tasks.setdefault(job_uid, []).append({
            "service_task_uid": uid, **body["service_task"], "updated_at": self.tick()})
        return self.ok({"service_task_uid": uid})

    def update_task(self, params, body, job_uid, task_uid) -> httpx.Response:
        for t in self.tasks.get(job_uid, []):
            if t["service_task_uid"] == task_uid:
                t.update(body["service_task"])
                t["updated_at"] = self.tick()
                return self.ok(message="updated")
        return self.not_found()

    def create_appointment(self, params, body) -> httpx.Response:
        data = dict(body["appointment"])
        if data.get("job_uid") not in self.jobs:
            return self.error("unknown job")
        uid = self.uid("appt")
        data.update(appointment_uid=uid, updated_at=self.tick(), is_deleted=False)
        self.appointments[uid] = data
        return self.ok({"appointment_uid": uid})

    def update_appointment(self, params, body, uid) -> httpx.Response:
        rec = self.appointments.get(uid)
        if rec is None:
            return self.not_found()
        rec.update(body["appointment"])
        rec["updated_at"] = self.tick()
        return self.ok(message="updated")

    def file(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(Seen(request.method, "FILE " + str(request.url), {}, None))
        hit = self.files.get(str(request.url))
        if hit is None:
            return httpx.Response(404)
        return httpx.Response(200, content=hit[0], headers={"content-type": hit[1]})

    # ------------------------------------------------------------------ "a person in Zuper"

    def edit_customer(self, uid: str, **fields) -> dict:
        rec = self.customers[uid]
        rec.update(fields)
        rec["updated_at"] = self.tick()
        rec["updated_by"] = {"user_uid": "u-owner"}
        return rec

    def edit_job(self, uid: str, **fields) -> dict:
        rec = self.jobs[uid]
        rec.update(fields)
        rec["updated_at"] = self.tick()
        rec["updated_by"] = {"user_uid": "u-owner"}
        return rec

    def set_custom(self, record: dict, label: str, value: Any) -> None:
        fields = [f for f in record.get("custom_fields") or [] if f["label"] != label]
        fields.append({"label": label, "value": value})
        record["custom_fields"] = fields
        record["updated_at"] = self.tick()

    def move_job(self, uid: str, status_uid: str) -> None:
        job = self.jobs[uid]
        self._job_status(job, status_uid)
        job["updated_at"] = self.tick()
        job["updated_by"] = {"user_uid": "u-owner"}

    def delete_job(self, uid: str) -> None:
        self.soft_delete(self.jobs, uid)

    def delete_customer(self, uid: str) -> None:
        self.soft_delete(self.customers, uid)

    def new_customer(self, **fields) -> str:
        uid = self.uid("cus")
        self.customers[uid] = {"customer_uid": uid, "created_at": self.tick(),
                               "updated_at": iso(self.clock), "is_deleted": False,
                               "customer_tags": [], "custom_fields": [], **fields}
        return uid

    def new_job(self, category_uid: str, status_uid: str | None = None, **fields) -> str:
        uid = self.uid("job")
        job = {"job_uid": uid, "job_category": {"category_uid": category_uid},
               "created_at": self.tick(), "updated_at": iso(self.clock), "is_deleted": False,
               "custom_fields": [], **fields}
        statuses = self.statuses.get(category_uid, [])
        if status_uid or statuses:
            self._job_status(job, status_uid or statuses[0]["status_uid"])
        self.jobs[uid] = job
        return uid

    def add_note(self, job_uid: str, text: str) -> str:
        uid = self.uid("note")
        self.notes.setdefault(job_uid, []).append({"note_uid": uid, "note": text,
                                                   "updated_at": self.tick()})
        self.jobs[job_uid]["updated_at"] = iso(self.clock)
        return uid

    def add_estimate(self, job_uid: str, *, status: str, total: str, number: str = "1001",
                     customer_uid: str | None = None) -> str:
        uid = self.uid("est")
        self.estimates[uid] = {
            "estimate_uid": uid, "estimate_number": number, "estimate_status": status,
            "total": total, "job": {"job_uid": job_uid},
            "customer": {"customer_uid": customer_uid} if customer_uid else None,
            "estimate_date": "2026-09-10", "expiry_date": "2026-10-10",
            "updated_at": self.tick(), "is_deleted": False}
        return uid

    def add_invoice(self, job_uid: str, *, status: str, total: str, balance: str,
                    number: str = "INV-7") -> str:
        uid = self.uid("inv")
        self.invoices[uid] = {
            "invoice_uid": uid, "invoice_number": number, "invoice_status": status,
            "total": total, "balance_due": balance, "job": {"job_uid": job_uid},
            "invoice_date": "2026-09-12", "due_date": "2026-09-26",
            "updated_at": self.tick(), "is_deleted": False}
        return uid

    def set_document(self, store: dict, uid: str, **fields) -> None:
        store[uid].update(fields)
        store[uid]["updated_at"] = self.tick()

    # ------------------------------------------------------------------ setup that passes

    def configure_account(self) -> FakeZuper:
        """Everything the setup checklist asks the owner to click, done."""
        from app.zuper import mapping
        text = {"field_type": "SINGLE_LINE"}
        self.custom_field_defs = {
            "CUSTOMER": [{"label": label, **text} for label in mapping.CUSTOMER_FIELDS],
            "JOB": [{"label": label, **text} for label in mapping.JOB_CRM_FIELDS] + [
                {"label": label, "field_type": types[0],
                 "field_options": [{"label": o} for o in options]}
                for label, (types, options) in mapping.CHECKLIST_TYPES.items()],
        }
        self.lead_sources = [{"source_uid": "src-%d" % i, "source_name": name}
                             for i, name in enumerate(mapping.ZUPER_LEAD_SOURCES)]
        self.webhooks = [{"webhook_url": "https://crm.dreamteamroofingfl.com/api/zuper/webhook"}]
        return self

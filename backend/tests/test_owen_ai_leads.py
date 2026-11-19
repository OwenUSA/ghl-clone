"""Owen AI's new leads with approval (2026-11-19, app/owen_ai/leads.py).

Pinned, by behaviour:
  * the tool reads Zuper and stores a PENDING row — every request it makes is a GET;
  * a possible duplicate (same email, same name, a CRM contact with the phone) is refused until
    Claude names the customer or says new_customer;
  * Approve sends exactly POST /customers_new, POST /jobs (no status, visit or technician) and
    ONE wrapped private note on that job, reads it back, and tells the thread; with an existing
    customer it sends no customer create; a double approve creates once;
  * flag off or registry Never: approve refuses before any request; reject writes nothing;
  * inside the scope, a PUT, a second job, a note on another job, a status or a technician are
    refused before a connection;
  * anyone but the owner gets 404.
"""
import json
import re

import httpx
import pytest
from app.auth import mint_api_token
from app.main import app
from app.models import (
    Contact,
    OwenAiLeadDraft,
    OwenAiMessage,
    OwenAiThread,
    Role,
    User,
    ZuperMapping,
)
from app.owen_ai import leads, proposals
from app.registry import catalog
from app.zuper import client
from app.zuper.client import ZuperError
from fastapi.testclient import TestClient
from tests.zuper_support import zuper_env  # noqa: F401  (fixture)

OWNER = "owner@dtr.test"
RETAIL = "5939c976-002a-4d80-8bfd-2669a1f8843f"
NEW_LEAD = "st-new-lead"


class FakeZ:
    """Zuper at the HTTP boundary: two boards, a customer store, every request kept."""

    def __init__(self, customers=None, job_status_type="NEW", note_text=None):
        self.requests: list[httpx.Request] = []
        self.customers = {c["customer_uid"]: c for c in (customers or [])}
        self.jobs: dict[str, dict] = {}
        self.notes: dict[str, list] = {}
        self.job_status_type = job_status_type
        self.note_text = note_text

    def ok(self, data):
        return httpx.Response(200, json={"type": "success", "data": data})

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        path = req.url.path.removeprefix("/api")
        if req.method == "GET":
            if path == "/jobs/category":
                return self.ok([{"category_uid": RETAIL, "category_name": "Retail"},
                                {"category_uid": "cat-nonew", "category_name": "Leak repair"}])
            if path == "/jobs/status/" + RETAIL:
                return self.ok({"job_statuses": [
                    {"status_uid": NEW_LEAD, "status_name": "New Lead", "status_type": "NEW"},
                    {"status_uid": "st-2", "status_name": "Contact Attempted",
                     "status_type": "OTHER"}]})
            if path == "/jobs/status/cat-nonew":
                return self.ok({"job_statuses": []})
            if path == "/customers":
                kw = req.url.params.get("filter.keyword", "").lower()

                def hay(c):
                    name = "%s %s" % (c.get("customer_first_name"), c.get("customer_last_name"))
                    return (json.dumps(c) + " " + name).lower()
                return self.ok([c for c in self.customers.values() if kw in hay(c)])
            m = re.match(r"^/customers/([^/]+)$", path)
            if m and m.group(1) in self.customers:
                return self.ok(self.customers[m.group(1)])
            m = re.match(r"^/jobs/([^/]+)/note$", path)
            if m:
                return self.ok(self.notes.get(m.group(1), []))
            m = re.match(r"^/jobs/([^/]+)$", path)
            if m and m.group(1) in self.jobs:
                return self.ok(self.jobs[m.group(1)])
            return httpx.Response(404, json={})
        body = json.loads(req.content or b"{}")
        if req.method == "POST" and path == "/customers_new":
            uid = "cust-%d" % (len(self.customers) + 1)
            self.customers[uid] = {"customer_uid": uid, **body["customer"]}
            return self.ok({"customer_uid": uid})
        if req.method == "POST" and path == "/jobs":
            uid = "job-%d" % (len(self.jobs) + 1)
            j = body["job"]
            self.jobs[uid] = {"job_uid": uid, "work_order_number": 864 + len(self.jobs),
                              "job_category": {"category_uid": j["job_category"]},
                              "customer": {"customer_uid": j["customer_uid"]},
                              "current_job_status": {"status_name": "New Lead",
                                                     "status_type": self.job_status_type},
                              "is_scheduled": False, "is_assigned": False}
            return self.ok({"job_uid": uid})
        m = re.match(r"^/jobs/([^/]+)/note$", path)
        if req.method == "POST" and m:
            text = self.note_text if self.note_text is not None else body["note"]["note"]
            self.notes.setdefault(m.group(1), []).append({"note": text})
            return self.ok({"note_uid": "n-1"})
        return httpx.Response(400, json={"type": "error", "message": "unexpected"})

    def writes(self):
        return [r for r in self.requests if r.method != "GET"]


@pytest.fixture()
def people(db, monkeypatch, zuper_env):  # noqa: F811
    monkeypatch.setenv("OWEN_AI_ENABLED", "true")
    monkeypatch.setenv("OWEN_AI_OWNER_EMAIL", OWNER)
    monkeypatch.setenv("OWEN_AI_LEADS_ENABLED", "true")
    monkeypatch.setenv("ZUPER_PULL_ONLY", "true")          # production's mirror mode
    out = {}
    for key, email, role in (("owner", OWNER, Role.ADMIN), ("admin", "a@dtr.test", Role.ADMIN)):
        u = User(email=email, name=key.title(), role=role)
        db.add(u)
        db.flush()
        plain, tok = mint_api_token(u, name=key)
        db.add(tok)
        out[key] = (u.id, {"Authorization": "Bearer " + plain})
    db.commit()
    return out


def install(**kw) -> FakeZ:
    z = FakeZ(**kw)
    client.TRANSPORT = httpx.MockTransport(z)
    return z


def call(method, path, who, people, **kw):
    return TestClient(app).request(method, "/api/owen-ai" + path, headers=people[who][1], **kw)


def lead(**over):
    s = {"first_name": "Bina", "last_name": "Maison", "phone": "(213) 709-4042",
         "email": "MaisonBina@example.test",
         "address": {"street": "1071 NW 51st St", "city": "Miami", "zip": "33127"},
         "note": "Web form. Roof leaks during heavy rain; wants a written report for insurance."}
    s.update(over)
    return s


def propose(db, people, **over) -> OwenAiLeadDraft:
    th = OwenAiThread(user_id=people["owner"][0], kind="task", title="Leads")
    db.add(th)
    db.commit()
    with proposals.answering(th.id):
        out = leads.tool_propose(lead(**over))
    assert leads.WAITING in out, out
    db.expire_all()
    return db.query(OwenAiLeadDraft).order_by(OwenAiLeadDraft.id.desc()).first()


# ------------------------------------------------------------------------------- the tool

def test_the_tool_reads_zuper_stores_a_pending_lead_and_writes_nothing(db, people):
    z = install()
    row = propose(db, people)
    assert z.requests and all(r.method == "GET" for r in z.requests)
    assert row.status == "pending" and row.board == "Retail" and row.category_uid == RETAIL
    assert row.job_title == "Bina Maison Retail" and row.customer_uid is None
    got = row.spec["lead"]
    assert got["phone"] == "2137094042" and got["email"] == "maisonbina@example.test"
    assert got["address"]["state"] == "FL" and row.spec["first_column"] == "New Lead"


@pytest.mark.parametrize("bad,expect", [
    ({"phone": "555-1234"}, "10-digit"),
    ({"email": "not-an-email"}, "email"),
    ({"address": {"street": "1 Main"}}, "zip or a city"),
    ({"address": {"zip": "331"}}, "5 digits"),
    ({"note": ""}, "note is required"),
    ({"status": "Scheduled"}, "unknown keys"),
    ({"assigned_to": "Antonio"}, "unknown keys"),
    ({"customer_uid": "c", "new_customer": True}, "not both"),
])
def test_a_bad_lead_is_refused_and_nothing_is_read(db, people, bad, expect):
    z = install()
    out = leads.tool_propose(lead(**bad))
    assert out.startswith("ERROR") and expect in out
    assert z.requests == [] and db.query(OwenAiLeadDraft).count() == 0


def test_a_board_without_a_new_column_is_refused(db, people):
    install()
    out = leads.tool_propose(lead(board="Leak repair"))
    assert out.startswith("ERROR") and "no NEW column" in out
    assert db.query(OwenAiLeadDraft).count() == 0


def test_a_possible_duplicate_is_refused_until_claude_names_it(db, people):
    same = {"customer_uid": "c-old", "customer_first_name": "Bina",
            "customer_last_name": "Maison", "customer_email": "other@example.test"}
    install(customers=[same])
    out = leads.tool_propose(lead())
    assert out.startswith("ERROR") and "c-old" in out and "same name" in out
    assert db.query(OwenAiLeadDraft).count() == 0
    # Owen says it is that customer: a new job on the existing one.
    row = propose(db, people, customer_uid="c-old")
    assert row.customer_uid == "c-old" and row.spec["existing_customer"]["customer_uid"] == "c-old"
    # Or a different person.
    assert propose(db, people, new_customer=True).customer_uid is None


def test_a_zuper_customer_with_the_same_phone_is_a_duplicate(db, people):
    install(customers=[{"customer_uid": "c-mob", "customer_first_name": "Other",
                        "customer_contact_no": {"mobile": "+12137094042"}}])
    out = leads.tool_propose(lead(first_name="Someone", last_name="Else", email=""))
    assert out.startswith("ERROR") and "c-mob" in out and "same phone" in out


def test_a_crm_contact_with_the_same_phone_counts_as_a_duplicate(db, people):
    z = install(customers=[{"customer_uid": "c-phone", "customer_first_name": "B",
                            "customer_last_name": "M"}])
    c = Contact(first_name="B", last_name="M", phone="+1 213 709 4042")
    db.add(c)
    db.flush()
    db.add(ZuperMapping(crm_type="contact", crm_id=c.id, zuper_type="customer",
                        zuper_uid="c-phone"))
    db.commit()
    out = leads.tool_propose(lead(first_name="Someone", last_name="Else", email=""))
    assert out.startswith("ERROR") and "c-phone" in out and "same phone" in out
    assert all(r.method == "GET" for r in z.requests)


# ------------------------------------------------------------------------------- approve

def test_approve_creates_customer_job_and_note_and_tells_the_thread(db, people):
    z = install()
    row = propose(db, people)
    r = call("POST", "/leads/%d/approve" % row.id, "owner", people)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["status"] == "created" and got["job_number"] == "864", got
    w = z.writes()
    assert [(q.method, q.url.path.removeprefix("/api")) for q in w] == [
        ("POST", "/customers_new"), ("POST", "/jobs"), ("POST", "/jobs/job-1/note")]
    cust, job, note = (json.loads(q.content) for q in w)
    assert cust["customer"]["customer_contact_no"] == {"mobile": "+12137094042"}
    assert job["job"]["customer_uid"] == "cust-1" and job["job"]["job_category"] == RETAIL
    assert not {"job_status", "status_uid", "assigned_to", "scheduled_start_time"} & set(job["job"])
    assert note == {"note": {"note": lead()["note"], "is_private": True}}
    msgs = [m.content for m in db.query(OwenAiMessage).filter_by(role="system")]
    assert any("APPROVED" in m and "#864" in m for m in msgs)


def test_an_existing_customer_gets_only_a_job_and_a_note(db, people):
    z = install(customers=[{"customer_uid": "c-old", "customer_first_name": "Bina",
                            "customer_last_name": "Maison"}])
    row = propose(db, people, customer_uid="c-old")
    assert call("POST", "/leads/%d/approve" % row.id, "owner", people).json()["status"] \
        == "created"
    assert [q.url.path.removeprefix("/api") for q in z.writes()] == [
        "/jobs", "/jobs/job-1/note"]
    assert json.loads(z.writes()[0].content)["job"]["customer_uid"] == "c-old"


def test_a_double_approve_creates_once(db, people):
    z = install()
    row = propose(db, people)
    assert call("POST", "/leads/%d/approve" % row.id, "owner", people).status_code == 200
    again = call("POST", "/leads/%d/approve" % row.id, "owner", people)
    assert again.status_code == 409 and len(z.writes()) == 3


@pytest.mark.parametrize("fake,expect", [({"job_status_type": "OTHER"}, "first column"),
                                         ({"note_text": ""}, "note is empty")])
def test_a_wrong_read_back_is_failed_not_created(db, people, fake, expect):
    install(**fake)
    row = propose(db, people)
    got = call("POST", "/leads/%d/approve" % row.id, "owner", people).json()
    assert got["status"] == "failed" and expect in got["error"]


def test_a_zuper_refusal_says_what_already_exists(db, people):
    z = install()
    row = propose(db, people)
    real = z.__call__

    def refuse_jobs(req):
        if req.method == "POST" and req.url.path.endswith("/jobs"):
            z.requests.append(req)
            return httpx.Response(400, json={"type": "error", "message": "Bad job"})
        return real(req)

    client.TRANSPORT = httpx.MockTransport(refuse_jobs)
    got = call("POST", "/leads/%d/approve" % row.id, "owner", people).json()
    assert got["status"] == "failed" and "customer cust-1" in got["error"]
    assert len(z.writes()) == 2          # customer + the refused job; never retried, no note


@pytest.mark.parametrize("how", ["flag", "registry"])
def test_flag_off_or_registry_never_refuses_before_any_request(db, people, monkeypatch, how):
    z = install()
    row = propose(db, people)
    before = len(z.requests)
    if how == "flag":
        monkeypatch.setenv("OWEN_AI_LEADS_ENABLED", "false")
    else:
        from app.registry import service
        monkeypatch.setattr(service, "level", lambda db, key, action: catalog.NEVER)
    r = call("POST", "/leads/%d/approve" % row.id, "owner", people)
    assert r.status_code == 409 and len(z.requests) == before
    db.expire_all()
    assert db.get(OwenAiLeadDraft, row.id).status == "pending"


def test_reject_writes_nothing_to_zuper_and_tells_the_thread(db, people):
    z = install()
    row = propose(db, people)
    before = len(z.requests)
    r = call("POST", "/leads/%d/reject" % row.id, "owner", people, json={"why": "spam"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert len(z.requests) == before
    assert any("REJECTED" in m.content for m in db.query(OwenAiMessage).filter_by(role="system"))


def test_only_the_owner_sees_the_routes(db, people):
    install()
    row = propose(db, people)
    for method, path in (("GET", "/leads"), ("POST", "/leads/%d/approve" % row.id)):
        assert call(method, path, "admin", people).status_code == 404
    listed = call("GET", "/leads", "owner", people).json()
    assert listed["pending"] == 1 and listed["enabled"] is True


# ------------------------------------------------------------------------------- the scope

@pytest.mark.parametrize("method,path,body", [
    ("PUT", "/jobs", {"job": {"job_uid": "x"}}),
    ("PUT", "/jobs/x/status", {"status_uid": NEW_LEAD}),
    ("DELETE", "/customers/c-old", None),
    ("POST", "/jobs", {"job": {"job_title": "t", "job_category": RETAIL, "customer_uid": "c-old",
                               "assigned_to": [{"user_uid": "u"}]}}),
    ("POST", "/jobs", {"job": {"job_title": "t", "job_category": RETAIL, "customer_uid": "c-old",
                               "job_status": NEW_LEAD}}),
    ("POST", "/jobs", {"job": {"job_title": "t", "job_category": RETAIL,
                               "customer_uid": "someone-else"}}),
    ("POST", "/jobs/other-job/note", {"note": {"note": "x", "is_private": True}}),
    ("POST", "/customers_new", {"customer": {"customer_first_name": "x"}}),
])
def test_inside_the_scope_anything_else_is_refused_before_a_connection(db, people, method,
                                                                       path, body):
    z = install()
    with client.owen_ai_lead_create(customer_uid="c-old"), pytest.raises(ZuperError) as exc:
        client.request(method, path, body=body)
    assert exc.value.kind == "refused" and z.requests == []


def test_the_scope_allows_one_job_then_one_note_on_it_only(db, people):
    z = install(customers=[{"customer_uid": "c-old", "customer_first_name": "B"}])
    job = {"job": {"job_title": "t", "job_category": RETAIL, "customer_uid": "c-old"}}
    with client.owen_ai_lead_create(customer_uid="c-old"):
        client.request("POST", "/jobs", body=job)
        with pytest.raises(ZuperError):
            client.request("POST", "/jobs", body=job)          # a second job
        client.owen_ai_lead_created("job", "job-1")
        client.request("POST", "/jobs/job-1/note", body={"note": {"note": "x",
                                                                  "is_private": True}})
        with pytest.raises(ZuperError):
            client.request("POST", "/jobs/job-1/note", body={"note": {"note": "y",
                                                                      "is_private": True}})
    assert len(z.writes()) == 2


def test_outside_the_scope_pull_only_still_refuses_the_same_writes(db, people):
    z = install()
    with pytest.raises(ZuperError):
        client.request("POST", "/customers_new", body={"customer": {"customer_first_name": "x"}})
    assert z.requests == []

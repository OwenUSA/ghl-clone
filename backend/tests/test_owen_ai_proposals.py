"""Owen AI's proposal drafts with approval (2026-11-09, app/owen_ai/proposals.py).

Pinned, by behaviour:
  * the tool reads Zuper and stores a PENDING row — every request it makes is a GET;
  * Approve sends exactly ONE POST /estimate (is_proposal, no status/send/email/option_image),
    reads it back and records the DRAFT; the fill path sends ONE PUT /estimate/{uid} for the
    recorded uid only; a double approve creates once;
  * flag off or registry Never: approve refuses before any request; reject writes nothing;
  * inside the scope, /send, /email, another uid, a status, a picture or a query string are
    refused before a connection;
  * anyone but the owner gets 404.
"""
import json
import re

import httpx
import pytest
from app.auth import mint_api_token
from app.main import app
from app.models import OwenAiMessage, OwenAiProposalDraft, OwenAiThread, Role, User
from app.owen_ai import proposals, runner
from app.registry import catalog
from app.zuper import client
from app.zuper.client import ZuperError
from fastapi.testclient import TestClient
from tests.zuper_support import zuper_env  # noqa: F401  (fixture)

OWNER = "owner@dtr.test"
JOB = "6abd527f-31a6-43e9-bb0a-6fe557dac596"
CUST = "2dbd7187-a6e8-4452-8600-c3d029599d75"
TILE = proposals.TEMPLATES["short"]    # the owner's Short Proposal template, used for every draft
PRODUCTS = {"GOOD": ("27e6e30d-ca23-4f92-a61b-f1f67a5402f5", "DTR-CUSTOM-GOOD", 750),
            "BETTER": ("38af9766-a840-43a8-a865-e18b5ecd18b3", "DTR-CUSTOM-BETTER", 815),
            "BEST": ("ac78d138-ab66-4781-814d-9e95662318b4", "DTR-TILE-BEST", 2000)}
PLACEHOLDER = ("<p><strong>Scope of Work</strong></p>\n<ul>\n<li>(write the scope for this "
               "job)</li>\n</ul>\n<p>Workmanship Warranty: (state warranty)</p>")


def template() -> dict:
    opts = []
    for name, (uid, code, price) in PRODUCTS.items():
        opts.append({"option_name": name, "option_description": "x", "option_image": "",
                     "is_recommended": name == "BEST",
                     "line_items": [{"line_item_type": "ITEM", "product_name": code,
                                     "product": {"product_uid": uid, "product_id": code,
                                                 "product_name": name + " - package",
                                                 "price": price, "product_type": "SERVICE",
                                                 "uom": "Job",
                                                 "product_description": PLACEHOLDER}}]})
    return {"template_uid": TILE,
            "template_name": "DTR - Tile Leak Repair - Short Proposal (Good / Better / Best)",
            "layout_template_uid": "f1432264-7dcb-4906-bdb9-ba9845eae6c0",   # DTR Proposal - Short
            "proposal_options": opts}


def auto_draft(uid="auto-1", no=150, status="DRAFT", tpl=TILE) -> dict:
    """What the CPQ trigger leaves: template products at their default prices."""
    return {"estimate_uid": uid, "estimate_no": no, "prefix": "DTR-", "estimate_status": status,
            "is_proposal": True, "proposal_template": {"template_uid": tpl},
            "job": {"job_uid": JOB, "work_order_number": 701}, "created_at": "2026-11-08",
            "proposal_options": [
                {"option_uid": "ou-" + n.lower(), "option_name": n, "total": p,
                 "line_items": [{"product_uid": u, "product_id": c, "unit_price": p, "total": p,
                                 "description": PLACEHOLDER}]}
                for n, (u, c, p) in PRODUCTS.items()]}


class FakeZ:
    """Zuper at the HTTP boundary: the job #701 world, an estimate store, every request kept."""

    def __init__(self, existing=None):
        self.requests: list[httpx.Request] = []
        self.estimates = {e["estimate_uid"]: e for e in (existing or [])}
        self.next_no = 160

    def ok(self, data):
        return httpx.Response(200, json={"type": "success", "data": data})

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        path = req.url.path.removeprefix("/api")
        if req.method == "GET":
            if path == "/jobs":
                return self.ok([{"job_uid": JOB, "work_order_number": 701}])
            if path == "/jobs/" + JOB:
                return self.ok({"job_uid": JOB, "work_order_number": 701,
                                "customer": {"customer_uid": CUST,
                                             "customer_first_name": "Santiago",
                                             "customer_last_name": "Villahermosa (TEST)"},
                                "customer_address": {"street": "TEST - 100 Test St",
                                                     "city": "Bradenton", "state": "FL",
                                                     "email": "nope@x.test"},
                                "property": {"property_uid": "prop-1"}})
            if path == "/customers/" + CUST:
                return self.ok({"customer_uid": CUST, "customer_email": "cust@dtr.test",
                                "customer_address": {"street": "TEST - 100 Test St",
                                                     "city": "Bradenton", "state": "FL",
                                                     "zip_code": "34205"}})
            if path == "/invoice_estimate/proposal_template/" + TILE:
                return self.ok(template())
            if path == "/estimate":
                return self.ok([{k: e[k] for k in ("estimate_uid", "estimate_no",
                                                   "estimate_status", "created_at")}
                                for e in self.estimates.values()])
            m = re.match(r"^/estimate/([^/]+)$", path)
            if m and m.group(1) in self.estimates:
                return self.ok(self.estimates[m.group(1)])
            return httpx.Response(404, json={})
        body = json.loads(req.content or b"{}")
        if req.method == "POST" and path == "/estimate":
            uid = "new-%d" % self.next_no
            self.estimates[uid] = self._record(uid, self.next_no, body["estimate"])
            self.next_no += 1
            return self.ok({"estimate_uid": uid})
        m = re.match(r"^/estimate/([^/]+)$", path)
        if req.method == "PUT" and m and m.group(1) in self.estimates:
            old = self.estimates[m.group(1)]
            self.estimates[m.group(1)] = self._record(m.group(1), old["estimate_no"],
                                                      body["estimate"])
            return self.ok({})
        return httpx.Response(400, json={"type": "error", "message": "unexpected"})

    @staticmethod
    def _record(uid, no, est):
        return {"estimate_uid": uid, "estimate_no": no, "prefix": "DTR-",
                "estimate_status": "DRAFT", "is_proposal": True,
                "proposal_template": {"template_uid": est.get("proposal_template") or TILE},
                "job": {"job_uid": JOB}, "created_at": "2026-11-09",
                "proposal_options": [
                    {"option_name": o["option_name"],
                     "total": sum(li["total"] for li in o.get("line_items", [])),
                     "line_items": o.get("line_items", [])} for o in est["proposal_options"]]}

    def writes(self):
        return [r for r in self.requests if r.method != "GET"]


@pytest.fixture()
def people(db, monkeypatch, zuper_env):  # noqa: F811
    monkeypatch.setenv("OWEN_AI_ENABLED", "true")
    monkeypatch.setenv("OWEN_AI_OWNER_EMAIL", OWNER)
    monkeypatch.setenv("OWEN_AI_PROPOSALS_ENABLED", "true")
    out = {}
    for key, email, role in (("owner", OWNER, Role.ADMIN), ("admin", "a@dtr.test", Role.ADMIN),
                             ("dispatcher", "d@dtr.test", Role.DISPATCHER)):
        u = User(email=email, name=key.title(), role=role)
        db.add(u)
        db.flush()
        plain, tok = mint_api_token(u, name=key)
        db.add(tok)
        out[key] = (u.id, {"Authorization": "Bearer " + plain})
    db.commit()
    return out


def install(existing=None) -> FakeZ:
    z = FakeZ(existing)
    client.TRANSPORT = httpx.MockTransport(z)
    return z


def call(method, path, who, people, **kw):
    return TestClient(app).request(method, "/api/owen-ai" + path, headers=people[who][1], **kw)


def spec(**over):
    s = {"job": "701", "template": "tile", "title": "Active Leak Repair - TEST - 100 Test St",
         "note": "From the #701 pictures.",
         "options": [
             {"name": "GOOD", "title": "Targeted vent repair", "price": 650,
              "warranty": "30 days on the vent flashing",
              "scope_html": "<p><b>Scope of Work</b></p><ul><li>Remove tiles</li>"
                            "<li onclick='x'>Reset <script>x</script></li></ul>"},
             {"name": "BETTER", "title": "Vent + roof edge", "price": 1450,
              "warranty": "Workmanship Warranty: 6 months", "scope_html": "<ul><li>More</li></ul>"},
             {"name": "BEST", "title": "Complete repair", "price": 3000, "deposit": 1000,
              "warranty": "1 year", "scope_html": "<ul><li>Everything</li></ul>"}]}
    s.update(over)
    return s


def thread(db, people) -> OwenAiThread:
    th = OwenAiThread(user_id=people["owner"][0], kind="job", title="Job #701", job_uid=JOB)
    db.add(th)
    db.commit()
    return th


def propose(db, people, z, **over) -> OwenAiProposalDraft:
    th = thread(db, people)
    with proposals.answering(th.id):
        out = proposals.tool_propose(spec(**over))
    assert proposals.WAITING in out, out
    db.expire_all()
    return db.query(OwenAiProposalDraft).order_by(OwenAiProposalDraft.id.desc()).first()


# ------------------------------------------------------------------------------- the tool

def test_the_tool_reads_zuper_stores_a_pending_draft_and_writes_nothing(db, people):
    z = install()
    row = propose(db, people, z)
    assert z.requests and all(r.method == "GET" for r in z.requests)
    assert row.status == "pending" and row.target == "new" and row.job_number == "701"
    assert row.customer_name == "Santiago Villahermosa (TEST)" and row.template_uid == TILE
    good, better, best = row.options
    # Products from the TEMPLATE; deposit 50% unless given; BEST recommended by default.
    assert good["product"]["code"] == "DTR-CUSTOM-GOOD"
    assert best["product"]["code"] == "DTR-TILE-BEST"
    assert good["deposit"] == 325 and best["deposit"] == 1000 and best["is_recommended"]
    # HTML escaped except the allowed tags; warranty and payment terms added when missing.
    assert "<script>" not in good["scope_html"] and "&lt;script&gt;" in good["scope_html"]
    assert "onclick" not in good["scope_html"].replace("&lt;li onclick", "")
    assert "<p>Workmanship Warranty: 30 days on the vent flashing</p>" in good["scope_html"]
    assert "Payment Terms: 50% Upfront ($325)" in good["scope_html"]
    assert better["scope_html"].count("Workmanship Warranty") == 1
    assert "Deposit $1,000" in best["scope_html"]


@pytest.mark.parametrize("bad,expect", [
    ({"options": []}, "1 to 3"),
    ({"options": [{"name": "GOOD", "title": "t", "price": -1, "warranty": "w",
                   "scope_html": "s"}]}, "price"),
    ({"options": [{"name": "GOOD", "title": "t", "price": "100", "warranty": "w",
                   "scope_html": "s"}]}, "price"),
    ({"options": [{"name": "GOOD", "title": "t", "price": 1, "warranty": "w", "scope_html": "s",
                   "option_image": "x.jpg"}]}, "unknown keys"),
    ({"template": "gutters"}, "template"),
    ({"product": "DTR-X"}, "unknown keys"),
])
def test_a_bad_spec_is_refused_with_every_problem_and_nothing_is_read(db, people, bad, expect):
    z = install()
    out = proposals.tool_propose(spec(**bad))
    assert out.startswith("ERROR") and expect in out
    assert z.requests == [] and db.query(OwenAiProposalDraft).count() == 0


def test_an_empty_auto_draft_is_filled_instead_of_making_a_new_one(db, people):
    z = install([auto_draft()])
    row = propose(db, people, z)
    assert row.target == "fill:auto-1" and row.spec["fill"]["estimate_number"] == "DTR-150"


def test_a_draft_someone_wrote_is_not_filled(db, people):
    written = auto_draft()
    written["proposal_options"][0]["line_items"][0]["description"] = "<p>Luis wrote this</p>"
    written["proposal_options"][0]["line_items"][0]["total"] = 900
    z = install([written])
    assert propose(db, people, z).target == "new"


def test_a_sent_proposal_on_the_same_template_refuses_unless_allow_second(db, people):
    z = install([auto_draft(uid="sent-1", no=149, status="AWAIT_RESPONSE")])
    th = thread(db, people)
    out = proposals.tool_propose(spec())
    assert out.startswith("ERROR") and "DTR-149" in out and "allow_second" in out
    assert db.query(OwenAiProposalDraft).count() == 0
    with proposals.answering(th.id):
        assert proposals.WAITING in proposals.tool_propose(spec(allow_second=True))
    assert all(r.method == "GET" for r in z.requests)


# ------------------------------------------------------------------------------- approve

def test_approve_sends_one_post_reads_it_back_and_tells_the_thread(db, people):
    z = install()
    row = propose(db, people, z)
    before = len(z.requests)
    r = call("POST", "/proposals/%d/approve" % row.id, "owner", people)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "created" and r.json()["estimate_number"] == "DTR-160"
    writes = z.writes()
    assert [(w.method, w.url.path) for w in writes] == [("POST", "/api/estimate")]
    assert writes[0].url.query == b""
    body = json.loads(writes[0].content)
    est = body["estimate"]
    assert est["is_proposal"] is True and est["job"] == JOB and est["proposal_template"] == TILE
    text = json.dumps(body).lower()
    for word in ("estimate_status", "option_image", "send", "notify", "subject"):
        assert word not in text, word
    assert [s["type"] for s in est["signatures"]] == ["CUSTOMER", "INTERNAL"]
    assert "email" not in json.dumps(est["customer_service_address"])
    assert [o["deposit"] for o in est["proposal_options"]] == [325, 725, 1000]
    assert [o["line_items"][0]["product_id"] for o in est["proposal_options"]] == [
        "DTR-CUSTOM-GOOD", "DTR-CUSTOM-BETTER", "DTR-TILE-BEST"]
    after = z.requests[before:]
    assert after[-1].method == "GET" and after[-1].url.path == "/api/estimate/new-160"
    note = db.query(OwenAiMessage).filter_by(thread_id=row.thread_id, role="system").one()
    assert "APPROVED" in note.content and "DTR-160" in note.content


def test_the_fill_path_puts_only_to_the_recorded_uid(db, people):
    z = install([auto_draft()])
    row = propose(db, people, z)
    r = call("POST", "/proposals/%d/approve" % row.id, "owner", people)
    assert r.json()["status"] == "created", r.json()
    writes = z.writes()
    assert [(w.method, w.url.path) for w in writes] == [("PUT", "/api/estimate/auto-1")]
    est = json.loads(writes[0].content)["estimate"]
    assert est["estimate_uid"] == "auto-1" and est["is_proposal"] is True
    assert est["proposal_options"][0]["option_uid"] == "ou-good"
    assert r.json()["estimate_number"] == "DTR-150"


def test_a_double_approve_creates_once(db, people):
    z = install()
    row = propose(db, people, z)
    assert call("POST", "/proposals/%d/approve" % row.id, "owner", people).status_code == 200
    again = call("POST", "/proposals/%d/approve" % row.id, "owner", people)
    assert again.status_code == 409 and "created" in again.json()["detail"]
    assert len(z.writes()) == 1


def test_a_failed_read_back_is_failed_not_created(db, people):
    z = install()
    row = propose(db, people, z)
    real = z._record
    z._record = lambda uid, no, est: {**real(uid, no, est), "estimate_status": "AWAIT_RESPONSE"}
    r = call("POST", "/proposals/%d/approve" % row.id, "owner", people).json()
    assert r["status"] == "failed" and "not DRAFT" in r["error"] and r["estimate_uid"]


def test_a_zuper_refusal_is_failed_with_its_message_and_never_retried(db, people):
    z = install()
    row = propose(db, people, z)
    z.estimates.clear()

    def refuse(req):
        z.requests.append(req)
        if req.method == "POST":
            return httpx.Response(400, json={"type": "error", "message": "Proposal Title Missing"})
        return FakeZ.__call__(z, req)
    client.TRANSPORT = httpx.MockTransport(refuse)
    r = call("POST", "/proposals/%d/approve" % row.id, "owner", people).json()
    assert r["status"] == "failed" and "Proposal Title Missing" in r["error"]
    assert len([q for q in z.requests if q.method == "POST"]) == 1


@pytest.mark.parametrize("how", ["flag", "registry"])
def test_flag_off_or_registry_never_refuses_before_any_request(db, people, monkeypatch, how):
    z = install()
    row = propose(db, people, z)
    if how == "flag":
        monkeypatch.setenv("OWEN_AI_PROPOSALS_ENABLED", "false")
    else:
        from app.registry import service
        monkeypatch.setattr(service, "level", lambda db, key, action: catalog.NEVER)
    before = len(z.requests)
    r = call("POST", "/proposals/%d/approve" % row.id, "owner", people)
    assert r.status_code == 409
    assert ("OWEN_AI_PROPOSALS_ENABLED" if how == "flag" else "Never") in r.json()["detail"]
    assert len(z.requests) == before
    db.refresh(row)
    assert row.status == "pending"


def test_reject_writes_nothing_to_zuper_and_tells_the_thread(db, people):
    z = install()
    row = propose(db, people, z)
    before = len(z.requests)
    r = call("POST", "/proposals/%d/reject" % row.id, "owner", people, json={"why": "Too high"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert len(z.requests) == before
    assert call("POST", "/proposals/%d/approve" % row.id, "owner", people).status_code == 409
    note = db.query(OwenAiMessage).filter_by(thread_id=row.thread_id, role="system").one()
    assert "REJECTED" in note.content and "Too high" in note.content


def test_the_next_message_carries_the_outcome_to_claude(db, people, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-test")
    z = install()
    row = propose(db, people, z)
    call("POST", "/proposals/%d/reject" % row.id, "owner", people, json={"why": "Too high"})
    db.add(OwenAiMessage(thread_id=row.thread_id, role="user", content="ok?", status="queued"))
    db.commit()
    seen = []

    def turn(prompt, session, note):
        from app.owen_ai import agent
        seen.append(prompt)
        return agent.TurnResult(text="noted", session_id="s")
    runner.run_once(db, turn)
    assert "REJECTED" in seen[0] and seen[0].endswith("ok?")


# ---------------------------------------------------------------------------- the scope

GOOD_BODY = {"estimate": {"is_proposal": True, "proposal_title": "t",
                          "proposal_options": [{"option_name": "GOOD"}]}}


@pytest.mark.parametrize("method,path,body,params", [
    ("POST", "/estimate/x1/send", GOOD_BODY, None),
    ("POST", "/estimate/x1/send", {"template_uid": "t", "email": "a@b.c"}, None),
    ("POST", "/email", GOOD_BODY, None),
    ("PUT", "/estimate/x1/status", {"estimate_status": "AWAIT_RESPONSE"}, None),
    ("DELETE", "/estimate/x1", None, None),
    ("PUT", "/estimate/other", {"estimate": {**GOOD_BODY["estimate"],
                                             "estimate_uid": "other"}}, None),
    ("POST", "/estimate", {"estimate": {**GOOD_BODY["estimate"],
                                        "estimate_status": "AWAIT_RESPONSE"}}, None),
    ("POST", "/estimate", {"estimate": {**GOOD_BODY["estimate"], "proposal_options": [
        {"option_name": "GOOD", "option_image": "https://x/banner.jpg"}]}}, None),
    ("POST", "/estimate", {"estimate": {**GOOD_BODY["estimate"], "send_email": True}}, None),
    ("POST", "/estimate", {"estimate": {**GOOD_BODY["estimate"], "notify_customer": True}}, None),
    ("POST", "/estimate", {"estimate": {**GOOD_BODY["estimate"], "email": "a@b.c"}}, None),
    ("POST", "/estimate", {"estimate": {**GOOD_BODY["estimate"], "is_proposal": False}}, None),
    ("POST", "/estimate", GOOD_BODY, {"send_proposal_layout": "true"}),
    ("POST", "/jobs", {"job": {}}, None),
    ("PUT", "/jobs", {"job": {"job_uid": "j"}}, None),
])
def test_inside_the_scope_everything_but_the_two_checked_writes_is_refused(
        zuper_env, monkeypatch, method, path, body, params):  # noqa: F811
    monkeypatch.setenv("OWEN_AI_PROPOSALS_ENABLED", "true")
    z = install()
    with client.owen_ai_proposal_write(fill_uids=["x1"]), pytest.raises(ZuperError) as exc:
        client.request(method, path, body=body, params=params)
    assert exc.value.kind == "refused"
    assert z.requests == []


def test_the_scope_allows_its_two_writes_only_with_the_flag_on(zuper_env, monkeypatch):  # noqa: F811
    z = install([auto_draft(uid="x1")])
    put = {"estimate": {**GOOD_BODY["estimate"], "estimate_uid": "x1"}}
    with client.owen_ai_proposal_write(fill_uids=["x1"]):
        with pytest.raises(ZuperError, match="OWEN_AI_PROPOSALS_ENABLED"):
            client.request("POST", "/estimate", body=GOOD_BODY)
        assert z.requests == []
        monkeypatch.setenv("OWEN_AI_PROPOSALS_ENABLED", "true")
        client.request("PUT", "/estimate/x1", body=put)
        client.request("POST", "/estimate", body={"estimate": {
            **GOOD_BODY["estimate"], "estimate_status": "DRAFT",
            "signatures": [{"email": "a@b.c", "type": "CUSTOMER"}]}})
        client.request("GET", "/invoice_estimate/proposal_template/" + TILE)   # any GET reads
    assert [r.method for r in z.requests] == ["PUT", "POST", "GET"]
    # Outside the scope the estimate writes are denylisted as before.
    with pytest.raises(ZuperError, match="never called"):
        client.request("POST", "/estimate", body=GOOD_BODY)
    assert len(z.requests) == 3


def test_owen_ai_read_still_reads_and_refuses_writes(zuper_env, monkeypatch):  # noqa: F811
    monkeypatch.setenv("OWEN_AI_PROPOSALS_ENABLED", "true")
    z = install()
    with client.owen_ai_read():
        client.request("GET", "/invoice_estimate/proposal_template/" + TILE)
        with pytest.raises(ZuperError):
            client.request("POST", "/estimate", body=GOOD_BODY)
    assert [r.method for r in z.requests] == ["GET"]


def test_the_registry_holds_it_at_approve_each_with_a_never_send():
    e = catalog.BY_KEY["owen_ai"]
    a = e.action("zuper_proposals")
    assert a.default == catalog.APPROVE and set(a.choices) == {catalog.NEVER, catalog.APPROVE}
    assert "Send a proposal to a customer" in e.never
    assert catalog.validate() == []


# ------------------------------------------------------------------------------- access

@pytest.mark.parametrize("who", ["admin", "dispatcher"])
@pytest.mark.parametrize("method,path,body", [
    ("GET", "/proposals", None), ("GET", "/proposals/1", None),
    ("POST", "/proposals/1/approve", None), ("POST", "/proposals/1/reject", {"why": "x"})])
def test_anyone_but_the_owner_gets_404(db, people, who, method, path, body):
    z = install()
    row = propose(db, people, z)
    path = path.replace("/1", "/%d" % row.id)
    assert call(method, path, who, people, json=body).status_code == 404
    db.refresh(row)
    assert row.status == "pending" and z.writes() == []


def test_the_owner_lists_pending_first_with_the_full_preview(db, people):
    z = install()
    first = propose(db, people, z)
    call("POST", "/proposals/%d/reject" % first.id, "owner", people, json={"why": "no"})
    second = propose(db, people, z, template="tile")
    got = call("GET", "/proposals", "owner", people).json()
    assert got["pending"] == 1 and got["proposals"][0]["id"] == second.id
    one = call("GET", "/proposals/%d" % second.id, "owner", people).json()
    assert one["options"][0]["scope_html"]
    assert one["options"][0]["product_code"] == "DTR-CUSTOM-GOOD"
    assert one["target"] == "new" and one["total"] == 5100


def test_a_newer_draft_replaces_an_older_pending_one(db, people):
    z = install()
    old = propose(db, people, z)
    new = propose(db, people, z)
    db.refresh(old)
    assert old.status == "rejected" and "Replaced" in old.error and new.status == "pending"


def test_every_draft_uses_the_short_proposal_template():
    """Owner, 2026-11-09: whatever template Claude names (or none), the draft is on the 5th DTR
    template, 'DTR - Tile Leak Repair - Short Proposal (Good / Better / Best)'."""
    assert proposals.TEMPLATES[proposals.SHORT] == "0e5d788e-656e-40f5-a4af-cb03ffc92fc0"
    base = {"job": "701", "title": "T", "options": [{"name": "GOOD", "title": "G", "price": 600,
                                                    "warranty": "3 months",
                                                    "scope_html": "<ul><li>x</li></ul>"}]}
    for tpl in (None, "tile", "flat", "custom", "replacement", "short"):
        s = dict(base, **({"template": tpl} if tpl else {}))
        assert proposals.validate(s)["template"] == proposals.SHORT


def test_the_default_warranty_is_1_3_6_months():
    """Owner, 2026-11-13: GOOD 1 month, BETTER 3 months, BEST 6 months unless Owen gives another."""
    opts = [{"name": n, "title": n.title(), "price": p, "scope_html": "<ul><li>x</li></ul>"}
            for n, p in (("GOOD", 600), ("BETTER", 1400), ("BEST", 3000))]
    clean = proposals.validate({"job": "701", "title": "T", "options": opts})
    for o, want in zip(clean["options"], ("1 Month ", "3 Months ", "6 Months "), strict=True):
        assert o["warranty"] == proposals.default_warranty(o["name"]) and want in o["scope_html"]
    opts[2]["warranty"] = "Workmanship Warranty: 1 Year"
    best = proposals.validate({"job": "701", "title": "T", "options": opts})["options"][2]
    assert best["warranty"].startswith("Workmanship Warranty: 1 Year")


def test_an_ampersand_is_escaped_once_whatever_claude_sends():
    for raw in ("Prep & Access", "Prep &amp; Access"):
        assert proposals.sanitize("<b>%s</b>" % raw) == "<b>Prep &amp; Access</b>"
    assert proposals.sanitize("<script>x</script>") == "&lt;script&gt;x&lt;/script&gt;"


# ------------------------------------------------------------------------------- changing a draft

def written_draft(uid="w-195", no=195, status="DRAFT", updated="2026-11-13T22:23:00Z"):
    """A proposal a person (or an earlier approve) already wrote: not empty, so never 'filled'."""
    d = auto_draft(uid=uid, no=no, status=status)
    d["updated_at"] = updated
    for o in d["proposal_options"]:
        o["line_items"][0]["description"] = "<p>Maximum Protection Tile Leak Repair</p>"
        o["line_items"][0]["total"] = o["total"] = 3375
    return d


def test_replace_changes_that_draft_in_place_with_one_put_and_no_second_proposal(db, people):
    z = install([written_draft()])
    row = propose(db, people, z, replace="DTR-195")
    assert row.target == "replace:w-195" and row.spec["fill"]["estimate_number"] == "DTR-195"
    assert row.spec["fill"]["before"][0]["total"] == 3375
    assert all(r.method == "GET" for r in z.requests)            # proposing writes nothing
    r = call("POST", "/proposals/%d/approve" % row.id, "owner", people)
    assert r.json()["status"] == "created", r.json()
    assert [(w.method, w.url.path) for w in z.writes()] == [("PUT", "/api/estimate/w-195")]
    est = json.loads(z.writes()[0].content)["estimate"]
    assert est["estimate_uid"] == "w-195" and est["proposal_options"][0]["option_uid"] == "ou-good"
    assert len(z.estimates) == 1                                  # no second proposal
    assert "Tile" not in json.dumps(z.estimates["w-195"])
    note = db.query(OwenAiMessage).filter_by(thread_id=row.thread_id, role="system").one()
    assert "changed in Zuper as DTR-195" in note.content


def test_replace_refuses_a_sent_proposal_before_storing_anything(db, people):
    z = install([written_draft(status="AWAIT_RESPONSE")])
    out = proposals.tool_propose(spec(replace="DTR-195"))
    assert out.startswith("ERROR") and "not a DRAFT" in out
    assert db.query(OwenAiProposalDraft).count() == 0 and z.writes() == []


def test_replace_refuses_when_someone_edited_it_in_zuper_after_owen_ai_read_it(db, people):
    z = install([written_draft()])
    row = propose(db, people, z, replace="DTR-195")
    z.estimates["w-195"]["updated_at"] = "2026-11-18T15:00:00Z"   # Luis edits it in Zuper
    r = call("POST", "/proposals/%d/approve" % row.id, "owner", people)
    assert r.json()["status"] == "failed" and "edited in Zuper" in r.json()["error"]
    assert z.writes() == []


def test_replace_needs_a_proposal_number(db, people):
    z = install([written_draft()])
    out = proposals.tool_propose(spec(replace="the tile one"))
    assert out.startswith("ERROR") and "DTR-195" in out and z.requests == []


def test_read_proposal_returns_every_options_full_text(db, people):
    from app.owen_ai import tools
    long_text = "<p>" + "Remove the loose cedar shakes along the stone wall. " * 40 + "</p>"
    d = written_draft()
    d["proposal_options"][2]["line_items"][0]["description"] = long_text
    install([d])
    out = json.loads(tools.read_proposal("701", "DTR-195"))
    assert out["proposal"] == "DTR-195" and out["can_change"] is True
    assert [o["name"] for o in out["options"]] == ["GOOD", "BETTER", "BEST"]
    assert out["options"][2]["lines"][0]["text"].count("cedar shakes") == 40
    assert "read_proposal" in {name for name, *_ in tools.SPECS}


def test_the_customer_reads_the_options_own_title_not_the_template_placeholder(db, people):
    """DTR-195: the BEST line read "Maximum Protection Tile Leak Repair" on a cedar shake roof,
    and GOOD / BETTER "Custom Package (write scope and price on site)"."""
    z = install()
    row = propose(db, people, z)
    call("POST", "/proposals/%d/approve" % row.id, "owner", people)
    est = json.loads(z.writes()[0].content)["estimate"]
    names = [o["line_items"][0]["name"] for o in est["proposal_options"]]
    assert names == ["GOOD - Targeted vent repair", "BETTER - Vent + roof edge",
                     "BEST - Complete repair"]
    assert [o["line_items"][0]["product_id"] for o in est["proposal_options"]] == [
        "DTR-CUSTOM-GOOD", "DTR-CUSTOM-BETTER", "DTR-TILE-BEST"]       # same products


# ------------------------------------------------------------------------------- wording rules

@pytest.mark.parametrize("name,text,word", [
    ("GOOD", "Install new matching 3-tab shingles over the repaired section.", "new shingles"),
    ("BETTER", "Install fresh waterproof underlayment.", "new underlayment"),
    ("BETTER", "Heavy-duty commercial-grade elastomeric sealant on every seam.", "commercial"),
    ("BEST", "Reinstall tiles, supplying matching replacement tiles for broken pieces.", "REUSE"),
    ("GOOD", "Clean up and run a magnetic sweep.", "magnetic"),
    ("GOOD", "You receive before-and-after photos.", "photos"),
    ("BETTER", "Double sealing of every seam.", "single sealing"),
    ("BETTER", "Replace damaged plywood decking.", "plywood"),
    ("GOOD", "Underlayment Replacement: remove the old underlayment, install new.", "PATCHES"),
    # DTR-247 / 251 (2026-11-26): the patch's material, brand and laying are the technicians' call
    ("GOOD", "Patch the leak with a TU Plus self-adhered membrane patch.", "brand"),
    ("BETTER", "Install premium self-adhered underlayment (TU Plus or equal).", "brand"),
    ("GOOD", "Patch the underlayment with a self-adhered waterproof membrane patch.",
     "patched WITH"),
    ("GOOD", "Seal all edges and laps of the membrane patch with elastomeric sealant.",
     "patched WITH"),
    ("GOOD", ("Patch the failed underlayment at the leak, lapped shingle-style into the "
              "surrounding underlayment so water sheds over it."), "how the patch is laid"),
    # owner, 2026-11-26 (DTR-247..253)
    ("GOOD", "L-Flashing Reset & Reseal: Reset and fasten the L-flashing over the leak area.",
     "not in GOOD"),
    ("GOOD", ("Reinstall the customer's own dimensional shingles, nailed and sealed into the "
              "existing courses."), "Reinstall the customer's own tiles"),
    ("BETTER", "Re-align, interlock and mechanically reinstall the customer's original tiles.",
     "Reinstall the customer's own tiles"),
    ("GOOD", "Lift the roof tiles over the leak area, about 13 ft x 6 ft.", "the leak area"),
    ("BETTER", "Everything in Good across a 2x footprint.", "the expanded area"),
    ("BEST", "Install two full layers of self-adhered underlayment.", "silicone coating"),
    ("BEST", "Double Underlayment Protection System.", "silicone coating"),
])
def test_owners_wording_rules_refuse_the_draft_with_the_reason(db, people, name, text, word):
    """Each one was a correction Owen made by hand on sent proposals (2026-11-16..24)."""
    z = install()
    base = "<p>Reinstall the original tiles.</p>"
    opts = [{"name": n, "title": "t", "price": p, "scope_html": base}
            for n, p in (("GOOD", 500), ("BETTER", 1000), ("BEST", 3000))]
    next(o for o in opts if o["name"] == name)["scope_html"] = "<p>%s</p>" % text
    out = proposals.tool_propose(spec(options=opts))
    assert out.startswith("ERROR") and name in out and word in out
    assert db.query(OwenAiProposalDraft).count() == 0 and z.writes() == []


def test_saying_that_we_patch_and_seal_is_still_fine():
    """The rewrite of DTR-247 (owner, 2026-11-26) passes; so do the bull membrane SEALANT and
    BETTER's self-adhered underlayment - only the GOOD patch's material / brand / laying is out."""
    assert proposals.wording_problems([
        {"name": "GOOD", "scope_html": (
            "<p>3. Underlayment Patch at the Leak: Patch the failed underlayment at the leak so "
            "water sheds over it, not under it.</p><p>4. Perimeter Sealing: Seal the edges of "
            "the patch with bull membrane sealant around the patch.</p>")},
        {"name": "BETTER", "scope_html": "<p>Install high-performance self-adhered peel and stick "
                                         "underlayment, securely lapped into the surrounding roof."
                                         "</p>"},
        {"name": "BEST", "scope_html": (
            "<p>Replace the underlayment across the extended area and apply a silicone coating "
            "along the edges of the underlayment.</p><p>Reinstall the customer's own tiles over "
            "the extended area.</p><p>Apply double sealing over the seams of the underlayment."
            "</p>")}]) == []


def test_a_sentence_saying_what_is_not_included_is_not_a_promise():
    """DTR-160 / 178 / 202 / 206: plain (not italic) 'it does not include ... plywood decking
    replacement' was flagged as promising plywood."""
    scope = ("<p>Inspect the plywood deck. This option repairs the wall corner only; it does not "
             "include the open seam or plywood decking replacement (optional add-on if rotted "
             "decking is found: $500 per sheet).</p>")
    assert proposals.wording_problems([{"name": "GOOD", "scope_html": scope},
                                       {"name": "BEST", "scope_html": "<p>x</p>"}]) == []
    better = {"name": "BETTER", "scope_html": "<p>Reinstall the original tiles.</p>"}
    assert proposals.wording_problems([{"name": "GOOD", "scope_html": scope + "<p>Replace the "
                                        "rotted plywood decking.</p>"}, better])


def test_a_one_option_proposal_may_replace_plywood_but_not_the_rest():
    """#848: Good only, 'replace up to three sheets of plywood' is the job itself."""
    one = [{"name": "GOOD", "scope_html": "<p>Replace up to three sheets of damaged plywood.</p>"}]
    assert proposals.wording_problems(one) == []
    one[0]["scope_html"] += "<p>Run a magnetic sweep.</p>"
    assert any("magnetic" in p for p in proposals.wording_problems(one))
    two = [*one, {"name": "BETTER", "scope_html": "<p>Reinstall the original tiles.</p>"}]
    assert any("plywood" in p for p in proposals.wording_problems(two))


def test_the_not_included_note_and_add_ons_may_name_them(db, people):
    z = install()
    scope = ("<p>Reinstall the original tiles. Patch the underlayment at the leak.</p>"
             "<p><i>This option does not include new shingles, plywood replacement or "
             "photos.</i></p><p>Optional add-ons (not included in the price): matching "
             "replacement tiles $75 each</p>")
    opts = [{"name": n, "title": "t", "price": p, "scope_html": scope}
            for n, p in (("GOOD", 500), ("BETTER", 1000), ("BEST", 3000))]
    th = thread(db, people)
    with proposals.answering(th.id):
        assert proposals.WAITING in proposals.tool_propose(spec(options=opts))
    assert z.writes() == []

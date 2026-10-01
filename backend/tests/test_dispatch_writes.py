"""The Dispatch page, phase 3 (2026-10-01): "let the agent do it" — built, wired, OFF.

What is pinned, by behaviour, against a fake Zuper that records every request:
  * **Off means nothing leaves.** With the defaults, every apply / book / stage answers 409 and
    NO request reaches Zuper. With every switch on but the server gate DISPATCH_ZUPER_WRITES
    unset, the same — and the 409 says why.
  * **Switches:** ADMIN only; turning one ON needs the typed phrase; every flip is logged with
    old -> new; turning off needs no phrase.
  * **All on:** exactly the expected requests, the custom-field write carrying the field's
    group metadata copied from the job; the job is read back and the suggestion is `applied`.
    A read-back that does not show the change is `apply_failed`, and nothing is retried.
  * **The scope is narrow:** inside `client.dispatch_write()` a delete, a new job, a customer
    write, a money document, Zuper Connect are refused before a connection exists.
  * **Stages:** never a closing stage, never another board.
  * A technician or a restricted user is refused 403; a hidden board answers 404.
"""
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.auth import mint_api_token
from app.db import SessionLocal
from app.dispatch import config as dconfig
from app.dispatch import writes
from app.main import app
from app.models import (
    DispatchJob,
    DispatchSettings,
    DispatchSuggestion,
    DispatchWriteLog,
    Pipeline,
    PipelinePermission,
    Role,
    User,
)
from app.zuper import client
from fastapi.testclient import TestClient

ALL_ON = {"writes_enabled": True, "write_fields": True, "write_address": True,
          "write_note": True, "write_booking": True, "write_stage": True}

INSPECTION_STATUSES = [
    {"status_uid": "s-wor", "status_name": "Work Order Received", "status_type": "NEW"},
    {"status_uid": "s-welcome", "status_name": "Welcome Call!", "status_type": "NEW"},
    {"status_uid": "s-sched", "status_name": "Scheduled", "status_type": "NEW"},
    {"status_uid": "s-done", "status_name": "Inspection Completed", "status_type": "COMPLETED"},
    {"status_uid": "s-cancel", "status_name": "Cancelled", "status_type": "CANCELED"},
]
RETAIL_STATUSES = [
    {"status_uid": "r-sent", "status_name": "Estimate Sent", "status_type": "NEW"},
]


def record():
    return {
        "job_uid": "j1", "work_order_number": 701,
        "job_category": {"category_uid": "cat-insp", "category_name": dconfig.INSPECTION_BOARD},
        "current_job_status": {"status_name": "Welcome Call!", "status_type": "NEW"},
        "custom_fields": [
            {"label": "Policy Number", "value": "", "group_name": "AHS Details",
             "group_uid": "g-ahs", "type": "SINGLE_LINE", "hide_to_fe": False},
            {"label": "Technician", "value": "", "group_name": "Visit", "group_uid": "g-visit",
             "type": "DROPDOWN", "read_only": False},
            {"label": "Locked", "value": "x", "group_name": "AHS Details",
             "group_uid": "g-ahs", "type": "SINGLE_LINE", "read_only": True},
        ],
        "customer_address": {"street": "1 Main St", "city": "Miami", "state": "FL",
                             "zip_code": "33101", "geo_cordinates": [25.7, -80.2]},
        "scheduled_start_time": None, "scheduled_end_time": None,
    }


class Zuper:
    """One job, its notes and two boards' statuses. Applies the writes it accepts (unless
    `ignore_writes`), records every request with its body; 418 for anything else."""

    def __init__(self):
        self.job = record()
        self.notes: list[dict] = []
        self.requests: list[tuple[str, str, object]] = []
        self.ignore_writes = False

    def writes(self):
        return [r for r in self.requests if r[0] != "GET"]

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, path, body))
        ok = lambda data: httpx.Response(200, json={"type": "success", "data": data})  # noqa: E731
        m = request.method
        if m == "GET" and path == "/api/jobs/j1":
            return ok(json.loads(json.dumps(self.job)))
        if m == "GET" and path == "/api/jobs/j1/note":
            return ok(self.notes)
        if m == "GET" and path == "/api/jobs/status/cat-insp":
            return ok({"_id": "x", "job_statuses": INSPECTION_STATUSES})
        if m == "GET" and path == "/api/jobs/status/cat-retail":
            return ok({"_id": "y", "job_statuses": RETAIL_STATUSES})
        if m == "PUT" and path == "/api/jobs":
            job = body["job"]
            if not self.ignore_writes:
                for f in job.get("custom_fields") or []:
                    for mine in self.job["custom_fields"]:
                        if mine["label"] == f["label"]:
                            mine["value"] = f["value"]
                if "customer_address" in job:
                    self.job["customer_address"] = job["customer_address"]
            return ok({"job_uid": "j1"})
        if m == "PUT" and path == "/api/jobs/j1/update":
            if not self.ignore_writes:
                one = body["job"][0]
                self.job["scheduled_start_time"] = one["scheduled_start_time"]
                self.job["scheduled_end_time"] = one["scheduled_end_time"]
            return ok({"job_uid": "j1"})
        if m == "PUT" and path == "/api/jobs/j1/status":
            st = next(s for s in INSPECTION_STATUSES if s["status_uid"] == body["status_uid"])
            if not self.ignore_writes:
                self.job["current_job_status"] = {"status_name": st["status_name"],
                                                  "status_type": st["status_type"]}
            return ok({"job_uid": "j1"})
        if m == "POST" and path == "/api/jobs/j1/note":
            if not self.ignore_writes:
                self.notes.append({"note_uid": "n%d" % len(self.notes),
                                   "note": "<p>%s</p>" % body["note"]["note"]})
            return ok({"note_uid": "n"})
        return httpx.Response(418)


@pytest.fixture()
def zuper(monkeypatch):
    z = Zuper()
    monkeypatch.setenv("ZUPER_SYNC_ENABLED", "true")
    monkeypatch.setenv("ZUPER_API_KEY", "test-key")
    monkeypatch.setenv("ZUPER_PULL_ONLY", "true")           # production's one-way mirror
    monkeypatch.delenv("DISPATCH_ZUPER_WRITES", raising=False)
    monkeypatch.setattr(client, "TRANSPORT", httpx.MockTransport(z.handle))
    monkeypatch.setattr(client, "_sleep", lambda s: None)
    client.reset_pacing()
    return z


@pytest.fixture()
def world(db, zuper):
    people = {}
    for key, role, restricted in (("admin", Role.ADMIN, False),
                                  ("dispatcher", Role.DISPATCHER, False),
                                  ("restricted", Role.DISPATCHER, True),
                                  ("tech", Role.TECH, False)):
        u = User(email="%s@x.test" % key, name=key.title(), role=role,
                 only_assigned_data=restricted)
        db.add(u)
        db.flush()
        plain, tok = mint_api_token(u, name=key)
        db.add(tok)
        people[key] = (u.id, plain)
    db.add(DispatchJob(job_uid="j1", job_number="701", board=dconfig.INSPECTION_BOARD,
                       status="Welcome Call!", is_open=True,
                       fields={"Policy Number": "", "Technician": ""}))
    db.commit()
    c = TestClient(app)

    def as_(who):
        return {"Authorization": "Bearer " + people[who][1]}

    return c, as_, people


def suggestion(db, kind="field", field="Policy Number", proposed="AHS-778812",
               state="open", board=dconfig.INSPECTION_BOARD):
    s = DispatchSuggestion(job_uid="j1", job_number="701", board=board, kind=kind,
                           field=field, current="", proposed=proposed, state=state,
                           evidence="Customer read it out on the 9/30 call")
    db.add(s)
    db.commit()
    return s.id


def switch_on(c, as_, **only):
    r = c.put("/api/dispatch/writes", json={**(only or ALL_ON), "confirm": "TURN ON"},
              headers=as_("admin"))
    assert r.status_code == 200, r.text
    return r.json()


def later(days=2, hour=14):
    d = (datetime.now(UTC) + timedelta(days=days)).replace(hour=hour, minute=0, second=0,
                                                         microsecond=0)
    return d, d + timedelta(hours=2)


# ---- off means nothing leaves ---------------------------------------------------------------

def test_everything_is_off_by_default_and_nothing_reaches_zuper(world, db, zuper):
    c, as_, _ = world
    w = c.get("/api/dispatch/writes", headers=as_("dispatcher")).json()
    assert w["server_gate"] is False and "DISPATCH_ZUPER_WRITES" in w["server_gate_sentence"]
    assert all(s["on"] is False for s in w["switches"])
    assert not any(w["can"].values())
    assert c.get("/api/dispatch/settings", headers=as_("admin")).json()["writes"]["can"] == \
        w["can"]
    sid = suggestion(db)
    start, end = later()
    for path, body in (("/api/dispatch/suggestions/%d/apply" % sid, {"approve": True}),
                       ("/api/dispatch/jobs/j1/book", {"start": start.isoformat(),
                                                       "end": end.isoformat(),
                                                       "technician": "Antonio Brown"}),
                       ("/api/dispatch/jobs/j1/stage", {"status_name": "Scheduled"})):
        r = c.post(path, json=body, headers=as_("admin"))
        assert r.status_code == 409, (path, r.text)
    assert zuper.requests == []
    db.expire_all()
    assert db.get(DispatchSuggestion, sid).state == "open"      # approve+apply changed nothing


def test_switches_on_but_the_server_gate_off_still_writes_nothing(world, db, zuper):
    c, as_, _ = world
    out = switch_on(c, as_)
    assert all(s["on"] for s in out["switches"]) and not any(out["can"].values())
    assert "DISPATCH_ZUPER_WRITES" in out["why_not"]["field"]
    sid = suggestion(db, state="approved")
    r = c.post("/api/dispatch/suggestions/%d/apply" % sid, headers=as_("admin"))
    assert r.status_code == 409 and "DISPATCH_ZUPER_WRITES" in r.json()["detail"]
    r = c.post("/api/dispatch/jobs/j1/stage", json={"status_name": "Scheduled"},
               headers=as_("dispatcher"))
    assert r.status_code == 409
    # ...and the client itself refuses inside the scope, before a connection exists.
    with client.dispatch_write(), pytest.raises(client.ZuperError) as exc:
        client.request("PUT", "/jobs", body={"job": {"job_uid": "j1"}})
    assert exc.value.kind == "refused" and "DISPATCH_ZUPER_WRITES" in exc.value.detail
    assert zuper.requests == []


def test_each_action_needs_its_own_switch_and_the_master(world, db, zuper, monkeypatch):
    monkeypatch.setenv("DISPATCH_ZUPER_WRITES", "true")
    c, as_, _ = world
    switch_on(c, as_, write_fields=True, write_note=True)          # master still off
    sid = suggestion(db, state="approved")
    r = c.post("/api/dispatch/suggestions/%d/apply" % sid, headers=as_("admin"))
    assert r.status_code == 409 and "Let the agent make changes" in r.json()["detail"]
    switch_on(c, as_, writes_enabled=True)
    note = suggestion(db, kind="address", field="Job address", proposed="2 Palm Ave, Weston, "
                      "FL 33326", state="approved")
    r = c.post("/api/dispatch/suggestions/%d/apply" % note, headers=as_("admin"))
    assert r.status_code == 409 and "service address" in r.json()["detail"]
    r = c.post("/api/dispatch/jobs/j1/stage", json={"status_name": "Scheduled"},
               headers=as_("admin"))
    assert r.status_code == 409 and "stage" in r.json()["detail"]
    assert zuper.requests == []


# ---- the switches ----------------------------------------------------------------------------

def test_only_an_admin_flips_switches_and_on_needs_the_typed_phrase(world, db):
    c, as_, people = world
    assert c.put("/api/dispatch/writes", json={**ALL_ON, "confirm": "TURN ON"},
                 headers=as_("dispatcher")).status_code == 403
    for confirm in (None, "turn on", "yes"):
        r = c.put("/api/dispatch/writes", json={"writes_enabled": True, "confirm": confirm},
                  headers=as_("admin"))
        assert r.status_code == 400 and "TURN ON" in r.json()["detail"]
    db.expire_all()
    s = db.get(DispatchSettings, 1)
    assert s is None or not s.writes_enabled
    assert db.query(DispatchWriteLog).count() == 0
    switch_on(c, as_, writes_enabled=True, write_note=True)
    r = c.put("/api/dispatch/writes", json={"write_note": False}, headers=as_("admin"))
    assert r.status_code == 200                                   # off needs no phrase
    db.expire_all()
    log = [(x.target, x.old_value, x.new_value, x.user_id)
           for x in db.query(DispatchWriteLog).order_by(DispatchWriteLog.id)]
    admin = people["admin"][0]
    assert log == [("writes_enabled", "off", "on", admin), ("write_note", "off", "on", admin),
                   ("write_note", "on", "off", admin)]
    # Setting a switch to what it already is records nothing.
    c.put("/api/dispatch/writes", json={"write_note": False}, headers=as_("admin"))
    assert db.query(DispatchWriteLog).count() == 3


# ---- all on: exactly the expected requests ---------------------------------------------------

@pytest.fixture()
def armed(world, monkeypatch):
    monkeypatch.setenv("DISPATCH_ZUPER_WRITES", "true")
    c, as_, people = world
    switch_on(c, as_)
    return c, as_, people


def test_a_field_is_written_with_its_group_metadata_and_read_back(armed, db, zuper):
    c, as_, people = armed
    sid = suggestion(db)
    r = c.post("/api/dispatch/suggestions/%d/apply" % sid, headers=as_("dispatcher"))
    assert r.status_code == 409 and "approve" in r.json()["detail"]
    r = c.post("/api/dispatch/suggestions/%d/apply" % sid, json={"approve": True},
               headers=as_("dispatcher"))
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    assert [(m, p) for m, p, _ in zuper.requests] == [
        ("GET", "/api/jobs/j1"), ("PUT", "/api/jobs"), ("GET", "/api/jobs/j1")]
    assert zuper.requests[1][2] == {"job": {"job_uid": "j1", "custom_fields": [{
        "label": "Policy Number", "value": "AHS-778812", "group_name": "AHS Details",
        "group_uid": "g-ahs", "type": "SINGLE_LINE", "hide_to_fe": False}]}}
    db.expire_all()
    s = db.get(DispatchSuggestion, sid)
    assert (s.state, s.decided_by_id, s.applied_by_id) == (
        "applied", people["dispatcher"][0], people["dispatcher"][0])
    assert "Policy Number" in s.apply_result
    log = db.query(DispatchWriteLog).filter(DispatchWriteLog.action == "field").one()
    assert (log.result, log.suggestion_id, log.new_value) == ("ok", sid, "AHS-778812")
    # Applied once: a second press is refused and sends nothing.
    before = len(zuper.requests)
    assert c.post("/api/dispatch/suggestions/%d/apply" % sid,
                  headers=as_("admin")).status_code == 409
    assert len(zuper.requests) == before


def test_a_read_only_field_is_never_sent(armed, db, zuper):
    c, as_, _ = armed
    sid = suggestion(db, field="Locked", proposed="y", state="approved")
    r = c.post("/api/dispatch/suggestions/%d/apply" % sid, headers=as_("admin"))
    assert r.json()["ok"] is False and "Nothing was sent" in r.json()["sentence"]
    assert zuper.writes() == []


def test_a_note_is_private_and_notifies_nobody(armed, db, zuper):
    c, as_, _ = armed
    sid = suggestion(db, kind="note", field="Note", proposed="Gate code 4411", state="approved")
    r = c.post("/api/dispatch/suggestions/%d/apply" % sid, headers=as_("admin"))
    assert r.json()["ok"] is True, r.text
    assert zuper.writes() == [("POST", "/api/jobs/j1/note", {"note": {
        "note": "Gate code 4411", "is_private": True, "notify_users": False}})]


def test_the_job_address_is_sent_only_when_it_reads_cleanly(armed, db, zuper):
    c, as_, _ = armed
    bad = suggestion(db, kind="address", field="Job address", proposed="near the big tree",
                     state="approved")
    r = c.post("/api/dispatch/suggestions/%d/apply" % bad, headers=as_("admin"))
    assert r.json()["ok"] is False and zuper.writes() == []
    good = suggestion(db, kind="address", field="Job address",
                      proposed="2 Palm Ave, Weston, FL 33326", state="approved")
    r = c.post("/api/dispatch/suggestions/%d/apply" % good, headers=as_("admin"))
    assert r.json()["ok"] is True, r.text
    (_, path, body), = zuper.writes()
    assert path == "/api/jobs" and body == {"job": {"job_uid": "j1", "customer_address": {
        "street": "2 Palm Ave", "city": "Weston", "state": "FL", "zip_code": "33326"}}}


def test_a_read_back_that_does_not_show_it_is_apply_failed_and_never_retried(armed, db, zuper):
    c, as_, _ = armed
    zuper.ignore_writes = True
    sid = suggestion(db, state="approved")
    r = c.post("/api/dispatch/suggestions/%d/apply" % sid, headers=as_("admin"))
    assert r.status_code == 200 and r.json()["ok"] is False
    assert "does not show it" in r.json()["sentence"]
    assert len(zuper.writes()) == 1                                # one PUT, no retry
    db.expire_all()
    assert db.get(DispatchSuggestion, sid).state == "apply_failed"
    assert db.query(DispatchWriteLog).filter(DispatchWriteLog.action == "field").one().result \
        == "failed"
    # Still shown to the office, and pressing again is refused until a person re-approves.
    shown = c.get("/api/dispatch/suggestions", headers=as_("admin")).json()["suggestions"]
    assert [s["state"] for s in shown] == ["apply_failed"]
    assert c.post("/api/dispatch/suggestions/%d/apply" % sid,
                  headers=as_("admin")).status_code == 409
    assert len(zuper.writes()) == 1


def test_a_zuper_refusal_is_apply_failed_with_its_sentence(armed, db, zuper, monkeypatch):
    c, as_, _ = armed
    handle = zuper.handle

    def refuse_puts(request):
        if request.method == "PUT":
            zuper.requests.append((request.method, request.url.path, None))
            return httpx.Response(400, json={"type": "error", "message": "Invalid field"})
        return handle(request)
    monkeypatch.setattr(client, "TRANSPORT", httpx.MockTransport(refuse_puts))
    sid = suggestion(db, state="approved")
    r = c.post("/api/dispatch/suggestions/%d/apply" % sid, headers=as_("admin"))
    assert r.json()["ok"] is False and "Invalid field" in r.json()["sentence"]
    assert len(zuper.writes()) == 1


def test_booking_sends_utc_times_and_the_technician_field(armed, db, zuper):
    c, as_, _ = armed
    start, end = later()
    r = c.post("/api/dispatch/jobs/j1/book", json={"start": start.isoformat(),
                                                   "end": end.isoformat(),
                                                   "technician": "Somebody Else"},
               headers=as_("admin"))
    assert r.status_code == 400 and zuper.requests == []
    r = c.post("/api/dispatch/jobs/j1/book", json={"start": start.isoformat(),
                                                   "end": end.isoformat(),
                                                   "technician": "Antonio Brown"},
               headers=as_("admin"))
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    fmt = "%Y-%m-%d %H:%M:%S"
    assert zuper.writes() == [
        ("PUT", "/api/jobs/j1/update", {"job": [{
            "scheduled_start_time": start.strftime(fmt), "scheduled_end_time": end.strftime(fmt),
            "type": "SCHEDULE"}]}),
        ("PUT", "/api/jobs", {"job": {"job_uid": "j1", "custom_fields": [{
            "label": "Technician", "value": "Antonio Brown", "group_name": "Visit",
            "group_uid": "g-visit", "type": "DROPDOWN", "read_only": False}]}}),
    ]
    log = db.query(DispatchWriteLog).filter(DispatchWriteLog.action == "booking").one()
    assert log.result == "ok"


@pytest.mark.parametrize("stage", ["Paid", "Cancelled", "Estimate Declined", "Review Received"])
def test_a_closing_stage_is_refused_before_anything_is_sent(armed, db, zuper, stage):
    c, as_, _ = armed
    r = c.post("/api/dispatch/jobs/j1/stage", json={"status_name": stage}, headers=as_("admin"))
    assert r.status_code == 400 and "person" in r.json()["detail"]
    assert zuper.requests == []
    assert db.query(DispatchWriteLog).filter(DispatchWriteLog.action == "stage").one().result \
        == "refused"


def test_a_stage_zuper_types_as_closing_or_on_another_board_is_never_sent(armed, db, zuper):
    c, as_, _ = armed
    for stage in ("Inspection Completed", "Estimate Sent"):
        r = c.post("/api/dispatch/jobs/j1/stage", json={"status_name": stage},
                   headers=as_("admin"))
        assert r.status_code == 200 and r.json()["ok"] is False, r.text
        assert "Nothing was sent" in r.json()["sentence"]
    assert zuper.writes() == []
    assert "/api/jobs/status/cat-retail" not in [p for _, p, _ in zuper.requests]


def test_a_stage_move_on_the_job_s_own_board(armed, db, zuper):
    c, as_, _ = armed
    r = c.post("/api/dispatch/jobs/j1/stage", json={"status_name": "Scheduled"},
               headers=as_("admin"))
    assert r.json()["ok"] is True, r.text
    assert zuper.writes() == [("PUT", "/api/jobs/j1/status", {
        "status_uid": "s-sched", "remarks": "Moved from the CRM's Dispatch page for Admin."})]
    assert zuper.job["current_job_status"]["status_name"] == "Scheduled"


# ---- the scope is narrow ---------------------------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("DELETE", "/jobs/j1/delete"), ("POST", "/jobs"), ("PUT", "/customers/c1"),
    ("POST", "/customers_new"), ("DELETE", "/jobs/j1/note/n1"), ("PUT", "/jobs/j1/note/n1"),
    ("PUT", "/jobs/j1/status/rollback"), ("POST", "/jobs/j1/service_tasks"),
    ("PUT", "/invoice/i1"), ("POST", "/estimate"), ("POST", "/telephony/calls/x/sms"),
    ("POST", "/jobs/j1/send_email"), ("GET", "/attachments/upload"), ("POST", "/attachments"),
])
def test_inside_the_scope_everything_else_is_refused(armed, zuper, method, path):
    with client.dispatch_write(), pytest.raises(client.ZuperError) as exc:
        client.request(method, path, body={})
    assert exc.value.kind == "refused"
    assert zuper.requests == []


def test_outside_the_scope_the_mirror_still_refuses_the_same_writes(armed, zuper):
    for method, path in (("PUT", "/jobs"), ("PUT", "/jobs/j1/update"),
                         ("PUT", "/jobs/j1/status"), ("POST", "/jobs/j1/note")):
        with pytest.raises(client.ZuperError) as exc:
            client.request(method, path, body={})
        assert exc.value.kind == "refused"
    assert zuper.requests == []


# ---- who -------------------------------------------------------------------------------------

@pytest.mark.parametrize("who", ["tech", "restricted"])
def test_a_tech_or_restricted_user_is_refused(armed, db, zuper, who):
    c, as_, _ = armed
    sid = suggestion(db, state="approved")
    start, end = later()
    assert c.get("/api/dispatch/writes", headers=as_(who)).status_code == 403
    assert c.put("/api/dispatch/writes", json={"write_note": False},
                 headers=as_(who)).status_code == 403
    assert c.post("/api/dispatch/suggestions/%d/apply" % sid,
                  headers=as_(who)).status_code == 403
    assert c.post("/api/dispatch/jobs/j1/book", json={
        "start": start.isoformat(), "end": end.isoformat(), "technician": "Antonio Brown"},
        headers=as_(who)).status_code == 403
    assert c.post("/api/dispatch/jobs/j1/stage", json={"status_name": "Scheduled"},
                  headers=as_(who)).status_code == 403
    assert zuper.requests == []


def test_a_hidden_board_answers_404_and_sends_nothing(armed, db, zuper):
    c, as_, people = armed
    p = Pipeline(name=dconfig.INSPECTION_BOARD)
    db.add(p)
    db.flush()
    db.add(PipelinePermission(pipeline_id=p.id, user_id=people["admin"][0]))
    db.commit()
    sid = suggestion(db, state="approved")
    start, end = later()
    assert c.post("/api/dispatch/suggestions/%d/apply" % sid,
                  headers=as_("dispatcher")).status_code == 404
    assert c.post("/api/dispatch/jobs/j1/book", json={
        "start": start.isoformat(), "end": end.isoformat(), "technician": "Antonio Brown"},
        headers=as_("dispatcher")).status_code == 404
    assert c.post("/api/dispatch/jobs/j1/stage", json={"status_name": "Scheduled"},
                  headers=as_("dispatcher")).status_code == 404
    assert zuper.requests == []


def test_writes_module_lists_exactly_four_requests():
    assert [m for m, _ in client.DISPATCH_WRITES] == ["PUT", "PUT", "PUT", "POST"]
    assert not any(m == "DELETE" for m, _ in client.DISPATCH_WRITES)
    assert writes.KIND_SWITCH["stage"] == "write_stage"


def teardown_module():
    SessionLocal().close()

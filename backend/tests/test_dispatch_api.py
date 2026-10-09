"""The Dispatch page end to end (2026-09-30): a pass against a fake Zuper, the bell, the API.

What is pinned, by behaviour:
  * **Reads only.** A whole pass makes GET requests, plus the ONE call-history search on Zuper
    Connect; nothing else reaches Zuper. A recorded call with no summary has its details
    opened (a GET), because Zuper's list leaves the summary text out; a summary once read is
    kept and the call is not opened again. The narrow Connect reader refuses every other path.
  * **The first pass rings nothing** (it would bury the office under old jobs); a job that
    appears afterwards rings once — for an admin and an unrestricted dispatcher, never for a
    technician or a restricted user, and not for a dispatcher whose pipeline permissions hide
    that board.
  * **Stage moves are kept with who made them** (zuper_status_history), from the whole job.
  * **The API** is ADMIN + unrestricted DISPATCHER; a hidden board's items are left out and
    its job answers 404; Done / Wrong close an item once and are counted.
"""
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.auth import mint_api_token
from app.db import SessionLocal
from app.dispatch import config as dconfig
from app.dispatch import service
from app.main import app
from app.models import (
    AiAlert,
    DispatchCall,
    DispatchItem,
    DispatchJob,
    Pipeline,
    PipelinePermission,
    Role,
    User,
    ZuperStatusHistory,
)
from app.zuper import client
from fastapi.testclient import TestClient

NOW = datetime(2026, 9, 30, 18, 0, tzinfo=UTC)          # Wed 2 PM New York


def iso(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def job(uid, number, board, status, *, created, phone="(941) 555-0100", start=None, end=None,
        updated=None, first="Jane", last="Doe"):
    return {
        "job_uid": uid, "work_order_number": number, "job_title": "%s roof" % last,
        "job_category": {"category_uid": "cat-" + board, "category_name": board},
        "current_job_status": {"status_name": status, "status_type": "NEW"},
        "job_status": [{"status_history_uid": "h-%s-1" % uid, "status_name": status,
                        "status_type": "NEW", "created_at": iso(created),
                        "category": {"category_uid": "cat-" + board, "category_name": board}}],
        "customer": {"customer_uid": "c-" + uid, "customer_first_name": first,
                     "customer_last_name": last, "customer_contact_no": {"mobile": phone}},
        "customer_address": {"street": "1 Main St", "city": "Miami",
                             "geo_cordinates": [25.7, -80.2]},
        "assigned_to": [], "created_at": iso(created), "updated_at": iso(updated or created),
        "scheduled_start_time": iso(start) if start else None,
        "scheduled_end_time": iso(end) if end else None,
    }


class Zuper:
    """Answers the reads a pass makes; records everything; 418 for anything else."""

    def __init__(self):
        self.jobs: list[dict] = []
        self.notes: dict[str, list[dict]] = {}
        self.calls: list[dict] = []
        self.details: dict[str, dict] = {}
        self.activities: list[dict] = []          # newest first, as Zuper lists them
        self.requests: list[tuple[str, str]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, request.url.host + path))
        ok = lambda data, **kw: httpx.Response(200, json={"type": "success", "data": data, **kw})  # noqa: E731
        if request.url.host == "us-east-1-connect.zuperpro.com":
            if request.method == "POST" and path == "/api/telephony/calls":
                return ok(self.calls, total_records=len(self.calls), total_pages=1)
            for uid, detail in self.details.items():
                if request.method == "GET" and path == "/api/telephony/calls/%s/details" % uid:
                    return ok(detail)
            return httpx.Response(418)
        if request.method != "GET":
            return httpx.Response(418)
        if path == "/api/jobs":
            return ok(self.jobs, total_records=len(self.jobs), total_pages=1)
        if path == "/api/activities/recent":
            page = int(request.url.params.get("page", 1))
            count = int(request.url.params.get("count", 50))
            return ok(self.activities[(page - 1) * count: page * count])
        for j in self.jobs:
            if path == "/api/jobs/%s" % j["job_uid"]:
                detail = json.loads(json.dumps(j))
                for e in detail["job_status"]:
                    e["done_by"] = {"user_uid": "u-luis", "first_name": "Luis",
                                    "last_name": "Candiales"}
                return ok(detail)
            if path == "/api/jobs/%s/note" % j["job_uid"]:
                return ok(self.notes.get(j["job_uid"], []))
        return httpx.Response(404, json={"type": "error", "message": "nope"})


@pytest.fixture()
def zuper(monkeypatch):
    z = Zuper()
    monkeypatch.setenv("ZUPER_SYNC_ENABLED", "true")
    monkeypatch.setenv("ZUPER_API_KEY", "test-key")
    monkeypatch.setenv("DISPATCH_ENABLED", "true")
    monkeypatch.setattr(client, "TRANSPORT", httpx.MockTransport(z.handle))
    monkeypatch.setattr(client, "_sleep", lambda s: None)
    client.reset_pacing()
    return z


@pytest.fixture()
def people(db):
    out = {}
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
        out[key] = (u.id, plain)
    db.commit()
    return out


def bells(db, user_id):
    return db.query(AiAlert).filter(AiAlert.user_id == user_id).all()


# ---- the narrow Connect reader -------------------------------------------------------------

def test_connect_reader_allows_the_two_reads_and_refuses_everything_else(zuper):
    assert client.connect_base("https://us-east-1.zuperpro.com/api") == \
        "https://us-east-1-connect.zuperpro.com/api/telephony"
    client.connect_calls()
    for method, path in (("POST", "/calls/abc/hangup_customer_call"), ("POST", "/voice/dial"),
                         ("GET", "/calls"), ("POST", "/message/send"),
                         ("DELETE", "/calls/abc/details")):
        with pytest.raises(client.ZuperError) as exc:
            client.connect_read(method, path)
        assert exc.value.kind == "refused"
    assert zuper.requests == [("POST", "us-east-1-connect.zuperpro.com/api/telephony/calls")]
    # ...and the general client still refuses Zuper Connect outright.
    with pytest.raises(client.ZuperError):
        client.request("POST", "/telephony/calls")


# ---- a pass ----------------------------------------------------------------------------------

def test_a_pass_reads_only_and_the_first_one_rings_nothing(db, zuper, people):
    zuper.jobs = [job("j1", 701, dconfig.INSPECTION_BOARD, "Work Order Received",
                      created=NOW - timedelta(hours=5))]
    zuper.calls = [{"call_uid": "call-1", "direction": "INCOMING", "status": "NO_ANSWER",
                    "duration": 0, "created_at": iso(NOW - timedelta(hours=2)),
                    "from": {"customer": {"number": "+19415550100"}},
                    "to": {"user": {"user_name": "Luis"}},
                    "call_modules": [{"module": "JOB", "module_uid": "j1"}],
                    # Zuper's live shape (2026-09-30): an OBJECT with no text in it.
                    "call_recordings": [{"call_summary": {"status": None,
                                                          "sentiment": "NEUTRAL"}}]}]
    counts = service.run(db, NOW)
    assert "error" not in counts, counts
    assert {m for m, _ in zuper.requests} == {"GET", "POST"}
    assert [r for r in zuper.requests if r[0] == "POST"] == [
        ("POST", "us-east-1-connect.zuperpro.com/api/telephony/calls")]
    j = db.query(DispatchJob).one()
    assert (j.job_number, j.phones, j.status_by) == ("701", ["9415550100"], "Luis Candiales")
    zc = db.query(DispatchCall).one()
    assert (zc.number, zc.summary, zc.job_uids) == ("9415550100", None, ["j1"])
    kinds = {i.kind for i in db.query(DispatchItem).filter(DispatchItem.state == "open")}
    assert {"new_not_called", "missed_call"} <= kinds
    moves = db.query(ZuperStatusHistory).all()
    assert [(m.status_name, m.done_by_name) for m in moves] == [
        ("Work Order Received", "Luis Candiales")]
    assert db.query(AiAlert).count() == 0                  # seeded silently


def test_a_job_that_appears_later_rings_once_for_the_right_people(db, zuper, people):
    zuper.jobs = [job("j1", 701, dconfig.INSPECTION_BOARD, "Work Order Received",
                      created=NOW - timedelta(days=2))]
    service.run(db, NOW)
    zuper.jobs.append(job("j2", 702, dconfig.INSPECTION_BOARD, "Work Order Received",
                          created=NOW - timedelta(minutes=4), phone="(941) 555-0177",
                          first="Eric", last="Davis"))
    later = NOW + timedelta(minutes=3)
    service.run(db, later)
    service.run(db, later + timedelta(minutes=3))          # the same job seen again
    admin, dispatcher = people["admin"][0], people["dispatcher"][0]
    for uid in (admin, dispatcher):
        new = [a for a in bells(db, uid) if a.kind == "dispatch_new_job"]
        assert len(new) == 1 and "#702 Eric Davis" in new[0].title
    for who in ("tech", "restricted"):
        assert bells(db, people[who][0]) == []


def test_a_hidden_board_rings_nobody_who_cannot_see_it(db, zuper, people):
    p = Pipeline(name="Retail")
    db.add(p)
    db.flush()
    db.add(PipelinePermission(pipeline_id=p.id, user_id=people["admin"][0]))
    db.commit()
    zuper.jobs = []
    service.run(db, NOW)
    zuper.jobs = [job("r1", 801, dconfig.RETAIL_BOARD, "New Lead",
                      created=NOW - timedelta(minutes=2))]
    service.run(db, NOW + timedelta(minutes=3))
    assert [a.kind for a in bells(db, people["admin"][0])] == ["dispatch_new_job"]
    assert bells(db, people["dispatcher"][0]) == []


def test_pictures_that_stop_ring_inspection_done_once(db, zuper, people):
    start, end = NOW - timedelta(hours=3), NOW - timedelta(hours=1)
    zuper.jobs = [job("j1", 701, dconfig.INSPECTION_BOARD, "Inspection In Progress",
                      created=NOW - timedelta(days=3), start=start, end=end)]
    service.run(db, NOW - timedelta(hours=2))                # seeding pass, before pictures
    zuper.jobs[0]["updated_at"] = iso(NOW - timedelta(minutes=40))
    zuper.notes["j1"] = [{"note_uid": "n%d" % i, "note_type": "IMAGE",
                          "attachments": [{}, {}],
                          "created_at": iso(NOW - timedelta(minutes=40 - i)),
                          "created_by": {"first_name": "Antonio", "last_name": "Brown"}}
                         for i in range(3)]
    service.run(db, NOW)
    service.run(db, NOW + timedelta(minutes=3))
    admin = people["admin"][0]
    done = [a for a in bells(db, admin) if a.kind == "dispatch_inspection_done"]
    assert len(done) == 1 and "Antonio Brown posted 6 pictures" in done[0].body
    j = db.query(DispatchJob).one()
    assert (j.photos_today, j.last_photo_by) == (6, "Antonio Brown")


def test_a_failed_pass_is_a_heartbeat_error_not_a_crash(db, zuper, people, monkeypatch):
    monkeypatch.setattr(client, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(401, json={})))
    counts = service.run(db, NOW)
    assert "error" in counts
    assert "API key" in service.state(db).last_error


def test_switched_off_it_never_runs(db, zuper, monkeypatch):
    monkeypatch.setenv("DISPATCH_ENABLED", "false")
    assert service.due(db, NOW) is False


# ---- the API ---------------------------------------------------------------------------------

@pytest.fixture()
def api(db, zuper, people):
    zuper.jobs = [job("j1", 701, dconfig.INSPECTION_BOARD, "Work Order Received",
                      created=NOW - timedelta(hours=5)),
                  job("r1", 801, dconfig.RETAIL_BOARD, "New Lead",
                      created=NOW - timedelta(hours=5), phone="(941) 555-0188")]
    service.run(db, NOW)
    c = TestClient(app)

    def as_(who):
        return {"Authorization": "Bearer " + people[who][1]}

    return c, as_


@pytest.mark.parametrize("who", ["tech", "restricted"])
def test_a_tech_or_restricted_user_is_refused(api, who):
    c, as_ = api
    for path in ("/api/dispatch/summary", "/api/dispatch/items", "/api/dispatch/jobs/j1"):
        assert c.get(path, headers=as_(who)).status_code == 403


def test_items_counts_and_done_wrong(api, db, people):
    c, as_ = api
    s = c.get("/api/dispatch/summary", headers=as_("dispatcher")).json()
    new = next(q for q in s["queues"] if q["key"] == "new")
    assert new["open"] == 2 and new["urgent"] == 2
    items = c.get("/api/dispatch/items?queue=new", headers=as_("dispatcher")).json()["items"]
    assert {i["job_number"] for i in items} == {"701", "801"}
    first, second = items
    r = c.post("/api/dispatch/items/%d/done" % first["id"], json={"note": "called"},
               headers=as_("dispatcher"))
    assert r.json()["state"] == "done"
    assert c.post("/api/dispatch/items/%d/done" % first["id"],
                  headers=as_("dispatcher")).status_code == 409
    c.post("/api/dispatch/items/%d/wrong" % second["id"], headers=as_("admin"))
    s = c.get("/api/dispatch/summary", headers=as_("admin")).json()
    assert s["accuracy_30d"] == {"done": 1, "wrong": 1}
    # Done stands on the next pass: the same evidence does not reopen it.
    service.run(db, NOW + timedelta(minutes=3))
    assert db.get(DispatchItem, first["id"]).state == "done"
    assert c.get("/api/dispatch/items?queue=new", headers=as_("admin")).json()["items"] == []


def test_a_hidden_board_is_left_out_and_its_job_is_404(api, db, people):
    c, as_ = api
    p = Pipeline(name="Retail")
    db.add(p)
    db.flush()
    db.add(PipelinePermission(pipeline_id=p.id, user_id=people["admin"][0]))
    db.commit()
    items = c.get("/api/dispatch/items?queue=new", headers=as_("dispatcher")).json()["items"]
    assert [i["job_number"] for i in items] == ["701"]
    assert c.get("/api/dispatch/jobs/r1", headers=as_("dispatcher")).status_code == 404
    retail_item = db.query(DispatchItem).filter(DispatchItem.job_uid == "r1").first()
    assert c.post("/api/dispatch/items/%d/done" % retail_item.id,
                  headers=as_("dispatcher")).status_code == 404
    assert c.get("/api/dispatch/jobs/r1", headers=as_("admin")).status_code == 200


def test_a_job_shows_its_moves_and_calls(api):
    c, as_ = api
    j = c.get("/api/dispatch/jobs/j1", headers=as_("admin")).json()
    assert j["moves"] == [{"status": "Work Order Received", "board": "AHS - Inspection",
                           "at": j["moves"][0]["at"], "by": "Luis Candiales"}]
    assert j["phones"] == ["9415550100"]


def teardown_module():
    SessionLocal().close()


def test_one_unreadable_job_is_skipped_and_a_deleted_job_closes(db, zuper, people):
    """Review 2026-10-01: a job that cannot be read is retried next pass instead of throwing
    the pass away; a job Zuper no longer lists was deleted there and its items close."""
    zuper.jobs = [job("j1", 701, dconfig.INSPECTION_BOARD, "Work Order Received",
                      created=NOW - timedelta(hours=5)),
                  job("j2", 702, dconfig.INSPECTION_BOARD, "Work Order Received",
                      created=NOW - timedelta(hours=5), phone="(941) 555-0177")]
    real = zuper.handle

    def flaky(request):
        if request.url.path == "/api/jobs/j2":
            return httpx.Response(404, json={"type": "error", "message": "gone"})
        return real(request)

    from app.zuper import client as zclient
    zclient.TRANSPORT = httpx.MockTransport(flaky)
    counts = service.run(db, NOW)
    assert "error" not in counts and counts["jobs"]["read_errors"] == 1
    assert db.query(DispatchJob).count() == 2
    zclient.TRANSPORT = httpx.MockTransport(real)
    zuper.jobs = zuper.jobs[:1]                          # j2 deleted in Zuper
    service.run(db, NOW + timedelta(minutes=3))
    j2 = db.query(DispatchJob).filter(DispatchJob.job_uid == "j2").one()
    assert j2.is_open is False
    assert db.query(DispatchItem).filter(DispatchItem.job_uid == "j2",
                                         DispatchItem.state == "open").count() == 0


def test_a_recorded_calls_summary_is_fetched_from_its_details_and_kept(db, zuper, people):
    """Live 2026-10-01: the list's call_summary is {status: null}; only the details carry the
    text — which is why it showed in Zuper only after someone opened the call."""
    def call(uid, hours):
        return {"call_uid": uid, "direction": "OUTGOING", "status": "COMPLETED", "duration": 90,
                "created_at": iso(NOW - timedelta(hours=hours)),
                "to": {"customer": {"number": "+19415550100"}},
                "from": {"user": {"user_name": "Marianne"}},
                "call_recordings": [{"call_recording_uid": "rec-" + uid,
                                     "recording_url": "https://example.test/r.mp3",
                                     "call_summary": {"status": None, "sentiment": "NEUTRAL"}}]}
    zuper.calls = [call("c-1", 1), call("c-2", 2), call("c-old", 24 * 30)]
    zuper.details = {"c-1": {"call_uid": "c-1", "call_recordings": [{"call_summary": {
        "summary": "Customer agreed to Tuesday for the inspection.",
        "next_action": "Book the inspection", "confidence": 96.7, "status": "COMPLETED"}}]},
                     "c-2": {"call_uid": "c-2", "call_recordings": [{"call_summary": {
                         "status": "IN_PROGRESS", "summary": None}}]}}
    service.run(db, NOW)
    got = {r.call_uid: r.summary for r in db.query(DispatchCall)}
    assert got["c-1"] == ("Customer agreed to Tuesday for the inspection.\n"
                          "Next step: Book the inspection")
    assert got["c-2"] is None and got["c-old"] is None
    opened = [p for m, p in zuper.requests if p.endswith("/details")]
    assert sorted(opened) == ["us-east-1-connect.zuperpro.com/api/telephony/calls/c-1/details",
                              "us-east-1-connect.zuperpro.com/api/telephony/calls/c-2/details"]
    assert all(m in ("GET", "POST") for m, _ in zuper.requests)
    # Next pass: the summary is kept (the list has none), c-1 is not opened again, c-2 is.
    zuper.requests.clear()
    zuper.details["c-2"]["call_recordings"][0]["call_summary"] = {
        "summary": "Left a voicemail.", "status": "COMPLETED"}
    service.run(db, NOW + timedelta(minutes=3))
    db.expire_all()
    got = {r.call_uid: r.summary for r in db.query(DispatchCall)}
    assert got["c-1"].startswith("Customer agreed") and got["c-2"] == "Left a voicemail."
    assert [p for m, p in zuper.requests if p.endswith("/details")] == [
        "us-east-1-connect.zuperpro.com/api/telephony/calls/c-2/details"]

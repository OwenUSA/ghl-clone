"""Owen AI changes Zuper on Owen's word in the chat (2026-11-21, app/owen_ai/changes.py).

Pinned, by behaviour:
  * propose READS Zuper only and stores one pending list per chat (a newer one replaces it);
    a column of another board, an unanswered required question, a photo question, a choice that
    is not an option, a CLOSED column, an unknown technician are refused with a sentence;
  * apply makes nothing — no request at all — unless it answers a LATER message Owen typed in
    the SAME chat whose words it quotes; not in the same answer, not for a sub-chat's message,
    not with the server gate off, the registry at Never, or a customer notification rule on;
  * each kind sends exactly its request (the column's answers, UTC times, ASSIGN / UNASSIGN +
    the Technician field, one merged field, only the changed customer keys, a private note, the
    chat's pictures into the job's gallery), reads it back, and is never retried; a record
    changed since the proposal is skipped untouched;
  * the runner hands Claude the waiting list and the pictures, and the whole round trip works;
  * the client scope refuses any other write before a connection;
  * the pictures routes are the owner's only.
"""
import copy
import io
import json
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.auth import mint_api_token
from app.main import app
from app.models import OwenAiChangeSet, OwenAiMessage, OwenAiThread, OwenAiUpload, Role, User
from app.owen_ai import changes, proposals, runner, uploads
from app.owen_ai.agent import TurnResult
from app.registry import catalog
from app.zuper import client
from app.zuper.client import ZuperError
from fastapi.testclient import TestClient
from tests.zuper_support import zuper_env  # noqa: F401  (fixture)

OWNER = "owner@dtr.test"
SF, RETAIL = "cat-sf", "cat-retail"
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00"
       b"\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
       b"\x00\x00\x00\x00IEND\xaeB`\x82")


def col(uid, name, typ="OTHER"):
    return {"status_uid": uid, "status_name": name, "status_type": typ}


BOARDS = {
    SF: [col("s-new", "Got a job from AHS", "NEW"), col("s-sched", "Scheduled", "SCHEDULED"),
         col("s-done", "Job Completed", "COMPLETED"), col("s-video", "Repair Done", "COMPLETED"),
         col("s-cancel", "Cancelled", "CANCELED"), col("s-locked", "Archived", "CLOSED")],
    RETAIL: [col("r-new", "New Lead", "NEW"), col("r-sched", "Scheduled", "SCHEDULED")],
}
CHECKLISTS = {
    "s-done": [{"checklist_uid": "q-out", "field_name": "Outcome", "field_type": "SINGLE_ITEM",
                "is_required": True, "field_options": ["Repaired", "Inspection only (trip)"]},
               {"checklist_uid": "q-pics", "field_name": "Completion photos",
                "field_type": "MULTI_IMAGE", "is_required": False}],
    "s-video": [{"checklist_uid": "q-vid", "field_name": "Video - finished repair",
                 "field_type": "VIDEO", "is_required": True}],
    "s-cancel": [{"checklist_uid": "q-why", "field_name": "Cancel reason", "field_type": "RADIO",
                  "is_required": True, "field_options": ["No longer needed", "Other"]},
                 {"checklist_uid": "q-other", "field_name": "Cancel note",
                  "field_type": "MULTI_LINE", "is_required": True, "is_dependent": True,
                  "dependent_on": "q-why", "dependent_options": ["Other"]}],
}


def a_job():
    return {"job_uid": "job-812", "work_order_number": 812, "job_title": "Kim Lee AHS",
            "job_category": {"category_uid": SF, "category_name": "AHS Simple Flow"},
            "current_job_status": col("s-new", "Got a job from AHS", "NEW"), "job_status": [],
            "customer": {"customer_uid": "c-1", "customer_first_name": "Kim",
                         "customer_last_name": "Lee"},
            "customer_address": {"street": "1 Palm Ave", "city": "Bradenton", "state": "FL",
                                 "zip_code": "34205", "geo_cordinates": [27.4, -82.5]},
            "assigned_to": [], "scheduled_start_time": None, "scheduled_end_time": None,
            "custom_fields": [
                {"label": "Technician", "value": "", "group_uid": "g-job", "group_name": "Job",
                 "type": "SINGLE_ITEM"},
                {"label": "Service address verified", "value": "No", "group_uid": "g-intake",
                 "group_name": "Intake", "type": "SINGLE_ITEM"},
                {"label": "Roof age", "value": "10", "group_uid": "g-intake",
                 "group_name": "Intake", "type": "SINGLE_LINE"}]}


class FakeZ:
    """Zuper at the HTTP boundary: one job #812, its customer, users, teams, a gallery."""

    def __init__(self, feedback=False, rules=()):
        self.requests: list[httpx.Request] = []
        self.job = a_job()
        self.customer = {"customer_uid": "c-1", "customer_first_name": "Kim",
                         "customer_last_name": "Lee", "customer_email": "kim@example.test",
                         "customer_contact_no": {"mobile": "+19415550100", "home": "",
                                                 "work": None},
                         "customer_address": {"street": "1 Palm Ave", "city": "Bradenton",
                                              "state": "FL", "zip_code": "34205"},
                         "customer_tags": ["AHS"]}
        self.notes: list[dict] = []
        self.gallery = [{"attachment_uid": "old-1", "attachment_name": "roof.jpg"}]
        self.feedback = feedback
        self.rules = list(rules)
        self.ignore_writes = False

    def ok(self, data):
        return httpx.Response(200, json={"type": "success", "data": data})

    def user(self, uid):
        return {"u-owen": {"user_uid": "u-owen", "first_name": "Owen", "last_name": "Buzaglo"},
                "u-ant": {"user_uid": "u-ant", "first_name": "Antonio", "last_name": "Brown"},
                "u-luis": {"user_uid": "u-luis", "first_name": "Luis",
                           "last_name": "Candiales"}}[uid]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        path, q = req.url.path.removeprefix("/api"), req.url.params
        if req.method == "GET":
            if path == "/jobs":
                kw = q.get("filter.keyword", "")
                return self.ok([{"job_uid": "job-812", "work_order_number": 812}]
                               if kw == "812" else [])
            if path == "/jobs/job-812":
                return self.ok(copy.deepcopy(self.job))
            m = re.match(r"^/jobs/status/(.+)$", path)
            if m:
                return self.ok({"job_statuses": BOARDS.get(m.group(1), [])})
            if path == "/settings/checklist":
                return self.ok(CHECKLISTS.get(q.get("job_status_uid"), []))
            if path == "/user/all":
                return self.ok([{**self.user(u), "is_active": True}
                                for u in ("u-owen", "u-ant", "u-luis")])
            if path == "/teams/summary":
                return self.ok([{"team_uid": "t-office", "team_name": "Back Office"},
                                {"team_uid": "t-rep", "team_name": "Repairs"}])
            if path == "/team/t-office":
                return self.ok({"team": {}, "users": [self.user("u-luis")]})
            if path == "/team/t-rep":
                return self.ok({"team": {}, "users": [self.user("u-owen"), self.user("u-ant")]})
            if path == "/customers/c-1":
                return self.ok(copy.deepcopy(self.customer))
            if path == "/attachments/group":
                assert q.get("filter.module_uid") == "job-812"
                return self.ok([{"attachments": list(self.gallery)}])
            if path == "/jobs/job-812/note":
                return self.ok(list(self.notes))
            if path == "/company/config":
                return self.ok({"jobs": {"enable_customer_feedback": self.feedback},
                                "zuper_connect": {"system_notification_enabled": True,
                                                  "system_notification_number": "19544207373"}})
            if path == "/customer_notification":
                return self.ok(self.rules)
            return httpx.Response(404, json={})
        if self.ignore_writes:
            return self.ok({})
        if path == "/misc/upload":
            uid = "att-%d" % len(self.gallery)
            if b'name="module_uid"' in req.content and b"job-812" in req.content:
                self.gallery.append({"attachment_uid": uid})
            return self.ok({"attachment_uid": uid})
        body = json.loads(req.content or b"{}")
        if req.method == "PUT" and path == "/jobs/job-812/status":
            target = next(s for s in BOARDS[SF] if s["status_uid"] == body["status_uid"])
            self.job["current_job_status"] = dict(target)
            self.job["job_status"].append({"status_name": target["status_name"],
                                           "created_at": "2026-11-21T15:00:00Z",
                                           "checklist": body.get("checklist", [])})
            return self.ok({})
        if req.method == "PUT" and path == "/jobs/job-812/update":
            row = body["job"][0]
            if row["type"] == "SCHEDULE":
                self.job["scheduled_start_time"] = row["scheduled_start_time"].replace(" ", "T") \
                    + "Z"
                self.job["scheduled_end_time"] = row["scheduled_end_time"].replace(" ", "T") + "Z"
            else:
                self.job["assigned_to"] = [a for a in self.job["assigned_to"]
                                           if a["user"]["user_uid"] != row["user_uid"]]
            return self.ok({})
        if req.method == "POST" and path == "/jobs/assign":
            u = body["users"][0]
            self.job["assigned_to"].append({"user": self.user(u["user_uid"]),
                                            "team": {"team_uid": u["team_uid"]}})
            return self.ok({})
        if req.method == "PATCH" and path == "/custom_fields":
            for f in body["custom_fields"]:
                for g in self.job["custom_fields"]:
                    if g["label"] == f["label"]:
                        g["value"] = f["value"]
            return self.ok({"type": "SUCCESS"})
        if req.method == "PUT" and path == "/jobs":
            self.job["customer_address"] = dict(body["job"]["customer_address"])
            return self.ok({})
        if req.method == "PUT" and path == "/customers/c-1":
            self.customer.update(body["customer"])
            return self.ok({})
        if req.method == "POST" and path == "/jobs/job-812/note":
            self.notes.append({"note": "<p>%s</p>" % body["note"]["note"]})
            return self.ok({"note_uid": "n-1"})
        return httpx.Response(400, json={"type": "error", "message": "unexpected"})

    def writes(self):
        return [r for r in self.requests if r.method != "GET"]

    def bodies(self):
        return [(r.method, r.url.path.removeprefix("/api"),
                 None if r.url.path.endswith("/misc/upload") else json.loads(r.content or b"{}"))
                for r in self.writes()]


@pytest.fixture()
def people(db, monkeypatch, zuper_env):  # noqa: F811
    monkeypatch.setenv("OWEN_AI_ENABLED", "true")
    monkeypatch.setenv("OWEN_AI_OWNER_EMAIL", OWNER)
    monkeypatch.setenv("OWEN_AI_ZUPER_CHANGES_ENABLED", "true")
    monkeypatch.setenv("ZUPER_PULL_ONLY", "true")          # production's mirror mode
    out = {}
    for key, email in (("owner", OWNER), ("admin", "a@dtr.test")):
        u = User(email=email, name=key.title(), role=Role.ADMIN)
        db.add(u)
        db.flush()
        plain, tok = mint_api_token(u, name=key)
        db.add(tok)
        out[key] = (u.id, {"Authorization": "Bearer " + plain})
    db.commit()
    return out


@pytest.fixture()
def pics_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OWEN_AI_PICTURE_DIR", str(tmp_path))
    return tmp_path


def install(**kw) -> FakeZ:
    z = FakeZ(**kw)
    client.TRANSPORT = httpx.MockTransport(z)
    return z


def chat(db, people, kind="task") -> OwenAiThread:
    th = OwenAiThread(user_id=people["owner"][0], kind=kind, title="Zuper")
    db.add(th)
    db.commit()
    return th


def says(db, th, text, kind="chat") -> OwenAiMessage:
    m = OwenAiMessage(thread_id=th.id, role="user", content=text, status="done", kind=kind)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def propose(db, th, msg, *items, why="from the calls") -> str:
    with proposals.answering(th.id), changes.answering_message(msg):
        return changes.tool_propose({"changes": list(items), "why": why})


def apply(th, msg, set_id, said, items=None) -> str:
    args = {"change_list": set_id, "owen_said": said}
    if items is not None:
        args["items"] = items
    with proposals.answering(th.id), changes.answering_message(msg):
        return changes.tool_apply(args)


def last_set(db) -> OwenAiChangeSet:
    db.expire_all()
    return db.query(OwenAiChangeSet).order_by(OwenAiChangeSet.id.desc()).first()


MOVE_DONE = {"kind": "move", "job": "812", "to": "job completed",
             "answers": {"outcome": "repaired"}, "evidence": "Antonio texted Nov 20: all fixed"}


def proposed(db, people, *items):
    th = chat(db, people)
    m1 = says(db, th, "what needs updating on 812?")
    out = propose(db, th, m1, *items)
    assert changes.WAITING in out, out
    return th, m1, last_set(db)


# ------------------------------------------------------------------------------- propose

def test_propose_reads_zuper_only_and_stores_one_pending_list(db, people):
    z = install()
    th, _, row = proposed(db, people, MOVE_DONE)
    assert z.requests and all(r.method == "GET" for r in z.requests)
    assert row.status == "pending" and row.thread_id == th.id and len(row.changes) == 1
    c = row.changes[0]
    assert c["title"] == ("Column: Got a job from AHS → Job Completed (answers: Outcome = "
                          "Repaired)")
    assert c["do"]["checklist"] == [{"question": "Outcome", "answer": "Repaired",
                                     "type": "SINGLE_ITEM"}]
    # A second list in the same chat replaces the first.
    m2 = says(db, th, "also the field")
    propose(db, th, m2, {"kind": "job_field", "job": "812", "field": "Roof age", "value": "12",
                         "evidence": "customer said 12 years"})
    db.expire_all()
    assert [r.status for r in db.query(OwenAiChangeSet).order_by(OwenAiChangeSet.id)] == [
        "superseded", "pending"]


@pytest.mark.parametrize("change,expect", [
    ({"kind": "move", "job": "812", "to": "New Lead"}, "not a column of AHS Simple Flow"),
    ({"kind": "move", "job": "812", "to": "Job Completed"}, "required: 'Outcome'"),
    ({"kind": "move", "job": "812", "to": "Job Completed", "answers": {"Outcome": "Fixed"}},
     "must be one of: Repaired"),
    ({"kind": "move", "job": "812", "to": "Repair Done"}, "cannot answer that"),
    ({"kind": "move", "job": "812", "to": "Archived"}, "CLOSED-type"),
    ({"kind": "move", "job": "812", "to": "Cancelled", "answers": {"Cancel reason": "Other"}},
     "required: 'Cancel note'"),
    ({"kind": "move", "job": "812", "to": "Scheduled", "answers": {"Mood": "ok"}},
     "has no question"),
    ({"kind": "technicians", "job": "812", "technicians": ["Marx"]}, "no single Zuper user"),
    ({"kind": "job_field", "job": "812", "field": "Technician", "value": "Owen"},
     "use a `technicians` change"),
    ({"kind": "schedule", "job": "812", "start": "2020-01-01 09:00", "end": "2020-01-01 10:00"},
     "in the past"),
    ({"kind": "move", "job": "999", "to": "Scheduled"}, "no Zuper job #999"),
    ({"kind": "pictures", "job": "812", "uploads": [77]}, "not a picture Owen sent"),
    ({"kind": "customer", "job": "812", "email": "kim@example.test"}, "already has"),
    ({"kind": "delete", "job": "812"}, "kind must be one of"),
])
def test_a_bad_change_is_refused_and_nothing_is_stored(db, people, change, expect):
    z = install()
    th = chat(db, people)
    out = propose(db, th, says(db, th, "go"), {"evidence": "Owen asked", **change})
    assert out.startswith("ERROR") and expect in out, out
    assert db.query(OwenAiChangeSet).count() == 0 and z.writes() == []


def test_the_dependent_question_is_needed_only_for_its_answer(db, people):
    install()
    th = chat(db, people)
    out = propose(db, th, says(db, th, "cancel it"), {
        "kind": "move", "job": "812", "to": "Cancelled", "evidence": "customer cancelled",
        "answers": {"Cancel reason": "No longer needed"}})
    assert changes.WAITING in out and "⚠ this column CLOSES the job" in out


def test_an_audit_or_no_chat_proposes_nothing(db, people):
    install()
    th = chat(db, people, kind="audit")
    out = propose(db, th, says(db, th, "Scheduled audit", kind="audit"), MOVE_DONE)
    assert out.startswith("ERROR") and "report_issue" in out
    assert changes.tool_propose({"changes": [MOVE_DONE]}).startswith("ERROR")
    assert db.query(OwenAiChangeSet).count() == 0


# ------------------------------------------------------------------------------- the OK

def test_the_same_answer_cannot_apply_what_it_proposed(db, people):
    z = install()
    th, m1, row = proposed(db, people, MOVE_DONE)
    out = apply(th, m1, row.id, "what needs updating")
    assert "Owen has not answered" in out and z.writes() == []
    assert last_set(db).status == "pending"


@pytest.mark.parametrize("case", ["delegate", "review", "words", "other_chat", "not_a_number"])
def test_only_owens_own_later_words_in_this_chat_say_do_it(db, people, case):
    z = install()
    th, _, row = proposed(db, people, MOVE_DONE)
    said = "do it"
    if case in ("delegate", "review"):
        m2 = says(db, th, "do it", kind=case)
    elif case == "words":
        m2 = says(db, th, "hmm, let me think")
    elif case == "other_chat":
        th = chat(db, people)
        m2 = says(db, th, "do it")
    else:
        m2 = says(db, th, "do it")
    out = apply(th, m2, "12" if case == "not_a_number" else row.id, said)
    assert out.startswith("ERROR: nothing was changed"), out
    assert z.writes() == [] and last_set(db).status == "pending"


@pytest.mark.parametrize("how", ["flag", "registry", "feedback", "rule"])
def test_switches_and_the_safety_read_refuse_before_any_write(db, people, monkeypatch, how):
    z = install(feedback=how == "feedback",
                rules=[{"notification_name": "Job reminder", "is_active": True}]
                if how == "rule" else [])
    th, _, row = proposed(db, people, MOVE_DONE)
    if how == "flag":
        monkeypatch.setenv("OWEN_AI_ZUPER_CHANGES_ENABLED", "false")
    elif how == "registry":
        from app.registry import service
        monkeypatch.setattr(service, "level", lambda db, key, action: catalog.NEVER)
    out = apply(th, says(db, th, "yes do it"), row.id, "do it")
    assert out.startswith("ERROR"), out
    assert z.writes() == [] and last_set(db).status == "pending"


def test_an_old_list_expires(db, people):
    z = install()
    th, _, row = proposed(db, people, MOVE_DONE)
    row.created_at = datetime.now(UTC) - timedelta(hours=25)
    db.commit()
    out = apply(th, says(db, th, "do it"), row.id, "do it")
    assert "more than 24 hours old" in out and z.writes() == []
    assert last_set(db).status == "expired"


# ------------------------------------------------------------------------------- each kind

def test_a_move_sends_the_columns_answers_reads_back_and_applies_once(db, people):
    z = install()
    th, _, row = proposed(db, people, MOVE_DONE)
    m2 = says(db, th, "Yes, do it please")
    out = apply(th, m2, row.id, "do it")
    assert "DONE" in out and "moved to Job Completed" in out, out
    assert z.bodies() == [("PUT", "/jobs/job-812/status", {
        "job_uid": "job-812", "status_uid": "s-done", "status_name": "Job Completed",
        "customer_signature": "", "remarks": changes.REMARKS,
        "checklist": [{"question": "Outcome", "answer": "Repaired", "type": "SINGLE_ITEM"}]})]
    got = last_set(db)
    assert got.status == "applied" and got.applied_message_id == m2.id
    assert got.owen_said == "do it" and got.changes[0]["status"] == "applied"
    # Saying it again applies nothing.
    assert "already applied" in apply(th, says(db, th, "do it again"), row.id, "do it")
    assert len(z.writes()) == 1


def test_a_record_changed_since_is_skipped_untouched(db, people):
    z = install()
    th, _, row = proposed(db, people, MOVE_DONE)
    z.job["current_job_status"] = col("s-sched", "Scheduled", "SCHEDULED")   # a person moved it
    out = apply(th, says(db, th, "do it"), row.id, "do it")
    assert "SKIPPED" in out and "it is in Scheduled now" in out
    assert z.writes() == [] and last_set(db).status == "failed"


def test_zuper_ignoring_a_write_is_failed_and_not_retried(db, people):
    z = install()
    th, _, row = proposed(db, people, MOVE_DONE)
    z.ignore_writes = True
    out = apply(th, says(db, th, "do it"), row.id, "do it")
    assert "FAILED" in out and "nothing was retried" in out and len(z.writes()) == 1


def test_only_the_numbers_owen_okd_are_made(db, people):
    z = install()
    th, _, row = proposed(db, people, MOVE_DONE, {
        "kind": "note", "job": "812", "text": "Customer prefers mornings",
        "evidence": "call Nov 20"})
    out = apply(th, says(db, th, "just do 2"), row.id, "do 2", items=[2])
    assert "1. #812 Kim Lee — Column" in out and "NOT DONE" in out
    assert [b[1] for b in z.bodies()] == ["/jobs/job-812/note"]
    assert last_set(db).status == "applied"


def test_a_schedule_is_sent_in_utc(db, people):
    z = install()
    th, _, row = proposed(db, people, {"kind": "schedule", "job": "812",
                                       "start": "2030-11-25 09:00", "end": "2030-11-25 11:00",
                                       "evidence": "customer texted: Tuesday 9 works"})
    out = apply(th, says(db, th, "ok"), row.id, "ok")
    assert "booked" in out, out
    assert z.bodies() == [("PUT", "/jobs/job-812/update", {"job": [{
        "scheduled_start_time": "2030-11-25 14:00:00", "scheduled_end_time": "2030-11-25 16:00:00",
        "type": "SCHEDULE"}]})]


def test_technicians_assign_unassign_and_the_technician_field(db, people):
    z = install()
    z.job["assigned_to"] = [{"user": z.user("u-luis"), "team": {"team_uid": "t-office"}}]
    th, _, row = proposed(db, people, {"kind": "technicians", "job": "812",
                                       "technicians": ["Antonio"], "evidence": "Owen asked"})
    assert row.changes[0]["title"] == ("Technicians: Luis Candiales → Antonio Brown; Technician "
                                       "field: empty → Antonio Brown")
    out = apply(th, says(db, th, "go ahead"), row.id, "go ahead")
    assert "DONE" in out, out
    assert z.bodies() == [
        ("POST", "/jobs/assign", {"job_uid": "job-812", "type": "ASSIGN",
                                  "users": [{"user_uid": "u-ant", "team_uid": "t-rep"}]}),
        ("PUT", "/jobs/job-812/update", {"job": [{"user_uid": "u-luis", "team_uid": "t-office",
                                                  "type": "UNASSIGN"}]}),
        ("PATCH", "/custom_fields", {"module_name": "JOB", "module_uid": "job-812",
                                     "custom_fields": [{"label": "Technician",
                                                        "value": "Antonio Brown",
                                                        "group_uid": "g-job", "group_name": "Job",
                                                        "type": "SINGLE_ITEM"}]})]


def test_a_job_field_is_one_merged_field_with_its_group(db, people):
    z = install()
    th, _, row = proposed(db, people, {"kind": "job_field", "job": "812",
                                       "field": "service address verified", "value": "Yes",
                                       "evidence": "customer confirmed the address on the call"})
    apply(th, says(db, th, "yes"), row.id, "yes")
    assert z.bodies() == [("PATCH", "/custom_fields", {
        "module_name": "JOB", "module_uid": "job-812", "custom_fields": [{
            "label": "Service address verified", "value": "Yes", "group_uid": "g-intake",
            "group_name": "Intake", "type": "SINGLE_ITEM"}]})]
    assert last_set(db).status == "applied"


def test_the_customer_gets_only_the_changed_keys_and_phones_are_merged(db, people):
    z = install()
    th, _, row = proposed(db, people, {"kind": "customer", "job": "812",
                                       "mobile": "941 555 0199", "last_name": "Lee-Park",
                                       "evidence": "she texted her new number Nov 19"})
    out = apply(th, says(db, th, "do it"), row.id, "do it")
    assert "DONE" in out, out
    assert z.bodies() == [("PUT", "/customers/c-1", {"customer": {
        "customer_last_name": "Lee-Park",
        "customer_contact_no": {"mobile": "+19415550199"}}})]
    assert z.customer["customer_tags"] == ["AHS"]


def test_the_job_address_keeps_to_the_job(db, people):
    z = install()
    th, _, row = proposed(db, people, {"kind": "job_address", "job": "812", "address": {
        "street": "9 Oak St", "city": "Sarasota", "zip": "34236"}, "evidence": "call Nov 20"})
    out = apply(th, says(db, th, "do it"), row.id, "do it")
    assert z.bodies() == [("PUT", "/jobs", {"job": {"job_uid": "job-812", "customer_address": {
        "street": "9 Oak St", "city": "Sarasota", "state": "FL", "zip_code": "34236",
        "country": "US"}}})]
    assert "DONE" in out


def test_a_note_is_private_and_says_owen_ai(db, people):
    z = install()
    th, _, row = proposed(db, people, {"kind": "note", "job": "812", "text": "Gate code 1234",
                                       "evidence": "customer text Nov 20"})
    apply(th, says(db, th, "do it"), row.id, "do it")
    assert z.bodies() == [("POST", "/jobs/job-812/note", {"note": {
        "note": "Owen AI: Gate code 1234", "is_private": True, "notify_users": False}})]


# ------------------------------------------------------------------------------- pictures

def call(method, path, who, people, **kw):
    return TestClient(app).request(method, "/api/owen-ai" + path, headers=people[who][1], **kw)


def test_pictures_dropped_in_the_chat_go_into_the_jobs_gallery(db, people, pics_dir):
    z = install()
    th = chat(db, people)
    up = call("POST", "/threads/%d/uploads" % th.id, "owner", people,
              files={"file": ("leak.png", PNG, "image/png")})
    assert up.status_code == 200, up.text
    uid = up.json()["id"]
    assert call("GET", "/uploads/%d" % uid, "owner", people).content == PNG
    sent = call("POST", "/threads/%d/messages" % th.id, "owner", people,
                json={"content": "put this on 812", "upload_ids": [uid]})
    assert sent.status_code == 200 and sent.json()["uploads"][0]["id"] == uid
    m1 = db.get(OwenAiMessage, sent.json()["id"])
    out = propose(db, th, m1, {"kind": "pictures", "job": "812", "uploads": [uid],
                               "evidence": "Owen sent them"})
    assert "Gallery: add 1 picture (leak.png)" in out
    out = apply(th, says(db, th, "do it"), last_set(db).id, "do it")
    assert "1 picture added to the gallery (1 → 2)" in out, out
    (w,) = z.writes()
    assert w.url.path.endswith("/misc/upload") and PNG in w.content
    for field, value in (("attachment_type", b"ATTACHMENT"), ("module", b"JOB"),
                         ("module_uid", b"job-812")):
        assert re.search(b'name="%s"\r\n\r\n%s\r\n' % (field.encode(), value), w.content)
    assert z.gallery[0]["attachment_uid"] == "old-1"   # the old one is kept


def test_the_picture_routes_are_the_owners_and_take_only_pictures(db, people, pics_dir):
    th = chat(db, people)
    assert call("POST", "/threads/%d/uploads" % th.id, "admin", people,
                files={"file": ("a.png", PNG, "image/png")}).status_code == 404
    bad = call("POST", "/threads/%d/uploads" % th.id, "owner", people,
               files={"file": ("a.png", b"%PDF-1.4 not a picture", "image/png")})
    assert bad.status_code == 422 and db.query(OwenAiUpload).count() == 0
    ok = call("POST", "/threads/%d/uploads" % th.id, "owner", people,
              files={"file": ("a.png", PNG, "image/png")}).json()
    assert call("GET", "/uploads/%d" % ok["id"], "admin", people).status_code == 404
    other = chat(db, people)
    r = call("POST", "/threads/%d/messages" % other.id, "owner", people,
             json={"content": "x", "upload_ids": [ok["id"]]})
    assert r.status_code == 422 and db.query(OwenAiMessage).count() == 0


def test_a_webp_is_kept_as_a_jpeg(db, people, pics_dir):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (200, 30, 30)).save(buf, "WEBP")
    th = chat(db, people)
    row = uploads.save(db, th.id, "photo.webp", buf.getvalue())
    assert row.content_type == "image/jpeg" and row.file_name == "photo.jpg"
    assert uploads.read(row)[:3] == b"\xff\xd8\xff"


# ------------------------------------------------------------------------------- the runner

def test_the_runner_round_trip_propose_then_owens_ok(db, people, pics_dir, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-test")
    z = install()
    th = chat(db, people)
    prompts = []

    def turn_propose(prompt, session_id, note):
        prompts.append(prompt)
        return TurnResult(text=changes.tool_propose({"changes": [MOVE_DONE]}), session_id="s1")

    def turn_apply(prompt, session_id, note):
        prompts.append(prompt)
        return TurnResult(text=changes.tool_apply({"change_list": last_set(db).id,
                                                   "owen_said": "do it"}), session_id="s1")

    m1 = OwenAiMessage(thread_id=th.id, role="user", content="what does 812 need?",
                       status="running")
    db.add(m1)
    db.commit()
    runner.answer(db, m1, turn_propose)
    assert z.writes() == [] and last_set(db).status == "pending"
    m2 = OwenAiMessage(thread_id=th.id, role="user", content="ok do it", status="running")
    db.add(m2)
    db.commit()
    runner.answer(db, m2, turn_apply)
    assert "[Waiting in this chat for Owen's OK]" in prompts[1] and "Change list #" in prompts[1]
    assert last_set(db).status == "applied" and len(z.writes()) == 1
    shown = call("GET", "/threads/%d" % th.id, "owner", people).json()
    assert shown["changes"][0]["status"] == "applied"
    assert shown["changes"][0]["changes"][0]["result"] == "moved to Job Completed."


def test_a_list_left_applying_by_a_restart_is_failed_not_retried(db, people):
    install()
    th, _, row = proposed(db, people, MOVE_DONE)
    row.status = "applying"
    db.commit()
    assert changes.recover(db) == 1
    got = last_set(db)
    assert got.status == "failed" and "restarted" in got.changes[0]["result"]


# ------------------------------------------------------------------------------- the scope

@pytest.mark.parametrize("method,path,body", [
    ("DELETE", "/jobs/job-812/delete", None),
    ("PUT", "/jobs", {"job": {"job_uid": "job-812", "custom_fields": []}}),
    ("PUT", "/jobs/job-812/status", {"status_uid": "s-cancel"}),        # not the agreed column
    ("PUT", "/jobs/other/status", {"status_uid": "s-done"}),            # another job
    ("POST", "/jobs/job-812/note", {"note": {"note": "x", "is_private": True}}),
    ("POST", "/customers/c-1/send", {"type": "SMS", "sms": {"body": "hi", "to": "+1"}}),
    ("PUT", "/customers/c-1", {"customer": {"customer_first_name": "X"}}),
])
def test_inside_a_move_scope_anything_else_is_refused_before_a_connection(db, people, method,
                                                                          path, body):
    z = install()
    with client.owen_ai_change("move", "job-812", status_uid="s-done"), \
            pytest.raises(ZuperError) as exc:
        client.request(method, path, body=body)
    assert exc.value.kind == "refused" and z.requests == []


def test_the_scope_with_the_gate_off_allows_nothing(db, people, monkeypatch):
    z = install()
    monkeypatch.setenv("OWEN_AI_ZUPER_CHANGES_ENABLED", "false")
    with client.owen_ai_change("note", "job-812"), pytest.raises(ZuperError):
        client.request("POST", "/jobs/job-812/note", body={"note": {
            "note": "x", "is_private": True, "notify_users": False}})
    assert z.requests == []


def test_a_picture_scope_uploads_only_into_this_jobs_gallery(db, people):
    z = install()
    form = {"attachment_type": "ATTACHMENT", "module": "JOB", "module_uid": "job-812"}
    with client.owen_ai_change("pictures", "job-812", uploads=1):
        with pytest.raises(ZuperError):          # another job's gallery
            client.request("POST", "/misc/upload", files={"attachment": ("a.png", PNG,
                                                                         "image/png")},
                           form={**form, "module_uid": "other"})
        with pytest.raises(ZuperError):          # not a picture
            client.request("POST", "/misc/upload", files={"attachment": ("a.pdf", b"%PDF-1",
                                                                         "image/png")}, form=form)
        client.request("POST", "/misc/upload", files={"attachment": ("a.png", PNG,
                                                                     "image/png")}, form=form)
        with pytest.raises(ZuperError):          # more than agreed
            client.request("POST", "/misc/upload", files={"attachment": ("b.png", PNG,
                                                                         "image/png")}, form=form)
    assert len(z.writes()) == 1

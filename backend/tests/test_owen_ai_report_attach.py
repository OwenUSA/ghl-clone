"""The after-inspection report's PDF on its DRAFT proposal in Zuper (2026-11-12,
owen_ai/report_attach.py, client.owen_ai_report_attach).

Pinned, by behaviour:
  * off (either switch) -> refused before any Zuper request;
  * only a DRAFT proposal on the report's own job; anything else is refused with nothing written;
  * the writes are exactly: one upload of the PDF, one attach to THIS proposal, and - only when
    an older report PDF is on it - one DELETE of THOSE files; Zuper's own proposal PDF and every
    other attachment stay; nothing sends;
  * the same version twice writes nothing the second time;
  * missing header settings are a note, not a refusal;
  * the route is the owner's only.
"""
import json
from urllib.parse import parse_qs

import httpx
import pytest
from app.owen_ai import report_attach, saved_reports
from app.zuper import client
from tests.test_owen_ai import call, people  # noqa: F401  (fixture)
from tests.test_owen_ai_report import JOB_UID, env  # noqa: F401  (fixture)
from tests.test_owen_ai_saved_reports import full, make, world  # noqa: F401  (fixture)
from tests.zuper_support import zuper_env  # noqa: F401  (fixture)

EST = "e5e5e5e5-0000-4000-8000-000000000168"


class FakeZ:
    """GET /estimate(s), POST /misc/upload, POST + DELETE /note_attachment."""

    def __init__(self, status="DRAFT", job=JOB_UID, attachments=None):
        self.requests: list[httpx.Request] = []
        self.est = {"estimate_uid": EST, "prefix": "DTR-", "estimate_no": 168,
                    "estimate_status": status, "is_proposal": True, "job": {"job_uid": job},
                    "attachments": attachments if attachments is not None else [
                        {"attachment_uid": "z-own", "file_name": "Proposal-DTR-168.pdf"},
                        {"attachment_uid": "ahs-wo", "file_name": "123456 BARSOUM.pdf"}]}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        path = req.url.path.removeprefix("/api")
        ok = lambda data: httpx.Response(200, json={"type": "success", "data": data})  # noqa: E731
        if req.method == "GET" and path == "/estimate":
            return ok([{k: self.est[k] for k in ("estimate_uid", "prefix", "estimate_no",
                                                  "estimate_status")}])
        if req.method == "GET" and path == "/estimate/" + EST:
            return ok(self.est)
        if req.method == "POST" and path == "/misc/upload":
            n = sum(1 for r in self.requests if r.url.path.endswith("/misc/upload"))
            return ok({"attachment_uid": "new-%d" % n, "attachment_size": 1234,
                       "attachment_path": "https://s3.amazonaws.com/z/new-%d.pdf" % n})
        if req.method == "POST" and path == "/note_attachment":
            a = json.loads(req.content)["data"]["attachments"][0]
            self.est["attachments"].append({"attachment_uid": a["attachment_uid"],
                                            "file_name": a["file_name"]})
            return ok({})
        if req.method == "DELETE" and path == "/note_attachment":
            gone = set(parse_qs(req.url.query.decode())["item_uid"][0].split(","))
            self.est["attachments"] = [a for a in self.est["attachments"]
                                       if a["attachment_uid"] not in gone]
            return ok({})
        return httpx.Response(404, json={"message": "no route " + path})

    def writes(self):
        return [(r.method, r.url.path.removeprefix("/api")) for r in self.requests
                if r.method != "GET"]


@pytest.fixture()
def attach_world(world, monkeypatch):  # noqa: F811
    monkeypatch.setenv("OWEN_AI_PROPOSALS_ENABLED", "true")
    monkeypatch.setenv("OWEN_AI_REPORT_ATTACH_ENABLED", "true")
    row, v = make(world, full(world["env"], proposal="DTR-168"))
    # A PDF for the version (the test machine has no Chromium).
    pdf = saved_reports.report_dir(row.id) / "v1" / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% test report\n%%EOF\n")
    v.has_pdf = True
    world["db"].commit()
    return {**world, "row": row}


def use(monkeypatch, z):
    monkeypatch.setattr(client, "TRANSPORT", httpx.MockTransport(z))


def test_it_attaches_to_the_draft_and_writes_only_upload_and_attach(attach_world, monkeypatch):
    z = FakeZ()
    use(monkeypatch, z)
    res = report_attach.attach(attach_world["db"], attach_world["row"].id, "Owen")
    assert res["status"] == "attached" and res["proposal"] == "DTR-168"
    assert z.writes() == [("POST", "/misc/upload"), ("POST", "/note_attachment")]
    names = [a["file_name"] for a in z.est["attachments"]]
    assert names == ["Proposal-DTR-168.pdf", "123456 BARSOUM.pdf",
                     "After-Inspection-Report-728-Brett-Edelson-v1.pdf"]
    assert not any("/send" in r.url.path for r in z.requests)
    assert z.est["estimate_status"] == "DRAFT"
    # The same version again: nothing is written.
    n = len(z.writes())
    assert report_attach.attach(attach_world["db"], attach_world["row"].id, "Owen")[
        "status"] == "already"
    assert len(z.writes()) == n


def test_a_newer_version_replaces_only_our_older_report(attach_world, monkeypatch):
    z = FakeZ(attachments=[
        {"attachment_uid": "z-own", "file_name": "Proposal-DTR-168.pdf"},
        {"attachment_uid": "ahs-wo", "file_name": "123456 BARSOUM.pdf"},
        {"attachment_uid": "old-rep", "file_name": "After-Inspection-Report-Wael-Barsoum.pdf"}])
    use(monkeypatch, z)
    res = report_attach.attach(attach_world["db"], attach_world["row"].id, "Owen")
    assert res["removed"] == ["After-Inspection-Report-Wael-Barsoum.pdf"] and not res["older"]
    assert z.writes() == [("POST", "/misc/upload"), ("POST", "/note_attachment"),
                          ("DELETE", "/note_attachment")]
    delete = next(r for r in z.requests if r.method == "DELETE")
    q = parse_qs(delete.url.query.decode())
    assert q == {"item_uid": ["old-rep"], "module_name": ["ESTIMATE"], "module_uid": [EST],
                 "type": ["ATTACHMENT"]}
    assert [a["file_name"] for a in z.est["attachments"]] == [
        "Proposal-DTR-168.pdf", "123456 BARSOUM.pdf",
        "After-Inspection-Report-728-Brett-Edelson-v1.pdf"]
    assert "Replaced the older report" in report_attach.sentence(res)


@pytest.mark.parametrize("z,why", [
    (FakeZ(status="AWAIT_RESPONSE"), "not a DRAFT"),
    (FakeZ(job="11111111-aaaa-4bbb-8ccc-000000000999"), "is not on job"),
])
def test_a_sent_proposal_or_another_jobs_is_refused_with_nothing_written(
        attach_world, monkeypatch, z, why):
    use(monkeypatch, z)
    with pytest.raises(report_attach.Refused, match=why):
        report_attach.attach(attach_world["db"], attach_world["row"].id, "Owen")
    assert z.writes() == []


@pytest.mark.parametrize("flag", ["OWEN_AI_REPORT_ATTACH_ENABLED", "OWEN_AI_PROPOSALS_ENABLED"])
def test_either_switch_off_refuses_before_any_request(attach_world, monkeypatch, flag):
    monkeypatch.setenv(flag, "false")
    z = FakeZ()
    use(monkeypatch, z)
    with pytest.raises(report_attach.Refused):
        report_attach.attach(attach_world["db"], attach_world["row"].id, "Owen")
    assert z.requests == []


def test_the_scope_refuses_any_other_delete(attach_world, monkeypatch):
    z = FakeZ()
    use(monkeypatch, z)
    with client.owen_ai_report_attach(EST):
        client.request("GET", "/estimate/" + EST)
        with pytest.raises(client.ZuperError):          # before the new one is attached
            client.request("DELETE", "/note_attachment", params={
                "item_uid": "z-own", "module_name": "ESTIMATE", "module_uid": EST,
                "type": "ATTACHMENT"})
    assert z.writes() == []


def test_missing_header_settings_are_a_note_not_a_refusal(attach_world, monkeypatch):
    db = attach_world["db"]
    v = saved_reports.version(db, attach_world["row"].id)
    v.warnings = ["No phone (OWEN_AI_REPORT_PHONE is not set): the report shows [placeholder]."]
    db.commit()
    use(monkeypatch, FakeZ())
    res = report_attach.attach(db, attach_world["row"].id, "Owen")
    assert res["status"] == "attached" and "OWEN_AI_REPORT_PHONE" in report_attach.sentence(res)


@pytest.mark.parametrize("who", ["admin", "dispatcher"])
def test_the_route_is_the_owners_only(attach_world, monkeypatch, who):
    z = FakeZ()
    use(monkeypatch, z)
    r = call("POST", "/inspection-reports/%d/attach" % attach_world["row"].id, who,
             attach_world["people"], json={})
    assert r.status_code == 404 and z.requests == []
    ok = call("POST", "/inspection-reports/%d/attach" % attach_world["row"].id, "owner",
              attach_world["people"], json={})
    assert ok.status_code == 200 and ok.json()["status"] == "attached"

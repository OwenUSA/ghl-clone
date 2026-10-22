"""The inspection-documentation agent (app/inspection_docs), by behaviour - the important ones.

Rules (pure): what an inspection and a repair need; a field, a checklist answer or a quoted note
satisfies a detail; "couldn't inspect" never flags; a VIDEO answer or a video file is a video.
Service (a fake Zuper job at the HTTP boundary, the model scripted, texts and calls recorded):
  * a fully documented visit raises nothing;
  * a missing detail raises ONE Dispatch item and ONE bell; the text is Shadow (recorded only);
  * Automatic: ONE text to the technician's mapped phone, never twice; the call stays Never;
  * the details added later resolve the item;
  * the AI's value is kept only with a quote that really is in the notes;
  * test only: other jobs are ignored; nothing is ever written to Zuper.
"""
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app import crmlink
from app.ai import vault
from app.dispatch import ai as dai
from app.inspection_docs import rules, service
from app.models import (
    AgentPermission,
    AgentRegistryEntry,
    AgentRegistryLog,
    AiAlert,
    AiConnection,
    DispatchItem,
    DispatchJob,
    DispatchNote,
    InspectionCheck,
    Role,
    User,
)
from app.zuper import client
from tests.ai_support import openai_completion

NOW = datetime(2026, 10, 20, 17, 0, tzinfo=UTC)             # Tue 1 PM New York
VISIT = NOW - timedelta(hours=1)
VIDEO = {"question": "Video - roof condition", "type": "VIDEO",
         "answer": ["https://zuper.example/v/roof.mp4"]}


# ------------------------------------------------------------------------- rules

def test_what_each_visit_needs():
    ev = rules.Evidence(photos=3, videos=1)
    assert rules.missing("repair", ev) == []
    assert rules.missing("inspection", ev) == ["leak_location", "tools_materials",
                                               "time_estimate"]
    assert rules.missing("repair", rules.Evidence(photos=3)) == ["video"]


def test_details_come_from_fields_checklist_and_never_flag_couldnt_inspect():
    ev = rules.Evidence(photos=2)
    rules.from_fields(ev, {"Leak location": "Over the garage", "Estimated time to complete":
                           "-", "Roof type": "Tile"})
    texts = rules.from_checklist(ev, [
        VIDEO, {"question": "Tools & materials needed", "answer": "Tile, mortar, ladder"},
        {"question": "Photos - roof", "answer": ["https://zuper.example/p/1.jpg"]}])
    assert ev.details["leak_location"].source == "field"
    assert ev.details["tools_materials"].value == "Tile, mortar, ladder"
    assert (ev.videos, ev.photos) == (1, 3)
    assert texts == ["Tools & materials needed: Tile, mortar, ladder"]
    assert rules.missing("inspection", ev) == ["time_estimate"]       # "-" is not an answer


def test_a_video_file_counts_and_a_quote_must_be_real():
    ev = rules.Evidence()
    rules.from_attachments(ev, [{"file_name": "IMG_1.HEIC"}, {"content_type": "video/mp4"},
                                {"attachment": "https://x/clip.MOV?sig=1"}])
    assert (ev.photos, ev.videos) == (1, 2)
    src = "Antonio: about 4 hours, need 2 bundles of tile."
    assert rules.quote_ok("about 4 hours", src) and not rules.quote_ok("6 hours", src)


# ------------------------------------------------------------------------- the service

@pytest.fixture()
def world(db, monkeypatch, secrets_key):
    monkeypatch.setenv("INSPECTION_DOCS_ENABLED", "true")
    monkeypatch.setenv("ZUPER_SYNC_ENABLED", "true")
    monkeypatch.setenv("ZUPER_API_KEY", "zk_test")
    monkeypatch.setenv("ZUPER_PULL_ONLY", "true")
    monkeypatch.setattr(client, "_sleep", lambda s: None)
    state = {"checklist": [VIDEO], "requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append((request.method, request.url.path))
        if request.method != "GET":
            return httpx.Response(500, json={"type": "error"})
        if request.url.path.endswith("/note"):
            return httpx.Response(200, json={"type": "success", "data": []})
        return httpx.Response(200, json={"type": "success", "data": {
            "job_uid": "job-1", "job_status": [{
                "created_at": VISIT.isoformat(), "status_name": "Inspection Completed",
                "checklist": state["checklist"]}]}})
    client.TRANSPORT = httpx.MockTransport(handler)
    sent, calls = [], []
    monkeypatch.setattr(crmlink, "configured", lambda: True)
    monkeypatch.setattr(crmlink, "send_sms", lambda to, body, **k: sent.append((to, body))
                        or crmlink.LinkResult(True, 200, "", {"message_id": "m1"}))
    monkeypatch.setattr(crmlink, "agent_call", lambda *a, **k: calls.append((a, k))
                        or crmlink.LinkResult(True, 202, "", {"call_id": "c1"}))
    conn = AiConnection(name="Deepseek", provider="deepseek",
                        api_key_encrypted=vault.encrypt("sk-test-1234"), api_key_last4="1234",
                        default_model="deepseek-flash")
    db.add(conn)
    db.flush()
    s = dai.settings(db, for_update=True)
    s.ai_enabled, s.connection_id, s.model = True, conn.id, "deepseek-flash"
    db.add(User(email="boss@x.test", name="Boss", role=Role.ADMIN))
    db.add(AgentRegistryLog(at=NOW - timedelta(days=1), entry_key="inspection_docs",
                            what="started"))
    db.add(AgentRegistryEntry(key="inspection_docs", config={
        "technicians": {"Antonio Brown": "5615550100"}}))
    db.add(DispatchJob(job_uid="job-1", job_number="701", board="AHS - Inspection",
                       status="Inspection Completed", status_since=VISIT,
                       customer_name="Santiago Villahermosa (TEST)", phones=["9549147244"],
                       is_open=True, visit_photo_count=5, visit_photo_at=VISIT,
                       visit_photo_by="Antonio Brown",
                       fields={"Leak location": "Over the garage",
                               "Tools & materials needed": "Tile, mortar"}))
    db.commit()
    state.update(sent=sent, calls=calls)
    return state


def promote(db, action, level="auto"):
    db.add(AgentPermission(entry_key="inspection_docs", action=action, level=level))
    db.commit()


def test_a_documented_visit_raises_nothing(db, world, script):
    j = db.query(DispatchJob).one()
    j.fields = dict(j.fields, **{"Estimated time to complete": "4 hours"})
    db.commit()
    out = service.run(db, NOW)
    row = db.query(InspectionCheck).one()
    assert row.state == "documented" and out["documented"] == 1, out
    assert db.query(DispatchItem).count() == 0 and world["sent"] == []
    assert all(m == "GET" for m, _ in world["requests"])            # Zuper is only read


def test_missing_time_flags_once_and_the_text_is_shadow(db, world, script):
    script.responses = [openai_completion(content=json.dumps({"time_estimate": None}),
                                          model="deepseek-flash")]
    service.run(db, NOW)
    item = db.query(DispatchItem).one()
    assert item.kind == "missing_docs" and "time to complete" in item.title
    assert db.query(AiAlert).count() == 1
    row = db.query(InspectionCheck).one()
    assert (row.state, row.text_state, row.tech_phone) == ("flagged", "shadow", "5615550100")
    assert world["sent"] == []
    service.run(db, NOW + timedelta(minutes=5))
    assert db.query(DispatchItem).count() == 1 and db.query(AiAlert).count() == 1


def test_automatic_texts_the_technician_once_and_never_calls(db, world, script):
    promote(db, "text_staff")
    service.run(db, NOW)
    service.run(db, NOW + timedelta(minutes=5))
    assert len(world["sent"]) == 1
    to, body = world["sent"][0]
    assert to == "+15615550100" and "#701" in body and "time to complete" in body
    service.run(db, NOW + timedelta(hours=2))                       # past the recheck wait
    assert world["calls"] == []
    assert db.query(InspectionCheck).one().call_state == "skipped"   # the call is at Never


def test_details_added_later_resolve_the_item(db, world, script):
    service.run(db, NOW)
    j = db.query(DispatchJob).one()
    j.fields = dict(j.fields, **{"Estimated time to complete": "1 day"})
    db.commit()
    service.run(db, NOW + timedelta(minutes=10))
    assert db.query(InspectionCheck).one().state == "resolved"
    assert db.query(DispatchItem).one().state == "resolved"


def test_the_ai_value_needs_a_real_quote(db, world, script):
    db.add(DispatchNote(note_uid="n1", job_uid="job-1", created_at=VISIT, by_name="Antonio",
                        text="Leak over garage. Should take about 4 hours with 2 guys."))
    db.commit()
    script.responses = [
        openai_completion(content=json.dumps({"time_estimate": {
            "value": "6 hours", "quote": "six hours of work"}}), model="deepseek-flash"),
        openai_completion(content=json.dumps({"time_estimate": {
            "value": "4 hours", "quote": "about 4 hours"}}), model="deepseek-flash")]
    service.run(db, NOW)
    assert db.query(InspectionCheck).one().state == "flagged"        # invented quote dropped
    service.run(db, NOW + timedelta(minutes=5))
    row = db.query(InspectionCheck).one()
    assert row.state == "resolved" and row.found["time_estimate"]["source"] == "note by Antonio"


def test_test_only_ignores_other_jobs(db, world, script):
    db.add(DispatchJob(job_uid="job-2", job_number="900", board="AHS - Inspection",
                       status="Inspection Completed", status_since=VISIT, is_open=True,
                       visit_photo_count=3, visit_photo_at=VISIT))
    db.commit()
    script.responses = [openai_completion(content="{}", model="deepseek-flash")]
    service.run(db, NOW)
    assert [r.job_number for r in db.query(InspectionCheck)] == ["701"]


def test_an_inspection_needs_a_picture_in_each_album_the_job_has():
    """2026-10-22: 360 Roof, 360 Ground, 360 Attic and For the Owner each need a picture; an
    album the job does not have (created before it existed) and "Leaks" are never asked for."""
    ev = rules.Evidence(photos=3, videos=1, albums={
        "360 Roof": 2, "360 ground": 0, "360 Attic": 0, "Leaks": 0, "Inspection": 0})
    for k in ("leak_location", "tools_materials", "time_estimate"):
        ev.add(k, rules.Found("x", "field"))
    gone = rules.missing("inspection", ev)
    assert gone == ["album:360 Ground", "album:360 Attic"]          # no For the Owner album
    assert rules.words(gone) == "pictures in 360 Ground and pictures in 360 Attic"
    assert rules.missing("repair", ev) == []                        # albums: inspections only
    ev.albums = None                                                # not read: never flagged
    assert rules.missing("inspection", ev) == []


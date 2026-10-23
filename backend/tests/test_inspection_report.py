"""The post-inspection report (app/inspection_report), by behaviour - the important ones.

Rules (pure): built only for an inspection whose pictures have been quiet long enough, once,
never for a visit before the first run; the model's answer keeps valid pictures only and drops
any caption / finding with money, approval or a date.
Service (a fake Zuper serving real image bytes, the vision model scripted at the HTTP boundary):
one draft + one Dispatch item + one bell, Zuper only read, PNG / HEIC kept as JPEG; a second
pass does nothing; a model error is "failed" with one alert and no retry.
API: a technician is refused; a hidden picture or one of another report is 404; a draft is
editable until approved; the PDF is a PDF.
"""
import io
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.ai import vault
from app.inspection_report import rules, service
from app.models import (
    AgentRegistryEntry,
    AgentRegistryLog,
    AiAlert,
    AiConnection,
    DispatchItem,
    DispatchJob,
    InspectionReport,
)
from app.zuper import client
from PIL import Image
from tests.ai_support import openai_completion

NOW = datetime(2026, 10, 20, 17, 0, tzinfo=UTC)
LAST = NOW - timedelta(minutes=31)
Q = timedelta(minutes=30)


def img(fmt="JPEG", color=(120, 60, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (800, 600), color).save(out, fmt)
    return out.getvalue()


# ------------------------------------------------------------------------- rules

def test_when_a_report_is_built():
    base = {"kind": "inspection", "photo_count": 5, "last_photo_at": LAST, "now": NOW,
            "quiet": Q, "started": NOW - timedelta(days=1), "already": False}
    assert rules.ready(**base)
    assert not rules.ready(**dict(base, last_photo_at=NOW - timedelta(minutes=29)))
    assert not rules.ready(**dict(base, kind="repair"))
    assert not rules.ready(**dict(base, already=True))
    assert not rules.ready(**dict(base, started=NOW - timedelta(minutes=10)))   # no backlog
    assert not rules.ready(**dict(base, photo_count=0))


def test_the_answer_is_checked_before_it_becomes_a_draft():
    d = rules.accept({"photos": [{"index": 1, "caption": "Cracked tiles near the valley"},
                                 {"index": 1, "caption": "duplicate"},
                                 {"index": 9, "caption": "no such picture"},
                                 {"index": 0, "caption": "Repair approved for $450"}],
                      "title": "Roof inspection", "summary": "Two areas need attention.",
                      "findings": ["Cracked tiles over the garage", "We will fix it Monday",
                                   "Price is $1,200", "Water stains in the attic"]},
                     candidates=3, max_photos=6)
    assert d.photos == [(1, "Cracked tiles near the valley"), (0, "")]
    assert d.findings == ["Cracked tiles over the garage", "Water stains in the attic"]
    assert len(d.dropped) == 3
    assert rules.accept({"photos": [], "findings": ["x"]}, candidates=3, max_photos=6) is None


# ------------------------------------------------------------------------- the service

@pytest.fixture()
def zr(db, monkeypatch, secrets_key):
    monkeypatch.setenv("INSPECTION_REPORT_ENABLED", "true")
    monkeypatch.setenv("ZUPER_SYNC_ENABLED", "true")
    monkeypatch.setenv("ZUPER_API_KEY", "zk_test")
    monkeypatch.setenv("ZUPER_PULL_ONLY", "true")
    monkeypatch.setattr(client, "_sleep", lambda s: None)
    files = {"https://files.example/a.jpg": img(), "https://files.example/b.png": img("PNG"),
             "https://files.example/c.jpg": img(color=(10, 90, 160))}
    try:
        import pillow_heif  # noqa: F401
        files["https://files.example/d.heic"] = img("HEIF")
    except Exception:  # noqa: BLE001
        pass
    state = {"requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append((request.method, str(request.url)))
        if request.method != "GET":
            return httpx.Response(500)
        url = str(request.url)
        if url in files:
            return httpx.Response(200, content=files[url],
                                  headers={"content-type": "application/octet-stream"})
        if url.endswith("/note"):
            return httpx.Response(200, json={"type": "success", "data": [{
                "note_uid": "n1", "note": "", "created_at": LAST.isoformat(),
                "attachments": [{"attachment": "https://files.example/c.jpg",
                                 "file_name": "c.jpg", "created_at": LAST.isoformat()}]}]})
        return httpx.Response(200, json={"type": "success", "data": {"job_status": [{
            "created_at": LAST.isoformat(), "status_name": "Inspection Completed",
            "checklist": [
                {"question": "Photos - roof", "answer": [
                    "https://files.example/a.jpg", "https://files.example/b.png"] + (
                    ["https://files.example/d.heic"] if "https://files.example/d.heic" in files
                    else [])},
                {"question": "Video - roof condition", "type": "VIDEO",
                 "answer": ["https://files.example/v.mp4"]}]}]}})
    client.TRANSPORT = httpx.MockTransport(handler)
    conn = AiConnection(name="OpenAI", provider="openai",
                        api_key_encrypted=vault.encrypt("sk-test-5678"), api_key_last4="5678",
                        default_model="gpt-5-mini", price_input_micros=1_000_000,
                        price_output_micros=2_000_000)
    db.add(conn)
    db.flush()
    db.add(AgentRegistryEntry(key="inspection_report", config={"vision_connection_id": conn.id}))
    db.add(AgentRegistryLog(at=NOW - timedelta(days=1), entry_key="inspection_report",
                            what="started"))
    db.add(DispatchJob(job_uid="job-1", job_number="701", board="AHS - Inspection",
                       status="Inspection Completed", customer_name="Santiago (TEST)",
                       address="100 Test St", phones=["9549147244"], is_open=True,
                       visit_photo_count=4, visit_photo_at=LAST, visit_photo_by="Antonio",
                       fields={"Leak location": "Over the garage", "Roof type": "Tile"}))
    db.commit()
    state["files"] = files
    return state


GOOD = {"photos": [{"index": 2, "caption": "The leak area over the garage"},
                   {"index": 0, "caption": "Cracked tiles"}],
        "title": "Your roof inspection", "summary": "We found a leak over the garage.",
        "findings": ["Cracked tiles over the garage", "Tile roof in fair condition"]}


def test_a_quiet_inspection_gets_one_draft_and_one_bell(db, zr, script):
    script.responses = [openai_completion(content=json.dumps(GOOD))]
    out = service.run(db, NOW)
    assert out["built"] == 1, out
    r = db.query(InspectionReport).one()
    assert r.state == "draft" and r.title == "Your roof inspection"
    assert [p["caption"] for p in r.photos] == ["The leak area over the garage", "Cracked tiles"]
    assert len(r.candidates) == len(zr["files"]) and r.cost_micros
    from app import attachments
    for p in r.candidates:                          # every copy is a JPEG in our store
        assert attachments.sniff(attachments.read("%s/%s" % (p["sha"][:2], p["sha"]))) \
            == "image/jpeg"
    sent = script.requests[0]["body"]
    blocks = sent["messages"][-1]["content"]
    assert sum(1 for b in blocks if b.get("type") == "image_url") == len(r.candidates)
    assert "Over the garage" in json.dumps(sent)              # the written findings went too
    assert not any("v.mp4" in u for _, u in zr["requests"])   # videos are never downloaded
    assert all(m == "GET" for m, _ in zr["requests"])         # Zuper is only read
    assert db.query(DispatchItem).one().kind == "report_ready"
    service.run(db, NOW + timedelta(minutes=5))
    assert db.query(InspectionReport).count() == 1 and script.calls == 1


def test_a_model_error_is_failed_once_with_one_alert(db, zr, script):
    from app.models import Role, User
    db.add(User(email="boss@x.test", name="Boss", role=Role.ADMIN))
    db.commit()
    script.responses = [(500, {"error": {"message": "down"}})]
    service.run(db, NOW)
    r = db.query(InspectionReport).one()
    assert r.state == "failed" and "did not answer" in r.error
    assert db.query(AiAlert).filter(AiAlert.kind == "agent_failure").count() == 1
    service.run(db, NOW + timedelta(minutes=5))
    assert script.calls == 1                                  # never retried by itself


# ------------------------------------------------------------------------- the API

@pytest.fixture()
def report(world, monkeypatch):
    from app import attachments
    from app.db import SessionLocal
    db = SessionLocal()
    sha, _ = attachments.store(img())
    r = InspectionReport(key="job-1|2026-10-20", job_uid="job-1", job_number="701",
                         board="AHS - Inspection", visit_at=NOW, state="draft",
                         title="Your roof inspection", summary="We found a leak.",
                         findings=["Cracked tiles"], photos=[{"sha": sha, "caption": "Leak"}],
                         candidates=[{"sha": sha}])
    other_sha, _ = attachments.store(img(color=(1, 2, 3)))
    db.add(r)
    db.commit()
    out = (r.id, sha, other_sha)
    db.close()
    return out


def test_the_review_api(world, report):
    rid, sha, other = report
    assert world.client("tech").get("/api/inspection-reports/%d" % rid).status_code == 403
    d = world.client("dispatcher")
    r = d.get("/api/inspection-reports/%d" % rid)
    assert r.status_code == 200 and r.json()["photos"][0]["caption"] == "Leak"
    assert d.get("/api/inspection-reports/%d/photos/%s" % (rid, sha)).status_code == 200
    assert d.get("/api/inspection-reports/%d/photos/%s" % (rid, other)).status_code == 404
    pdf = d.get("/api/inspection-reports/%d/report.pdf" % rid)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    r = d.patch("/api/inspection-reports/%d" % rid, json={
        "summary": "We found one leak.", "photos": [{"sha": other, "caption": "x"}]})
    assert r.status_code == 400                               # not one of this report's
    r = d.patch("/api/inspection-reports/%d" % rid, json={"summary": "We found one leak."})
    assert r.status_code == 200 and r.json()["summary"] == "We found one leak."
    assert d.post("/api/inspection-reports/%d/rebuild" % rid).status_code == 403
    assert d.post("/api/inspection-reports/%d/approve" % rid).json()["state"] == "approved"
    assert d.patch("/api/inspection-reports/%d" % rid, json={"title": "x"}).status_code == 409


# ------------------------------------------------------------------------- the guides

def test_the_ai_only_picks_approved_guides_and_they_reach_the_pdf(world, report):
    """2026-10-22: "Understanding your roof" - the AI picks guide KEYS (never writes the text),
    unknown keys are dropped, at most 3; the text is the library's, in the report's language;
    a reviewer can change them; the PDF carries the section."""
    from app.inspection_report import guides
    d = rules.accept({**GOOD, "guides": ["tiles", "made_up", "tiles", "flashing", "debris",
                                         "small_leaks"]}, candidates=3, max_photos=6)
    assert d.guides == ["tiles", "flashing", "debris"]
    es = guides.render(["tiles"], "es")[0]
    assert es["title"] == "Tejas agrietadas, rotas o desplazadas" and "filtración" in es["body"]
    rid, _sha, _other = report
    c = world.client("dispatcher")
    r = c.patch("/api/inspection-reports/%d" % rid, json={"guides": ["small_leaks", "nope"]})
    body = r.json()
    assert [g["key"] for g in body["guides"]] == ["small_leaks"]
    assert body["guides"][0]["body"] == guides.GUIDES["small_leaks"]["en"][1]
    assert len(body["guide_catalog"]) == len(guides.GUIDES)
    from app.db import SessionLocal
    from app.inspection_report import pdf
    db = SessionLocal()
    try:
        text = pdf.build(db.get(InspectionReport, rid))
    finally:
        db.close()
    assert text.startswith(b"%PDF") and len(text) > 1000

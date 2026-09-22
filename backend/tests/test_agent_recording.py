"""Playing a call the AI agent answered.

The thread draws a player from `recording_url` and nothing else. owen-main writes that
value, this CRM serves it, and the two agree on a string across two repositories — the kind
of coupling that rots in silence, so it is pinned from both ends (here, and in owen-main's
`test_agent_crm_report.py`).

The behaviours that matter:

  * a player appears only when there is audio to play — a player over a 404 is worse than
    no player at all;
  * "not fetched yet" is answered as its own thing, because it is the only one of the
    failures worth trying again;
  * a restricted technician is not handed audio from a thread they cannot open, and the
    refusal looks like "no recording" so a call id cannot be probed;
  * nothing is cached here: when owen-main goes away the audio stops, and the operator
    learns that from the screen rather than from silence.
"""
import pytest
from app import crmlink, owen_recordings
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Contact, NumberThreadEvent, Role, User
from fastapi.testclient import TestClient
from sqlalchemy import select

CALL_ID = "0a14b2b7-5182-4f1b-8cca-37dbb3e607c5"


@pytest.fixture()
def voice(monkeypatch):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    feed = User(email="owen@rec.test", name="OWEN feed", role=Role.DISPATCHER)
    admin = User(email="admin@rec.test", name="Owner", role=Role.ADMIN)
    db.add_all([feed, admin])
    db.add(Contact(first_name="Maria", last_name="Lopez", phone="(941) 555-0123"))
    db.flush()
    tokens = {}
    for key, user, scopes in (("feed", feed, "events:write read"), ("admin", admin, "")):
        plain, tok = mint_api_token(user, name=key, scopes=scopes)
        db.add(tok)
        tokens[key] = plain
    db.commit()
    db.close()
    monkeypatch.setenv("CRM_LINK_BASE_URL", "http://owen-main.test")
    monkeypatch.setenv("CRM_LINK_API_KEY", "owen_sk_test")
    with TestClient(app) as c:
        c.tokens = tokens
        yield c


def as_(c, who="admin"):
    return {"Authorization": "Bearer " + c.tokens[who]}


def ingest_agent_call(c, **overrides):
    body = {
        "type": "CALL", "direction": "INBOUND", "from_number": "+18135550142",
        "call_status": "completed", "duration_seconds": 96,
        "provider_ref": CALL_ID, "source_system": "BulkVS",
        "dedupe_key": "owen:call:%s:ended" % CALL_ID,
        "ai_call": {"agent": "Roofing Receptionist"},
    }
    body.update(overrides)
    return c.post("/api/events", json=body, headers=as_(c, "feed"))


def read(fn):
    db = SessionLocal()
    try:
        return fn(db)
    finally:
        db.close()


def test_no_audio_means_no_player_until_owen_says_there_is_some(voice):
    """The first report of a call arrives BEFORE its recording is on disk."""
    ingest_agent_call(voice)
    ev = read(lambda db: db.scalars(select(NumberThreadEvent)).one())
    assert ev.recording_url is None, "a player would point at audio that does not exist yet"

    # ...and the second report, once owen-main has the file, fills it in on the SAME row.
    ingest_agent_call(voice, recording_url="%s/%s" % (owen_recordings.RECORDINGS_PATH,
                                                      CALL_ID))
    rows = read(lambda db: db.scalars(select(NumberThreadEvent)).all())
    assert len(rows) == 1, "the completing report must not write a second call"
    assert rows[0].recording_url.endswith(CALL_ID)


def test_the_path_is_the_one_owen_main_writes():
    """Two repositories, one string. owen-main's `CRM_RECORDING_PATH` must be this."""
    assert owen_recordings.RECORDINGS_PATH == "/api/owen/recordings"
    # ...and the route really is mounted there, rather than the constant merely agreeing
    # with itself.
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/api/owen/recordings/{call_id}" in paths


def test_audio_is_served_when_owen_main_has_it(voice, monkeypatch):
    monkeypatch.setattr(crmlink, "fetch_owen_recording",
                        lambda call_id: (b"RIFF....WAVEfmt ", "audio/wav"))
    r = voice.get("/api/owen/recordings/%s" % CALL_ID, headers=as_(voice))
    assert r.status_code == 200
    assert r.content.startswith(b"RIFF")
    assert r.headers["content-type"].startswith("audio/")
    assert "no-store" not in r.headers.get("cache-control", ""), (
        "the player scrubs; a no-store would re-fetch the whole file per seek")
    assert "private" in r.headers.get("cache-control", ""), (
        "one customer's call audio must never sit in a shared cache")


def test_a_seek_gets_the_bytes_it_asked_for(voice, monkeypatch):
    monkeypatch.setattr(crmlink, "fetch_owen_recording",
                        lambda call_id: (bytes(range(50)), "audio/wav"))
    r = voice.get("/api/owen/recordings/%s" % CALL_ID,
                  headers={**as_(voice), "Range": "bytes=10-19"})
    assert r.status_code == 206, "without 206 the player restarts from zero on every seek"
    assert r.content == bytes(range(10, 20))


def test_the_three_failures_stay_distinguishable(voice, monkeypatch):
    class Result:
        def __init__(self, status, reason):
            self.status, self.reason, self.ok = status, reason, False

    monkeypatch.setattr(crmlink, "fetch_owen_recording",
                        lambda call_id: Result(404, "no recording"))
    assert voice.get("/api/owen/recordings/%s" % CALL_ID,
                     headers=as_(voice)).status_code == 404

    monkeypatch.setattr(crmlink, "fetch_owen_recording",
                        lambda call_id: Result(409, "not fetched yet"))
    later = voice.get("/api/owen/recordings/%s" % CALL_ID, headers=as_(voice))
    assert later.status_code == 409, "'not ready yet' is the one worth retrying"
    assert "try again" in later.json()["detail"]

    monkeypatch.setattr(crmlink, "fetch_owen_recording",
                        lambda call_id: Result(0, "could not reach the phone system"))
    assert voice.get("/api/owen/recordings/%s" % CALL_ID,
                     headers=as_(voice)).status_code == 502


def test_an_unconfigured_link_says_so_instead_of_failing_oddly(voice, monkeypatch):
    monkeypatch.delenv("CRM_LINK_BASE_URL", raising=False)
    monkeypatch.delenv("CRM_LINK_API_KEY", raising=False)
    r = voice.get("/api/owen/recordings/%s" % CALL_ID, headers=as_(voice))
    assert r.status_code == 503
    assert "not configured" in r.json()["detail"]


def test_it_needs_a_credential_like_everything_else(voice):
    assert voice.get("/api/owen/recordings/%s" % CALL_ID).status_code == 401

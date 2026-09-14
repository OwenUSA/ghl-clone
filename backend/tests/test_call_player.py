"""The call-recording player in Conversations (2026-09-14).

The row under a call used to be decoration: a play icon wired to nothing, a `▁▃▅▂▇`
glyph "waveform", a hard-coded "0:00 / 0:35" and "1x", with a native `<audio controls>`
under it as the only real control — which showed "0:00 / 0:00" because every mirrored
Quo call arrived with `recording_url = NULL` (owen-main's half of this fix).

**Executed.** `frontend/src/lib/callPlayer.ts` imports nothing at runtime, so node runs
the shipped code against a fake audio element that behaves like the browser's: play
loads the src and fires `playing`, a 404/502 from the stream fires `error` and rejects
`play()`. Asserted: nothing is fetched before play is pressed, play/pause, seeking,
elapsed / total, the 1x → 1.5x → 2x toggle, a failed load becoming "Recording
unavailable", and one recording playing at a time.

**Asserted against source.** The React wiring, which this project has no runner for:
no player without a `recording_url`, exactly one `<audio>` per call with
`preload="none"`, and no glyph waveform or second native player left anywhere.

**Server half.** A repeat delivery carrying `recording_url` — what owen-main's
`manage.py recordings --commit` sends for the 499 calls mirrored without one — fills the
blank on a contact thread AND on a number-only thread, and changes nothing else.
"""
import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Contact, ConversationEvent, NumberThreadEvent, Role, User
from fastapi.testclient import TestClient
from sqlalchemy import func, select

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
LIB = FRONTEND / "lib" / "callPlayer.ts"
NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None,
    reason="node is not on PATH, so lib/callPlayer.ts cannot be executed. CI's backend "
           "job installs it precisely so this file is never skipped there.")

# A stand-in for HTMLAudioElement with the browser's observable behaviour. `requests`
# counts src assignments that would hit the network; a src containing "missing" is a
# stream that answers 404.
FAKE_AUDIO = """
class FakeAudio {
  constructor() {
    this.paused = true; this.currentTime = 0; this.duration = NaN
    this.playbackRate = 1; this.defaultPlaybackRate = 1; this.preload = 'auto'
    this._src = ''; this.requests = []; this.l = {}
  }
  get src() { return this._src }
  set src(v) { this._src = v; this.requests.push(v) }
  addEventListener(t, fn) { (this.l[t] ||= []).push(fn) }
  removeEventListener(t, fn) { this.l[t] = (this.l[t] || []).filter((f) => f !== fn) }
  fire(t) { for (const fn of [...(this.l[t] || [])]) fn() }
  play() {
    const refuse = (why) =>
      Promise.reject(Object.assign(new Error(why), { name: 'NotSupportedError' }))
    if (!this._src) return refuse('no src')
    if (this._src.includes('missing')) {
      this.fire('error')
      return refuse('404')
    }
    this.paused = false
    this.duration = 35
    this.fire('loadedmetadata'); this.fire('durationchange'); this.fire('playing')
    return Promise.resolve()
  }
  pause() { this.paused = true; this.fire('pause') }
  tick(t) { this.currentTime = t; this.fire('timeupdate') }
}
const flush = () => new Promise((r) => setTimeout(r, 0))
"""


def run_js(body: str):
    script = textwrap.dedent("""
        import * as p from %s
        const out = (v) => console.log('@@' + JSON.stringify(v))
    """) % json.dumps(LIB.as_posix()) + FAKE_AUDIO + textwrap.dedent(body)
    proc = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-"],
        input=script, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert line, "the script printed no result:\n" + proc.stdout + proc.stderr
    return json.loads(line[-1][2:])


def read(*parts: str) -> str:
    return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


# ---------------- executed: the player's behaviour ----------------

@node
def test_nothing_is_fetched_until_play_is_pressed():
    got = run_js("""
        const a = new FakeAudio()
        const pl = p.createCallPlayer(a, '/api/openphone/recordings/AC1', { knownDuration: 35 })
        const before = { requests: a.requests.length, preload: a.preload, st: pl.getState() }
        pl.toggle()
        await flush()
        out({ before, after: a.requests, status: pl.getState().status })
    """)
    assert got["before"]["requests"] == 0, "the recording was requested before play"
    assert got["before"]["preload"] == "none"
    assert got["before"]["st"]["status"] == "idle"
    assert got["before"]["st"]["total"] == 35, "the call's own duration shows before loading"
    assert got["after"] == ["/api/openphone/recordings/AC1"], "one request, on play"
    assert got["status"] == "playing"


@node
def test_play_pause_and_the_clock():
    got = run_js("""
        const a = new FakeAudio()
        const pl = p.createCallPlayer(a, '/r/AC1', { knownDuration: null })
        const seen = []
        const snap = () => { const s = pl.getState()
          seen.push([s.status, p.formatClock(s.elapsed) + ' / ' + p.formatClock(s.total),
                     Math.round(s.progress * 100), a.paused]) }
        snap()
        pl.toggle(); await flush(); snap()
        a.tick(7.4); snap()
        pl.toggle(); snap()
        pl.toggle(); await flush(); snap()
        a.tick(35); a.paused = true; a.fire('ended'); snap()
        out({ seen, requests: a.requests.length })
    """)
    assert got["seen"] == [
        ["idle", "0:00 / 0:00", 0, True],
        ["playing", "0:00 / 0:35", 0, False],
        ["playing", "0:07 / 0:35", 21, False],
        ["paused", "0:07 / 0:35", 21, True],
        ["playing", "0:07 / 0:35", 21, False],
        ["paused", "0:35 / 0:35", 100, True],
    ]
    assert got["requests"] == 1, "resuming does not re-fetch the recording"


@node
def test_seeking_moves_the_audio_and_the_clock():
    got = run_js("""
        const a = new FakeAudio()
        const pl = p.createCallPlayer(a, '/r/AC1', { knownDuration: 35 })
        pl.seek(0.5)
        const beforeLoad = { at: a.currentTime, shown: p.formatClock(pl.getState().elapsed),
                             requests: a.requests.length }
        pl.toggle(); await flush()
        const applied = a.currentTime
        pl.seek(0.2)
        const loaded = { at: a.currentTime, shown: p.formatClock(pl.getState().elapsed),
                         progress: pl.getState().progress }
        pl.seek(7)            // clamped
        out({ beforeLoad, applied, loaded, clamped: a.currentTime })
    """)
    assert got["beforeLoad"] == {"at": 0, "shown": "0:17", "requests": 0}, \
        "seeking before play moves the clock but fetches nothing"
    assert got["applied"] == 17.5, "the pending position is applied once the media loads"
    assert got["loaded"] == {"at": 7, "shown": "0:07", "progress": 0.2}
    assert got["clamped"] == 35


@node
def test_the_speed_toggle_cycles_1x_15x_2x_and_reaches_the_audio():
    got = run_js("""
        const a = new FakeAudio()
        const pl = p.createCallPlayer(a, '/r/AC1', {})
        const labels = [p.speedLabel(pl.getState().rate)]
        const rates = []
        for (let i = 0; i < 3; i++) {
          pl.cycleSpeed()
          labels.push(p.speedLabel(pl.getState().rate))
          rates.push(a.playbackRate)
        }
        pl.cycleSpeed()                       // 1.5x, then play: the load keeps it
        pl.toggle(); await flush()
        out({ labels, rates, afterLoad: a.playbackRate })
    """)
    assert got["labels"] == ["1x", "1.5x", "2x", "1x"]
    assert got["rates"] == [1.5, 2, 1]
    assert got["afterLoad"] == 1.5


@node
def test_a_recording_that_fails_to_load_becomes_unavailable():
    got = run_js("""
        const a = new FakeAudio()
        const pl = p.createCallPlayer(a, '/api/openphone/recordings/missing', { knownDuration: 12 })
        let notified = 0
        pl.subscribe(() => notified++)
        pl.toggle(); await flush()
        const st = pl.getState().status
        pl.toggle(); pl.seek(0.5)             // a dead player ignores both
        await flush()
        out({ st, after: pl.getState().status, requests: a.requests.length,
              notified: notified > 0, message: p.UNAVAILABLE })
    """)
    assert got["st"] == "error" and got["after"] == "error"
    assert got["requests"] == 1, "pressing play again does not hammer a missing recording"
    assert got["notified"], "React is told, so the row is replaced"
    assert got["message"] == "Recording unavailable"


@node
def test_a_pause_racing_the_load_is_not_a_failure():
    got = run_js("""
        const a = new FakeAudio()
        a.play = function () { this.paused = false
          return Promise.reject(Object.assign(new Error('aborted'), { name: 'AbortError' })) }
        const pl = p.createCallPlayer(a, '/r/AC1', {})
        pl.toggle(); pl.toggle(); await flush()
        out(pl.getState().status)
    """)
    assert got == "paused"


@node
def test_one_recording_plays_at_a_time():
    got = run_js("""
        const a1 = new FakeAudio(), a2 = new FakeAudio()
        const one = p.createCallPlayer(a1, '/r/AC1', {})
        const two = p.createCallPlayer(a2, '/r/AC2', {})
        one.toggle(); await flush()
        two.toggle(); await flush()
        out([one.getState().status, a1.paused, two.getState().status, a2.paused])
    """)
    assert got == ["paused", True, "playing", False]


@node
def test_only_a_call_with_a_recording_url_gets_a_player_and_the_clock_formats():
    got = run_js("""
        out({
          has: [p.hasRecording({ recording_url: '/api/openphone/recordings/AC1' }),
                p.hasRecording({ recording_url: null }), p.hasRecording({ recording_url: '' }),
                p.hasRecording({ recording_url: '  ' }), p.hasRecording({})],
          clock: [p.formatClock(0), p.formatClock(7.9), p.formatClock(184),
                  p.formatClock(3723), p.formatClock(NaN), p.formatClock(Infinity),
                  p.formatClock(-4), p.formatClock(null)],
        })
    """)
    assert got["has"] == [True, False, False, False, False]
    assert got["clock"] == ["0:00", "0:07", "3:04", "1:02:03",
                            "0:00", "0:00", "0:00", "0:00"]


# ---------------- source: the React wiring ----------------

def test_the_fake_player_row_and_the_native_player_are_gone():
    page = read("pages", "ConversationsPage.tsx")
    waveform = re.findall(r"[▁-█]", page)
    assert waveform == [], "glyph 'waveform' characters are still in Conversations"
    assert "<audio" not in page, "Conversations renders its own <audio> besides the player"
    assert "1x</span>" not in page and "0:00 / {" not in page, "static player text remains"
    call = page.split("e.type === 'CALL' ? (", 1)[1].split(") : (", 1)[0]
    assert re.search(r"\{hasRecording\(e\) && \(\s*<CallRecordingPlayer", call), \
        "the player must render only for a call with a recording_url"
    assert "Transcript</summary>" in call, "the Transcript disclosure is kept as it was"
    assert not re.findall(r"[\u2581-\u2588]", read("components", "CallRecordingPlayer.tsx"))


def test_one_audio_element_per_call_loaded_only_on_play():
    comp = read("components", "CallRecordingPlayer.tsx")
    audios = re.findall(r"<audio\b[^>]*>", comp)
    assert len(audios) == 1, audios
    assert 'preload="none"' in audios[0]
    assert "src=" not in audios[0], "the src is assigned on play by lib/callPlayer.ts"
    assert "controls" not in audios[0], "no second, native player"
    assert "UNAVAILABLE" in comp and "status === 'error'" in comp
    for control in ('aria-label="Seek"', 'type="range"', "cycleSpeed()", "toggle()",
                    "formatClock(state.elapsed)", "formatClock(state.total)"):
        assert control in comp, control
    # No emoji or glyph icons: the play and pause buttons are the shared SVG icon set.
    assert "IconPlay" in comp and "IconPause" in comp
    assert not re.search(r"[▶⏸⏯\U0001F300-\U0001FAFF]", comp)


# ---------------- server: a repeat delivery fills the blank on both thread kinds ----------


@pytest.fixture()
def feed():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    u = User(email="owen@x.test", name="Owen", role=Role.DISPATCHER)
    admin = User(email="admin@x.test", name="Admin", role=Role.ADMIN)
    db.add_all([u, admin, Contact(first_name="Jane", last_name="Doe",
                                  phone="(941) 555-0101")])
    db.flush()
    plain, tok = mint_api_token(u, name="owen", scopes="events:write")
    admin_plain, admin_tok = mint_api_token(admin, name="admin")
    db.add_all([tok, admin_tok])
    db.commit()
    db.close()
    client = TestClient(app)
    return client, {"Authorization": "Bearer " + plain}, {"Authorization": "Bearer " + admin_plain}


def _as_first_sent(number, call_id):
    """A mirrored call exactly as the broken poll delivered it: no recording_url."""
    return {
        "from_number": number, "provider_ref": call_id,
        "dedupe_key": "openphone:call:" + call_id,
        "occurred_at": "2026-09-11T10:00:00+00:00",
        "source_system": "OpenPhone", "source_number": "+19417247244",
        "type": "CALL", "direction": "INBOUND",
        "body": "Inbound OpenPhone call on +19417247244. Outcome: completed. Duration: 184s.",
        "duration_seconds": 184, "call_status": "completed",
        "transcript": "+19415550101: The skylight is leaking.",
    }


def _as_repaired(number, call_id):
    """owen-main's `manage.py recordings --commit` enrichment for the same call: the
    SAME dedupe key, `recording_url`, no transcript — and a sentence that differs, which
    must not replace the one already on the thread."""
    return {
        "from_number": number, "provider_ref": call_id,
        "dedupe_key": "openphone:call:" + call_id,
        "occurred_at": "2026-09-11T10:00:00+00:00",
        "source_system": "OpenPhone", "source_number": "+19417247244",
        "type": "CALL", "direction": "OUTBOUND",
        "body": "Outbound OpenPhone call on +19417247244. Outcome: voicemail.",
        "duration_seconds": 60, "call_status": "voicemail",
        "recording_url": "/api/openphone/recordings/" + call_id,
    }


def _row(model, key):
    db = SessionLocal()
    try:
        ev = db.scalar(select(model).where(model.dedupe_key == key))
        return {c: getattr(ev, c) for c in ("id", "body", "direction", "duration_seconds",
                                            "call_status", "transcript", "recording_url")} \
            if ev else None
    finally:
        db.close()


def _counts():
    db = SessionLocal()
    try:
        return (db.scalar(select(func.count()).select_from(ConversationEvent)),
                db.scalar(select(func.count()).select_from(NumberThreadEvent)))
    finally:
        db.close()


@pytest.mark.parametrize("number,model,call_id", [
    ("+19415550101", ConversationEvent, "AC_known"),     # Jane's contact thread
    ("+19415559999", NumberThreadEvent, "AC_stranger"),  # a number-only thread
])
def test_a_repeat_delivery_fills_a_blank_recording_url_and_nothing_else(
        feed, number, model, call_id):
    client, as_feed, as_admin = feed
    first = client.post("/api/events", json=_as_first_sent(number, call_id), headers=as_feed)
    assert first.status_code == 201, first.text
    before = _row(model, "openphone:call:" + call_id)
    assert before is not None and before["recording_url"] is None
    counts = _counts()

    again = client.post("/api/events", json=_as_repaired(number, call_id), headers=as_feed)
    assert again.status_code == 201, again.text
    assert again.json()["id"] == before["id"]
    assert again.json()["enriched"] == ["recording_url"]

    after = _row(model, "openphone:call:" + call_id)
    assert after["recording_url"] == "/api/openphone/recordings/" + call_id
    assert {k: v for k, v in after.items() if k != "recording_url"} == \
        {k: v for k, v in before.items() if k != "recording_url"}, \
        "the repair overwrote something other than the blank recording_url"
    assert _counts() == counts, "the repair added a row"

    # What the browser gets, on whichever kind of thread it is.
    if model is ConversationEvent:
        events = client.get("/api/conversations/%d/events" % again.json()["conversation_id"],
                            headers=as_admin).json()
    else:
        events = client.get("/api/number-threads/%d/events"
                            % again.json()["number_thread_id"], headers=as_admin).json()
    assert [e["recording_url"] for e in events] == ["/api/openphone/recordings/" + call_id]

    # A third delivery changes nothing.
    third = client.post("/api/events", json=_as_repaired(number, call_id), headers=as_feed)
    assert third.json()["enriched"] == [] and _row(model, "openphone:call:" + call_id) == after


# ---------------- server: the recording route can be seeked ----------------


def test_the_recording_route_answers_a_range_request_so_the_seek_bar_works(
        feed, monkeypatch):
    """Chrome will not seek a media response that ignores Range; it restarts at 0.
    Measured in a real Chromium (.qa/state/rec-done). The route slices the bytes it
    already holds."""
    from app import crmlink

    audio = bytes(range(256)) * 4          # 1024 distinguishable bytes
    fetched = []

    class Resp:
        status_code = 200
        content = audio
        headers = {"content-type": "audio/mpeg"}  # noqa: RUF012

    def fake_get(url, **kw):
        fetched.append(url)
        return Resp()

    monkeypatch.setenv("CRM_LINK_BASE_URL", "http://callmon_app:8888")
    monkeypatch.setenv("CRM_LINK_API_KEY", "owen_sk_test")
    monkeypatch.setattr(crmlink.httpx, "get", fake_get)
    client, _, as_admin = feed
    url = "/api/openphone/recordings/AC_call_1"

    whole = client.get(url, headers=as_admin)
    assert whole.status_code == 200 and whole.content == audio
    assert whole.headers["accept-ranges"] == "bytes", "the browser must learn it can seek"

    part = client.get(url, headers={**as_admin, "Range": "bytes=512-"})
    assert part.status_code == 206
    assert part.content == audio[512:]
    assert part.headers["content-range"] == "bytes 512-1023/1024"

    mid = client.get(url, headers={**as_admin, "Range": "bytes=10-19"})
    assert (mid.status_code, mid.content) == (206, audio[10:20])
    tail = client.get(url, headers={**as_admin, "Range": "bytes=-4"})
    assert (tail.status_code, tail.content) == (206, audio[-4:])
    over = client.get(url, headers={**as_admin, "Range": "bytes=5000-"})
    assert over.status_code == 416 and over.headers["content-range"] == "bytes */1024"
    odd = client.get(url, headers={**as_admin, "Range": "bytes=0-1,5-6"})
    assert (odd.status_code, odd.content) == (200, audio), "unparsed ranges get the file"
    assert "owen_sk_test" not in str(part.headers)


def test_a_range_request_for_a_missing_recording_is_still_a_404(feed, monkeypatch):
    from app import crmlink

    class NotFound:
        status_code = 404
        content = b""
        headers = {}  # noqa: RUF012

        def json(self):
            return {"detail": "OpenPhone has no recording for that call"}

    monkeypatch.setenv("CRM_LINK_BASE_URL", "http://callmon_app:8888")
    monkeypatch.setenv("CRM_LINK_API_KEY", "owen_sk_test")
    monkeypatch.setattr(crmlink.httpx, "get", lambda url, **kw: NotFound())
    client, _, as_admin = feed
    r = client.get("/api/openphone/recordings/AC_x", headers={**as_admin, "Range": "bytes=0-"})
    assert r.status_code == 404

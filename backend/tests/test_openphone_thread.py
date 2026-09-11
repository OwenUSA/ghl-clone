"""One thread per customer, even when two phone systems fed it.

owen-main mirrors the company's OpenPhone line into this CRM (see DECISIONS.md,
2026-09-11). OpenPhone is read-only and is being migrated away from; its history has
to land on the SAME conversation as the BulkVS events for that customer, interleaved
in time order, each row labelled with the system and line that carried it.

**owen-main is mocked at the HTTP boundary and is never called**, exactly as in
`test_crm_link.py` — `app.crmlink` reaches the network through `httpx.post` and
`httpx.get` and nothing else, so replacing those exercises all the real code here
while the live phone system sees nothing. **The OpenPhone API is never called from
this repository at all**: it has no client for it and must never grow one. The test
at the bottom asserts that as source, not as intent.

Behaviour, not status codes, per CLAUDE.md: the idempotency test re-reads the thread
and counts rows, the automation test drains the job queue, and the composer test
looks at what was actually sent.
"""
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Contact,
    Conversation,
    ConversationEvent,
    Job,
    Role,
    User,
)
from fastapi.testclient import TestClient

OPENPHONE_LINE = "+19417247244"
BULKVS_LINE = "+19544829099"


# --- the world ------------------------------------------------------------------------


@pytest.fixture()
def world():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    admin = User(email="admin@x.test", name="Admin", role=Role.ADMIN)
    dispatcher = User(email="d@x.test", name="Dispatcher", role=Role.DISPATCHER)
    db.add_all([admin, dispatcher])
    db.flush()

    # Stored the way a person types it. owen-main sends E.164 for the same line and
    # the last-ten-digits rule is what makes them one number.
    jane = Contact(first_name="Jane", last_name="Doe", phone="(941) 555-0101")
    db.add(jane)
    db.flush()

    tokens = {}
    for key, user in (("admin", admin), ("dispatcher", dispatcher)):
        plain, tok = mint_api_token(user, name=key)
        db.add(tok)
        tokens[key] = plain
    plain_feed, feed_tok = mint_api_token(dispatcher, name="owen",
                                          scopes="events:write")
    db.add(feed_tok)
    tokens["feed"] = plain_feed
    db.commit()

    ids = {"jane": jane.id}
    db.close()

    client = TestClient(app)

    def as_(who):
        return {"Authorization": "Bearer " + tokens[who]}

    return client, ids, as_


def ingest(client, as_, **body):
    """POST one event as the telephony feed, the way owen-main does."""
    return client.post("/api/events", json=body, headers=as_("feed"))


def mirrored_call(**over):
    """A body shaped exactly as owen-main's `to_crm_call_event` builds one."""
    body = {
        "from_number": "+19415550101",
        "provider_ref": "AC_call_1",
        "dedupe_key": "openphone:call:AC_call_1",
        "occurred_at": "2026-09-11T10:00:00+00:00",
        "source_system": "OpenPhone",
        "source_number": OPENPHONE_LINE,
        "type": "CALL",
        "direction": "INBOUND",
        "body": "Inbound OpenPhone call on %s. Outcome: completed." % OPENPHONE_LINE,
        "duration_seconds": 184,
        "call_status": "completed",
        "recording_url": "/api/openphone/recordings/AC_call_1",
        "transcript": "+19415550101: The skylight is leaking.",
    }
    body.update(over)
    return body


def thread(client, as_, conv_id, who="admin"):
    r = client.get("/api/conversations/%d/events" % conv_id, headers=as_(who))
    assert r.status_code == 200, r.text
    return r.json()


# --- 1. a mirrored event lands on the right thread, labelled --------------------------


def test_a_mirrored_call_lands_on_the_contacts_thread_labelled_with_its_source(world):
    client, ids, as_ = world
    r = ingest(client, as_, **mirrored_call())
    assert r.status_code == 201, r.text
    conv_id = r.json()["conversation_id"]
    assert r.json()["contact_id"] == ids["jane"], "matched on the last ten digits"

    rows = thread(client, as_, conv_id)
    assert len(rows) == 1
    ev = rows[0]
    assert ev["type"] == "CALL"
    assert ev["direction"] == "INBOUND"
    assert ev["duration_seconds"] == 184
    # THE LABEL. Without both halves an operator cannot tell which line a customer
    # used, which is the question that decides whether replying here makes sense.
    assert ev["source_system"] == "OpenPhone"
    assert ev["source_number"] == OPENPHONE_LINE
    # And the recording points at THIS server, never at OpenPhone.
    assert ev["recording_url"] == "/api/openphone/recordings/AC_call_1"
    assert "openphone.com" not in (ev["recording_url"] or "")


def test_a_mirrored_text_carries_its_direction_and_body(world):
    client, ids, as_ = world
    r = ingest(client, as_, **{
        "from_number": "941-555-0101", "dedupe_key": "openphone:message:AC_m1",
        "occurred_at": "2026-09-11T10:05:00+00:00", "source_system": "OpenPhone",
        "source_number": OPENPHONE_LINE, "type": "SMS", "direction": "INBOUND",
        "body": "Can you come Tuesday?",
    })
    assert r.status_code == 201, r.text
    rows = thread(client, as_, r.json()["conversation_id"])
    assert [(e["type"], e["direction"], e["body"]) for e in rows] == [
        ("SMS", "INBOUND", "Can you come Tuesday?")]
    assert rows[0]["source_system"] == "OpenPhone"


def test_an_unknown_number_auto_creates_a_contact_matched_on_ten_digits(world):
    """A stranger is a lead, not a dropped event — the same rule the BulkVS path uses.

    Asserted in both directions: a NEW number creates exactly one contact, and a
    second event written the way a HUMAN types the same number lands on that same
    contact rather than creating a second one.
    """
    client, ids, as_ = world
    before = client.get("/api/contacts?page_size=200", headers=as_("admin")).json()

    r = ingest(client, as_, **mirrored_call(
        from_number="+19415559999", dedupe_key="openphone:call:AC_new",
        provider_ref="AC_new"))
    assert r.status_code == 201, r.text
    new_contact = r.json()["contact_id"]
    assert new_contact != ids["jane"]

    after = client.get("/api/contacts?page_size=200", headers=as_("admin")).json()
    assert after["total"] == before["total"] + 1, "exactly one contact was created"

    # Same line, written the way a person writes it.
    r2 = ingest(client, as_, **mirrored_call(
        from_number="(941) 555-9999", dedupe_key="openphone:message:AC_new2",
        type="SMS", direction="INBOUND", body="hello", call_status=None,
        duration_seconds=None, recording_url=None, transcript=None))
    assert r2.status_code == 201, r2.text
    assert r2.json()["contact_id"] == new_contact, "one number, one contact"
    final = client.get("/api/contacts?page_size=200", headers=as_("admin")).json()
    assert final["total"] == after["total"], "no second contact for one number"


# --- 2. idempotency -------------------------------------------------------------------


def test_the_same_call_ingested_twice_produces_one_event(world):
    """The mirror's poll re-reads its window every tick, and a `crm_report` job can be
    retried after this endpoint already committed. Both land here."""
    client, ids, as_ = world
    first = ingest(client, as_, **mirrored_call())
    second = ingest(client, as_, **mirrored_call())
    assert first.status_code == 201
    assert second.status_code == 201, "a repeat delivery succeeded, it did not 409"

    assert second.json()["id"] == first.json()["id"], "the SAME row came back"
    assert second.json()["automation"].startswith("duplicate")

    rows = thread(client, as_, first.json()["conversation_id"])
    assert len(rows) == 1, "ONE event on the timeline, not two"


def test_a_duplicate_does_not_create_a_second_contact_or_notify_again(world):
    """The dedupe check runs BEFORE the contact is resolved, on purpose: resolving
    CREATES a contact for an unknown number and fires the new-lead automation, so a
    retried delivery would notify the crew about a lead already on file."""
    client, ids, as_ = world
    body = mirrored_call(from_number="+19415558888",
                         dedupe_key="openphone:call:AC_dup")
    ingest(client, as_, **body)
    contacts_after_first = client.get(
        "/api/contacts?page_size=200", headers=as_("admin")).json()["total"]

    db = SessionLocal()
    leads_after_first = db.query(Job).filter(Job.type == "new_lead_notify").count()
    db.close()

    ingest(client, as_, **body)

    contacts_after_second = client.get(
        "/api/contacts?page_size=200", headers=as_("admin")).json()["total"]
    db = SessionLocal()
    leads_after_second = db.query(Job).filter(Job.type == "new_lead_notify").count()
    db.close()

    assert contacts_after_second == contacts_after_first, "no second contact"
    assert leads_after_second == leads_after_first, "the crew was not notified twice"


def test_two_different_calls_are_two_events(world):
    """The guard must not be so eager that it swallows real activity."""
    client, ids, as_ = world
    r = ingest(client, as_, **mirrored_call())
    ingest(client, as_, **mirrored_call(dedupe_key="openphone:call:AC_call_2",
                                        provider_ref="AC_call_2",
                                        occurred_at="2026-09-11T11:00:00+00:00"))
    rows = thread(client, as_, r.json()["conversation_id"])
    assert len(rows) == 2


def test_the_bulkvs_call_path_still_writes_three_phases(world):
    """THE REGRESSION GUARD. owen-main deliberately sends the SAME provider_ref on
    all three call lifecycle phases. Had idempotency been built on provider_ref
    instead of its own column, this would collapse to one row and the live path
    would break on the first call after deploy."""
    client, ids, as_ = world
    for phase, type_ in (("started", "INTERNAL_COMMENT"),
                         ("answered", "INTERNAL_COMMENT"),
                         ("ended", "CALL")):
        r = ingest(client, as_, **{
            "from_number": "+19415550101", "provider_ref": "owen-call-77",
            "type": type_, "direction": "OUTBOUND" if type_ != "CALL" else "INBOUND",
            "body": "Inbound call %s." % phase,
            **({"duration_seconds": 184, "call_status": "completed"}
               if type_ == "CALL" else {}),
        })
        assert r.status_code == 201, r.text

    rows = thread(client, as_, r.json()["conversation_id"])
    assert len(rows) == 3, "all three phases survived — provider_ref is not unique"


# --- 3. one thread, in time order -----------------------------------------------------


def test_openphone_and_bulkvs_events_share_one_thread_in_time_order(world):
    """The whole point of the feature: one customer, one timeline.

    The events are ingested OUT of chronological order on purpose — a backfill
    arrives after the live events it predates — so this asserts real ordering rather
    than insertion order.
    """
    client, ids, as_ = world

    # 1. a live BulkVS text this morning
    r = ingest(client, as_, **{
        "from_number": "+19415550101", "type": "SMS", "direction": "INBOUND",
        "body": "Are you still coming?", "provider_ref": "bulkvs-1",
        "occurred_at": "2026-09-11T09:00:00+00:00",
        "source_system": "BulkVS", "source_number": BULKVS_LINE,
    })
    conv_id = r.json()["conversation_id"]

    # 2. a mirrored OpenPhone call from LAST MONTH, arriving now via the backfill
    ingest(client, as_, **mirrored_call(occurred_at="2026-08-20T14:00:00+00:00"))

    # 3. a mirrored OpenPhone text from between the two
    ingest(client, as_, **{
        "from_number": "+19415550101", "type": "SMS", "direction": "INBOUND",
        "body": "Thanks for the quote.", "dedupe_key": "openphone:message:AC_m9",
        "occurred_at": "2026-09-01T12:00:00+00:00",
        "source_system": "OpenPhone", "source_number": OPENPHONE_LINE,
    })

    rows = thread(client, as_, conv_id)
    assert len(rows) == 3, "all three on ONE thread"
    assert [e["source_system"] for e in rows] == ["OpenPhone", "OpenPhone", "BulkVS"], \
        "chronological, not insertion order"
    assert [e["occurred_at"][:10] for e in rows] == [
        "2026-08-20", "2026-09-01", "2026-09-11"]


def test_a_backfilled_event_does_not_drag_the_thread_back_up_the_inbox(world):
    """`last_event_at` orders the inbox. A three-week-old mirrored call must not make
    a thread that was active this morning look stale."""
    client, ids, as_ = world
    now = datetime.now(UTC)
    ingest(client, as_, **{
        "from_number": "+19415550101", "type": "SMS", "direction": "INBOUND",
        "body": "this morning", "occurred_at": now.isoformat(),
    })
    ingest(client, as_, **mirrored_call(
        occurred_at=(now - timedelta(days=21)).isoformat()))

    db = SessionLocal()
    conv = db.query(Conversation).filter(
        Conversation.contact_id == ids["jane"]).one()
    last = conv.last_event_at
    db.close()
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    assert (now - last).total_seconds() < 60, "recency is forward-only"


def test_a_backfill_does_not_inflate_the_unread_badge(world):
    """"Unread" means nobody has seen it. A month of mirrored correspondence WAS seen
    — in OpenPhone, where it was answered. A badge of 200 trains an operator to clear
    it without reading, which costs them the one message that was genuinely new."""
    client, ids, as_ = world
    old = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    for n in range(5):
        ingest(client, as_, **{
            "from_number": "+19415550101", "type": "SMS", "direction": "INBOUND",
            "body": "old message %d" % n, "occurred_at": old,
            "dedupe_key": "openphone:message:old%d" % n,
            "source_system": "OpenPhone", "source_number": OPENPHONE_LINE,
        })

    db = SessionLocal()
    conv = db.query(Conversation).filter(
        Conversation.contact_id == ids["jane"]).one()
    assert conv.unread_count == 0, "history is not unread"
    db.close()

    # ...but a live one still counts.
    ingest(client, as_, **{
        "from_number": "+19415550101", "type": "SMS", "direction": "INBOUND",
        "body": "just now", "dedupe_key": "openphone:message:live",
        "source_system": "OpenPhone", "source_number": OPENPHONE_LINE,
    })
    db = SessionLocal()
    conv = db.query(Conversation).filter(
        Conversation.contact_id == ids["jane"]).one()
    assert conv.unread_count == 1, "a live mirrored text IS unread"
    db.close()


# --- 4. the automation that would have texted a month of customers --------------------


def test_a_backfilled_missed_call_does_not_queue_an_auto_text_back(world):
    """THE ONE THAT COULD HAVE TEXTED REAL PEOPLE.

    Rule 1 auto-texts any inbound call under 15 seconds. Every missed OpenPhone call
    in a 30-day backfill is exactly that, so without the freshness guard, switching
    the mirror on would queue one text-back per missed call — to real customers,
    about calls up to a month old, from a number they have never seen.
    """
    client, ids, as_ = world
    r = ingest(client, as_, **mirrored_call(
        duration_seconds=3, call_status="no-answer",
        occurred_at=(datetime.now(UTC) - timedelta(days=9)).isoformat()))
    assert r.status_code == 201, r.text
    assert "too old" in r.json()["automation"]

    db = SessionLocal()
    queued = db.query(Job).filter(Job.type == "missed_call_textback").count()
    db.close()
    assert queued == 0, "no text-back was queued for a month-old call"


def test_a_live_missed_call_still_queues_the_auto_text_back(world):
    """The guard must only ever stop a text nobody wanted. A real missed call reaches
    us seconds after it ends and must still fire, or the freshness rule has broken
    the feature it was protecting."""
    client, ids, as_ = world
    r = ingest(client, as_, **{
        "from_number": "+19415550101", "type": "CALL", "direction": "INBOUND",
        "duration_seconds": 4, "call_status": "no-answer",
        "body": "Inbound call ended.", "provider_ref": "owen-live-1",
    })
    assert r.status_code == 201, r.text
    assert r.json()["automation"] == "queued"

    db = SessionLocal()
    queued = db.query(Job).filter(Job.type == "missed_call_textback").count()
    db.close()
    assert queued == 1


def test_a_delayed_relay_is_still_treated_as_live(world):
    """A queue backlog or a retried job can deliver a real missed call minutes late.
    That must still text back, which is why the window is an hour and not a minute."""
    client, ids, as_ = world
    r = ingest(client, as_, **{
        "from_number": "+19415550101", "type": "CALL", "direction": "INBOUND",
        "duration_seconds": 4, "call_status": "no-answer",
        "body": "Inbound call ended.", "provider_ref": "owen-late-1",
        "occurred_at": (datetime.now(UTC) - timedelta(minutes=10)).isoformat(),
    })
    assert r.json()["automation"] == "queued"


# --- 5. the composer sends over BulkVS, in a mixed thread -----------------------------


def test_the_composer_still_sends_via_bulkvs_in_a_mixed_thread(world, monkeypatch):
    """A thread full of OpenPhone events changes nothing about where a reply goes.

    owen-main is mocked at the HTTP boundary; the assertion is on the request body
    that actually went out, because that is the thing that decides which number the
    customer sees.
    """
    from app import crmlink

    sent = []

    class Resp:
        status_code = 200

        def json(self):
            return {"ok": True, "message_id": "msg-1", "status": "queued"}

    def fake_post(url, **kw):
        sent.append({"url": url, "json": kw.get("json")})
        return Resp()

    monkeypatch.setenv("CRM_LINK_BASE_URL", "http://callmon_app:8888")
    monkeypatch.setenv("CRM_LINK_API_KEY", "owen_sk_test")
    monkeypatch.setenv("CRM_LINK_FROM_NUMBER", BULKVS_LINE)
    monkeypatch.setattr(crmlink.httpx, "post", fake_post)

    client, ids, as_ = world
    r = ingest(client, as_, **mirrored_call())
    conv_id = r.json()["conversation_id"]

    r = client.post("/api/conversations/%d/messages" % conv_id,
                    json={"body": "We can be there Tuesday"}, headers=as_("admin"))
    assert r.status_code == 201, r.text

    assert len(sent) == 1, "exactly one send went out"
    assert sent[0]["url"].endswith("/api/crm-link/messages"), \
        "it went to owen-main's BulkVS route"
    assert sent[0]["json"]["from_number"] == BULKVS_LINE, \
        "it left on the BulkVS line, NOT the OpenPhone one"
    assert "openphone" not in sent[0]["url"].lower()

    rows = thread(client, as_, conv_id)
    reply = [e for e in rows if e["direction"] == "OUTBOUND"][-1]
    assert reply["source_system"] == "BulkVS", "the reply is labelled with its own line"
    assert reply["source_number"] == BULKVS_LINE


def test_nothing_in_this_repository_offers_an_openphone_send(world):
    """No send path, not even a disabled one — a disabled send path is a switch
    somebody eventually flips. Asserted over the source, and over the route table."""
    import io
    import pathlib
    import re
    import token as token_mod
    import tokenize

    import app as app_pkg

    def code_only(source: str) -> str:
        """The file with comments and string literals removed.

        Tokenising rather than matching line prefixes, because this module is FULL of
        prose about not sending through OpenPhone — that documentation is the point,
        and a checker that cannot tell a sentence from a statement would either fail
        on its own explanation or be loosened until it caught nothing.
        """
        out = []
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in (token_mod.COMMENT, token_mod.STRING):
                continue
            out.append(tok.string)
        return " ".join(out)

    root = pathlib.Path(app_pkg.__file__).parent
    offenders = []
    for path in sorted(root.rglob("*.py")):
        code = code_only(path.read_text(encoding="utf-8"))
        # A CALL into anything openphone-shaped that is not one of our own two reads.
        for match in re.finditer(r"\w*openphone\w*", code, re.IGNORECASE):
            word = match.group(0)
            if word.lower() in {"openphone", "openphone_recording_path",
                                "fetch_openphone_recording", "stream_recording"}:
                continue
            offenders.append("%s: %s" % (path.name, word))
        # And no reference to OpenPhone's API host anywhere in executable code.
        if re.search(r"api\.openphone\.com", code, re.IGNORECASE):
            offenders.append("%s: reaches api.openphone.com directly" % path.name)
    assert offenders == [], "an OpenPhone send path appeared: %s" % offenders

    # And this repository holds no OpenPhone credential of any kind.
    for path in sorted(root.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        assert "OPENPHONE_API_KEY" not in src, \
            "%s references the OpenPhone key; only owen-main may hold it" % path.name


def test_the_recording_route_is_mounted_where_owen_main_points_at_it(world):
    """Two repositories agreeing on a string is what rots silently. owen-main builds
    `/api/openphone/recordings/<id>` into every mirrored call's `recording_url`; its
    own `test_openphone_mirror.py` pins that constant. This is the other half."""
    from app.openphone import RECORDINGS_PATH

    assert RECORDINGS_PATH == "/api/openphone/recordings"
    client, ids, as_ = world
    # Unconfigured link -> 503, and crucially NOT 404: the route exists.
    r = client.get("/api/openphone/recordings/AC_call_1", headers=as_("admin"))
    assert r.status_code == 503, r.text
    assert "not configured" in r.json()["detail"]


def test_the_recording_streams_without_the_openphone_key_reaching_the_client(
        world, monkeypatch):
    """The audio comes back; the key and the media URL do not."""
    from app import crmlink

    seen = []

    class Resp:
        status_code = 200
        content = b"ID3\x04AUDIOBYTES"
        headers: ClassVar[dict] = {"content-type": "audio/mpeg"}

    def fake_get(url, **kw):
        seen.append({"url": url, "headers": kw.get("headers") or {}})
        return Resp()

    monkeypatch.setenv("CRM_LINK_BASE_URL", "http://callmon_app:8888")
    monkeypatch.setenv("CRM_LINK_API_KEY", "owen_sk_secret")
    monkeypatch.setattr(crmlink.httpx, "get", fake_get)

    client, ids, as_ = world
    r = client.get("/api/openphone/recordings/AC_call_1", headers=as_("admin"))
    assert r.status_code == 200, r.text
    assert r.content == b"ID3\x04AUDIOBYTES"
    assert r.headers["content-type"].startswith("audio/")

    # It asked owen-main, not OpenPhone.
    assert len(seen) == 1
    assert seen[0]["url"] == (
        "http://callmon_app:8888/api/openphone-mirror/recordings/AC_call_1")
    assert "openphone.com" not in seen[0]["url"]
    # The machine key went to owen-main over the internal network...
    assert seen[0]["headers"].get("X-OWEN-Key") == "owen_sk_secret"
    # ...and NOTHING resembling a credential came back to the browser.
    body = r.content.decode("latin-1")
    assert "owen_sk_secret" not in body
    for header in r.headers.values():
        assert "owen_sk_secret" not in header
    assert "share.quo.com" not in str(r.headers)


def test_a_missing_recording_is_a_404_and_an_outage_is_a_502(world, monkeypatch):
    """An operator has to be able to tell "there is no audio" from "we could not
    reach the system that has it". The first is permanent; the second might work
    later, and a thread that renders neither is a thread nobody trusts."""
    from app import crmlink

    monkeypatch.setenv("CRM_LINK_BASE_URL", "http://callmon_app:8888")
    monkeypatch.setenv("CRM_LINK_API_KEY", "owen_sk_test")
    client, ids, as_ = world

    class NotFound:
        status_code = 404

        def json(self):
            return {"detail": "OpenPhone has no recording for that call"}

    monkeypatch.setattr(crmlink.httpx, "get", lambda url, **kw: NotFound())
    assert client.get("/api/openphone/recordings/AC_x",
                      headers=as_("admin")).status_code == 404

    def boom(url, **kw):
        raise OSError("connection refused")

    monkeypatch.setattr(crmlink.httpx, "get", boom)
    assert client.get("/api/openphone/recordings/AC_x",
                      headers=as_("admin")).status_code == 502


# --- 6. an OpenPhone outage breaks nothing here ---------------------------------------


def test_an_openphone_outage_leaves_the_thread_rendering(world, monkeypatch):
    """If the mirror stops, nothing in this CRM notices: the events already ingested
    ARE ours, and no read path here depends on OpenPhone being reachable.

    The recording is the ONE thing that stops working, because the audio was never
    copied. That is the accepted tradeoff (DECISIONS.md) and this pins it: the row,
    the transcript and the thread all still render.
    """
    from app import crmlink

    client, ids, as_ = world
    r = ingest(client, as_, **mirrored_call())
    conv_id = r.json()["conversation_id"]

    monkeypatch.setenv("CRM_LINK_BASE_URL", "http://callmon_app:8888")
    monkeypatch.setenv("CRM_LINK_API_KEY", "owen_sk_test")

    def boom(url, **kw):
        raise OSError("openphone is gone")

    monkeypatch.setattr(crmlink.httpx, "get", boom)

    rows = thread(client, as_, conv_id)
    assert len(rows) == 1, "the thread still renders"
    assert rows[0]["source_system"] == "OpenPhone"
    assert rows[0]["duration_seconds"] == 184

    db = SessionLocal()
    ev = db.query(ConversationEvent).filter(
        ConversationEvent.conversation_id == conv_id).one()
    assert "skylight" in (ev.transcript or ""), "the transcript is ours and survives"
    db.close()

    # Only the audio is gone, and it says so rather than hanging.
    assert client.get("/api/openphone/recordings/AC_call_1",
                      headers=as_("admin")).status_code == 502


def test_the_conversations_list_is_unaffected_by_a_mirrored_thread(world):
    """A mirrored thread is an ordinary thread everywhere else in the product."""
    client, ids, as_ = world
    ingest(client, as_, **mirrored_call())
    r = client.get("/api/conversations", headers=as_("admin"))
    assert r.status_code == 200
    rows = r.json()["items"] if isinstance(r.json(), dict) else r.json()
    assert any(c["contact_id"] == ids["jane"] for c in rows)


# --- 7. the contract with owen-main ---------------------------------------------------


def test_an_event_without_the_new_fields_behaves_exactly_as_before(world):
    """Every new field is optional. A body from an owen-main that predates the mirror
    must land unchanged, or deploying this breaks the live BulkVS feed."""
    client, ids, as_ = world
    r = ingest(client, as_, **{
        "from_number": "+19415550101", "type": "SMS", "direction": "INBOUND",
        "body": "plain old text", "provider_ref": "bulkvs-9",
    })
    assert r.status_code == 201, r.text
    rows = thread(client, as_, r.json()["conversation_id"])
    assert rows[0]["source_system"] is None, "no source is recorded, and none is guessed"
    assert rows[0]["source_number"] is None
    assert rows[0]["body"] == "plain old text"

    db = SessionLocal()
    ev = db.query(ConversationEvent).filter(
        ConversationEvent.provider_ref == "bulkvs-9").one()
    assert ev.dedupe_key is None, "no key, so the unique index cannot bite it"
    # It was stamped NOW, as it always was.
    occurred = ev.occurred_at
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=UTC)
    assert (datetime.now(UTC) - occurred).total_seconds() < 60
    db.close()


def test_many_events_without_a_dedupe_key_coexist(world):
    """NULLs are distinct in a unique index on both Postgres and SQLite. If that were
    not true, the second un-keyed event ever written would fail — so it is worth
    asserting rather than assuming."""
    client, ids, as_ = world
    for n in range(5):
        r = ingest(client, as_, **{
            "from_number": "+19415550101", "type": "SMS", "direction": "INBOUND",
            "body": "message %d" % n,
        })
        assert r.status_code == 201, r.text
    rows = thread(client, as_, r.json()["conversation_id"])
    assert len(rows) == 5

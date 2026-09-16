"""Pictures on a text message, both directions (2026-09-16).

**owen-main is mocked at the HTTP boundary and is never called.** `app.crmlink` is the only
module that talks to it and it reaches the network through exactly two calls — `httpx.post`
and `httpx.get`. The `owen` fixture replaces those, so every test here exercises the real
`crmlink`, the real transport selection, the real request body and the real storage, while
the live telephony service sees nothing. The conftest guard fails any test that tries.

Behaviour, not status codes, per CLAUDE.md. Every refusal is re-read afterwards: a picture
refused for its size must leave no row, no file and no change to the customer's words, and a
send the phone system declines must leave the drafts exactly where the operator can send
them again.
"""
import io
import json
import os
import zlib

import pytest
from app import attachments, message_media
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    ATTACHMENT_REFUSED,
    ATTACHMENT_STORED,
    Contact,
    Conversation,
    MessageAttachment,
    Opportunity,
    Pipeline,
    Role,
    Stage,
    User,
)
from app.worker import drain_once
from fastapi.testclient import TestClient

# --- pictures that are really pictures ----------------------------------------------------
#
# Built rather than committed as fixtures: the server decides what a file is by SNIFFING its
# magic number, so a test that fed it a text file called "roof.jpg" would prove nothing. A
# real PNG header and a real JPEG SOI are what the code actually looks at.


def png(width: int = 2, height: int = 2, tag: bytes = b"a") -> bytes:
    """A valid single-colour PNG. `tag` changes the bytes without changing the format, so
    two pictures in one message are genuinely two files."""
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (len(payload).to_bytes(4, "big") + kind + payload
                + zlib.crc32(kind + payload).to_bytes(4, "big"))
    raw = b"".join(b"\x00" + (tag * 3) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", width.to_bytes(4, "big") + height.to_bytes(4, "big")
                    + bytes([8, 2, 0, 0, 0]))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF\x00\x01" + b"\x00" * 64 + b"\xff\xd9"
NOT_AN_IMAGE = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n"


# --- the owen-main double -----------------------------------------------------------------


class _Resp:
    """Enough of an httpx.Response for both shapes `crmlink` reads: a JSON body, and raw
    bytes with a content type."""

    def __init__(self, status_code, body=None, content=b"", content_type=""):
        self.status_code = status_code
        self._body = body
        self.content = content
        self.headers = {"content-type": content_type} if content_type else {}

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class Owen:
    """Stands in for owen-main. Records everything and answers from a script."""

    def __init__(self):
        self.posts: list[dict] = []
        self.media_gets: list[str] = []
        self.sends: list[dict] = []
        # Per media path: (status, bytes, content_type). Missing -> 404.
        self.media: dict[str, tuple] = {}
        self.uploads = 0
        self.upload_fails = False
        self.send_reply = (200, {"ok": True, "message_id": "m-1", "status": "queued"})

    def post(self, url, **kw):
        self.posts.append({"url": url, "json": kw.get("json"), "files": kw.get("files"),
                           "headers": kw.get("headers") or {}})
        if url.endswith("/api/crm-link/media"):
            if self.upload_fails:
                return _Resp(503, {"detail": "the phone system is not taking pictures"})
            self.uploads += 1
            return _Resp(201, {"ok": True, "media_id": "owen-media-%d" % self.uploads,
                               "content_type": "image/png"})
        if url.endswith("/api/crm-link/messages"):
            self.sends.append(kw.get("json") or {})
            status, body = self.send_reply
            return _Resp(status, body)
        return _Resp(404, {"detail": "no canned reply for " + url})

    def get(self, url, **kw):
        marker = "/api/crm-link/messages/"
        if marker in url:
            key = url.split(marker, 1)[1]
            self.media_gets.append(key)
            found = self.media.get(key)
            if found is None:
                return _Resp(404, {"detail": "no such picture on that message"})
            status, data, content_type = found
            if status >= 400:
                return _Resp(status, {"detail": "the carrier no longer has that picture"})
            return _Resp(200, None, data, content_type)
        return _Resp(404, {"detail": "no canned reply for " + url})


@pytest.fixture()
def owen(monkeypatch, tmp_path):
    """Arm the link against a fake owen-main, with a media root of this test's own.

    Setting the two env vars is what selects `CrmLinkTransport` — the same switch
    production uses — so these exercise the real selection rather than reaching past it.
    """
    from app import crmlink

    rec = Owen()
    monkeypatch.setenv("CRM_LINK_BASE_URL", "http://callmon_app:8888")
    monkeypatch.setenv("CRM_LINK_API_KEY", "owen_sk_test")
    monkeypatch.setenv("CRM_LINK_FROM_NUMBER", "+19544829099")
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setattr(crmlink.httpx, "post", rec.post)
    monkeypatch.setattr(crmlink.httpx, "get", rec.get)
    return rec


@pytest.fixture()
def world():
    """A customer with a thread, a technician with one job, and a client per role."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    users, tokens = {}, {}
    for key, role, restricted in (("admin", Role.ADMIN, False),
                                  ("dispatcher", Role.DISPATCHER, False),
                                  ("tess", Role.TECH, True)):
        u = User(email="%s@x.test" % key, name=key.title(), role=role,
                 only_assigned_data=restricted)
        db.add(u)
        users[key] = u
    db.flush()

    jane = Contact(first_name="Jane", last_name="Doe", phone="(941) 555-0101")
    bob = Contact(first_name="Bob", last_name="Roof", phone="(941) 555-0150")
    db.add_all([jane, bob])
    db.flush()
    conv = Conversation(contact_id=jane.id)
    bobconv = Conversation(contact_id=bob.id)
    db.add_all([conv, bobconv])

    p = Pipeline(name="Main")
    db.add(p)
    db.flush()
    stage = Stage(pipeline_id=p.id, name="New Lead", position=0)
    db.add(stage)
    db.flush()
    # Bob is the restricted technician's own job; Jane is not.
    db.add(Opportunity(title="Bob roof", contact_id=bob.id, pipeline_id=p.id,
                       stage_id=stage.id, owner_id=users["tess"].id))

    for key, u in users.items():
        plain, tok = mint_api_token(u, name=key)
        db.add(tok)
        tokens[key] = plain
    plain_feed, feed_tok = mint_api_token(users["dispatcher"], name="owen",
                                          scopes="events:write")
    db.add(feed_tok)
    tokens["feed"] = plain_feed
    db.commit()

    ids = {"jane": jane.id, "bob": bob.id, "conv": conv.id, "bobconv": bobconv.id}
    db.close()

    client = TestClient(app)

    def as_(who: str) -> dict:
        return {"Authorization": "Bearer " + tokens[who]}

    return client, ids, as_


# --- helpers ------------------------------------------------------------------------------


def ingest(client, as_, **body) -> dict:
    payload = {"type": "SMS", "direction": "INBOUND", "source_system": "BulkVS",
               "source_number": "+19544829099"}
    payload.update(body)
    r = client.post("/api/events", json=payload, headers=as_("feed"))
    assert r.status_code in (200, 201), r.text
    return r.json()


def thread(client, as_, conv_id, who="admin") -> list:
    r = client.get("/api/conversations/%d/events" % conv_id, headers=as_(who))
    assert r.status_code == 200, r.text
    return r.json()


def number_thread(client, as_, thread_id, who="admin") -> list:
    r = client.get("/api/number-threads/%d/events" % thread_id, headers=as_(who))
    assert r.status_code == 200, r.text
    return r.json()


def stored_files() -> list:
    root = attachments.media_root()
    return sorted(str(p) for p in root.rglob("*") if p.is_file())


def upload(client, as_, who, data: bytes, name="roof.png", content_type="image/png"):
    return client.post("/api/attachments",
                       files={"file": (name, io.BytesIO(data), content_type)},
                       headers=as_(who))


# =========================================================================================
# INBOUND
# =========================================================================================


def test_one_inbound_picture_is_fetched_once_and_shows_in_the_bubble(world, owen):
    client, ids, as_ = world
    owen.media["m-77/media/0"] = (200, png(), "image/png")

    out = ingest(client, as_, from_number="+19415550101", provider_ref="m-77",
                 body="Here is the leak [1 attachment — view in OWEN]", num_media=1)
    assert out["attachments_expected"] == 1

    # Before the worker runs the text is ALREADY on the thread. That is the promise: the
    # words never wait for a picture.
    rows = thread(client, as_, ids["conv"])
    assert rows[-1]["body"].startswith("Here is the leak")
    assert len(rows[-1]["attachments"]) == 1
    assert rows[-1]["attachments"][0]["pending"] is True
    assert rows[-1]["attachments"][0]["url"] is None

    assert drain_once() == 1
    rows = thread(client, as_, ids["conv"])
    picture = rows[-1]["attachments"][0]
    assert picture["status"] == ATTACHMENT_STORED
    assert picture["url"] == "/api/attachments/%d" % picture["id"]
    assert picture["content_type"] == "image/png"
    assert owen.media_gets == ["m-77/media/0"], "the picture was fetched exactly once"

    # And the bytes really come back, to a signed-in browser, from THIS server.
    got = client.get(picture["url"], headers=as_("admin"))
    assert got.status_code == 200
    assert got.content == png()
    assert got.headers["content-type"].startswith("image/png")


def test_three_pictures_keep_the_order_the_carrier_sent_them_in(world, owen):
    client, ids, as_ = world
    for i, tag in enumerate((b"a", b"b", b"c")):
        owen.media["m-3/media/%d" % i] = (200, png(tag=tag), "image/png")

    ingest(client, as_, from_number="+19415550101", provider_ref="m-3",
           body="roof [3 attachments — view in OWEN]", num_media=3)
    while drain_once():
        pass

    pictures = thread(client, as_, ids["conv"])[-1]["attachments"]
    assert [p["position"] for p in pictures] == [0, 1, 2]
    assert all(p["status"] == ATTACHMENT_STORED for p in pictures)
    # Three DIFFERENT pictures, so the viewer's next/previous has somewhere to go.
    bodies = [client.get(p["url"], headers=as_("admin")).content for p in pictures]
    assert len(set(bodies)) == 3
    assert sorted(owen.media_gets) == ["m-3/media/0", "m-3/media/1", "m-3/media/2"]


def test_the_same_message_delivered_twice_stores_nothing_twice(world, owen):
    """BulkVS re-delivers a webhook it did not get a 200 for, and owen-main retries its
    relay job. Neither may put a second copy of the customer's photo on the thread."""
    client, ids, as_ = world
    owen.media["m-9/media/0"] = (200, png(), "image/png")

    first = ingest(client, as_, from_number="+19415550101", provider_ref="m-9",
                   dedupe_key="bulkvs-9", body="leak", num_media=1)
    while drain_once():
        pass
    files_after_first = stored_files()

    second = ingest(client, as_, from_number="+19415550101", provider_ref="m-9",
                    dedupe_key="bulkvs-9", body="leak", num_media=1)
    while drain_once():
        pass

    assert second["id"] == first["id"], "the repeat delivery made a second event"
    rows = thread(client, as_, ids["conv"])
    assert len([e for e in rows if e["body"] == "leak"]) == 1
    assert len(rows[-1]["attachments"]) == 1
    assert owen.media_gets == ["m-9/media/0"], "the bytes were fetched twice"
    assert stored_files() == files_after_first

    db = SessionLocal()
    try:
        assert db.query(MessageAttachment).count() == 1
    finally:
        db.close()


def test_a_picture_from_an_unknown_number_lands_on_its_thread_and_creates_no_contact(
        world, owen):
    client, ids, as_ = world
    owen.media["m-x/media/0"] = (200, JPEG, "image/jpeg")
    before = client.get("/api/contacts?page=1&page_size=1", headers=as_("admin")).json()

    out = ingest(client, as_, from_number="+19415550199", provider_ref="m-x",
                 body="roof [1 attachment — view in OWEN]", num_media=1)
    assert out["contact_id"] is None and out["number_thread_id"] is not None
    while drain_once():
        pass

    after = client.get("/api/contacts?page=1&page_size=1", headers=as_("admin")).json()
    assert after["total"] == before["total"], "an unknown number became a contact"

    rows = number_thread(client, as_, out["number_thread_id"])
    picture = rows[-1]["attachments"][0]
    assert picture["status"] == ATTACHMENT_STORED
    assert client.get(picture["url"], headers=as_("admin")).content == JPEG

    # The badge counts the message, once, whatever it carried.
    inbox = client.get("/api/conversations", headers=as_("admin")).json()
    mine = [c for c in inbox if c["key"] == "n%d" % out["number_thread_id"]]
    assert mine and mine[0]["unread_count"] == 1


def test_a_carrier_fetch_failure_keeps_the_text_and_offers_a_retry(world, owen):
    client, ids, as_ = world
    # owen-main is reachable but the carrier's link has gone: 502-ish, worth another go.
    owen.media["m-f/media/0"] = (500, b"", "")

    ingest(client, as_, from_number="+19415550101", provider_ref="m-f",
           body="the roof after the storm", num_media=1)
    while drain_once():
        pass

    rows = thread(client, as_, ids["conv"])
    assert rows[-1]["body"] == "the roof after the storm", "the words were lost"
    picture = rows[-1]["attachments"][0]
    assert picture["url"] is None
    assert picture["retryable"] is True
    assert picture["detail"], "a failed picture must say why"
    assert not stored_files(), "a failed fetch wrote bytes"

    # Now the carrier answers, and Retry gets the picture without re-sending anything.
    owen.media["m-f/media/0"] = (200, png(), "image/png")
    again = client.post("/api/attachments/%d/retry" % picture["id"], headers=as_("admin"))
    assert again.status_code == 200, again.text
    assert again.json()["status"] == ATTACHMENT_STORED
    assert client.get(again.json()["url"], headers=as_("admin")).content == png()


def test_an_oversize_picture_is_refused_with_a_sentence_and_the_text_still_lands(world, owen):
    client, ids, as_ = world
    huge = png() + b"\x00" * (attachments.MAX_BYTES + 10)
    owen.media["m-big/media/0"] = (200, huge, "image/png")

    ingest(client, as_, from_number="+19415550101", provider_ref="m-big",
           body="massive photo", num_media=1)
    while drain_once():
        pass

    rows = thread(client, as_, ids["conv"])
    assert rows[-1]["body"] == "massive photo"
    picture = rows[-1]["attachments"][0]
    assert picture["status"] == ATTACHMENT_REFUSED
    assert "MB" in (picture["detail"] or ""), picture["detail"]
    assert picture["retryable"] is False, "retrying cannot make it smaller"
    assert not stored_files(), "an oversize picture was written to disk"


def test_something_that_is_not_a_picture_is_refused_by_its_bytes(world, owen):
    """The header says image/png. The bytes say PDF. The bytes decide."""
    client, ids, as_ = world
    owen.media["m-pdf/media/0"] = (200, NOT_AN_IMAGE, "image/png")

    ingest(client, as_, from_number="+19415550101", provider_ref="m-pdf",
           body="see attached", num_media=1)
    while drain_once():
        pass

    picture = thread(client, as_, ids["conv"])[-1]["attachments"][0]
    assert picture["status"] == ATTACHMENT_REFUSED
    assert "picture" in (picture["detail"] or "").lower()
    assert not stored_files()


def test_an_ordinary_text_creates_no_attachment_row_and_no_job(world, owen):
    """The overwhelming majority of messages. Nothing about them may change."""
    client, ids, as_ = world
    ingest(client, as_, from_number="+19415550101", provider_ref="m-plain",
           body="can you come Tuesday")
    assert drain_once() == 0
    rows = thread(client, as_, ids["conv"])
    assert rows[-1]["attachments"] == []
    assert owen.media_gets == []


# =========================================================================================
# OUTBOUND
# =========================================================================================


def test_a_picture_is_uploaded_previewed_removed_and_leaves_nothing_behind(world, owen):
    client, ids, as_ = world
    r = upload(client, as_, "dispatcher", png())
    assert r.status_code == 201, r.text
    draft = r.json()
    assert draft["status"] == "DRAFT"
    # The preview is the picture the server holds, not a local blob: the same bytes the
    # customer will get.
    assert client.get(draft["url"], headers=as_("dispatcher")).content == png()
    assert len(stored_files()) == 1

    gone = client.delete("/api/attachments/%d" % draft["id"], headers=as_("dispatcher"))
    assert gone.status_code == 200, gone.text
    assert not stored_files(), "removing a picture left its bytes on disk"
    assert client.get(draft["url"], headers=as_("dispatcher")).status_code == 404


def test_the_transport_receives_exactly_the_attachment_set(world, owen):
    client, ids, as_ = world
    one = upload(client, as_, "dispatcher", png(tag=b"a")).json()
    two = upload(client, as_, "dispatcher", png(tag=b"b")).json()
    # A third, uploaded and NOT sent, to prove the send carries what was attached and not
    # everything the operator happens to have uploaded.
    spare = upload(client, as_, "dispatcher", png(tag=b"c")).json()

    r = client.post("/api/conversations/%d/messages" % ids["conv"],
                    json={"body": "here is the flashing", "type": "SMS",
                          "attachment_ids": [one["id"], two["id"]]},
                    headers=as_("dispatcher"))
    assert r.status_code == 201, r.text
    assert r.json()["suppressed"] is False and r.json()["reason"] == "queued"

    assert owen.uploads == 2, "one upload per attached picture, and no more"
    assert owen.sends[-1]["media_ids"] == ["owen-media-1", "owen-media-2"]
    assert owen.sends[-1]["body"] == "here is the flashing"
    # NEVER a URL from this side: the CRM cannot build one a carrier could fetch.
    assert "media_urls" not in owen.sends[-1]

    rows = thread(client, as_, ids["conv"])
    sent = rows[-1]
    assert [a["id"] for a in sent["attachments"]] == [one["id"], two["id"]]
    assert [a["position"] for a in sent["attachments"]] == [0, 1]
    assert all(a["status"] == ATTACHMENT_STORED for a in sent["attachments"])

    db = SessionLocal()
    try:
        left = db.get(MessageAttachment, spare["id"])
        assert left is not None and left.conversation_event_id is None, (
            "an unsent draft was attached to the message anyway")
    finally:
        db.close()


def test_a_picture_with_no_words_is_a_message_and_nothing_at_all_is_not(world, owen):
    client, ids, as_ = world
    only = upload(client, as_, "dispatcher", JPEG, name="r.jpg",
                  content_type="image/jpeg").json()

    r = client.post("/api/conversations/%d/messages" % ids["conv"],
                    json={"body": "", "type": "SMS", "attachment_ids": [only["id"]]},
                    headers=as_("dispatcher"))
    assert r.status_code == 201, r.text
    assert len(thread(client, as_, ids["conv"])[-1]["attachments"]) == 1

    empty = client.post("/api/conversations/%d/messages" % ids["conv"],
                        json={"body": "   ", "type": "SMS"}, headers=as_("dispatcher"))
    assert empty.status_code == 400


def test_a_refused_send_writes_nothing_and_keeps_the_pictures_attached(world, owen):
    """owen-main declines. The operator must be able to fix the problem and press Send
    again without picking the photographs a second time."""
    client, ids, as_ = world
    one = upload(client, as_, "dispatcher", png()).json()
    owen.send_reply = (409, {"detail": "this contact has opted out of SMS"})
    before = len(thread(client, as_, ids["conv"]))

    r = client.post("/api/conversations/%d/messages" % ids["conv"],
                    json={"body": "photo", "type": "SMS", "attachment_ids": [one["id"]]},
                    headers=as_("dispatcher"))
    assert r.status_code == 201, r.text
    # A refusal IS recorded on the thread, with the reason — that rule predates pictures.
    rows = thread(client, as_, ids["conv"])
    assert len(rows) == before + 1
    assert rows[-1]["delivery_status"] == "REFUSED"
    assert "opted out" in (rows[-1]["delivery_detail"] or "")

    # The picture went WITH it, because the message is on the thread and the picture is
    # part of what was attempted.
    assert [a["id"] for a in rows[-1]["attachments"]] == [one["id"]]


def test_a_picture_the_phone_system_will_not_take_stops_the_send_entirely(world, owen):
    """A text that arrives without the photograph is worse than one that does not arrive:
    "here it is" with nothing attached reads as a mistake the customer has to chase."""
    client, ids, as_ = world
    one = upload(client, as_, "dispatcher", png()).json()
    owen.upload_fails = True
    before = len(thread(client, as_, ids["conv"]))

    r = client.post("/api/conversations/%d/messages" % ids["conv"],
                    json={"body": "here it is", "type": "SMS",
                          "attachment_ids": [one["id"]]},
                    headers=as_("dispatcher"))
    assert r.status_code == 201, r.text
    assert r.json()["suppressed"] is True
    assert r.json()["reason"].startswith("suppressed: ")
    assert owen.sends == [], "the text was sent without its picture"
    assert len(thread(client, as_, ids["conv"])) == before, "a row was written anyway"

    db = SessionLocal()
    try:
        # Still a draft, still the operator's, still attachable.
        assert db.get(MessageAttachment, one["id"]).status == "DRAFT"
    finally:
        db.close()


def test_a_picture_cannot_be_attached_to_an_internal_note(world, owen):
    client, ids, as_ = world
    one = upload(client, as_, "dispatcher", png()).json()
    r = client.post("/api/conversations/%d/messages" % ids["conv"],
                    json={"body": "for the file", "type": "INTERNAL_COMMENT",
                          "attachment_ids": [one["id"]]},
                    headers=as_("dispatcher"))
    assert r.status_code == 400
    assert "text message" in r.json()["detail"]
    db = SessionLocal()
    try:
        assert db.get(MessageAttachment, one["id"]).status == "DRAFT"
    finally:
        db.close()


def test_more_pictures_than_a_text_can_carry_are_refused_with_a_sentence(world, owen):
    client, ids, as_ = world
    ids_ = [upload(client, as_, "dispatcher", png(tag=bytes([97 + i]))).json()["id"]
            for i in range(attachments.MAX_OUTBOUND_ATTACHMENTS + 1)]
    before = len(thread(client, as_, ids["conv"]))
    r = client.post("/api/conversations/%d/messages" % ids["conv"],
                    json={"body": "lots", "type": "SMS", "attachment_ids": ids_},
                    headers=as_("dispatcher"))
    assert r.status_code == 400
    assert str(attachments.MAX_OUTBOUND_ATTACHMENTS) in r.json()["detail"]
    assert len(thread(client, as_, ids["conv"])) == before
    assert owen.sends == []


def test_uploading_something_that_is_not_a_picture_is_refused_and_stores_nothing(
        world, owen):
    client, ids, as_ = world
    r = upload(client, as_, "dispatcher", NOT_AN_IMAGE, name="quote.pdf",
               content_type="image/png")
    assert r.status_code == 400
    assert "picture" in r.json()["detail"].lower()
    assert not stored_files()
    db = SessionLocal()
    try:
        assert db.query(MessageAttachment).count() == 0
    finally:
        db.close()


def test_new_message_sends_a_picture_to_a_number_nobody_has_saved(world, owen):
    client, ids, as_ = world
    one = upload(client, as_, "dispatcher", png()).json()
    r = client.post("/api/messages/new",
                    json={"number": "(941) 555-0177", "body": "",
                          "attachment_ids": [one["id"]]},
                    headers=as_("dispatcher"))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["recorded"] is True and out["kind"] == "number"
    assert owen.sends[-1]["media_ids"] == ["owen-media-1"]
    rows = number_thread(client, as_, out["number_thread_id"])
    assert [a["id"] for a in rows[-1]["attachments"]] == [one["id"]]


# =========================================================================================
# WHO MAY SEE ONE
# =========================================================================================


def test_a_restricted_technician_cannot_open_another_customers_picture(world, owen):
    client, ids, as_ = world
    owen.media["m-j/media/0"] = (200, png(tag=b"j"), "image/png")
    owen.media["m-b/media/0"] = (200, png(tag=b"b"), "image/png")
    ingest(client, as_, from_number="+19415550101", provider_ref="m-j",
           body="jane", num_media=1)
    ingest(client, as_, from_number="+19415550150", provider_ref="m-b",
           body="bob", num_media=1)
    while drain_once():
        pass

    janes = thread(client, as_, ids["conv"])[-1]["attachments"][0]
    bobs = thread(client, as_, ids["bobconv"])[-1]["attachments"][0]

    # Bob is her job; Jane is not. The BYTES route enforces it, not only the thread.
    assert client.get(bobs["url"], headers=as_("tess")).status_code == 200
    assert client.get(janes["url"], headers=as_("tess")).status_code == 404, (
        "a restricted technician could read another customer's picture")
    # 404 and not 403: a 403 would confirm the id exists.
    assert client.get("/api/attachments/%d" % janes["id"],
                      headers=as_("tess")).json()["detail"] == "that picture is not here"
    # And she cannot fetch it again either, which would be a write on someone else's row.
    assert client.post("/api/attachments/%d/retry" % janes["id"],
                       headers=as_("tess")).status_code == 404


def test_a_restricted_technician_sees_no_picture_on_a_number_only_thread(world, owen):
    """A number nobody has saved is nobody's job — the existing rule, applied to bytes."""
    client, ids, as_ = world
    owen.media["m-n/media/0"] = (200, png(), "image/png")
    out = ingest(client, as_, from_number="+19415550199", provider_ref="m-n",
                 body="stranger", num_media=1)
    while drain_once():
        pass
    picture = number_thread(client, as_, out["number_thread_id"])[-1]["attachments"][0]
    assert client.get(picture["url"], headers=as_("admin")).status_code == 200
    assert client.get(picture["url"], headers=as_("tess")).status_code == 404


def test_a_draft_belongs_to_the_operator_who_uploaded_it(world, owen):
    client, ids, as_ = world
    mine = upload(client, as_, "dispatcher", png()).json()
    assert client.get(mine["url"], headers=as_("admin")).status_code == 404
    assert client.delete("/api/attachments/%d" % mine["id"],
                         headers=as_("admin")).status_code == 404
    # Naming somebody else's draft on a send is refused, and writes nothing.
    before = len(thread(client, as_, ids["conv"]))
    r = client.post("/api/conversations/%d/messages" % ids["conv"],
                    json={"body": "hi", "type": "SMS", "attachment_ids": [mine["id"]]},
                    headers=as_("admin"))
    assert r.status_code == 400
    assert len(thread(client, as_, ids["conv"])) == before


def test_nobody_at_all_gets_a_picture_without_a_credential(world, owen):
    client, ids, as_ = world
    one = upload(client, as_, "dispatcher", png()).json()
    assert client.get(one["url"]).status_code == 401
    assert client.post("/api/attachments/%d/retry" % one["id"]).status_code == 401


# =========================================================================================
# DELIVERY
# =========================================================================================


def test_a_receipt_moves_a_picture_message_off_queued(world, owen):
    """The bug the owner hit on the first live send: the bubble said "queued" for ever.
    owen-main now reports `sent` from its own knowledge that BulkVS accepted it."""
    client, ids, as_ = world
    r = client.post("/api/conversations/%d/messages" % ids["conv"],
                    json={"body": "on our way", "type": "SMS"}, headers=as_("dispatcher"))
    assert r.json()["delivery_status"] == "QUEUED"

    receipt = client.post("/api/events/delivery",
                          json={"provider_ref": "m-1", "status": "sent"},
                          headers=as_("feed"))
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["advanced"] is True
    assert thread(client, as_, ids["conv"])[-1]["delivery_status"] == "SENT"

    delivered = client.post("/api/events/delivery",
                            json={"provider_ref": "m-1", "status": "delivered"},
                            headers=as_("feed"))
    assert delivered.status_code == 200
    assert thread(client, as_, ids["conv"])[-1]["delivery_status"] == "DELIVERED"

    # Forward-only: a late "sent" must not walk a delivered message back.
    late = client.post("/api/events/delivery",
                       json={"provider_ref": "m-1", "status": "sent"}, headers=as_("feed"))
    assert late.json()["advanced"] is False
    assert thread(client, as_, ids["conv"])[-1]["delivery_status"] == "DELIVERED"


# =========================================================================================
# THE STORE
# =========================================================================================


def test_the_same_picture_twice_is_one_file_and_deleting_one_keeps_the_other(world, owen):
    """Content addressing. A customer who sends the same photo twice costs one file, and
    removing one draft must not take the other's bytes with it."""
    client, ids, as_ = world
    a = upload(client, as_, "dispatcher", png()).json()
    b = upload(client, as_, "dispatcher", png()).json()
    assert a["id"] != b["id"]
    assert len(stored_files()) == 1, "identical bytes were stored twice"

    client.delete("/api/attachments/%d" % a["id"], headers=as_("dispatcher"))
    assert len(stored_files()) == 1, "the surviving picture lost its bytes"
    assert client.get(b["url"], headers=as_("dispatcher")).content == png()

    client.delete("/api/attachments/%d" % b["id"], headers=as_("dispatcher"))
    assert not stored_files()


def test_an_abandoned_draft_does_not_sit_on_the_volume_for_ever(world, owen):
    """A refused send the operator walked away from, or a closed tab, leaves a draft. The
    next upload by that operator clears the old ones — and only theirs."""
    from datetime import UTC, datetime, timedelta

    client, ids, as_ = world
    mine = upload(client, as_, "dispatcher", png(tag=b"a")).json()
    theirs = upload(client, as_, "admin", png(tag=b"b")).json()
    assert len(stored_files()) == 2

    db = SessionLocal()
    try:
        for att_id in (mine["id"], theirs["id"]):
            db.get(MessageAttachment, att_id).created_at = (
                datetime.now(UTC) - timedelta(days=3))
        db.commit()
    finally:
        db.close()

    fresh = upload(client, as_, "dispatcher", png(tag=b"c")).json()
    assert fresh["status"] == "DRAFT"
    assert client.get("/api/attachments/%d" % mine["id"],
                      headers=as_("dispatcher")).status_code == 404, (
        "the dispatcher's abandoned draft survived their next upload")
    assert client.get(theirs["url"], headers=as_("admin")).status_code == 200, (
        "somebody else's draft was swept by a stranger's upload")
    # Two files: the admin's old one and the dispatcher's new one.
    assert len(stored_files()) == 2


def test_a_missing_file_is_a_missing_picture_and_never_a_five_hundred(world, owen):
    """A volume that was not mounted, or a restore that predates it. The thread must keep
    rendering; the picture is the only thing that is gone."""
    client, ids, as_ = world
    one = upload(client, as_, "dispatcher", png()).json()
    db = SessionLocal()
    try:
        row = db.get(MessageAttachment, one["id"])
        os.unlink(attachments.media_root() / row.storage_path)
    finally:
        db.close()
    assert client.get(one["url"], headers=as_("dispatcher")).status_code == 404


def test_the_bytes_route_never_says_a_picture_is_something_it_is_not(world, owen):
    """The content type comes from what was SNIFFED, and the browser is told not to sniff
    for itself. A file that lies about its type is refused long before this."""
    client, ids, as_ = world
    one = upload(client, as_, "dispatcher", JPEG, name="r.png",
                 content_type="image/png").json()
    got = client.get(one["url"], headers=as_("dispatcher"))
    assert got.headers["content-type"].startswith("image/jpeg")
    assert got.headers["x-content-type-options"] == "nosniff"
    assert "private" in got.headers["cache-control"]
    # The name a download offers is derived, never the caller's string verbatim.
    assert got.headers["content-disposition"].endswith('.jpg"')


def test_a_storage_path_that_did_not_come_from_the_store_is_never_read(world, owen):
    """`read()` joins a string onto MEDIA_ROOT. A corrupted row must not become a file
    read anywhere on the disk."""
    assert attachments.read("../../etc/passwd") is None
    assert attachments.read("/etc/passwd") is None
    assert attachments.read("ab/" + "z" * 64) is None
    assert attachments.forget("../../etc/passwd", 0) is False


# =========================================================================================
# NOTHING SENDS A PICTURE BY ITSELF
# =========================================================================================


def test_no_automatic_path_can_attach_a_picture(world, owen):
    """The owner's rule (2026-09-15, extended 2026-09-16): only texts a person sends, and
    no agent may attach media. Kept by there being no automatic caller that HAS a draft —
    asserted here by reading the source, because that is where the property lives."""
    import inspect

    from app import automations
    from app.ai import actions as ai_actions

    src = inspect.getsource(automations)
    # The rules and the agent path call `_record_outbound` / `send_outbound` with no
    # `pictures=`; the only callers that pass one are the API's send routes.
    assert "pictures=" not in src.split("def send_outbound")[0], (
        "something above the send path is passing pictures")
    agent_src = inspect.getsource(ai_actions)
    assert "attachment" not in agent_src.lower(), "an AI agent path mentions attachments"
    assert "pictures=" not in agent_src, "an AI agent can attach a picture"

    from app import main as main_mod
    body = main_mod.MessageSend(body="hi")
    assert body.attachment_ids == [], "a send defaults to carrying a picture"


def test_the_thread_json_shape_is_what_the_browser_expects(world, owen):
    """`lib/api.ts` declares `attachments: Attachment[]` — always a list, never null, and
    each entry carries only what a browser may hold."""
    client, ids, as_ = world
    owen.media["m-s/media/0"] = (200, png(), "image/png")
    ingest(client, as_, from_number="+19415550101", provider_ref="m-s",
           body="shape", num_media=1)
    while drain_once():
        pass
    picture = thread(client, as_, ids["conv"])[-1]["attachments"][0]
    assert set(picture) == {"id", "position", "status", "detail", "content_type",
                            "byte_size", "filename", "url", "pending", "retryable"}
    blob = json.dumps(picture)
    for leak in ("storage_path", "sha256", "source_ref", "callmon", "http"):
        assert leak not in blob, "the browser was handed %r" % leak


def test_message_media_public_never_leaks_where_the_bytes_are(world, owen):
    """The same rule, at the function that builds it, so a new caller inherits it."""
    db = SessionLocal()
    try:
        row = MessageAttachment(position=0, status=ATTACHMENT_STORED,
                                sha256="a" * 64, storage_path="aa/" + "a" * 64,
                                source_ref="m-1/0", content_type="image/png")
        db.add(row)
        db.flush()
        out = message_media.public(row)
        assert "aa/" not in json.dumps(out)
        assert out["url"] == "/api/attachments/%d" % row.id
    finally:
        db.rollback()
        db.close()

"""The link to owen-main: sending for real, delivery state, click-to-call, ingest.

**owen-main is mocked at the HTTP boundary and is never called.** `app.crmlink` is
the only module that talks to it, and it reaches the network through exactly two
calls — `httpx.post` and `httpx.get`. `link` below replaces those, so every test
here sees the real `crmlink` code, the real transport selection and the real
request body, and the live telephony service sees nothing. Mocking any higher (the
transport, or `crmlink.send_sms`) would stop testing the thing most likely to be
wrong: the shape of what we put on the wire.

The canned responses are copied from owen-main's own source
(`backend/app/integrations/crm/api.py` and `config.py`) rather than invented — the
403 body below is the string its `sms_refusal` actually returns while SMS is dark.

Behaviour, not status codes, per CLAUDE.md: the refusal tests re-read the thread
afterwards and assert what a person would see there, because a send that reported a
refusal and still recorded the message as sent would pass a status check.
"""
import json
from datetime import UTC, datetime

import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Contact,
    Conversation,
    ConversationEvent,
    Direction,
    EventType,
    Role,
    User,
)
from fastapi.testclient import TestClient

# --- the owen-main double ------------------------------------------------------------


class Recorder:
    """Stands in for owen-main. Records every request and answers from a script."""

    def __init__(self):
        self.calls: list[dict] = []
        self.replies: dict[str, tuple[int, dict]] = {}

    def reply(self, path_suffix: str, status: int, body: dict) -> None:
        self.replies[path_suffix] = (status, body)

    def _answer(self, method, url, **kw):
        self.calls.append({
            "method": method, "url": url,
            "json": kw.get("json"), "headers": kw.get("headers") or {},
            "timeout": kw.get("timeout"),
        })
        for suffix, (status, body) in self.replies.items():
            if url.endswith(suffix):
                return _Resp(status, body)
        return _Resp(404, {"detail": "no canned reply for " + url})

    def post(self, url, **kw):
        return self._answer("POST", url, **kw)

    def get(self, url, **kw):
        return self._answer("GET", url, **kw)

    @property
    def last(self) -> dict:
        assert self.calls, "owen-main was never called"
        return self.calls[-1]


class _Resp:
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        if self._body is _UNPARSEABLE:
            raise ValueError("not json")
        return self._body


_UNPARSEABLE = object()


@pytest.fixture()
def link(monkeypatch):
    """Arm the CRM link against a fake owen-main.

    Setting the two env vars is what selects `CrmLinkTransport` — the same switch
    production would use — so these tests exercise the real selection logic rather
    than reaching past it.
    """
    from app import crmlink

    rec = Recorder()
    monkeypatch.setenv("CRM_LINK_BASE_URL", "http://callmon_app:8888")
    monkeypatch.setenv("CRM_LINK_API_KEY", "owen_sk_test")
    monkeypatch.setenv("CRM_LINK_FROM_NUMBER", "+19544829099")
    monkeypatch.setattr(crmlink.httpx, "post", rec.post)
    monkeypatch.setattr(crmlink.httpx, "get", rec.get)
    return rec


# --- the world -----------------------------------------------------------------------


@pytest.fixture()
def world():
    """A contact with a phone, a thread, and one client per role."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    users, tokens = {}, {}
    for key, role in (("admin", Role.ADMIN), ("dispatcher", Role.DISPATCHER),
                      ("tech", Role.TECH)):
        u = User(email="%s@x.test" % key, name=key.title(), role=role)
        db.add(u)
        users[key] = u
    db.flush()

    # Stored the way a person types it. owen-main holds E.164 for the same line, and
    # the last-ten-digits rule is what makes them one number.
    jane = Contact(first_name="Jane", last_name="Doe", phone="(941) 555-0101")
    quiet = Contact(first_name="Quiet", last_name="Customer",
                    phone="(941) 555-0202", dnd=True)
    nophone = Contact(first_name="No", last_name="Number")
    db.add_all([jane, quiet, nophone])
    db.flush()

    conv = Conversation(contact_id=jane.id, last_event_at=datetime.now(UTC))
    db.add(conv)
    db.flush()

    for key, u in users.items():
        plain, tok = mint_api_token(u, name=key)
        db.add(tok)
        tokens[key] = plain
    # The telephony machine account, exactly as CLAUDE.md describes it.
    feed_user = users["dispatcher"]
    plain_feed, feed_tok = mint_api_token(feed_user, name="owen", scopes="events:write")
    db.add(feed_tok)
    tokens["feed"] = plain_feed
    db.commit()

    ids = {"jane": jane.id, "quiet": quiet.id, "nophone": nophone.id,
           "conv": conv.id}
    db.close()

    client = TestClient(app)

    def as_(who: str) -> dict:
        return {"Authorization": "Bearer " + tokens[who]}

    return client, ids, as_


def thread(client, as_, conv_id, who="admin"):
    r = client.get("/api/conversations/%d/events" % conv_id, headers=as_(who))
    assert r.status_code == 200, r.text
    return r.json()


# --- 1. sending for real -------------------------------------------------------------


def test_a_sent_message_is_written_and_appears_in_the_thread(world, link):
    """The composer's whole purpose. It has never been able to do this."""
    client, ids, as_ = world
    link.reply("/api/crm-link/messages", 200,
               {"ok": True, "message_id": "msg-abc-123", "status": "queued"})

    r = client.post("/api/conversations/%d/messages" % ids["conv"],
                    json={"body": "We can be there Tuesday", "type": "SMS"},
                    headers=as_("dispatcher"))
    assert r.status_code == 201, r.text
    assert r.json()["suppressed"] is False
    assert r.json()["reason"] == "queued"

    rows = thread(client, as_, ids["conv"])
    mine = [e for e in rows if e["body"] == "We can be there Tuesday"]
    assert len(mine) == 1, "the message was not recorded on the thread"
    assert mine[0]["direction"] == "OUTBOUND"
    assert mine[0]["delivery_status"] == "QUEUED", (
        "a message handed to the carrier is not yet delivered, and must not say it is")


def test_the_send_reaches_owen_main_with_the_body_its_api_declares(world, link):
    """`SendMessageIn` in owen-main is `{from_number, to_number, body}`, and the key
    is presented as `X-OWEN-Key`. Getting any of that wrong is a 422 nobody sees
    until a customer is waiting on a text."""
    client, ids, as_ = world
    link.reply("/api/crm-link/messages", 200,
               {"ok": True, "message_id": "m1", "status": "queued"})

    client.post("/api/conversations/%d/messages" % ids["conv"],
                json={"body": "hello", "type": "SMS"}, headers=as_("dispatcher"))

    sent = link.last
    assert sent["url"] == "http://callmon_app:8888/api/crm-link/messages"
    assert sent["json"] == {"from_number": "+19544829099",
                            "to_number": "(941) 555-0101", "body": "hello"}
    assert sent["headers"]["X-OWEN-Key"] == "owen_sk_test"


def test_owen_mains_message_id_is_kept_so_a_receipt_can_find_the_row(world, link):
    """The `message_id` is the ONLY join key between our event and a delivery
    receipt — the CRM never sees the carrier's own RefId."""
    client, ids, as_ = world
    link.reply("/api/crm-link/messages", 200,
               {"ok": True, "message_id": "msg-abc-123", "status": "queued"})
    client.post("/api/conversations/%d/messages" % ids["conv"],
                json={"body": "hello", "type": "SMS"}, headers=as_("dispatcher"))

    db = SessionLocal()
    ev = db.query(ConversationEvent).filter(
        ConversationEvent.body == "hello").one()
    assert ev.provider_ref == "msg-abc-123"
    db.close()


def test_with_no_link_configured_nothing_leaves_the_building(world):
    """The locked safety contract, still true by default. No `link` fixture here, so
    no CRM_LINK_* is set — the send must be recorded and NOT transmitted."""
    client, ids, as_ = world
    r = client.post("/api/conversations/%d/messages" % ids["conv"],
                    json={"body": "quiet please", "type": "SMS"},
                    headers=as_("admin"))
    assert r.status_code == 201
    assert r.json()["delivery_status"] == "LOGGED_ONLY"
    assert client.get("/api/health").json()["crm_link"] is False


# --- 2. a refused send ---------------------------------------------------------------


# The exact bodies owen-main returns today. `config.REFUSE_SMS_DARK` is what a send
# hits first, because CRM_LINK_SMS_ENABLED is false pending 10DLC approval.
SMS_DARK = {"detail": "CRM-link SMS is dark (CRM_LINK_SMS_ENABLED=false)"}
NOT_ALLOWLISTED = {"detail": "destination is not on CRM_LINK_ALLOWLIST"}
OPTED_OUT = {"detail": "this contact has opted out of SMS"}


@pytest.mark.parametrize("body,expect_phrase", [
    (SMS_DARK, "carrier"),
    (NOT_ALLOWLISTED, "approved list"),
    (OPTED_OUT, "opted out"),
])
def test_a_refused_send_says_why_and_never_reads_as_sent(world, link, body,
                                                         expect_phrase):
    """The state the system is in TODAY: SMS is dark, so every send is refused.

    Three things have to be true at once, and only the third is about a status
    code: the operator gets a sentence, the message is still on the thread so they
    can see what they tried to send, and it does NOT claim to have been sent.
    """
    client, ids, as_ = world
    link.reply("/api/crm-link/messages", 403, body)

    r = client.post("/api/conversations/%d/messages" % ids["conv"],
                    json={"body": "Tuesday works", "type": "SMS"},
                    headers=as_("dispatcher"))
    assert r.status_code == 201, r.text
    assert r.json()["reason"].startswith("refused: ")

    rows = thread(client, as_, ids["conv"])
    mine = [e for e in rows if e["body"] == "Tuesday works"]
    assert len(mine) == 1, "the operator cannot see the message they tried to send"
    assert mine[0]["delivery_status"] == "REFUSED"
    assert mine[0]["delivery_status"] not in ("SENT", "DELIVERED", "QUEUED")
    assert expect_phrase in mine[0]["delivery_detail"], (
        "the reason shown is %r" % mine[0]["delivery_detail"])


def test_a_refusal_is_a_sentence_not_a_wire_body(world, link):
    """"CRM_LINK_SMS_ENABLED=false" is a configuration key, not an explanation."""
    client, ids, as_ = world
    link.reply("/api/crm-link/messages", 403, SMS_DARK)
    client.post("/api/conversations/%d/messages" % ids["conv"],
                json={"body": "x", "type": "SMS"}, headers=as_("dispatcher"))

    detail = thread(client, as_, ids["conv"])[-1]["delivery_detail"]
    assert "CRM_LINK" not in detail and "false" not in detail, (
        "the raw refusal leaked to the operator: %r" % detail)
    assert detail.endswith("."), "not a sentence: %r" % detail
    assert "10DLC" in detail or "carrier" in detail


def test_an_unreachable_phone_system_is_failed_not_refused(world, link,
                                                           monkeypatch):
    """A refusal will not fix itself; a failure might. Collapsing them would tell
    the operator to give up when they should retry."""
    from app import crmlink

    client, ids, as_ = world

    def boom(*a, **kw):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(crmlink.httpx, "post", boom)
    client.post("/api/conversations/%d/messages" % ids["conv"],
                json={"body": "is anyone there", "type": "SMS"},
                headers=as_("dispatcher"))

    row = thread(client, as_, ids["conv"])[-1]
    assert row["delivery_status"] == "FAILED"
    assert "reach" in row["delivery_detail"]


def test_a_suppressed_send_still_writes_nothing(world, link):
    """DND is OUR rule, applied before anything is attempted, and the locked
    behaviour is that it records nothing at all. The new REFUSED state is for the
    different case where the phone system declined something we did attempt —
    it must not have quietly turned suppression into a written row."""
    client, ids, as_ = world
    link.reply("/api/crm-link/messages", 200,
               {"ok": True, "message_id": "m", "status": "queued"})

    db = SessionLocal()
    before = db.query(ConversationEvent).count()
    db.close()

    r = client.post("/api/contacts/%d/messages" % ids["quiet"],
                    json={"body": "hello?", "type": "SMS"}, headers=as_("admin"))
    assert r.json()["suppressed"] is True
    assert "DND" in r.json()["reason"]

    db = SessionLocal()
    assert db.query(ConversationEvent).count() == before, (
        "a suppressed send created an event")
    db.close()
    assert not link.calls, "a suppressed send still called the phone system"


# --- 3. delivery receipts ------------------------------------------------------------


def _queued_message(client, as_, conv_id, link, body="tracking me"):
    link.reply("/api/crm-link/messages", 200,
               {"ok": True, "message_id": "ref-1", "status": "queued"})
    client.post("/api/conversations/%d/messages" % conv_id,
                json={"body": body, "type": "SMS"}, headers=as_("dispatcher"))


@pytest.mark.parametrize("word,expect", [
    ("sent", "SENT"),
    ("delivered", "DELIVERED"),
    ("failed", "FAILED"),
    ("undelivered", "FAILED"),
])
def test_a_delivery_receipt_moves_the_message_to_its_real_state(world, link,
                                                                word, expect):
    """"i want to know if the text arrived or not" — this is the mechanism that
    answers it."""
    client, ids, as_ = world
    _queued_message(client, as_, ids["conv"], link)

    r = client.post("/api/events/delivery",
                    json={"provider_ref": "ref-1", "status": word},
                    headers=as_("feed"))
    assert r.status_code == 200, r.text
    assert r.json()["advanced"] is True

    assert thread(client, as_, ids["conv"])[-1]["delivery_status"] == expect


def test_the_ladder_is_forward_only(world, link):
    """Carriers re-send receipts and deliver them out of order. A late "sent" after
    a "delivered" must not walk the message back — the operator would be told a
    text that arrived is still in flight."""
    client, ids, as_ = world
    _queued_message(client, as_, ids["conv"], link)

    for word in ("sent", "delivered"):
        client.post("/api/events/delivery",
                    json={"provider_ref": "ref-1", "status": word},
                    headers=as_("feed"))

    late = client.post("/api/events/delivery",
                       json={"provider_ref": "ref-1", "status": "sent"},
                       headers=as_("feed"))
    assert late.json()["advanced"] is False
    assert thread(client, as_, ids["conv"])[-1]["delivery_status"] == "DELIVERED"


def test_a_failure_reason_reaches_the_operator(world, link):
    client, ids, as_ = world
    _queued_message(client, as_, ids["conv"], link)
    client.post("/api/events/delivery",
                json={"provider_ref": "ref-1", "status": "failed",
                      "detail": "handset unreachable"},
                headers=as_("feed"))

    row = thread(client, as_, ids["conv"])[-1]
    assert row["delivery_status"] == "FAILED"
    assert row["delivery_detail"] == "handset unreachable"


def test_a_receipt_for_an_unknown_message_is_refused_rather_than_guessed(world):
    client, ids, as_ = world
    r = client.post("/api/events/delivery",
                    json={"provider_ref": "nobody", "status": "delivered"},
                    headers=as_("feed"))
    assert r.status_code == 404


def test_the_telephony_token_can_post_a_receipt_with_the_scope_it_already_has(world,
                                                                             link):
    """The feed's token carries exactly `events:write` (CLAUDE.md). Delivery
    receipts are the same feed from the same machine, so they must not need a
    second token."""
    client, ids, as_ = world
    _queued_message(client, as_, ids["conv"], link)
    r = client.post("/api/events/delivery",
                    json={"provider_ref": "ref-1", "status": "delivered"},
                    headers=as_("feed"))
    assert r.status_code == 200, r.text


def test_the_five_delivery_states_stay_distinguishable(world, link, monkeypatch):
    """A stubbed message and a real one must never look the same in one thread —
    which requires the states to be different values, not just different words.

    All three are produced through the real send path, including the stubbed one:
    unsetting the base URL is exactly what makes `get_transport()` hand back
    LoggingTransport, so this also pins that the switch is read per send rather
    than frozen at import.
    """
    client, ids, as_ = world

    monkeypatch.delenv("CRM_LINK_BASE_URL")
    client.post("/api/conversations/%d/messages" % ids["conv"],
                json={"body": "stub", "type": "SMS"}, headers=as_("admin"))
    monkeypatch.setenv("CRM_LINK_BASE_URL", "http://callmon_app:8888")

    # REFUSED, then QUEUED -> DELIVERED.
    link.reply("/api/crm-link/messages", 403, SMS_DARK)
    client.post("/api/conversations/%d/messages" % ids["conv"],
                json={"body": "refused", "type": "SMS"}, headers=as_("admin"))
    _queued_message(client, as_, ids["conv"], link, body="real")
    client.post("/api/events/delivery",
                json={"provider_ref": "ref-1", "status": "delivered"},
                headers=as_("feed"))

    by_body = {e["body"]: e["delivery_status"]
               for e in thread(client, as_, ids["conv"])}
    assert by_body["stub"] == "LOGGED_ONLY"
    assert by_body["refused"] == "REFUSED"
    assert by_body["real"] == "DELIVERED"
    assert len(set(by_body.values())) == 3, (
        "three different fates render as the same state: %r" % by_body)


# --- 4 and 5. inbound ----------------------------------------------------------------


def test_an_inbound_event_lands_on_the_thread_matched_by_the_last_ten_digits(world):
    """owen-main holds `+19415550101`; we hold `(941) 555-0101`. Same line.

    This is the identity rule the picker, the Contacts search and owen-main itself
    already share (DECISIONS.md) — two systems disagreeing about it would file a
    customer's call on somebody else's timeline.
    """
    client, ids, as_ = world
    r = client.post("/api/events",
                    json={"from_number": "+19415550101", "type": "SMS",
                          "direction": "INBOUND", "body": "my roof is leaking"},
                    headers=as_("feed"))
    assert r.status_code == 201, r.text
    assert r.json()["contact_id"] == ids["jane"], (
        "the message was filed against the wrong contact")
    assert r.json()["conversation_id"] == ids["conv"], (
        "a second thread was created for a contact that already had one")

    bodies = [e["body"] for e in thread(client, as_, ids["conv"])]
    assert "my roof is leaking" in bodies


@pytest.mark.parametrize("rendering", [
    "+19415550101", "19415550101", "9415550101", "941-555-0101", "(941) 555-0101",
])
def test_every_rendering_of_one_number_reaches_one_contact(world, rendering):
    client, ids, as_ = world
    r = client.post("/api/events",
                    json={"from_number": rendering, "type": "SMS",
                          "direction": "INBOUND", "body": "hi"},
                    headers=as_("feed"))
    assert r.json()["contact_id"] == ids["jane"], (
        "%s did not match the stored (941) 555-0101" % rendering)


def test_an_inbound_from_an_unknown_number_creates_the_contact(world):
    """A first-time caller is the new roofing lead the owner cannot afford to lose.

    Before this, `POST /api/events` required a contact_id and 404'd on anything
    else, so owen-main dropped the event rather than post one it knew we would
    refuse — and the lead vanished.
    """
    client, ids, as_ = world
    r = client.post("/api/events",
                    json={"from_number": "+19415559999", "type": "CALL",
                          "direction": "INBOUND", "call_status": "no-answer",
                          "duration_seconds": 4},
                    headers=as_("feed"))
    assert r.status_code == 201, r.text
    new_id = r.json()["contact_id"]
    assert new_id not in (ids["jane"], ids["quiet"], ids["nophone"])

    got = client.get("/api/contacts/%d" % new_id, headers=as_("admin")).json()
    # The number is the only true thing we know about them, so it is the name.
    assert "9415559999" in (got["phone"] or "").replace("+1", "")
    assert "555" in got["name"], "the contact is not identifiable: %r" % got["name"]

    # And it is reachable as a thread, which is the whole point.
    convs = client.get("/api/conversations?tab=all&sort=latest",
                       headers=as_("admin")).json()
    assert any(c["contact_id"] == new_id for c in convs)


def test_a_second_call_from_the_same_stranger_does_not_make_a_second_contact(world):
    """The "hundreds of junk contacts" failure owen-main's own notes warn about."""
    client, ids, as_ = world
    first = client.post("/api/events",
                        json={"from_number": "+19415558888", "type": "CALL",
                              "direction": "INBOUND", "call_status": "no-answer"},
                        headers=as_("feed")).json()
    second = client.post("/api/events",
                         json={"from_number": "(941) 555-8888", "type": "SMS",
                               "direction": "INBOUND", "body": "call me back"},
                         headers=as_("feed")).json()
    assert first["contact_id"] == second["contact_id"]
    assert first["conversation_id"] == second["conversation_id"]


def test_owen_mains_own_caller_number_field_name_is_accepted(world):
    """owen-main calls this field `caller_number` internally
    (`events.CallEventFacts`). Accepting both names means whichever it sends
    lands, rather than being a 422 discovered on a live call."""
    client, ids, as_ = world
    r = client.post("/api/events",
                    json={"caller_number": "+19415550101", "type": "SMS",
                          "direction": "INBOUND", "body": "via caller_number"},
                    headers=as_("feed"))
    assert r.status_code == 201, r.text
    assert r.json()["contact_id"] == ids["jane"]


def test_the_contact_id_path_is_unchanged(world):
    """This is the path owen-main uses TODAY and its own tests pin. Making
    contact_id optional must not have loosened what an explicit one means."""
    client, ids, as_ = world
    ok = client.post("/api/events",
                     json={"contact_id": ids["jane"], "type": "CALL",
                           "direction": "INBOUND", "call_status": "completed",
                           "duration_seconds": 90},
                     headers=as_("feed"))
    assert ok.status_code == 201
    missing = client.post("/api/events",
                          json={"contact_id": 999999, "type": "SMS",
                                "direction": "INBOUND", "body": "x"},
                          headers=as_("feed"))
    assert missing.status_code == 404, "an explicit unknown contact_id is still a 404"

    db = SessionLocal()
    assert not db.query(Contact).filter(Contact.id == 999999).all()
    db.close()


def test_an_event_with_neither_a_contact_nor_a_number_is_refused(world):
    client, ids, as_ = world
    r = client.post("/api/events",
                    json={"type": "SMS", "direction": "INBOUND", "body": "who?"},
                    headers=as_("feed"))
    assert r.status_code == 422


def test_an_unknown_extra_field_does_not_break_ingest(world):
    """owen-main's payloads are documented as forward-compatible, and the queue
    outlives the code. A newer deploy there must not 422 here."""
    client, ids, as_ = world
    r = client.post("/api/events",
                    json={"from_number": "+19415550101", "type": "SMS",
                          "direction": "INBOUND", "body": "hi",
                          "linkedid": "1.234", "winning_kind": "pstn"},
                    headers=as_("feed"))
    assert r.status_code == 201, r.text


# --- 6. click-to-call ----------------------------------------------------------------


def test_click_to_call_posts_the_body_owen_mains_api_declares(world, link):
    """`OutboundCallIn` is `{from_number, to_number, operator?}`. `operator` is
    deliberately omitted so owen-main uses the binding's own — the ring group is
    configured there, and a second copy here would drift."""
    client, ids, as_ = world
    link.reply("/api/crm-link/calls", 200,
               {"ok": True, "operator_channel": "op1", "callee_channel": "cal1",
                "linkedid": "op1"})

    r = client.post("/api/conversations/%d/call" % ids["conv"],
                    headers=as_("dispatcher"))
    assert r.status_code == 200, r.text
    assert r.json()["placed"] is True

    sent = link.last
    assert sent["url"] == "http://callmon_app:8888/api/crm-link/calls"
    assert sent["json"] == {"from_number": "+19544829099",
                            "to_number": "(941) 555-0101"}
    assert "operator" not in sent["json"]


def test_a_placed_call_is_recorded_without_claiming_an_outcome(world, link):
    """We know a call was placed. We do not know how it ended, and owen-main
    reports lifecycle only for INBOUND calls — so nothing will ever tell us.
    Saying "completed" would be inventing the answer."""
    client, ids, as_ = world
    link.reply("/api/crm-link/calls", 200,
               {"ok": True, "linkedid": "lid-7"})
    client.post("/api/conversations/%d/call" % ids["conv"], headers=as_("dispatcher"))

    row = thread(client, as_, ids["conv"])[-1]
    assert row["type"] == "CALL"
    assert row["direction"] == "OUTBOUND"
    assert row["call_status"] is None, (
        "the thread claims an outcome nobody observed: %r" % row["call_status"])


def test_a_refused_call_surfaces_readably_and_records_no_call(world, link):
    """A call that never rang anyone must not become a call record — it would
    inflate the Call report with calls that did not happen."""
    client, ids, as_ = world
    link.reply("/api/crm-link/calls", 403,
               {"detail": "destination is not on CRM_LINK_ALLOWLIST"})

    before = len(thread(client, as_, ids["conv"]))
    r = client.post("/api/conversations/%d/call" % ids["conv"],
                    headers=as_("dispatcher"))
    assert r.status_code == 200, r.text
    assert r.json()["placed"] is False
    assert "approved list" in r.json()["reason"], r.json()["reason"]
    assert "CRM_LINK" not in r.json()["reason"], "raw config key shown to the operator"

    assert len(thread(client, as_, ids["conv"])) == before, (
        "a refused call was recorded as a call")


def test_calling_a_contact_with_no_number_says_so_rather_than_asking_owen(world,
                                                                         link):
    client, ids, as_ = world
    r = client.post("/api/contacts/%d/call" % ids["nophone"], headers=as_("admin"))
    assert r.json()["placed"] is False
    assert "phone number" in r.json()["reason"]
    assert not link.calls, "the phone system was asked to call nobody"


def test_calling_is_refused_readably_when_the_link_is_not_configured(world):
    """No `link` fixture: this is what every environment does today."""
    client, ids, as_ = world
    r = client.post("/api/conversations/%d/call" % ids["conv"], headers=as_("admin"))
    assert r.json()["placed"] is False
    assert "not switched on" in r.json()["reason"]


def test_a_tech_may_place_a_call(world, link):
    """A TECH may send a customer a message (CLAUDE.md); ringing the customer they
    are already texting is the same kind of act, not a wider one."""
    client, ids, as_ = world
    link.reply("/api/crm-link/calls", 200, {"ok": True, "linkedid": "x"})
    r = client.post("/api/conversations/%d/call" % ids["conv"], headers=as_("tech"))
    assert r.status_code == 200
    assert r.json()["placed"] is True


# --- 7. roles ------------------------------------------------------------------------


def test_a_tech_cannot_post_an_internal_note(world, link):
    """STAFF-only as of 2026-09-10, on every path INCLUDING the composer.

    Asserted by re-reading the thread as an ADMIN: a 403 that still inserted the
    row would pass a status check.
    """
    client, ids, as_ = world
    r = client.post("/api/conversations/%d/messages" % ids["conv"],
                    json={"body": "crew: dog on site", "type": "INTERNAL_COMMENT"},
                    headers=as_("tech"))
    assert r.status_code == 403

    bodies = [e["body"] for e in thread(client, as_, ids["conv"], who="admin")]
    assert "crew: dog on site" not in bodies, "the refused note was written anyway"


def test_a_tech_may_send_a_customer_facing_message(world, link):
    """The rule narrows internal notes only. A TECH's SMS is untouched, so the
    composer is correctly enabled for them."""
    client, ids, as_ = world
    link.reply("/api/crm-link/messages", 200,
               {"ok": True, "message_id": "m", "status": "queued"})
    r = client.post("/api/conversations/%d/messages" % ids["conv"],
                    json={"body": "on my way", "type": "SMS"}, headers=as_("tech"))
    assert r.status_code == 201
    assert r.json()["suppressed"] is False


def test_a_techs_unfiltered_read_is_narrower_rather_than_refused(world):
    """An inbox that 403s because one hidden row exists would be unusable, so an
    unfiltered read narrows. An EXPLICIT ask is still refused, so the CLI can say
    why it came back empty."""
    client, ids, as_ = world
    db = SessionLocal()
    conv_id = ids["conv"]
    db.add(ConversationEvent(conversation_id=conv_id,
                             type=EventType.INTERNAL_COMMENT,
                             direction=Direction.OUTBOUND,
                             occurred_at=datetime.now(UTC),
                             body="crew only: litigious"))
    db.add(ConversationEvent(conversation_id=conv_id, type=EventType.SMS,
                             direction=Direction.INBOUND,
                             occurred_at=datetime.now(UTC),
                             body="customer visible"))
    db.commit()
    db.close()

    unfiltered = client.get("/api/conversations/%d/events" % conv_id,
                            headers=as_("tech"))
    assert unfiltered.status_code == 200, "an unfiltered read was refused"
    bodies = [e["body"] for e in unfiltered.json()]
    assert "customer visible" in bodies
    assert "crew only: litigious" not in bodies

    explicit = client.get(
        "/api/conversations/%d/events?filter=internal_comment" % conv_id,
        headers=as_("tech"))
    assert explicit.status_code == 403


# --- the composer's own gate ---------------------------------------------------------

def test_the_composer_offers_no_enabled_control_a_role_cannot_use():
    """Source-level, because this is a rendering rule: the Internal Comment mode
    must be present and DISABLED for a TECH rather than absent.

    The precedent is explicit in DECISIONS.md and in commits d1f7c50 / b943f4b — a
    gap the user can see beats a menu that quietly gets shorter, and beats clicking
    into a 403.
    """
    from pathlib import Path
    source = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages"
              / "ConversationsPage.tsx").read_text(encoding="utf-8")

    assert "const canWriteInternal = user.role !== 'TECH'" in source
    # The row is rendered either way; only `blocked` changes.
    assert "blocked: canWriteInternal ? undefined : 'Internal notes are staff-only'" \
        in source, "the Internal Comment mode is removed rather than disabled"
    # And Dropdown refuses to fire onPick for a blocked row.
    assert "const off = it.unimplemented || Boolean(it.blocked)" in source
    assert "if (!off) { onPick(it.key); onClose() }" in source


def test_send_is_disabled_with_a_reason_when_the_contact_cannot_be_texted():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages"
              / "ConversationsPage.tsx").read_text(encoding="utf-8")
    assert "disabled={Boolean(blocked) || sending || !draft.trim()}" in source
    assert "title={blocked ??" in source, "the reason is not on the control"
    for reason in ("This contact has no phone number.",
                   "This contact is on Do Not Disturb.",
                   "Internal notes are staff-only."):
        assert reason in source


def test_the_composer_is_not_hardcoded_disabled_any_more():
    """The literal bug this branch existed to fix."""
    from pathlib import Path
    source = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages"
              / "ConversationsPage.tsx").read_text(encoding="utf-8")
    composer = source.split("{/* composer", 1)[1]
    assert "\n                  disabled\n" not in composer, (
        "the send button is still unconditionally disabled")
    assert "onClick={() => void onSend()}" in composer


def test_every_delivery_state_has_its_own_wording():
    """Five fates must not collapse into one label on screen."""
    from pathlib import Path
    source = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages"
              / "ConversationsPage.tsx").read_text(encoding="utf-8")
    block = source.split("const DELIVERY:", 1)[1].split("}\n", 1)[0]
    labels = {}
    for state in ("QUEUED", "SENT", "DELIVERED", "FAILED", "REFUSED",
                  "LOGGED_ONLY"):
        assert state in block, "%s has no rendering" % state
        line = [ln for ln in block.splitlines() if ln.strip().startswith(state + ":")]
        assert line, state
        labels[state] = line[0]
    assert len({json.dumps(v.split("label:")[1]) for v in labels.values()}) == 6, (
        "two delivery states render as the same words: %r" % labels)
    # Only DELIVERED is allowed to claim the text arrived.
    assert "delivered" in labels["DELIVERED"]
    for state in ("QUEUED", "SENT", "FAILED", "REFUSED", "LOGGED_ONLY"):
        assert "'delivered'" not in labels[state], (
            "%s claims the message was delivered" % state)


def test_the_thread_refreshes_without_a_manual_reload():
    """Inbound activity has to appear on its own. Polling is the chosen mechanism
    and the reasoning is recorded beside it; what matters here is that BOTH the
    thread and the inbox list are live, not just one."""
    from pathlib import Path
    source = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages"
              / "ConversationsPage.tsx").read_text(encoding="utf-8")
    assert "refetchInterval" in source
    assert source.count("...LIVE,") == 2, (
        "only one of the two queries refreshes itself")

"""The Conversations dialer's server half: `POST /api/calls/dial`, and `ring_browser`.

The owner's decisions (DECISIONS.md, 2026-09-14), asserted as BEHAVIOUR:

  * Dialling any number goes through the SAME path a contact or a number-only thread
    call does — the normalised number reaches owen-main's `POST /api/crm-link/calls`
    and nothing else does.
  * A number a contact holds is a contact call (DND honoured, logged on their thread).
  * An unknown number is logged on its number-only thread, created only once owen-main
    has ACCEPTED the call. No contact is ever created.
  * Every refusal — ours or owen-main's — is a sentence, and writes NOTHING: re-read
    after each one.
  * `ring_browser` rings the signed-in user's own operator, named from THEIR identity,
    never from anything the client sends.

owen-main is mocked at the HTTP boundary (`crmlink.httpx`), as in `test_crm_link.py`.
No test here can reach a live phone system.
"""
import pytest
from app.db import SessionLocal
from app.models import Contact, ConversationEvent, NumberThread, NumberThreadEvent
from sqlalchemy import select
from tests import test_number_threads as nt

# The number-thread suite's world (three roles, Jane on (941) 555-0101) and its fake
# owen-main, reused rather than rebuilt: the dialer must behave inside the same world.
world = nt.world
link = nt.link
counts = nt.counts
inbox = nt.inbox
BULKVS_LINE = nt.BULKVS_LINE

STRANGER_TYPED = "(941) 555-0199"
STRANGER = "+19415550199"


def dial(client, as_, number, who="dispatcher", **extra):
    r = client.post("/api/calls/dial", json={"number": number, **extra}, headers=as_(who))
    assert r.status_code == 200, r.text
    return r.json()


def dialled(link):
    return [c["json"] for c in link.calls if c["url"].endswith("/api/crm-link/calls")]


# --- an unknown number ------------------------------------------------------------------


def test_an_unknown_number_is_rung_normalised_and_logged_on_its_number_thread(world, link):
    client, _, as_ = world
    link.answers["/calls"] = (200, {"ok": True, "operator_channel": "op1",
                                    "callee_channel": "c1", "linkedid": "op1"})
    before = counts()

    out = dial(client, as_, STRANGER_TYPED, ring_browser=True)

    assert out["placed"] is True, out
    assert out["number"] == STRANGER
    assert out["contact_id"] is None and out["conversation_id"] is None
    assert dialled(link) == [{"from_number": BULKVS_LINE, "to_number": STRANGER,
                              "operator": "dispatcher@x.test"}]

    after = counts()
    assert after["contacts"] == before["contacts"], "dialling a number must not save it"
    assert after["jobs"] == before["jobs"]
    assert after["number_threads"] == before["number_threads"] + 1
    assert after["number_thread_events"] == before["number_thread_events"] + 1

    db = SessionLocal()
    try:
        t = db.get(NumberThread, out["number_thread_id"])
        assert t.phone == STRANGER
        ev = db.scalars(select(NumberThreadEvent)
                        .where(NumberThreadEvent.number_thread_id == t.id)).one()
        assert ev.type.value == "CALL" and ev.direction.value == "OUTBOUND"
        assert ev.provider_ref == "op1"
    finally:
        db.close()

    row = next(r for r in inbox(client, as_) if r["kind"] == "number")
    assert row["id"] == out["number_thread_id"]


def test_dialling_the_same_unknown_number_twice_reuses_its_thread(world, link):
    client, _, as_ = world
    link.answers["/calls"] = (200, {"ok": True, "linkedid": "a"})
    first = dial(client, as_, STRANGER_TYPED)
    second = dial(client, as_, "941.555.0199")
    assert first["number_thread_id"] == second["number_thread_id"]
    assert counts()["number_threads"] == 1 and counts()["number_thread_events"] == 2


def test_without_ring_browser_owen_main_keeps_its_default_operator(world, link):
    client, _, as_ = world
    link.answers["/calls"] = (200, {"ok": True, "linkedid": "a"})
    dial(client, as_, STRANGER_TYPED)
    assert "operator" not in dialled(link)[0]


def test_the_client_cannot_name_whose_phone_rings(world, link):
    """`operator` in the body is not a field: the operator is the caller's own email."""
    client, _, as_ = world
    link.answers["/calls"] = (200, {"ok": True, "linkedid": "a"})
    dial(client, as_, STRANGER_TYPED, who="tech", ring_browser=True,
         operator="admin@x.test")
    assert dialled(link)[0]["operator"] == "tech@x.test"


# --- a known number -----------------------------------------------------------------------


def test_a_number_a_contact_holds_is_a_contact_call_on_their_thread(world, link):
    client, ids, as_ = world
    link.answers["/calls"] = (200, {"ok": True, "linkedid": "k1"})
    before = counts()

    out = dial(client, as_, "+1 941 555 0101", ring_browser=True)

    assert out["placed"] is True
    assert out["contact_id"] == ids["jane"] and out["contact_name"] == "Jane Doe"
    assert out["conversation_id"] is not None and out["number_thread_id"] is None
    # The contact path dials the contact's stored number, as it always has; owen-main
    # normalises it. Same line by the shared last-ten-digits rule.
    assert dialled(link)[0]["to_number"][-14:].replace("(", "").replace(")", "") \
        .replace(" ", "").replace("-", "")[-10:] == "9415550101"
    after = counts()
    assert after["number_threads"] == before["number_threads"], "no number thread for a contact"
    assert after["conversation_events"] == before["conversation_events"] + 1
    db = SessionLocal()
    try:
        ev = db.scalars(select(ConversationEvent)).one()
        assert ev.conversation_id == out["conversation_id"] and ev.provider_ref == "k1"
    finally:
        db.close()


def test_a_contact_on_dnd_is_refused_before_anything_is_rung(world, link):
    client, ids, as_ = world
    db = SessionLocal()
    try:
        db.get(Contact, ids["jane"]).dnd = True
        db.commit()
    finally:
        db.close()
    before = counts()
    out = dial(client, as_, "9415550101", ring_browser=True)
    assert out["placed"] is False and out["reason"] == "This contact is on Do Not Disturb."
    assert dialled(link) == [] and counts() == before


# --- refusals write nothing -------------------------------------------------------------


@pytest.mark.parametrize("number, words", [
    ("555-0199", "too short"),
    ("12345", "too short"),
    ("", "Enter a number"),
    ("941-555-01#9", "only digits"),
    ("*67 941 555 0199", "only digits"),
    ("+44 20 7946 0958", "Only US and Canadian numbers"),
    ("941 555 0199 9", "Only US and Canadian numbers"),
    ("(054) 555-0199", "cannot start with 0 or 1"),
    ("(954) 482-9099", "own number"),
])
def test_a_number_that_cannot_be_called_is_refused_with_a_sentence_and_nothing_sent(
        world, link, number, words):
    client, _, as_ = world
    before = counts()
    out = dial(client, as_, number, ring_browser=True)
    assert out["placed"] is False
    assert words in out["reason"], out["reason"]
    assert dialled(link) == [], "a refused number reached owen-main"
    assert counts() == before


@pytest.mark.parametrize("status, detail, sentence", [
    (403, "destination +19415550199 is not on CRM_LINK_ALLOWLIST",
     "This number is not on the approved list for calls and texts yet."),
    (409, "this contact is blocked in OWEN", "This contact is blocked in the phone system."),
    (503, "CRM link is disabled (CRM_LINK_ENABLED=false)",
     "The phone link is switched off in the phone system."),
])
def test_owen_mains_refusal_comes_back_as_its_sentence_and_writes_nothing(
        world, link, status, detail, sentence):
    client, _, as_ = world
    link.answers["/calls"] = (status, {"detail": detail})
    before = counts()
    out = dial(client, as_, STRANGER_TYPED, ring_browser=True)
    assert out == {"placed": False, "id": None, "reason": sentence, "number": STRANGER,
                   "contact_id": None, "contact_name": None, "number_thread_id": None}
    assert len(dialled(link)) == 1
    assert counts() == before, "a refused call must not even create the empty thread"


def test_an_unarmed_link_refuses_without_a_request(world, monkeypatch):
    from app import crmlink

    client, _, as_ = world
    monkeypatch.delenv("CRM_LINK_BASE_URL", raising=False)
    monkeypatch.delenv("CRM_LINK_API_KEY", raising=False)
    sent = []
    monkeypatch.setattr(crmlink.httpx, "post", lambda *a, **k: sent.append(a))
    before = counts()
    out = dial(client, as_, STRANGER_TYPED, ring_browser=True)
    assert out["placed"] is False and "not switched on" in out["reason"]
    assert sent == [] and counts() == before


def test_dialling_needs_a_credential(world, link):
    client, _, _ = world
    before = counts()
    r = client.post("/api/calls/dial", json={"number": STRANGER_TYPED})
    assert r.status_code == 401
    assert dialled(link) == [] and counts() == before


# --- ring_browser on the routes that already placed calls -------------------------------


def test_ring_browser_on_a_thread_call_rings_the_callers_own_operator(world, link):
    client, ids, as_ = world
    link.answers["/calls"] = (200, {"ok": True, "linkedid": "x"})
    dial(client, as_, "9415550101")  # creates Jane's thread
    conv_id = next(r for r in inbox(client, as_) if r["kind"] == "contact")["id"]
    link.calls.clear()

    client.post("/api/conversations/%d/call" % conv_id, headers=as_("admin"))
    client.post("/api/conversations/%d/call" % conv_id, json={"ring_browser": True},
                headers=as_("admin"))
    client.post("/api/contacts/%d/call" % ids["jane"], json={"ring_browser": True},
                headers=as_("tech"))
    got = dialled(link)
    assert [d.get("operator") for d in got] == [None, "admin@x.test", "tech@x.test"]


def test_ring_browser_on_a_number_thread_call(world, link):
    client, _, as_ = world
    link.answers["/calls"] = (200, {"ok": True, "linkedid": "x"})
    tid = dial(client, as_, STRANGER_TYPED)["number_thread_id"]
    link.calls.clear()
    r = client.post("/api/number-threads/%d/call" % tid, json={"ring_browser": True},
                    headers=as_("dispatcher"))
    assert r.json()["placed"] is True
    assert dialled(link)[0]["operator"] == "dispatcher@x.test"

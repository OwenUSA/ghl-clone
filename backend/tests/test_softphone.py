"""The CRM end of the browser softphone.

Behaviour, not status codes — the house standard. In particular:

* a request must never reach the telephony project from a test. `post_json` is
  replaced in every test that could make one, and one test asserts that an
  unconfigured deployment does not even try;
* the CRM-link machine key must never appear in anything the browser can read;
* "who is calling" must use the same last-ten-digits identity rule as the rest of
  the app, and must refuse to guess from a fragment;
* a user must never be able to ask for somebody else's operator credentials.

What is NOT asserted here, and cannot be: that a real call rings this browser.
That needs a live Asterisk, a provisioned pjsip endpoint and somebody to dial the
number — see `.qa/state/softphone-done`.
"""
import httpx
import pytest
from app import softphone
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Contact, Role, User
from fastapi.testclient import TestClient

CREDS = {
    "ok": True,
    "operator": "owner-x.test",
    "endpoint": "operator-owner-x.test",
    "sip": {"endpoint": "operator-owner-x.test", "username": "operator-owner-x.test",
            "authorization_username": "operator-owner-x.test", "password": "sip-secret",
            "domain": "owen.example", "wss_url": "wss://api.owen.example/ws",
            "expires_at": 1_900_000_000},
    "ice_servers": [{"urls": ["turns:turn.owen.example:443"], "username": "1:owner-x.test",
                     "credential": "turn-cred"}],
}

KEY = "owen_sk_the_machine_key"


@pytest.fixture()
def client(monkeypatch):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    users = [User(email="owner@x.test", name="Owen", role=Role.ADMIN),
             User(email="tech@x.test", name="Tech", role=Role.TECH)]
    db.add_all(users)
    db.flush()
    # Two shapes of the SAME number, which is exactly what production holds: rows written
    # before feature/phone-normalisation kept whatever was typed, newer ones are E.164.
    db.add_all([
        Contact(first_name="Jane", last_name="Doe", phone="(941) 555-0100",
                email="jane@x.test"),
        Contact(first_name="Bob", last_name="Marsh", phone="+19415550199",
                business_name="Marsh Roofing", email="bob@x.test"),
    ])
    tokens = {}
    for key, user in (("admin", users[0]), ("tech", users[1])):
        plain, token = mint_api_token(user, name="test-%s" % key)
        db.add(token)
        tokens[key] = plain
    db.commit()
    db.close()

    # Default: the deployment knows about OWEN, but every round trip is faked. A test that
    # wants "unconfigured" clears these itself.
    monkeypatch.setenv("OWEN_BASE_URL", "http://owen.internal:8000")
    monkeypatch.setenv("OWEN_SOFTPHONE_KEY", KEY)

    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + tokens["admin"]
        c.tokens = tokens
        yield c


class Upstream:
    """A stand-in for OWEN that records what it was asked, and answers what it is told."""

    def __init__(self, status=200, body=None, raises=None):
        self.status, self.body, self.raises = status, body if body is not None else CREDS, raises
        self.calls = []

    def __call__(self, url, *, key, payload, timeout):
        self.calls.append({"url": url, "key": key, "payload": payload, "timeout": timeout})
        if self.raises is not None:
            raise self.raises
        return self.status, self.body


def use(monkeypatch, upstream):
    monkeypatch.setattr(softphone, "post_json", upstream)
    return upstream


# ---------------- credentials ----------------

def test_credentials_are_minted_for_the_signed_in_user_and_nobody_else(client, monkeypatch):
    """The identity is the session's, never a parameter. An operator endpoint IS a ring
    destination, so registering as someone else is 'answer their calls', not a paper
    privilege."""
    up = use(monkeypatch, Upstream())
    r = client.post("/api/softphone/credentials", json={"email": "tech@x.test"})
    assert r.status_code == 200
    assert up.calls[0]["payload"] == {"email": "owner@x.test"}, (
        "the email sent upstream came from the request body, not the session")

    # The other user gets their own, from the same route with no parameter at all.
    client.headers["Authorization"] = "Bearer " + client.tokens["tech"]
    client.post("/api/softphone/credentials")
    assert up.calls[1]["payload"] == {"email": "tech@x.test"}


def test_the_machine_key_never_reaches_the_browser(client, monkeypatch):
    up = use(monkeypatch, Upstream())
    r = client.post("/api/softphone/credentials")
    assert up.calls[0]["key"] == KEY, "the key was not presented to OWEN"
    assert KEY not in r.text, "the CRM-link machine key was echoed to the browser"
    # ...and not through a refusal either, which is where a careless implementation
    # leaks configuration.
    use(monkeypatch, Upstream(status=403, body={"detail": "not a provisioned operator"}))
    assert KEY not in client.post("/api/softphone/credentials").text


def test_an_unconfigured_deployment_refuses_without_making_a_request(client, monkeypatch):
    """The default everywhere: a fresh checkout, the test suite, and any deployment that
    has not been told where the phone system is. It must not merely fail — it must not
    reach out at all."""
    monkeypatch.delenv("OWEN_SOFTPHONE_KEY", raising=False)
    up = use(monkeypatch, Upstream())
    r = client.post("/api/softphone/credentials")
    assert r.status_code == 503
    assert "OWEN_SOFTPHONE_KEY" in r.json()["detail"]
    assert up.calls == [], "an unconfigured deployment tried to call the phone system"

    monkeypatch.setenv("OWEN_SOFTPHONE_KEY", KEY)
    monkeypatch.setenv("OWEN_BASE_URL", "")
    assert client.post("/api/softphone/credentials").status_code == 503
    assert up.calls == []


@pytest.mark.parametrize(("upstream", "ours"), [
    (503, 503),   # the CRM link is off, or telephony is dark
    (403, 403),   # this user is not a provisioned operator
    (422, 422),   # we sent something malformed — our bug, stays visible
    (500, 502),   # OWEN is broken
    (404, 502),   # the route moved
])
def test_an_upstream_refusal_keeps_its_meaning(client, monkeypatch, upstream, ours):
    use(monkeypatch, Upstream(status=upstream, body={"detail": "upstream said so"}))
    r = client.post("/api/softphone/credentials")
    assert r.status_code == ours
    if ours != 502:
        assert r.json()["detail"] == "upstream said so", (
            "OWEN's reason is written for a machine caller and says exactly what is "
            "missing — it must not be replaced with a generic one")


def test_an_unreachable_phone_system_is_a_502_not_a_crash(client, monkeypatch):
    use(monkeypatch, Upstream(raises=httpx.ConnectError("no route to host")))
    r = client.post("/api/softphone/credentials")
    assert r.status_code == 502
    assert "did not answer" in r.json()["detail"]


def test_a_200_that_is_not_a_credential_blob_is_refused(client, monkeypatch):
    """A softphone that silently fails to register is the failure this feature exists to
    make visible, so a malformed 200 must not be passed to SIP.js to fail further away."""
    for junk in ({"ok": True},
                 "<html>gateway</html>",
                 {"sip": None, "ice_servers": []},
                 {"sip": {"username": "operator-x"}},       # no wss_url: nothing to dial
                 {"sip": {"wss_url": "wss://x/ws"}}):       # no identity to register as
        use(monkeypatch, Upstream(body=junk))
        r = client.post("/api/softphone/credentials")
        assert r.status_code == 502, f"{junk!r} was accepted as a credential blob"


def test_every_role_may_hold_a_softphone(client, monkeypatch):
    """A ring group that excluded the field crew would miss the call it exists to catch."""
    use(monkeypatch, Upstream())
    client.headers["Authorization"] = "Bearer " + client.tokens["tech"]
    assert client.post("/api/softphone/credentials").status_code == 200


def test_credentials_need_a_credential(client, monkeypatch):
    use(monkeypatch, Upstream())
    del client.headers["Authorization"]
    assert client.post("/api/softphone/credentials").status_code == 401


# ---------------- who is calling ----------------

def test_a_caller_is_resolved_whatever_shape_the_number_is_stored_in(client):
    """Production holds both formats at once. The caller-ID arrives as E.164 from the
    carrier, so a contact typed as `(941) 555-0100` has to match it."""
    r = client.get("/api/softphone/caller", params={"number": "+19415550100"})
    assert r.status_code == 200
    assert r.json()["contact"]["name"] == "Jane Doe"

    r = client.get("/api/softphone/caller", params={"number": "+19415550199"})
    assert r.json()["contact"]["name"] == "Bob Marsh"
    assert r.json()["contact"]["business_name"] == "Marsh Roofing"


def test_an_unknown_caller_is_a_200_with_no_contact(client):
    """Never a 404: a failed lookup must not be able to stop somebody answering."""
    r = client.get("/api/softphone/caller", params={"number": "+13055557777"})
    assert r.status_code == 200
    assert r.json()["contact"] is None
    assert r.json()["number"] == "+13055557777"


def test_a_fragment_identifies_nobody(client):
    """`phone_match` matches a short fragment ANYWHERE in a stored number, which is right
    for a search box and wrong for a ringing phone: putting a stranger's name on an
    incoming call is worse than showing digits."""
    for fragment in ("0100", "555", "941555", "", "anonymous", "+1"):
        r = client.get("/api/softphone/caller", params={"number": fragment})
        assert r.status_code == 200
        assert r.json()["contact"] is None, f"{fragment!r} was matched to a contact"

    # The negative control: the full number DOES match, so the rule above is a real
    # restriction rather than a lookup that never works.
    assert client.get("/api/softphone/caller",
                      params={"number": "9415550100"}).json()["contact"]["name"] == "Jane Doe"


def test_who_is_calling_needs_a_credential(client):
    del client.headers["Authorization"]
    assert client.get("/api/softphone/caller",
                      params={"number": "+19415550100"}).status_code == 401


# ---------------- the pure parts ----------------

def test_relay_status_maps_every_upstream_answer():
    assert softphone.relay_status(503) == 503
    assert softphone.relay_status(403) == 403
    assert softphone.relay_status(422) == 422
    for broken in (500, 502, 404, 418, 200):
        assert softphone.relay_status(broken) == 502


def test_a_junk_timeout_cannot_hang_a_request(monkeypatch):
    monkeypatch.setenv("OWEN_SOFTPHONE_TIMEOUT", "not a number")
    assert softphone.config().timeout == 5.0
    monkeypatch.setenv("OWEN_SOFTPHONE_TIMEOUT", "6000")
    assert softphone.config().timeout == 30.0
    monkeypatch.setenv("OWEN_SOFTPHONE_TIMEOUT", "0")
    assert softphone.config().timeout == 1.0

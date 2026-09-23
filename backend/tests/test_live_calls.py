"""A live AI-agent call in the CRM: the banner's list, Listen and Take over (2026-09-23).

What is pinned, by behaviour rather than by status code:

  * **Who may.** ADMIN and DISPATCHER. A TECH, and a DISPATCHER with "Only assigned data"
    on, are refused 403 — and the fake owen-main proves NOTHING was relayed.
  * **Off unless configured.** With the link unset the list is `{"calls": []}` and Listen /
    Take over are 503, and no request was attempted at all.
  * **The email is the signed-in user's own.** A body naming somebody else is ignored; the
    relay carries the principal's address and nothing else.
  * **owen-main's refusals become sentences** and keep their meaning: not a provisioned
    operator (403), the call ended (404), anything else (502).
  * **The caller is named** from Contacts when the CRM knows them.

owen-main is mocked at the `crmlink.httpx` boundary, like `test_number_threads.py`'s `link`
fixture; `tests/owen_guard.py` would fail any test here that reached a real host.
"""
import pytest
from app import crmlink
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Contact, Role, User
from fastapi.testclient import TestClient

BASE = "http://callmon_app:8888"
LINKEDID = "1758640000.42"
CUSTOMER = "+19415550101"
STRANGER = "+19415550199"
DID = "+19547758492"

LIVE = {"calls": [
    {"linkedid": LINKEDID, "caller_number": CUSTOMER, "dialed_number": DID,
     "agent": "Intake", "started_at": "2026-09-23T14:05:00+00:00", "duration_s": 83,
     "turns": 7},
    {"linkedid": "1758640099.7", "caller_number": STRANGER, "dialed_number": DID,
     "agent": "Intake", "started_at": None, "duration_s": 4, "turns": 1},
]}


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = str(body)
        self.headers = {"content-type": "application/json"}
        self.content = b""

    def json(self):
        return self._body


class Owen:
    """A fake owen-main: records every request, answers what it is told to."""

    def __init__(self):
        self.calls = []
        self.answers = {}

    def _answer(self, url, default):
        return FakeResponse(*self.answers.get(url.rsplit("/api/crm-link", 1)[-1], default))

    def post(self, url, json=None, headers=None, timeout=None, **kw):
        self.calls.append({"method": "POST", "url": url, "json": json, "headers": headers})
        return self._answer(url, (200, {"ok": True, "operator": "slug",
                                        "operator_channel": "op-1"}))

    def get(self, url, headers=None, timeout=None, **kw):
        self.calls.append({"method": "GET", "url": url, "json": None, "headers": headers})
        return self._answer(url, (200, LIVE))


@pytest.fixture()
def world():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    tokens = {}
    for key, role, only_assigned in (("admin", Role.ADMIN, False),
                                     ("dispatcher", Role.DISPATCHER, False),
                                     ("restricted", Role.DISPATCHER, True),
                                     ("tech", Role.TECH, False)):
        u = User(email="%s@x.test" % key, name=key.title(), role=role,
                 only_assigned_data=only_assigned)
        db.add(u)
        db.flush()
        plain, tok = mint_api_token(u, name=key)
        db.add(tok)
        tokens[key] = plain
    db.add(Contact(first_name="Jane", last_name="Doe", phone="(941) 555-0101"))
    db.commit()
    db.close()
    client = TestClient(app)

    def as_(who):
        return {"Authorization": "Bearer " + tokens[who]}

    return client, as_


@pytest.fixture()
def owen(monkeypatch):
    rec = Owen()
    monkeypatch.setenv("CRM_LINK_BASE_URL", BASE)
    monkeypatch.setenv("CRM_LINK_API_KEY", "owen_sk_test")
    monkeypatch.setattr(crmlink.httpx, "post", rec.post)
    monkeypatch.setattr(crmlink.httpx, "get", rec.get)
    return rec


@pytest.fixture()
def unconfigured(monkeypatch):
    """The link unset — and httpx rigged to fail the test if anything tries it anyway."""
    monkeypatch.delenv("CRM_LINK_BASE_URL", raising=False)
    monkeypatch.delenv("CRM_LINK_API_KEY", raising=False)
    attempts = []

    def refuse(*a, **kw):
        attempts.append(a)
        raise AssertionError("a request was attempted with the link unconfigured")

    monkeypatch.setattr(crmlink.httpx, "post", refuse)
    monkeypatch.setattr(crmlink.httpx, "get", refuse)
    return attempts


# --- who may ------------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["admin", "dispatcher"])
def test_admin_and_dispatcher_see_whats_live_with_the_caller_named(world, owen, who):
    client, as_ = world
    r = client.get("/api/live-calls", headers=as_(who))
    assert r.status_code == 200, r.text
    calls = r.json()["calls"]
    assert [c["linkedid"] for c in calls] == [LINKEDID, "1758640099.7"]
    first, second = calls
    assert first["contact"]["name"] == "Jane Doe"
    assert (first["agent"], first["duration_s"], first["dialed_number"]) == ("Intake", 83, DID)
    assert second["contact"] is None and second["caller_number"] == STRANGER
    assert [(c["method"], c["url"]) for c in owen.calls] == [
        ("GET", BASE + "/api/crm-link/live-calls")]
    assert owen.calls[0]["headers"]["X-OWEN-Key"] == "owen_sk_test"


@pytest.mark.parametrize("who", ["tech", "restricted"])
def test_a_tech_or_restricted_user_is_refused_and_nothing_is_relayed(world, owen, who):
    client, as_ = world
    assert client.get("/api/live-calls", headers=as_(who)).status_code == 403
    for action in ("listen", "takeover"):
        r = client.post("/api/live-calls/%s/%s" % (LINKEDID, action), headers=as_(who))
        assert r.status_code == 403
        assert "admins and dispatchers" in r.json()["detail"]
    assert owen.calls == []


def test_no_credential_is_refused_before_anything(world, owen):
    client, _ = world
    assert client.get("/api/live-calls").status_code == 401
    assert client.post("/api/live-calls/%s/takeover" % LINKEDID).status_code == 401
    assert owen.calls == []


# --- off unless configured ----------------------------------------------------------------


def test_unconfigured_link_is_an_empty_list_and_no_request(world, unconfigured):
    client, as_ = world
    r = client.get("/api/live-calls", headers=as_("admin"))
    assert r.status_code == 200
    assert r.json() == {"calls": []}
    for action in ("listen", "takeover"):
        r = client.post("/api/live-calls/%s/%s" % (LINKEDID, action), headers=as_("admin"))
        assert r.status_code == 503
    assert unconfigured == []


def test_an_owen_main_that_does_not_answer_is_still_an_empty_list(world, owen, monkeypatch):
    client, as_ = world

    def down(*a, **kw):
        raise crmlink.httpx.ConnectError("refused")

    monkeypatch.setattr(crmlink.httpx, "get", down)
    r = client.get("/api/live-calls", headers=as_("dispatcher"))
    assert r.status_code == 200
    assert r.json()["calls"] == []
    assert "could not reach" in r.json()["unavailable"]


# --- listen / take over ---------------------------------------------------------------------


@pytest.mark.parametrize("action", ["listen", "takeover"])
def test_the_relay_carries_the_signed_in_users_own_email(world, owen, action):
    client, as_ = world
    r = client.post("/api/live-calls/%s/%s" % (LINKEDID, action),
                    json={"operator_email": "admin@x.test"},    # not ours to choose
                    headers=as_("dispatcher"))
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert len(owen.calls) == 1
    sent = owen.calls[0]
    assert sent["url"] == "%s/api/crm-link/live-calls/%s/%s" % (BASE, LINKEDID, action)
    assert sent["json"] == {"operator_email": "dispatcher@x.test"}


def test_an_unprovisioned_operator_gets_a_sentence_not_a_code(world, owen):
    client, as_ = world
    owen.answers["/live-calls/%s/takeover" % LINKEDID] = (403, {
        "detail": "that CRM user is not a provisioned OWEN operator (add them to "
                  "CRM_LINK_SOFTPHONE_OPERATORS and to asterisk/pjsip.conf)"})
    r = client.post("/api/live-calls/%s/takeover" % LINKEDID, headers=as_("admin"))
    assert r.status_code == 403
    assert "browser phone line" in r.json()["detail"]
    assert "CRM_LINK_SOFTPHONE_OPERATORS" not in r.json()["detail"]


def test_a_call_that_ended_says_so(world, owen):
    client, as_ = world
    owen.answers["/live-calls/%s/listen" % LINKEDID] = (
        404, {"detail": "no live agent session for that linkedid"})
    r = client.post("/api/live-calls/%s/listen" % LINKEDID, headers=as_("admin"))
    assert r.status_code == 404
    assert r.json()["detail"] == "That call has already ended."


def test_a_broken_owen_main_is_502(world, owen):
    client, as_ = world
    owen.answers["/live-calls/%s/listen" % LINKEDID] = (500, {"detail": "boom"})
    r = client.post("/api/live-calls/%s/listen" % LINKEDID, headers=as_("admin"))
    assert r.status_code == 502


@pytest.mark.parametrize("bad", ["..", ".hidden", "a" * 65, "x;y"])
def test_a_linkedid_that_is_not_one_is_never_put_in_a_url(world, owen, bad):
    client, as_ = world
    r = client.post("/api/live-calls/%s/takeover" % bad, headers=as_("admin"))
    assert r.status_code == 404
    assert owen.calls == []


def test_the_helper_refuses_an_action_that_is_not_listen_or_takeover(owen):
    with pytest.raises(ValueError):
        crmlink.live_call_action(LINKEDID, "stop", "a@x.test")
    assert owen.calls == []

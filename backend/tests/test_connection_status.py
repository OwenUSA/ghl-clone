"""`GET /api/connection-status` — the top-bar dot's link and Quo checks.

Behaviour, not status codes:

* owen-main unreachable is the RED answer, delivered with a 200 — the endpoint must never
  fail the way the thing it reports on has failed;
* each owen-main fact maps to the right state, and a stale tick is amber, not green;
* the machine key, owen-main's URL and any phone number never reach the browser;
* a request never reaches a live telephony service from a test (`get_json` is replaced),
  and an unconfigured deployment does not even try;
* one owen-main request serves every poll inside the cache window;
* the route requires a signed-in user.
"""
import json
import re

import httpx
import pytest
from app import connection_status as cs
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Role, User
from fastapi.testclient import TestClient

KEY = "owen_sk_status_machine_key"
BASE = "http://callmon_app.internal:8000"
NOW = "2026-09-14T15:00:00+00:00"


def healthy(**quo_overrides) -> dict:
    """What owen-main's GET /api/link-status answers when everything is working."""
    quo = {
        "mirror_enabled": True, "api_key_present": True, "poll_seconds": 300,
        "last_tick_at": "2026-09-14T14:57:00+00:00", "last_tick_ran": True,
        "last_tick_reason": None, "backfill_days": 30,
        "backfill_completed_at": "2026-09-12T02:00:00+00:00",
        "webhook_enabled": True, "webhook_secret_configured": True,
        "last_webhook_at": "2026-09-14T14:58:30+00:00",
    }
    quo.update(quo_overrides)
    return {"checked_at": NOW, "database_readable": True,
            "crm_link": {"enabled": True, "telephony_enabled": True}, "quo": quo}


class Upstream:
    """owen-main, faked. Records what it was asked."""

    def __init__(self, status=200, body=None, raises=None):
        self.status, self.raises = status, raises
        self.body = healthy() if body is None else body
        self.calls = []

    def __call__(self, url, *, key, timeout):
        self.calls.append({"url": url, "key": key, "timeout": timeout})
        if self.raises is not None:
            raise self.raises
        return self.status, self.body


@pytest.fixture()
def client(monkeypatch):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    user = User(email="tech@x.test", name="Tech", role=Role.TECH)
    db.add(user)
    db.flush()
    plain, token = mint_api_token(user, name="status-test")
    db.add(token)
    db.commit()
    db.close()

    for name in ("CRM_LINK_BASE_URL", "CRM_LINK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OWEN_BASE_URL", BASE)
    monkeypatch.setenv("OWEN_SOFTPHONE_KEY", KEY)
    cs.clear_cache()
    with TestClient(app) as c:
        c.token = plain
        c.headers["Authorization"] = "Bearer " + plain
        yield c
    cs.clear_cache()


def use(monkeypatch, upstream):
    monkeypatch.setattr(cs, "get_json", upstream)
    cs.clear_cache()
    return upstream


def status(client):
    r = client.get("/api/connection-status")
    assert r.status_code == 200, r.text
    return r.json()


# ---------------- the link ----------------

def test_everything_working_is_ok_on_both_checks(client, monkeypatch):
    up = use(monkeypatch, Upstream())
    body = status(client)
    assert body["link"] == {"state": "ok", "sentence": cs.LINK_OK}
    assert body["quo"]["state"] == "ok" and body["quo"]["sentence"] == cs.QUO_OK
    assert up.calls[0]["url"] == BASE + "/api/link-status"
    assert up.calls[0]["key"] == KEY
    assert up.calls[0]["timeout"] <= 5, "a dead phone system would hang every poll"


def test_owen_unreachable_is_red_with_a_sentence_and_still_200(client, monkeypatch):
    use(monkeypatch, Upstream(raises=httpx.ConnectTimeout("timed out")))
    body = status(client)  # asserts the 200
    assert body["link"]["state"] == "down"
    assert body["link"]["sentence"] == cs.LINK_UNREACHABLE
    assert body["quo"]["state"] == "unknown", "Quo cannot be known when owen-main is down"

    use(monkeypatch, Upstream(raises=httpx.ConnectError("refused")))
    assert status(client)["link"]["state"] == "down"


@pytest.mark.parametrize("code,body,state,sentence", [
    (401, {"detail": "bad key"}, "down", cs.LINK_REFUSED),
    (403, {"detail": "missing scope"}, "down", cs.LINK_REFUSED),
    (404, {"detail": "Not Found"}, "degraded", cs.LINK_TOO_OLD),
    (502, "<html>bad gateway</html>", "down", cs.LINK_ERROR % 502),
])
def test_owen_error_answers_map_to_a_state(client, monkeypatch, code, body, state, sentence):
    use(monkeypatch, Upstream(status=code, body=body))
    got = status(client)
    assert got["link"] == {"state": state, "sentence": sentence}
    assert got["quo"]["state"] == "unknown"
    assert "bad gateway" not in json.dumps(got), "an upstream error page reached the browser"


def test_the_crm_link_switched_off_is_red(client, monkeypatch):
    body = healthy()
    body["crm_link"]["enabled"] = False
    use(monkeypatch, Upstream(body=body))
    assert status(client)["link"] == {"state": "down", "sentence": cs.LINK_DISABLED}

    body = healthy()
    body["crm_link"]["telephony_enabled"] = False
    use(monkeypatch, Upstream(body=body))
    assert status(client)["link"] == {"state": "down", "sentence": cs.LINK_NO_TELEPHONY}


def test_an_unconfigured_deployment_is_off_and_asks_nobody(client, monkeypatch):
    monkeypatch.delenv("OWEN_SOFTPHONE_KEY", raising=False)
    up = use(monkeypatch, Upstream())
    body = status(client)
    assert body["link"] == {"state": "off", "sentence": cs.LINK_OFF}
    assert body["quo"]["state"] == "unknown"
    assert up.calls == [], "an unconfigured deployment tried to reach the phone system"


def test_the_send_link_key_is_preferred_when_configured(client, monkeypatch):
    monkeypatch.setenv("CRM_LINK_BASE_URL", "http://other-owen:8000")
    monkeypatch.setenv("CRM_LINK_API_KEY", "owen_sk_send_link")
    up = use(monkeypatch, Upstream())
    status(client)
    assert up.calls[0]["url"] == "http://other-owen:8000/api/link-status"
    assert up.calls[0]["key"] == "owen_sk_send_link"


# ---------------- Quo ----------------

@pytest.mark.parametrize("overrides,state,sentence", [
    ({"mirror_enabled": False}, "down", cs.QUO_OFF),
    ({"api_key_present": False}, "down", cs.QUO_NO_KEY),
    ({"last_tick_at": None, "last_tick_ran": None}, "degraded", cs.QUO_NEVER_TICKED),
    # 11 minutes old at a 5-minute interval: more than two intervals, so stale.
    ({"last_tick_at": "2026-09-14T14:49:00+00:00"}, "degraded", cs.QUO_STALE),
    ({"last_tick_ran": False, "last_tick_reason": "OpenPhone unreachable"}, "degraded",
     cs.QUO_TICK_FAILED),
    ({"backfill_completed_at": None}, "degraded", cs.QUO_BACKFILL),
    ({"webhook_enabled": False}, "degraded", cs.QUO_WEBHOOK_OFF),
    ({"webhook_secret_configured": False}, "degraded", cs.QUO_WEBHOOK_NO_SECRET),
    ({"last_webhook_at": None}, "ok", cs.QUO_OK),
])
def test_each_quo_fact_maps_to_the_right_state(client, monkeypatch, overrides, state, sentence):
    use(monkeypatch, Upstream(body=healthy(**overrides)))
    quo = status(client)["quo"]
    assert (quo["state"], quo["sentence"]) == (state, sentence)


def test_a_stale_tick_is_amber_and_a_fresh_one_is_not(client, monkeypatch):
    # Nine minutes at a five-minute poll is inside two intervals: still fine.
    use(monkeypatch, Upstream(body=healthy(last_tick_at="2026-09-14T14:51:00+00:00")))
    quo = status(client)["quo"]
    assert quo["state"] == "ok" and quo["facts"]["tick_stale"] is False

    use(monkeypatch, Upstream(body=healthy(last_tick_at="2026-09-14T14:49:59+00:00")))
    quo = status(client)["quo"]
    assert quo["state"] == "degraded" and quo["facts"]["tick_stale"] is True

    # The interval is owen-main's, not a constant here: at a 60s poll, 3 minutes is stale.
    use(monkeypatch, Upstream(body=healthy(poll_seconds=60,
                                            last_tick_at="2026-09-14T14:57:00+00:00")))
    assert status(client)["quo"]["facts"]["tick_stale"] is True


def test_an_unreadable_owen_database_is_amber(client, monkeypatch):
    body = healthy()
    body["database_readable"] = False
    use(monkeypatch, Upstream(body=body))
    got = status(client)
    assert got["link"]["state"] == "ok"
    assert (got["quo"]["state"], got["quo"]["sentence"]) == ("degraded", cs.QUO_UNREADABLE)


def test_the_hover_card_facts_are_forwarded(client, monkeypatch):
    use(monkeypatch, Upstream())
    facts = status(client)["quo"]["facts"]
    assert facts["last_tick_at"] == "2026-09-14T14:57:00+00:00"
    assert facts["backfill_completed_at"] == "2026-09-12T02:00:00+00:00"
    assert facts["last_webhook_at"] == "2026-09-14T14:58:30+00:00"
    assert facts["webhook_enabled"] is True and facts["poll_seconds"] == 300


# ---------------- what reaches the browser ----------------

def test_no_secret_url_or_phone_number_in_the_payload(client, monkeypatch):
    monkeypatch.setenv("CRM_LINK_BASE_URL", BASE)
    monkeypatch.setenv("CRM_LINK_API_KEY", KEY)
    # An owen-main that misbehaves and puts things in its answer that must not travel on.
    body = healthy(last_tick_reason="failed for +19415550123",
                   include=["9417247244"], token="crm_token_secret")
    body["crm_link"]["bindings"] = [{"phone_number": "+19544829099"}]
    body["crm_base_url"] = "http://ghl_clone_api:8000"
    use(monkeypatch, Upstream(body=body))
    r = client.get("/api/connection-status")
    text = r.text
    assert KEY not in text
    assert "http" not in text, "a URL reached the browser"
    assert "crm_token_secret" not in text
    # WIDENED 2026-09-16. `our_line` — the number this CRM sends FROM — is now in the body
    # on purpose: it is the company's own published number, already printed on the composer,
    # and putting it here is what stopped four screens hard-coding a line the owner had
    # retired. The property this line has always been for is unchanged and is asserted
    # exactly: NOTHING ELSE phone-shaped survives, and `our_line` is the CONFIGURED line
    # rather than anything owen-main said.
    from app import crmlink

    ours = crmlink.current().from_number
    assert r.json()["our_line"] == ours
    others = [run for run in re.findall(r"\d{7,}", text) if run not in ours]
    assert not others, ("a phone-number-shaped run other than our own line reached the "
                        "browser: %s" % others)
    assert "9415550123" not in text, "a CUSTOMER's number reached the browser"
    assert "9417247244" not in text, "owen-main's own number reached the browser"
    assert "9544829099" not in text, "owen-main's binding list reached the browser"

    # ...including when owen-main is down, which is where a careless error leaks config.
    use(monkeypatch, Upstream(raises=httpx.ConnectError(f"cannot connect to {BASE}")))
    text = client.get("/api/connection-status").text
    assert KEY not in text and "http" not in text


def test_the_route_requires_a_signed_in_user(client, monkeypatch):
    up = use(monkeypatch, Upstream())
    del client.headers["Authorization"]
    r = client.get("/api/connection-status")
    assert r.status_code == 401
    assert "link" not in r.json()
    assert up.calls == [], "an anonymous request made owen-main do work"


# ---------------- the cache ----------------

def test_one_upstream_request_serves_every_poll_in_the_window(client, monkeypatch):
    up = use(monkeypatch, Upstream())
    for _ in range(5):
        status(client)
    assert len(up.calls) == 1

    # A failure is cached too: a dead phone system costs one slow request per window.
    down = use(monkeypatch, Upstream(raises=httpx.ConnectTimeout("t")))
    for _ in range(3):
        assert status(client)["link"]["state"] == "down"
    assert len(down.calls) == 1

    # And the window does end.
    monkeypatch.setattr(cs, "CACHE_SECONDS", 0.0)
    status(client)
    assert len(down.calls) == 2

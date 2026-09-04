"""Authentication and authorization.

Behaviour, not status codes: a 403 that still performed the mutation would pass a
status-only assertion, so the role tests re-read the record afterwards.

The first test here is the important one — it enumerates the app's own routing table
rather than a hand-written list, so a route added later without authorization fails
the suite instead of quietly shipping open.
"""
from datetime import UTC, datetime, timedelta

import pytest
from app import auth
from app.auth import hash_password, mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    ApiToken,
    Contact,
    ConversationEvent,
    Opportunity,
    Pipeline,
    Role,
    Stage,
    User,
)
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

PASSWORD = "correct-horse-battery"


def utcnow():
    return datetime.now(UTC)


@pytest.fixture()
def env():
    """Three users with passwords, one opportunity to try to mutate."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    users = {}
    for key, role in (("admin", Role.ADMIN), ("dispatcher", Role.DISPATCHER),
                      ("tech", Role.TECH)):
        u = User(email="%s@x.test" % key, name=key.title(), role=role,
                 password_hash=hash_password(PASSWORD))
        db.add(u)
        users[key] = u
    db.flush()

    contact = Contact(first_name="Test", last_name="Caller", phone="(941) 555-0100")
    db.add(contact)
    pipe = Pipeline(name="AHS")
    db.add(pipe)
    db.flush()
    s1 = Stage(pipeline_id=pipe.id, name="New Lead", position=0)
    s2 = Stage(pipeline_id=pipe.id, name="Inspection", position=1)
    db.add_all([s1, s2])
    db.flush()
    opp = Opportunity(title="OPP", contact_id=contact.id, pipeline_id=pipe.id,
                      stage_id=s1.id, value_cents=1000)
    db.add(opp)
    db.flush()

    tokens = {}
    for key, u in users.items():
        plain, tok = mint_api_token(u, name=key)
        db.add(tok)
        tokens[key] = plain
    db.commit()

    ids = {"contact": contact.id, "opp": opp.id, "pipeline": pipe.id,
           "stage1": s1.id, "stage2": s2.id,
           **{"user_" + k: u.id for k, u in users.items()}}
    db.close()
    yield {"tokens": tokens, "ids": ids}


def client_for(env, who: str | None) -> TestClient:
    c = TestClient(app)
    if who:
        c.headers["Authorization"] = "Bearer " + env["tokens"][who]
    return c


# ---------------- the gate ----------------

def _protected_routes():
    """Every /api route that is not on the exemption allowlist."""
    out = []
    for r in app.routes:
        if not isinstance(r, APIRoute) or not r.path.startswith("/api"):
            continue
        if r.path in auth.EXEMPT:
            continue
        for method in sorted(r.methods - {"HEAD", "OPTIONS"}):
            out.append((method, r.path))
    return sorted(set(out))


@pytest.mark.parametrize("method,path", _protected_routes())
def test_every_api_route_requires_authentication(env, method, path):
    """Enumerated from app.routes, so a new unprotected route fails here.

    Asserts 401 specifically: a 404 or 422 would mean the request was routed and
    partially processed before anything checked who was asking.
    """
    concrete = (path.replace("{contact_id}", str(env["ids"]["contact"]))
                    .replace("{opp_id}", str(env["ids"]["opp"]))
                    .replace("{conv_id}", "1").replace("{tag_id}", "1")
                    .replace("{token_id}", "1").replace("{user_id}", "1")
                    .replace("{appointment_id}", "1").replace("{job_id}", "1"))
    r = client_for(env, None).request(method, concrete, json={})
    assert r.status_code == 401, "%s %s returned %d, not 401" % (
        method, concrete, r.status_code)


def test_exempt_routes_are_reachable_without_credentials(env):
    c = client_for(env, None)
    assert c.get("/api/health").status_code == 200
    # login must be reachable, and must still reject a bad password
    assert c.post("/api/auth/login",
                  json={"email": "admin@x.test", "password": "wrong"}
                  ).status_code == 401


def test_docs_are_open_because_app_dependencies_do_not_cover_them(env):
    """Locks in the verified FastAPI behaviour that shaped the gate's design.

    /docs and /openapi.json are plain Starlette routes with no dependant, so
    app-level dependencies never run for them. If a future FastAPI upgrade changes
    that, this test fails and tells us the exemption reasoning needs revisiting.
    """
    c = client_for(env, None)
    assert c.get("/openapi.json").status_code == 200
    assert c.get("/docs").status_code == 200


# ---------------- login ----------------

def test_login_sets_cookies_and_wrong_password_sets_none(env):
    c = TestClient(app)
    r = c.post("/api/auth/login",
               json={"email": "admin@x.test", "password": "wrong"})
    assert r.status_code == 401
    assert not c.cookies.keys(), "a failed login must not set a session cookie"

    r = c.post("/api/auth/login",
               json={"email": "admin@x.test", "password": PASSWORD})
    assert r.status_code == 200
    assert {auth.COOKIE_ACCESS, auth.COOKIE_REFRESH,
            auth.COOKIE_CSRF} <= set(c.cookies.keys())
    assert c.get("/api/contacts").status_code == 200


def test_unknown_email_and_wrong_password_are_indistinguishable(env):
    c = TestClient(app)
    a = c.post("/api/auth/login", json={"email": "nobody@x.test", "password": "x"})
    b = c.post("/api/auth/login", json={"email": "admin@x.test", "password": "x"})
    assert a.status_code == b.status_code == 401
    assert a.json()["detail"] == b.json()["detail"]


def test_a_user_without_a_password_can_never_log_in(env):
    """Every user seeded before auth existed has password_hash="". That must not
    be a way in — an empty stored hash has to fail, not match an empty password."""
    db = SessionLocal()
    db.add(User(email="legacy@x.test", name="Legacy", role=Role.ADMIN,
                password_hash=""))
    db.commit()
    db.close()
    c = TestClient(app)
    for attempt in ("", " ", "anything"):
        r = c.post("/api/auth/login",
                   json={"email": "legacy@x.test", "password": attempt})
        assert r.status_code == 401


def test_password_is_not_recoverable_from_what_is_stored(env):
    db = SessionLocal()
    stored = db.scalar(
        SessionLocal().query(User).filter(User.email == "admin@x.test")
        .statement.with_only_columns(User.password_hash))
    db.close()
    assert stored.startswith("scrypt$")
    assert PASSWORD not in stored


# ---------------- cookies, CSRF, refresh ----------------

def test_cookie_writes_need_a_csrf_token_but_bearer_writes_do_not(env):
    c = TestClient(app)
    c.post("/api/auth/login", json={"email": "admin@x.test", "password": PASSWORD})
    body = {"first_name": "Csrf", "last_name": "Test", "phone": "(941) 555-1111"}

    assert c.post("/api/contacts", json=body).status_code == 403
    r = c.post("/api/contacts", json=body,
               headers={"X-CSRF-Token": c.cookies.get(auth.COOKIE_CSRF)})
    assert r.status_code == 201

    # The CLI sends no cookie, so it needs no CSRF token.
    assert client_for(env, "admin").post("/api/contacts", json=body
                                         ).status_code == 201


def test_expired_access_token_is_rejected_then_refresh_mints_a_working_one(
        env, monkeypatch):
    """The 15-minute access token expiring must be recoverable, not a logout.

    Rather than forging a cookie, this patches only the token factory, so the server
    issues a live cookie (normal max-age, so the client keeps it) containing an
    already-expired JWT. Shortening ACCESS_TTL instead would drive the cookie's
    max-age negative and the client would simply discard it — that is a different
    failure ("no credential") from the one under test ("credential, but stale").

    This test caught a real bug. `current_principal` is a dependency of
    `require_auth`, so it ran BEFORE the exemption check and 401'd on the expired
    cookie — making refresh permanently unreachable.
    """
    monkeypatch.setattr(auth, "make_access_token",
                        lambda u: auth._make_token(u, "access",
                                                   timedelta(seconds=-10)))
    c = TestClient(app)
    assert c.post("/api/auth/login",
                  json={"email": "admin@x.test",
                        "password": PASSWORD}).status_code == 200

    r = c.get("/api/contacts")
    assert r.status_code == 401
    assert "expired" in r.json()["detail"]

    csrf = c.cookies.get(auth.COOKIE_CSRF)
    assert c.post("/api/auth/refresh").status_code == 403, "refresh needs CSRF too"

    # Restore the real factory so the refreshed token is actually usable.
    monkeypatch.undo()
    assert c.post("/api/auth/refresh",
                  headers={"X-CSRF-Token": csrf}).status_code == 200
    assert c.get("/api/contacts").status_code == 200


def test_changing_the_password_invalidates_tokens_issued_before_it(env):
    c = TestClient(app)
    c.post("/api/auth/login", json={"email": "admin@x.test", "password": PASSWORD})
    stale = c.cookies.get(auth.COOKIE_ACCESS)

    r = c.post("/api/auth/password",
               json={"current_password": PASSWORD, "new_password": "a-new-password"},
               headers={"X-CSRF-Token": c.cookies.get(auth.COOKIE_CSRF)})
    assert r.status_code == 200

    other = TestClient(app)
    other.cookies.set(auth.COOKIE_ACCESS, stale)
    assert other.get("/api/contacts").status_code == 401


def test_wrong_current_password_cannot_change_it(env):
    c = TestClient(app)
    c.post("/api/auth/login", json={"email": "admin@x.test", "password": PASSWORD})
    r = c.post("/api/auth/password",
               json={"current_password": "nope", "new_password": "a-new-password"},
               headers={"X-CSRF-Token": c.cookies.get(auth.COOKIE_CSRF)})
    assert r.status_code == 403
    # and the old password still works
    assert TestClient(app).post(
        "/api/auth/login",
        json={"email": "admin@x.test", "password": PASSWORD}).status_code == 200


# ---------------- API tokens ----------------

def test_a_revoked_token_stops_working_on_the_very_next_request(env):
    c = client_for(env, "admin")
    token_id = c.get("/api/auth/me").json()["token"]["id"]
    assert c.get("/api/contacts").status_code == 200

    assert c.delete("/api/auth/tokens/%d" % token_id).status_code == 200
    r = c.get("/api/contacts")
    assert r.status_code == 401
    assert r.json()["detail"] == "api token revoked"


def test_an_expired_token_is_rejected(env):
    db = SessionLocal()
    user = db.scalar(SessionLocal().query(User)
                     .filter(User.email == "admin@x.test").statement)
    plain, tok = mint_api_token(user, name="stale")
    tok.expires_at = utcnow() - timedelta(minutes=1)
    db.add(tok)
    db.commit()
    db.close()

    c = TestClient(app)
    c.headers["Authorization"] = "Bearer " + plain
    r = c.get("/api/contacts")
    assert r.status_code == 401
    assert r.json()["detail"] == "api token expired"


def test_the_plaintext_token_is_returned_once_and_never_stored(env):
    c = client_for(env, "admin")
    r = c.post("/api/auth/tokens", json={"name": "laptop"})
    assert r.status_code == 201
    plain = r.json()["token"]

    # It works...
    c2 = TestClient(app)
    c2.headers["Authorization"] = "Bearer " + plain
    assert c2.get("/api/contacts").status_code == 200

    # ...but it is not in the listing, and not in the database.
    listing = c.get("/api/auth/tokens").json()
    assert all("token" not in row for row in listing)
    db = SessionLocal()
    hashes = [t.token_hash for t in db.query(ApiToken).all()]
    db.close()
    assert plain not in hashes


def test_a_user_cannot_revoke_someone_elses_token(env):
    admin = client_for(env, "admin")
    tech = client_for(env, "tech")
    admin_token_id = admin.get("/api/auth/me").json()["token"]["id"]

    # 404 rather than 403 — do not confirm that the token exists.
    assert tech.delete("/api/auth/tokens/%d" % admin_token_id).status_code == 404
    assert admin.get("/api/contacts").status_code == 200


# ---------------- roles ----------------

def test_tech_cannot_create_a_contact_and_none_is_created(env):
    tech = client_for(env, "tech")
    before = client_for(env, "admin").get("/api/contacts").json()["total"]
    r = tech.post("/api/contacts", json={"first_name": "No", "last_name": "Way",
                                         "phone": "(941) 555-9999"})
    assert r.status_code == 403
    after = client_for(env, "admin").get("/api/contacts").json()["total"]
    assert after == before, "a 403 must not have created the contact anyway"


def test_tech_may_drag_an_opportunity_between_stages(env):
    """Deliberate: a field tech updates job status, but cannot edit the record."""
    tech = client_for(env, "tech")
    r = tech.patch("/api/opportunities/%d" % env["ids"]["opp"],
                   json={"stage_id": env["ids"]["stage2"], "position": 0})
    assert r.status_code == 200
    assert tech.get("/api/opportunities/%d" % env["ids"]["opp"]
                    ).json()["stage_id"] == env["ids"]["stage2"]

    # ...but the detail form is closed to them, and the value is untouched.
    r = tech.patch("/api/opportunities/%d/detail" % env["ids"]["opp"],
                   json={"value_cents": 999999})
    assert r.status_code == 403
    assert client_for(env, "admin").get(
        "/api/opportunities/%d" % env["ids"]["opp"]).json()["value_cents"] == 1000


def test_dispatcher_cannot_reach_the_admin_surface(env):
    d = client_for(env, "dispatcher")
    assert d.post("/api/users", json={"email": "new@x.test", "name": "New",
                                      "password": "a-password-1",
                                      "role": "TECH"}).status_code == 403
    assert d.patch("/api/users/%d" % env["ids"]["user_tech"],
                   json={"role": "ADMIN"}).status_code == 403


def test_only_admin_sees_email_addresses_in_the_roster(env):
    """The roster stays readable so owner dropdowns work; emails do not."""
    for who in ("dispatcher", "tech"):
        rows = client_for(env, who).get("/api/users").json()
        assert rows, "the roster must remain readable — the UI depends on it"
        assert all(r["email"] is None for r in rows)
        assert all(r["name"] for r in rows)
    assert all(r["email"] for r in client_for(env, "admin").get("/api/users").json())


def test_deactivating_a_user_takes_effect_immediately(env):
    admin = client_for(env, "admin")
    tech = client_for(env, "tech")
    assert tech.get("/api/contacts").status_code == 200

    assert admin.patch("/api/users/%d" % env["ids"]["user_tech"],
                       json={"is_active": False}).status_code == 200
    r = tech.get("/api/contacts")
    assert r.status_code == 401, "a deactivated user's token must stop working now"


# ---------------- scoped machine tokens ----------------

@pytest.fixture()
def ingest_token(env):
    db = SessionLocal()
    user = db.scalar(SessionLocal().query(User)
                     .filter(User.email == "dispatcher@x.test").statement)
    plain, tok = mint_api_token(user, name="owen-ingest", scopes="events:write")
    db.add(tok)
    db.commit()
    db.close()
    return plain


def test_the_telephony_token_can_ingest_and_nothing_else(env, ingest_token):
    c = TestClient(app)
    c.headers["Authorization"] = "Bearer " + ingest_token

    r = c.post("/api/events", json={"contact_id": env["ids"]["contact"],
                                    "type": "CALL", "direction": "INBOUND",
                                    "duration_seconds": 5})
    assert r.status_code == 201

    # Scoped to one job: it cannot read the contact database.
    assert c.get("/api/contacts").status_code == 403
    assert c.get("/api/opportunities?pipeline_id=%d" % env["ids"]["pipeline"]
                 ).status_code == 403


def test_a_tech_cannot_ingest_events(env):
    """Ingesting a short inbound call fires the missed-call automation, which
    texts a customer. That is not a field-tech action."""
    r = client_for(env, "tech").post(
        "/api/events", json={"contact_id": env["ids"]["contact"],
                             "type": "CALL", "direction": "INBOUND",
                             "duration_seconds": 5})
    assert r.status_code == 403


def test_a_scope_cannot_grant_more_than_the_role_allows(env):
    """Scopes narrow a credential; they must never widen it.

    Any user may mint a token with any scope string, so a TECH could ask for
    "events:write" — the scope a machine token uses to reach an endpoint their
    role forbids. Found by review: the ingest dependency returned early on the
    scope before ever checking the role.
    """
    tech = client_for(env, "tech")
    minted = tech.post("/api/auth/tokens",
                       json={"name": "escalate", "scopes": "events:write"})
    assert minted.status_code == 201, "minting a scoped token is allowed"

    escalated = TestClient(app)
    escalated.headers["Authorization"] = "Bearer " + minted.json()["token"]
    r = escalated.post("/api/events",
                       json={"contact_id": env["ids"]["contact"],
                             "type": "CALL", "direction": "INBOUND",
                             "duration_seconds": 5})
    assert r.status_code == 403, "a TECH's own scope must not buy them the endpoint"

    db = SessionLocal()
    events = db.query(ConversationEvent).count()
    db.close()
    assert events == 0, "the rejected ingest must not have written an event"


def test_an_admin_scope_does_not_make_a_dispatcher_an_admin(env):
    """Same invariant on the admin surface."""
    d = client_for(env, "dispatcher")
    minted = d.post("/api/auth/tokens",
                    json={"name": "wishful", "scopes": "admin"})
    escalated = TestClient(app)
    escalated.headers["Authorization"] = "Bearer " + minted.json()["token"]
    assert escalated.get("/api/jobs").status_code == 403

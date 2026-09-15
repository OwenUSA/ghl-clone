"""My Staff — Settings' user management (operator addendum, 2026-09-15).

Behaviour, not status codes: a refusal is re-read to prove it changed nothing, a
deactivation is proven by a session that stops working, and the forced password change
is proven by a real browser session that can reach nothing else.
"""
import pytest
from app.auth import hash_password, mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    ApiToken,
    Calendar,
    Contact,
    Opportunity,
    OpportunityNote,
    OpportunityTask,
    Pipeline,
    Role,
    Stage,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import func, select

PASSWORD = "admin-password-1"


@pytest.fixture()
def staff():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    owen = User(email="owen@x.test", name="Owen Buzaglo", role=Role.ADMIN,
                password_hash=hash_password(PASSWORD), phone="+15619903772")
    dana = User(email="dana@x.test", name="Dana Dispatch", role=Role.DISPATCHER,
                password_hash=hash_password(PASSWORD))
    luis = User(email="luis@x.test", name="Luis Candialies", role=Role.TECH,
                password_hash=hash_password(PASSWORD), only_assigned_data=True)
    feed = User(email="owen-telephony@x.test", name="OWEN telephony", role=Role.DISPATCHER,
                password_hash="")
    db.add_all([owen, dana, luis, feed])
    db.flush()
    p = Pipeline(name="Main")
    db.add(p)
    db.flush()
    st = Stage(pipeline_id=p.id, name="Lead", position=0)
    db.add(st)
    db.flush()
    c = Contact(first_name="Jane", last_name="Roof", phone="+19415550100")
    db.add(c)
    db.flush()
    o = Opportunity(title="Jane roof", contact_id=c.id, pipeline_id=p.id, stage_id=st.id,
                    owner_id=luis.id)
    db.add(o)
    db.flush()
    db.add(OpportunityTask(opportunity_id=o.id, title="Ladder", assigned_user_id=luis.id,
                           created_by_id=luis.id))
    db.add(OpportunityNote(opportunity_id=o.id, body="Flashing loose",
                           created_by_id=luis.id))
    tokens = {}
    for key, u in (("owen", owen), ("dana", dana), ("luis", luis), ("feed", feed)):
        plain, tok = mint_api_token(u, name="t-" + key,
                                    scopes="events:write" if key == "feed" else "")
        db.add(tok)
        tokens[key] = plain
    db.commit()
    ids = {"owen": owen.id, "dana": dana.id, "luis": luis.id, "feed": feed.id,
           "opp": o.id}
    db.close()

    def client(key):
        cl = TestClient(app)
        cl.headers["Authorization"] = "Bearer " + tokens[key]
        return cl
    yield ids, client, tokens


def _user(pk):
    db = SessionLocal()
    try:
        u = db.get(User, pk)
        db.expunge(u)
        return u
    finally:
        db.close()


def _browser(email, password):
    b = TestClient(app)
    r = b.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    b.headers["X-CSRF-Token"] = b.cookies.get("ghl_csrf")
    return b, r.json()["user"]


# ---------- only an ADMIN ----------

def test_only_an_admin_reads_staff_details_and_manages_users(staff):
    ids, client, _ = staff
    admin = {u["id"]: u for u in client("owen").get("/api/users").json()}
    assert admin[ids["owen"]]["phone"] == "+15619903772"
    assert admin[ids["owen"]]["email"] == "owen@x.test"
    for who in ("dana", "luis"):
        rows = client(who).get("/api/users").json()
        assert all(r["email"] is None and "phone" not in r for r in rows), who
        r = client(who).post("/api/users", json={"email": "x@x.test", "name": "X",
                                                 "password": "long-enough-1"})
        assert r.status_code == 403
        r = client(who).patch("/api/users/%d" % ids["luis"], json={"is_active": False})
        assert r.status_code == 403
    db = SessionLocal()
    assert db.scalar(select(func.count(User.id))) == 4
    assert db.get(User, ids["luis"]).is_active is True
    db.close()


def test_search_and_role_filter_narrow_the_list(staff):
    ids, client, _ = staff
    admin = client("owen")

    def names(**params):
        return sorted(u["name"] for u in admin.get("/api/users", params=params).json())

    assert len(names()) == 4
    assert names(role="TECH") == ["Luis Candialies"]
    assert names(role="ADMIN") == ["Owen Buzaglo"]
    assert names(q="candial") == ["Luis Candialies"]
    assert names(q="dana@x") == ["Dana Dispatch"]
    assert names(q="(561) 990-3772") == ["Owen Buzaglo"], "phone, as it is shown"
    assert names(q=str(ids["dana"])) == ["Dana Dispatch"]
    assert names(q="candial", role="ADMIN") == []


def test_machine_accounts_are_marked_and_can_never_be_given_a_password(staff):
    ids, client, tokens = staff
    rows = {u["id"]: u for u in client("owen").get("/api/users").json()}
    assert rows[ids["feed"]]["machine"] is True and rows[ids["luis"]]["machine"] is False
    r = client("owen").patch("/api/users/%d" % ids["feed"], json={"password": "a-login-now"})
    assert r.status_code == 400 and "machine account" in r.json()["detail"]
    assert _user(ids["feed"]).password_hash == ""
    b = TestClient(app)
    assert b.post("/api/auth/login", json={"email": "owen-telephony@x.test",
                                           "password": "a-login-now"}).status_code == 401


# ---------- add ----------

def test_add_creates_the_user_their_calendar_and_forces_a_password_change(staff):
    ids, client, _ = staff
    r = client("owen").post("/api/users", json={
        "email": "Antonio@X.test", "name": "Antonio Brown", "password": "first-pass-1",
        "role": "TECH", "phone": "(941) 555-0199"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "antonio@x.test" and body["phone"] == "+19415550199"
    assert body["only_assigned_data"] is True and body["must_change_password"] is True
    assert body["calendar"]["created"] is True and body["calendar"]["name"] == "Antonio Brown"
    db = SessionLocal()
    cal = db.scalar(select(Calendar).where(Calendar.user_id == body["id"]))
    assert cal is not None and cal.name == "Antonio Brown"
    db.close()

    # First sign-in: the session exists, but it can reach nothing but the change.
    b, user = _browser("antonio@x.test", "first-pass-1")
    assert user["must_change_password"] is True
    assert b.get("/api/auth/me").status_code == 200
    for method, url in (("get", "/api/opportunities"), ("get", "/api/contacts"),
                        ("get", "/api/conversations"), ("post", "/api/auth/tokens")):
        kw = {"json": {"name": "sneaky"}} if method == "post" else {}
        refused = getattr(b, method)(url, **kw)
        assert refused.status_code == 403, url
        assert "new password" in refused.json()["detail"]
    db = SessionLocal()
    assert db.scalar(select(func.count(ApiToken.id)).where(
        ApiToken.user_id == body["id"])) == 0, "no token slipped past the gate"
    db.close()
    same = b.post("/api/auth/password", json={"current_password": "first-pass-1",
                                              "new_password": "first-pass-1"})
    assert same.status_code == 400
    changed = b.post("/api/auth/password", json={"current_password": "first-pass-1",
                                                 "new_password": "my-own-pass-1"})
    assert changed.status_code == 200
    b.headers["X-CSRF-Token"] = b.cookies.get("ghl_csrf")
    assert b.get("/api/opportunities").status_code == 200
    assert _user(body["id"]).must_change_password is False


def test_dispatchers_and_admins_get_no_calendar_and_the_switch_defaults_off(staff):
    ids, client, _ = staff
    for role in ("DISPATCHER", "ADMIN"):
        r = client("owen").post("/api/users", json={
            "email": role.lower() + "@new.test", "name": "New " + role,
            "password": "first-pass-1", "role": role})
        assert r.json()["calendar"] is None and r.json()["only_assigned_data"] is False
    tech_off = client("owen").post("/api/users", json={
        "email": "t@new.test", "name": "Tech Off", "password": "first-pass-1",
        "role": "TECH", "only_assigned_data": False})
    assert tech_off.json()["only_assigned_data"] is False


def test_a_calendar_with_the_same_name_is_left_alone_and_the_new_one_is_numbered(staff):
    ids, client, _ = staff
    db = SessionLocal()
    existing = Calendar(name="Antonio Brown", user_id=None)
    db.add(existing)
    db.commit()
    existing_id = existing.id
    db.close()
    r = client("owen").post("/api/users", json={
        "email": "antonio@x.test", "name": "Antonio Brown", "password": "first-pass-1",
        "role": "TECH"}).json()
    assert r["calendar"]["name"] == "Antonio Brown (2)"
    assert r["calendar"]["renamed_from"] == "Antonio Brown"
    db = SessionLocal()
    assert db.get(Calendar, existing_id).user_id is None, "the old calendar is untouched"
    assert db.scalar(select(func.count(Calendar.id)).where(
        Calendar.user_id == r["id"])) == 1
    db.close()


def test_a_technician_never_gets_a_second_calendar(staff):
    from app.main import _technician_calendar
    ids, client, _ = staff
    db = SessionLocal()
    u = db.get(User, ids["luis"])
    first = _technician_calendar(db, u)
    again = _technician_calendar(db, u)
    db.commit()
    assert first["created"] is True
    assert again["created"] is False and again["id"] == first["id"]
    assert db.scalar(select(func.count(Calendar.id)).where(
        Calendar.user_id == ids["luis"])) == 1
    db.close()


# ---------- edit, reset ----------

def test_edit_changes_name_email_phone_role_and_the_switch(staff):
    ids, client, _ = staff
    r = client("owen").patch("/api/users/%d" % ids["dana"], json={
        "name": "Dana  Smith", "email": "DANA.S@x.test", "phone": "941-555-0142",
        "role": "TECH", "only_assigned_data": True})
    assert r.status_code == 200, r.text
    u = _user(ids["dana"])
    assert (u.name, u.email, u.phone, u.role, u.only_assigned_data) == (
        "Dana Smith", "dana.s@x.test", "+19415550142", Role.TECH, True)
    phone_cleared = client("owen").patch("/api/users/%d" % ids["dana"], json={"phone": ""})
    assert phone_cleared.json()["phone"] is None


def test_an_email_another_user_holds_is_refused_and_nothing_changes(staff):
    ids, client, _ = staff
    r = client("owen").patch("/api/users/%d" % ids["dana"],
                             json={"email": "luis@x.test", "name": "Changed"})
    assert r.status_code == 400
    u = _user(ids["dana"])
    assert u.email == "dana@x.test" and u.name == "Dana Dispatch"


def test_an_admin_password_reset_forces_a_change_at_next_sign_in(staff):
    ids, client, _ = staff
    b, _ = _browser("luis@x.test", PASSWORD)
    assert b.get("/api/opportunities").status_code == 200
    client("owen").patch("/api/users/%d" % ids["luis"], json={"password": "temp-pass-12"})
    assert b.get("/api/opportunities").status_code == 401, "the old session ended"
    b2, user = _browser("luis@x.test", "temp-pass-12")
    assert user["must_change_password"] is True
    assert b2.get("/api/opportunities").status_code == 403


# ---------- deactivate / reactivate ----------

def test_deactivate_blocks_login_ends_sessions_and_revokes_tokens_but_keeps_their_work(
        staff):
    ids, client, tokens = staff
    b, _ = _browser("luis@x.test", PASSWORD)
    assert client("luis").get("/api/contacts").status_code == 200
    r = client("owen").patch("/api/users/%d" % ids["luis"], json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert b.get("/api/contacts").status_code == 401, "browser session ended now"
    assert client("luis").get("/api/contacts").status_code == 401, "token refused now"
    login = TestClient(app).post("/api/auth/login",
                                 json={"email": "luis@x.test", "password": PASSWORD})
    assert login.status_code == 403
    db = SessionLocal()
    assert all(t.revoked_at is not None for t in db.scalars(
        select(ApiToken).where(ApiToken.user_id == ids["luis"])).all())
    assert db.get(User, ids["luis"]) is not None, "never hard-deleted"
    db.close()
    opp = client("owen").get("/api/opportunities/%d" % ids["opp"]).json()
    assert opp["owner_name"] == "Luis Candialies"
    notes = client("owen").get("/api/opportunities/%d/notes" % ids["opp"]).json()
    assert notes["notes"][0]["created_by"] == "Luis Candialies"
    tasks = client("owen").get("/api/opportunities/%d/tasks" % ids["opp"]).json()
    assert tasks[0]["assigned_user_name"] == "Luis Candialies"

    back = client("owen").patch("/api/users/%d" % ids["luis"], json={"is_active": True})
    assert back.json()["is_active"] is True
    b3, _ = _browser("luis@x.test", PASSWORD)
    assert b3.get("/api/contacts").status_code == 200, "reactivated: can sign in again"
    assert client("luis").get("/api/contacts").status_code == 401, (
        "a revoked token stays revoked — a reactivated person mints a new one")


def test_a_machine_accounts_token_is_refused_while_inactive_and_works_again_after(staff):
    ids, client, tokens = staff
    feed = TestClient(app)
    feed.headers["Authorization"] = "Bearer " + tokens["feed"]
    body = {"from_number": "+19415550100", "type": "SMS", "body": "hello"}
    assert feed.post("/api/events", json=body).status_code == 201
    client("owen").patch("/api/users/%d" % ids["feed"], json={"is_active": False})
    assert feed.post("/api/events", json=body).status_code == 401
    client("owen").patch("/api/users/%d" % ids["feed"], json={"is_active": True})
    assert feed.post("/api/events", json=body).status_code == 201, (
        "the telephony token was not destroyed")


def test_an_admin_cannot_deactivate_or_demote_themselves(staff):
    ids, client, _ = staff
    db = SessionLocal()
    db.add(User(email="second@x.test", name="Second Admin", role=Role.ADMIN,
                password_hash=hash_password(PASSWORD)))
    db.commit()
    db.close()
    for body in ({"is_active": False}, {"role": "DISPATCHER"}):
        r = client("owen").patch("/api/users/%d" % ids["owen"], json=body)
        assert r.status_code == 400, body
        u = _user(ids["owen"])
        assert u.is_active is True and u.role is Role.ADMIN


def test_the_last_active_admin_can_never_be_deactivated_or_demoted(staff):
    """Owen is the only admin who can sign in. The telephony machine account, made an
    ADMIN with an unscoped token, is an admin caller who is NOT Owen — and still cannot
    remove him, because a machine account does not count as an admin who can manage."""
    ids, client, tokens = staff
    db = SessionLocal()
    feed = db.get(User, ids["feed"])
    feed.role = Role.ADMIN
    plain, tok = mint_api_token(feed, name="unscoped")
    db.add(tok)
    db.commit()
    db.close()
    machine = TestClient(app)
    machine.headers["Authorization"] = "Bearer " + plain
    for body in ({"is_active": False}, {"role": "DISPATCHER"}, {"role": "TECH"}):
        r = machine.patch("/api/users/%d" % ids["owen"], json=body)
        assert r.status_code == 409, body
        assert "last active admin" in r.json()["detail"]
        u = _user(ids["owen"])
        assert u.is_active is True and u.role is Role.ADMIN

    # With a second admin who can sign in, removing one of the two is allowed...
    db = SessionLocal()
    second = User(email="second@x.test", name="Second Admin", role=Role.ADMIN,
                  password_hash=hash_password(PASSWORD))
    db.add(second)
    db.commit()
    second_id = second.id
    db.close()
    assert client("owen").patch("/api/users/%d" % second_id,
                                json={"role": "DISPATCHER"}).status_code == 200
    # ...and then Owen is the last again, and an INACTIVE admin does not count.
    db = SessionLocal()
    db.add(User(email="gone@x.test", name="Gone Admin", role=Role.ADMIN,
                password_hash=hash_password(PASSWORD), is_active=False))
    db.commit()
    db.close()
    assert machine.patch("/api/users/%d" % ids["owen"],
                         json={"is_active": False}).status_code == 409
    assert _user(ids["owen"]).is_active is True


def test_phone_round_trips(staff):
    ids, client, _ = staff
    client("owen").patch("/api/users/%d" % ids["luis"], json={"phone": "+1 (941) 555 0177"})
    row = next(u for u in client("owen").get("/api/users").json() if u["id"] == ids["luis"])
    assert row["phone"] == "+19415550177"
    assert row["phone_display"]
    me = client("luis").get("/api/auth/me").json()["user"]
    assert me["phone"] == "+19415550177"

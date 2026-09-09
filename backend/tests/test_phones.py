"""Phone numbers: store E.164, display formatted, on writes only.

The owner's decision (2026-09-09) has three halves and each is load-bearing:

1. a bare 10-digit number is a US number, because the business is in Bradenton FL;
2. a number that already carries a `+` keeps its own country code — the owner's
   own mobile is Venezuelan and must survive a save unchanged;
3. **existing rows are not rewritten.** Production holds real contacts whose
   numbers were typed by hand. A backfill is a separate decision.

These assert behaviour through the API, not the helper's return value alone: the
question that matters is what is in the row afterwards.
"""
import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Contact, Role, User
from app.phones import InvalidPhone, format_phone, normalize_phone
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    admin = User(email="a@x.test", name="Owen", role=Role.ADMIN)
    tech = User(email="t@x.test", name="Tech", role=Role.TECH)
    db.add_all([admin, tech])
    db.flush()
    # Written straight to the row, the way the 268 seeded contacts and the 13 real
    # production ones were. Nothing in this module may rewrite it.
    legacy = Contact(first_name="Legacy", last_name="Row", phone="(941) 555-0100")
    db.add(legacy)
    db.flush()
    ids = {"legacy": legacy.id}
    tokens = {}
    for key, u in (("admin", admin), ("tech", tech)):
        plain, tok = mint_api_token(u, name="t-" + key)
        db.add(tok)
        tokens[key] = plain
    db.commit()
    db.close()
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + tokens["admin"]
        c.ids = ids
        c.tokens = tokens
        yield c


# ---------------- the helper ----------------

@pytest.mark.parametrize("typed,stored", [
    ("8135550102", "+18135550102"),
    ("(813) 555-0102", "+18135550102"),
    ("813-555-0102", "+18135550102"),
    ("813.555.0102", "+18135550102"),
    ("1 813 555 0102", "+18135550102"),
    ("+1 (813) 555-0102", "+18135550102"),
    # Already correct: idempotent, so re-saving a contact cannot drift the number.
    ("+18135550102", "+18135550102"),
])
def test_a_us_number_however_it_is_typed_stores_as_one_string(typed, stored):
    assert normalize_phone(typed) == stored


def test_a_number_with_a_country_code_is_not_re_homed_to_the_us():
    """The owner's own number is Venezuelan. Defaulting it to +1 would break it."""
    assert normalize_phone("+58 412 123 4567") == "+584121234567"
    assert normalize_phone("+584121234567") == "+584121234567"
    # `00` is the same statement as `+` in most of the world.
    assert normalize_phone("00584121234567") == "+584121234567"
    # ...and it is not formatted as if it were American.
    assert format_phone("+584121234567") == "+584121234567"


def test_blank_is_absent_rather_than_invalid():
    for blank in (None, "", "   "):
        assert normalize_phone(blank) is None


@pytest.mark.parametrize("junk", [
    "12345",              # too few digits for anything
    "555-0102",           # 7 digits: a US local number with no area code
    "81355501020",        # 11 digits that do not start with 1
    "not a phone",
    "813555010x",
    "+123",               # too short to be an international number
    "+1234567890123456",  # longer than E.164 allows
    "(013) 555-0102",     # NANP area codes cannot start with 0
    "(813) 155-0102",     # ...nor can exchanges start with 1
])
def test_an_invalid_number_is_refused_not_mangled(junk):
    with pytest.raises(InvalidPhone) as e:
        normalize_phone(junk)
    assert str(e.value), "the refusal must carry a message a person can read"


def test_the_refusal_says_what_is_wrong():
    """"Invalid phone number" sends the user hunting. Name the problem."""
    with pytest.raises(InvalidPhone) as e:
        normalize_phone("5550102")
    assert "10 digits" in str(e.value) and "7" in str(e.value)
    with pytest.raises(InvalidPhone) as e:
        normalize_phone("+44")
    assert "too short" in str(e.value)


def test_display_formatting_only_claims_to_know_us_numbers():
    assert format_phone("+18135550102") == "(813) 555-0102"
    assert format_phone("+584121234567") == "+584121234567"
    # A row written before normalisation existed renders as whatever it holds.
    assert format_phone("(941) 555-0100") == "(941) 555-0100"
    assert format_phone(None) is None


# ---------------- through the API ----------------

def test_a_created_contact_is_stored_e164_and_shown_formatted(client):
    r = client.post("/api/contacts", json={"first_name": "Jane", "phone": "8135550102"})
    assert r.status_code == 201
    body = r.json()
    assert body["phone"] == "+18135550102", "the row does not hold E.164"
    assert body["phone_display"] == "(813) 555-0102", "the panel would show E.164"

    # ...and the grid agrees with the panel.
    row = next(i for i in client.get("/api/contacts?page_size=100").json()["items"]
               if i["id"] == body["id"])
    assert row["phone"] == "+18135550102"
    assert row["phone_display"] == "(813) 555-0102"


def test_the_owners_venezuelan_number_survives_a_create(client):
    r = client.post("/api/contacts",
                    json={"first_name": "Owen", "phone": "+58 412 123 4567"})
    assert r.status_code == 201
    assert r.json()["phone"] == "+584121234567", "a +58 number was re-homed to the US"


def test_an_invalid_number_creates_nothing(client):
    before = client.get("/api/contacts?page_size=100").json()["total"]
    r = client.post("/api/contacts", json={"first_name": "Ghost", "phone": "5550102"})
    assert r.status_code == 422
    assert "10 digits" in r.text, "the refusal does not say what is wrong"
    after = client.get("/api/contacts?page_size=100").json()
    assert after["total"] == before, "a refused create still wrote a row"
    assert not [i for i in after["items"] if i["first_name"] == "Ghost"]


def test_an_invalid_number_does_not_overwrite_a_good_one(client):
    made = client.post("/api/contacts",
                       json={"first_name": "Jane", "phone": "8135550102"}).json()
    r = client.patch("/api/contacts/%d" % made["id"], json={"phone": "nonsense"})
    assert r.status_code == 422
    assert client.get("/api/contacts/%d" % made["id"]).json()["phone"] == "+18135550102"


def test_editing_a_contact_normalises_the_number(client):
    made = client.post("/api/contacts",
                       json={"first_name": "Jane", "email": "j@x.test"}).json()
    assert made["phone"] is None
    patched = client.patch("/api/contacts/%d" % made["id"],
                           json={"phone": "(727) 555-0143"}).json()
    assert patched["phone"] == "+17275550143"
    assert patched["phone_display"] == "(727) 555-0143"


def test_an_existing_row_is_left_exactly_as_it_was(client):
    """No backfill. Production holds real numbers the owner has not decided about.

    Reading a legacy contact, and editing an unrelated field on it, must both
    leave the stored number byte-for-byte as it was.
    """
    cid = client.ids["legacy"]
    assert client.get("/api/contacts/%d" % cid).json()["phone"] == "(941) 555-0100"

    client.patch("/api/contacts/%d" % cid, json={"business_name": "Legacy LLC"})
    after = client.get("/api/contacts/%d" % cid).json()
    assert after["phone"] == "(941) 555-0100", "an unrelated edit rewrote the number"
    assert after["business_name"] == "Legacy LLC"
    # It is still displayed as it is stored, rather than blanked for not parsing.
    assert after["phone_display"] == "(941) 555-0100"


def test_a_tech_cannot_write_a_number_at_all(client):
    """A role refusal must mutate nothing — not even a valid, normalisable number."""
    cid = client.ids["legacy"]
    tech = {"Authorization": "Bearer " + client.tokens["tech"]}
    r = client.patch("/api/contacts/%d" % cid, json={"phone": "8135550102"},
                     headers=tech)
    assert r.status_code == 403
    assert client.get("/api/contacts/%d" % cid).json()["phone"] == "(941) 555-0100"

    before = client.get("/api/contacts?page_size=100").json()["total"]
    assert client.post("/api/contacts", json={"first_name": "T", "phone": "8135550103"},
                       headers=tech).status_code == 403
    assert client.get("/api/contacts?page_size=100").json()["total"] == before

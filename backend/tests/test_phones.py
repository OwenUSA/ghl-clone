"""Phone numbers: store E.164 when we can, never block a save.

The owner's decision has four halves and each is load-bearing:

1. a bare 10-digit number is a US number, because the business is in Bradenton FL;
2. a number that already carries a `+` keeps its own country code — the owner's
   own mobile is Venezuelan and must survive a save unchanged;
3. **existing rows are not rewritten.** Production holds real contacts whose
   numbers were typed by hand. A backfill is a separate decision.
4. (2026-09-10) **a number never stops a contact being saved.** One we cannot parse
   is stored exactly as typed and the save succeeds. Staff enter numbers with a
   customer in front of them; a refusal at that moment loses the number.

These assert behaviour through the API, not the helper's return value alone: the
question that matters is what is in the row afterwards.
"""
import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Contact, Role, User
from app.phones import (
    InvalidPhone,
    format_phone,
    normalize_phone,
    phone_warning,
    store_phone,
)
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
    # The write path agrees with the parser whenever the parser succeeds.
    assert store_phone(typed) == stored
    assert phone_warning(stored) is None


def test_a_number_with_a_country_code_is_not_re_homed_to_the_us():
    """The owner's own number is Venezuelan. Defaulting it to +1 would break it."""
    assert normalize_phone("+58 412 123 4567") == "+584121234567"
    assert store_phone("+58 412 123 4567") == "+584121234567"
    assert normalize_phone("+584121234567") == "+584121234567"
    # `00` is the same statement as `+` in most of the world.
    assert normalize_phone("00584121234567") == "+584121234567"
    # ...and it is not formatted as if it were American.
    assert format_phone("+584121234567") == "+584121234567"


def test_blank_is_absent_rather_than_invalid():
    for blank in (None, "", "   "):
        assert normalize_phone(blank) is None
        assert store_phone(blank) is None
        assert phone_warning(blank) is None


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
def test_the_parser_says_no_to_what_it_cannot_read(junk):
    """`normalize_phone` is the parser, not the write path — it is allowed to fail.

    `store_phone` is what a write calls, and it turns each of these into a stored
    value rather than a refusal (see below).
    """
    with pytest.raises(InvalidPhone) as e:
        normalize_phone(junk)
    assert str(e.value), "the refusal must carry a message a person can read"

    # ...and the same input, on the write path, is kept rather than lost.
    assert store_phone(junk) == junk.strip()
    assert phone_warning(store_phone(junk)), "a kept-as-typed number says nothing"


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


@pytest.mark.parametrize("typed", [
    "5550102",                  # a US local number with no area code
    "941 555 0100 ext 12",      # a real number with an extension on it
    "0424-1234567",             # a Venezuelan number typed the way it is at home
    "ask for Maria 9415550100",
])
def test_a_number_we_cannot_parse_still_saves_a_contact(client, typed):
    """The behaviour change. A refusal here loses a real customer's number.

    What matters is the row afterwards: the contact exists and holds the exact
    characters that were typed.
    """
    before = client.get("/api/contacts?page_size=100").json()["total"]
    r = client.post("/api/contacts", json={"first_name": "Walkup", "phone": typed})
    assert r.status_code == 201, "an unrecognised number blocked the save"
    body = r.json()
    assert body["phone"] == typed, "the number was mangled instead of kept"
    assert body["phone_display"] == typed, "an unreadable number is shown as typed"

    after = client.get("/api/contacts?page_size=100").json()
    assert after["total"] == before + 1, "the contact was not actually created"
    saved = client.get("/api/contacts/%d" % body["id"]).json()
    assert saved["phone"] == typed
    # Saved, and flagged — but flagged is a note beside it, not a wall in front.
    assert saved["phone_warning"], "nothing told anyone the number looked odd"


def test_a_number_we_cannot_parse_still_saves_on_an_edit(client):
    """Same rule on PATCH: correcting a contact mid-job cannot be refused."""
    made = client.post("/api/contacts",
                       json={"first_name": "Jane", "phone": "8135550102"}).json()
    assert made["phone"] == "+18135550102"

    r = client.patch("/api/contacts/%d" % made["id"],
                     json={"phone": "941 555 0100 ext 12"})
    assert r.status_code == 200, "an unrecognised number blocked an edit"
    assert r.json()["phone"] == "941 555 0100 ext 12"

    reread = client.get("/api/contacts/%d" % made["id"]).json()
    assert reread["phone"] == "941 555 0100 ext 12", "the edit did not stick"
    assert reread["phone_warning"]


def test_a_good_number_carries_no_warning(client):
    """The signal has to be quiet when nothing is wrong, or it means nothing."""
    made = client.post("/api/contacts",
                       json={"first_name": "Jane", "phone": "(813) 555-0102"}).json()
    assert made["phone"] == "+18135550102"
    assert made["phone_warning"] is None
    # A legacy row parses too, so it is not nagged about either.
    legacy = client.get("/api/contacts/%d" % client.ids["legacy"]).json()
    assert legacy["phone"] == "(941) 555-0100"
    assert legacy["phone_warning"] is None
    # ...and a contact with no number at all is not "invalid", it is empty.
    none = client.post("/api/contacts",
                       json={"first_name": "Ed", "email": "e@x.test"}).json()
    assert none["phone"] is None and none["phone_warning"] is None


def test_the_grid_and_the_panel_agree_about_an_unparseable_number(client):
    made = client.post("/api/contacts",
                       json={"first_name": "Walkup", "phone": "ask for Maria"}).json()
    row = next(i for i in client.get("/api/contacts?page_size=100").json()["items"]
               if i["id"] == made["id"])
    panel = client.get("/api/contacts/%d" % made["id"]).json()
    assert row["phone"] == panel["phone"] == "ask for Maria"
    assert row["phone_display"] == panel["phone_display"] == "ask for Maria"
    assert row["phone_warning"] == panel["phone_warning"]
    assert row["phone_warning"]


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

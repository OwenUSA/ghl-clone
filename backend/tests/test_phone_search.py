"""Searching contacts by phone number, in whatever shape the number was typed.

The bug: `/api/contacts?q=8135550102` found nobody when the row said
`(813) 555-0102`, which is the one shape a person is most likely to type — it is
what their phone shows them. And `feature/phone-normalisation` stores new
numbers as `+18135550102` while leaving the existing production rows alone, so
the database holds both formats at once and matching the stored string can only
ever find one of them.

These tests assert behaviour, not shape: the same person is found through three
different spellings of their number, a name is still a name, and the search
still narrows the set rather than returning everything.
"""
import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Contact, Role, User
from app.phone_match import digits, looks_like_phone, match_key, same_number
from fastapi.testclient import TestClient

# One number, two rows, two storage formats — exactly the production situation.
LEGACY = "(813) 555-0102"      # typed by hand before normalisation existed
E164 = "+18135550102"          # what a write stores now

# The three ways a person types that same number.
SPELLINGS = ["8135550102", "(813) 555-0102", "813-555-0102", "+1 813 555 0102"]


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    admin = User(email="owner@x.test", name="Owen", role=Role.ADMIN)
    db.add(admin)
    db.flush()

    db.add_all([
        Contact(first_name="Dana", last_name="Legacy", phone=LEGACY),
        Contact(first_name="Ray", last_name="Modern", phone=E164),
        Contact(first_name="Maria", last_name="Alvarez", phone="+19415550188"),
        Contact(first_name="Nolan", last_name="Nophone", phone=None),
        # Phone-shaped once the slash is dropped, and a real business name.
        Contact(first_name="Ted", last_name="Nights", phone="+19415550199",
                business_name="24/7 Roofing"),
        # No phone, but a carrier email-to-SMS address — which is a phone number
        # spelled as an email, and only the text search can find it.
        Contact(first_name="Casey", last_name="Gateway", phone=None,
                email="8635550123@vtext.com"),
    ])
    plain, token = mint_api_token(admin, name="test")
    db.add(token)
    db.commit()
    db.close()

    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + plain
        yield c


def names(response) -> set[str]:
    assert response.status_code == 200, response.text
    return {i["name"] for i in response.json()["items"]}


def search(client, q):
    return client.get("/api/contacts", params={"q": q, "page_size": 50})


# ---------------- the rule itself ----------------

def test_looks_like_phone_accepts_every_way_a_number_is_typed():
    for spelling in SPELLINGS:
        assert looks_like_phone(spelling), spelling


@pytest.mark.parametrize("q", ["Maria", "", "   ", "Roofing 24", "813-CALL", "x"])
def test_looks_like_phone_rejects_anything_with_letters_or_nothing_to_match(q):
    """A name must never be stripped to digits: `match_key("Maria")` is `""`,
    and a `%%`-style key would match every contact in the database."""
    assert not looks_like_phone(q)


def test_a_two_digit_query_is_not_a_phone_search():
    """Below MIN_MATCH_DIGITS a number is noise, not a search."""
    assert not looks_like_phone("81")
    assert looks_like_phone("0102")


def test_match_key_is_the_last_ten_digits_whatever_the_country_code():
    assert {match_key(s) for s in SPELLINGS} == {"8135550102"}
    assert match_key("00 1 813 555 0102") == "8135550102"


def test_same_number_ignores_formatting_and_country_code():
    assert same_number(LEGACY, E164)
    assert same_number("813.555.0102", "+1 (813) 555-0102")
    assert not same_number(LEGACY, "+19415550188")
    # A blank is not "the same number" as anything, including another blank.
    assert not same_number(None, None)
    assert not same_number("", LEGACY)


def test_digits_keeps_order_and_drops_everything_else():
    assert digits("+1 (813) 555-0102") == "18135550102"
    assert digits(None) == ""


# ---------------- what the API does with it ----------------

def test_digits_find_a_contact_stored_in_the_legacy_format(client):
    """The original complaint: typing the number as it appears on a phone."""
    assert "Dana Legacy" in names(search(client, "8135550102"))


def test_formatted_query_finds_a_contact_stored_as_e164(client):
    """And the reverse, which is what the concurrent branch creates from now on."""
    assert "Ray Modern" in names(search(client, "(813) 555-0102"))


def test_one_number_finds_both_storage_formats_at_once(client):
    """Two rows, two formats, one line. Every spelling reaches both."""
    for spelling in SPELLINGS:
        assert names(search(client, spelling)) == {"Dana Legacy", "Ray Modern"}, spelling


def test_the_phone_search_still_narrows_the_set(client):
    everyone = client.get("/api/contacts", params={"page_size": 50}).json()["total"]
    hit = search(client, "813-555-0102").json()
    assert hit["total"] == 2 < everyone
    # The count has to agree with the rows, or pagination lies about the pages.
    assert len(hit["items"]) == hit["total"]


def test_a_name_is_still_matched_as_a_name(client):
    """`Maria` must not be treated as digits — and must not match everyone."""
    hit = search(client, "Maria")
    assert names(hit) == {"Maria Alvarez"}


def test_a_partial_number_matches_from_either_end(client):
    """The four digits off a caller ID, and the front of the number people
    remember, are both real things a dispatcher types."""
    assert names(search(client, "0188")) == {"Maria Alvarez"}
    assert names(search(client, "941555")) == {"Maria Alvarez", "Ted Nights"}


def test_a_phone_shaped_query_still_runs_the_text_search(client):
    """The digit rule is added to the ILIKE terms, never swapped in for them.

    `8635550123` is a phone number in every sense a person cares about, but it
    is stored as a carrier email address, so only the text search can reach it.
    Replacing the ILIKE terms with the digit clause would lose this row."""
    assert names(search(client, "8635550123")) == {"Casey Gateway"}


def test_a_short_numeric_query_is_still_a_text_search(client):
    """`24/7` is nothing but digits and punctuation, but it carries three of
    them — below MIN_MATCH_DIGITS — and it is a real business's real name."""
    assert names(search(client, "24/7")) == {"Ted Nights"}


def test_a_search_that_matches_nothing_is_empty_not_an_error(client):
    r = search(client, "5205550000")
    assert r.status_code == 200
    assert r.json()["items"] == [] and r.json()["total"] == 0
    assert r.json()["pages"] == 1


def test_a_contact_with_no_phone_is_never_a_phone_match(client):
    """A NULL phone must not be coalesced into something a fragment matches."""
    for spelling in (*SPELLINGS, "0102"):
        assert "Nolan Nophone" not in names(search(client, spelling))

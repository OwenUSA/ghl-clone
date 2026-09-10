"""The contact picker: create a contact without leaving the form you are in.

Three kinds of test, because three different things can break:

* `frontend/src/lib/phoneMatch.ts` is **executed under node**, the way
  `calendarGrid.ts` is (DECISIONS.md). It decides where a prefill goes and
  whether a number is already on file, and a regex asserting the file mentions
  `looksLikePhone` would pass against a broken one.
* The same cases are then run through `app.phone_match` and the two answers are
  compared. The browser warns "already on file" about contacts the server has to
  be able to find; if the two rules drift, it warns about duplicates that cannot
  be reached, which is worse than not warning.
* The API is driven for real: a contact created the way the picker creates one
  is findable by the phone rule afterwards, a duplicate number is genuinely
  allowed through, and a TECH's create is genuinely refused — which is what the
  disabled `+` in the browser is mirroring.

The component wiring itself (which callback selects what) is asserted against
source, this project's standing idiom for anything React: there is no JS test
runner here.
"""
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Contact, Role, User
from app.phone_match import MIN_MATCH_DIGITS, NATIONAL_DIGITS, looks_like_phone, same_number
from fastapi.testclient import TestClient

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"
PHONE_TS = FRONTEND / "lib" / "phoneMatch.ts"
PICKER_TSX = FRONTEND / "components" / "ContactPicker.tsx"
DIALOG_TSX = FRONTEND / "components" / "AddContactDialog.tsx"
OPPS_TSX = FRONTEND / "pages" / "OpportunitiesPage.tsx"

NODE = shutil.which("node")

node = pytest.mark.skipif(
    NODE is None,
    reason="node is not on PATH, so phoneMatch.ts cannot be executed. CI's backend "
           "job installs it precisely so this file is never skipped there.")


def run_js(body: str):
    """Execute `body` with the real module imported as `m`, and return its JSON."""
    script = ("import * as m from %s\n" % json.dumps(PHONE_TS.as_posix())
              + "const out = (v) => console.log('@@' + JSON.stringify(v))\n"
              + textwrap.dedent(body))
    proc = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-"],
        input=script, capture_output=True, text=True, env=dict(os.environ))
    assert proc.returncode == 0, proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@")]
    assert line, "the script printed no result:\n" + proc.stdout + proc.stderr
    return json.loads(line[-1][2:])


# Everything a person might type into the picker, in one list, so the two
# implementations below are compared on identical input.
TYPED = [
    "8135550102", "(813) 555-0102", "813-555-0102", "+1 813 555 0102",
    "0102", "941555", "81", "Maria", "Maria Alvarez", "24/7", "", "   ",
    "813-CALL", "smith",
]

PAIRS = [
    ("(813) 555-0102", "+18135550102"),
    ("8135550102", "813.555.0102"),
    ("(813) 555-0102", "(941) 555-0188"),
    ("", "+18135550102"),
    ("0102", "+18135550102"),
]


# ---------------- the browser's copy of the rule, executed ----------------

@node
def test_prefill_puts_a_number_in_the_phone_field():
    """A dispatcher who searched `8135550102` and found nobody must not type it
    again — and must not find it sitting in the First name box."""
    assert run_js("out(m.prefillFrom('8135550102'))") == {"phone": "8135550102"}
    assert run_js("out(m.prefillFrom('(813) 555-0102'))") == {"phone": "(813) 555-0102"}


@node
def test_prefill_puts_a_word_in_the_name_field():
    assert run_js("out(m.prefillFrom('Maria'))") == {"first_name": "Maria"}
    assert run_js("out(m.prefillFrom('Maria Alvarez'))") == {
        "first_name": "Maria", "last_name": "Alvarez"}
    # A middle name belongs with the surname rather than being dropped.
    assert run_js("out(m.prefillFrom('Ana Maria Alvarez'))") == {
        "first_name": "Ana", "last_name": "Maria Alvarez"}
    assert run_js("out(m.prefillFrom('   '))") == {}


@node
def test_the_browser_and_the_server_agree_on_what_a_phone_number_is():
    """Same input, same answer, both languages. The picker's duplicate warning
    is only meaningful if the number it warns about is one the server's search
    would actually have found."""
    js = run_js("out(%s.map((q) => m.looksLikePhone(q)))" % json.dumps(TYPED))
    assert js == [looks_like_phone(q) for q in TYPED]


@node
def test_the_browser_and_the_server_agree_on_two_numbers_being_the_same():
    js = run_js("out(%s.map(([a, b]) => m.sameNumber(a, b)))" % json.dumps(PAIRS))
    assert js == [same_number(a, b) for a, b in PAIRS]
    # And it is not vacuous: these cases include both answers.
    assert set(js) == {True, False}


@node
def test_the_two_constants_are_the_same_number_in_both_languages():
    assert run_js("out([m.NATIONAL_DIGITS, m.MIN_MATCH_DIGITS])") == [
        NATIONAL_DIGITS, MIN_MATCH_DIGITS]


# ---------------- what creating from the picker actually does ----------------

@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    users = [User(email="a@x.test", name="Owen", role=Role.ADMIN),
             User(email="c@x.test", name="Tech", role=Role.TECH)]
    db.add_all(users)
    db.add(Contact(first_name="Dana", last_name="Legacy", phone="(813) 555-0102"))
    db.flush()
    tokens = {}
    for key, u in (("admin", users[0]), ("tech", users[1])):
        plain, token = mint_api_token(u, name="test-" + key)
        db.add(token)
        tokens[key] = plain
    db.commit()
    db.close()
    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + tokens["admin"]
        c.tokens = tokens
        yield c


def test_creating_a_contact_writes_it_and_returns_what_the_picker_selects(client):
    """The picker selects `{id, name}` straight off the create response, so the
    response has to carry both — and the row has to be really there."""
    r = client.post("/api/contacts",
                    json={"first_name": "Maria", "last_name": "Alvarez",
                          "phone": "(941) 555-0188"})
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["id"] and created["name"] == "Maria Alvarez"

    # Really written: found by a fresh read, not just echoed back.
    assert client.get("/api/contacts/%d" % created["id"]).json()["name"] == "Maria Alvarez"
    # And selectable the next time somebody searches for that number in any shape.
    found = client.get("/api/contacts", params={"q": "9415550188"}).json()
    assert [i["id"] for i in found["items"]] == [created["id"]]


def test_a_duplicate_number_is_a_warning_not_a_wall(client):
    """A couple share a mobile; a property manager is the number for a dozen
    addresses. The browser warns — the write itself must still go through, or
    the warning would be a refusal wearing a warning's clothes."""
    r = client.post("/api/contacts",
                    json={"first_name": "Marco", "last_name": "Legacy",
                          "phone": "+18135550102"})
    assert r.status_code == 201, r.text
    # Both people are now on that one number, and both are findable by it.
    both = client.get("/api/contacts", params={"q": "813-555-0102"}).json()
    assert {i["name"] for i in both["items"]} == {"Dana Legacy", "Marco Legacy"}


def test_the_duplicate_lookup_finds_the_existing_contact_before_anything_is_written(client):
    """What the dialog asks for as the number is typed: the search it runs must
    return the person already on file, whatever format either side is in."""
    hit = client.get("/api/contacts", params={"q": "+18135550102", "page_size": 5}).json()
    assert [i["name"] for i in hit["items"]] == ["Dana Legacy"]


def test_a_tech_really_cannot_create_a_contact(client):
    """The reason the `+` renders disabled instead of opening a form that 403s.
    Precedent: d1f7c50, b943f4b."""
    before = client.get("/api/contacts", params={"page_size": 50}).json()["total"]
    r = client.post("/api/contacts", json={"first_name": "Nope", "last_name": "Tech"},
                    headers={"Authorization": "Bearer " + client.tokens["tech"]})
    assert r.status_code == 403
    # A 403 that still wrote the row would be worse than no gate at all.
    assert client.get("/api/contacts", params={"page_size": 50}).json()["total"] == before


# ---------------- how the components are wired ----------------

def test_there_is_exactly_one_contact_creation_form():
    """The picker reuses AddContactDialog. A second form would be a second
    answer to "what does a contact need", and they would drift apart."""
    callers = {p.name for p in FRONTEND.rglob("*.tsx")
               if "createContact(" in p.read_text(encoding="utf-8")}
    assert callers == {"AddContactDialog.tsx"}, callers


def test_the_picker_selects_what_it_created_and_says_so():
    src = PICKER_TSX.read_text(encoding="utf-8")
    body = src.split("onCreated={", 1)[1].split("onUseExisting", 1)[0]
    # The created contact is passed to `select`, which is the one place that
    # calls `onChange` — so creating one selects it in the form that opened it.
    assert "select({ id: contact.id, name: contact.name }" in body
    assert "onChange(contact)" in src, "select() must be what tells the form"
    assert "created and selected" in src, "a new customer record is not created silently"


def test_the_picker_prefills_the_form_with_what_was_typed():
    src = PICKER_TSX.read_text(encoding="utf-8")
    assert "initial={prefillFrom(typed)}" in src


def test_the_empty_state_names_what_was_typed_and_offers_the_plus():
    src = PICKER_TSX.read_text(encoding="utf-8")
    assert "No contacts match" in src and "${typed}" in src
    # The `+` is rendered in the empty state too, not only beside the input:
    # that is the moment the dispatcher learns the person is not on file. One
    # declaration, two call sites — the disabled state cannot differ between them.
    assert "const plusButton = " in src
    assert src.count("plusButton(") == 2, "beside the input, and again in the empty state"
    assert "plusButton('Add \u201c' + typed + '\u201d')" in src


def test_the_plus_is_disabled_with_a_reason_for_a_role_that_cannot_create():
    src = PICKER_TSX.read_text(encoding="utf-8")
    assert "user.role !== 'TECH'" in src
    assert "Your role cannot create contacts" in src
    assert "disabled={!canCreate}" in src
    assert "title={createTitle}" in src


def test_the_duplicate_warning_offers_the_existing_contact_and_still_allows_create():
    src = DIALOG_TSX.read_text(encoding="utf-8")
    assert "onUseExisting(c)" in src
    assert "you can still create this contact" in src
    # Every contact on that number is named and offered, not just the first —
    # a property manager's number really is shared, and naming one of four
    # would misdescribe what is on file.
    assert ".filter((c) => sameNumber(c.phone, form.phone))" in src
    assert "duplicates.map((c) => c.name).join(', ')" in src
    # The Create button's only disabled condition is the in-flight POST — a
    # duplicate must never make it unclickable.
    assert "disabled={create.isPending}" in src


def test_the_opportunities_dialog_uses_the_shared_picker():
    src = OPPS_TSX.read_text(encoding="utf-8")
    assert "<ContactPicker" in src
    # ...and no longer runs a contact search of its own.
    assert "listContacts" not in src

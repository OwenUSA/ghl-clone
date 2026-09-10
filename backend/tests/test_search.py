"""The global (ctrl+K) search endpoint, and the staff-only rule on internal notes.

Behaviour, not status codes, as everywhere else here: a search that returns the
row you asked for is only half the assertion — the other half is that it did NOT
return the rows you did not ask for. Every test below checks both sides, because
a filter that quietly matches everything still answers 200 with your record in it.

The role tests compare the DATA three roles get back from the same query on the
same database. A 403 on one endpoint proves nothing about what leaked from
another, and this project has already shipped four role-signalling bugs.
"""
from datetime import UTC, datetime, timedelta

import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Contact,
    Conversation,
    ConversationEvent,
    Direction,
    EventType,
    Opportunity,
    Pipeline,
    Role,
    Stage,
    User,
)
from fastapi.testclient import TestClient


def utcnow():
    return datetime.now(UTC)


@pytest.fixture()
def api():
    """One bearer client per role, over a small hand-built world.

    Tokens rather than passwords: scrypt costs ~700ms a hash on this host
    (DECISIONS.md) and nothing here exercises login.
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    users = {}
    for key, role in (("admin", Role.ADMIN), ("dispatcher", Role.DISPATCHER),
                      ("tech", Role.TECH)):
        u = User(email="%s@x.test" % key, name=key.title(), role=role)
        db.add(u)
        users[key] = u
    db.flush()

    # Three people who each match exactly one of name / email / phone, so a query
    # aimed at one column cannot accidentally pass because of another.
    jane = Contact(first_name="Jane", last_name="Doe",
                   email="jane@roofmail.test", phone="(941) 555-0101")
    marcus = Contact(first_name="Marcus", last_name="Webb",
                     email="skylight.person@example.test", phone="(941) 555-0202")
    priya = Contact(first_name="Priya", last_name="Nair",
                    email="priya@example.test", phone="(813) 777-4242")
    db.add_all([jane, marcus, priya])
    db.flush()

    pipe = Pipeline(name="Dream Team Roofing AHS")
    db.add(pipe)
    db.flush()
    new_lead = Stage(pipeline_id=pipe.id, name="New Lead", position=0)
    inspection = Stage(pipeline_id=pipe.id, name="Inspection", position=1)
    db.add_all([new_lead, inspection])
    db.flush()

    opps = {
        "roof": Opportunity(title="Jane roof replacement", contact_id=jane.id,
                            pipeline_id=pipe.id, stage_id=inspection.id,
                            value_cents=950000),
        "gutter": Opportunity(title="Webb gutter repair", contact_id=marcus.id,
                              pipeline_id=pipe.id, stage_id=new_lead.id,
                              value_cents=120000),
        "won": Opportunity(title="Nair roof tune-up", contact_id=priya.id,
                           pipeline_id=pipe.id, stage_id=inspection.id,
                           status="won", value_cents=40000),
    }
    db.add_all(opps.values())
    db.flush()

    convs = {}
    for key, c in (("jane", jane), ("marcus", marcus), ("priya", priya)):
        conv = Conversation(contact_id=c.id, last_event_at=utcnow())
        db.add(conv)
        convs[key] = conv
    db.flush()

    def ev(conv, type_, body, minutes_ago=1, direction=Direction.INBOUND,
           transcript=None):
        e = ConversationEvent(conversation_id=conv.id, type=type_,
                              direction=direction, body=body,
                              transcript=transcript,
                              occurred_at=utcnow() - timedelta(minutes=minutes_ago))
        db.add(e)
        return e

    ev(convs["jane"], EventType.SMS, "The skylight is leaking again", 10)
    ev(convs["marcus"], EventType.SMS, "Can you quote a new gutter run?", 9)
    ev(convs["priya"], EventType.EMAIL, "Nothing relevant in this one", 8)
    # An internal note: staff-only. Contains a word that appears NOWHERE else, so
    # a role test can look for that word alone.
    ev(convs["jane"], EventType.INTERNAL_COMMENT,
       "Crew note: homeowner is litigious, document everything", 7)
    ev(convs["marcus"], EventType.NOTE, "Crew note: dog on site, call ahead", 6)
    # A CALL whose transcript says "skylight". Transcripts are deliberately OUT of
    # the palette's scope, so this row must never surface in /api/search.
    ev(convs["priya"], EventType.CALL, None, 5,
       transcript="he mentioned the skylight three times")

    tokens = {}
    for key, u in users.items():
        plain, tok = mint_api_token(u, name=key)
        db.add(tok)
        tokens[key] = plain
    db.commit()

    ids = {"jane": jane.id, "marcus": marcus.id, "priya": priya.id,
           "conv_jane": convs["jane"].id, "conv_marcus": convs["marcus"].id,
           "pipeline": pipe.id,
           **{"opp_" + k: o.id for k, o in opps.items()}}
    db.close()

    def client(who="admin"):
        c = TestClient(app)
        c.headers["Authorization"] = "Bearer " + tokens[who]
        return c

    yield {"client": client, "ids": ids, "tokens": tokens}


def groups(payload):
    return {g["type"]: g for g in payload["groups"]}


def search(api, term, who="admin", **params):
    r = api["client"](who).get("/api/search", params={"q": term, **params})
    assert r.status_code == 200, r.text
    return r.json()


def names(group):
    return [i.get("name") or i.get("title") or i["snippet"] for i in group["items"]]


# ---------------- contacts: name, email, phone ----------------

def test_a_contact_is_found_by_name_and_the_others_are_not(api):
    g = groups(search(api, "jane"))["contacts"]
    assert names(g) == ["Jane Doe"]
    assert g["total"] == 1


def test_a_contact_is_found_by_a_full_name_spanning_both_columns(api):
    """"jane doe" is in neither first_name nor last_name on its own."""
    g = groups(search(api, "jane doe"))["contacts"]
    assert [i["id"] for i in g["items"]] == [api["ids"]["jane"]]


def test_a_contact_is_found_by_email_and_the_others_are_not(api):
    """The term appears in NO name and NO phone, so only the email can match."""
    g = groups(search(api, "skylight.person"))["contacts"]
    assert [i["id"] for i in g["items"]] == [api["ids"]["marcus"]]
    assert g["total"] == 1


def test_a_contact_is_found_by_phone_and_the_others_are_not(api):
    g = groups(search(api, "813) 777-4242"))["contacts"]
    assert [i["id"] for i in g["items"]] == [api["ids"]["priya"]]


def test_a_contact_row_carries_enough_to_tell_two_people_apart(api):
    item = groups(search(api, "jane"))["contacts"]["items"][0]
    assert item["name"] == "Jane Doe"
    assert item["email"] == "jane@roofmail.test"
    assert item["phone"] == "(941) 555-0101"


def test_the_contact_filter_actually_narrows(api):
    """Three contacts exist; a query that matches one must not return three."""
    everyone = groups(search(api, "9"))["contacts"]["total"]
    one = groups(search(api, "jane"))["contacts"]["total"]
    assert one == 1 < everyone


# ---------------- opportunities ----------------

def test_an_opportunity_is_found_by_title(api):
    g = groups(search(api, "gutter"))["opportunities"]
    assert names(g) == ["Webb gutter repair"]
    assert g["items"][0]["id"] == api["ids"]["opp_gutter"]


def test_an_opportunity_row_names_its_stage(api):
    """"Inspection" vs "New Lead" is how you tell two jobs for one roof apart."""
    item = groups(search(api, "Jane roof"))["opportunities"]["items"][0]
    assert item["stage_name"] == "Inspection"
    assert item["contact_name"] == "Jane Doe"


def test_a_closed_opportunity_is_still_findable(api):
    """GET /api/opportunities defaults to status=open. A palette is how you go
    back to a deal you already won, so search must not inherit that default."""
    found = groups(search(api, "tune-up"))["opportunities"]
    assert [i["id"] for i in found["items"]] == [api["ids"]["opp_won"]]
    assert found["items"][0]["status"] == "won"


def test_searching_an_opportunity_does_not_return_every_opportunity(api):
    assert groups(search(api, "gutter"))["opportunities"]["total"] == 1
    assert groups(search(api, "roof"))["opportunities"]["total"] == 2


# ---------------- messages ----------------

def test_a_message_is_found_by_a_word_in_its_body(api):
    g = groups(search(api, "leaking"))["messages"]
    assert g["total"] == 1
    assert "leaking" in g["items"][0]["snippet"]


def test_a_message_result_identifies_the_thread_it_was_said_in(api):
    """Finding the sentence is half the job; the other half is being taken to the
    conversation it was said in."""
    item = groups(search(api, "leaking"))["messages"]["items"][0]
    assert item["conversation_id"] == api["ids"]["conv_jane"]
    assert item["contact_id"] == api["ids"]["jane"]
    assert item["contact_name"] == "Jane Doe"


def test_call_transcripts_are_not_searched(api):
    """Deliberately out of scope (DECISIONS.md 2026-09-10). "skylight" is in one
    SMS body and in one CALL transcript; only the SMS may come back."""
    g = groups(search(api, "skylight"))["messages"]
    assert g["total"] == 1
    assert g["items"][0]["type"] == "SMS"
    assert g["items"][0]["conversation_id"] == api["ids"]["conv_jane"]

    # ...and the transcript really is there to be found, on the endpoint that owns
    # transcripts. Otherwise this test would pass against an empty database.
    calls = api["client"]().get("/api/calls", params={"q": "skylight"}).json()
    assert calls["total"] == 1


def test_a_message_snippet_shows_the_matched_word_not_just_the_opening(api):
    long_tail = "x" * 400 + " weathervane " + "y" * 400
    c = api["client"]()
    conv = api["ids"]["conv_marcus"]
    assert c.post("/api/conversations/%d/messages" % conv,
                  json={"body": long_tail, "type": "SMS"}).status_code == 201

    snippet = groups(search(api, "weathervane"))["messages"]["items"][0]["snippet"]
    assert "weathervane" in snippet
    assert len(snippet) < 200


# ---------------- grouping, caps, and the empty case ----------------

def test_every_group_is_present_even_when_nothing_matches(api):
    payload = search(api, "zzzz-nothing-matches-this")
    assert [g["type"] for g in payload["groups"]] == [
        "contacts", "opportunities", "messages"]
    assert all(g["items"] == [] and g["total"] == 0 and not g["truncated"]
               for g in payload["groups"])
    assert payload["total"] == 0


def test_a_blank_query_is_an_empty_result_not_an_error(api):
    payload = search(api, "   ")
    assert payload["q"] == ""
    assert all(g["items"] == [] for g in payload["groups"])


def test_each_group_carries_a_heading(api):
    assert {g["type"]: g["label"] for g in search(api, "roof")["groups"]} == {
        "contacts": "Contacts", "opportunities": "Opportunities",
        "messages": "Messages"}


def test_the_cap_is_reported_rather_than_silently_truncating(api):
    """Nine matching contacts, a cap of two: the response must say nine."""
    c = api["client"]()
    for i in range(9):
        assert c.post("/api/contacts", json={
            "first_name": "Capped%d" % i, "last_name": "Person",
            "phone": "(941) 555-9%03d" % i}).status_code == 201

    g = groups(search(api, "Capped", limit=2))["contacts"]
    assert len(g["items"]) == 2
    assert g["total"] == 9
    assert g["truncated"] is True


def test_a_group_that_fits_is_not_reported_as_truncated(api):
    g = groups(search(api, "gutter"))["opportunities"]
    assert len(g["items"]) == g["total"] == 1
    assert g["truncated"] is False


def test_a_wildcard_character_matches_itself_and_not_everything(api):
    """`%` and `_` are LIKE wildcards. Typing one must narrow to nothing, not
    match every row.

    The Contacts list is asserted alongside the palette on purpose: the two used
    to disagree, because only one of them escaped the term. One `contains()`
    helper now serves both, and this is what says so.
    """
    c = api["client"]()
    for term in ("%", "_"):
        assert groups(search(api, term))["contacts"]["total"] == 0, term
        assert c.get("/api/contacts", params={"q": term}).json()["total"] == 0, term

    # ...and the escaping did not break ordinary matching on the way past.
    assert c.get("/api/contacts", params={"q": "jane"}).json()["total"] == 1


# ---------------- role: internal notes are staff-only ----------------
#
# "Crew note:" appears only on the INTERNAL_COMMENT and the NOTE. Nothing else in
# the fixture contains it, so the word alone separates the roles.

def test_a_dispatcher_and_an_admin_see_the_internal_notes(api):
    for who in ("admin", "dispatcher"):
        g = groups(search(api, "Crew note", who=who))["messages"]
        assert g["total"] == 2, who
        assert {i["type"] for i in g["items"]} == {"INTERNAL_COMMENT", "NOTE"}


def test_a_tech_gets_no_internal_notes_from_the_same_query(api):
    g = groups(search(api, "Crew note", who="tech"))["messages"]
    assert g["items"] == []
    assert g["total"] == 0, "the count must not leak the rows either"


def test_a_tech_still_sees_the_customer_conversation(api):
    """The rule hides the crew's commentary, not the customer's messages —
    otherwise it is not a permission, it is a broken feature."""
    g = groups(search(api, "leaking", who="tech"))["messages"]
    assert [i["id"] for i in g["items"]] == [
        i["id"] for i in groups(search(api, "leaking"))["messages"]["items"]]


def test_the_three_roles_differ_only_by_the_internal_notes(api):
    """The whole comparison in one place: same query, three roles, and the
    difference between the sets is exactly the internal rows."""
    def ids(who):
        return {i["id"] for i in
                groups(search(api, "note", who=who))["messages"]["items"]}

    admin, dispatcher, tech = ids("admin"), ids("dispatcher"), ids("tech")
    assert admin == dispatcher
    assert tech < admin, "a TECH must see strictly fewer rows here"
    hidden = admin - tech
    assert len(hidden) == 2

    # And the hidden rows really are the internal ones, read back as ADMIN.
    types = {i["type"] for i in
             groups(search(api, "note"))["messages"]["items"] if i["id"] in hidden}
    assert types == {"INTERNAL_COMMENT", "NOTE"}


def test_a_tech_sees_the_same_contacts_and_opportunities_as_an_admin(api):
    """Nothing else was narrowed. Contacts and opportunities are ANY_USER on the
    list endpoints, so hiding them in search would be a new restriction nobody
    asked for — and the DECISIONS triage of 2026-09-09 says per-deal money is
    TECH-visible on purpose."""
    for term in ("roof", "jane"):
        a, t = search(api, term), search(api, term, who="tech")
        for key in ("contacts", "opportunities"):
            assert groups(a)[key] == groups(t)[key], (term, key)
    assert groups(search(api, "roof"))["opportunities"]["items"][0]["value_cents"]


def test_search_requires_a_credential(api):
    """test_auth.py enumerates every route and asserts this too; stated here as
    well so the endpoint's own file records it."""
    assert TestClient(app).get("/api/search?q=jane").status_code == 401

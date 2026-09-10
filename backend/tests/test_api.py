"""End-to-end functional tests, one section per module.

These drive the real FastAPI app against a throwaway SQLite database and assert
behaviour, not just HTTP 200: filters must actually filter, sorting must change
order, pagination must not repeat rows, a drag must move the card and update the
stage counts, and a stubbed send must never be marked as delivered.
"""
from datetime import UTC, datetime, timedelta

import pytest
from app.auth import mint_api_token
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Appointment,
    Calendar,
    Contact,
    Conversation,
    ConversationEvent,
    Direction,
    EventType,
    Job,
    Opportunity,
    Pipeline,
    Role,
    Stage,
    User,
)
from fastapi.testclient import TestClient
from sqlalchemy import select


def utcnow():
    return datetime.now(UTC)


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    users = [User(email="a@x.test", name="Owen", role=Role.ADMIN),
             User(email="b@x.test", name="Dispatch", role=Role.DISPATCHER),
             User(email="c@x.test", name="Tech", role=Role.TECH)]
    db.add_all(users)
    db.flush()

    contacts = []
    for i in range(25):
        c = Contact(first_name="First%02d" % i, last_name="Last%02d" % i,
                    phone="(941) 555-%04d" % i, email="c%02d@x.test" % i,
                    source="Referral" if i % 2 else "Google LSA",
                    business_name="Biz%02d" % i if i % 3 == 0 else None)
        contacts.append(c)
    db.add_all(contacts)
    db.flush()

    pipe = Pipeline(name="AHS")
    db.add(pipe)
    db.flush()
    s1 = Stage(pipeline_id=pipe.id, name="New Lead", position=0)
    s2 = Stage(pipeline_id=pipe.id, name="Inspection", position=1)
    db.add_all([s1, s2])
    db.flush()
    for i in range(6):
        # `position` is explicit: the column default is 0, so leaving it off gives
        # six cards that all claim the top of the stage and an order the ordering
        # tests could not assert. The seed and POST /api/opportunities both fill it.
        db.add(Opportunity(title="OPP %d" % i, contact_id=contacts[i].id,
                           pipeline_id=pipe.id, stage_id=s1.id, position=i,
                           value_cents=10000 * (i + 1),
                           status="won" if i == 0 else "open"))

    cal = Calendar(name="Owen's Personal Calendar", user_id=users[0].id)
    db.add(cal)
    db.flush()

    conv = Conversation(contact_id=contacts[0].id, unread_count=2)
    db.add(conv)
    db.flush()
    db.add_all([
        ConversationEvent(conversation_id=conv.id, type=EventType.CALL,
                          direction=Direction.INBOUND, occurred_at=utcnow(),
                          duration_seconds=30, call_status="completed"),
        ConversationEvent(conversation_id=conv.id, type=EventType.SMS,
                          direction=Direction.INBOUND, occurred_at=utcnow(),
                          body="hello"),
        ConversationEvent(conversation_id=conv.id, type=EventType.APPOINTMENT,
                          direction=Direction.OUTBOUND, occurred_at=utcnow(),
                          body="Appointment scheduled"),
    ])
    db.commit()

    ids = {"contact": contacts[0].id, "pipeline": pipe.id,
           "stage1": s1.id, "stage2": s2.id, "conv": conv.id,
           "calendar": cal.id, "user": users[0].id}

    # Every route now requires a credential. Mint a PAT directly rather than going
    # through /api/auth/login, so these 23 behaviour tests stay independent of the
    # login route — a bug there should fail test_auth.py, not all of them at once.
    tokens = {}
    for key, user in (("admin", users[0]), ("dispatcher", users[1]),
                      ("tech", users[2])):
        plain, token = mint_api_token(user, name="test-%s" % key)
        db.add(token)
        tokens[key] = plain
    db.commit()
    db.close()

    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + tokens["admin"]
        c.ids = ids
        c.tokens = tokens
        yield c


# ---------------- Contacts ----------------

def test_contacts_pagination_does_not_repeat_rows(client):
    p1 = client.get("/api/contacts?page=1&page_size=10").json()
    p2 = client.get("/api/contacts?page=2&page_size=10").json()
    assert p1["total"] == 25 and p1["pages"] == 3
    assert len(p1["items"]) == 10 and len(p2["items"]) == 10
    assert not ({i["id"] for i in p1["items"]} & {i["id"] for i in p2["items"]})


def test_contacts_search_matches_a_full_name(client):
    """Matching first_name and last_name separately never matches "First Last",
    which is what anyone types — and what the CLI resolves names with."""
    r = client.get("/api/contacts", params={"q": "First03 Last03"})
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["name"] == "First03 Last03"


def test_contacts_search_filters(client):
    all_n = client.get("/api/contacts?page=1&page_size=100").json()["total"]
    hit = client.get("/api/contacts?q=First07").json()
    assert hit["total"] == 1 < all_n
    assert hit["items"][0]["first_name"] == "First07"


def test_contacts_sort_reverses(client):
    asc = client.get("/api/contacts?sort=name&order=asc&page_size=25").json()["items"]
    desc = client.get("/api/contacts?sort=name&order=desc&page_size=25").json()["items"]
    assert asc[0]["id"] != desc[0]["id"]
    assert [i["id"] for i in asc] == [i["id"] for i in reversed(desc)]


def test_contact_edit_persists_and_leaves_other_fields(client):
    cid = client.ids["contact"]
    before = client.get(f"/api/contacts/{cid}").json()
    r = client.patch(f"/api/contacts/{cid}", json={"business_name": "Verified LLC"})
    assert r.status_code == 200
    after = client.get(f"/api/contacts/{cid}").json()
    assert after["business_name"] == "Verified LLC"
    assert after["email"] == before["email"], "unrelated field was clobbered"


def test_contact_tags_add_and_remove(client):
    cid = client.ids["contact"]
    added = client.post(f"/api/contacts/{cid}/tags", json={"name": "Warranty"}).json()
    assert [t["name"] for t in added["tags"]] == ["Warranty"]
    tag_id = added["tags"][0]["id"]
    removed = client.delete(f"/api/contacts/{cid}/tags/{tag_id}").json()
    assert removed["tags"] == []


def test_contact_create_requires_a_way_to_reach_them(client):
    assert client.post("/api/contacts", json={"first_name": "Ghost"}).status_code == 400
    ok = client.post("/api/contacts", json={"first_name": "Nora", "phone": "(941) 555-9999"})
    assert ok.status_code == 201
    assert ok.json()["automation"] == "queued", "new lead rule did not fire"


def test_contact_create_refuses_a_value_wider_than_its_column(client):
    """An over-long field must be refused, not handed to the database.

    On Postgres an unbounded value reaches `character varying(120)` and comes back
    as StringDataRightTruncation, which FastAPI serves as a bare 500 with nothing
    naming the field. SQLite does not enforce the width at all and would happily
    store it — which is exactly why the limit has to live in the request schema and
    not be left to whichever database happens to be underneath.
    """
    before = client.get("/api/contacts?page_size=100").json()["total"]
    r = client.post("/api/contacts",
                    json={"first_name": "L" * 121, "email": "long@x.test"})
    assert r.status_code == 422, "an over-long first_name must be refused"
    assert "first_name" in r.text, "the refusal must name the offending field"
    after = client.get("/api/contacts?page_size=100").json()["total"]
    assert after == before, "a refused create still wrote a row"
    assert client.get("/api/contacts", params={"q": "long@x.test"}).json()["items"] == []

    # The boundary itself still works — the guard must not be off by one.
    at_limit = client.post("/api/contacts",
                           json={"first_name": "L" * 120, "email": "edge@x.test"})
    assert at_limit.status_code == 201
    assert len(at_limit.json()["first_name"]) == 120


def test_whitespace_is_not_a_way_to_reach_a_contact(client):
    """"   " satisfied the "needs a phone or an email" guard.

    That stored a contact with no reachable channel at all, rendering as a blank
    row in the list -- the guard was there precisely to make that impossible.
    """
    before = client.get("/api/contacts?page_size=100").json()["total"]
    r = client.post("/api/contacts",
                    json={"first_name": "  ", "email": "   ", "phone": "   "})
    assert r.status_code == 400, "whitespace still counts as a contact detail"
    after = client.get("/api/contacts?page_size=100").json()["total"]
    assert after == before, "a refused create still wrote an unreachable contact"

    # A real detail with incidental whitespace is fine, and is stored trimmed.
    ok = client.post("/api/contacts", json={"first_name": " Nora ", "phone": " 9415550000 "})
    assert ok.status_code == 201
    assert ok.json()["first_name"] == "Nora" and ok.json()["phone"] == "9415550000"


def test_an_email_field_has_to_hold_an_email(client):
    before = client.get("/api/contacts?page_size=100").json()["total"]
    r = client.post("/api/contacts", json={"email": "not-an-email"})
    assert r.status_code == 422
    assert client.get("/api/contacts?page_size=100").json()["total"] == before

    cid = client.ids["contact"]
    was = client.get("/api/contacts/%d" % cid).json()["email"]
    assert client.patch("/api/contacts/%d" % cid,
                        json={"email": "still-not-an-email"}).status_code == 422
    still = client.get("/api/contacts/%d" % cid).json()["email"]
    assert still == was, "a refused patch still wrote a junk email"

    assert client.post("/api/contacts",
                       json={"email": "nora@example.test"}).status_code == 201


def test_contact_patch_refuses_a_value_wider_than_its_column(client):
    """The panel saves one field at a time, and hit the same 500 on save."""
    cid = client.ids["contact"]
    before = client.get(f"/api/contacts/{cid}").json()
    r = client.patch(f"/api/contacts/{cid}", json={"last_name": "L" * 121})
    assert r.status_code == 422
    after = client.get(f"/api/contacts/{cid}").json()
    assert after["last_name"] == before["last_name"], "a refused patch still wrote"


def test_contact_delete_keeps_the_opportunities_and_drops_the_conversations(client):
    """The whole shape of this endpoint in one test.

    `custom_fields.owen_call_id` is the telephony project's join key (DECISIONS.md),
    so an opportunity must outlive the contact it hung off — detached, not deleted.
    A conversation has nothing anyone else joins to, `conversations.contact_id` is
    NOT NULL, and nothing cascades it, so it has to go explicitly.
    """
    cid = client.ids["contact"]
    detail = client.get(f"/api/contacts/{cid}").json()
    opp_ids = [o["id"] for o in detail["opportunities"]]
    assert opp_ids, "fixture changed — this contact is supposed to have an opportunity"
    convs_before = client.get("/api/conversations?tab=all").json()
    mine = [c["id"] for c in convs_before if c["contact_id"] == cid]
    assert mine, "fixture changed — this contact is supposed to have a conversation"

    r = client.delete(f"/api/contacts/{cid}?force=true")
    assert r.status_code == 200
    assert r.json() == {"deleted": cid, "detached_opportunities": opp_ids}

    # The contact is gone, and gone from the list the UI renders.
    assert client.get(f"/api/contacts/{cid}").status_code == 404
    listed = client.get("/api/contacts?page=1&page_size=100").json()
    assert cid not in {i["id"] for i in listed["items"]}
    assert listed["total"] == 24

    # Its conversations went with it...
    convs_after = client.get("/api/conversations?tab=all").json()
    assert not [c for c in convs_after if c["id"] in mine]

    # ...and its opportunities did NOT.
    for oid in opp_ids:
        opp = client.get(f"/api/opportunities/{oid}")
        assert opp.status_code == 200, "an opportunity was deleted with the contact"
        assert opp.json()["contact_id"] is None, "it is still attached to a dead row"
        assert opp.json()["contact_name"] is None


def test_contact_delete_is_refused_while_it_has_opportunities_and_mutates_nothing(client):
    """A 409 that had already detached half of them would pass a status assertion."""
    cid = client.ids["contact"]
    before = client.get(f"/api/contacts/{cid}").json()
    opp_ids = [o["id"] for o in before["opportunities"]]
    convs = [c["id"] for c in client.get("/api/conversations?tab=all").json()
             if c["contact_id"] == cid]
    assert opp_ids and convs, "fixture changed — retarget this test"

    r = client.delete(f"/api/contacts/{cid}")
    assert r.status_code == 409
    # The message has to name them: the panel turns this refusal into the
    # "these will be detached" confirmation.
    for oid in opp_ids:
        assert str(oid) in r.json()["detail"]

    assert client.get(f"/api/contacts/{cid}").json() == before
    assert [c["id"] for c in client.get("/api/conversations?tab=all").json()
            if c["contact_id"] == cid] == convs
    for oid in opp_ids:
        assert client.get(f"/api/opportunities/{oid}").json()["contact_id"] == cid


def test_contact_delete_needs_no_force_when_there_is_nothing_to_detach(client):
    """force=true is consent to detach, not a general "really delete" flag — a
    contact with no opportunities must not need it."""
    fresh = client.post("/api/contacts", json={
        "first_name": "Nobody", "last_name": "Attached",
        "phone": "(941) 555-7777"}).json()
    assert client.get(f"/api/contacts/{fresh['id']}").json()["opportunities"] == []

    assert client.delete(f"/api/contacts/{fresh['id']}").status_code == 200
    assert client.get(f"/api/contacts/{fresh['id']}").status_code == 404


# ---------------- Conversations ----------------

def test_conversation_tabs_filter(client):
    all_c = client.get("/api/conversations?tab=all").json()
    unread = client.get("/api/conversations?tab=unread").json()
    starred = client.get("/api/conversations?tab=starred").json()
    assert len(all_c) == 1
    assert all(c["unread_count"] > 0 for c in unread)
    assert starred == []


def test_thread_filters_split_conversations_and_activities(client):
    cid = client.ids["conv"]
    all_ev = client.get(f"/api/conversations/{cid}/events?filter=all").json()
    convo = client.get(f"/api/conversations/{cid}/events?filter=conversations").json()
    acts = client.get(f"/api/conversations/{cid}/events?filter=activities").json()
    calls = client.get(f"/api/conversations/{cid}/events?filter=call").json()
    assert len(all_ev) == 3
    assert {e["type"] for e in convo} == {"CALL", "SMS"}
    assert {e["type"] for e in acts} == {"APPOINTMENT"}
    assert len(calls) == 1 and calls[0]["type"] == "CALL"
    assert calls[0]["duration_seconds"] == 30


def test_unknown_thread_filter_is_rejected(client):
    cid = client.ids["conv"]
    assert client.get(f"/api/conversations/{cid}/events?filter=nope").status_code == 400


def test_inbound_event_ingest_increments_unread(client):
    cid = client.ids["contact"]
    before = client.get("/api/conversations?tab=all").json()[0]["unread_count"]
    r = client.post("/api/events", json={"contact_id": cid, "type": "SMS",
                                        "direction": "INBOUND", "body": "hi"})
    assert r.status_code == 201
    after = client.get("/api/conversations?tab=all").json()[0]["unread_count"]
    assert after == before + 1


# ---------------- Opportunities ----------------

def test_drag_moves_card_and_updates_stage_counts(client):
    pid, s1, s2 = client.ids["pipeline"], client.ids["stage1"], client.ids["stage2"]
    opp = client.get(f"/api/opportunities?pipeline_id={pid}&status=all").json()[0]

    def counts():
        stages = client.get("/api/pipelines").json()[0]["stages"]
        return {s["id"]: s["count"] for s in stages}

    before = counts()
    r = client.patch(f"/api/opportunities/{opp['id']}",
                     json={"stage_id": s2, "position": 0})
    assert r.status_code == 200
    after = counts()
    assert after[s1] == before[s1] - 1
    assert after[s2] == before[s2] + 1


def test_cross_pipeline_move_is_rejected(client):
    pid = client.ids["pipeline"]
    opp = client.get(f"/api/opportunities?pipeline_id={pid}&status=all").json()[0]
    r = client.patch(f"/api/opportunities/{opp['id']}",
                     json={"stage_id": 9999, "position": 0})
    assert r.status_code == 400


# ---------------- Opportunities: board order ----------------
#
# `position` is what the kanban drag persists. The tests below assert the ORDER a
# re-read of the board returns, and that both stages come back packed 0..n-1 with
# no gap and no duplicate — a hole is invisible on screen but makes the browser's
# optimistic prediction of the drop disagree with the server.


def _stage_order(client, stage_id):
    """Card ids in the order the board would draw them, for one stage."""
    pid = client.ids["pipeline"]
    rows = client.get(f"/api/opportunities?pipeline_id={pid}&status=all").json()
    return [o["id"] for o in rows if o["stage_id"] == stage_id]


def _stage_positions(client, stage_id):
    pid = client.ids["pipeline"]
    rows = client.get(f"/api/opportunities?pipeline_id={pid}&status=all").json()
    return [o["position"] for o in rows if o["stage_id"] == stage_id]


def _assert_packed(client, stage_id):
    got = _stage_positions(client, stage_id)
    assert got == list(range(len(got))), (
        "stage %s came back as %s, not 0..n-1 — a gap or a duplicate"
        % (stage_id, got))


def _move(client, opp_id, stage_id, position):
    return client.patch(f"/api/opportunities/{opp_id}",
                        json={"stage_id": stage_id, "position": position})


def test_dragging_a_card_down_its_own_stage_persists_the_new_order(client):
    """The same-column reorder the board could not do at all: onDragEnd returned
    early whenever the drop landed in the stage the card came from."""
    s1 = client.ids["stage1"]
    before = _stage_order(client, s1)
    assert len(before) >= 5, "fixture changed — this needs a column to reorder in"

    # Take the top card and drop it two below, between the 3rd and the 4th.
    moved = before[0]
    assert _move(client, moved, s1, 2).status_code == 200

    expected = [before[1], before[2], moved, *before[3:]]
    assert _stage_order(client, s1) == expected, "the new order did not survive a re-read"
    _assert_packed(client, s1)


def test_dragging_a_card_up_its_own_stage_persists_the_new_order(client):
    s1 = client.ids["stage1"]
    before = _stage_order(client, s1)
    moved = before[4]
    assert _move(client, moved, s1, 1).status_code == 200

    expected = [before[0], moved, before[1], before[2], before[3], *before[5:]]
    assert _stage_order(client, s1) == expected
    _assert_packed(client, s1)


def test_a_card_dropped_in_the_middle_of_another_stage_lands_at_that_index(client):
    """Every cross-column drop used to land at the top: the drag handler sent no
    position at all and `moveOpportunity`'s default is 0."""
    s1, s2 = client.ids["stage1"], client.ids["stage2"]
    source = _stage_order(client, s1)

    # Fill the destination first, so "the middle" is a real place.
    for card in source[:3]:
        assert _move(client, card, s2, 99).status_code == 200  # 99 clamps to the end
    dest = _stage_order(client, s2)
    assert dest == source[:3], "the three cards did not arrive in the order they were sent"

    left_behind = _stage_order(client, s1)
    moved = left_behind[1]
    assert _move(client, moved, s2, 1).status_code == 200

    assert _stage_order(client, s2) == [dest[0], moved, dest[1], dest[2]]
    assert _stage_order(client, s1) == [left_behind[0], *left_behind[2:]]
    # Both ends of the drag, not just the one the card landed in.
    _assert_packed(client, s2)
    _assert_packed(client, s1)


def _stage_change_jobs():
    db = SessionLocal()
    try:
        return [j.dedupe_key for j in db.scalars(select(Job)).all()
                if j.type == "stage_change_notify"]
    finally:
        db.close()


def test_a_reorder_inside_one_stage_never_texts_the_customer(client):
    """Rule 4 texts the customer on a stage change. Reordering is not a stage
    change, and when a real SMS transport replaces LoggingTransport a drag that
    texted every customer in the column would be an incident.

    Asserted on the job queue, because the reorder returns 200 either way."""
    s1 = client.ids["stage1"]
    order = _stage_order(client, s1)
    assert not _stage_change_jobs(), "the fixture is not starting from a clean queue"

    for card, pos in ((order[0], 3), (order[4], 0), (order[2], 5)):
        r = _move(client, card, s1, pos)
        assert r.status_code == 200
        assert r.json()["automation"] == "stage unchanged"

    assert _stage_change_jobs() == [], "a same-stage reorder queued a customer text"


def test_a_move_to_another_stage_still_texts_the_customer(client):
    """The other half: guarding the reorder must not have muted the real thing."""
    s1, s2 = client.ids["stage1"], client.ids["stage2"]
    moved = _stage_order(client, s1)[1]
    r = _move(client, moved, s2, 0)
    assert r.status_code == 200 and r.json()["automation"] == "queued"
    assert _stage_change_jobs() == ["stage_change:%d:%d" % (moved, s2)]


@pytest.mark.parametrize("why,body", [
    ("a stage in nobody's pipeline", {"stage_id": 999999, "position": 1}),
    # -1 is not "the end" — it is Python's negative slicing, and it would file the
    # card second from the bottom of the column.
    ("a negative position", {"position": -1}),
])
def test_a_rejected_move_leaves_the_board_exactly_as_it_found_it(client, why, body):
    s1 = client.ids["stage1"]
    before = _stage_order(client, s1)
    before_positions = _stage_positions(client, s1)
    moved = before[2]

    r = client.patch(f"/api/opportunities/{moved}",
                     json={"stage_id": s1, **body})
    assert r.status_code in (400, 422), why

    assert _stage_order(client, s1) == before, "a refused move still reordered the column"
    assert _stage_positions(client, s1) == before_positions
    assert not _stage_change_jobs(), "a refused move queued a customer text"


def test_a_tech_may_reorder_and_move_cards(client):
    """Deliberate, per CLAUDE.md: a TECH cannot edit a record but can move an
    opportunity between stages. Asserted as found — a reorder is the same route,
    so this pins that the drag stays usable for the field crew rather than
    widening anything."""
    s1, s2 = client.ids["stage1"], client.ids["stage2"]
    tech = TestClient(app)
    tech.headers["Authorization"] = "Bearer " + client.tokens["tech"]
    tech.ids = client.ids

    order = _stage_order(client, s1)
    moved = order[0]
    assert _move(tech, moved, s1, 2).status_code == 200
    assert _stage_order(client, s1) == [order[1], order[2], moved, *order[3:]]

    assert _move(tech, moved, s2, 0).status_code == 200
    assert _stage_order(client, s2) == [moved]
    # ...and editing the same record is still refused, which is the line being held.
    assert tech.patch(f"/api/opportunities/{moved}/detail",
                      json={"title": "TECH EDIT"}).status_code == 403


def test_opportunity_detail_validation(client):
    pid = client.ids["pipeline"]
    opp = client.get(f"/api/opportunities?pipeline_id={pid}&status=all").json()[0]
    oid = opp["id"]
    assert client.patch(f"/api/opportunities/{oid}/detail",
                        json={"title": "  "}).status_code == 400
    assert client.patch(f"/api/opportunities/{oid}/detail",
                        json={"value_cents": -1}).status_code == 400
    assert client.patch(f"/api/opportunities/{oid}/detail",
                        json={"status": "banana"}).status_code == 422
    ok = client.patch(f"/api/opportunities/{oid}/detail",
                      json={"value_cents": 123456, "status": "won"})
    assert ok.status_code == 200 and ok.json()["value_cents"] == 123456


def _all_opps(client):
    """Every opportunity in the fixture pipeline, whatever its status."""
    pid = client.ids["pipeline"]
    r = client.get(f"/api/opportunities?pipeline_id={pid}&status=all")
    assert r.status_code == 200
    return r.json()


def _create(client, **over):
    """POST a valid opportunity with `over` applied on top."""
    body = {"title": "Jane roof", "pipeline_id": client.ids["pipeline"],
            "stage_id": client.ids["stage2"], "contact_id": client.ids["contact"],
            "value_cents": 950000}
    body.update(over)
    return client.post("/api/opportunities", json=body)


def test_create_puts_the_card_in_the_chosen_stage(client):
    """The board has to show the new card where it was filed, with its money intact.

    Asserted through what comes back on the board and in the stage header, not on
    the 201: a create that lands in the wrong stage still answers 201.
    """
    def stage_counts():
        return {s["id"]: (s["count"], s["value_cents"])
                for s in client.get("/api/pipelines").json()[0]["stages"]}

    before, s2 = stage_counts(), client.ids["stage2"]
    r = _create(client, title="  Jane roof  ", value_cents=950001)
    assert r.status_code == 201
    new_id = r.json()["id"]

    card = next(o for o in _all_opps(client) if o["id"] == new_id)
    assert card["stage_id"] == s2, "filed in the wrong stage"
    assert card["value_cents"] == 950001
    assert card["title"] == "Jane roof", "the name was stored unstripped"
    assert card["status"] == "open"

    after = stage_counts()
    assert after[s2][0] == before[s2][0] + 1
    assert after[s2][1] == before[s2][1] + 950001, "stage total did not pick it up"

    detail = client.get(f"/api/opportunities/{new_id}").json()
    assert detail["contact_id"] == client.ids["contact"]
    assert detail["pipeline_id"] == client.ids["pipeline"]


@pytest.mark.parametrize("bad", [
    pytest.param({"title": "   "}, id="whitespace-only name"),
    pytest.param({"title": ""}, id="empty name"),
    pytest.param({"title": "x" * 121}, id="over-long name"),
    pytest.param({"value_cents": -1}, id="negative value"),
    pytest.param({"contact_id": 999999}, id="unknown contact"),
])
def test_a_refused_create_writes_no_row(client, bad):
    """Each of these used to be stored. The point is the row count, not the status:
    an orphan opportunity or a nameless card is damage a 400 does not undo."""
    before = _all_opps(client)
    r = _create(client, **bad)
    assert r.status_code in (400, 422), r.text
    assert _all_opps(client) == before, "a refused create still wrote a row"


def test_a_stage_from_another_pipeline_creates_nothing(client):
    """Pipelines are separate boards; a card cannot start life on neither."""
    db = SessionLocal()
    other = Pipeline(name="Retail")
    db.add(other)
    db.flush()
    foreign = Stage(pipeline_id=other.id, name="New Lead", position=0)
    db.add(foreign)
    db.commit()
    foreign_id, other_id = foreign.id, other.id
    db.close()

    before = _all_opps(client)
    # A real stage, just not one of this pipeline's, and a stage id that is nobody's.
    for stage_id in (foreign_id, 999999):
        assert _create(client, stage_id=stage_id).status_code == 400
    assert _all_opps(client) == before
    assert not client.get(
        f"/api/opportunities?pipeline_id={other_id}&status=all").json()


def test_a_tech_cannot_create_an_opportunity(client):
    """STAFF-only, and the refusal has to be a refusal — no row, no card."""
    before = _all_opps(client)
    tech = TestClient(app)
    tech.headers["Authorization"] = "Bearer " + client.tokens["tech"]
    tech.ids = client.ids
    r = _create(tech)
    assert r.status_code == 403
    assert _all_opps(client) == before, "a forbidden create still wrote a row"


def test_money_round_trips_without_cent_drift(client):
    """Cents are integers end to end: what is posted is what the board adds up.

    9500.005 dollars is the case that breaks a float conversion — 9500.005 * 100 is
    950000.49999999994 — so the awkward amounts are pinned here at the cents the UI
    is expected to send, and checked again in the stage total, which is a sum.
    """
    amounts = [950001, 0, 1, 99, 100, 12345678, 950000]
    ids = []
    for cents in amounts:
        r = _create(client, title="MONEY %d" % cents, value_cents=cents)
        assert r.status_code == 201
        ids.append((r.json()["id"], cents))

    by_id = {o["id"]: o for o in _all_opps(client)}
    for oid, cents in ids:
        assert by_id[oid]["value_cents"] == cents
        assert client.get(f"/api/opportunities/{oid}").json()["value_cents"] == cents

    s2 = client.ids["stage2"]
    header = next(s for s in client.get("/api/pipelines").json()[0]["stages"]
                  if s["id"] == s2)
    assert header["value_cents"] == sum(amounts), "the stage total lost a cent"


def test_status_filter_actually_filters(client):
    pid = client.ids["pipeline"]
    open_only = client.get(f"/api/opportunities?pipeline_id={pid}&status=open").json()
    every = client.get(f"/api/opportunities?pipeline_id={pid}&status=all").json()
    assert len(open_only) < len(every)
    assert all(o["status"] == "open" for o in open_only)


# ---------------- Calendars ----------------

def test_appointment_create_and_range_filter(client):
    cid = client.ids["contact"]
    start = utcnow() + timedelta(days=3)
    r = client.post("/api/appointments", json={
        "title": "Roof Inspection", "contact_id": cid,
        "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=1)).isoformat(),
    })
    assert r.status_code == 201
    assert r.json()["automation"] == "queued 24h,1h"

    inside = client.get("/api/appointments", params={
        "start": (start - timedelta(days=1)).isoformat(),
        "end": (start + timedelta(days=1)).isoformat()}).json()
    outside = client.get("/api/appointments", params={
        "start": (start + timedelta(days=10)).isoformat(),
        "end": (start + timedelta(days=20)).isoformat()}).json()
    assert len(inside) == 1 and outside == []


def test_appointment_rejects_backwards_range(client):
    start = utcnow()
    r = client.post("/api/appointments", json={
        "title": "Bad", "starts_at": start.isoformat(),
        "ends_at": (start - timedelta(hours=1)).isoformat()})
    assert r.status_code == 400


def _appointment_rows():
    """Read the table directly. A refusal that still wrote a row would pass any
    assertion made against the response alone."""
    db = SessionLocal()
    try:
        return db.scalars(select(Appointment).order_by(Appointment.id)).all()
    finally:
        db.close()


def test_creating_an_appointment_writes_the_row_the_form_described(client):
    """The create dialog's whole job: what was typed is what is stored, and it
    then comes back from the range query the calendar draws itself from."""
    cid, cal, uid = client.ids["contact"], client.ids["calendar"], client.ids["user"]
    starts = utcnow().replace(microsecond=0) + timedelta(days=4)
    ends = starts + timedelta(minutes=90)

    r = client.post("/api/appointments", json={
        "title": "  Roof inspection  ", "contact_id": cid, "calendar_id": cal,
        "assigned_user_id": uid, "notes": "gate code 1174",
        "starts_at": starts.isoformat(), "ends_at": ends.isoformat()})
    assert r.status_code == 201

    rows = _appointment_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row.title == "Roof inspection", "the title was stored with its whitespace"
    assert (row.contact_id, row.calendar_id, row.assigned_user_id) == (cid, cal, uid)
    assert row.notes == "gate code 1174"
    assert row.starts_at.replace(tzinfo=UTC) == starts
    assert row.ends_at.replace(tzinfo=UTC) == ends
    # The POST model has no `status`, so a booking made from the UI takes the
    # model default. If that ever changes, the dialog's disabled Status control
    # is telling the user something untrue.
    assert row.status == "confirmed"

    # ...and the calendar can actually see it. The month grid asks for whole
    # weeks around the anchor, so query the way the browser does.
    window = client.get("/api/appointments", params={
        "start": (starts - timedelta(days=14)).isoformat(),
        "end": (starts + timedelta(days=14)).isoformat()}).json()
    assert [a["id"] for a in window] == [row.id]
    assert window[0]["title"] == "Roof inspection"
    assert window[0]["contact_name"] == "First00 Last00"
    assert window[0]["calendar_id"] == cal


def test_a_tech_cannot_create_an_appointment_and_writes_no_row(client):
    """Creating is STAFF. The browser disables the control for a TECH, but the
    backend is the thing that enforces it — and a 403 that still wrote a row
    would pass a status-only assertion."""
    starts = utcnow() + timedelta(days=2)
    before = len(_appointment_rows())

    tech = TestClient(app)
    tech.headers["Authorization"] = "Bearer " + client.tokens["tech"]
    r = tech.post("/api/appointments", json={
        "title": "Sneaky booking", "contact_id": client.ids["contact"],
        "starts_at": starts.isoformat(),
        "ends_at": (starts + timedelta(hours=1)).isoformat()})
    assert r.status_code == 403

    assert len(_appointment_rows()) == before, "a refused create still wrote a row"
    # ...and nothing was queued for it either: a suppressed booking must not text
    # the customer a reminder for an appointment that does not exist.
    db = SessionLocal()
    try:
        assert not [j for j in db.scalars(select(Job)).all()
                    if j.type == "appointment_reminder"]
    finally:
        db.close()


@pytest.mark.parametrize("why,body", [
    ("end before start", {"title": "Backwards", "offset_hours": -1}),
    ("end equal to start", {"title": "Zero length", "offset_hours": 0}),
    ("missing title", {"title": "", "offset_hours": 1}),
    ("whitespace title", {"title": "   ", "offset_hours": 1}),
])
def test_an_invalid_appointment_creates_nothing(client, why, body):
    """`title: str` accepts "" and "   ", so an untitled booking was created
    happily and rendered as an empty chip. Each of these must leave the table
    exactly as it found it."""
    starts = utcnow() + timedelta(days=5)
    before = len(_appointment_rows())

    r = client.post("/api/appointments", json={
        "title": body["title"], "starts_at": starts.isoformat(),
        "ends_at": (starts + timedelta(hours=body["offset_hours"])).isoformat()})
    assert r.status_code == 400, why
    # A sentence, not a schema dump: the dialog renders `detail` verbatim.
    assert isinstance(r.json()["detail"], str) and r.json()["detail"]

    assert len(_appointment_rows()) == before, "%s still created an appointment" % why


def test_calendars_list_exposes_filter_groups(client):
    cals = client.get("/api/calendars").json()
    assert len(cals) == 1
    assert cals[0]["user_name"] == "Owen"


# ---------------- Dashboard ----------------

def test_dashboard_totals_match_opportunities(client):
    pid = client.ids["pipeline"]
    every = client.get(f"/api/opportunities?pipeline_id={pid}&status=all").json()
    d = client.get("/api/dashboard").json()
    assert d["total"] == len(every)
    assert d["total_value_cents"] == sum(o["value_cents"] for o in every)
    assert d["status"]["won"] == sum(1 for o in every if o["status"] == "won")


def test_conversion_rate_excludes_open(client):
    d = client.get("/api/dashboard").json()
    won, lost = d["status"]["won"], d["status"]["lost"]
    expected = round(won / (won + lost) * 100, 2) if (won + lost) else 0
    assert d["conversion_rate"] == expected


# ---- the date range and the funnel, on data built by hand ----------------
#
# Four stages, every one a DIFFERENT occupancy, and the occupancies rise before
# they fall (10 / 3 / 6 / 1) exactly as the production pipeline's do. That shape
# is what put a "next step conversion" of 400% on screen: 6 sitting in stage 2
# over 3 sitting in stage 1 is a ratio of two occupancies, not a conversion. A
# wrong denominator cannot pass against these numbers.
#
# The old rows are all won and expensive, so if the range filter leaks even once
# every figure on the endpoint moves at the same time.

IN_RANGE_DAYS = 5
OUT_OF_RANGE_DAYS = 200

#                  stage, status, value_cents, how many
IN_RANGE_ROWS = [(0, "open", 1000, 6), (0, "won", 5000, 2),
                 (0, "lost", 3000, 1), (0, "abandoned", 2000, 1),
                 (1, "open", 1000, 1), (1, "won", 5000, 1),
                 (1, "lost", 3000, 1),
                 (2, "open", 1000, 3), (2, "won", 5000, 1),
                 (2, "lost", 3000, 1), (2, "abandoned", 2000, 1),
                 (3, "open", 1000, 1)]
OLD_ROWS = [(0, "won", 999_900, 5)]

# Hand-computed from the two tables above, not read back off the endpoint.
RECENT = {"total": 20, "won": 4, "open": 11, "lost": 3, "abandoned": 2,
          "total_value": 44_000, "won_value": 20_000,
          "value_by_status": {"won": 20_000, "open": 11_000,
                              "lost": 9_000, "abandoned": 4_000},
          "counts": [10, 3, 6, 1], "reached": [20, 10, 7, 1],
          "cumulative": [100.0, 50.0, 35.0, 5.0],
          "next_step": [50.0, 70.0, 14.29, None],
          "stage_value": [21_000, 9_000, 13_000, 1_000]}
ALL_TIME = {"total": 25, "won": 9, "open": 11, "lost": 3, "abandoned": 2,
            "total_value": 5_043_500, "won_value": 5_019_500,
            "counts": [15, 3, 6, 1], "reached": [25, 10, 7, 1],
            "cumulative": [100.0, 40.0, 28.0, 4.0],
            "next_step": [40.0, 70.0, 14.29, None]}


@pytest.fixture()
def dash():
    """Its own database, because the shared `client` fixture's opportunities would
    land in these totals and nothing here could then be asserted as a literal."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    users = [User(email="a@x.test", name="Owen", role=Role.ADMIN),
             User(email="c@x.test", name="Tech", role=Role.TECH)]
    db.add_all(users)
    db.flush()

    pipe = Pipeline(name="AHS")
    db.add(pipe)
    db.flush()
    stages = [Stage(pipeline_id=pipe.id, name=n, position=i) for i, n in
              enumerate(["New Lead", "Inspection", "Request the Approval",
                         "Submit The Invoice"])]
    db.add_all(stages)
    db.flush()

    n = 0
    for rows, days in ((IN_RANGE_ROWS, IN_RANGE_DAYS), (OLD_ROWS, OUT_OF_RANGE_DAYS)):
        for stage_i, status, cents, how_many in rows:
            for _ in range(how_many):
                n += 1
                db.add(Opportunity(
                    title="OPP %d" % n, pipeline_id=pipe.id,
                    stage_id=stages[stage_i].id, value_cents=cents, status=status,
                    created_at=utcnow() - timedelta(days=days)))
    db.commit()

    tokens = {}
    for key, user in (("admin", users[0]), ("tech", users[1])):
        plain, token = mint_api_token(user, name="test-%s" % key)
        db.add(token)
        tokens[key] = plain
    db.commit()
    # Read the ids out before the session closes — the ORM objects detach with it.
    pipeline_id = pipe.id
    stage_ids = [s.id for s in stages]
    db.close()

    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + tokens["admin"]
        c.tokens = tokens
        c.pipeline_id = pipeline_id
        c.stage_ids = stage_ids
        yield c


def _last(days: int) -> dict:
    return {"start": (utcnow() - timedelta(days=days)).isoformat()}


def test_the_date_range_narrows_every_card(dash):
    """The control offered ranges and `/api/dashboard` took no date parameter at
    all, so "Last 30 days" and "All time" returned the same numbers."""
    wide = dash.get("/api/dashboard").json()
    assert wide["total"] == ALL_TIME["total"]
    assert wide["total_value_cents"] == ALL_TIME["total_value"]

    narrow = dash.get("/api/dashboard", params=_last(30)).json()
    assert narrow["total"] == RECENT["total"] < wide["total"], (
        "the range does not exclude the five opportunities created 200 days ago")
    assert narrow["status"] == {"won": RECENT["won"], "open": RECENT["open"],
                                "lost": RECENT["lost"],
                                "abandoned": RECENT["abandoned"]}
    assert narrow["total_value_cents"] == RECENT["total_value"]
    assert narrow["won_value_cents"] == RECENT["won_value"]
    # ...and the excluded rows were the expensive ones, so this is not a coincidence.
    assert narrow["won_value_cents"] < wide["won_value_cents"]


def test_an_opportunity_created_inside_the_range_is_counted(dash):
    """The other half: a window that contains only the OLD rows must show only
    those. A filter that narrows to nothing would pass the test above."""
    only_old = dash.get("/api/dashboard", params={
        "start": (utcnow() - timedelta(days=OUT_OF_RANGE_DAYS + 1)).isoformat(),
        "end": (utcnow() - timedelta(days=OUT_OF_RANGE_DAYS - 1)).isoformat()}).json()
    assert only_old["total"] == 5
    assert only_old["status"]["won"] == 5
    assert only_old["won_value_cents"] == 5 * 999_900


def test_all_time_stays_the_default_when_no_range_is_given(dash):
    """The owner's decision: no range means every opportunity ever created.

    A 30-day default would have emptied this screen the moment the filter started
    working — production's opportunities were created in one week of August.
    """
    bare = dash.get("/api/dashboard").json()
    assert bare["total"] == ALL_TIME["total"]
    assert bare["total"] != RECENT["total"], "no range is being read as 30 days"
    assert bare["range"] == {"start": None, "end": None}
    # Same answer as an explicitly enormous window.
    wide = dash.get("/api/dashboard", params=_last(3650)).json()
    assert wide["total"] == bare["total"]
    assert wide["total_value_cents"] == bare["total_value_cents"]


def test_counts_and_revenue_match_hand_computed_values(dash):
    """Per-status money used to be invented in the browser: Lost was hard-coded to
    $0 and Open was drawn as `total - won`, which is only right when nothing has
    been lost or abandoned. Here all four buckets are non-empty and different."""
    d = dash.get("/api/dashboard", params=_last(30)).json()
    assert d["value_by_status"] == RECENT["value_by_status"]
    assert sum(d["value_by_status"].values()) == d["total_value_cents"]
    assert d["value_by_status"]["lost"] == 9_000 != 0
    assert d["value_by_status"]["open"] != d["total_value_cents"] - d["won_value_cents"]


def test_conversion_rates_cannot_exceed_one_hundred_percent(dash):
    d = dash.get("/api/dashboard", params=_last(30)).json()
    # 4 won of 7 decided; 4 won of 20 opportunities.
    assert d["conversion_rate"] == 57.14
    assert d["conversion_rate_all"] == 20.0
    assert 0 <= d["conversion_rate"] <= 100
    assert 0 <= d["conversion_rate_all"] <= 100


def test_the_funnel_obeys_the_date_range(dash):
    """The Funnel and Stage distribution cards read `/api/pipelines`, which counts
    every opportunity ever created — so those two cards ignored the range even
    once the other three obeyed it."""
    wide = dash.get("/api/dashboard/funnel").json()
    assert [s["count"] for s in wide["stages"]] == ALL_TIME["counts"]

    narrow = dash.get("/api/dashboard/funnel", params=_last(30)).json()
    assert [s["count"] for s in narrow["stages"]] == RECENT["counts"]
    assert [s["value_cents"] for s in narrow["stages"]] == RECENT["stage_value"]
    assert narrow["total"] == RECENT["total"] < wide["total"]
    assert sum(s["count"] for s in narrow["stages"]) == narrow["total"]


def test_next_step_conversion_is_a_share_of_the_deals_that_got_there(dash):
    """`count[i+1] / count[i]` is how 400% reached the screen: stage 2 holds more
    deals than stage 1, and a ratio of two occupancies is not a conversion rate.

    Of the deals that REACHED a stage, the share that went on to the next one is
    `reached[i+1] / reached[i]`, which cannot exceed 1 because `reached` only falls.
    """
    for params, expected in ((_last(30), RECENT), ({}, ALL_TIME)):
        stages = dash.get("/api/dashboard/funnel", params=params).json()["stages"]
        assert [s["reached"] for s in stages] == expected["reached"]
        assert [s["next_step_pct"] for s in stages] == expected["next_step"]
        for s in stages:
            assert s["next_step_pct"] is None or 0 <= s["next_step_pct"] <= 100, (
                "%s converts at %s%%" % (s["name"], s["next_step_pct"]))
        # The last stage has nothing to convert into.
        assert stages[-1]["next_step_pct"] is None

    # The naive formula really would have been over 100% on this data.
    stages = dash.get("/api/dashboard/funnel", params=_last(30)).json()["stages"]
    naive = stages[2]["count"] / stages[1]["count"] * 100
    assert naive > 100 and stages[1]["next_step_pct"] < 100


def test_the_cumulative_column_falls_monotonically(dash):
    """It read New Lead 30%, Inspection 10%, Request Approval 40% — a distribution
    (`count / total`) printed under a heading that promises a cumulative."""
    for params, expected in ((_last(30), RECENT), ({}, ALL_TIME)):
        stages = dash.get("/api/dashboard/funnel", params=params).json()["stages"]
        cumulative = [s["cumulative_pct"] for s in stages]
        assert cumulative == expected["cumulative"]
        assert cumulative[0] == 100.0, "the first stage is not everybody"
        assert cumulative == sorted(cumulative, reverse=True), (
            "the cumulative column goes back up at some stage: %s" % cumulative)
        # ...and it is consistent with the next-step column beside it.
        for i, s in enumerate(stages[:-1]):
            assert round(cumulative[i] * s["next_step_pct"] / 100, 2) == cumulative[i + 1]


def test_an_empty_range_leaves_the_funnel_without_percentages(dash):
    """Nothing reached any stage, so there is no denominator. `0%` everywhere would
    claim the pipeline converts at zero, which is a different statement."""
    empty = dash.get("/api/dashboard/funnel", params={
        "start": (utcnow() - timedelta(days=1)).isoformat()}).json()
    assert empty["total"] == 0
    assert [s["count"] for s in empty["stages"]] == [0, 0, 0, 0]
    assert all(s["cumulative_pct"] is None for s in empty["stages"])
    assert all(s["next_step_pct"] is None for s in empty["stages"])


def test_the_dashboard_is_staff_only_and_leaks_nothing_to_a_tech(dash):
    tech = dash.get("/api/dashboard",
                    headers={"Authorization": "Bearer " + dash.tokens["tech"]})
    assert tech.status_code == 403
    body = tech.text
    for leaked in ("total", "conversion", "value_cents", str(ALL_TIME["total_value"])):
        assert leaked not in body, "the refusal carries %r" % leaked


def test_the_funnel_stays_readable_by_a_tech(dash):
    """Deliberate, and recorded in DECISIONS.md (2026-09-09): per-stage counts and
    money are already TECH-visible through `/api/pipelines` and `/api/opportunities`,
    so putting the funnel behind the STAFF gate would have removed a card from a
    dispatched tech's screen without withholding anything they cannot already read.
    """
    r = dash.get("/api/dashboard/funnel",
                 headers={"Authorization": "Bearer " + dash.tokens["tech"]})
    assert r.status_code == 200
    assert [s["count"] for s in r.json()["stages"]] == ALL_TIME["counts"]


def test_an_unknown_pipeline_is_a_404_rather_than_an_empty_funnel(dash):
    """An empty funnel for a pipeline that does not exist reads as "no deals yet"."""
    assert dash.get("/api/dashboard/funnel",
                    params={"pipeline_id": 99_999}).status_code == 404
    ok = dash.get("/api/dashboard/funnel", params={"pipeline_id": dash.pipeline_id})
    assert ok.status_code == 200 and ok.json()["pipeline_id"] == dash.pipeline_id


# ---------------- Reporting ----------------

def test_call_report_counts_and_durations(client):
    r = client.get("/api/reports/calls", params={"direction": "INBOUND"}).json()
    assert r["total_calls"] == 1
    assert r["by_status"] == {"completed": 1}
    assert r["avg_duration_seconds"] == 30
    assert r["total_duration_seconds"] == 30
    assert r["top_sources"][0]["calls"] == 1


def test_call_report_direction_filter(client):
    out = client.get("/api/reports/calls", params={"direction": "OUTBOUND"}).json()
    assert out["total_calls"] == 0


def test_appointment_report_tiles(client):
    start = utcnow() + timedelta(days=2)
    client.post("/api/appointments", json={
        "title": "A", "contact_id": client.ids["contact"],
        "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(hours=1)).isoformat()})
    r = client.get("/api/reports/appointments").json()
    assert r["total"] == 1
    assert set(r["tiles"]) == {"booked", "confirmed", "cancelled", "new",
                               "showed", "no-show", "invalid", "rescheduled"}
    assert sum(r["tiles"].values()) == r["total"]
    assert [c["count"] for c in r["by_calendar"]] == sorted(
        [c["count"] for c in r["by_calendar"]], reverse=True)


# ---------------- Transport seam ----------------

def test_outbound_is_never_marked_delivered(client):
    """The whole v1 promise: the UI is real, nothing leaves the building."""
    from app.transport import get_transport
    ref = get_transport().send_sms(to="+19415550000", body="x", from_number="")
    assert ref.delivered is False
    assert client.get("/api/health").json()["transport"] == "LoggingTransport"


def test_opportunity_search_treats_wildcards_as_characters(client):
    """`%` and `_` are LIKE wildcards, so typing either matched every opportunity.

    The term was always parameterised — this was never an injection — but the
    wildcards inside it were still read as wildcards, so the box did the opposite of
    narrowing. Asserted by what comes back, not by the status: the filter has to pick
    out the row that literally contains the character and leave the other six.
    """
    pid = client.ids["pipeline"]
    cid = client.ids["contact"]
    sid = client.ids["stage1"]

    def titles(q):
        r = client.get("/api/opportunities", params={"pipeline_id": pid, "q": q,
                                                     "status": "all"})
        assert r.status_code == 200
        return sorted(o["title"] for o in r.json())

    for title in ("50% off new roof", "roof_inspection"):
        assert client.post("/api/opportunities", json={
            "title": title, "pipeline_id": pid, "stage_id": sid,
            "contact_id": cid, "value_cents": 1000}).status_code == 201

    assert len(titles("")) == 8, "fixture changed — retarget this test"
    # Before the fix each of these returned all 8.
    assert titles("%") == ["50% off new roof"]
    assert titles("_") == ["roof_inspection"]
    # A wildcard must not smuggle a match in either: `%roof` is not `roof`.
    assert titles("%roof") == []
    assert titles("zzzznope") == []
    # ...and an ordinary term still matches both, so the escaping did not break search.
    assert titles("roof") == ["50% off new roof", "roof_inspection"]

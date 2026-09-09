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
    Calendar,
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
        db.add(Opportunity(title="OPP %d" % i, contact_id=contacts[i].id,
                           pipeline_id=pipe.id, stage_id=s1.id,
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

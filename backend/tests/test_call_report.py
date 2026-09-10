"""The Call report has to be computed from real records, not from defaults.

Every fixture number here is DELIBERATELY DIFFERENT from every other one: three
sources with three different call counts, five different durations, a caller who
called before the window, a call with no status at all, and a won opportunity on a
contact who never called. A shared, copy-pasted or hardcoded value cannot pass —
which is the point. The screen shipped looking real against uniform seed data,
where "avg duration" read the same on every row whether or not it was computed.

Reference for what each number means: DECISIONS.md, "Reporting — SCOPE CHANGE".
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

NOW = datetime.now(UTC)


def _call(conv_id, *, ago_days, seconds, status, direction=Direction.INBOUND):
    return ConversationEvent(
        conversation_id=conv_id, type=EventType.CALL, direction=direction,
        occurred_at=NOW - timedelta(days=ago_days),
        duration_seconds=seconds, call_status=status)


@pytest.fixture()
def client():
    """Five inbound calls in the window, across three sources.

    | contact   | source     | when  | secs | status    | first-time? |
    |-----------|------------|-------|------|-----------|-------------|
    | lsa1      | Google LSA | -100d | 45   | completed | (outside the window) |
    | lsa1      | Google LSA | -5d   | 600  | completed | no — called before   |
    | lsa2      | Google LSA | -4d   | 200  | no-answer | yes |
    | lsa2      | Google LSA | -3d   | 100  | completed | no  |
    | ref1      | Referral   | -2d   | 20   | voicemail | yes |
    | yelp1     | Yelp       | -1d   | 12   | NULL      | yes |
    | yelp1     | Yelp       | -1d   | 500  | completed | OUTBOUND    |

    Won opportunities: two on lsa1, one on lsa2, one on `quiet` (Google LSA, never
    called), one lost on yelp1.
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    admin = User(email="a@x.test", name="Owen", role=Role.ADMIN)
    db.add(admin)
    db.flush()

    people = {}
    for key, source in (("lsa1", "Google LSA"), ("lsa2", "Google LSA"),
                        ("ref1", "Referral"), ("yelp1", "Yelp"),
                        ("quiet", "Google LSA")):
        c = Contact(first_name=key, last_name="Caller", source=source,
                    phone="(941) 555-01%02d" % len(people))
        db.add(c)
        db.flush()
        people[key] = c

    pipe = Pipeline(name="AHS")
    db.add(pipe)
    db.flush()
    stage = Stage(pipeline_id=pipe.id, name="New Lead", position=0)
    db.add(stage)
    db.flush()

    def opp(contact, status, cents):
        db.add(Opportunity(title="%s deal" % contact.first_name,
                           contact_id=contact.id, pipeline_id=pipe.id,
                           stage_id=stage.id, value_cents=cents, status=status))

    opp(people["lsa1"], "won", 900_00)
    opp(people["lsa1"], "won", 250_00)
    opp(people["lsa2"], "won", 400_00)
    opp(people["quiet"], "won", 999_00)   # never called: must NOT be attributed
    opp(people["yelp1"], "lost", 100_00)
    opp(people["ref1"], "open", 100_00)

    convs = {}
    for key in ("lsa1", "lsa2", "ref1", "yelp1"):
        conv = Conversation(contact_id=people[key].id)
        db.add(conv)
        db.flush()
        convs[key] = conv.id

    db.add_all([
        _call(convs["lsa1"], ago_days=100, seconds=45, status="completed"),
        _call(convs["lsa1"], ago_days=5, seconds=600, status="completed"),
        _call(convs["lsa2"], ago_days=4, seconds=200, status="no-answer"),
        _call(convs["lsa2"], ago_days=3, seconds=100, status="completed"),
        _call(convs["ref1"], ago_days=2, seconds=20, status="voicemail"),
        # A call the telephony feed never gave a status to. It is not "completed".
        _call(convs["yelp1"], ago_days=1, seconds=12, status=None),
        _call(convs["yelp1"], ago_days=1, seconds=500, status="completed",
              direction=Direction.OUTBOUND),
        # An SMS in the window: the report must not count it as a call.
        ConversationEvent(conversation_id=convs["ref1"], type=EventType.SMS,
                          direction=Direction.INBOUND,
                          occurred_at=NOW - timedelta(days=2), body="hi"),
    ])

    plain, token = mint_api_token(admin, name="test-admin")
    db.add(token)
    db.commit()
    ids = {k: v.id for k, v in people.items()}
    db.close()

    with TestClient(app) as c:
        c.headers["Authorization"] = "Bearer " + plain
        c.ids = ids
        yield c


def window(days_back=7, days_forward=1):
    return {"start": (NOW - timedelta(days=days_back)).isoformat(),
            "end": (NOW + timedelta(days=days_forward)).isoformat()}


def call_report(client, direction="INBOUND", **extra):
    r = client.get("/api/reports/calls",
                   params={**window(), "direction": direction, **extra})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------- totals and status ----------------

def test_totals_count_calls_in_the_window_only(client):
    r = call_report(client)
    # Six inbound calls exist; one is 100 days old and one row is an SMS.
    assert r["total_calls"] == 5
    assert r["total_duration_seconds"] == 600 + 200 + 100 + 20 + 12 == 932
    assert r["avg_duration_seconds"] == round(932 / 5)


def test_a_call_with_no_status_is_not_reported_as_completed(client):
    """`call_status` is nullable and the telephony feed does not always set it.
    Defaulting the blank to "completed" turns an unknown call into a good one."""
    r = call_report(client)
    assert r["by_status"] == {"completed": 2, "no-answer": 1,
                              "voicemail": 1, "unknown": 1}
    assert sum(r["by_status"].values()) == r["total_calls"]


def test_direction_filter_changes_every_number(client):
    inbound = call_report(client, "INBOUND")
    outbound = call_report(client, "OUTBOUND")
    assert outbound["total_calls"] == 1
    assert outbound["total_duration_seconds"] == 500
    assert outbound["avg_duration_seconds"] == 500
    assert outbound["by_status"] == {"completed": 1}
    assert outbound["avg_duration_seconds"] != inbound["avg_duration_seconds"]
    assert [s["source"] for s in outbound["top_sources"]] == ["Yelp"]


# ---------------- first-time callers ----------------

def test_first_time_means_first_ever_not_first_in_the_window(client):
    """lsa1 called 100 days ago, so their call inside the window is a repeat.
    Counting "first seen in this range" makes every long-standing caller new."""
    r = call_report(client)
    assert r["first_time_by_status"] == {"no-answer": 1, "voicemail": 1, "unknown": 1}
    assert sum(r["first_time_by_status"].values()) == 3


def test_first_time_card_reports_first_time_durations(client):
    """The two cards showed the same duration strip: the "First-time calls"
    figures were the whole window's, relabelled."""
    r = call_report(client)
    assert r["first_time_total_duration_seconds"] == 200 + 20 + 12 == 232
    assert r["first_time_avg_duration_seconds"] == round(232 / 3)
    assert r["first_time_total_duration_seconds"] != r["total_duration_seconds"]
    assert r["first_time_avg_duration_seconds"] != r["avg_duration_seconds"]


# ---------------- top sources ----------------

def test_each_source_gets_its_own_call_count_and_average(client):
    """Three sources, three different counts, three different averages. A shared
    average — the "2m 8s on every row" the owner reported — cannot pass this."""
    rows = {s["source"]: s for s in call_report(client)["top_sources"]}
    assert set(rows) == {"Google LSA", "Referral", "Yelp"}
    assert [s["source"] for s in call_report(client)["top_sources"]] == [
        "Google LSA", "Referral", "Yelp"]   # ordered by call count, descending

    assert rows["Google LSA"]["calls"] == 3
    assert rows["Referral"]["calls"] == 1
    assert rows["Yelp"]["calls"] == 1

    assert rows["Google LSA"]["avg_duration"] == round((600 + 200 + 100) / 3) == 300
    assert rows["Referral"]["avg_duration"] == 20
    assert rows["Yelp"]["avg_duration"] == 12
    assert len({s["avg_duration"] for s in rows.values()}) == 3

    assert sum(s["calls"] for s in rows.values()) == 5


def test_won_deals_are_attributed_only_to_sources_that_actually_called(client):
    """`quiet` has a won deal and the same source as three callers, but never
    called. Counting every won opportunity whose contact happens to share a source
    credits the phone with deals the phone never touched."""
    rows = {s["source"]: s for s in call_report(client)["top_sources"]}
    assert rows["Google LSA"]["won"] == 3      # lsa1 x2 + lsa2 — not `quiet`
    assert rows["Referral"]["won"] == 0        # one open opportunity
    assert rows["Yelp"]["won"] == 0            # one lost opportunity


def test_a_repeat_caller_is_not_counted_twice_for_the_same_deal(client):
    """lsa2 called twice in the window. Their one won deal is still one deal."""
    before = {s["source"]: s["won"] for s in call_report(client)["top_sources"]}
    db = SessionLocal()
    conv = db.query(Conversation).filter_by(contact_id=client.ids["lsa2"]).one()
    db.add(_call(conv.id, ago_days=1, seconds=77, status="completed"))
    db.commit()
    db.close()
    after = {s["source"]: s["won"] for s in call_report(client)["top_sources"]}
    assert after["Google LSA"] == before["Google LSA"] == 3


def test_winning_a_deal_moves_the_number(client):
    """The strongest evidence that the column is computed: change one record and
    exactly one cell changes."""
    db = SessionLocal()
    o = db.query(Opportunity).filter_by(contact_id=client.ids["ref1"]).one()
    o.status = "won"
    db.commit()
    db.close()
    rows = {s["source"]: s for s in call_report(client)["top_sources"]}
    assert rows["Referral"]["won"] == 1
    assert rows["Google LSA"]["won"] == 3
    assert rows["Yelp"]["won"] == 0


def test_narrowing_the_window_narrows_the_report(client):
    """A date filter that does not actually filter is the failure this project
    keeps finding, so assert the set really shrinks."""
    wide = call_report(client)
    narrow = client.get("/api/reports/calls", params={
        "start": (NOW - timedelta(days=2, hours=1)).isoformat(),
        "end": (NOW + timedelta(days=1)).isoformat(),
        "direction": "INBOUND"}).json()
    assert narrow["total_calls"] == 2 < wide["total_calls"]
    assert narrow["total_duration_seconds"] == 32
    assert {s["source"] for s in narrow["top_sources"]} == {"Referral", "Yelp"}
    # `quiet`'s won deal must not leak in through the narrower window either.
    assert all(s["won"] == 0 for s in narrow["top_sources"])


# ---------------- the telephony feed ----------------

def test_ingested_calls_carry_their_status_into_the_report(client):
    """The report can only be as real as the feed. `/api/events` is how the
    telephony project writes calls, and it had no way to send a call status at
    all, so every ingested call arrived blank."""
    before = call_report(client)["by_status"].get("no-answer", 0)
    r = client.post("/api/events", json={
        "contact_id": client.ids["ref1"], "type": "CALL", "direction": "INBOUND",
        "duration_seconds": 4, "call_status": "no-answer"})
    assert r.status_code == 201, r.text
    after = call_report(client)
    assert after["by_status"]["no-answer"] == before + 1
    assert after["by_status"].get("unknown", 0) == 1   # unchanged: still just yelp1


def test_ingest_refuses_a_call_status_it_cannot_report(client):
    """A typo'd status would silently become its own slice of the donut."""
    r = client.post("/api/events", json={
        "contact_id": client.ids["ref1"], "type": "CALL",
        "duration_seconds": 4, "call_status": "answered"})
    assert r.status_code == 400
    assert "answered" in r.text
    assert call_report(client)["total_calls"] == 5   # nothing was written

"""The owner's scheduling rules (2026-11-19), the same for the Dispatch page, its chat and Owen AI.

Pinned, by behaviour:
  * **The boards in use** are AHS Simple Flow and Retail (the default); Simple Flow's columns
    say what the next visit is — its first column holds new jobs AND booked inspections, so the
    date decides; pictures of a past visit mean it happened.
  * **A repair goes on the plan only with the customer's OK**: an accepted proposal, AHS's
    authorization (an AHS Approved move, Simple Flow's own flow, an AUTHO # in a note) or a yes
    passed with the customer's words — remembered by the next plans. A declined proposal wins
    over an older yes. Everything else is listed as waiting, with what was checked — callbacks
    created straight in Repair Day too.
  * **An inspection nobody verified by phone is CALL FIRST**: only a real conversation (30 s+,
    a person, after the job came in), the "Service address verified" field, or a welcome call
    followed by a booking counts — not the migration's move off Welcome Call!.
  * **Rain is a flag**: the same plan with and without the forecast; flagged visits say so.
  * **The crew's days told in the chat survive the Settings page's save.**
"""
from datetime import UTC, datetime, timedelta

import pytest
from app.dispatch import config as c
from app.dispatch import crew, readiness, scheduling
from app.main import app
from app.models import (
    Contact,
    Conversation,
    ConversationEvent,
    Direction,
    DispatchJob,
    DispatchNote,
    EventType,
    WeatherGrid,
    WeatherPoint,
    ZuperDocument,
    ZuperStatusHistory,
)
from app.weather import rules as wrules
from fastapi.testclient import TestClient
from tests.test_dispatch_ai import people  # noqa: F401  (admin, dispatcher, tech)

NOW = datetime(2026, 11, 19, 18, 0, tzinfo=UTC)           # Thu 1 PM New York
WESTON = (26.10, -80.40)
SF, RETAIL = c.SIMPLE_FLOW_BOARD, c.RETAIL_BOARD


def ny(month, day, hour, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=c.TZ).astimezone(UTC)


def job(number, status, *, board=SF, start=None, photos=False, phones=None, fields=None,
        created_days=10):
    n = int(number)
    return DispatchJob(
        job_uid="u%d" % n, job_number=str(n), board=board, status=status,
        customer_name="C%d" % n, city="Weston", address="%d Main St" % n,
        lat=WESTON[0] + n % 7 * 0.002, lng=WESTON[1], is_open=True,
        status_since=NOW - timedelta(days=3), phones=phones or [],
        zuper_created_at=NOW - timedelta(days=created_days), fields=fields,
        scheduled_start=start, scheduled_end=start + timedelta(hours=2) if start else None,
        visit_photo_at=start + timedelta(minutes=30) if photos and start else None,
        visit_photo_count=4 if photos and start else 0,
        assigned=["Antonio Brown"] if start else None)


def moves(db, number, *names, board=SF):
    for i, name in enumerate(names):
        db.add(ZuperStatusHistory(history_uid="h%s-%d" % (number, i), job_uid="u%s" % number,
                                  job_number=str(number), status_name=name, category_name=board,
                                  changed_at=NOW - timedelta(days=9 - i), done_by_name="Luis"))


def quote(db, number, status, updated, n="DTR-%s"):
    db.add(ZuperDocument(kind=ZuperDocument.QUOTE, zuper_uid="q%s-%s" % (number, status),
                         number=n % number, status=status, job_uid="u%s" % number,
                         zuper_updated_at=updated))


def call(db, phone, at, seconds, *, out=True, ai=False):
    con = db.query(Contact).filter_by(phone="+1" + phone).first()
    if con is None:
        con = Contact(first_name="P" + phone[-4:], phone="+1" + phone)
        db.add(con)
        db.flush()
        db.add(Conversation(contact_id=con.id))
        db.flush()
    conv = db.query(Conversation).filter_by(contact_id=con.id).one()
    db.add(ConversationEvent(conversation_id=conv.id, type=EventType.CALL,
                             direction=Direction.OUTBOUND if out else Direction.INBOUND,
                             occurred_at=at, duration_seconds=seconds, call_status="completed",
                             ai_call={"outcome": "ended"} if ai else None))


def proposed(out):
    return {v["job_number"]: v for d in out["days"] for t in d["techs"] for v in t["visits"]
            if not v["existing"]}


# ---- which jobs ---------------------------------------------------------------------------

def test_simple_flow_and_retail_columns_say_what_the_next_visit_is(db):
    db.add_all([
        job(701, c.SF_NEW),                                               # inspection to book
        job(702, c.SF_NEW, start=ny(11, 24, 9)),                          # booked: out
        job(703, c.SF_INSPECTION_DAY, start=ny(11, 14, 9)),               # did not happen
        job(704, c.SF_NEW, start=ny(11, 14, 9), photos=True),             # inspected -> repair
        job(705, c.SF_PROPOSAL),                                          # repair
        job(706, c.SF_HANDOFF),                                           # repair, no date
        job(707, c.SF_REPAIR_DAY, start=ny(11, 17, 9), photos=True),      # repaired: out
        job(708, c.SF_QUALITY),                                           # out
        job(709, "New Lead", board=RETAIL),                               # inspection
        job(710, "Proposal Made", board=RETAIL),                          # repair
        job(711, "Scheduled", board=RETAIL),                              # no quote: inspection
        job(712, "Scheduled", board=RETAIL),                              # quoted: repair
        job(713, "Repair Complete", board=RETAIL),                        # out
        job(714, "Welcome Call!", board=c.INSPECTION_BOARD),              # old board: not active
    ])
    quote(db, 712, "APPROVED", "2026-11-10")
    db.commit()
    got = {j.number: j for j in scheduling.waiting_jobs(db, NOW, hidden=set())}
    assert set(got) == {"701", "703", "704", "705", "706", "709", "710", "711", "712"}
    kinds = {n: j.kind for n, j in got.items()}
    assert kinds == {"701": "inspection", "703": "inspection", "704": "repair", "705": "repair",
                     "706": "repair", "709": "inspection", "710": "repair",
                     "711": "inspection", "712": "repair"}
    assert "did not happen" in got["703"].why and "Sat 11/14" in got["703"].why
    assert "714" in {j.number for j in scheduling.waiting_jobs(db, NOW, hidden=set(),
                                                                   boards="ahs")}
    # Simple Flow is AHS work: hidden from whoever may not see AHS - Inspection.
    hid = {j.number for j in scheduling.waiting_jobs(db, NOW, hidden={c.INSPECTION_BOARD})}
    assert "701" not in hid and "709" in hid


# ---- the repair's OK ----------------------------------------------------------------------

def test_a_repair_goes_on_the_plan_only_with_the_customers_ok(db):
    db.add_all([job(801, c.SF_PROPOSAL), job(802, c.SF_PROPOSAL), job(803, c.SF_HANDOFF),
                job(804, "Follow Up", board=RETAIL), job(805, "Proposal Made", board=RETAIL),
                job(806, c.SF_REPAIR_DAY), job(807, c.SF_PROPOSAL)])
    quote(db, 801, "APPROVED", "2026-11-15")                            # accepted
    quote(db, 802, "AWAIT_RESPONSE", "2026-11-15")                      # sent, no answer
    moves(db, 803, c.SF_NEW, c.SF_INSPECTION_DAY, c.SF_PROPOSAL)        # through AHS's step
    quote(db, 804, "APPROVED", "2026-11-01")
    quote(db, 804, "DECLINED", "2026-11-12")                            # the later answer wins
    moves(db, 806, c.SF_REPAIR_DAY)                                     # a callback, made there
    db.add(DispatchNote(note_uid="n807", job_uid="u807", job_number="807",
                        text="Note Added in Frontdoor System NCC $750 AUTHO # 12345RNCL",
                        created_at=NOW - timedelta(days=2)))
    db.commit()
    out = scheduling.make_plan(db, NOW, days=6, agreed=[
        {"job_number": "805", "source": "text", "quote": "Yes, go ahead with the Better option",
         "said_at": "2026-11-18"},
        {"job_number": "802", "source": "call", "quote": "", "said_at": "2026-11-18"}])
    got = proposed(out)
    assert set(got) == {"801", "803", "805", "807"}
    assert "DTR-801 accepted" in got["801"]["agreed"]
    assert "Proposal Creation" in got["803"]["agreed"]
    assert "Better option" in got["805"]["agreed"]
    assert "AUTHO # 12345RNCL" in got["807"]["agreed"]
    waiting = {w["job_number"]: w for w in out["waiting_ok"]}
    assert set(waiting) == {"802", "804", "806"}
    assert waiting["802"]["waiting_on"] == "customer" and "await_response" in \
        waiting["802"]["checked"]
    assert "DECLINED" in waiting["804"]["checked"]
    assert waiting["806"]["waiting_on"] == "ahs"
    assert out["summary"]["waiting_ok"] == 3
    assert any("802" in x and "own words" in x for x in out["agreed_refused"])
    # The yes is remembered by the next plan (the page's button too), with where it came from.
    again = proposed(scheduling.make_plan(db, NOW + timedelta(hours=1), days=6))
    assert "805" in again and "passed in plan #%d" % out["id"] in again["805"]["agreed"]
    # ...until the customer declines: a newer DECLINED proposal beats the yes.
    quote(db, 805, "DECLINED", "2026-11-19")
    db.commit()
    later = scheduling.make_plan(db, NOW + timedelta(hours=2), days=6)
    assert "805" not in proposed(later)
    assert "805" in {w["job_number"] for w in later["waiting_ok"]}


# ---- the inspection's call ----------------------------------------------------------------

def test_an_inspection_nobody_verified_by_phone_is_call_first(db):
    db.add_all([
        job(811, c.SF_NEW, phones=["3055550811"]),
        job(812, c.SF_NEW, phones=["3055550812"]),
        job(813, c.SF_NEW, phones=["3055550813"]),
        job(814, c.SF_NEW, fields={"Service address verified": "Yes"}),
        job(815, c.SF_NEW),
        job(816, c.SF_NEW),
        job(817, c.SF_NEW, phones=["3055550817"]),
    ])
    db.flush()
    call(db, "3055550811", NOW - timedelta(days=2), 240)                # a real conversation
    call(db, "3055550812", NOW - timedelta(days=2), 10)                 # 10 s: a voicemail
    call(db, "3055550813", NOW - timedelta(days=12), 300)               # before the job
    call(db, "3055550817", NOW - timedelta(days=1), 400, out=False, ai=True)   # the AI answered
    moves(db, 815, "Welcome Call!", c.SF_NEW)                           # the migration's move
    moves(db, 816, "Welcome Call!", "Scheduled", board=c.INSPECTION_BOARD)
    db.commit()
    out = scheduling.make_plan(db, NOW, days=6)
    got = proposed(out)
    assert {n for n, v in got.items() if not v["tentative"]} == {"811", "814", "816"}
    assert "Our call" in got["811"]["verified"] and "4 min" in got["811"]["verified"]
    assert got["814"]["verified"] == "Service address verified: Yes"
    assert "Welcome call made" in got["816"]["verified"]
    for n in ("812", "813", "815", "817"):
        assert got[n]["tentative"] and "verify the information" in got[n]["call_first_why"]
    verified_only = scheduling.make_plan(db, NOW, days=6, include_unreached=False)
    assert set(proposed(verified_only)) == {"811", "814", "816"}


# ---- rain ---------------------------------------------------------------------------------

def _wet(db, lat, lng, pop):
    p = WeatherPoint(point_key="%.2f,%.2f" % (lat, lng), lat=lat, lng=lng, grid_url="g:%s" % lat)
    db.add(p)
    hours = {wrules.key(NOW + timedelta(hours=h)): [pop, 6.0] for h in range(0, 24 * 8)}
    db.add(WeatherGrid(grid_url=p.grid_url, hours=hours, fetched_at=NOW))


def test_rain_is_a_flag_and_never_moves_a_visit(db, monkeypatch):
    db.add_all([job(n, c.SF_NEW, fields={"Service address verified": "Yes"})
                for n in (901, 902, 903)])
    db.commit()
    monkeypatch.setenv("WEATHER_ENABLED", "false")
    off = scheduling.make_plan(db, NOW, days=6, store=False)
    assert off["weather"]["enabled"] is False and "WEATHER_ENABLED" in off["weather"]["note"]
    monkeypatch.setenv("WEATHER_ENABLED", "true")
    for lat, lng in {(round(j.lat, 2), round(j.lng, 2)) for j in db.query(DispatchJob)}:
        _wet(db, lat, lng, 80)
    db.commit()
    on = scheduling.make_plan(db, NOW, days=6, store=False)

    def where(out):
        return {n: (v["start"], v["end"]) for n, v in proposed(out).items()}
    assert where(on) == where(off) and len(where(on)) == 3             # the same plan
    flagged = on["weather"]["flagged"]
    assert {f["job_number"] for f in flagged} == {"901", "902", "903"}
    assert all("80% chance of rain" in f["text"] for f in flagged)
    first = on["days"][0]["date"]
    assert all(f["next_day"] == (f["date"] == first) for f in flagged)
    assert all(v["weather"]["risk"] for v in proposed(on).values())


# ---- the crew -----------------------------------------------------------------------------

def test_the_days_told_in_the_chat_survive_a_settings_save(db, people):  # noqa: F811
    today = NOW.date()
    owen = {**crew.CREW_2026_11[2]}
    owen, problems = crew.change_ranges(owen, today=today, max_per_day=3, working=[
        {"from": "2026-11-20", "to": "2026-11-20"}])
    assert not problems and owen["only"] == [{"from": "2026-11-20", "to": "2026-11-20",
                                              "max": 3}]
    marx = crew.decided_crew("Marx Real")[1]
    marx, problems = crew.change_ranges(marx, today=today, working=[
        {"from": "2026-11-21", "to": "2026-11-21"}])
    assert problems and "not on call" in problems[0]                    # nothing changed
    antonio = {**crew.CREW_2026_11[0], "minutes": {"repair": 150}}
    crew.save(db, [antonio, marx, owen], None, NOW)
    # The Settings page sends the list back WITHOUT the chat's keys (lib/dispatch forPageSave).
    page = [{k: v for k, v in t.items() if k not in crew.RANGE_KEYS} for t in (antonio, marx, owen)]
    page[0]["max"] = 3
    r = TestClient(app).put("/api/dispatch/settings", json={"technicians": page},
                            headers=people["admin"][1])
    assert r.status_code == 200, r.text
    saved = {t["name"]: t for t in r.json()["technicians"]}
    assert saved["Antonio Brown"]["max"] == 3
    assert saved["Antonio Brown"]["minutes"] == {"repair": 150}
    assert saved["Owen Buzaglo"]["on_call"] is True
    assert saved["Owen Buzaglo"]["only"] == [{"from": "2026-11-20", "to": "2026-11-20",
                                              "max": 3}]
    assert saved["Marx Real"]["does"] == ["repair"]
    bad = TestClient(app).put("/api/dispatch/settings", json={"technicians": [
        {**page[2], "off": [{"from": "2026-11-25", "to": "2026-11-24"}]}]},
        headers=people["admin"][1])
    assert bad.status_code == 400


@pytest.mark.parametrize("raw,ok", [
    ({"job_number": "#805", "source": "text", "quote": "yes please", "said_at": "11/18"}, True),
    ({"job_number": "805", "source": "rumor", "quote": "yes", "said_at": "11/18"}, False),
    ({"job_number": "805", "source": "call", "quote": "  ", "said_at": "11/18"}, False),
])
def test_a_yes_needs_the_job_where_it_came_from_and_the_customers_words(raw, ok):
    keep, refused = readiness.clean_agreed([raw], NOW)
    assert bool(keep) is ok and bool(refused) is not ok
    if ok:
        assert keep[0]["job_number"] == "805" and keep[0]["said_at"] == "2026-11-18"

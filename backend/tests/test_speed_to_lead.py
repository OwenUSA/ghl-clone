"""Speed to lead (app/dispatch/speed.py, 2026-10-15).

What is pinned, by behaviour:
  * the clock counts BUSINESS minutes (Mon-Sat 8-6 New York): a work order at 9 PM answered at
    8:20 the next morning took 20 minutes and is on time;
  * it starts at the job's creation — or at the customer's own call or text in the three days
    before, when they reached out first;
  * it stops at the first thing a PERSON did: our call or text, or their call a person answered;
    an AI agent's answer or text is not a response;
  * on time / late / not contacted / waiting against the owner's targets (AHS 2 h, Retail 1 h),
    and a lead still inside its target is not counted for or against the on-time rate;
  * attempts before the first conversation, and Zuper's own trace (the first column move);
  * a missed call is one wait however often they rang, until a person called back;
  * the API: ADMIN + unrestricted DISPATCHER only, no repair board, no test job, and a board
    hidden from the reader is left out entirely.
"""
from datetime import UTC, datetime, timedelta

import pytest
from app.auth import mint_api_token
from app.dispatch import config as c
from app.dispatch import speed
from app.dispatch.comms import Comm
from app.main import app
from app.models import DispatchCall, DispatchJob, Pipeline, PipelinePermission, Role, User
from fastapi.testclient import TestClient

PHONE = "9415550100"


def ny(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=c.TZ).astimezone(UTC)


# Wednesday 2026-09-30 in New York.
WED_9AM = ny(2026, 9, 30, 9)


def call(at, *, out=False, talked=False, missed=False, ai=False, source="Quo", staff=None,
         answered=None):
    return Comm(at=at, kind="call", out=out, talked=talked, missed=missed, seconds=60 if talked
                else 0, summary="", source=source, staff=staff, ai=ai,
                answered=talked if answered is None else answered)


def text(at, *, out=False, ai=False):
    return Comm(at=at, kind="text", out=out, talked=False, missed=False, seconds=0, summary="hi",
                source="Quo", ai=ai)


def lead(created, board=c.INSPECTION_BOARD, phones=(PHONE,), uid="j1"):
    return speed.Job(job_uid=uid, job_number="701", board=board, customer_name="Jane Doe",
                     status="Work Order Received", created_at=created, phones=list(phones))


# ---- the clock ----------------------------------------------------------------------------

def test_business_minutes_skip_nights_and_sunday_but_not_saturday():
    assert speed.business_minutes(ny(2026, 9, 30, 21), ny(2026, 10, 1, 8, 30)) == 30
    assert speed.business_minutes(ny(2026, 10, 2, 17), ny(2026, 10, 3, 9)) == 120   # Fri->Sat
    assert speed.business_minutes(ny(2026, 10, 3, 17), ny(2026, 10, 5, 9)) == 120   # Sat->Mon
    assert speed.business_minutes(WED_9AM, WED_9AM - timedelta(minutes=5)) == 0
    # Across the end of daylight saving time (Sun 2026-11-01): Sat 5 PM -> Mon 9 AM.
    assert speed.business_minutes(ny(2026, 10, 31, 17), ny(2026, 11, 2, 9)) == 120


def test_a_work_order_at_night_answered_first_thing_is_on_time():
    created = ny(2026, 9, 30, 21)
    r = speed.measure(lead(created), [call(ny(2026, 10, 1, 8, 20), out=True)], [],
                      now=ny(2026, 10, 1, 12))
    assert (r["response_minutes"], r["result"], r["arrived"]) == (20, "on_time",
                                                                   "Mon-Sat, after hours")
    assert r["response_clock_minutes"] == 11 * 60 + 20
    assert r["first_response_how"] == "Quo call"
    assert r["first_response_by"] == "Quo (person not recorded)"


def test_the_clock_starts_when_the_customer_reached_out_first():
    created = WED_9AM + timedelta(hours=3)
    rang = WED_9AM - timedelta(days=1)                       # Tue 9 AM, nobody answered
    r = speed.measure(lead(created), [call(rang, missed=True),
                                      call(WED_9AM + timedelta(hours=4), out=True)], [],
                      now=WED_9AM + timedelta(hours=6))
    assert r["started_by"] == "Customer called first"
    assert r["start_at"] == rang.isoformat()
    assert r["missed_their_calls"] == 1
    assert r["result"] == "late"                             # 1 business day + 4 h
    # Older than three days is not this lead.
    r = speed.measure(lead(created), [call(created - timedelta(days=4), missed=True)], [],
                      now=created)
    assert r["started_by"] == "Job created"


def test_an_ai_agent_answering_or_texting_is_not_a_response():
    r = speed.measure(lead(WED_9AM), [call(WED_9AM + timedelta(minutes=5), talked=True, ai=True),
                                      text(WED_9AM + timedelta(minutes=6), out=True, ai=True),
                                      call(WED_9AM + timedelta(minutes=90), out=True,
                                           source="Zuper", staff="Luis Candiales")], [],
                      now=WED_9AM + timedelta(hours=5))
    assert r["response_minutes"] == 90
    assert r["first_response_by"] == "Luis Candiales"
    assert r["first_response_how"] == "Zuper call"
    assert r["ai_handled"] is True
    # Their call a person answered IS a response.
    r = speed.measure(lead(WED_9AM), [call(WED_9AM + timedelta(minutes=7), talked=True)], [],
                      now=WED_9AM + timedelta(hours=1))
    assert (r["response_minutes"], r["first_response_how"]) == (7, "Answered their Quo call")


@pytest.mark.parametrize("board,answer_after,now_after,result", [
    (c.INSPECTION_BOARD, 119, 300, "on_time"),
    (c.INSPECTION_BOARD, 121, 300, "late"),
    (c.INSPECTION_BOARD, None, 121, "not_contacted"),
    (c.INSPECTION_BOARD, None, 119, "waiting"),
    (c.RETAIL_BOARD, 61, 300, "late"),
    ("Sarasota Repairs", 59, 300, "on_time"),           # a regional board is Retail
])
def test_targets_are_the_owners(board, answer_after, now_after, result):
    comms = [text(WED_9AM + timedelta(minutes=answer_after), out=True)] if answer_after else []
    r = speed.measure(lead(WED_9AM, board=board), comms, [],
                      now=WED_9AM + timedelta(minutes=now_after))
    assert r["result"] == result


def test_attempts_before_the_first_conversation_and_zuper_s_first_move():
    comms = [call(WED_9AM + timedelta(minutes=10), out=True),
             call(WED_9AM + timedelta(minutes=40), out=True),
             call(WED_9AM + timedelta(minutes=70), out=True, talked=True)]
    moves = [(WED_9AM, "Work Order Received", "Owen Buzaglo"),
             (WED_9AM + timedelta(minutes=12), "Not Answering -Keep Call!", "Luis Candiales")]
    r = speed.measure(lead(WED_9AM), comms, moves, now=WED_9AM + timedelta(hours=3))
    assert (r["response_minutes"], r["talk_minutes"], r["attempts_before_talk"]) == (10, 70, 2)
    assert (r["move_minutes"], r["first_move_to"], r["first_move_by"]) == (
        12, "Not Answering -Keep Call!", "Luis Candiales")


def test_a_lead_with_no_phone_or_still_waiting_is_not_judged():
    now = WED_9AM + timedelta(hours=3)
    rows = [speed.measure(lead(WED_9AM, uid="a"), [text(WED_9AM + timedelta(minutes=30),
                                                        out=True)], [], now),
            speed.measure(lead(WED_9AM, uid="b"), [], [], now),               # not contacted
            speed.measure(lead(now - timedelta(minutes=5), uid="c"), [], [], now),   # waiting
            speed.measure(lead(WED_9AM, uid="d", phones=()), [], [], now)]    # no phone
    s = speed.summarize(rows)
    assert (s["leads"], s["on_time"], s["not_contacted"], s["waiting"], s["no_phone"]) == (
        4, 1, 1, 1, 1)
    assert s["on_time_pct"] == 50
    assert s["response_median"] == 30


def test_a_missed_call_is_one_wait_until_a_person_calls_back():
    t = WED_9AM + timedelta(hours=1)
    comms = {PHONE: [call(t, missed=True), call(t + timedelta(minutes=5), missed=True),
                     text(t + timedelta(minutes=6), out=True, ai=True),
                     call(t + timedelta(minutes=20), out=True)],
             "9415550111": [call(t, missed=True)]}
    m = speed.missed_calls(comms, WED_9AM, WED_9AM + timedelta(days=1),
                           now=t + timedelta(hours=3))
    assert (m["missed"], m["rings"], m["called_back"]) == (2, 3, 1)
    assert (m["on_time"], m["never_called_back"], m["callback_median"]) == (1, 1, 20)
    assert m["on_time_pct"] == 50


def test_the_window_is_when_the_clock_started():
    jobs = [lead(WED_9AM, uid="in"), lead(WED_9AM - timedelta(days=10), uid="out")]
    out = speed.build(jobs, {}, {}, WED_9AM - timedelta(days=1), WED_9AM + timedelta(days=1),
                      now=WED_9AM + timedelta(hours=1))
    assert [r["job_uid"] for r in out["leads"]] == ["in"]
    assert out["summary"]["AHS"]["leads"] == 1 and out["summary"]["Retail"]["leads"] == 0


# ---- the API ----------------------------------------------------------------------------------

@pytest.fixture()
def people(db):
    out = {}
    for key, role, restricted in (("admin", Role.ADMIN, False),
                                  ("dispatcher", Role.DISPATCHER, False),
                                  ("restricted", Role.DISPATCHER, True),
                                  ("tech", Role.TECH, False)):
        u = User(email="%s@x.test" % key, name=key.title(), role=role,
                 only_assigned_data=restricted)
        db.add(u)
        db.flush()
        plain, tok = mint_api_token(u, name=key)
        db.add(tok)
        out[key] = (u.id, plain)
    db.commit()
    return out


@pytest.fixture()
def api(db, people):
    created = datetime.now(UTC) - timedelta(days=2)
    for uid, number, board, title, phone in (
            ("a1", "701", c.INSPECTION_BOARD, "Doe roof", "9415550100"),
            ("r1", "801", c.RETAIL_BOARD, "Roe roof", "9415550188"),
            ("rr", "901", c.REPAIR_BOARD, "Doe repair", "9415550100"),
            ("t1", "734", c.INSPECTION_BOARD, "TEST - Owen reminder test", "9415550199")):
        db.add(DispatchJob(job_uid=uid, job_number=number, board=board, title=title,
                           status="New", customer_name="Jane Doe", phones=[phone],
                           zuper_created_at=created))
    db.add(DispatchCall(call_uid="z1", occurred_at=created + timedelta(minutes=15),
                        direction="OUTGOING", status="COMPLETED", duration_seconds=90,
                        number="9415550100", staff_name="Luis Candiales", job_uids=["a1"]))
    db.commit()
    client = TestClient(app)
    return client, lambda who: {"Authorization": "Bearer " + people[who][1]}


@pytest.mark.parametrize("who", ["tech", "restricted"])
def test_a_tech_or_restricted_user_is_refused(api, who):
    c_, as_ = api
    assert c_.get("/api/dispatch/speed-to-lead", headers=as_(who)).status_code == 403


def test_leads_only_from_lead_boards_with_the_first_response(api):
    c_, as_ = api
    out = c_.get("/api/dispatch/speed-to-lead", headers=as_("dispatcher")).json()
    by = {r["job_number"]: r for r in out["leads"]}
    assert set(by) == {"701", "801"}                    # no repair board, no TEST job
    assert by["701"]["first_response_by"] == "Luis Candiales"
    assert by["701"]["first_response_how"] == "Zuper call"
    assert by["801"]["result"] in ("not_contacted", "waiting")
    assert out["summary"]["all"]["leads"] == 2
    assert out["coverage"]["notes"]


def test_a_hidden_board_is_left_out(api, db, people):
    c_, as_ = api
    p = Pipeline(name="Retail")
    db.add(p)
    db.flush()
    db.add(PipelinePermission(pipeline_id=p.id, user_id=people["admin"][0]))
    db.commit()
    out = c_.get("/api/dispatch/speed-to-lead", headers=as_("dispatcher")).json()
    assert [r["job_number"] for r in out["leads"]] == ["701"]
    assert out["summary"]["Retail"]["leads"] == 0
    out = c_.get("/api/dispatch/speed-to-lead", headers=as_("admin")).json()
    assert {r["job_number"] for r in out["leads"]} == {"701", "801"}


def test_a_backwards_range_is_refused(api):
    c_, as_ = api
    r = c_.get("/api/dispatch/speed-to-lead?since=2026-10-10&until=2026-10-01",
               headers=as_("admin"))
    assert r.status_code == 400


def test_the_group_filter_narrows_every_table(api):
    c_, as_ = api
    out = c_.get("/api/dispatch/speed-to-lead?group=Retail", headers=as_("admin")).json()
    assert [r["job_number"] for r in out["leads"]] == ["801"]
    assert [t["name"] for t in out["by_board"]] == [c.RETAIL_BOARD]
    assert c_.get("/api/dispatch/speed-to-lead?group=Other",
                  headers=as_("admin")).status_code == 422


# ---- first call, booked, time in each column (2026-10-15, second round) -------------------

def test_the_first_call_ignores_texts_and_booked_is_the_first_move_to_scheduled():
    comms = [text(WED_9AM + timedelta(minutes=5), out=True),
             call(WED_9AM + timedelta(minutes=50), out=True, talked=True)]
    moves = [(WED_9AM, "Work Order Received", None),
             (WED_9AM + timedelta(minutes=52), "Welcome Call!", "Luis"),
             (WED_9AM + timedelta(minutes=55), "Scheduled", "Luis")]
    r = speed.measure(lead(WED_9AM), comms, moves, now=WED_9AM + timedelta(hours=3))
    assert (r["response_minutes"], r["call_minutes"], r["talk_minutes"]) == (5, 50, 50)
    assert r["booked_minutes"] == 55
    s = speed.summarize([r])
    assert (s["call_avg"], s["talk_avg"], s["booked_avg"], s["booked_pct"]) == (50, 50, 55, 100)


def test_time_in_each_column_is_business_time_until_the_next_move():
    t = ny(2026, 10, 5, 9)                                   # Monday
    moves = {
        "a": [(t, c.INSPECTION_BOARD, "Work Order Received"),
              (t + timedelta(hours=1), c.INSPECTION_BOARD, "Welcome Call!"),
              (t + timedelta(hours=4), c.INSPECTION_BOARD, "Scheduled")],      # still there
        "b": [(t, c.INSPECTION_BOARD, "Work Order Received"),
              (t + timedelta(hours=3), c.INSPECTION_BOARD, "Cancelled")],      # closed: left out
        "old": [(ny(2026, 9, 20, 9), c.INSPECTION_BOARD, "Welcome Call!")],    # the load
    }
    now = t + timedelta(hours=6)                             # Monday 3 PM
    out = speed.column_times(moves, ny(2026, 9, 1, 0), now, now)
    assert [b["board"] for b in out] == [c.INSPECTION_BOARD]
    cols = {x["name"]: x for x in out[0]["columns"]}
    assert set(cols) == {"Work Order Received", "Welcome Call!", "Scheduled"}
    wor = cols["Work Order Received"]
    assert (wor["entered"], wor["left"], wor["avg_minutes"], wor["median_minutes"]) == (
        2, 2, 120, 120)
    assert (cols["Welcome Call!"]["avg_minutes"], cols["Scheduled"]["now_in"],
            cols["Scheduled"]["longest_now_minutes"]) == (180, 1, 120)
    # 4 h in WOR + 3 h in Welcome Call! + 2 h so far in Scheduled = 9 h on the board.
    assert out[0]["columns"][0]["name"] == "Work Order Received"
    assert (wor["share_pct"], cols["Scheduled"]["share_pct"]) == (44, 22)


def test_jobs_the_zuper_load_dated_are_not_leads(db):
    """Zuper dates every job the 2026-09-17 load made by the day it was loaded (109 on
    production) — they are not leads and must not count, whatever the range."""
    for uid, created in (("load", ny(2026, 9, 17, 15)), ("batch", ny(2026, 9, 22, 11)),
                         ("real", ny(2026, 9, 23, 9))):
        db.add(DispatchJob(job_uid=uid, job_number=uid, board=c.INSPECTION_BOARD,
                           title="roof", status="New", phones=["9415550100"],
                           zuper_created_at=created))
    db.commit()
    assert [j.job_uid for j in speed.lead_jobs(db)] == ["real"]


# ---- the first CALL, answered or not (2026-10-16) -------------------------------------------

def test_the_first_call_is_ours_answered_or_not_or_theirs_that_we_picked_up():
    now = WED_9AM + timedelta(hours=6)
    # A text first, then our call nobody answered, then a conversation.
    r = speed.measure(lead(WED_9AM), [text(WED_9AM + timedelta(minutes=5), out=True),
                                      call(WED_9AM + timedelta(minutes=30), out=True,
                                           source="Zuper", staff="Luis Candiales"),
                                      call(WED_9AM + timedelta(minutes=90), out=True,
                                           talked=True)], [], now)
    assert (r["response_minutes"], r["call_minutes"], r["talk_minutes"]) == (5, 30, 90)
    assert (r["first_call_how"], r["first_call_by"]) == ("We called, Zuper", "Luis Candiales")
    assert (r["first_call_answered"], r["first_call_reached"], r["calls_to_reach"]) == (
        False, False, 2)
    # Their call that a person picked up is the first call; their missed call is not.
    r = speed.measure(lead(WED_9AM), [call(WED_9AM + timedelta(minutes=3), missed=True),
                                      call(WED_9AM + timedelta(minutes=8), talked=True)], [], now)
    assert (r["call_minutes"], r["first_call_how"], r["first_call_answered"]) == (
        8, "They called us, Quo", True)
    # A short call a person picked up is a response too, not only one of 30 seconds or more.
    r = speed.measure(lead(WED_9AM), [call(WED_9AM + timedelta(minutes=4), answered=True)], [],
                      now)
    assert (r["response_minutes"], r["call_minutes"], r["talk_minutes"]) == (4, 4, None)
    # Calls after the first conversation are about later stages: not counted to reach them.
    r = speed.measure(lead(WED_9AM), [call(WED_9AM + timedelta(minutes=10), out=True),
                                      call(WED_9AM + timedelta(minutes=20), out=True,
                                           talked=True),
                                      call(WED_9AM + timedelta(hours=3), out=True)], [], now)
    assert r["calls_to_reach"] == 2
    # Nobody called yet: how long it has been, in business time.
    r = speed.measure(lead(WED_9AM), [], [], now)
    assert (r["first_call_at"], r["not_called_minutes"]) == (None, 360)


def test_the_share_of_first_calls_answered_counts_only_leads_that_had_one():
    now = WED_9AM + timedelta(hours=6)
    rows = [speed.measure(lead(WED_9AM, uid="a"), [call(WED_9AM + timedelta(minutes=10),
                                                        out=True, talked=True)], [], now),
            speed.measure(lead(WED_9AM, uid="b"), [call(WED_9AM + timedelta(minutes=10),
                                                        out=True)], [], now),
            speed.measure(lead(WED_9AM, uid="c"), [], [], now)]
    s = speed.summarize(rows)
    assert (s["first_call_answered_pct"], s["first_call_reached_pct"], s["not_called"]) == (
        50, 50, 1)

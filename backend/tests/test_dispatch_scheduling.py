"""Remaking the schedule (2026-10-08). The owner's ask: take every new job that needs an
inspection, every AHS-approved repair and every visit that has to be rescheduled or fell
through, and fit as many as possible Monday to Saturday for Owen and Antonio with the least
driving — shown as a calendar, a table and an Excel.

What is pinned, by behaviour:
  * **What goes in:** a "book a visit" stage with no visit ahead; a visit stage whose visit has
    no time or has PASSED; a customer nobody reached is "call first" (or left out on ask); the
    AHS boards only unless all are asked for; a hidden board never.
  * **The customer's limits are never broken** (after 3 PM, not before a day, only Fridays, one
    technician); a job no day can satisfy is "not placed", with the limit named.
  * **The search beats a simple plan:** it never places fewer jobs, and on the same jobs it does
    not drive more; jobs in one area share a day.
  * **A plan is kept**: the person who made it and an ADMIN open it and its Excel; anyone else
    gets 404. The Excel has the office's Schedule layout, a week calendar, and what did not fit.
  * **The chat** plans with the limits it read, and hands out the Excel and the calendar.
"""
import io
import json
from datetime import UTC, datetime, time, timedelta
from itertools import pairwise

import pytest
from app.ai import vault
from app.dispatch import ai, booking, planner, scheduling
from app.dispatch import config as c
from app.main import app
from app.models import (
    AiConnection,
    Contact,
    Conversation,
    ConversationEvent,
    Direction,
    DispatchJob,
    DispatchPlan,
    EventType,
)
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from tests.ai_support import openai_call, openai_completion
from tests.test_dispatch_ai import people  # noqa: F401  (admin, dispatcher, tech)

NOW = datetime(2026, 10, 8, 18, 0, tzinfo=UTC)            # Thu 2 PM New York
WESTON = (26.10, -80.40)
HOMESTEAD = (25.47, -80.48)
MIAMI = (25.80, -80.20)


def ny(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=c.TZ).astimezone(UTC)


def job(uid, number, status, *, board=c.INSPECTION_BOARD, where=WESTON, start=None, waited=3,
        phones=None):
    return DispatchJob(job_uid=uid, job_number=str(number), board=board, status=status,
                       customer_name="C%s" % number, city="X", address="%s Main St" % number,
                       lat=where[0] + int(number) % 7 * 0.002, lng=where[1], is_open=True,
                       status_since=NOW - timedelta(days=waited), phones=phones or [],
                       scheduled_start=start, scheduled_end=start + timedelta(hours=2)
                       if start else None, assigned=["Owen Buzaglo"] if start else None)


# ---- what goes in --------------------------------------------------------------------------------

def test_every_job_that_needs_a_visit_goes_in_and_nothing_else(db):
    db.add_all([
        job("a", 701, "Welcome Call!"),                                  # new, not reached
        job("b", 702, "AHS Approved"),                                   # repair to book
        job("c", 703, "Reschedule Required", board=c.REPAIR_BOARD),
        job("d", 704, "Inspection: Same-Day Confirm", start=ny(7, 15)),   # visit fell through
        job("e", 705, "Scheduled"),                                      # visit stage, no time
        job("f", 706, "Scheduled", start=ny(9, 9)),                      # booked ahead: out
        job("g", 707, "Awaiting AHS Decision"),                          # waiting on AHS: out
        job("h", 708, "New Lead", board=c.RETAIL_BOARD),                 # Retail: out by default
        job("i", 709, "Reschedule Required", start=ny(12, 9)),           # has a new date: out
    ])
    db.commit()
    got = {j.number: j for j in scheduling.waiting_jobs(db, NOW, hidden=set())}
    assert set(got) == {"701", "702", "703", "704", "705"}
    assert got["701"].tentative and not got["702"].tentative
    assert got["702"].kind == "repair" and got["701"].kind == "inspection"
    assert "has passed" in got["704"].why and "Wed 10/07" in got["704"].why
    assert "no visit time" in got["705"].why
    reached = scheduling.waiting_jobs(db, NOW, hidden=set(), include_unreached=False)
    assert "701" not in {j.number for j in reached}
    every = scheduling.waiting_jobs(db, NOW, hidden=set(), boards="all")
    assert "708" in {j.number for j in every}
    hidden = scheduling.waiting_jobs(db, NOW, hidden={c.REPAIR_BOARD})
    assert "703" not in {j.number for j in hidden}


# ---- the customer's limits ----------------------------------------------------------------------

TECHS = [dict(booking.DEFAULT_TECHNICIANS[0]), dict(booking.DEFAULT_TECHNICIANS[1])]
MINUTES = {"Antonio Brown": {"repair": 120, "inspection": 90},
           "Owen Buzaglo": {"repair": 120, "inspection": 90}}


def pj(n, where=WESTON, kind="repair", **kw):
    return planner.PlanJob(uid="j%d" % n, number=str(n), customer="C%d" % n, city="X",
                           lat=where[0] + n * 0.001, lng=where[1], kind=kind,
                           waiting_since=NOW - timedelta(days=n), **kw)


def visits(out):
    return {v["job_number"]: (d["date"], t["tech"], v) for d in out["days"] for t in d["techs"]
            for v in t["visits"] if not v["existing"]}


def test_a_customers_limits_are_never_broken(db):
    late = pj(1)
    scheduling.apply_limits(late, {"after": "3 PM", "evidence": "son: only after 3"}, NOW)
    friday = pj(2)
    scheduling.apply_limits(friday, {"days": ["Fri"]}, NOW)
    later = pj(3)
    scheduling.apply_limits(later, {"not_before": "2026-10-14", "technician": "Antonio"}, NOW)
    never = pj(4)
    scheduling.apply_limits(never, {"days": ["Sun"]}, NOW)
    out = planner.plan(jobs=[late, friday, later, never], booked=[], technicians=TECHS,
                       minutes=MINUTES, now=NOW, days=12)
    got = visits(out)
    start = c.local(datetime.fromisoformat(got["1"][2]["start"])).time()
    assert start >= time(15, 0)
    assert "start after 3:00 PM (son: only after 3)" in got["1"][2]["limits"][0]
    assert datetime.fromisoformat(got["2"][0]).weekday() == 4
    assert got["3"][0] >= "2026-10-14" and got["3"][1] == "Antonio Brown"
    assert "4" not in got
    reason = next(u for u in out["unplaced"] if u["job_number"] == "4")["reason"]
    assert "customer's limits" in reason and "only Sun" in reason


# ---- the search ---------------------------------------------------------------------------------

def test_the_search_places_as_many_and_drives_no_more_than_a_simple_plan():
    # Waiting order zigzags between two far areas; a simple pass sends one technician back and
    # forth. The search keeps each area on its own day.
    jobs = [pj(i, WESTON if i % 2 else HOMESTEAD) for i in range(1, 9)]
    out = planner.plan(jobs=jobs, booked=[], technicians=TECHS, minutes=MINUTES, now=NOW, days=6)
    s, b = out["summary"], out["baseline"]
    assert s["placed"] >= b["placed"] and s["placed"] == 8
    assert s["drive_minutes"] <= b["drive_minutes"]
    for d in out["days"]:
        for t in d["techs"]:
            areas = {"W" if v["lat"] > 26 else "H" for v in t["visits"] if not v["existing"]}
            assert len(areas) <= 1, (d["date"], t["tech"], areas)   # one area a day each


def test_a_full_day_is_never_overbooked_and_booked_visits_keep_their_time():
    booked = [booking.Visit("Owen Buzaglo", ny(9, 10), ny(9, 12), *MIAMI, "#600 booked")]
    jobs = [pj(i, MIAMI) for i in range(1, 15)]
    out = planner.plan(jobs=jobs, booked=booked, technicians=TECHS, minutes=MINUTES, now=NOW,
                       days=2)
    for d in out["days"]:
        for t in d["techs"]:
            assert t["count"] <= t["capacity"]
            stops = t["visits"]
            for a, b in pairwise(stops):
                assert a["end"] <= b["start"]
    fri = next(t for t in out["days"][0]["techs"] if t["tech"] == "Owen Buzaglo")
    keep = next(v for v in fri["visits"] if v["existing"])
    assert keep["start"] == ny(9, 10).astimezone(c.TZ).isoformat()


# ---- kept plans, the API and the Excel ----------------------------------------------------------

def test_a_plan_is_kept_and_only_its_maker_and_an_admin_open_it(db, people):  # noqa: F811
    db.add_all([job("a", 701, "Welcome Call!"), job("b", 702, "AHS Approved", where=MIAMI)])
    db.commit()
    cl = TestClient(app)
    r = cl.post("/api/dispatch/plans", json={"days": 6}, headers=people["dispatcher"][1])
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["id"] and plan["summary"]["placed"] == 2 and plan["summary"]["tentative"] == 1
    assert db.get(DispatchPlan, plan["id"]).created_by_id == people["dispatcher"][0]
    mine = cl.get("/api/dispatch/plans/%d" % plan["id"], headers=people["dispatcher"][1])
    assert mine.json()["summary"] == plan["summary"]
    assert cl.get("/api/dispatch/plans/%d" % plan["id"],
                  headers=people["admin"][1]).status_code == 200
    assert cl.get("/api/dispatch/plans/%d" % plan["id"],
                  headers=people["tech"][1]).status_code == 403
    assert cl.post("/api/dispatch/plans", json={"mode": "fastest"},
                   headers=people["admin"][1]).status_code == 400
    # Another dispatcher: 404, never 403 (the plan was made with someone else's boards).
    from app.auth import mint_api_token
    from app.models import Role, User
    other = User(email="other@x.test", name="Other", role=Role.DISPATCHER)
    db.add(other)
    db.flush()
    plain, tok = mint_api_token(other, name="o")
    db.add(tok)
    db.commit()
    assert cl.get("/api/dispatch/plans/%d" % plan["id"], headers={
        "Authorization": "Bearer " + plain}).status_code == 404
    x = cl.get("/api/dispatch/plans/%d/schedule.xlsx" % plan["id"],
               headers=people["dispatcher"][1])
    assert x.status_code == 200
    wb = load_workbook(io.BytesIO(x.content))
    assert wb.sheetnames == ["Schedule", "Calendar", "By day", "Not placed",
                             "How it was planned"]
    sched = [[c_.value for c_ in row] for row in wb["Schedule"].iter_rows()]
    assert sched[0][:4] == ["Day", "Tech", "Window", "Customer"]
    rows = {r_[4]: r_ for r_ in sched[1:] if r_[4]}
    assert rows["701"][9].startswith("Proposed - CALL FIRST")
    assert rows["702"][5] == "Repair"
    cal = "\n".join(str(c_.value or "") for row in wb["Calendar"].iter_rows() for c_ in row)
    assert "#701" in cal and "CALL FIRST" in cal


# ---- the chat ------------------------------------------------------------------------------

@pytest.fixture()
def assistant_on(db, secrets_key):
    conn = AiConnection(name="DeepSeek", provider="deepseek",
                        api_key_encrypted=vault.encrypt("sk-test-DS-1"), api_key_last4="1",
                        default_model="deepseek-chat")
    db.add(conn)
    db.flush()
    s = ai.settings(db, for_update=True)
    s.ai_enabled, s.connection_id, s.model = True, conn.id, "deepseek-chat"
    db.commit()


def test_the_chat_plans_with_the_limits_it_read_and_hands_out_the_links(db, assistant_on,
                                                                         script):
    db.add(job("p", 721, "Reschedule Required", where=MIAMI, phones=["9547018639"]))
    con = Contact(first_name="Sebastian", phone="+19547018639")
    db.add(con)
    db.flush()
    conv = Conversation(contact_id=con.id)
    db.add(conv)
    db.flush()
    db.add(ConversationEvent(conversation_id=conv.id, type=EventType.SMS,
                             direction=Direction.INBOUND, occurred_at=NOW - timedelta(hours=3),
                             body="I can only be there after 3 PM"))
    db.commit()
    script.responses = [
        openai_completion(content=None, finish="tool_calls", tool_calls=[
            openai_call("plan_schedule", json.dumps({"days": 6}), "c1")]),
        openai_completion(content=None, finish="tool_calls", tool_calls=[
            openai_call("plan_schedule", json.dumps({"days": 6, "limits": [{
                "job_number": "721", "after": "3 PM",
                "evidence": "text Thu 10/08: only after 3 PM"}]}), "c2")]),
        openai_completion(content="#721 goes after 3 PM."),
    ]
    out = ai.chat(db, [{"role": "user", "content": "Remake the schedule"}], user_id=None,
                  hidden=set(), now=NOW)
    assert out["reply"] == "#721 goes after 3 PM." and not out["error"]
    first = json.loads(next(m for m in script.requests[1]["body"]["messages"]
                            if m["role"] == "tool")["content"])
    words = first["days"][0]["techs"][0]["list"][0]["last_calls_and_texts"]
    assert any("only be there after 3 PM" in w for w in words)
    second = json.loads([m for m in script.requests[2]["body"]["messages"]
                         if m["role"] == "tool"][-1]["content"])
    v = second["days"][0]["techs"][0]["list"][0]
    assert v["time"].startswith("3:00pm") and "after 3:00 PM" in v["limits"][0]
    plans = db.query(DispatchPlan).order_by(DispatchPlan.id).all()
    assert len(plans) == 2 and plans[-1].source == "chat"
    assert {"label": "Schedule plan #%d (Excel)" % plans[-1].id,
            "url": "/api/dispatch/plans/%d/schedule.xlsx" % plans[-1].id} in out["downloads"]
    assert any(d["url"] == "/dispatch?tab=book&plan=%d" % plans[-1].id for d in out["downloads"])

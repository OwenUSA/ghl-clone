"""Scheduling inside the Dispatch chat (2026-10-14).

The office: "help me schedule 1 new customer giving me 3 best options, or the unscheduled
customers — check the calls, flag what times they cannot be there, then a suggested schedule by
weeks". Pinned, by behaviour (all read only, nothing is booked):

  * **3 options for one job** respect what the customer said in calls and texts: only the days
    they offered or agreed, never a day they are away; each says how it fits and why.
  * **A hold blocks the options** (with the reason); "can't make it" gets NEW dates, noted.
    "Luis was placed on hold" is the phone line, not the job.
  * **A day agreed on a day nobody works** says so instead of "no free time".
  * **The week plan** leaves out a job whose visit happened or that is booked on the customer's
    other job, narrows the rest to what the calls allow, and lists every flagged customer.
"""
import json
from datetime import timedelta

from app.dispatch import ai, booking, briefs, scheduling
from app.dispatch import config as c
from app.models import DispatchJob
from tests.test_dispatch_briefs import FRI, ny, text, visit, zcall


def _job(db, number):
    return db.query(DispatchJob).filter(DispatchJob.job_number == str(number)).one()


def test_options_are_only_on_the_days_the_customer_offered_and_say_why(db):
    db.add(visit("r", 687, None, 0, board=c.INSPECTION_BOARD, status="Reschedule Required",
                 status_type="ON_HOLD", phones=["3055550501"], name="Frederick Ryerson"))
    db.add(visit("o", 273, 16, 9, who=("Antonio Brown",), phones=["3055550502"],
                 name="Savannah Vanwyk"))
    for j in db.query(DispatchJob):
        j.lat, j.lng = 26.05, -80.25
    zcall(db, "3055550501", ny(9, 12, 36), "Frederick is out of town but available Friday, "
          "Monday, or possibly late Thursday afternoon.")
    db.commit()
    out = ai.booking_options(db, _job(db, 687), FRI, hidden=set())
    days = {o["start"][:10] for o in out["options"]}
    # said on Fri 10/09: this Friday (10/09) is past, so Mon 10/12, Thu 10/15 or Fri 10/16
    assert days and days <= {"2026-10-12", "2026-10-15", "2026-10-16"}
    assert len(out["options"]) == 3
    assert all(o["fit"] in ("Great fit", "Good fit", "Detour") and o["why"] for o in
               out["options"])
    assert any("offered" in x for x in out["limits"])
    near = [o for o in out["options"] if o["start"].startswith("2026-10-16")
            and o["tech"] == "Antonio Brown"]
    assert not near or "#273" in near[0]["why"]               # already booked nearby that day


def test_a_hold_blocks_the_options_and_a_cancel_gets_new_dates(db):
    db.add(visit("h", 727, None, 0, board=c.INSPECTION_BOARD, status="Awaiting AHS Decision",
                 status_type="ESTIMATING", phones=["3055550503"], name="Henrietta Harrison"))
    db.add(visit("p", 318, None, 0, board=c.INSPECTION_BOARD, status="Reschedule Required",
                 status_type="ON_HOLD", phones=["3055550504"], name="Penny Berry"))
    db.add(visit("k", 299, None, 0, status="Reschedule Required", status_type="ON_HOLD",
                 phones=["3055550505"], name="Nidal Kalach"))
    zcall(db, "3055550503", ny(8, 16, 49), "Next step: [\"Put a hold on the roofing service\", "
          "\"Wait for insurance company inspection results before proceeding\"]")
    text(db, "3055550504", ny(8, 14, 35), "I can't make it, I won't be there tomorrow",
         out=False)
    zcall(db, "3055550505", ny(9, 10, 14), "After being placed on hold, Luis was informed that "
          "the person he was trying to reach was not available.")
    db.commit()
    held = ai.booking_options(db, _job(db, 727), FRI, hidden=set())
    assert held["options"] == [] and "hold" in held["note"]
    moved = ai.booking_options(db, _job(db, 318), FRI, hidden=set())
    assert moved["options"] and any("asked to move" in x for x in moved["limits"])
    phone = ai.booking_options(db, _job(db, 299), FRI, hidden=set())
    assert phone["options"]                                     # the phone line, not the job


def test_a_day_agreed_on_a_day_nobody_works_says_so(db):
    db.add(visit("s", 737, None, 0, status="Repair Scheduling Call", status_type="PRODUCTION",
                 phones=["3055550506"], name="Carol Rauseo-Danclair"))
    zcall(db, "3055550506", ny(9, 10, 16), "Marianne confirmed the appointment for Sunday "
          "between 11 AM and 3 PM.")
    db.commit()
    out = ai.booking_options(db, _job(db, 737), FRI, hidden=set())
    assert out["options"] == []
    assert "Sun 10/11" in out["note"] and "not a working day" in out["note"]


def test_slots_never_offer_a_day_the_customer_is_away_or_outside_their_hours():
    from app.dispatch import planner
    rules = planner.PlanJob(uid="x", number="1", customer="C", city=None, lat=26.0,
                            lng=-80.2, kind="repair")
    scheduling.apply_limits(rules, {"blocked": [{"from": "2026-10-12", "to": "2026-10-14"}],
                                    "after": "13:00"}, FRI)
    out = booking.slots(lat=26.0, lng=-80.2, kind="repair", visits=[], now=FRI, rules=rules,
                        days_ahead=8)
    assert out
    for s in out:
        d = c.local(s.start)
        assert not ("2026-10-12" <= d.date().isoformat() <= "2026-10-14")
        assert d.hour >= 13


def test_the_plan_leaves_out_and_flags_from_the_calls(db):
    # booked on the customer's other job -> left out
    db.add(visit("h1", 321, 3, 10, status="Call Back", status_type="FOLLOW_UP",
                 phones=["3055550507"], name="Nathaniel Haeems"))
    db.add(visit("h2", 720, 13, 9, status="Call Back", status_type="FOLLOW_UP",
                 phones=["3055550507"], name="Nathaniel Haeems"))
    # offered days -> planned only on those, and flagged
    db.add(visit("r", 687, None, 0, board=c.INSPECTION_BOARD, status="Reschedule Required",
                 status_type="ON_HOLD", phones=["3055550501"], name="Frederick Ryerson"))
    for j in db.query(DispatchJob):
        j.lat, j.lng = 26.05, -80.25
    zcall(db, "3055550501", ny(9, 12, 36), "Frederick is out of town but available Friday, "
          "Monday, or possibly late Thursday afternoon.")
    db.commit()
    out = scheduling.make_plan(db, FRI, days=12, store=False)
    left = {x["job_number"]: x["reason"] for x in out["left_out"]}
    assert "already booked" in left["321"]
    assert "687" in {x["job_number"] for x in out["flags"]}
    for d in out["days"]:
        for t in d["techs"]:
            for v in t["visits"]:
                if v.get("job_number") == "687":
                    assert v["start"][:10] in ("2026-10-12", "2026-10-15", "2026-10-16")
    small = scheduling.for_model(db, out, FRI)
    assert small["flagged_limits"] and small["left_out"]
    json.dumps(small, default=str)


def test_booked_visits_three_weeks_out_still_block_the_technician(db):
    db.add(visit("far", 900, 9, 9, who=("Antonio Brown",)))
    db.flush()
    j = _job(db, 900)
    j.scheduled_start = FRI + timedelta(days=20)
    j.scheduled_end = j.scheduled_start + timedelta(hours=2)
    db.commit()
    assert any(v.start == c.aware(j.scheduled_start)
               for v in ai.visits_for_booking(db, FRI, set()))


def test_the_chat_tool_returns_the_options_for_the_card(db):
    db.add(visit("r", 687, None, 0, board=c.INSPECTION_BOARD, status="Reschedule Required",
                 status_type="ON_HOLD", phones=["3055550501"], name="Frederick Ryerson"))
    db.commit()
    out = json.loads(ai.run_tool(db, "booking_options", {"job_number": "#687"}, FRI, set(), []))
    assert out["job_number"] == "687" and "options" in out
    assert json.loads(ai.run_tool(db, "find_slots", {"job_number": "687"}, FRI, set(), [])
                      )["job_number"] == "687"                     # the old name still works
    assert briefs.BOOK_DAYS >= 30

"""The week planner (2026-10-01): every job waiting for a visit fitted into the next business
days — close jobs on the same day, 4-5 a day per technician, Monday to Saturday, around what is
already booked, with the visit lengths the office really books. Pure; a fixed clock.
"""
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from app.dispatch import booking, planner
from app.dispatch import config as c

NOW = datetime(2026, 9, 30, 18, 0, tzinfo=UTC)          # Wed 2 PM New York
WESTON = (26.10, -80.40)
HOMESTEAD = (25.47, -80.48)
ANTONIO = {"name": "Antonio Brown", "does": ["repair", "inspection"], "prefers": "repair",
           "days": [0, 1, 2, 3, 4, 5], "start": "07:30", "end": "17:00", "max": 5,
           "home": [26.2173, -80.2259]}
OWEN = {"name": "Owen Buzaglo", "does": ["inspection", "repair"], "prefers": "inspection",
        "days": [0, 1, 2, 3, 4, 5], "start": "09:00", "end": "17:00", "max": 5, "home": None}
MINUTES = {"Antonio Brown": {"repair": 120, "inspection": 120},
           "Owen Buzaglo": {"repair": 120, "inspection": 60}}


def job(n, where, kind="repair", waited=1):
    return planner.PlanJob(uid="j%d" % n, number=str(n), customer="C%d" % n, city="X",
                           lat=where[0] + n * 0.001, lng=where[1], kind=kind,
                           waiting_since=NOW - timedelta(days=waited))


def placed(out):
    return {v["job_number"]: (d["date"], t["tech"]) for d in out["days"] for t in d["techs"]
            for v in t["visits"] if not v["existing"]}


def test_close_jobs_share_a_day():
    jobs = [job(1, WESTON, waited=9), job(2, HOMESTEAD, waited=8), job(3, WESTON, waited=7),
            job(4, HOMESTEAD, waited=6)]
    where = placed(planner.plan(jobs=jobs, booked=[], technicians=[ANTONIO], minutes=MINUTES,
                                now=NOW, days=6))
    assert where["1"][0] == where["3"][0]               # the two in Weston together
    assert where["2"][0] == where["4"][0]               # the two in Homestead together
    assert len(where) == 4


def test_never_more_than_the_daily_maximum_and_never_sunday():
    jobs = [job(n, WESTON) for n in range(1, 8)]
    tech = {**ANTONIO, "start": "07:00", "end": "20:00"}
    out = planner.plan(jobs=jobs, booked=[], technicians=[tech], minutes=MINUTES, now=NOW, days=6)
    for d in out["days"]:
        assert c.local(datetime.fromisoformat(d["date"] + "T12:00:00+00:00")).weekday() != 6
        for t in d["techs"]:
            assert t["count"] <= 5
    assert len(placed(out)) == 7


def test_a_day_holds_four_two_hour_repairs_not_five():
    """The owner's 4-5 a day: at 2 h a repair, 7:30-5 holds four plus driving."""
    jobs = [job(n, WESTON) for n in range(1, 6)]
    out = planner.plan(jobs=jobs, booked=[], technicians=[ANTONIO], minutes=MINUTES, now=NOW,
                       days=2)
    first = out["days"][0]["techs"][0]
    assert first["count"] == 4 and len(placed(out)) == 5


def test_booked_visits_stay_where_they_are_and_nothing_overlaps():
    day = datetime(2026, 10, 1, 10, 0, tzinfo=c.TZ)       # Thu 10:00-12:00 already booked
    booked = [booking.Visit("Antonio Brown", day, day + timedelta(hours=2), *WESTON, "#900 X")]
    out = planner.plan(jobs=[job(1, WESTON), job(2, WESTON)], booked=booked,
                       technicians=[ANTONIO], minutes=MINUTES, now=NOW, days=1)
    visits = out["days"][0]["techs"][0]["visits"]
    assert any(v["existing"] and v["start"] == day.isoformat() for v in visits)
    spans = sorted((datetime.fromisoformat(v["start"]), datetime.fromisoformat(v["end"]))
                   for v in visits)
    assert all(a[1] <= b[0] for a, b in pairwise(spans))


def test_inspections_go_to_owen_and_a_job_without_a_map_position_is_listed():
    nowhere = planner.PlanJob(uid="j9", number="9", customer="C9", city=None, lat=None,
                              lng=None, kind="repair")
    out = planner.plan(jobs=[job(1, WESTON, kind="inspection"), nowhere], booked=[],
                       technicians=[ANTONIO, OWEN], minutes=MINUTES, now=NOW, days=3)
    assert placed(out)["1"][1] == "Owen Buzaglo"
    assert [u["job_number"] for u in out["unplaced"]] == ["9"]
    assert "map position" in out["unplaced"][0]["reason"]


def test_visit_lengths_are_learned_from_history():
    rows = [("Owen Buzaglo", "inspection", m) for m in (55, 60, 62, 120)] + [
        ("Owen Buzaglo", "repair", 120), ("Antonio Brown", "repair", 1000)]
    assert planner.learned_minutes(rows) == {"Owen Buzaglo": {"inspection": 60}}


def test_a_day_that_starts_with_a_booked_visit_at_opening_still_takes_new_visits():
    """Live 2026-10-01: a 7:30 booked visit (with a drive from home before it) made the whole
    day "impossible", hiding the booked visits and wasting the free time after them."""
    first = datetime(2026, 10, 1, 7, 30, tzinfo=c.TZ)
    booked = [booking.Visit("Antonio Brown", first, first + timedelta(hours=2), *WESTON, "#271")]
    out = planner.plan(jobs=[job(1, WESTON)], booked=booked, technicians=[ANTONIO],
                       minutes=MINUTES, now=NOW, days=1)
    visits = out["days"][0]["techs"][0]["visits"]
    assert [v["existing"] for v in visits] == [True, False]
    assert datetime.fromisoformat(visits[1]["start"]) >= first + timedelta(hours=2)

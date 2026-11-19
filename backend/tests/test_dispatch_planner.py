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


# ---- the owner's rules (2026-11-19) -------------------------------------------------------------
# "prioritize for that day the customer that cannot do other day"; Antonio and Marx, 2 jobs a
# day; Marx does repairs only; Owen works only the days he says.

MARX = {"name": "Marx Test", "does": ["repair"], "prefers": "repair",
        "days": [0, 1, 2, 3, 4, 5], "start": "07:30", "end": "17:00", "max": 2, "home": None}
TWO = {**ANTONIO, "max": 2}


def test_a_customer_who_can_only_do_one_day_gets_it_before_flexible_jobs():
    # Two days (Thu, Fri), ONE visit a day: three customers, room for two. The two flexible
    # ones are next to Antonio's home, the one who can ONLY do Thursday is far away — on driving
    # alone the plan would take the two near ones and drop her. The owner's rule: she goes first.
    one = {**ANTONIO, "max": 1}
    flex = [job(1, WESTON, waited=9), job(2, WESTON, waited=8)]
    only = job(3, HOMESTEAD, waited=0)
    only.weekdays = frozenset({3})                          # Thursdays only
    out = planner.plan(jobs=[*flex, only], booked=[], technicians=[one], minutes=MINUTES,
                       now=NOW, days=2)
    where = placed(out)
    assert where["3"] == ("2026-10-01", "Antonio Brown")
    assert len(where) == 2 and where.keys() & {"1", "2"}    # one flexible one on Friday
    v = next(v for d in out["days"] for t in d["techs"] for v in t["visits"]
             if v["job_number"] == "3")
    assert v["one_day"] is True
    assert out["summary"]["one_day"] == {"placed": 1, "total": 1}
    left = next(u for u in out["unplaced"] if u["job_number"] in ("1", "2"))
    assert "No room" in left["reason"] and not left.get("one_day")


def test_a_full_one_day_is_named_when_the_customer_cannot_fit():
    booked = [booking.Visit("Antonio Brown", datetime(2026, 10, 1, 12, tzinfo=c.TZ),
                            datetime(2026, 10, 1, 14, tzinfo=c.TZ), *WESTON, "#600"),
              booking.Visit("Antonio Brown", datetime(2026, 10, 1, 14, 30, tzinfo=c.TZ),
                            datetime(2026, 10, 1, 16, tzinfo=c.TZ), *WESTON, "#601")]
    only = job(3, WESTON)
    only.not_before = datetime(2026, 10, 1).date()
    only.blocked = [(datetime(2026, 10, 2).date(), datetime(2026, 10, 31).date())]
    out = planner.plan(jobs=[only], booked=booked, technicians=[TWO], minutes=MINUTES, now=NOW,
                       days=6)
    why = next(u for u in out["unplaced"] if u["job_number"] == "3")["reason"]
    assert "can only do Thu 10/01" in why and "Antonio 2 of 2" in why


def test_an_on_call_technician_works_only_the_days_he_gave_and_a_day_off_is_kept_free():
    owen = {**OWEN, "max": 2, "on_call": True,
            "only": [{"from": "2026-10-02", "to": "2026-10-02", "max": 1}]}
    antonio = {**TWO, "off": [{"from": "2026-10-01", "to": "2026-10-01"}]}
    booked = [booking.Visit("Antonio Brown", datetime(2026, 10, 1, 9, tzinfo=c.TZ),
                            datetime(2026, 10, 1, 11, tzinfo=c.TZ), *WESTON, "#600 booked")]
    jobs = [job(n, WESTON, kind="inspection") for n in range(1, 5)]
    out = planner.plan(jobs=jobs, booked=booked, technicians=[antonio, owen], minutes=MINUTES,
                       now=NOW, days=3)
    cells = {(d["date"], t["tech"]): t for d in out["days"] for t in d["techs"]}
    # Owen: Friday only, and only ONE that day (what he said).
    assert {k[0] for k in cells if k[1] == "Owen Buzaglo"} == {"2026-10-02"}
    assert cells[("2026-10-02", "Owen Buzaglo")]["capacity"] == 1
    assert cells[("2026-10-02", "Owen Buzaglo")]["count"] <= 1
    # Antonio's day off still shows the visit already booked — and gets nothing new.
    off = cells[("2026-10-01", "Antonio Brown")]
    assert off["working"] is False
    assert [v["existing"] for v in off["visits"]] == [True]
    assert booking.works(owen, datetime(2026, 10, 5).date()) is False
    assert booking.day_max(owen, datetime(2026, 10, 2).date()) == 1


def test_a_repairs_only_technician_never_gets_an_inspection_and_booked_visits_count_toward_two():
    booked = [booking.Visit("Marx Test", datetime(2026, 10, 1, 8, tzinfo=c.TZ),
                            datetime(2026, 10, 1, 10, tzinfo=c.TZ), *WESTON, "#700 booked")]
    jobs = [job(n, WESTON, kind="inspection") for n in range(1, 4)] + \
        [job(n, WESTON, kind="repair") for n in range(4, 8)]
    out = planner.plan(jobs=jobs, booked=booked, technicians=[TWO, MARX],
                       minutes={**MINUTES, "Marx Test": {"repair": 120}}, now=NOW, days=2)
    for d in out["days"]:
        for t in d["techs"]:
            assert t["count"] <= 2
            if t["tech"] == "Marx Test":
                assert all(v["kind"] == "repair" for v in t["visits"])
    marx_thu = next(t for d in out["days"] for t in d["techs"]
                    if t["tech"] == "Marx Test" and d["date"] == "2026-10-01")
    assert sum(1 for v in marx_thu["visits"] if not v["existing"]) <= 1   # 1 booked + 1 new

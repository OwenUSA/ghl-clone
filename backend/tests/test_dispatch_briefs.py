"""What the scripts work out before any AI is asked (2026-10-13, app/dispatch/briefs.py).

The owner: "everything that doesn't need an agent we won't spend tokens on". Built against
the live Friday 10/09 that a person checked by hand, and the answer the CRM's assistant gave
for it (it moved jobs "On the Way" from an agreed time, with no one on the way). Pinned, by
behaviour:

  * **Signals** come from the words, English and Spanish, with their time: a "yes" answering
    our proposal is for the day the customer names, else the day we proposed; "cannot be
    present tomorrow" agrees to nothing; "a heads-up WHEN he is on his way" is not on the way;
    "if HE can't make it in time" is not the customer cancelling; "Ok, please call me" is a
    request, not a yes.
  * **The check** flags what Zuper needs, each with its evidence: double-booked (not two
    arrival windows in a row, not one customer's two jobs), a day the customer AGREED to that
    Zuper does not show (not a proposal nobody answered, not a sibling job's visit), on the
    way with the stage behind, a visit past its time with nothing from the field, a report sent
    to AHS with the column behind, the Technician field. A fix only where it is unambiguous.
  * **Test jobs are never checked**, and the check writes nothing.
  * **GET /api/dispatch/check** is the Dispatch audience's, and the chat's zuper_check /
    customers tools return the same reading compactly.
"""
import json
from datetime import UTC, datetime, timedelta

import pytest
from app.dispatch import ai, briefs
from app.dispatch import config as c
from app.main import app
from app.models import (
    Contact,
    Conversation,
    ConversationEvent,
    Direction,
    DispatchActivity,
    DispatchCall,
    DispatchItem,
    DispatchJob,
    DispatchSuggestion,
    EventType,
)
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.test_dispatch_api import people  # noqa: F401  (the four users and their tokens)


def ny(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=c.TZ).astimezone(UTC)


FRI = ny(9, 14, 48)            # Fri 10/09 2:48 PM New York


def visit(uid, number, day, hour, *, minute=0, who=("Owen Buzaglo",), status="Day-Before Call",
          board=c.REPAIR_BOARD, tech=None, phones=None, name="Jane Doe", minutes=120,
          status_type="PRODUCTION", address="1 Main St"):
    start = ny(day, hour, minute) if day else None
    return DispatchJob(job_uid=uid, job_number=str(number), board=board, status=status,
                       status_type=status_type, customer_name=name, phones=phones or [],
                       address=address, city="Miami", scheduled_start=start, is_open=True,
                       scheduled_end=start + timedelta(minutes=minutes) if start else None,
                       assigned=list(who) if who else None, technician=tech)


def text(db, phone, at, body, *, out):
    conv = db.scalar(select(Conversation.id).join(Contact, Contact.id == Conversation.contact_id)
                     .where(Contact.phone == "+1" + phone))
    if conv is None:
        con = Contact(first_name="C" + phone[-4:], last_name="", phone="+1" + phone)
        db.add(con)
        db.flush()
        cv = Conversation(contact_id=con.id)
        db.add(cv)
        db.flush()
        conv = cv.id
    db.add(ConversationEvent(conversation_id=conv, type=EventType.SMS, occurred_at=at, body=body,
                             direction=Direction.OUTBOUND if out else Direction.INBOUND,
                             source_system="OpenPhone"))
    db.flush()


def zcall(db, phone, at, summary, *, out=True, seconds=120, uid=None):
    db.add(DispatchCall(call_uid=uid or "z%s%s" % (phone, at.isoformat()), occurred_at=at,
                        direction="OUTGOING" if out else "INCOMING", status="COMPLETED",
                        duration_seconds=seconds, number=phone, staff_name="Luis Candiales",
                        summary=summary))


def found(out, kind=None, number=None):
    return [f for f in out["findings"] if (kind is None or f["kind"] == kind)
            and (number is None or f["job_number"] == str(number))]


# ---- signals ----------------------------------------------------------------------------------

def _sigs(db, phone):
    return [(s.kind, s.day.isoformat() if s.day else None, s.window)
            for b in briefs.load(db, FRI, set()) if phone in (b.job.phones or [])
            for s in b.sigs]


def test_a_yes_is_for_the_day_the_customer_names_else_the_day_we_proposed(db):
    """Live 10/09: our 8:40 text still mentioned Sunday; "Yes Today will work" is today."""
    db.add(visit("g", 716, 9, 9, board=c.INSPECTION_BOARD, status="Scheduled",
                 phones=["7862089222"]))
    text(db, "7862089222", ny(9, 8, 39), "Mr Owen can go to your address today, he can "
         "arrive in around an hour", out=True)
    text(db, "7862089222", ny(9, 8, 40), "otherwise we can still visit you on sunday like we "
         "agreed", out=True)
    text(db, "7862089222", ny(9, 9, 11), "Hi Yes Today will work. Thank you", out=False)
    db.commit()
    assert ("confirmed", "2026-10-09", None) in _sigs(db, "7862089222")


def test_words_that_only_look_like_signals_are_not(db):
    db.add(visit("m", 900, 9, 9, phones=["3055550101"]))
    p = "3055550101"
    text(db, p, ny(8, 18, 16), "This is a reminder of your appointment for tomorrow, Friday, "
         "between 9 and 11 AM. We'll give you a heads-up when our technician Mr Antonio is on "
         "his way", out=True)
    text(db, p, ny(8, 18, 20), "Ok c favor que me llame por whatsapp", out=False)
    text(db, p, ny(9, 9, 19), "If he can't make it in time, please let me know. I need to "
         "leave at 11", out=False)
    zcall(db, p, ny(9, 10, 0), "Mary cannot be present at her house tomorrow due to work.")
    zcall(db, p, ny(9, 10, 30), "Luis informed the customer the repair will be paid via Zelle "
          "after the repair is done.")
    db.commit()
    kinds = [k for k, _, _ in _sigs(db, p)]
    assert "on_the_way" not in kinds          # a heads-up WHEN he is on his way
    assert "confirmed" not in kinds           # "Ok, please call me" is a request
    assert "cancel" not in kinds              # "if HE can't make it" is about the technician
    assert "done" not in kinds                # "after the repair is done"
    assert ("promise", "2026-10-10", None) not in _sigs(db, p)   # "cannot be present tomorrow"


def test_real_signals_in_english_and_spanish(db):
    db.add(visit("o", 713, 9, 12, phones=["3058781755"]))
    db.add(visit("p", 721, 9, 7, minute=30, board=c.INSPECTION_BOARD,
                 status="Inspection Completed", status_type="ESTIMATING", phones=["9549931801"]))
    db.add(visit("r", 680, 9, 14, phones=["9546500183"], status="Repair Scheduling Call"))
    text(db, "3058781755", ny(9, 13, 12), "Nuestro técnico, el señor Antonio llegará en unos "
         "28 minutos con usted!", out=True)
    text(db, "9549931801", ny(9, 8, 44), "Ya hemos terminado la inspección en su casa y tenemos "
         "el reporte completo, lo subimos a American Home Shield", out=True)
    zcall(db, "9546500183", ny(6, 16, 39), "Luis scheduled a new appointment for Friday between "
          "2 PM and 4 PM, which Quentin accepted.")
    text(db, "9546500183", ny(5, 13, 20), "Let's cancel today", out=False)
    db.commit()
    assert ("on_the_way", None, None) in _sigs(db, "3058781755")
    s = _sigs(db, "9549931801")
    assert ("ahs_sent", None, None) in s and ("done", None, None) in s
    s = _sigs(db, "9546500183")
    assert ("confirmed", "2026-10-09", (14 * 60, 16 * 60)) in s
    assert ("cancel", None, None) in s


# ---- the check ---------------------------------------------------------------------------------

def test_double_booked_is_two_visits_starting_together_not_a_route(db):
    db.add_all([
        visit("z", 695, 9, 14, phones=["9542984854"], name="Ross Zelnick"),
        visit("q", 680, 9, 14, phones=["9546500183"], name="Quentin Rushin"),
        # 7:30-9:30 then 9-11: arrival windows in a row, one route
        visit("a", 721, 9, 7, minute=30, phones=["9549931801"]),
        visit("b", 716, 9, 9, phones=["7862089222"]),
        # one customer's two jobs at the same time: one visit
        visit("h1", 410, 9, 11, who=("Antonio Brown",), phones=["3055550199"]),
        visit("h2", 720, 9, 11, who=("Antonio Brown",), phones=["3055550199"]),
    ])
    db.commit()
    out = briefs.check(db, FRI)
    assert sorted(f["job_number"] for f in found(out, "double_booked")) == ["680", "695"]
    assert "Owen Buzaglo" in found(out, "double_booked", 695)[0]["what"]


def test_a_day_the_customer_agreed_to_that_zuper_does_not_show(db):
    db.add(visit("r", 680, None, 0, phones=["9546500183"], status="Repair Scheduling Call"))
    zcall(db, "9546500183", ny(8, 16, 39), "Luis scheduled a new appointment for tomorrow "
          "between 2 PM and 4 PM, which Quentin accepted.")
    # a proposal nobody answered is not an agreement
    db.add(visit("f", 703, None, 0, phones=["3055550123"], status="Repair Scheduling Call"))
    zcall(db, "3055550123", ny(9, 10, 0), "Owen offered an available appointment slot for "
          "tomorrow between 2 and 4 PM.")
    db.commit()
    out = briefs.check(db, FRI)
    [f] = found(out, "agreed_other_time")
    assert f["job_number"] == "680" and "no visit" in f["what"]
    assert f["evidence"][0]["said"].startswith("…") or "accepted" in f["evidence"][0]["said"]
    assert f["fix"] == {"action": "booking", "day": "2026-10-09",
                        "start": "2026-10-09T14:00:00-04:00", "end": "2026-10-09T16:00:00-04:00",
                        "label": "Fri 10/09 2:00 PM–4:00 PM", "technician": "Owen Buzaglo"}  # noqa: RUF001


def test_a_day_agreed_for_a_siblings_visit_is_not_this_jobs(db):
    """Live: Janeth Palacio has four jobs on one number; #721's confirmation landed on #323."""
    db.add(visit("p1", 721, 9, 7, minute=30, phones=["9549931801"]))
    db.add(visit("p2", 323, 1, 12, phones=["9549931801"], status="Call Back"))
    zcall(db, "9549931801", ny(8, 14, 58), "They confirmed the appointment for tomorrow between "
          "7:30 AM and 10:00 AM.")
    db.commit()
    assert not found(briefs.check(db, FRI), "agreed_other_time")


def test_on_the_way_done_and_ahs_with_the_stage_behind(db):
    db.add_all([
        visit("o", 713, 9, 12, who=("Antonio Brown",), phones=["3058781755"]),
        visit("g", 716, 9, 9, board=c.INSPECTION_BOARD, status="Scheduled",
              status_type="ESTIMATING", phones=["7862089222"]),
        visit("p", 721, 9, 7, minute=30, board=c.INSPECTION_BOARD,
              status="Inspection Completed", status_type="ESTIMATING", phones=["9549931801"]),
    ])
    text(db, "3058781755", ny(9, 13, 12), "Antonio llegará en unos 28 minutos con usted!",
         out=True)
    text(db, "7862089222", ny(9, 9, 12), "Mr Owen is on his way to you right now!", out=True)
    text(db, "9549931801", ny(9, 8, 44), "lo subimos a American Home Shield", out=True)
    db.commit()
    out = briefs.check(db, FRI)
    [o] = found(out, "on_the_way_not_moved", 713)
    assert o["fix"] == {"action": "stage", "to": "On the Way to Repair"}
    assert "llegará" in o["evidence"][0]["said"]
    # the inspection board's on-the-way stage is a technician's name: no fix, a sentence
    [g] = found(out, "on_the_way_not_moved", 716)
    assert g["fix"] is None
    # Owen's visit ended at 11 and nothing came from the field
    assert found(out, "no_field_update", 716)
    [a] = found(out, "ahs_sent_not_moved", 721)
    assert a["fix"] == {"action": "stage", "to": "Awaiting AHS Decision"}


def test_a_picture_or_an_app_action_is_an_update_from_the_field(db):
    j = visit("g", 716, 9, 9, board=c.INSPECTION_BOARD, status="Scheduled",
              status_type="ESTIMATING", phones=["7862089222"])
    db.add(j)
    db.add(DispatchActivity(activity_uid="a1", at=ny(9, 10, 5), user_name="Owen", job_uid="g",
                            job_number="716", via="zuper_v3_ios", message="added a new note"))
    db.commit()
    assert not found(briefs.check(db, FRI), "no_field_update")


def test_the_technician_field_and_a_visit_nobody_confirmed(db):
    db.add(visit("d", 690, 10, 9, tech="Antonio Brown", phones=["9546083874"]))
    db.commit()
    out = briefs.check(db, FRI)
    [t] = found(out, "technician_field", 690)
    assert t["fix"] == {"action": "field", "field": "Technician", "to": "Owen Buzaglo"}
    assert found(out, "not_confirmed", 690)


def test_test_jobs_are_never_checked_and_nothing_is_written(db):
    db.add(visit("t", 701, 9, 13, who=None, name="Santiago Villahermosa (TEST)",
                 address="TEST - 100 Test St"))
    db.add(visit("o", 734, 9, 13, name="Owen Test", phones=["5616905516"]))
    db.commit()
    before = (db.query(DispatchItem).count(), db.query(DispatchSuggestion).count(),
              db.query(DispatchJob).count())
    out = briefs.check(db, FRI)
    assert out["skipped_test_jobs"] == 2 and not out["findings"]
    assert before == (db.query(DispatchItem).count(), db.query(DispatchSuggestion).count(),
                      db.query(DispatchJob).count())


# ---- the page and the chat ---------------------------------------------------------------------

@pytest.mark.parametrize("who, code", [("admin", 200), ("dispatcher", 200), ("tech", 403),
                                       ("restricted", 403)])
def test_the_check_is_the_dispatch_audiences(db, people, who, code):  # noqa: F811
    cl = TestClient(app)
    r = cl.get("/api/dispatch/check", headers={"Authorization": "Bearer " + people[who][1]})
    assert r.status_code == code
    if code == 200:
        assert {"findings", "visits_checked", "jobs_checked", "counts"} <= set(r.json())
    bad = cl.get("/api/dispatch/check?date_from=someday",
                 headers={"Authorization": "Bearer " + people["admin"][1]})
    assert bad.status_code == 400


def test_the_chat_reads_the_same_briefs_in_one_look_up(db):
    db.add_all([visit("o", 713, 9, 12, who=("Antonio Brown",), phones=["3058781755"],
                      name="Enrique Ortega"),
                visit("z", 695, 9, 14, phones=["9542984854"], name="Ross Zelnick"),
                visit("q", 680, 9, 14, phones=["9546500183"], name="Quentin Rushin")])
    text(db, "3058781755", ny(9, 13, 12), "Antonio llegará en unos 28 minutos!", out=True)
    db.commit()
    check = json.loads(ai.run_tool(db, "zuper_check", {"date_from": "today"}, FRI, set(), []))
    assert {f["kind"] for f in check["findings"]} >= {"double_booked", "on_the_way_not_moved"}
    assert all("job_uid" not in f for f in check["findings"])
    one = json.loads(ai.run_tool(db, "customers", {"job_numbers": ["#713"]}, FRI, set(), []))
    [b] = one["briefs"]
    assert b["job"] == "#713" and b["signals_newest_first"][0]["signal"] == "on_the_way"
    assert json.loads(ai.run_tool(db, "customers", {}, FRI, set(), []))["briefs"] == []
    # a hidden board stays hidden
    hidden = json.loads(ai.run_tool(db, "customers", {"job_numbers": ["713"]}, FRI,
                                    {c.REPAIR_BOARD}, []))
    assert hidden["briefs"] == []

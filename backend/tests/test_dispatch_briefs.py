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


# ---- 2026-10-13, from the first answer on deepseek-flash ---------------------------------------

def test_a_confirmation_still_to_come_is_not_a_confirmation(db):
    """#703: the call ended "needs to call back or text to confirm the appointment"."""
    db.add(visit("f", 703, 9, 16, who=("Antonio Brown",), phones=["3055550123"]))
    zcall(db, "3055550123", ny(9, 11, 37), "Owen offered today between 2 and 4 PM. Next step: "
          "[\"Jacqueline needs to call back or text at earliest convenience to confirm "
          "appointment\"]")
    zcall(db, "3055550123", ny(9, 11, 50), "The visit is pending confirmation for today.",
          uid="z2")
    db.commit()
    assert "confirmed" not in [k for k, _, _ in _sigs(db, "3055550123")]
    assert found(briefs.check(db, FRI), "not_confirmed", 703)


def test_a_cancel_after_the_visit_day_still_says_reschedule(db):
    """#287 cancelled on Sunday's call and still sat in "On the Way to Repair" on Tuesday."""
    db.add(visit("a", 287, 7, 13, status="On the Way to Repair", status_type="ON_MY_WAY",
                 phones=["3055550150"]))
    zcall(db, "3055550150", ny(7, 9, 52), "Anna has an appointment scheduled today from 3 to "
          "5 PM but cannot make it and wants to reschedule.")
    db.commit()
    out = briefs.check(db, FRI)
    [f] = found(out, "customer_cancelled", 287)
    assert f["fix"] == {"action": "stage", "to": "Reschedule Required"}
    assert not found(out, "stale_on_the_way", 287)     # said once, as the cancel


def test_a_visit_on_an_invoiced_or_paid_job_is_flagged(db):
    db.add(visit("h1", 410, 10, 9, who=("Antonio Brown",), status="Invoice Submitted to AHS",
                 status_type="INVOICING", phones=["3055550199"]))
    db.add(visit("h2", 720, 10, 9, who=("Antonio Brown",), status="Call Back",
                 status_type="FOLLOW_UP", phones=["3055550199"]))
    db.commit()
    out = briefs.check(db, FRI)
    assert [f["job_number"] for f in found(out, "visit_on_closed_job")] == ["410"]


def test_a_visit_scheduled_on_a_call_is_agreed_even_with_the_time_to_come(db):
    """#735: "Owen scheduled a visit for the roof inspection on Wednesday of next week and will
    confirm the time one day before." The day is agreed; the time is not."""
    db.add(visit("b", 735, None, 0, board=c.RETAIL_BOARD, status="New Lead",
                 status_type="LEAD", phones=["9418077719"]))
    zcall(db, "9418077719", ny(9, 10, 46), "Owen scheduled a visit for the roof inspection on "
          "Wednesday of next week and will confirm the time one day before.")
    db.commit()
    [f] = found(briefs.check(db, FRI), "agreed_other_time", 735)
    assert "Wed 10/14" in f["what"] and f["fix"] is None       # no time said: no booking


def test_her_scheduled_appointment_is_a_description_not_an_agreement(db):
    db.add(visit("f", 703, 9, 16, who=("Antonio Brown",), phones=["3055550123"]))
    zcall(db, "3055550123", ny(9, 9, 54), "Luis asked her to say a good time to call back before "
          "her scheduled appointment to finalize the service details.")
    db.commit()
    assert found(briefs.check(db, FRI), "not_confirmed", 703)


# ---- checking the answer against what the look-ups returned (2026-10-13) ----------------------

CHECK_SAID = json.dumps({"findings": [
    {"job_number": "695", "stage": "Day-Before Call", "assigned": "Owen Buzaglo"},
    {"job_number": "711", "stage": "Repair In Process", "assigned": "Antonio Brown"},
    {"job_number": "739", "stage": "Inspection: Same-Day Confi", "assigned": "Owen Buzaglo"},
    {"job_number": "691", "stage": "Awaiting AHS Payment", "assigned": "Antonio Brown"}]})


def test_a_stage_copied_from_another_row_and_a_technicians_stage_are_flagged(db):
    """The live answer: #695 written as "Repair In Process" (#711's) and "Antonio On the Way"
    proposed for Owen's #739."""
    reply = "\n".join([
        "| Job | Customer | Zuper now | What to do |",
        "|---|---|---|---|",
        "| #695 | Ross Zelnick | Repair In Process, Owen | Ask Owen |",
        "| #711 | Robert Spray | Repair In Process, Antonio | Ask Antonio |",
        ("| #739 | Anissa Holmes | Inspection: Same-Day Confirm, Owen | move to Antonio On "
         "the Way |"),
        "| #691 | Katrina Phillip | Awaiting AHS Payment | Job done — paid; take the visit off |",
        "",
        ("#737 went from Welcome Call! straight to the repair board; move it to Inspection "
         "Completed once the report is in.")])
    out = ai.flag_contradictions(db, reply, [CHECK_SAID])
    notes = out[len(reply):]
    assert "#695: the answer says Zuper shows “Repair In Process”; Zuper shows “Day-Before Call”" \
        in notes
    assert "#739 is Owen Buzaglo's job; “Antonio On the Way” is Antonio's own stage" in notes
    # a right row, a word like "paid", a stage to move to, the stage history: never flagged
    assert "#711" not in notes and "#691" not in notes and "#737" not in notes
    assert ai.flag_contradictions(db, "| Job | Zuper now |\n|---|---|\n| #711 | Repair In "
                                  "Process |", [CHECK_SAID]).endswith("Process |")


def test_every_look_up_is_kept_whole_and_the_zuper_link_is_the_new_app(monkeypatch):
    from app.zuper import config as zc
    assert ai.STEP_RESULT_MAX == ai.TOOL_RESULT_MAX
    monkeypatch.delenv("ZUPER_JOB_URL_TEMPLATE", raising=False)
    assert zc.job_url_template() == "https://next.zuperpro.com/jobs/{uid}/details"


# ---- 2026-10-13, checked against live Zuper -------------------------------------------------

def test_a_visit_under_way_past_its_window_is_running_late_not_silent(db):
    """#711: truck trouble, the office moved it to Repair In Process at 12:56 PM, calls about
    the plywood and a $441 deposit — nothing from Antonio's app, but not silent."""
    j = visit("s", 711, 9, 9, who=("Antonio Brown",), status="Repair In Process",
              status_type="STARTED", phones=["3055550160"])
    j.status_since = ny(9, 12, 56)
    db.add(j)
    zcall(db, "3055550160", ny(9, 13, 17), "Luis called Robert to offer a discounted price; "
          "Robert to pay $441 upfront deposit.")
    db.commit()
    out = briefs.check(db, FRI)
    assert not found(out, "no_field_update", 711)
    [f] = found(out, "running_late", 711)
    assert "1:17 PM" in f["what"] and "deposit" in f["evidence"][0]["said"]


def test_days_the_customer_offered_are_options_not_an_agreement(db):
    """#687: "out of town but available Friday, Monday, or possibly late Thursday" and "call
    back with confirmed appointment date and time" — nothing agreed."""
    db.add(visit("r", 687, None, 0, board=c.INSPECTION_BOARD, status="Scheduled",
                 status_type="ESTIMATING", phones=["3055550170"]))
    zcall(db, "3055550170", ny(9, 12, 36), "Frederick is out of town but available Friday, "
          "Monday, or possibly late Thursday afternoon. Next step: [\"Call back with confirmed "
          "appointment date and time\"]")
    db.commit()
    out = briefs.check(db, FRI)
    assert not found(out, "agreed_other_time", 687)
    [f] = found(out, "customer_options", 687)
    assert "Mon 10/12" in f["what"] and "Thu 10/15" in f["what"]


def test_a_stage_left_in_progress_with_notes_after_it_looks_done(db):
    j = visit("f", 736, 8, 14, minute=30, board=c.INSPECTION_BOARD,
              status="Inspection In Progress", status_type="STARTED")
    j.status_since, j.last_note_at = ny(8, 16, 23), ny(8, 16, 49)
    db.add(j)
    db.commit()
    [f] = found(briefs.check(db, FRI), "stale_on_the_way", 736)
    assert "looks done" in f["what"] and "Inspection Completed" in f["do"]


# ---- the office's day plan, judged by script (2026-10-13) ---------------------------------------

def test_each_visit_on_the_day_plan_gets_its_verdict_from_the_calls(db):
    """Customer_List (12): the AI left out the visits Zuper did not have. Each now comes judged:
    agreed → book; nothing said → call first; the calls say another day Zuper has → the Excel
    is wrong; on hold → do not book; the same visit → matches."""
    from app.dispatch import lookups
    db.add_all([
        visit("w", 656, None, 0, who=("Antonio Brown",), status="Reschedule Required",
              status_type="ON_HOLD", phones=["3055550201"], name="Elisha Wright"),
        visit("m", 704, None, 0, status="AHS Approved", status_type="SOLD",
              phones=["3055550202"], name="Rose Marie McLean"),
        visit("k1", 707, None, 0, status="Awaiting AHS Decision", status_type="ESTIMATING",
              phones=["3055550203"], name="Pankaj Kasabwala"),
        visit("k2", 627, 16, 14, minute=30, phones=["3055550203"], name="Pankaj Kasabwala"),
        visit("h", 727, None, 0, status="Awaiting AHS Decision", status_type="ESTIMATING",
              phones=["3055550204"], name="Henrietta Harrison"),
        visit("z", 695, 10, 9, phones=["3055550205"], name="Ross Zelnick"),
    ])
    zcall(db, "3055550201", ny(9, 11, 57), "They confirmed a Wednesday morning appointment "
          "between 7:30-11:30 AM.")
    zcall(db, "3055550203", ny(9, 13, 36), "Owen confirmed that they are good for next Friday.")
    zcall(db, "3055550204", ny(9, 12, 49), "Next step: [\"Put a hold on the roofing service\", "
          "\"Wait for insurance company inspection results before proceeding\"]")
    db.commit()
    rows = [("Wed 10/14", "Owen", "7:30–11:30 AM", "Elisha Wright"),  # noqa: RUF001
            ("Wed 10/14", "Antonio", "12:00–4:00 PM", "Rose Marie McLean"),  # noqa: RUF001
            ("Wed 10/14", "Owen", "2:30–6:30 PM", "Pankaj Kasabwala"),  # noqa: RUF001
            ("Fri 10/16", "Owen", "7:30–9:30 AM", "Henrietta Harrison"),  # noqa: RUF001
            ("Sat 10/10", "Owen", "9:00–11:00 AM", "Ross Zelnick")]  # noqa: RUF001
    sheets = [{"name": "Schedule", "columns": ["Day", "Tech", "Window", "Customer"], "rows": [
        {"row": i + 2, "values": dict(zip(("Day", "Tech", "Window", "Customer"), r,
                                          strict=True))} for i, r in enumerate(rows)]}]
    out = briefs.day_plan_verdicts(db, FRI, lookups.compare_schedule(
        db, FRI, set(), sheets, date_from="today", date_to="2026-10-17"))
    kinds = {v["file"]["customer"]: v["verdict"]["kind"] for v in out["visits"]}
    assert kinds == {"Elisha Wright": "book", "Rose Marie McLean": "call_first",
                     "Pankaj Kasabwala": "file_wrong", "Henrietta Harrison": "on_hold",
                     "Ross Zelnick": "matches"}
    wright = next(v for v in out["visits"] if v["file"]["customer"] == "Elisha Wright")
    tech = wright["verdict"]["technician"]
    assert "Owen" in tech and "Antonio" in tech
    assert out["by_verdict"][0]["verdict"].startswith("Agreed, not in Zuper")
    assert "Report EVERY group" in out["how_to_judge"]


# ---- who still needs a booking, judged by script (2026-10-13) ----------------------------------

def test_to_book_judges_every_waiting_job_from_its_calls(db):
    """The AI's answer put Haeems' return visit on the wrong one of his three jobs, said "book"
    for visits that had happened, and called September imports "nobody has called"."""
    db.add_all([
        # agreed on a call, no visit in Zuper -> book
        visit("b", 735, None, 0, board=c.RETAIL_BOARD, status="New Lead", status_type="LEAD",
              phones=["3055550301"], name="Robert Burwell"),
        # the customer's other job holds the visit -> nothing to book here
        visit("h1", 321, 3, 10, status="Call Back", status_type="FOLLOW_UP",
              phones=["3055550302"], name="Nathaniel Haeems"),
        visit("h2", 720, 14, 9, status="Call Back", status_type="FOLLOW_UP",
              phones=["3055550302"], name="Nathaniel Haeems"),
        # its visit passed, and the technician was on the way that day -> it happened
        visit("l", 683, 8, 14, status="Call Back", status_type="FOLLOW_UP",
              phones=["3055550303"], name="Thompson Laguerre"),
        # nothing at all in 60 days -> check it is still a job
        visit("o", 334, None, 0, board=c.RETAIL_BOARD, status="New Lead", status_type="LEAD",
              name="David Jackson"),
    ])
    for j in db.query(DispatchJob).filter(DispatchJob.job_uid.in_(("l", "o"))):
        j.status_since = ny(8, 10) if j.job_uid == "l" else ny(9, 9) - timedelta(days=40)
    zcall(db, "3055550301", ny(9, 10, 46), "Owen scheduled a visit for the roof inspection on "
          "Wednesday of next week and will confirm the time one day before.")
    zcall(db, "3055550303", ny(8, 13, 17), "Antonio Brown called Thompson to inform him he's on "
          "his way to inspect the roof.")
    db.commit()
    out = briefs.to_book(db, FRI)
    verdicts = {r["job_number"]: g["verdict"] for g in out["groups"] for r in g["jobs"]}
    assert verdicts["735"] == "book"
    assert verdicts["321"] == "on_other_job"
    assert verdicts["683"] == "visit_happened"
    assert verdicts["334"] == "stale"
    assert "720" not in verdicts                       # it has its visit: not waiting
    assert out["groups"][0]["verdict"] == "book"


# ---- the whole pipeline, mapped (2026-10-14) ----------------------------------------------------

def test_the_pipeline_map_lists_every_job_with_where_it_should_go(db):
    """The AI's answer counted 59 of 129 AHS jobs and missed every job stuck after its visit.
    Every job is a row; a job stuck after its visit says where it goes next; follow-ups are a
    person's call, not a Zuper change; waiting jobs are counted, not hidden."""
    stuck = visit("rc", 288, 1, 10, status="Repair Complete", status_type="COMPLETED",
                  phones=["3055550401"], name="Gustavo Marquez")
    stuck.status_since = FRI - timedelta(days=14)
    waiting = visit("wc", 413, None, 0, board=c.INSPECTION_BOARD, status="Waiting for Customer",
                    status_type="ON_HOLD", phones=["3055550402"], name="Joseph Pomerantz")
    waiting.status_since = FRI - timedelta(days=16)
    paid = visit("iv", 291, None, 0, status="Invoice Submitted to AHS", status_type="INVOICING",
                 phones=["3055550403"], name="Miguel Alvarado")
    paid.status_since = FRI - timedelta(days=5)
    sent = visit("p", 721, 9, 7, minute=30, board=c.INSPECTION_BOARD,
                 status="Reschedule Required", status_type="ON_HOLD", phones=["9549931801"])
    sent.status_since = FRI - timedelta(hours=2)
    db.add_all([stuck, waiting, paid, sent])
    text(db, "9549931801", ny(9, 8, 44), "lo subimos a American Home Shield", out=True)
    db.commit()
    out = briefs.pipeline_map(db, FRI)
    rows = {r["job_number"]: r for b in out["boards"] for col in b["columns"]
            for r in col["jobs"]}
    assert set(rows) == {"288", "413", "291", "721"}                  # every job, a row each
    assert rows["288"]["state"] == "update"
    assert rows["288"]["should"] == "Call Satisfaction Check"
    assert rows["413"]["state"] == "call" and "follow up" in rows["413"]["should"]
    assert rows["291"]["state"] == "waiting"
    assert rows["721"]["state"] == "update" and rows["721"]["should"] == "Awaiting AHS Decision"
    repair = next(b for b in out["boards"] if b["board"] == c.REPAIR_BOARD)
    assert repair["total"] == 2 and repair["by_state"]["update"] == 1
    # the chat's copy: every update / call row in full, the rest as job numbers
    small = briefs.pipeline_for_model(out)
    cols = {col["stage"]: col for b in small["boards"] for col in b["columns"]}
    assert cols["Invoice Submitted to AHS"]["waiting"] == ["#291"]
    assert cols["Repair Complete"]["act"][0]["should"] == "Call Satisfaction Check"


# ---- one customer, everything (2026-10-15) --------------------------------------------------

def test_a_customer_profile_has_every_job_every_number_and_the_flags(db):
    """"Tell me all the info about Anita McGruder": the AI made 14 look-ups (four of them the
    same conversation) and missed her son's line. One look-up gathers everything."""
    db.add_all([
        visit("a1", 355, 3, 7, status="Call Back", status_type="FOLLOW_UP",
              phones=["3053103701"], name="ANITA MCGRUDER"),
        visit("a2", 722, 7, 7, minute=30, status="Repair Complete", status_type="COMPLETED",
              phones=["3053103701"], name="Anita Mcgruder"),
    ])
    text(db, "3053103701", ny(7, 9, 1), "What is the arrival time?", out=False)
    zcall(db, "3053103701", ny(5, 9, 54), "Owen advised Anita to call American Home Shield "
          "regarding the new leak. The original leak is still under warranty.")
    zcall(db, "9413107677", ny(5, 10, 0), "Luis gave our number 941-310-7677.", uid="ours")
    # her son, saved in Quo under her name
    from app.models import Contact, Conversation, ConversationEvent, Direction, EventType
    son = Contact(first_name="Basil Binns (Anita Mcgruder) SON", last_name="",
                  phone="+13052988799")
    db.add(son)
    db.flush()
    conv = Conversation(contact_id=son.id)
    db.add(conv)
    db.flush()
    db.add(ConversationEvent(conversation_id=conv.id, type=EventType.SMS, occurred_at=ny(6, 7, 22),
                             direction=Direction.INBOUND, source_system="OpenPhone",
                             body="Yesterday's roof repair is still leaking."))
    db.commit()
    out = briefs.customer_profile(db, FRI, query="anita mcgruder")
    assert {x["job"] for x in out["jobs"]} == {"#355", "#722"}
    assert any(x.startswith("3052988799") and "SON" in x for x in out["family_or_other_numbers"])
    kinds = {f["flag"] for f in out["flags"]}
    assert {"new AHS claim", "warranty", "complaint"} <= kinds
    assert any("still leaking" in t["said"] for t in out["timeline_oldest_first"])
    assert out["last_standing"]["last_contact"].startswith("Wed 10/07 9:01 AM")
    assert "LAST STANDING" in out["how_to_write"]
    # the same customer by job number; our own line is never theirs
    assert {x["job"] for x in briefs.customer_profile(db, FRI, query="#722")["jobs"]} == {
        "#355", "#722"}
    assert "error" in briefs.customer_profile(db, FRI, query="nobody like this")


def test_a_flagged_call_reaches_the_ai_whole(db):
    """John Dahdouh, 10/15: the Zuper Connect summary was cut at 300 characters, right before
    "proposing to pay $450 instead of $675" — the AI reported $675 and missed the counter-offer."""
    db.add(visit("d1", 738, 9, 8, status="Day-Before Call", status_type="NEW",
                 phones=["3057945221"], name="John Dahdouh"))
    zcall(db, "3057945221", ny(8, 9, 25), "Luis Candiales from Dream Team Roofing called John "
          "Dahdouh to schedule a roof repair appointment for today between 2:00 PM and 4:00 PM "
          "due to a cancellation. The conversation shifted to discussing the non-covered costs, "
          "where the insurance covers $1,125 and there's an additional $675 out-of-pocket charge. "
          "John expressed concern that the cost is high and offered to negotiate, proposing to "
          "pay $450 instead of $675 (having already paid $125 for the service call). Luis agreed "
          "to have Owen call John to discuss the pricing.")
    db.commit()
    out = briefs.customer_profile(db, FRI, query="dahdouh")
    money = [f for f in out["flags"] if f["flag"] == "money"]
    assert money and "$450" in money[0]["said"] and "$675" in money[0]["said"]
    assert any("$450" in t["said"] for t in out["timeline_oldest_first"])
    assert "counter-offer" in out["how_to_write"]

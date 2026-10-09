"""The Dispatch assistant reads like the office (2026-10-08).

Built from a working session that answered "which visits this week are not right in Zuper",
"did the customer really confirm", "who changed what" and "where is the technician". What is
pinned, by behaviour:

  * **The pass keeps what the assistant reads**: each job note's TEXT, and Zuper's activity log
    with where each line came from. A line our own scripts wrote (Zuper's `API_KEY` source) is
    marked automatic: Zuper shows it under the key owner's name, and the assistant must never
    say that person did it. Still GET only; a second pass adds nothing it already has.
  * **day_schedule** lists a day's visits per technician and says what is wrong with each.
  * **search_comms** finds any number (a relative's too), cuts transcripts unless the full text
    is asked for, and leaves out a customer whose only jobs are on a hidden board.
  * **activity_log** leaves our scripts out by default and names every deletion.
  * **tech_day** rebuilds a technician's day and says there is no live location.
  * **compare_schedule** reads an Excel's dated visits and names every difference with Zuper.
  * **DeepSeek** is a connection type of its own, on DeepSeek's address, which no test can
    reach; a thinking model's reasoning is sent back inside a tool loop, and dropped once if
    refused.
  * **The chat** runs the new tools and stops looking things up once it has read enough.
"""
import json
from datetime import UTC, datetime, timedelta

import pytest
from app.ai import providers, vault
from app.dispatch import ai, lookups, service
from app.dispatch import config as c
from app.models import (
    AiConnection,
    Contact,
    Conversation,
    ConversationEvent,
    Direction,
    DispatchActivity,
    DispatchCall,
    DispatchJob,
    DispatchNote,
    EventType,
    NumberThread,
    NumberThreadEvent,
)
from tests.ai_guard import provider_host
from tests.ai_support import openai_call, openai_completion
from tests.test_dispatch_api import iso, job, zuper  # noqa: F401  (the pass's Zuper fake)

NOW = datetime(2026, 10, 8, 18, 0, tzinfo=UTC)          # Thu 2 PM New York


def ny(day: int, hour: int, minute: int = 0) -> datetime:
    """A New York time in October 2026, as UTC: SQLite drops an offset (CLAUDE.md), so the
    app stores UTC and so do these rows."""
    return datetime(2026, 10, day, hour, minute, tzinfo=c.TZ).astimezone(UTC)


def activity(uid, at, who, message, *, source, job_uid=None, kind="UPDATE", module="JOB"):
    return {"user_activity_uid": uid, "created_at": iso(at), "activity_type": kind,
            "activity_module": module, "activity_message": message,
            "activity_action_uid": job_uid,
            "users": {"user_uid": "u-" + who.lower(), "first_name": who, "last_name": "X"},
            "metadata": {"request_source": "{'type': '%s', 'version': '1'}" % source}}


# ---- the pass ---------------------------------------------------------------------------------

def test_a_pass_keeps_note_text_and_the_activity_log_with_its_source(db, zuper):  # noqa: F811
    zuper.jobs = [job("j1", 724, c.INSPECTION_BOARD, "Scheduled",
                      created=NOW - timedelta(days=3), updated=NOW - timedelta(minutes=5))]
    zuper.notes = {"j1": [{"note_uid": "n1", "note_type": "TEXT", "created_at": iso(NOW),
                           "created_by": {"first_name": "Owen", "last_name": "Buzaglo"},
                           "note": "<p>The leak is in the <b>valley</b>.</p><p>Flashing cracked"
                                   "&nbsp;all over.</p>"}]}
    zuper.activities = [
        activity("a3", NOW, "Luis", "deleted Job 712 Tracey-Ann Lennon", source="WEB_APP",
                 kind="DELETE", job_uid="gone-712"),
        activity("a2", NOW - timedelta(minutes=1), "Owen",
                 'updated the job route "Owen Buzaglo - Thu Oct 8"', source="API_KEY",
                 module="ROUTE"),
        activity("a1", NOW - timedelta(minutes=2), "Owen", "updated status to Inspection In "
                 "Progress for Job Man Yan Chang", source="zuper_v3_android", job_uid="j1"),
    ]
    counts = service.run(db, NOW)
    assert "error" not in counts and counts["activity"] == 3, counts
    note = db.query(DispatchNote).one()
    assert "leak is in the valley" in note.text.replace("  ", " ")
    assert "<" not in note.text and "&nbsp;" not in note.text and "cracked all over" in note.text
    assert note.by_name == "Owen Buzaglo" and note.job_number == "724"
    rows = {a.activity_uid: a for a in db.query(DispatchActivity)}
    assert rows["a2"].automatic is True and rows["a2"].via == "API_KEY"
    assert rows["a1"].automatic is False and rows["a1"].via == "zuper_v3_android"
    assert rows["a1"].job_number == "724" and rows["a3"].activity_type == "DELETE"
    # Reads only (the Connect call search is the one POST, and it is a read).
    assert all(m == "GET" for m, path in zuper.requests if "connect" not in path)
    # The next pass stops at the first line it already has and adds nothing.
    again = service.run(db, NOW + timedelta(minutes=3))
    assert again["activity"] == 0
    assert db.query(DispatchActivity).count() == 3


# ---- the schedule -------------------------------------------------------------------------------

def visit(uid, number, day, hour, *, minute=0, who=("Owen Buzaglo",), status="Day-Before Call",
          board=c.REPAIR_BOARD, tech=None, phones=None, name="Jane Doe", open_=True, minutes=120,
          address="1 Main St", city="Miami"):
    return DispatchJob(job_uid=uid, job_number=str(number), board=board, status=status,
                       customer_name=name, phones=phones or [], address=address, city=city,
                       scheduled_start=ny(day, hour, minute), is_open=open_,
                       scheduled_end=ny(day, hour, minute) + timedelta(minutes=minutes),
                       assigned=list(who) if who else None, technician=tech)


def test_day_schedule_lists_a_days_visits_and_what_is_wrong_with_each(db):
    db.add_all([
        visit("a", 481, 8, 11, tech="Antonio Brown"),
        visit("b", 674, 8, 16, status="Reschedule Required", board=c.INSPECTION_BOARD),
        visit("c", 710, 8, 10, who=()),
        visit("d", 690, 9, 7),                                     # another day
        visit("e", 269, 8, 7, who=("Antonio Brown",), status="Repair In Process"),
    ])
    db.commit()
    out = lookups.day_schedule(db, NOW, set(), date_from="today")
    day = out["days"][0]
    assert day["day"] == "Thu 10/08" and len(out["days"]) == 1
    by_tech = {t["technician"]: t["visits"] for t in day["technicians"]}
    assert [v["job_number"] for v in by_tech["Owen Buzaglo"]] == ["481", "674"]
    checks = {v["job_number"]: v["check"] for v in by_tech["Owen Buzaglo"]}
    assert "Antonio Brown" in checks["481"][0]                       # field says someone else
    assert "not a booked-visit stage" in checks["674"][0]
    assert "on no route" in by_tech["(nobody assigned)"][0]["check"][0]
    assert by_tech["Antonio Brown"][0]["check"] is None
    only = lookups.day_schedule(db, NOW, set(), date_from="10/08", technician="antonio")
    assert [v["job_number"] for t in only["days"][0]["technicians"] for v in t["visits"]] \
        == ["269"]
    # A hidden board's visits are left out: only the Inspection board's #674 remains.
    hid = lookups.day_schedule(db, NOW, {c.REPAIR_BOARD}, date_from="today")
    assert [v["job_number"] for t in hid["days"][0]["technicians"]
            for v in t["visits"]] == ["674"]


# ---- calls and texts ----------------------------------------------------------------------------

def _thread(db, phone, name="Sebastian"):
    con = Contact(first_name=name, last_name="", phone="+1" + phone)
    db.add(con)
    db.flush()
    conv = Conversation(contact_id=con.id)
    db.add(conv)
    db.flush()
    return conv


def test_search_comms_finds_any_number_and_cuts_transcripts_unless_asked(db):
    long_talk = "+19549147244: Hello. " + "blah " * 300 + "+19547018639: after 3 PM works."
    son = _thread(db, "9547018639")
    db.add(ConversationEvent(conversation_id=son.id, type=EventType.CALL,
                             direction=Direction.INBOUND, occurred_at=NOW - timedelta(hours=20),
                             call_status="completed", duration_seconds=196,
                             transcript=long_talk, source_system="OpenPhone", body="call"))
    nt = NumberThread(phone="+13055550123", phone_key="3055550123")
    db.add(nt)
    db.flush()
    db.add(NumberThreadEvent(number_thread_id=nt.id, type=EventType.SMS,
                             direction=Direction.INBOUND, occurred_at=NOW - timedelta(hours=1),
                             body="Can I reschedule please?"))
    db.add(DispatchCall(call_uid="z1", occurred_at=NOW - timedelta(hours=2), direction="OUTGOING",
                        status="COMPLETED", duration_seconds=46, number="3055550123",
                        staff_name="Luis Candiales", summary="Customer agreed to come now."))
    db.add(DispatchJob(job_uid="h", job_number="900", board=c.RETAIL_BOARD, status="New Lead",
                       phones=["3055550123"], customer_name="Hidden Person", is_open=True))
    db.commit()
    short = lookups.search_comms(db, NOW, set(), phone="(954) 701-8639")
    item = short["items_oldest_first"][0]
    assert item["source"] == "Quo" and item["from"] == "customer"
    # Cut: the start AND the end — a call's agreement is at its end (live 2026-10-08).
    short_tr = item["transcript"]
    assert len(short_tr) < 1000 and " … " in short_tr
    assert short_tr.startswith("+19549147244: Hello.") and short_tr.endswith("after 3 PM works.")
    full = lookups.search_comms(db, NOW, set(), phone="9547018639", full=True)
    assert len(full["items_oldest_first"][0]["transcript"]) > 1500
    words = lookups.search_comms(db, NOW, set(), text="reschedule")
    assert [i["text"] for i in words["items_oldest_first"]] == ["Can I reschedule please?"]
    both = lookups.search_comms(db, NOW, set(), phone="3055550123")
    assert [i["source"] for i in both["items_oldest_first"]] == ["Zuper Connect", "CRM line"]
    # The customer's only job is on a board hidden from this person: nothing about them.
    assert lookups.search_comms(db, NOW, {c.RETAIL_BOARD}, phone="3055550123")["found"] == 0


# ---- Zuper's log --------------------------------------------------------------------------------

def _act(db, uid, at, who, message, *, via, automatic=False, kind="UPDATE", job_uid=None,
         number=None):
    db.add(DispatchActivity(activity_uid=uid, at=at, user_name=who, activity_type=kind,
                            module="JOB", message=message, via=via, automatic=automatic,
                            job_uid=job_uid, job_number=number))


def test_activity_log_leaves_scripts_out_and_names_every_deletion(db):
    db.add(DispatchJob(job_uid="r", job_number="901", board=c.RETAIL_BOARD, status="New Lead",
                       is_open=True))
    _act(db, "1", NOW - timedelta(hours=4), "Luis Candiales", "deleted Job 712 Lennon",
         via="WEB_APP", kind="DELETE", job_uid="gone")
    _act(db, "2", NOW - timedelta(hours=3), "Owen Buzaglo", 'updated the job route "Owen"',
         via="API_KEY", automatic=True)
    _act(db, "3", NOW - timedelta(hours=2), "Antonio Brown", "updated status to Review Received",
         via="zuper_v3_ios")
    _act(db, "4", NOW - timedelta(hours=1), "Luis Candiales", "rescheduled a retail job",
         via="WEB_APP", job_uid="r", number="901")
    _act(db, "5", NOW - timedelta(days=1), "Luis Candiales", "yesterday", via="WEB_APP")
    db.commit()
    out = lookups.activity_log(db, NOW, {c.RETAIL_BOARD})
    assert [x["what"] for x in out["log_oldest_first"]] == [
        "deleted Job 712 Lennon", "updated status to Review Received"]
    assert out["deletions"][0]["who"] == "Luis Candiales"
    assert out["log_oldest_first"][1]["via"] == "field app (iPhone)"
    scripts = lookups.activity_log(db, NOW, set(), include_scripts=True, person="owen")
    assert len(scripts["log_oldest_first"]) == 1
    assert "OUR SCRIPT" in scripts["log_oldest_first"][0]["via"]
    assert [x["what"] for x in lookups.activity_log(db, NOW, set(), day="yesterday")[
        "log_oldest_first"]] == ["yesterday"]


# ---- a technician's day -------------------------------------------------------------------------

def test_tech_day_rebuilds_the_day_and_says_there_is_no_live_location(db):
    db.add_all([visit("m", 710, 8, 10, name="Lindsay Marin", address="19918 SW 7th Pl",
                      city="Pembroke Pines", phones=["9549077271"]),
                visit("f", 481, 8, 11, name="Christophe Frochaux", phones=["3059654559"])])
    db.add(DispatchNote(note_uid="n", job_uid="m", job_number="710", created_at=ny(8, 12, 4),
                        by_name="Owen Buzaglo", text="The issue is in the valley."))
    _act(db, "a", ny(8, 11, 21), "Owen Buzaglo", "updated status to Inspection In Progress",
         via="zuper_v3_android", job_uid="m", number="710")
    _act(db, "s", ny(8, 11, 22), "Owen Buzaglo", "updated the job route", via="API_KEY",
         automatic=True)
    db.add(DispatchCall(call_uid="c1", occurred_at=ny(8, 9, 46), direction="OUTGOING",
                        status="COMPLETED", duration_seconds=26, number="3059654559",
                        staff_name="Owen Buzaglo", summary="Could not reach the customer."))
    db.commit()
    out = lookups.tech_day(db, NOW, set(), technician="Owen")
    assert [v["job_number"] for v in out["visits_booked"]] == ["710", "481"]
    whats = [e["what"] for e in out["timeline"]]
    assert whats[0].startswith("Zuper Connect call outgoing")
    assert whats[1] == "updated status to Inspection In Progress"
    assert whats[2].startswith("note: The issue is in the valley")
    assert not any("route" in w for w in whats)                    # our script, not Owen
    assert out["last_place_with_evidence"]["address"] == "19918 SW 7th Pl, Pembroke Pines"
    assert "no live location" in out["note"]


# ---- the Excel against Zuper --------------------------------------------------------------------

def test_compare_schedule_reads_dated_rows_and_names_every_difference(db):
    db.add_all([
        visit("v1", 481, 8, 10, name="Christophe Frochaux", phones=["3059654559"]),
        visit("v2", 724, 8, 7, minute=30, name="Man Yan Chang", board=c.INSPECTION_BOARD,
              status="Scheduled", phones=["2019200441"]),
        DispatchJob(job_uid="v3", job_number="273", board=c.REPAIR_BOARD,
                    status="Waiting for Customer", customer_name="Savannah Vanwyk",
                    is_open=True, assigned=["Antonio Brown"], scheduled_start=ny(2, 10),
                    scheduled_end=ny(2, 12)),
        visit("v4", 999, 9, 8, name="Somebody Else"),
    ])
    conv = _thread(db, "3059654559", "Christophe")
    db.add(ConversationEvent(conversation_id=conv.id, type=EventType.SMS,
                             direction=Direction.INBOUND, occurred_at=NOW - timedelta(hours=5),
                             body="What time?"))
    db.commit()
    sheets = [{"name": "Schedule", "columns": ["Day", "Tech", "Window", "Customer", "Status"],
               "rows": [
                   {"row": 2, "values": {"Day": "Thu 10/08  -  Owen"}},
                   {"row": 3, "values": {"Day": "Thu 10/08", "Tech": "Owen",
                                         "Window": "11:00 AM\u20131:00 PM",
                                         "Customer": "Christophe Frochaux",
                                         "Status": "Repair Scheduled"}},
                   {"row": 4, "values": {"Day": "Thu 10/08", "Tech": "Owen",
                                         "Window": "7:30\u20139:30 AM",
                                         "Customer": "Man Yan Chang"}},
                   {"row": 5, "values": {"Day": "Fri 10/09", "Tech": "Antonio",
                                         "Window": "10:30 AM\u201312:30 PM",
                                         "Customer": "Savannah Vanwyk"}},
                   {"row": 6, "values": {"Day": "Fri 10/09", "Tech": "Owen",
                                         "Window": "2:00\u20134:00 PM",
                                         "Customer": "Nobody Known"}},
                   {"row": 7, "values": {"Day": "Mon 10/12", "Customer": "Next Week"}},
               ]},
              {"name": "By City", "columns": ["City", "Customer", "Booked for"], "rows": [
                  {"row": 2, "values": {"Customer": "Christophe Frochaux",
                                        "Booked for": "Thu 10/08 11:00 AM-1:00 PM (Owen)"}}]}]
    out = lookups.compare_schedule(db, NOW, set(), sheets, date_from="today", date_to="10/10")
    by = {v["file"]["customer"]: v for v in out["visits"]}
    assert out["visits_in_file"] == 4 and "Next Week" not in by        # outside the range
    assert by["Christophe Frochaux"]["file"]["window"] == "11:00 AM-1:00 PM"
    assert by["Christophe Frochaux"]["differences"] == [
        "time differs: file 11:00 AM, Zuper 10:00 AM"]
    assert by["Christophe Frochaux"]["recent_calls_and_texts"][0]["text"] == "What time?"
    assert by["Man Yan Chang"]["differences"] == []
    vanwyk = by["Savannah Vanwyk"]["differences"]
    assert any("Fri 10/02" in d for d in vanwyk) and any("not a booked-visit" in d
                                                         for d in vanwyk)
    assert by["Nobody Known"]["differences"][0].startswith("not in Zuper")
    assert [x["job_number"] for x in out["zuper_visits_not_in_file"]] == ["999"]


def test_days_and_windows_read_the_way_the_office_writes_them():
    assert lookups.parse_day("tomorrow", NOW).isoformat() == "2026-10-09"
    assert lookups.parse_day("Jue 10/08", NOW).isoformat() == "2026-10-08"
    assert lookups.parse_day("01/05", datetime(2026, 12, 20, tzinfo=UTC)).year == 2027
    w = lookups._window
    assert w("Jue 10/08 2:00\u20134:00 con Owen") == (14 * 60, 16 * 60)
    assert w("11:00 AM\u20131:00 PM") == (11 * 60, 13 * 60)
    assert w("7:30\u20139:30 AM") == (7 * 60 + 30, 9 * 60 + 30)
    assert w("no time here") is None


# ---- DeepSeek -----------------------------------------------------------------------------------

def test_deepseek_is_its_own_connection_on_its_own_address_and_no_test_can_reach_it():
    p = providers.build("deepseek", "sk-test", None)
    assert isinstance(p, providers.DeepSeekProvider)
    assert p.base_url == providers.DEEPSEEK_BASE_URL
    assert provider_host("api.deepseek.com")
    assert "deepseek" in AiConnection.PROVIDERS


def _deepseek_turn(*, content=None, calls=None, reasoning=None):
    body = openai_completion(content=content, tool_calls=calls,
                             finish="tool_calls" if calls else "stop", model="deepseek-chat")
    if reasoning:
        body["choices"][0]["message"]["reasoning_content"] = reasoning
    return body


@pytest.fixture()
def deepseek_on(db, secrets_key):
    conn = AiConnection(name="DeepSeek", provider="deepseek",
                        api_key_encrypted=vault.encrypt("sk-test-DS-9999"), api_key_last4="9999",
                        default_model="deepseek-chat")
    db.add(conn)
    db.flush()
    s = ai.settings(db, for_update=True)
    s.ai_enabled, s.connection_id, s.model = True, conn.id, "deepseek-chat"
    db.add(visit("d1", 481, 8, 11, name="Christophe Frochaux"))
    db.commit()
    return conn


def test_the_chat_runs_the_new_tools_and_sends_back_deepseeks_reasoning(db, deepseek_on, script):
    script.responses = [
        _deepseek_turn(calls=[openai_call("day_schedule", json.dumps({"date_from": "today"}))],
                       reasoning="Check today's visits first."),
        _deepseek_turn(content="Owen has #481 at 11:00 AM."),
    ]
    out = ai.chat(db, [{"role": "user", "content": "Who is booked today?"}], user_id=None,
                  hidden=set(), now=NOW)
    assert out["reply"] == "Owen has #481 at 11:00 AM." and not out["error"]
    assert script.requests[0]["url"].startswith(providers.DEEPSEEK_BASE_URL)
    second = script.requests[1]["body"]["messages"]
    assistant = next(m for m in second if m["role"] == "assistant")
    assert assistant["reasoning_content"] == "Check today's visits first."
    tool = json.loads(next(m for m in second if m["role"] == "tool")["content"])
    assert tool["days"][0]["technicians"][0]["visits"][0]["job_number"] == "481"


def test_a_reasoning_refusal_is_retried_once_without_it(db, deepseek_on, script):
    script.responses = [
        _deepseek_turn(calls=[openai_call("list_items", "{}")], reasoning="thinking"),
        (400, {"error": {"message": "reasoning_content is not allowed in the input"}}),
        _deepseek_turn(content="Nothing is open."),
    ]
    out = ai.chat(db, [{"role": "user", "content": "What is open?"}], user_id=None,
                  hidden=set(), now=NOW)
    assert out["reply"] == "Nothing is open."
    assert "reasoning_content" not in json.dumps(script.requests[2]["body"])


def test_the_chat_stops_looking_once_it_has_read_enough(db, deepseek_on, script, monkeypatch):
    monkeypatch.setattr(ai, "CHAT_READ_BUDGET", 10)
    script.responses = [
        _deepseek_turn(calls=[openai_call("day_schedule", json.dumps({"date_from": "today"}))]),
        _deepseek_turn(content="Here is what I read."),
    ]
    out = ai.chat(db, [{"role": "user", "content": "Everything, please"}], user_id=None,
                  hidden=set(), now=NOW)
    assert out["reply"] == "Here is what I read."
    last = script.requests[-1]["body"]["messages"][-1]
    assert last["role"] == "user" and "no more look-ups" in last["content"]


# ---- 2026-10-09: what the first real answer got wrong ------------------------------------------

def test_a_number_the_customer_gave_in_a_call_is_theirs_and_our_lines_never_are(db):
    """Janeth Palacio gave her son's number on a call; he confirmed on THAT number. The office
    line is a speaker in every transcript — it must never become a customer's number."""
    office = "+19549147244"
    for n, phone in (("721", "9549931801"), ("722", "3055550201"), ("723", "3055550202")):
        db.add(DispatchJob(job_uid="j" + n, job_number=n, board=c.INSPECTION_BOARD,
                           status="Scheduled", phones=[phone], is_open=True))
        conv = _thread(db, phone, "C" + n)
        db.add(ConversationEvent(conversation_id=conv.id, type=EventType.CALL,
                                 direction=Direction.OUTBOUND, occurred_at=NOW - timedelta(days=2),
                                 call_status="completed", duration_seconds=90, body="call",
                                 source_system="OpenPhone",
                                 transcript="%s: Hello. +1%s: Hi." % (office, phone) + (
                                     " Call my son, his number is 954-701-8639." if n == "721"
                                     else "")))
    son = _thread(db, "9547018639", "Sebastian")
    db.add(ConversationEvent(conversation_id=son.id, type=EventType.SMS,
                             direction=Direction.INBOUND, occurred_at=NOW - timedelta(hours=3),
                             body="Yes, Friday 7:30 works"))
    db.commit()
    reach = lookups.customer_phones(db, NOW)
    assert reach["j721"] == {"9549931801", "9547018639"}
    assert "9549147244" not in reach["j722"] | reach["j723"]
    words = lookups.search_comms(db, NOW, set(), job_number="721")
    assert any(i.get("text") == "Yes, Friday 7:30 works" for i in words["items_oldest_first"])


def test_the_excel_disagreeing_with_itself_is_said_and_a_match_says_it_matches(db):
    db.add_all([visit("v", 680, 9, 14, name="Quentin Rushin"),
                visit("d", 690, 10, 9, name="Diana Reyes")])
    db.commit()
    sheets = [{"name": "Schedule", "columns": ["Day", "Tech", "Window", "Customer"], "rows": [
                  {"row": 2, "values": {"Day": "Fri 10/09", "Tech": "Owen",
                                        "Window": "2:00\u20134:00 PM",
                                        "Customer": "Quentin Rushin"}},
                  {"row": 3, "values": {"Day": "Sat 10/10", "Tech": "Owen",
                                        "Window": "9:00\u201311:00 AM",
                                        "Customer": "Diana Reyes"}}]},
              {"name": "By City", "columns": ["City", "Customer", "Booked for"], "rows": [
                  {"row": 63, "values": {"Customer": "Diana Reyes",
                                         "Booked for": "Fri 10/09 7:30\u201310:00 AM (Owen)"}}]}]
    out = lookups.compare_schedule(db, NOW, set(), sheets, date_from="tomorrow")
    assert [v["file"]["customer"] for v in out["visits"]] == ["Quentin Rushin"]
    assert out["visits"][0]["matches"].startswith("day, time, technician")
    assert out["file_disagrees_with_itself"] == [{
        "customer": "Diana Reyes", "this_sheet": "By City", "row": 63, "says": "Fri 10/09",
        "day_plan_says": "Sat 10/10"}]


def test_a_relative_who_is_also_a_customer_still_counts(db):
    """Live 2026-10-09: Janeth Palacio's son confirmed her visit; he is the customer on his own
    AHS job, and the first rule (never another customer's number) hid his confirmation."""
    db.add(DispatchJob(job_uid="mom", job_number="721", board=c.INSPECTION_BOARD,
                       status="Scheduled", phones=["9549931801"], is_open=True))
    db.add(DispatchJob(job_uid="son", job_number="731", board=c.INSPECTION_BOARD,
                       status="Scheduled", phones=["9547018639"], is_open=True))
    conv = _thread(db, "9549931801", "Janeth")
    db.add(ConversationEvent(conversation_id=conv.id, type=EventType.SMS,
                             direction=Direction.INBOUND, occurred_at=NOW - timedelta(days=5),
                             body="Please call my son Sebastian at (954) 701-8639"))
    db.commit()
    assert lookups.customer_phones(db, NOW)["mom"] == {"9549931801", "9547018639"}


def test_one_customer_with_many_jobs_does_not_make_their_relative_look_like_our_line(db):
    """Live 2026-10-09: Janeth Palacio has six jobs on one number; counting JOBS made her son's
    number look like one of ours (in 3+ conversations). It is counted per customer."""
    for n in ("721", "418", "467", "311"):
        db.add(DispatchJob(job_uid="j" + n, job_number=n, board=c.INSPECTION_BOARD,
                           status="Scheduled", phones=["9549931801"], is_open=True))
    conv = _thread(db, "9549931801", "Janeth")
    db.add(ConversationEvent(conversation_id=conv.id, type=EventType.SMS,
                             direction=Direction.INBOUND, occurred_at=NOW - timedelta(days=5),
                             body="My son is at 954-701-8639"))
    db.commit()
    reach = lookups.customer_phones(db, NOW)
    assert all("9547018639" in reach["j" + n] for n in ("721", "418", "467", "311"))

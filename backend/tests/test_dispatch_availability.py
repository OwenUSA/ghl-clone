"""What each customer said about WHEN they can have the visit (2026-10-08).

The owner's question: "if a customer said on a call or a text that they won't be available next
week, will the schedule consider that?" What is pinned, by behaviour:

  * **The AI reads ALL of a customer's calls and texts** (every number on the job, whole Quo
    transcripts) and its reading is saved with the quotes; the dates it gives are checked, and
    a range that already ended is dropped.
  * **It is read again only when the conversation changes** — a second pass asks nothing.
  * **AI off, paused or over the cap: nothing is read**, and the saved readings still apply.
  * **Every plan applies the saved reading**: "away next week" keeps the visit out of that week;
    a customer away for the whole planning window is "not placed", with the reason.
  * **The office corrects a reading**; the correction stands when new messages arrive (they are
    flagged), and "use the calls again" hands it back to the AI.
  * **A hidden board's job answers 404.**
"""
import json
from datetime import UTC, date, datetime, timedelta

import pytest
from app.ai import vault
from app.dispatch import ai, availability, scheduling
from app.dispatch import config as c
from app.main import app
from app.models import (
    AiConnection,
    AiSettings,
    Contact,
    Conversation,
    ConversationEvent,
    Direction,
    DispatchAiRun,
    DispatchAvailability,
    DispatchJob,
    EventType,
    Pipeline,
    PipelinePermission,
)
from fastapi.testclient import TestClient
from tests.ai_support import openai_completion
from tests.test_dispatch_ai import people  # noqa: F401  (admin, dispatcher, tech)

NOW = datetime(2026, 10, 8, 18, 0, tzinfo=UTC)            # Thu 2 PM New York


def answer(limits, summary="", evidence=None):
    return openai_completion(content=json.dumps({
        "limits": limits, "summary": summary, "evidence": evidence or [],
        "confidence": "high"}), model="deepseek-chat")


@pytest.fixture()
def on(db, secrets_key):
    conn = AiConnection(name="DeepSeek", provider="deepseek",
                        api_key_encrypted=vault.encrypt("sk-test-DS-2"), api_key_last4="2",
                        default_model="deepseek-chat")
    db.add(conn)
    db.flush()
    s = ai.settings(db, for_update=True)
    s.ai_enabled, s.connection_id, s.model = True, conn.id, "deepseek-chat"
    db.commit()


def customer(db, number, phone, body, *, hours_ago=3, kind=EventType.SMS, board=None,
             transcript=None):
    db.add(DispatchJob(job_uid="j%s" % number, job_number=str(number),
                       board=board or c.REPAIR_BOARD, status="Reschedule Required",
                       customer_name="C%s" % number, city="Miami", lat=25.8, lng=-80.2,
                       is_open=True, phones=[phone], status_since=NOW - timedelta(days=4)))
    con = Contact(first_name="C%s" % number, phone="+1" + phone)
    db.add(con)
    db.flush()
    conv = Conversation(contact_id=con.id)
    db.add(conv)
    db.flush()
    db.add(ConversationEvent(conversation_id=conv.id, type=kind, direction=Direction.INBOUND,
                             occurred_at=NOW - timedelta(hours=hours_ago), body=body,
                             transcript=transcript, call_status="completed",
                             duration_seconds=120, source_system="OpenPhone"))
    db.commit()
    return conv


def test_the_ai_reads_every_message_and_its_reading_is_kept_with_the_quote(db, on, script):
    long_call = ("+19549147244: Hi, how are you. " + "We talked about the roof. " * 80 +
                 "+13055550111: By the way I'm traveling all next week, back on the 19th.")
    customer(db, 500, "3055550111", "call", kind=EventType.CALL, transcript=long_call)
    script.responses = [answer({"blocked": [{"from": "2026-10-12", "to": "2026-10-17"}],
                                "not_days": ["tuesday", "Funday"]},
                               "Traveling next week, back on the 19th.",
                               [{"at": "Thu 10/08 11:00 AM", "quote": "traveling all next week"}])]
    counts = availability.refresh(db, NOW)
    assert counts["read"] == 1
    sent = script.requests[0]["body"]["messages"][-1]["content"]
    assert "back on the 19th" in sent                               # the WHOLE transcript
    row = db.query(DispatchAvailability).one()
    assert row.limits == {"blocked": [{"from": "2026-10-12", "to": "2026-10-17"}],
                          "not_days": ["Tue"]}                      # "Funday" dropped
    assert availability.describe(row.limits) == "away Mon 10/12-Sat 10/17, not Tue"
    assert row.evidence[0]["quote"] == "traveling all next week"
    assert db.query(DispatchAiRun).filter(DispatchAiRun.kind == "availability").count() == 1
    # Nothing new said: the next pass asks nothing.
    assert availability.refresh(db, NOW + timedelta(minutes=2))["unchanged"] == 1
    assert script.calls == 1


def test_a_new_message_is_read_again_and_a_past_range_is_dropped(db, on, script):
    conv = customer(db, 501, "3055550112", "next week is bad for me")
    script.responses = [answer({"blocked": [{"from": "2026-10-12", "to": "2026-10-17"}]})]
    availability.refresh(db, NOW)
    db.add(ConversationEvent(conversation_id=conv.id, type=EventType.SMS,
                             direction=Direction.INBOUND, occurred_at=NOW - timedelta(hours=1),
                             body="actually come any day after 3"))
    db.commit()
    script.responses = [answer({"after": "3 PM",
                                "blocked": [{"from": "2026-10-01", "to": "2026-10-05"}]})]
    assert availability.refresh(db, NOW)["read"] == 1
    assert db.query(DispatchAvailability).one().limits == {"after": "15:00"}


def test_nothing_is_read_while_the_ai_is_paused_and_saved_readings_still_apply(db, on, script):
    customer(db, 502, "3055550113", "away next week")
    db.add(DispatchAvailability(job_uid="j502", job_number="502", source="ai",
                                limits={"blocked": [{"from": "2026-10-12", "to": "2026-10-17"}]},
                                read_at=NOW - timedelta(days=1), fingerprint="old"))
    db.add(AiSettings(id=1, paused=True))
    db.commit()
    counts = availability.refresh(db, NOW)
    assert counts["read"] == 0 and "paused" in counts["skipped"] and script.calls == 0
    jobs = scheduling.waiting_jobs(db, NOW, hidden=set())
    assert jobs[0].blocked == [(date(2026, 10, 12), date(2026, 10, 17))]


def test_every_plan_keeps_the_visit_out_of_the_week_the_customer_is_away(db):
    for n, phone in ((503, "3055550114"), (504, "3055550115")):
        db.add(DispatchJob(job_uid="j%d" % n, job_number=str(n), board=c.REPAIR_BOARD,
                           status="Reschedule Required", customer_name="C%d" % n, lat=25.8,
                           lng=-80.2, city="Miami", is_open=True, phones=[phone]))
    db.add(DispatchAvailability(job_uid="j503", job_number="503", source="ai", read_at=NOW,
                                limits={"blocked": [{"from": "2026-10-09",
                                                     "to": "2026-10-14"}]},
                                evidence=[{"at": "Thu 10/08", "quote": "away until Thursday"}]))
    db.add(DispatchAvailability(job_uid="j504", job_number="504", source="ai", read_at=NOW,
                                limits={"not_before": "2026-11-30"}))
    db.commit()
    out = scheduling.make_plan(db, NOW, days=6, store=False)
    placed = {v["job_number"]: d["date"] for d in out["days"] for t in d["techs"]
              for v in t["visits"] if not v["existing"]}
    assert placed["503"] >= "2026-10-15"
    v = next(v for d in out["days"] for t in d["techs"] for v in t["visits"]
             if v["job_number"] == "503")
    assert 'away Fri 10/09-Wed 10/14' in v["limits"][0] and "away until Thursday" in v["limits"][0]
    assert "504" not in placed
    why = next(u for u in out["unplaced"] if u["job_number"] == "504")["reason"]
    assert "customer's limits" in why and "not before Mon 11/30" in why
    assert out["availability"]["with_limits"] == 2


def test_the_office_corrects_a_reading_and_it_stands(db, on, script, people):  # noqa: F811
    conv = customer(db, 505, "3055550116", "mornings only please")
    script.responses = [answer({"before": "12:00"})]
    availability.refresh(db, NOW)
    cl = TestClient(app)
    h = people["dispatcher"][1]
    listed = cl.get("/api/dispatch/availability", headers=h).json()["jobs"]
    assert listed[0]["in_words"] == "done by 12:00 PM" and listed[0]["source"] == "ai"
    r = cl.put("/api/dispatch/availability/505", json={"limits": {"after": "13:00"},
                                                       "summary": "changed on the phone"},
               headers=h)
    assert r.status_code == 200 and r.json()["source"] == "office"
    db.add(ConversationEvent(conversation_id=conv.id, type=EventType.SMS,
                             direction=Direction.INBOUND, occurred_at=NOW, body="thanks"))
    db.commit()
    availability.refresh(db, NOW + timedelta(minutes=5))
    row = db.query(DispatchAvailability).one()
    db.refresh(row)
    assert row.limits == {"after": "13:00"} and row.newer_messages is True
    assert script.calls == 1                                       # the correction was kept
    assert cl.delete("/api/dispatch/availability/505", headers=h).json() == {"cleared": True}
    script.responses = [answer({})]
    availability.refresh(db, NOW + timedelta(minutes=6))
    db.refresh(row)
    assert row.source == "ai" and script.calls == 2


def test_a_hidden_boards_job_answers_404(db, people):  # noqa: F811
    db.add(DispatchJob(job_uid="r1", job_number="900", board=c.RETAIL_BOARD, status="New Lead",
                       is_open=True, lat=25.8, lng=-80.2))
    p = Pipeline(name="Retail")
    db.add(p)
    db.flush()
    db.add(PipelinePermission(pipeline_id=p.id, user_id=people["admin"][0]))
    db.commit()
    cl = TestClient(app)
    r = cl.put("/api/dispatch/availability/900", json={"limits": {}},
               headers=people["dispatcher"][1])
    assert r.status_code == 404
    assert cl.get("/api/dispatch/availability", headers=people["tech"][1]).status_code == 403

"""Reviews & QC (app/reviews), by behaviour - the important ones.

Rules: asked 2 days after Repair Complete, never later than 14; unhappy words, an open issue, a
callback job, money due, STOP or a recent ask skip it (with the reason); test mode; a bare 1-5 is
read in code; the wording never trips Zuper's "Thank you for confirming" workflow.
Pass (Zuper faked at HTTP): Shadow sends nothing; 5 -> thank you + the review link; 3 -> "what
could we do better?" + ONE Dispatch item + the bell, their answer kept and the link offered too
(never only to happy customers); "it still leaks" -> an urgent issue; without
REVIEWS_TEXTS_ENABLED the client refuses the send; the auto-reply leaves the customer alone.
"""
from datetime import timedelta

import httpx
import pytest
from app import crmlink
from app.autoreply import rules as arules
from app.models import (
    AgentPermission,
    AgentRegistryEntry,
    AgentRegistryLog,
    AiAlert,
    ConversationSignal,
    DispatchItem,
    DispatchJob,
    ReviewRequest,
    Role,
    User,
    ZuperStatusHistory,
)
from app.reviews import rules, service
from app.zuper import client
from tests.test_autoreply_service import NOW, TEST_UID, FakeConnect, msg

PHONE = "9549147244"
LINK = "https://g.page/r/dtr-test/review"


# ------------------------------------------------------------------------- rules

def test_rules_timing_skips_and_reading():
    done = NOW - timedelta(days=3)
    assert rules.due(done, NOW, 2, 14) == "due"
    assert rules.due(NOW - timedelta(days=1), NOW, 2, 14) == "early"
    assert rules.due(NOW - timedelta(days=20), NOW, 2, 14) == "late"
    ok = rules.Facts(completed_at=done, phone=PHONE, customer_uid="c1", test=True)
    assert rules.skip_reason(ok, test_only=True) is None
    for kw, words in (({"unhappy_tag": "still_leaking"}, "still leaking"),
                      ({"callback_job": True}, "callback"), ({"money_due": True}, "money"),
                      ({"opted_out": True}, "STOP"), ({"test": False}, "test mode")):
        f = rules.Facts(completed_at=done, phone=PHONE, customer_uid="c1",
                        **{"test": True, **kw})
        assert words in rules.skip_reason(f, test_only=True)
    assert [rules.parse_score(t) for t in ("5", " 4/5 ", "3 stars", "⭐⭐⭐⭐⭐", "great job", "15")
            ] == [5, 4, 3, 5, None, None]
    assert rules.mentions_leak("it is still leaking in the bedroom")
    for text in (rules.ask_text("Ana Smith"), rules.five_text("Ana", LINK),
                 rules.improve_text("Ana"), rules.offer_text("Ana", LINK)):
        assert not arules.is_template(text), text          # Zuper's workflow 6 stays out
    assert LINK not in rules.five_text("Ana", "")


# ------------------------------------------------------------------------- the pass

@pytest.fixture()
def rv(db, monkeypatch):
    monkeypatch.setenv("ZUPER_SYNC_ENABLED", "true")
    monkeypatch.setenv("ZUPER_API_KEY", "zk_test")
    monkeypatch.setenv("ZUPER_PULL_ONLY", "true")
    monkeypatch.setenv("REVIEWS_ENABLED", "true")
    monkeypatch.setenv("REVIEWS_TEXTS_ENABLED", "true")
    monkeypatch.setattr(client, "_sleep", lambda s: None)
    monkeypatch.setattr(crmlink, "configured", lambda: True)
    fake = FakeConnect()
    fake.calls = []
    monkeypatch.setattr(crmlink, "agent_call", lambda *a, **k: fake.calls.append((a, k)) or
                        crmlink.LinkResult(True, 202, "", {"call_id": "x"}))
    client.TRANSPORT = httpx.MockTransport(fake.handler)
    fake.add("conv-1", TEST_UID, "Ana Smith", PHONE,
             [msg("m0", NOW - timedelta(days=9), body="Thanks for coming")])
    db.add(User(email="boss@x.test", name="Boss", role=Role.ADMIN))
    db.add(AgentRegistryLog(at=NOW - timedelta(days=10), entry_key="reviews", what="started"))
    db.add(AgentRegistryEntry(key="reviews", config={"review_link": LINK}))
    db.add(DispatchJob(job_uid="job-1", job_number="701", board="Retail",
                       status="Repair Complete", customer_uid=TEST_UID,
                       customer_name="Ana Smith", phones=[PHONE], is_open=True,
                       technician="Luis"))
    db.add(ZuperStatusHistory(history_uid="h1", job_uid="job-1", job_number="701",
                              category_name="Retail", status_name="Repair Complete",
                              changed_at=NOW - timedelta(days=3)))
    db.commit()
    return fake


def auto(db, *actions):
    for a in actions:
        db.add(AgentPermission(entry_key="reviews", action=a, level="auto",
                               changed_at=NOW - timedelta(days=1)))
    db.commit()


def reply(fake, uid, body, minutes=5):
    fake.history["conv-1"].append(msg(uid, NOW + timedelta(minutes=minutes), body=body))


def test_shadow_records_the_question_and_sends_nothing(db, rv):
    out = service.run(db, NOW)
    row = db.query(ReviewRequest).one()
    assert out["asked"] == 1 and row.asked_state == "shadow" and "1 to 5" in row.asked_body
    assert rv.sent == [] and rv.notes == []


def test_unhappy_words_since_the_visit_skip_it(db, rv):
    db.add(ConversationSignal(source="quo", source_ref="ce:1", kind="text",
                              at=NOW - timedelta(days=1), phone=PHONE, inbound=True,
                              tag="still_leaking", quote="still leaking"))
    db.commit()
    service.run(db, NOW)
    row = db.query(ReviewRequest).one()
    assert row.state == "skipped" and "still leaking" in row.reason and rv.sent == []


def test_a_five_gets_the_review_link(db, rv):
    auto(db, "send_text")
    service.run(db, NOW)
    assert rv.sent[0]["sms"]["body"].startswith("Hi Ana, this is Dream Team Roofing")
    assert service.active_on(db, PHONE, NOW)               # the auto-reply stays out
    reply(rv, "r1", "5")
    service.run(db, NOW + timedelta(minutes=10))
    row = db.query(ReviewRequest).one()
    assert (row.state, row.score, row.link_sent) == ("done", 5, True)
    assert LINK in rv.sent[-1]["sms"]["body"] and len(rv.sent) == 2
    assert db.query(ConversationSignal).one().tag == "praise"
    service.run(db, NOW + timedelta(minutes=20))
    assert len(rv.sent) == 2                                # the reply is handled once


def test_a_three_alerts_the_office_and_still_offers_the_link(db, rv):
    auto(db, "send_text")
    service.run(db, NOW)
    reply(rv, "r1", "3")
    service.run(db, NOW + timedelta(minutes=10))
    item = db.query(DispatchItem).one()
    assert (item.kind, item.queue, "3 of 5" in item.title) == ("low_rating", "issues", True)
    assert db.query(AiAlert).count() == 1
    assert "done better" in rv.sent[-1]["sms"]["body"] and LINK not in rv.sent[-1]["sms"]["body"]
    reply(rv, "r2", "The crew left nails in my driveway", minutes=30)
    service.run(db, NOW + timedelta(minutes=40))
    row = db.query(ReviewRequest).one()
    assert row.state == "done" and "nails" in row.improve
    assert LINK in rv.sent[-1]["sms"]["body"]               # offered to everyone who answers
    assert "nails" in db.query(DispatchItem).one().why


def test_a_leak_is_an_urgent_issue(db, rv):
    auto(db, "send_text")
    service.run(db, NOW)
    reply(rv, "r1", "It is still leaking in the bedroom")
    service.run(db, NOW + timedelta(minutes=10))
    item = db.query(DispatchItem).one()
    assert item.urgent and "STILL LEAKING" in item.title
    assert db.query(ConversationSignal).one().tag == "still_leaking"


def test_without_the_texts_gate_zuper_is_never_asked_to_send(db, rv, monkeypatch):
    monkeypatch.delenv("REVIEWS_TEXTS_ENABLED")
    auto(db, "send_text")
    service.run(db, NOW)
    assert rv.sent == [] and db.query(ReviewRequest).one().asked_state == "failed"


def test_no_reply_calls_only_when_calls_are_allowed(db, rv):
    auto(db, "send_text")
    service.run(db, NOW)
    service.run(db, NOW + timedelta(days=2, hours=1))
    assert db.query(ReviewRequest).one().call_state == "skipped" and rv.calls == []

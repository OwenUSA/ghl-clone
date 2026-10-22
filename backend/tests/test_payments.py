"""Payments & collections (app/payments), by behaviour - the important ones.

Rules: a 50/50 split adds up to the cent; due dates before / after the job / on a date / not
known yet; upcoming / due / overdue; reminder spacing and cap.
Ledger: every change is logged; Undo puts the agreement back; only the latest can be undone.
Pass: a due part keeps ONE Dispatch item, rings once when overdue, resolves when paid; Zuper's
invoice money becomes ONE suggestion, nothing marked paid; a payment screenshot becomes a proof to
review + a suggestion + a bell, a holiday photo nothing; reminders at Never send nothing.
Chat (provider scripted): "paid the deposit, $1,500" is applied and can be undone; an amount the
person never said is refused.
API: a technician is refused; a proof must be a picture.
"""
import io
import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from app.ai import vault
from app.dispatch import ai as dai
from app.models import (
    AgentRegistryEntry,
    AgentRegistryLog,
    AiAlert,
    AiConnection,
    Contact,
    Conversation,
    ConversationEvent,
    Direction,
    DispatchItem,
    DispatchJob,
    EventType,
    MessageAttachment,
    PaymentAgreement,
    PaymentChange,
    PaymentMilestone,
    PaymentProof,
    PaymentSuggestion,
    Role,
    User,
    ZuperDocument,
)
from app.payments import chat, ledger, rules, service
from PIL import Image
from tests.ai_support import openai_completion

NOW = datetime(2026, 10, 21, 16, 0, tzinfo=UTC)               # Wed noon New York
TODAY = date(2026, 10, 21)


# ------------------------------------------------------------------------- rules

def test_split_due_and_status():
    a, b = rules.split(750001, "50/50")
    assert (a.amount_cents + b.amount_cents, a.due_rule, b.due_rule) == (
        750001, "before_start", "after_completion")
    assert [p.amount_cents for p in rules.split(100000, "30/70")] == [30000, 70000]
    start = datetime(2026, 10, 24, 13, 0, tzinfo=UTC)
    kw = {"status": None, "finished_at": None, "deposit_days_before": 1}
    assert rules.due_date("before_start", None, scheduled_start=start, **kw) == date(2026, 10, 23)
    assert rules.due_date("before_start", None, scheduled_start=None, **kw) is None
    assert rules.due_date("after_completion", None, scheduled_start=start, **dict(
        kw, status="Repair Complete", finished_at=NOW)) == TODAY
    assert rules.due_date("on_date", date(2026, 11, 1), scheduled_start=None, **kw) == \
        date(2026, 11, 1)
    assert rules.status("pending", TODAY + timedelta(days=1), TODAY, 3) == "upcoming"
    assert rules.status("pending", TODAY - timedelta(days=2), TODAY, 3) == "due"
    assert rules.status("pending", TODAY - timedelta(days=4), TODAY, 3) == "overdue"
    assert rules.status("paid", TODAY - timedelta(days=9), TODAY, 3) == "paid"
    assert rules.reminder_due(st="overdue", sent=0, last=None, now=NOW, every_days=3,
                              max_reminders=3)
    assert not rules.reminder_due(st="overdue", sent=1, last=NOW - timedelta(days=1), now=NOW,
                                  every_days=3, max_reminders=3)
    assert not rules.reminder_due(st="overdue", sent=3, last=None, now=NOW, every_days=3,
                                  max_reminders=3)
    assert (rules.money(150000), rules.cents("$1,500"), rules.cents("abc")) == (
        "$1,500.00", 150000, None)


# ------------------------------------------------------------------------- ledger

def test_every_change_is_logged_and_the_latest_can_be_undone(db):
    a, _ = ledger.create(db, user_id=None, via="page", customer_name="Ana Smith",
                         agreed_cents=300000, split="50/50")
    dep, bal = ledger.milestones(db, a.id)
    ch1 = ledger.record_payment(db, a, dep, amount_cents=150000, paid_on=TODAY, method="Zelle",
                                user_id=None, via="page", source="manual")
    db.commit()
    assert a.state == "collecting" and dep.state == "paid"
    ch2 = ledger.waive(db, a, bal, user_id=None, via="page")
    db.commit()
    assert a.state == "paid"
    with pytest.raises(ledger.Invalid):
        ledger.undo(db, ch1, user_id=None)                        # not the latest
    ledger.undo(db, ch2, user_id=None)
    db.commit()
    assert db.get(PaymentMilestone, bal.id).state == "pending" and a.state == "collecting"
    ledger.undo(db, ch1, user_id=None)
    db.commit()
    assert db.get(PaymentMilestone, dep.id).state == "pending"
    assert db.query(PaymentChange).count() == 3                   # create + 2, never deleted


# ------------------------------------------------------------------------- the pass

@pytest.fixture()
def pay(db, monkeypatch, secrets_key):
    monkeypatch.setenv("PAYMENTS_ENABLED", "true")
    db.add(User(email="boss@x.test", name="Boss", role=Role.ADMIN))
    db.add(AgentRegistryLog(at=NOW - timedelta(days=5), entry_key="payments", what="started"))
    db.add(DispatchJob(job_uid="job-1", job_number="701", board="Retail", status="Scheduled",
                       customer_name="Ana Smith", phones=["9415550101"], is_open=True,
                       customer_uid="cust-1",
                       scheduled_start=NOW - timedelta(days=4)))
    db.commit()
    a, _ = ledger.create(db, user_id=None, via="page", customer_name="Ana Smith",
                         job_uid="job-1", job_number="701", board="Retail",
                         phone="9415550101", agreed_cents=300000, split="50/50")
    db.commit()
    return a


def test_an_overdue_part_has_one_item_and_one_bell_and_resolves_when_paid(db, pay):
    service.run(db, NOW)
    service.run(db, NOW + timedelta(minutes=10))
    item = db.query(DispatchItem).one()
    assert item.kind == "payment_due" and item.queue == "collect" and "overdue" in item.title
    assert db.query(AiAlert).count() == 1
    dep = ledger.milestones(db, pay.id)[0]
    ledger.record_payment(db, pay, dep, amount_cents=150000, paid_on=TODAY, method=None,
                          user_id=None, via="page", source="manual")
    db.commit()
    service.run(db, NOW + timedelta(minutes=20))
    assert db.query(DispatchItem).one().state == "resolved"


def test_zuper_money_becomes_one_suggestion_never_paid(db, pay):
    db.add(ZuperDocument(kind=ZuperDocument.INVOICE, zuper_uid="inv-1", job_uid="job-1",
                         total_cents=300000, balance_cents=150000))
    db.commit()
    service.run(db, NOW)
    service.run(db, NOW + timedelta(minutes=10))
    s = db.query(PaymentSuggestion).one()
    assert (s.source, s.amount_cents, s.state) == ("zuper", 150000, "open")
    assert all(m.state == "pending" for m in ledger.milestones(db, pay.id))


def _picture(db, contact_id, color=(10, 120, 40)):
    from app import attachments
    out = io.BytesIO()
    Image.new("RGB", (400, 300), color).save(out, "PNG")
    _sha, path = attachments.store(out.getvalue())
    conv = db.query(Conversation).filter_by(contact_id=contact_id).first()
    ev = ConversationEvent(conversation_id=conv.id, type=EventType.SMS,
                           direction=Direction.INBOUND, occurred_at=NOW, body="")
    db.add(ev)
    db.flush()
    att = MessageAttachment(conversation_event_id=ev.id, direction=Direction.INBOUND,
                            status="STORED", content_type="image/png", storage_path=path,
                            created_at=NOW)
    db.add(att)
    db.commit()
    return att


@pytest.mark.parametrize("looks", [True, False])
def test_a_payment_screenshot_is_flagged_for_a_person(db, pay, script, looks):
    c = Contact(first_name="Ana", last_name="Smith", phone="+19415550101")
    db.add(c)
    db.flush()
    db.add(Conversation(contact_id=c.id))
    pay.contact_id = c.id
    conn = AiConnection(name="OpenAI", provider="openai",
                        api_key_encrypted=vault.encrypt("sk-1"), api_key_last4="1",
                        default_model="gpt-5-mini")
    db.add(conn)
    db.flush()
    db.add(AgentRegistryEntry(key="payments", config={"vision_connection_id": conn.id}))
    db.commit()
    _picture(db, c.id)
    script.responses = [openai_completion(content=json.dumps(
        {"looks_like_payment": looks, "amount": "1500.00" if looks else None,
         "date": "2026-10-21", "app": "Zelle"}))]
    service.run(db, NOW)
    p = db.query(PaymentProof).one()
    if looks:
        s = db.query(PaymentSuggestion).one()
        assert (p.state, s.source, s.amount_cents) == ("to_review", "screenshot", 150000)
        assert db.query(AiAlert).filter(AiAlert.kind == "dispatch_payment_screenshot").count() \
            == 1
    else:
        assert p.state == "not_payment" and db.query(PaymentSuggestion).count() == 0
    assert all(m.state == "pending" for m in ledger.milestones(db, pay.id))
    service.run(db, NOW + timedelta(minutes=10))
    assert script.calls == 1                                       # each picture once


def test_reminders_at_never_send_nothing(db, pay, monkeypatch):
    from app.zuper import client
    sent = []
    client.TRANSPORT = httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(500))
    monkeypatch.setenv("PAYMENTS_TEXTS_ENABLED", "true")
    service.run(db, NOW)
    assert sent == [] and all(m.reminders_sent == 0 for m in ledger.milestones(db, pay.id))


# ------------------------------------------------------------------------- the chat

@pytest.fixture()
def ds(db, secrets_key):
    conn = AiConnection(name="Deepseek", provider="deepseek",
                        api_key_encrypted=vault.encrypt("sk-2"), api_key_last4="2",
                        default_model="deepseek-flash")
    db.add(conn)
    db.flush()
    s = dai.settings(db, for_update=True)
    s.ai_enabled, s.connection_id, s.model = True, conn.id, "deepseek-flash"
    db.commit()


def call(name, **args):
    return openai_completion(tool_calls=[{"id": "c1", "type": "function", "function": {
        "name": name, "arguments": json.dumps(args)}}], finish="tool_calls",
        model="deepseek-flash")


def test_the_chat_records_a_payment_and_it_can_be_undone(db, pay, ds, script):
    script.responses = [call("record_payment", agreement_id=pay.id, part="Deposit",
                             amount=1500, method="Zelle"),
                        openai_completion(content="Done: deposit marked paid.",
                                          model="deepseek-flash")]
    res = chat.answer(db, [{"role": "user", "text": "Ana Smith paid the deposit, $1,500 by "
                                                    "Zelle"}], user_id=None, now=NOW)
    assert not res["error"] and len(res["change_ids"]) == 1
    dep = ledger.milestones(db, pay.id)[0]
    assert (dep.state, dep.paid_cents, dep.source) == ("paid", 150000, "chat")
    ledger.undo(db, db.get(PaymentChange, res["change_ids"][0]), user_id=None)
    db.commit()
    assert ledger.milestones(db, pay.id)[0].state == "pending"


def test_the_chat_refuses_an_amount_nobody_said(db, ds, script):
    script.responses = [call("create_agreement", customer_name="Bob Jones", agreed=9999),
                        openai_completion(content="How much did they agree to?",
                                          model="deepseek-flash")]
    res = chat.answer(db, [{"role": "user", "text": "Bob Jones agreed, 50/50"}], user_id=None,
                      now=NOW)
    assert res["change_ids"] == [] and db.query(PaymentAgreement).count() == 0
    assert "not said" in res["steps"][0]["result"]


# ------------------------------------------------------------------------- the API

def test_the_api_access_and_a_proof_must_be_a_picture(world):
    assert world.client("tech").get("/api/payments").status_code == 403
    d = world.client("dispatcher")
    r = d.post("/api/payments", json={"customer_name": "Ana Smith", "agreed": "3,000",
                                      "split": "50/50"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert [p["amount_cents"] for p in body["parts"]] == [150000, 150000]
    r = d.post("/api/payments/%d/proofs" % body["id"],
               files={"file": ("x.txt", b"not a picture", "text/plain")})
    assert r.status_code == 415
    pid = body["parts"][0]["id"]
    r = d.post("/api/payments/%d/parts/%d/paid" % (body["id"], pid), json={"method": "Zelle"})
    assert r.status_code == 200 and r.json()["parts"][0]["status"] == "paid"
    undo = r.json()["history"][0]
    assert undo["undoable"] is True
    assert d.post("/api/payments/changes/%d/undo" % undo["id"]).status_code == 200
    assert d.get("/api/payments/%d" % body["id"]).json()["parts"][0]["status"] != "paid"

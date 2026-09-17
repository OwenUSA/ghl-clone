"""Registering Zuper's webhooks (app/zuper/webhook.py), against the fake Zuper.

Behaviour: a dry run sends no write and records nothing; a commit creates one webhook per
(module, event) that posts to this CRM with the token header; a second commit creates nothing
— also when Zuper cannot list its webhooks; Zuper's refusal stops the command with Zuper's own
message and a corrected run carries on; an unknown outcome is never retried; the token is
never printed; the setup check's webhook item follows.
"""
import json

import httpx
from app.db import SessionLocal
from app.models import ZuperMapping
from app.zuper import setup, webhook
from sqlalchemy import func, select

URL = "https://crm.dreamteamroofingfl.com/api/zuper/webhook"


def ours(fake) -> list[dict]:
    return [w for w in fake.webhooks if w.get("webhook_module")]


def run(commit: bool) -> tuple[int, dict]:
    with SessionLocal() as s:
        return webhook.run(s, commit=commit)


def records(state: str | None = None) -> int:
    with SessionLocal() as s:
        q = select(func.count(ZuperMapping.id)).where(ZuperMapping.crm_type == "webhook")
        if state:
            q = q.where(ZuperMapping.state == state)
        return s.scalar(q)


def webhook_item() -> dict:
    with SessionLocal() as s:
        report = setup.run(s, commit=False)
    return next(c for c in report["checks"] if c["key"] == "webhook")


def test_dry_run_lists_what_it_would_create_and_writes_nothing(zworld, fake):
    code, report = run(commit=False)
    assert code == 0, report
    assert fake.writes() == [] and ours(fake) == [] and records() == 0
    assert {i["action"] for i in report["items"]} == {"create"}
    assert len(report["items"]) == len(webhook.EVENTS)
    assert "would create" in webhook.render(report)


def test_commit_creates_one_webhook_per_event_with_the_token_header(zworld, fake):
    code, report = run(commit=True)
    assert code == 0, report
    hooks = ours(fake)
    assert {(h["webhook_module"], h["webhook_event"]) for h in hooks} == set(webhook.EVENTS)
    assert len(hooks) == len(webhook.EVENTS)
    for h in hooks:
        assert h["webhook_url"] == URL
        assert h["request_method"] == "POST" and h["content_type"] == "application/json"
        assert h["headers"] == [{"key": "X-Webhook-Token", "value": "whk_test"}]
    assert all(i["action"] == "created" and i["authenticates"] is True
               for i in report["items"])
    assert records("linked") == len(webhook.EVENTS)
    assert {"JOB", "CUSTOMER", "APPOINTMENT", "NOTE", "SERVICE_TASK", "ESTIMATE",
            "INVOICE"} <= {m for m, _ in webhook.EVENTS}


def test_a_second_commit_creates_nothing(zworld, fake):
    run(commit=True)
    writes = len(fake.writes())
    code, report = run(commit=True)
    assert code == 0, report
    assert len(fake.writes()) == writes
    assert {i["action"] for i in report["items"]} == {"exists"}


def test_when_zuper_cannot_list_webhooks_the_crm_s_record_prevents_duplicates(zworld, fake):
    fake.webhooks_listable = False
    code, report = run(commit=True)
    assert code == 0, report
    assert report["listed"] is False
    assert any("GET /service/notifications/webhook: not_found (HTTP 404)" in p
               for p in report["list_problems"])
    assert len(ours(fake)) == len(webhook.EVENTS)
    writes = len(fake.writes())
    code, report = run(commit=True)
    assert code == 0, report
    assert len(fake.writes()) == writes
    assert {i["action"] for i in report["items"]} == {"registered"}
    item = webhook_item()
    assert item["state"] == setup.PASS
    assert "registered by this CRM" in item["sentences"][0]


def test_zuper_refusing_an_event_name_stops_with_its_message_and_a_fix_carries_on(zworld,
                                                                                  fake):
    first, second = webhook.EVENTS[0], webhook.EVENTS[1]
    fake.webhook_events = {first}
    code, report = run(commit=True)
    assert code == 2
    assert "Invalid webhook_event %s for module %s" % (second[1], second[0]) in report["stopped"]
    assert len(fake.calls("POST", r"^/webhook$")) == 2       # nothing after the refusal
    assert len(ours(fake)) == 1
    assert records() == 1                                    # the refused one left no record
    assert "STOPPED: " in webhook.render(report)
    fake.webhook_events = None
    code, report = run(commit=True)
    assert code == 0, report
    assert len(ours(fake)) == len(webhook.EVENTS)


def test_an_unknown_outcome_is_never_retried(zworld, fake, monkeypatch):
    fake.webhooks_listable = False
    real = fake.create_webhook
    calls = {"n": 0}

    def outage_after_making_it(params, body):
        calls["n"] += 1
        real(params, body)
        return httpx.Response(502, json={"type": "error", "message": "bad gateway"})

    monkeypatch.setattr(fake, "create_webhook", outage_after_making_it)
    code, report = run(commit=True)
    assert code == 2 and calls["n"] == 1
    assert records("creating") == 1
    monkeypatch.setattr(fake, "create_webhook", real)
    code, report = run(commit=True)
    assert code == 1
    assert report["items"][0]["action"] == "unknown"
    assert any("never made twice" in p for p in report["problems"])
    made = [(h["webhook_module"], h["webhook_event"]) for h in ours(fake)]
    assert made.count(webhook.EVENTS[0]) == 1
    assert webhook_item()["state"] == setup.FAIL


def test_a_listed_webhook_without_the_token_is_a_problem(zworld, fake):
    module, event = webhook.EVENTS[0]
    fake.webhooks.append({"webhook_uid": "whk-old", "webhook_module": module,
                          "webhook_event": event, "webhook_url": URL, "headers": []})
    code, report = run(commit=False)
    assert code == 1
    assert report["items"][0]["authenticates"] is False
    assert any("does not carry the configured token" in p for p in report["problems"])


def test_url_mode_puts_the_token_on_the_url_and_never_prints_it(zworld, fake, monkeypatch,
                                                               capsys):
    monkeypatch.setattr(webhook, "TOKEN_IN", "url")
    assert webhook.main(["--commit"]) == 0
    assert "whk_test" not in capsys.readouterr().out
    hooks = ours(fake)
    assert hooks and all(h["webhook_url"] == URL + "?token=whk_test" for h in hooks)
    assert all("headers" not in h for h in hooks)
    assert webhook.main(["--json"]) == 0
    out = capsys.readouterr().out
    assert "whk_test" not in out
    report = json.loads(out)
    assert all(i["authenticates"] is True for i in report["items"])


def test_zuper_s_message_echoing_the_token_is_redacted(zworld, fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "create_webhook", lambda p, b: fake.error(
        "bad header %s" % b["web_hook"]["headers"][0]["value"]))
    assert webhook.main(["--commit"]) == 2
    out = capsys.readouterr().out
    assert "whk_test" not in out and "bad header [token]" in out


def test_refuses_without_a_token_and_sends_nothing(zworld, fake, monkeypatch):
    monkeypatch.delenv("ZUPER_WEBHOOK_TOKEN")
    code, report = run(commit=True)
    assert code == 2 and report["refused"] == webhook.NO_TOKEN_SENTENCE
    assert fake.requests == [] and records() == 0


def test_the_setup_check_webhook_item_fails_before_and_passes_after(zworld, fake):
    fake.webhooks = []
    assert webhook_item()["state"] == setup.FAIL
    run(commit=True)
    assert webhook_item()["state"] == setup.PASS

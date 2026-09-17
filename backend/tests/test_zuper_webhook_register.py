"""Registering Zuper's webhooks (app/zuper/webhook.py), against the fake Zuper.

Behaviour: a dry run sends no write; a commit creates one webhook per (module, event) that
posts to this CRM with the token header; a second commit creates nothing; Zuper's refusal
stops the command with Zuper's own message; the token is never printed.
"""
import json

from app.zuper import webhook

URL = "https://crm.dreamteamroofingfl.com/api/zuper/webhook"


def ours(fake) -> list[dict]:
    return [w for w in fake.webhooks if w.get("webhook_module")]


def test_dry_run_lists_what_it_would_create_and_writes_nothing(zworld, fake):
    code, report = webhook.run(commit=False)
    assert code == 0, report
    assert fake.writes() == []
    assert ours(fake) == []
    assert {i["action"] for i in report["items"]} == {"create"}
    assert len(report["items"]) == len(webhook.EVENTS)
    text = webhook.render(report)
    assert "would create" in text and "DRY RUN" in text


def test_commit_creates_one_webhook_per_event_with_the_token_header(zworld, fake):
    code, report = webhook.run(commit=True)
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
    modules = {m for m, _ in webhook.EVENTS}
    assert {"JOB", "CUSTOMER", "APPOINTMENT", "NOTE", "SERVICE_TASK", "ESTIMATE",
            "INVOICE"} <= modules


def test_a_second_commit_creates_nothing(zworld, fake):
    webhook.run(commit=True)
    writes = len(fake.writes())
    code, report = webhook.run(commit=True)
    assert code == 0, report
    assert len(fake.writes()) == writes
    assert len(ours(fake)) == len(webhook.EVENTS)
    assert {i["action"] for i in report["items"]} == {"exists"}


def test_zuper_refusing_an_event_name_stops_with_its_message(zworld, fake):
    first = webhook.EVENTS[0]
    fake.webhook_events = {first}
    code, report = webhook.run(commit=True)
    assert code == 2
    second = webhook.EVENTS[1]
    assert "Invalid webhook_event %s for module %s" % (second[1], second[0]) in report["stopped"]
    assert len(fake.calls("POST", r"^/webhook$")) == 2       # nothing after the refusal
    assert len(ours(fake)) == 1
    assert "STOPPED: " in webhook.render(report)
    # Corrected (here: Zuper accepts everything), the rerun adds only what is missing.
    fake.webhook_events = None
    code, report = webhook.run(commit=True)
    assert code == 0, report
    assert len(ours(fake)) == len(webhook.EVENTS)


def test_a_header_zuper_drops_is_reported_as_a_problem(zworld, fake):
    fake.keeps_webhook_headers = False
    fake.webhook_events = {webhook.EVENTS[0]}
    code, report = webhook.run(commit=True)
    # The first create is accepted without its header; the listing shows no header field,
    # so whether it authenticates cannot be told — never claimed as yes.
    assert report["items"][0]["authenticates"] is None
    fake.webhooks[-1]["headers"] = []                       # Zuper shows an empty list
    fake.webhook_events = None
    code, report = webhook.run(commit=False)
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
    assert json.loads(out)["token_in"] == "url"
    code, report = webhook.run(commit=False)
    assert all(i["authenticates"] is True for i in report["items"])


def test_zuper_s_message_echoing_the_token_is_redacted(zworld, fake, monkeypatch, capsys):
    monkeypatch.setattr(fake, "create_webhook", lambda p, b: fake.error(
        "bad url %s" % b["web_hook"]["headers"][0]["value"]))
    assert webhook.main(["--commit"]) == 2
    out = capsys.readouterr().out
    assert "whk_test" not in out and "bad url [token]" in out


def test_refuses_without_a_token_and_sends_nothing(zworld, fake, monkeypatch):
    monkeypatch.delenv("ZUPER_WEBHOOK_TOKEN")
    code, report = webhook.run(commit=True)
    assert code == 2 and report["refused"] == webhook.NO_TOKEN_SENTENCE
    assert fake.requests == []


def test_the_setup_check_webhook_item_passes_after_registering(zworld, fake):
    from app.db import SessionLocal
    from app.zuper import setup
    fake.webhooks = []
    webhook.run(commit=True)
    with SessionLocal() as s:
        report = setup.run(s, commit=False)
    item = next(c for c in report["checks"] if c["key"] == "webhook")
    assert item["state"] == setup.PASS

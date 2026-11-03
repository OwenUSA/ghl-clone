"""Owen AI's job audit: alerts and the scheduled sweep (2026-11-03).

Pinned, by behaviour:
  * one OPEN alert per (job, rule); raising it again refreshes it; a NEW urgent/today alert (or one
    that got worse) rings the owner's bell, a watch one does not;
  * an alert the owner dismissed stays quiet for a week unless it gets worse;
  * after a job's audit succeeds, its open alerts the audit did not raise again are resolved; a
    failed audit resolves nothing;
  * the sweep runs once per configured slot, Mon-Sat, within 3 hours of the slot; each job is a
    FRESH Claude session that never touches the thread's session; the owner's own messages go
    first; when the last audit is done one summary is posted from the alerts table;
  * /api/owen-ai/alerts is the owner's only; dismiss closes an alert.
"""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from app.auth import mint_api_token
from app.main import app
from app.models import AiAlert, OwenAiAlert, OwenAiMessage, OwenAiThread, Role, User
from app.owen_ai import agent, alerts, runner
from fastapi.testclient import TestClient

NY = ZoneInfo("America/New_York")
OWNER = "owner@dtr.test"
TUESDAY_0745 = datetime(2026, 11, 3, 7, 45, tzinfo=NY)


@pytest.fixture()
def owner(db, monkeypatch):
    monkeypatch.setenv("OWEN_AI_ENABLED", "true")
    monkeypatch.setenv("OWEN_AI_OWNER_EMAIL", OWNER)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-test")
    monkeypatch.setenv("OWEN_AI_SWEEP_TIMES", "7:30, 13:00")
    u = User(email=OWNER, name="Owner", role=Role.ADMIN)
    other = User(email="a@dtr.test", name="Admin", role=Role.ADMIN)
    db.add_all([u, other])
    db.flush()
    heads = {}
    for key, usr in (("owner", u), ("admin", other)):
        plain, tok = mint_api_token(usr, name=key)
        db.add(tok)
        heads[key] = {"Authorization": "Bearer " + plain}
    db.commit()
    return u, heads


def _raise(db, rule="SF-CONFIRM-01", severity="today", job="uid-1", **kw):
    return alerts.raise_alert(db, job_uid=job, job_number="801", rule_id=rule,
                              severity=severity, title=kw.get("title", "Not confirmed"),
                              evidence="No call on Mon 11/2", action="Call today")


def bells(db):
    return db.query(AiAlert).filter(AiAlert.kind == "owen_ai_issue").count()


# ------------------------------------------------------------------------------ alerts

def test_one_open_alert_per_job_and_rule_and_new_ones_ring_the_bell(db, owner):
    first = _raise(db)
    assert first["new"] is True and bells(db) == 1
    again = _raise(db)
    assert again["new"] is False and again["id"] == first["id"] and again["times_seen"] == 2
    assert bells(db) == 1                                   # refreshing does not ring again
    worse = _raise(db, severity="urgent")
    assert worse["severity"] == "urgent" and bells(db) == 2  # it got worse: ring
    _raise(db, rule="SF-STALE-01", severity="watch")
    assert bells(db) == 2                                   # watch never rings
    assert db.query(AiAlert).filter_by(kind="owen_ai_issue").first().user_id == owner[0].id


def test_bad_input_is_refused(db, owner):
    with pytest.raises(ValueError):
        _raise(db, severity="critical")
    assert alerts.tool_report_issue({"job_uid": "u", "rule_id": "X", "severity": "today",
                                     "title": ""}).startswith("ERROR")


def test_a_dismissed_alert_stays_quiet_unless_it_gets_worse(db, owner):
    a = _raise(db)
    alerts.close(db, db.get(OwenAiAlert, a["id"]), "dismissed", "handled by phone")
    db.commit()
    quiet = _raise(db)
    assert quiet.get("suppressed") is True
    assert db.query(OwenAiAlert).filter_by(status="open").count() == 0
    assert "dismissed" in alerts.tool_report_issue({
        "job_uid": "uid-1", "job_number": "801", "rule_id": "SF-CONFIRM-01",
        "severity": "today", "title": "x", "evidence": "y"})
    worse = _raise(db, severity="urgent")
    assert worse["new"] is True and worse["status"] == "open"


def test_settle_resolves_only_what_the_audit_did_not_find_again(db, owner):
    _raise(db, rule="SF-A")
    started = datetime.now(UTC) + timedelta(seconds=1)
    later = _raise(db, rule="SF-B")
    db.get(OwenAiAlert, later["id"]).last_seen_at = started + timedelta(seconds=5)
    db.commit()
    assert alerts.settle_job(db, "uid-1", started) == 1
    assert {a.rule_id for a in alerts.open_for(db)} == {"SF-B"}


# ------------------------------------------------------------------------------- sweep

def test_the_sweep_is_due_once_per_slot_on_working_days(db, owner):
    assert runner.due_slot(db, TUESDAY_0745) == "07:30"
    assert runner.due_slot(db, TUESDAY_0745.replace(hour=7, minute=0)) is None   # too early
    assert runner.due_slot(db, TUESDAY_0745.replace(hour=11)) is None            # > 3 h late
    assert runner.due_slot(db, datetime(2026, 11, 8, 7, 45, tzinfo=NY)) is None  # Sunday
    runner.start_sweep(db, "07:30", [], TUESDAY_0745)
    assert runner.due_slot(db, TUESDAY_0745) is None                             # already ran
    assert runner.due_slot(db, TUESDAY_0745.replace(hour=13, minute=5)) == "13:00"


def test_no_sweep_without_times_login_or_switch(db, owner, monkeypatch):
    calls = []
    monkeypatch.setenv("OWEN_AI_SWEEP_TIMES", "")
    assert runner.maybe_sweep(db, TUESDAY_0745, calls.append) is None
    monkeypatch.setenv("OWEN_AI_SWEEP_TIMES", "7:30")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    assert runner.maybe_sweep(db, TUESDAY_0745, calls.append) is None
    assert calls == []


def test_a_zuper_failure_makes_no_half_sweep(db, owner):
    def boom(board):
        raise RuntimeError("zuper down")
    assert runner.maybe_sweep(db, TUESDAY_0745, boom) is None
    assert db.query(OwenAiThread).filter_by(kind="audit").count() == 0


class AuditTurn:
    """Plays Claude: for job uid-1 it reports one problem; for uid-2 it fails."""
    def __init__(self, db):
        self.db, self.calls = db, []

    def __call__(self, prompt, session_id, note):
        self.calls.append((prompt, session_id, note))
        if "uid-2" in prompt:
            return agent.TurnResult(text="", session_id="x", error="CLI crashed")
        if "uid-1" in prompt:
            alerts.tool_report_issue({"job_uid": "uid-1", "job_number": "801",
                                      "rule_id": "SF-CONFIRM-01", "severity": "urgent",
                                      "title": "Repair tomorrow not confirmed",
                                      "evidence": "no call", "action": "Call now"})
        return agent.TurnResult(text="#801 · 1 issue", session_id="fresh-%d" % len(self.calls))


def test_a_sweep_audits_each_job_fresh_owner_first_then_summarises(db, owner):
    jobs = [{"uid": "uid-1", "number": "801", "stage": "Repair Day"},
            {"uid": "uid-2", "number": "802", "stage": "Quality Call"},
            {"uid": "uid-3", "number": "803", "stage": "Handoff Meeting"}]
    # Stale alerts: uid-3's is fixed (resolved by a successful audit), uid-2's audit fails.
    old3 = _raise(db, rule="SF-OLD", job="uid-3")
    old2 = _raise(db, rule="SF-OLD", job="uid-2")
    th = runner.maybe_sweep(db, TUESDAY_0745, lambda board: jobs)
    assert th is not None and th.title == "Audit — Tue Nov 3, 7:30 AM"
    chat = OwenAiThread(user_id=owner[0].id, kind="task", title="mine", session_id="s-chat")
    db.add(chat)
    db.flush()
    db.add(OwenAiMessage(thread_id=chat.id, role="user", content="hi", status="queued"))
    db.commit()
    turn = AuditTurn(db)
    while runner.run_once(db, turn):
        pass
    assert turn.calls[0][0] == "hi" and turn.calls[0][1] == "s-chat"     # the owner first
    audit_calls = turn.calls[1:]
    assert len(audit_calls) == 3 and all(c[1] is None for c in audit_calls)  # fresh sessions
    assert "uid-1" in audit_calls[0][2]                                   # the job in the note
    db.refresh(th)
    assert th.session_id is None                                          # never saved
    assert db.get(OwenAiAlert, old3["id"]).status == "resolved"
    assert db.get(OwenAiAlert, old2["id"]).status == "open"              # failed audit: kept
    summary = db.query(OwenAiMessage).filter_by(thread_id=th.id, role="assistant") \
        .order_by(OwenAiMessage.id.desc()).first()
    assert summary.content.startswith("## Audit — Tue Nov 3, 7:30 AM — done")
    assert "2 jobs audited** (1 failed)" in summary.content
    assert "[urgent] #801 — Repair tomorrow not confirmed → Call now" in summary.content


# --------------------------------------------------------------------------------- api

def test_alerts_api_is_the_owners_only_and_dismiss_closes(db, owner):
    _, heads = owner
    a = _raise(db, severity="urgent")
    _raise(db, rule="SF-W", severity="watch")
    c = TestClient(app)
    assert c.get("/api/owen-ai/alerts", headers=heads["admin"]).status_code == 404
    got = c.get("/api/owen-ai/alerts", headers=heads["owner"]).json()
    assert [x["severity"] for x in got["alerts"]] == ["urgent", "watch"]
    assert got["open_counts"] == {"urgent": 1, "today": 0, "watch": 1}
    r = c.post("/api/owen-ai/alerts/%d/dismiss" % a["id"], json={"why": "called already"},
               headers=heads["owner"])
    assert r.status_code == 200 and r.json()["status"] == "dismissed"
    assert "called already" in r.json()["resolution"]
    assert c.post("/api/owen-ai/alerts/%d/dismiss" % a["id"], json={"why": "x"},
                  headers=heads["admin"]).status_code == 404

"""The field technician briefing (app/tech_briefing), by behaviour - the important ones.

Rules (pure): due only after the send time and for tomorrow; visits grouped by technician (the
Technician field, else the first assigned); a job line shows what was recorded, says "not
recorded", and marks a suggestion; the text is split between jobs, never inside one.
Service (DispatchJobs for tomorrow, DeepSeek scripted, the text recorded): Shadow records and
sends nothing; Automatic sends ONE text to the mapped phone, never twice a day; before 6 PM
nothing; no phone is skipped with a note; test only ignores other technicians; recorded tools
win over the AI; an AI suggestion with money is dropped.
"""
import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from app import crmlink
from app.ai import vault
from app.dispatch import ai as dai
from app.models import (
    AgentPermission,
    AgentRegistryEntry,
    AiConnection,
    DispatchJob,
    TechBriefing,
)
from app.tech_briefing import rules, service
from tests.ai_support import openai_completion

NOW = datetime(2026, 10, 21, 22, 30, tzinfo=UTC)               # Wed 6:30 PM New York
TOMORROW = date(2026, 10, 22)
T9 = datetime(2026, 10, 22, 13, 0, tzinfo=UTC)                  # Thu 9 AM New York


def job(n, tech="Owen Buzaglo", start=T9, **kw):
    return SimpleNamespace(job_number=n, technician=tech, assigned=[], scheduled_start=start,
                           scheduled_end=start + timedelta(hours=2), is_open=True,
                           status="Repair Scheduled", customer_name="Ana Diaz",
                           address="1 Palm St", city="Bradenton", title="Leak repair",
                           fields=kw.get("fields", {}))


# ------------------------------------------------------------------------- rules

def test_due_only_after_the_send_time_and_for_tomorrow():
    assert rules.due(NOW, 18, 0) == TOMORROW
    assert rules.due(NOW - timedelta(hours=1), 18, 0) is None              # 5:30 PM
    sun = datetime(2026, 10, 25, 23, 0, tzinfo=UTC)                         # Sun 7 PM
    assert rules.due(sun, 18, 0) == date(2026, 10, 26)


def test_grouped_by_technician_else_assigned_and_ordered():
    a = job("1", start=T9 + timedelta(hours=3))
    b = job("2")
    c = job("3", tech=None)
    c.assigned = ["Antonio Brown"]
    d = job("4", start=T9 + timedelta(days=1))                             # not tomorrow
    e = job("5")
    e.status = "Reschedule Required"
    g = rules.by_technician([a, b, c, d, e], TOMORROW, skip_stages=["Reschedule Required"])
    assert [j.job_number for j in g["Owen Buzaglo"]] == ["2", "1"]
    assert [j.job_number for j in g["Antonio Brown"]] == ["3"]


def test_a_job_line_shows_recorded_missing_and_suggested():
    rec = rules.job_line(job("701", fields={"Leak location": "Over the garage",
                                            "Tools & materials needed": "Tile, mortar",
                                            "Estimated time to complete": "4 hours"}),
                         {}, "repair", "should not show")
    assert "Leak: Over the garage." in rec.text and "Bring: Tile, mortar." in rec.text
    assert "suggested" not in rec.text and "Time: 4 hours." in rec.text
    bare = rules.job_line(job("702"), {"leak_location": {"value": "north valley"}}, "repair")
    assert "Leak: north valley." in bare.text and "Bring: not recorded." in bare.text
    tip = rules.job_line(job("703"), {}, "repair", "ladder, sealant")
    assert "Bring (suggested - check the inspection): ladder, sealant." in tip.text


def test_long_briefings_split_between_jobs():
    lines = ["x" * 400, "y" * 400, "z" * 400]
    parts = rules.texts("Owen Buzaglo", TOMORROW, lines, limit=1000)
    assert len(parts) == 2 and all(len(p) <= 1000 for p in parts)
    assert "y" * 400 in parts[0] and "z" * 400 in parts[1]
    assert parts[0].startswith("Dream Team Roofing - Owen, your jobs for Thu Oct 22:")


# ------------------------------------------------------------------------- the service

@pytest.fixture()
def tb(db, monkeypatch, secrets_key):
    monkeypatch.setenv("TECH_BRIEFING_ENABLED", "true")
    conn = AiConnection(name="Deepseek", provider="deepseek",
                        api_key_encrypted=vault.encrypt("sk-test-1"), api_key_last4="1",
                        default_model="deepseek-flash")
    db.add(conn)
    db.flush()
    s = dai.settings(db, for_update=True)
    s.ai_enabled, s.connection_id, s.model = True, conn.id, "deepseek-flash"
    db.add(AgentRegistryEntry(key="inspection_docs", config={
        "technicians": {"Owen Buzaglo": "5616905516"}}))
    for n, tech, fields in (("701", "Owen Buzaglo", {"Tools & materials needed": "Tile"}),
                            ("702", "Owen Buzaglo", {"Leak location": "Chimney"}),
                            ("900", "Antonio Brown", {})):
        db.add(DispatchJob(job_uid="j" + n, job_number=n, board="AHS - Repair & Review",
                           status="Repair Scheduled", customer_name="Customer " + n,
                           address="1 Palm St", is_open=True, scheduled_start=T9,
                           scheduled_end=T9 + timedelta(hours=2), technician=tech,
                           fields=fields))
    db.commit()
    sent = []
    monkeypatch.setattr(crmlink, "configured", lambda: True)
    monkeypatch.setattr(crmlink, "send_sms", lambda to, body, **k: sent.append((to, body))
                        or crmlink.LinkResult(True, 200, "", {}))
    return sent


def tip(**jobs):
    return openai_completion(content=json.dumps(jobs), model="deepseek-flash")


def test_shadow_records_and_sends_nothing(db, tb, script):
    script.responses = [tip(**{"702": "ladder, flashing, sealant"})]
    out = service.run(db, NOW)
    assert out == {"briefed": 1, "skipped": 0}, out
    row = db.query(TechBriefing).one()                         # test only: Owen, not Antonio
    assert row.state == "shadow" and tb == []
    assert "Bring: Tile." in row.body                          # recorded wins
    assert "Bring (suggested - check the inspection): ladder, flashing, sealant" in row.body
    assert len(script.requests) == 1                           # only 702 needed a suggestion
    assert "701" not in json.dumps(script.requests[0]["body"]["messages"][-1])


def test_automatic_sends_once_to_the_mapped_phone(db, tb, script):
    db.add(AgentPermission(entry_key="tech_briefing", action="text_staff", level="auto"))
    db.commit()
    script.responses = [tip(**{"702": "Price $200 for flashing"})]   # money: dropped
    service.run(db, NOW)
    service.run(db, NOW + timedelta(minutes=10))
    assert len(tb) == 1 and tb[0][0] == "+15616905516"
    assert "$" not in tb[0][1] and "Bring: not recorded." in tb[0][1]
    assert db.query(TechBriefing).one().state == "sent"


def test_nothing_before_the_send_time_and_no_phone_is_skipped(db, tb, script):
    assert service.run(db, NOW - timedelta(hours=1)) == {"briefed": 0, "skipped": 0}
    db.add(AgentPermission(entry_key="tech_briefing", action="text_staff", level="auto"))
    db.add(AgentRegistryEntry(key="tech_briefing", config={"test_technicians":
                                                          ["Antonio Brown"]}))
    db.commit()
    script.responses = [tip()]
    out = service.run(db, NOW)
    row = db.query(TechBriefing).one()
    assert out["skipped"] == 1 and row.technician == "Antonio Brown" and tb == []
    assert "no phone for Antonio Brown" in row.note


def test_each_job_carries_its_zuper_link():
    """2026-10-22: the technician can open every job in Zuper from the text."""
    url = "https://next.zuperpro.com/jobs/uid-701/details"
    line = rules.job_line(job("701"), {}, "inspection", link=url)
    assert line.text.endswith("Zuper: " + url)
    assert "Zuper:" not in rules.job_line(job("702"), {}, "inspection").text
    parts = rules.texts("Luis Perez", date(2026, 10, 23), [line.text] * 12, 1000)
    assert all(len(p) <= 1000 for p in parts) and sum(p.count(url) for p in parts) == 12


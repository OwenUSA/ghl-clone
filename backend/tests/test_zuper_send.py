"""Send to Zuper, the mirror locks, AHS auto-send, AI agents and the lead outcome — the owner's
revised decisions (2026-09-16, v2), against a fake Zuper at the HTTP boundary.

Behaviour, not status codes: every refusal is followed by a re-read proving nothing changed,
and every send by what the fake Zuper holds.
"""
import json
from datetime import UTC, datetime, timedelta

import pytest
from app.ai import actions as ai_actions
from app.db import SessionLocal
from app.models import (
    AiSuggestion,
    Appointment,
    Contact,
    Job,
    Opportunity,
    OpportunityTask,
    Role,
    User,
)
from app.zuper import api as zuper_api
from app.zuper import mapping
from sqlalchemy import func, select
from tests.ai_support import anthropic_message, config_for, text, tool_use
from tests.zuper_support import arm, drain, mapping_row, uid_of


def send(world, who: str, opp_id: int):
    return world.client(who).post("/api/opportunities/%d/zuper/send" % opp_id)


def jobs_titled(fake, title: str) -> list[dict]:
    return [j for j in fake.jobs.values() if j.get("job_title") == title]


def card_row(opp_id: int) -> tuple:
    with SessionLocal() as s:
        o = s.get(Opportunity, opp_id)
        return (o.title, o.stage_id, o.pipeline_id, o.status, o.value_cents, o.owner_id,
                o.address_street, o.address_city, o.lead_outcome, o.position)


# ------------------------------------------------------------------ Send to Zuper

def test_only_an_admin_or_unrestricted_dispatcher_may_send(loaded, fake):
    card = loaded.ids["follow_card"]
    for who in ("tess", "terry"):
        r = send(loaded, who, card)
        assert r.status_code == 403
    with SessionLocal() as s:
        s.get(User, loaded.ids["dana"]).only_assigned_data = True
        s.commit()
    assert send(loaded, "dana", card).status_code == 403
    with SessionLocal() as s:
        assert mapping_row(s, "opportunity", card) is None
        assert s.scalar(select(func.count(Job.id)).where(Job.type == "zuper_send")) == 0
    detail = loaded.client("terry").get("/api/opportunities/%d" % card).json()["zuper"]
    assert detail["may_send"] is False and detail["can_send"] is False


def test_send_refuses_with_sentences_until_name_phone_and_job_address_are_there(armed, fake):
    c = armed.client("owner")
    writes = len(fake.writes())
    who = c.post("/api/contacts", json={"first_name": "Nora", "last_name": "Nophone",
                                        "email": "nora@example.test"}).json()
    card = c.post("/api/opportunities", json={
        "title": "Nora - leak", "pipeline_id": armed.ids["retail"], "contact_id": who["id"],
        "stage_id": armed.ids["stages"]["retail:Scheduled"]}).json()["id"]
    detail = c.get("/api/opportunities/%d" % card).json()["zuper"]
    assert detail["state"] == "not_sent" and detail["may_send"] is True
    assert detail["can_send"] is False
    assert "The customer needs a phone number." in detail["send_problems"]
    assert any("missing: street, city, state, ZIP code" in p for p in detail["send_problems"])
    r = send(armed, "owner", card)
    assert r.status_code == 409
    assert "needs a phone number" in r.json()["detail"]
    with SessionLocal() as s:
        assert mapping_row(s, "opportunity", card) is None
    drain()
    assert len(fake.writes()) == writes


def test_send_refuses_while_the_sync_is_off(zworld, fake):
    r = send(zworld, "owner", zworld.ids["follow_card"])
    assert r.status_code == 409 and "not switched on" in r.json()["detail"]
    with SessionLocal() as s:
        assert mapping_row(s, "opportunity", zworld.ids["follow_card"]) is None
    assert fake.requests == []


def test_send_creates_the_customer_and_job_once_and_a_second_press_does_nothing(armed, fake):
    card = armed.ids["follow_card"]
    r = send(armed, "dana", card)
    assert r.status_code == 202 and r.json() == {"state": "queued"}
    again = send(armed, "owner", card)
    assert again.status_code == 200 and again.json() == {"state": "queued", "already": True}
    detail = armed.client("owner").get("/api/opportunities/%d" % card).json()["zuper"]
    assert detail["state"] == "queued" and "title" in detail["locked_fields"]
    drain()
    assert len(jobs_titled(fake, "Ann Shared - follow up")) == 1
    customers = [c for c in fake.customers.values()
                 if mapping.custom_values(c).get("CRM Contact ID") == str(armed.ids["ann"])]
    assert len(customers) == 1
    writes = len(fake.writes())
    third = send(armed, "owner", card)
    assert third.json() == {"state": "sent", "already": True}
    drain()
    assert len(fake.writes()) == writes
    detail = armed.client("owner").get("/api/opportunities/%d" % card).json()["zuper"]
    assert detail["state"] == "sent" and detail["job_uid"] == uid_of("opportunity", card)
    assert detail["job_url"].endswith("/jobs/%s/details" % detail["job_uid"])
    board = armed.client("owner").get("/api/opportunities?pipeline_id=%d"
                                      % armed.ids["retail"]).json()
    managed = {o["id"]: o["managed_in_zuper"] for o in board}
    assert managed[card] is True and managed[armed.ids["tim_card"]] is False


def test_a_send_zuper_refuses_is_shown_as_failed_and_can_be_pressed_again(armed, fake):
    card = armed.ids["follow_card"]
    fake.fail_next["POST ^/jobs$"] = 1
    send(armed, "owner", card)
    with SessionLocal() as s:
        from app.zuper import worker
        worker.drain(s)
        job = s.scalar(select(Job).where(Job.type == "zuper_send"))
        job.attempts = job.max_attempts          # the last try
        job.status, job.run_after = "pending", datetime.now(UTC) - timedelta(seconds=1)
        s.commit()
    fake.fail_next["POST ^/jobs$"] = 1
    drain()
    detail = armed.client("owner").get("/api/opportunities/%d" % card).json()["zuper"]
    assert detail["state"] == "failed" and "Zuper could not be reached" in detail["error"]
    assert detail["can_send"] is True
    assert send(armed, "owner", card).status_code == 202
    drain()
    assert len(jobs_titled(fake, "Ann Shared - follow up")) == 1


# ------------------------------------------------------------------ AHS email auto-send

def ahs_order(**kw) -> dict:
    body = {"ahs_job_id": "77001234", "service": "ROOF", "customer_name": "Paula Warranty",
            "phone": "+19415550333", "email": "paula@example.test",
            "service_address": "12 Bay St, Bradenton, FL 34205", "value_cents": 0,
            "description": "Roof leak over kitchen", "message_id": "<m1@dispatch.me>",
            "received_at": "2026-09-16T13:05:00+00:00"}
    body.update(kw)
    return body


def test_an_ahs_email_card_is_sent_automatically(armed, fake):
    r = armed.client("owner").post("/api/ahs-jobs", json=ahs_order())
    assert r.status_code == 201, r.text
    card = r.json()["opportunity"]["id"]
    drain()
    job = fake.jobs[uid_of("opportunity", card)]
    assert mapping.custom_values(job)["AHS Job ID"] == "77001234"


def test_an_ahs_email_card_is_not_sent_while_the_sync_is_off(zworld, fake):
    arm(enable=False)
    r = zworld.client("owner").post("/api/ahs-jobs", json=ahs_order())
    assert r.status_code == 201, r.text
    card = r.json()["opportunity"]["id"]
    with SessionLocal() as s:
        assert mapping_row(s, "opportunity", card) is None
        assert s.scalar(select(func.count(Job.id)).where(Job.type.like("zuper_%"))) == 0
    drain()
    assert fake.jobs == {}


# ------------------------------------------------------------------ the mirror locks

LOCKED_EDITS = [
    ("title", "Retitled"), ("stage_id", "ahs:Call Back"), ("value_cents", 5),
    ("address_street", "99 Other Rd"), ("owner_id", "dana"), ("status", "lost"),
    ("pipeline_id", "retail"),
]


@pytest.mark.parametrize("field,value", LOCKED_EDITS)
def test_the_modal_refuses_every_zuper_field_on_a_sent_card(loaded, fake, field, value):
    card = loaded.ids["jane_card"]
    body = {field: value}
    if field == "stage_id":
        body = {"stage_id": loaded.ids["stages"][value]}
    elif field == "owner_id":
        body = {"owner_id": loaded.ids["dana"]}
    elif field == "pipeline_id":
        body = {"pipeline_id": loaded.ids["retail"],
                "stage_id": loaded.ids["stages"]["retail:Scheduled"]}
    before = card_row(card)
    writes = len(fake.writes())
    r = loaded.client("owner").patch("/api/opportunities/%d/detail" % card, json=body)
    assert r.status_code == 409, r.text
    assert r.json()["detail"].startswith("Change this in Zuper")
    assert card_row(card) == before
    drain()
    assert len(fake.writes()) == writes


def test_every_other_path_to_a_sent_job_is_refused_and_nothing_changes(loaded, fake):
    ids = loaded.ids
    card = ids["jane_card"]
    c = loaded.client("owner")
    before = card_row(card)
    with SessionLocal() as s:
        visit = s.get(Appointment, ids["visit"])
        visit_before = (visit.starts_at, visit.status, visit.title)
        jane = s.get(Contact, ids["jane"])
        contact_before = (jane.first_name, jane.phone, jane.email, jane.address_street)
    refused = [
        c.patch("/api/opportunities/%d" % card, json={"stage_id": ids["stages"]["ahs:Call Back"]}),
        c.post("/api/opportunities/bulk/stage", json={
            "ids": [card, ids["noaddr_card"]], "stage_id": ids["stages"]["ahs:Call Back"]}),
        c.post("/api/opportunities/bulk/owner", json={"ids": [card], "owner_id": ids["dana"]}),
        c.post("/api/appointments", json={
            "title": "Extra visit", "calendar_id": ids["calendar"], "contact_id": ids["jane"],
            "opportunity_id": card, "starts_at": "2026-10-02T13:00:00Z",
            "ends_at": "2026-10-02T15:00:00Z"}),
        c.patch("/api/appointments/%d" % ids["visit"], json={
            "starts_at": "2026-10-03T13:00:00Z", "ends_at": "2026-10-03T14:00:00Z"}),
        c.delete("/api/appointments/%d" % ids["visit"]),
        c.patch("/api/contacts/%d" % ids["jane"], json={"first_name": "Janet"}),
        c.patch("/api/contacts/%d" % ids["jane"], json={"phone": "(941) 555-0999"}),
        c.patch("/api/contacts/%d" % ids["jane"], json={"email": "other@example.test"}),
    ]
    for r in refused:
        assert r.status_code == 409, (r.request.method, r.request.url, r.text)
        assert r.json()["detail"].startswith("Change this in Zuper")
    with SessionLocal() as s:
        visit = s.get(Appointment, ids["visit"])
        assert (visit.starts_at, visit.status, visit.title) == visit_before
        jane = s.get(Contact, ids["jane"])
        assert (jane.first_name, jane.phone, jane.email, jane.address_street) == contact_before
        assert s.scalar(select(func.count(Appointment.id)).where(
            Appointment.title == "Extra visit")) == 0
        assert s.get(Opportunity, ids["noaddr_card"]).stage_id == ids["stages"]["ahs:New Lead"]
    assert card_row(card) == before


def test_notes_checklist_and_tasks_stay_editable_on_a_sent_card_and_are_pushed(loaded, fake):
    card = loaded.ids["jane_card"]
    c = loaded.client("owner")
    detail = c.get("/api/opportunities/%d" % card).json()
    echo = c.patch("/api/opportunities/%d/detail" % card, json={
        "title": detail["title"], "stage_id": detail["stage_id"],
        "custom_fields": {"checklist_roof_material": "Metal"}})
    assert echo.status_code == 200, echo.text           # an unchanged echo is not a change
    assert c.post("/api/opportunities/%d/notes" % card, json={"body": "Tarped"}).status_code == 201
    assert c.post("/api/opportunities/%d/tasks" % card,
                  json={"title": "Order tile"}).status_code == 201
    drain()
    job_uid = uid_of("opportunity", card)
    assert mapping.custom_values(fake.jobs[job_uid])["Roof material"] == "Metal"
    assert "Tarped" in [n["note"] for n in fake.notes[job_uid]]
    assert "Order tile" in [t["service_task_title"] for t in fake.tasks[job_uid]]


def test_contact_details_are_locked_only_for_a_customer_with_a_sent_job(loaded, fake):
    c = loaded.client("owner")
    tim = c.get("/api/contacts/%d" % loaded.ids["tim"]).json()
    assert tim["zuper_locked"] is False and tim["zuper_locked_fields"] == []
    assert c.patch("/api/contacts/%d" % loaded.ids["tim"],
                   json={"first_name": "Timothy"}).status_code == 200
    jane = c.get("/api/contacts/%d" % loaded.ids["jane"]).json()
    assert jane["zuper_locked"] is True and "phone" in jane["zuper_locked_fields"]
    # Tags, owner and other CRM-only details stay editable.
    assert c.patch("/api/contacts/%d" % loaded.ids["jane"],
                   json={"dnd": True}).status_code == 200


def test_the_calendar_marks_a_sent_job_s_visits_read_only(loaded, fake):
    c = loaded.client("owner")
    rows = {a["id"]: a["zuper_locked"] for a in c.get(
        "/api/appointments?start=2026-09-01T00:00:00Z&end=2026-10-31T00:00:00Z").json()}
    assert rows[loaded.ids["visit"]] is True and rows[loaded.ids["tim_visit"]] is False
    assert c.get("/api/appointments/%d" % loaded.ids["visit"]).json()["zuper_locked"] is True


# ------------------------------------------------------------------ AI agents

def agent_ctx(world, opportunity_id: int, contact_id: int, approved_by=None):
    s = SessionLocal()
    return s, ai_actions.Context(db=s, agent_id=1, agent_name="Receptionist", config={},
                                 subject=ai_actions.Subject(contact_id, opportunity_id),
                                 approved_by=approved_by)


def test_an_agent_s_change_request_on_a_sent_job_is_an_urgent_task_and_zero_zuper_writes(
        loaded, fake):
    ids = loaded.ids
    writes = len(fake.writes())
    before = card_row(ids["jane_card"])
    future = (datetime.now(UTC) + timedelta(days=5)).strftime("%Y-%m-%dT10:00")
    for name, args in (("reschedule_appointment", {"appointment_id": ids["visit"],
                                                   "starts_at": future}),
                       ("cancel_appointment", {"appointment_id": ids["visit"]}),
                       ("move_stage", {"stage_id": ids["stages"]["ahs:Call Back"]}),
                       ("book_appointment", {"starts_at": future,
                                             "calendar_id": ids["calendar"]})):
        s, ctx = agent_ctx(loaded, ids["jane_card"], ids["jane"])
        with s:
            action = ai_actions.CATALOGUE[name]
            prep = action.prepare(ctx, dict(args))
            assert prep.data.get("change_request") is True, name
            result = action.execute(ctx, prep)
            s.commit()
        assert result["done_in_zuper"] is False
    with SessionLocal() as s:
        tasks = s.scalars(select(OpportunityTask).where(
            OpportunityTask.opportunity_id == ids["jane_card"],
            OpportunityTask.priority == "urgent")).all()
        assert len(tasks) == 4
        assert all("change it in Zuper (job %s)" % uid_of("opportunity", ids["jane_card"])
                   in t.title for t in tasks)
        assert s.get(Appointment, ids["visit"]).status == "confirmed"
    drain()
    new_writes = list(fake.writes()[writes:])
    # Only the urgent tasks themselves travel (tasks sync both ways); nothing about the job.
    assert {w.path.split("/")[-1] for w in new_writes} <= {"service_tasks"}
    assert card_row(ids["jane_card"]) == before


def test_the_agent_s_context_reads_the_mirrored_job(loaded, fake):
    s, ctx = agent_ctx(loaded, ids := loaded.ids["jane_card"], loaded.ids["jane"])
    with s:
        out = ai_actions.build_context(ctx)
    card = next(o for o in out["opportunities"] if o["id"] == ids)
    assert card["managed_in_zuper"] is True
    assert card["zuper_job"]["status"] == "Inspection"
    assert [v["title"] for v in card["zuper_job"]["visits"]] == ["Inspection"]
    assert "never tell the customer it is done" in card["zuper_job"]["rule"]


def test_an_agent_cannot_send_it_can_only_suggest(loaded, fake):
    s, ctx = agent_ctx(loaded, loaded.ids["follow_card"], loaded.ids["ann"])
    with s:
        action = ai_actions.CATALOGUE["suggest_send_to_zuper"]
        assert action.kind == ai_actions.SUGGEST_ONLY
        prep = action.prepare(ctx, {})
        with pytest.raises(ai_actions.Refused, match="cannot send"):
            action.execute(ctx, prep)
    with SessionLocal() as s:
        assert mapping_row(s, "opportunity", loaded.ids["follow_card"]) is None


def ai_agent(world, secrets_key, mode: str, actions: list[str]) -> int:
    admin = world.client("owner")
    r = admin.post("/api/ai/connections", json={
        "name": "Claude", "provider": "anthropic", "api_key": "sk-ant-test-0000",
        "default_model": "claude-sonnet-5", "price_input": "2.00", "price_output": "10.00"})
    assert r.status_code == 201, r.text

    w = type("W", (), {"ids": {"connection": r.json()["id"]}})
    aid = admin.post("/api/ai/agents", json={"name": "Receptionist"}).json()["id"]
    r = admin.patch("/api/ai/agents/%s" % aid, json={"draft": config_for(w, actions=actions)})
    assert r.status_code == 200, r.text
    assert admin.post("/api/ai/agents/%s/publish" % aid).status_code == 201
    r = admin.post("/api/ai/agents/%s/mode" % aid, json={"mode": mode, "confirm": True})
    assert r.status_code == 200, r.text
    return aid


def test_auto_pilot_still_only_suggests_a_send_and_one_approval_sends_once(loaded, fake,
                                                                           script,
                                                                           secrets_key):
    from app.worker import drain_once
    card = loaded.ids["follow_card"]
    aid = ai_agent(loaded, secrets_key, "auto", ["get_context", "suggest_send_to_zuper"])
    script.responses.extend([
        anthropic_message([tool_use("suggest_send_to_zuper", {"reason": "booked"}, "a")],
                          "tool_use"),
        anthropic_message([text("Suggested.")])])
    r = loaded.client("dana").post("/api/ai/agents/%s/run" % aid,
                                   json={"opportunity_id": card})
    assert r.status_code in (200, 201, 202), r.text
    while drain_once():
        pass
    with SessionLocal() as s:
        assert mapping_row(s, "opportunity", card) is None
        suggestion = s.scalar(select(AiSuggestion).where(
            AiSuggestion.action == "suggest_send_to_zuper"))
        assert suggestion is not None and suggestion.status == "pending"
    ok = loaded.client("dana").post("/api/ai/suggestions/%s/approve" % suggestion.id)
    assert ok.status_code == 200 and ok.json()["status"] == "approved", ok.text
    assert loaded.client("owner").post(
        "/api/ai/suggestions/%s/approve" % suggestion.id).status_code == 409
    drain()
    assert len(jobs_titled(fake, "Ann Shared - follow up")) == 1


def test_the_lead_outcome_action_suggests_in_suggest_mode_and_sets_on_auto(loaded, fake):
    s, ctx = agent_ctx(loaded, loaded.ids["follow_card"], loaded.ids["ann"])
    with s:
        action = ai_actions.CATALOGUE["set_lead_outcome"]
        with pytest.raises(ai_actions.Refused, match="needs a note"):
            action.prepare(ctx, {"outcome": "Other"})
        prep = action.prepare(ctx, {"outcome": "Price shopping"})
        assert action.kind == ai_actions.WRITE
        action.execute(ctx, prep)
        s.commit()
    with SessionLocal() as s:
        assert s.get(Opportunity, loaded.ids["follow_card"]).lead_outcome == "Price shopping"


# ------------------------------------------------------------------ lead outcome

def test_closing_a_card_never_booked_needs_a_lead_outcome(zworld):
    c = zworld.client("owner")
    card = zworld.ids["follow_card"]
    r = c.patch("/api/opportunities/%d/detail" % card, json={"status": "lost"})
    assert r.status_code == 400 and "Choose a lead outcome" in r.json()["detail"]
    r = c.patch("/api/opportunities/%d/detail" % card, json={"status": "abandoned",
                                                              "lead_outcome": "Other"})
    assert r.status_code == 400 and "needs a note" in r.json()["detail"]
    r = c.patch("/api/opportunities/%d/detail" % card, json={"status": "lost",
                                                              "lead_outcome": "Nope"})
    assert r.status_code == 400 and "is not a lead outcome" in r.json()["detail"]
    with SessionLocal() as s:
        o = s.get(Opportunity, card)
        assert (o.status, o.lead_outcome) == ("open", None)
    r = c.patch("/api/opportunities/%d/detail" % card, json={
        "status": "lost", "lead_outcome": "Other", "lead_outcome_note": "Moved to Ohio"})
    assert r.status_code == 200, r.text
    assert (r.json()["lead_outcome"], r.json()["lead_outcome_note"]) == ("Other", "Moved to Ohio")
    with SessionLocal() as s:
        assert s.get(Opportunity, card).lead_outcome_set_at is not None


def test_a_booked_card_closes_without_an_outcome(zworld):
    r = zworld.client("owner").patch("/api/opportunities/%d/detail" % zworld.ids["tim_card"],
                                     json={"status": "lost"})
    assert r.status_code == 200, r.text
    assert r.json()["booked"] is True and r.json()["lead_outcome"] is None


def test_a_card_created_closed_needs_an_outcome_and_a_tech_cannot_set_one(zworld):
    c = zworld.client("owner")
    body = {"title": "Spam call", "pipeline_id": zworld.ids["retail"],
            "stage_id": zworld.ids["stages"]["retail:New Lead"],
            "contact_id": zworld.ids["leo"], "status": "lost"}
    r = c.post("/api/opportunities", json=body)
    assert r.status_code == 400 and "Choose a lead outcome" in r.json()["detail"]
    with SessionLocal() as s:
        assert s.scalar(select(Opportunity).where(Opportunity.title == "Spam call")) is None
    assert c.post("/api/opportunities", json={**body, "lead_outcome": "Spam"}).status_code == 201
    r = zworld.client("terry").patch("/api/opportunities/%d/detail" % zworld.ids["follow_card"],
                                     json={"lead_outcome": "Spam"})
    assert r.status_code == 403
    with SessionLocal() as s:
        assert s.get(Opportunity, zworld.ids["follow_card"]).lead_outcome is None


def test_bulk_lead_outcome_and_the_report_by_source_and_campaign(zworld):
    c = zworld.client("owner")
    ids = zworld.ids
    with SessionLocal() as s:
        o = s.get(Opportunity, ids["tim_card"])
        o.custom_fields = {"owen_campaign": "Retail Google Ads"}
        o.source = "Google"
        s.commit()
    r = c.post("/api/opportunities/bulk/lead-outcome", json={
        "ids": [ids["follow_card"], ids["tim_card"]], "lead_outcome": "No response"})
    assert r.status_code == 200 and sorted(r.json()["updated"]) == sorted(
        [ids["follow_card"], ids["tim_card"]])
    bad = c.post("/api/opportunities/bulk/lead-outcome", json={
        "ids": [ids["other_card"]], "lead_outcome": "Other"})
    assert bad.status_code == 400
    with SessionLocal() as s:
        assert s.get(Opportunity, ids["other_card"]).lead_outcome is None
    c.patch("/api/opportunities/%d/detail" % ids["other_card"],
            json={"lead_outcome": "Spam", "source": "Craigslist"})
    report = c.get("/api/reports/lead-outcomes").json()
    assert report["total"] == 3
    by_source = {row["source"]: row["counts"] for row in report["by_source"]}
    assert by_source["Google"] == {"No response": 2}
    assert by_source["Craigslist"] == {"Spam": 1}
    by_campaign = {row["campaign"]: row["counts"] for row in report["by_campaign"]}
    assert by_campaign["Retail Google Ads"] == {"No response": 1}
    assert by_campaign[None] == {"No response": 1, "Spam": 1}
    later = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert c.get("/api/reports/lead-outcomes", params={"since": later}).json()["total"] == 0
    assert zworld.client("tess").get("/api/reports/lead-outcomes").status_code == 403
    assert json.dumps(report).count("Moved to Ohio") == 0


def test_the_send_route_is_on_every_access_audit(zworld):
    from tests.test_only_assigned_data import AUDITED as ASSIGNED
    from tests.test_pipeline_permissions import AUDITED as PIPELINES
    for name in ("zuper_send_opportunity",):
        assert name in ASSIGNED and name in PIPELINES
    assert zuper_api.may_send(None) is False
    assert Role.ADMIN  # the enum stays importable for the role gate

"""The sync's rules for a SENT job (2026-09-16, v2), against a fake Zuper at the HTTP boundary.

Every test starts from a completed day-one load (`loaded`): Jane's AHS card, Bob's Workiz
Retail card (Scheduled) and Al's won Retail card are sent, with their customers, visits, a
note and a task. "A person in Zuper" is the fake's own helpers (they bump `updated_at` without
going through the client); "a person in the CRM" is the API, whose commit queues a job the
test drains like the worker's Zuper thread; "the Workiz importer" is a quiet session.
"""
import io
from datetime import UTC, datetime, timedelta

from app.db import SessionLocal
from app.models import (
    Appointment,
    Contact,
    Conversation,
    ConversationEvent,
    Direction,
    EventType,
    Job,
    Opportunity,
    OpportunityNote,
    OpportunityTask,
    Stage,
    ZuperConflict,
    ZuperDeleteSnapshot,
    ZuperDocument,
    ZuperMapping,
    ZuperSettings,
)
from app.zuper import config, digest, engine, mapping, outcomes, sweep
from sqlalchemy import func, select
from tests.zuper_support import drain, mapping_row, uid_of


def pull(zuper_type: str, uid: str) -> dict:
    with SessionLocal() as s:
        ctx = engine.Ctx(s)
        outcome = engine.pull(ctx, zuper_type, uid)
        s.commit()
        return {"outcome": outcome, **ctx.counts}


def document(kind: str, uid: str) -> None:
    with SessionLocal() as s:
        engine.pull_document(engine.Ctx(s), kind, uid)
        s.commit()


def conflicts() -> list[ZuperConflict]:
    with SessionLocal() as s:
        return list(s.scalars(select(ZuperConflict).order_by(ZuperConflict.id)).all())


def set_cutover(day: str | None) -> None:
    with SessionLocal() as s:
        s.get(ZuperSettings, 1).workiz_cutover_date = day
        s.commit()


def quiet_session(why: str = "test"):
    s = SessionLocal()
    s.info["zuper_quiet"] = why
    return s


# ------------------------------------------------------------------ Zuper owns the job

def test_a_customer_edited_in_zuper_updates_the_contact(loaded, fake):
    uid = uid_of("contact", loaded.ids["jane"])
    fake.edit_customer(uid, customer_email="jane.zuper@example.test",
                       customer_company_name="Roof Co")
    pull("customer", uid)
    with SessionLocal() as s:
        c = s.get(Contact, loaded.ids["jane"])
        assert (c.email, c.business_name) == ("jane.zuper@example.test", "Roof Co")
    assert conflicts() == []                      # only Zuper changed: a propagation


def test_contact_details_changed_in_the_crm_anyway_go_back_to_zuper_s_and_are_logged(loaded,
                                                                                   fake):
    """The API refuses the edit (test_zuper_send); a quiet write — the importer's — is put back
    by the next sync, logged, never pushed to Zuper."""
    uid = uid_of("contact", loaded.ids["jane"])
    with quiet_session() as s:
        s.get(Contact, loaded.ids["jane"]).email = "jane.crm@example.test"
        s.commit()
    fake.edit_customer(uid, customer_email="jane.zuper@example.test")
    writes = len(fake.writes())
    pull("customer", uid)
    with SessionLocal() as s:
        assert s.get(Contact, loaded.ids["jane"]).email == "jane.zuper@example.test"
    assert len(fake.writes()) == writes
    [c] = conflicts()
    assert (c.field, c.rule, c.winner, c.written_to) == ("email", "zuper_owned", "zuper", "crm")
    assert (c.before, c.after) == ("jane.crm@example.test", "jane.zuper@example.test")


def test_a_title_address_and_owner_set_in_zuper_follow_on_the_card(loaded, fake):
    card = loaded.ids["jane_card"]
    uid = uid_of("opportunity", card)
    fake.edit_job(uid, job_title="Jane Roof - full reroof",
                  customer_address={"street": "2 Palm St", "city": "Bradenton", "state": "FL",
                                    "zip_code": "34205"},
                  assigned_to=[{"user_uid": "u-owner"}])
    pull("job", uid)
    with SessionLocal() as s:
        o = s.get(Opportunity, card)
        assert (o.title, o.address_street, o.owner_id) == (
            "Jane Roof - full reroof", "2 Palm St", loaded.ids["owner"])


def test_a_status_move_in_zuper_moves_the_card(loaded, fake):
    card = loaded.ids["jane_card"]
    uid = uid_of("opportunity", card)
    target = loaded.ids["stages"]["ahs:Submit Invoices"]
    fake.move_job(uid, uid_of("stage", target))
    pull("job", uid)
    with SessionLocal() as s:
        o = s.get(Opportunity, card)
        assert o.stage_id == target and o.position == 0
        # Through the ORM: no rule and no agent trigger fired, nothing queued.
        assert s.scalar(select(func.count(Job.id)).where(Job.type.not_like("zuper_%"))) == 0


def test_a_visit_rescheduled_in_zuper_moves_the_crm_visit(loaded, fake):
    set_cutover("2026-01-01")
    visit = loaded.ids["visit"]
    uid = uid_of("appointment", visit)
    fake.appointments[uid].update(start_time="2026-09-25T16:00:00Z", updated_at=fake.tick())
    pull("appointment", uid)
    with SessionLocal() as s:
        assert mapping.iso_minute(s.get(Appointment, visit).starts_at) == "2026-09-25T16:00:00Z"


def test_a_visit_booked_in_zuper_lands_on_the_crm_calendar(loaded, fake):
    job_uid = uid_of("opportunity", loaded.ids["jane_card"])
    fake.appointments["appt-new"] = {"appointment_uid": "appt-new", "job_uid": job_uid,
                                     "appointment_title": "Repair", "is_deleted": False,
                                     "start_time": "2026-10-01T13:00:00Z",
                                     "end_time": "2026-10-01T15:00:00Z",
                                     "updated_at": fake.tick()}
    pull("job", job_uid)
    with SessionLocal() as s:
        a = s.scalar(select(Appointment).where(Appointment.title == "Repair",
                                               Appointment.opportunity_id ==
                                               loaded.ids["jane_card"]))
        assert a is not None and mapping.iso_minute(a.starts_at) == "2026-10-01T13:00:00Z"


def test_a_visit_deleted_in_zuper_is_cancelled_never_deleted(loaded, fake):
    uid = uid_of("appointment", loaded.ids["visit"])
    fake.appointments.pop(uid)
    pull("job", uid_of("opportunity", loaded.ids["jane_card"]))
    with SessionLocal() as s:
        assert s.get(Appointment, loaded.ids["visit"]).status == "cancelled"
    assert [(c.rule, c.after) for c in conflicts()] == [("mirrored_cancel", "cancelled")]


# ------------------------------------------------------------------ edited on both sides

def test_checklist_answers_sync_both_ways_by_label(loaded, fake):
    card = loaded.ids["jane_card"]
    uid = uid_of("opportunity", card)
    job = fake.jobs[uid]
    fake.set_custom(job, "How many leaks?", "5")
    fake.set_custom(job, "Roof material", "Tile")
    fake.set_custom(job, "Explained the AHS repair process", "Yes")
    pull("job", uid)
    with SessionLocal() as s:
        blob = s.get(Opportunity, card).custom_fields
    assert blob["checklist_leak_count"] == 5
    assert blob["checklist_roof_material"] == "Tile"
    assert blob["checklist_explained_ahs_process"] is True
    r = loaded.client("owner").patch("/api/opportunities/%d/detail" % card, json={
        "custom_fields": {"checklist_stories": "2"}})
    assert r.status_code == 200, r.text
    drain()
    assert mapping.custom_values(fake.jobs[uid])["How many stories?"] == "2"
    assert conflicts() == []


def test_checklist_latest_edit_wins_when_both_sides_changed(loaded, fake):
    card = loaded.ids["jane_card"]
    uid = uid_of("opportunity", card)
    with quiet_session() as s:
        o = s.get(Opportunity, card)
        o.custom_fields = {**o.custom_fields, "checklist_leak_count": 7}
        o.updated_at = datetime.now(UTC) - timedelta(days=1)
        s.commit()
    fake.set_custom(fake.jobs[uid], "How many leaks?", "4")
    pull("job", uid)
    with SessionLocal() as s:
        assert s.get(Opportunity, card).custom_fields["checklist_leak_count"] == 4
    [c] = conflicts()
    assert (c.field, c.rule, c.winner, c.written_to) == (
        "cl:How many leaks?", "latest_edit", "zuper", "crm")


def test_a_zuper_answer_the_crm_question_cannot_hold_goes_back_and_is_logged(loaded, fake):
    card = loaded.ids["jane_card"]
    uid = uid_of("opportunity", card)
    fake.set_custom(fake.jobs[uid], "How old is the roof?", "Ancient")
    pull("job", uid)
    with SessionLocal() as s:
        assert s.get(Opportunity, card).custom_fields["checklist_roof_age"] == \
            "10" + chr(0x2013) + "15 yrs"
    assert mapping.custom_values(fake.jobs[uid])["How old is the roof?"] == "10-15 yrs"
    [c] = conflicts()
    assert (c.field, c.rule, c.written_to) == ("cl:How old is the roof?", "crm_cannot_hold",
                                               "zuper")


def test_notes_and_task_completion_sync_both_ways(loaded, fake):
    card = loaded.ids["jane_card"]
    job_uid = uid_of("opportunity", card)
    task_uid = uid_of("task", loaded.ids["task"])
    task = next(t for t in fake.tasks[job_uid] if t["service_task_uid"] == task_uid)
    task.update(service_task_status="COMPLETED", updated_at=fake.tick())
    fake.add_note(job_uid, "Crew: bring the tall ladder")
    pull("job", job_uid)
    with SessionLocal() as s:
        assert s.get(OpportunityTask, loaded.ids["task"]).completed_at is not None
        bodies = sorted(s.scalars(select(OpportunityNote.body).where(
            OpportunityNote.opportunity_id == card)).all())
    assert bodies == ["Crew: bring the tall ladder", "Gate code 1234"]
    c = loaded.client("owner")
    assert c.patch("/api/tasks/%d" % loaded.ids["task"], json={"done": False}).status_code == 200
    assert c.patch("/api/opportunity-notes/%d" % loaded.ids["note"],
                   json={"body": "Gate code 9999"}).status_code == 200
    new_task = c.post("/api/opportunities/%d/tasks" % card, json={"title": "Order shingles"})
    assert new_task.status_code == 201, new_task.text
    drain()
    task = next(t for t in fake.tasks[job_uid] if t["service_task_uid"] == task_uid)
    assert task["service_task_status"] == "NEW"
    note_uid = uid_of("note", loaded.ids["note"])
    assert next(n for n in fake.notes[job_uid] if n["note_uid"] == note_uid)["note"] == \
        "Gate code 9999"
    assert "Order shingles" in [t["service_task_title"] for t in fake.tasks[job_uid]]


def test_a_note_on_a_card_never_sent_goes_nowhere(loaded, fake):
    writes = len(fake.writes())
    r = loaded.client("owner").post("/api/opportunities/%d/notes" % loaded.ids["follow_card"],
                                    json={"body": "Call back Friday"})
    assert r.status_code == 201
    with SessionLocal() as s:
        assert s.scalar(select(func.count(Job.id)).where(Job.type.like("zuper_%"),
                                                         Job.status == "pending")) == 0
    drain()
    assert len(fake.writes()) == writes


def test_crm_owned_fields_written_in_zuper_are_put_back(loaded, fake):
    uid = uid_of("opportunity", loaded.ids["bob_card"])
    fake.set_custom(fake.jobs[uid], "Technicians", "Somebody Else")
    pull("job", uid)
    assert mapping.custom_values(fake.jobs[uid])["Technicians"] == "Antonio"
    [c] = conflicts()
    assert (c.field, c.rule, c.winner, c.written_to) == (
        "crm:Technicians", "crm_owned", "crm", "zuper")


# ------------------------------------------------------------------ Workiz before cutover

def test_workiz_wins_its_fields_before_cutover(loaded, fake):
    card = loaded.ids["bob_card"]
    uid = uid_of("opportunity", card)
    fake.edit_job(uid, job_title="Retitled in Zuper")
    pull("job", uid)
    with SessionLocal() as s:
        assert s.get(Opportunity, card).title == "Bob Workiz - repair"
    assert fake.jobs[uid]["job_title"] == "Bob Workiz - repair"
    [c] = conflicts()
    assert (c.field, c.rule, c.winner, c.written_to) == (
        "title", "workiz_before_cutover", "workiz", "zuper")


def test_after_cutover_zuper_owns_the_workiz_job_like_any_other(loaded, fake):
    set_cutover("2026-01-01")
    card = loaded.ids["bob_card"]
    uid = uid_of("opportunity", card)
    fake.edit_job(uid, job_title="Retitled in Zuper")
    pull("job", uid)
    with SessionLocal() as s:
        assert s.get(Opportunity, card).title == "Retitled in Zuper"
    assert conflicts() == []


def test_the_importer_s_stage_moves_reach_zuper_forward_and_back_with_rollback(loaded, fake):
    """The importer bypass: its quiet writes to a Workiz job are pushed by the sweep."""
    card = loaded.ids["bob_card"]
    uid = uid_of("opportunity", card)
    stages = loaded.ids["stages"]
    for stage_key, expect_rollback in (("retail:Invoice", 0), ("retail:Estimate Sent", 1)):
        with quiet_session("workiz import") as s:
            o = s.get(Opportunity, card)
            o.stage_id = stages[stage_key]
            s.commit()
        with SessionLocal() as s:
            sweep.run(s)
        assert fake.jobs[uid]["current_job_status"]["status_uid"] == uid_of(
            "stage", stages[stage_key])
        assert len(fake.calls("PUT", r"/status/rollback$")) == expect_rollback


def test_the_importer_s_reschedule_of_its_visit_reaches_zuper_before_cutover(loaded, fake):
    visit = loaded.ids["workiz_visit"]
    with quiet_session("workiz import") as s:
        a = s.get(Appointment, visit)
        a.starts_at = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
        a.ends_at = datetime(2026, 9, 28, 16, 0, tzinfo=UTC)
        s.commit()
    with SessionLocal() as s:
        sweep.run(s)
    assert fake.appointments[uid_of("appointment", visit)]["start_time"] == \
        "2026-09-28T14:00:00Z"


def test_a_zuper_reschedule_of_a_workiz_visit_is_put_back_before_cutover(loaded, fake):
    uid = uid_of("appointment", loaded.ids["workiz_visit"])
    fake.appointments[uid].update(start_time="2026-09-30T15:00:00Z", updated_at=fake.tick())
    pull("appointment", uid)
    assert fake.appointments[uid]["start_time"] == "2026-09-21T14:00:00Z"
    [c] = conflicts()
    assert (c.field, c.rule, c.written_to) == ("starts_at", "workiz_before_cutover", "zuper")


def test_the_importer_refuses_on_and_after_the_cutover_date(zworld, capsys, tmp_path):
    from app import workiz_import
    with SessionLocal() as s:
        config.settings(s).workiz_cutover_date = "2026-09-01"
        s.commit()
    missing = str(tmp_path / "missing.csv")
    assert workiz_import.run(missing, missing, commit=True, stream=io.StringIO()) == 10
    assert "Workiz was retired on 2026-09-01" in capsys.readouterr().err
    with SessionLocal() as s:
        config.settings(s).workiz_cutover_date = "2999-01-01"
        s.commit()
    # Before the date it goes on to read the export (which is missing here: exit 4).
    assert workiz_import.run(missing, missing, commit=True, stream=io.StringIO()) == 4


def test_new_workiz_jobs_ahs_sent_retail_by_the_stage_rule_and_the_import_queues_nothing(
        loaded, fake, tmp_path):
    from app import workiz_import
    from app.models import Pipeline
    from tests.test_workiz_import import client_row, job_row, write_csvs
    with quiet_session() as s:
        for name, stages in workiz_import.STAGE_FOR.items():
            p = s.scalar(select(Pipeline).where(Pipeline.name == name))
            have = set(s.scalars(select(Stage.name).where(Stage.pipeline_id == p.id)).all())
            for i, stage in enumerate(sorted(set(stages.values()) - have)):
                s.add(Stage(pipeline_id=p.id, name=stage, position=50 + i))
        s.commit()
    addr = {"address": "5 Bay Rd", "city": "Sarasota", "state": "FL", "zip_code": "34236"}
    cp, jp = write_csvs(tmp_path, [
        client_row("C900", "Wanda Workiz", phone="9415550900"),
        client_row("C901", "Rita Retail", phone="9415550901")], [
        job_row("J900", "Wanda Workiz", status="Submitted", source="AHS", phone="9415550900",
                **addr),
        job_row("J901", "Rita Retail", status="Submitted", source="Google",
                phone="9415550901", **addr)])
    assert workiz_import.run(cp, jp, commit=True, stream=io.StringIO()) == 0
    with SessionLocal() as s:
        assert s.scalar(select(func.count(Job.id)).where(Job.type.like("zuper_%"))) == 0
        wanda = s.scalar(select(Opportunity.id).join(Contact, Contact.id ==
                                                     Opportunity.contact_id)
                         .where(Contact.first_name == "Wanda"))
        rita = s.scalar(select(Opportunity.id).join(Contact, Contact.id ==
                                                    Opportunity.contact_id)
                        .where(Contact.first_name == "Rita"))
        sweep.run(s)
        assert mapping_row(s, "opportunity", rita) is None        # Retail New Lead stays
    assert mapping.custom_values(fake.jobs[uid_of("opportunity", wanda)])["Workiz Job #"] \
        == "J900"


# ------------------------------------------------------------------ money: read-only, no outcomes

def test_quotes_and_invoices_are_cached_the_value_follows_and_nothing_else_happens(loaded,
                                                                                  fake):
    card = loaded.ids["jane_card"]
    job_uid = uid_of("opportunity", card)
    document("quote", fake.add_estimate(job_uid, status="APPROVED", total="1234.50"))
    with SessionLocal() as s:
        assert s.get(Opportunity, card).value_cents == 123450
    inv = fake.add_invoice(job_uid, status="PAID", total="1300.00", balance="0.00")
    document("invoice", inv)
    declined = fake.add_estimate(job_uid, status="DECLINED", total="900", number="Q-9")
    document("quote", declined)
    with SessionLocal() as s:
        o = s.get(Opportunity, card)
        # The owner DROPPED the automatic outcomes: no Won, no task.
        assert o.status == "open" and o.value_cents == 130000
        assert s.scalar(select(func.count(OpportunityTask.id)).where(
            OpportunityTask.opportunity_id == card, OpportunityTask.priority == "urgent")) == 0
        doc = s.scalar(select(ZuperDocument).where(ZuperDocument.zuper_uid == inv))
        assert (doc.status, doc.total_cents, doc.balance_cents) == ("PAID", 130000, 0)
    assert outcomes.ENABLED is False and outcomes.RULES == []
    assert [(c.field, c.rule) for c in conflicts()] == [("value", "zuper_money")] * 2


def test_the_outcome_hook_is_the_one_place_a_rule_would_run(loaded, fake, monkeypatch):
    seen = []
    monkeypatch.setattr(outcomes, "RULES", [lambda ctx, doc, o: seen.append((doc.kind, o.id))])
    document("invoice", fake.add_invoice(uid_of("opportunity", loaded.ids["jane_card"]),
                                         status="PAID", total="1", balance="0"))
    assert seen == []                                   # switched off
    monkeypatch.setattr(outcomes, "ENABLED", True)
    document("invoice", fake.add_invoice(uid_of("opportunity", loaded.ids["jane_card"]),
                                         status="PAID", total="2", balance="0"))
    assert seen == [("invoice", loaded.ids["jane_card"])]


# ------------------------------------------------------------------ deletes

def test_a_card_deleted_in_the_crm_is_deleted_in_zuper_with_a_snapshot_and_restores(loaded, fake):
    card = loaded.ids["jane_card"]
    job_uid = uid_of("opportunity", card)
    c = loaded.client("owner")
    assert c.delete("/api/opportunities/%d" % card).status_code == 200
    with SessionLocal() as s:
        rows = s.scalars(select(ZuperDeleteSnapshot)).all()
        assert {r.crm_type for r in rows} == {"opportunity", "note", "task"}
        assert {r.state for r in rows} == {"pending"}
        opp_row = next(r for r in rows if r.crm_type == "opportunity")
        assert opp_row.snapshot["record"]["title"] == "Jane Roof - leak"
        assert loaded.ids["visit"] in opp_row.snapshot["children"]["appointment_ids"]
    drain()
    assert fake.jobs[job_uid]["is_deleted"] is True
    assert fake.calls("DELETE", r"/note/") == []          # went with its job, not separately
    listing = c.get("/api/zuper/deletes").json()["items"]
    item = next(i for i in listing if i["crm_type"] == "opportunity")
    assert item["state"] == "mirrored" and item["restorable"]
    r = c.post("/api/zuper/deletes/%d/restore" % item["id"])
    assert r.status_code == 200, r.text
    assert {x["state"] for x in r.json()["results"]} == {"restored"}
    with SessionLocal() as s:
        assert s.get(Opportunity, card).title == "Jane Roof - leak"
        assert s.get(OpportunityNote, loaded.ids["note"]).body == "Gate code 1234"
        assert s.get(OpportunityTask, loaded.ids["task"]) is not None
        assert s.get(Appointment, loaded.ids["visit"]).opportunity_id == card
        assert mapping_row(s, "opportunity", card).state == "linked"
    assert fake.jobs[job_uid]["is_deleted"] is False
    assert c.post("/api/zuper/deletes/%d/restore" % item["id"]).status_code == 409


def test_a_job_deleted_in_zuper_is_deleted_in_the_crm_with_a_snapshot_and_restores(loaded, fake):
    card = loaded.ids["bob_card"]
    job_uid = uid_of("opportunity", card)
    fake.delete_job(job_uid)
    assert pull("job", job_uid)["outcome"] == "deleted_in_crm"
    with SessionLocal() as s:
        assert s.get(Opportunity, card) is None
        assert s.get(Appointment, loaded.ids["workiz_visit"]).opportunity_id is None
        row = s.scalar(select(ZuperDeleteSnapshot))
        assert (row.direction, row.state, row.crm_id) == ("zuper_to_crm", "mirrored", card)
    r = loaded.client("owner").post("/api/zuper/deletes/%d/restore" % row.id)
    assert r.json()["results"][0]["state"] == "restored", r.text
    with SessionLocal() as s:
        assert s.get(Opportunity, card).custom_fields["workiz_id"] == "W100"
        assert s.get(Appointment, loaded.ids["workiz_visit"]).opportunity_id == card
    assert fake.jobs[job_uid]["is_deleted"] is False


def test_a_customer_deleted_in_zuper_with_nothing_else_deletes_contact_and_sent_job(loaded,
                                                                                   fake):
    uid = uid_of("contact", loaded.ids["bob"])
    fake.delete_customer(uid)
    pull("customer", uid)
    with SessionLocal() as s:
        assert s.get(Contact, loaded.ids["bob"]) is None
        assert s.get(Opportunity, loaded.ids["bob_card"]) is None
        kinds = sorted(s.scalars(select(ZuperDeleteSnapshot.crm_type)).all())
    assert kinds == ["contact", "opportunity"]


def test_a_contact_with_unsent_cards_or_conversations_is_never_deleted_by_zuper(loaded, fake):
    ids = loaded.ids
    uid = uid_of("contact", ids["al"])          # Al also has a lost Retail card never sent
    fake.delete_customer(uid)
    assert pull("customer", uid)["outcome"] == "contact_kept"
    with SessionLocal() as s:
        assert s.get(Contact, ids["al"]) is not None
        assert s.get(Opportunity, ids["lost_estimate"]) is not None
        assert s.get(Opportunity, ids["al_card"]) is None        # the mirrored job side
        kept = s.scalar(select(ZuperDeleteSnapshot).where(ZuperDeleteSnapshot.state == "kept"))
        assert "never sent to Zuper" in kept.error
    listing = loaded.client("owner").get("/api/zuper/deletes").json()["items"]
    assert next(i for i in listing if i["state"] == "kept")["restorable"] is False
    # And a conversation alone protects a contact too.
    with quiet_session() as s:
        conv = Conversation(contact_id=ids["jane"])
        s.add(conv)
        s.flush()
        s.add(ConversationEvent(conversation_id=conv.id, type=EventType.SMS,
                                direction=Direction.INBOUND, body="hello"))
        s.commit()
    uid = uid_of("contact", ids["jane"])
    fake.delete_customer(uid)
    assert pull("customer", uid)["outcome"] == "contact_kept"
    with SessionLocal() as s:
        assert s.get(Contact, ids["jane"]) is not None
        assert s.get(Opportunity, ids["jane_card"]) is None


def test_a_contact_deleted_in_the_crm_deletes_the_customer_and_restores_both(loaded, fake):
    bob = loaded.ids["bob"]
    uid = uid_of("contact", bob)
    c = loaded.client("owner")
    assert c.delete("/api/contacts/%d?force=true" % bob).status_code == 200
    drain()
    assert fake.customers[uid]["is_deleted"] is True
    item = next(i for i in c.get("/api/zuper/deletes").json()["items"]
                if i["crm_type"] == "contact")
    assert (item["state"], item["label"]) == ("mirrored", "Bob Workiz")
    assert c.post("/api/zuper/deletes/%d/restore" % item["id"]).json()["results"][0][
        "state"] == "restored"
    with SessionLocal() as s:
        assert s.get(Contact, bob).last_name == "Workiz"
    assert fake.customers[uid]["is_deleted"] is False


def test_a_note_deleted_in_zuper_is_deleted_in_the_crm(loaded, fake):
    job_uid = uid_of("opportunity", loaded.ids["jane_card"])
    note_uid = uid_of("note", loaded.ids["note"])
    fake.notes[job_uid] = [n for n in fake.notes[job_uid] if n["note_uid"] != note_uid]
    pull("job", job_uid)
    with SessionLocal() as s:
        assert s.get(OpportunityNote, loaded.ids["note"]) is None
        assert s.scalar(select(ZuperDeleteSnapshot.crm_type)) == "note"


# ------------------------------------------------------------------ records made in Zuper

def test_a_new_job_and_customer_in_zuper_become_a_contact_and_card(loaded, fake):
    stage = loaded.ids["stages"]["ahs:Request the Approval (AHS)"]
    cus = fake.new_customer(customer_first_name="Zed", customer_last_name="New",
                            customer_contact_no={"mobile": "9415550199"})
    job = fake.new_job(uid_of("pipeline", loaded.ids["ahs"]), uid_of("stage", stage),
                       job_title="Zed New - leak", customer={"customer_uid": cus},
                       custom_fields=[{"label": "How many leaks?", "value": "3"}])
    pull("job", job)
    with SessionLocal() as s:
        o = s.scalar(select(Opportunity).where(Opportunity.title == "Zed New - leak"))
        assert o.stage_id == stage and o.pipeline_id == loaded.ids["ahs"]
        assert o.created_by == "Zuper sync"
        assert o.custom_fields["checklist_leak_count"] == 3
        c = s.get(Contact, o.contact_id)
        assert (c.first_name, c.phone, c.created_by) == ("Zed", "+19415550199", "Zuper sync")
    assert mapping.custom_values(fake.jobs[job])["CRM Opportunity ID"] == str(o.id)
    assert mapping.custom_values(fake.customers[cus])["CRM Contact ID"] == str(c.id)


def test_a_customer_alone_in_zuper_creates_no_contact(loaded, fake):
    cus = fake.new_customer(customer_first_name="Solo", customer_last_name="Customer")
    assert pull("customer", cus)["outcome"] == "customer_without_job"
    with SessionLocal() as s:
        assert s.scalar(select(Contact).where(Contact.first_name == "Solo")) is None


def test_our_own_create_seen_before_its_commit_links_instead_of_duplicating(loaded, fake):
    c = loaded.client("owner")
    new = c.post("/api/contacts", json={"first_name": "Rae", "last_name": "Race",
                                        "phone": "(941) 555-0122"}).json()
    with quiet_session() as s:
        s.add(ZuperMapping(crm_type="contact", crm_id=new["id"], zuper_type="customer",
                           state="creating"))
        s.commit()
    cus = fake.new_customer(customer_first_name="Rae", customer_last_name="Race",
                            custom_fields=[{"label": "CRM Contact ID", "value": str(new["id"])}])
    assert pull("customer", cus)["outcome"] == "linked_existing"
    with SessionLocal() as s:
        assert s.scalar(select(func.count(Contact.id)).where(Contact.first_name == "Rae")) == 1
        assert mapping_row(s, "contact", new["id"]).zuper_uid == cus


def test_a_job_in_a_template_category_is_ignored(loaded, fake):
    job = fake.new_job("cat-template", job_title="Roofle lead")
    assert pull("job", job)["outcome"] == "out_of_scope"
    with SessionLocal() as s:
        assert s.scalar(select(Opportunity).where(Opportunity.title == "Roofle lead")) is None


# ------------------------------------------------------------------ the sweep

def test_the_sweep_catches_a_change_no_webhook_reported(loaded, fake):
    uid = uid_of("opportunity", loaded.ids["jane_card"])
    fake.edit_job(uid, job_title="Swept title")
    with SessionLocal() as s:
        counts = sweep.run(s)
        assert s.get(Opportunity, loaded.ids["jane_card"]).title == "Swept title"
        state = s.get(engine.ZuperSyncState, 1)
        assert state.filter_checks["jobs"] is True and state.last_error is None
    assert counts["swept_jobs"] >= 1


def test_the_sweep_still_catches_it_when_zuper_ignores_the_updated_at_filter(loaded, fake):
    fake.honour_updated_filter = False
    uid = uid_of("opportunity", loaded.ids["jane_card"])
    with SessionLocal() as s:
        sweep.run(s)
    fake.edit_job(uid, job_title="Found by paging")
    with SessionLocal() as s:
        sweep.run(s)
        assert s.get(Opportunity, loaded.ids["jane_card"]).title == "Found by paging"
        assert s.get(engine.ZuperSyncState, 1).filter_checks["jobs"] is False


def test_the_sweep_pushes_a_quiet_change_to_a_crm_owned_field(loaded, fake):
    """The importer's new technician name: queued by nobody, found by its content hash."""
    with quiet_session("workiz import") as s:
        o = s.get(Opportunity, loaded.ids["bob_card"])
        o.custom_fields = {**o.custom_fields, "workiz_tech": ["Antonio", "Luis"]}
        s.commit()
    with SessionLocal() as s:
        sweep.run(s)
    assert mapping.custom_values(fake.jobs[uid_of("opportunity", loaded.ids["bob_card"])])[
        "Technicians"] == "Antonio, Luis"


def test_the_sweep_mirrors_a_delete_the_listener_did_not_see(loaded, fake):
    uid = uid_of("task", loaded.ids["task"])
    with quiet_session() as s:
        s.delete(s.get(OpportunityTask, loaded.ids["task"]))
        s.commit()
    with SessionLocal() as s:
        sweep.run(s)
    job_uid = uid_of("opportunity", loaded.ids["jane_card"])
    assert uid not in [t["service_task_uid"] for t in fake.tasks[job_uid]]


# ------------------------------------------------------------------ the daily digest

def test_the_daily_digest_counts_activity_and_never_carries_a_message(loaded, fake):
    jane = loaded.ids["jane"]
    day = config.today_et() - timedelta(days=1)
    start, _ = digest.day_bounds(day)
    with quiet_session() as s:
        conv = Conversation(contact_id=jane)
        s.add(conv)
        s.flush()
        for minutes, kind, direction, body, extra in (
                (60, EventType.SMS, Direction.INBOUND, "Please call me about the leak", {}),
                (61, EventType.SMS, Direction.INBOUND, "It is dripping on the bed", {}),
                (70, EventType.SMS, Direction.OUTBOUND, "On our way", {}),
                (80, EventType.CALL, Direction.INBOUND, None,
                 {"duration_seconds": 200, "call_status": "completed",
                  "transcript": "secret words about the shingles"})):
            s.add(ConversationEvent(conversation_id=conv.id, type=kind, direction=direction,
                                    body=body, occurred_at=start + timedelta(minutes=minutes),
                                    **extra))
        s.add(ConversationEvent(conversation_id=conv.id, type=EventType.SMS,
                                direction=Direction.INBOUND, body="the next day",
                                occurred_at=start + timedelta(days=1, minutes=5)))
        s.commit()
    with SessionLocal() as s:
        counts = digest.run(s, day)
        assert counts["written"] == 1
        assert digest.run(s, day)["written"] == 0
    job_uid = uid_of("opportunity", loaded.ids["jane_card"])
    notes = [n["note"] for n in fake.notes[job_uid] if n["note"] != "Gate code 1234"]
    assert notes == ["%s %d - 3 texts (2 in, 1 out) - 1 call, 4 min, answered - "
                     "https://crm.dreamteamroofingfl.com/opportunities?opportunity=%d"
                     % (day.strftime("%b"), day.day, loaded.ids["jane_card"])]
    for secret in ("leak", "dripping", "On our way", "secret", "shingles", "bed"):
        assert secret not in notes[0]
    # The digest note is never pulled back into the CRM as a note.
    pull("job", job_uid)
    with SessionLocal() as s:
        assert s.scalar(select(func.count(OpportunityNote.id))) == 1

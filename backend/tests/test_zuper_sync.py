"""The two-way sync's rules, one test per ownership row, against a fake Zuper.

Every test starts from a completed initial load (`loaded`): six contacts, three cards in the
AHS / Retail pipelines, two visits, a note and a task, all mapped. "A person in Zuper" is the
fake's own helpers (they bump `updated_at` without going through the client); "a person in the
CRM" is the API, whose commit queues a job the test drains like the worker's Zuper thread.
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
    Opportunity,
    OpportunityNote,
    OpportunityTask,
    ZuperConflict,
    ZuperDeleteSnapshot,
    ZuperDocument,
    ZuperSettings,
)
from app.zuper import config, digest, engine, mapping, sweep
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


def quiet_session():
    s = SessionLocal()
    s.info["zuper_quiet"] = "test"
    return s


# ------------------------------------------------------------------ latest edit wins

def test_contact_latest_edit_wins_zuper_later(loaded, fake):
    uid = uid_of("contact", loaded.ids["jane"])
    with quiet_session() as s:
        c = s.get(Contact, loaded.ids["jane"])
        c.email = "jane.crm@example.test"
        c.updated_at = datetime.now(UTC) - timedelta(hours=1)
        s.commit()
    fake.edit_customer(uid, customer_email="jane.zuper@example.test")
    pull("customer", uid)
    with SessionLocal() as s:
        assert s.get(Contact, loaded.ids["jane"]).email == "jane.zuper@example.test"
    [c] = conflicts()
    assert (c.field, c.rule, c.winner, c.written_to) == ("email", "latest_edit", "zuper", "crm")
    assert (c.before, c.after) == ("jane.crm@example.test", "jane.zuper@example.test")


def test_contact_latest_edit_wins_crm_later_and_other_fields_merge(loaded, fake):
    uid = uid_of("contact", loaded.ids["jane"])
    fake.edit_customer(uid, customer_email="jane.zuper@example.test",
                       customer_company_name="Roof Co")
    with quiet_session() as s:
        c = s.get(Contact, loaded.ids["jane"])
        c.email = "jane.crm@example.test"
        c.updated_at = fake.clock + timedelta(hours=1)
        s.commit()
    pull("customer", uid)
    with SessionLocal() as s:
        c = s.get(Contact, loaded.ids["jane"])
        assert c.email == "jane.crm@example.test"
        assert c.business_name == "Roof Co"        # only Zuper changed it: no conflict
    assert fake.customers[uid]["customer_email"] == "jane.crm@example.test"
    [c] = conflicts()
    assert (c.field, c.winner, c.written_to) == ("email", "crm", "zuper")


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
    # ...and the other way, through the API a dispatcher uses.
    r = loaded.client("owner").patch("/api/opportunities/%d/detail" % card, json={
        "custom_fields": {"checklist_stories": "2"}})
    assert r.status_code == 200
    drain()
    assert mapping.custom_values(fake.jobs[uid])["How many stories?"] == "2"
    assert conflicts() == []


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
    # Zuper: the crew ticks the service task and adds a note.
    task = next(t for t in fake.tasks[job_uid] if t["service_task_uid"] == task_uid)
    task.update(service_task_status="COMPLETED", updated_at=fake.tick())
    fake.add_note(job_uid, "Crew: bring the tall ladder")
    pull("job", job_uid)
    with SessionLocal() as s:
        assert s.get(OpportunityTask, loaded.ids["task"]).completed_at is not None
        bodies = sorted(s.scalars(select(OpportunityNote.body).where(
            OpportunityNote.opportunity_id == card)).all())
    assert bodies == ["Crew: bring the tall ladder", "Gate code 1234"]
    # CRM: reopen the task and edit the note.
    c = loaded.client("owner")
    assert c.patch("/api/tasks/%d" % loaded.ids["task"], json={"done": False}).status_code == 200
    assert c.patch("/api/opportunity-notes/%d" % loaded.ids["note"],
                   json={"body": "Gate code 9999"}).status_code == 200
    drain()
    task = next(t for t in fake.tasks[job_uid] if t["service_task_uid"] == task_uid)
    assert task["service_task_status"] == "NEW"
    note_uid = uid_of("note", loaded.ids["note"])
    assert next(n for n in fake.notes[job_uid] if n["note_uid"] == note_uid)["note"] == \
        "Gate code 9999"


# ------------------------------------------------------------------ CRM-owned fields

def test_crm_owned_fields_written_in_zuper_are_put_back(loaded, fake):
    uid = uid_of("opportunity", loaded.ids["bob_card"])
    fake.set_custom(fake.jobs[uid], "Technicians", "Somebody Else")
    pull("job", uid)
    assert mapping.custom_values(fake.jobs[uid])["Technicians"] == "Antonio"
    [c] = conflicts()
    assert (c.field, c.rule, c.winner, c.written_to) == (
        "crm:Technicians", "crm_owned", "crm", "zuper")


# ------------------------------------------------------------------ Workiz and cutover

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
    assert (c.before, c.after) == ("Retitled in Zuper", "Bob Workiz - repair")


def test_after_cutover_the_same_edit_is_latest_edit_wins(loaded, fake):
    set_cutover("2026-01-01")
    card = loaded.ids["bob_card"]
    uid = uid_of("opportunity", card)
    fake.edit_job(uid, job_title="Retitled in Zuper")
    pull("job", uid)
    with SessionLocal() as s:
        assert s.get(Opportunity, card).title == "Retitled in Zuper"
    assert conflicts() == []


def test_workiz_wins_the_schedule_of_its_visit_before_cutover(loaded, fake):
    uid = uid_of("appointment", loaded.ids["workiz_visit"])
    fake.appointments[uid].update(start_time="2026-09-30T15:00:00Z", updated_at=fake.tick())
    pull("appointment", uid)
    with SessionLocal() as s:
        a = s.get(Appointment, loaded.ids["workiz_visit"])
        assert mapping.iso_minute(a.starts_at) == "2026-09-21T14:00:00Z"
    assert fake.appointments[uid]["start_time"] == "2026-09-21T14:00:00Z"
    [c] = conflicts()
    assert (c.field, c.rule, c.written_to) == ("starts_at", "workiz_before_cutover", "zuper")


def test_zuper_wins_the_schedule_after_cutover_when_both_changed(loaded, fake):
    set_cutover("2026-01-01")
    visit = loaded.ids["visit"]
    uid = uid_of("appointment", visit)
    with quiet_session() as s:
        a = s.get(Appointment, visit)
        a.starts_at = datetime(2026, 9, 22, 14, 0, tzinfo=UTC)
        s.commit()
    fake.appointments[uid].update(start_time="2026-09-25T16:00:00Z", updated_at=fake.tick())
    pull("appointment", uid)
    with SessionLocal() as s:
        assert mapping.iso_minute(s.get(Appointment, visit).starts_at) == "2026-09-25T16:00:00Z"
    [c] = conflicts()
    assert (c.field, c.rule, c.winner, c.written_to) == (
        "starts_at", "zuper_schedule_after_cutover", "zuper", "crm")


def test_a_crm_reschedule_alone_after_cutover_still_reaches_zuper(loaded, fake):
    set_cutover("2026-01-01")
    visit = loaded.ids["visit"]
    r = loaded.client("owner").patch("/api/appointments/%d" % visit, json={
        "starts_at": "2026-09-23T14:00:00Z", "ends_at": "2026-09-23T15:00:00Z"})
    assert r.status_code == 200, r.text
    drain()
    assert fake.appointments[uid_of("appointment", visit)]["start_time"] == "2026-09-23T14:00:00Z"


def test_the_importer_refuses_on_and_after_the_cutover_date(zworld, capsys, tmp_path):
    from app import workiz_import
    with SessionLocal() as s:
        config.settings(s).workiz_cutover_date = "2026-09-01"
        s.commit()
    code = workiz_import.run(str(tmp_path / "missing.csv"), str(tmp_path / "missing.csv"),
                             commit=True)
    assert code == 10
    assert "Workiz was retired on 2026-09-01" in capsys.readouterr().err
    with SessionLocal() as s:
        config.settings(s).workiz_cutover_date = "2999-01-01"
        s.commit()
    # Before the date it goes on to read the export (which is missing here: exit 4).
    assert workiz_import.run(str(tmp_path / "missing.csv"), str(tmp_path / "missing.csv"),
                             commit=True) == 4


def test_a_workiz_import_with_the_sync_armed_queues_nothing_and_the_sweep_pushes_it(
        loaded, fake, tmp_path):
    from app import workiz_import
    from tests.test_workiz_import import client_row, job_row, write_csvs
    # The importer refuses to run while a column it routes to is missing: give it all of them.
    with quiet_session() as s:
        from app.models import Pipeline, Stage
        for name, stages in workiz_import.STAGE_FOR.items():
            p = s.scalar(select(Pipeline).where(Pipeline.name == name))
            have = set(s.scalars(select(Stage.name).where(Stage.pipeline_id == p.id)).all())
            for i, stage in enumerate(sorted(set(stages.values()) - have)):
                s.add(Stage(pipeline_id=p.id, name=stage, position=50 + i))
        s.commit()
    cp, jp = write_csvs(tmp_path, [client_row("C900", "Wanda Workiz", phone="9415550900")],
                        [job_row("J900", "Wanda Workiz", status="Submitted", source="AHS",
                                 phone="9415550900")])
    report = io.StringIO()
    assert workiz_import.run(cp, jp, commit=True, stream=report) == 0   # the jobs guard held
    with SessionLocal() as s:
        from app.models import Job
        assert s.scalar(select(func.count(Job.id)).where(Job.type.like("zuper_%"))) == 0
        wanda = s.scalar(select(Contact.id).where(Contact.first_name == "Wanda"))
        card = s.scalar(select(Opportunity.id).where(Opportunity.contact_id == wanda))
        sweep.run(s)
    assert mapping.custom_values(fake.customers[uid_of("contact", wanda)])[
        "CRM Contact ID"] == str(wanda)
    assert mapping.custom_values(fake.jobs[uid_of("opportunity", card)])[
        "Workiz Job #"] == "J900"


# ------------------------------------------------------------------ stages

def test_a_stage_move_in_the_crm_moves_the_job_forward_and_back_with_rollback(loaded, fake):
    card = loaded.ids["jane_card"]
    uid = uid_of("opportunity", card)
    stages = loaded.ids["stages"]
    c = loaded.client("owner")
    assert c.patch("/api/opportunities/%d" % card,
                   json={"stage_id": stages["ahs:Call Back"]}).status_code == 200
    drain()
    assert fake.jobs[uid]["current_job_status"]["status_uid"] == uid_of(
        "stage", stages["ahs:Call Back"])
    assert fake.calls("PUT", r"/status$") and not fake.calls("PUT", r"/rollback$")
    assert c.patch("/api/opportunities/%d" % card,
                   json={"stage_id": stages["ahs:New Lead"]}).status_code == 200
    drain()
    assert fake.jobs[uid]["current_job_status"]["status_uid"] == uid_of(
        "stage", stages["ahs:New Lead"])
    assert len(fake.calls("PUT", r"/status/rollback$")) == 1


def test_a_status_move_in_zuper_moves_the_card(loaded, fake):
    card = loaded.ids["jane_card"]
    uid = uid_of("opportunity", card)
    target = loaded.ids["stages"]["ahs:Submit Invoices"]
    fake.move_job(uid, uid_of("stage", target))
    pull("job", uid)
    with SessionLocal() as s:
        o = s.get(Opportunity, card)
        assert o.stage_id == target
        # Filed at the bottom of its new column, and no rule or agent fired: nothing queued.
        assert o.position == 0
        from app.models import Job
        assert s.scalar(select(func.count(Job.id)).where(Job.type.not_like("zuper_%"))) == 0


# ------------------------------------------------------------------ money

def test_quote_and_invoice_money_rules(loaded, fake):
    card = loaded.ids["jane_card"]
    job_uid = uid_of("opportunity", card)
    est = fake.add_estimate(job_uid, status="APPROVED", total="1234.50", number="Q-1")
    document("quote", est)
    with SessionLocal() as s:
        assert s.get(Opportunity, card).value_cents == 123450
    inv = fake.add_invoice(job_uid, status="AWAIT_PAYMENT", total="1300.00", balance="1300.00")
    document("invoice", inv)
    with SessionLocal() as s:
        o = s.get(Opportunity, card)
        assert o.value_cents == 130000 and o.status == "open"
    fake.set_document(fake.invoices, inv, invoice_status="PAID", balance_due="0")
    document("invoice", inv)
    with SessionLocal() as s:
        o = s.get(Opportunity, card)
        assert o.status == "won"
        doc = s.scalar(select(ZuperDocument).where(ZuperDocument.zuper_uid == inv))
        assert (doc.number, doc.status, doc.total_cents, doc.balance_cents) == (
            "INV-7", "PAID", 130000, 0)
    rules = [(c.field, c.rule, c.after) for c in conflicts()]
    assert ("value", "zuper_money", 123450) in rules and ("status", "invoice_paid", "won") in rules
    # A person edits the value in the CRM: Zuper owns money now, so it goes back.
    r = loaded.client("owner").patch("/api/opportunities/%d/detail" % card,
                                     json={"value_cents": 500})
    assert r.status_code == 200
    drain()
    with SessionLocal() as s:
        assert s.get(Opportunity, card).value_cents == 130000
    last = conflicts()[-1]
    assert (last.field, last.rule, last.written_to, last.before, last.after) == (
        "value", "zuper_money", "crm", 500, 130000)


def test_a_declined_quote_makes_one_urgent_task_for_the_card_owner_not_lost(loaded, fake):
    card = loaded.ids["jane_card"]
    est = fake.add_estimate(uid_of("opportunity", card), status="DECLINED", total="900",
                            number="Q-9")
    document("quote", est)
    document("quote", est)
    with SessionLocal() as s:
        tasks = s.scalars(select(OpportunityTask).where(
            OpportunityTask.opportunity_id == card, OpportunityTask.priority == "urgent")).all()
        assert len(tasks) == 1
        assert tasks[0].assigned_user_id == loaded.ids["owner"]
        assert "Quote Q-9 was declined" in tasks[0].title
        assert s.get(Opportunity, card).status == "open"


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
    assert item["direction"] == "crm_to_zuper" and item["label"] == "Jane Roof - leak"
    r = c.post("/api/zuper/deletes/%d/restore" % item["id"])
    assert r.status_code == 200, r.text
    assert {x["state"] for x in r.json()["results"]} == {"restored"}
    with SessionLocal() as s:
        o = s.get(Opportunity, card)
        assert o is not None and o.title == "Jane Roof - leak"
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
        assert row.snapshot["record"]["custom_fields"]["workiz_id"] == "W100"
    r = loaded.client("owner").post("/api/zuper/deletes/%d/restore" % row.id)
    assert r.json()["results"][0]["state"] == "restored", r.text
    with SessionLocal() as s:
        assert s.get(Opportunity, card).custom_fields["workiz_id"] == "W100"
        assert s.get(Appointment, loaded.ids["workiz_visit"]).opportunity_id == card
    assert fake.jobs[job_uid]["is_deleted"] is False


def test_a_customer_deleted_in_zuper_takes_its_conversation_into_the_snapshot(loaded, fake):
    leo = loaded.ids["leo"]
    with quiet_session() as s:
        conv = Conversation(contact_id=leo)
        s.add(conv)
        s.flush()
        s.add(ConversationEvent(conversation_id=conv.id, type=EventType.SMS,
                                direction=Direction.INBOUND, body="hello"))
        s.commit()
        conv_id = conv.id
    uid = uid_of("contact", leo)
    fake.delete_customer(uid)
    pull("customer", uid)
    with SessionLocal() as s:
        assert s.get(Contact, leo) is None and s.get(Conversation, conv_id) is None
        row = s.scalar(select(ZuperDeleteSnapshot))
    loaded.client("owner").post("/api/zuper/deletes/%d/restore" % row.id)
    with SessionLocal() as s:
        assert s.get(Contact, leo).first_name == "Leo"
        assert [e.body for e in s.get(Conversation, conv_id).events] == ["hello"]


def test_a_contact_deleted_in_the_crm_deletes_the_customer_and_restores_both(loaded, fake):
    leo = loaded.ids["leo"]
    uid = uid_of("contact", leo)
    c = loaded.client("owner")
    assert c.delete("/api/contacts/%d" % leo).status_code == 200
    drain()
    assert fake.customers[uid]["is_deleted"] is True
    item = c.get("/api/zuper/deletes").json()["items"][0]
    assert (item["crm_type"], item["state"], item["label"]) == ("contact", "mirrored", "Leo Lead")
    assert c.post("/api/zuper/deletes/%d/restore" % item["id"]).json()["results"][0][
        "state"] == "restored"
    with SessionLocal() as s:
        assert s.get(Contact, leo).last_name == "Lead"
    assert fake.customers[uid]["is_deleted"] is False


def test_a_visit_booked_in_the_crm_is_created_under_the_job(loaded, fake):
    card = loaded.ids["jane_card"]
    r = loaded.client("owner").post("/api/appointments", json={
        "title": "Repair visit", "calendar_id": loaded.ids["calendar"],
        "contact_id": loaded.ids["jane"], "opportunity_id": card,
        "starts_at": "2026-10-02T13:00:00Z", "ends_at": "2026-10-02T15:00:00Z"})
    assert r.status_code == 201, r.text
    drain()
    made = [a for a in fake.appointments.values() if a.get("appointment_title") == "Repair visit"]
    assert len(made) == 1
    assert made[0]["job_uid"] == uid_of("opportunity", card)
    assert (made[0]["start_time"], made[0]["end_time"]) == ("2026-10-02T13:00:00Z",
                                                           "2026-10-02T15:00:00Z")


def test_a_note_deleted_in_zuper_is_deleted_in_the_crm(loaded, fake):
    job_uid = uid_of("opportunity", loaded.ids["jane_card"])
    note_uid = uid_of("note", loaded.ids["note"])
    fake.notes[job_uid] = [n for n in fake.notes[job_uid] if n["note_uid"] != note_uid]
    pull("job", job_uid)
    with SessionLocal() as s:
        assert s.get(OpportunityNote, loaded.ids["note"]) is None
        assert s.scalar(select(ZuperDeleteSnapshot.crm_type)) == "note"


def test_a_visit_deleted_in_zuper_is_cancelled_never_deleted(loaded, fake):
    uid = uid_of("appointment", loaded.ids["visit"])
    fake.appointments.pop(uid)
    pull("job", uid_of("opportunity", loaded.ids["jane_card"]))
    with SessionLocal() as s:
        assert s.get(Appointment, loaded.ids["visit"]).status == "cancelled"
    assert [(c.rule, c.after) for c in conflicts()] == [("mirrored_cancel", "cancelled")]


# ------------------------------------------------------------------ new records

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


def test_our_own_create_seen_before_its_commit_links_instead_of_duplicating(loaded, fake):
    """A customer carrying a CRM id whose mapping is still `creating` is that create."""
    c = loaded.client("owner")
    new = c.post("/api/contacts", json={"first_name": "Rae", "last_name": "Race",
                                        "phone": "(941) 555-0122"}).json()
    with quiet_session() as s:
        from app.models import ZuperMapping
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


def test_a_new_card_in_the_crm_creates_the_customer_and_the_job(loaded, fake):
    c = loaded.client("owner")
    contact = c.post("/api/contacts", json={"first_name": "Nia", "last_name": "New",
                                            "phone": "(941) 555-0111"}).json()
    r = c.post("/api/opportunities", json={
        "title": "Nia New - roof", "pipeline_id": loaded.ids["retail"],
        "stage_id": loaded.ids["stages"]["retail:Estimate Sent"], "contact_id": contact["id"]})
    assert r.status_code == 201
    drain()
    job = fake.jobs[uid_of("opportunity", r.json()["id"])]
    assert job["job_title"] == "Nia New - roof"
    assert job["customer"]["customer_uid"] == uid_of("contact", contact["id"])
    assert job["current_job_status"]["status_uid"] == uid_of(
        "stage", loaded.ids["stages"]["retail:Estimate Sent"])
    # Nia had no job while she was only a contact; now the Lead tag is gone.
    assert "Lead" not in fake.customers[uid_of("contact", contact["id"])]["customer_tags"]


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
        sweep.run(s)                              # move the cursor past the load
    fake.edit_job(uid, job_title="Found by paging")
    with SessionLocal() as s:
        sweep.run(s)
        assert s.get(Opportunity, loaded.ids["jane_card"]).title == "Found by paging"
        assert s.get(engine.ZuperSyncState, 1).filter_checks["jobs"] is False


def test_the_sweep_pushes_a_crm_change_nothing_queued(loaded, fake):
    """A quiet session — the Workiz importer's — queues nothing; the content hash finds it."""
    with quiet_session() as s:
        s.get(Contact, loaded.ids["bob"]).first_name = "Robert"
        s.commit()
    with SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(
            select(engine.Job).where(engine.Job.type.like("zuper_%")).subquery())) == 0
        sweep.run(s)
    assert fake.customers[uid_of("contact", loaded.ids["bob"])]["customer_first_name"] == "Robert"


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

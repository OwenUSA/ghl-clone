"""The one-way mirror (2026-09-23): Zuper's boards are the truth and the CRM copies them.

`python -m app.zuper.mirror` lines the two sides up once — stages become Zuper's statuses,
existing records are paired, then every job is pulled — and `ZUPER_PULL_ONLY` makes sure the
CRM never writes back while it does. Behaviour, not status codes: a renamed stage must keep
its deals, a removed stage must not strand one, and a pull that used to stamp Zuper must make
no request at all.
"""
from app.db import SessionLocal
from app.models import Opportunity, Pipeline, Stage, ZuperMapping
from app.zuper import config, mapping, mirror
from sqlalchemy import select
from tests.zuper_support import AHS, mapping_row

# The live AHS board, in its own order: 2026-09-22, then as the owner changed it on 2026-09-25
# ("Inspecting" renamed "Inspection", two stages added after it, "On My Way" deleted).
AHS_STATUSES_0922 = ["Work Order Received", "Contact Attempted", "Scheduled", "On My Way",
                     "Inspecting", "Proposal Made", "Approval Requested", "Approved",
                     "Repair Scheduled", "Repair In Process", "Repair Complete",
                     "Invoice Submitted to AHS", "Awaiting AHS Payment", "Paid", "Call Back",
                     "Waiting for Customer", "Reschedule Required", "Estimate Declined",
                     "Cancelled"]
AHS_STATUSES = ["Work Order Received", "Contact Attempted", "Scheduled", "Inspection",
                "Inspection Completed", "Notify Auth Dept", "Proposal Made",
                "Approval Requested", "Approved",
                "Repair Scheduled", "Repair In Process", "Repair Complete",
                "Invoice Submitted to AHS", "Awaiting AHS Payment", "Paid", "Call Back",
                "Waiting for Customer", "Reschedule Required", "Estimate Declined",
                "Cancelled"]
RETAIL_STATUSES = ["New Lead", "Contact Attempted", "Inspection / Estimate", "Proposal Made",
                   "Estimate Sent", "Follow Up", "Scheduled", "Repair In Process",
                   "Repair Complete", "Invoice", "Collect Balance", "Paid",
                   "Waiting for Customer", "Reschedule Required", "Estimate Declined",
                   "Cancelled"]


def board(fake, name: str, statuses: list[str]) -> str:
    """A Zuper category with its statuses, as the account already has them."""
    uid = fake.uid("cat")
    fake.categories[uid] = {"category_uid": uid, "category_name": name}
    fake.statuses[uid] = [{"status_uid": "st-%s-%d" % (name.lower(), i),
                           "status_name": s, "status_type": "OTHER"}
                          for i, s in enumerate(statuses)]
    return uid


def boards_of(fake):
    return (board(fake, mapping.CATEGORIES[AHS], AHS_STATUSES),
            board(fake, "Retail", RETAIL_STATUSES))


def run(**kw):
    with SessionLocal() as s:
        return mirror.run(s, **kw)


def stages(name: str = AHS) -> list[str]:
    with SessionLocal() as s:
        p = s.scalar(select(Pipeline).where(Pipeline.name == name))
        return [x.name for x in s.scalars(select(Stage).where(Stage.pipeline_id == p.id)
                                          .order_by(Stage.position, Stage.id))]


def test_the_crm_board_becomes_zuper_s_board_in_zuper_s_order(zworld, fake):
    boards_of(fake)
    code, report = run(commit=True, phases={"boards"})
    assert code == 0, report["problems"]
    assert stages(AHS) == AHS_STATUSES
    assert stages("Retail") == RETAIL_STATUSES


def test_a_renamed_stage_keeps_its_deals_and_a_removed_one_hands_them_over(zworld, fake):
    boards_of(fake)
    with SessionLocal() as s:
        approval = s.scalar(select(Stage).where(Stage.name == "Request the Approval (AHS)"))
        card = s.get(Opportunity, zworld.ids['jane_card'])
        card.stage_id, card.pipeline_id = approval.id, approval.pipeline_id
        stage_id, moved_deal = approval.id, card.id
        leftover = s.scalar(select(Stage).where(Stage.name == "Submit Invoices"))
        s.add(Opportunity(title="stranded", contact_id=zworld.ids['jane'],
                          pipeline_id=leftover.pipeline_id, stage_id=leftover.id,
                          value_cents=0))
        s.commit()
    run(commit=True, phases={"boards"})
    with SessionLocal() as s:
        # "Request the Approval (AHS)" is Zuper's "Approval Requested": the same row, renamed,
        # with its deal still on it.
        renamed = s.get(Stage, stage_id)
        assert renamed is not None and renamed.name == "Approval Requested"
        assert s.get(Opportunity, moved_deal).stage_id == stage_id
        # "Submit Invoices" is not on Zuper's board: it goes, and its deal lands on the stage
        # the alias names ("Awaiting AHS Payment"), never on no stage at all.
        assert s.scalar(select(Stage).where(Stage.name == "Submit Invoices")) is None
        stranded = s.scalar(select(Opportunity).where(Opportunity.title == "stranded"))
        assert s.get(Stage, stranded.stage_id).name == "Awaiting AHS Payment"


def test_the_2026_09_25_ahs_board_keeps_every_card(zworld, fake):
    """Production after 2026-09-23 has "Inspecting" and "On My Way" stages. The owner renamed
    "Inspecting" to "Inspection" in Zuper (same status), added two stages and deleted "On My
    Way": the CRM row is renamed, not replaced, and a card on "On My Way" lands on Inspection."""
    ahs_uid, _ = boards_of(fake)
    fake.statuses[ahs_uid] = [{"status_uid": "st-ahs-old-%d" % i, "status_name": n,
                               "status_type": "OTHER"} for i, n in enumerate(AHS_STATUSES_0922)]
    run(commit=True, phases={"boards"})
    assert stages(AHS) == AHS_STATUSES_0922
    with SessionLocal() as s:
        inspecting = s.scalar(select(Stage).where(Stage.name == "Inspecting"))
        on_my_way = s.scalar(select(Stage).where(Stage.name == "On My Way"))
        card = s.get(Opportunity, zworld.ids['jane_card'])
        card.stage_id, card.pipeline_id = inspecting.id, inspecting.pipeline_id
        s.add(Opportunity(title="on the way", contact_id=zworld.ids['jane'],
                          pipeline_id=on_my_way.pipeline_id, stage_id=on_my_way.id,
                          value_cents=0))
        s.commit()
        inspecting_id, card_id = inspecting.id, card.id
    # The owner's change in Zuper: same uid for the renamed status, two new ones, one deleted.
    old = {r["status_name"]: r["status_uid"] for r in fake.statuses[ahs_uid]}
    fake.statuses[ahs_uid] = [{"status_uid": old.get("Inspecting" if n == "Inspection" else n)
                               or "st-ahs-new-%d" % i, "status_name": n, "status_type": "OTHER"}
                              for i, n in enumerate(AHS_STATUSES)]
    code, report = run(commit=True, phases={"boards"})
    assert code == 0, report["problems"]
    assert stages(AHS) == AHS_STATUSES
    with SessionLocal() as s:
        row = s.get(Stage, inspecting_id)
        assert row is not None and row.name == "Inspection"          # the same row, renamed
        assert s.get(Opportunity, card_id).stage_id == inspecting_id
        moved = s.scalar(select(Opportunity).where(Opportunity.title == "on the way"))
        assert moved.stage_id == inspecting_id                       # never the first stage
        assert s.scalar(select(Stage).where(Stage.name == "On My Way")) is None
        assert mapping.stage_for_status(s, old["Inspecting"]) == inspecting_id


REPAIR_BOARD = "AHS - Repair & Review"
REPAIR_STATUSES = ["Repair Scheduled", "Repair In Process", "Repair Complete",
                   "Invoice Submitted to AHS", "Awaiting AHS Payment", "Paid", "Review Requested",
                   "Review Received", "Call Back", "Waiting for Customer", "Reschedule Required",
                   "Cancelled"]


def test_an_ahs_card_follows_its_job_onto_the_repair_and_review_pipeline(zworld, fake, monkeypatch):
    """2026-09-25: the owner split the AHS board; a job reaching Approved moves (the same job) to
    "AHS - Repair & Review". Its CRM card follows it into a CRM pipeline of that name."""
    monkeypatch.setenv("ZUPER_PULL_ONLY", "true")
    ahs_uid, _ = boards_of(fake)
    repair = board(fake, REPAIR_BOARD, REPAIR_STATUSES)
    cust = fake.new_customer(customer_first_name="Jane", customer_last_name="Roof")
    job = fake.new_job(ahs_uid, job_title="Jane Roof - leak", customer={"customer_uid": cust})
    fake.set_custom(fake.jobs[job], "Workiz Job #", "WZ-SPLIT")
    with SessionLocal() as s:
        card = s.get(Opportunity, zworld.ids['jane_card'])
        card.custom_fields = {**(card.custom_fields or {}), "workiz_id": "WZ-SPLIT"}
        s.commit()
    run(commit=True, phases={"boards", "links", "backfill"})
    fake.jobs[job]["job_category"] = {"category_uid": repair}
    fake.move_job(job, "st-%s-0" % REPAIR_BOARD.lower())   # "Repair Scheduled"
    code, report = run(commit=True, phases={"boards", "backfill"})
    assert code == 0, report["problems"]
    assert stages(REPAIR_BOARD) == REPAIR_STATUSES
    with SessionLocal() as s:
        card = s.get(Opportunity, zworld.ids['jane_card'])
        assert s.get(Pipeline, card.pipeline_id).name == REPAIR_BOARD
        assert s.get(Stage, card.stage_id).name == "Repair Scheduled"
    assert not fake.writes()


def test_every_stage_is_paired_with_the_status_of_the_same_name(zworld, fake):
    ahs_uid, _ = boards_of(fake)
    run(commit=True, phases={"boards"})
    with SessionLocal() as s:
        p = s.scalar(select(Pipeline).where(Pipeline.name == AHS))
        assert mapping_row(s, "pipeline", p.id).zuper_uid == ahs_uid
        for st in s.scalars(select(Stage).where(Stage.pipeline_id == p.id)):
            m = mapping_row(s, "stage", st.id)
            assert m is not None and m.state == "linked" and m.parent_uid == ahs_uid
            # the pairing the pull reads: status uid -> this stage
            assert mapping.stage_for_status(s, m.zuper_uid) == st.id


def test_a_job_pairs_with_the_card_holding_the_same_workiz_number(zworld, fake):
    ahs_uid, _ = boards_of(fake)
    run(commit=True, phases={"boards"})
    with SessionLocal() as s:
        card = s.get(Opportunity, zworld.ids['jane_card'])
        card.custom_fields = {**(card.custom_fields or {}), "workiz_id": "WZ-77"}
        s.commit()
    cust = fake.new_customer(customer_first_name="Jane", customer_last_name="Roof")
    job = fake.new_job(ahs_uid, job_title="Jane Roof - leak", customer={"customer_uid": cust})
    fake.set_custom(fake.jobs[job], "Workiz Job #", "WZ-77")
    code, report = run(commit=True, phases={"links"})
    assert code == 0 and report["links"]["jobs_linked"] == 1
    with SessionLocal() as s:
        assert mapping_row(s, "opportunity", zworld.ids['jane_card']).zuper_uid == job
        assert mapping_row(s, "contact", zworld.ids['jane']).zuper_uid == cust


def test_the_backfill_moves_a_paired_card_to_the_status_zuper_has(zworld, fake, monkeypatch):
    monkeypatch.setenv("ZUPER_PULL_ONLY", "true")
    ahs_uid, _ = boards_of(fake)
    cust = fake.new_customer(customer_first_name="Jane", customer_last_name="Roof")
    job = fake.new_job(ahs_uid, job_title="Jane Roof - leak", customer={"customer_uid": cust})
    fake.set_custom(fake.jobs[job], "Workiz Job #", "WZ-88")
    fake.move_job(job, "st-%s-9" % mapping.CATEGORIES[AHS].lower())  # "Repair Scheduled"
    with SessionLocal() as s:
        card = s.get(Opportunity, zworld.ids['jane_card'])
        card.custom_fields = {**(card.custom_fields or {}), "workiz_id": "WZ-88"}
        s.commit()
    run(commit=True, phases={"boards", "links", "backfill"})
    with SessionLocal() as s:
        card = s.get(Opportunity, zworld.ids['jane_card'])
        # A Workiz-origin card used to keep the CRM's stage until the cutover; a mirror follows
        # Zuper, and rolls nothing back there.
        assert s.get(Stage, card.stage_id).name == "Repair Scheduled"
    assert not fake.writes()


def test_a_dry_run_changes_nothing(zworld, fake):
    boards_of(fake)
    before = stages(AHS)
    code, report = run(commit=False, phases={"boards", "links"})
    assert stages(AHS) == before
    with SessionLocal() as s:
        assert s.scalars(select(ZuperMapping)).first() is None
    assert report["boards"][0]["created"], "a dry run still says what it would do"


# ------------------------------------------------------- the regional boards (2026-09-24)
# The owner made six more Zuper categories with Retail's statuses (Miami / Sarasota x repair,
# roof replacement, gutters). The mirror makes a CRM pipeline for each one the account has.

GUTTERS = "Miami Gutters"


def test_a_regional_zuper_board_becomes_a_new_crm_pipeline(zworld, fake):
    boards_of(fake)
    cat = board(fake, GUTTERS, RETAIL_STATUSES)
    code, report = run(commit=True, phases={"boards"})
    assert code == 0, report["problems"]
    assert stages(GUTTERS) == RETAIL_STATUSES
    with SessionLocal() as s:
        p = s.scalar(select(Pipeline).where(Pipeline.name == GUTTERS))
        assert mapping_row(s, "pipeline", p.id).zuper_uid == cat
        for st in s.scalars(select(Stage).where(Stage.pipeline_id == p.id)):
            m = mapping_row(s, "stage", st.id)
            assert m.parent_uid == cat and mapping.stage_for_status(s, m.zuper_uid) == st.id
    # A second run finds the pipeline it made: still one, with the same stages.
    run(commit=True, phases={"boards"})
    with SessionLocal() as s:
        assert len(s.scalars(select(Pipeline).where(Pipeline.name == GUTTERS)).all()) == 1
    assert stages(GUTTERS) == RETAIL_STATUSES


def test_a_regional_board_the_account_lacks_is_skipped_not_a_problem(zworld, fake):
    boards_of(fake)                                        # AHS and Retail only
    code, report = run(commit=True, phases={"boards"})
    assert code == 0 and [b["pipeline"] for b in report["boards"]] == [AHS, "Retail"]
    with SessionLocal() as s:
        assert s.scalar(select(Pipeline).where(Pipeline.name == GUTTERS)) is None


def test_a_dry_run_names_the_pipeline_it_would_make_and_makes_none(zworld, fake):
    boards_of(fake)
    board(fake, GUTTERS, RETAIL_STATUSES)
    run(commit=False, phases={"boards"})
    code, report = run(commit=False, phases={"boards"})
    entry = next(b for b in report["boards"] if b["pipeline"] == GUTTERS)
    assert entry.get("pipeline_created") and entry["created"] == RETAIL_STATUSES
    assert "Miami Gutters <- Miami Gutters (new CRM pipeline)" in mirror.render(report)
    with SessionLocal() as s:
        assert s.scalar(select(Pipeline).where(Pipeline.name == GUTTERS)) is None


def test_a_job_on_a_regional_board_lands_in_that_pipeline(zworld, fake, monkeypatch):
    monkeypatch.setenv("ZUPER_PULL_ONLY", "true")
    boards_of(fake)
    cat = board(fake, GUTTERS, RETAIL_STATUSES)
    cust = fake.new_customer(customer_first_name="Gus", customer_last_name="Gutter")
    job = fake.new_job(cat, job_title="Gus Gutter - gutters", customer={"customer_uid": cust})
    fake.move_job(job, "st-miami gutters-6")               # "Scheduled"
    run(commit=True, phases={"boards", "links", "backfill"})
    with SessionLocal() as s:
        m = s.scalar(select(ZuperMapping).where(ZuperMapping.crm_type == "opportunity",
                                                ZuperMapping.zuper_uid == job))
        card = s.get(Opportunity, m.crm_id)
        assert s.get(Pipeline, card.pipeline_id).name == GUTTERS
        assert s.get(Stage, card.stage_id).name == "Scheduled"
    assert not fake.writes()


def test_a_card_follows_its_job_from_retail_to_a_regional_board(zworld, fake, monkeypatch):
    monkeypatch.setenv("ZUPER_PULL_ONLY", "true")
    _, retail_uid = boards_of(fake)
    cat = board(fake, GUTTERS, RETAIL_STATUSES)
    cust = fake.new_customer(customer_first_name="Jane", customer_last_name="Roof")
    job = fake.new_job(retail_uid, job_title="Jane Roof - gutters",
                       customer={"customer_uid": cust})
    fake.set_custom(fake.jobs[job], "Workiz Job #", "WZ-99")
    with SessionLocal() as s:
        card = s.get(Opportunity, zworld.ids['jane_card'])
        card.custom_fields = {**(card.custom_fields or {}), "workiz_id": "WZ-99"}
        s.commit()
    run(commit=True, phases={"boards", "links", "backfill"})
    # The office moves the job to the regional board in Zuper; the next pull moves the card.
    fake.jobs[job]["job_category"] = {"category_uid": cat}
    fake.move_job(job, "st-miami gutters-8")               # "Repair Complete"
    run(commit=True, phases={"backfill"})
    with SessionLocal() as s:
        card = s.get(Opportunity, zworld.ids['jane_card'])
        assert s.get(Pipeline, card.pipeline_id).name == GUTTERS
        assert s.get(Stage, card.stage_id).name == "Repair Complete"
    assert not fake.writes()


def test_a_regional_pipeline_leaves_the_setup_check_and_the_switch_alone(armed, fake):
    """Setup, its check and the Settings switch know AHS and Retail only: a mirrored regional
    pipeline is neither a problem there nor something setup tries to make in Zuper."""
    board(fake, GUTTERS, RETAIL_STATUSES)
    code, report = run(commit=True, phases={"boards"})
    assert code == 0, report["problems"]
    writes = len(fake.writes())
    from app.zuper import setup
    with SessionLocal() as s:
        checked = setup.run(s, commit=False)
        assert checked["passed"], checked["checks"]
        assert GUTTERS not in repr(checked)
        assert not setup.blockers(s)
    assert len(fake.writes()) == writes


def test_a_card_on_a_regional_board_is_never_sent_to_zuper(armed, fake):
    """Pull-only off and the sync on: a Retail card may be sent, a hand-made card in a
    regional pipeline may not — its jobs are made in Zuper — and refusing queues nothing."""
    from app.models import Job
    from sqlalchemy import func
    board(fake, GUTTERS, RETAIL_STATUSES)
    run(commit=True, phases={"boards"})
    assert not config.pull_only()
    c = armed.client("owner")
    who = c.post("/api/contacts", json={"first_name": "Gil", "last_name": "Gutter",
                                        "phone": "+19415550142"}).json()
    address = {"address_street": "1 Main St", "address_city": "Miami",
               "address_state": "FL", "address_postal_code": "33101"}
    with SessionLocal() as s:
        p = s.scalar(select(Pipeline).where(Pipeline.name == GUTTERS))
        first = s.scalars(select(Stage).where(Stage.pipeline_id == p.id)
                          .order_by(Stage.position, Stage.id)).first()
        retail = s.scalar(select(Pipeline).where(Pipeline.name == "Retail"))
        retail_first = s.scalars(select(Stage).where(Stage.pipeline_id == retail.id)
                                 .order_by(Stage.position, Stage.id)).first()
        pid, sid, rid, rsid = p.id, first.id, retail.id, retail_first.id
    regional = c.post("/api/opportunities", json={
        "title": "Gil - gutters", "pipeline_id": pid, "stage_id": sid,
        "contact_id": who["id"], **address}).json()["id"]
    at_retail = c.post("/api/opportunities", json={
        "title": "Gil - roof", "pipeline_id": rid, "stage_id": rsid,
        "contact_id": who["id"], **address}).json()["id"]
    block = c.get("/api/opportunities/%d" % regional).json()["zuper"]
    assert block["can_send"] is False
    assert any("copied from Zuper" in p for p in block["send_problems"]), block
    assert c.get("/api/opportunities/%d" % at_retail).json()["zuper"]["can_send"] is True
    writes = len(fake.writes())
    r = c.post("/api/opportunities/%d/zuper/send" % regional)
    assert r.status_code == 409 and "copied from Zuper" in r.json()["detail"]
    with SessionLocal() as s:
        assert mapping_row(s, "opportunity", regional) is None
        assert s.scalar(select(func.count(Job.id)).where(Job.type == "zuper_send")) == 0
    assert len(fake.writes()) == writes


# ------------------------------------------------------------------ the one-way guard

def test_pull_only_makes_a_pull_write_nothing_back_to_zuper(zworld, fake, monkeypatch):
    monkeypatch.setenv("ZUPER_PULL_ONLY", "true")
    assert config.pull_only()
    ahs_uid, _ = boards_of(fake)
    cust = fake.new_customer(customer_first_name="Jane", customer_last_name="Roof")
    fake.new_job(ahs_uid, job_title="Jane Roof - leak", customer={"customer_uid": cust})
    run(commit=True, phases={"boards", "links", "backfill"})
    # Pulling a job used to stamp "CRM Opportunity ID" / "CRM Link" on it straight away.
    assert not fake.writes(), [ (w.method, w.path) for w in fake.writes() ]


def test_pull_only_queues_no_push_when_the_crm_changes(armed, fake, monkeypatch):
    monkeypatch.setenv("ZUPER_PULL_ONLY", "true")
    from tests.zuper_support import zuper_jobs
    with SessionLocal() as s:
        card = s.get(Opportunity, armed.ids['jane_card'])
        card.title = "renamed in the CRM"
        s.commit()
    with SessionLocal() as s:
        assert zuper_jobs(s) == []


def test_pull_only_refuses_send_to_zuper_with_a_sentence(loaded, fake, monkeypatch):
    monkeypatch.setenv("ZUPER_PULL_ONLY", "true")
    from app.zuper import api as zuper_api
    with SessionLocal() as s:
        o = s.get(Opportunity, loaded.ids['tim_card'])
        block = zuper_api.send_block(s, o, None)
    assert not block["can_send"]
    assert any("one way" in p for p in block["send_problems"]), block["send_problems"]


def test_the_client_refuses_any_write_while_the_mirror_is_one_way(zuper_env, monkeypatch):
    from app.zuper import client
    monkeypatch.setenv("ZUPER_PULL_ONLY", "true")
    try:
        client.request("PUT", "/jobs", body={"job": {}})
    except client.ZuperError as exc:
        assert exc.kind == "refused"
    else:
        raise AssertionError("a write reached Zuper while ZUPER_PULL_ONLY was set")


HOOK = {"web_hook": {"webhook_module": "JOB", "webhook_event": "job.create",
                     "webhook_url": "https://crm.example/api/zuper/webhook"}}


def test_the_webhook_may_still_be_registered_while_the_mirror_is_one_way(fake, monkeypatch):
    """Registering the webhook is how Zuper is asked to TELL us about a change, so that is the
    one write a mirror still makes — and only from the operator's command."""
    from app.zuper import client
    monkeypatch.setenv("ZUPER_PULL_ONLY", "true")
    try:
        client.request("POST", "/webhook", body=HOOK)
    except client.ZuperError as exc:
        assert exc.kind == "refused", "outside operator mode even this one is refused"
    else:
        raise AssertionError("a webhook was registered outside the operator's command")
    with client.operator_mode():
        client.request("POST", "/webhook", body=HOOK)
    assert [w.path for w in fake.writes()] == ["/webhook"]


def test_two_zuper_customers_pointing_at_one_contact_do_not_collide(zworld, fake):
    """The owner split customers who shared a phone in Workiz, so two Zuper customers can be
    the same person here. A mapping is one-to-one on both sides: the first pairing wins and
    the second is counted, never attempted (it would break the whole run)."""
    ahs_uid, _ = boards_of(fake)
    run(commit=True, phases={"boards"})
    with SessionLocal() as s:
        for i, cid in enumerate(["WZ-A", "WZ-B"]):
            card = s.get(Opportunity, zworld.ids["jane_card" if i == 0 else "noaddr_card"])
            card.custom_fields = {**(card.custom_fields or {}), "workiz_id": cid}
        s.commit()
    one = fake.new_customer(customer_first_name="Jane", customer_last_name="Roof")
    two = fake.new_customer(customer_first_name="Coco", customer_last_name="Torres")
    for cid, cust in (("WZ-A", one), ("WZ-B", two)):
        job = fake.new_job(ahs_uid, job_title="j " + cid, customer={"customer_uid": cust})
        fake.set_custom(fake.jobs[job], "Workiz Job #", cid)
    code, report = run(commit=True, phases={"links"})
    assert code == 0, report["problems"]
    assert report["links"]["jobs_linked"] == 2
    assert report["links"]["contacts_linked"] + report["links"]["contacts_taken"] == 2


def test_a_job_still_syncs_when_a_child_module_is_not_available(zworld, fake, monkeypatch):
    """Live Zuper answers 403 for appointments (the account has no Appointments module) and
    404 for the service-task path. A mirror must take the job anyway."""
    monkeypatch.setenv("ZUPER_PULL_ONLY", "true")
    ahs_uid, _ = boards_of(fake)
    cust = fake.new_customer(customer_first_name="Jane", customer_last_name="Roof")
    job = fake.new_job(ahs_uid, job_title="Jane Roof - leak", customer={"customer_uid": cust})
    fake.set_custom(fake.jobs[job], "Workiz Job #", "WZ-99")
    fake.move_job(job, "st-%s-9" % mapping.CATEGORIES[AHS].lower())  # "Repair Scheduled"
    with SessionLocal() as s:
        card = s.get(Opportunity, zworld.ids["jane_card"])
        card.custom_fields = {**(card.custom_fields or {}), "workiz_id": "WZ-99"}
        s.commit()

    from app.zuper import client as zclient
    from app.zuper import zapi
    real = zapi.request

    def refuse_children(method, path, **kw):
        if "/appointments" in path:
            raise zclient.ZuperError("unauthorized", "HTTP 403")
        if "/service_tasks" in path:
            raise zclient.ZuperError("not_found", path)
        return real(method, path, **kw)

    # zapi does `from .client import request`, so the name to replace is zapi's own.
    monkeypatch.setattr(zapi, "request", refuse_children)
    code, report = run(commit=True, phases={"boards", "links", "backfill"})
    assert code == 0, report["problems"]
    with SessionLocal() as s:
        card = s.get(Opportunity, zworld.ids["jane_card"])
        assert s.get(Stage, card.stage_id).name == "Repair Scheduled"


def test_a_sweep_succeeds_when_a_whole_module_is_refused(armed, fake, monkeypatch):
    """Live Zuper answers 403 for every appointments read (no Appointments module on this
    plan). The sweep must skip that module and still succeed, or its cursor never moves and
    every sweep re-reads the whole account."""
    from app.zuper import client as zclient
    from app.zuper import engine as zengine
    from app.zuper import sweep, zapi
    monkeypatch.setenv("ZUPER_PULL_ONLY", "true")
    real = zapi.request

    def refuse_appointments(method, path, **kw):
        if path.startswith("/appointments"):
            raise zclient.ZuperError("unauthorized", "HTTP 403")
        return real(method, path, **kw)

    monkeypatch.setattr(zapi, "request", refuse_appointments)
    with SessionLocal() as s:
        counts = sweep.run(s)
    assert counts.get("module_unavailable:appointments") == 1, counts
    with SessionLocal() as s:
        state = zengine.sync_state(s)
        assert state.last_sweep_success_at is not None, state.last_error
        assert state.last_error is None

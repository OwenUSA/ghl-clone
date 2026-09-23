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

# The live AHS board (2026-09-22), in its own order.
AHS_STATUSES = ["Work Order Received", "Contact Attempted", "Scheduled", "On My Way",
                "Inspecting", "Proposal Made", "Approval Requested", "Approved",
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
    return (board(fake, "AHS", AHS_STATUSES), board(fake, "Retail", RETAIL_STATUSES))


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
        inspection = s.scalar(select(Stage).where(Stage.name == "Inspection"))
        stage_id, moved_deal = inspection.id, zworld.ids['jane_card']
        leftover = s.scalar(select(Stage).where(Stage.name == "Submit Invoices"))
        s.add(Opportunity(title="stranded", contact_id=zworld.ids['jane'],
                          pipeline_id=leftover.pipeline_id, stage_id=leftover.id,
                          value_cents=0))
        s.commit()
    run(commit=True, phases={"boards"})
    with SessionLocal() as s:
        # "Inspection" is Zuper's "Inspecting": the same row, renamed, with its deal still on it.
        renamed = s.get(Stage, stage_id)
        assert renamed is not None and renamed.name == "Inspecting"
        assert s.get(Opportunity, moved_deal).stage_id == stage_id
        # "Submit Invoices" is not on Zuper's board: it goes, and its deal lands on the stage
        # the alias names ("Awaiting AHS Payment"), never on no stage at all.
        assert s.scalar(select(Stage).where(Stage.name == "Submit Invoices")) is None
        stranded = s.scalar(select(Opportunity).where(Opportunity.title == "stranded"))
        assert s.get(Stage, stranded.stage_id).name == "Awaiting AHS Payment"


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
    fake.move_job(job, "st-ahs-8")                       # "Repair Scheduled"
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

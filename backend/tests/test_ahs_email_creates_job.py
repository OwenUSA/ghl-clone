"""An AHS work-order email creates its job in Zuper — the one write a one-way mirror makes.

Zuper has no email ingest of any kind (no endpoint, and a workflow is only ever started by a
record event), so a work order that arrives as an email reaches Zuper only if the CRM creates it.
`ZUPER_AHS_EMAIL_CREATES_JOBS` permits exactly that, on top of `ZUPER_PULL_ONLY`, and these
tests pin how narrow it is: three POSTs for one card, nothing else — no update, no status move,
no delete, no other card, and no visit or task even on the card that is allowed.

Behaviour, read back from the fake Zuper's own records: what was created, on which board, in
which column, and — the point of most of these — what never left at all.
"""
from app import ahs_jobs
from app.db import SessionLocal
from app.models import Appointment, Calendar, Opportunity, OpportunityTask, Pipeline, Stage
from app.zuper import api as zuper_api
from app.zuper import client, config, engine, mapping
from sqlalchemy import select
from tests.zuper_support import AHS, drain

WORK_ORDER = ("Job 66450639 ROOF (Normal priority)\nCustomer: Guillermo Escala\n"
              "Problem: Roof leak over the kitchen")

ORDER = {
    "ahs_job_id": "66450639",
    "service": "ROOF",
    "customer_name": "Guillermo Escala",
    "phone": "+13059629757",
    "email": "scalas02@example.test",
    "service_address": "14436 SW 95TH LN MIAMI, FL 33186",
    "value_cents": 12500,
    "description": WORK_ORDER,
    "message_id": "<abc@dispatch.me>",
    "received_at": "2026-09-14T13:05:00+00:00",
}


def one_way(monkeypatch, *, ahs_creates: bool = True) -> None:
    """Production's shape after 2026-09-28: the mirror is one way, with the AHS exception."""
    monkeypatch.setenv("ZUPER_PULL_ONLY", "true")
    monkeypatch.setenv("ZUPER_AHS_EMAIL_CREATES_JOBS", "true" if ahs_creates else "false")
    assert config.pull_only()


def deliver(world, fake, **kw):
    """One work order, as owen-main posts it, then the worker's Zuper thread.

    The write log is cleared first: arming the sync (the `armed` fixture) creates the categories
    and their statuses, and those are the operator's writes, not this feature's.
    """
    fake.requests.clear()
    body = {**ORDER, **kw}
    r = world.client().post("/api/ahs-jobs", json=body)
    assert r.status_code in (200, 201), r.text
    drain()
    return r.json()


def zuper_job(fake) -> dict:
    jobs = [j for j in fake.jobs.values() if not j.get("is_deleted")]
    assert len(jobs) == 1, [j.get("job_title") for j in jobs]
    return jobs[0]


def board_of(fake, job) -> str:
    uid = (job.get("job_category") or {}).get("category_uid")
    return fake.categories[uid]["category_name"]


def column_of(fake, job) -> str:
    return (job.get("current_job_status") or job.get("job_status") or {}).get("status_name")


def card_of(result) -> int:
    return result["opportunity"]["id"]


# ------------------------------------------------------------------ it creates the job

def test_an_emailed_work_order_creates_its_job_on_the_ahs_inspection_board(armed, fake,
                                                                          monkeypatch):
    one_way(monkeypatch)
    deliver(armed, fake)
    job = zuper_job(fake)
    assert board_of(fake, job) == mapping.CATEGORIES[AHS] == "AHS - Inspection"
    assert job["job_title"]
    assert job.get("customer", {}).get("customer_uid"), "the customer went with it"


def test_the_job_lands_in_the_boards_first_column_because_nothing_sets_a_status(armed, fake,
                                                                               monkeypatch):
    """"In the AHS - Inspection item by default" — the CRM never names a status, so the job
    takes whatever Zuper opens a new job in, which is that board's first column."""
    one_way(monkeypatch)
    deliver(armed, fake)
    job = zuper_job(fake)
    first = fake.statuses[(job["job_category"] or {})["category_uid"]][0]["status_name"]
    assert column_of(fake, job) == first
    assert fake.calls("PUT", r"/jobs/[^/]+/status") == [], "no status was ever moved"


def test_the_work_order_goes_with_it_as_a_note(armed, fake, monkeypatch):
    one_way(monkeypatch)
    deliver(armed, fake)
    notes = fake.notes[zuper_job(fake)["job_uid"]]
    assert any(WORK_ORDER.split("\n")[0] in (n.get("note") or "") for n in notes), notes


def test_only_three_creates_ever_leave_and_no_other_kind_of_write(armed, fake, monkeypatch):
    one_way(monkeypatch)
    deliver(armed, fake)
    seen = [(w.method, w.path) for w in fake.writes()]
    assert seen, "something was created"
    assert {m for m, _ in seen} == {"POST"}, seen
    assert all(path in ("/customers_new", "/jobs") or path.endswith("/note")
               for _, path in seen), seen


def test_the_visit_and_the_tasks_stay_in_the_crm_even_on_the_card_that_is_allowed(armed, fake,
                                                                                 monkeypatch):
    """Under the mirror Zuper owns the schedule and its own task list, so the send carries the
    work order and nothing else — a visit or a task would be the CRM writing Zuper's truth."""
    one_way(monkeypatch)
    result = deliver(armed, fake)
    with SessionLocal() as s:
        card = s.get(Opportunity, card_of(result))
        cal = s.scalar(select(Calendar))
        s.add(Appointment(title="Inspection", calendar_id=cal.id, contact_id=card.contact_id,
                          opportunity_id=card.id,
                          starts_at=card.created_at, ends_at=card.created_at))
        s.add(OpportunityTask(opportunity_id=card.id, title="Call AHS",
                              created_by_id=armed.ids["owner"]))
        s.commit()
    drain()
    assert fake.calls("POST", r"^/appointments$") == []
    assert fake.calls("POST", r"/service_tasks") == []


def test_the_same_work_order_delivered_twice_makes_one_zuper_job(armed, fake, monkeypatch):
    one_way(monkeypatch)
    first = deliver(armed, fake)
    again = deliver(armed, fake)
    assert card_of(again) == card_of(first)
    zuper_job(fake)                                     # exactly one, or this raises


# ------------------------------------------------------------------ and nothing else

def test_with_the_switch_off_the_email_still_makes_the_card_and_zuper_hears_nothing(
        armed, fake, monkeypatch):
    one_way(monkeypatch, ahs_creates=False)
    result = deliver(armed, fake)
    with SessionLocal() as s:
        assert s.get(Opportunity, card_of(result)) is not None
    assert fake.writes() == [], [(w.method, w.path) for w in fake.writes()]


def test_an_ordinary_card_is_still_refused_while_the_ahs_email_is_allowed(armed, fake,
                                                                         monkeypatch):
    one_way(monkeypatch)
    with SessionLocal() as s:
        other = s.get(Opportunity, armed.ids["tim_card"])
        block = zuper_api.send_block(s, other, None)
    assert not block["can_send"]
    assert any("one way" in p for p in block["send_problems"]), block["send_problems"]


def test_a_card_moved_off_the_ahs_pipeline_is_no_longer_the_exception(armed, fake, monkeypatch):
    one_way(monkeypatch)
    result = deliver(armed, fake)
    with SessionLocal() as s:
        card = s.get(Opportunity, card_of(result))
        retail = s.scalar(select(Pipeline).where(Pipeline.name == "Retail"))
        card.pipeline_id = retail.id
        card.stage_id = s.scalar(select(Stage.id).where(Stage.pipeline_id == retail.id))
        assert not zuper_api.may_create_in_zuper(s, card)


def test_a_card_without_the_work_orders_id_is_not_the_exception(armed, fake, monkeypatch):
    one_way(monkeypatch)
    result = deliver(armed, fake)
    with SessionLocal() as s:
        card = s.get(Opportunity, card_of(result))
        card.custom_fields = {}
        assert not zuper_api.may_create_in_zuper(s, card)


def test_a_card_the_mailbox_did_not_make_is_not_the_exception(armed, fake, monkeypatch):
    one_way(monkeypatch)
    result = deliver(armed, fake)
    with SessionLocal() as s:
        card = s.get(Opportunity, card_of(result))
        card.created_by = "Workiz import"
        assert not zuper_api.may_create_in_zuper(s, card)


def test_the_scope_itself_refuses_an_update_a_status_move_and_a_delete(zuper_env, monkeypatch):
    """Even inside `client.ahs_create()` — the widest the exception ever gets — the only
    non-GETs that pass are the three creates."""
    one_way(monkeypatch)
    refused = []
    with client.ahs_create():
        for method, path, body in (("PUT", "/jobs", {"job": {}}),
                                   ("PUT", "/jobs/j1/status", {"status_uid": "s"}),
                                   ("DELETE", "/jobs/j1/delete", None),
                                   ("PUT", "/customers/c1", {}),
                                   ("POST", "/appointments", {})):
            try:
                client.request(method, path, body=body)
            except client.ZuperError as exc:
                refused.append((method, path, exc.kind))
    assert [r[2] for r in refused] == ["refused"] * 5, refused


def test_outside_the_scope_even_the_three_creates_are_refused(zuper_env, monkeypatch):
    one_way(monkeypatch)
    for path in ("/customers_new", "/jobs"):
        try:
            client.request("POST", path, body={})
        except client.ZuperError as exc:
            assert exc.kind == "refused", (path, exc.kind)
        else:
            raise AssertionError("POST %s was not refused outside ahs_create()" % path)


def test_the_worker_opens_the_scope_only_for_a_send_the_ahs_ingest_queued(armed, fake,
                                                                         monkeypatch):
    """The flag is on the queued job, not read from the card at run time, so a send queued for
    anything else cannot borrow the scope."""
    one_way(monkeypatch)
    from app.models import Job
    deliver(armed, fake)
    with SessionLocal() as s:
        sends = s.scalars(select(Job).where(Job.type == "zuper_send")).all()
        assert sends and all((j.payload or {}).get("ahs_email") for j in sends), \
            [j.payload for j in sends]


def test_the_constants_are_the_ahs_ingests_own(armed):
    """`api.may_create_in_zuper` reads two of `ahs_jobs`' values without importing it."""
    assert zuper_api.AHS_EMAIL_CREATED_BY == ahs_jobs.CREATED_BY
    assert mapping.AHS_JOB_ID == ahs_jobs.AHS_JOB_ID


def test_the_relaxed_address_rule_belongs_to_the_ahs_email_not_to_the_switch(armed, fake,
                                                                             monkeypatch):
    """An AHS address usually has no city to split off, so an AHS email card needs the street
    only — and that is true of the card itself, whether or not the exception is switched on.
    Any other card still needs all four parts."""
    monkeypatch.setenv("ZUPER_PULL_ONLY", "false")
    monkeypatch.setenv("ZUPER_AHS_EMAIL_CREATES_JOBS", "false")
    result = deliver(armed, fake)
    with SessionLocal() as s:
        card = s.get(Opportunity, card_of(result))
        assert not card.address_city, "the AHS format left no city to split off"
        assert engine.send_problems(s, card) == []
        other = s.get(Opportunity, armed.ids["noaddr_card"])
        assert any("missing: street, city" in p for p in engine.send_problems(s, other))
    assert board_of(fake, zuper_job(fake)) == mapping.CATEGORIES[AHS]


def test_the_job_create_names_the_customer_with_customer_uid(armed, fake, monkeypatch):
    """The key the LIVE API takes on a write (measured 2026-09-28).

    `customer` — which is how a GET nests it back — makes the live API answer "Either customer
    or organization data is required" and create nothing; the first seven AHS email sends failed
    exactly so, while these tests passed, because the fake accepted both spellings. The fake now
    refuses `customer` the way Zuper does, so this test fails if the key is ever changed back.
    """
    one_way(monkeypatch)
    deliver(armed, fake)
    posted = [w for w in fake.writes() if w.path == "/jobs"]
    assert len(posted) == 1, [(w.method, w.path) for w in fake.writes()]
    body = posted[0].body["job"]
    assert body.get("customer_uid"), body
    assert "customer" not in body, body
    assert zuper_job(fake)["customer"]["customer_uid"] == body["customer_uid"]


def test_the_job_create_carries_a_due_date_and_books_no_visit(armed, fake, monkeypatch):
    """Zuper refuses a create with no end/due date, and a work order has no visit yet.

    `due_date` satisfies it and leaves the job UNSCHEDULED (measured on the AHS - TEST board,
    2026-09-28: `is_scheduled` false, `scheduled_start_time` null). A placeholder
    `scheduled_end_time` would instead have invented a visit on the dispatch board — so this
    test pins both halves: a date is sent, and it is not a schedule.
    """
    one_way(monkeypatch)
    deliver(armed, fake)
    body = next(w for w in fake.writes() if w.path == "/jobs").body["job"]
    assert body.get("due_date"), body
    assert "scheduled_start_time" not in body and "scheduled_end_time" not in body, body

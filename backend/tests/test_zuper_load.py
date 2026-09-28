"""The setup command and the initial load, against a fake Zuper at the HTTP boundary.

Behaviour, not status codes: a dry run is proven to write nothing on EITHER side (every
request the fake saw was a GET, and every CRM table hashes the same before and after); a
commit is proven by what the fake holds; idempotency by a second commit that sends no write.
"""
import hashlib
import json

import httpx
import pytest
from app.db import SessionLocal, engine
from app.models import Contact, Opportunity, Stage, ZuperMapping, ZuperSettings
from app.zuper import client, config, load, mapping, setup
from app.zuper.client import ZuperError
from sqlalchemy import func, inspect, select, text
from tests.zuper_support import AHS, RETAIL, arm, mapping_row, uid_of

# The Zuper category names setup creates (2026-09-25: "AHS" became "AHS - Inspection").
AHS_CATEGORY = mapping.CATEGORIES[mapping.AHS_PIPELINE]
CATEGORY_NAMES = sorted(mapping.CATEGORIES.values())


def crm_fingerprint() -> str:
    """A hash of every row of every table: equal before and after means nothing was written."""
    h = hashlib.sha256()
    with engine.connect() as conn:
        for table in sorted(inspect(conn).get_table_names()):
            for row in conn.execute(text('SELECT * FROM "%s" ORDER BY 1' % table)).all():
                h.update(json.dumps([table, *row], default=str).encode())
    return h.hexdigest()


def run_setup(commit: bool) -> dict:
    with SessionLocal() as s:
        return setup.run(s, commit=commit)


# ------------------------------------------------------------------ setup verification

def test_setup_dry_run_reads_only_and_writes_nothing_on_either_side(zworld, fake):
    before = crm_fingerprint()
    report = run_setup(commit=False)
    assert crm_fingerprint() == before
    assert fake.writes() == []
    assert not fake.categories and not fake.statuses
    ahs = next(c for c in report["categories"]["categories"] if c["pipeline"] == AHS)
    assert ahs["action"] == "create"
    assert [s["status"] for s in ahs["statuses"]] == [
        "New Lead", "Inspection", "Request the Approval (AHS)", "Submit Invoices", "Call Back"]
    assert report["categories"]["created_statuses"] == 11


def test_setup_commit_creates_both_categories_with_every_stage_mapped_by_uid(zworld, fake):
    report = run_setup(commit=True)
    names = {c["category_name"] for c in fake.categories.values()}
    assert names == set(mapping.CATEGORIES.values())
    with SessionLocal() as s:
        for key, stage_id in zworld.ids["stages"].items():
            m = mapping_row(s, "stage", stage_id)
            if key.startswith("other:"):
                assert m is None            # "Commercial" is not synced
                continue
            assert m is not None and m.state == "linked"
            cat = next(c for c in fake.categories.values()
                       if c["category_uid"] == m.parent_uid)
            status = next(x for x in fake.statuses[cat["category_uid"]]
                          if x["status_uid"] == m.zuper_uid)
            assert status["status_name"] == s.get(Stage, stage_id).name
    # The empty "Submit Invoices" AHS stage is a status like any other.
    ahs_uid = uid_of("pipeline", zworld.ids["ahs"])
    assert "Submit Invoices" in [x["status_name"] for x in fake.statuses[ahs_uid]]
    # Retail's stages all sit at position 0: ordered by id, and the report says so.
    retail = next(c for c in report["categories"]["categories"] if c["pipeline"] == RETAIL)
    assert retail["ordered_by"].startswith("id")
    assert [x["status_name"] for x in fake.statuses[uid_of("pipeline", zworld.ids["retail"])]] \
        == ["New Lead", "Inspection / Estimate", "Estimate Sent", "Follow Up", "Scheduled",
            "Invoice"]
    assert report["passed"]


def test_a_second_setup_commit_creates_nothing_more(zworld, fake):
    run_setup(commit=True)
    writes = len(fake.writes())
    report = run_setup(commit=True)
    assert len(fake.writes()) == writes
    assert report["categories"]["created_categories"] == 0
    assert report["categories"]["created_statuses"] == 0


def test_a_renamed_stage_renames_its_status_on_the_next_setup(zworld, fake):
    run_setup(commit=True)
    with SessionLocal() as s:
        s.get(Stage, zworld.ids["stages"]["ahs:Call Back"]).name = "Call Back Customer"
        s.commit()
    report = run_setup(commit=True)
    assert report["categories"]["renamed_statuses"] == 1
    ahs_uid = uid_of("pipeline", zworld.ids["ahs"])
    assert "Call Back Customer" in [x["status_name"] for x in fake.statuses[ahs_uid]]


def test_setup_check_passes_and_says_what_must_be_confirmed_by_hand(zworld, fake):
    report = run_setup(commit=True)
    states = {r["key"]: r["state"] for r in report["checks"]}
    assert states["connection"] == setup.PASS
    assert states["sync_user"] == setup.PASS
    assert states["customer_fields"] == states["job_fields_crm"] == setup.PASS
    assert states["job_fields_checklist"] == setup.PASS
    assert states["lead_sources"] == setup.PASS
    assert states["categories"] == setup.PASS
    for key in ("lead_tag", "job_notifications", "portal_invites", "zuper_connect",
                "workflows", "company", "quote_invoice_reminders", "booking_widget"):
        assert states[key] == setup.BY_HAND
    with SessionLocal() as s:
        row = s.get(ZuperSettings, 1)
        assert row.setup_passed and row.sync_user_uid == "u-sync"
        assert row.lead_sources["Facebook"] == "src-6"


def test_setup_check_fails_in_plain_sentences(zworld, fake):
    fake.me = {"user_uid": "u-owner", "first_name": "Owen", "last_name": "Owner",
               "role": "Admin"}
    fake.custom_field_defs["JOB"] = [f for f in fake.custom_field_defs["JOB"]
                                     if f["label"] != "How many leaks?"]
    for f in fake.custom_field_defs["JOB"]:
        if f["label"] == "How old is the roof?":
            f["field_options"] = [{"label": "Under 5 yrs"}]
        if f["label"] == "Technicians":
            f["field_type"] = "NUMBER"
    fake.lead_sources = [s for s in fake.lead_sources if s["source_name"] != "Yelp"]
    report = run_setup(commit=True)
    by_key = {r["key"]: r for r in report["checks"]}
    assert not report["passed"]
    assert by_key["sync_user"]["state"] == setup.PASS
    assert "belongs to “Owen Owner” (Admin)" in by_key["sync_user"]["sentences"][0]
    checklist = " ".join(by_key["job_fields_checklist"]["sentences"])
    assert "The job field “How many leaks?” is missing" in checklist
    assert "“How old is the roof?” is missing the options “5-10 yrs”" in checklist
    assert "“Technicians” is number, not Single line" in " ".join(
        by_key["job_fields_crm"]["sentences"])
    assert by_key["lead_sources"]["sentences"] == ["Missing lead source: “Yelp”."]
    with SessionLocal() as s:
        assert s.get(ZuperSettings, 1).setup_passed is False
        blockers = setup.blockers(s)
    assert any("How many leaks?" in b for b in blockers)


def test_undocumented_settings_become_confirm_by_hand_not_a_guess(zworld, fake):
    fake.custom_field_defs = None       # Zuper answers 404: the REST path does not exist
    fake.lead_sources = None
    report = run_setup(commit=True)
    by_key = {r["key"]: r for r in report["checks"]}
    for key in ("customer_fields", "job_fields_crm", "job_fields_checklist", "lead_sources"):
        assert by_key[key]["state"] == setup.BY_HAND
    assert "“How many leaks?” (Number)" in by_key["job_fields_checklist"]["sentences"][0]
    assert report["passed"]             # nothing FAILED...
    with SessionLocal() as s:           # ...but the switch stays off until each is ticked
        assert any("Not confirmed by hand yet: Customer fields" in b
                   for b in setup.blockers(s))


def test_the_switch_cannot_be_turned_on_until_every_item_passes_and_is_confirmed(zworld, fake):
    c = zworld.client("owner")
    r = c.put("/api/zuper/settings", json={"enabled": True})
    assert r.status_code == 409 and "setup check has not been run" in r.json()["detail"]
    arm(confirm=False, enable=False)
    r = c.put("/api/zuper/settings", json={"enabled": True})
    assert r.status_code == 409 and "Not confirmed by hand yet" in r.json()["detail"]
    with SessionLocal() as s:
        assert s.get(ZuperSettings, 1).enabled is False
    for item in c.get("/api/zuper/status").json()["setup"]["results"]:
        if item["state"] == setup.BY_HAND:
            assert c.put("/api/zuper/setup/confirmations/%s" % item["key"],
                         json={"confirmed": True}).status_code == 200
    r = c.put("/api/zuper/settings", json={"enabled": True})
    assert r.status_code == 200 and r.json()["sync"] == "on"
    # Un-ticking a safety item pauses the sync at once.
    c.put("/api/zuper/setup/confirmations/job_notifications", json={"confirmed": False})
    with SessionLocal() as s:
        assert s.get(ZuperSettings, 1).enabled is False


def test_a_technician_or_dispatcher_cannot_touch_settings_and_nothing_changes(armed, fake):
    for who in ("dana", "tess"):
        c = armed.client(who)
        assert c.get("/api/zuper/status").status_code == 403
        assert c.put("/api/zuper/settings", json={"enabled": False}).status_code == 403
        assert c.put("/api/zuper/settings",
                     json={"workiz_cutover_date": "2026-01-01"}).status_code == 403
    with SessionLocal() as s:
        row = s.get(ZuperSettings, 1)
        assert row.enabled is True and row.workiz_cutover_date is None


# ------------------------------------------------------------------ the initial load (v2)

def test_load_dry_run_writes_nothing_and_counts_the_day_one_selection(armed, fake):
    before = crm_fingerprint()
    writes = len(fake.writes())
    code, report = load.run(SessionLocal(), commit=False)
    assert code == 0, report
    assert crm_fingerprint() == before
    assert len(fake.writes()) == writes
    sel = report["selection"]
    assert sel["groups"] == {
        "AHS (all)": 2, "Retail / Scheduled (open)": 1,
        "Retail / Inspection / Estimate (open)": 0, "Retail / Estimate Sent (open)": 0,
        "Retail / Invoice (open)": 0, "Retail / Invoice (won)": 1}
    assert (sel["selected"], sel["to_send"], sel["skipped"]) == (4, 3, 1)
    assert sel["skipped_ids"] == [armed.ids["noaddr_card"]]
    assert sel["skip_reasons"] == {"no job address": 1}
    # Customers are only the customers of the cards that will be sent.
    assert report["phases"]["contact"]["crm"] == 3
    assert report["phases"]["opportunity"] == {"crm": 3, "already_linked": 0,
                                               "link_existing": 0, "create": 3}
    assert report["phases"]["appointment"]["crm"] == 2
    text_report = load.render(report)
    assert "Retail / Invoice (won): 1" in text_report
    assert "Jane" not in json.dumps(report) and "555" not in json.dumps(report)


def test_new_lead_and_follow_up_and_leads_never_reach_zuper(loaded, fake):
    ids = loaded.ids
    sent = {mapping.custom_values(j)["CRM Opportunity ID"] for j in fake.jobs.values()}
    assert sent == {str(ids["jane_card"]), str(ids["bob_card"]), str(ids["al_card"])}
    customers = {mapping.custom_values(c)["CRM Contact ID"] for c in fake.customers.values()}
    assert customers == {str(ids["jane"]), str(ids["bob"]), str(ids["al"])}
    for lead in ("leo", "tim", "ann"):
        with SessionLocal() as s:
            assert mapping_row(s, "contact", ids[lead]) is None


def test_load_refuses_when_a_named_retail_stage_is_missing(armed, fake):
    with SessionLocal() as s:
        s.get(Stage, armed.ids["stages"]["retail:Scheduled"]).name = "Booked"
        s.commit()
    for commit in (False, True):
        code, report = load.run(SessionLocal(), commit=commit)
        assert code == 3
        assert "no stage named “Scheduled”" in report["refused"]
    assert fake.jobs == {} and fake.customers == {}


def test_load_commit_creates_customers_jobs_visits_notes_tasks_with_mappings(loaded, fake):
    ids = loaded.ids
    assert len(fake.customers) == 3
    assert len(fake.jobs) == 3
    assert len(fake.appointments) == 2
    assert sum(len(v) for v in fake.notes.values()) == 1
    assert sum(len(v) for v in fake.tasks.values()) == 1
    jane = fake.customers[uid_of("contact", ids["jane"])]
    assert mapping.custom_values(jane)["CRM Contact ID"] == str(ids["jane"])
    assert mapping.custom_values(jane)["CRM Link"].endswith("/contacts?contact=%d" % ids["jane"])
    assert jane["customer_contact_no"]["mobile"] == "+19415550101"
    assert jane["source_uid"] == "src-6"                    # CRM "FB" -> "Facebook"
    assert "Lead" not in jane["customer_tags"]
    bob = fake.customers[uid_of("contact", ids["bob"])]
    assert bob["source_uid"] == "src-2"                     # "Existig Customer"
    assert bob["customer_email"] == ""                      # no fake email
    job = fake.jobs[uid_of("opportunity", ids["jane_card"])]
    values = mapping.custom_values(job)
    assert values["CRM Opportunity ID"] == str(ids["jane_card"])
    assert values["AHS Job ID"] == "AHS-555"
    assert values["How many leaks?"] == "2"
    assert values["How old is the roof?"] == "10-15 yrs"     # en dash -> Zuper's hyphen
    assert values["Email verified"] == "Yes"
    assert values["Any previous repair? - details"] == "2019 patch"
    assert job["customer"]["customer_uid"] == uid_of("contact", ids["jane"])
    assert job["assigned_to"] == [{"user_uid": "u-owner"}]     # office staff with a Zuper user
    assert job["current_job_status"]["status_uid"] == uid_of(
        "stage", ids["stages"]["ahs:Inspection"])
    bob_job = fake.jobs[uid_of("opportunity", ids["bob_card"])]
    assert mapping.custom_values(bob_job)["Workiz Job #"] == "W100"
    assert mapping.custom_values(bob_job)["Technicians"] == "Antonio"
    assert bob_job["assigned_to"] == []                      # no owner: unassigned
    note = next(iter(fake.notes.values()))[0]
    assert note["note"] == "Gate code 1234" and note["is_private"] is True


def test_the_load_starts_the_sweep_cursor_so_nothing_edited_before_go_live_is_missed(loaded):
    from app.zuper import engine
    with SessionLocal() as s:
        state = s.get(engine.ZuperSyncState, 1)
        started = mapping.parse_time(state.load_report["started_at"])
        assert mapping.aware(state.sweep_cursor) == started


def test_a_second_commit_changes_nothing(loaded, fake):
    writes = len(fake.writes())
    code, report = load.run(SessionLocal(), commit=True)
    assert code == 0, report
    assert len(fake.writes()) == writes
    assert report["phases"]["contact"]["created"] == 0
    assert report["phases"]["contact"]["already_linked"] == 3
    assert report["verification"]["mismatches"] == 0
    assert report["verification"]["contact"] == {
        "crm": 3, "mapped": 3, "zuper": 3, "crm_not_mapped": [], "mapped_not_in_zuper": [],
        "zuper_not_mapped": []}


def test_resume_after_a_crash_mid_page_does_not_duplicate(armed, fake, monkeypatch):
    """Zuper created the customer, then the process died before our commit."""
    from app.zuper import zapi
    real = client.request
    state = {"creates": 0}

    def crash_after_second_create(method, path, **kw):
        out = real(method, path, **kw)
        if method == "POST" and path == "/customers_new":
            state["creates"] += 1
            if state["creates"] == 2:
                raise KeyboardInterrupt("power cut")
        return out

    monkeypatch.setattr(zapi, "request", crash_after_second_create)
    with pytest.raises(KeyboardInterrupt):
        load.run(SessionLocal(), commit=True)
    monkeypatch.setattr(zapi, "request", real)
    assert len(fake.customers) == 2
    with SessionLocal() as s:
        states = sorted(s.scalars(select(ZuperMapping.state).where(
            ZuperMapping.crm_type == "contact")).all())
        assert states == ["creating", "linked"]
    code, report = load.run(SessionLocal(), commit=True)
    assert code == 0, report
    assert len(fake.customers) == 3                  # not 4
    crm_ids = [mapping.custom_values(c)["CRM Contact ID"] for c in fake.customers.values()]
    assert len(crm_ids) == len(set(crm_ids))
    assert report["phases"]["contact"]["linked_existing"] == 1


def test_an_outage_stops_the_load_with_a_sentence_and_the_rerun_finishes(armed, fake):
    fake.fail_next["POST ^/jobs$"] = 1               # a 503 on a create (writes never retry)
    code, report = load.run(SessionLocal(), commit=True)
    assert code == 2
    assert "Run the same command again to resume" in report["stopped"]
    code, report = load.run(SessionLocal(), commit=True)
    assert code == 0, report
    assert len(fake.jobs) == 3


def test_load_refuses_when_setup_fails_and_writes_nothing_to_zuper(zworld, fake):
    fake.me = {"user_uid": "x", "first_name": "Someone", "last_name": "Else",
               "role": "Field Executive"}
    code, report = load.run(SessionLocal(), commit=True)
    assert code == 3 and "setup check failed" in report["refused"]
    assert fake.customers == {} and fake.jobs == {} and fake.categories == {}


def test_load_command_without_a_key_refuses(zworld, monkeypatch):
    monkeypatch.delenv("ZUPER_API_KEY", raising=False)
    code, report = load.run(SessionLocal(), commit=True)
    assert code == 2 and report["refused"] == config.NO_KEY_SENTENCE


def test_the_verification_report_lists_mismatches_by_id(loaded, fake):
    ids = loaded.ids
    fake.customers.pop(uid_of("contact", ids["al"]))     # gone from Zuper behind our back
    stray = fake.new_customer(customer_first_name="X",
                              custom_fields=[{"label": "CRM Contact ID", "value": "99999"}])
    with SessionLocal() as s, client.operator_mode():
        eligible, _ = load.selected(s)
        report = load.verify(s, eligible, deep=True)
    assert report["contact"]["mapped_not_in_zuper"] == [ids["al"]]
    assert report["contact"]["zuper_not_mapped"] == [stray]
    assert report["mismatches"] == 2


def test_no_contact_or_card_is_invented_by_the_load(loaded, fake):
    with SessionLocal() as s:
        assert s.scalar(select(func.count(Contact.id))) == 6
        assert s.scalar(select(func.count(Opportunity.id))) == 8


def test_zuper_error_kinds_are_plain_sentences():
    assert "API key" in client.sentence(ZuperError("unauthorized"))
    assert client.sentence(ZuperError("rejected", "field x is bad")) == \
        "Zuper rejected the request. field x is bad"


def test_a_person_admin_key_is_accepted_and_never_treated_as_the_sync_itself(zworld, fake):
    """No seat for a dedicated user: the owner's own Admin key. Its user id must not be
    recorded as the sync's, or that person's real edits in Zuper would be dropped."""
    with SessionLocal() as s:
        config.settings(s).sync_user_uid = "u-stale"
        s.commit()
    fake.me = {"user_uid": "u-owner", "first_name": "Owen", "last_name": "Owner",
               "role": "Admin"}
    report = run_setup(commit=True)
    by_key = {r["key"]: r for r in report["checks"]}
    assert by_key["sync_user"]["state"] == setup.PASS
    with SessionLocal() as s:
        assert s.get(ZuperSettings, 1).sync_user_uid is None


def test_a_non_admin_key_still_fails_with_a_sentence(zworld, fake):
    fake.me = {"user_uid": "u-fe", "first_name": "Field", "last_name": "Tech",
               "role": "Field Executive"}
    report = run_setup(commit=True)
    by_key = {r["key"]: r for r in report["checks"]}
    assert by_key["sync_user"]["state"] == setup.FAIL
    assert "must be Admin" in by_key["sync_user"]["sentences"][0]


def test_a_refused_first_create_stops_the_load_with_zuper_s_message(armed, fake, monkeypatch):
    """The create bodies are UNVERIFIED. If Zuper refuses the very first customer, the load
    stops there with Zuper's own sentence instead of trying (and losing the reason for)
    every other record; fixing the body and running again finishes without duplicates."""
    real = fake.create_customer
    monkeypatch.setattr(fake, "create_customer", lambda params, body: fake.error(
        "customer_contact_no.mobile must be a valid phone number"))
    code, report = load.run(SessionLocal(), commit=True)
    assert code == 2
    assert "customer_contact_no.mobile must be a valid phone number" in report["stopped"]
    assert "The first contact sent" in report["stopped"]
    assert "Run the same command again to resume" in report["stopped"]
    assert "STOPPED: " in load.render(report)
    assert len(fake.calls("POST", r"^/customers_new$")) == 1       # not one per contact
    assert fake.customers == {} and fake.jobs == {}
    with SessionLocal() as s:
        assert s.scalar(select(func.count(ZuperMapping.id)).where(
            ZuperMapping.crm_type == "contact", ZuperMapping.state == "linked")) == 0
    monkeypatch.setattr(fake, "create_customer", real)
    code, report = load.run(SessionLocal(), commit=True)
    assert code == 0, report
    assert len(fake.customers) == 3 and len(fake.jobs) == 3


def test_a_refusal_after_the_first_success_is_counted_with_its_reason(armed, fake, monkeypatch):
    real = fake.create_customer
    state = {"n": 0}

    def second_refused(params, body):
        state["n"] += 1
        if state["n"] == 2:
            return fake.error("customer_email is not a valid email")
        return real(params, body)

    monkeypatch.setattr(fake, "create_customer", second_refused)
    code, report = load.run(SessionLocal(), commit=True)
    contacts = report["phases"]["contact"]
    assert "stopped" not in report
    assert (contacts["created"], contacts["failed"]) == (2, 1)
    assert len(contacts["failed_ids"]) == 1
    assert contacts["failed_reasons"] == {
        "Zuper rejected the request. customer_email is not a valid email": 1}
    text_report = load.render(report)
    assert "failed 1: Zuper rejected the request. customer_email is not a valid email" \
        in text_report
    assert "failed_reasons" not in text_report
    # The refused customer is tried again when its card is sent, and made exactly once.
    crm_ids = [mapping.custom_values(c)["CRM Contact ID"] for c in fake.customers.values()]
    assert len(crm_ids) == 3 == len(set(crm_ids))


# ------------------------------------------------------------------ unverified create bodies

def test_setup_commit_uses_the_category_body_zuper_accepts(zworld, fake):
    """Live Zuper refused {"job_category": {...}} with "Category Name Missing" (2026-09-17):
    the flat body goes first, one POST per category, and the report names the shape."""
    report = run_setup(commit=True)
    assert {c["category_name"] for c in fake.categories.values()} == set(CATEGORY_NAMES)
    assert len(fake.calls("POST", r"^/jobs/category$")) == 2
    assert report["categories"]["accepted_shapes"]["category"] == "flat"
    assert "Zuper accepted these create bodies: category = flat" in setup.render(report)


def test_a_refused_body_shape_falls_through_to_the_next_without_duplicates(zworld, fake,
                                                                          monkeypatch):
    real = fake.create_category

    def wrapped_only(params, body):
        if "category" not in body:
            return fake.error("Unexpected field category_name")
        return real(params, body["category"])

    monkeypatch.setattr(fake, "create_category", wrapped_only)
    report = run_setup(commit=True)
    assert sorted(c["category_name"] for c in fake.categories.values()) == CATEGORY_NAMES
    # AHS: flat refused, then wrapped; Retail goes straight to the shape Zuper accepted.
    assert len(fake.calls("POST", r"^/jobs/category$")) == 3
    assert report["categories"]["accepted_shapes"]["category"] == "category"
    assert report["passed"]


def test_every_shape_refused_stops_with_each_message_and_creates_nothing(zworld, fake,
                                                                         monkeypatch):
    monkeypatch.setattr(fake, "create_category",
                        lambda params, body: fake.error("Category Name Missing"))
    assert setup.main(["--commit"]) == 2
    assert fake.categories == {}
    with SessionLocal() as s:
        assert s.scalar(select(func.count(ZuperMapping.id))) == 0
    with pytest.raises(ZuperError) as caught, SessionLocal() as s, client.operator_mode():
        setup.ensure_categories(s, commit=True)
    assert "refused every category body tried" in caught.value.detail
    assert caught.value.detail.count("Category Name Missing") == 3


def test_an_unreadable_answer_to_a_create_is_never_retried_with_another_shape(zworld, fake,
                                                                            monkeypatch):
    """The create may have gone through: trying another body could make a second category."""
    real = fake.create_category

    def made_but_unreadable(params, body):
        real(params, body)
        return fake.ok(message="created")                   # no category_uid anywhere

    monkeypatch.setattr(fake, "create_category", made_but_unreadable)
    with pytest.raises(ZuperError) as caught, SessionLocal() as s, client.operator_mode():
        setup.ensure_categories(s, commit=True)
    assert caught.value.kind == "bad_response"
    assert len(fake.calls("POST", r"^/jobs/category$")) == 1
    assert len(fake.categories) == 1
    # The rerun links the category Zuper already has by name: still one "AHS".
    monkeypatch.setattr(fake, "create_category", real)
    run_setup(commit=True)
    assert sorted(c["category_name"] for c in fake.categories.values()) == CATEGORY_NAMES


def test_when_zuper_does_not_list_statuses_a_rerun_never_creates_them_twice(zworld, fake,
                                                                            monkeypatch):
    """The first live read found the status list empty/unreadable: the CRM's own mapping of
    each status it created is trusted, the check passes on it and says so."""
    monkeypatch.setattr(fake, "routes", lambda real=fake.routes: [
        (rx, fn) for rx, fn in real()
        if not (rx in (r"/jobs/status/([^/]+)", r"/jobs/status_new/([^/]+)")
                and fn[0] == "GET")])
    report = run_setup(commit=True)
    created = len(fake.calls("POST", r"^/jobs/status_new/"))
    assert created == 11
    assert all(c["statuses_listed"] is False for c in report["categories"]["categories"])
    report = run_setup(commit=True)
    assert len(fake.calls("POST", r"^/jobs/status_new/")) == created      # none again
    assert len(fake.calls("POST", r"^/jobs/category$")) == 2
    item = next(c for c in report["checks"] if c["key"] == "categories")
    assert item["state"] == setup.PASS
    assert "does not list the statuses" in " ".join(item["sentences"])


def test_a_status_create_that_fails_midway_keeps_what_was_made(zworld, fake, monkeypatch):
    real = fake.create_status
    state = {"n": 0}

    def third_is_down(params, body, cat):
        state["n"] += 1
        if state["n"] == 3:
            return httpx.Response(503, json={"type": "error", "message": "down"})
        return real(params, body, cat)

    monkeypatch.setattr(fake, "create_status", third_is_down)
    with pytest.raises(ZuperError), SessionLocal() as s, client.operator_mode():
        setup.ensure_categories(s, commit=True)
    with SessionLocal() as s:
        assert s.scalar(select(func.count(ZuperMapping.id)).where(
            ZuperMapping.crm_type == "stage", ZuperMapping.state == "linked")) == 2
        # The third was sent into an outage: its outcome is unknown, its checkpoint stays.
        assert s.scalar(select(func.count(ZuperMapping.id)).where(
            ZuperMapping.crm_type == "stage", ZuperMapping.state == "creating")) == 1
        assert s.scalar(select(func.count(ZuperMapping.id)).where(
            ZuperMapping.crm_type == "pipeline")) == 1
    monkeypatch.setattr(fake, "create_status", real)
    run_setup(commit=True)
    names = [x["status_name"] for sts in fake.statuses.values() for x in sts]
    assert len(names) == 11 and len(fake.categories) == 2
    for sts in fake.statuses.values():                        # no status made twice
        assert len({x["status_name"] for x in sts}) == len(sts)


def test_verification_says_what_zuper_would_not_read_back_instead_of_stopping(loaded, fake,
                                                                             monkeypatch):
    monkeypatch.setattr(fake, "routes", lambda real=fake.routes: [
        (rx, (m, (lambda *a: fake.error("service tasks module disabled"))
              if rx == r"/jobs/([^/]+)/service_tasks" and m == "GET" else f))
        for rx, (m, f) in real()])
    code, report = load.run(SessionLocal(), commit=True)
    assert "stopped" not in report, report
    task = report["verification"]["task"]
    assert "service tasks module disabled" in task["zuper_unreadable"]
    assert "mapped_not_in_zuper" not in task
    assert "Zuper could not be read back" in load.render(report)


def test_the_status_move_and_child_bodies_fall_back_and_remember_the_accepted_shape(
        armed, fake, monkeypatch):
    """Zuper wants the wrapped status move here: the first job tries flat (refused) then
    wrapped; every later job goes straight to the wrapped shape. Nothing is made twice."""
    from app.zuper import zapi
    zapi.ACCEPTED_SHAPES.pop("job_status", None)
    real_move = fake.move

    def wrapped_only(uid, body, *, back):
        if "job_status" not in body:
            return fake.error("status_uid is required")
        return real_move(uid, {"status_uid": body["job_status"]["status_uid"]}, back=back)

    monkeypatch.setattr(fake, "move", wrapped_only)
    code, report = load.run(SessionLocal(), commit=True)
    assert code == 0, report
    moves = fake.calls("PUT", r"^/jobs/[^/]+/status$")
    assert len(moves) == len(fake.jobs) + 1                   # one refused, then one per job
    assert report["accepted_shapes"]["job_status"] == "job_status"
    for job in fake.jobs.values():
        assert job.get("current_job_status", {}).get("status_uid")
    assert len(fake.appointments) == 2 and sum(len(v) for v in fake.notes.values()) == 1
    assert "Zuper accepted these request bodies" in load.render(report)


def test_a_status_create_answered_without_a_uid_is_found_by_name_not_made_twice(
        zworld, fake, monkeypatch):
    """Live, 2026-09-17: the status create answered success with no status_uid."""
    real = fake.create_status

    def no_uid(params, body, cat):
        real(params, body, cat)
        return fake.ok(message="Status created")

    monkeypatch.setattr(fake, "create_status", no_uid)
    report = run_setup(commit=True)
    assert report["passed"], report["checks"]
    posts = len(fake.calls("POST", r"^/jobs/status_new/"))
    assert posts == 11
    with SessionLocal() as s:
        for key, stage_id in zworld.ids["stages"].items():
            if key.startswith("other:"):
                continue
            m = mapping_row(s, "stage", stage_id)
            status = next(x for x in fake.statuses[m.parent_uid] if x["status_uid"] == m.zuper_uid)
            assert status["status_name"] == s.get(Stage, stage_id).name
    run_setup(commit=True)
    assert len(fake.calls("POST", r"^/jobs/status_new/")) == posts
    text = setup.render(report)
    assert "Zuper lists" in text


def test_statuses_zuper_lists_without_readable_names_stop_the_commit_before_creating(
        zworld, fake, monkeypatch):
    run_setup(commit=True)                            # categories and statuses exist
    with SessionLocal() as s:
        s.execute(text("DELETE FROM zuper_mappings WHERE crm_type = 'stage'"))
        s.commit()
    for sts in fake.statuses.values():
        for x in sts:
            x["label"] = x.pop("status_name")
    posts = len(fake.calls("POST", r"^/jobs/status_new/"))
    with pytest.raises(ZuperError) as caught, SessionLocal() as s, client.operator_mode():
        setup.ensure_categories(s, commit=True)
    assert "cannot read" in caught.value.detail and "label" in caught.value.detail
    assert len(fake.calls("POST", r"^/jobs/status_new/")) == posts


def test_a_create_answer_without_a_uid_names_its_fields_but_no_values():
    with pytest.raises(ZuperError) as caught:
        client.uid_of({"type": "success", "message": "Jane Roof created",
                       "data": {"customer_name": "Jane", "items": [{"x": 1}]}}, "customer_uid")
    detail = caught.value.detail
    assert "customer_name" in detail and "items[1 of {x}]" in detail
    assert "Jane" not in detail
    assert client.uid_of({"data": {"customer": {"customer_uid": "c-1"}}}, "customer_uid") == "c-1"
    assert client.uid_of({"data": {"job_status_uid": "s-1"}}, "status_uid",
                         "job_status_uid") == "s-1"


# ------------------------------------------------ live 2026-09-17: statuses unlisted

def stage_states() -> dict[str, int]:
    with SessionLocal() as s:
        rows = s.scalars(select(ZuperMapping.state).where(ZuperMapping.crm_type == "stage")).all()
    return {st: rows.count(st) for st in set(rows)}


def test_statuses_only_listed_at_status_new_are_found_there(zworld, fake):
    fake.statuses_listed_at = {"status_new"}
    fake.status_create_echoes_uid = False
    report = run_setup(commit=True)
    assert report["passed"], report["checks"]
    assert stage_states() == {"linked": 11}
    assert len(fake.calls("POST", r"^/jobs/status_new/")) == 11
    run_setup(commit=True)
    assert len(fake.calls("POST", r"^/jobs/status_new/")) == 11
    ahs = report["categories"]["categories"][0]
    assert any(n.startswith("GET /jobs/status_new/") for n in ahs["status_sources"])


def test_empty_lists_and_uids_echoed_a_rerun_trusts_the_crm_s_mapping(zworld, fake):
    fake.statuses_listed_at = set()                  # both lists answer, always empty
    run_setup(commit=True)
    assert stage_states() == {"linked": 11}
    report = run_setup(commit=True)
    assert len(fake.calls("POST", r"^/jobs/status_new/")) == 11     # none twice
    item = next(c for c in report["checks"] if c["key"] == "categories")
    assert item["state"] == setup.PASS
    assert "does not list the statuses" in " ".join(item["sentences"])
    assert any(r["action"] == "mapped (not listed by Zuper)"
               for c in report["categories"]["categories"] for r in c["statuses"])


def test_empty_lists_and_no_uid_stop_once_and_never_send_that_status_again(zworld, fake):
    fake.statuses_listed_at = set()
    fake.status_list_readable = False            # no list Zuper's absence can be read from
    fake.status_create_echoes_uid = False
    assert setup.main(["--commit"]) == 2
    assert len(fake.calls("POST", r"^/jobs/status_new/")) == 1
    assert stage_states() == {"creating": 1}
    assert setup.main(["--commit"]) == 2
    assert len(fake.calls("POST", r"^/jobs/status_new/")) == 1        # never re-sent
    with pytest.raises(ZuperError) as caught, SessionLocal() as s, client.operator_mode():
        setup.ensure_categories(s, commit=True)
    assert "never said what it made" in caught.value.detail
    assert "Settings → Jobs → Categories" in caught.value.detail
    dry = run_setup(commit=False)
    assert dry["categories"]["categories"][0]["statuses"][0]["action"] == "unresolved"


def test_a_refused_status_create_leaves_no_checkpoint(zworld, fake, monkeypatch):
    monkeypatch.setattr(fake, "create_status",
                        lambda params, body, cat, **kw: fake.error("status_type invalid"))
    assert setup.main(["--commit"]) == 2
    assert stage_states() == {}


def test_an_unknown_outcome_is_resent_only_when_zuper_s_list_is_shown_reliable(zworld, fake,
                                                                             monkeypatch):
    """Zuper's list shows the statuses the CRM made, and not the one sent into an outage:
    it was not made, so the rerun sends it — once."""
    real = fake.create_status
    state = {"n": 0}

    def second_is_lost(params, body, cat):
        state["n"] += 1
        if state["n"] == 2:
            return httpx.Response(503, json={"type": "error", "message": "down"})
        return real(params, body, cat)

    monkeypatch.setattr(fake, "create_status", second_is_lost)
    assert setup.main(["--commit"]) == 2
    assert stage_states() == {"linked": 1, "creating": 1}
    monkeypatch.setattr(fake, "create_status", real)
    report = run_setup(commit=True)
    assert report["passed"]
    assert stage_states() == {"linked": 11}
    for sts in fake.statuses.values():
        assert len({x["status_name"] for x in sts}) == len(sts)


# ------------------------------------------------ live 2026-09-17 (4): OK without effect

def live_like(fake, form: str = "job_statuses", *, reliable: bool = True) -> None:
    fake.status_create_echoes_uid = False
    fake.statuses_listed_at = set()
    fake.category_embeds_statuses = True
    fake.template_category_with_statuses = False
    fake.status_list_readable = reliable
    fake.status_list_wrapped = True
    fake.statuses_listed_at = {"status"}
    fake.status_create_form = form


def test_status_creates_try_each_form_verify_in_the_category_record_and_remember(zworld,
                                                                                 fake):
    live_like(fake, "job_statuses")
    report = run_setup(commit=True)
    assert report["passed"], report["checks"]
    assert stage_states() == {"linked": 11}
    names = {c: [x["status_name"] for x in sts] for c, sts in fake.statuses.items()}
    assert all(len(v) == len(set(v)) for v in names.values())            # none twice
    # First status: flat (no effect), job_status (no effect), job_statuses[] (made);
    # the other ten go straight to the form that worked.
    assert len(fake.calls("POST", r"^/jobs/status_new/")) == 3 + 10
    assert report["categories"]["accepted_shapes"]["status"] == "status_new job_statuses[]"
    for body in [c.body for c in fake.calls("POST", r"^/jobs/status_new/")]:
        one = body if isinstance(body, dict) and "status_name" in body else (
            body.get("job_status") or body.get("job_statuses", [None])[0])
        assert one["status_color"]


def test_ok_without_effect_and_an_unreliable_list_stops_at_once(zworld, fake):
    live_like(fake, "plain", reliable=False)
    assert setup.main(["--commit"]) == 2
    assert len(fake.calls("POST", r"^/jobs/status_new/")) == 1
    assert stage_states() == {"creating": 1}
    assert setup.main(["--commit"]) == 2
    assert len(fake.calls("POST", r"^/jobs/status_new/")) == 1        # never re-sent


def test_every_form_without_effect_on_a_reliable_list_reports_each_message(zworld, fake,
                                                                          capsys):
    live_like(fake, "nothing-works")
    assert setup.main(["--commit"]) == 2
    err = capsys.readouterr().err
    assert "No way of creating the status “New Lead” worked" in err
    assert err.count("Job status updated successfully") == 6
    assert stage_states() == {}                                     # nothing made: no checkpoint
    assert all(not sts for sts in fake.statuses.values())


def test_a_leftover_checkpoint_is_resent_once_the_list_is_reliable(zworld, fake):
    live_like(fake, "plain", reliable=False)
    assert setup.main(["--commit"]) == 2
    assert stage_states() == {"creating": 1}
    fake.status_list_readable = True                                 # the list is readable now
    report = run_setup(commit=True)
    assert report["passed"], report["checks"]
    assert stage_states() == {"linked": 11}
    assert len(fake.calls("POST", r"^/jobs/status$")) == 11


# ------------------------------------------------ live 2026-09-17 (5): the real status list

def test_the_wrapped_status_list_is_read_trusted_and_duplicates_reported(zworld, fake):
    """GET /jobs/status/{c} answers {data: {_id, job_statuses: [...]}}. Two earlier OK-only
    creates may have made "New Lead" twice: one is linked, the extra is reported, the
    leftover checkpoint is resolved, nothing more named "New Lead" is made."""
    fake.status_list_wrapped = True
    fake.status_create_echoes_uid = False
    fake.status_create_form = "job_status"
    # Zuper already holds the AHS category with two "New Lead" statuses, and the CRM has the
    # category mapped and a "creating" checkpoint for its first stage (the live state).
    uid = "cat-ahs"
    fake.categories[uid] = {"category_uid": uid, "category_name": AHS_CATEGORY}
    fake.statuses[uid] = [{"status_uid": "st-a", "status_name": "New Lead"},
                          {"status_uid": "st-b", "status_name": "New Lead"}]
    with SessionLocal() as s:
        ahs = zworld.ids["ahs"]
        s.add(ZuperMapping(crm_type="pipeline", crm_id=ahs, zuper_type="category",
                           zuper_uid=uid, state="linked"))
        first = zworld.ids["stages"]["ahs:New Lead"]
        s.add(ZuperMapping(crm_type="stage", crm_id=first, zuper_type="status",
                           parent_uid=uid, state="creating"))
        s.commit()
    report = run_setup(commit=True)
    assert report["passed"], report["checks"]
    ahs_entry = report["categories"]["categories"][0]
    assert ahs_entry["duplicate_statuses"] == ["New Lead"]
    assert "delete the extra in Zuper" in setup.render(report)
    assert [x["status_name"] for x in fake.statuses[uid]].count("New Lead") == 2   # not 3
    assert stage_states() == {"linked": 11}
    assert any("GET /jobs/status/cat-ahs: 2" in n for n in ahs_entry["status_sources"])

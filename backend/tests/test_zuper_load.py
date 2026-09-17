"""The setup command and the initial load, against a fake Zuper at the HTTP boundary.

Behaviour, not status codes: a dry run is proven to write nothing on EITHER side (every
request the fake saw was a GET, and every CRM table hashes the same before and after); a
commit is proven by what the fake holds; idempotency by a second commit that sends no write.
"""
import hashlib
import json

import pytest
from app.db import SessionLocal, engine
from app.models import Contact, Opportunity, Stage, ZuperMapping, ZuperSettings
from app.zuper import client, config, load, mapping, setup
from app.zuper.client import ZuperError
from sqlalchemy import func, inspect, select, text
from tests.zuper_support import AHS, RETAIL, arm, mapping_row, uid_of


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
    assert names == {"AHS", "Retail"}
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
    assert by_key["sync_user"]["state"] == setup.FAIL
    assert "belongs to “Owen Owner”, not “CRM Sync”" in by_key["sync_user"]["sentences"][0]
    checklist = " ".join(by_key["job_fields_checklist"]["sentences"])
    assert "The job field “How many leaks?” is missing" in checklist
    assert "“How old is the roof?” is missing the options “5-10 yrs”" in checklist
    assert "“Technicians” is number, not Single line" in " ".join(
        by_key["job_fields_crm"]["sentences"])
    assert by_key["lead_sources"]["sentences"] == ["Missing lead source: “Yelp”."]
    with SessionLocal() as s:
        assert s.get(ZuperSettings, 1).setup_passed is False
        blockers = setup.blockers(s)
    assert any("CRM Sync" in b for b in blockers)


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
    fake.me = {"user_uid": "x", "first_name": "Someone", "last_name": "Else"}
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

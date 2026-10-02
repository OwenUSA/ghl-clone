"""A signed proposal's option goes onto its job's line items (2026-10-02), against the fake Zuper.

The owner's rule: ONE source — the job's line items — accurate upsells and sales, nothing doubled,
nothing else on the job changed, and nothing written unless the switch is on.
"""
import pytest
from app.db import SessionLocal
from app.models import ZuperProposalLine
from app.zuper import client, proposals
from sqlalchemy import select
from tests.zuper_support import uid_of

PRODUCT = {"product_uid": "p-flat-better", "product_id": "DTR-FLAT-BETTER",
           "product_name": "BETTER - Heavy-Duty Flat Roof Leak Repair", "product_type": "SERVICE",
           "uom": "Job", "product_category": {"category_uid": "cat-dtr"}}
AHS_LINE = {"product_id": "AHS-LEAK-REPAIR", "product_uid": "p-leak", "quantity": 1,
            "price": 1100, "total": 1100}


def signed(fake, job_uid, *, option="BETTER", total=1900, uid="e1", lines=None):
    fake.products[PRODUCT["product_uid"]] = PRODUCT
    fake.estimates[uid] = {
        "estimate_uid": uid, "estimate_no": 31, "estimate_status": "APPROVED", "is_deleted": False,
        "is_proposal": True, "job": {"job_uid": job_uid, "work_order_number": 512},
        "proposal_options": [
            {"option_name": "GOOD - AHS covered", "is_accepted": False, "total": 0,
             "line_items": [{"product_id": "DTR-CUSTOM-GOOD", "total": 0}]},
            {"option_name": option, "is_accepted": True, "total": total,
             "line_items": lines if lines is not None else [
                 {"product_id": "DTR-FLAT-BETTER", "product_uid": "p-flat-better",
                  "name": PRODUCT["product_name"], "quantity": 1, "unit_price": total,
                  "total": total}]}]}


def run(commit=True):
    with SessionLocal() as s:
        return proposals.run(s, commit=commit)


def rows():
    with SessionLocal() as s:
        return s.scalars(select(ZuperProposalLine)).all()


@pytest.fixture()
def on(monkeypatch):
    monkeypatch.setenv("ZUPER_PROPOSAL_LINES", "true")


def test_the_signed_option_is_added_to_its_job_once_and_nothing_else_changes(loaded, fake, on):
    uid = uid_of("opportunity", loaded.ids["jane_card"])
    fake.edit_job(uid, products=[dict(AHS_LINE)], job_total=1100)
    before = {k: v for k, v in fake.jobs[uid].items() if k not in ("products", "job_total",
                                                                   "updated_at", "updated_by")}
    signed(fake, uid)
    assert run() == {"added": 1}
    job = fake.jobs[uid]
    assert [(p["product_id"], p["total"]) for p in job["products"]] == [
        ("AHS-LEAK-REPAIR", 1100), ("DTR-FLAT-BETTER", 1900)]
    assert job["job_total"] == 3000
    assert {k: v for k, v in job.items() if k in before} == before
    writes = [w for w in fake.writes() if w.method == "PUT" and w.path == "/jobs"]
    assert len(writes) == 1 and set(writes[0].body["job"]) <= {"job_uid", "products", "job_total"}
    # Seen again on the next sweep: recorded once, never added twice.
    assert run() == {}
    assert len(fake.jobs[uid]["products"]) == 2 and len(rows()) == 1


def test_a_zero_option_adds_nothing(loaded, fake, on):
    uid = uid_of("opportunity", loaded.ids["jane_card"])
    signed(fake, uid, option="GOOD - AHS covered", total=0,
           lines=[{"product_id": "DTR-CUSTOM-GOOD", "total": 0}])
    assert run() == {"zero_option": 1}
    assert not fake.jobs[uid].get("products")


def test_an_upgrade_a_person_already_added_is_never_doubled(loaded, fake, on):
    uid = uid_of("opportunity", loaded.ids["jane_card"])
    fake.edit_job(uid, products=[dict(AHS_LINE), {"product_id": "UPG-BETTER", "total": 1900}],
                  job_total=3000)
    signed(fake, uid)
    assert run() == {"job_has_other_upgrade": 1}
    assert len(fake.jobs[uid]["products"]) == 2
    assert rows()[0].outcome == "job_has_other_upgrade"


def test_a_job_value_that_did_not_match_its_lines_is_left_alone(loaded, fake, on):
    uid = uid_of("opportunity", loaded.ids["jane_card"])
    fake.edit_job(uid, products=[], job_total=2600)          # a Workiz total with no lines
    signed(fake, uid)
    assert run() == {"added": 1}
    assert fake.jobs[uid]["job_total"] == 2600
    assert "did not equal" in rows()[0].detail["note"]


def test_switched_off_it_writes_nothing(loaded, fake):
    uid = uid_of("opportunity", loaded.ids["jane_card"])
    signed(fake, uid)
    before = len(fake.writes())
    assert run() == {"off": 1}
    assert fake.writes()[before:] == [] and not rows()


def test_the_scope_allows_only_the_job_lines_write(loaded, fake, on):
    uid = uid_of("opportunity", loaded.ids["jane_card"])
    with client.proposal_lines():
        with pytest.raises(client.ZuperError):
            client.request("PUT", "/jobs", body={"job": {"job_uid": uid, "job_title": "x"}})
        with pytest.raises(client.ZuperError):
            client.request("PUT", "/jobs/%s/status" % uid, body={"status_uid": "s"})
    assert fake.jobs[uid].get("job_title") != "x"

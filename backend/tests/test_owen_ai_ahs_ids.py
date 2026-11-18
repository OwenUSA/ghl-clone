"""Which AHS dispatch is a Zuper job (app/owen_ai/ahs_ids.py, 2026-11-18).

Job #768's "AHS Job ID" field was empty, so its proposal was priced without the Frontdoor figure
and went out $1,700 too low; the dispatch number was on the customer's AHS email card in the CRM.
"""
from app.models import Contact, Opportunity
from app.owen_ai import ahs_ids, report


def _job(field=None, title="Latanja Sherard - Leak Repair", phone="(504) 555-0101",
         first="LATANJA", last="SHERARD"):
    fields = [{"label": "Roof material", "value": "Shingle"}]
    if field is not None:
        fields.append({"label": "AHS Job ID", "value": field})
    return {"job_uid": "j-768", "work_order_number": 768, "job_title": title,
            "custom_fields": fields,
            "customer": {"customer_first_name": first, "customer_last_name": last,
                         "customer_contact_no": {"mobile": phone, "home": None}}}


def _card(db, pipeline, ahs_id, first="Latanja", last="Sherard", phone=None):
    p, a, _ = pipeline
    c = Contact(first_name=first, last_name=last, phone=phone)
    db.add(c)
    db.flush()
    o = Opportunity(title="%s %s - %s ROOF" % (first, last, ahs_id), contact_id=c.id,
                    pipeline_id=p.id, stage_id=a.id, custom_fields={"ahs_job_id": ahs_id})
    db.add(o)
    db.flush()
    return o


def test_the_jobs_own_field_wins_and_needs_no_lookup(db, pipeline):
    _card(db, pipeline, "11111111")
    rec = _job(field="98180079")
    assert ahs_ids.single(rec, db) == "98180079"
    assert ahs_ids.candidates(rec, db)[0] == {"dispatch": "98180079",
                                              "source": "the job's AHS Job ID field"}


def test_an_empty_field_finds_the_customers_ahs_email_card_by_name(db, pipeline):
    _card(db, pipeline, "98180079")
    _card(db, pipeline, "22222222", first="Someone", last="Else")
    rec = _job()
    assert ahs_ids.field_id(rec) is None
    assert ahs_ids.single(rec, db) == "98180079"
    (only,) = ahs_ids.candidates(rec, db)
    assert "email card" in only["source"] and "name" in only["source"]


def test_by_phone_when_the_name_is_spelled_differently(db, pipeline):
    _card(db, pipeline, "98180079", first="Latonja", last="Sherrard", phone="504-555-0101")
    assert ahs_ids.single(_job(), db) == "98180079"


def test_a_number_in_the_job_title_counts(db, pipeline):
    assert ahs_ids.single(_job(title="Jane Roe - 87773369 ROOF"), db) == "87773369"


def test_two_dispatches_are_never_picked_silently(db, pipeline):
    _card(db, pipeline, "87269539")
    _card(db, pipeline, "102017789")
    rec = _job()
    assert ahs_ids.single(rec, db) is None
    assert {c["dispatch"] for c in ahs_ids.candidates(rec, db)} == {"87269539", "102017789"}


def test_nothing_found_is_none_not_a_guess(db, pipeline):
    _card(db, pipeline, "33333333", first="Other", last="Person", phone="(305) 555-0199")
    assert ahs_ids.candidates(_job(), db) == []
    assert ahs_ids.single(_job(), db) is None


def test_the_report_reads_frontdoor_by_the_found_number(db, pipeline, monkeypatch):
    _card(db, pipeline, "98180079")
    db.commit()
    asked = []
    monkeypatch.setattr(report, "frontdoor_refusal", lambda: None)
    monkeypatch.setattr(report.frontdoor, "authorization_for",
                        lambda i: asked.append(i) or {"total": 3200.0, "customer_owes": 2075.0})
    got = report.ahs_authorization(_job(), [])
    assert asked == ["98180079"] and got["customer_owes"] == 2075.0


def test_owen_ai_has_the_tool():
    from app.owen_ai import tools
    assert "find_ahs_dispatch" in {name for name, *_ in tools.SPECS}

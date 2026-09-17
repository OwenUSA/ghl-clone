"""Shared fixtures for tests/test_zuper_*.py: a seeded CRM, a fake Zuper, an armed sync.

Nothing here can reach Zuper: `FakeZuper` answers at the HTTP boundary, and
tests/zuper_guard.py refuses every zuperpro.com lookup besides.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from app import checklist_seed
from app.auth import mint_api_token
from app.db import SessionLocal
from app.main import app
from app.models import (
    Appointment,
    Calendar,
    Contact,
    Job,
    Opportunity,
    OpportunityNote,
    OpportunityTask,
    Pipeline,
    Role,
    Stage,
    User,
    ZuperMapping,
)
from app.zuper import client, config, engine, setup
from fastapi.testclient import TestClient
from tests.zuper_fake import FakeZuper

AHS = "Dream Team Roofing AHS"
RETAIL = "Retail"


@pytest.fixture()
def zuper_env(monkeypatch):
    monkeypatch.setenv("ZUPER_SYNC_ENABLED", "true")
    monkeypatch.setenv("ZUPER_API_KEY", "zk_test")
    monkeypatch.setenv("ZUPER_WEBHOOK_TOKEN", "whk_test")
    monkeypatch.setattr(client, "_sleep", lambda seconds: None)


@pytest.fixture()
def fake(zuper_env, monkeypatch):
    from app.zuper import api as zuper_api
    from app.zuper import listener
    # Queued pushes are due at once, so a test can drain them straight after the save.
    monkeypatch.setattr(listener, "COALESCE_SECONDS", -1)
    from app.zuper import zapi
    zuper_api.clear_cache()
    zapi.ACCEPTED_SHAPES.clear()            # per process in production; per test here
    f = FakeZuper().configure_account().install()
    yield f
    # Package-wide: whatever a test did, no denylisted endpoint reached even the fake.
    assert f.violations == [], f.violations


def drain() -> int:
    """Run every queued Zuper job, as the worker's Zuper thread would."""
    from app.zuper import worker
    total = 0
    for _ in range(10):
        with SessionLocal() as s:
            n = worker.drain(s, limit=100)
        total += n
        if n == 0:
            break
    return total


@dataclass
class World:
    ids: dict
    tokens: dict

    def client(self, who: str | None = "owner") -> TestClient:
        c = TestClient(app)
        if who:
            c.headers["Authorization"] = "Bearer " + self.tokens[who]
        return c


def seed(db) -> World:
    """A CRM shaped like production for the v2 rules (2026-09-16).

    Day-one selection: AHS = jane_card and noaddr_card (every AHS card); Retail = bob_card
    (Scheduled, open, a Workiz job) and al_card (Invoice, won). Not selected: tim_card (New
    Lead), follow_card (Follow Up), lost_estimate (Estimate Sent but lost), other_card (another
    pipeline). noaddr_card has no job address, so the load skips it and lists its id.
    """
    owner = User(email="owner@example.test", name="Owen Owner", role=Role.ADMIN)
    dana = User(email="dana@example.test", name="Dana Dispatch", role=Role.DISPATCHER)
    tess = User(email="tess@example.test", name="Tess Tech", role=Role.TECH,
                only_assigned_data=True)
    terry = User(email="terry@example.test", name="Terry Tech", role=Role.TECH)
    db.add_all([owner, dana, tess, terry])
    db.flush()
    ahs = Pipeline(name=AHS, position=0)
    retail = Pipeline(name=RETAIL, position=1)
    other = Pipeline(name="Commercial", position=2)
    db.add_all([ahs, retail, other])
    db.flush()
    stages = {}
    for i, name in enumerate(["New Lead", "Inspection", "Request the Approval (AHS)",
                              "Submit Invoices", "Call Back"]):
        stages["ahs:" + name] = Stage(pipeline_id=ahs.id, name=name, position=i)
    # Production's Retail stages all have position 0: id order decides.
    for name in ["New Lead", "Inspection / Estimate", "Estimate Sent", "Follow Up",
                 "Scheduled", "Invoice"]:
        stages["retail:" + name] = Stage(pipeline_id=retail.id, name=name, position=0)
    stages["other:New"] = Stage(pipeline_id=other.id, name="New", position=0)
    for s in stages.values():
        db.add(s)
        db.flush()
    checklist_seed.run(db, commit=True)
    cal = Calendar(name="Workiz Jobs (imported)")
    tess_cal = Calendar(name="Tess's calendar", user_id=tess.id)
    db.add_all([cal, tess_cal])
    db.flush()

    jane = Contact(first_name="Jane", last_name="Roof", email="jane@example.test",
                   phone="+19415550101", address_street="1 Palm St", address_city="Bradenton",
                   address_state="FL", address_postal_code="34205", source="FB")
    bob = Contact(first_name="Bob", last_name="Workiz", phone="+19415550150",
                  source="Existig Customer")
    leo = Contact(first_name="Leo", last_name="Lead", phone="+19415550170")
    # Two people sharing one phone stay two customers.
    ann = Contact(first_name="Ann", last_name="Shared", phone="+19415550180")
    al = Contact(first_name="Al", last_name="Shared", phone="+19415550180")
    tim = Contact(first_name="Tim", last_name="Techjob", phone="+19415550190")
    db.add_all([jane, bob, leo, ann, al, tim])
    db.flush()
    addr = {"address_street": "9 Gulf Dr", "address_city": "Palmetto", "address_state": "FL",
            "address_postal_code": "34221"}
    jane_card = Opportunity(title="Jane Roof - leak", contact_id=jane.id, pipeline_id=ahs.id,
                            stage_id=stages["ahs:Inspection"].id, value_cents=0,
                            owner_id=owner.id, address_street="1 Palm St",
                            address_city="Bradenton", address_state="FL",
                            address_postal_code="34205", source="AHS",
                            custom_fields={"checklist_leak_count": 2,
                                           "checklist_roof_age": "10" + chr(0x2013) + "15 yrs",
                                           "checklist_email_verified": True,
                                           "checklist_previous_repair": "Yes",
                                           "checklist_previous_repair__details": "2019 patch",
                                           "ahs_job_id": "AHS-555"})
    bob_card = Opportunity(title="Bob Workiz - repair", contact_id=bob.id,
                           pipeline_id=retail.id, stage_id=stages["retail:Scheduled"].id,
                           created_by="Workiz import", **addr,
                           custom_fields={"workiz_id": "W100", "workiz_tech": ["Antonio"],
                                          "job_type": "Roof Repair"})
    al_card = Opportunity(title="Al Shared - reroof", contact_id=al.id, pipeline_id=retail.id,
                          stage_id=stages["retail:Invoice"].id, status="won",
                          value_cents=1200000, **addr)
    tim_card = Opportunity(title="Tim Techjob", contact_id=tim.id, pipeline_id=retail.id,
                           stage_id=stages["retail:New Lead"].id, owner_id=tess.id, **addr)
    follow_card = Opportunity(title="Ann Shared - follow up", contact_id=ann.id,
                              pipeline_id=retail.id, stage_id=stages["retail:Follow Up"].id,
                              source="Google", **addr)
    lost_estimate = Opportunity(title="Al Shared - gutters", contact_id=al.id,
                                pipeline_id=retail.id,
                                stage_id=stages["retail:Estimate Sent"].id, status="lost",
                                **addr)
    noaddr_card = Opportunity(title="Ann Shared - AHS no address", contact_id=ann.id,
                              pipeline_id=ahs.id, stage_id=stages["ahs:New Lead"].id)
    other_card = Opportunity(title="Commercial - out of scope", contact_id=ann.id,
                             pipeline_id=other.id, stage_id=stages["other:New"].id, **addr)
    db.add_all([jane_card, bob_card, al_card, tim_card, follow_card, lost_estimate,
                noaddr_card, other_card])
    db.flush()
    start = datetime(2026, 9, 20, 14, 0, tzinfo=UTC)
    visit = Appointment(title="Inspection", calendar_id=cal.id, contact_id=jane.id,
                        opportunity_id=jane_card.id, starts_at=start,
                        ends_at=start + timedelta(hours=1), status="confirmed")
    workiz_visit = Appointment(title="Repair", calendar_id=cal.id, contact_id=bob.id,
                               opportunity_id=bob_card.id, starts_at=start + timedelta(days=1),
                               ends_at=start + timedelta(days=1, hours=2), status="confirmed")
    tim_visit = Appointment(title="Tim estimate", calendar_id=tess_cal.id, contact_id=tim.id,
                            opportunity_id=tim_card.id, starts_at=start + timedelta(days=2),
                            ends_at=start + timedelta(days=2, hours=1), status="confirmed")
    db.add_all([visit, workiz_visit, tim_visit])
    db.flush()
    bob_card.custom_fields = {**bob_card.custom_fields, "workiz_appointment": {
        "id": workiz_visit.id, "starts_at": workiz_visit.starts_at.isoformat(),
        "ends_at": workiz_visit.ends_at.isoformat(), "status": "confirmed", "title": "Repair"}}
    note = OpportunityNote(opportunity_id=jane_card.id, body="Gate code 1234",
                           created_by_id=owner.id)
    task = OpportunityTask(opportunity_id=jane_card.id, contact_id=jane.id,
                           title="Call AHS for approval", created_by_id=owner.id)
    db.add_all([note, task])
    tokens = {}
    for key, u in (("owner", owner), ("dana", dana), ("tess", tess), ("terry", terry)):
        plain, tok = mint_api_token(u, name=key)
        db.add(tok)
        tokens[key] = plain
    db.commit()
    ids = {"owner": owner.id, "dana": dana.id, "tess": tess.id, "terry": terry.id,
           "ahs": ahs.id, "retail": retail.id, "other": other.id,
           "stages": {k: s.id for k, s in stages.items()},
           "jane": jane.id, "bob": bob.id, "leo": leo.id, "ann": ann.id, "al": al.id,
           "tim": tim.id, "jane_card": jane_card.id, "bob_card": bob_card.id,
           "al_card": al_card.id, "tim_card": tim_card.id, "follow_card": follow_card.id,
           "lost_estimate": lost_estimate.id, "noaddr_card": noaddr_card.id,
           "other_card": other_card.id, "visit": visit.id, "workiz_visit": workiz_visit.id,
           "tim_visit": tim_visit.id, "note": note.id, "task": task.id, "calendar": cal.id}
    return World(ids=ids, tokens=tokens)


@pytest.fixture()
def zworld(db):
    return seed(db)


def arm(*, confirm: bool = True, enable: bool = True) -> None:
    """Setup --commit (categories + checks), every by-hand item ticked, switch on."""
    with SessionLocal() as s:
        report = setup.run(s, commit=True)
        assert report["passed"], report["checks"]
    with SessionLocal() as s:
        row = config.settings(s)
        if confirm:
            row.confirmations = {r["key"]: {"by": "owner@example.test", "at": "now"}
                                 for r in row.setup_results if r["state"] == setup.BY_HAND}
        if enable:
            assert not setup.blockers(s), setup.blockers(s)
            row.enabled = True
            row.enabled_at = datetime.now(UTC) - timedelta(days=2)
        s.commit()


@pytest.fixture()
def armed(zworld, fake):
    arm()
    return zworld


def load(commit: bool = True) -> tuple[int, dict]:
    from app.zuper import load as loader
    with SessionLocal() as s:
        return loader.run(s, commit=commit)


@pytest.fixture()
def loaded(armed, fake):
    code, report = load()
    assert code == 0, report
    return armed


def ctx(session=None) -> engine.Ctx:
    return engine.Ctx(session or SessionLocal())


def mapping_row(s, crm_type: str, crm_id: int) -> ZuperMapping | None:
    from sqlalchemy import select
    return s.scalar(select(ZuperMapping).where(ZuperMapping.crm_type == crm_type,
                                               ZuperMapping.crm_id == crm_id))


def uid_of(crm_type: str, crm_id: int) -> str:
    with SessionLocal() as s:
        m = mapping_row(s, crm_type, crm_id)
        assert m is not None and m.state == "linked", (crm_type, crm_id)
        return m.zuper_uid


def status_uid(stage_id: int) -> str:
    return uid_of("stage", stage_id)


def zuper_jobs(s) -> list[Job]:
    from sqlalchemy import select
    return list(s.scalars(select(Job).where(Job.type.like("zuper_%"))).all())

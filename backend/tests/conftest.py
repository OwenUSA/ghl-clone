"""Test fixtures.

Tests run against a throwaway SQLite file, not the real Postgres database —
`dtr_ghl_clone` holds seeded working data and must never be dropped by a test run.
DATABASE_URL is set before app modules import, because app.db builds its engine at
import time.
"""
import os
import tempfile

import pytest

_TMP = os.path.join(tempfile.gettempdir(), "ghl_clone_test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Contact, Opportunity, Pipeline, Role, Stage, User  # noqa: E402


@pytest.fixture()
def db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture()
def contact(db):
    c = Contact(first_name="Test", last_name="Caller", phone="(941) 555-0100",
                email="test@example.test")
    db.add(c)
    db.flush()
    return c


@pytest.fixture()
def pipeline(db):
    p = Pipeline(name="Dream Team Roofing AHS")
    db.add(p)
    db.flush()
    a = Stage(pipeline_id=p.id, name="New Lead", position=0)
    b = Stage(pipeline_id=p.id, name="Inspection", position=1)
    db.add_all([a, b])
    db.flush()
    return p, a, b


@pytest.fixture()
def user(db):
    u = User(email="owner@example.test", name="Owner", role=Role.ADMIN)
    db.add(u)
    db.flush()
    return u


@pytest.fixture()
def opportunity(db, contact, pipeline):
    p, a, _ = pipeline
    o = Opportunity(title="TEST - Active", contact_id=contact.id,
                    pipeline_id=p.id, stage_id=a.id, value_cents=100000)
    db.add(o)
    db.flush()
    return o

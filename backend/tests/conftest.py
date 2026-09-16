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

# ---- pictures go to a throwaway directory, never to a real MEDIA_ROOT --------------------
# Set before any app module reads it, for the same reason DATABASE_URL is: a shell with
# production's MEDIA_ROOT exported must not have the suite writing JPEGs into the volume
# that holds customers' photographs — or, worse, `forget()` unlinking one. Per RUN rather
# than per test, so `attachments.store` can still prove that identical bytes are stored once
# across two messages in the same test.
os.environ["MEDIA_ROOT"] = os.path.join(tempfile.gettempdir(), "ghl_clone_test_media")

# ---- no test may reach owen-main (texting goes live, 2026-09-15) -------------------------
# Before any app module reads the environment: a shell with production's link settings
# exported must not arm the suite. See tests/owen_guard.py.
from tests import owen_guard  # noqa: E402

owen_guard.strip_environment()
owen_guard.install()

# ---- no test may reach Zuper (Zuper sync, 2026-09-16) -----------------------------------
# Same stance, same moment: no ZUPER_* setting survives into the suite, and every lookup of a
# zuperpro.com host is refused. See tests/zuper_guard.py.
from tests import zuper_guard  # noqa: E402

zuper_guard.strip_environment()
zuper_guard.install()

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Contact, Opportunity, Pipeline, Role, Stage, User  # noqa: E402

# ---- no test may reach an AI provider (AI Agents, 2026-09-15) ----------------------------
# Every lookup of a provider host is refused; a test that attempted one FAILS. See
# tests/ai_guard.py, and tests/test_ai_network_guard.py for the proof it is live.
from tests import ai_guard  # noqa: E402

ai_guard.install()

# The AI Agents fixtures (mocked providers, a seeded world), shared by tests/test_ai_*.py.
from tests.ai_support import script, secrets_key, world  # noqa: E402, F401

# The Zuper sync's fixtures (a fake Zuper, a seeded CRM, an armed and a loaded sync), shared by
# tests/test_zuper_*.py. See tests/zuper_support.py.
from tests.zuper_support import armed, fake, loaded, zuper_env, zworld  # noqa: E402, F401


@pytest.fixture(autouse=True)
def _no_owen_main_network():
    owen_guard.ATTEMPTS.clear()
    yield
    attempted = list(owen_guard.ATTEMPTS)
    owen_guard.ATTEMPTS.clear()
    assert not attempted, ("this test tried to reach owen-main (%s) — a configured link sends "
                           "REAL texts; mock crmlink at the HTTP boundary (tests/test_crm_link.py "
                           "`link`)" % ", ".join(attempted))


@pytest.fixture(autouse=True)
def _no_zuper_network():
    from app.zuper import client as zuper_client
    from app.zuper import listener as zuper_listener

    zuper_guard.ATTEMPTS.clear()
    zuper_client.TRANSPORT = zuper_guard.GUARD_TRANSPORT
    zuper_client.reset_pacing()
    zuper_listener.reset()
    yield
    zuper_client.TRANSPORT = zuper_guard.GUARD_TRANSPORT
    attempted = list(zuper_guard.ATTEMPTS)
    zuper_guard.ATTEMPTS.clear()
    assert not attempted, ("this test tried to reach Zuper (%s) — mock it at the HTTP boundary "
                           "(tests/zuper_fake.py)" % ", ".join(attempted))


@pytest.fixture(autouse=True)
def _no_provider_network():
    ai_guard.ATTEMPTS.clear()
    yield
    attempted = list(ai_guard.ATTEMPTS)
    ai_guard.ATTEMPTS.clear()
    assert not attempted, ("this test tried to reach a real AI provider (%s) — mock it at "
                           "the HTTP boundary (tests/ai_support.py)" % ", ".join(attempted))


# ---- rules 3 and 4 are OFF (owner's decision, 2026-09-15) --------------------------------
# "No automatic texts at all — only texts a person sends." Their code is kept behind the
# flags, and so are the tests that pin its mechanics (the reminder dedupe trap, reschedule,
# cancel-withdraws, one text per real move from every path). Those tests ask for the rule
# BY NAME with these fixtures; the default — off, nothing queued, a queued job refused — is
# asserted in tests/test_sms_live.py.

@pytest.fixture()
def rule_3_armed(monkeypatch):
    """Appointment reminders switched back ON for a test of their kept mechanics."""
    from app import automations
    monkeypatch.setattr(automations, "APPOINTMENT_REMINDERS_ENABLED", True)


@pytest.fixture()
def rule_4_armed(monkeypatch):
    """Stage-change texts switched back ON for a test of their kept mechanics."""
    from app import automations
    monkeypatch.setattr(automations, "STAGE_CHANGE_TEXT_ENABLED", True)


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

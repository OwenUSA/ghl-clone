"""Database session + base.

PostgreSQL is the target and the default (DECISIONS.md), shared with the
telephony project. Project database: **dtr_ghl_clone** (uniquely named so it
can't collide with the other databases on this machine).

Override with DATABASE_URL. A sqlite:// URL still works for a no-server bootstrap,
but `custom_fields` is only JSONB — indexable — on Postgres.
"""
import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DEFAULT_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/dtr_ghl_clone"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_URL)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


@event.listens_for(Session, "before_flush")
def _adopt_number_threads(session, flush_context, instances):
    """A contact that comes to exist with a number adopts that number's thread.

    Registered on the Session CLASS, so it covers every session this process makes —
    the API, the CLI-facing routes, the Workiz import and the tests alike — and a
    new way of creating a contact is covered without anyone remembering to call it.
    Imported lazily: the models import this module. See app/number_threads.py.
    """
    from .number_threads import adopt_on_flush

    adopt_on_flush(session)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---- Zuper sync (2026-09-16) ------------------------------------------------------------
# CRM changes become queued sync jobs from the flush, for every session, so no write path
# can be forgotten. With ZUPER_SYNC_ENABLED false each hook returns on its first line.
# Imported lazily for the same reason as above. See app/zuper/listener.py.

@event.listens_for(Session, "before_flush")
def _zuper_before_flush(session, flush_context, instances):
    from .zuper import listener

    listener.before_flush(session)


@event.listens_for(Session, "after_flush")
def _zuper_after_flush(session, flush_context):
    from .zuper import listener

    listener.after_flush(session)


@event.listens_for(Session, "after_commit")
def _zuper_after_commit(session):
    from .zuper import listener

    listener.after_commit(session)


@event.listens_for(Session, "after_rollback")
def _zuper_after_rollback(session):
    from .zuper import listener

    listener.after_rollback(session)

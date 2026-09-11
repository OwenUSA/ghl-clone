"""`alembic check` as a pytest case: the models and the migrations must agree.

CLAUDE.md documents `uv run alembic check` as the drift gate, and it is the right gate —
but it is a separate command nobody has to run, and it is not in the gate list any of
this project's briefs hand out. So it does not get run, and drift ships.

It shipped once, on this branch. The mirrored-feeds migration created the idempotency
guarantee as a unique INDEX while `ConversationEvent` declared it as `unique=True` on the
column, which renders a UniqueConstraint. Both enforce the same thing on both backends,
and every behavioural test passed — but to Alembic they are different OBJECTS, so
`alembic check` reported a phantom

    remove_index  Index('uq_conversation_events_dedupe_key', ..., unique=True)
    add_constraint UniqueConstraint(Column('dedupe_key'))

forever, and the next `--autogenerate` would have quietly folded a spurious
drop-and-recreate of a uniqueness guarantee into some unrelated migration. That is the
kind of edit that gets approved without comment and drops a constraint in production.

Running it HERE means it runs in `uv run pytest -q`, which every brief does list.

It builds the whole chain on a throwaway SQLite. That is not the production backend, so
this cannot catch a Postgres-only difference (a server_default rendering, a type that
SQLite collapses); `uv run alembic check` against the real database is still the
authority. What it does catch is the shape of mistake that is backend-independent —
declaring one object and creating another — which is the one that just happened.
"""
import contextlib
import pathlib
import tempfile

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

BACKEND = pathlib.Path(__file__).resolve().parent.parent


def _config() -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    return cfg


@contextlib.contextmanager
def _migrated_to_head():
    """A throwaway database at head, and the engine to inspect it.

    `migrations/env.py` resolves the URL from `app.db` and OVERRIDES whatever the Config
    carries — deliberately, so migrations can never run against a different database than
    the app. That is the right behaviour and this does not fight it: it rebinds
    `app.db.DATABASE_URL` for the duration instead, which is the value env.py reads, and
    puts it back afterwards.

    Without this the upgrade would run against the SHARED test SQLite file conftest
    points every test at, stamping an `alembic_version` table into it as a side effect.
    """
    import app.db as db_mod

    saved = db_mod.DATABASE_URL
    with tempfile.TemporaryDirectory() as tmp:
        url = "sqlite:///" + str(pathlib.Path(tmp) / "drift.db")
        db_mod.DATABASE_URL = url
        engine = create_engine(url)
        try:
            command.upgrade(_config(), "head")
            yield engine
        finally:
            engine.dispose()
            db_mod.DATABASE_URL = saved


def test_the_migrations_build_exactly_what_the_models_declare():
    """Upgrade a fresh database to head, then diff it against the models."""
    from app.db import Base

    with _migrated_to_head() as engine, engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn,
            # The same options env.py runs autogenerate with, or this would be
            # answering a different question than the real gate does.
            opts={"compare_type": True, "render_as_batch": True},
        )
        diff = compare_metadata(ctx, Base.metadata)

    assert diff == [], (
        "the models and the migration chain disagree — `uv run alembic check` would "
        "fail, and the next --autogenerate will emit this as a spurious change:\n  "
        + "\n  ".join(repr(d) for d in diff)
    )


def test_there_is_exactly_one_alembic_head():
    """Two heads make `alembic upgrade head` fail outright, which on this project has
    already cost a production outage. A branch that adds a migration without repointing
    it after a rebase produces exactly that, and it is invisible until deploy."""
    from alembic.script import ScriptDirectory

    heads = ScriptDirectory.from_config(_config()).get_heads()
    assert len(heads) == 1, (
        "expected ONE alembic head, found %d: %s — `alembic upgrade head` will fail. "
        "A migration added on a branch must have its down_revision repointed at the "
        "current head after a rebase." % (len(heads), sorted(heads))
    )

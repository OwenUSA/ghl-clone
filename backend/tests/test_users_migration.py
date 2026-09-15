"""Migration b5d1e8f3a276 only ADDS three columns to `users` — proven on a throwaway SQLite.

Stood up at the production head (c4e8a2f6b913) with production-shaped rows — a TECH, an
admin and a token-only machine account among them — upgraded, and compared: every table
keeps its columns and its rows byte-for-byte, `users` gains exactly
`only_assigned_data`, `phone` and `must_change_password`, and every existing user reads
false / NULL / false: their access today, and nobody forced to change a password. The
source is read too: `upgrade()` is three `op.add_column` calls and nothing else.
"""
import ast
import contextlib
import pathlib
import tempfile

from alembic import command
from sqlalchemy import create_engine, inspect, text
from tests.test_migration_drift import _config

BACKEND = pathlib.Path(__file__).resolve().parent.parent
PREVIOUS_HEAD = "c4e8a2f6b913"
REVISION = "b5d1e8f3a276"
NEW_COLUMNS = ["only_assigned_data", "phone", "must_change_password"]


@contextlib.contextmanager
def _database_at(revision: str):
    import app.db as db_mod

    saved = db_mod.DATABASE_URL
    with tempfile.TemporaryDirectory() as tmp:
        url = "sqlite:///" + str(pathlib.Path(tmp) / "users.db")
        db_mod.DATABASE_URL = url
        eng = create_engine(url)
        try:
            command.upgrade(_config(), revision)
            yield eng
        finally:
            eng.dispose()
            db_mod.DATABASE_URL = saved


def _dump(conn) -> dict:
    return {t: [dict(r._mapping)
                for r in conn.execute(text('SELECT * FROM "%s" ORDER BY 1' % t))]
            for t in inspect(conn).get_table_names()}


def _columns(conn) -> dict:
    insp = inspect(conn)
    return {t: [(c["name"], str(c["type"]), c["nullable"]) for c in insp.get_columns(t)]
            for t in insp.get_table_names()}


def _seed(conn):
    for uid, email, role, pw in ((1, "owen@x.test", "ADMIN", "scrypt$1$1$1$a$b"),
                                 (2, "luis@x.test", "TECH", "scrypt$1$1$1$c$d"),
                                 (3, "owen-telephony@x.test", "DISPATCHER", "")):
        conn.execute(text(
            "INSERT INTO users (id, email, name, password_hash, role, token_version, "
            "is_active, created_at) VALUES (:i, :e, :n, :p, :r, 3, 1, "
            "'2026-01-01 00:00:00')"), {"i": uid, "e": email, "n": email.split("@")[0],
                                        "p": pw, "r": role})
    conn.execute(text(
        "INSERT INTO api_tokens (id, user_id, name, token_hash, prefix, scopes, created_at) "
        "VALUES (1, 3, 'owen-ingest', 'h', 'ghl_pat_abc', 'events:write', "
        "'2026-01-01 00:00:00')"))
    conn.execute(text("INSERT INTO calendars (id, name, user_id, color) "
                      "VALUES (1, 'Luis Candialies', 2, '#004eeb')"))
    conn.execute(text("INSERT INTO pipelines (id, name, position) VALUES (1, 'AHS', 0)"))
    conn.execute(text("INSERT INTO stages (id, pipeline_id, name, position) "
                      "VALUES (1, 1, 'New Lead', 0)"))
    conn.execute(text(
        "INSERT INTO opportunities (id, title, pipeline_id, stage_id, value_cents, status, "
        "position, owner_id, custom_fields, created_at, updated_at) VALUES (1, 'Roof', 1, "
        "1, 950000, 'open', 0, 2, '{\"owen_call_id\": \"c-1\"}', '2026-02-01 00:00:00', "
        "'2026-02-01 00:00:00')"))


def test_the_migration_only_adds_three_user_columns_and_keeps_every_row():
    with _database_at(PREVIOUS_HEAD) as eng:
        with eng.begin() as conn:
            _seed(conn)
        with eng.connect() as conn:
            before_rows, before_cols = _dump(conn), _columns(conn)

        command.upgrade(_config(), REVISION)
        eng.dispose()
        with eng.connect() as conn:
            after_rows, after_cols = _dump(conn), _columns(conn)
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()

    assert version == REVISION
    assert set(after_cols) == set(before_cols), "no table added or dropped"
    for table, cols in before_cols.items():
        if table == "users":
            assert after_cols[table][:len(cols)] == cols, "an existing users column changed"
            assert [c[0] for c in after_cols[table][len(cols):]] == NEW_COLUMNS
            continue
        assert after_cols[table] == cols, "the columns of %s changed" % table
    for table, rows in before_rows.items():
        if table == "alembic_version":
            continue
        if table == "users":
            for old, new in zip(rows, after_rows["users"], strict=True):
                assert {k: new[k] for k in old} == old, "a user row changed"
                assert (new["only_assigned_data"], new["phone"],
                        new["must_change_password"]) == (0, None, 0)
            continue
        assert after_rows[table] == rows, "rows of %s changed" % table


def test_server_defaults_fill_a_row_written_without_the_new_columns_and_it_round_trips():
    with _database_at(REVISION) as eng:
        with eng.begin() as conn:
            _seed(conn)
            row = conn.execute(text("SELECT only_assigned_data, phone, must_change_password "
                                    "FROM users WHERE id = 2")).one()
            assert tuple(row) == (0, None, 0)
        command.downgrade(_config(), PREVIOUS_HEAD)
        eng.dispose()
        with eng.connect() as conn:
            names = [c["name"] for c in inspect(conn).get_columns("users")]
            assert not set(NEW_COLUMNS) & set(names)
            assert conn.execute(text("SELECT COUNT(*) FROM users")).scalar() == 3
            assert conn.execute(text("SELECT COUNT(*) FROM api_tokens")).scalar() == 1
        command.upgrade(_config(), REVISION)
        eng.dispose()
        with eng.connect() as conn:
            names = [c["name"] for c in inspect(conn).get_columns("users")]
            assert set(NEW_COLUMNS) <= set(names)


def test_the_upgrade_is_three_add_columns_and_nothing_else():
    path = next((BACKEND / "migrations" / "versions").glob(REVISION + "_*.py"))
    tree = ast.parse(path.read_text(encoding="utf-8"))
    upgrade = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name == "upgrade")
    calls = [n.func.attr for n in ast.walk(upgrade) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
             and n.func.value.id == "op"]
    assert calls == ["add_column", "add_column", "add_column"]
    assigned = {n.target.id: n.value.value for n in tree.body
                if isinstance(n, ast.AnnAssign) and isinstance(n.value, ast.Constant)}
    assert assigned["revision"] == REVISION
    assert assigned["down_revision"] == PREVIOUS_HEAD

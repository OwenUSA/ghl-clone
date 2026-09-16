"""Migration f5c1e9a3d742 only ADDS — proven on a throwaway SQLite stood up at the head it is
written on, e1b4d7c96a05 (production after feature/mms-images), with production-shaped rows.

Every pre-existing table keeps its columns and every row; exactly the eight zuper_* tables
appear, empty; their server defaults fill a row inserted without them; the round trip is clean;
and `upgrade()` calls nothing but create_table and create_index.
"""
import ast
import pathlib

from alembic import command
from sqlalchemy import inspect, text
from tests.test_ai_migration import _columns, _database_at, _dump, _seed
from tests.test_migration_drift import _config

BACKEND = pathlib.Path(__file__).resolve().parent.parent
PREVIOUS_HEAD = "e1b4d7c96a05"
REVISION = "f5c1e9a3d742"
NEW_TABLES = {"zuper_settings", "zuper_mappings", "zuper_sync_state", "zuper_webhook_inbox",
              "zuper_conflict_log", "zuper_delete_snapshots", "zuper_documents",
              "zuper_digests"}


def test_the_migration_only_adds_tables_and_keeps_every_row():
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
    assert set(after_cols) - set(before_cols) == NEW_TABLES
    for table in NEW_TABLES:
        assert after_rows[table] == [], table
    for table, cols in before_cols.items():
        assert after_cols[table] == cols, "an existing %s column changed" % table
    for table, rows in before_rows.items():
        if table != "alembic_version":
            assert after_rows[table] == rows, "a row of %s changed" % table


def test_server_defaults_fill_and_the_round_trip_is_clean():
    with _database_at(REVISION) as eng:
        with eng.begin() as conn:
            _seed(conn)
            conn.execute(text("INSERT INTO zuper_settings (id) VALUES (1)"))
            assert tuple(conn.execute(text(
                "SELECT enabled, setup_passed, workiz_cutover_date FROM zuper_settings")).one()) \
                == (0, 0, None)
            conn.execute(text("INSERT INTO zuper_mappings (id, crm_type, crm_id, zuper_type) "
                              "VALUES (1, 'contact', 1, 'customer')"))
            assert conn.execute(text("SELECT state FROM zuper_mappings")).scalar() == "linked"
            conn.execute(text("INSERT INTO zuper_webhook_inbox (id, body, body_sha256) "
                              "VALUES (1, '{}', 'abc')"))
            assert tuple(conn.execute(text(
                "SELECT status, signature, attempts FROM zuper_webhook_inbox")).one()) == (
                "pending", "none", 0)
            conn.execute(text("INSERT INTO zuper_delete_snapshots (id, batch, direction, "
                              "crm_type, crm_id, snapshot) VALUES (1, 'b', 'zuper_to_crm', "
                              "'contact', 1, '{}')"))
            assert conn.execute(text("SELECT state FROM zuper_delete_snapshots")).scalar() \
                == "pending"
            conn.execute(text("INSERT INTO zuper_documents (id, kind, zuper_uid) "
                              "VALUES (1, 'invoice', 'inv-1')"))
            assert conn.execute(text("SELECT removed_in_zuper FROM zuper_documents")).scalar() == 0
        command.downgrade(_config(), PREVIOUS_HEAD)
        eng.dispose()
        with eng.connect() as conn:
            assert not NEW_TABLES & set(inspect(conn).get_table_names())
            assert conn.execute(text("SELECT COUNT(*) FROM opportunities")).scalar() == 1
        command.upgrade(_config(), REVISION)
        eng.dispose()
        with eng.connect() as conn:
            assert set(inspect(conn).get_table_names()) >= NEW_TABLES


def test_upgrade_is_create_table_and_create_index_only():
    path = next((BACKEND / "migrations" / "versions").glob(REVISION + "_*.py"))
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    ops = set()
    for n in ast.walk(functions["upgrade"]):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "op"):
            ops.add(n.func.attr)
    assert ops == {"create_table", "create_index"}
    body = ast.get_source_segment(source, functions["upgrade"])
    assert "batch_alter_table" not in body and "execute" not in body
    assigned = {n.target.id: n.value.value for n in tree.body
                if isinstance(n, ast.AnnAssign) and isinstance(n.value, ast.Constant)}
    assert (assigned["revision"], assigned["down_revision"]) == (REVISION, PREVIOUS_HEAD)

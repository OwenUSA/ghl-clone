"""The CompanyCam migration (c4e8a2f6b913) only ADDS — proven on a throwaway SQLite.

Stood up at the previous head (a7d4c2e9f130) with representative production-shaped rows,
upgraded, and compared: every pre-existing table has the same columns and byte-identical
rows, exactly four tables are new and they are empty, and a downgrade/upgrade round trip is
clean. The source is read too: `upgrade()` is create_table / create_index and nothing else.
"""
import ast
import contextlib
import pathlib
import sqlite3
import tempfile

from alembic import command
from sqlalchemy import create_engine, inspect, text
from tests.test_migration_drift import _config

BACKEND = pathlib.Path(__file__).resolve().parent.parent
PREVIOUS_HEAD = "a7d4c2e9f130"
REVISION = "c4e8a2f6b913"
NEW_TABLES = {"companycam_links", "companycam_review_items", "companycam_sync_state",
              "companycam_project_requests"}


@contextlib.contextmanager
def _database_at(revision: str):
    import app.db as db_mod

    saved = db_mod.DATABASE_URL
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "cc.db"
        url = "sqlite:///" + str(path)
        db_mod.DATABASE_URL = url
        eng = create_engine(url)
        try:
            command.upgrade(_config(), revision)
            yield eng, path
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
    conn.execute(text(
        "INSERT INTO contacts (id, first_name, last_name, phone, email, dnd, "
        "address_street, address_city, address_state, address_postal_code, "
        "contact_type, custom_fields, created_at, updated_at) VALUES "
        "(1, 'Helios', 'Holdings', '+19415550100', 'h@x.test', 0, "
        "'1 Palm Ave', 'Bradenton', 'FL', '34205', 'Customer', '{}', "
        "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"))
    conn.execute(text("INSERT INTO pipelines (id, name, position) "
                      "VALUES (1, 'Dream Team Roofing AHS', 0)"))
    conn.execute(text("INSERT INTO stages (id, pipeline_id, name, position) "
                      "VALUES (1, 1, 'New Lead', 0)"))
    for i, (contact, status, blob, street) in enumerate(
            ((1, "open", '{"owen_call_id": "c-77", "workiz_id": "J1"}', "12 Palm Ave"),
             (1, "won", '{"workiz_id": "J2"}', None), (None, "open", "{}", None)), start=1):
        conn.execute(text(
            "INSERT INTO opportunities (id, title, contact_id, pipeline_id, stage_id, "
            "value_cents, status, position, custom_fields, address_street, created_at, "
            "updated_at) VALUES (:id, :t, :c, 1, 1, :v, :s, :p, :b, :st, "
            "'2026-02-01 00:00:00', '2026-02-02 00:00:00')"),
            {"id": i, "t": "Helios card %d" % i, "c": contact, "v": 1000 * i,
             "s": status, "p": i - 1, "b": blob, "st": street})


def test_the_migration_only_adds_four_empty_tables_and_keeps_every_row():
    with _database_at(PREVIOUS_HEAD) as (eng, _):
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
    assert set(before_cols) <= set(after_cols), "a table was dropped"
    for table, cols in before_cols.items():
        assert after_cols[table] == cols, "the columns of %s changed" % table
    for table, rows in before_rows.items():
        if table == "alembic_version":
            continue
        assert after_rows[table] == rows, "rows of %s changed" % table
    for table in NEW_TABLES:
        assert after_rows[table] == [], table


def test_a_card_delete_takes_its_link_rows_and_the_round_trip_is_clean():
    with _database_at(REVISION) as (eng, path):
        with eng.begin() as conn:
            _seed(conn)
            conn.execute(text("INSERT INTO companycam_links (opportunity_id, project_id, "
                              "method) VALUES (1, '101', 'workiz_job')"))
            conn.execute(text("INSERT INTO companycam_project_requests (opportunity_id, "
                              "origin) VALUES (1, 'created')"))
            row = conn.execute(text("SELECT state, attempts FROM "
                                    "companycam_project_requests")).one()
            assert tuple(row) == ("pending", 0), "server defaults"
            conn.execute(text("INSERT INTO companycam_review_items (project_id) "
                              "VALUES ('555')"))
            assert conn.execute(text("SELECT candidate_opportunity_ids FROM "
                                     "companycam_review_items")).scalar() == "[]"
        eng.dispose()
        raw = sqlite3.connect(path)
        try:
            raw.execute("PRAGMA foreign_keys=ON")
            raw.execute("DELETE FROM opportunities WHERE id = 1")
            raw.commit()
            assert raw.execute("SELECT COUNT(*) FROM companycam_links").fetchone()[0] == 0
            assert raw.execute(
                "SELECT COUNT(*) FROM companycam_project_requests").fetchone()[0] == 0
        finally:
            raw.close()

        command.downgrade(_config(), PREVIOUS_HEAD)
        eng.dispose()
        with eng.connect() as conn:
            assert not NEW_TABLES & set(inspect(conn).get_table_names())
            assert conn.execute(text("SELECT COUNT(*) FROM opportunities")).scalar() == 2
        command.upgrade(_config(), REVISION)
        eng.dispose()
        with eng.connect() as conn:
            assert set(inspect(conn).get_table_names()) >= NEW_TABLES


def test_the_upgrade_is_create_table_and_create_index_and_nothing_else():
    path = next((BACKEND / "migrations" / "versions").glob(REVISION + "_*.py"))
    tree = ast.parse(path.read_text(encoding="utf-8"))
    upgrade = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name == "upgrade")
    calls = [n.func.attr for n in ast.walk(upgrade) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
             and n.func.value.id == "op"]
    assert sorted(set(calls)) == ["create_index", "create_table"]
    assert calls.count("create_table") == 4
    assigned = {n.target.id: n.value.value for n in tree.body
                if isinstance(n, ast.AnnAssign) and isinstance(n.value, ast.Constant)}
    assert assigned["revision"] == REVISION
    assert assigned["down_revision"] == PREVIOUS_HEAD

"""Migration d7c3a9e5f214 only ADDS — proven on a throwaway SQLite stood up at the production
head b5d1e8f3a276 with production-shaped rows in every table it touches.

Every pre-existing table keeps its columns (the five it extends gain exactly their new ones,
at the end) and every row byte-for-byte; the added columns read NULL / 'normal'; exactly the
fifteen ai_* tables appear, empty; the round trip is clean; and `upgrade()` calls nothing
but create_table, create_index and add_column.
"""
import ast
import contextlib
import pathlib
import tempfile

from alembic import command
from sqlalchemy import create_engine, inspect, text
from tests.test_migration_drift import _config

BACKEND = pathlib.Path(__file__).resolve().parent.parent
PREVIOUS_HEAD = "b5d1e8f3a276"
REVISION = "d7c3a9e5f214"
NEW_TABLES = {"ai_settings", "ai_connections", "ai_folders", "ai_agents", "ai_agent_versions",
              "ai_knowledge_bases", "ai_kb_items", "ai_kb_chunks", "ai_knowledge_gaps",
              "ai_runs", "ai_run_steps", "ai_suggestions", "ai_agent_threads", "ai_templates",
              "ai_alerts"}
ADDED = {"conversation_events": ["ai_agent_id"], "number_thread_events": ["ai_agent_id"],
         "opportunity_notes": ["ai_agent_id"], "opportunity_tasks": ["ai_agent_id", "priority"],
         "appointments": ["ai_agent_id"]}


@contextlib.contextmanager
def _database_at(revision: str):
    import app.db as db_mod

    saved = db_mod.DATABASE_URL
    with tempfile.TemporaryDirectory() as tmp:
        url = "sqlite:///" + str(pathlib.Path(tmp) / "ai.db")
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
    ts = "'2026-08-01 12:00:00'"
    run = lambda sql: conn.execute(text(sql))  # noqa: E731
    run("INSERT INTO users (id, email, name, password_hash, role, token_version, is_active, "
        "only_assigned_data, must_change_password, created_at) VALUES "
        "(1, 'owen@x.test', 'Owen', 'scrypt$1', 'ADMIN', 0, 1, 0, 0, %s)" % ts)
    run("INSERT INTO contacts (id, first_name, last_name, phone, contact_type, dnd, "
        "custom_fields, created_at, updated_at) VALUES (1, 'Jane', 'Doe', '+19415550101', "
        "'Customer', 0, '{\"workiz_id\": \"C-1\"}', %s, %s)" % (ts, ts))
    run("INSERT INTO pipelines (id, name, position, color_mode, use_opportunity_probability) "
        "VALUES (1, 'AHS', 0, 'none', 0)")
    run("INSERT INTO stages (id, pipeline_id, name, position, show_in_funnel, show_in_pie) "
        "VALUES (1, 1, 'New Lead', 0, 1, 1)")
    run("INSERT INTO opportunities (id, title, contact_id, pipeline_id, stage_id, value_cents, "
        "status, position, custom_fields, created_at, updated_at) VALUES (1, 'Roof', 1, 1, 1, "
        "950000, 'open', 0, '{\"owen_call_id\": \"c-1\"}', %s, %s)" % (ts, ts))
    run("INSERT INTO conversations (id, contact_id, last_event_at, unread_count, starred) "
        "VALUES (1, 1, %s, 2, 0)" % ts)
    run("INSERT INTO conversation_events (id, conversation_id, type, direction, occurred_at, "
        "body, delivery_status, source_system) VALUES (1, 1, 'SMS', 'INBOUND', %s, 'leak!', "
        "NULL, 'BulkVS')" % ts)
    run("INSERT INTO number_threads (id, phone, phone_key, last_event_at, unread_count, "
        "starred, created_at) VALUES (1, '+13055550199', '3055550199', %s, 1, 0, %s)"
        % (ts, ts))
    run("INSERT INTO number_thread_events (id, number_thread_id, type, direction, "
        "occurred_at, body) VALUES (1, 1, 'CALL', 'INBOUND', %s, NULL)" % ts)
    run("INSERT INTO opportunity_notes (id, opportunity_id, body, created_by_id, created_at, "
        "updated_at) VALUES (1, 1, 'Work order', 1, %s, %s)" % (ts, ts))
    run("INSERT INTO opportunity_tasks (id, opportunity_id, contact_id, title, created_by_id, "
        "created_at, updated_at) VALUES (1, 1, 1, 'Call back', 1, %s, %s)" % (ts, ts))
    run("INSERT INTO calendars (id, name, user_id, color) VALUES (1, 'Crew', 1, '#004eeb')")
    run("INSERT INTO appointments (id, title, calendar_id, contact_id, starts_at, ends_at, "
        "status, opportunity_id) VALUES (1, 'Inspection', 1, 1, %s, %s, 'confirmed', 1)"
        % (ts, ts))
    run("INSERT INTO jobs (id, type, payload, status, attempts, max_attempts, run_after, "
        "created_at, dedupe_key) VALUES (1, 'appointment_reminder', '{\"appointment_id\": 1}',"
        " 'pending', 0, 5, %s, %s, 'appt_reminder:1:x:24h')" % (ts, ts))


def test_the_migration_only_adds_and_keeps_every_row():
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
    assert set(before_cols) <= set(after_cols), "a table was dropped"
    for table in NEW_TABLES:
        assert after_rows[table] == [], table
    for table, cols in before_cols.items():
        added = ADDED.get(table, [])
        assert after_cols[table][:len(cols)] == cols, "an existing %s column changed" % table
        assert [c[0] for c in after_cols[table][len(cols):]] == added, table
    for table, rows in before_rows.items():
        if table == "alembic_version":
            continue
        assert len(after_rows[table]) == len(rows), table
        for old, new in zip(rows, after_rows[table], strict=True):
            assert {k: new[k] for k in old} == old, "a row of %s changed" % table
            extra = {k: new[k] for k in ADDED.get(table, [])}
            expected = {k: ("normal" if k == "priority" else None) for k in extra}
            assert extra == expected, table


def test_a_row_written_without_the_new_columns_gets_the_defaults_and_it_round_trips():
    with _database_at(REVISION) as eng:
        with eng.begin() as conn:
            _seed(conn)
            assert conn.execute(text("SELECT priority, ai_agent_id FROM opportunity_tasks"))\
                .one() == ("normal", None)
            conn.execute(text("INSERT INTO ai_agents (id, name, draft) VALUES (1, 'A', '{}')"))
            row = conn.execute(text("SELECT mode, channel FROM ai_agents")).one()
            assert tuple(row) == ("off", "text")        # an agent is born Off
            conn.execute(text("INSERT INTO ai_runs (id, agent_id, \"trigger\", mode) VALUES "
                              "(1, 1, 'manual', 'off')"))
            row = conn.execute(text("SELECT outcome, is_test, input_tokens, cost_micros "
                                    "FROM ai_runs")).one()
            assert tuple(row) == ("queued", 0, 0, None)
        command.downgrade(_config(), PREVIOUS_HEAD)
        eng.dispose()
        with eng.connect() as conn:
            names = inspect(conn).get_table_names()
            assert not NEW_TABLES & set(names)
            assert "priority" not in [c["name"] for c in
                                      inspect(conn).get_columns("opportunity_tasks")]
            assert conn.execute(text("SELECT COUNT(*) FROM opportunity_tasks")).scalar() == 1
        command.upgrade(_config(), REVISION)
        eng.dispose()
        with eng.connect() as conn:
            assert set(inspect(conn).get_table_names()) >= NEW_TABLES


def test_upgrade_is_create_table_create_index_and_add_column_only():
    path = next((BACKEND / "migrations" / "versions").glob(REVISION + "_*.py"))
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    ops = set()
    for name in ("upgrade", "_index"):
        for n in ast.walk(functions[name]):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == "op"):
                ops.add(n.func.attr)
    assert ops == {"create_table", "create_index", "add_column"}
    source = ast.get_source_segment(path.read_text(encoding="utf-8"), functions["upgrade"])
    assert "batch_alter_table" not in source and "execute" not in source
    assigned = {n.target.id: n.value.value for n in tree.body
                if isinstance(n, ast.AnnAssign) and isinstance(n.value, ast.Constant)}
    assert (assigned["revision"], assigned["down_revision"]) == (REVISION, PREVIOUS_HEAD)

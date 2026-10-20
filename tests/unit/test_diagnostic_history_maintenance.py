"""Exercise destructive maintenance at the guarded SQLite boundary, without providers."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from planner.conversation.lifecycle_storage import ConversationRuntimeAlreadyOwned
from planner.conversation.retention.data import read_report
from planner.conversation.retention.policy import RETENTION_SECONDS
from planner.conversation.runtime_composition import SignalRuntimeComposition
from planner.conversation.views.storage import read_page
from planner.core.db import connect, create_schema
from planner.maintenance import diagnostic_history as maintenance

AS_OF = RETENTION_SECONDS + 1000


def seed_archive(database: Path) -> None:
    with closing(connect(str(database))) as conn:
        create_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,"
            "identity_environment_variables,latest_sequence,created_at) VALUES "
            "('history','codex','/tmp','full',?,4,1)",
            (json.dumps([["PLAN_ACTOR", "worker"], ["PLAN_TICKET_ID", "ticket"]]),),
        )
        conn.execute(
            "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
            "conversation_id,field_values,created_at,updated_at) VALUES "
            "('ticket','Completed work','coding','codex','done','done','history','{}',1,1)"
        )
        for sequence, kind, payload in (
            (1, "prompt", {"sender_label": "owner", "mode": "queue", "text": "Work"}),
            (2, "agent_message", {"text": "Old diagnostics"}),
            (3, "agent_message", {"text": "More old diagnostics"}),
            (4, "turn_ended", {"ending": "completed", "error_summary": None}),
        ):
            conn.execute(
                "INSERT INTO conversation_events VALUES ('history',?,?,?,?)",
                (sequence, kind, json.dumps(payload), sequence),
            )
        maintenance.rebuild_projection(conn, "history")
        conn.commit()
    runtime = SignalRuntimeComposition(str(database), 1000)
    try:
        runtime.bootstrap()
        runtime.coordinator.bind_runtime(lambda _identity: None)
        runtime.initialize()
    finally:
        runtime.close()


@pytest.fixture
def archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    for key in list(__import__("os").environ):
        if key.startswith("PLAN_"):
            monkeypatch.delenv(key)
    proc = tmp_path / "proc"
    proc.mkdir()
    scanner = maintenance._require_no_backend_activity
    monkeypatch.setattr(
        maintenance,
        "_require_no_backend_activity",
        lambda database, **options: scanner(database, proc=proc, **options),
    )
    database = tmp_path / "archive.db"
    seed_archive(database)
    yield database


def rows(database: Path, table: str) -> list[tuple[object, ...]]:
    with closing(sqlite3.connect(database)) as conn:
        return conn.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall()


def test_exact_removal_records_sparse_ranges_and_rebuilds_reader(archive: Path) -> None:
    report = read_report(archive, as_of_unix=AS_OF)
    assert [(c.conversation_id, c.sequence) for d in report.decisions for c in d.candidates] == [
        ("history", 2),
        ("history", 3),
    ]
    protected = rows(archive, "conversations"), rows(archive, "tickets")
    result = maintenance.apply_report(archive, report, confirmed=True)
    assert result.removed_event_count == 2
    assert rows(archive, "conversations") == protected[0]
    assert rows(archive, "tickets") == protected[1]
    with closing(sqlite3.connect(archive)) as conn:
        conn.row_factory = sqlite3.Row
        assert [row[0] for row in conn.execute("SELECT sequence FROM conversation_events")] == [
            1,
            4,
        ]
        assert tuple(conn.execute("SELECT * FROM conversation_expired_ranges").fetchone())[0:4] == (
            "history",
            2,
            3,
            1000,
        )
        for lens in ("full", "focus"):
            page = read_page(conn, "history", lens)
            assert page["items"]
        assert (
            conn.execute(
                "SELECT count(*) FROM conversation_view_rows r LEFT JOIN conversation_events e "
                "ON e.conversation_id=r.conversation_id AND e.sequence=r.sequence "
                "WHERE e.sequence IS NULL"
            ).fetchone()[0]
            == 0
        )
        assert (
            list(conn.execute("SELECT conversation_id FROM signal_publication_dirty"))[0][0]
            == "history"
        )
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with pytest.raises(RuntimeError, match="stale"):
        maintenance.apply_report(archive, report, confirmed=True)


def test_atomic_rollback_includes_delete_projection_and_new_metadata(
    archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = read_report(archive, as_of_unix=AS_OF)
    original = rows(archive, "conversation_events"), rows(archive, "conversation_view_rows")

    def fail(*_args: object) -> None:
        raise RuntimeError("projection failure")

    monkeypatch.setattr(maintenance, "rebuild_projection", fail)
    with pytest.raises(RuntimeError, match="projection failure"):
        maintenance.apply_report(archive, report, confirmed=True)
    assert rows(archive, "conversation_events") == original[0]
    assert rows(archive, "conversation_view_rows") == original[1]
    with closing(sqlite3.connect(archive)) as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='conversation_expired_ranges'"
            ).fetchone()
            is None
        )


def test_stale_protection_fact_and_actor_refuse_before_delete(
    archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = read_report(archive, as_of_unix=AS_OF)
    before = rows(archive, "conversation_events")
    with pytest.raises(RuntimeError, match="future"):
        maintenance.apply_report(
            archive, replace(report, as_of_unix=int(time.time()) + 1), confirmed=True
        )
    assert rows(archive, "conversation_events") == before
    monkeypatch.setenv("PLAN_ACTOR", "worker")
    with pytest.raises(RuntimeError, match="requires the owner"):
        maintenance.apply_report(archive, report, confirmed=True)
    monkeypatch.delenv("PLAN_ACTOR")
    with maintenance._exclusive_database(archive) as (conn, bootstrap):
        with maintenance.bind_transaction_admission(conn, bootstrap):
            conn.execute("BEGIN")
            conn.execute("UPDATE tickets SET stage='needs_implementation' WHERE id='ticket'")
            conn.commit()
    with pytest.raises(RuntimeError, match="stale"):
        maintenance.apply_report(archive, report, confirmed=True)
    assert rows(archive, "conversation_events") == before


def test_database_runtime_owner_and_sqlite_reader_block_apply(archive: Path) -> None:
    report = read_report(archive, as_of_unix=AS_OF)
    with closing(maintenance.acquire_runtime_ownership(str(archive))):
        with pytest.raises(ConversationRuntimeAlreadyOwned):
            maintenance.apply_report(archive, report, confirmed=True)
    with closing(sqlite3.connect(archive, isolation_level=None)) as competitor:
        competitor.execute("BEGIN")
        competitor.execute("SELECT * FROM conversation_events").fetchall()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            maintenance.apply_report(archive, report, confirmed=True)
    assert len(rows(archive, "conversation_events")) == 4


def test_exclusive_sqlite_lock_survives_commit_and_compaction(archive: Path) -> None:
    with maintenance._exclusive_database(archive) as (conn, _bootstrap):
        for operation in (lambda: None, lambda: conn.execute("VACUUM")):
            operation()
            with closing(sqlite3.connect(archive, timeout=0)) as competitor:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    competitor.execute("SELECT * FROM conversation_events").fetchall()
    before = rows(archive, "conversation_events")
    assert maintenance.reclaim_database(archive, confirmed=True).wal_bytes_after == 0
    assert rows(archive, "conversation_events") == before


def test_installer_is_idempotent_and_matches_future_migration_callback(archive: Path) -> None:
    with maintenance._exclusive_database(archive) as (conn, bootstrap):
        with maintenance.bind_transaction_admission(conn, bootstrap):
            conn.execute("BEGIN")
            maintenance.install_expiry_metadata(conn)
            definition = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='conversation_expired_ranges'"
            ).fetchone()[0]
            maintenance.install_expiry_metadata(conn)
            assert (
                conn.execute(
                    "SELECT sql FROM sqlite_master WHERE name='conversation_expired_ranges'"
                ).fetchone()[0]
                == definition
            )
            assert conn.execute("SELECT count(*) FROM conversation_events").fetchone()[0] == 4
            conn.commit()
    # A future migration receives the raw SQLite driver, not the core adapter.
    with closing(sqlite3.connect(":memory:")) as migration_connection:
        migration_connection.execute("CREATE TABLE conversations(conversation_id TEXT PRIMARY KEY)")
        maintenance.install_expiry_metadata(migration_connection)
        assert (
            migration_connection.execute(
                "SELECT sql FROM sqlite_master WHERE name='conversation_expired_ranges'"
            ).fetchone()[0]
            == definition
        )


@pytest.mark.parametrize("drift", ["table", "trigger"])
def test_metadata_schema_drift_refuses_before_deletion(archive: Path, drift: str) -> None:
    with maintenance._exclusive_database(archive) as (conn, bootstrap):
        with maintenance.bind_transaction_admission(conn, bootstrap):
            conn.execute("BEGIN")
            if drift == "table":
                conn.execute("CREATE TABLE conversation_expired_ranges (wrong TEXT)")
            else:
                maintenance.install_expiry_metadata(conn)
                conn.execute(
                    "CREATE TRIGGER unexpected_omission_side_effect AFTER INSERT "
                    "ON conversation_expired_ranges BEGIN DELETE FROM tickets; END"
                )
            conn.commit()
    report = read_report(archive, as_of_unix=AS_OF)
    before = rows(archive, "conversation_events")
    protected_tickets = rows(archive, "tickets")
    with pytest.raises(RuntimeError, match="omission metadata schema"):
        maintenance.apply_report(archive, report, confirmed=True)
    assert rows(archive, "conversation_events") == before
    assert rows(archive, "tickets") == protected_tickets


def test_cli_configuration_and_port_lease_refuse_before_writer(
    archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.maintenance import cli

    monkeypatch.setenv("PLAN_DB_PATH", str(archive))
    with pytest.raises(RuntimeError, match="differs from configuration"):
        cli._diagnostic_mutation(archive.with_name("wrong.db"), lambda: None)

    def refuse(_self: object) -> None:
        raise RuntimeError("port lifecycle lease is held")

    monkeypatch.setattr(cli.PortScopedServerLifecycleLease, "acquire", refuse)
    called = False

    def writer() -> None:
        nonlocal called
        called = True

    with pytest.raises(RuntimeError, match="port lifecycle lease is held"):
        cli._diagnostic_mutation(archive, writer)
    assert not called


@pytest.mark.parametrize(
    "identity",
    [
        {"PLAN_ACTOR": "worker", "PLAN_TICKET_ID": "ticket"},
        {"PLAN_ACTOR": "chief"},
        {"PLAN_ACTOR": "sprint_item_supervisor", "PLAN_SPRINT_ITEM_ID": "orchestrator"},
        {"PLAN_SERVER_URL": "http://127.0.0.1:8767"},
    ],
)
def test_orphan_backend_without_database_identity_blocks_all_principals(
    tmp_path: Path, identity: dict[str, str]
) -> None:
    proc = tmp_path / "proc"
    process = proc / "12345"
    process.mkdir(parents=True)
    (process / "environ").write_bytes(
        b"\0".join(key.encode() + b"=" + value.encode() for key, value in identity.items())
    )
    (process / "cmdline").write_bytes(b"python\0hermes_acp_extension.py\0")
    database = tmp_path / "archive.db"
    with pytest.raises(RuntimeError, match="Cannot resolve Panels backend database"):
        maintenance._require_no_backend_activity(database, proc=proc)
    maintenance._require_no_backend_activity(database, proc=proc, private_copy=True)
    (process / "environ").write_bytes(b"PLAN_DB_PATH=" + str(database).encode())
    with pytest.raises(RuntimeError, match="still owns this database"):
        maintenance._require_no_backend_activity(database, proc=proc, private_copy=True)

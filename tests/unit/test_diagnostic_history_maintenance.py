"""Exercise destructive maintenance at the guarded SQLite boundary, without providers."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import AsyncExitStack, ExitStack, closing
from dataclasses import replace
from pathlib import Path

import pytest

from planner.conversation.lifecycle_storage import (
    ConversationRuntimeAlreadyOwned,
    acquire_runtime_ownership,
)
from planner.conversation.retention.data import read_report
from planner.conversation.retention.policy import RETENTION_SECONDS
from planner.conversation.runtime_composition import SignalRuntimeComposition
from planner.conversation.views.storage import read_page
from planner.core import db
from planner.core.db import bind_transaction_admission, connect, create_schema
from planner.maintenance import diagnostic_history as maintenance
from planner.server_lifecycle.supervisor import PortScopedServerLifecycleLease

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
    assert rows(archive, "conversation_expired_ranges") == []


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
        with bind_transaction_admission(conn, bootstrap):
            conn.execute("BEGIN")
            conn.execute("UPDATE tickets SET stage='needs_implementation' WHERE id='ticket'")
            conn.commit()
    with pytest.raises(RuntimeError, match="stale"):
        maintenance.apply_report(archive, report, confirmed=True)
    assert rows(archive, "conversation_events") == before


def test_database_runtime_owner_and_sqlite_reader_block_apply(archive: Path) -> None:
    report = read_report(archive, as_of_unix=AS_OF)
    with closing(acquire_runtime_ownership(str(archive))):
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


@pytest.mark.parametrize("explicit_first", [True, False])
def test_actual_migration_and_explicit_install_converge_preserving_ranges(
    archive: Path, explicit_first: bool, tmp_path: Path
) -> None:
    copied = tmp_path / "migration.db"
    # Build the real historical schema. A current database cannot safely receive
    # a historical revision stamp after intervening structural migrations.
    with db.schema_migration_transaction(str(copied)) as migration:
        db.upgrade_schema_connection(migration, "open_work_group_index")
    with sqlite3.connect(archive) as source, closing(connect(str(copied))) as target:
        target.execute("BEGIN IMMEDIATE")
        # Transfer only columns that existed at this historical boundary.
        # The approved workspace policy is a later additive conversation column.
        for table in ("conversations", "conversation_events"):
            columns = [row[1] for row in target.execute(f"PRAGMA table_info({table})")]
            cursor = source.execute(f"SELECT {','.join(columns)} FROM {table}")
            placeholders = ",".join("?" for _ in columns)
            target.executemany(
                f"INSERT INTO {table}({','.join(columns)}) VALUES({placeholders})",
                cursor.fetchall(),
            )
        target.commit()
    archive = copied
    with sqlite3.connect(archive) as conn:
        if explicit_first:
            maintenance.install_expiry_metadata(conn)
            conn.execute("INSERT INTO conversation_expired_ranges VALUES ('history',2,3,1,2,2)")
    with db.schema_migration_transaction(str(archive)) as migration:
        db.upgrade_schema_connection(migration, "diagnostic_expiry_metadata")
    with sqlite3.connect(archive) as conn:
        definition = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='conversation_expired_ranges'"
        ).fetchone()[0]
        index_definition = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='conversation_view_rows_ordinary_turn'"
        ).fetchone()[0]
        assert [
            row[2]
            for row in conn.execute("PRAGMA index_info(conversation_view_rows_ordinary_turn)")
        ] == ["conversation_id", "turn_key", "sequence"]
        assert (
            "WHERE row_kind='agent_message' AND focus_visible=0 AND removed=0" in index_definition
        )
        if not explicit_first:
            conn.execute("INSERT INTO conversation_expired_ranges VALUES ('history',2,3,1,2,2)")
        maintenance.install_expiry_metadata(conn)
        maintenance.install_expiry_metadata(conn)
        assert (
            conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='conversation_view_rows_ordinary_turn'"
            ).fetchone()[0]
            == index_definition
        )
        assert conn.execute("SELECT * FROM conversation_expired_ranges").fetchall() == [
            ("history", 2, 3, 1, 2, 2)
        ]
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            "diagnostic_expiry_metadata"
        )
        assert conn.execute("SELECT count(*) FROM conversation_events").fetchone()[0] == 4
        assert (
            conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='conversation_expired_ranges'"
            ).fetchone()[0]
            == definition
        )


@pytest.mark.parametrize("drift", ["table", "trigger"])
def test_metadata_schema_drift_refuses_before_deletion(archive: Path, drift: str) -> None:
    with maintenance._exclusive_database(archive) as (conn, bootstrap):
        with bind_transaction_admission(conn, bootstrap):
            conn.execute("BEGIN")
            if drift == "table":
                conn.execute("DROP TABLE conversation_expired_ranges")
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

    monkeypatch.setattr(PortScopedServerLifecycleLease, "acquire", refuse)
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


def test_real_expiry_rebuild_preserves_deep_full_supervisor_controls_and_tool_identity(
    archive: Path, tmp_path: Path
) -> None:
    """Actual deletion/rebuild must preserve non-Ticket history and classify real tool refs."""
    from tests.unit.test_conversation_api import (
        CONVERSATION_PREFIX,
        _EventStreamDrive,
        _Harness,
        _run,
    )

    from planner.conversation.api import COMMITTED_EVENT_STREAM_NAME
    from planner.conversation.views.api import router
    from planner.conversation.views.storage import _body, _body_reference, _row_record, encode_wire
    from planner.core.errors import PlannerError
    from planner.core.server import handle_planner_error

    # Reuse the private archive and guarded offline writer; no provider is running.
    with maintenance._exclusive_database(archive) as (conn, bootstrap):
        with bind_transaction_admission(conn, bootstrap):
            conn.execute("BEGIN")
            conn.execute("UPDATE tickets SET stage='needs_implementation' WHERE id='ticket'")
            conn.execute(
                "INSERT INTO projects(id,name,created_at,updated_at) VALUES ('p','Archive',1,1)"
            )
            conn.execute(
                "INSERT INTO managers(id,title,project_id,manager_backend,manager_model,"
                "created_at,updated_at,settled) VALUES ('item','Settled','p','codex','test',1,1,1)"
            )
            conn.execute(
                "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,"
                "identity_environment_variables,latest_sequence,created_at) "
                "VALUES ('supervisor','codex','/tmp','full',?,200,1)",
                (
                    json.dumps(
                        [["PLAN_ACTOR", "sprint_item_supervisor"], ["PLAN_SPRINT_ITEM_ID", "item"]]
                    ),
                ),
            )
            controls = []
            for turn in range(40):
                base = turn * 5
                controls += [base + 1, base + 5]
                events = (
                    (
                        "prompt",
                        {
                            "text": f"Owner turn {turn}",
                            "sender_label": "owner",
                            "mode": "queue",
                            "sender_message_id": f"message-{turn}",
                        },
                    ),
                    (
                        "tool_call_started",
                        {
                            "tool_call_id": f"tool-{turn}",
                            "title": "Inspect",
                            "tool_kind": "exec",
                            "detail": "inspect",
                        },
                    ),
                    (
                        "tool_call_finished",
                        {
                            "tool_call_id": f"tool-{turn}",
                            "tool_call_status": "completed",
                            "detail": "output",
                        },
                    ),
                    ("agent_message", {"text": f"Diagnostic {turn}"}),
                    ("turn_ended", {"ending": "completed", "error_summary": None}),
                )
                for offset, (kind, payload) in enumerate(events, 1):
                    conn.execute(
                        "INSERT INTO conversation_events VALUES ('supervisor',?,?,?,?)",
                        (base + offset, kind, json.dumps(payload), base + offset),
                    )
            maintenance.rebuild_projection(conn, "supervisor")
            source_row = _row_record(conn, "supervisor", 97)
            tool_reference = _body_reference(
                source_row,
                encode_wire(_body(conn, "supervisor", source_row, full_tool_detail=True)).encode(),
                detail=True,
            )
            conn.commit()
    report = read_report(archive, as_of_unix=AS_OF)
    held = next(item for item in report.decisions if item.conversation_id == "history")
    assert held.hold_reasons == ("unfinished_ticket",) and held.candidates == ()
    eligible = next(item for item in report.decisions if item.conversation_id == "supervisor")
    assert not eligible.hold_reasons and len(eligible.candidates) == 120
    assert maintenance.apply_report(archive, report, confirmed=True).removed_event_count == 120
    with sqlite3.connect(archive) as raw_conn:
        assert [
            row[0]
            for row in raw_conn.execute(
                "SELECT sequence FROM conversation_events WHERE conversation_id='supervisor' "
                "ORDER BY sequence"
            )
        ] == controls
        assert (
            raw_conn.execute(
                "SELECT latest_sequence FROM conversations WHERE conversation_id='supervisor'"
            ).fetchone()[0]
            == 200
        )
        assert (
            raw_conn.execute(
                "SELECT count(*) FROM conversation_events WHERE conversation_id='history'"
            ).fetchone()[0]
            == 4
        )
    # The offline writer leaves its closed identity unavailable. Reopen a private copy
    # through the existing HTTP harness to exercise actual route envelopes and replay.
    copied = tmp_path / "post-expiry.db"
    with sqlite3.connect(archive) as source, sqlite3.connect(copied) as target:
        source.backup(target)

    async def exercise() -> None:
        async with AsyncExitStack() as cleanup:
            composition = SignalRuntimeComposition(str(copied), 1000)
            cleanup.callback(composition.close)
            composition.bootstrap()
            publication_cleanup = cleanup.enter_context(ExitStack())
            harness = _Harness(copied, signal_composition=composition, cleanup=cleanup)
            composition.initialize()
            attachment = composition.coordinator.attach_signal_publication(
                install=lambda connection, snapshot: None,
                participate=lambda connection, batch: None,
                unavailable=lambda reason: None,
            )
            publication_cleanup.callback(attachment.close)
            harness.app.include_router(router, prefix=CONVERSATION_PREFIX)
            harness.app.add_exception_handler(PlannerError, handle_planner_error)
            async with harness.client() as client:
                path = f"{CONVERSATION_PREFIX}/conversations/supervisor"
                snapshot = (await client.get(path + "/view", params={"lens": "full"})).json()
                assert snapshot["coverage"]["through_sequence"] == 200
                assert snapshot["status"]["diagnostics_expired"] is True
                page = snapshot["page"]
                seen = list(page["items"])
                while page["older"] is not None:
                    page = (
                        await client.get(
                            path + "/view/pages",
                            params={
                                "lens": "full",
                                "cursor": json.dumps(page["older"]),
                            },
                        )
                    ).json()
                    seen = page["items"] + seen
                assert [
                    item["source_sequence"] for item in seen if item["kind"] == "row"
                ] == controls
                assert not any(item.get("row_kind") == "tool_call" for item in seen)
                assert (await client.get(path + "/events/98/detail")).status_code == 410
                for suffix in ("/body", "/body/content"):
                    expired = await client.get(
                        path + "/view" + suffix,
                        params={
                            "reference": json.dumps(tool_reference),
                        },
                    )
                    assert expired.status_code == 410
                    assert expired.json()["error"]["code"] == "diagnostic_expired"
                async with _EventStreamDrive(harness.app, path + "/tail", "after=95") as stream:
                    for sequence in [value for value in controls if value > 95]:
                        name, event = await stream.next_named_frame()
                        assert name == COMMITTED_EVENT_STREAM_NAME
                        assert event["sequence"] == sequence

    _run(exercise)


def test_native_stop_proof_holds_diagnostics_after_terminal_lifecycle(archive: Path) -> None:
    """A locally ended turn cannot release history while its Mac child may still run."""
    from planner.maintenance.diagnostic_history_measure import independent_removal_oracle

    def candidates() -> set[tuple[str, int]]:
        report = read_report(archive, as_of_unix=AS_OF)
        keys = {(c.conversation_id, c.sequence) for d in report.decisions for c in d.candidates}
        with closing(sqlite3.connect(archive)) as conn:
            assert independent_removal_oracle(conn, report.cutoff_unix) == keys
        return keys

    expected = {("history", 2), ("history", 3)}
    assert candidates() == expected
    with closing(sqlite3.connect(archive)) as conn:
        conn.execute(
            "INSERT INTO mac_runs VALUES ('history','mac','child','ticket','ticket',1,0,'open')"
        )
        conn.commit()
    assert candidates() == set()
    with closing(sqlite3.connect(archive)) as conn:
        conn.execute("UPDATE mac_runs SET stop_state='stopping'")
        conn.commit()
    assert candidates() == set()
    with closing(sqlite3.connect(archive)) as conn:
        conn.execute("UPDATE mac_runs SET stop_state='closed'")
        conn.commit()
    assert candidates() == expected
    with closing(sqlite3.connect(archive)) as conn:
        conn.execute("UPDATE mac_runs SET stop_state='unproved'")
        conn.commit()
    assert candidates() == set()

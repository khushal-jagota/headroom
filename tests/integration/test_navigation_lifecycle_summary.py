"""Compact lifecycle migration and bounded settled-history proof on synthetic files."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import cast

import pytest
from alembic import command

from planner.core.db import _alembic_config, _migration_engine, connect, create_schema
from planner.navigation import journal, snapshot
from planner.navigation.contracts import HistoryUnavailable, LifecycleSummary
from planner.navigation.lifecycle_summary import (
    LATEST_TURN_SQL,
    UNRESOLVED_COUNTS_SQL,
    read_summary,
)


def _database(path: Path) -> sqlite3.Connection:
    conn = connect(str(path))
    create_schema(conn)
    conn.execute(
        "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,created_at) "
        "VALUES('c','codex','/synthetic','full',0)"
    )
    return conn


def _summary_expected() -> LifecycleSummary:
    return {
        "latest_turn": {"identity": "latest", "turn_number": 100000, "status": "completed"},
        "turn_status_counts": {"intent": 1, "running": 1},
        "request_status_counts": {"pending": 1, "answering": 1},
        "attempt_status_counts": {"intent": 1, "unknown": 1},
    }


def _add_history(conn: sqlite3.Connection, size: int) -> None:
    conn.execute("BEGIN")
    facts = [
        ("c", "turn", "latest", 100000, "turn", "completed", None),
        ("c", "turn", "old-turn-intent", 1, "turn", "intent", None),
        ("c", "turn", "old-turn-running", 2, "turn", "running", None),
        ("c", "request", "old-pending", 1, "permission", "pending", None),
        ("c", "request", "old-answering", 2, "user_input", "answering", None),
        ("c", "attempt", "old-intent", 1, "prompt", "intent", None),
        ("c", "attempt", "old-unknown", 2, "prompt", "unknown", None),
    ]
    # These are settled facts with known outcomes, not unknown attempted deliveries.
    settled_kinds = (
        ("turn", "turn", "completed"),
        ("request", "permission", "answered"),
        ("attempt", "prompt", "started"),
    )
    for index in range(size):
        kind, operation, status = settled_kinds[index % len(settled_kinds)]
        facts.append(("c", kind, f"settled-{index}", index + 3, operation, status, None))
    conn.executemany("INSERT INTO conversation_lifecycle_facts VALUES(?,?,?,?,?,?,?)", facts)
    conn.commit()


def test_summary_preserves_all_old_uncertainty_without_history_arrays(tmp_path: Path) -> None:
    conn = _database(tmp_path / "facts.db")
    _add_history(conn, 10)
    assert read_summary(conn, "c") == _summary_expected()
    record = next(r for r in snapshot(conn)["records"] if r["kind"] == "conversation")
    assert "lifecycle" not in record["value"]
    assert record["value"]["lifecycle_summary"] == _summary_expected()
    assert snapshot(conn)["semantics"] == "generic-source-facts-v3"
    assert snapshot(conn)["schema_version"] == 3
    conn.close()


def test_settled_history_does_not_grow_packet_or_update_vm_work(tmp_path: Path) -> None:
    from planner.conversation.lifecycle import (
        LifecycleFact,
        LifecycleKind,
        LifecycleOperation,
        LifecycleStatus,
    )
    from planner.conversation.lifecycle_storage import apply_facts

    measured: dict[str, list[tuple[str, int, int]]] = {
        "sequence": [],
        "read": [],
        "lifecycle": [],
    }
    for size in (1000, 10000):
        conn = _database(tmp_path / f"history-{size}.db")
        _add_history(conn, size)
        plans = [
            str(row[3])
            for sql in (LATEST_TURN_SQL, UNRESOLVED_COUNTS_SQL)
            for row in conn.execute("EXPLAIN QUERY PLAN " + sql, ("c",))
        ]
        assert any("navigation_lifecycle_latest_turn" in plan for plan in plans)
        assert any("navigation_lifecycle_unresolved" in plan for plan in plans)
        assert all("SCAN conversation_lifecycle_facts" not in plan for plan in plans)
        for scenario in measured:
            conn.execute("BEGIN")
            if scenario == "sequence":
                conn.execute("UPDATE conversations SET latest_sequence=7 WHERE conversation_id='c'")
            elif scenario == "read":
                conn.execute(
                    "UPDATE conversations SET owner_read_through_sequence=3 "
                    "WHERE conversation_id='c'"
                )
            else:
                # The canonical writer reads source history. Its separate cost is not
                # part of this measurement of navigation finalization after the write.
                apply_facts(
                    conn,
                    "c",
                    (
                        LifecycleFact(
                            LifecycleKind.attempt,
                            "old-intent",
                            1,
                            LifecycleOperation.prompt,
                            LifecycleStatus.unknown,
                        ),
                    ),
                )
            instructions = 0

            def progress() -> int:
                nonlocal instructions
                instructions += 1
                return 0

            conn.set_progress_handler(progress, 1)
            conn.commit()
            conn.set_progress_handler(None, 0)
            record = next(r for r in snapshot(conn)["records"] if r["kind"] == "conversation")
            packet = json.dumps(record, sort_keys=True, separators=(",", ":"))
            expected = _summary_expected()
            if scenario == "lifecycle":
                expected["attempt_status_counts"] = {"intent": 0, "unknown": 2}
            assert record["value"]["lifecycle_summary"] == expected
            measured[scenario].append((packet, instructions, len(packet.encode())))
            print(
                json.dumps(
                    {
                        "scenario": scenario,
                        "settled_rows": size,
                        "finalize_vm_instructions": instructions,
                        "record_bytes": len(packet.encode()),
                    }
                )
            )
        conn.close()
    for scenario, samples in measured.items():
        assert samples[0][0] == samples[1][0], scenario
        assert samples[0][2] == samples[1][2], scenario
        # B-tree depth can add a few instructions. History cannot add a row scan.
        assert samples[1][1] <= samples[0][1] + 100, scenario


def test_unknown_history_is_counted_without_a_packet_history_list(tmp_path: Path) -> None:
    conn = _database(tmp_path / "uncertainty.db")
    _add_history(conn, 0)
    before = read_summary(conn, "c")
    instructions = 0

    def progress() -> int:
        nonlocal instructions
        instructions += 1
        return 0

    conn.set_progress_handler(progress, 1)
    read_summary(conn, "c")
    conn.set_progress_handler(None, 0)
    small_work = instructions
    conn.execute("BEGIN")
    conn.executemany(
        "INSERT INTO conversation_lifecycle_facts "
        "VALUES('c','attempt',?,1,'prompt','unknown',NULL)",
        [(f"unknown-{index}",) for index in range(5000)],
    )
    conn.commit()
    instructions = 0
    conn.set_progress_handler(progress, 1)
    after = read_summary(conn, "c")
    conn.set_progress_handler(None, 0)
    assert after["attempt_status_counts"] == {"intent": 1, "unknown": 5001}
    assert after["latest_turn"] == before["latest_turn"]
    assert set(after) == set(before)
    assert len(json.dumps(after)) < len(json.dumps(before)) + 16
    assert instructions > small_work + 5000
    print(
        json.dumps(
            {
                "unknown_rows": 5001,
                "summary_vm_instructions": instructions,
                "small_summary_vm_instructions": small_work,
                "summary_bytes": len(json.dumps(after)),
            }
        )
    )
    conn.close()


def test_latest_turn_uses_deterministic_identity_tie_and_absence(tmp_path: Path) -> None:
    conn = _database(tmp_path / "latest.db")
    assert read_summary(conn, "c")["latest_turn"] is None
    conn.execute("BEGIN")
    conn.executemany(
        "INSERT INTO conversation_lifecycle_facts VALUES('c','turn',?,8,'turn',?,NULL)",
        [("a", "failed"), ("z", "interrupted")],
    )
    conn.commit()
    assert read_summary(conn, "c")["latest_turn"] == {
        "identity": "z",
        "turn_number": 8,
        "status": "interrupted",
    }
    conn.close()


def test_actual_v1_list_upgrade_and_failure_preserve_old_continuity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.navigation import data as navigation_data
    from planner.projects.data import seed_default_projects
    from planner.worker_types.shipped import seed_chief_settings, seed_shipped_worker_types

    path = tmp_path / "old-v1.db"
    engine = _migration_engine(str(path), 1000)
    with engine.begin() as connection:
        command.upgrade(_alembic_config(connection), "durable_conversation_facts")
        raw = cast(sqlite3.Connection, connection.connection.driver_connection)
        seed_default_projects(raw)
        seed_shipped_worker_types(raw)
        seed_chief_settings(raw)
        connection.exec_driver_sql(
            "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,"
            "access,created_at) VALUES('c','codex','/synthetic','full',0)"
        )
        connection.exec_driver_sql(
            "INSERT INTO conversation_lifecycle_facts "
            "VALUES('c','attempt','old',1,'prompt','unknown',NULL)"
        )
        command.upgrade(_alembic_config(connection), "transactional_navigation")
        connection.exec_driver_sql(
            "UPDATE conversations SET latest_sequence=1 WHERE conversation_id='c'"
        )
        navigation_data.finalize(raw)
        before = tuple(connection.exec_driver_sql("SELECT * FROM navigation_meta").one())
        batches = connection.exec_driver_sql("SELECT * FROM navigation_journal").fetchall()
        assert len(batches) == 1
        assert json.loads(batches[0][1])["semantics"] == "generic-source-facts-v1"
        records = connection.exec_driver_sql(
            "SELECT kind,id,value FROM navigation_records ORDER BY kind,id"
        ).fetchall()
        old_value = json.loads(next(row[2] for row in records if row[0] == "conversation"))
        assert old_value["lifecycle"] == [
            {
                "kind": "attempt",
                "identity": "old",
                "turn_number": 1,
                "operation": "prompt",
                "status": "unknown",
                "provider_identity": None,
            }
        ]
        assert "lifecycle_summary" not in old_value
        assert before[2] == 1
    conn = connect(str(path))
    with pytest.raises(RuntimeError, match="current schema"):
        snapshot(conn)
    with pytest.raises(RuntimeError, match="current schema"):
        journal(conn, str(before[3]), "0")
    conn.close()

    original = navigation_data.rebuild

    def fail_rebuild(conn: sqlite3.Connection) -> None:
        original(conn)
        raise RuntimeError("injected compact migration failure")

    with monkeypatch.context() as patch:
        patch.setattr(navigation_data, "rebuild", fail_rebuild)
        with pytest.raises(RuntimeError, match="injected compact migration failure"):
            with engine.begin() as connection:
                command.upgrade(_alembic_config(connection), "head")
    with engine.begin() as connection:
        assert tuple(connection.exec_driver_sql("SELECT * FROM navigation_meta").one()) == before
        assert connection.exec_driver_sql("SELECT * FROM navigation_journal").fetchall() == batches
        assert (
            connection.exec_driver_sql(
                "SELECT kind,id,value FROM navigation_records ORDER BY kind,id"
            ).fetchall()
            == records
        )
        assert (
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='navigation_lifecycle_unresolved'"
            ).scalar()
            == 0
        )
        assert (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
            == "transactional_navigation"
        )
        command.upgrade(_alembic_config(connection), "head")
    engine.dispose()
    conn = connect(str(path))
    after = snapshot(conn)
    assert after["scope"] == before[1]
    assert after["schema_version"] == 3
    assert after["epoch"] != before[3]
    assert after["revision"] == "0"
    value = next(r for r in after["records"] if r["kind"] == "conversation")["value"]
    assert value["lifecycle_summary"]["attempt_status_counts"]["unknown"] == 1
    assert "lifecycle" not in value
    with pytest.raises(HistoryUnavailable):
        journal(conn, str(before[3]), "0")
    create_schema(conn)
    assert snapshot(conn) == after
    conn.close()


def test_schema_two_metadata_upgrade_is_atomic_and_rejects_old_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from planner.navigation import data, proof
    from planner.navigation.configuration import DeliveryConfig
    from planner.navigation.delivery import read_page
    from planner.navigation.delivery_contracts import ResetRequired, decode_cursor, encode_cursor
    from planner.projects.data import seed_default_projects
    from planner.worker_types.shipped import seed_chief_settings, seed_shipped_worker_types

    path = tmp_path / "schema-two.db"
    engine = _migration_engine(str(path), 1000)
    with engine.begin() as connection:
        command.upgrade(_alembic_config(connection), "reconcile_navigation_staging")
        raw = cast(sqlite3.Connection, connection.connection.driver_connection)
        seed_default_projects(raw)
        seed_shipped_worker_types(raw)
        seed_chief_settings(raw)
        raw.execute(
            "INSERT OR IGNORE INTO projects(id,name,created_at,updated_at) "
            "VALUES('project_panels','Synthetic Panels',0,0)"
        )
        raw.execute(
            "INSERT INTO sprint_items(id,title,project_id,supervisor_backend,supervisor_model,"
            "priority,created_at,updated_at) "
            "VALUES('o','Orchestrator','project_panels','codex','synthetic','P1',17,18)"
        )
        raw.execute(
            "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
            "field_values,created_at,updated_at,worker_step_claim) "
            "VALUES('held','Ticket','coding','codex','needs_plan','done','{}',19,20,'out')"
        )
        raw.execute(
            "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
            "field_values,created_at,updated_at) "
            "VALUES('blocker','Blocker','coding','codex','needs_plan','done','{}',19,20)"
        )
        raw.execute("INSERT INTO ticket_blocks VALUES('blocker','held')")
        data.finalize(raw)
        records = [
            json.loads(row[0])
            for row in raw.execute("SELECT value FROM navigation_records WHERE kind='ticket'")
        ]
        assert all("updated_at" not in value and "blocked" not in value for value in records)
        assert "priority" not in json.loads(
            raw.execute(
                "SELECT value FROM navigation_records WHERE kind='orchestrator'"
            ).fetchone()[0]
        )
        scope, version, epoch, revision = raw.execute(
            "SELECT scope,schema_version,epoch,revision FROM navigation_meta"
        ).fetchone()
        assert version == 2
        old_continuity = {
            "scope": scope,
            "schema_version": 2,
            "epoch": epoch,
            "revision": str(revision),
            "semantics": "generic-source-facts-v2",
        }
        old_cursor = decode_cursor(encode_cursor(old_continuity))  # type: ignore[arg-type]
        old_fence = {"version": 1, **old_continuity, "coverage": "complete", "witnesses": []}
        old_fence["signature"] = proof._signature(raw, old_fence)
        before = list(raw.iterdump())
    real_rebuild = data.rebuild
    calls = 0

    def fail_after_rebuild(conn: sqlite3.Connection) -> None:
        nonlocal calls
        calls += 1
        real_rebuild(conn)
        raise RuntimeError("metadata rebuild failure")

    with monkeypatch.context() as patch:
        patch.setattr(data, "rebuild", fail_after_rebuild)
        with pytest.raises(RuntimeError, match="metadata rebuild failure"):
            with engine.begin() as connection:
                command.upgrade(_alembic_config(connection), "head")
    assert calls == 1
    with engine.begin() as connection:
        raw = cast(sqlite3.Connection, connection.connection.driver_connection)
        assert list(raw.iterdump()) == before
        command.upgrade(_alembic_config(connection), "head")
        current = snapshot(raw)
        assert current["schema_version"] == 3
        assert current["scope"] == scope
        assert current["epoch"] != epoch
        assert current["revision"] == "0"
        assert raw.execute("SELECT COUNT(*) FROM navigation_journal").fetchone()[0] == 0
        held = next(
            r["value"] for r in current["records"] if r["kind"] == "ticket" and r["id"] == "held"
        )
        assert held["updated_at"] == 20
        assert held["proposal_created_at"] is None
        assert held["worker_step_claim_out"] is True
        assert held["blocked"] is True
        assert proof.certify(raw, old_fence) == {
            "status": "unknown",
            "reason": "incompatible_scope",
        }
    engine.dispose()
    with pytest.raises(ResetRequired, match="incompatible_continuity"):
        read_page(path, old_cursor, DeliveryConfig())
    conn = connect(str(path))
    create_schema(conn)
    assert snapshot(conn) == current
    conn.close()

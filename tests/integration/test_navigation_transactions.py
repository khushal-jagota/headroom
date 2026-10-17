"""All fixtures use synthetic SQLite files in pytest temporary directories."""

import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest

from planner.core.db import connect, create_schema
from planner.navigation import journal, rebuild, snapshot
from planner.navigation.contracts import HistoryUnavailable


def database(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(str(tmp_path / "planner.db"))
    create_schema(conn)
    return conn


def add_conversation(conn: sqlite3.Connection, identity: str = "c") -> None:
    conn.execute("BEGIN")
    conn.execute(
        "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,created_at) "
        "VALUES(?,'codex','/synthetic','full',0)",
        (identity,),
    )
    conn.commit()


def values(conn: sqlite3.Connection) -> dict[tuple[str, str], dict[str, Any]]:
    # Assertions below select fields by the dynamic (tag, identity) lookup key.
    return {
        (record["kind"], record["id"]): cast(dict[str, Any], record["value"])
        for record in snapshot(conn)["records"]
    }


def test_durable_batch_survives_closed_connection_without_wake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.core import change_signal

    conn = database(tmp_path)
    baseline = snapshot(conn)
    monkeypatch.setattr(change_signal, "emit", lambda: None)
    add_conversation(conn)
    current = snapshot(conn)
    assert int(current["revision"]) == int(baseline["revision"]) + 1
    conn.close()
    conn = connect(str(tmp_path / "planner.db"))
    page = journal(conn, baseline["epoch"], baseline["revision"])
    assert len(page["batches"]) == 1
    assert any(
        r["kind"] == "conversation" and r["id"] == "c" for r in page["batches"][0]["upserts"]
    )
    conn.close()


def test_rollback_and_savepoint_restore_dirty_rows_and_revision(tmp_path: Path) -> None:
    conn = database(tmp_path)
    add_conversation(conn)
    baseline = snapshot(conn)
    conn.execute("BEGIN")
    conn.execute("SAVEPOINT inner")
    conn.execute("UPDATE conversations SET latest_sequence=9 WHERE conversation_id='c'")
    conn.execute(
        "INSERT INTO conversation_lifecycle_facts VALUES"
        "('c','attempt','receipt',1,'prompt','unknown',NULL)"
    )
    conn.execute("ROLLBACK TO inner")
    conn.execute("RELEASE inner")
    conn.commit()
    assert snapshot(conn) == baseline
    assert conn.execute("SELECT COUNT(*) FROM conversation_lifecycle_facts").fetchone()[0] == 0
    conn.execute("BEGIN")
    conn.execute("UPDATE conversations SET latest_sequence=9 WHERE conversation_id='c'")
    with pytest.raises(RuntimeError, match="pending source writes"):
        snapshot(conn)
    conn.rollback()
    assert snapshot(conn) == baseline
    conn.close()


def test_rebuild_resets_epoch_preserves_scope_and_canonical_read_facts(tmp_path: Path) -> None:
    conn = database(tmp_path)
    add_conversation(conn)
    conn.execute("BEGIN")
    conn.execute(
        "UPDATE conversations SET latest_sequence=8,owner_read_through_sequence=5 "
        "WHERE conversation_id='c'"
    )
    conn.commit()
    baseline = snapshot(conn)
    conn.execute("BEGIN")
    rebuild(conn)
    conn.commit()
    current = snapshot(conn)
    assert current["scope"] == baseline["scope"]
    assert current["epoch"] != baseline["epoch"]
    assert current["revision"] == "0"
    assert current["records"] == baseline["records"]
    with pytest.raises(HistoryUnavailable):
        journal(conn, baseline["epoch"], baseline["revision"])
    assert values(conn)["conversation", "c"]["owner_read_through_sequence"] == 5
    conn.close()


def test_private_cursor_update_does_not_allocate_revision(tmp_path: Path) -> None:
    conn = database(tmp_path)
    add_conversation(conn)
    baseline = snapshot(conn)
    conn.execute("BEGIN")
    conn.execute(
        "UPDATE conversations SET vendor_session_cursor='private' WHERE conversation_id='c'"
    )
    conn.commit()
    assert snapshot(conn) == baseline
    conn.close()


def test_schema_availability_refreshes_and_relevant_autocommit_fails_closed(tmp_path: Path) -> None:
    conn = database(tmp_path)
    conn.close()
    # A raw connection can read, but cannot bypass composition for canonical writes.
    raw = sqlite3.connect(tmp_path / "planner.db", isolation_level=None)
    with pytest.raises(sqlite3.OperationalError, match="navigation_transaction_active"):
        raw.execute("INSERT INTO projects(id,name,created_at,updated_at) VALUES('x','X',0,0)")
    raw.close()


def add_ticket(conn: sqlite3.Connection, identity: str, parent: str | None = None) -> None:
    conn.execute(
        "INSERT INTO tickets(id,title,worker_type,employee_backend,ceiling,field_values,"
        "created_at,updated_at,stage,orchestrator_kind,sprint_item_id) "
        "VALUES(?,?,'coding','codex','needs_implementation','{}',0,0,"
        "'needs_implementation',?,?)",
        (identity, identity, "chief" if parent is None else "sprint_item", parent),
    )


def add_parent(conn: sqlite3.Connection, identity: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO projects(id,name,created_at,updated_at) "
        "VALUES('project_panels','Synthetic Panels',0,0)"
    )
    conn.execute(
        "INSERT INTO sprint_items(id,title,project_id,supervisor_backend,supervisor_model,"
        "created_at,updated_at) VALUES(?,?,'project_panels','codex','synthetic',0,0)",
        (identity, identity),
    )


def test_aliases_historical_memberships_moves_and_deletes(tmp_path: Path) -> None:
    conn = database(tmp_path)
    add_conversation(conn)
    conn.execute("BEGIN")
    add_parent(conn, "a")
    add_parent(conn, "b")
    add_parent(conn, "middle")
    add_ticket(conn, "t", "a")
    conn.execute("UPDATE tickets SET conversation_id='c' WHERE id='t'")
    conn.execute("INSERT INTO ticket_conversations VALUES('c','t')")
    conn.execute("INSERT INTO agents VALUES('sprint_item_supervisor_a','c')")
    conn.commit()
    actual = values(conn)
    assert actual["totals", "orchestrator:a"] == {
        "child_count": 1,
        "distinct_conversation_count": 1,
    }
    assert len([k for k in actual if k[0] == "address"]) == 2
    assert actual["ticket", "t"]["ticket_status"] == "empty"
    assert actual["ticket", "t"]["stage_ownership"] == "worker"
    conn.execute("BEGIN")
    conn.execute("UPDATE tickets SET sprint_item_id='middle' WHERE id='t'")
    conn.execute("UPDATE tickets SET sprint_item_id='b',conversation_id=NULL WHERE id='t'")
    conn.commit()
    actual = values(conn)
    assert actual["totals", "orchestrator:a"] == {
        "child_count": 0,
        "distinct_conversation_count": 1,
    }
    assert actual["totals", "orchestrator:b"] == {
        "child_count": 1,
        "distinct_conversation_count": 1,
    }
    assert actual["totals", "orchestrator:middle"] == {
        "child_count": 0,
        "distinct_conversation_count": 0,
    }
    assert actual["totals", "workspace"] == {"child_count": 1, "distinct_conversation_count": 1}
    ticket_address = [
        value
        for key, value in actual.items()
        if key[0] == "address" and value["owner_kind"] == "ticket"
    ][0]
    assert ticket_address["current"] is False
    assert ticket_address["parent_id"] == "b"
    conn.execute("BEGIN")
    conn.execute("DELETE FROM tickets WHERE id='t'")
    conn.commit()
    actual = values(conn)
    assert ("ticket", "t") not in actual
    assert actual["totals", "orchestrator:b"] == {
        "child_count": 0,
        "distinct_conversation_count": 0,
    }
    assert len([k for k in actual if k[0] == "address"]) == 1
    conn.close()


def test_blocker_status_old_dependencies_and_same_transaction_definition(tmp_path: Path) -> None:
    conn = database(tmp_path)
    conn.execute("BEGIN")
    add_ticket(conn, "blocker")
    add_ticket(conn, "held")
    conn.execute("INSERT INTO ticket_blocks VALUES('blocker','held')")
    conn.commit()
    assert values(conn)["ticket", "held"]["ticket_status"] == "blocked"
    conn.execute("BEGIN")
    conn.execute("UPDATE tickets SET stage='done' WHERE id='blocker'")
    conn.commit()
    assert values(conn)["ticket", "held"]["ticket_status"] == "empty"
    conn.execute("BEGIN")
    conn.execute("UPDATE tickets SET stage='needs_implementation' WHERE id='blocker'")
    conn.execute("DELETE FROM tickets WHERE id='blocker'")
    conn.commit()
    assert values(conn)["ticket", "held"]["ticket_status"] == "empty"
    conn.close()


def test_historical_supervisor_identity_survives_current_pointer_reset(tmp_path: Path) -> None:
    conn = database(tmp_path)
    add_conversation(conn)
    conn.execute("BEGIN")
    add_parent(conn, "a")
    conn.execute(
        "UPDATE conversations SET identity_environment_variables=? WHERE conversation_id='c'",
        ('[["PLAN_ACTOR","sprint_item_supervisor"],["PLAN_SPRINT_ITEM_ID","a"]]',),
    )
    conn.execute("INSERT INTO agents VALUES('sprint_item_supervisor_a','c')")
    conn.commit()
    conn.execute("BEGIN")
    conn.execute(
        "UPDATE agents SET conversation_id=NULL WHERE agent_key='sprint_item_supervisor_a'"
    )
    conn.commit()
    actual = values(conn)
    address = [value for key, value in actual.items() if key[0] == "address"][0]
    assert address["current"] is False
    assert actual["totals", "orchestrator:a"]["distinct_conversation_count"] == 1
    conn.close()


def test_definition_and_approval_metadata_use_current_transaction(tmp_path: Path) -> None:
    import json

    conn = database(tmp_path)
    conn.execute("BEGIN")
    add_ticket(conn, "t")
    raw = conn.execute(
        "SELECT definition_json FROM worker_types WHERE worker_type='coding'"
    ).fetchone()[0]
    definition = json.loads(raw)
    stage = next(s for s in definition["stages"] if s["id"] == "needs_implementation")
    stage["ownership_mode"] = "user"
    conn.execute(
        "UPDATE worker_types SET definition_json=? WHERE worker_type='coding'",
        (json.dumps(definition),),
    )
    proposal = {
        "field": "implementation",
        "body": "SECRET BODY",
        "proposed_by": "worker",
        "created_at": 0,
    }
    conn.execute(
        "UPDATE tickets SET pending_proposal=?,pending_proposal_revision=7 WHERE id='t'",
        (json.dumps(proposal),),
    )
    conn.commit()
    record = values(conn)["ticket", "t"]
    assert record["stage_ownership"] == "user"
    assert record["proposal_field"] == "implementation"
    assert record["proposal_revision"] == 7
    assert record["ticket_status"] == "awaiting_approval"
    assert record["ceiling_holder"] == {"kind": "owner", "id": "owner"}
    assert "SECRET BODY" not in json.dumps(snapshot(conn))
    conn.close()


def test_real_projection_failure_rolls_back_source_and_journal(tmp_path: Path) -> None:
    conn = database(tmp_path)
    add_conversation(conn)
    baseline = snapshot(conn)
    conn.execute(
        "CREATE TRIGGER fail_nav BEFORE INSERT ON navigation_journal "
        "BEGIN SELECT RAISE(ABORT,'injected nav failure'); END"
    )
    conn.execute("BEGIN")
    conn.execute("UPDATE conversations SET latest_sequence=9 WHERE conversation_id='c'")
    conn.execute(
        "INSERT INTO conversation_lifecycle_facts VALUES"
        "('c','attempt','receipt',1,'prompt','unknown',NULL)"
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected nav failure"):
        conn.commit()
    assert not conn.in_transaction
    assert (
        conn.execute(
            "SELECT latest_sequence FROM conversations WHERE conversation_id='c'"
        ).fetchone()[0]
        == 0
    )
    assert snapshot(conn) == baseline
    assert conn.execute("SELECT COUNT(*) FROM conversation_lifecycle_facts").fetchone()[0] == 0
    conn.close()


def test_journal_pages_gap_cursor_empty_delta_and_delete(tmp_path: Path) -> None:
    conn = database(tmp_path)
    baseline = snapshot(conn)
    add_conversation(conn)
    conn.execute("BEGIN")
    conn.execute(
        "UPDATE conversations SET latest_sequence=latest_sequence WHERE conversation_id='c'"
    )
    conn.commit()
    page = journal(conn, baseline["epoch"], baseline["revision"], 1)
    assert len(page["batches"]) == 1 and page["has_more"]
    after = page["batches"][0]["revision"]
    tail = journal(conn, baseline["epoch"], after)
    assert tail["batches"][0]["upserts"] == []
    assert tail["batches"][0]["deletes"] == []
    conn.execute("BEGIN")
    conn.execute("DELETE FROM conversations WHERE conversation_id='c'")
    conn.commit()
    tail = journal(conn, baseline["epoch"], tail["revision"])
    assert {"kind": "conversation", "id": "c"} in tail["batches"][0]["deletes"]
    with pytest.raises(HistoryUnavailable):
        journal(conn, baseline["epoch"], str(int(tail["revision"]) + 1))
    with pytest.raises(ValueError):
        journal(conn, baseline["epoch"], "00")
    conn.execute("DELETE FROM navigation_journal WHERE revision=?", (int(after) + 1,))
    with pytest.raises(HistoryUnavailable, match="missing"):
        journal(conn, baseline["epoch"], after)
    conn.close()


def test_synthetic_old_head_migration_backfills_and_failure_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alembic import command

    from planner.core.db import _alembic_config, _migration_engine
    from planner.navigation import data as navigation_data

    path = tmp_path / "historical.db"
    engine = _migration_engine(str(path), 1000)
    with engine.begin() as connection:
        command.upgrade(_alembic_config(connection), "durable_conversation_facts")
        connection.exec_driver_sql(
            "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,"
            "created_at,latest_sequence,owner_read_through_sequence) "
            "VALUES('historical','codex','/synthetic','full',0,12,4)"
        )
    real_rebuild = navigation_data.rebuild

    def fail_rebuild(conn: sqlite3.Connection) -> None:
        real_rebuild(conn)
        raise RuntimeError("injected migration failure")

    with monkeypatch.context() as patch:
        patch.setattr(navigation_data, "rebuild", fail_rebuild)
        with pytest.raises(RuntimeError, match="injected migration failure"):
            with engine.begin() as connection:
                command.upgrade(_alembic_config(connection), "head")
    with engine.begin() as connection:
        assert (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
            == "durable_conversation_facts"
        )
        assert (
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='navigation_meta'"
            ).scalar()
            == 0
        )
        command.upgrade(_alembic_config(connection), "head")
    engine.dispose()
    conn = connect(str(path))
    actual = values(conn)
    assert actual["conversation", "historical"] == {
        "latest_sequence": 12,
        "owner_read_through_sequence": 4,
        "latest_agent_activity_sequence": 0,
        "latest_agent_activity_at": None,
        "error_acknowledged_through_sequence": 0,
        "lifecycle": [],
    }
    assert actual["totals", "workspace"] == {"child_count": 0, "distinct_conversation_count": 0}
    before = snapshot(conn)
    conn.close()
    conn = connect(str(path))
    assert snapshot(conn) == before
    conn.close()


def test_snapshot_is_consistent_while_another_connection_commits(tmp_path: Path) -> None:
    conn = database(tmp_path)
    add_conversation(conn)
    reader = connect(str(tmp_path / "planner.db"))
    reader.execute("BEGIN")
    old = snapshot(reader)
    conn.execute("BEGIN")
    conn.execute("UPDATE conversations SET latest_sequence=17 WHERE conversation_id='c'")
    conn.commit()
    assert snapshot(reader) == old
    reader.rollback()
    new = snapshot(reader)
    assert int(new["revision"]) == int(old["revision"]) + 1
    conversation = next(r for r in new["records"] if r["kind"] == "conversation")
    assert conversation["value"]["latest_sequence"] == 17
    reader.close()
    conn.close()


def test_second_schema_open_keeps_continuity_identical(tmp_path: Path) -> None:
    conn = database(tmp_path)
    add_conversation(conn)
    baseline = snapshot(conn)
    create_schema(conn)
    assert snapshot(conn) == baseline
    conn.close()


def test_two_ticket_aliases_count_one_conversation_per_parent(tmp_path: Path) -> None:
    conn = database(tmp_path)
    add_conversation(conn)
    conn.execute("BEGIN")
    add_parent(conn, "a")
    add_ticket(conn, "first", "a")
    add_ticket(conn, "second", "a")
    conn.execute("UPDATE tickets SET conversation_id='c' WHERE id IN ('first','second')")
    conn.commit()
    actual = values(conn)
    assert actual["totals", "orchestrator:a"] == {
        "child_count": 2,
        "distinct_conversation_count": 1,
    }
    assert actual["totals", "workspace"] == {"child_count": 2, "distinct_conversation_count": 1}
    assert len([key for key in actual if key[0] == "address"]) == 2
    baseline = snapshot(conn)
    conn.execute(
        "INSERT OR IGNORE INTO conversations(conversation_id,backend_key,workspace_folder,"
        "access,created_at) VALUES('c','codex','/synthetic','full',0)"
    )
    assert snapshot(conn) == baseline
    conn.close()


def test_lifecycle_upsert_and_repeated_updates_capture_one_dirty_key(tmp_path: Path) -> None:
    from planner.conversation.lifecycle import (
        LifecycleFact,
        LifecycleKind,
        LifecycleOperation,
        LifecycleStatus,
    )
    from planner.conversation.lifecycle_storage import apply_facts

    conn = database(tmp_path)
    add_conversation(conn)
    baseline = snapshot(conn)
    conn.execute("BEGIN")
    intent = LifecycleFact(
        LifecycleKind.attempt,
        "a",
        1,
        LifecycleOperation.prompt,
        LifecycleStatus.intent,
    )
    unknown = LifecycleFact(
        LifecycleKind.attempt,
        "a",
        1,
        LifecycleOperation.prompt,
        LifecycleStatus.unknown,
    )
    apply_facts(conn, "c", (intent,))
    apply_facts(conn, "c", (unknown,))
    apply_facts(conn, "c", (unknown,))
    conn.execute("UPDATE conversations SET latest_sequence=1 WHERE conversation_id='c'")
    conn.execute("UPDATE conversations SET latest_sequence=2 WHERE conversation_id='c'")
    conn.commit()
    actual = snapshot(conn)
    assert int(actual["revision"]) == int(baseline["revision"]) + 1
    conversation = next(r for r in actual["records"] if r["kind"] == "conversation")
    assert conversation["value"]["lifecycle"][0]["status"] == "unknown"
    assert conversation["value"]["latest_sequence"] == 2
    conn.close()


def test_replace_unique_collision_deletes_old_identity(tmp_path: Path) -> None:
    conn = database(tmp_path)
    conn.execute(
        "INSERT INTO projects(id,name,created_at,updated_at) VALUES('old','Replacement',0,0)"
    )
    baseline = snapshot(conn)
    conn.execute("BEGIN")
    conn.execute(
        "INSERT OR REPLACE INTO projects(id,name,created_at,updated_at) "
        "VALUES('new','Replacement',0,0)"
    )
    conn.commit()
    actual = values(conn)
    assert ("project", "old") not in actual
    assert actual["project", "new"] == {"name": "Replacement", "priority": None}
    delta = journal(conn, baseline["epoch"], baseline["revision"])["batches"][0]
    assert {"kind": "project", "id": "old"} in delta["deletes"]
    assert actual["totals", "workspace"] == {"child_count": 0, "distinct_conversation_count": 0}
    conn.close()

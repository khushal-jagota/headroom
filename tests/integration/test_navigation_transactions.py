"""All fixtures use synthetic SQLite files in pytest temporary directories."""

import json
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest
from tests.integration.test_navigation_staging_merge import historical_snapshot
from tests.support.navigation_current import (
    current_navigation_database,
    prepared_navigation_database,
)

from planner.core.db import connect, create_schema
from planner.navigation import journal, rebuild, snapshot
from planner.navigation.contracts import HistoryUnavailable, NavigationRecord
from planner.worker_types.contracts import WorkerTypeDefinition


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

    with current_navigation_database(tmp_path / "planner.db") as conn:
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
    with current_navigation_database(tmp_path / "planner.db") as conn:
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
    with current_navigation_database(tmp_path / "planner.db") as conn:
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
    with current_navigation_database(tmp_path / "planner.db") as conn:
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
    with current_navigation_database(tmp_path / "planner.db") as conn:
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
    with current_navigation_database(tmp_path / "planner.db") as conn:
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
    with current_navigation_database(tmp_path / "planner.db") as conn:
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
    with current_navigation_database(tmp_path / "planner.db") as conn:
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

    with current_navigation_database(tmp_path / "planner.db") as conn:
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
        assert record["proposal_created_at"] == 0
        assert record["ticket_status"] == "awaiting_approval"
        assert record["ceiling_holder"] == {"kind": "owner", "id": "owner"}
        assert "SECRET BODY" not in json.dumps(snapshot(conn))
        before = snapshot(conn)
        proposal["created_at"] = 42
        proposal["body"] = "REPLACEMENT BODY"
        conn.execute("BEGIN")
        conn.execute(
            "UPDATE tickets SET pending_proposal=?,pending_proposal_revision=8 WHERE id='t'",
            (json.dumps(proposal),),
        )
        conn.commit()
        replacement = values(conn)["ticket", "t"]
        assert replacement["proposal_field"] == "implementation"
        assert replacement["proposal_created_at"] == 42
        assert replacement["proposal_revision"] == 8
        assert journal(conn, before["epoch"], before["revision"])["batches"][0]["upserts"] == [
            {"kind": "ticket", "id": "t", "value": replacement}
        ]
        conn.execute("BEGIN")
        conn.execute("UPDATE tickets SET pending_proposal=NULL WHERE id='t'")
        conn.commit()
        assert values(conn)["ticket", "t"]["proposal_created_at"] is None
        conn.close()


def test_metadata_edits_publish_source_values_without_status_changes(tmp_path: Path) -> None:
    with current_navigation_database(tmp_path / "planner.db") as conn:
        conn.execute("BEGIN")
        add_parent(conn, "a")
        add_ticket(conn, "t", "a")
        conn.commit()
        before = snapshot(conn)
        conn.execute("BEGIN")
        conn.execute("UPDATE sprint_items SET created_at=88 WHERE id='a'")
        conn.execute("UPDATE tickets SET updated_at=99 WHERE id='t'")
        conn.commit()
        actual = values(conn)
        assert "priority" not in actual["orchestrator", "a"]
        assert actual["orchestrator", "a"]["created_at"] == 88
        assert actual["ticket", "t"]["updated_at"] == 99
        assert actual["ticket", "t"]["ticket_status"] == "empty"
        batch = journal(conn, before["epoch"], before["revision"])["batches"]
        assert len(batch) == 1
        assert {(r["kind"], r["id"]) for r in batch[0]["upserts"]} == {
            ("ticket", "t"),
            ("orchestrator", "a"),
        }
        before_creation_edit = snapshot(conn)
        original_ticket = actual["ticket", "t"]
        conn.execute("UPDATE tickets SET created_at=12 WHERE id='t'")
        changed_ticket = values(conn)["ticket", "t"]
        assert changed_ticket == {**original_ticket, "created_at": 12}
        assert changed_ticket["updated_at"] == 99
        assert changed_ticket["ticket_status"] == "empty"
        batches = journal(conn, before_creation_edit["epoch"], before_creation_edit["revision"])[
            "batches"
        ]
        assert len(batches) == 1
        assert batches[0]["upserts"] == [{"kind": "ticket", "id": "t", "value": changed_ticket}]
        assert batches[0]["deletes"] == []
        before_preview_edit = snapshot(conn)
        original_parent = values(conn)["orchestrator", "a"]
        recap = '\t"雪" \\\n ' + "😀" * 200 + " SOURCE-BODY-TAIL"
        conn.execute("BEGIN")
        conn.execute("UPDATE tickets SET recap=? WHERE id='t'", (recap,))
        conn.execute("UPDATE sprint_items SET body=? WHERE id='a'", ("\n Parent\tline\nRésumé ",))
        conn.commit()
        previews = values(conn)
        assert previews["ticket", "t"] == {
            **changed_ticket,
            "recap_preview": '"雪" \\ ' + "😀" * 153 + "…",
        }
        assert previews["orchestrator", "a"] == {
            **original_parent,
            "recap_preview": "Parent line Résumé",
        }
        assert len(previews["ticket", "t"]["recap_preview"]) == 160
        assert "SOURCE-BODY-TAIL" not in json.dumps(snapshot(conn))
        batches = journal(conn, before_preview_edit["epoch"], before_preview_edit["revision"])[
            "batches"
        ]
        assert len(batches) == 1
        assert {(r["kind"], r["id"]) for r in batches[0]["upserts"]} == {
            ("ticket", "t"),
            ("orchestrator", "a"),
        }
        assert batches[0]["deletes"] == []
        stable = snapshot(conn)
        conn.execute("BEGIN")
        conn.execute(
            "UPDATE tickets SET updated_at=100,created_at=13,recap='rollback' WHERE id='t'"
        )
        conn.execute("UPDATE sprint_items SET body='rollback' WHERE id='a'")
        conn.execute("DELETE FROM tickets WHERE id='t'")
        conn.execute("DELETE FROM sprint_items WHERE id='a'")
        conn.rollback()
        assert snapshot(conn) == stable
        conn.execute("BEGIN")
        rebuild(conn)
        conn.commit()
        assert snapshot(conn)["records"] == stable["records"]
        conn.close()


def test_claim_fact_counts_every_stored_out_without_status_or_membership_filter(
    tmp_path: Path,
) -> None:
    with current_navigation_database(tmp_path / "planner.db") as conn:
        conn.execute("BEGIN")
        for identity, stage, claim, backlog in (
            ("ordinary", "needs_implementation", "out", 0),
            ("done", "done", "out", 0),
            ("backlog", "needs_implementation", "out", 1),
            ("proposal", "needs_implementation", "out", 0),
            ("errored", "needs_implementation", "errored", 0),
            ("none", "needs_implementation", "none", 0),
        ):
            add_ticket(conn, identity)
            conn.execute(
                "UPDATE tickets SET stage=?,worker_step_claim=?,is_backlogged=? WHERE id=?",
                (stage, claim, backlog, identity),
            )
        conn.execute(
            "UPDATE tickets SET pending_proposal=? WHERE id='proposal'",
            (
                json.dumps(
                    {
                        "field": "implementation",
                        "body": "body",
                        "proposed_by": "worker",
                        "created_at": 1,
                    }
                ),
            ),
        )
        conn.execute("INSERT INTO ticket_blocks VALUES('none','ordinary')")
        conn.commit()
        tickets = {key[1]: value for key, value in values(conn).items() if key[0] == "ticket"}
        assert sum(record["worker_step_claim_out"] for record in tickets.values()) == 4
        assert tickets["proposal"]["ticket_status"] == "awaiting_approval"
        assert tickets["ordinary"]["blocked"] is True
        assert tickets["ordinary"]["ticket_status"] == "agent"
        assert tickets["done"]["worker_step_claim_out"] is True
        assert tickets["errored"]["worker_step_claim_out"] is False
        assert tickets["none"]["worker_step_claim_out"] is False
        before = snapshot(conn)
        conn.execute("BEGIN")
        conn.execute("DELETE FROM tickets WHERE id='done'")
        conn.commit()
        batch = journal(conn, before["epoch"], before["revision"])["batches"][0]
        assert {"kind": "ticket", "id": "done"} in batch["deletes"]
        assert (
            sum(v["worker_step_claim_out"] for k, v in values(conn).items() if k[0] == "ticket")
            == 3
        )
        conn.close()


@pytest.mark.parametrize("masked", ["proposal", "out", "errored"])
def test_live_blocker_is_independent_and_captures_all_edge_transitions(
    tmp_path: Path,
    masked: str,
) -> None:
    with current_navigation_database(tmp_path / "planner.db") as conn:
        conn.execute("BEGIN")
        for identity in ("held", "a", "b"):
            add_ticket(conn, identity)
        if masked == "proposal":
            conn.execute(
                "UPDATE tickets SET pending_proposal=? WHERE id='held'",
                (
                    json.dumps(
                        {
                            "field": "implementation",
                            "body": "body",
                            "proposed_by": "worker",
                            "created_at": 1,
                        }
                    ),
                ),
            )
        else:
            conn.execute("UPDATE tickets SET worker_step_claim=? WHERE id='held'", (masked,))
        conn.commit()
        expected_status = {"proposal": "awaiting_approval", "out": "agent", "errored": "errored"}[
            masked
        ]

        def transition(sql: str, blocked: bool) -> None:
            before = snapshot(conn)
            conn.execute("BEGIN")
            conn.execute(sql)
            conn.commit()
            record = values(conn)["ticket", "held"]
            assert record["blocked"] is blocked
            assert record["ticket_status"] == expected_status
            batch = journal(conn, before["epoch"], before["revision"])["batches"][0]
            changed = [r for r in batch["upserts"] if r["kind"] == "ticket" and r["id"] == "held"]
            # Equal public values yield no upsert, but retain a complete atomic batch.
            assert changed == (
                []
                if values_before[0] == blocked
                else [{"kind": "ticket", "id": "held", "value": record}]
            )
            values_before[0] = blocked

        values_before = [False]
        transition("INSERT INTO ticket_blocks VALUES('a','held')", True)
        transition("INSERT INTO ticket_blocks VALUES('b','held')", True)
        transition("DELETE FROM ticket_blocks WHERE blocking_ticket_id='a'", True)
        stable = snapshot(conn)
        conn.execute("BEGIN")
        conn.execute("DELETE FROM tickets WHERE id='b'")
        conn.rollback()
        assert snapshot(conn) == stable
        transition("UPDATE tickets SET stage='done' WHERE id='b'", False)
        transition("UPDATE tickets SET stage='needs_implementation' WHERE id='b'", True)
        transition("DELETE FROM tickets WHERE id='b'", False)
        conn.execute("BEGIN")
        rebuild(conn)
        conn.commit()
        assert values(conn)["ticket", "held"]["blocked"] is False
        conn.close()


@pytest.mark.parametrize("timestamp", [True, None, "12"])
def test_invalid_proposal_timestamp_rolls_back_instead_of_defaulting(
    tmp_path: Path,
    timestamp: object,
) -> None:
    from planner.core.contracts import PlannerError

    with current_navigation_database(tmp_path / "planner.db") as conn:
        conn.execute("BEGIN")
        add_ticket(conn, "t")
        conn.commit()
        before = snapshot(conn)
        conn.execute("BEGIN")
        conn.execute(
            "UPDATE tickets SET pending_proposal=? WHERE id='t'",
            (
                json.dumps(
                    {
                        "field": "implementation",
                        "body": "body",
                        "proposed_by": "worker",
                        "created_at": timestamp,
                    }
                ),
            ),
        )
        with pytest.raises(PlannerError, match="corrupt ticket field state"):
            conn.commit()
        assert snapshot(conn) == before
        assert (
            conn.execute("SELECT pending_proposal FROM tickets WHERE id='t'").fetchone()[0] is None
        )
        conn.close()


def test_real_projection_failure_rolls_back_source_and_journal(tmp_path: Path) -> None:
    with current_navigation_database(tmp_path / "planner.db") as conn:
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
    with current_navigation_database(tmp_path / "planner.db") as conn:
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
    actual = {
        (record["kind"], record["id"]): record["value"]
        for record in historical_snapshot(conn)["records"]
    }
    assert actual["conversation", "historical"] == {
        "latest_sequence": 12,
        "owner_read_through_sequence": 4,
        "latest_agent_activity_sequence": 0,
        "latest_agent_activity_at": None,
        "error_acknowledged_through_sequence": 0,
        "lifecycle_summary": {
            "latest_turn": None,
            "turn_status_counts": {"intent": 0, "running": 0},
            "request_status_counts": {"pending": 0, "answering": 0},
            "attempt_status_counts": {"intent": 0, "unknown": 0},
        },
    }
    assert actual["totals", "workspace"] == {"child_count": 0, "distinct_conversation_count": 0}
    before = historical_snapshot(conn)
    conn.close()
    conn = connect(str(path))
    assert historical_snapshot(conn) == before
    conn.close()


def test_snapshot_is_consistent_while_another_connection_commits(tmp_path: Path) -> None:
    with current_navigation_database(tmp_path / "planner.db") as conn:
        add_conversation(conn)
        reader = sqlite3.connect(
            (tmp_path / "planner.db").as_uri() + "?mode=ro", uri=True, isolation_level=None
        )
        reader.execute("PRAGMA query_only=ON")
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
    with current_navigation_database(tmp_path / "planner.db") as conn:
        add_conversation(conn)
        baseline = historical_snapshot(conn)
    # A new bootstrap lease performs the physical second open before installation.
    with prepared_navigation_database(tmp_path / "planner.db") as conn:
        assert historical_snapshot(conn) == baseline


def test_two_ticket_aliases_count_one_conversation_per_parent(tmp_path: Path) -> None:
    with current_navigation_database(tmp_path / "planner.db") as conn:
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

    with current_navigation_database(tmp_path / "planner.db") as conn:
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
        assert conversation["value"]["lifecycle_summary"]["attempt_status_counts"]["unknown"] == 1
        assert conversation["value"]["latest_sequence"] == 2
        conn.close()


def test_replace_unique_collision_deletes_old_identity(tmp_path: Path) -> None:
    with current_navigation_database(tmp_path / "planner.db") as conn:
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
        assert actual["project", "new"] == {"name": "Replacement"}
        delta = journal(conn, baseline["epoch"], baseline["revision"])["batches"][0]
        assert {"kind": "project", "id": "old"} in delta["deletes"]
        assert actual["totals", "workspace"] == {"child_count": 0, "distinct_conversation_count": 0}
        conn.close()


def test_project_rename_and_private_timestamps_avoid_ticket_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.navigation import data as navigation_data

    with current_navigation_database(tmp_path / "planner.db") as conn:
        conn.execute("BEGIN")
        add_parent(conn, "a")
        for index in range(5):
            add_ticket(conn, f"t{index}", "a")
        conn.commit()
        original = navigation_data._derive
        derived: list[tuple[str, str]] = []

        def capture(
            connection: sqlite3.Connection,
            root: tuple[str, str],
            definitions: dict[str, WorkerTypeDefinition] | None = None,
        ) -> tuple[list[NavigationRecord], set[tuple[str, str]]]:
            derived.append(root)
            return original(connection, root, definitions)

        monkeypatch.setattr(navigation_data, "_derive", capture)
        baseline = snapshot(conn)
        conn.execute("UPDATE projects SET name='Renamed Synthetic' WHERE id='project_panels'")
        assert derived == [("project", "project_panels")]
        assert values(conn)["project", "project_panels"]["name"] == "Renamed Synthetic"
        assert (
            len(journal(conn, baseline["epoch"], baseline["revision"])["batches"][0]["upserts"])
            == 1
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM navigation_dependencies "
                "WHERE source_kind='projects' AND kind='ticket'"
            ).fetchone()[0]
            == 0
        )
        derived.clear()
        baseline = snapshot(conn)
        conn.execute("UPDATE projects SET updated_at=updated_at+1 WHERE id='project_panels'")
        conn.execute("UPDATE worker_types SET updated_at=updated_at+1 WHERE worker_type='coding'")
        conn.execute("UPDATE chief_settings SET employee_launch_model='private-change'")
        assert derived == []
        assert snapshot(conn) == baseline
        conn.close()


def test_actual_worker_definition_change_updates_each_referencing_ticket_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from planner.navigation import data as navigation_data

    with current_navigation_database(tmp_path / "planner.db") as conn:
        conn.execute("BEGIN")
        add_ticket(conn, "first")
        add_ticket(conn, "second")
        conn.commit()
        original = navigation_data._derive
        derived: list[tuple[str, str]] = []

        def capture(
            connection: sqlite3.Connection,
            root: tuple[str, str],
            definitions: dict[str, WorkerTypeDefinition] | None = None,
        ) -> tuple[list[NavigationRecord], set[tuple[str, str]]]:
            derived.append(root)
            return original(connection, root, definitions)

        monkeypatch.setattr(navigation_data, "_derive", capture)
        definition = json.loads(
            conn.execute(
                "SELECT definition_json FROM worker_types WHERE worker_type='coding'"
            ).fetchone()[0]
        )
        next(stage for stage in definition["stages"] if stage["id"] == "needs_implementation")[
            "ownership_mode"
        ] = "user"
        conn.execute(
            "UPDATE worker_types SET definition_json=? WHERE worker_type='coding'",
            (json.dumps(definition),),
        )
        assert sorted(derived) == [("ticket", "first"), ("ticket", "second")]
        actual = values(conn)
        assert actual["ticket", "first"]["stage_ownership"] == "user"
        assert actual["ticket", "second"]["stage_ownership"] == "user"
        conn.execute("BEGIN")
        add_ticket(conn, "terminal")
        conn.execute("UPDATE tickets SET stage='done' WHERE id='terminal'")
        conn.commit()
        assert values(conn)["ticket", "terminal"]["gating_field"] is None
        before = snapshot(conn)
        derived.clear()
        previous_definition = json.dumps(definition)
        # Swap only gates. The definition stays valid with one stage per field.
        implementation = next(
            stage for stage in definition["stages"] if stage["id"] == "needs_implementation"
        )
        consequences = next(
            stage for stage in definition["stages"] if stage["id"] == "needs_consequences"
        )
        implementation["gating_field"], consequences["gating_field"] = (
            consequences["gating_field"],
            implementation["gating_field"],
        )
        conn.execute(
            "UPDATE worker_types SET definition_json=? WHERE worker_type='coding'",
            (json.dumps(definition),),
        )
        assert sorted(derived) == [
            ("ticket", "first"),
            ("ticket", "second"),
            ("ticket", "terminal"),
        ]
        current = snapshot(conn)
        after = values(conn)
        for identity in ("first", "second"):
            assert after["ticket", identity]["gating_field"] == "consequences"
            assert after["ticket", identity]["stage_ownership"] == "user"
            assert after["ticket", identity]["ticket_status"] == "empty"
        batches = journal(conn, before["epoch"], before["revision"])["batches"]
        assert len(batches) == 1
        assert [(record["kind"], record["id"]) for record in batches[0]["upserts"]] == [
            ("ticket", "first"),
            ("ticket", "second"),
        ]
        assert batches[0]["deletes"] == []
        conn.execute("BEGIN")
        conn.execute(
            "UPDATE worker_types SET definition_json=? WHERE worker_type='coding'",
            (previous_definition,),
        )
        conn.rollback()
        assert snapshot(conn) == current
        conn.execute("BEGIN")
        navigation_data.rebuild(conn)
        conn.commit()
        assert snapshot(conn)["records"] == current["records"]
        conn.close()


def test_isolated_old_head_restore_discards_later_fixture_commit(tmp_path: Path) -> None:
    """Fixture restoration discards later writes, not lossless production rollback."""
    from contextlib import closing

    from alembic import command

    from planner.core.db import _alembic_config, _migration_engine

    source_path = tmp_path / "before-upgrade.db"
    backup_path = tmp_path / "consistent-backup.db"
    restored_path = tmp_path / "restored-old-head.db"
    engine = _migration_engine(str(source_path), 1000)
    with engine.begin() as connection:
        command.upgrade(_alembic_config(connection), "durable_conversation_facts")
        connection.exec_driver_sql(
            "INSERT INTO projects(id,name,created_at,updated_at) "
            "VALUES('fixture-project','Before upgrade',0,0)"
        )
        connection.exec_driver_sql(
            "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,"
            "created_at,latest_sequence,owner_read_through_sequence) "
            "VALUES('fixture-conversation','codex','/synthetic','full',0,11,3)"
        )
    engine.dispose()
    # Only this synthetic source is opened, with explicit SQLite read-only mode.
    with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(backup_path)) as backup:
            source.backup(backup)
    candidate = connect(str(source_path))
    create_schema(candidate)
    candidate.execute("BEGIN")
    candidate.execute("UPDATE projects SET name='Later fixture commit' WHERE id='fixture-project'")
    candidate.execute(
        "UPDATE conversations SET latest_sequence=19 WHERE conversation_id='fixture-conversation'"
    )
    candidate.commit()
    assert (
        next(
            record["value"]["name"]
            for record in historical_snapshot(candidate)["records"]
            if record["kind"] == "project" and record["id"] == "fixture-project"
        )
        == "Later fixture commit"
    )
    candidate.close()
    # Restore to another isolated target after every candidate runtime handle closes.
    with closing(sqlite3.connect(backup_path.as_uri() + "?mode=ro", uri=True)) as backup:
        with closing(sqlite3.connect(restored_path)) as restored:
            backup.backup(restored)
    old_head_engine = _migration_engine(str(restored_path), 1000)
    with old_head_engine.begin() as connection:
        command.upgrade(_alembic_config(connection), "durable_conversation_facts")
        assert (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
            == "durable_conversation_facts"
        )
        assert (
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'navigation_%'"
            ).scalar()
            == 0
        )
        assert (
            connection.exec_driver_sql(
                "SELECT name FROM projects WHERE id='fixture-project'"
            ).scalar()
            == "Before upgrade"
        )
        assert connection.exec_driver_sql(
            "SELECT latest_sequence,owner_read_through_sequence FROM conversations "
            "WHERE conversation_id='fixture-conversation'"
        ).one() == (11, 3)
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    old_head_engine.dispose()

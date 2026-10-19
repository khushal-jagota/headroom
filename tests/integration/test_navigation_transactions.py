"""All fixtures use synthetic SQLite files in pytest temporary directories."""

import json
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest
from tests.support.navigation_current import (
    current_navigation_database,
)

from planner.navigation import journal, rebuild, snapshot
from planner.navigation.contracts import NavigationRecord
from planner.worker_types.contracts import WorkerTypeDefinition


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


def add_ticket(conn: sqlite3.Connection, identity: str, parent: str | None = None) -> None:
    conn.execute(
        "INSERT INTO tickets(id,title,worker_type,employee_backend,ceiling,field_values,"
        "created_at,updated_at,stage,orchestrator_kind,sprint_item_id) "
        "VALUES(?,?,'coding','codex','needs_implementation','{}',0,0,"
        "'needs_implementation',?,?)",
        (identity, identity, "chief" if parent is None else "sprint_item", parent),
    )


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
            ticket_owners: dict[str, dict[str, dict[str, Any]]] | None = None,
        ) -> tuple[list[NavigationRecord], set[tuple[str, str]]]:
            derived.append(root)
            return original(connection, root, definitions, ticket_owners)

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


def projection_tables(conn: sqlite3.Connection) -> dict[str, list[tuple[Any, ...]]]:
    return {
        table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1,2,3")]
        for table in ("navigation_records", "navigation_root_records", "navigation_dependencies")
    }


def assert_rebuild_preserves_projection(conn: sqlite3.Connection) -> None:
    before = projection_tables(conn)
    # Incremental deletion retains dependencies for now-empty roots. Compare the
    # dependencies of roots that still own records; rebuild drops obsolete roots.
    live_roots = {(row[0], row[1]) for row in before["navigation_root_records"]}
    before["navigation_dependencies"] = [
        row for row in before["navigation_dependencies"] if (row[2], row[3]) in live_roots
    ]
    conn.execute("BEGIN")
    rebuild(conn)
    assert projection_tables(conn) == before
    conn.commit()


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


def test_rebuild_refreshes_ownership_within_transaction_and_preserves_incremental_fallback(
    tmp_path: Path,
) -> None:
    from planner.navigation import data

    with current_navigation_database(tmp_path / "planner.db") as conn:
        for identity in ("old", "current", "unowned"):
            add_conversation(conn, identity)
        conn.execute("BEGIN")
        add_parent(conn, "a")
        add_parent(conn, "b")
        add_ticket(conn, "t", "a")
        conn.execute("UPDATE tickets SET conversation_id='old' WHERE id='t'")
        conn.execute("INSERT INTO ticket_conversations VALUES('old','t')")
        conn.execute(
            "UPDATE conversations SET identity_environment_variables=? WHERE conversation_id='old'",
            ('[["PLAN_ACTOR","sprint_item_supervisor"],["PLAN_SPRINT_ITEM_ID","a"]]',),
        )
        conn.execute("INSERT INTO agents VALUES('sprint_item_supervisor_a','old')")
        rebuild(conn)
        first = values(conn)
        assert first["address", '["ticket","t","old"]']["parent_id"] == "a"
        conn.execute("UPDATE tickets SET sprint_item_id='b',conversation_id='current' WHERE id='t'")
        add_ticket(conn, "shared")
        conn.execute("UPDATE tickets SET conversation_id='current' WHERE id='shared'")
        rebuild(conn)
        assert conn.in_transaction
        actual = values(conn)
        assert actual["address", '["ticket","t","old"]'] == {
            "owner_kind": "ticket",
            "owner_id": "t",
            "conversation_id": "old",
            "current": False,
            "parent_kind": "orchestrator",
            "parent_id": "b",
        }
        assert actual["address", '["ticket","t","current"]']["current"] is True
        assert actual["address", '["ticket","shared","current"]']["parent_kind"] == "chief"
        assert actual["totals", "orchestrator:a"]["distinct_conversation_count"] == 1
        assert actual["totals", "orchestrator:b"]["distinct_conversation_count"] == 2
        assert actual["address", '["orchestrator","a","old"]']["current"] is True
        assert not any(
            key[0] == "address" and value["conversation_id"] == "unowned"
            for key, value in actual.items()
        )
        # Exercise the no-lookup path directly, proving its query still supplies
        # complete records and dependencies for historical, shared and empty roots.
        stored = projection_tables(conn)
        traced: list[str] = []
        conn.set_trace_callback(traced.append)
        try:
            for identity in ("old", "current", "unowned"):
                records, deps = data._derive(conn, ("conversation", identity))
                expected_records = {
                    (row[2], row[3])
                    for row in stored["navigation_root_records"]
                    if row[:2] == ("conversation", identity)
                }
                assert {(record["kind"], record["id"]) for record in records} == expected_records
                assert all(
                    actual[record["kind"], record["id"]] == record["value"] for record in records
                )
                assert deps == {
                    (row[0], row[1])
                    for row in stored["navigation_dependencies"]
                    if row[2:] == ("conversation", identity)
                }
        finally:
            conn.set_trace_callback(None)
        assert sum("LEFT JOIN ticket_conversations tc" in query for query in traced) == 3
        conn.commit()
        assert_rebuild_preserves_projection(conn)
        conn.execute("BEGIN")
        conn.execute("DELETE FROM tickets WHERE id='t'")
        conn.execute(
            "UPDATE agents SET conversation_id=NULL WHERE agent_key='sprint_item_supervisor_a'"
        )
        conn.commit()
        assert_rebuild_preserves_projection(conn)
        actual = values(conn)
        assert ("ticket", "t") not in actual
        assert not any(
            key[0] == "address" and value["owner_kind"] == "ticket" and value["owner_id"] == "t"
            for key, value in actual.items()
        )
        assert actual["address", '["orchestrator","a","old"]']["current"] is False
        assert actual["totals", "orchestrator:a"]["distinct_conversation_count"] == 1
        assert actual["totals", "orchestrator:b"]["distinct_conversation_count"] == 0
        assert actual["totals", "workspace"]["distinct_conversation_count"] == 2


def test_rebuild_ticket_ownership_reads_are_constant_as_conversations_grow(tmp_path: Path) -> None:
    from planner.navigation import data

    with current_navigation_database(tmp_path / "planner.db") as conn:
        add_conversation(conn, "owned")
        conn.execute("BEGIN")
        add_ticket(conn, "t")
        conn.execute("UPDATE tickets SET conversation_id='owned' WHERE id='t'")
        conn.execute("INSERT INTO ticket_conversations VALUES('owned','t')")
        conn.commit()
        for population in (2, 40):
            conn.execute("BEGIN")
            for index in range(population):
                conn.execute(
                    "INSERT OR IGNORE INTO conversations("
                    "conversation_id,backend_key,workspace_folder,access,created_at) "
                    "VALUES(?,'codex','/synthetic','full',0)",
                    (f"unowned{index}",),
                )
            conn.commit()
            conn.execute("BEGIN")
            traced: list[str] = []
            conn.set_trace_callback(traced.append)
            try:
                data.rebuild(conn)
            finally:
                conn.set_trace_callback(None)
            assert (
                sum(
                    query == "SELECT id,sprint_item_id,conversation_id FROM tickets"
                    for query in traced
                )
                == 1
            )
            assert (
                sum(
                    query == "SELECT conversation_id,ticket_id FROM ticket_conversations"
                    for query in traced
                )
                == 1
            )
            assert not any("LEFT JOIN ticket_conversations tc" in query for query in traced)
            actual = values(conn)
            assert sum(key[0] == "conversation" for key in actual) == population + 1
            assert sum(key[0] == "address" for key in actual) == 1
            assert actual["totals", "workspace"]["distinct_conversation_count"] == 1
            conn.commit()

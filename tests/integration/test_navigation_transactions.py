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

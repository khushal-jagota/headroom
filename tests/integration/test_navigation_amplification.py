"""Performance invariants preserve the existing atomic source-derived projection."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from tests.integration.test_navigation_transactions import (
    add_conversation,
    add_ticket,
    values,
)
from tests.support.navigation_current import current_navigation_database

from planner.navigation import data, snapshot
from planner.navigation.contracts import NavigationRecord
from planner.worker_types.contracts import WorkerTypeDefinition
from planner.worker_types.store import definition_from_json


def test_unchanged_roots_do_not_rewrite_record_ownership_or_dependencies(tmp_path: Path) -> None:
    with current_navigation_database(tmp_path / "planner.db") as conn:
        add_conversation(conn)
        conn.execute("BEGIN")
        add_ticket(conn, "t")
        conn.execute("UPDATE tickets SET conversation_id='c' WHERE id='t'")
        conn.execute("INSERT INTO ticket_conversations VALUES('c','t')")
        conn.commit()
        before = snapshot(conn)
        conn.execute("BEGIN")
        writes = conn.total_changes
        assert data._put_root(conn, ("ticket", "t")) == ([], [])
        assert data._put_root(conn, ("conversation", "c")) == ([], [])
        assert conn.total_changes == writes
        conn.commit()
        assert snapshot(conn) == before
        conn.close()


def test_conversation_derivation_is_shared_by_closure_and_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with current_navigation_database(tmp_path / "planner.db") as conn:
        add_conversation(conn)
        conn.execute("BEGIN")
        add_ticket(conn, "t")
        conn.execute("UPDATE tickets SET conversation_id='c' WHERE id='t'")
        conn.commit()
        original = data._derive
        calls: list[tuple[str, str]] = []

        def capture(
            connection: sqlite3.Connection,
            root: tuple[str, str],
            definitions: dict[str, WorkerTypeDefinition] | None = None,
        ) -> tuple[list[NavigationRecord], set[tuple[str, str]]]:
            calls.append(root)
            return original(connection, root, definitions)

        monkeypatch.setattr(data, "_derive", capture)
        conn.execute("UPDATE conversations SET latest_sequence=7 WHERE conversation_id='c'")
        assert calls.count(("conversation", "c")) == 1
        actual = values(conn)
        assert actual["conversation", "c"]["latest_sequence"] == 7
        assert actual["totals", "chief:chief"]["distinct_conversation_count"] == 1
        conn.close()


def test_worker_definition_decode_cache_is_local_to_each_final_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with current_navigation_database(tmp_path / "planner.db") as conn:
        original = definition_from_json
        calls: list[str] = []

        def decode(raw: str) -> WorkerTypeDefinition:
            calls.append(raw)
            return original(raw)

        monkeypatch.setattr(data, "definition_from_json", decode)
        conn.execute("BEGIN")
        for index in range(5):
            add_ticket(conn, f"t{index}")
        conn.commit()
        assert len(calls) == 1
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
        assert len(calls) == 2
        actual = values(conn)
        assert all(actual["ticket", f"t{index}"]["stage_ownership"] == "user" for index in range(5))
        conn.execute("BEGIN")
        data.rebuild(conn)
        conn.commit()
        assert len(calls) == 3
        assert values(conn) == actual
        conn.close()

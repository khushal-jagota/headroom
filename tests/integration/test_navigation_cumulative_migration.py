"""Pre-lifecycle synthetic history through both branches and actual Signal install.

The distinct gap is cumulative ordering/backfill, not individual migration behavior.
There is no server, provider process, real agent home or production database here.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, cast

import pytest
from alembic import command
from sqlalchemy import Engine
from tests.integration.navigation_staging_merge_support import historical_snapshot
from tests.support.navigation_current import (
    current_navigation_database,
    prepared_navigation_database,
)

from planner.conversation.contracts import PromptDeliveryMode, PromptDeliveryRefusalReason
from planner.conversation.events import (
    ConversationEventKind,
    ConversationEventPayload,
    ConversationTurnEnding,
    MessageToOwnerEventPayload,
    PromptDeliveryRefusedEventPayload,
    PromptEventPayload,
    TurnEndedEventPayload,
    conversation_event_payload_from_canonical_json,
    conversation_event_payload_to_canonical_json,
)
from planner.conversation.message_content import text_message_content
from planner.core.contracts import Principal, PrincipalKind
from planner.core.db import _alembic_config, _migration_engine
from planner.navigation import data, journal, snapshot
from planner.navigation.contracts import HistoryUnavailable
from planner.projects.data import seed_default_projects
from planner.worker_types.shipped import seed_chief_settings, seed_shipped_worker_types

NAVIGATION_BRANCH = (
    "durable_conversation_facts",
    "transactional_navigation",
    "compact_navigation_lifecycle",
    "navigation_delivery_proof",
)
SOURCE_BRANCH = (
    "recorded_native_usage",
    "native_usage_prices",
    "current_model_variants",
    "remove_ticket_retirement",
    "chief_recap",
    "remove_priority",
)


def _event_json(payload: ConversationEventPayload) -> str:
    # Keep deliberate legacy whitespace and Unicode, with domain-valid payloads.
    return json.dumps(
        json.loads(conversation_event_payload_to_canonical_json(payload)),
        indent=1,
        ensure_ascii=False,
    )


EVENTS = [
    (
        "current",
        1,
        "prompt",
        _event_json(
            PromptEventPayload(
                text_message_content("Synthetic — prompt"),
                "Owner",
                PromptDeliveryMode.queue,
                sender_message_id="fixture",
            )
        ),
        21,
    ),
    (
        "current",
        2,
        "message_to_owner",
        _event_json(
            MessageToOwnerEventPayload(
                text_message_content("Known message"),
                Principal(PrincipalKind.ticket, "held"),
                Principal(PrincipalKind.owner, "owner"),
                "Fixture Worker",
            )
        ),
        22,
    ),
    (
        "current",
        3,
        "turn_ended",
        _event_json(
            TurnEndedEventPayload(
                ConversationTurnEnding.failed,
                error_summary="Synthetic failure",
            )
        ),
        23,
    ),
    (
        "current",
        4,
        "prompt_delivery_refused",
        _event_json(
            PromptDeliveryRefusedEventPayload(
                text_message_content("Undelivered fixture message"),
                "Owner",
                PromptDeliveryMode.queue,
                PromptDeliveryRefusalReason.backend_did_not_start,
                sender_message_id="fixture-refused",
            )
        ),
        24,
    ),
    (
        "historical",
        1,
        "message_to_owner",
        _event_json(
            MessageToOwnerEventPayload(
                text_message_content("Already read"),
                Principal(PrincipalKind.ticket, "held"),
                Principal(PrincipalKind.owner, "owner"),
                "Fixture Worker",
            )
        ),
        25,
    ),
]
PROPOSAL = (
    '{ "field": "plan", "body": "Keep approval bytes", "proposed_by": "worker", "created_at": 31 }'
)


def _raw(connection: Any) -> sqlite3.Connection:
    return cast(sqlite3.Connection, connection.connection.driver_connection)


def _seed_pre_lifecycle(path: Path) -> Engine:
    engine = _migration_engine(str(path), 1000)
    with engine.begin() as connection:
        command.upgrade(_alembic_config(connection), "scheduled_ticket_locale")
        conn = _raw(connection)
        assert (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE name IN "
                "('conversation_lifecycle_facts','navigation_meta')"
            ).fetchall()
            == []
        )
        seed_default_projects(conn)
        seed_shipped_worker_types(conn)
        seed_chief_settings(conn)
        conn.execute(
            "INSERT INTO projects(id,name,priority,created_at,updated_at) "
            "VALUES('fixture-project','Fixture project','P1',11,12)"
        )
        conn.execute(
            "INSERT INTO sprint_items(id,title,project_id,priority,created_at,updated_at,"
            "supervisor_backend,supervisor_model) "
            "VALUES('parent','Fixture parent','fixture-project','P0',13,14,'codex','synthetic')"
        )
        conn.executemany(
            "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,"
            "created_at,latest_sequence,owner_read_through_sequence) "
            "VALUES(?,'codex',?,'full',15,?,?)",
            [
                ("current", str(path.parent / "workspace"), 4, 0),
                ("historical", str(path.parent / "workspace"), 1, 1),
            ],
        )
        conn.executemany("INSERT INTO conversation_events VALUES(?,?,?,?,?)", EVENTS)
        # Reintroduce only the legacy fields that the accepted source rewrite removes.
        definition = json.loads(
            conn.execute(
                "SELECT definition_json FROM worker_types WHERE worker_type='coding'"
            ).fetchone()[0]
        )
        definition["fields"].append({"id": "retirement", "label": "Retirement"})
        definition["stages"].append(
            {
                "id": "cancelled",
                "label": "Cancelled",
                "is_terminal": True,
                "gating_field": None,
                "ownership_mode": None,
            }
        )
        conn.execute(
            "UPDATE worker_types SET definition_json=? WHERE worker_type='coding'",
            (json.dumps(definition),),
        )
        for identity in ("held", "alias", "blocker"):
            conn.execute(
                "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
                "field_values,created_at,updated_at,project_id,sprint_item_id,conversation_id,"
                "orchestrator_kind) "
                "VALUES(?,?,'coding','codex','needs_plan','done',?,17,18,"
                "'fixture-project','parent',?,'sprint_item')",
                (
                    identity,
                    identity,
                    json.dumps({"brief": "Keep", "retirement": "Remove"}),
                    None if identity == "blocker" else "current",
                ),
            )
        conn.execute(
            "UPDATE tickets SET pending_proposal=?,pending_proposal_revision=7,"
            "worker_step_claim='out',worker_step_claim_revision=9,is_backlogged=1 "
            "WHERE id='held'",
            (PROPOSAL,),
        )
        conn.execute("INSERT INTO ticket_blocks VALUES('blocker','held')")
        conn.execute("INSERT INTO ticket_conversations VALUES('historical','held')")
    return engine


def _assert_canonical(conn: sqlite3.Connection) -> None:
    """Independent seed/rewrite oracle; never infer correctness from path equality."""
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT conversation_id,sequence,kind,payload,created_at FROM conversation_events "
            "ORDER BY conversation_id,sequence"
        )
    ] == EVENTS
    for _, _, kind, payload, _ in EVENTS:
        decoded = conversation_event_payload_from_canonical_json(
            ConversationEventKind(kind), payload
        )
        assert decoded.kind.value == kind
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT conversation_id,latest_sequence,owner_read_through_sequence "
            "FROM conversations ORDER BY conversation_id"
        )
    ] == [("current", 4, 0), ("historical", 1, 1)]
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT id,stage,ceiling,pending_proposal,pending_proposal_revision,"
            "worker_step_claim,worker_step_claim_revision,is_backlogged,conversation_id "
            "FROM tickets ORDER BY id"
        )
    ] == [
        ("alias", "needs_plan", "done", None, 0, "none", 0, 0, "current"),
        ("blocker", "needs_plan", "done", None, 0, "none", 0, 0, None),
        ("held", "needs_plan", "done", PROPOSAL, 7, "out", 9, 1, "current"),
    ]
    assert [
        json.loads(row[0]) for row in conn.execute("SELECT field_values FROM tickets ORDER BY id")
    ] == [{"brief": "Keep"}] * 3
    definition = json.loads(
        conn.execute(
            "SELECT definition_json FROM worker_types WHERE worker_type='coding'"
        ).fetchone()[0]
    )
    assert "retirement" not in {field["id"] for field in definition["fields"]}
    assert "cancelled" not in {stage["id"] for stage in definition["stages"]}
    assert [tuple(row) for row in conn.execute("SELECT * FROM ticket_blocks")] == [
        ("blocker", "held")
    ]
    assert [tuple(row) for row in conn.execute("SELECT * FROM ticket_conversations")] == [
        ("historical", "held")
    ]
    assert conn.execute("SELECT COUNT(*) FROM conversation_lifecycle_facts").fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def _assert_projection(conn: sqlite3.Connection, *, activated: bool) -> list[dict[str, Any]]:
    current = historical_snapshot(conn)
    assert current["schema_version"] == (6 if activated else 5)
    records = current["records"]
    values = {(record["kind"], record["id"]): record["value"] for record in records}
    assert values["project", "fixture-project"] == {"name": "Fixture project"}
    assert values["orchestrator", "parent"] == {
        "title": "Fixture parent",
        "recap": "",
        "recap_preview": "",
        "created_at": 13,
        "project_id": "fixture-project",
        "settled": False,
        "conversation_id": None,
    }
    held = values["ticket", "held"]
    assert held == {
        "title": "held",
        "recap": "",
        "recap_preview": "",
        "worker_type": "coding",
        "stage": "needs_plan",
        "ticket_status": "awaiting_approval",
        "blocked": True,
        "updated_at": 18,
        "created_at": 17,
        "gating_field": "plan",
        "worker_step_claim_out": True,
        "deadline": None,
        "project_id": "fixture-project",
        "orchestrator_kind": "sprint_item",
        "sprint_item_id": "parent",
        "conversation_id": "current",
        "is_backlogged": True,
        "ceiling": "done",
        "ceiling_holder": {"kind": "owner", "id": "owner"},
        "proposal_field": "plan",
        "proposal_revision": 7,
        "proposal_created_at": 31,
        "stage_ownership": "worker",
        "terminal": False,
        "locale": "vps",
    }
    assert values["totals", "workspace"] == {"child_count": 3, "distinct_conversation_count": 2}
    assert values["totals", "orchestrator:parent"] == {
        "child_count": 3,
        "distinct_conversation_count": 2,
    }
    addresses = [record["value"] for record in records if record["kind"] == "address"]
    assert sorted(
        (value["owner_id"], value["conversation_id"], value["current"]) for value in addresses
    ) == [
        ("alias", "current", True),
        ("held", "current", True),
        ("held", "historical", False),
    ]
    for identity, latest, read, unread, failed in (
        ("current", 4, 0, True, True),
        ("historical", 1, 1, False, False),
    ):
        expected: dict[str, Any] = {
            "latest_sequence": latest,
            "owner_read_through_sequence": read,
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
        if activated:
            expected["attention"] = {
                "current": {
                    "signal": {"working": False, "message_for_owner": unread},
                    "awaiting_answer": False,
                    "last_turn_failed": failed,
                },
                "durable": {"message_for_owner": unread, "last_turn_failed": failed},
            }
        assert values["conversation", identity] == expected
    return cast(list[dict[str, Any]], records)


def test_cumulative_branch_orders_install_reopen_restart_and_rebuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One bounded connected case covers the missing cumulative source history."""
    seed_root = tmp_path / "seed"
    seed_root.mkdir()
    seed_path = seed_root / "pre-lifecycle.db"
    _seed_pre_lifecycle(seed_path).dispose()
    by_order: list[list[dict[str, Any]]] = []
    real_rebuild = data.rebuild

    def fail_reconcile(conn: sqlite3.Connection) -> None:
        real_rebuild(conn)
        raise RuntimeError("cumulative reconciliation failure")

    for name, branches in (
        ("navigation-first", (NAVIGATION_BRANCH, SOURCE_BRANCH)),
        ("source-first", (SOURCE_BRANCH, NAVIGATION_BRANCH)),
    ):
        runtime = tmp_path / name
        runtime.mkdir()
        path = runtime / "synthetic.db"
        # Both paths start from the exact same committed synthetic database.
        with closing(sqlite3.connect(seed_path.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(path)) as target:
                source.backup(target)
        engine = _migration_engine(str(path), 1000)
        try:
            with engine.begin() as connection:
                for branch in branches:
                    for revision in branch:
                        command.upgrade(_alembic_config(connection), revision)
                conn = _raw(connection)
                assert {
                    row[0] for row in conn.execute("SELECT version_num FROM alembic_version")
                } == {
                    "navigation_delivery_proof",
                    "remove_priority",
                }
                _assert_canonical(conn)
                before = list(conn.iterdump())
            # This immediate checkpoint spans only the reconciliation transaction.
            with monkeypatch.context() as patch:
                patch.setattr(data, "rebuild", fail_reconcile)
                with pytest.raises(RuntimeError, match="cumulative reconciliation failure"):
                    with engine.begin() as connection:
                        command.upgrade(_alembic_config(connection), "reconcile_navigation_staging")
            with engine.begin() as connection:
                conn = _raw(connection)
                assert list(conn.iterdump()) == before
                for revision in (
                    "reconcile_navigation_staging",
                    "navigation_consumer_metadata",
                    "prepare_navigation_attention",
                    "navigation_current_workspace",
                ):
                    command.upgrade(_alembic_config(connection), revision)
                _assert_canonical(conn)
                _assert_projection(conn, activated=False)
                physical = historical_snapshot(conn)
                assert (
                    conn.execute("SELECT COUNT(*) FROM navigation_signal_installation").fetchone()[
                        0
                    ]
                    == 0
                )
                assert (
                    conn.execute("SELECT COUNT(*) FROM navigation_signal_publications").fetchone()[
                        0
                    ]
                    == 0
                )
        finally:
            engine.dispose()
        # Bootstrap preparation is distinct from initialization and complete install.
        with prepared_navigation_database(path) as conn:
            assert historical_snapshot(conn) == physical
        with current_navigation_database(path) as conn:
            _assert_canonical(conn)
            records = _assert_projection(conn, activated=True)
            installed = snapshot(conn)
            assert installed["semantics"] == "attention-knowledge-v6-recap"
            assert installed["epoch"] != physical["epoch"]
            assert installed["scope"] == physical["scope"]
            assert installed["revision"] == "0"
            by_order.append(records)
        with prepared_navigation_database(path) as conn:
            assert historical_snapshot(conn)["records"] == installed["records"]
            assert historical_snapshot(conn)["epoch"] == installed["epoch"]
            assert historical_snapshot(conn)["revision"] == installed["revision"]
        with current_navigation_database(path) as conn:
            restarted = snapshot(conn)
            assert restarted["epoch"] != installed["epoch"]
            assert restarted["scope"] == installed["scope"]
            assert restarted["records"] == installed["records"]
            conn.execute("BEGIN")
            data.rebuild(conn)
            conn.commit()
            rebuilt = snapshot(conn)
            assert rebuilt["records"] == restarted["records"]
            assert rebuilt["scope"] == restarted["scope"]
            assert rebuilt["epoch"] != restarted["epoch"]
            assert rebuilt["revision"] == "0"
            with pytest.raises(HistoryUnavailable):
                journal(conn, restarted["epoch"], restarted["revision"])
            _assert_canonical(conn)
    # No time/identity scrubbing: records contain only stable seeded source values.
    assert by_order[0] == by_order[1]

"""Use production runtime admission to import evidence before navigation publishes."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from planner.conversation.deploy_interruptions.contracts import CapturedTurn, CaptureManifest
from planner.conversation.deploy_interruptions.data import read_summary
from planner.conversation.deploy_interruptions.files import write_manifest
from planner.conversation.runtime_composition import (
    SignalRuntimeComposition,
    bind_admission,
    transaction_control,
)
from planner.conversation.storage import ConversationStore
from planner.core.db import connect, create_schema


def test_boot_import_uses_admission_exact_claim_and_publishes_no_runtime_turn(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime.db"
    conn = connect(str(path))
    create_schema(conn)
    conn.execute("INSERT INTO projects(id,name,created_at,updated_at) VALUES ('p','Project',0,0)")
    conn.execute(
        "INSERT INTO managers(id,title,project_id,manager_backend,manager_model,"
        "created_at,updated_at) VALUES ('m','Manager','p','codex','model',0,0)"
    )
    for identity in ("current", "stale_claim", "stale_link"):
        conn.execute(
            "INSERT INTO "
            "conversations(conversation_id,backend_key,workspace_folder,access,created_a"
            "t) VALUES (?,'codex','/synthetic','full',0)",
            (identity,),
        )
        conn.execute(
            "INSERT INTO conversation_lifecycle_facts VALUES "
            "(?,'turn','1',1,'turn','running',NULL)",
            (identity,),
        )
        conn.execute(
            "INSERT INTO conversation_turn_occurrences VALUES (?,1,?,'old-epoch')",
            (identity, identity + "-nonce"),
        )
        conn.execute(
            "INSERT INTO "
            "tickets(id,title,worker_type,employee_backend,stage,ceiling,field_values,cr"
            "eated_at,updated_at,conversation_id,worker_step_claim,worker_step_claim_rev"
            "ision) VALUES "
            "(?,?,'coding','codex','needs_implementation','needs_implementation','{}',0,"
            "0,?,'out',?)",
            (
                identity,
                identity,
                "different" if identity == "stale_link" else identity,
                8 if identity == "stale_claim" else 7,
            ),
        )
    conn.execute("UPDATE tickets SET manager_kind='manager',manager_id='m'")
    conn.execute("INSERT INTO conversation_runtime_epoch VALUES (1,'old-epoch')")
    conn.commit()
    conn.close()
    write_manifest(
        path,
        CaptureManifest(
            "release",
            "stop",
            123,
            "captured",
            None,
            tuple(
                CapturedTurn(
                    identity,
                    1,
                    identity + "-nonce",
                    "old-epoch",
                    "running",
                    identity,
                    "ticket",
                    identity,
                    7,
                    "out",
                )
                for identity in ("current", "stale_claim", "stale_link")
            ),
        ),
    )
    composition = SignalRuntimeComposition(str(path), 1000)
    composition.bootstrap()
    try:
        store = ConversationStore(
            str(path),
            signal_coordinator=composition.coordinator,
            bind_signal_admission=bind_admission,
            transaction_control_guard=transaction_control,
            runtime_reconciliation_admission=composition.initialization,
        )
        store.claim_runtime(existing_ownership=composition.ownership)
        composition.coordinator.bind_runtime(lambda identity: None)
        composition.initialize()
        attachment = composition.coordinator.attach_signal_publication(
            install=lambda conn, snapshot: None,
            participate=lambda conn, batch: None,
            unavailable=lambda reason: None,
        )
        with closing(composition.connect()) as result:
            states = {
                str(r[0]): tuple(r[1:])
                for r in result.execute(
                    "SELECT id,worker_step_claim,worker_step_claim_revision FROM tickets"
                )
            }
            assert states == {
                "current": ("errored", 8),
                "stale_claim": ("out", 8),
                "stale_link": ("out", 7),
            }
            assert result.execute("SELECT COUNT(*) FROM conversation_events").fetchone()[0] == 0
            assert result.execute("SELECT COUNT(*) FROM manager_wakes").fetchone()[0] == 0
            assert all(
                entry["recovery_state"] == "awaiting_action"
                for entry in read_summary(result)["accounts"][0]["entries"]
            )
            assert all(
                '"working":false' in row[0]
                for row in result.execute("SELECT value FROM signal_publication_values")
            )
            with closing(sqlite3.connect(path)) as unadmitted:
                with pytest.raises(sqlite3.OperationalError, match="signal_publication_admitted"):
                    unadmitted.execute("UPDATE deploy_interruptions SET recovery_state='resumed'")
                unadmitted.rollback()
        attachment.close()
    finally:
        composition.close()

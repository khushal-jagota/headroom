"""The executor_mailboxes revision on a fresh and on a staging-shaped database, and the
later drop of the WebSocket tables it copied from.

Risk: the switch-over loses the current Mac incarnation, so the new runtime's replayed
outbox items (which keep that incarnation through handoff.json) are fenced before its
first lease; or it keeps a replaced incarnation current; or it loses the recorded
refusal and contact time the Mac page shows; or the drop runs before the copy and loses
both. No test covers these revisions. Layer: real
migrations on a SQLite file. The staging shape is a database built to the staging head
(canonical_manager_identities) by the shipped migrations, with the WebSocket rows a live
staging database holds, then upgraded by create_schema like application startup.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

import pytest
from tests.support.migration_head import shipped_migration_head

from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import (
    BASELINE_REVISION,
    connect,
    create_schema,
    schema_migration_transaction,
    upgrade_schema_connection,
)
from planner.execution_configuration import PROTOCOL_VERSION
from planner.execution_identity import MAC_NEEDS_UPDATE, ExecutorIdentity
from planner.executor.logic.presence import judge_presence
from planner.executor.protocol import LeaseRequest, as_object
from planner.executor.vps_presence_store import (
    accept_lease_in_transaction,
    read_presence,
    require_current_incarnation,
)

NEW_TABLES = (
    "executor_incarnations",
    "mac_executor_presence",
    "executor_commands",
    "stall_findings",
    "stall_checker_heartbeat",
)
WEBSOCKET_TABLES = ("executor_connections", "mac_contact_status")
CURRENT = ExecutorIdentity(21, "current-release", 5, str(uuid4()), 4)
REPLACED = ExecutorIdentity(21, "older-release", 5, str(uuid4()), 9)
REFUSAL = {
    "reason": MAC_NEEDS_UPDATE,
    "peer_identity": as_object(ExecutorIdentity(20, "old", 5, str(uuid4()), 1)),
    "server_identity": as_object(ExecutorIdentity(21, "server", 5, "", 0)),
    "refused_at_ms": 777,
}


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _identity_json(identity: ExecutorIdentity) -> str:
    return json.dumps(as_object(identity), sort_keys=True, separators=(",", ":"))


def _staging_database(tmp_path: Path, refusal_json: str | None) -> str:
    path = str(tmp_path / "staging.db")
    with schema_migration_transaction(path) as migration:
        upgrade_schema_connection(migration, BASELINE_REVISION)
    with closing(connect(path)) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            BASELINE_REVISION
        )
        conn.executemany(
            "INSERT INTO executor_connections VALUES (?,?,?,?,?)",
            [
                (REPLACED.executor_incarnation, 9, _identity_json(REPLACED), "disconnected", 0),
                (CURRENT.executor_incarnation, 4, _identity_json(CURRENT), "ready", 1),
            ],
        )
        conn.execute("INSERT INTO mac_contact_status VALUES (1, 123456, ?)", (refusal_json,))
        conn.commit()
    return path


def test_fresh_database_has_the_mailbox_tables_and_no_presence(tmp_path: Path) -> None:
    with closing(connect(str(tmp_path / "fresh.db"))) as conn:
        create_schema(conn)
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            shipped_migration_head()
        )
        for table in NEW_TABLES:
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert not _tables(conn) & set(WEBSOCKET_TABLES)
        assert read_presence(conn) is None
        assert judge_presence(read_presence(conn), 1).presence == "never"
        # The stored lease is diagnostic: an unreadable one reads as none, never an error.
        conn.execute(
            "INSERT INTO mac_executor_presence(singleton,last_lease_at_ms,lease_json) "
            'VALUES (1,5,\'{"older":"shape"}\')'
        )
        presence = read_presence(conn)
        assert presence is not None
        assert (presence.lease, presence.last_lease_at_ms) == (None, 5)


@pytest.mark.parametrize("refusal", [None, REFUSAL])
def test_staging_upgrade_keeps_the_current_incarnation_contact_and_refusal(
    tmp_path: Path, refusal: dict[str, object] | None
) -> None:
    refusal_json = None if refusal is None else json.dumps(refusal)
    path = _staging_database(tmp_path, refusal_json)
    with closing(connect(path)) as conn:
        create_schema(conn)
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            shipped_migration_head()
        )
        incarnations = {
            row[0]: (row[1], row[2]) for row in conn.execute("SELECT * FROM executor_incarnations")
        }
        (migrated_at_ms, current_replaced), (replaced_first, replaced_at) = (
            incarnations[CURRENT.executor_incarnation],
            incarnations[REPLACED.executor_incarnation],
        )
        assert len(incarnations) == 2
        assert not _tables(conn) & set(WEBSOCKET_TABLES)
        assert current_replaced is None
        assert replaced_at == replaced_first == migrated_at_ms
        row = conn.execute(
            "SELECT identity_json,refusal_json,last_lease_at_ms,continuous_since_ms "
            "FROM mac_executor_presence"
        ).fetchone()
        assert "connection_generation" not in json.loads(row[0])
        assert row[1] == refusal_json
        assert (row[2], row[3]) == (None, None)
        presence = read_presence(conn)
        assert presence is not None
        assert presence.executor_incarnation == CURRENT.executor_incarnation
        assert presence.identity is not None
        assert presence.identity.application_revision == "current-release"
        assert presence.last_contact_at_ms == 123456
        judgement = judge_presence(presence, migrated_at_ms)
        if refusal is None:
            assert presence.refusal is None
            assert judgement.presence == "never"
        else:
            assert presence.refusal is not None
            assert presence.refusal.reason == MAC_NEEDS_UPDATE
            assert presence.refusal.peer_identity is not None
            assert presence.refusal.peer_identity.protocol_version == 20
            assert (judgement.presence, judgement.since_ms) == ("refused", 777)


def test_the_migrated_incarnation_passes_the_fence_until_a_new_process_leases(
    tmp_path: Path,
) -> None:
    """Why the incarnation is copied: the new runtime replays its items before leasing."""
    path = _staging_database(tmp_path, None)
    with closing(connect(path)) as conn:
        create_schema(conn)
    require_current_incarnation(path, CURRENT)
    with pytest.raises(PlannerError) as refused:
        require_current_incarnation(path, REPLACED)
    assert refused.value.code is ErrorCode.executor_incarnation_not_current

    successor = ExecutorIdentity(PROTOCOL_VERSION, "new-release", 5, str(uuid4()))
    lease = LeaseRequest(successor, 1, 0, 0, None, (), False, (), False)
    with closing(connect(path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        accepted = accept_lease_in_transaction(conn, lease, 2_000_000)
        conn.commit()
        assert accepted.new_incarnation
        assert accepted.replaced_incarnation == CURRENT.executor_incarnation
        assert accepted.current.continuous_since_ms == 2_000_000
        assert judge_presence(read_presence(conn), 2_000_001).presence == "present"
        assert [
            row[0]
            for row in conn.execute(
                "SELECT executor_incarnation FROM executor_incarnations "
                "WHERE replaced_at_ms IS NULL"
            )
        ] == [successor.executor_incarnation]
    require_current_incarnation(path, successor)
    with pytest.raises(PlannerError):
        require_current_incarnation(path, CURRENT)
    # The replaced process may not lease its way back in; it must exit.
    with closing(connect(path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(PlannerError) as replaced:
            accept_lease_in_transaction(
                conn, LeaseRequest(CURRENT, 1, 0, 0, None, (), False, (), False), 2_010_000
            )
        conn.rollback()
    assert replaced.value.code is ErrorCode.executor_incarnation_not_current

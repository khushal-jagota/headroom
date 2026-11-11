"""Recovery captures proven-dead children before restart settles their turn facts."""

import asyncio
from pathlib import Path

from tests.support.ticket_workflows import preset_workflow_json

from planner.conversation.child_custody import write_pending, write_retired
from planner.conversation.storage import ConversationStore
from planner.core.db import connect, create_schema


def test_crash_capture_keeps_unknown_owners_and_deduplicates_recovery(tmp_path: Path) -> None:
    path = tmp_path / "home.db"
    conn = connect(str(path))
    create_schema(conn)
    with conn:
        conn.execute("INSERT INTO conversation_runtime_epoch VALUES (1,'old-runtime')")
        for name in ("dead", "unknown", "stale"):
            conn.execute(
                "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,"
                "access,created_at) VALUES (?,'codex','/synthetic','full',0)",
                (name,),
            )
            conn.execute(
                "INSERT INTO conversation_lifecycle_facts VALUES "
                "(?,'turn','1',1,'turn','running',NULL)",
                (name,),
            )
            conn.execute(
                "INSERT INTO conversation_turn_occurrences VALUES (?,1,?,'old-runtime')",
                (name, name + "-nonce"),
            )
            conn.execute(
                "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
                "field_values,created_at,updated_at,conversation_id,worker_step_claim,"
                "worker_step_claim_revision,workflow_json) VALUES "
                "(?,?,'coding','codex','needs_implementation','needs_implementation','{}',"
                "0,0,?,'out',7,?)",
                (name, name, name, preset_workflow_json(conn)),
            )
        write_pending(conn, "dead", "exact-child")
        write_retired(conn, "dead", "exact-child")
        write_pending(conn, "stale", "previous-child")
        write_retired(conn, "stale", "previous-child")
        # A restored/current turn cannot borrow the previous occurrence's receipt.
        conn.execute(
            "UPDATE conversation_turn_occurrences SET occurrence_id='current-nonce' "
            "WHERE conversation_id='stale'"
        )
        conn.commit()
    store = ConversationStore(str(path))
    for _ in range(2):
        with store.claim_runtime():
            assert store.blocked_local_conversations == {"unknown", "stale"}
            recoveries = asyncio.run(store.select_deployment_recoveries())
            assert set(recoveries) == {"dead"}
    with connect(str(path)) as conn:
        assert conn.execute("SELECT count(*) FROM deploy_interruptions").fetchone()[0] == 1
        assert (
            conn.execute("SELECT worker_step_claim FROM tickets WHERE id='unknown'").fetchone()[0]
            == "out"
        )
        assert (
            conn.execute(
                "SELECT status FROM conversation_lifecycle_facts WHERE conversation_id='unknown'"
            ).fetchone()[0]
            == "running"
        )
        assert tuple(conn.execute(
            "SELECT worker_step_claim,worker_step_claim_revision FROM tickets WHERE id='stale'"
        ).fetchone()) == ("out", 7)
        assert conn.execute(
            "SELECT status FROM conversation_lifecycle_facts WHERE conversation_id='stale'"
        ).fetchone()[0] == "running"

    from tests.unit.test_conversation_system import _Harness, _run

    from planner.conversation.message_content import text_message_content

    async def refuse_replacement() -> None:
        harness = _Harness(path)
        try:
            await harness.system.send(
                "stale", text_message_content("new work"), sender_label="owner"
            )
            assert harness.spawned_conversation_ids == []
            assert harness.system.workspace_activity() == "unknown"
            with connect(str(path)) as db:
                assert tuple(db.execute(
                    "SELECT worker_step_claim,worker_step_claim_revision "
                    "FROM tickets WHERE id='stale'"
                ).fetchone()) == ("out", 7)
                assert db.execute(
                    "SELECT occurrence_id FROM conversation_child_custody "
                    "WHERE conversation_id='stale'"
                ).fetchone()[0] == "stale-nonce"
        finally:
            await harness.system.shutdown()

    _run(refuse_replacement)


def test_pre_dispatch_claim_release_uses_exact_committed_intent(tmp_path: Path) -> None:
    from planner.tickets import data as tickets_data

    path = tmp_path / "claims.db"
    conn = connect(str(path))
    create_schema(conn)
    for name in ("prepared", "dispatching", "newer"):
        conn.execute(
            "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
            "field_values,created_at,updated_at,workflow_json) VALUES "
            "(?,?,'coding','codex','needs_implementation','needs_implementation','{}',"
            "0,0,?)", (name, name, preset_workflow_json(conn)),
        )
    conn.commit()
    for name in ("prepared", "dispatching", "newer"):
        claimed = tickets_data.claim_ticket_for_worker_step(
            conn, name, readiness_check=lambda *args, **kwargs: True, now=1,
        )
        assert claimed is not None
        if name == "dispatching":
            tickets_data.mark_worker_step_dispatch_started(
                conn, name, claimed.worker_step_claim_revision,
            )
        elif name == "newer":
            conn.execute(
                "UPDATE tickets SET worker_step_claim_revision=worker_step_claim_revision+1 "
                "WHERE id=?", (name,),
            )
            conn.commit()
    conn.close()
    with ConversationStore(str(path)).claim_runtime():
        with connect(str(path)) as db:
            claims = dict(db.execute("SELECT id,worker_step_claim FROM tickets"))
            assert claims["prepared"] == "none"
            assert claims["dispatching"] == "out"
            assert claims["newer"] == "out"

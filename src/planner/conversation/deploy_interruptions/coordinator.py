"""Recovery through the ordinary conversation delivery boundary.

A deploy recovers every eligible conversation once, at startup. A Mac process restart
recovers the one conversation whose turn it interrupted, while the server keeps running.
"""

from __future__ import annotations

import asyncio
import logging

from planner.conversation.contracts import ConversationSystem, PromptDeliveryMode
from planner.conversation.deploy_interruptions.contracts import CaptureManifest
from planner.conversation.deploy_interruptions.data import import_captured_turns
from planner.conversation.deploy_interruptions.logic import recovery_message_identity
from planner.conversation.message_content import text_message_content
from planner.conversation.storage import ConversationStore
from planner.execution_identity import ChildReference

_LOG = logging.getLogger(__name__)

MAC_RESTART_CLAIM_ERROR_REASON = "interrupted by a Mac restart"


async def run_automatic_recovery(system: ConversationSystem, store: ConversationStore) -> None:
    # Startup only: a recovery prompt still at intent was lost with the previous process.
    await store.record_unknown_recovery_deliveries()
    await recover_conversations(system, store)


async def recover_conversations(
    system: ConversationSystem,
    store: ConversationStore,
    *,
    conversation_ids: tuple[str, ...] | None = None,
    mode: PromptDeliveryMode = PromptDeliveryMode.queue,
) -> None:
    """Send each eligible conversation its one recovery prompt, optionally only some.

    ``send_now`` puts the prompt ahead of messages already waiting in the conversation.
    """
    selected = await store.select_deployment_recoveries()
    if conversation_ids is not None:
        selected = {
            conversation_id: references
            for conversation_id, references in selected.items()
            if conversation_id in conversation_ids
        }

    async def resume(conversation_id: str) -> None:
        references = selected[conversation_id]
        # Do not manufacture activity over work already running on this boot.
        if await system.is_running(conversation_id):
            return
        identity = recovery_message_identity(tuple(ref.interruption_id for ref in references))
        try:
            await system.send(
                conversation_id,
                text_message_content(
                    "Inspect the interrupted work and continue the current authorized step."
                ),
                sender_label="Panels deployment recovery",
                mode=mode,
                sender_message_id=identity,
                record_refusal=True,
                recovery_references=references,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.exception("Automatic recovery failed for conversation %s", conversation_id)

    async with asyncio.TaskGroup() as group:
        for conversation_id in selected:
            group.create_task(resume(conversation_id))


def close_run_and_record_mac_process_interruption(
    db_path: str, reference: ChildReference, replaced_by: str, now_ms: int
) -> None:
    """Close the run of a replaced Mac process and record the turn it left unfinished, as
    a deploy records one, in one transaction.

    Committed together, a VPS crash either leaves the run open, so the drain is rebuilt at
    startup and comes back here, or leaves the recorded interruption, which startup
    recovery resumes. The turn is read after the close in the same transaction, so it is
    no longer taken for one the executor still owns.
    """
    from planner.core.db import connect
    from planner.environments.interruption_capture import read_unresolved_turns
    from planner.executor.store import ExecutorStore

    conversation_id = reference.conversation_id
    now = now_ms // 1000
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        ExecutorStore.close_child_in_transaction(conn, reference, now_ms)
        turns = read_unresolved_turns(conn, conversation_ids=(conversation_id,))
        if turns:
            import_captured_turns(
                conn,
                CaptureManifest(
                    f"mac-process:{replaced_by}",
                    f"mac-process:{replaced_by}:{conversation_id}",
                    now,
                    "captured",
                    None,
                    turns,
                ),
                now,
                claim_error_reason=MAC_RESTART_CLAIM_ERROR_REASON,
                ended_by="A Mac restart",
            )
        conn.commit()
    finally:
        conn.close()

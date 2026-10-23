"""One startup pass through the ordinary conversation delivery boundary."""

from __future__ import annotations

import asyncio
import logging

from planner.conversation.contracts import ConversationSystem
from planner.conversation.deploy_interruptions.logic import recovery_message_identity
from planner.conversation.message_content import text_message_content
from planner.conversation.storage import ConversationStore

_LOG = logging.getLogger(__name__)


async def run_automatic_recovery(system: ConversationSystem, store: ConversationStore) -> None:
    await store.record_unknown_recovery_deliveries()
    selected = await store.select_deployment_recoveries()

    async def resume(conversation_id: str) -> None:
        references = selected[conversation_id]
        # Do not manufacture activity over work already running on this boot.
        if await system.is_running(conversation_id):
            return
        identity = recovery_message_identity(tuple(ref.interruption_id for ref in references))
        try:
            await system.send_with_receipt(
                conversation_id,
                text_message_content(
                    "Inspect the interrupted work and continue the current authorized step."
                ),
                sender_label="Panels deployment recovery",
                sender_message_id=identity,
                reply_requested=False,
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

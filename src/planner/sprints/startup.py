"""Deliver a saved orchestrator's brief through the ordinary supervisor message door."""

from __future__ import annotations

import logging
import sqlite3
from typing import cast
from uuid import uuid4

from planner.conversation.api import delivery_fate_json
from planner.conversation.contracts import ConversationSystem
from planner.core.authctx import RequestContext
from planner.core.clock import Clock
from planner.core.contracts import Principal, PrincipalKind
from planner.message_delivery import service as message_delivery_service
from planner.runtime import conversation_start
from planner.sprints.contracts import OrchestratorStartupResult, SprintItem

logger = logging.getLogger(__name__)


async def start_created_orchestrator(
    conversations: ConversationSystem,
    conn: sqlite3.Connection,
    clock: Clock,
    ctx: RequestContext,
    item: SprintItem,
) -> OrchestratorStartupResult | None:
    """Keep creation successful when its separately attempted first message fails."""
    if not item.body.strip():
        return None
    try:
        delivered = await message_delivery_service.send_message(
            conversations,
            conn,
            clock,
            ctx,
            Principal(PrincipalKind.sprint_item, item.id),
            item.body,
            sender_message_id=str(uuid4()),
            retain_created_conversation=True,
        )
        return cast(
            OrchestratorStartupResult,
            {"conversation_id": delivered.conversation_id, **delivery_fate_json(delivered.fate)},
        )
    except Exception:
        logger.exception("Saved orchestrator %s but its initial brief failed to start", item.id)
        return {
            "conversation_id": conversation_start.read_agent_conversation(
                conn, item.supervisor_agent_key
            ),
            "fate": "failed",
            "error": "The orchestrator was saved, but startup did not complete. "
            "Inspect its conversation before you retry the brief.",
        }

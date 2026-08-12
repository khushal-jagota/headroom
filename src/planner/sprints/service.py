"""Coordinated Sprint Item actions that cross database and runtime boundaries."""

from __future__ import annotations

import asyncio
import sqlite3

from planner.conversation.contracts import ConversationSystem
from planner.files.lifecycle import (
    purge_quarantined_sprint_item_files,
    quarantine_sprint_item_files,
    restore_quarantined_sprint_item_files,
)
from planner.runtime import conversation_start
from planner.sprints import data as sprints_data
from planner.sprints.contracts import SprintItemDeletion
from planner.tickets.logic import admission

_SUPERVISOR_LIFECYCLE_LOCKS: dict[str, asyncio.Lock] = {}


def supervisor_lifecycle_lock(item_id: str) -> asyncio.Lock:
    lock = _SUPERVISOR_LIFECYCLE_LOCKS.get(item_id)
    if lock is None:
        lock = asyncio.Lock()
        _SUPERVISOR_LIFECYCLE_LOCKS[item_id] = lock
    return lock


async def delete_item(
    system: ConversationSystem,
    conn: sqlite3.Connection,
    item_id: str,
    *,
    actor: str,
) -> SprintItemDeletion:
    """Stop the supervisor and delete its childless item as one guarded action."""
    admission.require_direct_actor(actor, "delete_item")
    async with supervisor_lifecycle_lock(item_id):
        sprints_data._require_item_can_delete(conn, item_id)
        quarantined = None
        try:
            while True:
                item = sprints_data._require_item_can_delete(conn, item_id)
                conversation_id = conversation_start.read_agent_conversation(
                    conn, item.supervisor_agent_key
                )
                if conversation_id is not None:
                    await system.kill(conversation_id)
                    with conn:
                        conn.execute(
                            "UPDATE agents SET conversation_id=NULL WHERE agent_key=? "
                            "AND conversation_id=?",
                            (item.supervisor_agent_key, conversation_id),
                        )

                conn.execute("BEGIN IMMEDIATE")
                item = sprints_data._require_item_can_delete(conn, item_id)
                late_conversation_id = conversation_start.read_agent_conversation(
                    conn, item.supervisor_agent_key
                )
                if late_conversation_id is not None:
                    conn.execute("ROLLBACK")
                    continue
                quarantined = quarantine_sprint_item_files(conn, item_id)
                deleted = sprints_data._delete_item_rows(conn, item_id)
                conn.execute("COMMIT")
                break
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            restore_quarantined_sprint_item_files(quarantined)
            raise
        purge_quarantined_sprint_item_files(quarantined)
        return deleted

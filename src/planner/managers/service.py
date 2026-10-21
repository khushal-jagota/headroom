"""Coordinated Manager actions that cross database and runtime boundaries."""

from __future__ import annotations

import asyncio
import sqlite3

from planner.conversation.contracts import ConversationSystem
from planner.core import authority
from planner.core.authority import require_above
from planner.core.contracts import Principal
from planner.files.lifecycle import (
    purge_quarantined_manager_files,
    quarantine_manager_files,
    restore_quarantined_manager_files,
)
from planner.managers import data as managers_data
from planner.managers.contracts import ManagerDeletion
from planner.runtime import conversation_start

_MANAGER_LIFECYCLE_LOCKS: dict[str, asyncio.Lock] = {}


def manager_lifecycle_lock(item_id: str) -> asyncio.Lock:
    lock = _MANAGER_LIFECYCLE_LOCKS.get(item_id)
    if lock is None:
        lock = asyncio.Lock()
        _MANAGER_LIFECYCLE_LOCKS[item_id] = lock
    return lock


async def delete_manager(
    system: ConversationSystem,
    conn: sqlite3.Connection,
    item_id: str,
    *,
    principal: Principal,
) -> ManagerDeletion:
    """Stop the Manager and delete its childless item as one guarded action."""
    require_above(conn, principal, authority.manager(item_id))
    async with manager_lifecycle_lock(item_id):
        managers_data._require_manager_can_delete(conn, item_id)
        quarantined = None
        try:
            while True:
                item = managers_data._require_manager_can_delete(conn, item_id)
                conversation_id = conversation_start.read_agent_conversation(
                    conn, item.manager_agent_key
                )
                if conversation_id is not None:
                    await system.kill(conversation_id)
                    with conn:
                        conn.execute(
                            "UPDATE agents SET conversation_id=NULL WHERE agent_key=? "
                            "AND conversation_id=?",
                            (item.manager_agent_key, conversation_id),
                        )

                conn.execute("BEGIN IMMEDIATE")
                item = managers_data._require_manager_can_delete(conn, item_id)
                late_conversation_id = conversation_start.read_agent_conversation(
                    conn, item.manager_agent_key
                )
                if late_conversation_id is not None:
                    conn.execute("ROLLBACK")
                    continue
                quarantined = quarantine_manager_files(conn, item_id)
                deleted = managers_data._delete_manager_rows(conn, item_id)
                conn.execute("COMMIT")
                break
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            restore_quarantined_manager_files(quarantined)
            raise
        purge_quarantined_manager_files(quarantined)
        return deleted

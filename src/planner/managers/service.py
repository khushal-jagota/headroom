"""Coordinated Manager actions that cross database and runtime boundaries."""

from __future__ import annotations

import asyncio
import sqlite3

from fastapi.concurrency import run_in_threadpool

from planner.conversation.contracts import ConversationSystem
from planner.core.contracts import Principal
from planner.files.lifecycle import (
    QuarantinedManagerFiles,
    purge_quarantined_manager_files,
    quarantine_manager_files,
    restore_quarantined_manager_files,
)
from planner.managers import data as managers_data
from planner.managers.contracts import Manager, ManagerDeletion
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
    """Stop the Manager and delete its childless item as one guarded action.

    The lifecycle lock and the kill belong to the event loop. Every database and file step
    runs on a worker thread, so the deleting write waits on the SQLite lock there.
    """
    async with manager_lifecycle_lock(item_id):
        while True:
            item, conversation_id = await run_in_threadpool(
                _deletable_manager_and_its_conversation, conn, item_id
            )
            if conversation_id is not None:
                await system.kill(conversation_id)
                await run_in_threadpool(
                    _unlink_killed_conversation, conn, item.manager_agent_key, conversation_id
                )
            outcome = await run_in_threadpool(_delete_manager_with_no_conversation, conn, item_id)
            if outcome is not None:
                deleted, quarantined = outcome
                await run_in_threadpool(purge_quarantined_manager_files, quarantined)
                return deleted


def _deletable_manager_and_its_conversation(
    conn: sqlite3.Connection, item_id: str
) -> tuple[Manager, str | None]:
    item = managers_data._require_manager_can_delete(conn, item_id)
    return item, conversation_start.read_agent_conversation(conn, item.manager_agent_key)


def _unlink_killed_conversation(
    conn: sqlite3.Connection, agent_key: str, conversation_id: str
) -> None:
    conn.execute(
        "UPDATE agents SET conversation_id=NULL WHERE agent_key=? AND conversation_id=?",
        (agent_key, conversation_id),
    )


def _delete_manager_with_no_conversation(
    conn: sqlite3.Connection, item_id: str
) -> tuple[ManagerDeletion, QuarantinedManagerFiles | None] | None:
    """Delete the Manager in one transaction, or return None if a conversation linked late."""
    quarantined = None
    conn.execute("BEGIN IMMEDIATE")
    try:
        item = managers_data._require_manager_can_delete(conn, item_id)
        if conversation_start.read_agent_conversation(conn, item.manager_agent_key) is not None:
            conn.execute("ROLLBACK")
            return None
        quarantined = quarantine_manager_files(conn, item_id)
        deleted = managers_data._delete_manager_rows(conn, item_id)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        restore_quarantined_manager_files(quarantined)
        raise
    return deleted, quarantined

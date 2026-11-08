"""Bring stored conversation views up to the current projection version after startup.

A read refuses a view at any other ``VIEW_PROJECTION_VERSION``. So a change that bumps the
version needs nothing more: the running server finds every view that is not current and
rebuilds it from its events here, one conversation in each write transaction.

The rebuild does not hold startup. Until a view is rebuilt, its reads answer 409.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import closing

from planner.conversation.views.contracts import VIEW_PROJECTION_VERSION
from planner.conversation.views.writer import rebuild_conversation_view
from planner.conversation.workspace_admission import WorkspaceAdmission
from planner.core.db import commit_without_change_signal, connect

logger = logging.getLogger(__name__)

# A conversation with events and no current view. A missing view row counts, because the
# read refuses that too.
_OUT_OF_DATE = (
    "SELECT c.conversation_id FROM conversations c "
    "LEFT JOIN conversation_view_state v ON v.conversation_id=c.conversation_id "
    "WHERE c.latest_sequence>0 AND (v.conversation_id IS NULL OR v.projection_version!=?)"
)


def out_of_date_conversation_ids(db_path: str, busy_timeout_ms: int) -> list[str]:
    with closing(connect(db_path, busy_timeout_ms)) as conn:
        return [
            row[0]
            for row in conn.execute(
                _OUT_OF_DATE + " ORDER BY c.latest_sequence", (VIEW_PROJECTION_VERSION,)
            )
        ]


def rebuild_one_conversation_view(db_path: str, busy_timeout_ms: int, conversation_id: str) -> None:
    """Rebuild one view in its own write transaction, if it is still out of date.

    The transaction takes the write lock before it reads, so a live event for this
    conversation commits either wholly before the rebuild or wholly after it.
    """
    with closing(connect(db_path, busy_timeout_ms)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute(
                _OUT_OF_DATE + " AND c.conversation_id=?",
                (VIEW_PROJECTION_VERSION, conversation_id),
            ).fetchone():
                rebuild_conversation_view(conn, conversation_id)
            # Only an open conversation shows these rows, and its reader retries on its own.
            commit_without_change_signal(conn)
        except BaseException:
            conn.rollback()
            raise


async def rebuild_out_of_date_views(
    db_path: str, busy_timeout_ms: int, admission: WorkspaceAdmission
) -> None:
    """Rebuild every out-of-date view off the event loop, then end.

    After each conversation the rebuild waits as long as that write took, so live writes
    get at least half of the time. Each conversation is one admitted operation, so an
    update can still find the server idle between two of them.
    """
    conversation_ids = await asyncio.to_thread(
        out_of_date_conversation_ids, db_path, busy_timeout_ms
    )
    if not conversation_ids:
        return
    logger.info("Rebuilding %d out-of-date conversation views", len(conversation_ids))
    failed = 0
    for conversation_id in conversation_ids:
        with admission.operation() as admitted:
            if not admitted:
                return
            started = time.monotonic()
            try:
                await asyncio.to_thread(
                    rebuild_one_conversation_view, db_path, busy_timeout_ms, conversation_id
                )
            except Exception:
                failed += 1
                logger.exception("Conversation view rebuild failed for %s", conversation_id)
            took = time.monotonic() - started
        await asyncio.sleep(took)
    logger.info(
        "Rebuilt out-of-date conversation views: %d rebuilt, %d failed",
        len(conversation_ids) - failed,
        failed,
    )

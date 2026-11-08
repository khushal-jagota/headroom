"""Bring stored conversation views up to the current projection version after startup.

A read refuses a view at any other ``VIEW_PROJECTION_VERSION``. So a change that bumps the
version needs nothing more: the running server finds every view that is not current and
rebuilds it from its events here, one conversation at a time.

A conversation's view is computed from a plain read into private temporary tables, without
the write lock. The write lock is held only to check that nothing changed and to copy the
rows in, so live writes never wait behind the computation.

The rebuild does not hold startup. Until a view is rebuilt, its reads answer 409.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import time
from contextlib import closing

from planner.conversation.views.contracts import VIEW_PROJECTION_VERSION
from planner.conversation.views.schema import SCHEMA_STATEMENTS
from planner.conversation.views.writer import rebuild_conversation_view
from planner.conversation.workspace_admission import WorkspaceAdmission
from planner.core.db import commit_without_change_signal, connect

logger = logging.getLogger(__name__)

_VIEW_TABLES = ("state", "rows", "turns", "groups", "lookups")

# A conversation with events and no current view. A missing view row counts, because the
# read refuses that too.
_OUT_OF_DATE = (
    "SELECT c.conversation_id FROM main.conversations c "
    "LEFT JOIN main.conversation_view_state v ON v.conversation_id=c.conversation_id "
    "WHERE c.latest_sequence>0 AND (v.conversation_id IS NULL OR v.projection_version!=?)"
)

# A conversation that keeps changing while its view is computed is left to a retry.
_COMPUTE_ATTEMPTS = 3
# Failed conversations are tried again later in the same run, after a growing wait.
_RETRY_ROUNDS = 5
_FIRST_RETRY_WAIT_SECONDS = 30.0
_LONGEST_RETRY_WAIT_SECONDS = 300.0


def out_of_date_conversation_ids(db_path: str, busy_timeout_ms: int) -> list[str]:
    with closing(connect(db_path, busy_timeout_ms)) as conn:
        return [
            row[0]
            for row in conn.execute(
                _OUT_OF_DATE + " ORDER BY c.latest_sequence", (VIEW_PROJECTION_VERSION,)
            )
        ]


def _create_private_view_tables(conn: sqlite3.Connection) -> None:
    """Shadow the view tables with empty temporary copies on this connection only.

    An unqualified table name resolves to the temporary copy first, so the ordinary
    projection writer fills these, and reads its own earlier rows from them, unchanged.
    The foreign key to ``conversations`` is dropped: a temporary table cannot name a main
    table. The indexes are kept, because the writer's lookups depend on them.
    """
    for statement in SCHEMA_STATEMENTS:
        statement = re.sub(
            r"\s+REFERENCES conversations\(conversation_id\) ON DELETE CASCADE", "", statement
        )
        conn.execute(statement.replace("CREATE TABLE ", "CREATE TEMP TABLE ", 1))


def compute_conversation_view(conn: sqlite3.Connection, conversation_id: str) -> int:
    """Fill the private tables from one consistent read, and return the sequence it covers.

    This takes no write lock on the database. Writes to temporary tables stay private to
    this connection.
    """
    conn.execute("BEGIN")
    try:
        latest = conn.execute(
            "SELECT latest_sequence FROM main.conversations WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()[0]
        rebuild_conversation_view(conn, conversation_id)
        commit_without_change_signal(conn)
    except BaseException:
        conn.rollback()
        raise
    return int(latest)


def write_conversation_view_if_unchanged(
    conn: sqlite3.Connection, conversation_id: str, computed_through: int
) -> bool:
    """Copy the private rows in, if the conversation is unchanged and still out of date.

    Returns False when a new event arrived after the computation, so it must run again.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        latest = conn.execute(
            "SELECT latest_sequence FROM main.conversations WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()[0]
        if latest != computed_through:
            conn.rollback()
            return False
        if conn.execute(
            _OUT_OF_DATE + " AND c.conversation_id=?", (VIEW_PROJECTION_VERSION, conversation_id)
        ).fetchone():
            for table in _VIEW_TABLES:
                name = f"conversation_view_{table}"
                columns = ",".join(
                    row[1] for row in conn.execute(f"PRAGMA temp.table_info({name})")
                )
                conn.execute(f"DELETE FROM main.{name} WHERE conversation_id=?", (conversation_id,))
                conn.execute(
                    f"INSERT INTO main.{name}({columns}) SELECT {columns} FROM temp.{name}"
                )
        # Only an open conversation shows these rows, and its reader retries on its own.
        commit_without_change_signal(conn)
        return True
    except BaseException:
        conn.rollback()
        raise


def rebuild_one_conversation_view(db_path: str, busy_timeout_ms: int, conversation_id: str) -> None:
    """Rebuild one view, holding the write lock only to check and copy rows in."""
    with closing(connect(db_path, busy_timeout_ms)) as conn:
        _create_private_view_tables(conn)
        for _ in range(_COMPUTE_ATTEMPTS):
            computed_through = compute_conversation_view(conn, conversation_id)
            if write_conversation_view_if_unchanged(conn, conversation_id, computed_through):
                return
        raise RuntimeError("Conversation kept changing while its view was rebuilt")


async def _rebuild_each(
    conversation_ids: list[str],
    db_path: str,
    busy_timeout_ms: int,
    admission: WorkspaceAdmission,
    attempt: int,
) -> list[str] | None:
    """Rebuild each conversation once and return the ones that failed, or None if stopped."""
    failed: list[str] = []
    for conversation_id in conversation_ids:
        if admission.closed:
            return None
        started = time.monotonic()
        try:
            await asyncio.to_thread(
                rebuild_one_conversation_view, db_path, busy_timeout_ms, conversation_id
            )
        except Exception:
            failed.append(conversation_id)
            logger.warning(
                "Conversation view rebuild failed for %s (attempt %d)",
                conversation_id,
                attempt,
                exc_info=True,
            )
        await asyncio.sleep(time.monotonic() - started)
    return failed


async def rebuild_out_of_date_views(
    db_path: str, busy_timeout_ms: int, admission: WorkspaceAdmission
) -> None:
    """Rebuild every out-of-date view off the event loop, then end.

    After each conversation the rebuild waits as long as that one took, so it takes at most
    half of the time. Failed conversations are tried again later in the same run, after a
    growing wait, up to a fixed number of rounds. Whatever is left is logged.

    The rebuild is not work an update waits for, so it never makes the server look busy.
    It stops once an update closes the workspace.
    """
    pending = await asyncio.to_thread(out_of_date_conversation_ids, db_path, busy_timeout_ms)
    if not pending:
        return
    total = len(pending)
    logger.info("Rebuilding %d out-of-date conversation views", total)
    wait = _FIRST_RETRY_WAIT_SECONDS
    for attempt in range(1, _RETRY_ROUNDS + 2):
        if attempt > 1:
            logger.info("Retrying %d conversation view rebuilds in %.0f s", len(pending), wait)
            await asyncio.sleep(wait)
            wait = min(wait * 2, _LONGEST_RETRY_WAIT_SECONDS)
        failed = await _rebuild_each(pending, db_path, busy_timeout_ms, admission, attempt)
        if failed is None:
            return
        pending = failed
        if not pending:
            logger.info("Rebuilt all %d out-of-date conversation views", total)
            return
    logger.error(
        "Conversation views left out of date after %d attempts: %s",
        _RETRY_ROUNDS + 1,
        ", ".join(pending),
    )

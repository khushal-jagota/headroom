"""Retry only proposal lock acquisition, before any proposal effect exists."""

import sqlite3
import time

from planner.conversation.workspace_admission import current_write_admission
from planner.core.errors import ErrorCode, PlannerError
from planner.tickets import config


def begin_proposal(conn: sqlite3.Connection) -> None:
    admission = current_write_admission.get()
    previous_timeout = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
    deadline = time.monotonic() + config.PROPOSAL_WRITER_WAIT_SECONDS
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PlannerError(
                    ErrorCode.retry_later,
                    "The database is busy. No proposal was saved. Try again shortly.",
                )
            if admission is not None and not admission.resume():
                raise PlannerError(
                    ErrorCode.retry_later, "An update paused work. No proposal was saved."
                )
            timeout = min(config.PROPOSAL_WRITER_ATTEMPT_MS, max(0, int(remaining * 1000)))
            conn.execute(f"PRAGMA busy_timeout={timeout}")
            try:
                conn.execute("BEGIN IMMEDIATE")
                return
            except sqlite3.OperationalError as error:
                code = getattr(error, "sqlite_errorcode", None)
                if code is None or code & 0xFF != sqlite3.SQLITE_BUSY:
                    raise
                if conn.in_transaction:
                    raise
                if admission is not None:
                    admission.suspend()
                time.sleep(
                    min(config.PROPOSAL_WRITER_RETRY_SECONDS, max(0, deadline - time.monotonic()))
                )
    finally:
        conn.execute(f"PRAGMA busy_timeout={previous_timeout}")

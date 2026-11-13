"""Retry only proposal lock acquisition, before any proposal effect exists."""

import sqlite3
import time

from planner.core.errors import ErrorCode, PlannerError
from planner.tickets import config


def begin_proposal(conn: sqlite3.Connection) -> None:
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
                time.sleep(
                    min(config.PROPOSAL_WRITER_RETRY_SECONDS, max(0, deadline - time.monotonic()))
                )
    finally:
        conn.execute(f"PRAGMA busy_timeout={previous_timeout}")

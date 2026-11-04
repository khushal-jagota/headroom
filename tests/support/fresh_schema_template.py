"""Build the current schema once per test process, and copy it into each new database.

Replaying every shipped migration takes about half a second, and about a thousand tests
start from a brand-new database. So the first brand-new database a test process brings
to head is built by that real replay, and later ones receive a copy of its schema and
rows. The copy runs inside the real migration transaction, so the write lock, the
transaction admission and the foreign key check apply to it exactly as to a replay.

A database that already holds anything, a migration tree a test swapped in, and any
revision short of head always get the real replay.
"""

from __future__ import annotations

import secrets
import sqlite3
import tempfile
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Final

from sqlalchemy import Connection

from planner.core import db

SHIPPED_MIGRATIONS_DIRECTORY: Final = db.MIGRATIONS_DIRECTORY
replay_migrations: Final = db.upgrade_schema_connection

_template_lock = threading.Lock()
_template_statements: tuple[str, ...] | None = None


def install_fresh_schema_template() -> None:
    """Route every schema upgrade in this process through the copy below."""
    db.upgrade_schema_connection = upgrade_schema_connection


def upgrade_schema_connection(connection: Connection, revision: str) -> None:
    if (
        revision == "head"
        and db.MIGRATIONS_DIRECTORY == SHIPPED_MIGRATIONS_DIRECTORY
        and connection.exec_driver_sql("SELECT 1 FROM sqlite_master LIMIT 1").first() is None
    ):
        for statement in _template():
            connection.exec_driver_sql(statement)
        _give_the_copy_its_own_identity(connection)
        return
    replay_migrations(connection, revision)


def _template() -> tuple[str, ...]:
    global _template_statements
    with _template_lock:
        if _template_statements is None:
            with tempfile.TemporaryDirectory(prefix="panels-schema-template-") as scratch:
                path = str(Path(scratch) / "template.db")
                with db.schema_migration_transaction(path) as connection:
                    replay_migrations(connection, "head")
                with closing(sqlite3.connect(path)) as built:
                    _template_statements = tuple(
                        statement
                        for statement in built.iterdump()
                        if statement not in ("BEGIN TRANSACTION;", "COMMIT;")
                    )
        return _template_statements


def _give_the_copy_its_own_identity(connection: Connection) -> None:
    # A replay draws these values fresh for every database: the attention history start
    # from the clock, and the navigation scope, epochs and proof secret at random. Two
    # databases must never share them, so each copy draws its own the same way.
    connection.exec_driver_sql(
        "UPDATE attention_history_metadata SET started_at=?", (int(time.time()),)
    )
    connection.exec_driver_sql(
        "UPDATE navigation_meta SET scope=?, epoch=?", (str(uuid.uuid4()), str(uuid.uuid4()))
    )
    connection.exec_driver_sql(
        "UPDATE navigation_delivery_retention SET epoch=?", (str(uuid.uuid4()),)
    )
    connection.exec_driver_sql(
        "UPDATE navigation_proof_identity SET secret=?", (secrets.token_hex(32),)
    )

"""The source action's transaction, composable with a caller-owned transaction."""

from collections.abc import Iterator
from contextlib import contextmanager
from sqlite3 import Connection


@contextmanager
def transaction(conn: Connection) -> Iterator[None]:
    nested = conn.in_transaction
    conn.execute("SAVEPOINT measure_source_action" if nested else "BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if nested:
            conn.execute("ROLLBACK TO measure_source_action")
            conn.execute("RELEASE measure_source_action")
        else:
            conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("RELEASE measure_source_action" if nested else "COMMIT")

"""The authoritative execution admission read.

All implemented backends are intended for Mac execution. Native qualification
and installed prerequisites remain separate from admission. Creation never
requires reachability.
"""

import sqlite3

from planner.tickets.contracts import TicketLocale

CURRENT_EXECUTION_LOCALE = TicketLocale.vps


def can_execute_locale(conn: sqlite3.Connection, locale: TicketLocale) -> bool:
    """Admit remote work only with a current, reconciled VPS connection record."""
    if locale is CURRENT_EXECUTION_LOCALE:
        return True
    if locale is not TicketLocale.macos:
        return False
    return (
        conn.execute(
            "SELECT 1 FROM executor_connections WHERE active=1 AND connection_state='ready' LIMIT 1"
        ).fetchone()
        is not None
    )

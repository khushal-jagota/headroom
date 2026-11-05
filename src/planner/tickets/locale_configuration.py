"""The authoritative execution admission read.

All implemented backends are intended for Mac execution. Native qualification
and installed prerequisites remain separate from admission. Creation never
requires reachability.
"""

import json
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


def execution_unavailable_reason(conn: sqlite3.Connection, locale: TicketLocale) -> str:
    if locale is TicketLocale.macos:
        row = conn.execute(
            "SELECT refusal_json FROM mac_contact_status WHERE singleton=1"
        ).fetchone()
        if row is not None and row[0] is not None:
            refusal = json.loads(row[0])
            peer, server = refusal["peer_identity"], refusal["server_identity"]
            return (
                f"{refusal['reason']}: Mac {peer['application_revision']} "
                f"(protocol {peer['protocol_version']}, outbox {peer['spool_format_version']}); "
                f"server {server['application_revision']} "
                f"(protocol {server['protocol_version']}, "
                f"outbox {server['spool_format_version']}). "
                "Last contact confirmed a version mismatch."
            )
        return (
            "Mac Tickets cannot start: the Mac is disconnected or has not completed reconciliation"
        )
    return f"The Ticket locale {locale.value} cannot execute on this machine"

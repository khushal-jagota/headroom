"""The authoritative execution admission read.

All implemented backends are intended for Mac execution. Native qualification
and installed prerequisites remain separate from admission. Creation never
requires reachability.
"""

import sqlite3

from planner.execution_identity import supports_executor_versions
from planner.executor.logic.presence import judge_presence
from planner.executor.protocol import ExecutorRefusal
from planner.executor.vps_presence_store import read_presence
from planner.tickets.contracts import TicketLocale

CURRENT_EXECUTION_LOCALE = TicketLocale.vps
WAITING_FOR_THE_MAC = "Waiting for the Mac"


def can_execute_locale(conn: sqlite3.Connection, locale: TicketLocale) -> bool:
    """Admit Mac work once a compatible executor process is known and not refused.

    The Mac need not be present: work for it waits in the mailbox while it is away.
    """
    if locale is CURRENT_EXECUTION_LOCALE:
        return True
    if locale is not TicketLocale.macos:
        return False
    record = read_presence(conn)
    return (
        record is not None
        and record.executor_incarnation is not None
        and record.identity is not None
        and record.refusal is None
        and supports_executor_versions(
            record.identity.protocol_version, record.identity.spool_format_version
        )
    )


def mac_is_present(conn: sqlite3.Connection, now_ms: int) -> bool:
    """Whether the Mac's lease is fresh by the VPS clock."""
    return judge_presence(read_presence(conn), now_ms).presence == "present"


def _render_refusal(refusal: ExecutorRefusal) -> str:
    server = refusal.server_identity
    server_text = (
        f"server {server.application_revision} "
        f"(protocol {server.protocol_version}, outbox {server.spool_format_version})"
    )
    peer = refusal.peer_identity
    if peer is None:
        return (
            f"{refusal.reason}: the Mac runs the old WebSocket transport; {server_text}. "
            "Last contact confirmed a version mismatch."
        )
    return (
        f"{refusal.reason}: Mac {peer.application_revision} "
        f"(protocol {peer.protocol_version}, outbox {peer.spool_format_version}); "
        f"{server_text}. Last contact confirmed a version mismatch."
    )


def execution_unavailable_reason(conn: sqlite3.Connection, locale: TicketLocale) -> str:
    if locale is TicketLocale.macos:
        record = read_presence(conn)
        if record is not None and record.refusal is not None:
            return _render_refusal(record.refusal)
        if can_execute_locale(conn, locale):
            return WAITING_FOR_THE_MAC
        return "Mac Tickets cannot start: no compatible Mac executor has leased yet"
    return f"The Ticket locale {locale.value} cannot execute on this machine"

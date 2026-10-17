"""Locale compatibility and the authoritative execution admission read.

Codex has accepted Mac GUI-session evidence. Installed-session and unattended
proof remain separate release requirements. Creation never requires reachability.
"""

import sqlite3

from planner.conversation.contracts import ConversationBackendKey
from planner.core.errors import ErrorCode, PlannerError
from planner.tickets.contracts import TicketLocale

CURRENT_EXECUTION_LOCALE = TicketLocale.vps
LOCALE_SUPPORTED_BACKENDS: dict[TicketLocale, frozenset[str]] = {
    TicketLocale.vps: frozenset(backend.value for backend in ConversationBackendKey),
    TicketLocale.macos: frozenset({ConversationBackendKey.codex.value}),
}


def can_execute_locale(conn: sqlite3.Connection, locale: TicketLocale) -> bool:
    """Admit remote work only with a current, reconciled VPS connection record."""
    if locale is CURRENT_EXECUTION_LOCALE:
        return True
    if locale is not TicketLocale.macos:
        return False
    return conn.execute(
        "SELECT 1 FROM executor_connections WHERE active=1 "
        "AND connection_state='ready' LIMIT 1"
    ).fetchone() is not None


def require_locale_backend_compatibility(locale: TicketLocale, backend: str) -> None:
    if backend not in LOCALE_SUPPORTED_BACKENDS[locale]:
        reason = "backend compatibility has not been established for this locale"
        raise PlannerError(
            ErrorCode.validation,
            f"locale {locale.value} cannot use backend {backend}: {reason}",
            {"locale": locale.value, "employee_backend": backend, "reason": reason},
        )

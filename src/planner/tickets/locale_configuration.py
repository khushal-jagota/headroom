"""Machine identity and the backend compatibility established for each locale.

Mac compatibility remains empty until the Mac backend investigation establishes it.
Reachability is deliberately absent: a machine may be offline when work is created.
"""

from planner.conversation.contracts import ConversationBackendKey
from planner.core.errors import ErrorCode, PlannerError
from planner.tickets.contracts import TicketLocale

CURRENT_EXECUTION_LOCALE = TicketLocale.vps
LOCALE_SUPPORTED_BACKENDS: dict[TicketLocale, frozenset[str]] = {
    TicketLocale.vps: frozenset(backend.value for backend in ConversationBackendKey),
    TicketLocale.macos: frozenset(),
}


def require_locale_backend_compatibility(locale: TicketLocale, backend: str) -> None:
    if backend not in LOCALE_SUPPORTED_BACKENDS[locale]:
        reason = "backend compatibility has not been established for this locale"
        raise PlannerError(
            ErrorCode.validation,
            f"locale {locale.value} cannot use backend {backend}: {reason}",
            {"locale": locale.value, "employee_backend": backend, "reason": reason},
        )

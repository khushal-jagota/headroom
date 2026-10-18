"""Pure Ticket locale selection from an explicit choice and creation requirements."""

from planner.core.errors import ErrorCode, PlannerError
from planner.tickets.contracts import TicketLocale


def require_ticket_locale(raw: object) -> TicketLocale:
    try:
        if not isinstance(raw, str):
            raise ValueError("locale must be text")
        return TicketLocale(raw)
    except (ValueError, TypeError):
        raise PlannerError(
            ErrorCode.validation, "locale must be vps or macos", {"locale": str(raw)}
        ) from None


def resolve_ticket_locale(
    explicit: TicketLocale | str | None,
    *,
    project_requires_macos: bool,
    worker_requires_macos: bool,
) -> TicketLocale:
    if explicit is not None:
        return require_ticket_locale(explicit)
    if project_requires_macos or worker_requires_macos:
        return TicketLocale.macos
    return TicketLocale.vps

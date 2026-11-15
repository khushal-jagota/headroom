"""Pure Ticket locale selection from an explicit choice or the VPS default."""

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


def resolve_ticket_locale(explicit: TicketLocale | str | None) -> TicketLocale:
    if explicit is not None:
        return require_ticket_locale(explicit)
    return TicketLocale.vps

"""Pure eligibility and port restrictions."""

from planner.previews.contracts import PreviewError, TicketEligibility
from planner.tickets.contracts import TicketLocale


def require_vps(ticket: TicketEligibility) -> None:
    if ticket.locale is TicketLocale.macos:
        raise PreviewError("Application previews are unavailable for Tickets running on the Mac.")


def require_eligible(ticket: TicketEligibility) -> None:
    require_vps(ticket)
    if ticket.terminal:
        raise PreviewError("This Ticket has finished; its preview has expired.", 410)
    if ticket.project_folder is None:
        raise PreviewError("The Ticket project needs a configured folder.")


def require_port(port: int, panels_port: int) -> None:
    if not 1024 <= port <= 65535 or port in {8767, panels_port}:
        raise PreviewError("Choose a non-privileged application port distinct from Panels.")

"""Pure eligibility and port restrictions."""

from planner.previews.contracts import PreviewError, TicketEligibility


def require_eligible(ticket: TicketEligibility) -> None:
    if ticket.terminal:
        raise PreviewError("This Ticket has finished; its preview has expired.", 410)
    if ticket.project_folder is None:
        raise PreviewError("The Ticket project needs a configured folder.")


def require_port(port: int, panels_port: int) -> None:
    if not 1024 <= port <= 65535 or port in {8767, panels_port}:
        raise PreviewError("Choose a non-privileged application port distinct from Panels.")

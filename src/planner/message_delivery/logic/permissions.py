"""The speaking permission between ordinary orchestrator supervisors."""

from planner.core.contracts import Principal, PrincipalKind


def permits_supervisor_message(caller: Principal, recipient: Principal) -> bool:
    """Recognize supervisor peers; the service establishes that both exist."""
    return caller.kind is PrincipalKind.sprint_item and recipient.kind is PrincipalKind.sprint_item

"""The speaking permission between ordinary Managers."""

from planner.core.contracts import Principal, PrincipalKind


def permits_manager_message(caller: Principal, recipient: Principal) -> bool:
    """Recognize Manager peers; the service establishes that both exist."""
    return caller.kind is PrincipalKind.manager and recipient.kind is PrincipalKind.manager

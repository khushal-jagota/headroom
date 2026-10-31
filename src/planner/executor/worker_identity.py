"""Read the Ticket identity supplied to a Worker conversation start."""

from planner.conversation.contracts import ResolvedConversationStart


def ticket_id_of(start: ResolvedConversationStart) -> str | None:
    environment = dict(
        start.role_materials.identity_environment_variables if start.role_materials else ()
    )
    return environment.get("PLAN_TICKET_ID") if environment.get("PLAN_ACTOR") == "worker" else None

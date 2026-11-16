"""Stable principals for tests that do not depend on an exact work-object id."""

from typing import Final

from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind

TEST_TICKET_PRINCIPAL: Final = Principal(PrincipalKind.ticket, "t_test_worker")
# A bespoke test app built from routers, not create_app, has no owner credential file.
# Setting this as its app.state.owner_credential lets the shared test client act as the
# owner on it (tests/conftest.py).
TEST_OWNER_CREDENTIAL: Final = "owner-credential-for-bespoke-test-apps"


def ticket_principal(ticket_id: str) -> Principal:
    """Return the Worker identity for a generated Ticket fixture."""
    return Principal(PrincipalKind.ticket, ticket_id)


__all__ = [
    "CHIEF_PRINCIPAL",
    "OWNER_PRINCIPAL",
    "TEST_OWNER_CREDENTIAL",
    "TEST_TICKET_PRINCIPAL",
    "ticket_principal",
]

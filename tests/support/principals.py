"""Stable principals for tests that do not depend on an exact work-object id."""

from typing import Final

from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind

TEST_TICKET_PRINCIPAL: Final = Principal(PrincipalKind.ticket, "t_test_worker")

__all__ = ["CHIEF_PRINCIPAL", "OWNER_PRINCIPAL", "TEST_TICKET_PRINCIPAL"]

"""Supervisor coordination is a speaking permission only for supervisor peers."""

import pytest

from planner.core.contracts import Principal, PrincipalKind
from planner.message_delivery.logic.permissions import permits_supervisor_message


@pytest.mark.parametrize("caller_kind", list(PrincipalKind))
@pytest.mark.parametrize("recipient_kind", list(PrincipalKind))
def test_only_supervisor_pairs_get_the_message_permission(
    caller_kind: PrincipalKind, recipient_kind: PrincipalKind
) -> None:
    def principal(kind: PrincipalKind) -> Principal:
        identity = {PrincipalKind.owner: "owner", PrincipalKind.chief: "chief"}.get(kind, "test")
        return Principal(kind, identity)

    assert permits_supervisor_message(principal(caller_kind), principal(recipient_kind)) is (
        caller_kind is PrincipalKind.sprint_item and recipient_kind is PrincipalKind.sprint_item
    )

from __future__ import annotations

import pytest

from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind


def test_principal_kind_is_the_complete_shared_identity_vocabulary() -> None:
    assert tuple(PrincipalKind) == (
        PrincipalKind.owner,
        PrincipalKind.chief,
        PrincipalKind.sprint_item,
        PrincipalKind.ticket,
    )


def test_singletons_and_work_objects_have_required_stable_ids() -> None:
    assert OWNER_PRINCIPAL == Principal(PrincipalKind.owner, "owner")
    assert CHIEF_PRINCIPAL == Principal(PrincipalKind.chief, "chief")
    assert Principal(PrincipalKind.sprint_item, "si_one").id == "si_one"
    assert Principal(PrincipalKind.ticket, "t_one").id == "t_one"


@pytest.mark.parametrize(
    ("kind", "principal_id"),
    [
        (PrincipalKind.owner, "somebody_else"),
        (PrincipalKind.chief, "chief_of_staff"),
        (PrincipalKind.ticket, ""),
        (PrincipalKind.sprint_item, " si_one "),
    ],
)
def test_principal_rejects_invalid_ids(kind: PrincipalKind, principal_id: str) -> None:
    with pytest.raises(ValueError):
        Principal(kind, principal_id)


def test_principal_rejects_a_runtime_kind_outside_the_closed_enum() -> None:
    with pytest.raises(ValueError, match="principal kind must be a PrincipalKind"):
        Principal("agent", "reviewer")  # type: ignore[arg-type]

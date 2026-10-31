"""Exact original policy facts, canonical writer races, and crash recovery."""

from dataclasses import replace
from sqlite3 import Connection

import pytest

from planner.conversation.backend_state import write_model_enablement
from planner.conversation.contracts import ConversationBackendKey as Backend
from planner.conversation.launch_intent import bind_model_intent
from planner.conversation.model_resolution.contracts import SelectionKind, ValidationState
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.errors import PlannerError
from planner.tickets import data as tickets


def test_configuration_commit_rechecks_disables_without_catalog_evidence(
    tmp_db: Connection,
) -> None:
    from planner.tickets.validation import data as validation_data

    ticket = tickets.create_ticket(
        tmp_db,
        title="Configuration race",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        worker_type="coding",
        employee_backend="codex",
        employee_launch_model="gpt-6-sol",
        selection_kind=SelectionKind.concrete,
    )
    expected = tickets.employee_launch_configuration(ticket)
    desired = replace(
        validation_data.read_launch_selection(tmp_db, ticket.id), reasoning_effort="high"
    )
    result = bind_model_intent(desired)
    assert result.launch_token == "gpt-6-sol"
    write_model_enablement(tmp_db, Backend.codex, "gpt-6-sol", False)

    def commit_configuration() -> None:
        tickets.write_employee_configuration(
            tmp_db,
            ticket.id,
            expected_employee_configuration=expected,
            employee_backend="codex",
            employee_launch_model="gpt-6-sol",
            employee_launch_reasoning_effort="high",
            selection_kind=SelectionKind.concrete,
            validation=result,
            now=2,
        )

    with pytest.raises(PlannerError, match="policy changed") as refused:
        commit_configuration()
    assert refused.value.detail["reason"] == "model_incompatible"
    assert refused.value.detail["corrective_action"] == "choose_model"
    assert tickets.employee_launch_configuration(tickets.read_ticket(tmp_db, ticket.id)) == expected
    write_model_enablement(tmp_db, Backend.codex, "gpt-6-sol", True)
    commit_configuration()
    current = validation_data.read_launch_validation(tmp_db, ticket.id)
    assert current is not None and current.current
    assert current.validation.state is ValidationState.compatible
    assert current.validation.reason.value == "supported"
    assert current.validation.launch_token == "gpt-6-sol"
    assert current.validation.selection.submitted_token == "gpt-6-sol"
    assert current.validation.selection.reasoning_effort == "high"

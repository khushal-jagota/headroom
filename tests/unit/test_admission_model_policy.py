"""Exact original policy facts, canonical writer races, and crash recovery."""

from dataclasses import replace
from sqlite3 import Connection

import pytest
from tests.unit.test_current_model_resolution import context, observation, row

from planner.conversation.admission.policy import (
    check_policy_dependencies,
)
from planner.conversation.backend_state import model_is_enabled, write_model_enablement
from planner.conversation.contracts import ConversationBackendKey as Backend
from planner.conversation.launch_intent import bind_model_intent
from planner.conversation.model_resolution.contracts import SelectionKind, ValidationState
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect
from planner.core.errors import PlannerError
from planner.tickets import data as tickets


def test_historical_policy_barriers_do_not_block_edits_but_disables_still_apply(
    tmp_db: Connection,
) -> None:
    from planner.conversation.admission.policy import ModelPolicyDependencies
    from planner.conversation.backends.contracts import AdmissionRefused

    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    tmp_db.executemany(
        "INSERT INTO ticket_launch_authority "
        "(lease_id,authority_key,mode,owner_process,command_id,executor_incarnation) "
        "VALUES('historical',?,'read','old-owner','uncertain-command','executor-1')",
        [("model-policy:codex:gpt-6.1-terra",),
         ("model-policy-recovery:codex",), ("model-policy-recovery:*",)],
    )
    tmp_db.commit()
    dependencies = ModelPolicyDependencies(Backend.codex, ("gpt-6.1-terra",))
    before = [tuple(row) for row in tmp_db.execute("SELECT * FROM ticket_launch_authority")]
    other = connect(path)
    try:
        write_model_enablement(other, Backend.codex, "gpt-6.1-terra", False)
        assert not model_is_enabled(other, Backend.codex, "gpt-6.1-terra")
        with pytest.raises(AdmissionRefused, match="disabled"):
            check_policy_dependencies(other, dependencies)
        write_model_enablement(other, Backend.codex, "gpt-6.1-terra", True)
        check_policy_dependencies(other, dependencies)
        retained = [tuple(row) for row in other.execute("SELECT * FROM ticket_launch_authority")]
        assert retained == before
    finally:
        other.close()


def test_configuration_commit_rechecks_disables_without_catalog_evidence(
    tmp_db: Connection,
) -> None:
    from planner.runtime.conversation_start import worker_resolve, workspace_binding_from_start
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
    binding = workspace_binding_from_start(worker_resolve(tmp_db, ticket), ticket.locale)
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
            advertised_models=frozenset({"gpt-6-sol"}),
            reasoning_supported=True,
            advertised_reasoning_efforts=frozenset({"high"}),
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
    tmp_db.execute("DELETE FROM scoped_model_catalog_rows")
    tmp_db.execute("DELETE FROM scoped_model_catalog_observations")
    commit_configuration()
    current = validation_data.read_launch_validation(tmp_db, ticket.id)
    assert current is not None and current.current
    assert current.validation.state is ValidationState.compatible
    assert current.validation.reason.value == "supported"
    assert current.validation.launch_token == "gpt-6-sol"
    assert current.validation.selection.submitted_token == "gpt-6-sol"
    assert current.validation.selection.reasoning_effort == "high"

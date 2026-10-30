"""Exact original policy facts, canonical writer races, and crash recovery."""

import json
from dataclasses import replace
from sqlite3 import Connection

import pytest
from pydantic import TypeAdapter
from tests.unit.test_current_model_resolution import context, observation, row, selection

from planner.conversation.admission.contracts import AdmissionExpectation
from planner.conversation.admission.policy import (
    check_policy_dependencies,
    policy_key,
    protect_validation_policy,
    recover_policy_ownership,
)
from planner.conversation.admission.service import expectation_from_validation
from planner.conversation.backend_state import model_is_enabled, write_model_enablement
from planner.conversation.contracts import ConversationBackendKey as Backend
from planner.conversation.model_resolution.contracts import SelectionKind, ValidationState
from planner.conversation.model_resolution.data import ModelResolutionStore
from planner.conversation.model_resolution.logic.validation import validate_model_selection
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect
from planner.core.errors import PlannerError
from planner.executor.contracts import ChildCommand, ChildOperation, ChildReference
from planner.tickets import data as tickets
from planner.tickets import launch_authority as authority


def test_exact_alias_support_fences_canonical_writer_and_allows_unrelated_policy(
    tmp_db: Connection,
) -> None:
    ticket = tickets.create_ticket(
        tmp_db,
        title="Policy",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        worker_type="coding",
        employee_backend="codex",
        employee_launch_model="gpt-6-sol",
        selection_kind=SelectionKind.concrete,
    )
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    # Concrete ID is supported by an alias row carrying exact identity-preserving
    # launch evidence. That row token remains a policy dependency.
    source = observation(
        (
            replace(
                row("provider-current", kind=SelectionKind.provider_alias, target="gpt-6-sol"),
                identity_preserving_launch_token="gpt-6-sol",
            ),
        )
    )
    ModelResolutionStore(path).keep_observation(source)
    result = validate_model_selection(
        selection(), context(), source, now=1000, attempt_id="attempt-1"
    )
    assert result.launch_token == "gpt-6-sol"
    permit = authority.acquire_read(tmp_db, ticket.id)
    dependencies = protect_validation_policy(tmp_db, permit, result)
    assert dependencies.model_ids == ("gpt-6-sol",)
    assert permit.lease_id
    other = connect(path)
    try:
        with pytest.raises(PlannerError, match="unsettled operation"):
            write_model_enablement(other, Backend.codex, "gpt-6-sol", False)
        write_model_enablement(other, Backend.codex, "gpt-other", False)
        check_policy_dependencies(tmp_db, dependencies)
        write_model_enablement(other, Backend.codex, "gpt-6-sol", True)  # semantic no-op
        # A canonical exclusive writer wins before same-lease extension.
        permit.release(tmp_db)
        writer = authority.acquire_mutation(other, (policy_key(Backend.codex, "gpt-6-sol"),))
        with pytest.raises(PlannerError, match="unsettled operation"):
            authority.acquire_read(tmp_db, ticket.id)
        writer.release(other)
        pending = authority.acquire_read(tmp_db, ticket.id)
        protect_validation_policy(tmp_db, pending, result)
        pending.release(tmp_db)
        write_model_enablement(other, Backend.codex, "gpt-6-sol", False)
        assert not model_is_enabled(other, Backend.codex, "gpt-6-sol")
    finally:
        other.close()


def test_recovery_extends_existing_lease_from_original_evidence_and_keeps_unknown(
    tmp_db: Connection,
) -> None:
    ticket = tickets.create_ticket(
        tmp_db,
        title="Recovery",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        worker_type="coding",
        employee_backend="codex",
        employee_launch_model="gpt-6-sol",
        selection_kind=SelectionKind.concrete,
    )
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    source = observation((row(),))
    ModelResolutionStore(path).keep_observation(source)
    result = validate_model_selection(
        selection(), context(), source, now=1000, attempt_id="attempt-1"
    )
    child = ChildReference("inc", "child", "conversation")
    expectation = expectation_from_validation(
        result,
        attempt_id="attempt-1",
        conversation_id=child.conversation_id,
        disabled_models_revision="original-attribution",
        child=child,
    )
    command = ChildCommand(
        "command",
        child,
        ChildOperation.start,
        None,
        {
            "admission_expectation": TypeAdapter(AdmissionExpectation).dump_python(
                expectation, mode="json"
            ),
        },
    )

    tmp_db.execute(
        "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,"
        "access,created_at) "
        "VALUES('conversation','codex','gpt-6-sol','/workspace','full',1)"
    )
    tmp_db.execute("UPDATE tickets SET conversation_id='conversation' WHERE id=?", (ticket.id,))
    tmp_db.execute(
        "INSERT INTO executor_core_turns VALUES(?,?,?,?)",
        (
            child.conversation_id,
            TypeAdapter(ChildReference).dump_json(child).decode(),
            "{}",
            json.dumps(
                {"initial_commands": [TypeAdapter(ChildCommand).dump_python(command, mode="json")]}
            ),
        ),
    )
    permit = authority.acquire_read(tmp_db, ticket.id)
    # Simulate a pre-policy lease from an older binary, before extension.
    tmp_db.execute("DELETE FROM ticket_launch_authority WHERE authority_key LIKE 'model-policy:%'")
    protect_validation_policy(tmp_db, permit, result, conversation_id=child.conversation_id)
    permit.bind_command(tmp_db, command.command_id, child.executor_incarnation)
    permit.release(tmp_db)
    tmp_db.close()
    reopened = connect(path)
    try:
        recover_policy_ownership(reopened)
        keys = {r[0] for r in reopened.execute("SELECT authority_key FROM ticket_launch_authority")}
        assert policy_key(Backend.codex, "gpt-6-sol") in keys
        assert not any(key.startswith("model-policy-recovery:") for key in keys)
        # A new preparation crashed before completion. The older exact command
        # on this same conversation must not taint its cleanup or widen scope.
        preparation = authority.acquire_read(reopened, ticket.id)
        with pytest.raises(PlannerError, match="complete launch policy proof"):
            preparation.bind_command(reopened, "must-not-record", child.executor_incarnation)
        reopened.execute(
            "UPDATE ticket_launch_authority SET owner_process='recovered:preparation' "
            "WHERE lease_id=?",
            (preparation.lease_id,),
        )
        recovery = authority.reconcile_persisted_launch_authority(reopened)
        assert recovery.settled == 1
        assert not any(
            key.startswith("model-policy-recovery:") for key in recovery.blocked_authority_keys
        )
        assert reopened.execute(
            "SELECT 1 FROM ticket_launch_authority WHERE command_id=?",
            (command.command_id,),
        ).fetchone()
        # Contradictory original backend facts cannot be hidden by an earlier
        # successful local-proof derivation in the same recovery pass.
        reopened.execute(
            "UPDATE conversations SET backend_key='claude' WHERE conversation_id='conversation'"
        )
        recover_policy_ownership(reopened)
        assert reopened.execute(
            "SELECT 1 FROM ticket_launch_authority WHERE authority_key='model-policy-recovery:*'"
        ).fetchone()
        with pytest.raises(PlannerError):
            write_model_enablement(reopened, Backend.claude, "other", False)
        reopened.execute(
            "UPDATE conversations SET backend_key='codex' WHERE conversation_id='conversation'"
        )
        recover_policy_ownership(reopened)
        assert not reopened.execute(
            "SELECT 1 FROM ticket_launch_authority "
            "WHERE authority_key LIKE 'model-policy-recovery:%'"
        ).fetchone()
        # Missing evidence is not success, expiry, startup failure, or global
        # workspace locking. It becomes a policy-only barrier for original Codex.
        reopened.execute("DELETE FROM scoped_model_catalog_rows")
        reopened.execute("DELETE FROM scoped_model_catalog_observations")
        recover_policy_ownership(reopened)
        write_model_enablement(reopened, Backend.codex, "unrelated", False)
        with pytest.raises(PlannerError):
            write_model_enablement(reopened, Backend.codex, "gpt-6-sol", False)
        write_model_enablement(reopened, Backend.claude, "unrelated", False)
        authority.settle_command(reopened, command.command_id, child.executor_incarnation)
        write_model_enablement(reopened, Backend.codex, "unrelated", False)
        assert not reopened.execute("SELECT 1 FROM ticket_launch_authority").fetchone()
    finally:
        reopened.close()


def test_unbound_submitted_only_lease_gets_policy_recovery_barrier(tmp_db: Connection) -> None:
    ticket = tickets.create_ticket(
        tmp_db,
        title="Local uncertainty",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        worker_type="coding",
        employee_backend="codex",
        employee_launch_model="gpt-6-sol",
    )
    permit = authority.acquire_read(tmp_db, ticket.id)
    tmp_db.execute(
        "UPDATE ticket_launch_authority SET policy_context_json=NULL WHERE lease_id=?",
        (permit.lease_id,),
    )
    recover_policy_ownership(tmp_db)
    assert tmp_db.execute(
        "SELECT 1 FROM ticket_launch_authority WHERE lease_id=? "
        "AND authority_key='model-policy-recovery:codex'",
        (permit.lease_id,),
    ).fetchone()
    with pytest.raises(PlannerError):
        write_model_enablement(tmp_db, Backend.codex, "other", False)
    permit.release(tmp_db)


def test_unknown_backend_policy_barrier_narrows_when_original_source_becomes_provable(
    tmp_db: Connection,
) -> None:
    ticket = tickets.create_ticket(
        tmp_db,
        title="Old lease",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        worker_type="coding",
        employee_backend="codex",
        employee_launch_model="gpt-6-sol",
    )
    permit = authority.acquire_read(tmp_db, ticket.id)
    tmp_db.execute("UPDATE ticket_launch_authority SET policy_context_json=NULL")
    tmp_db.execute("DELETE FROM ticket_launch_authority WHERE authority_key LIKE 'model-policy:%'")
    recover_policy_ownership(tmp_db)
    with pytest.raises(PlannerError):
        write_model_enablement(tmp_db, Backend.claude, "other", False)
    # Original linked immutable source recovers the backend; the current Ticket
    # employee config is not consulted to narrow policy ownership.
    tmp_db.execute(
        "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,"
        "access,created_at) VALUES('original-source','codex','gpt-6-sol','/workspace','full',1)"
    )
    tmp_db.execute("UPDATE tickets SET conversation_id='original-source' WHERE id=?", (ticket.id,))
    recover_policy_ownership(tmp_db)
    keys = {r[0] for r in tmp_db.execute("SELECT authority_key FROM ticket_launch_authority")}
    assert "model-policy-recovery:codex" in keys
    assert "model-policy-recovery:*" not in keys
    write_model_enablement(tmp_db, Backend.claude, "other", False)
    with pytest.raises(PlannerError):
        write_model_enablement(tmp_db, Backend.codex, "other", False)
    permit.release(tmp_db)


def test_complete_local_proof_stays_narrow_but_preparation_crash_cleans_safely(
    tmp_db: Connection,
) -> None:
    ticket = tickets.create_ticket(
        tmp_db,
        title="Local proof",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        worker_type="coding",
        employee_backend="codex",
        employee_launch_model="gpt-6-sol",
    )
    tmp_db.execute(
        "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,"
        "access,created_at) VALUES('local-source','codex','gpt-6-sol','/workspace','full',1)"
    )
    tmp_db.execute("UPDATE tickets SET conversation_id='local-source' WHERE id=?", (ticket.id,))
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    prepared = authority.acquire_read(tmp_db, ticket.id)
    tmp_db.execute(
        "UPDATE ticket_launch_authority SET owner_process='recovered:pre-discovery' "
        "WHERE lease_id=?",
        (prepared.lease_id,),
    )
    # Preparation never crossed provider admission, and has no retained command.
    assert authority.reconcile_persisted_launch_authority(tmp_db).settled == 1
    source = observation((row(),))
    ModelResolutionStore(path).keep_observation(source)
    result = validate_model_selection(
        selection(), context(), source, now=1000, attempt_id="attempt-1"
    )
    permit = authority.acquire_read(tmp_db, ticket.id)
    protect_validation_policy(tmp_db, permit, result, conversation_id="local-source")
    tmp_db.execute(
        "UPDATE ticket_launch_authority SET owner_process='recovered:local-emission' "
        "WHERE lease_id=?",
        (permit.lease_id,),
    )
    reopened = connect(path)
    try:
        report = authority.reconcile_persisted_launch_authority(reopened)
        assert report.settled == 0
        assert not any(
            key.startswith("model-policy-recovery:") for key in report.blocked_authority_keys
        )
        write_model_enablement(reopened, Backend.codex, "unrelated", False)
        with pytest.raises(PlannerError, match="unsettled operation"):
            write_model_enablement(reopened, Backend.codex, "gpt-6-sol", False)
        serialized = reopened.execute(
            "SELECT policy_context_json FROM ticket_launch_authority WHERE lease_id=? LIMIT 1",
            (permit.lease_id,),
        ).fetchone()[0]
        malformed = json.loads(serialized)
        malformed["unexpected"] = "must not be ignored"
        reopened.execute(
            "UPDATE ticket_launch_authority SET policy_context_json=? WHERE lease_id=?",
            (json.dumps(malformed), permit.lease_id),
        )
        report = authority.reconcile_persisted_launch_authority(reopened)
        assert "model-policy-recovery:*" in report.blocked_authority_keys
        reopened.execute(
            "UPDATE ticket_launch_authority SET policy_context_json=? WHERE lease_id=?",
            (serialized, permit.lease_id),
        )
        report = authority.reconcile_persisted_launch_authority(reopened)
        assert not any(
            key.startswith("model-policy-recovery:") for key in report.blocked_authority_keys
        )
        # Losing immutable original support cannot renew or invent admission facts.
        reopened.execute("DELETE FROM scoped_model_catalog_rows")
        reopened.execute("DELETE FROM scoped_model_catalog_observations")
        report = authority.reconcile_persisted_launch_authority(reopened)
        assert "model-policy:codex:gpt-6-sol" in report.blocked_authority_keys
        assert not any(
            key.startswith("model-policy-recovery:") for key in report.blocked_authority_keys
        )
        permit.release(reopened)
    finally:
        reopened.close()


def test_configuration_commit_rechecks_alias_policy_and_downgrades_missing_original_evidence(
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
    launch = context(workspace=binding, localized_workspace_folder=binding.workspace_folder)
    source = observation(
        (
            replace(
                row("supporting-alias", kind=SelectionKind.provider_alias, target="gpt-6-sol"),
                identity_preserving_launch_token="gpt-6-sol",
            ),
        ),
        launch=launch,
    )
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    ModelResolutionStore(path).keep_observation(source)
    result = validate_model_selection(desired, launch, source, now=1000, attempt_id="attempt-1")
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
    assert current.validation.state is ValidationState.unresolved
    assert current.validation.reason.value == "unknown_model"
    assert current.validation.launch_token == "gpt-6-sol"
    assert current.validation.selection.submitted_token == "gpt-6-sol"
    assert current.validation.selection.reasoning_effort == "high"

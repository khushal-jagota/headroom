"""The layers a conversation's start values are resolved from."""

from __future__ import annotations

from pathlib import Path

import pytest

from planner.conversation.contracts import ConversationAccess, ConversationBackendKey
from planner.core.contracts import ErrorCode, PlannerError
from planner.runtime.logic.conversation_start_resolution import (
    NO_CONVERSATION_START_OVERRIDES,
    WORKER_ROLE_TEXT,
    ConversationStartConfiguration,
    ConversationStartOverrides,
    resolve_worker_conversation_start,
)

_WORKSPACE = Path("/tmp/panels-workspace")

_WORKER_TYPE_DEFAULTS = ConversationStartConfiguration(
    backend_key=ConversationBackendKey.codex,
    model="gpt-5.6-sol",
    reasoning_effort="medium",
)


def _worker(
    *,
    ticket_last_chosen: ConversationStartConfiguration | None = None,
    overrides: ConversationStartOverrides = NO_CONVERSATION_START_OVERRIDES,
) -> tuple[ConversationBackendKey, str, str | None]:
    values = resolve_worker_conversation_start(
        ticket_id="t_abc",
        worker_type_launch_defaults=_WORKER_TYPE_DEFAULTS,
        ticket_last_chosen=ticket_last_chosen,
        overrides=overrides,
        workspace_folder=_WORKSPACE,
    )
    return values.backend_key, values.model, values.reasoning_effort


def test_with_no_ticket_layer_and_no_overrides_the_worker_type_defaults_answer() -> None:
    assert _worker() == (ConversationBackendKey.codex, "gpt-5.6-sol", "medium")


def test_the_ticket_layer_replaces_the_worker_type_defaults() -> None:
    assert _worker(
        ticket_last_chosen=ConversationStartConfiguration(
            backend_key=ConversationBackendKey.claude,
            model="opus",
            reasoning_effort="high",
        )
    ) == (ConversationBackendKey.claude, "opus", "high")


def test_an_override_that_changes_the_backend_without_a_model_is_refused() -> None:
    # There is nothing left for the conversation to run on: the layer below is about a
    # different backend, and the models of one backend mean nothing to another.
    with pytest.raises(PlannerError) as refusal:
        _worker(overrides=ConversationStartOverrides(backend_key=ConversationBackendKey.hermes))

    assert refusal.value.code is ErrorCode.validation


def test_a_worker_start_carries_the_worker_role_and_its_ticket_identity() -> None:
    values = resolve_worker_conversation_start(
        ticket_id="t_abc",
        worker_type_launch_defaults=_WORKER_TYPE_DEFAULTS,
        ticket_last_chosen=None,
        workspace_folder=_WORKSPACE,
    )

    assert values.role_materials.role_text == WORKER_ROLE_TEXT
    assert values.role_materials.identity_environment_variables == (
        ("PLAN_ACTOR", "worker"),
        ("PLAN_TICKET_ID", "t_abc"),
    )
    assert values.workspace_folder == _WORKSPACE
    assert values.access is ConversationAccess.full



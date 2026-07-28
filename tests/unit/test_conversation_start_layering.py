"""The layers a conversation's start values are resolved from.

Named for the layering rather than for the module because
``test_conversation_start_resolution.py`` is already taken by the conversation
contract's own floor-default resolution.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from planner.conversation.contracts import ConversationAccess, ConversationBackendKey
from planner.core.contracts import ErrorCode, PlannerError
from planner.runtime.logic.conversation_start_resolution import (
    CHIEF_ROLE_TEXT,
    NO_CONVERSATION_START_OVERRIDES,
    WORKER_ROLE_TEXT,
    ConversationStartConfiguration,
    ConversationStartOverrides,
    resolve_agent_conversation_start,
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


def test_a_ticket_layer_on_the_same_backend_still_replaces_the_model_and_effort() -> None:
    assert _worker(
        ticket_last_chosen=ConversationStartConfiguration(
            backend_key=ConversationBackendKey.codex, model="gpt-5.6-terra"
        )
    ) == (ConversationBackendKey.codex, "gpt-5.6-terra", None)


def test_an_override_that_changes_the_backend_without_a_model_is_refused() -> None:
    # There is nothing left for the conversation to run on: the layer below is about a
    # different backend, and the models of one backend mean nothing to another.
    with pytest.raises(PlannerError) as refusal:
        _worker(overrides=ConversationStartOverrides(backend_key=ConversationBackendKey.hermes))

    assert refusal.value.code is ErrorCode.validation


def test_an_override_that_changes_the_backend_keeps_the_model_it_names_itself() -> None:
    assert _worker(
        overrides=ConversationStartOverrides(
            backend_key=ConversationBackendKey.hermes, model="sonnet"
        )
    ) == (ConversationBackendKey.hermes, "sonnet", None)


def test_an_override_naming_the_same_backend_is_not_a_change_and_resets_nothing() -> None:
    assert _worker(
        overrides=ConversationStartOverrides(backend_key=ConversationBackendKey.codex)
    ) == (ConversationBackendKey.codex, "gpt-5.6-sol", "medium")


def test_an_override_of_the_model_alone_leaves_the_backend_and_effort_alone() -> None:
    assert _worker(overrides=ConversationStartOverrides(model="gpt-5.6-codex")) == (
        ConversationBackendKey.codex,
        "gpt-5.6-codex",
        "medium",
    )


def test_an_override_of_the_effort_alone_leaves_the_backend_and_model_alone() -> None:
    assert _worker(overrides=ConversationStartOverrides(reasoning_effort="high")) == (
        ConversationBackendKey.codex,
        "gpt-5.6-sol",
        "high",
    )


def test_an_override_changing_the_backend_takes_nothing_from_the_ticket_layer() -> None:
    assert _worker(
        ticket_last_chosen=ConversationStartConfiguration(
            backend_key=ConversationBackendKey.claude, model="opus", reasoning_effort="high"
        ),
        overrides=ConversationStartOverrides(
            backend_key=ConversationBackendKey.hermes, model="sonnet"
        ),
    ) == (ConversationBackendKey.hermes, "sonnet", None)


def test_an_override_of_the_model_lands_on_the_ticket_layer_not_the_defaults() -> None:
    assert _worker(
        ticket_last_chosen=ConversationStartConfiguration(
            backend_key=ConversationBackendKey.claude,
            model="haiku",
            reasoning_effort="high",
        ),
        overrides=ConversationStartOverrides(model="opus"),
    ) == (ConversationBackendKey.claude, "opus", "high")


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


def test_the_chief_defaults_answer_when_nothing_is_overridden() -> None:
    values = resolve_agent_conversation_start(
        chief_launch_defaults=_WORKER_TYPE_DEFAULTS,
        workspace_folder=_WORKSPACE,
    )

    assert (values.backend_key, values.model, values.reasoning_effort) == (
        ConversationBackendKey.codex,
        "gpt-5.6-sol",
        "medium",
    )


def test_a_chief_override_that_changes_the_backend_brings_its_own_model() -> None:
    values = resolve_agent_conversation_start(
        chief_launch_defaults=_WORKER_TYPE_DEFAULTS,
        overrides=ConversationStartOverrides(
            backend_key=ConversationBackendKey.claude, model="opus"
        ),
        workspace_folder=_WORKSPACE,
    )

    assert (values.backend_key, values.model, values.reasoning_effort) == (
        ConversationBackendKey.claude,
        "opus",
        None,
    )


def test_a_chief_start_carries_the_chief_role_and_its_identity() -> None:
    values = resolve_agent_conversation_start(
        chief_launch_defaults=_WORKER_TYPE_DEFAULTS,
        workspace_folder=_WORKSPACE,
    )

    assert values.role_materials.role_text == CHIEF_ROLE_TEXT
    assert values.role_materials.identity_environment_variables == (("PLAN_ACTOR", "chief"),)
    assert values.access is ConversationAccess.full


def test_the_two_role_texts_both_send_the_agent_to_the_panels_skill_first() -> None:
    assert WORKER_ROLE_TEXT.startswith("Start with the `panels` skill.")
    assert CHIEF_ROLE_TEXT.startswith("Start with the `panels` skill.")
    assert WORKER_ROLE_TEXT.endswith("You are a ticket worker.")
    assert CHIEF_ROLE_TEXT.endswith("You are a chief of staff.")

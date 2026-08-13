"""The floor defaults, tested directly: they are deliberately not readable through the
contract, so the pure resolution function is the only place they can be asserted."""

from __future__ import annotations

from pathlib import Path

import pytest

from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
)
from planner.conversation.logic.conversation_start_resolution import (
    resolve_conversation_start_request,
)


def _request_naming_only_a_model() -> ConversationStartRequest:
    """The smallest request there is: an id and the model, and every floor left to fill."""
    return ConversationStartRequest(conversation_id="c", model="a-model")


def test_an_absent_backend_key_resolves_to_codex() -> None:
    resolved = resolve_conversation_start_request(_request_naming_only_a_model())
    assert resolved.backend_key is ConversationBackendKey.codex


def test_an_absent_workspace_folder_resolves_to_the_projects_folder() -> None:
    resolved = resolve_conversation_start_request(_request_naming_only_a_model())
    assert resolved.workspace_folder == Path.home() / "projects"


def test_an_absent_access_posture_resolves_to_full_access() -> None:
    resolved = resolve_conversation_start_request(_request_naming_only_a_model())
    assert resolved.access is ConversationAccess.full


def test_explicit_values_win_over_every_floor_default() -> None:
    role_materials = ConversationRoleMaterials(
        role_text="you are the worker",
        identity_environment_variables=(("PANELS_IDENTITY", "worker-1"),),
    )
    resolved = resolve_conversation_start_request(
        ConversationStartRequest(
            conversation_id="c",
            backend_key=ConversationBackendKey.hermes,
            model="a-model",
            reasoning_effort="high",
            role_materials=role_materials,
            workspace_folder=Path("/tmp/somewhere-else"),
            access=ConversationAccess.full,
        )
    )
    assert resolved.conversation_id == "c"
    assert resolved.backend_key is ConversationBackendKey.hermes
    assert resolved.model == "a-model"
    assert resolved.reasoning_effort == "high"
    assert resolved.role_materials == role_materials
    assert resolved.workspace_folder == Path("/tmp/somewhere-else")
    assert resolved.access is ConversationAccess.full


def test_values_with_no_floor_default_stay_absent() -> None:
    resolved = resolve_conversation_start_request(_request_naming_only_a_model())
    assert resolved.reasoning_effort is None
    assert resolved.role_materials is None


def test_an_empty_conversation_id_is_rejected() -> None:
    with pytest.raises(ValueError):
        resolve_conversation_start_request(
            ConversationStartRequest(conversation_id="", model="a-model")
        )


def test_an_untrimmed_conversation_id_is_rejected() -> None:
    with pytest.raises(ValueError):
        resolve_conversation_start_request(
            ConversationStartRequest(conversation_id=" c ", model="a-model")
        )


def test_an_empty_model_is_rejected() -> None:
    with pytest.raises(ValueError):
        resolve_conversation_start_request(
            ConversationStartRequest(conversation_id="c", model="")
        )


def test_an_untrimmed_model_is_rejected() -> None:
    with pytest.raises(ValueError):
        resolve_conversation_start_request(
            ConversationStartRequest(conversation_id="c", model=" a-model ")
        )


def test_a_relative_workspace_folder_is_rejected() -> None:
    with pytest.raises(ValueError):
        resolve_conversation_start_request(
            ConversationStartRequest(
                conversation_id="c", model="a-model", workspace_folder=Path("relative/folder")
            )
        )

"""The whole conversation contract, against the real system over real ACP child processes."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest
from tests.support.conversation_contract_conformance import (
    ConversationContractConformanceSuite,
    ConversationSystemUnderTest,
)
from tests.support.conversation_system_under_test import (
    open_conversation_system_under_test,
)
from tests.support.in_memory_conversation_system_under_test import (
    open_in_memory_conversation_system_under_test,
)

from planner.conversation.logic import conversation_start_resolution


@pytest.fixture(autouse=True)
def existing_floor_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        conversation_start_resolution, "FLOOR_DEFAULT_WORKSPACE_FOLDER", workspace
    )


class TestConversationSystemConformance(ConversationContractConformanceSuite):
    def open_system_under_test(self) -> AbstractAsyncContextManager[ConversationSystemUnderTest]:
        return open_conversation_system_under_test()


class TestInMemoryConversationSystemConformance(ConversationContractConformanceSuite):
    def open_system_under_test(self) -> AbstractAsyncContextManager[ConversationSystemUnderTest]:
        return open_in_memory_conversation_system_under_test()

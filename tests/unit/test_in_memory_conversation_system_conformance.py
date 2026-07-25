"""Runs the conversation contract conformance suite against the in-memory system."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager

from tests.support.conversation_contract_conformance import (
    ConversationContractConformanceSuite,
    ConversationSystemUnderTest,
)
from tests.support.in_memory_conversation_system_under_test import (
    open_in_memory_conversation_system_under_test,
)


class TestInMemoryConversationSystemConformance(ConversationContractConformanceSuite):
    def open_system_under_test(self) -> AbstractAsyncContextManager[ConversationSystemUnderTest]:
        return open_in_memory_conversation_system_under_test()

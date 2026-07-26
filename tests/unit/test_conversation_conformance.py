"""The whole conversation contract, against the real system over real ACP child processes."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager

from tests.support.conversation_contract_conformance import (
    ConversationContractConformanceSuite,
    ConversationSystemUnderTest,
)
from tests.support.conversation_system_under_test import (
    open_conversation_system_under_test,
)


class TestConversationSystemConformance(ConversationContractConformanceSuite):
    def open_system_under_test(self) -> AbstractAsyncContextManager[ConversationSystemUnderTest]:
        return open_conversation_system_under_test()

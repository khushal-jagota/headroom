"""The whole conversation contract, against the real system over real ACP child processes."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager

from tests.support.conversation2_system_under_test import (
    open_conversation2_system_under_test,
)
from tests.support.conversation_contract_conformance import (
    ConversationContractConformanceSuite,
    ConversationSystemUnderTest,
)


class TestConversation2SystemConformance(ConversationContractConformanceSuite):
    def open_system_under_test(self) -> AbstractAsyncContextManager[ConversationSystemUnderTest]:
        return open_conversation2_system_under_test()

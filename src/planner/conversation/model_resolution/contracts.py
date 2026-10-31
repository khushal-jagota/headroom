"""The shared model-family resolution contract for creation, correction and dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.snapshot import BackendSnapshot
from planner.execution_identity import ExecutorIdentity


@dataclass(frozen=True, slots=True)
class ModelVariant:
    backend_key: ConversationBackendKey
    name: str


class RemoteCatalogSource(Protocol):
    @property
    def identity(self) -> ExecutorIdentity | None: ...

    @property
    def ready(self) -> bool: ...

    def owns_conversation(self, conversation_id: str) -> bool: ...

    def retained(self, backend_key: ConversationBackendKey) -> BackendSnapshot | None: ...

    async def snapshot(
        self, backend_key: ConversationBackendKey, *, refresh: bool = False
    ) -> BackendSnapshot: ...

"""Typed publication boundary for live ACP conversation runtime state."""

from __future__ import annotations

from typing import Protocol

from acp.schema import RequestPermissionRequest, RequestPermissionResponse

from .contracts import (
    ContextCompaction,
    ConversationActivity,
    ConversationActivityState,
    ConversationEmployee,
    ConversationPermissionOutcome,
    ConversationPermissionRequest,
    ConversationSessionBinding,
    ConversationTerminalState,
    ProgrammaticPrompt,
    QueuedPrompt,
    TurnDeliveryReceipt,
)


class ConversationRuntimeEventPublisher(Protocol):
    """The only ACP-02 boundary allowed to allocate visible event sequence."""

    async def publish_activity(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        state: ConversationActivityState,
        detail: str,
    ) -> ConversationActivity: ...

    async def publish_delivery_receipt(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        receipt: TurnDeliveryReceipt,
    ) -> None: ...

    async def publish_programmatic_prompt(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        prompt: ProgrammaticPrompt,
    ) -> None: ...

    async def publish_queue_snapshot(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        prompts: tuple[QueuedPrompt, ...],
    ) -> None: ...

    async def publish_compaction(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        compaction: ContextCompaction,
    ) -> None: ...

    async def publish_permission_request(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        request_id: str,
        backend_key: str,
        request: RequestPermissionRequest,
        deadline_at: int,
    ) -> ConversationPermissionRequest: ...

    async def publish_permission_outcome(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        request_id: str,
        response: RequestPermissionResponse,
        cancellation_reason: str | None,
    ) -> ConversationPermissionOutcome: ...

    async def publish_terminal_state(
        self,
        employee: ConversationEmployee,
        binding: ConversationSessionBinding,
        state: ConversationTerminalState,
    ) -> None: ...

"""Own the application conversation/Signal lifetime at one composition boundary.

The producer and navigation binding keep their separate contracts. HTTP resources
stop through one callback after source invalidation and before conversation drain.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager

from planner.conversation.backends.codex_app_server.login import CodexLoginRejectionMemory
from planner.conversation.backends.contracts import BackendChildFactory
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.runtime_composition import (
    ConversationRuntime,
    SignalRuntimeComposition,
    build_conversation_runtime,
)
from planner.navigation.delivery import DeliveryRuntime
from planner.navigation.signal_runtime import SignalNavigationBinding


@asynccontextmanager
async def application_runtime(
    *,
    db_path: str,
    db_busy_timeout_ms: int,
    sse_heartbeat_ms: int,
    backend_child_factories: Mapping[ConversationBackendKey, BackendChildFactory],
    remembered_codex_login_rejection: CodexLoginRejectionMemory | None,
    navigation_delivery: DeliveryRuntime,
    shutdown_dependents: Callable[[], Awaitable[None]],
) -> AsyncIterator[ConversationRuntime]:
    """Enter phases synchronously, then yield one fully installed runtime.

    No producer gate spans an await. Cleanup preserves the exact borrowed handle
    and immediate successful-constructor cleanup, including partial entry failures.
    """
    async with AsyncExitStack() as cleanup:
        composition = SignalRuntimeComposition(db_path, db_busy_timeout_ms)
        cleanup.callback(composition.close)
        composition.bootstrap()
        binding = SignalNavigationBinding(composition.coordinator, navigation_delivery)
        cleanup.callback(binding.close)
        conversation = build_conversation_runtime(
            db_path=db_path,
            db_busy_timeout_ms=db_busy_timeout_ms,
            sse_heartbeat_ms=sse_heartbeat_ms,
            backend_child_factories=backend_child_factories,
            remembered_codex_login_rejection=remembered_codex_login_rejection,
            cleanup=cleanup,
            signal_composition=composition,
        )
        try:
            composition.initialize()
            binding.attach()
            conversation.system.require_runtime_ownership()
            composition.coordinator.admit_runtime()
            yield conversation
        finally:
            composition.coordinator.invalidate("shutdown")
            # A callback error must still enter every cleanup in the outer stack.
            await shutdown_dependents()

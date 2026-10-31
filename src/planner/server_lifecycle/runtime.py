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
    defer_view_cleanup: bool = False,
) -> AsyncIterator[ConversationRuntime]:
    """Enter phases synchronously, then yield one fully installed runtime.

    No producer gate spans an await. Cleanup preserves the exact borrowed handle
    and immediate successful-constructor cleanup, including partial entry failures.
    """
    failure: BaseException | None = None
    try:
        async with AsyncExitStack() as cleanup:
            try:
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
                    defer_view_cleanup=defer_view_cleanup,
                )
                try:
                    composition.initialize()
                    binding.attach()
                    conversation.system.require_runtime_ownership()
                    composition.coordinator.admit_runtime()
                    yield conversation
                except BaseException as error:
                    failure = error
                    raise
                finally:
                    shutdown_failures: list[BaseException] = []
                    try:
                        composition.coordinator.invalidate("shutdown")
                    except BaseException as error:
                        shutdown_failures.append(error)
                    try:
                        await shutdown_dependents()
                    except BaseException as error:
                        shutdown_failures.append(error)
                    for shutdown_error in shutdown_failures:
                        if failure is None:
                            failure = shutdown_error
                        else:
                            failure.add_note(
                                f"application shutdown also failed: {shutdown_error!r}"
                            )
                            for note in getattr(shutdown_error, "__notes__", ()):
                                failure.add_note(note)
                    if failure is not None:
                        raise failure
            except BaseException as error:
                if failure is None:
                    failure = error
                raise
    except BaseException as error:
        # AsyncExitStack still runs every registered cleanup. Its last error must
        # not replace the original startup, body, or dependent-shutdown failure.
        if failure is not None and error is not failure:
            failure.add_note(f"runtime cleanup also failed: {error!r}")
            for note in getattr(error, "__notes__", ()):
                failure.add_note(note)
            raise failure from None
        raise

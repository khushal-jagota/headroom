from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from planner.conversation.backend_contracts import (
    AgentBackendDefinition,
    BackendTurnCapabilities,
    ReverseServiceCapabilities,
)
from planner.conversation.contracts import (
    ContextCompaction,
    ConversationEmployee,
    ConversationSessionBinding,
)
from planner.conversation.runtime_ports import (
    ConversationCompactionCaptureDeadlineExpired,
    ConversationCompactionCaptureFailed,
    ConversationRuntimeHandle,
    ConversationRuntimeLease,
    GenerationBoundBackendTurnStrategy,
)


class _Child:
    alive = True


class _InPlaceStrategy:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.calls: list[ConversationSessionBinding] = []

    async def capture_compaction_in_place(
        self, binding: ConversationSessionBinding
    ) -> object:
        self.calls.append(binding)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class _Runtime:
    def __init__(
        self,
        handle: ConversationRuntimeHandle,
        *,
        lease_error: BaseException | None = None,
    ) -> None:
        self.handle = handle
        self.lease_error = lease_error
        self.calls: list[str] = []

    async def acquire_runtime_lease(
        self, handle: ConversationRuntimeHandle
    ) -> ConversationRuntimeLease:
        assert handle is self.handle
        self.calls.append("acquire")
        if self.lease_error is not None:
            raise self.lease_error
        return ConversationRuntimeLease(handle)

    async def prepare_compaction_capture(self, *_args: Any, **_kwargs: Any) -> Any:
        self.calls.append("prepare/fork/private-load")
        raise AssertionError("in-place compaction must not prepare a fork")

    async def commit_compaction_capture(self, *_args: Any, **_kwargs: Any) -> Any:
        self.calls.append("durable-CAS/transition")
        raise AssertionError("in-place compaction must not commit a transition")

    async def abort_compaction_capture(self, *_args: Any, **_kwargs: Any) -> Any:
        self.calls.append("abort-transition")
        raise AssertionError("in-place compaction must not abort a transition")


def _fixture(
    strategy: Any,
    *,
    lease_error: BaseException | None = None,
) -> tuple[
    GenerationBoundBackendTurnStrategy,
    _Runtime,
    ConversationSessionBinding,
]:
    employee = ConversationEmployee(
        employee_id="employee-in-place",
        entity_kind="ticket",
        entity_id="ticket-in-place",
        workspace_roots=(Path("/tmp"),),
        backend_key="in-place",
    )
    binding = ConversationSessionBinding(
        employee_id=employee.employee_id,
        acp_session_id="session-in-place",
        backend_key=employee.backend_key,
        binding_generation=7,
    )
    definition = AgentBackendDefinition(
        backend_key=employee.backend_key,
        argv=("/in-place",),
        inherited_environment_names=(),
        environment_overrides=(),
        expected_agent_name="in-place",
        expected_agent_version="1",
        turn_capabilities=BackendTurnCapabilities(
            supports_steer=False,
            observes_compaction=True,
        ),
        reverse_service_capabilities=ReverseServiceCapabilities(
            filesystem=False,
            terminal=False,
            permission=True,
        ),
        working_directory_resolver=lambda value: value.workspace_roots[0],
        turn_strategy=strategy,
    )
    handle = ConversationRuntimeHandle(
        employee=employee,
        binding=binding,
        child_generation=3,
        child=_Child(),  # type: ignore[arg-type]
        definition=definition,
        record_identity=object(),
    )
    runtime = _Runtime(handle, lease_error=lease_error)
    wrapper = GenerationBoundBackendTurnStrategy(
        runtime,  # type: ignore[arg-type]
        ConversationRuntimeLease(handle),
        strategy,
    )
    return wrapper, runtime, binding


@pytest.mark.parametrize(
    "terminal",
    [
        ContextCompaction(
            boundary_id="provider-compact",
            state="compacted",
            trigger="automatic",
        ),
        ContextCompaction(
            boundary_id="provider-failed",
            state="failed",
            trigger="automatic",
            reason="Compacting failed: exact provider reason",
        ),
    ],
)
def test_in_place_compaction_returns_exact_terminal_without_runtime_transition(
    terminal: ContextCompaction,
) -> None:
    async def exercise() -> None:
        strategy = _InPlaceStrategy(terminal)
        wrapper, runtime, binding = _fixture(strategy)

        result = await wrapper.capture_compaction_with_deadline(
            binding,
            asyncio.get_running_loop().time() + 1,
            capture_timeout_seconds=300,
        )

        assert result is terminal
        assert strategy.calls == [binding]
        assert runtime.calls == ["acquire"]
        assert wrapper.take_capture_transition() is None

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "invalid",
    [
        ContextCompaction(
            boundary_id="provider-still-running",
            state="compacting",
            trigger="automatic",
        ),
        "not a ContextCompaction",
    ],
)
def test_in_place_compaction_rejects_non_terminal_or_untyped_output(
    invalid: object,
) -> None:
    async def exercise() -> None:
        wrapper, runtime, binding = _fixture(_InPlaceStrategy(invalid))

        with pytest.raises(ConversationCompactionCaptureFailed) as raised:
            await wrapper.capture_compaction_with_deadline(
                binding, asyncio.get_running_loop().time() + 1
            )

        error = raised.value
        assert error.phase == "backend observation"
        assert error.generation_fatal is False
        assert str(error) == (
            "Conversation compaction capture failed during backend observation: "
            "ConversationRuntimeUnavailable: backend in-place compaction did not "
            "produce a terminal ContextCompaction"
        )
        assert runtime.calls == ["acquire"]

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "provider_error",
    [
        RuntimeError("adapter reported compaction failure"),
        TimeoutError("adapter observation timed out immediately"),
    ],
)
def test_in_place_compaction_preserves_concrete_provider_failure(
    provider_error: BaseException,
) -> None:
    async def exercise() -> None:
        wrapper, runtime, binding = _fixture(_InPlaceStrategy(provider_error))

        with pytest.raises(ConversationCompactionCaptureFailed) as raised:
            await wrapper.capture_compaction_with_deadline(
                binding, asyncio.get_running_loop().time() + 60
            )

        error = raised.value
        assert error.phase == "backend observation"
        assert error.primary_error is provider_error
        assert error.generation_fatal is False
        assert type(provider_error).__name__ in str(error)
        assert str(provider_error) in str(error)
        assert "exceeded its configured" not in str(error)
        assert runtime.calls == ["acquire"]

    asyncio.run(exercise())


def test_in_place_compaction_maps_only_panels_timer_to_nonfatal_stayed_deadline() -> None:
    class WaitingStrategy:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled_cleanup = asyncio.Event()

        async def capture_compaction_in_place(
            self, binding: ConversationSessionBinding
        ) -> ContextCompaction:
            del binding
            self.started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled_cleanup.set()
            raise AssertionError("unreachable")

    async def exercise() -> None:
        strategy = WaitingStrategy()
        wrapper, runtime, binding = _fixture(strategy)

        with pytest.raises(ConversationCompactionCaptureDeadlineExpired) as raised:
            await wrapper.capture_compaction_with_deadline(
                binding,
                asyncio.get_running_loop().time() + 0.01,
                capture_timeout_seconds=300,
            )

        await asyncio.wait_for(strategy.cancelled_cleanup.wait(), timeout=1)
        error = raised.value
        assert strategy.started.is_set()
        assert error.phase == "backend observation"
        assert error.capture_timeout_seconds == 300
        assert error.durable_binding_disposition == "stayed"
        assert error.original_binding_generation == 7
        assert error.generation_fatal is False
        assert str(error) == (
            "Conversation compaction capture exceeded its configured 300-second budget "
            "during backend observation; durable binding stayed on generation 7"
        )
        assert runtime.calls == ["acquire"]

    asyncio.run(exercise())


def test_in_place_compaction_propagates_cancellation_and_cancels_provider_wait() -> None:
    class WaitingStrategy:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled_cleanup = asyncio.Event()

        async def capture_compaction_in_place(
            self, binding: ConversationSessionBinding
        ) -> ContextCompaction:
            del binding
            self.started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled_cleanup.set()
            raise AssertionError("unreachable")

    async def exercise() -> None:
        strategy = WaitingStrategy()
        wrapper, runtime, binding = _fixture(strategy)
        capture = asyncio.create_task(
            wrapper.capture_compaction_with_deadline(
                binding, asyncio.get_running_loop().time() + 60
            )
        )
        await strategy.started.wait()

        capture.cancel()
        with pytest.raises(asyncio.CancelledError):
            await capture
        await asyncio.wait_for(strategy.cancelled_cleanup.wait(), timeout=1)
        assert runtime.calls == ["acquire"]

    asyncio.run(exercise())


def test_in_place_compaction_requires_exact_lease_before_hook_detection() -> None:
    async def exercise() -> None:
        lease_error = RuntimeError("exact runtime lease was replaced")
        strategy = _InPlaceStrategy(
            ContextCompaction(
                boundary_id="must-not-run",
                state="compacted",
                trigger="automatic",
            )
        )
        wrapper, runtime, binding = _fixture(strategy, lease_error=lease_error)

        with pytest.raises(ConversationCompactionCaptureFailed) as raised:
            await wrapper.capture_compaction_with_deadline(
                binding, asyncio.get_running_loop().time() + 1
            )

        error = raised.value
        assert error.phase == "hub admission"
        assert error.primary_error is lease_error
        assert error.generation_fatal is False
        assert strategy.calls == []
        assert runtime.calls == ["acquire"]

    asyncio.run(exercise())

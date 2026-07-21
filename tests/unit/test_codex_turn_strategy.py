from __future__ import annotations

import asyncio
from typing import Any

from acp.schema import (
    AgentMessageChunk,
    PromptRequest,
    SessionNotification,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
)

from planner.conversation.codex_turn_strategy import CodexAcpTurnStrategy
from planner.conversation.contracts import (
    ContextCompaction,
    ConversationCompactionBoundaryProvenance,
    ConversationSessionBinding,
)


def _binding() -> ConversationSessionBinding:
    return ConversationSessionBinding(
        employee_id="employee-codex",
        acp_session_id="session-codex",
        backend_key="codex",
        binding_generation=4,
    )


def _notification(update: Any, *, session_id: str = "session-codex") -> SessionNotification:
    return SessionNotification(session_id=session_id, update=update)


def _start(tool_call_id: str = "compact-1") -> SessionNotification:
    return _notification(
        ToolCallStart(
            session_update="tool_call",
            tool_call_id=tool_call_id,
            kind="other",
            title="Context compacting",
            status="in_progress",
            field_meta={"contextCompaction": True},
        )
    )


def _terminal(*, tool_call_id: str = "compact-1") -> SessionNotification:
    return _notification(
        ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id=tool_call_id,
            title="Context compacted",
            status="completed",
            field_meta={"contextCompaction": True},
        )
    )


def test_codex_strategy_declares_steer_unavailable_without_fallback() -> None:
    async def exercise() -> None:
        strategy = CodexAcpTurnStrategy()
        receipt = await strategy.steer(
            _binding(),
            PromptRequest(
                session_id="session-codex",
                prompt=[TextContentBlock(type="text", text="change direction")],
            ),
            "message-1",
        )
        assert receipt.model_dump() == {
            "client_message_id": "message-1",
            "choice": "steer",
            "state": "rejected",
            "queue_position": None,
            "reason": "Codex ACP does not support native steer",
        }

    asyncio.run(exercise())


def test_codex_explicit_and_automatic_compaction_have_exact_lifecycle() -> None:
    async def exercise() -> None:
        strategy = CodexAcpTurnStrategy()
        start = strategy.observe_compaction(_binding(), _start())
        assert start == ContextCompaction(
            boundary_id="employee-codex:session-codex:4:compact-1",
            state="compacting",
            trigger="automatic",
        )
        assert strategy.observe_compaction(_binding(), _start()) == start
        assert strategy.observe_compaction(_binding(), _terminal()) is None

        completed = await strategy.capture_compaction_in_place(_binding())
        assert completed == ContextCompaction(
            boundary_id=start.boundary_id,
            state="compacted",
            trigger="automatic",
        )

    asyncio.run(exercise())


def test_codex_compaction_requires_exact_namespaced_shapes_and_session() -> None:
    strategy = CodexAcpTurnStrategy()
    binding = _binding()
    mutations = (
        _notification(_start().update, session_id="another-session"),
        _notification(_start().update.model_copy(update={"field_meta": None})),
        _notification(
            _start().update.model_copy(
                update={"field_meta": {"contextCompaction": False}}
            )
        ),
        _notification(_start().update.model_copy(update={"title": "Context compacting?"})),
        _notification(_start().update.model_copy(update={"status": "pending"})),
        _terminal(),
    )
    assert all(strategy.observe_compaction(binding, item) is None for item in mutations)


def test_codex_capture_wait_is_cancel_safe_and_does_not_poison_next_compaction() -> None:
    async def exercise() -> None:
        strategy = CodexAcpTurnStrategy()
        strategy.observe_compaction(_binding(), _start())
        waiting = asyncio.create_task(strategy.capture_compaction_in_place(_binding()))
        await asyncio.sleep(0)
        waiting.cancel()
        try:
            await waiting
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("capture must propagate cancellation")

        next_start = strategy.observe_compaction(_binding(), _start("compact-next"))
        assert next_start is not None
        strategy.observe_compaction(
            _binding(), _terminal(tool_call_id="compact-next")
        )
        assert (await strategy.capture_compaction_in_place(_binding())).state == "compacted"

    asyncio.run(exercise())


def test_codex_prompt_failure_reason_is_exact_only_for_compaction_and_resets_state() -> None:
    strategy = CodexAcpTurnStrategy()
    compact = PromptRequest(
        session_id="session-codex",
        prompt=[TextContentBlock(type="text", text="/compact")],
    )
    ordinary = PromptRequest(
        session_id="session-codex",
        prompt=[TextContentBlock(type="text", text="ordinary work")],
    )

    assert strategy.prompt_failure_reason(
        _binding(), compact, RuntimeError("explicit compaction rejected")
    ) == "RuntimeError: explicit compaction rejected"
    assert strategy.prompt_failure_reason(
        _binding(), ordinary, RuntimeError("ordinary prompt rejected")
    ) is None
    assert strategy.prompt_failure_reason(
        _binding(),
        compact.model_copy(update={"session_id": "another-session"}),
        RuntimeError("wrong session"),
    ) is None
    assert strategy.prompt_failure_reason(
        _binding(),
        compact.model_copy(
            update={
                "prompt": [TextContentBlock(type="text", text=" /compact ")]
            }
        ),
        RuntimeError("not exact"),
    ) is None

    started = strategy.observe_compaction(_binding(), _start("automatic-compact"))
    assert started is not None
    assert strategy.prompt_failure_reason(
        _binding(), ordinary, ValueError("automatic compaction rejected")
    ) == "ValueError: automatic compaction rejected"
    replacement = strategy.observe_compaction(_binding(), _start("replacement-compact"))
    assert replacement is not None
    assert replacement.boundary_id.endswith(":replacement-compact")


def test_codex_prompt_failure_reason_rejects_non_display_safe_error_and_resets() -> None:
    strategy = CodexAcpTurnStrategy()
    strategy.observe_compaction(_binding(), _start("unsafe-compact"))
    ordinary = PromptRequest(
        session_id="session-codex",
        prompt=[TextContentBlock(type="text", text="ordinary work")],
    )

    assert strategy.prompt_failure_reason(
        _binding(), ordinary, RuntimeError("unsafe\nreason")
    ) is None
    assert strategy.observe_compaction(
        _binding(), _start("after-unsafe-failure")
    ) is not None


def test_codex_replay_stays_typed_and_adds_only_durable_content_free_boundaries() -> None:
    strategy = CodexAcpTurnStrategy()
    ordinary = _notification(
        AgentMessageChunk(
            session_update="agent_message_chunk",
            content=TextContentBlock(type="text", text="ordinary"),
        )
    )
    boundaries = (
        ConversationCompactionBoundaryProvenance(
            boundary_id="panels-explicit",
            trigger="explicit",
        ),
        ConversationCompactionBoundaryProvenance(
            boundary_id="panels-automatic",
            trigger="automatic",
        ),
    )

    assert strategy.classify_replay(_binding(), (ordinary,), ()) == (ordinary,)
    assert strategy.classify_replay(_binding(), (ordinary,), boundaries) == (
        ordinary,
        ContextCompaction(
            boundary_id="panels-explicit",
            state="compacted",
            trigger="explicit",
        ),
        ContextCompaction(
            boundary_id="panels-automatic",
            state="compacted",
            trigger="automatic",
        ),
    )

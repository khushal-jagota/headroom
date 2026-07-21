from __future__ import annotations

import asyncio

from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    PromptRequest,
    SessionNotification,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
)
from tests.support.acp_conformance import (
    CompactionEvidence,
    DeliveryCapabilityEvidence,
    assert_all_probes,
)
from tests.support.acp_reference_subject import ReferenceAcpConformanceSubject

from planner.conversation.codex_turn_strategy import CodexAcpTurnStrategy
from planner.conversation.contracts import ConversationSessionBinding


def _binding() -> ConversationSessionBinding:
    return ConversationSessionBinding(
        employee_id="employee-codex",
        acp_session_id="session-codex",
        backend_key="codex",
        binding_generation=1,
    )


class _CodexConformanceSubject:
    def __init__(
        self,
        base: ReferenceAcpConformanceSubject,
        delivery: DeliveryCapabilityEvidence,
        compaction: CompactionEvidence,
    ) -> None:
        self._base = base
        self._delivery = delivery
        self._compaction = compaction

    def observe_load_replay(self):
        return self._base.observe_load_replay()

    def observe_typed_thought(self):
        return self._base.observe_typed_thought()

    def observe_message_grouping(self):
        return self._base.observe_message_grouping()

    def observe_plan_tool_reconciliation(self):
        return self._base.observe_plan_tool_reconciliation()

    def observe_permission_settlement(self):
        return self._base.observe_permission_settlement()

    def observe_refresh_binding(self):
        return self._base.observe_refresh_binding()

    def observe_delivery_capabilities(self) -> DeliveryCapabilityEvidence:
        return self._delivery

    def observe_compaction(self) -> CompactionEvidence:
        return self._compaction

    def observe_callback_order(self):
        return self._base.observe_callback_order()

    def observe_protocol_rejection(self):
        return self._base.observe_protocol_rejection()


def test_codex_live_and_loaded_updates_pass_common_conformance() -> None:
    async def exercise() -> None:
        strategy = CodexAcpTurnStrategy()
        receipt = await strategy.steer(
            _binding(),
            PromptRequest(
                session_id="session-codex",
                prompt=[TextContentBlock(type="text", text="steer")],
            ),
            "steer-message",
        )
        base = await ReferenceAcpConformanceSubject.create()
        compaction_states: list[str] = []
        started = strategy.observe_compaction(
            _binding(),
            SessionNotification(
                session_id="session-codex",
                update=ToolCallStart(
                    session_update="tool_call",
                    tool_call_id="compact-conformance",
                    kind="other",
                    title="Context compacting",
                    status="in_progress",
                    field_meta={"contextCompaction": True},
                ),
            ),
        )
        assert started is not None
        compaction_states.append(started.state)
        strategy.observe_compaction(
            _binding(),
            SessionNotification(
                session_id="session-codex",
                update=ToolCallProgress(
                    session_update="tool_call_update",
                    tool_call_id="compact-conformance",
                    title="Context compacted",
                    status="completed",
                    field_meta={"contextCompaction": True},
                ),
            ),
        )
        compaction_states.append(
            (await strategy.capture_compaction_in_place(_binding())).state
        )
        subject = _CodexConformanceSubject(
            base,
            DeliveryCapabilityEvidence(
                supports_steer=False,
                steer_result=(
                    "rejected_unavailable"
                    if receipt.state == "rejected"
                    else "accepted_native"
                ),
                unsupported_steer_visible=receipt.reason is not None,
                broker_operations=("queue", "send_now"),
            ),
            CompactionEvidence(
                explicit_states=tuple(compaction_states),
                automatic_states=tuple(compaction_states),
            ),
        )
        assert_all_probes(subject)

    asyncio.run(exercise())


def test_codex_stored_plan_replay_matches_pinned_upstream_limitation() -> None:
    strategy = CodexAcpTurnStrategy()
    stored_plan = SessionNotification(
        session_id="session-codex",
        update=AgentMessageChunk(
            session_update="agent_message_chunk",
            content=TextContentBlock(
                type="text",
                text="Plan:\n1. Inspect\n2. Change",
            ),
        ),
    )

    replay = strategy.classify_replay(_binding(), (stored_plan,), ())

    assert replay == (stored_plan,)
    assert isinstance(replay[0], SessionNotification)
    assert isinstance(replay[0].update, AgentMessageChunk)
    assert not isinstance(replay[0].update, AgentPlanUpdate)

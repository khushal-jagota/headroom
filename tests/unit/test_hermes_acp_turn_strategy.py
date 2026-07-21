from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import pytest
from acp.schema import (
    AgentMessageChunk,
    ImageContentBlock,
    PromptRequest,
    PromptResponse,
    SessionInfoUpdate,
    SessionNotification,
    TextContentBlock,
    UserMessageChunk,
)

from planner.conversation.backend_contracts import (
    AgentBackendDefinition,
    BackendTurnCapabilities,
    ReverseServiceCapabilities,
)
from planner.conversation.contracts import (
    ContextCompaction,
    ConversationCompactionBoundaryProvenance,
    ConversationEmployee,
    ConversationSessionBinding,
)
from planner.conversation.hermes_turn_strategy import (
    HERMES_MERGED_PRIOR_CONTEXT_HEADER,
    HERMES_MERGED_SUMMARY_DELIMITER,
    HERMES_SUMMARY_END_MARKER,
    HERMES_SUMMARY_PREFIX,
    HermesAcpTurnStrategy,
)
from planner.conversation.runtime_ports import (
    ConversationRuntimeHandle,
    ConversationRuntimeLease,
    ConversationRuntimeUnavailable,
    GenerationBoundBackendTurnStrategy,
)
from planner.conversation.wire_contracts import ProtocolUpdateRejectedPayload


def _binding() -> ConversationSessionBinding:
    return ConversationSessionBinding(
        employee_id="employee-a",
        acp_session_id="acp-a",
        backend_key="hermes",
        binding_generation=1,
    )


def _notification(update: Any) -> SessionNotification:
    return SessionNotification(session_id="acp-a", update=update)


def test_steer_delivers_one_real_prefixed_concurrent_prompt() -> None:
    async def exercise() -> None:
        delivered: list[PromptRequest] = []

        async def concurrent(binding: Any, client_message_id: str, prompt: PromptRequest):
            assert binding == _binding()
            assert client_message_id == "m-steer"
            delivered.append(prompt)
            return PromptResponse(stop_reason="end_turn")

        strategy = HermesAcpTurnStrategy(concurrent_prompt=concurrent, capture_updates=None)
        receipt = await strategy.steer(
            _binding(),
            PromptRequest(
                session_id="acp-a",
                prompt=[TextContentBlock(type="text", text="change direction")],
            ),
            "m-steer",
        )
        assert receipt.state == "accepted"
        assert delivered[0].prompt == [
            TextContentBlock(type="text", text="/steer change direction")
        ]

        rejected = await strategy.steer(
            _binding(),
            PromptRequest(
                session_id="acp-a",
                prompt=[ImageContentBlock(type="image", mime_type="image/png", data="AA==")],
            ),
            "m-image",
        )
        assert rejected.state == "rejected"
        assert len(delivered) == 1

    asyncio.run(exercise())


def test_only_exact_hermes_compression_provenance_is_observed_and_deduplicable() -> None:
    strategy = HermesAcpTurnStrategy(concurrent_prompt=None, capture_updates=None)
    update = SessionInfoUpdate(
        session_update="session_info_update",
        field_meta={
            "hermes": {
                "sessionProvenance": {
                    "reason": "compression",
                    "acpSessionId": "acp-a",
                    "previousHermesSessionId": "old",
                    "currentHermesSessionId": "new",
                    "compressionDepth": 2,
                }
            }
        },
    )
    observed = strategy.observe_compaction(_binding(), _notification(update))
    assert observed is not None
    assert observed.state == "compacting"
    assert observed.trigger == "automatic"
    assert observed.boundary_id == "old:new:2"
    partial = update.model_copy(update={"field_meta": {"hermes": {}}})
    assert strategy.observe_compaction(_binding(), _notification(partial)) is None


def test_pinned_hermes_summary_prefix_matches_inspected_source() -> None:
    assert len(HERMES_SUMMARY_PREFIX) == 1428
    assert hashlib.sha256(HERMES_SUMMARY_PREFIX.encode()).hexdigest() == (
        "f953b27ebb6288ec9aea994184be18cbc56783d2237241df2b1f24b55f3b0230"
    )


@pytest.mark.parametrize("role", ["user", "assistant"])
@pytest.mark.parametrize("merged", [False, True])
def test_capture_accepts_all_pinned_private_marker_placements(role: str, merged: bool) -> None:
    async def exercise() -> None:
        summary = HERMES_SUMMARY_PREFIX + "\nkept state"
        if merged:
            text = (
                HERMES_MERGED_PRIOR_CONTEXT_HEADER
                + "\nold tail\n\n"
                + HERMES_MERGED_SUMMARY_DELIMITER
                + "\n\n"
                + summary
                + "\n\n"
                + HERMES_SUMMARY_END_MARKER
            )
        else:
            text = summary + "\n\n" + HERMES_SUMMARY_END_MARKER
        update_type = UserMessageChunk if role == "user" else AgentMessageChunk

        async def capture(binding: Any):
            assert binding == _binding()
            return (
                _notification(
                    update_type(
                        session_update=(
                            "user_message_chunk" if role == "user" else "agent_message_chunk"
                        ),
                        content=TextContentBlock(type="text", text=text),
                    )
                ),
            )

        strategy = HermesAcpTurnStrategy(concurrent_prompt=None, capture_updates=capture)
        result = await strategy.capture_compaction(_binding())
        assert result.state == "compacted"
        assert "summary" not in result.model_dump()

    asyncio.run(exercise())


def test_capture_fails_when_private_markers_are_ambiguous_or_malformed() -> None:
    valid = HERMES_SUMMARY_PREFIX + "\nsummary\n\n" + HERMES_SUMMARY_END_MARKER
    ambiguous = tuple(
        _notification(
            UserMessageChunk(
                session_update="user_message_chunk",
                content=TextContentBlock(type="text", text=valid),
            )
        )
        for _ in range(2)
    )
    malformed = (
        _notification(
            UserMessageChunk(
                session_update="user_message_chunk",
                content=TextContentBlock(
                    type="text", text=HERMES_SUMMARY_PREFIX + "\nunterminated"
                ),
            )
        ),
    )
    strategy = HermesAcpTurnStrategy(concurrent_prompt=None, capture_updates=None)

    for replay in (ambiguous, malformed):
        result = strategy.capture_compaction_from_updates(_binding(), replay)
        assert result.state == "failed"
        assert result.reason == "Compacted private context marker was invalid"


def test_capture_fails_for_a_protocol_rejected_replay() -> None:
    result = HermesAcpTurnStrategy(
        concurrent_prompt=None, capture_updates=None
    ).capture_compaction_from_updates(
        _binding(),
        (
            ProtocolUpdateRejectedPayload(
                rejected_session_update="future_update",
                reason="unsupported",
                status="Agent sent an unsupported update",
            ),
        ),
    )

    assert result.state == "failed"
    assert result.reason == "Compaction capture did not match the protocol"


def test_capture_accepts_a_compacted_replay_without_a_presentable_summary() -> None:
    """The live two-message Hermes fork copied context without a summary marker."""

    strategy = HermesAcpTurnStrategy(concurrent_prompt=None, capture_updates=None)
    replay = (
        _notification(
            UserMessageChunk(
                session_update="user_message_chunk",
                content=TextContentBlock(
                    type="text", text="Reply exactly ACP REPLAY PREPARED 20260720."
                ),
            )
        ),
        _notification(
            AgentMessageChunk(
                session_update="agent_message_chunk",
                content=TextContentBlock(
                    type="text", text="ACP REPLAY PREPARED 20260720."
                ),
            )
        ),
    )

    result = strategy.capture_compaction_from_updates(_binding(), replay)

    assert result.state == "compacted"
    assert "summary" not in result.model_dump()


@pytest.mark.parametrize("role", ["user", "assistant"])
@pytest.mark.parametrize("merged", [False, True])
@pytest.mark.parametrize("boundary_count", [1, 2])
def test_classify_replay_replaces_summary_in_place_with_durable_boundaries(
    role: str,
    merged: bool,
    boundary_count: int,
) -> None:
    strategy = HermesAcpTurnStrategy(concurrent_prompt=None, capture_updates=None)
    normalized_summary = HERMES_SUMMARY_PREFIX + "\nkept state"
    summary_text = normalized_summary + "\n\n" + HERMES_SUMMARY_END_MARKER
    if merged:
        summary_text = (
            HERMES_MERGED_PRIOR_CONTEXT_HEADER
            + "\nprior tail\n"
            + HERMES_MERGED_SUMMARY_DELIMITER
            + "\n"
            + summary_text
        )
    update_type = UserMessageChunk if role == "user" else AgentMessageChunk
    update_discriminator = (
        "user_message_chunk" if role == "user" else "agent_message_chunk"
    )
    before = _notification(
        AgentMessageChunk(
            session_update="agent_message_chunk",
            message_id="before",
            content=TextContentBlock(type="text", text="before"),
        )
    )
    summary = _notification(
        update_type(
            session_update=update_discriminator,
            message_id="summary",
            content=TextContentBlock(type="text", text=summary_text),
        )
    )
    after = _notification(
        AgentMessageChunk(
            session_update="agent_message_chunk",
            message_id="after",
            content=TextContentBlock(type="text", text="after"),
        )
    )
    boundaries = (
        ConversationCompactionBoundaryProvenance(
            boundary_id="explicit-boundary",
            trigger="explicit",
        ),
        ConversationCompactionBoundaryProvenance(
            boundary_id="automatic-boundary",
            trigger="automatic",
        ),
    )[:boundary_count]

    classified = strategy.classify_replay(
        _binding(),
        (before, summary, after),
        boundaries,
    )

    assert classified[0] is before
    assert classified[-1] is after
    assert classified[1:-1] == tuple(
        ContextCompaction(
            boundary_id=boundary.boundary_id,
            state="compacted",
            trigger=boundary.trigger,
        )
        for boundary in boundaries
    )


def test_classify_replay_keeps_an_ordinary_uncompacted_batch_unchanged() -> None:
    strategy = HermesAcpTurnStrategy(concurrent_prompt=None, capture_updates=None)
    ordinary = _notification(
        AgentMessageChunk(
            session_update="agent_message_chunk",
            content=TextContentBlock(type="text", text="ordinary"),
        )
    )
    replay = (ordinary,)

    assert strategy.classify_replay(_binding(), replay, ()) == replay


def test_classify_replay_appends_durable_boundaries_after_marker_free_history() -> None:
    strategy = HermesAcpTurnStrategy(concurrent_prompt=None, capture_updates=None)
    ordinary = _notification(
        AgentMessageChunk(
            session_update="agent_message_chunk",
            content=TextContentBlock(type="text", text="ordinary"),
        )
    )
    boundary = ConversationCompactionBoundaryProvenance(
        boundary_id="boundary", trigger="explicit"
    )

    classified = strategy.classify_replay(_binding(), (ordinary,), (boundary,))

    assert classified[0] is ordinary
    assert classified[1] == ContextCompaction(
        boundary_id="boundary", state="compacted", trigger="explicit"
    )


@pytest.mark.parametrize(
    "replay,boundaries",
    [
        (
            (
                _notification(
                    UserMessageChunk(
                        session_update="user_message_chunk",
                        content=TextContentBlock(
                            type="text",
                            text=(
                                HERMES_SUMMARY_PREFIX
                                + "\nsummary\n\n"
                                + HERMES_SUMMARY_END_MARKER
                            ),
                        ),
                    )
                ),
            ),
            (),
        ),
        (
            tuple(
                _notification(
                    UserMessageChunk(
                        session_update="user_message_chunk",
                        content=TextContentBlock(
                            type="text",
                            text=(
                                HERMES_SUMMARY_PREFIX
                                + "\nsummary\n\n"
                                + HERMES_SUMMARY_END_MARKER
                            ),
                        ),
                    )
                )
                for _ in range(2)
            ),
            (
                ConversationCompactionBoundaryProvenance(
                    boundary_id="boundary", trigger="explicit"
                ),
            ),
        ),
        (
            (
                _notification(
                    UserMessageChunk(
                        session_update="user_message_chunk",
                        content=TextContentBlock(
                            type="text", text=HERMES_SUMMARY_PREFIX + "\nunterminated"
                        ),
                    )
                ),
            ),
            (
                ConversationCompactionBoundaryProvenance(
                    boundary_id="boundary", trigger="explicit"
                ),
            ),
        ),
        (
            (
                SessionNotification(
                    session_id="wrong-session",
                    update=UserMessageChunk(
                        session_update="user_message_chunk",
                        content=TextContentBlock(
                            type="text",
                            text=(
                                HERMES_SUMMARY_PREFIX
                                + "\nsummary\n\n"
                                + HERMES_SUMMARY_END_MARKER
                            ),
                        ),
                    ),
                ),
            ),
            (
                ConversationCompactionBoundaryProvenance(
                    boundary_id="boundary", trigger="explicit"
                ),
            ),
        ),
        (
            (
                ProtocolUpdateRejectedPayload(
                    rejected_session_update="future_update",
                    reason="unsupported",
                    status="Agent sent an unsupported update",
                ),
            ),
            (
                ConversationCompactionBoundaryProvenance(
                    boundary_id="boundary", trigger="explicit"
                ),
            ),
        ),
    ],
    ids=(
        "summary-without-provenance",
        "multiple-summaries",
        "malformed-summary",
        "wrong-session-summary",
        "rejected-replay",
    ),
)
def test_classify_replay_fails_closed_for_unmatched_replay_and_provenance(
    replay: tuple[SessionNotification | ProtocolUpdateRejectedPayload, ...],
    boundaries: tuple[ConversationCompactionBoundaryProvenance, ...],
) -> None:
    strategy = HermesAcpTurnStrategy(concurrent_prompt=None, capture_updates=None)

    with pytest.raises(ValueError, match="compacted replay"):
        strategy.classify_replay(_binding(), replay, boundaries)


def test_sequential_compaction_classification_uses_only_each_current_binding_boundary() -> None:
    strategy = HermesAcpTurnStrategy(concurrent_prompt=None, capture_updates=None)
    summary = _notification(
        UserMessageChunk(
            session_update="user_message_chunk",
            content=TextContentBlock(
                type="text",
                text=(
                    HERMES_SUMMARY_PREFIX
                    + "\ncurrent summary\n\n"
                    + HERMES_SUMMARY_END_MARKER
                ),
            ),
        )
    )
    first = ConversationCompactionBoundaryProvenance(
        boundary_id="first", trigger="explicit"
    )
    second = ConversationCompactionBoundaryProvenance(
        boundary_id="second", trigger="automatic"
    )

    first_replay = strategy.classify_replay(_binding(), (summary,), (first,))
    second_replay = strategy.classify_replay(_binding(), (summary,), (second,))

    assert [item.boundary_id for item in first_replay if isinstance(item, ContextCompaction)] == [
        "first"
    ]
    assert [item.boundary_id for item in second_replay if isinstance(item, ContextCompaction)] == [
        "second"
    ]


def test_generation_bound_steer_never_redirects_paused_n_to_n_plus_one() -> None:
    class Child:
        alive = True

        def __init__(self, generation: int) -> None:
            self.generation = generation
            self.requests: list[PromptRequest] = []
            self.response: asyncio.Future[PromptResponse] | None = None

        async def prompt(self, request: PromptRequest) -> PromptResponse:
            self.requests.append(request)
            self.response = asyncio.get_running_loop().create_future()
            return await self.response

    class Runtime:
        def __init__(self, current: ConversationRuntimeHandle) -> None:
            self.current = current

        async def acquire_runtime_lease(self, handle: ConversationRuntimeHandle):
            if (
                handle.record_identity is not self.current.record_identity
                or not handle.child.alive
            ):
                raise ConversationRuntimeUnavailable("stale")
            return ConversationRuntimeLease(handle)

        async def resolve_runtime_handle(
            self, employee_id: str, binding_generation: int
        ) -> ConversationRuntimeHandle:
            del employee_id, binding_generation
            return self.current

        async def retire_runtime_lease(
            self, lease: ConversationRuntimeLease, deadline: float
        ) -> None:
            del lease, deadline

        async def replace_runtime_for_capture(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError((args, kwargs))

        def strategy_for_lease(self, lease: ConversationRuntimeLease) -> Any:
            del lease
            return self.current.definition.turn_strategy

    async def exercise() -> None:
        employee = ConversationEmployee(
            employee_id="employee-a",
            entity_kind="ticket",
            entity_id="ticket-a",
            workspace_roots=(Path("/tmp"),),
            backend_key="hermes",
        )
        async def raw_concurrent_prompt(*_args: Any) -> PromptResponse:
            raise AssertionError("raw callback must not be used")

        strategy = HermesAcpTurnStrategy(
            concurrent_prompt=raw_concurrent_prompt,
            capture_updates=None,
        )
        definition = AgentBackendDefinition(
            backend_key="hermes",
            argv=("/hermes", "acp"),
            inherited_environment_names=(),
            environment_overrides=(),
            expected_agent_name="hermes",
            expected_agent_version="1",
            turn_capabilities=BackendTurnCapabilities(
                supports_steer=True, observes_compaction=True
            ),
            reverse_service_capabilities=ReverseServiceCapabilities(
                filesystem=False, terminal=False, permission=True
            ),
            working_directory_resolver=lambda value: value.workspace_roots[0],
            turn_strategy=strategy,
        )
        child_n = Child(1)
        handle_n = ConversationRuntimeHandle(
            employee=employee,
            binding=_binding(),
            child_generation=1,
            child=child_n,
            definition=definition,
            record_identity=object(),
        )
        runtime = Runtime(handle_n)
        proxy = GenerationBoundBackendTurnStrategy(
            runtime, ConversationRuntimeLease(handle_n), strategy
        )
        delivery = asyncio.create_task(
            proxy.steer(
                _binding(),
                PromptRequest(
                    session_id="acp-a",
                    prompt=[TextContentBlock(type="text", text="new direction")],
                ),
                "steer-n",
            )
        )
        while not child_n.requests:
            await asyncio.sleep(0)
        child_n_plus_one = Child(2)
        runtime.current = ConversationRuntimeHandle(
            employee=employee,
            binding=_binding(),
            child_generation=2,
            child=child_n_plus_one,
            definition=definition,
            record_identity=object(),
        )
        assert child_n.response is not None
        child_n.response.set_result(PromptResponse(stop_reason="end_turn"))
        receipt = await delivery
        assert receipt.state == "accepted"
        assert child_n.requests[0].prompt[0].text == "/steer new direction"
        assert not child_n_plus_one.requests
        with pytest.raises(ConversationRuntimeUnavailable):
            await proxy.steer(
                _binding(),
                PromptRequest(
                    session_id="acp-a",
                    prompt=[TextContentBlock(type="text", text="late")],
                ),
                "late",
            )
        assert not child_n_plus_one.requests

    asyncio.run(exercise())

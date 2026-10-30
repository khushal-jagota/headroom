"""Provider-owned exact requests, policy scope, and durable local admission boundaries."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from tests.unit.test_conversation_system import _FakeBackendChild, _Harness
from tests.unit.test_current_model_resolution import (
    ScopedCatalogue,
    context,
    observation,
    row,
    selection,
    setup,
)

from planner.conversation.admission.policy import recorded_policy_dependencies
from planner.conversation.backends.contracts import BackendPromptRejected, PromptWriteFailed
from planner.conversation.contracts import ConversationBackendKey as Backend
from planner.conversation.contracts import (
    PromptDeliveryUncertain,
    WorkspacePathPolicy,
)
from planner.conversation.events import PromptDeliveryUncertainEventPayload, PromptEventPayload
from planner.conversation.message_content import text_message_content
from planner.conversation.model_resolution.contracts import CatalogWorkspaceBinding, SelectionKind
from planner.conversation.model_resolution.logic.validation import (
    reported_selection_blocks,
    submission_validation,
)
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect
from planner.runtime import conversation_start
from planner.tickets import data as tickets
from planner.tickets import launch_authority as authority


def test_unknown_alias_target_has_no_inferred_policy_or_concrete_mismatch(tmp_db: Any) -> None:
    for kind in (SelectionKind.provider_alias, SelectionKind.concrete, SelectionKind.panels_alias):
        requested = selection("opus", kind, backend_key=Backend.claude)
        result = submission_validation(requested)
        assert result.launch_token == "opus"
        dependencies = recorded_policy_dependencies(
            tmp_db,
            selection=requested,
            launch_token=result.launch_token,
            resolved_model_id=result.resolved_model_id,
            evidence=None,
        )
        assert dependencies.model_ids == ("opus",)
        assert not reported_selection_blocks(
            result,
            reported_model="claude-opus-9",
            reported_effort=None,
            disabled_models=frozenset({"different-model"}),
        )
        assert reported_selection_blocks(
            result,
            reported_model="claude-opus-9",
            reported_effort=None,
            disabled_models=frozenset({"claude-opus-9"}),
        )


def test_panels_alias_needs_mapping_but_custom_token_and_effort_do_not() -> None:
    assert submission_validation(selection("sol", SelectionKind.panels_alias)).launch_token is None
    result = submission_validation(
        selection(
            "provider-future", SelectionKind.legacy_unclassified, effort="provider-future-effort"
        )
    )
    assert result.launch_token == "provider-future"
    assert result.selection.reasoning_effort == "provider-future-effort"
    mapped = submission_validation(
        selection("sol", SelectionKind.panels_alias), observation((row(), row("gpt-7-sol")))
    )
    assert mapped.launch_token == "gpt-7-sol"
    disabled = submission_validation(
        selection("sol", SelectionKind.panels_alias),
        observation((row(),)),
        disabled_models=frozenset({"gpt-6-sol"}),
    )
    assert disabled.launch_token is None


@pytest.mark.parametrize("case", ["unknown", "rebind_unknown", "rejected", "cancel_accepted"])
def test_actual_local_send_retains_exact_ownership_after_possible_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    binding = CatalogWorkspaceBinding(tmp_path, WorkspacePathPolicy.legacy_home_mapping, None)
    launch = context(workspace=binding, localized_workspace_folder=tmp_path)
    source = ScopedCatalogue(observation((row(),), launch=launch))
    service, _, path = setup(tmp_path, source)
    harness = _Harness(path)
    harness.system._model_resolver = service
    harness.system.configure_destination_validation(source)
    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda: tmp_path)
    original = _FakeBackendChild.write_prompt
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def write(self: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if case == "rebind_unknown" and calls == 1:
            from planner.conversation.backends.contracts import NeedsRebind

            raise NeedsRebind("known unsent old child")
        result = await original(self, *args, **kwargs)
        entered.set()
        if case == "cancel_accepted":
            await release.wait()
            return result
        if case == "rejected":
            raise BackendPromptRejected("provider refused correlated request")
        raise PromptWriteFailed("provider acknowledgment absent")

    monkeypatch.setattr(_FakeBackendChild, "write_prompt", write)

    async def exercise() -> None:
        conn = connect(str(path))
        try:
            ticket = tickets.create_ticket(
                conn,
                title="Exact local hold",
                principal=OWNER_PRINCIPAL,
                now=1000,
                title_max_chars=200,
                worker_type="coding",
                employee_backend="codex",
                employee_launch_model="gpt-6-sol",
                selection_kind=SelectionKind.concrete,
            )

            async def send() -> Any:
                async with conversation_start.conversation_link_lock(f"ticket:{ticket.id}"):
                    return await conversation_start.send_to_ticket_conversation(
                        harness.system,
                        conn,
                        ticket.id,
                        text_message_content("one attempt"),
                        conversation_id=None,
                        sender_label="Owner",
                        now=1000,
                    )

            sending = asyncio.create_task(send())
            await entered.wait()
            if case == "cancel_accepted":
                sending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await sending
                release.set()
                await harness.settle()
                conversation_id = tickets.read_ticket(conn, ticket.id).conversation_id
                assert conversation_id is not None
                prompts = [
                    e
                    for e in await harness.events(conversation_id)
                    if isinstance(e.payload, PromptEventPayload)
                ]
                assert len(prompts) == 1
            else:
                result = await sending
                conversation_id = result.conversation_id or harness.spawned_conversation_ids[-1]
                if case == "rejected":
                    assert result.fate.refusal_reason.value == "backend_rejected_prompt"
                else:
                    assert isinstance(result.fate, PromptDeliveryUncertain)
                    state = await harness.system._conversation_state(conversation_id)
                    assert state is not None and state.child_is_quarantined
                    assert state.reserved_turn is not None
                    events = await harness.events(conversation_id)
                    assert any(
                        isinstance(e.payload, PromptDeliveryUncertainEventPayload) for e in events
                    )
                    rows = conn.execute(
                        "SELECT policy_context_json FROM ticket_launch_authority"
                    ).fetchall()
                    assert rows and all('"phase":"submitted"' in r[0] for r in rows)
                    conn.execute(
                        "UPDATE ticket_launch_authority SET owner_process='recovered:dead'"
                    )
                    conn.commit()
                    authority.reconcile_persisted_launch_authority(conn)
                    assert conn.execute("SELECT COUNT(*) FROM ticket_launch_authority").fetchone()[
                        0
                    ]
            if case in ("rejected", "cancel_accepted"):
                assert (
                    conn.execute("SELECT COUNT(*) FROM ticket_launch_authority").fetchone()[0] == 0
                )
        finally:
            conn.close()
            await harness.system.shutdown()

    asyncio.run(asyncio.wait_for(exercise(), 15))


@pytest.mark.parametrize(
    "case",
    ["early", "mismatch", "native_alias", "reported_disabled", "held_unknown", "legacy_held"],
)
def test_real_core_report_and_held_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    from planner.conversation.backends.contracts import BackendPromptAccepted
    from planner.conversation.contracts import (
        HeldPromptPromotionMode,
        PromptDeliveryMode,
        PromptDeliveryRefused,
    )
    from planner.conversation.events import (
        ConversationTurnEnding,
        ProviderSelectionReportedEventPayload,
        ProviderSubmissionFact,
        ToolCallStartedEventPayload,
        TurnEndedEventPayload,
    )

    token = "opus" if case in ("native_alias", "reported_disabled") else "claude-exact"
    binding = CatalogWorkspaceBinding(tmp_path, WorkspacePathPolicy.legacy_home_mapping, None)
    launch = context(
        backend_key=Backend.claude, workspace=binding, localized_workspace_folder=tmp_path
    )
    source = ScopedCatalogue(observation((row(token),), launch=launch))
    service, _, path = setup(tmp_path, source)
    harness = _Harness(path)
    harness.system._model_resolver = service
    harness.system.configure_destination_validation(source)
    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda: tmp_path)
    original = _FakeBackendChild.write_prompt
    buffered_deltas: list[str] = []
    original_publish = harness.system._publish_live_tail_frame

    def publish_frame(state: Any, turn: Any, frame: Any) -> None:
        from planner.conversation.events import AgentMessageDeltaFrame

        if isinstance(frame, AgentMessageDeltaFrame):
            assert state.running_turn is not None and state.running_turn.token == turn
            buffered_deltas.append(frame.text_delta)
        original_publish(state, turn, frame)

    monkeypatch.setattr(harness.system, "_publish_live_tail_frame", publish_frame)
    attempted_tokens: list[str | None] = []
    writes = 0

    async def write(self: Any, turn: Any, content: Any, **kwargs: Any) -> Any:
        nonlocal writes
        writes += 1
        attempted_tokens.append(kwargs["model_change"])
        if case == "held_unknown" and writes > 1:
            raise PromptWriteFailed("acknowledgment absent after write")
        accepted = await original(self, turn, content, **kwargs)
        if case == "early" and writes == 1:
            # These callbacks return while acceptance is still pending. The reader
            # must remain free to observe the correlated provider response.
            await self._sink.agent_message_delta(turn, "early delta")
            await self._sink.provider_selection_reported(
                turn,
                provider=Backend.claude,
                source_kind="response_model",
                model=token,
                child_lifetime="captured-fake-child",
            )
            await self._sink.tool_call_started(
                turn,
                tool_call_id="early-tool",
                title="Tool-only response",
                tool_kind="read",
                detail=None,
            )
            await self._sink.turn_ended(
                turn,
                ending=ConversationTurnEnding.completed,
                error_summary=None,
                standard_error_tail=None,
            )
            return BackendPromptAccepted(
                accepted.composed_content_delivered,
                ProviderSubmissionFact(Backend.claude, "accepted", "command_lifecycle", "uuid-1"),
            )
        return accepted

    monkeypatch.setattr(_FakeBackendChild, "write_prompt", write)

    async def exercise() -> None:
        nonlocal harness
        conn = connect(str(path))
        try:
            ticket = tickets.create_ticket(
                conn,
                title="Captured report",
                principal=OWNER_PRINCIPAL,
                now=1000,
                title_max_chars=200,
                worker_type="coding",
                employee_backend="claude",
                employee_launch_model=token,
                selection_kind=SelectionKind.concrete,
            )
            async with conversation_start.conversation_link_lock(f"ticket:{ticket.id}"):
                result = await conversation_start.send_to_ticket_conversation(
                    harness.system,
                    conn,
                    ticket.id,
                    text_message_content("first"),
                    conversation_id=None,
                    sender_label="Owner",
                    now=1000,
                )
            conversation_id = result.conversation_id
            assert conversation_id is not None
            await harness.settle()
            backend = harness.backend(conversation_id)
            assert backend.sink is not None
            if case == "early":
                events = await harness.events(conversation_id)
                prompt = next(e for e in events if isinstance(e.payload, PromptEventPayload))
                report_event = next(
                    e
                    for e in events
                    if isinstance(e.payload, ProviderSelectionReportedEventPayload)
                )
                tool = next(e for e in events if isinstance(e.payload, ToolCallStartedEventPayload))
                ended = next(e for e in events if isinstance(e.payload, TurnEndedEventPayload))
                assert prompt.sequence < report_event.sequence < tool.sequence < ended.sequence
                assert buffered_deltas == ["early delta"]
                assert isinstance(prompt.payload, PromptEventPayload)
                assert prompt.payload.provider_submission is not None
                assert isinstance(report_event.payload, ProviderSelectionReportedEventPayload)
                assert prompt.payload.provider_submission.correlation_id == "uuid-1"
                assert report_event.payload.turn_number == 1
                assert report_event.payload.child_lifetime == "captured-fake-child"
                from planner.conversation.backends.contracts import TurnToken

                await harness.system.send(
                    conversation_id, text_message_content("second"), sender_label="Owner"
                )
                await backend.sink.provider_selection_reported(
                    TurnToken(conversation_id, 1),
                    provider=Backend.claude,
                    source_kind="response_model",
                    model="late-wrong",
                    child_lifetime="captured-fake-child",
                )
                await harness.settle()
                assert (
                    sum(
                        isinstance(e.payload, ProviderSelectionReportedEventPayload)
                        for e in await harness.events(conversation_id)
                    )
                    == 1
                )
                return
            if case in ("held_unknown", "legacy_held"):
                await harness.system.send(
                    conversation_id, text_message_content("held original"), sender_label="Owner"
                )
                await harness.system.send(
                    conversation_id, text_message_content("held tail"), sender_label="Owner"
                )
                if case == "legacy_held":
                    from dataclasses import replace

                    state = await harness.system._conversation_state(conversation_id)
                    assert state is not None
                    state.held_prompts[0] = replace(state.held_prompts[0], original_selection=None)
                    state.held_prompts.pop()
                    await harness.complete_turn(conversation_id)
                    assert attempted_tokens == [token]
                    from planner.conversation.events import PromptDeliveryRefusedEventPayload

                    assert any(
                        isinstance(e.payload, PromptDeliveryRefusedEventPayload)
                        and "original provider token" in (e.payload.refusal_detail or "")
                        for e in await harness.events(conversation_id)
                    )
                    return
                # A different canonical selection cannot retarget previously held text.
                conn.execute(
                    "UPDATE tickets SET employee_launch_model='claude-future' WHERE id=?",
                    (ticket.id,),
                )
                conn.commit()
                await harness.complete_turn(conversation_id)
                state = await harness.system._conversation_state(conversation_id)
                assert state is not None and state.child_is_quarantined
                assert state.reserved_turn is not None
                assert attempted_tokens == [token, token]
                assert conn.execute("SELECT COUNT(*) FROM ticket_launch_authority").fetchone()[0]
                assert any(
                    isinstance(e.payload, PromptDeliveryUncertainEventPayload)
                    for e in await harness.events(conversation_id)
                )
                return
            if case == "reported_disabled":
                from planner.conversation.backend_state import BackendStateStore

                BackendStateStore(str(path)).write_model_enablement(
                    Backend.claude, "claude-reported-other", False
                )
            current = backend.live_turn_token
            assert current is not None
            await backend.sink.provider_selection_reported(
                current,
                provider=Backend.claude,
                source_kind="response_model",
                model="claude-reported-other",
                child_lifetime="captured-fake-child",
            )
            await harness.settle()
            report = next(
                e.payload
                for e in await harness.events(conversation_id)
                if isinstance(e.payload, ProviderSelectionReportedEventPayload)
            )
            assert report.blocks_submission is (case in ("mismatch", "reported_disabled"))
            direct = await harness.system.send(
                conversation_id,
                text_message_content("direct steer"),
                sender_label="Owner",
                mode=PromptDeliveryMode.steer,
            )
            from planner.conversation.contracts import PromptDeliveryQueued

            assert isinstance(direct, PromptDeliveryQueued) is (
                case in ("mismatch", "reported_disabled")
            )
            await harness.system.send(
                conversation_id, text_message_content("held steer"), sender_label="Owner"
            )
            held = await harness.system.held_prompts(conversation_id)
            assert len(held) == (2 if case in ("mismatch", "reported_disabled") else 1)
            promoted = await harness.system.promote_held_prompt(
                conversation_id, held[0].held_prompt_id, HeldPromptPromotionMode.steer
            )
            assert isinstance(promoted, PromptDeliveryRefused) is (
                case in ("mismatch", "reported_disabled")
            )
            await harness.system.shutdown()
            harness = _Harness(path)
            harness.system._model_resolver = service
            harness.system.configure_destination_validation(source)
            restarted = await harness.system.send(
                conversation_id, text_message_content("restart"), sender_label="Owner"
            )
            assert isinstance(restarted, PromptDeliveryRefused) is (
                case in ("mismatch", "reported_disabled")
            )
        finally:
            conn.close()
            await harness.system.shutdown()

    asyncio.run(asyncio.wait_for(exercise(), 15))

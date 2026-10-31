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
    setup,
)

from planner.conversation.backends.contracts import BackendPromptRejected, PromptWriteFailed
from planner.conversation.contracts import ConversationBackendKey as Backend
from planner.conversation.contracts import (
    PromptDeliveryUncertain,
    WorkspacePathPolicy,
)
from planner.conversation.events import (
    PromptDeliveryRefusedEventPayload,
    PromptDeliveryUncertainEventPayload,
    PromptEventPayload,
)
from planner.conversation.message_content import text_message_content
from planner.conversation.model_resolution.contracts import CatalogWorkspaceBinding, SelectionKind
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect
from planner.runtime import conversation_start
from planner.tickets import data as tickets


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
        refreshed = await harness.system.destination_backend_snapshot(
            Backend.codex,
            binding,
            "vps",
            refresh=True,
        )
        assert refreshed.available_models and len(source.calls) == 1
        source.calls.clear()
        source.fail = True
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
                    refusals = [
                        e.payload for e in await harness.events(conversation_id)
                        if isinstance(e.payload, PromptDeliveryRefusedEventPayload)
                    ]
                    assert len(refusals) == 1
                    assert refusals[0].refusal_reason.value == "backend_rejected_prompt"
                    assert refusals[0].refusal_detail == "provider refused correlated request"
                else:
                    assert isinstance(result.fate, PromptDeliveryUncertain)
                    state = await harness.system._conversation_state(conversation_id)
                    assert state is not None and state.child_is_quarantined
                    assert state.reserved_turn is not None
                    events = await harness.events(conversation_id)
                    assert any(
                        isinstance(e.payload, PromptDeliveryUncertainEventPayload) for e in events
                    )
            assert source.calls == []
        finally:
            conn.close()
            await harness.system.shutdown()

    asyncio.run(asyncio.wait_for(exercise(), 15))


@pytest.mark.parametrize("early", [True, False], ids=["early-events", "held-uncertainty"])
def test_provider_events_and_held_messages_keep_their_original_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, early: bool
) -> None:
    from planner.conversation.backends.contracts import BackendPromptAccepted
    from planner.conversation.events import (
        ConversationTurnEnding,
        ToolCallStartedEventPayload,
        TurnEndedEventPayload,
    )

    _, _, path = setup(tmp_path)
    harness = _Harness(path)
    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda: tmp_path)
    original = _FakeBackendChild.write_prompt
    attempted_tokens: list[str | None] = []

    async def write(self: Any, turn: Any, content: Any, **kwargs: Any) -> Any:
        attempted_tokens.append(
            self._backend.model if kwargs["model_change"] is None else kwargs["model_change"]
        )
        if not early and len(attempted_tokens) > 1:
            raise PromptWriteFailed("acknowledgment absent after write")
        accepted = await original(self, turn, content, **kwargs)
        if early and len(attempted_tokens) == 1:
            # These callbacks must finish before the provider returns acceptance.
            await self._sink.agent_message_delta(turn, "early delta")
            await self._sink.tool_call_started(
                turn,
                tool_call_id="early-tool",
                title="Read",
                tool_kind="read",
                detail="detail",
            )
            await self._sink.turn_ended(
                turn,
                ending=ConversationTurnEnding.completed,
                error_summary=None,
                standard_error_tail=None,
            )
            return BackendPromptAccepted(
                True,
            )
        return accepted

    monkeypatch.setattr(_FakeBackendChild, "write_prompt", write)

    async def exercise() -> None:
        conn = connect(str(path))
        try:
            ticket = tickets.create_ticket(
                conn,
                title="Captured early events",
                principal=OWNER_PRINCIPAL,
                now=1000,
                title_max_chars=200,
                worker_type="coding",
                employee_backend="claude",
                employee_launch_model="claude-exact",
                selection_kind=SelectionKind.concrete,
            )
            async with conversation_start.conversation_link_lock(f"ticket:{ticket.id}"):
                first = await conversation_start.send_to_ticket_conversation(
                    harness.system,
                    conn,
                    ticket.id,
                    text_message_content("first"),
                    conversation_id=None,
                    sender_label="Owner",
                    now=1000,
                )
            conversation_id = first.conversation_id
            assert conversation_id is not None
            await harness.settle()
            if early:
                events = await harness.events(conversation_id)
                prompt = next(e for e in events if isinstance(e.payload, PromptEventPayload))
                tool = next(e for e in events if isinstance(e.payload, ToolCallStartedEventPayload))
                ended = next(e for e in events if isinstance(e.payload, TurnEndedEventPayload))
                assert prompt.sequence < tool.sequence < ended.sequence
                assert isinstance(prompt.payload, PromptEventPayload)
            else:
                await harness.system.send(
                    conversation_id, text_message_content("held original"), sender_label="Owner"
                )
                conn.execute(
                    "UPDATE tickets SET employee_launch_model='claude-future' WHERE id=?",
                    (ticket.id,),
                )
                conn.commit()
                await harness.complete_turn(conversation_id)
                assert attempted_tokens == ["claude-exact", "claude-exact"]
                assert any(
                    isinstance(e.payload, PromptDeliveryUncertainEventPayload)
                    for e in await harness.events(conversation_id)
                )
        finally:
            conn.close()
            await harness.system.shutdown()

    asyncio.run(asyncio.wait_for(exercise(), 15))

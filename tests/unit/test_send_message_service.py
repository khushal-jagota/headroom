"""The general Send Message service carries one public mode to every owner door."""

from __future__ import annotations

import asyncio
from pathlib import Path
from sqlite3 import Connection
from types import SimpleNamespace
from typing import cast

import pytest
from tests.support.conversation_system_under_test import open_conversation_system_under_test
from tests.support.in_memory_conversation_system import (
    InMemoryConversationSystem,
)

from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationStartRequest,
    ConversationSystem,
    PromptDeliveryMode,
    PromptDeliveryStarted,
)
from planner.conversation.events import PromptEventPayload
from planner.conversation.message_content import (
    MessageContent,
    message_content_text,
)
from planner.core.authctx import RequestContext
from planner.core.authority.intake import admit_command
from planner.core.clock import TestClock as PlannerTestClock
from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.managers import data as managers_data
from planner.message_delivery import recap_request, service
from planner.runtime import conversation_start
from planner.runtime.conversation_start import DeliveredMessage
from planner.tickets import data as tickets_data


def test_chief_to_worker_and_worker_reply_cross_two_real_conversations_without_a_loop(
    tmp_db: Connection,
    fake_clock: PlannerTestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        conversations = InMemoryConversationSystem()
        for conversation_id in ("c_chief", "c_worker"):
            await conversations.start_conversation(
                ConversationStartRequest(
                    conversation_id=conversation_id,
                    backend_key=ConversationBackendKey.hermes,
                    model="test-model",
                )
            )
        chief = Principal(PrincipalKind.chief, "chief")
        worker = Principal(PrincipalKind.ticket, "t_worker")
        monkeypatch.setattr(
            conversation_start,
            "read_agent_conversation",
            lambda _conn, _key: "c_chief",
        )
        monkeypatch.setattr(
            tickets_data,
            "read_ticket",
            lambda _conn, _ticket_id: SimpleNamespace(conversation_id="c_worker"),
        )

        async def send_to_ticket(
            system: ConversationSystem,
            _conn: Connection,
            _ticket_id: str,
            content: object,
            **kwargs: object,
        ) -> DeliveredMessage:
            fate = await system.send(
                "c_worker",
                cast(MessageContent, content),
                sender_label=cast(str, kwargs["sender_label"]),
                sender=cast(Principal, kwargs["sender"]),
                recipient=cast(Principal, kwargs["recipient"]),
            )
            return DeliveredMessage("c_worker", fate)

        async def send_to_agent(
            system: ConversationSystem,
            _conn: Connection,
            _agent_key: str,
            content: object,
            _values: object,
            **kwargs: object,
        ) -> DeliveredMessage:
            fate = await system.send(
                "c_chief",
                cast(MessageContent, content),
                sender_label=cast(str, kwargs["sender_label"]),
                sender=cast(Principal, kwargs["sender"]),
                recipient=cast(Principal, kwargs["recipient"]),
            )
            return DeliveredMessage("c_chief", fate)

        monkeypatch.setattr(conversation_start, "send_to_ticket_conversation", send_to_ticket)
        monkeypatch.setattr(conversation_start, "send_to_agent_conversation", send_to_agent)
        monkeypatch.setattr(conversation_start, "agent_resolve", lambda _conn: object())

        await service.send_message(
            conversations,
            tmp_db,
            fake_clock,
            RequestContext(chief),
            worker,
            "Please report",
        )
        worker_write = conversations.backend_prompt_writes("c_worker")[-1]
        assert worker_write.text == "Chief:\nPlease report"

        await service.send_message(
            conversations,
            tmp_db,
            fake_clock,
            RequestContext(worker),
            chief,
            "Automatic action",
            system_message=True,
        )
        assert conversations.backend_prompt_writes("c_chief")[-1].text == (
            "Panels:\nAutomatic action"
        )
        conversations.complete_running_turn("c_chief")

        await service.send_message(
            conversations,
            tmp_db,
            fake_clock,
            RequestContext(worker),
            chief,
            "Report delivered",
            sender_message_id="worker-reply-1",
        )
        chief_write = conversations.backend_prompt_writes("c_chief")[-1]
        assert message_content_text(chief_write.content) == "Ticket t_worker:\nReport delivered"

        await service.send_message(
            conversations,
            tmp_db,
            fake_clock,
            RequestContext(worker),
            chief,
            "Report delivered",
            sender_message_id="worker-reply-1",
        )
        assert len(conversations.backend_prompt_writes("c_chief")) == 2

    asyncio.run(exercise())


def test_supervisor_messages_keep_authenticated_addresses_in_durable_record_and_wire(
    tmp_db: Connection,
    tmp_path: Path,
    fake_clock: PlannerTestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the real conversation store and backend process at the service seam."""
    first = Principal(PrincipalKind.manager, "si_first")
    second = Principal(PrincipalKind.manager, "si_second")
    monkeypatch.setattr(
        managers_data,
        "read_manager",
        lambda _conn, item_id: SimpleNamespace(item=SimpleNamespace(manager_agent_key=item_id)),
    )
    monkeypatch.setattr(
        conversation_start,
        "read_agent_conversation",
        lambda _conn, key: f"c_{key}",
    )
    monkeypatch.setattr(conversation_start, "manager_resolve", lambda _conn, _item: object())

    async def send_to_agent(
        system: ConversationSystem,
        _conn: Connection,
        agent_key: str,
        content: object,
        _values: object,
        **kwargs: object,
    ) -> DeliveredMessage:
        conversation_id = f"c_{agent_key}"
        fate = await system.send(
            conversation_id,
            cast(MessageContent, content),
            sender_label=cast(str, kwargs["sender_label"]),
            sender=cast(Principal, kwargs["sender"]),
            recipient=cast(Principal, kwargs["recipient"]),
        )
        return DeliveredMessage(conversation_id, fate)

    monkeypatch.setattr(conversation_start, "send_to_agent_conversation", send_to_agent)

    async def exercise() -> None:
        async with open_conversation_system_under_test() as subject:
            for principal in (first, second):
                await subject.system.start_conversation(
                    ConversationStartRequest(
                        conversation_id=f"c_{principal.id}",
                        backend_key=ConversationBackendKey.hermes,
                        model="test-model",
                        workspace_folder=tmp_path,
                    )
                )
            for sender, recipient, text in (
                (first, second, "Coordinate"),
                (second, first, "Agreed"),
            ):
                admit_command(
                    tmp_db, sender, "POST", "/api/messages/send", {},
                    {"target": {"kind": recipient.kind.value, "id": recipient.id}},
                )
                await service.send_message(
                    subject.system, tmp_db, fake_clock, RequestContext(sender), recipient, text
                )
                await subject.settle()
                events = await subject.recorded_events(f"c_{recipient.id}")
                prompts = [e.payload for e in events if isinstance(e.payload, PromptEventPayload)]
                assert len(prompts) == 1
                assert prompts[0].sender == sender
                assert prompts[0].recipient == recipient
                assert message_content_text(prompts[0].content) == text
                assert prompts[0].sender_label == f"Manager {sender.id}"
            first_wire = (await subject.backend_writes("c_si_second"))[-1].text
            assert first_wire == "Manager si_first:\nCoordinate"
            reply_wire = (await subject.backend_writes("c_si_first"))[-1].text
            assert reply_wire == "Manager si_second:\nAgreed"

    asyncio.run(exercise())


def test_a_recap_request_clears_the_line_it_replaces_and_queues_behind_a_refused_steer(
    tmp_db: Connection,
    tmp_path: Path,
    fake_clock: PlannerTestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The press clears the recap first, then reaches the agent as a Panels message."""

    expected_request = (
        "File a short, top-level recap with your recap tool. "
        "Do not send the user a message. "
        "Workers: `panels worker recap`. Managers: `panels manager set <manager-id> recap`. "
        "Chief: `PUT /api/workers/chief-of-staff/recap`."
    )
    owner = Principal(PrincipalKind.owner, "owner")
    worker = Principal(PrincipalKind.ticket, "t_worker")
    monkeypatch.setattr(
        tickets_data,
        "read_ticket",
        lambda _conn, _ticket_id: SimpleNamespace(conversation_id="c_worker"),
    )
    cleared: list[object] = []
    modes: list[PromptDeliveryMode] = []
    monkeypatch.setattr(
        tickets_data,
        "edit_ticket",
        lambda _conn, ticket_id, **kwargs: cleared.append((ticket_id, kwargs["edit"])),
    )

    async def send_to_ticket(
        system: ConversationSystem,
        _conn: Connection,
        _ticket_id: str,
        content: object,
        **kwargs: object,
    ) -> DeliveredMessage:
        modes.append(cast(PromptDeliveryMode, kwargs["mode"]))
        if kwargs["mode"] is PromptDeliveryMode.steer:
            assert cleared == [("t_worker", {"recap": ""})]
        fate = await system.send(
            "c_worker",
            cast(MessageContent, content),
            mode=cast(PromptDeliveryMode, kwargs["mode"]),
            sender_label=cast(str, kwargs["sender_label"]),
            sender=cast(Principal | None, kwargs["sender"]),
            recipient=cast(Principal, kwargs["recipient"]),
        )
        return DeliveredMessage("c_worker", fate)

    monkeypatch.setattr(conversation_start, "send_to_ticket_conversation", send_to_ticket)

    async def exercise() -> None:
        async with open_conversation_system_under_test() as subject:
            # A backend refusal holds steering until the active turn ends, exercising
            # the real deferred attribution boundary rather than a direct-send fake.
            await subject.system.start_conversation(
                ConversationStartRequest(
                    conversation_id="c_worker",
                    backend_key=ConversationBackendKey.codex,
                    model="test-model",
                    workspace_folder=tmp_path,
                )
            )
            result = await service.send_message(
                subject.system, tmp_db, fake_clock, RequestContext(owner), worker, "Work"
            )
            assert isinstance(result.fate, PromptDeliveryStarted), result.fate
            await subject.tell_agent("c_worker", {"command": "reject_steer"})
            await recap_request.request_recap(
                subject.system, tmp_db, fake_clock, RequestContext(owner), worker
            )
            await subject.settle()
            assert modes == [PromptDeliveryMode.queue, PromptDeliveryMode.steer]
            assert len(await subject.backend_writes("c_worker")) == 1
            await subject.complete_running_turn("c_worker")
            writes = await subject.backend_writes("c_worker")
            assert len(writes) == 2
            assert writes[1].text == f"Panels:\n{expected_request}"
            assert writes[1].mode is PromptDeliveryMode.queue
            await subject.complete_running_turn("c_worker")
            events = await subject.recorded_events("c_worker")
            prompts = [e.payload for e in events if isinstance(e.payload, PromptEventPayload)]
            assert prompts[0].sender == owner
            assert prompts[0].sender_label == "owner"
            assert prompts[1].sender is None
            assert prompts[1].sender_label == "Panels"
            assert prompts[1].recipient == worker
            assert message_content_text(prompts[1].content) == expected_request

    asyncio.run(exercise())


def test_a_recap_cannot_be_asked_of_the_owner(
    tmp_db: Connection,
    fake_clock: PlannerTestClock,
) -> None:
    """Every agent keeps a recap. The owner is the one reachable principal that does not."""

    async def exercise() -> None:
        conversations = InMemoryConversationSystem()
        with pytest.raises(PlannerError) as refusal:
            await recap_request.request_recap(
                conversations,
                tmp_db,
                fake_clock,
                RequestContext(Principal(PrincipalKind.owner, "owner")),
                Principal(PrincipalKind.owner, "owner"),
            )
        assert refusal.value.code is ErrorCode.validation

    asyncio.run(exercise())


def test_a_recap_request_empties_an_manager_recap_through_its_own_writer(
    tmp_db: Connection,
    fake_clock: PlannerTestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each kind of agent keeps its recap in its own field, under its own writer.

    A Ticket, an manager, and the Chief store the line in three different places,
    and only the manager's writer validates the field name it is handed. Nothing
    else here presses that writer, so a renamed field would refuse every manager
    press and no test would notice.
    """
    item = managers_data.create_manager(
        tmp_db, title="Surfaces", project_id="project_vylo", clock=fake_clock
    )
    managers_data.update_manager(
        tmp_db, item.id, edits={"recap": "Three surfaces reviewed."}, clock=fake_clock
    )

    async def exercise() -> None:
        sent: list[str] = []

        async def send(*_args: object, **_kwargs: object) -> None:
            sent.append("sent")

        monkeypatch.setattr(service, "send_message", send)
        await recap_request.request_recap(
            InMemoryConversationSystem(),
            tmp_db,
            fake_clock,
            RequestContext(Principal(PrincipalKind.owner, "owner")),
            Principal(PrincipalKind.manager, item.id),
        )
        assert managers_data.read_manager(tmp_db, item.id).item.recap == ""
        assert sent == ["sent"]

    asyncio.run(exercise())


def test_removed_goal_conversation_boundaries_reject_before_resolution() -> None:
    """Domain goal responsibility grants no conversation identity or address."""
    from fastapi import Request

    from planner.conversation.history.api import history_page
    from planner.core.authctx import _classify
    from planner.message_delivery.api import _principal

    with pytest.raises(PlannerError, match="unknown message recipient"):
        _principal({"kind": "goal", "id": "goal_old"})
    with pytest.raises(PlannerError, match="does not name a Panels principal"):
        _classify("goal")
    with pytest.raises(PlannerError, match="unknown conversation history owner"):
        asyncio.run(history_page(Request({"type": "http"}), "goal", "goal_old"))

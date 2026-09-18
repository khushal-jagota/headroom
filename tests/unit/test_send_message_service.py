"""The general Send Message service carries one public mode to every owner door."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from sqlite3 import Connection
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationStartRequest,
    ConversationSystem,
    ConversationTurnReference,
    PromptDeliveryInjected,
    PromptDeliveryMode,
    PromptDeliveryQueued,
    PromptDeliveryRefusalReason,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
    PromptDeliveryUncertain,
)
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.conversation.message_content import (
    MessageContent,
    message_content_text,
    text_message_content,
)
from planner.core.authctx import RequestContext
from planner.core.clock import TestClock as PlannerTestClock
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.message_delivery import service
from planner.message_delivery.contracts import MessageDeliveryMode
from planner.runtime import conversation_start
from planner.runtime.conversation_start import DeliveredMessage
from planner.sprints import data as sprints_data
from planner.sprints import service as sprints_service
from planner.tickets import data as tickets_data


@pytest.mark.parametrize("mode", list(MessageDeliveryMode))
@pytest.mark.parametrize(
    ("recipient", "existing_conversation_id", "expected_door"),
    [
        (Principal(PrincipalKind.chief, "chief"), "c_chief", "agent"),
        (Principal(PrincipalKind.ticket, "t_one"), "c_ticket", "ticket"),
        (Principal(PrincipalKind.sprint_item, "si_one"), "c_supervisor", "agent"),
    ],
)
def test_each_public_mode_reaches_each_destination_door_once(
    tmp_db: Connection,
    fake_clock: PlannerTestClock,
    monkeypatch: pytest.MonkeyPatch,
    mode: MessageDeliveryMode,
    recipient: Principal,
    existing_conversation_id: str,
    expected_door: str,
) -> None:
    ticket_send = AsyncMock(return_value=DeliveredMessage("c_result", PromptDeliveryStarted()))
    agent_send = AsyncMock(return_value=DeliveredMessage("c_result", PromptDeliveryStarted()))
    monkeypatch.setattr(conversation_start, "send_to_ticket_conversation", ticket_send)
    monkeypatch.setattr(conversation_start, "send_to_agent_conversation", agent_send)
    monkeypatch.setattr(conversation_start, "new_conversation_id", lambda: "c_new")
    monkeypatch.setattr(conversation_start, "agent_resolve", lambda _conn: object())
    monkeypatch.setattr(
        conversation_start,
        "sprint_item_supervisor_resolve",
        lambda _item: object(),
    )
    monkeypatch.setattr(
        conversation_start,
        "read_agent_conversation",
        lambda _conn, key: {
            "chief_of_staff": "c_chief",
            "sprint-item-supervisor:si_one": "c_supervisor",
        }.get(key),
    )
    monkeypatch.setattr(
        tickets_data,
        "read_ticket",
        lambda _conn, _ticket_id: SimpleNamespace(conversation_id="c_ticket"),
    )
    monkeypatch.setattr(
        sprints_data,
        "read_item",
        lambda _conn, _item_id: SimpleNamespace(
            item=SimpleNamespace(supervisor_agent_key="sprint-item-supervisor:si_one")
        ),
    )

    @asynccontextmanager
    async def supervisor_lock(_item_id: str) -> AsyncIterator[None]:
        yield

    monkeypatch.setattr(sprints_service, "supervisor_lifecycle_lock", supervisor_lock)
    result = asyncio.run(
        service.send_message(
            cast(ConversationSystem, object()),
            tmp_db,
            fake_clock,
            RequestContext(OWNER_PRINCIPAL),
            recipient,
            "Hello",
            mode,
        )
    )

    expected_mode = PromptDeliveryMode(mode.value)
    selected_send = ticket_send if expected_door == "ticket" else agent_send
    other_send = agent_send if expected_door == "ticket" else ticket_send
    assert selected_send.await_count == 1
    assert other_send.await_count == 0
    selected_call = selected_send.await_args
    assert selected_call is not None
    assert selected_call.kwargs["conversation_id"] == existing_conversation_id
    assert selected_call.kwargs["mode"] is expected_mode
    assert selected_call.kwargs["sender_label"] == "owner"
    assert result.conversation_id == "c_result"
    assert isinstance(result.fate, PromptDeliveryStarted)


@pytest.mark.parametrize("mode", list(MessageDeliveryMode))
def test_owner_has_no_deliverable_conversation(
    tmp_db: Connection, fake_clock: PlannerTestClock, mode: MessageDeliveryMode
) -> None:
    with pytest.raises(PlannerError) as caught:
        asyncio.run(
            service.send_message(
                InMemoryConversationSystem(),
                tmp_db,
                fake_clock,
                RequestContext(OWNER_PRINCIPAL),
                OWNER_PRINCIPAL,
                "Hello",
                mode,
            )
        )

    assert caught.value.code is ErrorCode.validation
    assert caught.value.message == "the owner cannot send a message to the owner"


def test_employee_message_to_owner_uses_the_senders_current_conversation(
    tmp_db: Connection,
    fake_clock: PlannerTestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversations = AsyncMock()
    monkeypatch.setattr(
        tickets_data,
        "read_ticket",
        lambda _conn, _ticket_id: SimpleNamespace(conversation_id="c_sender"),
    )
    sender = Principal(PrincipalKind.ticket, "t_sender")

    result = asyncio.run(
        service.send_message(
            conversations,
            tmp_db,
            fake_clock,
            RequestContext(sender),
            OWNER_PRINCIPAL,
            "Here is the result.",
        )
    )

    assert result.conversation_id == "c_sender"
    assert result.fate.__class__.__name__ == "MessageRecordedToOwner"
    conversations.record_message_to_owner.assert_awaited_once()
    assert conversations.record_message_to_owner.await_args.kwargs["sender"] == sender
    assert conversations.record_message_to_owner.await_args.kwargs["recipient"] == OWNER_PRINCIPAL


def test_employee_without_a_conversation_cannot_message_the_owner(
    tmp_db: Connection,
    fake_clock: PlannerTestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tickets_data,
        "read_ticket",
        lambda _conn, _ticket_id: SimpleNamespace(conversation_id=None),
    )
    with pytest.raises(PlannerError) as caught:
        asyncio.run(
            service.send_message(
                InMemoryConversationSystem(),
                tmp_db,
                fake_clock,
                RequestContext(Principal(PrincipalKind.ticket, "t_sender")),
                OWNER_PRINCIPAL,
                "Hello",
            )
        )
    assert caught.value.code is ErrorCode.not_found
    assert caught.value.message == "the sender has no current conversation"


@pytest.mark.parametrize("target_kind", [PrincipalKind.ticket, PrincipalKind.chief])
def test_attachment_materialization_and_send_hold_the_link_against_reset(
    target_kind: PrincipalKind,
    tmp_db: Connection,
    fake_clock: PlannerTestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        target = (
            Principal(PrincipalKind.ticket, "t_one")
            if target_kind is PrincipalKind.ticket
            else Principal(PrincipalKind.chief, "chief")
        )
        conversation_id = "c_target"
        factory_entered = asyncio.Event()
        let_factory_finish = asyncio.Event()
        reset_acquired = asyncio.Event()

        async def content_for(resolved_conversation_id: str):  # type: ignore[no-untyped-def]
            assert resolved_conversation_id == conversation_id
            factory_entered.set()
            await let_factory_finish.wait()
            return text_message_content("with attachment")

        async def ticket_send(
            _system: object,
            _conn: object,
            _ticket_id: str,
            content: object,
            **_kwargs: object,
        ) -> DeliveredMessage:
            assert callable(content)
            await content(conversation_id)
            return DeliveredMessage(conversation_id, PromptDeliveryStarted())

        async def agent_send(
            _system: object,
            _conn: object,
            _agent_key: str,
            content: object,
            _values: object,
            **_kwargs: object,
        ) -> DeliveredMessage:
            assert callable(content)
            await content(conversation_id)
            return DeliveredMessage(conversation_id, PromptDeliveryStarted())

        monkeypatch.setattr(conversation_start, "send_to_ticket_conversation", ticket_send)
        monkeypatch.setattr(conversation_start, "send_to_agent_conversation", agent_send)
        monkeypatch.setattr(conversation_start, "agent_resolve", lambda _conn: object())
        monkeypatch.setattr(
            conversation_start,
            "read_agent_conversation",
            lambda _conn, _key: conversation_id,
        )
        monkeypatch.setattr(
            tickets_data,
            "read_ticket",
            lambda _conn, _ticket_id: SimpleNamespace(conversation_id=conversation_id),
        )
        link_key = "ticket:t_one" if target_kind is PrincipalKind.ticket else "agent:chief_of_staff"

        sending = asyncio.create_task(
            service.send_message(
                cast(ConversationSystem, object()),
                tmp_db,
                fake_clock,
                RequestContext(OWNER_PRINCIPAL),
                target,
                content_for,
            )
        )
        await factory_entered.wait()

        async def reset() -> None:
            async with conversation_start.conversation_link_lock(link_key):
                reset_acquired.set()

        resetting = asyncio.create_task(reset())
        await asyncio.sleep(0)
        assert not reset_acquired.is_set()
        let_factory_finish.set()
        await sending
        await resetting
        assert reset_acquired.is_set()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "fate",
    [
        PromptDeliveryRefused(PromptDeliveryRefusalReason.backend_cannot_steer),
        PromptDeliveryUncertain(),
    ],
)
def test_service_preserves_terminal_steer_fates_without_another_send(
    tmp_db: Connection,
    fake_clock: PlannerTestClock,
    monkeypatch: pytest.MonkeyPatch,
    fate: PromptDeliveryRefused | PromptDeliveryUncertain,
) -> None:
    ticket_send = AsyncMock(return_value=DeliveredMessage("c_ticket", fate))
    agent_send = AsyncMock()
    monkeypatch.setattr(conversation_start, "send_to_ticket_conversation", ticket_send)
    monkeypatch.setattr(conversation_start, "send_to_agent_conversation", agent_send)
    monkeypatch.setattr(
        tickets_data,
        "read_ticket",
        lambda _conn, _ticket_id: SimpleNamespace(conversation_id="c_ticket"),
    )

    result = asyncio.run(
        service.send_message(
            cast(ConversationSystem, object()),
            tmp_db,
            fake_clock,
            RequestContext(OWNER_PRINCIPAL),
            Principal(PrincipalKind.ticket, "t_one"),
            "Guide it",
            MessageDeliveryMode.steer,
        )
    )

    assert result.fate is fate
    assert ticket_send.await_count == 1
    assert agent_send.await_count == 0


@pytest.mark.parametrize(
    ("fate", "credited"),
    [
        (PromptDeliveryStarted(), True),
        (PromptDeliveryQueued(queue_position=2), True),
        (PromptDeliveryInjected(), True),
        (PromptDeliveryUncertain(), True),
        (
            PromptDeliveryRefused(PromptDeliveryRefusalReason.backend_cannot_steer),
            False,
        ),
    ],
)
def test_employee_send_credits_only_an_accepted_reply_to_the_captured_source_turn(
    tmp_db: Connection,
    fake_clock: PlannerTestClock,
    monkeypatch: pytest.MonkeyPatch,
    fate: (
        PromptDeliveryStarted
        | PromptDeliveryQueued
        | PromptDeliveryInjected
        | PromptDeliveryUncertain
        | PromptDeliveryRefused
    ),
    credited: bool,
) -> None:
    conversations = AsyncMock()
    turn = ConversationTurnReference("c_sender", 7)
    conversations.active_turn_reference.return_value = turn
    conversations.turn_expects_reply.return_value = True
    ticket_send = AsyncMock(return_value=DeliveredMessage("c_recipient", fate, credited))
    monkeypatch.setattr(
        tickets_data,
        "read_ticket",
        lambda _conn, ticket_id: SimpleNamespace(
            conversation_id="c_sender" if ticket_id == "t_sender" else "c_recipient"
        ),
    )
    monkeypatch.setattr(
        conversation_start,
        "send_to_ticket_conversation",
        ticket_send,
    )

    asyncio.run(
        service.send_message(
            conversations,
            tmp_db,
            fake_clock,
            RequestContext(Principal(PrincipalKind.ticket, "t_sender")),
            Principal(PrincipalKind.ticket, "t_recipient"),
            "Explicit reply",
        )
    )

    conversations.active_turn_reference.assert_awaited_once_with("c_sender")
    conversations.turn_expects_reply.assert_awaited_once_with(
        turn, Principal(PrincipalKind.ticket, "t_recipient")
    )
    assert ticket_send.await_args is not None
    assert ticket_send.await_args.kwargs["reply_requested"] is False
    if credited:
        conversations.record_explicit_reply.assert_awaited_once_with(
            turn, Principal(PrincipalKind.ticket, "t_recipient")
        )
    else:
        conversations.record_explicit_reply.assert_not_awaited()


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
            receipt = await system.send_with_receipt(
                "c_worker",
                cast(MessageContent, content),
                sender_label=cast(str, kwargs["sender_label"]),
                sender=cast(Principal, kwargs["sender"]),
                recipient=cast(Principal, kwargs["recipient"]),
                reply_requested=cast(bool, kwargs["reply_requested"]),
            )
            return DeliveredMessage("c_worker", receipt.fate, receipt.newly_accepted)

        async def send_to_agent(
            system: ConversationSystem,
            _conn: Connection,
            _agent_key: str,
            content: object,
            _values: object,
            **kwargs: object,
        ) -> DeliveredMessage:
            receipt = await system.send_with_receipt(
                "c_chief",
                cast(MessageContent, content),
                sender_label=cast(str, kwargs["sender_label"]),
                sender=cast(Principal, kwargs["sender"]),
                recipient=cast(Principal, kwargs["recipient"]),
                reply_requested=cast(bool, kwargs["reply_requested"]),
            )
            return DeliveredMessage("c_chief", receipt.fate, receipt.newly_accepted)

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
        assert "panels send-message --chief" in worker_write.text
        assert worker_write.text.index("Authenticated Panels reply requirement") < (
            worker_write.text.index("Chief:\nPlease report")
        )

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
        assert "Authenticated Panels reply requirement" not in chief_write.text

        worker_turn = await conversations.active_turn_reference("c_worker")
        assert worker_turn is not None
        assert await conversations.turn_expects_reply(worker_turn, chief) is False

        await service.send_message(
            conversations,
            tmp_db,
            fake_clock,
            RequestContext(worker),
            chief,
            "Report delivered",
            sender_message_id="worker-reply-1",
        )
        assert len(conversations.backend_prompt_writes("c_chief")) == 1

    asyncio.run(exercise())

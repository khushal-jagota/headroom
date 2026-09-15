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
    ConversationSystem,
    PromptDeliveryMode,
    PromptDeliveryRefusalReason,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
    PromptDeliveryUncertain,
)
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
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
    assert selected_call.kwargs["sender_label"] == "You"
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
    assert caught.value.message == "the owner does not have a deliverable conversation"


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

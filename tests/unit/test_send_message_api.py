"""HTTP contract for the general Send Message mode."""

from __future__ import annotations

import asyncio
from sqlite3 import Connection
from typing import cast

import pytest

from planner.conversation.contracts import (
    ConversationSystem,
    PromptDeliveryRefusalReason,
    PromptDeliveryRefused,
    PromptDeliveryUncertain,
)
from planner.core.authctx import RequestContext
from planner.core.clock import Clock
from planner.core.errors import ErrorCode, PlannerError
from planner.message_delivery import api
from planner.message_delivery import service as message_delivery_service
from planner.message_delivery.contracts import (
    MessageDeliveryMode,
    MessageDeliveryResult,
    MessageTarget,
    ResolvedMessageDestination,
)


@pytest.mark.parametrize(
    ("body_mode", "expected"),
    [
        (None, MessageDeliveryMode.queue),
        ("queue", MessageDeliveryMode.queue),
        ("steer", MessageDeliveryMode.steer),
        ("send_now", MessageDeliveryMode.send_now),
    ],
)
def test_api_defaults_and_validates_the_public_mode(
    monkeypatch: pytest.MonkeyPatch,
    body_mode: str | None,
    expected: MessageDeliveryMode,
) -> None:
    captured: list[MessageDeliveryMode] = []

    async def send_message(
        _conversations: ConversationSystem,
        _conn: Connection,
        _clock: Clock,
        _ctx: RequestContext,
        target: MessageTarget,
        _message: str,
        mode: MessageDeliveryMode,
    ) -> MessageDeliveryResult:
        captured.append(mode)
        return MessageDeliveryResult(
            target,
            ResolvedMessageDestination("agent", "chief"),
            "c_one",
            PromptDeliveryUncertain(),
        )

    monkeypatch.setattr(message_delivery_service, "send_message", send_message)
    body: dict[str, object] = {
        "target": {"type": "chief"},
        "message": "Guide it",
    }
    if body_mode is not None:
        body["mode"] = body_mode

    result = asyncio.run(
        api.send_message(
            body,
            cast(Connection, object()),
            RequestContext("unattributed", False, False),
            cast(Clock, object()),
            cast(ConversationSystem, object()),
        )
    )

    assert captured == [expected]
    assert result["fate"] == "uncertain"


def test_api_serializes_the_conversation_refusal_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def send_message(
        _conversations: ConversationSystem,
        _conn: Connection,
        _clock: Clock,
        _ctx: RequestContext,
        target: MessageTarget,
        _message: str,
        _mode: MessageDeliveryMode,
    ) -> MessageDeliveryResult:
        return MessageDeliveryResult(
            target,
            ResolvedMessageDestination("agent", "chief"),
            None,
            PromptDeliveryRefused(PromptDeliveryRefusalReason.no_running_turn_to_steer_into),
        )

    monkeypatch.setattr(message_delivery_service, "send_message", send_message)
    result = asyncio.run(
        api.send_message(
            {"target": {"type": "chief"}, "message": "Guide it", "mode": "steer"},
            cast(Connection, object()),
            RequestContext("unattributed", False, False),
            cast(Clock, object()),
            cast(ConversationSystem, object()),
        )
    )

    assert result["fate"] == "refused"
    assert result["refusal_reason"] == "no_running_turn_to_steer_into"


@pytest.mark.parametrize("invalid", [None, "run_when_free", "STEER", 1])
def test_api_rejects_values_outside_the_public_mode_contract(invalid: object) -> None:
    with pytest.raises(PlannerError) as caught:
        api._message_delivery_mode(invalid)

    assert caught.value.code is ErrorCode.validation
    assert caught.value.message == "mode must be queue, steer, or send_now"
    assert caught.value.detail == {"mode": invalid}

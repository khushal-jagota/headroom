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
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal
from planner.core.errors import ErrorCode, PlannerError
from planner.message_delivery import api
from planner.message_delivery import service as message_delivery_service
from planner.message_delivery.contracts import (
    MessageDeliveryMode,
    MessageDeliveryResult,
    MessageRecordedToOwner,
)


@pytest.mark.parametrize(
    ("body_mode", "expected"),
    [
        (None, MessageDeliveryMode.queue),
        ("queue", MessageDeliveryMode.queue),
        ("steer", MessageDeliveryMode.steer),
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
        recipient: Principal,
        _message: str,
        mode: MessageDeliveryMode,
    ) -> MessageDeliveryResult:
        captured.append(mode)
        return MessageDeliveryResult(
            recipient,
            "c_one",
            PromptDeliveryUncertain(),
        )

    monkeypatch.setattr(message_delivery_service, "send_message", send_message)
    body: dict[str, object] = {
        "target": {"kind": "chief", "id": "chief"},
        "message": "Guide it",
    }
    if body_mode is not None:
        body["mode"] = body_mode

    result = asyncio.run(
        api.send_message(
            body,
            cast(Connection, object()),
            RequestContext(OWNER_PRINCIPAL),
            cast(Clock, object()),
            cast(ConversationSystem, object()),
        )
    )

    assert captured == [expected]
    assert result["target"] == {"kind": "chief", "id": "chief"}
    assert "resolved_destination" not in result
    assert result["fate"] == "uncertain"


def test_api_serializes_the_conversation_refusal_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def send_message(
        _conversations: ConversationSystem,
        _conn: Connection,
        _clock: Clock,
        _ctx: RequestContext,
        recipient: Principal,
        _message: str,
        _mode: MessageDeliveryMode,
    ) -> MessageDeliveryResult:
        return MessageDeliveryResult(
            recipient,
            None,
            PromptDeliveryRefused(PromptDeliveryRefusalReason.no_running_turn_to_steer_into),
        )

    monkeypatch.setattr(message_delivery_service, "send_message", send_message)
    result = asyncio.run(
        api.send_message(
            {
                "target": {"kind": "chief", "id": "chief"},
                "message": "Guide it",
                "mode": "steer",
            },
            cast(Connection, object()),
            RequestContext(OWNER_PRINCIPAL),
            cast(Clock, object()),
            cast(ConversationSystem, object()),
        )
    )

    assert result["fate"] == "refused"
    assert result["refusal_reason"] == "no_running_turn_to_steer_into"


@pytest.mark.parametrize("invalid", [None, "run_when_free", "send_now", "STEER", 1])
def test_api_rejects_values_outside_the_public_mode_contract(invalid: object) -> None:
    with pytest.raises(PlannerError) as caught:
        api._message_delivery_mode(invalid)

    assert caught.value.code is ErrorCode.validation
    assert caught.value.message == "mode must be queue or steer"
    assert caught.value.detail == {"mode": invalid}


def test_api_uses_the_shared_principal_shape_and_rejects_agent_keys() -> None:
    assert api._principal({"kind": "chief", "id": "chief"}) == CHIEF_PRINCIPAL

    with pytest.raises(PlannerError) as caught:
        api._principal({"kind": "agent", "id": "reviewer"})

    assert caught.value.code is ErrorCode.validation
    assert caught.value.detail == {"kind": "agent"}


def test_api_serializes_an_employee_message_to_owner_as_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def send_message(*_args: object, **_kwargs: object) -> MessageDeliveryResult:
        return MessageDeliveryResult(OWNER_PRINCIPAL, "c_sender", MessageRecordedToOwner())

    monkeypatch.setattr(message_delivery_service, "send_message", send_message)
    result = asyncio.run(
        api.send_message(
            {
                "target": {"kind": "owner", "id": "owner"},
                "message": "Done.",
            },
            cast(Connection, object()),
            RequestContext(CHIEF_PRINCIPAL),
            cast(Clock, object()),
            cast(ConversationSystem, object()),
        )
    )
    assert result["fate"] == "recorded"
    assert result["conversation_id"] == "c_sender"

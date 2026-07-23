from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pytest

from planner.conversation.hub import (
    INVALID_ACTION_CLOSE_REASON,
    REPLAY_UNAVAILABLE_CLOSE_REASON,
    BrowserSubscription,
    ConversationHub,
)


class _Socket:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.messages: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        for message in messages:
            self.messages.put_nowait(message)
        self.accepted = 0
        self.sent: list[str] = []
        self.closed: list[tuple[int, str]] = []

    async def accept(self) -> None:
        self.accepted += 1

    async def receive(self) -> dict[str, Any]:
        return await self.messages.get()

    async def send_text(self, value: str) -> None:
        self.sent.append(value)

    async def close(self, *, code: int = 1000, reason: str = "") -> None:
        self.closed.append((code, reason))


def _text_action(value: dict[str, Any]) -> dict[str, Any]:
    return {"type": "websocket.receive", "text": json.dumps(value)}


class _Hub(ConversationHub):
    def __init__(self) -> None:
        self.subscription = BrowserSubscription(
            "browser-1", "t_employee", asyncio.Queue(maxsize=8)
        )
        self.attach_calls: list[tuple[Any, ...]] = []
        self.actions: list[str] = []
        self.detached: list[str] = []
        self.dispatch_active = False
        self.new_conversation_calls: list[str] = []

    async def attach_browser(self, employee_id: str, **kwargs: Any) -> BrowserSubscription:
        self.attach_calls.append((employee_id, kwargs))
        return self.subscription

    async def dispatch_action(self, connection_id: str, action: Any) -> None:
        assert connection_id == self.subscription.connection_id
        if action.employee_id != self.subscription.employee_id:
            raise ValueError("employee changed")
        assert not self.dispatch_active
        self.dispatch_active = True
        self.actions.append(action.type)
        self.dispatch_active = False

    async def new_conversation(self, employee_id: str) -> Any:
        self.new_conversation_calls.append(employee_id)
        return None

    async def detach_browser(self, connection_id: str) -> None:
        self.detached.append(connection_id)


def test_websocket_requires_valid_attach_before_any_action() -> None:
    async def exercise() -> None:
        hub = _Hub()
        socket = _Socket(
            [
                _text_action(
                    {
                        "type": "cancel",
                        "employeeId": "t_employee",
                    }
                )
            ]
        )
        await hub.websocket(socket)  # type: ignore[arg-type]
        assert hub.attach_calls == []
        assert socket.closed == [(1008, INVALID_ACTION_CLOSE_REASON)]

    asyncio.run(exercise())


def test_websocket_rejects_binary_and_malformed_frames() -> None:
    async def exercise() -> None:
        for frame in (
            {"type": "websocket.receive", "bytes": b"{}"},
            {"type": "websocket.receive", "text": "{"},
        ):
            hub = _Hub()
            socket = _Socket([frame])
            await hub.websocket(socket)  # type: ignore[arg-type]
            assert socket.closed == [(1008, INVALID_ACTION_CLOSE_REASON)]
            assert hub.attach_calls == []

    asyncio.run(exercise())


def test_websocket_actions_are_serial_and_normal_disconnect_is_quiet() -> None:
    async def exercise() -> None:
        hub = _Hub()
        socket = _Socket(
            [
                _text_action({"type": "attach", "employeeId": "t_employee"}),
                _text_action(
                    {
                        "type": "cancel",
                        "employeeId": "t_employee",
                        "queuedClientMessageId": "queued-1",
                    }
                ),
                _text_action(
                    {
                        "type": "new_conversation",
                        "employeeId": "t_employee",
                    }
                ),
                {"type": "websocket.disconnect", "code": 1000},
            ]
        )
        await hub.websocket(socket)  # type: ignore[arg-type]
        assert hub.actions == ["cancel", "new_conversation"]
        assert socket.closed == []
        assert hub.detached == ["browser-1"]

    asyncio.run(exercise())


def test_websocket_can_start_new_conversation_before_attaching_old_binding() -> None:
    async def exercise() -> None:
        hub = _Hub()
        socket = _Socket(
            [
                _text_action(
                    {
                        "type": "new_conversation",
                        "employeeId": "t_employee",
                    }
                ),
                {"type": "websocket.disconnect", "code": 1000},
            ]
        )
        await hub.websocket(socket)  # type: ignore[arg-type]
        assert hub.new_conversation_calls == ["t_employee"]
        assert hub.attach_calls == [("t_employee", {
            "last_seen_binding_generation": None,
            "last_seen_sequence": None,
        })]
        assert socket.closed == []

    asyncio.run(exercise())


def test_websocket_scopes_socket_to_first_employee() -> None:
    async def exercise() -> None:
        hub = _Hub()
        socket = _Socket(
            [
                _text_action({"type": "attach", "employeeId": "t_employee"}),
                _text_action(
                    {
                        "type": "cancel",
                        "employeeId": "t_other",
                    }
                ),
            ]
        )
        await hub.websocket(socket)  # type: ignore[arg-type]
        assert socket.closed == [(1008, INVALID_ACTION_CLOSE_REASON)]
        assert hub.detached == ["browser-1"]

    asyncio.run(exercise())


def test_websocket_logs_unexpected_attach_failure_with_employee(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _FailingAttachHub(_Hub):
        async def attach_browser(
            self, employee_id: str, **kwargs: Any
        ) -> BrowserSubscription:
            del kwargs
            raise RuntimeError("no rollout found")

    async def exercise() -> None:
        hub = _FailingAttachHub()
        socket = _Socket(
            [_text_action({"type": "attach", "employeeId": "t_employee"})]
        )
        with caplog.at_level(logging.ERROR, logger="planner.conversation.hub"):
            await hub.websocket(socket)  # type: ignore[arg-type]

        assert socket.closed == [(1011, "conversation transport failed")]
        failures = [
            record
            for record in caplog.records
            if record.getMessage() == "conversation websocket failed"
        ]
        assert len(failures) == 1
        assert failures[0].conversation_employee_id == "t_employee"  # type: ignore[attr-defined]
        assert failures[0].exc_info is not None
        assert "no rollout found" in caplog.text

    asyncio.run(exercise())


def test_replay_unavailable_and_slow_consumer_close_with_stable_retry() -> None:
    async def exercise() -> None:
        hub = _Hub()
        hub.subscription.close_reason = REPLAY_UNAVAILABLE_CLOSE_REASON
        socket = _Socket(
            [_text_action({"type": "attach", "employeeId": "t_employee"})]
        )
        await hub.websocket(socket)  # type: ignore[arg-type]
        assert socket.closed == [(1013, REPLAY_UNAVAILABLE_CLOSE_REASON)]

        live = BrowserSubscription(
            "browser-2", "t_employee", asyncio.Queue(maxsize=1)
        )
        live.close_reason = "conversation client is too slow"
        live.closed.set()
        writer_socket = _Socket([])
        await hub._websocket_writer(writer_socket, live)  # noqa: SLF001
        assert writer_socket.closed == [(1013, "conversation client is too slow")]

    asyncio.run(exercise())

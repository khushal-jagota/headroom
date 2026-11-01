"""Route native backend events to live delivery or the durable runner output."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from planner.conversation.backends.contracts import (
    BackendEventValue,
    ToolCallFinished,
    ToolCallProgress,
    ToolCallStarted,
    TurnToken,
    UserInputFailed,
)
from planner.execution_identity import ChildReference
from planner.executor.configuration import MAX_TOOL_DETAIL_CHARACTERS
from planner.executor.protocol import (
    LIVE_BACKEND_EVENT_METHODS,
    BackendEvent,
    encode_backend_event,
)

if TYPE_CHECKING:
    from planner.executor.mac_runner import MacRunner


class NativeEventSink:
    def __init__(self, runner: MacRunner, reference: ChildReference) -> None:
        self.runner = runner
        self.reference = reference

    async def report(self, turn: TurnToken | None, event: BackendEventValue) -> None:
        if isinstance(
            event, (ToolCallStarted, ToolCallProgress, ToolCallFinished, UserInputFailed)
        ):
            if event.detail is not None:
                event = replace(event, detail=event.detail[:MAX_TOOL_DETAIL_CHARACTERS])
        method, arguments = encode_backend_event(event)
        if method in LIVE_BACKEND_EVENT_METHODS:
            await self.runner._send_live_event(
                BackendEvent(self.reference, method, turn, arguments)
            )
            return
        await self.runner.report_event(self.reference, turn, event)

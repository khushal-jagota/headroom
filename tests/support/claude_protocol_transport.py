"""A scripted Claude transport for native emission tests; no provider subprocess."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions

from planner.conversation.backends.claude_agent_sdk import _ClaudeCleanupTransport


class ScriptedTransport(_ClaudeCleanupTransport):
    def __init__(self, options: ClaudeAgentOptions) -> None:
        super().__init__("", options)
        self.frames: list[dict[str, Any]] = []
        self.replies: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._stdin_stream = self  # type: ignore[assignment]
        self._ready = True

    async def connect(self) -> None:
        self._ready = True

    async def send(self, data: str) -> None:
        frame = json.loads(data)
        self.frames.append(frame)
        if frame.get("type") == "user":
            self.replies.put_nowait(
                {"type": "command_lifecycle", "command_uuid": frame["uuid"], "state": "queued"}
            )
            return
        if frame.get("type") != "control_request":
            return
        assert frame["request"]["subtype"] == "initialize"
        response = {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": frame["request_id"],
                "response": {
                    "commands": [],
                    "models": [],
                    "agents": [],
                    "account": {},
                    "output_style": "default",
                    "available_output_styles": [],
                },
            },
        }
        self.replies.put_nowait(response)

    async def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        while (message := await self.replies.get()) is not None:
            yield message

    async def close(self) -> None:
        self._ready = False
        self.replies.put_nowait(None)

    async def end_input(self) -> None:
        return

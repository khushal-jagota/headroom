"""Claude metadata control transport.

Catalog requests correlate their replies without a settings proof.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from claude_agent_sdk import Transport

from planner.conversation.backends.claude_settings_config import (
    CONTROL_TIMEOUT_SECONDS,
)


class ClaudeSettingsUnavailable(Exception):
    """A sanitized failure to obtain a complete supported control snapshot."""


class ClaudeControlTransport(Transport):
    """Use the SDK's custom transport seam to own metadata response futures."""

    def __init__(self, inner: Transport) -> None:
        self._inner = inner
        self._metadata_responses: dict[str, asyncio.Future[dict[str, Any] | None]] = {}

    async def connect(self) -> None:
        await self._inner.connect()

    async def write(self, data: str) -> None:
        await self._inner.write(data)

    async def control_request(self, subtype: str) -> dict[str, Any]:
        if subtype != "list_models":
            raise ClaudeSettingsUnavailable("Unsupported Claude metadata request")
        identity = f"panels-metadata-{uuid4().hex}"
        future: asyncio.Future[dict[str, Any] | None] = asyncio.get_running_loop().create_future()
        self._metadata_responses[identity] = future
        try:
            async with asyncio.timeout(CONTROL_TIMEOUT_SECONDS):
                await self.write(json.dumps({"type": "control_request", "request_id": identity,
                                             "request": {"subtype": subtype}}) + "\n")
                result = await asyncio.shield(future)
            if result is None:
                raise ClaudeSettingsUnavailable("Claude metadata response unavailable")
            return result
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ClaudeSettingsUnavailable("Claude metadata response unavailable") from None
        finally:
            self._metadata_responses.pop(identity, None)
            if not future.done():
                future.cancel()

    def observe_control(self, message: dict[str, Any]) -> None:
        if message.get("type") != "control_response":
            return
        response = message.get("response")
        if not isinstance(response, dict):
            return
        identity = response.get("request_id")
        future = self._metadata_responses.get(identity) if isinstance(identity, str) else None
        if future is None or future.done():
            return
        payload = response.get("response")
        future.set_result(payload if response.get("subtype") == "success"
                          and isinstance(payload, dict) else None)

    def settle_control(self) -> None:
        for future in self._metadata_responses.values():
            if not future.done():
                future.set_result(None)
        self._metadata_responses.clear()

    async def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        try:
            async for message in self._inner.read_messages():
                self.observe_control(message)
                yield message
        finally:
            self.settle_control()

    async def close(self) -> None:
        self.settle_control()
        await self._inner.close()

    async def end_input(self) -> None:
        await self._inner.end_input()

    def is_ready(self) -> bool:
        return self._inner.is_ready()

"""The real pinned SDK reader correlates synthetic metadata before user bytes."""
from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions

from planner.conversation.backends.claude_agent_sdk import (
    _ClaudeCleanupTransport,
    _ClaudeProtocolTransport,
    _ObservedClaudeSdkClient,
)
from planner.conversation.backends.claude_settings import (
    ClaudeSettingsUnavailable,
)
from planner.conversation.backends.contracts import (
    AdmissionRefused,
    emission_check_scope,
)

SECRET = "synthetic-never-disclose-settings-value"


def settings() -> dict[str, Any]:
    return {"effective": {"env": {"ANTHROPIC_API_KEY": SECRET}, "model": "opus"},
            "sources": [{"source": "userSettings", "settings": {"model": "opus"}}],
            "applied": {"model": "claude-opus-5", "effort": "high"}}


def models() -> dict[str, Any]:
    return {"models": [{"value": "opus", "resolvedModel": "claude-opus-5",
                        "supportedEffortLevels": ["high", "max"]}]}


class ScriptedTransport(_ClaudeCleanupTransport):
    def __init__(self, options: ClaudeAgentOptions) -> None:
        super().__init__("", options)
        self.frames: list[dict[str, Any]] = []
        self.replies: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self.settings = settings()
        self.models = models()
        self.error = False
        self.pause = False
        self.requests = asyncio.Event()
        self._stdin_stream = self  # type: ignore[assignment]
        self._ready = True

    async def connect(self) -> None:
        self._ready = True

    async def send(self, data: str) -> None:
        frame = json.loads(data)
        self.frames.append(frame)
        if frame.get("type") == "user":
            self.replies.put_nowait({"type": "command_lifecycle",
                                    "command_uuid": frame["uuid"], "state": "queued"})
            return
        if frame.get("type") != "control_request":
            return
        subtype = frame["request"]["subtype"]
        if subtype == "initialize":
            payload = {"commands": [], "models": [], "agents": [], "account": {},
                       "output_style": "default", "available_output_styles": []}
        elif subtype == "get_settings":
            payload = copy.deepcopy(self.settings)
        else:
            assert subtype == "list_models"
            payload = copy.deepcopy(self.models)
        if self.pause and subtype != "initialize":
            self.requests.set()
            return
        response = {"type": "control_response", "response": {
            "subtype": "error" if self.error and subtype != "initialize" else "success",
            "request_id": frame["request_id"], "response": payload,
            "error": SECRET,
        }}
        self.replies.put_nowait(response)

    async def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        while (message := await self.replies.get()) is not None:
            yield message

    async def close(self) -> None:
        self._ready = False
        self.replies.put_nowait(None)

    async def end_input(self) -> None:
        return


def bench() -> tuple[_ObservedClaudeSdkClient, _ClaudeProtocolTransport,
                     ScriptedTransport]:
    options = ClaudeAgentOptions(model="opus", effort="high")
    inner = ScriptedTransport(options)
    transport = _ClaudeProtocolTransport(inner)
    client = _ObservedClaudeSdkClient(options, transport)
    return client, transport, inner


async def message(identity: str) -> AsyncIterator[dict[str, Any]]:
    yield {"type": "user", "uuid": identity,
           "message": {"role": "user", "content": "synthetic text"}}


def test_control_cancellation_disconnect_and_late_reply_release_owned_futures() -> None:
    async def exercise() -> None:
        client, transport, inner = bench()
        await client.connect()
        try:
            inner.pause = True
            waiting = asyncio.create_task(transport.control_request("list_models"))
            await inner.requests.wait()
            identity = inner.frames[-1]["request_id"]
            assert identity.startswith("panels-metadata-")
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
            assert not transport._metadata_responses
            transport.observe_control({"type": "control_response", "response": {
                "subtype": "success", "request_id": identity, "response": settings(),
            }})
            inner.requests.clear()
            waiting = asyncio.create_task(transport.control_request("list_models"))
            await inner.requests.wait()
            transport.settle_control()
            with pytest.raises(ClaudeSettingsUnavailable):
                await waiting
            assert not transport._metadata_responses
        finally:
            await client.disconnect()
    asyncio.run(exercise())







def test_user_emission_needs_only_final_token_policy_guard() -> None:
    async def exercise() -> None:
        client, _transport, inner = bench()
        await client.connect()
        try:
            inner.error = True  # Metadata cannot veto a literal provider token.
            checks: list[object] = []
            with emission_check_scope(lambda prepared=None: checks.append(prepared)):
                await client.query(message("exact-uuid"))
            users = [frame for frame in inner.frames if frame["type"] == "user"]
            assert [frame["uuid"] for frame in users] == ["exact-uuid"]
            assert len(checks) == 1
            assert checks[0] is not None
            assert [frame["request"]["subtype"] for frame in inner.frames
                    if frame["type"] == "control_request"] == ["initialize"]
        finally:
            await client.disconnect()
    asyncio.run(exercise())


def test_final_policy_refusal_reaches_actual_user_write_lock() -> None:
    async def exercise() -> None:
        client, _transport, inner = bench()
        await client.connect()
        try:
            def refuse(prepared: object = None) -> None:
                raise AdmissionRefused("explicit token disabled")
            with emission_check_scope(refuse), pytest.raises(AdmissionRefused):
                await client.query(message("never-written"))
            assert not [frame for frame in inner.frames if frame["type"] == "user"]
        finally:
            await client.disconnect()
    asyncio.run(exercise())

"""The real pinned SDK reader correlates synthetic metadata before user bytes."""
from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions

from planner.conversation.backends.claude_agent_sdk import (
    _ClaudeCleanupTransport,
    _ClaudeProtocolTransport,
    _ObservedClaudeSdkClient,
)
from planner.conversation.backends.claude_settings import (
    ClaudeSettingsScopeRegistry,
    ClaudeSettingsUnavailable,
    read_claude_catalog_settings_snapshot,
)
from planner.conversation.backends.contracts import (
    AdmissionRefused,
    BackendUserEmissionReceipt,
    UserEmissionRequirement,
    emission_check_scope,
    user_emission_requirement_scope,
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
                     ScriptedTransport, ClaudeSettingsScopeRegistry]:
    options = ClaudeAgentOptions(model="opus", effort="high")
    inner = ScriptedTransport(options)
    transport = _ClaudeProtocolTransport(inner)
    registry = ClaudeSettingsScopeRegistry()
    client = _ObservedClaudeSdkClient(options, transport, claude_scope_registry=registry)
    return client, transport, inner, registry


async def message(identity: str) -> AsyncIterator[dict[str, Any]]:
    yield {"type": "user", "uuid": identity,
           "message": {"role": "user", "content": "synthetic text"}}


def requirement(scope: str, accepted: list[BackendUserEmissionReceipt]) -> UserEmissionRequirement:
    return UserEmissionRequirement(scope, "opus", "claude-opus-5", "high", "attempt", None,
                                   accepted.append)


def test_sdk_control_reader_proves_each_actual_uuid_and_keeps_sources_private(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def exercise() -> None:
        client, transport, inner, registry = bench()
        await client.connect()
        try:
            sample = await read_claude_catalog_settings_snapshot(transport)
            scope = registry.retain(sample.settings)
            assert SECRET not in repr(sample)
            assert SECRET not in repr(registry)
            accepted: list[BackendUserEmissionReceipt] = []
            with emission_check_scope(lambda prepared=None: None), user_emission_requirement_scope(
                requirement(scope, accepted)
            ):
                await asyncio.gather(client.query(message("first")), client.query(message("steer")))
            assert [frame["uuid"] for frame in inner.frames if frame["type"] == "user"] == [
                "first", "steer",
            ]
            assert [receipt.message_uuid for receipt in accepted] == ["first", "steer"]
            for receipt in accepted:
                with pytest.raises(AdmissionRefused):
                    registry.consume(receipt, child_lifetime=receipt.child_lifetime,
                                     message_uuid=receipt.message_uuid)
            subtypes = [frame["request"]["subtype"] for frame in inner.frames
                        if frame["type"] == "control_request"]
            assert subtypes == ["initialize"] + ["get_settings", "list_models", "get_settings"] * 3
            assert not transport._metadata_responses
            assert SECRET not in caplog.text
        finally:
            await client.disconnect()
    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["scope", "disabled", "hidden", "alias", "effort", "conflict",
                                     "error", "missing-proof"])
def test_actual_child_refuses_before_user_bytes(failure: str) -> None:
    async def exercise() -> None:
        client, transport, inner, registry = bench()
        await client.connect()
        try:
            sample = await read_claude_catalog_settings_snapshot(transport)
            scope = registry.retain(sample.settings)
            if failure == "scope":
                inner.settings["effective"]["model"] = "sonnet"
            elif failure == "disabled":
                inner.models["models"][0]["disabled"] = True
            elif failure == "hidden":
                inner.models["models"][0]["hidden"] = True
            elif failure == "alias":
                inner.models["models"][0]["resolvedModel"] = "claude-opus-6"
            elif failure == "effort":
                inner.settings["applied"]["effort"] = "max"
            elif failure == "conflict":
                duplicate = copy.deepcopy(inner.models["models"][0])
                duplicate["supportedEffortLevels"] = ["high"]
                inner.models["models"].append(duplicate)
            elif failure == "error":
                inner.error = True
            accepted: list[BackendUserEmissionReceipt] = []
            with emission_check_scope(lambda prepared=None: None):
                with pytest.raises(AdmissionRefused) as refused:
                    if failure == "missing-proof":
                        await client.query(message("never-written"))
                    else:
                        with user_emission_requirement_scope(requirement(scope, accepted)):
                            await client.query(message("never-written"))
            assert SECRET not in str(refused.value)
            assert SECRET not in repr(refused.value)
            assert not accepted
            assert not [frame for frame in inner.frames if frame["type"] == "user"]
            assert not transport._metadata_responses
            assert not registry._receipts
        finally:
            await client.disconnect()
    asyncio.run(exercise())


def test_control_cancellation_disconnect_and_late_reply_release_owned_futures() -> None:
    async def exercise() -> None:
        client, transport, inner, _registry = bench()
        await client.connect()
        try:
            inner.pause = True
            waiting = asyncio.create_task(transport.control_request("get_settings"))
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
            waiting = asyncio.create_task(transport.control_request("get_settings"))
            await inner.requests.wait()
            transport.settle_control()
            with pytest.raises(ClaudeSettingsUnavailable):
                await waiting
            assert not transport._metadata_responses
        finally:
            await client.disconnect()
    asyncio.run(exercise())


def test_receipt_cannot_transfer_child_uuid_or_forged_scope() -> None:
    async def exercise() -> None:
        client, transport, _inner, registry = bench()
        await client.connect()
        try:
            sample = await read_claude_catalog_settings_snapshot(transport)
            scope = registry.retain(sample.settings)
            receipt = registry.issue_receipt(sample, requirement(scope, []),
                                             child_lifetime="child", message_uuid="uuid")
            with pytest.raises(AdmissionRefused):
                registry.consume(receipt, child_lifetime="other", message_uuid="uuid")
            with pytest.raises(AdmissionRefused):
                registry.consume(receipt, child_lifetime="child", message_uuid="uuid")
            receipt = registry.issue_receipt(sample, requirement(scope, []),
                                             child_lifetime="child", message_uuid="uuid")
            with pytest.raises(AdmissionRefused):
                registry.consume(replace(receipt, message_uuid="different"),
                                 child_lifetime="child", message_uuid="different")
            with pytest.raises(AdmissionRefused):
                registry.issue_receipt(sample, requirement("forged", []),
                                       child_lifetime="child", message_uuid="uuid")
        finally:
            await client.disconnect()
    asyncio.run(exercise())


@pytest.mark.parametrize("change", ["between-replies", "malformed-effort", "final-policy"])
def test_snapshot_change_malformed_metadata_and_final_policy_refuse_without_bytes(
    change: str,
) -> None:
    async def exercise() -> None:
        client, transport, inner, registry = bench()
        await client.connect()
        try:
            sample = await read_claude_catalog_settings_snapshot(transport)
            scope = registry.retain(sample.settings)
            if change == "between-replies":
                original_send = inner.send

                async def changing_send(data: str) -> None:
                    await original_send(data)
                    frame = json.loads(data)
                    if frame.get("request", {}).get("subtype") == "list_models":
                        inner.settings["sources"][0]["settings"]["effortLevel"] = "max"

                inner.send = changing_send  # type: ignore[method-assign]
            elif change == "malformed-effort":
                inner.settings["applied"]["effort"] = [SECRET]
            accepted: list[BackendUserEmissionReceipt] = []
            requested = requirement(scope, accepted)
            if change == "final-policy":
                def refuse(_receipt: BackendUserEmissionReceipt) -> None:
                    raise AdmissionRefused("model verification policy changed")
                requested = replace(requested, check=refuse)
            with emission_check_scope(lambda prepared=None: None), user_emission_requirement_scope(
                requested
            ):
                with pytest.raises(AdmissionRefused) as refused:
                    await client.query(message("refused"))
            assert SECRET not in str(refused.value)
            assert not [frame for frame in inner.frames if frame["type"] == "user"]
            assert not registry._receipts
            assert not transport._metadata_responses
        finally:
            await client.disconnect()
    asyncio.run(exercise())


def test_scope_comparison_preserves_json_types_configured_effort_and_null_launch_intent() -> None:
    async def exercise() -> None:
        options = ClaudeAgentOptions(model="opus")
        inner = ScriptedTransport(options)
        transport = _ClaudeProtocolTransport(inner)
        registry = ClaudeSettingsScopeRegistry()
        client = _ObservedClaudeSdkClient(options, transport, claude_scope_registry=registry)
        await client.connect()
        try:
            inner.settings["effective"]["someSwitch"] = True
            inner.settings["sources"][0]["settings"]["effortLevel"] = "max"
            sample = await read_claude_catalog_settings_snapshot(transport)
            scope = registry.retain(sample.settings)
            accepted: list[BackendUserEmissionReceipt] = []
            requested = replace(requirement(scope, accepted), reasoning_effort=None)
            with emission_check_scope(lambda prepared=None: None), user_emission_requirement_scope(
                requested
            ):
                await client.query(message("provider-default"))
            assert accepted[0].reasoning_effort is None
            # Source effort remains max while observed runtime default is high.
            inner.settings["effective"]["someSwitch"] = 1
            changed = await read_claude_catalog_settings_snapshot(transport)
            assert not registry.matches(scope, changed.settings)
            inner.settings["effective"]["someSwitch"] = True
            inner.settings["sources"][0]["settings"]["effortLevel"] = "high"
            changed = await read_claude_catalog_settings_snapshot(transport)
            assert not registry.matches(scope, changed.settings)
        finally:
            await client.disconnect()
    asyncio.run(exercise())

"""Deterministic in-memory ACP agent used only by the conformance harness."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any, cast

import acp
from acp.interfaces import Client
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    AllowedOutcome,
    AvailableCommand,
    AvailableCommandsUpdate,
    ClientCapabilities,
    CloseSessionResponse,
    ContentToolCallContent,
    ForkSessionResponse,
    Implementation,
    InitializeResponse,
    LoadSessionResponse,
    NewSessionResponse,
    PermissionOption,
    PlanEntry,
    PromptResponse,
    SessionCapabilities,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SessionForkCapabilities,
    SessionInfoUpdate,
    SessionNotification,
    SetSessionConfigOptionResponse,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    UserMessageChunk,
)

from planner.conversation.hermes_turn_strategy import (
    HERMES_SUMMARY_END_MARKER,
    HERMES_SUMMARY_PREFIX,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HERMES_REPLAY_FIXTURE = REPOSITORY_ROOT / "tests/fixtures/acp/hermes-shaped-replay-v1.json"


def _diagnostic(message: str) -> None:
    print(f"scripted-acp-agent: {message}", file=sys.stderr, flush=True)


def _text(text: str) -> TextContentBlock:
    return TextContentBlock(type="text", text=text)


def _audit_prompt_receipt(session_id: str, prompt: list[Any]) -> None:
    """Optional cross-process evidence that the SDK agent received the real prompt."""

    audit_path = os.environ.get("ACP_TEST_PROMPT_AUDIT_PATH")
    audit_udp = os.environ.get("ACP_TEST_PROMPT_AUDIT_UDP")
    if audit_path is None and audit_udp is None:
        return
    record = {
        "sessionId": session_id,
        "prompt": [
            item.model_dump(mode="json", by_alias=True) if hasattr(item, "model_dump") else item
            for item in prompt
        ],
    }
    serialized = json.dumps(record, separators=(",", ":"))
    if audit_path is not None:
        with Path(audit_path).open("a", encoding="utf-8") as stream:
            stream.write(serialized + "\n")
    if audit_udp is not None:
        host, separator, raw_port = audit_udp.rpartition(":")
        if not separator:
            raise RuntimeError("ACP_TEST_PROMPT_AUDIT_UDP must be host:port")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as audit_socket:
            audit_socket.sendto(serialized.encode(), (host, int(raw_port)))


def _audit_requested_cancel_late_send(session_id: str) -> None:
    audit_path = os.environ.get("ACP_TEST_LATE_SEND_AUDIT_PATH")
    if audit_path is None:
        return
    record = {
        "sessionId": session_id,
        "event": "requested_cancel_late_send_completed",
    }
    with Path(audit_path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, separators=(",", ":")) + "\n")


def _audit_legacy_model(record: dict[str, Any]) -> None:
    audit_path = os.environ.get("ACP_TEST_LEGACY_MODEL_AUDIT_PATH")
    if audit_path is None:
        return
    payload = {"processId": os.getpid(), **record}
    with Path(audit_path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, separators=(",", ":")) + "\n")


class ScriptedAcpAgent:
    def __init__(self) -> None:
        self._client: Client | None = None
        self._next_session_number = 1
        self._durable_store_path = (
            Path(value) if (value := os.environ.get("ACP_TEST_DURABLE_STORE_PATH")) else None
        )
        self._durable_session_updates = self._load_durable_store()
        self._session_updates: dict[str, list[Any]] = copy.deepcopy(self._durable_session_updates)
        existing_numbers = [
            int(session_id.rsplit("-", 1)[1])
            for session_id in self._session_updates
            if session_id.startswith("scripted-session-") and session_id.rsplit("-", 1)[1].isdigit()
        ]
        if existing_numbers:
            self._next_session_number = max(existing_numbers) + 1
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._session_configuration: dict[str, dict[str, str]] = {}
        self._post_fork_metadata_complete: dict[str, asyncio.Event] = {}
        self._prompt_release_socket: socket.socket | None = None
        prompt_release_udp = os.environ.get("ACP_TEST_PROMPT_RELEASE_UDP")
        if prompt_release_udp is not None:
            host, separator, raw_port = prompt_release_udp.rpartition(":")
            if not separator:
                raise RuntimeError("ACP_TEST_PROMPT_RELEASE_UDP must be host:port")
            self._prompt_release_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._prompt_release_socket.bind((host, int(raw_port)))

    def on_connect(self, client: Client) -> None:
        self._client = client

    @property
    def client(self) -> Client:
        if self._client is None:
            raise RuntimeError("scripted agent has not connected")
        return self._client

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        del client_capabilities, client_info, kwargs
        _diagnostic("initialize")
        stderr_bytes = int(os.environ.get("ACP_TEST_STDERR_BYTES", "0"))
        if stderr_bytes:
            remaining = stderr_bytes
            chunk = b"scripted-stderr-" * 256
            while remaining > 0:
                written = chunk[:remaining]
                sys.stderr.buffer.write(written)
                sys.stderr.buffer.flush()
                remaining -= len(written)
        return InitializeResponse(
            protocol_version=int(
                os.environ.get("ACP_TEST_PROTOCOL_VERSION", str(protocol_version))
            ),
            agent_capabilities=AgentCapabilities(
                load_session=os.environ.get("ACP_TEST_LOAD_SESSION", "1") == "1",
                session_capabilities=SessionCapabilities(
                    fork=(
                        SessionForkCapabilities()
                        if os.environ.get("ACP_TEST_FORK_SESSION", "1") == "1"
                        else None
                    )
                ),
            ),
            agent_info=Implementation(
                name=os.environ.get("ACP_TEST_AGENT_NAME", "panels-scripted-agent"),
                version=os.environ.get("ACP_TEST_AGENT_VERSION", "1.0.0"),
            ),
        )

    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        del mcp_servers, kwargs
        session_id = f"scripted-session-{self._next_session_number}"
        self._next_session_number += 1
        self._session_updates[session_id] = []
        if os.environ.get("ACP_TEST_HERMES_HISTORY") == "1":
            raw_fixture = json.loads(HERMES_REPLAY_FIXTURE.read_text())
            self._session_updates[session_id] = [
                SessionNotification.model_validate(item, strict=True).update for item in raw_fixture
            ]
        self._durable_session_updates[session_id] = copy.deepcopy(self._session_updates[session_id])
        self._persist_durable_store()
        self._cancel_events[session_id] = asyncio.Event()
        self._session_configuration[session_id] = {
            "model": "probe-model",
            "reasoning": "probe-high",
        }
        _audit_legacy_model({"event": "new_session", "sessionId": session_id})
        _diagnostic(f"new {session_id}")
        return NewSessionResponse(
            session_id=session_id,
            config_options=self._configuration_options(session_id),
            field_meta={
                "scripted": {
                    "cwd": cwd,
                    "additionalDirectories": additional_directories or [],
                    "safeEnvironmentPresent": sorted(
                        name
                        for name in ("PLAN_ACTOR", "PLAN_TICKET_ID", "HERMES_HOME")
                        if name in os.environ
                    ),
                }
            },
        )

    async def set_config_option(
        self,
        session_id: str,
        config_id: str,
        value: str | bool,
        **kwargs: Any,
    ) -> SetSessionConfigOptionResponse:
        del kwargs
        if os.environ.get("ACP_TEST_CONFIG_FAIL") == config_id:
            raise RuntimeError(f"scripted configuration failure: {config_id}")
        if not isinstance(value, str):
            raise ValueError("scripted configuration accepts select values only")
        configuration = self._session_configuration[session_id]
        if config_id == "scripted-model":
            if value not in {"probe-model", "probe-alt"}:
                raise ValueError("unknown scripted model")
            configuration["model"] = value
            if value == "probe-alt":
                configuration["reasoning"] = "probe-low"
        elif config_id == "scripted-reasoning":
            available = (
                {"probe-low", "probe-high"}
                if configuration["model"] == "probe-model"
                else {"probe-low"}
            )
            if value not in available:
                raise ValueError("unknown scripted reasoning effort")
            configuration["reasoning"] = value
        else:
            raise ValueError("unknown scripted configuration option")
        self._audit_configuration(
            {
                "event": "set_config_option",
                "sessionId": session_id,
                "configId": config_id,
                "value": value,
            }
        )
        return SetSessionConfigOptionResponse(
            config_options=self._configuration_options(session_id)
        )

    async def set_legacy_session_model(self, session_id: str, model_id: str) -> dict[str, Any]:
        if os.environ.get("ACP_TEST_LEGACY_MODEL_FAIL") == "1":
            raise RuntimeError("scripted legacy model failure")
        if session_id not in self._session_configuration:
            raise ValueError("unknown scripted session")
        self._session_configuration[session_id]["model"] = model_id
        _audit_legacy_model(
            {
                "event": "set_model",
                "sessionId": session_id,
                "modelId": model_id,
            }
        )
        return {}

    async def close_session(self, session_id: str, **kwargs: Any) -> CloseSessionResponse:
        del kwargs
        self._audit_configuration({"event": "close_session", "sessionId": session_id})
        self._session_configuration.pop(session_id, None)
        self._session_updates.pop(session_id, None)
        self._cancel_events.pop(session_id, None)
        return CloseSessionResponse()

    async def fork_session(
        self,
        session_id: str,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> ForkSessionResponse:
        _diagnostic(f"fork {session_id}")
        fork_session_id = (
            ""
            if os.environ.get("ACP_TEST_EMPTY_FORK_ID") == "1"
            else f"scripted-session-{self._next_session_number}"
        )
        if fork_session_id:
            self._next_session_number += 1
        if fork_session_id:
            self._session_updates[fork_session_id] = copy.deepcopy(
                self._session_updates.get(session_id, [])
            )
            self._durable_session_updates[fork_session_id] = copy.deepcopy(
                self._session_updates[fork_session_id]
            )
            self._persist_durable_store()
        self._cancel_events[fork_session_id] = asyncio.Event()
        if fork_session_id and (
            os.environ.get("ACP_TEST_POST_FORK_SOURCE_UPDATE") == "1"
            or os.environ.get("ACP_TEST_POST_FORK_CANDIDATE_UPDATE") == "1"
        ):
            metadata_complete = asyncio.Event()
            self._post_fork_metadata_complete[fork_session_id] = metadata_complete
            asyncio.get_running_loop().call_soon(
                asyncio.create_task,
                self._emit_post_fork_metadata(session_id, fork_session_id, metadata_complete),
            )
        return ForkSessionResponse(
            session_id=fork_session_id,
            field_meta={
                "scripted": {
                    "sourceSessionId": session_id,
                    "cwd": cwd,
                    "additionalDirectories": additional_directories or [],
                    "mcpServerCount": len(mcp_servers or []),
                    **kwargs,
                }
            },
        )

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[Any] | None = None,
        additional_directories: list[str] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse:
        del cwd, mcp_servers, additional_directories, kwargs
        _diagnostic(f"load {session_id}")
        metadata_complete = self._post_fork_metadata_complete.pop(session_id, None)
        if metadata_complete is not None:
            await metadata_complete.wait()
        durable = self._durable_session_updates.setdefault(session_id, [])
        self._session_updates[session_id] = copy.deepcopy(durable)
        self._cancel_events.setdefault(session_id, asyncio.Event())
        for update in self._session_updates[session_id]:
            await self.client.session_update(session_id=session_id, update=update)
        malformed_load = os.environ.get("ACP_TEST_MALFORMED_LOAD")
        if malformed_load:
            await self._emit_malformed_update(
                session_id=session_id,
                partial=malformed_load == "partial",
            )
        if os.environ.get("ACP_TEST_POST_LOAD_METADATA") == "1":
            asyncio.get_running_loop().call_soon(
                asyncio.create_task,
                self._emit_ephemeral_available_commands(session_id, "post-load-metadata"),
            )
        return LoadSessionResponse()

    async def prompt(
        self,
        session_id: str,
        prompt: list[Any],
        **kwargs: Any,
    ) -> PromptResponse:
        _audit_prompt_receipt(session_id, prompt)
        configuration = self._session_configuration.get(session_id)
        if configuration is not None:
            _audit_legacy_model(
                {
                    "event": "prompt",
                    "sessionId": session_id,
                    "modelId": configuration["model"],
                }
            )
            self._audit_configuration(
                {
                    "event": "prompt",
                    "sessionId": session_id,
                    "model": configuration["model"],
                    "reasoning": configuration["reasoning"],
                }
            )
        script = str(kwargs.get("script", "default"))
        if script == "default" and any(
            "[ACP_TEST_WAIT_FOR_CANCEL]" in str(getattr(item, "text", "")) for item in prompt
        ):
            script = "wait_for_cancel"
        if script == "default" and any(
            "[ACP_TEST_PERMISSION]" in str(getattr(item, "text", "")) for item in prompt
        ):
            script = "permission"
        _diagnostic(f"prompt {session_id} {script}")
        if script == "die":
            _diagnostic("deterministic death")
            os._exit(23)

        if script == "burst":
            burst_count = int(kwargs.get("count", 128))
            for index in range(burst_count):
                await self._emit(
                    session_id,
                    AgentThoughtChunk(
                        session_update="agent_thought_chunk",
                        message_id="burst-message",
                        content=_text(f"burst-{index}"),
                    ),
                )
            return PromptResponse(stop_reason="end_turn")

        cancel_event = self._cancel_events[session_id]
        cancel_event.clear()
        message_id = None if script == "missing_ids" else f"{script}-message"
        for content in prompt:
            await self._emit(
                session_id,
                UserMessageChunk(
                    session_update="user_message_chunk",
                    message_id=message_id,
                    content=content,
                ),
            )

        if script in {"explicit_compaction", "explicit_compaction_marker_free"}:
            await self._emit(
                session_id,
                SessionInfoUpdate(
                    session_update="session_info_update",
                    title="Compacting session",
                    field_meta={
                        "scripted": {
                            "compaction": {
                                "trigger": "explicit",
                                "state": "compacting",
                            }
                        }
                    },
                ),
            )
        if script == "automatic_compaction":
            await self._emit(
                session_id,
                SessionInfoUpdate(
                    session_update="session_info_update",
                    title="Compacting session",
                    field_meta={
                        "hermes": {
                            "sessionProvenance": {
                                "reason": "compression",
                                "state": "compacting",
                                "acpSessionId": session_id,
                                "previousHermesSessionId": "hermes-before",
                                "currentHermesSessionId": "hermes-after",
                                "compressionDepth": 1,
                            }
                        }
                    },
                ),
            )

        if script == "wait_for_cancel":
            await cancel_event.wait()
            return PromptResponse(stop_reason="cancelled")

        if script == "requested_cancel_unwind":
            await cancel_event.wait()
            await self.client.session_update(
                session_id=session_id,
                update=AgentMessageChunk(
                    session_update="agent_message_chunk",
                    message_id="requested-cancel-late-old",
                    content=_text("REQUESTED CANCEL LATE OLD OUTPUT"),
                ),
            )
            _audit_requested_cancel_late_send(session_id)
            raise RuntimeError("scripted prompt unwound after requested cancellation")

        if script == "delayed":
            await asyncio.sleep(0.01)

        if script == "permission":
            tool_call = ToolCallStart(
                session_update="tool_call",
                tool_call_id="permission-tool",
                title="Inspect protected file",
                kind="read",
                status="pending",
            )
            await self._emit(session_id, tool_call)
            response = await self.client.request_permission(
                session_id=session_id,
                tool_call=ToolCallUpdate(
                    tool_call_id="permission-tool",
                    title="Inspect protected file",
                    kind="read",
                    status="pending",
                ),
                options=[
                    PermissionOption(option_id="allow-once", name="Allow once", kind="allow_once"),
                    PermissionOption(option_id="reject-once", name="Reject", kind="reject_once"),
                ],
            )
            selected = (
                response.outcome.option_id
                if isinstance(response.outcome, AllowedOutcome)
                else "cancelled"
            )
            await self._emit(
                session_id,
                AgentMessageChunk(
                    session_update="agent_message_chunk",
                    message_id="permission-result",
                    content=_text(f"Permission outcome: {selected}"),
                ),
            )
            return PromptResponse(stop_reason="end_turn")

        if script == "reverse_filesystem":
            path = str(kwargs["path"])
            before = await self.client.read_text_file(
                session_id=session_id,
                path=path,
                line=2,
                limit=1,
            )
            await self.client.write_text_file(
                session_id=session_id,
                path=path,
                content="written through ACP",
            )
            after = await self.client.read_text_file(
                session_id=session_id,
                path=path,
            )
            await self._emit(
                session_id,
                AgentMessageChunk(
                    session_update="agent_message_chunk",
                    message_id="reverse-filesystem-result",
                    content=_text(f"{before.content!r}|{after.content!r}"),
                ),
            )
            return PromptResponse(stop_reason="end_turn")

        if script == "reverse_terminal":
            created = await self.client.create_terminal(
                session_id=session_id,
                command=sys.executable,
                args=[str(kwargs["fixture"]), "stream"],
                cwd=str(kwargs["cwd"]),
                output_byte_limit=64,
            )
            waited = await self.client.wait_for_terminal_exit(
                session_id=session_id,
                terminal_id=created.terminal_id,
            )
            output = await self.client.terminal_output(
                session_id=session_id,
                terminal_id=created.terminal_id,
            )
            await self.client.release_terminal(
                session_id=session_id,
                terminal_id=created.terminal_id,
            )
            await self._emit(
                session_id,
                AgentMessageChunk(
                    session_update="agent_message_chunk",
                    message_id="reverse-terminal-result",
                    content=_text(f"{output.output}|{waited.exit_code}|{output.truncated}"),
                ),
            )
            return PromptResponse(stop_reason="end_turn")

        if script == "concurrent":
            concurrent_updates = [
                AgentThoughtChunk(
                    session_update="agent_thought_chunk",
                    message_id="concurrent-message",
                    content=_text(f"wire-{index}"),
                )
                for index in range(1, 4)
            ]

            async def emit_after(index: int, update: Any) -> None:
                await asyncio.sleep(index * 0.001)
                await self._emit(session_id, update)

            await asyncio.gather(
                *(emit_after(index, update) for index, update in enumerate(concurrent_updates))
            )
            return PromptResponse(stop_reason="end_turn")

        for thought_text in ("private typed ", "reasoning"):
            await self._emit(
                session_id,
                AgentThoughtChunk(
                    session_update="agent_thought_chunk",
                    message_id=message_id,
                    content=_text(thought_text),
                ),
            )
        await self._emit(
            session_id,
            ToolCallStart(
                session_update="tool_call",
                tool_call_id="tool-1",
                title="Read contract",
                kind="read",
                status="in_progress",
                content=[ContentToolCallContent(type="content", content=_text("opening"))],
            ),
        )
        await self._emit(
            session_id,
            AgentPlanUpdate(
                session_update="plan",
                entries=[PlanEntry(content="First snapshot", priority="high", status="pending")],
            ),
        )
        await self._emit(
            session_id,
            ToolCallProgress(
                session_update="tool_call_update",
                tool_call_id="tool-1",
                title="Read contract",
                status="completed",
            ),
        )
        await self._emit(
            session_id,
            AgentPlanUpdate(
                session_update="plan",
                entries=[
                    PlanEntry(content="Replacement snapshot", priority="high", status="completed")
                ],
            ),
        )
        await self._emit(
            session_id,
            AvailableCommandsUpdate(
                session_update="available_commands_update",
                available_commands=[
                    AvailableCommand(name="compact", description="Compact context")
                ],
            ),
        )
        if script == "automatic_compaction":
            await self._emit(
                session_id,
                SessionInfoUpdate(
                    session_update="session_info_update",
                    title="Compacted session",
                    field_meta={
                        "hermes": {
                            "sessionProvenance": {
                                "reason": "compression",
                                "state": "compacted",
                            }
                        }
                    },
                ),
            )
        await self._emit(
            session_id,
            AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id=message_id,
                content=_text(
                    "SEND NOW SUCCESSOR ANSWER"
                    if script == "send_now_successor"
                    else "typed answer"
                ),
            ),
        )
        if os.environ.get("ACP_TEST_MALFORMED_DURING_PROMPT") == "1":
            await self._emit_malformed_update(session_id=session_id, partial=False)
        if self._prompt_release_socket is not None:
            await asyncio.to_thread(self._prompt_release_socket.recvfrom, 1)
        if script == "automatic_compaction":
            self._install_live_compacted_history(session_id, "Inspectable automatic summary")
        if script in {"explicit_compaction", "explicit_compaction_marker_free"}:
            await self._emit(
                session_id,
                SessionInfoUpdate(
                    session_update="session_info_update",
                    title="Compacted session",
                    field_meta={
                        "scripted": {
                            "compaction": {
                                "trigger": "explicit",
                                "state": "compacted",
                            }
                        }
                    },
                ),
            )
            if script == "explicit_compaction_marker_free":
                self._install_live_marker_free_compacted_history(session_id)
            else:
                self._install_live_compacted_history(session_id, "Inspectable explicit summary")
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        del kwargs
        _diagnostic(f"cancel {session_id}")
        self._cancel_events[session_id].set()

    def _configuration_options(self, session_id: str) -> list[SessionConfigOptionSelect]:
        configuration = self._session_configuration[session_id]
        model = SessionConfigOptionSelect(
            type="select",
            id="scripted-model",
            name="Model",
            category="model",
            current_value=configuration["model"],
            options=[
                SessionConfigSelectOption(
                    value="probe-model", name="Probe model", description="Primary scripted model"
                ),
                SessionConfigSelectOption(
                    value="probe-alt", name="Probe alternate", description=None
                ),
            ],
        )
        if (
            os.environ.get("ACP_TEST_CONFIG_DISAPPEAR_REASONING") == "1"
            and configuration["model"] == "probe-alt"
        ):
            return [model]
        efforts = (
            (
                SessionConfigSelectOption(value="probe-low", name="Low"),
                SessionConfigSelectOption(value="probe-high", name="High"),
            )
            if configuration["model"] == "probe-model"
            else (SessionConfigSelectOption(value="probe-low", name="Low"),)
        )
        return [
            model,
            SessionConfigOptionSelect(
                type="select",
                id="scripted-reasoning",
                name="Reasoning",
                category="thought_level",
                current_value=configuration["reasoning"],
                options=list(efforts),
            ),
        ]

    @staticmethod
    def _audit_configuration(record: dict[str, Any]) -> None:
        audit_path = os.environ.get("ACP_TEST_CONFIG_AUDIT_PATH")
        if audit_path is None:
            return
        payload = {"processId": os.getpid(), **record}
        with Path(audit_path).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, separators=(",", ":")) + "\n")

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method != "emit_malformed_update":
            return {"handled": False}
        await self._emit_malformed_update(
            session_id=str(params["sessionId"]),
            partial=bool(params.get("partial", False)),
        )
        return {"handled": True}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        del method, params

    async def _emit(self, session_id: str, update: Any) -> None:
        self._session_updates[session_id].append(update)
        self._durable_session_updates[session_id] = copy.deepcopy(self._session_updates[session_id])
        self._persist_durable_store()
        await self.client.session_update(session_id=session_id, update=update)

    async def _emit_post_fork_metadata(
        self,
        source_session_id: str,
        fork_session_id: str,
        metadata_complete: asyncio.Event,
    ) -> None:
        try:
            if os.environ.get("ACP_TEST_POST_FORK_CANDIDATE_UPDATE") == "1":
                await self._emit_ephemeral_available_commands(
                    fork_session_id, "candidate-after-fork"
                )
            if os.environ.get("ACP_TEST_POST_FORK_SOURCE_UPDATE") == "1":
                await self._emit_ephemeral_available_commands(
                    source_session_id, "source-after-fork"
                )
        finally:
            metadata_complete.set()

    async def _emit_ephemeral_available_commands(self, session_id: str, command_name: str) -> None:
        await self.client.session_update(
            session_id=session_id,
            update=AvailableCommandsUpdate(
                session_update="available_commands_update",
                available_commands=[
                    AvailableCommand(
                        name=command_name,
                        description=f"Deterministic {command_name} metadata",
                    )
                ],
            ),
        )

    def _install_live_compacted_history(self, session_id: str, summary: str) -> None:
        text = f"{HERMES_SUMMARY_PREFIX}\n\n{summary}\n\n{HERMES_SUMMARY_END_MARKER}"
        self._session_updates[session_id] = [
            AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id="durable-before-compaction-summary",
                content=_text("DURABLE BEFORE COMPACTION SUMMARY"),
            ),
            UserMessageChunk(
                session_update="user_message_chunk",
                message_id="durable-compaction-summary",
                content=_text(text),
            ),
            AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id="durable-after-compaction-summary",
                content=_text("DURABLE AFTER COMPACTION SUMMARY"),
            ),
        ]

    def _install_live_marker_free_compacted_history(self, session_id: str) -> None:
        self._session_updates[session_id] = [
            AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id="durable-before-compaction-summary",
                content=_text("DURABLE BEFORE COMPACTION SUMMARY"),
            ),
            AgentMessageChunk(
                session_update="agent_message_chunk",
                message_id="durable-after-compaction-summary",
                content=_text("DURABLE AFTER COMPACTION SUMMARY"),
            ),
        ]

    def _load_durable_store(self) -> dict[str, list[Any]]:
        path = self._durable_store_path
        if path is None or not path.exists():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        sessions = raw.get("sessions")
        if not isinstance(sessions, dict):
            raise RuntimeError("scripted ACP durable store is invalid")
        loaded: dict[str, list[Any]] = {}
        for session_id, notifications in sessions.items():
            if not isinstance(session_id, str) or not isinstance(notifications, list):
                raise RuntimeError("scripted ACP durable store is invalid")
            loaded[session_id] = [
                SessionNotification.model_validate(item, strict=True).update
                for item in notifications
            ]
        return loaded

    def _persist_durable_store(self) -> None:
        path = self._durable_store_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "sessions": {
                session_id: [
                    SessionNotification(
                        session_id=session_id,
                        update=update,
                    ).model_dump(mode="json", by_alias=True, exclude_none=True)
                    for update in updates
                ]
                for session_id, updates in self._durable_session_updates.items()
            }
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)

    async def _emit_malformed_update(self, *, session_id: str, partial: bool) -> None:
        """Isolated test-only escape hatch for traffic the SDK refuses to construct."""

        update = (
            {"sessionUpdate": "agent_thought_chunk"}
            if partial
            else {
                "sessionUpdate": "future_update",
                "content": {"type": "text", "text": "must never become assistant text"},
            }
        )
        raw_connection = cast(Any, self.client)._conn
        await raw_connection.send_notification(
            "session/update",
            {"sessionId": session_id, "update": update},
        )


async def _main() -> None:
    from acp.agent import connection as agent_connection

    agent = ScriptedAcpAgent()
    standard_router_builder = agent_connection.build_agent_router

    def build_scripted_router(routed_agent: Any, *, use_unstable_protocol: bool = False) -> Any:
        standard_router = standard_router_builder(
            routed_agent, use_unstable_protocol=use_unstable_protocol
        )

        async def route(method: str, params: object, is_notification: bool) -> object:
            if method != "session/set_model":
                return await standard_router(method, params, is_notification)
            if (
                is_notification
                or not isinstance(params, dict)
                or set(params) != {"sessionId", "modelId"}
                or not isinstance(params["sessionId"], str)
                or not isinstance(params["modelId"], str)
            ):
                raise ValueError("invalid legacy model request")
            return await agent.set_legacy_session_model(params["sessionId"], params["modelId"])

        return route

    agent_connection.build_agent_router = build_scripted_router
    try:
        await acp.run_agent(agent, use_unstable_protocol=True)
    finally:
        agent_connection.build_agent_router = standard_router_builder


if __name__ == "__main__":
    asyncio.run(_main())

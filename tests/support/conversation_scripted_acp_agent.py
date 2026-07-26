"""A real ACP agent that does exactly what it is told, so a test can be the agent.

This is a standalone process. It speaks the Agent Client Protocol over its stdio the way
hermes does — `initialize`, `session/new`, `session/load`, `session/prompt`,
`session/cancel`, the retired `session/set_model`, and the `session/request_permission`
call back the other way — and it holds a turn open until a test tells it how that turn
ends. Everything the conversation system does to it goes over a genuine wire into a
genuine process, which is the point: it is the backend side's own account, not a mirror of
what the system thinks it did.

A test drives it over a unix socket whose path arrives in the environment. One JSON object
per line, one JSON object back. The socket is deliberately not the ACP wire: the ACP wire
belongs to the conversation system, and a test that shared it could not tell the two
apart.

**How this agent refuses to be written to.** A conversation system that writes over ACP
cannot be told "no" by a prompt response — a prompt is answered when the turn ends, long
after the write. So the way this agent stops being writable is the way a real agent's wire
stops being writable: it closes the end of the pipe it reads from. The process stays
alive, keeps answering the test over its socket, and keeps its side of any turn that was
already running; only writes to it fail, at the write boundary, for as long as the agent
lives. That is the failure the conformance suite asks for — the same boundary every time,
with the child still there — reached the only way ACP leaves open.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from acp import CLIENT_METHODS, PROTOCOL_VERSION
from acp.agent.router import build_agent_router
from acp.connection import Connection
from acp.exceptions import RequestError
from acp.interfaces import Agent
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    ContentToolCallContent,
    Cost,
    ImageContentBlock,
    Implementation,
    InitializeResponse,
    LoadSessionResponse,
    NewSessionResponse,
    PermissionOption,
    PlanEntry,
    PromptResponse,
    RequestPermissionRequest,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SessionInfoUpdate,
    SessionNotification,
    SetSessionConfigOptionResponse,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    Usage,
    UsageUpdate,
)
from acp.stdio import stdio_streams
from acp.utils import serialize_params

from planner.conversation.backends.hermes_acp import AcpChildLaunch

CONTROL_SOCKET_ENVIRONMENT_NAME = "PANELS_SCRIPTED_ACP_AGENT_CONTROL_SOCKET"
ARMS_ENVIRONMENT_NAME = "PANELS_SCRIPTED_ACP_AGENT_ARMS"

# Arms that have to be in place before the conversation system has ever spoken to this
# agent, so they arrive in the environment rather than over the socket.
ARM_REJECT_NEW_SESSION = "reject_new_session"
ARM_REJECT_LOAD_SESSION = "reject_load_session"
ARM_BREAK_WIRE_ON_SESSION = "break_wire_on_session"

LEGACY_SET_SESSION_MODEL_METHOD = "session/set_model"
STEER_COMMAND_PREFIX = "/steer "
REASONING_EFFORT_CONFIGURATION_OPTION_ID = "thought_level"
REASONING_EFFORT_CONFIGURATION_CATEGORY = "thought_level"

# The names of this process's environment a test reads back, to prove the values a
# conversation was started with reached the agent rather than being dropped on the way.
REPORTED_ENVIRONMENT_NAMES = ("HERMES_HOME", "HERMES_PYTHON_SRC_ROOT", "HERMES_YOLO_MODE")

CHILD_LINE_LIMIT_BYTES = 50 * 1024 * 1024

STANDARD_INPUT_FILE_DESCRIPTOR = 0


def _block_report(block: Any, steered: bool) -> dict[str, Any]:
    """One prompt block as a plain dictionary, for the account this agent reports.

    Only what a test asks about is reported. The steer command word comes off the first
    run of words the way it does above, so a steered message reads as the message that
    was steered.
    """
    if isinstance(block, TextContentBlock):
        text = block.text
        return {
            "piece": "text",
            "text": text.removeprefix(STEER_COMMAND_PREFIX) if steered else text,
        }
    if isinstance(block, ImageContentBlock):
        return {"piece": "image", "media_type": block.mime_type, "data": block.data}
    return {"piece": "unknown"}


def _scripted_usage(usage: Any) -> Usage | None:
    """The token counts a test wants on the answer that ends a turn, if it wants any.

    A count left out of the command is left off the answer, because a turn hermes counted
    nothing for and a turn it counted zero for are two different things.
    """
    if not isinstance(usage, dict):
        return None
    return Usage(
        input_tokens=int(usage["input_tokens"]),
        output_tokens=int(usage["output_tokens"]),
        total_tokens=int(usage["total_tokens"]),
        thought_tokens=_optional_count(usage.get("thought_tokens")),
        cached_read_tokens=_optional_count(usage.get("cached_read_tokens")),
    )


def _optional_count(value: Any) -> int | None:
    return None if value is None else int(value)


@dataclass(slots=True)
class _PromptWrite:
    """One prompt that actually arrived here, as this agent read it.

    ``turn_open_on_arrival`` is what catches a conversation system writing into a gap. A
    real agent that is handed a prompt while it still has a turn open does not start it —
    it holds it for later, and answers about having held it. Recording the condition is
    how a test can say that never happened rather than hoping.
    """

    text: str
    # Every block of the prompt, as this agent read them off the wire, in order. The text
    # above is the words out of them; this is what actually arrived, which is what a test
    # about a message reaching the backend whole has to be able to ask.
    blocks: tuple[dict[str, Any], ...]
    sender_label: str | None
    delivery_mode: str | None
    turn_open_on_arrival: bool = False


@dataclass(slots=True)
class _RaisedAsk:
    tool_call_id: str
    answer: str | None = None


@dataclass(slots=True)
class _Account:
    """Everything this agent can say about what was done to it."""

    prompt_writes: list[_PromptWrite] = field(default_factory=list)
    cancellations: int = 0
    asks: list[_RaisedAsk] = field(default_factory=list)
    session_id: str | None = None
    sessions_created: int = 0
    sessions_loaded: int = 0
    loaded_from: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    agent_messages_emitted: int = 0


class ScriptedAcpAgent:
    """An ACP agent whose every move is a test's decision."""

    def __init__(self, arms: frozenset[str]) -> None:
        self._arms = set(arms)
        self.account = _Account()
        self._connection: Connection | None = None
        self._read_transport: asyncio.ReadTransport | None = None
        self._open_turn: asyncio.Future[PromptResponse] | None = None
        self._break_wire_at_next_answer = False
        self._seconds_to_take_over_a_cancel = 0.0
        self._wire_broken = False
        self.shutting_down = asyncio.Event()
        self._background: set[asyncio.Task[Any]] = set()

    def bind(self, connection: Connection, read_transport: asyncio.ReadTransport) -> None:
        self._connection = connection
        self._read_transport = read_transport

    # --- the ACP agent side --------------------------------------------------------------

    async def initialize(self, **kwargs: Any) -> InitializeResponse:
        del kwargs
        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(load_session=True),
            agent_info=Implementation(
                name="panels-scripted-acp-agent", title="Scripted ACP agent", version="1"
            ),
        )

    async def new_session(self, cwd: str, **kwargs: Any) -> NewSessionResponse:
        del cwd, kwargs
        if ARM_REJECT_NEW_SESSION in self._arms:
            raise RequestError.internal_error({"details": "this session will not be created"})
        self.account.sessions_created += 1
        self.account.session_id = f"scripted-session-{self.account.sessions_created}"
        if ARM_BREAK_WIRE_ON_SESSION in self._arms:
            self._break_wire_at_next_answer = True
        return NewSessionResponse(
            session_id=self.account.session_id, config_options=[self._reasoning_effort_option()]
        )

    async def load_session(self, cwd: str, session_id: str, **kwargs: Any) -> LoadSessionResponse:
        del cwd, kwargs
        if ARM_REJECT_LOAD_SESSION in self._arms:
            raise RequestError.internal_error({"details": "this session will not load"})
        self.account.sessions_loaded += 1
        self.account.loaded_from = session_id
        self.account.session_id = session_id
        if ARM_BREAK_WIRE_ON_SESSION in self._arms:
            self._break_wire_at_next_answer = True
        return LoadSessionResponse(config_options=[self._reasoning_effort_option()])

    async def set_config_option(
        self, config_id: str, session_id: str, value: Any, **kwargs: Any
    ) -> SetSessionConfigOptionResponse:
        del session_id, kwargs
        if config_id == REASONING_EFFORT_CONFIGURATION_OPTION_ID:
            self.account.reasoning_effort = str(value)
        return SetSessionConfigOptionResponse(config_options=[self._reasoning_effort_option()])

    async def set_legacy_session_model(self, params: Any) -> dict[str, Any]:
        """Hermes' retired ``session/set_model``, which is how its model really changes."""
        payload = params if isinstance(params, dict) else {}
        self.account.model = str(payload.get("modelId"))
        return {}

    async def prompt(
        self, session_id: str, prompt: Sequence[Any], **kwargs: Any
    ) -> PromptResponse:
        """Take a prompt down the instant it arrives, then hold the turn open.

        The write is recorded before this coroutine ever suspends, so it is on the account
        in the order it came off the wire — which is what lets a test ask what has reached
        the agent without waiting for anything.
        """
        del session_id
        text = "".join(block.text for block in prompt if isinstance(block, TextContentBlock))
        steered = text.startswith(STEER_COMMAND_PREFIX)
        blocks = tuple(_block_report(block, steered) for block in prompt)
        self.account.prompt_writes.append(
            _PromptWrite(
                text=text.removeprefix(STEER_COMMAND_PREFIX) if steered else text,
                blocks=blocks,
                sender_label=kwargs.get("sender_label"),
                delivery_mode=kwargs.get("delivery_mode"),
                turn_open_on_arrival=not steered and self._open_turn is not None,
            )
        )
        if steered:
            # A steer joins the turn that is running; it is not a turn of its own, so its
            # own request answers straight away.
            return PromptResponse(stop_reason="end_turn")
        open_turn: asyncio.Future[PromptResponse] = asyncio.get_running_loop().create_future()
        self._open_turn = open_turn
        return await open_turn

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        """Take the cancel, and take as long over it as a test has asked for.

        A real agent does not finish the instant it is told to stop, and an agent that did
        would hide the one thing worth testing here: what happens to a prompt written into
        the moment between the stop and the stopping.
        """
        del session_id, kwargs
        self.account.cancellations += 1
        if self._seconds_to_take_over_a_cancel <= 0:
            self._finish_open_turn(PromptResponse(stop_reason="cancelled"))
            return
        self._run_in_background(self._finish_the_turn_slowly())

    async def _finish_the_turn_slowly(self) -> None:
        await asyncio.sleep(self._seconds_to_take_over_a_cancel)
        self._finish_open_turn(PromptResponse(stop_reason="cancelled"))

    def _reasoning_effort_option(self) -> SessionConfigOptionSelect:
        return SessionConfigOptionSelect(
            type="select",
            id=REASONING_EFFORT_CONFIGURATION_OPTION_ID,
            name="Thinking",
            category=REASONING_EFFORT_CONFIGURATION_CATEGORY,
            current_value=self.account.reasoning_effort or "",
            options=[
                SessionConfigSelectOption(value=value, name=value)
                for value in ("low", "medium", "high")
            ],
        )

    # --- what a test tells it to do -------------------------------------------------------

    async def handle_control(self, command: dict[str, Any]) -> dict[str, Any]:
        name = command.get("command")
        match name:
            case "report":
                return self._report()
            case "complete_turn":
                self._finish_open_turn(
                    PromptResponse(
                        stop_reason="end_turn", usage=_scripted_usage(command.get("usage"))
                    )
                )
                return {"ok": True}
            case "fail_turn":
                self._fail_open_turn(str(command.get("reason", "the agent fell over")))
                return {"ok": True}
            case "raise_permission_ask":
                return {"ok": True, "ask_index": self._raise_permission_ask()}
            case "emit_agent_message":
                await self._emit_agent_message(
                    str(command["text"]), str(command.get("message_id", "scripted-message"))
                )
                return {"ok": True}
            case "emit_agent_image":
                await self._emit_agent_image(
                    str(command["data"]),
                    str(command["media_type"]),
                    str(command.get("message_id", "scripted-message")),
                )
                return {"ok": True}
            case "emit_plan":
                await self._emit_plan(command)

            case "emit_thought":
                await self._emit_thought(str(command["text"]))
                return {"ok": True}
            case "emit_tool_call":
                await self._emit_tool_call(command)
                return {"ok": True}
            case "emit_tool_call_progress":
                await self._emit_tool_call_progress(command)
                return {"ok": True}
            case "emit_tool_call_finished":
                await self._emit_tool_call_finished(command)
                return {"ok": True}
            case "emit_usage_update":
                await self._emit_usage_update(command)
                return {"ok": True}
            case "emit_session_info_update":
                await self._emit_session_info_update(bool(command.get("compacted", False)))
                return {"ok": True}
            case "take_this_long_over_a_cancel":
                self._seconds_to_take_over_a_cancel = float(command["seconds"])
                return {"ok": True}
            case "break_wire":
                self._break_the_wire()
                return {"ok": True}
            case "break_wire_at_next_answer":
                self._break_wire_at_next_answer = True
                return {"ok": True}
            case "shutdown":
                self.shutting_down.set()
                return {"ok": True}
            case _:
                return {"ok": False, "unknown_command": name}

    def _report(self) -> dict[str, Any]:
        account = self.account
        return {
            "prompt_writes": [
                {
                    "text": write.text,
                    "blocks": [dict(block) for block in write.blocks],
                    "sender_label": write.sender_label,
                    "delivery_mode": write.delivery_mode,
                    "turn_open_on_arrival": write.turn_open_on_arrival,
                }
                for write in account.prompt_writes
            ],
            "cancellations": account.cancellations,
            "asks": [
                {"tool_call_id": ask.tool_call_id, "answer": ask.answer} for ask in account.asks
            ],
            "answered": sum(1 for ask in account.asks if ask.answer is not None),
            "session_id": account.session_id,
            "sessions_created": account.sessions_created,
            "sessions_loaded": account.sessions_loaded,
            "loaded_from": account.loaded_from,
            "model": account.model,
            "reasoning_effort": account.reasoning_effort,
            "working_directory": os.getcwd(),
            "environment": {
                name: os.environ[name]
                for name in REPORTED_ENVIRONMENT_NAMES
                if name in os.environ
            },
            "identity_environment": {
                name: value
                for name, value in os.environ.items()
                if name.startswith("PANELS_IDENTITY_")
            },
        }

    def _finish_open_turn(self, response: PromptResponse) -> None:
        open_turn = self._open_turn
        self._open_turn = None
        if open_turn is not None and not open_turn.done():
            open_turn.set_result(response)

    def _fail_open_turn(self, reason: str) -> None:
        open_turn = self._open_turn
        self._open_turn = None
        if open_turn is not None and not open_turn.done():
            open_turn.set_exception(RequestError.internal_error({"details": reason}))

    def _raise_permission_ask(self) -> int:
        index = len(self.account.asks)
        ask = _RaisedAsk(tool_call_id=f"scripted-tool-call-{index + 1}")
        self.account.asks.append(ask)
        self._run_in_background(self._ask_for_permission(ask))
        return index

    async def _ask_for_permission(self, ask: _RaisedAsk) -> None:
        request = RequestPermissionRequest(
            session_id=self.account.session_id or "",
            tool_call=ToolCallUpdate(
                tool_call_id=ask.tool_call_id,
                title=f"Do the thing called {ask.tool_call_id}",
                content=[
                    ContentToolCallContent(
                        type="content", content=TextContentBlock(type="text", text="the details")
                    )
                ],
            ),
            options=[
                PermissionOption(option_id="allow-once", name="Allow once", kind="allow_once"),
                PermissionOption(
                    option_id="allow-always", name="Always allow", kind="allow_always"
                ),
                PermissionOption(option_id="reject-once", name="Decline", kind="reject_once"),
            ],
        )
        response = await self._request(
            CLIENT_METHODS["session_request_permission"], serialize_params(request)
        )
        outcome = response.get("outcome") if isinstance(response, dict) else None
        if isinstance(outcome, dict) and outcome.get("outcome") == "selected":
            ask.answer = str(outcome.get("optionId"))

    async def _emit_agent_message(self, text: str, message_id: str) -> None:
        self.account.agent_messages_emitted += 1
        await self._notify_session_update(
            AgentMessageChunk(
                session_update="agent_message_chunk",
                content=TextContentBlock(type="text", text=text),
                message_id=message_id,
            )
        )

    async def _emit_agent_image(self, data: str, media_type: str, message_id: str) -> None:
        """A picture inside the agent's message, which ACP carries as its bytes.

        The same message id as the words around it, so this is one message with a picture
        in the middle of it rather than three messages in a row.
        """
        await self._notify_session_update(
            AgentMessageChunk(
                session_update="agent_message_chunk",
                content=ImageContentBlock(type="image", data=data, mime_type=media_type),
                message_id=message_id,
            )
        )

    async def _emit_plan(self, command: dict[str, Any]) -> None:
        """A plan the way ACP words one: content, a status, and a priority nobody reads."""
        await self._notify_session_update(
            AgentPlanUpdate(
                session_update="plan",
                entries=[
                    PlanEntry(
                        content=str(entry["text"]),
                        status=entry.get("status", "pending"),
                        priority=entry.get("priority", "medium"),
                    )
                    for entry in command["entries"]
                ],
            )
        )

    async def _emit_thought(self, text: str) -> None:
        await self._notify_session_update(
            AgentThoughtChunk(
                session_update="agent_thought_chunk",
                content=TextContentBlock(type="text", text=text),
            )
        )

    async def _emit_tool_call(self, command: dict[str, Any]) -> None:
        await self._notify_session_update(
            ToolCallStart(
                session_update="tool_call",
                tool_call_id=str(command.get("tool_call_id", "scripted-tool-call")),
                title=str(command.get("title", "A scripted tool call")),
                kind=command.get("tool_kind", "execute"),
                status="in_progress",
                content=[
                    ContentToolCallContent(
                        type="content",
                        content=TextContentBlock(type="text", text=str(command.get("detail", ""))),
                    )
                ],
            )
        )

    async def _emit_tool_call_progress(self, command: dict[str, Any]) -> None:
        """A tool call that has started saying how it is getting on, without finishing."""
        await self._notify_session_update(
            ToolCallProgress(
                session_update="tool_call_update",
                tool_call_id=str(command.get("tool_call_id", "scripted-tool-call")),
                status="in_progress",
                content=[
                    ContentToolCallContent(
                        type="content",
                        content=TextContentBlock(type="text", text=str(command.get("detail", ""))),
                    )
                ],
            )
        )

    async def _emit_tool_call_finished(self, command: dict[str, Any]) -> None:
        await self._notify_session_update(
            ToolCallProgress(
                session_update="tool_call_update",
                tool_call_id=str(command.get("tool_call_id", "scripted-tool-call")),
                status=command.get("status", "completed"),
                content=[
                    ContentToolCallContent(
                        type="content",
                        content=TextContentBlock(type="text", text=str(command.get("detail", ""))),
                    )
                ],
            )
        )

    async def _emit_usage_update(self, command: dict[str, Any]) -> None:
        """Hermes' context indicator: what the session carries, in how big a window, and
        what it has cost when hermes puts a figure on it."""
        stated_cost = command.get("cost")
        await self._notify_session_update(
            UsageUpdate(
                session_update="usage_update",
                used=int(command["used"]),
                size=int(command["size"]),
                cost=(
                    None
                    if stated_cost is None
                    else Cost(
                        amount=float(stated_cost["amount"]),
                        currency=str(stated_cost["currency"]),
                    )
                ),
            )
        )

    async def _emit_session_info_update(self, compacted: bool) -> None:
        """The update hermes sends when it has changed something about the session itself.

        A compaction is one of those things, and the only place it is said is hermes' own
        ``_meta``: the ACP session id never moves, so what hermes reports is that its
        internal session was replaced and that compression is why. An update about anything
        else — a title it has just written — carries the same metadata without a reason.
        """
        provenance: dict[str, Any] = {
            "acpSessionId": self.account.session_id or "",
            "currentHermesSessionId": "hermes-session-2",
            "rootHermesSessionId": "hermes-session-1",
            "parentHermesSessionId": "hermes-session-1",
            "sessionKind": "continuation" if compacted else "root",
            "compressionDepth": 1 if compacted else 0,
        }
        if compacted:
            provenance["previousHermesSessionId"] = "hermes-session-1"
            provenance["reason"] = "compression"
            provenance["creatorKind"] = "compression"
        await self._notify_session_update(
            SessionInfoUpdate(
                session_update="session_info_update",
                title="A scripted session",
                updated_at="2026-01-01T00:00:00+00:00",
                field_meta={"hermes": {"sessionProvenance": provenance}},
            )
        )

    async def _notify_session_update(self, update: Any) -> None:
        notification = SessionNotification(
            session_id=self.account.session_id or "", update=update
        )
        connection = self._require_connection()
        await connection.send_notification(
            CLIENT_METHODS["session_update"], serialize_params(notification)
        )

    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        return await self._require_connection().send_request(method, params)

    def _require_connection(self) -> Connection:
        connection = self._connection
        if connection is None:
            raise RuntimeError("the scripted agent has no connection yet")
        return connection

    def _run_in_background(self, work: Any) -> None:
        task = asyncio.ensure_future(work)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # --- the wire -------------------------------------------------------------------------

    def break_the_wire_if_armed(self) -> None:
        """Break the wire between answering a request and the answer going out.

        The order matters, and it is the whole reason this is not left until a moment
        later. The answer still reaches the conversation system over stdout, so whatever
        that request established — a session, a model — really is established; what has
        gone by the time the system writes again is the way to write. A wire broken after
        the answer had already been read would be a race, and a test would sometimes watch
        the write it was told would fail succeed instead.
        """
        if not self._break_wire_at_next_answer:
            return
        self._break_wire_at_next_answer = False
        self._break_the_wire()

    def _break_the_wire(self) -> None:
        """Close the end of the pipe this agent reads from, and stay alive without it.

        Everything already sent stands and everything already open stays open; there is
        simply no longer a way to write to this agent. It goes on answering the test.

        The pipe is closed here and now rather than left to the transport's own shutdown,
        which happens a turn of the event loop later — long enough for one more write to
        get through, which would make the failure this arms for come and go.
        """
        self._wire_broken = True
        transport = self._read_transport
        if transport is not None:
            transport.close()
        # The descriptor itself, not the file object: Python's standard streams are opened
        # with ``closefd=False``, so closing ``sys.stdin`` would leave the pipe's read end
        # open and every write to this agent would go on quietly succeeding.
        with contextlib.suppress(OSError):
            os.close(STANDARD_INPUT_FILE_DESCRIPTOR)

    @property
    def wire_broken(self) -> bool:
        return self._wire_broken


SCRIPTED_ACP_AGENT_SCRIPT = Path(__file__).resolve()


def scripted_acp_agent_launch(
    *, control_socket_path: str, arms: Iterable[str] = ()
) -> AcpChildLaunch:
    """Point the real hermes adapter at this agent instead of at hermes."""
    return AcpChildLaunch(
        argv=(sys.executable, str(SCRIPTED_ACP_AGENT_SCRIPT)),
        environment_overrides=(
            (CONTROL_SOCKET_ENVIRONMENT_NAME, control_socket_path),
            (ARMS_ENVIRONMENT_NAME, ",".join(sorted(arms))),
        ),
    )


class ScriptedAcpAgentControl:
    """The test's end of the agent's control socket. One command, one answer.

    A fresh connection per command, because a test asks rarely and a live connection to a
    child that may be armed to die is one more thing to get wrong. When nothing is
    listening — no child was ever spawned, or the one that was has gone — every command
    answers ``None`` rather than raising: a conversation whose backend never came up has
    an empty account, and that is a fact a test is allowed to ask for.
    """

    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path

    async def send(self, command: dict[str, Any]) -> dict[str, Any] | None:
        try:
            reader, writer = await asyncio.open_unix_connection(self.socket_path)
        except (OSError, ValueError):
            return None
        try:
            writer.write((json.dumps(command) + "\n").encode("utf-8"))
            await writer.drain()
            line = await reader.readline()
        except OSError:
            return None
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        if not line:
            return None
        answer = json.loads(line)
        return answer if isinstance(answer, dict) else None


async def _serve_control_socket(agent: ScriptedAcpAgent, socket_path: str) -> None:
    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                try:
                    command = json.loads(line)
                except ValueError:
                    answer: dict[str, Any] = {"ok": False, "unreadable": True}
                else:
                    answer = await agent.handle_control(command)
                writer.write((json.dumps(answer) + "\n").encode("utf-8"))
                await writer.drain()
        finally:
            writer.close()

    # A child that was killed leaves its socket file behind, and the next child for the
    # same conversation binds the same path.
    with contextlib.suppress(FileNotFoundError):
        os.unlink(socket_path)
    server = await asyncio.start_unix_server(serve, path=socket_path)
    async with server:
        await agent.shutting_down.wait()


def _build_handler(agent: ScriptedAcpAgent) -> Any:
    """Route ACP's own methods to the agent, plus the one the protocol has retired."""
    router = build_agent_router(cast(Agent, agent))

    async def handle(method: str, params: Any, is_notification: bool) -> Any:
        if method == LEGACY_SET_SESSION_MODEL_METHOD and not is_notification:
            answer = await agent.set_legacy_session_model(params)
        else:
            answer = await router(method, params, is_notification)
        agent.break_the_wire_if_armed()
        return answer

    return handle


async def main() -> None:
    socket_path = os.environ[CONTROL_SOCKET_ENVIRONMENT_NAME]
    arms = frozenset(
        arm for arm in os.environ.get(ARMS_ENVIRONMENT_NAME, "").split(",") if arm
    )
    agent = ScriptedAcpAgent(arms)
    reader, writer = await stdio_streams(limit=CHILD_LINE_LIMIT_BYTES)
    # The transport is what actually holds the pipe open, and closing it is how this agent
    # stops being writable without stopping.
    read_transport = reader._transport  # type: ignore[attr-defined]
    connection = Connection(_build_handler(agent), writer, reader, listening=False)
    agent.bind(connection, read_transport)

    async def listen() -> None:
        try:
            await connection.main_loop()
        finally:
            # A wire this agent broke on purpose is not a reason to stop; a wire that ended
            # because the conversation system went away is.
            if not agent.wire_broken:
                agent.shutting_down.set()

    listening = asyncio.create_task(listen())
    try:
        await _serve_control_socket(agent, socket_path)
    finally:
        listening.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await listening


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)

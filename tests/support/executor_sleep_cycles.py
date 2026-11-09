"""The sleep-cycle harness: no Mac event is lost or duplicated across freezes and wakes.

Risk: a sleep (the Mac process frozen, its connections dead) loses or duplicates what the
Mac's agents emitted, runs a command twice, leaves a command or an item stuck, raises a
stall alarm for a sleeping Mac, or, when the Mac process or the VPS dies mid-way, drops
the replayed events, applies a resent batch twice, or recovers an interrupted turn more or
less than once.
Gap: the unit and integration tests prove each wait point once, in process. Nothing else
runs the real Mac half as its own process group, freezes it with SIGSTOP and judges the
whole record afterwards.
Layer: three kinds of process, all started and stopped here.
- The VPS half runs in the harness process: the real VpsExecutor and conversation core
  over a temporary SQLite database, the real stall checker, uvicorn behind the isolated
  TLS proxy (tests/support/executor_https.py). Its blackhole stops answering with every
  socket left open, as a sleep does.
- The test executor is a separate process group (``python -m
  tests.support.executor_sleep_cycles executor``) running the real ``MacService`` with
  ``executor_id=test-executor``.
- Its agents are child processes in that group (``... agent``). Each emits numbered
  events (tool calls, asks, messages, turn ends) and writes every event to its own
  durable log before sending it, then logs the executor's acknowledgement, which comes
  only after the event is committed to the Mac outbox.
What arrived is judged twice: in the stored conversation record, and at the link, where
every effect the inbox applies is counted in its own transaction (harness_link_applied).
It never touches a live server, the live database or the live executor, and signals only
the process group it started, by its recorded PGID.

Known gaps:
- Linux CLOCK_MONOTONIC keeps running under SIGSTOP, unlike a macOS sleep, so every Mac
  timer and backoff has already expired when the group wakes.
- A VPS restart is a crash inside one process: its tasks are cancelled without waiting
  and its conversation core can no longer write, but the cleanup the cancellations run
  (finally blocks, worker threads already inside a transaction) still finishes.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import queue
import random
import re
import shutil
import signal
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
from collections import Counter, deque
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import asdict, dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, cast

import aiohttp
from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

from planner.conversation.backends.contracts import (
    AgentMessageCompleted,
    AgentMessageDelta,
    BackendChild,
    BackendChildFactory,
    BackendEventSink,
    BackendEventValue,
    BackendPermissionAsk,
    BackendPromptAccepted,
    BackendSteerAccepted,
    BackendSteerOutcome,
    BackendSteerRefused,
    PermissionAskRaised,
    ToolCallFinished,
    ToolCallStarted,
    TurnEnded,
    TurnToken,
)
from planner.conversation.contracts import (
    AnswerFate,
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
    PromptDeliveryFate,
    PromptDeliveryMode,
    PromptDeliveryRefusalReason,
    PromptDeliveryRefused,
    ResolvedConversationStart,
    WorkspacePathPolicy,
)
from planner.conversation.events import (
    ConversationTurnEnding,
    PermissionAskOption,
    ToolCallStatus,
    UserInputAnswer,
)
from planner.conversation.message_content import (
    MessageContent,
    message_content_text,
    text_message_content,
)
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.clock import RealClock
from planner.core.contracts import (
    CHIEF_PRINCIPAL,
    OWNER_PRINCIPAL,
    ErrorCode,
    PlannerError,
    Principal,
    PrincipalKind,
)
from planner.core.db import connect, create_schema
from planner.execution_identity import ExecutorIdentity
from planner.executor.clock import now_ms
from planner.executor.diagnostics import ConnectionPhase, connection_diagnostics
from planner.executor.protocol import ChildCommand, ItemBatch, ItemBatchReply
from planner.executor.vps_transport import VpsExecutor
from planner.stalls.checker import StallChecker
from planner.stalls.contracts import TrackedWaitKind
from planner.stalls.registry import WAITS

EXECUTOR_ID = "test-executor"
REPO_ROOT = Path(__file__).resolve().parents[2]
# The deploy-interruption recovery prompt (deploy_interruptions/coordinator.py).
RECOVERY_TEXT = "Inspect the interrupted work and continue the current authorized step."
# Every event an agent emits that the VPS stores carries one of these.
EVENT_TOKEN = re.compile(r"evt\[(?P<conversation>[a-z0-9]+):(?P<seq>\d+)\]")
# Every message the harness sends carries one of these.
MESSAGE_MARKER = re.compile(r"msg\[(?P<marker>[a-z0-9.]+)\]")
_DIRECTIVE = re.compile(r"\b(?P<name>steps|interval|ask)=(?P<value>[0-9.]+)")
# The kinds the VPS stores in the conversation record. Agent prose is not stored there
# (the core drops AgentMessageCompleted by design), so messages travel the same ordered
# outbox and must be acknowledged, but only these are counted in the record.
_STORED_KINDS = frozenset({"tool_started", "tool_finished", "ask"})
_TOKEN_EVENT_KINDS = (
    "tool_call_started",
    "tool_call_finished",
    "permission_asked",
)
_CHILD_OPERATIONS = frozenset(
    {"start", "write_prompt", "steer", "cancel_running_turn", "answer_permission_ask"}
)

_LOG = logging.getLogger(__name__)


# --- shared ------------------------------------------------------------------------------


class DurableLog:
    """Append-only JSON lines, each fsynced before write() returns."""

    def __init__(self, path: Path) -> None:
        self._fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)

    def write(self, record: dict[str, object]) -> None:
        os.write(self._fd, (json.dumps(record, sort_keys=True) + "\n").encode())
        os.fsync(self._fd)

    def close(self) -> None:
        os.close(self._fd)


def read_records(path: Path) -> list[dict[str, Any]]:
    """Every whole record; a line torn by a kill is skipped."""
    if not path.exists():
        return []
    records = []
    for line in path.read_bytes().splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _die_with_parent(parent: int) -> None:
    """A process left behind by a dead parent would stay frozen forever: the kernel
    kills this one when its parent goes, even while it is stopped."""
    if sys.platform == "linux":
        import ctypes

        ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0)  # PR_SET_PDEATHSIG
    if os.getppid() != parent:
        os._exit(1)


# --- the agent (a child process of the test executor) -------------------------------------


def run_agent(log_path: Path, conversation_id: str, executor_pid: int) -> int:
    """One agent process. It reads requests as JSON lines on stdin and writes events and
    replies on stdout. It sends one event at a time and waits for its acknowledgement."""
    records = read_records(log_path)
    next_seq = 1 + max((int(r["seq"]) for r in records if r.get("t") == "emit"), default=0)
    life = 1 + sum(1 for r in records if r.get("t") == "life")
    log = DurableLog(log_path)
    log.write({"t": "life", "life": life, "pid": os.getpid(), "executor": executor_pid})
    inbox: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def read_stdin() -> None:
        for line in sys.stdin:
            inbox.put(json.loads(line))
        inbox.put(None)

    threading.Thread(target=read_stdin, daemon=True).start()

    def send(message: dict[str, object]) -> None:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()

    plan: deque[dict[str, Any]] = deque()
    turn: int | None = None
    ending = False
    waiting_ack: int | None = None
    waiting_answer: str | None = None
    next_at = 0.0

    def emit(kind: str, **fields: object) -> None:
        nonlocal next_seq, waiting_ack
        seq, next_seq = next_seq, next_seq + 1
        token = f"evt[{conversation_id}:{seq}]"
        log.write(
            {
                "t": "emit",
                "seq": seq,
                "kind": kind,
                "turn": turn,
                "life": life,
                "visible": kind in _STORED_KINDS,
            }
        )
        send({"event": kind, "seq": seq, "turn": turn, "token": token, **fields})
        waiting_ack = seq

    def plan_turn(number: int, text: str) -> None:
        directives = {m["name"]: float(m["value"]) for m in _DIRECTIVE.finditer(text)}
        steps = int(directives.get("steps", 2))
        interval = directives.get("interval", 0.05)
        ask_at = int(directives.get("ask", 0))
        plan.clear()
        for step in range(1, steps + 1):
            tool = f"{conversation_id}-l{life}-t{number}-s{step}"
            plan.append({"kind": "tool_started", "tool": tool})
            if step == ask_at:
                plan.append({"kind": "ask", "ask_id": f"ask-{conversation_id}-l{life}-t{number}"})
            plan.append({"kind": "tool_finished", "tool": tool})
            plan.append({"kind": "delta", "text": f"step {step}"})
            plan.append(
                {"kind": "message", "text": f"turn {number} step {step}", "delay": interval}
            )
        plan.append({"kind": "message", "text": f"turn {number} done"})
        plan.append({"kind": "turn_ended", "ending": "completed"})

    while True:
        now = time.monotonic()
        if waiting_ack is None and waiting_answer is None and plan and now >= next_at:
            step = plan.popleft()
            kind = step["kind"]
            if kind == "ask":
                emit("ask", ask_id=step["ask_id"])
                waiting_answer = step["ask_id"]
            elif kind == "turn_ended":
                ending = True
                emit("turn_ended", ending=step["ending"])
            else:
                emit(kind, **{k: v for k, v in step.items() if k not in ("kind", "delay")})
            next_at = now + float(step.get("delay", 0.0))
            continue
        timeout = (
            None
            if waiting_ack is not None or waiting_answer is not None or not plan
            else max(0.0, next_at - now)
        )
        try:
            message = inbox.get(timeout=timeout)
        except queue.Empty:
            continue
        if message is None:
            return 0
        op = message["op"]
        if op == "ack":
            log.write({"t": "ack", "seq": message["seq"]})
            if message["seq"] == waiting_ack:
                waiting_ack = None
            if ending and waiting_ack is None and not plan:
                log.write({"t": "turn_end", "turn": turn, "life": life})
                turn, ending = None, False
            continue
        reply: dict[str, object] = {"reply": message["id"]}
        text = str(message.get("text", ""))
        markers = [m["marker"] for m in MESSAGE_MARKER.finditer(text)]
        if op == "prompt":
            if turn is not None:
                log.write({"t": "anomaly", "detail": "a prompt while a turn is active"})
            turn, ending = int(message["turn"]), False
            log.write(
                {
                    "t": "prompt",
                    "turn": turn,
                    "life": life,
                    "markers": markers,
                    "recovery": RECOVERY_TEXT in text,
                }
            )
            plan_turn(turn, text)
        elif op == "steer":
            accepted = turn is not None and not ending and int(message["turn"]) == turn
            log.write(
                {"t": "steer", "turn": message["turn"], "markers": markers, "accepted": accepted}
            )
            if accepted:
                plan.appendleft({"kind": "message", "text": "steer seen " + " ".join(markers)})
            reply["accepted"] = accepted
        elif op == "answer":
            matched = message["ask_id"] == waiting_answer
            log.write({"t": "answer", "ask_id": message["ask_id"], "matched": matched})
            if matched:
                waiting_answer = None
        elif op == "cancel":
            log.write({"t": "cancel", "turn": turn})
            waiting_answer = None
            if turn is not None and not ending:
                plan.clear()
                plan.append({"kind": "turn_ended", "ending": "interrupted"})
        elif op == "stop":
            log.write({"t": "stop", "life": life})
            send(reply)
            return 0
        send(reply)


# --- the executor side of an agent -------------------------------------------------------


class CycleAgent:
    """The BackendChild the test executor's runner drives. It owns one agent process."""

    def __init__(self, conversation_id: str, sink: BackendEventSink, logs: Path) -> None:
        self.conversation_id = conversation_id
        self.sink = sink
        self.logs = logs
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._replies: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 0

    def stop_outside_lock(self) -> bool:
        return False

    async def start(
        self, resolved_start: ResolvedConversationStart, *, vendor_session_cursor: str | None
    ) -> None:
        del resolved_start, vendor_session_cursor
        self._process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "tests.support.executor_sleep_cycles",
            "agent",
            "--log",
            str(self.logs / f"{self.conversation_id}.jsonl"),
            "--conversation",
            self.conversation_id,
            "--parent",
            str(os.getpid()),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            cwd=REPO_ROOT,
        )
        self._reader = asyncio.create_task(self._read())

    async def write_prompt(
        self, turn_token: TurnToken, content: MessageContent, **arguments: object
    ) -> BackendPromptAccepted:
        del arguments
        await self._request(
            "prompt", turn=turn_token.turn_number, text=message_content_text(content)
        )
        return BackendPromptAccepted(True)

    async def steer(
        self, turn_token: TurnToken, content: MessageContent, **arguments: object
    ) -> BackendSteerOutcome:
        del arguments
        reply = await self._request(
            "steer", turn=turn_token.turn_number, text=message_content_text(content)
        )
        if reply.get("accepted"):
            return BackendSteerAccepted()
        return BackendSteerRefused(PromptDeliveryRefusalReason.no_running_turn_to_steer_into)

    async def cancel_running_turn(self) -> None:
        await self._request("cancel")

    async def answer_permission_ask(
        self, ask_id: str, option_id: str, **arguments: object
    ) -> AnswerFate:
        del arguments
        await self._request("answer", ask_id=ask_id, option=option_id)
        return AnswerFate.applied

    async def answer_user_input(
        self, request_id: str, answers: tuple[UserInputAnswer, ...], **arguments: object
    ) -> AnswerFate:
        del request_id, answers, arguments
        return AnswerFate.refused

    async def stop(self) -> None:
        process = self._process
        if process is None:
            return
        with contextlib.suppress(Exception):
            async with asyncio.timeout(5):
                await self._request("stop")
                await process.wait()
        if process.returncode is None:
            process.kill()  # this agent's own process, by its handle
            await process.wait()
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)

    def _send(self, message: dict[str, object]) -> None:
        assert self._process is not None and self._process.stdin is not None
        self._process.stdin.write((json.dumps(message) + "\n").encode())

    async def _request(self, op: str, **fields: object) -> dict[str, Any]:
        self._next_id += 1
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._replies[self._next_id] = future
        self._send({"op": op, "id": self._next_id, **fields})
        return await future

    async def _read(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            while line := await self._process.stdout.readline():
                message = json.loads(line)
                if "reply" in message:
                    future = self._replies.pop(int(message["reply"]), None)
                    if future is not None and not future.done():
                        future.set_result(message)
                    continue
                try:
                    await self.sink.report(
                        TurnToken(self.conversation_id, int(message["turn"])),
                        _agent_event(message),
                    )
                except Exception:
                    # Not acknowledged: the agent's log keeps it as emitted and unacked.
                    _LOG.exception("an agent event was not reported")
                    continue
                self._send({"op": "ack", "seq": message["seq"]})
        finally:
            for future in self._replies.values():
                if not future.done():
                    future.set_exception(ConnectionError("the agent process exited"))
            self._replies.clear()


def _agent_event(message: dict[str, Any]) -> BackendEventValue:
    token = str(message["token"])
    match message["event"]:
        case "tool_started":
            return ToolCallStarted(
                tool_call_id=str(message["tool"]), title="step", tool_kind="execute", detail=token
            )
        case "tool_finished":
            return ToolCallFinished(
                tool_call_id=str(message["tool"]),
                tool_call_status=ToolCallStatus.completed,
                detail=token,
            )
        case "delta":
            return AgentMessageDelta(text_delta=str(message["text"]))
        case "message":
            return AgentMessageCompleted(content=text_message_content(f"{message['text']} {token}"))
        case "ask":
            return PermissionAskRaised(
                ask=BackendPermissionAsk(
                    str(message["ask_id"]),
                    f"Allow the next step? {token}",
                    None,
                    (
                        PermissionAskOption("allow", "Allow", "allow_once"),
                        PermissionAskOption("deny", "Deny", "reject_once"),
                    ),
                )
            )
        case "turn_ended":
            return TurnEnded(ending=ConversationTurnEnding(message["ending"]))
    raise ValueError(f"unknown agent event {message['event']!r}")


def run_executor(
    root: Path, server_url: str, ca: Path, agent_logs: Path, commands_log: Path
) -> None:
    """The test executor: the real MacService, with scripted agents and model lists."""
    from tests.support.executor_catalog import ScriptedBackendSnapshots

    from planner.executor.configuration import ExecutorPaths
    from planner.executor.mac_service import MacService

    logging.basicConfig(
        level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    logging.getLogger("planner.executor").setLevel(logging.INFO)

    async def main() -> None:
        service = MacService(
            paths=ExecutorPaths(root),
            revision="a" * 40,
            executor_id=EXECUTOR_ID,
            panels_server_url=server_url,
            ssl_context=ssl.create_default_context(cafile=str(ca)),
        )
        snapshots = ScriptedBackendSnapshots()
        snapshots.model = "scripted"
        service.catalog.snapshots = snapshots

        def factory(resolved: ResolvedConversationStart) -> BackendChildFactory:
            def create(**arguments: object) -> BackendChild:
                start = cast(ResolvedConversationStart, arguments["resolved_start"])
                sink = cast(BackendEventSink, arguments["event_sink"])
                return cast(BackendChild, CycleAgent(start.conversation_id, sink, agent_logs))

            del resolved
            return cast(BackendChildFactory, create)

        service.runner.factory = factory
        # Every provider invocation is logged before the agent is called, so a command
        # that runs twice, in this process or across a restart, is seen.
        invoked = DurableLog(commands_log)
        mark_invoking = service.runner._mark_invoking

        def logged_mark_invoking(command: ChildCommand) -> None:
            mark_invoking(command)
            invoked.write(
                {
                    "command_id": command.command_id,
                    "operation": command.operation.value,
                    "conversation_id": command.child.conversation_id,
                    "incarnation": service.identity.executor_incarnation,
                    "pid": os.getpid(),
                }
            )

        service.runner._mark_invoking = logged_mark_invoking  # type: ignore[method-assign]
        # The shipped diagnostics name only the exception; a failure that is not the
        # network's gets its traceback in this executor's log.
        report = connection_diagnostics.report

        def report_with_traceback(error: Exception, *, phase: ConnectionPhase) -> None:
            report(error, phase=phase)
            if not isinstance(error, (OSError, TimeoutError, aiohttp.ClientError)):
                _LOG.warning("%s failure", phase.value, exc_info=error)

        connection_diagnostics.report = report_with_traceback  # type: ignore[method-assign]
        await service.run()

    asyncio.run(main())


# --- the VPS side ------------------------------------------------------------------------


@dataclass(frozen=True)
class CycleSchedule:
    """What each cycle does. The defaults are the 100-cycle run (contract §8.3, §11.8,
    §11.10 row F)."""

    freeze_seconds: tuple[float, float] = (5.0, 60.0)
    dark_wake_every: int = 3
    dark_wake_seconds: tuple[float, float] = (2.0, 10.0)
    cut_every: int = 4
    mac_restart_every: int = 25
    # Every this-many-th Mac restart also restarts the VPS while that restart's drain waits.
    vps_restart_every: int = 2
    drain_limit_seconds: float = 120.0
    # A streaming turn outlasts the moment the group is frozen.
    stream_steps: int = 40
    stream_interval: float = 0.15
    # After the last cycle, longer than the Mac's longest request timeout (the 60 s poll),
    # so a late duplicate arrives before the final judge.
    final_quiet_seconds: float = 65.0


@dataclass
class CycleReport:
    cycle: int
    kinds: list[str]
    freeze_seconds: float
    dark_wake: dict[str, float] | None
    no_network_after_wake_seconds: float
    roles: dict[str, str]
    held_requests: str = ""
    kill_mode: str | None = None
    sends: list[dict[str, object]] = field(default_factory=list)
    presence_while_frozen: list[str] = field(default_factory=list)
    kill: dict[str, object] | None = None
    vps_restart_during_drain: bool | None = None
    held_at_vps_restart: list[str] = field(default_factory=list)
    drain_seconds: float | None = None
    counts: dict[str, object] = field(default_factory=dict)
    violations: list[str] = field(default_factory=list)
    started_at_ms: int = 0
    ended_at_ms: int = 0


class _DownSwitch:
    """While on, every executor request is answered 503, as the TLS front answers while
    the server behind it restarts."""

    def __init__(self, app: ASGIApp, *, state: dict[str, bool]) -> None:
        self.app = app
        self.state = state

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self.state.get("down"):
            await send(
                {
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": b"the VPS is restarting"})
            return
        await self.app(scope, receive, send)


# The harness's own table in the VPS database: one row per effect the inbox applied,
# written by the inbox's own transaction, so it holds exactly what committed, even when a
# crash cancels the call before it returns.
LINK_APPLIED_SCHEMA = (
    "CREATE TABLE harness_link_applied (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "item_key TEXT NOT NULL, conversation_id TEXT NOT NULL)"
)


@dataclass
class LinkLedger:
    """What reached the VPS over the link, kept in the harness across VPS restarts: every
    item submission received. What was applied is in harness_link_applied, keyed by the
    agent's event token (``evt:<conversation>:<seq>``), turn end
    (``end:<conversation>:<turn>``) or answer (``answer:<answer id>``)."""

    received: Counter[str] = field(default_factory=Counter)


def _event_key(turn: TurnToken | None, event: BackendEventValue) -> str | None:
    if isinstance(event, TurnEnded):
        return None if turn is None else f"end:{turn.conversation_id}:{turn.turn_number}"
    if isinstance(event, (ToolCallStarted, ToolCallFinished)):
        text = event.detail or ""
    elif isinstance(event, PermissionAskRaised):
        text = event.ask.title
    elif isinstance(event, AgentMessageCompleted):
        text = message_content_text(event.content)
    else:
        return None
    match = EVENT_TOKEN.search(text)
    return None if match is None else f"evt:{match['conversation']}:{match['seq']}"


def _counted(
    participant: Callable[..., Any], key: str | None, conversation_id: str
) -> Callable[..., Any]:
    """The inbox's commit participant, recording its effect in the same transaction when
    the item was taken (a replay or refusal is not)."""

    def counted(connection: sqlite3.Connection, *arguments: Any) -> Any:
        result = participant(connection, *arguments)
        if key is not None and getattr(result, "taken", False):
            connection.execute(
                "INSERT INTO harness_link_applied(item_key,conversation_id) VALUES (?,?)",
                (key, conversation_id),
            )
        return result

    return counted


class _CountingConversations:
    """The conversation core as the inbox sees it, with every commit counted."""

    def __init__(self, inner: SqliteProcessConversationSystem) -> None:
        self._inner = inner

    async def take_backend_event(
        self, turn: TurnToken | None, arguments: BackendEventValue, **options: Any
    ) -> Any:
        conversation_id = str(options.get("conversation_id") or "")
        options["in_commit"] = _counted(
            options["in_commit"], _event_key(turn, arguments), conversation_id
        )
        return await self._inner.take_backend_event(turn, arguments, **options)

    async def take_answer_application(
        self,
        turn: TurnToken,
        answer_id: str,
        provider_id: str,
        kind: str,
        in_commit: Callable[..., Any],
        **options: Any,
    ) -> Any:
        return await self._inner.take_answer_application(
            turn,
            answer_id,
            provider_id,
            kind,
            _counted(in_commit, f"answer:{answer_id}", turn.conversation_id),
            **options,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class CycleEndpoint(VpsExecutor):
    """The real VPS half, plus what the harness needs from the link.

    While ``replies_held`` is set, an item batch is applied and committed as usual but
    its reply waits: a Mac killed, or a VPS crashed, in that window has saved the items
    without the Mac ever seeing their receipts, so the replay is a true duplicate."""

    def __init__(self, db_path: str, ledger: LinkLedger) -> None:
        super().__init__(db_path, RealClock())
        self.ledger = ledger
        self.replies_held = False
        self.held_replies = 0
        self.held_conversations: set[str] = set()
        self._released = asyncio.Event()
        self._closed = False
        self._crash_cleanup: asyncio.Task[None] | None = None

    def bind_conversations(self, conversations: SqliteProcessConversationSystem) -> None:
        super().bind_conversations(conversations)
        self.inbox.conversations = cast(Any, _CountingConversations(conversations))

    def release_replies(self) -> None:
        self.replies_held = False
        self._released.set()

    async def accept_items(
        self, identity: ExecutorIdentity, conversation_id: str, batch: ItemBatch
    ) -> ItemBatchReply:
        if self._closed:
            raise PlannerError(ErrorCode.retry_later, "this VPS process has gone")
        self.ledger.received.update(item.envelope.submission_id for item in batch.items)
        reply = await super().accept_items(identity, conversation_id, batch)
        if self.replies_held and reply.receipts:
            self.held_replies += 1
            self.held_conversations.add(conversation_id)
            await self._released.wait()
        if self._closed:
            raise PlannerError(ErrorCode.retry_later, "this VPS process has gone")
        return reply

    def crash(self) -> None:
        """As near a process kill as one process allows: every task is cancelled and
        nothing waits for it. The cleanup the cancellations run finishes in the
        background; the old core is cut off from the database separately."""
        self._closed = True
        self._released.set()
        for task in (*self._owned, *self.runs._tasks):
            task.cancel()
        self._crash_cleanup = asyncio.ensure_future(super().close())

    async def close(self) -> None:
        if self._crash_cleanup is not None:
            await asyncio.gather(self._crash_cleanup, return_exceptions=True)
            return
        self._closed = True
        self._released.set()
        await super().close()


def _make_core(endpoint: VpsExecutor) -> SqliteProcessConversationSystem:
    system = SqliteProcessConversationSystem(
        store=ConversationStore(endpoint.db_path),
        backend_child_factories={key: endpoint.runs for key in ConversationBackendKey},
        message_files=ConversationMessageFiles(endpoint.db_path),
    )
    system.configure_machine_catalog(endpoint.catalog)
    endpoint.bind_conversations(system)
    endpoint.app = FastAPI()
    return system


class Violation(Exception):
    pass


async def _eventually(
    condition: Callable[[], Awaitable[bool]], detail: str, seconds: float
) -> None:
    deadline = time.monotonic() + seconds
    while not await condition():
        if time.monotonic() > deadline:
            raise Violation(detail)
        await asyncio.sleep(0.2)


def _query(db_path: str, sql: str, parameters: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(db_path, timeout=30)
    try:
        return [tuple(row) for row in connection.execute(sql, parameters)]
    finally:
        connection.close()


def _optional_query(path: Path, sql: str) -> list[tuple[Any, ...]]:
    if not path.exists():
        return []
    connection = sqlite3.connect(path, timeout=30)
    try:
        connection.execute("PRAGMA query_only=1")
        return [tuple(row) for row in connection.execute(sql)]
    finally:
        connection.close()


@dataclass(frozen=True)
class _Emitted:
    life: int
    stored: bool
    kind: str
    turn: int | None


@dataclass
class _Agents:
    """What every agent process of one conversation logged, across all its lives."""

    emitted: dict[int, _Emitted]
    acked: set[int]
    received: Counter[str]
    answers: Counter[str]
    recoveries: int
    lives: int
    executors: list[int]
    # The turn of the last prompt, unless its turn_ended was acknowledged (in the outbox).
    open_turn: int | None
    anomalies: list[str]

    @classmethod
    def read(cls, path: Path) -> _Agents:
        emitted: dict[int, _Emitted] = {}
        acked: set[int] = set()
        received: Counter[str] = Counter()
        answers: Counter[str] = Counter()
        recoveries = lives = 0
        executors: list[int] = []
        open_turn: int | None = None
        anomalies: list[str] = []
        for record in read_records(path):
            kind = record.get("t")
            if kind == "life":
                lives = int(record["life"])
                executors.append(int(record["executor"]))
                open_turn = None
            elif kind == "emit":
                turn = record.get("turn")
                emitted[int(record["seq"])] = _Emitted(
                    int(record["life"]),
                    bool(record["visible"]),
                    str(record["kind"]),
                    None if turn is None else int(turn),
                )
            elif kind == "ack":
                seq = int(record["seq"])
                acked.add(seq)
                ended = emitted.get(seq)
                if ended is not None and ended.kind == "turn_ended" and ended.turn == open_turn:
                    open_turn = None
            elif kind == "prompt":
                received.update(record["markers"])
                recoveries += bool(record["recovery"])
                open_turn = int(record["turn"])
            elif kind == "steer" and record["accepted"]:
                received.update(record["markers"])
            elif kind == "answer":
                answers[str(record["ask_id"])] += 1
                if not record["matched"]:
                    anomalies.append(f"answer for an ask not waiting: {record['ask_id']}")
            elif kind == "anomaly":
                anomalies.append(str(record["detail"]))
        return cls(
            emitted, acked, received, answers, recoveries, lives, executors, open_turn, anomalies
        )


class SleepCycles:
    """Boots the isolated world, runs the cycles, judges each one, and stops everything
    it started."""

    conversations = ("ca", "cb", "cc")
    roles = ("stream", "ask", "idle")

    def __init__(self, work: Path, out: Path, schedule: CycleSchedule, seed: int) -> None:
        self.work = work
        self.out = out
        self.schedule = schedule
        self.seed = seed
        self.db_path = str(work / "vps" / "panels.sqlite")
        self.mac_root = work / "mac"
        self.agent_logs = work / "agents"
        self.commands_log = work / "commands-run.jsonl"
        self.logs = work / "logs"
        self.tickets: dict[str, str] = {}
        self.endpoint: CycleEndpoint
        self.system: SqliteProcessConversationSystem
        self.checker: StallChecker | None = None
        self.old_systems: list[SqliteProcessConversationSystem] = []
        self.down = {"down": False}
        self.transport_app = FastAPI()
        self.proxy: Any = None
        self.process: subprocess.Popen[bytes] | None = None
        self.pgid: int | None = None
        self.executor_lives = 0
        self.killed_lives: dict[str, set[int]] = {c: set() for c in self.conversations}
        self.messages: dict[str, dict[str, object]] = {}
        self.saved_answers: dict[str, int] = {}
        self.sends: list[asyncio.Task[None]] = []
        self.reports: list[CycleReport] = []
        self.vps_restarts_during_drain = 0
        self.lost_with_vps_restart: set[str] = set()
        self.ledger = LinkLedger()
        self.old_endpoints: list[CycleEndpoint] = []
        # Outbox items still waiting when a Mac process was killed: they must arrive.
        self.required_after_kill: set[str] = set()
        # Messages that never reached an agent and whose record says so: marker to the
        # record's kind and detail.
        self.undelivered_in_record: dict[str, tuple[str, str]] = {}

    # --- the run -------------------------------------------------------------------------

    async def run(self, cycles: int) -> list[CycleReport]:
        from tests.support.executor_https import serve_executor_app

        for folder in (self.work / "vps", self.mac_root, self.agent_logs, self.logs, self.out):
            folder.mkdir(parents=True, exist_ok=True)
        self._create_database()
        self.endpoint = CycleEndpoint(self.db_path, self.ledger)
        self.system = _make_core(self.endpoint)
        await self.endpoint.start()
        self._build_transport_app()
        try:
            async with serve_executor_app(self.transport_app, self.work / "tls") as proxy:
                self.proxy = proxy
                try:
                    self._start_checker()
                    await self._launch_executor(previous=None)
                    await self._start_conversations()
                    for cycle in range(1, cycles + 1):
                        report = await self._cycle(cycle, last=cycle == cycles)
                        self.reports.append(report)
                        with (self.out / "cycles.jsonl").open("a") as handle:
                            handle.write(json.dumps(asdict(report), sort_keys=True) + "\n")
                        _LOG.warning(
                            "cycle %d %s drain=%.1fs violations=%d",
                            cycle,
                            ",".join(report.kinds) or "plain",
                            report.drain_seconds or -1,
                            len(report.violations),
                        )
                        if report.violations:
                            break
                finally:
                    await self._stop_everything()
        finally:
            self._keep_evidence()
        return self.reports

    def _create_database(self) -> None:
        from planner.projects.data import create_project
        from planner.tickets import data
        from planner.tickets.contracts import TITLE_MAX_CHARS, TicketLocale

        connection = connect(self.db_path)
        try:
            create_schema(connection)
            connection.execute(LINK_APPLIED_SCHEMA)
            project = create_project(connection, name="Sleep cycles", folder_path=self.work, now=1)
            for conversation_id in self.conversations:
                ticket = data.create_ticket(
                    connection,
                    title=f"Sleep cycle worker {conversation_id}",
                    worker_type="coding",
                    principal=OWNER_PRINCIPAL,
                    now=1,
                    title_max_chars=TITLE_MAX_CHARS,
                    locale=TicketLocale.macos,
                    project_id=project.id,
                    employee_backend="codex",
                    employee_launch_model="scripted",
                )
                self.tickets[conversation_id] = ticket.id
        finally:
            connection.close()

    def _build_transport_app(self) -> None:
        """As the server mounts the executor routes, plus the restart switch."""
        from planner.core.response_compression import (
            CompressExceptMarkedRoutes,
            uncompressed_route_patterns,
        )
        from planner.core.trusted_ingress import TrustedIngressConfig, TrustedIngressMiddleware
        from planner.executor.app_transport import build_executor_router

        app = self.transport_app
        app.state.executor_id = EXECUTOR_ID
        app.state.executor_endpoint = self.endpoint
        router = build_executor_router()
        app.include_router(router)
        app.add_middleware(
            TrustedIngressMiddleware,
            config=TrustedIngressConfig("tailscale", "owner@example.com", "https://localhost"),
        )
        app.add_middleware(
            CompressExceptMarkedRoutes,
            uncompressed_patterns=uncompressed_route_patterns(router.routes),
            minimum_size=1024,
            compresslevel=4,
        )
        app.add_middleware(_DownSwitch, state=self.down)

    def _start_checker(self) -> None:
        # The production limits; the checker only looks more often than every 15 s.
        from planner.stalls import configuration

        configuration.STALL_CHECK_INTERVAL_SECONDS = 1  # type: ignore[misc]
        endpoint = self.endpoint
        self.checker = StallChecker(self.db_path, WAITS, endpoint.stall_sources, now_ms)
        self.checker.start()

    async def _launch_executor(self, previous: str | None) -> None:
        self.executor_lives += 1
        home = self.work / "mac-home"
        (home / ".codex").mkdir(parents=True, exist_ok=True)
        environment = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith(("PLAN_", "PANELS_", "CODEX_", "CLAUDE_"))
        }
        environment.update(HOME=str(home), CODEX_HOME=str(home / ".codex"), PYTHONUNBUFFERED="1")
        log = (self.logs / f"executor-{self.executor_lives}.log").open("wb")
        try:
            self.process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "tests.support.executor_sleep_cycles",
                    "executor",
                    "--root",
                    str(self.mac_root),
                    "--server-url",
                    self.proxy.origin,
                    "--ca",
                    str(self.proxy.ca_path),
                    "--agent-logs",
                    str(self.agent_logs),
                    "--commands-log",
                    str(self.commands_log),
                    "--parent",
                    str(os.getpid()),
                ],
                cwd=REPO_ROOT,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        finally:
            log.close()
        # Its own session, so its PGID is its PID; its agents inherit it.
        self.pgid = self.process.pid

        async def leased() -> bool:
            self._require_executor()
            current = self.endpoint.presence.current_identity()
            return (
                current is not None
                and current.executor_incarnation != previous
                and self.endpoint.presence.is_present()
            )

        await _eventually(leased, "the test executor did not lease", 60)

    def _require_executor(self) -> None:
        if self.process is None or self.process.poll() is not None:
            code = None if self.process is None else self.process.returncode
            raise Violation(f"the test executor exited unexpectedly (code {code})")

    def _signal(self, number: signal.Signals) -> None:
        """Only the recorded group, and only while its leader is unreaped, so the PGID
        cannot belong to anything else."""
        self._require_executor()
        assert self.pgid is not None
        os.killpg(self.pgid, number)

    async def _kill_executor(self) -> None:
        self._signal(signal.SIGKILL)
        assert self.process is not None
        killed = self.process.pid
        await asyncio.to_thread(self.process.wait, 30)
        self.process = None
        # Its agents got the same SIGKILL; wait until this life's agent PIDs are gone.
        for conversation_id in self.conversations:
            for record in read_records(self.agent_logs / f"{conversation_id}.jsonl"):
                if record.get("t") == "life" and record.get("executor") == killed:
                    await self._wait_gone(int(record["pid"]))

    @staticmethod
    async def _wait_gone(pid: int) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)  # signal 0 delivers nothing; it only asks if pid exists
            except ProcessLookupError:
                return
            except PermissionError:
                return  # the PID was reused by someone else's process: ours is gone
            await asyncio.sleep(0.05)

    async def _start_conversations(self) -> None:
        from planner.tickets import data

        for conversation_id in self.conversations:
            ticket_id = self.tickets[conversation_id]
            await self.system.start_conversation(
                ConversationStartRequest(
                    conversation_id=conversation_id,
                    model="scripted",
                    backend_key=ConversationBackendKey.codex,
                    workspace_folder=self.work,
                    workspace_path_policy=WorkspacePathPolicy.absolute,
                    role_materials=ConversationRoleMaterials(
                        "A scripted sleep-cycle worker.",
                        (("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", ticket_id)),
                    ),
                )
            )
            with contextlib.closing(connect(self.db_path)) as connection:
                data.write_ticket_conversation_start(
                    connection,
                    ticket_id,
                    conversation_id=conversation_id,
                    backend="codex",
                    model="scripted",
                    reasoning_effort=None,
                    now=2,
                )
                # The worker step is out with this conversation, so an interrupted turn
                # is recoverable, as the readiness loop would leave it.
                connection.execute(
                    "UPDATE tickets SET stage='needs_implementation',"
                    "ceiling='needs_implementation',worker_step_claim='out',"
                    "worker_step_claim_revision=7 WHERE id=?",
                    (ticket_id,),
                )
            self._send(0, conversation_id, f"msg[c0.{conversation_id}.start] steps=2")
        await self._wait_settled(60)
        await self._await_sends()

    # --- one cycle -----------------------------------------------------------------------

    async def _cycle(self, cycle: int, *, last: bool) -> CycleReport:
        schedule = self.schedule
        rng = random.Random(self.seed * 100_003 + cycle)
        dark = cycle % schedule.dark_wake_every == 0
        cut = cycle % schedule.cut_every == 0
        mac_restart = cycle % schedule.mac_restart_every == 0
        vps_restart = (
            mac_restart and (cycle // schedule.mac_restart_every) % schedule.vps_restart_every == 0
        )
        # Requests held by the blackhole: on odd cycles they stay dead after the wake
        # until the Mac's own timeouts give up on them (a long poll on a socket that died
        # in sleep); on even ones they are aborted when the network returns.
        held_requests = "dropped" if mac_restart or cycle % 2 == 0 else "left_dead"
        kinds = [
            name
            for flag, name in (
                (dark, "dark_wake"),
                (cut, "cut"),
                (mac_restart, "mac_restart"),
                (vps_restart, "vps_restart"),
            )
            if flag
        ]
        freeze = round(rng.uniform(*schedule.freeze_seconds), 1)
        dark_wake = None
        if dark:
            seconds = round(rng.uniform(*schedule.dark_wake_seconds), 1)
            dark_wake = {"after": round(rng.uniform(0.3, 0.6) * freeze, 1), "seconds": seconds}
        no_network = 0.0 if mac_restart else round(rng.choice((0.0, 0.0, rng.uniform(0.5, 3))), 1)
        roles = {
            c: self.roles[(cycle + index) % len(self.roles)]
            for index, c in enumerate(self.conversations)
        }
        report = CycleReport(
            cycle,
            kinds,
            freeze,
            dark_wake,
            no_network,
            roles,
            held_requests=held_requests,
            started_at_ms=now_ms(),
        )
        marks = await asyncio.to_thread(self._event_marks)
        interruptions_before = await asyncio.to_thread(self._interruptions)
        received_before = sum(self.ledger.received.values())
        resent_before = self._resent()
        try:
            await self._run_cycle(cycle, report, rng)
            if last:
                # A late duplicate of the last cycle has time to arrive before the judge.
                await asyncio.sleep(schedule.final_quiet_seconds)
                await self._wait_settled(schedule.drain_limit_seconds)
                report.counts["final_quiet_seconds"] = schedule.final_quiet_seconds
        except Violation as error:
            report.violations.append(str(error))
        report.counts["items_received"] = sum(self.ledger.received.values()) - received_before
        report.counts["items_resent"] = self._resent() - resent_before
        if not report.violations:
            presence = self.endpoint.health().presence
            report.violations.extend(
                await asyncio.to_thread(
                    self._judge, cycle, report, marks, interruptions_before, presence
                )
            )
        report.ended_at_ms = now_ms()
        return report

    def _resent(self) -> int:
        return sum(n - 1 for n in self.ledger.received.values() if n > 1)

    async def _blackhole_off(self, report: CycleReport) -> None:
        await asyncio.to_thread(
            partial(self.proxy.blackhole, False, drop_held=report.held_requests == "dropped")
        )

    async def _run_cycle(self, cycle: int, report: CycleReport, rng: random.Random) -> None:
        schedule = self.schedule
        # Work in flight when the Mac goes to sleep.
        # Before a Mac restart the stream outlasts the kill, so a turn is caught mid-way.
        steps = schedule.stream_steps * (10 if "mac_restart" in report.kinds else 1)
        for conversation_id, role in report.roles.items():
            if role == "stream":
                base = await asyncio.to_thread(self._token_count, conversation_id)
                self._send(
                    cycle,
                    conversation_id,
                    f"msg[c{cycle}.{conversation_id}.work] steps={steps} "
                    f"interval={schedule.stream_interval}",
                )
                await _eventually(
                    partial(self._tokens_beyond, conversation_id, base + 4),
                    f"{conversation_id}'s streaming turn did not start",
                    30,
                )
            elif role == "ask":
                self._send(
                    cycle, conversation_id, f"msg[c{cycle}.{conversation_id}.work] steps=3 ask=2"
                )
                await _eventually(
                    partial(self._has_ask, conversation_id),
                    f"{conversation_id}'s permission ask did not arrive",
                    30,
                )
        await self._await_sends()

        # Sleep: the group is frozen and the network stops answering, sockets left open.
        self._signal(signal.SIGSTOP)
        await asyncio.to_thread(self.proxy.blackhole, True)
        frozen_at = time.monotonic()
        for conversation_id, role in report.roles.items():
            prefix = f"msg[c{cycle}.{conversation_id}"
            if role == "stream":
                self._send(cycle, conversation_id, f"{prefix}.steer]", PromptDeliveryMode.steer)
                self._send(cycle, conversation_id, f"{prefix}.next] steps=2")
            elif role == "ask":
                await self._answer(cycle, conversation_id, report)
                self._send(cycle, conversation_id, f"{prefix}.next] steps=2")
            else:
                self._send(cycle, conversation_id, f"{prefix}.wake] steps=3")
                self._send(cycle, conversation_id, f"{prefix}.steer]", PromptDeliveryMode.steer)
        if report.dark_wake is not None:
            await self._frozen_until(frozen_at + report.dark_wake["after"], report)
            self._signal(signal.SIGCONT)
            await self._blackhole_off(report)
            await asyncio.sleep(report.dark_wake["seconds"])
            self._signal(signal.SIGSTOP)
            await asyncio.to_thread(self.proxy.blackhole, True)
        await self._frozen_until(frozen_at + report.freeze_seconds, report)

        # Wake, without the wake hook.
        self._signal(signal.SIGCONT)
        if "mac_restart" in report.kinds:
            await self._restart_mac(report, vps_restart="vps_restart" in report.kinds)
        else:
            await asyncio.sleep(report.no_network_after_wake_seconds)
            await self._blackhole_off(report)
            if "cut" in report.kinds:
                await asyncio.sleep(rng.uniform(0.5, 2.0))
                await asyncio.to_thread(self.proxy.cut_connections)
        woke = time.monotonic()
        await self._wait_settled(schedule.drain_limit_seconds)
        report.drain_seconds = round(time.monotonic() - woke, 1)
        await self._await_sends()

    async def _frozen_until(self, until: float, report: CycleReport) -> None:
        while (left := until - time.monotonic()) > 0:
            presence = self.endpoint.health().presence
            if not report.presence_while_frozen or report.presence_while_frozen[-1] != presence:
                report.presence_while_frozen.append(presence)
            if await asyncio.to_thread(self._stall_findings):
                raise Violation("a stall finding appeared while the Mac was frozen")
            self._require_executor()
            await asyncio.sleep(min(1.0, left))

    async def _restart_mac(self, report: CycleReport, *, vps_restart: bool) -> None:
        """The Mac process dies (SIGKILL to the group) and is launched again with the same
        spool. Either it dies awake but still offline, with the agents' events piling up
        in the outbox, and the VPS then crashes while the replay drains; or it dies
        mid-upload, after the VPS saved a batch whose receipts it never saw."""
        if vps_restart:
            await asyncio.sleep(2.5)
            report.kill_mode = "offline"
        else:
            self.endpoint.replies_held = True
            await self._blackhole_off(report)

            async def saved_unacknowledged() -> bool:
                self._require_executor()
                return self.endpoint.held_replies > 0

            await _eventually(saved_unacknowledged, "no batch was saved before the kill", 60)
            report.kill_mode = "mid_upload"
        running = {c: await self.system.is_running(c) for c in self.conversations}
        await self._kill_executor()
        self.endpoint.release_replies()
        waiting = await asyncio.to_thread(
            _optional_query,
            self.mac_root / "spool" / "outbox.sqlite3",
            "SELECT conversation_id,CAST(payload_json AS TEXT) FROM items "
            "WHERE receipt_json IS NULL ORDER BY sequence",
        )
        keys = [k for _, payload in waiting if (k := _payload_key(str(payload))) is not None]
        self.required_after_kill.update(keys)
        lives = {}
        for conversation_id in self.conversations:
            agents = _Agents.read(self.agent_logs / f"{conversation_id}.jsonl")
            self.killed_lives[conversation_id].add(agents.lives)
            # A turn whose end is still waiting in the outbox ends when it is replayed.
            open_turn = agents.open_turn
            if f"end:{conversation_id}:{open_turn}" in keys:
                open_turn = None
            lives[conversation_id] = {
                "life": agents.lives,
                "active_turn": open_turn is not None,
                "unacked": sorted(set(agents.emitted) - agents.acked),
            }
        report.kill = {
            "vps_running": running,
            "agents": lives,
            "outbox_waiting": dict(Counter(str(c) for c, _ in waiting)),
            "outbox_waiting_keys": len(keys),
            "saved_without_receipt": self.endpoint.held_replies,
        }
        previous = self.endpoint.presence.current_identity()
        if vps_restart:
            self.endpoint.replies_held = True
            await self._blackhole_off(report)
        await self._launch_executor(None if previous is None else previous.executor_incarnation)
        if not vps_restart:
            return

        async def drain_pending() -> bool:
            """A drain waits on the held items, and every other conversation's drain has
            finished its retire and recovery send: the crash cuts short only the drain.
            (A crash in the middle of a recovery send is another case; see the notes.)"""
            self._require_executor()
            held = self.endpoint.held_conversations
            return (
                bool(held)
                and set(self.endpoint.runs._drains) <= held
                and not any(
                    wait.kind is TrackedWaitKind.mac_replacement_pending
                    for wait in WAITS.snapshot()
                )
                and bool(await asyncio.to_thread(self._unresolved_replaced_runs))
                and not await asyncio.to_thread(
                    _query,
                    self.db_path,
                    "SELECT 1 FROM deploy_interruption_deliveries WHERE status='intent'",
                )
            )

        await _eventually(drain_pending, "no Mac drain was pending to restart the VPS in", 60)
        report.vps_restart_during_drain = True
        await self._await_sends()
        await self._restart_vps(report)
        self.vps_restarts_during_drain += 1

    async def _restart_vps(self, report: CycleReport) -> None:
        """A crash and restart of the VPS process, then a new one built over the same
        database. The old one is cut off: its connections are cut, its tasks cancelled
        with nothing waiting for them, and its conversation core can no longer write.
        Saved batches whose replies were held lose only their receipts, so the Mac's
        resend to the new process is a true duplicate."""
        self.down["down"] = True
        await asyncio.to_thread(self.proxy.cut_connections)
        # Held messages live in the VPS process's memory and a restart loses them
        # (docs/conversations.md). That is the core's documented behaviour, not the
        # link's: each one held at this moment is recorded, and is excused only if no
        # mailbox row for it exists.
        held = [
            marker["marker"]
            for conversation_id in self.conversations
            for prompt in await self.system.held_prompts(conversation_id)
            for marker in MESSAGE_MARKER.finditer(message_content_text(prompt.content))
        ]
        report.held_at_vps_restart = held
        self.lost_with_vps_restart.update(held)
        if self.checker is not None:
            await self.checker.close()
        self.endpoint.crash()
        self.old_endpoints.append(self.endpoint)
        old = self.system

        async def crashed(*arguments: object, **options: object) -> object:
            raise RuntimeError("this VPS process has crashed")

        old._store._write = crashed  # type: ignore[method-assign,assignment]
        self.old_systems.append(old)
        # A process exit releases the lifecycle lease without a native stop.
        old._runtime_ownership.close()
        self.endpoint = CycleEndpoint(self.db_path, self.ledger)
        self.system = _make_core(self.endpoint)
        self.transport_app.state.executor_endpoint = self.endpoint
        await self.endpoint.start()
        self._start_checker()
        self.down["down"] = False
        # What the server's startup does next (core/server.py, recover_conversations):
        # recover the interrupted turns whose interruption was recorded.
        from planner.conversation.deploy_interruptions.coordinator import (
            run_automatic_recovery,
        )

        with self.system.workspace_admission.operation() as admitted:
            if admitted:
                await self.system.tell_agents_of_background_tasks_lost_at_restart()
                await run_automatic_recovery(self.system, self.system._store)

    # --- sends and answers ---------------------------------------------------------------

    def _send(
        self,
        cycle: int,
        conversation_id: str,
        text: str,
        mode: PromptDeliveryMode = PromptDeliveryMode.queue,
    ) -> None:
        marker = MESSAGE_MARKER.search(text)
        assert marker is not None
        record: dict[str, object] = {
            "marker": marker["marker"],
            "conversation": conversation_id,
            "cycle": cycle,
            "mode": mode.value,
            "fate": None,
        }
        self.messages[marker["marker"]] = record

        async def send() -> None:
            fate: PromptDeliveryFate = await self.system.send(
                conversation_id,
                text_message_content(text),
                sender_label="Sleep cycles",
                mode=mode,
                sender=CHIEF_PRINCIPAL,
                recipient=Principal(PrincipalKind.ticket, self.tickets[conversation_id]),
            )
            record["fate"] = type(fate).__name__
            if isinstance(fate, PromptDeliveryRefused):
                record["reason"] = fate.refusal_reason.value

        self.sends.append(asyncio.create_task(send()))

    async def _await_sends(self) -> None:
        sends, self.sends = self.sends, []
        if not sends:
            return
        done, pending = await asyncio.wait(sends, timeout=30)
        for task in pending:
            task.cancel()
        if pending:
            raise Violation(f"{len(pending)} sends did not return within 30 s")
        for task in done:
            if task.exception() is not None:
                raise Violation(f"a send failed: {task.exception()!r}")

    async def _answer(self, cycle: int, conversation_id: str, report: CycleReport) -> None:
        for ask_id in sorted(await self.system.pending_permission_ask_ids(conversation_id)):
            fate = await self.system.answer_permission_ask(conversation_id, ask_id, "allow")
            report.sends.append({"answer": ask_id, "fate": fate.value})
            if fate is not AnswerFate.saved:
                raise Violation(f"answer to {ask_id} was {fate.value}, not saved")
            self.saved_answers[ask_id.rsplit(":", 1)[-1]] = cycle

    async def _has_ask(self, conversation_id: str) -> bool:
        return bool(await self.system.pending_permission_ask_ids(conversation_id))

    async def _tokens_beyond(self, conversation_id: str, count: int) -> bool:
        return await asyncio.to_thread(self._token_count, conversation_id) >= count

    # --- settling ------------------------------------------------------------------------

    async def _wait_settled(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        stable = 0
        unsettled: list[str] = []
        while time.monotonic() < deadline:
            self._require_executor()
            unsettled = await self._unsettled()
            stable = 0 if unsettled else stable + 1
            if stable >= 3:
                return
            await asyncio.sleep(0.5)
        raise Violation(f"not settled after {seconds:.0f} s: {'; '.join(unsettled)}")

    async def _unsettled(self) -> list[str]:
        reasons = []
        if any(not task.done() for task in self.sends):
            reasons.append("a send has not returned")
        for conversation_id in self.conversations:
            if await self.system.is_running(conversation_id):
                reasons.append(f"{conversation_id} is running")
            if await self.system.held_prompts(conversation_id):
                reasons.append(f"{conversation_id} holds messages")
        presence = self.endpoint.health().presence
        if presence != "present":
            reasons.append(f"presence is {presence}")
        reasons.extend(await asyncio.to_thread(self._store_unsettled))
        return reasons

    def _store_unsettled(self) -> list[str]:
        reasons = []
        open_rows = _query(
            self.db_path,
            "SELECT operation,state,count(*) FROM executor_commands "
            "WHERE state IN ('waiting','delivered') GROUP BY operation,state",
        )
        if open_rows:
            reasons.append(f"mailbox rows open: {open_rows}")
        if self._unresolved_replaced_runs():
            reasons.append("a replaced process's run is not closed")
        waiting = _optional_query(
            self.mac_root / "spool" / "outbox.sqlite3",
            "SELECT count(*) FROM items WHERE receipt_json IS NULL",
        )
        if waiting and waiting[0][0]:
            reasons.append(f"{waiting[0][0]} Mac outbox items wait")
        unreported = _optional_query(
            self.mac_root / "spool" / "commands.sqlite3",
            "SELECT count(*) FROM commands WHERE NOT (phase='finished' AND result_reported=1 "
            "AND outputs_published=1)",
        )
        if unreported and unreported[0][0]:
            reasons.append(f"{unreported[0][0]} journal rows unreported")
        return reasons

    def _unresolved_replaced_runs(self) -> list[tuple[Any, ...]]:
        return _query(
            self.db_path,
            "SELECT r.conversation_id FROM mac_runs AS r "
            "LEFT JOIN mac_executor_presence AS p ON p.singleton=1 "
            "WHERE r.stop_state<>'closed' AND r.executor_incarnation IS NOT p.executor_incarnation",
        )

    def _stall_findings(self) -> list[tuple[Any, ...]]:
        return _query(self.db_path, "SELECT kind,conversation_id,detail FROM stall_findings")

    # --- judging -------------------------------------------------------------------------

    def _event_marks(self) -> dict[str, int]:
        rows = _query(
            self.db_path,
            "SELECT conversation_id,max(sequence) FROM conversation_events "
            "GROUP BY conversation_id",
        )
        return {str(c): int(s) for c, s in rows}

    def _token_count(self, conversation_id: str) -> int:
        return len(self._tokens(conversation_id))

    def _tokens(self, conversation_id: str, after: int = 0) -> list[int]:
        rows = _query(
            self.db_path,
            "SELECT payload FROM conversation_events WHERE conversation_id=? AND sequence>? "
            f"AND kind IN ({','.join('?' * len(_TOKEN_EVENT_KINDS))}) ORDER BY sequence",
            (conversation_id, after, *_TOKEN_EVENT_KINDS),
        )
        return [
            int(match["seq"])
            for (payload,) in rows
            for match in EVENT_TOKEN.finditer(str(payload))
            if match["conversation"] == conversation_id
        ]

    def _interruptions(self) -> Counter[str]:
        """Recorded interruptions of turns by a Mac restart, per conversation."""
        return Counter(
            str(c)
            for (c,) in _query(
                self.db_path,
                "SELECT source_conversation_id FROM deploy_interruptions "
                "WHERE deployment_id LIKE 'mac-process:%'",
            )
        )

    def _judge(
        self,
        cycle: int,
        report: CycleReport,
        marks: dict[str, int],
        interruptions_before: Counter[str],
        presence: str,
    ) -> list[str]:
        violations: list[str] = []
        restart = "mac_restart" in report.kinds
        findings = self._stall_findings()
        if findings:
            violations.append(f"stall findings: {findings}")
        if presence != "present":
            violations.append(f"presence is {presence}, not present")
        violations.extend(self._store_unsettled())
        invoked = read_records(self.commands_log)
        applied_rows = _query(
            self.db_path, "SELECT item_key,conversation_id FROM harness_link_applied ORDER BY id"
        )
        applied: Counter[str] = Counter(str(key) for key, _ in applied_rows)
        starts = Counter(
            str(r["conversation_id"]) for r in invoked if r.get("operation") == "start"
        )

        events_checked = recoveries_in_cycle = 0
        for conversation_id in self.conversations:
            agents = _Agents.read(self.agent_logs / f"{conversation_id}.jsonl")
            violations.extend(f"{conversation_id}: agent {a}" for a in agents.anomalies)
            killed = self.killed_lives[conversation_id]

            def required(seq: int, key: str, a: _Agents = agents) -> bool:
                """Acknowledged (in the outbox), or still waiting there at a kill."""
                return seq in a.acked or key in self.required_after_kill

            # 1a. The stored record: every required stored event once, in order.
            tokens = self._tokens(conversation_id)
            events_checked += len(tokens)
            duplicated = sorted(k for k, n in Counter(tokens).items() if n > 1)
            if duplicated:
                violations.append(f"{conversation_id}: events stored twice: {duplicated[:20]}")
            disorder = [(a, b) for a, b in zip(tokens, tokens[1:], strict=False) if b <= a]
            if disorder:
                violations.append(f"{conversation_id}: events out of order: {disorder[:10]}")
            stored_kinds = {seq for seq, e in agents.emitted.items() if e.stored}
            unknown = sorted(set(tokens) - stored_kinds)
            if unknown:
                violations.append(f"{conversation_id}: events no agent emitted: {unknown[:20]}")
            lost = sorted(
                seq
                for seq in stored_kinds - set(tokens)
                if required(seq, f"evt:{conversation_id}:{seq}")
                or agents.emitted[seq].life not in killed
            )
            if lost:
                violations.append(f"{conversation_id}: events missing from the record: {lost[:20]}")

            # 1b. The link: every required item, of every kind, applied exactly once.
            expected_keys = set()
            for seq, emitted in agents.emitted.items():
                if emitted.kind == "delta":
                    continue  # live text: lossy by design, never an item
                key = (
                    f"end:{conversation_id}:{emitted.turn}"
                    if emitted.kind == "turn_ended"
                    else f"evt:{conversation_id}:{seq}"
                )
                expected_keys.add(key)
                times = applied[key]
                if times > 1:
                    violations.append(f"{key} ({emitted.kind}) applied {times} times")
                elif not times and (required(seq, key) or emitted.life not in killed):
                    violations.append(f"{key} ({emitted.kind}) never applied at the VPS")
            strangers = sorted(
                k
                for k in applied
                if k.split(":")[1] == conversation_id and not k.startswith("answer:")
                if k not in expected_keys
            )
            if strangers:
                violations.append(
                    f"{conversation_id}: applied items no agent sent: {strangers[:10]}"
                )
            order = [
                int(key.split(":")[2])
                for key, conversation in applied_rows
                if conversation == conversation_id and str(key).startswith("evt:")
            ]
            if any(b <= a for a, b in zip(order, order[1:], strict=False)):
                violations.append(f"{conversation_id}: items applied out of order")

            # One agent process per conversation per executor process, one per start.
            if len(agents.executors) != len(set(agents.executors)):
                violations.append(
                    f"{conversation_id}: two agent processes under one executor process"
                )
            if agents.lives != starts[conversation_id]:
                violations.append(
                    f"{conversation_id}: {agents.lives} agent processes for "
                    f"{starts[conversation_id]} starts"
                )

            # Recovery: exactly one per turn a Mac restart interrupted, none otherwise. A
            # turn counts as interrupted by its interruption record: one whose retire a
            # VPS crash cut short is ended by the restart, not by a turn_ended row.
            mark = marks.get(conversation_id, 0)
            interrupted = (
                self._interruptions()[conversation_id] - interruptions_before[conversation_id]
            )
            ended_interrupted = _query(
                self.db_path,
                "SELECT count(*) FROM conversation_events WHERE conversation_id=? "
                "AND sequence>? AND kind='turn_ended' "
                "AND json_extract(payload,'$.ending')='interrupted'",
                (conversation_id, mark),
            )[0][0]
            if ended_interrupted > interrupted:
                violations.append(
                    f"{conversation_id}: {ended_interrupted} turns ended interrupted, "
                    f"{interrupted} interruptions recorded"
                )
            recoveries = _query(
                self.db_path,
                "SELECT count(*) FROM conversation_events WHERE conversation_id=? "
                "AND sequence>? AND kind='prompt' AND instr(payload,?)>0",
                (conversation_id, mark, RECOVERY_TEXT),
            )[0][0]
            all_recoveries = _query(
                self.db_path,
                "SELECT count(*) FROM conversation_events WHERE conversation_id=? "
                "AND kind='prompt' AND instr(payload,?)>0",
                (conversation_id, RECOVERY_TEXT),
            )[0][0]
            recoveries_in_cycle += recoveries
            report.counts[f"{conversation_id}_interrupted"] = interrupted
            report.counts[f"{conversation_id}_recoveries"] = recoveries
            if not restart and (interrupted or recoveries):
                violations.append(
                    f"{conversation_id}: {interrupted} interrupted turns and {recoveries} "
                    "recoveries in a cycle without a Mac restart"
                )
            if restart:
                active = bool(
                    report.kill
                    and cast(dict[str, Any], report.kill["agents"])[conversation_id]["active_turn"]
                )
                if interrupted > 1 or recoveries != interrupted or (active and interrupted != 1):
                    violations.append(
                        f"{conversation_id}: {interrupted} interrupted turns, {recoveries} "
                        f"recovery messages (agent turn active at the kill: {active})"
                    )
            if agents.recoveries != all_recoveries:
                violations.append(
                    f"{conversation_id}: {all_recoveries} recovery messages stored, "
                    f"{agents.recoveries} reached the agent"
                )
        report.counts["events_checked"] = events_checked
        twice = [k for k, n in applied.items() if k.startswith("answer:") and n > 1]
        if twice:
            violations.append(f"answer applications applied twice: {twice}")

        # The restart really happened as designed.
        if restart:
            kill = report.kill or {}
            agents_at_kill = cast(dict[str, Any], kill.get("agents", {}))
            if not any(a["active_turn"] for a in agents_at_kill.values()):
                violations.append("the Mac kill caught no turn mid-way")
            if report.kill_mode == "offline" and not kill.get("outbox_waiting"):
                violations.append("the Mac was killed with nothing waiting in its outbox")
            if report.kill_mode == "mid_upload" and not kill.get("saved_without_receipt"):
                violations.append("the Mac was killed with no batch saved unacknowledged")
            if not report.counts.get("items_resent"):
                violations.append("no item was sent twice across the restart")
            if not recoveries_in_cycle:
                violations.append("no interrupted turn was recovered")
            if "vps_restart" in report.kinds and not report.vps_restart_during_drain:
                violations.append("the VPS restart did not land during a Mac drain")

        # 2. Each command ran at the agent at most once, and every success exactly once.
        invocations = Counter(str(record["command_id"]) for record in invoked)
        twice = sorted(c for c, n in invocations.items() if n > 1)
        if twice:
            violations.append(f"commands run more than once: {twice[:10]}")
        rows = _query(
            self.db_path,
            "SELECT command_id,operation,state,outcome_json,command_json FROM executor_commands "
            "ORDER BY command_sequence",
        )
        known = {str(row[0]) for row in rows}
        strangers = sorted(set(invocations) - known)
        if strangers:
            violations.append(f"commands run that the mailbox never held: {strangers[:10]}")
        last_fate: dict[str, bool] = {}
        for command_id, operation, state, outcome_json, content in rows:
            if operation not in _CHILD_OPERATIONS:
                continue
            disposition = _disposition(outcome_json)
            runs = invocations[str(command_id)]
            if state == "finished" and disposition == "succeeded" and runs != 1:
                violations.append(f"{operation} {command_id} succeeded but ran {runs} times")
            if (disposition == "refused" or state in ("withdrawn", "expired")) and runs:
                violations.append(f"{operation} {command_id} was {state} {disposition} but ran")
            # The last row naming a message or an ask decides whether it may be lost.
            may_be_lost = (
                state in ("withdrawn", "expired")
                or disposition in ("refused", "uncertain")
                or '"steer_refused"' in str(outcome_json or "")
            )
            for found in MESSAGE_MARKER.finditer(str(content)):
                last_fate[found["marker"]] = may_be_lost
            if operation == "answer_permission_ask":
                for ask in self.saved_answers:
                    if ask in str(content):
                        last_fate[f"ask:{ask}"] = may_be_lost
        report.counts["commands"] = len(rows)
        report.counts["commands_run"] = sum(invocations.values())

        # 3. Each message and answer reached its agent once, unless the last mailbox row
        # for it says it may not have (withdrawn, refused, uncertain, or the agent's own
        # steer refusal); a message never in the mailbox only if a VPS restart lost it
        # while it was held.
        received: Counter[str] = Counter()
        answered: Counter[str] = Counter()
        for conversation_id in self.conversations:
            agents = _Agents.read(self.agent_logs / f"{conversation_id}.jsonl")
            received.update(agents.received)
            answered.update(agents.answers)
        report.sends.extend(
            {k: v for k, v in record.items() if k != "cycle"}
            for record in self.messages.values()
            if record["cycle"] == cycle
        )
        # The core's own record of a delivery that did not happen.
        undelivered = {
            found["marker"]: (str(kind), str(json.loads(payload).get("refusal_detail") or ""))
            for kind, payload in _query(
                self.db_path,
                "SELECT kind,payload FROM conversation_events WHERE kind IN "
                "('prompt_delivery_refused','prompt_delivery_uncertain','prompt_discarded')",
            )
            for found in MESSAGE_MARKER.finditer(str(payload))
        }
        excused = 0
        for marker, record in self.messages.items():
            count = received[marker]
            if record["fate"] == "PromptDeliveryRefused":
                violations.append(f"message {marker} was refused: {record.get('reason')}")
            elif count > 1:
                violations.append(f"message {marker} reached its agent {count} times")
            elif count == 0:
                if marker in last_fate:
                    if not last_fate[marker]:
                        violations.append(
                            f"message {marker} ({record['fate']}) never reached its agent, "
                            "though its last mailbox row succeeded"
                        )
                elif marker in undelivered and (
                    undelivered[marker][0] == "prompt_delivery_uncertain"
                    or "process that owned this child was replaced" in undelivered[marker][1]
                ):
                    # Recorded: unknown, or refused because its Mac process was replaced.
                    self.undelivered_in_record[marker] = undelivered[marker]
                elif marker not in self.lost_with_vps_restart:
                    violations.append(
                        f"message {marker} ({record['fate']}) never reached the mailbox"
                    )
                excused += record["cycle"] == cycle
        for ask in self.saved_answers:
            count = answered[ask]
            if count > 1:
                violations.append(f"the answer to {ask} reached its agent {count} times")
            elif count == 0 and not last_fate.get(f"ask:{ask}", False):
                violations.append(f"the answer to {ask} never reached its agent")
        report.counts["messages_received"] = sum(
            received[m] for m, r in self.messages.items() if r["cycle"] == cycle
        )
        report.counts["messages_excused"] = excused
        report.counts["answers_saved"] = sum(1 for c in self.saved_answers.values() if c == cycle)
        return violations

    # --- teardown ------------------------------------------------------------------------

    async def _stop_everything(self) -> None:
        """Every process this harness started stops here, on success and on failure."""
        try:
            if self.process is not None and self.process.poll() is None:
                assert self.pgid is not None
                os.killpg(self.pgid, signal.SIGKILL)
                await asyncio.to_thread(self.process.wait, 30)
            self.process = None
            for task in self.sends:
                task.cancel()
            await asyncio.gather(*self.sends, return_exceptions=True)
        finally:
            if self.checker is not None:
                await self.checker.close()
            for endpoint in (*self.old_endpoints, self.endpoint):
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(endpoint.close(), 10)
            # The Mac is gone; nothing may send it a stop on the way out.
            for system in (*self.old_systems, self.system):
                for state in system._conversations.values():
                    state.child = None
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(system.shutdown(), 10)

    def _keep_evidence(self) -> None:
        """Copy the logs and the databases into the output folder."""
        evidence = self.out / "evidence"
        with contextlib.suppress(FileNotFoundError):
            shutil.rmtree(evidence)
        evidence.mkdir(parents=True)
        for source in (self.logs, self.agent_logs):
            if source.exists():
                shutil.copytree(source, evidence / source.name)
        for source in (
            self.commands_log,
            Path(self.db_path),
            self.mac_root / "spool" / "outbox.sqlite3",
            self.mac_root / "spool" / "commands.sqlite3",
            self.work / "tls" / "proxy.log",
        ):
            for path in (source, *(Path(f"{source}{s}") for s in ("-wal", "-shm"))):
                if path.exists():
                    shutil.copy2(path, evidence / path.name)


def _payload_key(payload: str) -> str | None:
    """The ledger key of an outbox item's BackendEvent payload."""
    match = EVENT_TOKEN.search(payload)
    if match is not None:
        return f"evt:{match['conversation']}:{match['seq']}"
    value = json.loads(payload)
    turn = value.get("turn") if isinstance(value, dict) else None
    if value.get("method") == "turn_ended" and isinstance(turn, dict):
        return f"end:{turn['conversation_id']}:{turn['turn_number']}"
    return None


def _disposition(outcome_json: object) -> str | None:
    if not outcome_json:
        return None
    value = json.loads(str(outcome_json))
    outcome = value.get("child_outcome") if isinstance(value, dict) else None
    return str(outcome.get("disposition")) if isinstance(outcome, dict) else None


@contextlib.contextmanager
def _work_folder() -> Iterator[Path]:
    from tests.support.executor_https import short_transport_root

    with short_transport_root() as root:
        yield root


async def run_sleep_cycles(
    out: Path, cycles: int, *, schedule: CycleSchedule | None = None, seed: int = 1
) -> list[CycleReport]:
    """Run the cycles in a fresh temporary world, and write the per-cycle JSON lines and a
    summary to ``out``."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "cycles.jsonl").unlink(missing_ok=True)
    chosen = schedule or CycleSchedule()
    started = time.monotonic()
    reports: list[CycleReport] = []
    error: BaseException | None = None
    with _work_folder() as work:
        harness = SleepCycles(work, out, chosen, seed)
        try:
            reports = await harness.run(cycles)
        except BaseException as caught:
            error = caught
            reports = harness.reports
            raise
        finally:
            violations = [v for report in reports for v in report.violations]
            summary = {
                "cycles_requested": cycles,
                "cycles_run": len(reports),
                "ok": error is None and not violations and len(reports) == cycles,
                "violations": violations,
                "error": None if error is None else repr(error),
                "seconds": round(time.monotonic() - started, 1),
                "seed": seed,
                "schedule": asdict(chosen),
                "mac_restarts": sum("mac_restart" in r.kinds for r in reports),
                "vps_restarts_during_drain": harness.vps_restarts_during_drain,
                "dark_wakes": sum("dark_wake" in r.kinds for r in reports),
                "cuts": sum("cut" in r.kinds for r in reports),
                "presence_seen_while_frozen": sorted(
                    {p for r in reports for p in r.presence_while_frozen}
                ),
                "events_checked": max(
                    (int(cast(int, r.counts.get("events_checked", 0))) for r in reports),
                    default=0,
                ),
                "messages": len(harness.messages),
                "messages_held_at_a_vps_restart": sorted(harness.lost_with_vps_restart),
                "messages_undelivered_in_the_record": {
                    marker: list(fate) for marker, fate in harness.undelivered_in_record.items()
                },
                "items_received": sum(harness.ledger.received.values()),
                "items_resent": harness._resent(),
                "answers": len(harness.saved_answers),
            }
            (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.support.executor_sleep_cycles")
    commands = parser.add_subparsers(dest="command", required=True)
    executor = commands.add_parser("executor")
    executor.add_argument("--root", type=Path, required=True)
    executor.add_argument("--server-url", required=True)
    executor.add_argument("--ca", type=Path, required=True)
    executor.add_argument("--agent-logs", type=Path, required=True)
    executor.add_argument("--commands-log", type=Path, required=True)
    executor.add_argument("--parent", type=int, required=True)
    agent = commands.add_parser("agent")
    agent.add_argument("--log", type=Path, required=True)
    agent.add_argument("--conversation", required=True)
    agent.add_argument("--parent", type=int, required=True)
    args = parser.parse_args(argv)
    _die_with_parent(args.parent)
    if args.command == "agent":
        return run_agent(args.log, args.conversation, args.parent)
    try:
        run_executor(args.root, args.server_url, args.ca, args.agent_logs, args.commands_log)
    except SystemExit as exit_:
        return int(exit_.code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

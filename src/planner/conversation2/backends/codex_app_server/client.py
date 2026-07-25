"""The wire to one ``codex app-server`` child: framing, correlation, typed dispatch.

Codex's app-server speaks JSON-RPC's shapes over stdio without JSON-RPC's envelope: one
JSON object per line, and no ``jsonrpc`` field to route on. So messages are routed by what
they carry rather than by a tag — a ``method`` with an ``id`` is a request, a ``method``
without one is a notification, and an ``id`` with a ``result`` or an ``error`` is the answer
to something we asked. Ids are monotonic integers this side mints.

Three rules hold the rest of it together.

**Nothing that arrives is allowed to block the reader.** A server request that has to wait
for a person is handed to its handler, parked, and answered later with ``respond``; the
loop that read it has already moved on. A reader blocked on a human is a child whose
notifications stop arriving.

**A message we consume and cannot read is said out loud.** Unknown notifications are noise
from a protocol far larger than what this adapter uses, and they are dropped quietly. But a
notification whose method is one we act on and whose body will not decode means the protocol
moved under us, and a silently swallowed decode failure would make that look like an agent
that simply said nothing. It is logged as a warning naming the method.

**Standard error is drained, always.** A child whose stderr pipe fills stops running, so it
is read continuously into a small ring buffer, which is also what a failed turn's line quotes.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
from collections import deque
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from planner.conversation2.backends.codex_app_server import bindings_gen as bindings

LOGGER = logging.getLogger("planner.conversation2.backends.codex_app_server")

# The methods this adapter acts on. A notification outside this table is one of the many
# codex sends that Panels has no use for, and is dropped where it arrives.
CONSUMED_SERVER_NOTIFICATIONS: dict[str, type[BaseModel]] = {
    "turn/started": bindings.TurnStartedNotification,
    "turn/completed": bindings.TurnCompletedNotification,
    "item/agentMessage/delta": bindings.AgentMessageDeltaNotification,
    "item/started": bindings.ItemStartedNotification,
    "item/completed": bindings.ItemCompletedNotification,
    "item/commandExecution/outputDelta": bindings.CommandExecutionOutputDeltaNotification,
    "item/mcpToolCall/progress": bindings.McpToolCallProgressNotification,
    "error": bindings.ErrorNotification,
}

# The requests codex makes of us that we answer. Anything else it asks is refused with
# method-not-found rather than left hanging, because a server request nobody answers is a
# codex that waits forever.
HANDLED_SERVER_REQUESTS: dict[str, type[BaseModel]] = {
    "item/commandExecution/requestApproval": bindings.CommandExecutionRequestApprovalParams,
    "item/fileChange/requestApproval": bindings.FileChangeRequestApprovalParams,
}

METHOD_NOT_FOUND_ERROR_CODE = -32601

# A finished agent message can be long and arrives as one line, so the reader's line limit
# is raised well past the stream default rather than left to fail on a big turn.
CHILD_OUTPUT_LINE_LIMIT_BYTES = 50 * 1024 * 1024

# Enough of a dead child's standard error to say what happened, in the failed turn's line.
STANDARD_ERROR_RING_BUFFER_BYTES = 64 * 1024

# How long a request that should answer promptly is given. Every request this adapter makes
# is one codex answers as soon as it has taken the message — none of them wait for a turn —
# so a wait this long means the child is not answering at all.
REQUEST_TIMEOUT_SECONDS = 60.0

# How long a child gets to end politely before it is killed.
CHILD_SHUTDOWN_GRACE_SECONDS = 2.0


class CodexAppServerError(Exception):
    """Something on the codex wire could not be done. Always say which."""


class CodexChildWouldNotStart(CodexAppServerError):
    """The ``codex app-server`` process would not spawn."""


class CodexWireFailed(CodexAppServerError):
    """The message did not reach the child, or the wire it would go over is finished."""


class CodexRequestRejected(CodexAppServerError):
    """The child answered the request with an error rather than a result."""

    def __init__(self, method: str, code: int | None, message: str) -> None:
        super().__init__(f"{method} was rejected: {message}")
        self.method = method
        self.code = code
        self.message = message


class CodexServerMessageHandler(Protocol):
    """What the adapter does with what the child says.

    Neither call returns an answer. A notification has none, and a server request is
    answered later through ``respond`` — that is what keeps a person's thinking time out of
    the reader loop.
    """

    async def on_notification(self, method: str, notification: BaseModel) -> None: ...

    async def on_server_request(
        self, method: str, request_id: Any, params: BaseModel
    ) -> None: ...

    async def on_child_ended(self) -> None:
        """The process is gone. Anything waiting on its wire will never be answered."""


class CodexAppServerClient:
    """One ``codex app-server`` child process and the wire to it."""

    def __init__(self, *, handler: CodexServerMessageHandler, description: str) -> None:
        self._handler = handler
        self._description = description
        self._process: asyncio.subprocess.Process | None = None
        self._request_ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader: asyncio.Task[None] | None = None
        self._standard_error_reader: asyncio.Task[None] | None = None
        self._child_watcher: asyncio.Task[None] | None = None
        self._standard_error: deque[bytes] = deque()
        self._standard_error_bytes = 0
        self._wire_broken = False

    # --- the process --------------------------------------------------------------------

    async def start(
        self,
        *,
        argv: Sequence[str],
        environment: Mapping[str, str],
        working_directory: Path,
    ) -> None:
        """Spawn the child and begin reading it. Raises ``CodexChildWouldNotStart``."""
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=dict(environment),
                cwd=str(working_directory),
                limit=CHILD_OUTPUT_LINE_LIMIT_BYTES,
            )
        except OSError as would_not_spawn:
            raise CodexChildWouldNotStart(str(would_not_spawn)) from would_not_spawn
        self._process = process
        self._reader = asyncio.create_task(
            self._read_messages(), name=f"planner.conversation2.codex.read.{self._description}"
        )
        if process.stderr is not None:
            self._standard_error_reader = asyncio.create_task(
                self._read_standard_error(process.stderr),
                name=f"planner.conversation2.codex.stderr.{self._description}",
            )
        self._child_watcher = asyncio.create_task(
            self._watch_for_the_child_ending(process),
            name=f"planner.conversation2.codex.child.{self._description}",
        )

    async def stop(self) -> None:
        """End the child: stop reading it, close its input, and make sure it is gone."""
        self._wire_broken = True
        for task in (self._reader, self._standard_error_reader, self._child_watcher):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        self._reader = None
        self._standard_error_reader = None
        self._child_watcher = None
        self._fail_everything_pending("this child is being shut down")
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin is not None:
            with suppress(Exception):
                process.stdin.close()
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), CHILD_SHUTDOWN_GRACE_SECONDS)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
                with suppress(Exception):
                    await process.wait()

    @property
    def is_alive(self) -> bool:
        process = self._process
        return process is not None and process.returncode is None and not self._wire_broken

    def standard_error_tail(self) -> str | None:
        """What the child last said on standard error, or nothing if it said nothing."""
        if not self._standard_error:
            return None
        return b"".join(self._standard_error).decode("utf-8", errors="replace")

    # --- talking ------------------------------------------------------------------------

    async def request(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        """Ask the child something and wait for its answer.

        Raises ``CodexWireFailed`` if the request did not get out or the child stopped
        answering, and ``CodexRequestRejected`` if the child answered with an error.
        """
        answer = await self.begin_request(method, params)
        return await self.finish_request(method, answer)

    async def begin_request(
        self, method: str, params: Mapping[str, Any] | None = None
    ) -> asyncio.Future[dict[str, Any]]:
        """Put a request on the wire and hand back the answer that has not arrived yet.

        Returning at the write is what lets a caller who has another way of knowing the
        request was taken — a notification saying so — stop waiting for the answer without
        losing it.
        """
        request_id = next(self._request_ids)
        answer: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = answer
        message: dict[str, Any] = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = dict(params)
        try:
            await self._write(message)
        except CodexWireFailed:
            self._pending.pop(request_id, None)
            if not answer.done():
                answer.cancel()
            raise
        return answer

    async def finish_request(self, method: str, answer: asyncio.Future[dict[str, Any]]) -> Any:
        """Wait for one begun request's answer and unwrap it."""
        try:
            envelope = await asyncio.wait_for(answer, REQUEST_TIMEOUT_SECONDS)
        except TimeoutError as never_answered:
            self._wire_broken = True
            raise CodexWireFailed(f"{method} was not answered") from never_answered
        except asyncio.CancelledError:
            raise
        except CodexWireFailed:
            raise
        error = envelope.get("error")
        if error is not None:
            raise CodexRequestRejected(
                method,
                error.get("code") if isinstance(error, dict) else None,
                str(error.get("message")) if isinstance(error, dict) else str(error),
            )
        return envelope.get("result")

    async def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"method": method}
        if params is not None:
            message["params"] = dict(params)
        await self._write(message)

    async def respond(self, request_id: Any, result: Mapping[str, Any]) -> None:
        """Answer a request the child is holding open."""
        await self._write({"id": request_id, "result": dict(result)})

    async def respond_with_error(self, request_id: Any, *, code: int, message: str) -> None:
        await self._write({"id": request_id, "error": {"code": code, "message": message}})

    async def _write(self, message: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or self._wire_broken:
            raise CodexWireFailed("this child's wire is not usable")
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            process.stdin.write(line.encode("utf-8"))
            await process.stdin.drain()
        except Exception as did_not_reach:
            self._wire_broken = True
            raise CodexWireFailed(str(did_not_reach)) from did_not_reach

    # --- listening ----------------------------------------------------------------------

    async def _read_messages(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        while True:
            try:
                line = await process.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError) as unreadable:
                LOGGER.warning(
                    "codex %s: a line could not be read: %r", self._description, unreadable
                )
                break
            if not line:
                break
            text = line.strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except ValueError:
                LOGGER.warning("codex %s: a line was not JSON", self._description)
                continue
            if not isinstance(message, dict):
                LOGGER.warning("codex %s: a line was not a JSON object", self._description)
                continue
            await self._route(message)

    async def _route(self, message: dict[str, Any]) -> None:
        """Say what a message is from what it carries, since nothing tags it."""
        method = message.get("method")
        if isinstance(method, str):
            if "id" in message:
                await self._on_server_request(method, message["id"], message.get("params"))
            else:
                await self._on_notification(method, message.get("params"))
            return
        request_id = message.get("id")
        if isinstance(request_id, int) and ("result" in message or "error" in message):
            answer = self._pending.pop(request_id, None)
            if answer is None:
                LOGGER.debug("codex %s: an answer arrived for nothing", self._description)
                return
            if not answer.done():
                answer.set_result(message)
            return
        LOGGER.warning("codex %s: a message was none of the three shapes", self._description)

    async def _on_notification(self, method: str, params: Any) -> None:
        model = CONSUMED_SERVER_NOTIFICATIONS.get(method)
        if model is None:
            LOGGER.debug("codex %s: dropped notification %s", self._description, method)
            return
        try:
            notification = model.model_validate(params if params is not None else {})
        except ValidationError as would_not_decode:
            # A method we act on that will not decode is the protocol moving under us. It
            # is said plainly here, because a dropped one looks exactly like an agent that
            # did nothing.
            LOGGER.warning(
                "codex %s: the %s notification did not decode: %s",
                self._description,
                method,
                would_not_decode,
            )
            return
        await self._handler.on_notification(method, notification)

    async def _on_server_request(self, method: str, request_id: Any, params: Any) -> None:
        model = HANDLED_SERVER_REQUESTS.get(method)
        if model is None:
            await self._refuse(request_id, f"{method} is not a request this client answers")
            return
        try:
            decoded = model.model_validate(params if params is not None else {})
        except ValidationError as would_not_decode:
            LOGGER.warning(
                "codex %s: the %s request did not decode: %s",
                self._description,
                method,
                would_not_decode,
            )
            await self._refuse(request_id, f"{method} did not decode")
            return
        await self._handler.on_server_request(method, request_id, decoded)

    async def _refuse(self, request_id: Any, message: str) -> None:
        with suppress(CodexAppServerError):
            await self.respond_with_error(
                request_id, code=METHOD_NOT_FOUND_ERROR_CODE, message=message
            )

    async def _read_standard_error(self, stream: asyncio.StreamReader) -> None:
        while True:
            try:
                chunk = await stream.readline()
            except (ValueError, asyncio.LimitOverrunError):
                continue
            if not chunk:
                return
            self._standard_error.append(chunk)
            self._standard_error_bytes += len(chunk)
            while (
                self._standard_error_bytes > STANDARD_ERROR_RING_BUFFER_BYTES
                and len(self._standard_error) > 1
            ):
                self._standard_error_bytes -= len(self._standard_error.popleft())

    async def _watch_for_the_child_ending(self, process: asyncio.subprocess.Process) -> None:
        """Wait for the process to go, then stop everything that is waiting on its wire."""
        await process.wait()
        self._wire_broken = True
        self._fail_everything_pending("the codex process ended")
        await self._handler.on_child_ended()

    def _fail_everything_pending(self, why: str) -> None:
        for answer in list(self._pending.values()):
            if not answer.done():
                answer.set_exception(CodexWireFailed(why))
        self._pending.clear()


def child_environment(
    *, overrides: Sequence[tuple[str, str]] = (), identity: Sequence[tuple[str, str]] = ()
) -> dict[str, str]:
    """The environment a codex child runs in: this process's, then what was asked for.

    Codex is inherited wholesale rather than given a curated list of names the way the ACP
    SDK gives hermes one. Its account lives under the real ``HOME`` and it reads a long tail
    of its own variables, so taking the environment away would be taking away the login.
    ``CODEX_HOME`` is deliberately untouched: Panels runs one codex account.
    """
    environment = dict(os.environ)
    environment.update(dict(overrides))
    environment.update(dict(identity))
    return environment

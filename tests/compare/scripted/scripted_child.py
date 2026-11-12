"""Thin Python backend seam to the same scripted process used by the TS host."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, NotRequired, TypedDict, Unpack, cast

from planner.conversation.backends import contracts as backend
from planner.conversation.contracts import (
    AnswerFate,
    ComposerCatalogEntry,
    ComposerCatalogEntryKind,
    PromptDeliveryMode,
    PromptDeliveryRefusalReason,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    ConversationTurnEnding,
    PermissionAskOption,
    PlanEntry,
    PlanEntryStatus,
    ToolCallStatus,
    UserInputAnswer,
    UserInputOption,
    UserInputQuestion,
)
from planner.conversation.message_content import (
    MessageContent,
    message_content_from_stored,
    message_content_json_entries,
)

_ERRORS: dict[str, type[Exception]] = {
    name: getattr(backend, name)
    for name in (
        "BackendAdapterError",
        "BackendSpawnFailed",
        "SessionLoadFailed",
        "PromptWriteRefused",
        "PromptWriteFailed",
        "RemoteCommandUncertain",
        "DeliveryCancelled",
        "BackendPromptRejected",
        "PermissionAnswerWriteFailed",
        "UserInputAnswerWriteFailed",
        "NeedsRebind",
    )
}


def _json_default(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise TypeError(f"cannot serialize scripted value {type(value).__name__}")


def _content(content: MessageContent) -> list[dict[str, Any]]:
    stored = message_content_json_entries(content)
    if "content" in stored:
        return cast(list[dict[str, Any]], stored["content"])
    return [{"piece": "text", "text": stored["text"]}]


def decode_scripted_event(frame: dict[str, Any]) -> backend.BackendEventValue:
    values = dict(frame["values"])
    match frame["kind"]:
        case "AgentMessageDelta":
            return backend.AgentMessageDelta(**values)
        case "ModelThinkingHappened":
            return backend.ModelThinkingHappened()
        case "AgentMessageCompleted":
            return backend.AgentMessageCompleted(content=message_content_from_stored(values))
        case "CommandReplyReported":
            return backend.CommandReplyReported(**values)
        case "ToolCallStarted":
            return backend.ToolCallStarted(**values)
        case "ToolCallProgress":
            return backend.ToolCallProgress(**values)
        case "ToolCallFinished":
            values["tool_call_status"] = ToolCallStatus(values["tool_call_status"])
            return backend.ToolCallFinished(**values)
        case "PlanUpdated":
            return backend.PlanUpdated(
                entries=tuple(
                    PlanEntry(text=item["text"], status=PlanEntryStatus(item["status"]))
                    for item in values["entries"]
                )
            )
        case "TokenUsageReported":
            return backend.TokenUsageReported(**values)
        case "ContextCompacted":
            return backend.ContextCompacted()
        case "PermissionAskRaised":
            ask = dict(values["ask"])
            ask["options"] = tuple(PermissionAskOption(**item) for item in ask["options"])
            return backend.PermissionAskRaised(ask=backend.BackendPermissionAsk(**ask))
        case "UserInputRequested":
            request = values["request"]
            questions = []
            for item in request["questions"]:
                question = dict(item)
                question["options"] = tuple(UserInputOption(**item) for item in item["options"])
                questions.append(UserInputQuestion(**question))
            return backend.UserInputRequested(
                request=backend.BackendUserInputRequest(
                    request_id=request["request_id"], questions=tuple(questions)
                )
            )
        case "UserInputFailed":
            return backend.UserInputFailed(**values)
        case "TurnEnded":
            values["ending"] = ConversationTurnEnding(values["ending"])
            return backend.TurnEnded(**values)
        case "VendorSessionCursorRebound":
            return backend.VendorSessionCursorRebound(**values)
        case "ComposerCatalogReported":
            entries = []
            for item in values["composer_catalog"]:
                entry = dict(item)
                entry["kind"] = ComposerCatalogEntryKind(entry["kind"])
                entries.append(ComposerCatalogEntry(**entry))
            return backend.ComposerCatalogReported(composer_catalog=tuple(entries))
        case "BackgroundTaskStarted":
            return backend.BackgroundTaskStarted(**values)
        case "BackgroundTaskEnded":
            return backend.BackgroundTaskEnded(**values)
        case "BackendStreamEnded":
            return backend.BackendStreamEnded()
        case _:
            raise ValueError(f"unknown scripted backend event {frame['kind']}")


class _EventTarget(TypedDict):
    turn: NotRequired[backend.TurnToken | None]


class ScriptedChild(backend.BackendChild):
    """An adapter owns transport and observation caches, never scripted agent behavior."""

    stop_outside_lock = False

    def __init__(self, sink: backend.BackendEventSink) -> None:
        self.sink = sink
        self.token: backend.TurnToken | None = None
        self.reject_next_prompt = False
        self.delivered_text: list[str] = []
        self.answers: dict[str, tuple[UserInputAnswer, ...]] = {}
        self.last_observation: dict[str, Any] | None = None
        self._observed_deliveries = 0
        self._spawn_task: asyncio.Task[asyncio.subprocess.Process] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[bytes] | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._reports: set[asyncio.Task[None]] = set()
        self._report_errors: list[BaseException] = []
        self._next_id = 0
        self._closed = False

    async def _spawn(self) -> asyncio.subprocess.Process:
        process = await asyncio.create_subprocess_exec(
            "node",
            str(
                Path(__file__).resolve().parents[3]
                / "server/conversation/testing/scripted_process.ts"
            ),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stderr is not None
        self._stderr_task = asyncio.create_task(process.stderr.read())
        self._reader_task = asyncio.create_task(self._read(process))
        return process

    async def _process(self) -> asyncio.subprocess.Process:
        if self._spawn_task is None:
            self._spawn_task = asyncio.create_task(self._spawn())
        # A caller cancellation cannot lose a subprocess before its PID is retained.
        return await asyncio.shield(self._spawn_task)

    def _fail(self, error: BaseException) -> None:
        self._closed = True
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    def _report_finished(self, task: asyncio.Task[None]) -> None:
        if task not in self._reports:
            return
        self._reports.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            self._report_errors.append(error)

    def _take_frame(self, frame: dict[str, Any]) -> None:
        if "event" in frame:
            token = None if frame["turn"] is None else backend.TurnToken(**frame["turn"])
            # The report can await a receipt. The reply reader must remain independent.
            task = asyncio.create_task(
                self.sink.report(token, decode_scripted_event(frame["event"]))
            )
            self._reports.add(task)
            task.add_done_callback(self._report_finished)
            return
        future = self._pending.pop(frame["id"], None)
        if future is None or future.done():
            return
        if "error" in frame:
            error = frame["error"]
            exception: Exception
            if error["name"] == "NeedsRebind":
                exception = backend.NeedsRebind(
                    error["message"],
                    failed_child_recovery=error.get("failed_child_recovery", False),
                )
            else:
                exception = _ERRORS.get(error["name"], RuntimeError)(error["message"])
            future.set_exception(exception)
        else:
            future.set_result(frame["result"])

    async def _read(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        try:
            buffered = b""
            while chunk := await process.stdout.read(65536):
                buffered += chunk
                while b"\n" in buffered:
                    line, buffered = buffered.split(b"\n", 1)
                    self._take_frame(json.loads(line))
            if buffered:
                raise RuntimeError("incomplete scripted frame at process exit")
            code = await process.wait()
            stderr = b"" if self._stderr_task is None else await self._stderr_task
            self._fail(RuntimeError(f"scripted child exited ({code}): {stderr.decode()}"))
        except Exception as error:
            self._fail(error)
            if process.returncode is None:
                process.terminate()
            await process.wait()

    async def _request(self, operation: str, **arguments: Any) -> Any:
        if self._closed:
            raise RuntimeError("scripted child is closed")
        process = await self._process()
        if self._closed or process.returncode is not None:
            raise RuntimeError("scripted child is closed")
        assert process.stdin is not None
        self._next_id += 1
        identity = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[identity] = future
        try:
            process.stdin.write(
                (
                    json.dumps(
                        {"id": identity, "command": operation, "arguments": arguments},
                        default=_json_default,
                    )
                    + "\n"
                ).encode()
            )
            await process.stdin.drain()
            return await future
        except BaseException:
            self._pending.pop(identity, None)
            future.cancel()
            raise

    async def inspect(self) -> dict[str, Any]:
        snapshot = cast(dict[str, Any], await self._request("inspect"))
        self.last_observation = snapshot
        self.token = None if snapshot["token"] is None else backend.TurnToken(**snapshot["token"])
        texts: list[str] = snapshot["delivered_text"]
        self.delivered_text.extend(texts[self._observed_deliveries :])
        self._observed_deliveries = len(texts)
        self.answers = {
            request: tuple(
                UserInputAnswer(question_id=item["question_id"], answers=tuple(item["answers"]))
                for item in answers
            )
            for request, answers in snapshot["answers"].items()
        }
        return snapshot

    async def _observed_request(self, command: str, **arguments: Any) -> Any:
        try:
            result = await self._request(command, **arguments)
        except Exception:
            # Observation failure must not replace the native operation's exact exception.
            if not self._closed:
                try:
                    await self.inspect()
                except Exception:
                    pass
            raise
        try:
            await self.inspect()
        except Exception:
            # A cache read after a receipt cannot change that native delivery outcome.
            pass
        return result

    async def start(
        self,
        resolved_start: ResolvedConversationStart,
        *,
        vendor_session_cursor: str | None,
    ) -> None:
        await self._request(
            "start", resolved_start=resolved_start, vendor_session_cursor=vendor_session_cursor
        )

    async def write_prompt(
        self,
        turn_token: backend.TurnToken,
        content: MessageContent,
        *,
        sender_label: str,
        sender_content: MessageContent,
        sender_message_count: int = 1,
        mode: PromptDeliveryMode,
        model_change: str | None,
        reasoning_effort_change: str | None,
        automatic_compaction: bool = False,
        reasoning_effort_is_set: bool = False,
    ) -> backend.BackendPromptAccepted:
        if self.reject_next_prompt:
            self.reject_next_prompt = False
            await self.configure(
                "write_prompt",
                {
                    "error": {
                        "name": "BackendPromptRejected",
                        "message": "Synthetic provider refused this prompt.",
                    }
                },
            )
        result = await self._observed_request(
            "write_prompt",
            turn_token=turn_token,
            content=_content(content),
            sender_content=_content(sender_content),
            sender_label=sender_label,
            sender_message_count=sender_message_count,
            mode=mode,
            model_change=model_change,
            reasoning_effort_change=reasoning_effort_change,
            automatic_compaction=automatic_compaction,
            reasoning_effort_is_set=reasoning_effort_is_set,
        )
        return backend.BackendPromptAccepted(**result)

    async def steer(
        self,
        turn_token: backend.TurnToken,
        content: MessageContent,
        *,
        sender_content: MessageContent | None = None,
        sender_label: str,
    ) -> backend.BackendSteerOutcome:
        result = await self._request(
            "steer",
            turn_token=turn_token,
            content=_content(content),
            sender_content=None if sender_content is None else _content(sender_content),
            sender_label=sender_label,
        )
        if result["fate"] == "refused":
            return backend.BackendSteerRefused(
                refusal_reason=PromptDeliveryRefusalReason(result["refusal_reason"])
            )
        if result["fate"] == "uncertain":
            return backend.BackendSteerUncertain(
                composed_content_delivered=result.get("composed_content_delivered", True)
            )
        composed = result.get("composed_content_delivered")
        return backend.BackendSteerAccepted(
            composed_content_delivered=True if composed is None else composed
        )

    async def cancel_running_turn(self) -> None:
        await self._observed_request("cancel_running_turn")

    async def answer_permission_ask(
        self,
        ask_id: str,
        option_id: str,
        *,
        answer_id: str | None = None,
        turn_token: backend.TurnToken | None = None,
    ) -> AnswerFate:
        return AnswerFate(
            await self._request(
                "answer_permission_ask",
                ask_id=ask_id,
                option_id=option_id,
                answer_id=answer_id,
                turn_token=turn_token,
            )
        )

    async def answer_user_input(
        self,
        request_id: str,
        answers: tuple[UserInputAnswer, ...],
        *,
        answer_id: str | None = None,
        turn_token: backend.TurnToken | None = None,
    ) -> AnswerFate:
        return AnswerFate(
            await self._observed_request(
                "answer_user_input",
                request_id=request_id,
                answers=answers,
                answer_id=answer_id,
                turn_token=turn_token,
            )
        )

    async def withdraw_delivery(self) -> bool:
        return cast(bool, await self._request("withdraw_delivery"))

    async def _close(self) -> None:
        process = await self._process()
        assert process.stdin is not None
        process.stdin.close()
        await process.wait()
        if self._reader_task is not None:
            await self._reader_task

    async def stop(self) -> None:
        if self._spawn_task is None:
            self._closed = True
            return
        if self._closed:
            if self._spawn_task is not None:
                await (await self._process()).wait()
            return
        await self._observed_request("stop")
        await self._close()

    async def detach(self) -> None:
        if self._spawn_task is None:
            self._closed = True
            return
        await self._observed_request("detach")
        await self._close()

    async def configure(self, command: str, behavior: dict[str, Any]) -> None:
        await self._request("configure", command=command, behavior=behavior)

    async def wait_for_barrier(self, name: str) -> None:
        await self._request("wait_for_barrier", name=name)

    async def release(self, name: str) -> None:
        await self._request("release", name=name)

    async def emit(
        self,
        event: backend.BackendEventValue,
        **target: Unpack[_EventTarget],
    ) -> None:
        values = (
            message_content_json_entries(event.content)
            if isinstance(event, backend.AgentMessageCompleted)
            else asdict(event)
        )
        await self._request(
            "emit", event={"kind": type(event).__name__, "values": values}, **target
        )

    async def request_questions(self) -> None:
        await self._request("questions")
        await self.flush_events()

    async def finish(self, *, ending: str = "completed", error_summary: str | None = None) -> None:
        await self._observed_request("finish", ending=ending, error_summary=error_summary)
        await self.flush_events()

    async def flush_events(self) -> None:
        while self._reports:
            reports = tuple(self._reports)
            await asyncio.gather(*reports, return_exceptions=True)
            # Awaiting already-done tasks can finish before their queued done callbacks.
            for task in reports:
                self._report_finished(task)
        if self._report_errors:
            raise self._report_errors.pop(0)

    async def terminate(self) -> None:
        """Stop only the exact subprocess that this adapter started."""
        if self._spawn_task is None:
            return
        process = await self._process()
        if process.returncode is None:
            process.terminate()
        await process.wait()
        if self._reader_task is not None:
            await self._reader_task

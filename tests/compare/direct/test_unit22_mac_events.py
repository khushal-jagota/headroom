"""The complete local runner routes native events and captures real artifacts."""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from tests.compare.direct.mac_executor_fixture import report_direct
from tests.compare.direct.mac_runner_fixture import (
    make_runner,
    paired_fixture,
    pending_envelopes,
    record_commands,
)
from tests.compare.direct.test_unit22_mac_commands import fixture as command_fixture

from planner.conversation import events as conversation_events
from planner.conversation.backends import contracts as backend
from planner.conversation.message_content import MessageText
from planner.executor.configuration import MAX_TOOL_DETAIL_CHARACTERS, ExecutorPaths
from planner.executor.protocol import (
    AnswerPermission,
    BackendEvent,
    ChildCommand,
    as_object,
    decode_event_arguments,
    encode_backend_event,
    encode_value,
    type_adapter,
)

NOW = 1_700_000_010_000
ARTIFACTS = {"alpha.txt": "alpha 🦉\r\n".encode(), "binary.dat": b"\x00\x01\xffpayload"}


def fixture(root: Path) -> dict[str, Any]:
    paths = ExecutorPaths(root)
    paths.workspace.mkdir(parents=True)
    for name, content in ARTIFACTS.items():
        path = paths.artifacts / "ticket-codex" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        os.utime(path, ns=(1_700_000_000_000_000_000,) * 2)
    shared = command_fixture(root)
    commands = {key: shared["commands"][key] for key in ("start", "prompt", "permission", "stop")}
    permission = type_adapter(ChildCommand).validate_json(commands["permission"])
    args = permission.arguments
    assert isinstance(args, AnswerPermission) and args.answer is not None
    child, turn = as_object(args.answer.ask.child), as_object(args.answer.ask.turn)
    detail = "🦉" * MAX_TOOL_DETAIL_CHARACTERS + "discard"
    permission_ask = backend.BackendPermissionAsk(
        ask_id=args.answer.ask.ask_id,
        title="Allow?",
        detail="Permission",
        options=(
            conversation_events.PermissionAskOption(args.option_id, "Allow once", "allow_once"),
        ),
    )
    question = conversation_events.UserInputQuestion(
        question_id="q",
        header="Choice",
        question="Choose",
        options=(conversation_events.UserInputOption("Yes", "Confirm"),),
        multi_select=False,
        allow_other=True,
    )
    completed = conversation_events.ConversationTurnEnding.completed
    values: dict[str, backend.BackendEventValue] = {
        "tool": backend.ToolCallStarted(
            tool_call_id="tool", title="Build", tool_kind="shell", detail=detail
        ),
        "permission": backend.PermissionAskRaised(ask=permission_ask),
        "question": backend.UserInputRequested(
            request=backend.BackendUserInputRequest("question", (question,))
        ),
        "background": backend.BackgroundTaskStarted(task_id="background", task_description="Index"),
        "delta": backend.AgentMessageDelta(text_delta="live 🦉"),
        "thinking": backend.ModelThinkingHappened(),
        "progress": backend.ToolCallProgress(tool_call_id="tool", detail=detail),
        "stale": backend.TurnEnded(
            ending=completed, error_summary="old", standard_error_tail="old stderr"
        ),
        "finished": backend.ToolCallFinished(
            tool_call_id="tool",
            tool_call_status=conversation_events.ToolCallStatus.completed,
            detail=detail,
        ),
        "background-end": backend.BackgroundTaskEnded(
            task_id="background", task_status="completed"
        ),
        "input-failed": backend.UserInputFailed(request_id="gone", detail=detail),
        "message": backend.AgentMessageCompleted(content=(MessageText("complete 🦉"),)),
        "ended": backend.TurnEnded(ending=completed, standard_error_tail="last stderr"),
        "held": backend.CommandReplyReported(reply_text="held source"),
        "stream": backend.BackendStreamEnded(),
    }
    events = {}
    for key, value in values.items():
        method, payload = encode_backend_event(value)
        token = None if key == "stream" else {**turn, "turn_number": 2} if key == "stale" else turn
        event = type_adapter(BackendEvent).validate_python(
            dict(child=child, turn=token, method=method, arguments=payload)
        )
        decode_event_arguments(event)
        events[key] = type_adapter(BackendEvent).dump_json(event).decode()
    return dict(
        commands=commands,
        events=events,
        before=["tool", "permission", "question", "background"],
        middle=["delta", "thinking", "progress", "stale"],
        after=["finished", "background-end", "input-failed", "message"],
    )


async def reference(root: Path, data: dict[str, Any], patch: pytest.MonkeyPatch) -> dict[str, Any]:
    commands = {
        key: type_adapter(ChildCommand).validate_json(raw) for key, raw in data["commands"].items()
    }
    events = {
        key: type_adapter(BackendEvent).validate_json(raw) for key, raw in data["events"].items()
    }
    live: list[Any] = []
    states: list[Any] = []
    stopped = 0
    sink: Any = None
    release, entered = asyncio.Event(), asyncio.Event()

    async def emit(key: str) -> None:
        event = events[key]
        await sink.report(event.turn, decode_event_arguments(event))

    class Native:
        async def start(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def write_prompt(self, *args: Any, **kwargs: Any) -> Any:
            return backend.BackendPromptAccepted(True)

        async def answer_permission_ask(self, *args: Any) -> None:
            pass

        async def stop(self) -> None:
            nonlocal stopped
            stopped += 1
            await emit("stream")

    def factory(resolved: Any) -> Any:
        def create(**values: Any) -> Any:
            nonlocal sink
            sink = values["event_sink"]
            return Native()

        return create

    runner, box, journal = make_runner(
        patch,
        root=root,
        now_ms=NOW,
        factory=factory,
        files=None,
        live=cast(Any, SimpleNamespace(send=lambda event: live.append(as_object(event)))),
        paths=ExecutorPaths(root),
        workspace_home=root,
    )
    record_commands(journal, commands, now_ms=NOW)

    async def snapshot() -> None:
        states.append(
            dict(
                working=encode_value(runner.working_turns()),
                quiescence=as_object(await runner.quiesce()),
                background=sorted(runner._children["child"].background_task_ids),
            )
        )
        runner.resume_after_cancelled_update()

    async def held() -> None:
        entered.set()
        await release.wait()
        await emit("held")

    source: asyncio.Task[Any] | None = None
    stopping: asyncio.Task[Any] | None = None
    try:
        await runner.run(commands["start"])
        await runner.run(commands["prompt"])
        for key in data["before"]:
            await emit(key)
        await snapshot()
        answer = as_object(await runner.run(commands["permission"]))
        for key in data["middle"]:
            await emit(key)
        await snapshot()
        for key in data["after"]:
            await emit(key)
        source = runner.source_work(commands["start"].child, held)
        await entered.wait()
        await emit("ended")
        await snapshot()
        stopping = asyncio.create_task(runner.run(commands["stop"]))
        while True:
            assert not stopping.done()
            try:
                await runner.source_work(commands["stop"].child, lambda: asyncio.sleep(0))
            except PermissionError:
                break
        waiting = not stopping.done() and stopped == 0
        release.set()
        await source
        stop = as_object(await stopping)
        return dict(
            live=live,
            states=states,
            answer=answer,
            stop=stop,
            pending=pending_envelopes(box),
            waiting=waiting,
            native_stops=stopped,
        )
    finally:
        release.set()
        await asyncio.gather(
            *(task for task in (source, stopping) if task is not None), return_exceptions=True
        )
        journal.close()
        box.close()


def test_unit22_mac_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    expected, observed, target = paired_fixture(
        tmp_path,
        monkeypatch,
        reference=lambda root, patch: reference(root, fixture(root), patch),
        inputs=fixture,
        entry="server/executor/mac/testing/events_parity.ts",
        python_timeout=20,
        node_timeout=30,
    )
    assert observed["parity"] == expected
    assert expected["waiting"] and expected["native_stops"] == 1
    assert observed["lifecycle"] and all(observed["lifecycle"].values())
    assert [event["method"] for event in expected["live"]] == [
        "agent_message_delta",
        "model_thinking_happened",
        "tool_call_progress",
    ]
    event_values = expected["live"] + [
        row["payload"] for row in expected["pending"] if row["kind"] == "backend_event"
    ]
    details = [
        event["arguments"]["detail"] for event in event_values if "detail" in event["arguments"]
    ]
    assert len(details) == 4 and {len(detail) for detail in details} == {MAX_TOOL_DETAIL_CHARACTERS}
    captured = [row for row in expected["pending"] if row["file_dependencies"]]
    assert captured and all(
        {manifest["relative_path"]: manifest["sha256"] for manifest in row["file_dependencies"]}
        == {name: hashlib.sha256(content).hexdigest() for name, content in ARTIFACTS.items()}
        for row in captured
    )
    assert [row["payload"].get("method", row["kind"]) for row in expected["pending"][-3:]] == [
        "command_reply_reported",
        "backend_stream_ended",
        "child_stopped",
    ]
    for content in ARTIFACTS.values():
        assert (
            target / "spool" / "blobs" / hashlib.sha256(content).hexdigest()
        ).read_bytes() == content
    report_direct(
        request,
        "unit-22-mac-events.json",
        observed,
        started,
        "Complete MacRunner native sink, real journal/spool and artifact capture; "
        "no transport or provider",
    )

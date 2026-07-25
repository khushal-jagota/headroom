"""A codex app-server the tests write the script for, speaking the real wire.

The adapter's job is to turn one protocol into another, so the only honest way to test it
is to put that protocol on the other end: a real child process, real pipes, real
newline-delimited JSON with no envelope. This is that child. It answers ``initialize``,
starts and resumes threads, runs turns made of whatever the script says, asks for approvals
and waits for the answers, and writes down everything it was sent so a test can assert on
what actually reached it.

It holds no test of its own. The name is ``test_...`` only because this ticket's files are
its tests, and it is spawned as a script — ``python <this file>`` with the script's path in
the environment — rather than imported by the code under test.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

SCRIPT_PATH_ENVIRONMENT_NAME = "PANELS_CODEX_SCRIPTED_APP_SERVER_SCRIPT"

# What a thread and a turn have to carry to be one, filled in around whatever the script
# cares about. Codex's own shapes require them, and a fake that skipped them would be
# testing the adapter against a protocol nobody speaks.
_THREAD_FILLER: dict[str, Any] = {
    "cliVersion": "0.145.0",
    "createdAt": 0,
    "updatedAt": 0,
    "ephemeral": False,
    "turns": [],
    "modelProvider": "openai",
    "preview": "",
    "source": "appServer",
    "status": {"type": "idle"},
}


def scripted_app_server_launch(script_path: Path) -> tuple[tuple[str, ...], dict[str, str]]:
    """The argv and environment overrides that run this file as a codex app-server."""
    return (
        (sys.executable, str(Path(__file__).resolve()), "app-server"),
        {SCRIPT_PATH_ENVIRONMENT_NAME: str(script_path)},
    )


class ScriptedAppServer:
    def __init__(self, script: dict[str, Any]) -> None:
        self._script = script
        self._transcript = Path(script["transcript_path"])
        self._thread_id: str = script.get("thread_id", "thread-1")
        self._turns: list[dict[str, Any]] = list(script.get("turns", []))
        self._turns_started = 0
        self._interrupted = asyncio.Event()
        self._approval_answered = asyncio.Event()
        self._writing = asyncio.Lock()
        self._exit_code: int | None = None
        self._turn_tasks: set[asyncio.Task[None]] = set()

    # --- running ------------------------------------------------------------------------

    async def run(self) -> None:
        # What this child was actually launched with, so a test can prove the identity and
        # the workspace folder a conversation runs under reached the process itself.
        self._write_down(
            {"launched": {"cwd": str(Path.cwd()), "environment": dict(os.environ)}}
        )
        standard_error = self._script.get("stderr")
        if standard_error:
            sys.stderr.write(standard_error)
            sys.stderr.flush()
        reader = await self._stdin_reader()
        while True:
            line = await reader.readline()
            if not line:
                return
            message = json.loads(line)
            self._write_down({"received": message})
            await self._handle(message)
            if self._exit_code is not None:
                sys.exit(self._exit_code)

    async def _stdin_reader(self) -> asyncio.StreamReader:
        reader = asyncio.StreamReader()
        await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
        )
        return reader

    async def _handle(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if method is None:
            # An answer to something we asked. Every ask this makes is one it can carry on
            # without, so the answer is written down and noted and nothing waits on it.
            self._write_down({"answer": message})
            self._approval_answered.set()
            return
        request_id = message.get("id")
        parameters = message.get("params") or {}
        match method:
            case "initialized":
                return
            case "initialize":
                await self._answer_initialize(request_id)
            case "thread/start":
                await self._answer_thread_start(request_id, parameters)
            case "thread/resume":
                await self._answer_thread_resume(request_id, parameters)
            case "turn/start":
                # A turn runs alongside the reader so that an interrupt can arrive while it
                # is running, which is the whole point of an interrupt.
                running = asyncio.create_task(self._run_turn(request_id, parameters))
                self._turn_tasks.add(running)
                running.add_done_callback(self._turn_tasks.discard)
            case "turn/interrupt":
                self._interrupted.set()
                await self._respond(request_id, {})
            case _:
                await self._respond_with_error(request_id, f"{method} is not scripted")

    # --- the handshake and the thread -----------------------------------------------------

    async def _answer_initialize(self, request_id: Any) -> None:
        if self._script.get("initialize") == "error":
            await self._respond_with_error(request_id, "initialize was refused")
            return
        await self._respond(
            request_id,
            {
                "codexHome": str(Path.home() / ".codex"),
                "platformFamily": "unix",
                "platformOs": "macos",
                "userAgent": "codex-cli/0.145.0 (scripted)",
            },
        )

    async def _answer_thread_start(self, request_id: Any, parameters: dict[str, Any]) -> None:
        await self._respond(request_id, self._thread_answer(self._thread_id, parameters))

    async def _answer_thread_resume(self, request_id: Any, parameters: dict[str, Any]) -> None:
        resume = self._script.get("resume", {})
        outcome = resume.get("outcome", "ok")
        if outcome == "error":
            await self._respond_with_error(request_id, resume.get("message", "no such thread"))
            return
        # ``other_thread`` is the trap: a resume that answers with a thread nobody asked for.
        answered_with = (
            resume["thread_id"] if outcome == "other_thread" else parameters["threadId"]
        )
        self._thread_id = answered_with
        await self._respond(request_id, self._thread_answer(answered_with, parameters))

    def _thread_answer(self, thread_id: str, parameters: dict[str, Any]) -> dict[str, Any]:
        cwd = parameters.get("cwd") or str(Path.cwd())
        return {
            "thread": {
                **_THREAD_FILLER,
                "id": thread_id,
                "sessionId": thread_id,
                "cwd": cwd,
            },
            "approvalPolicy": parameters.get("approvalPolicy", "never"),
            "approvalsReviewer": parameters.get("approvalsReviewer", "user"),
            "sandbox": {"type": "dangerFullAccess"},
            "cwd": cwd,
            "model": parameters.get("model") or "gpt-5.4-mini",
            "modelProvider": "openai",
        }

    # --- the turn ---------------------------------------------------------------------------

    async def _run_turn(self, request_id: Any, parameters: dict[str, Any]) -> None:
        script = (
            self._turns[self._turns_started] if self._turns_started < len(self._turns) else {}
        )
        self._turns_started += 1
        turn_id = script.get("turn_id", f"turn-{self._turns_started}")
        self._interrupted.clear()

        if script.get("respond") == "error":
            await self._respond_with_error(request_id, script.get("message", "turn refused"))
            return
        await self._notify("turn/started", {"threadId": self._thread_id, "turn": _turn(turn_id)})
        if script.get("respond") != "never":
            await self._respond(request_id, {"turn": _turn(turn_id)})
        # A turn nobody wrote actions for is a turn that simply finishes. An empty list is
        # a turn that deliberately does not.
        actions = (
            script["actions"]
            if "actions" in script
            else [{"do": "complete", "status": "completed"}]
        )
        for action in actions:
            await self._act(action, turn_id, parameters)

    async def _act(self, action: dict[str, Any], turn_id: str, parameters: dict[str, Any]) -> None:
        del parameters
        route = {"threadId": self._thread_id, "turnId": turn_id}
        match action["do"]:
            case "agent_delta":
                await self._notify(
                    "item/agentMessage/delta",
                    {**route, "itemId": action["item_id"], "delta": action["text"]},
                )
            case "reasoning_delta":
                await self._notify(
                    "item/reasoning/textDelta",
                    {**route, "itemId": action.get("item_id", "r"), "delta": action["text"]},
                )
            case "unknown_notification":
                await self._notify("thread/tokenUsage/updated", {**route, "usage": {}})
            case "undecodable_agent_delta":
                await self._notify(
                    "item/agentMessage/delta", {**route, "itemId": action["item_id"]}
                )
            case "command_output_delta":
                await self._notify(
                    "item/commandExecution/outputDelta",
                    {**route, "itemId": action["item_id"], "delta": action["text"]},
                )
            case "mcp_progress":
                await self._notify(
                    "item/mcpToolCall/progress",
                    {**route, "itemId": action["item_id"], "message": action["text"]},
                )
            case "item_started":
                await self._notify(
                    "item/started", {**route, "item": action["item"], "startedAtMs": 0}
                )
            case "item_completed":
                await self._notify(
                    "item/completed", {**route, "item": action["item"], "completedAtMs": 0}
                )
            case "request_approval":
                await self._request_approval(action, route)
            case "ask_unknown":
                await self._ask("item/tool/requestUserInput", {**route, "questions": []})
            case "await_approval":
                await self._approval_answered.wait()
                self._approval_answered.clear()
            case "error_notification":
                await self._notify(
                    "error",
                    {
                        **route,
                        "error": {"message": action["message"]},
                        "willRetry": action.get("will_retry", False),
                    },
                )
            case "await_interrupt":
                await self._interrupted.wait()
            case "complete":
                await self._notify(
                    "turn/completed",
                    {
                        "threadId": self._thread_id,
                        "turn": _turn(
                            turn_id,
                            status=action.get("status", "completed"),
                            error=action.get("error"),
                        ),
                    },
                )
            case "stop_reading":
                self._exit_code = 0
            case "die":
                sys.stderr.write(action.get("stderr", "the scripted app-server died\n"))
                sys.stderr.flush()
                os._exit(action.get("code", 3))

    async def _request_approval(self, action: dict[str, Any], route: dict[str, Any]) -> None:
        if action.get("which") == "file":
            await self._ask(
                "item/fileChange/requestApproval",
                {**route, "itemId": action.get("item_id", "i1"), "startedAtMs": 0},
            )
            return
        await self._ask(
            "item/commandExecution/requestApproval",
            {
                **route,
                "itemId": action.get("item_id", "i1"),
                "startedAtMs": 0,
                "command": action.get("command", "rm -rf /"),
                "commandActions": [],
            },
        )

    # --- the wire ---------------------------------------------------------------------------

    async def _respond(self, request_id: Any, result: dict[str, Any]) -> None:
        await self._send({"id": request_id, "result": result})

    async def _respond_with_error(self, request_id: Any, message: str) -> None:
        await self._send({"id": request_id, "error": {"code": -32000, "message": message}})

    async def _notify(self, method: str, parameters: dict[str, Any]) -> None:
        await self._send({"method": method, "params": parameters})

    async def _ask(self, method: str, parameters: dict[str, Any]) -> None:
        await self._send({"id": f"server-{method}", "method": method, "params": parameters})

    async def _send(self, message: dict[str, Any]) -> None:
        async with self._writing:
            sys.stdout.write(json.dumps(message) + "\n")
            sys.stdout.flush()

    def _write_down(self, entry: dict[str, Any]) -> None:
        with self._transcript.open("a", encoding="utf-8") as transcript:
            transcript.write(json.dumps(entry) + "\n")


def _turn(turn_id: str, *, status: str = "inProgress", error: Any = None) -> dict[str, Any]:
    turn: dict[str, Any] = {"id": turn_id, "status": status, "items": []}
    if error is not None:
        turn["error"] = error
    return turn


def main() -> None:
    script = json.loads(Path(os.environ[SCRIPT_PATH_ENVIRONMENT_NAME]).read_text(encoding="utf-8"))
    asyncio.run(ScriptedAppServer(script).run())


if __name__ == "__main__":
    main()

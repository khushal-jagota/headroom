"""Manual three-send provider proof. Never collected by pytest.

Run only after the Manager accepts the merged scripted adapter evidence and
answers the real-provider request. The real mode requires the executable that
the operator selects through production's override/legacy/PATH rule. This module
performs no CLI discovery, login, configuration change, or provider-file scan.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from enum import Enum
from pathlib import Path
from typing import Any, NoReturn, cast
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
FRESH_PROMPT = "Reply exactly UNIT18_FRESH_OK. Do not use tools."
RECALL_PROMPT = (
    "Recall the exact memory token from the previous turn. "
    "Reply with only that token. Do not use tools."
)


def plain(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: plain(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    return value


def synthetic_environment(root: Path) -> dict[str, str]:
    home = root / "parent-home"
    runtime = root / "disconnected-runtime"
    home.mkdir(mode=0o700)
    runtime.mkdir(mode=0o700)
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(home / "claude"),
        "CODEX_HOME": str(home / "codex"),
        "HERMES_HOME": str(home / "hermes"),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "XDG_DATA_HOME": str(home / "data"),
        "XDG_RUNTIME_DIR": str(runtime),
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/nonexistent-bus",
        "TMPDIR": str(root),
    }


def run_typescript(
    spec: dict[str, Any], environment: dict[str, str], root: Path
) -> dict[str, Any]:
    source = root / f"{spec['mode']}-input.json"
    source.write_text(json.dumps(spec))
    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {readFileSync} from 'node:fs'; import {pathToFileURL} from 'node:url'; "
            "const {run_part3_turn}=await import(pathToFileURL(process.argv[2]).href); "
            "process.stdout.write(JSON.stringify(await run_part3_turn("
            "JSON.parse(readFileSync(process.argv[1],'utf8')))));",
            str(source),
            str(ROOT / "server/conversation/testing/claude/part3_real.ts"),
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        # Raw CLI stderr can contain account data. Keep only a process-level fact here.
        raise RuntimeError(
            f"TypeScript {spec['mode']} harness failed, exit {completed.returncode}"
        )
    return cast(dict[str, Any], json.loads(completed.stdout))


async def seed_python(spec: dict[str, Any], prompt: str) -> dict[str, Any]:
    from claude_agent_sdk import (
        AssistantMessage,
        HookEventMessage,
        PermissionResultDeny,
        SystemMessage,
        TextBlock,
        ToolUseBlock,
    )
    from claude_agent_sdk._internal.transport.subprocess_cli import (
        SubprocessCLITransport,
    )

    from planner.conversation.api import delivery_fate_json
    from planner.conversation.backends.claude_agent_sdk import (
        ClaudeAgentSdkBackendChildFactory,
        ClaudeAgentSdkChildLaunch,
        claude_sdk_client,
    )
    from planner.conversation.contracts import (
        ConversationBackendKey,
        ConversationStartRequest,
        WorkspacePathPolicy,
    )
    from planner.conversation.events import (
        AgentMessageDeltaFrame,
        TurnEndedEventPayload,
    )
    from planner.conversation.live_tail import ConversationLiveTail
    from planner.conversation.message_content import text_message_content
    from planner.conversation.message_files import ConversationMessageFiles
    from planner.conversation.storage import ConversationStore
    from planner.conversation.system import SqliteProcessConversationSystem

    before_environment = dict(os.environ)
    report: dict[str, Any] = {
        "mode": "python-seed",
        "conversation_id": spec["conversation_id"],
        "native_messages": [],
        "startup_tools": None,
        "unexpected": [],
        "cleanup_errors": [],
        "version_probes": [],
        "usage": [],
        "success": False,
    }
    processes: list[asyncio.subprocess.Process] = []
    native_spawn = asyncio.create_subprocess_exec
    spawn_count = 0
    spawn_failure: str | None = None
    sdk_version_check_code = SubprocessCLITransport._check_claude_version.__code__
    post_prompt = False
    completed_text: list[str] = []
    unexpected_activity = asyncio.Event()

    def prohibit(detail: dict[str, Any]) -> None:
        report["unexpected"].append(detail)
        unexpected_activity.set()

    def refuse_launch(reason: str) -> NoReturn:
        nonlocal spawn_failure
        spawn_failure = reason
        report["launch_budget_failure"] = reason
        raise RuntimeError(reason)

    class VersionReader:
        def __init__(self, reader: Any, evidence: dict[str, Any]) -> None:
            self.reader = reader
            self.evidence = evidence
            self.output = b""

        def __getattr__(self, name: str) -> Any:
            return getattr(self.reader, name)

        async def read(self, size: int = -1) -> bytes:
            value: bytes = await self.reader.read(size)
            self.output = (self.output + value)[:512]
            decoded = self.output.decode(errors="replace").strip()
            match = (
                re.match(r"([0-9]+\.[0-9]+\.[0-9]+)", decoded)
                if self.evidence["arguments"] == ["-v"]
                else re.fullmatch(r"(\S+) \(Claude Code\)", decoded)
            )
            self.evidence["version"] = match[1] if match else None
            return value

    async def spawn(*arguments: Any, **keywords: Any) -> asyncio.subprocess.Process:
        nonlocal spawn_count, spawn_failure
        if spawn_failure is not None:
            refuse_launch(
                "The seed launch budget remains closed after its first failure"
            )
        if str(arguments[0]) != spec["executable"]:
            refuse_launch("The seed proof refused an unexpected executable")
        native_arguments = list(arguments[1:])
        evidence_rows = report.get("launches", [])
        expected_probes = [["--version"], ["-v"]][: min(spawn_count, 2)]
        if (
            len(processes) != spawn_count
            or len(evidence_rows) != spawn_count
            or [row["arguments"] for row in report["version_probes"]] != expected_probes
        ):
            refuse_launch(
                "A prior seed spawn lacks its exact completed handle/evidence"
            )
        if spawn_count in (1, 2):
            prior = report["version_probes"][-1]
            code = processes[-1].returncode
            sdk_owned_termination = (
                spawn_count == 2
                and code == -signal.SIGTERM
                and prior.get("sdk_version_cleanup_signals") == ["SIGTERM"]
            )
            if (code != 0 and not sdk_owned_termination) or not prior["version"]:
                refuse_launch(
                    "The seed stopped after an unsuccessful normal version probe"
                )
            if (
                spawn_count == 2
                and prior["version"] != report["version_probes"][0]["version"]
            ):
                refuse_launch(
                    "The selected CLI version changed between its normal probes"
                )
        if spawn_count == 0:
            allowed = native_arguments == ["--version"]
        elif spawn_count == 1:
            allowed = native_arguments == ["-v"]
        elif spawn_count == 2:
            allowed = (
                "--output-format" in native_arguments
                and "stream-json" in native_arguments
                and "--input-format" in native_arguments
                and "--version" not in native_arguments
                and "-v" not in native_arguments
            )
        else:
            allowed = False
        if not allowed:
            refuse_launch("The seed proof refused an extra probe or SDK respawn")
        # Consume the budget before the native attempt, even if that spawn fails.
        spawn_count += 1
        child_environment = keywords.get("env") or os.environ
        if any(name.startswith("PLAN_") for name in child_environment):
            refuse_launch("A Panels identity reached the manual seed child")
        try:
            process = await native_spawn(*arguments, **keywords)
        except BaseException as error:
            spawn_failure = f"Native seed spawn failed: {type(error).__name__}"
            report["launch_budget_failure"] = spawn_failure
            raise
        processes.append(process)
        evidence: dict[str, Any] = {
            "arguments": native_arguments,
            "pid": process.pid,
            "closed": False,
            "exit_code": None,
            "version": None,
        }
        report.setdefault("launches", []).append(evidence)
        if native_arguments in (["--version"], ["-v"]):
            report["version_probes"].append(evidence)
            if process.stdout is not None:
                cast(Any, process).stdout = VersionReader(process.stdout, evidence)
        if native_arguments == ["-v"]:
            native_terminate = process.terminate

            def observe_sdk_terminate() -> None:
                # AnyIO Process.terminate forwards to this exact asyncio handle.
                # Match only the installed SDK check's finally, never harness fallback.
                caller = sys._getframe(1)
                sdk_cleanup = (
                    caller.f_code is sdk_version_check_code
                    or caller.f_back is not None
                    and caller.f_back.f_code is sdk_version_check_code
                )
                native_terminate()
                if sdk_cleanup:
                    evidence.setdefault("sdk_version_cleanup_signals", []).append(
                        "SIGTERM"
                    )

            cast(Any, process).terminate = observe_sdk_terminate
        return process

    async def deny_tool(tool: str, _input: Any, _context: Any) -> Any:
        prohibit({"kind": "tool_callback", "tool": tool})
        return PermissionResultDeny(
            message="Tools are disabled for this migration proof.", interrupt=True
        )

    class ObservedClient:
        def __init__(self, inner: Any) -> None:
            self.inner = inner

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

        async def query(self, value: Any) -> None:
            nonlocal post_prompt
            post_prompt = True
            await self.inner.query(value)

        async def receive_messages(self) -> Any:
            async for message in self.inner.receive_messages():
                name = type(message).__name__
                report["native_messages"].append(
                    {"type": name, "subtype": getattr(message, "subtype", None)}
                )
                if isinstance(message, HookEventMessage) or (
                    isinstance(message, SystemMessage)
                    and message.subtype.startswith("hook_")
                ):
                    prohibit({"kind": "hook", "type": name})
                    raise RuntimeError("The seed CLI emitted an unexpected hook")
                if isinstance(message, SystemMessage) and message.subtype == "init":
                    tools = message.data.get("tools")
                    report["startup_tools"] = tools
                    if tools != []:
                        prohibit(
                            {
                                "kind": "startup_tools",
                                "count": (
                                    len(tools) if isinstance(tools, list) else None
                                ),
                            }
                        )
                        raise RuntimeError(
                            "The seed CLI did not advertise an empty tool set"
                        )
                    model = message.data.get("model")
                    report["native_model"] = model if isinstance(model, str) else None
                    if not isinstance(model, str) or "haiku" not in model.lower():
                        prohibit({"kind": "unexpected_model"})
                        raise RuntimeError("The seed CLI did not report Haiku")
                if isinstance(message, AssistantMessage):
                    if any(
                        isinstance(block, ToolUseBlock) for block in message.content
                    ):
                        prohibit({"kind": "native_tool_call"})
                        raise RuntimeError(
                            "The seed CLI emitted an unexpected tool call"
                        )
                    if post_prompt and message.parent_tool_use_id is None:
                        completed_text.extend(
                            block.text
                            for block in message.content
                            if isinstance(block, TextBlock)
                        )
                yield message

    def client(options: Any) -> Any:
        options.tools = []
        options.setting_sources = []
        options.strict_mcp_config = True
        options.mcp_servers = {}
        options.permission_mode = "default"
        options.can_use_tool = deny_tool
        options.include_hook_events = True
        options.settings = json.dumps({"disableAllHooks": True})
        if any(name.startswith("PLAN_") for name in options.env):
            raise RuntimeError("The seed SDK options contain a Panels identity")
        return ObservedClient(claude_sdk_client(options))

    overrides = []
    if spec.get("claude_home"):
        overrides.append(("CLAUDE_CONFIG_DIR", spec["claude_home"]))
    if spec.get("scripted"):
        overrides += [("UNIT18_PART3_SCRIPTED", "1"), ("UNIT18_TRACE", spec["trace"])]
    factory = ClaudeAgentSdkBackendChildFactory(
        ClaudeAgentSdkChildLaunch(
            claude_executable=Path(spec["executable"]),
            environment_overrides=tuple(overrides),
        ),
        client_factory=client,
    )
    store = ConversationStore(spec["database"])
    tail = ConversationLiveTail()
    subscription = tail.subscribe(spec["conversation_id"])
    core = SqliteProcessConversationSystem(
        store=store,
        message_files=ConversationMessageFiles(spec["database"]),
        backend_child_factories={key: factory for key in ConversationBackendKey},
        live_tail=tail,
    )
    turn_task: asyncio.Task[None] | None = None
    unexpected_task: asyncio.Task[bool] | None = None

    async def deliver_and_finish() -> None:
        fate = await core.send(
            spec["conversation_id"],
            text_message_content(prompt),
            sender_label="Owner",
            sender_message_id="unit18-python-seed",
        )
        report["delivery_fate"] = delivery_fate_json(fate)
        if report["delivery_fate"] != {"fate": "started"}:
            raise RuntimeError("The Python seed did not start")
        while await core.is_running(spec["conversation_id"]):
            await asyncio.sleep(0.01)
        await core.wait_until_quiescent()

    with patch.object(asyncio, "create_subprocess_exec", spawn):
        try:
            await core.start_conversation(
                ConversationStartRequest(
                    conversation_id=spec["conversation_id"],
                    backend_key=ConversationBackendKey.claude,
                    model="haiku",
                    reasoning_effort="low",
                    workspace_folder=Path(spec["workspace"]),
                    workspace_path_policy=WorkspacePathPolicy.absolute,
                )
            )
            turn_task = asyncio.create_task(deliver_and_finish())
            unexpected_task = asyncio.create_task(unexpected_activity.wait())
            done, _pending = await asyncio.wait(
                {turn_task, unexpected_task},
                timeout=120,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if unexpected_task in done:
                report["stopped_on_unexpected_activity"] = True
                raise RuntimeError(
                    "The seed stopped immediately on unexpected native activity"
                )
            if turn_task not in done:
                raise TimeoutError("The Python seed turn did not end")
            await turn_task
            events = await store.read_events_after(spec["conversation_id"], 0)
            report["events"] = plain(events)
            report["usage"] = [
                plain(row.payload) for row in events if row.kind == "token_usage"
            ]
            endings = [
                row.payload
                for row in events
                if isinstance(row.payload, TurnEndedEventPayload)
            ]
            report["ending"] = plain(endings[-1]) if endings else None
            record = await store.read_conversation(spec["conversation_id"])
            report["cursor_after"] = record.vendor_session_cursor if record else None
            subscription.close()
            live = []
            async for item in subscription:
                if isinstance(item, AgentMessageDeltaFrame):
                    live.append(item.text_delta)
            report["live_text"] = "".join(live)
            report["new_native_text"] = "".join(completed_text)
            report["reply"] = report["live_text"] or report["new_native_text"]
            report["success"] = bool(
                endings
                and str(endings[-1].ending) == "completed"
                and report["reply"].strip() == "STORED"
                and report["cursor_after"]
                and report["startup_tools"] == []
                and not report["unexpected"]
            )
            if not report["success"]:
                report["stop_reason"] = (
                    "Python seed did not meet its exact reply/session contract"
                )
        except Exception as error:
            report["stop_reason"] = str(error)
            report["error_class"] = type(error).__name__
        finally:
            for task in (turn_task, unexpected_task):
                if task is not None and not task.done():
                    task.cancel()
            subscription.close()
            try:
                await asyncio.wait_for(core.shutdown(), 20)
            except Exception as error:
                report["cleanup_errors"].append(type(error).__name__)
            for process in processes:
                if process.returncode is None:
                    report["cleanup_errors"].append("SDK left an owned process alive")
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        await process.wait()
                        continue
                    try:
                        await asyncio.wait_for(process.wait(), 5)
                    except TimeoutError:
                        process.kill()
                        await process.wait()
            for process, evidence in zip(
                processes, report.get("launches", []), strict=True
            ):
                evidence["closed"] = process.returncode is not None
                evidence["exit_code"] = process.returncode
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        *(
                            task
                            for task in (turn_task, unexpected_task)
                            if task is not None
                        ),
                        return_exceptions=True,
                    ),
                    5,
                )
            except TimeoutError:
                report["cleanup_errors"].append(
                    "Seed observation task did not settle after native cleanup"
                )
            try:
                record = await store.read_conversation(spec["conversation_id"])
                report["cursor_after"] = (
                    record.vendor_session_cursor if record else None
                )
            except Exception as error:
                report["cleanup_errors"].append(f"cursor read: {type(error).__name__}")
            report["cleanup"] = {
                "owned_processes": len(processes),
                "all_exited": all(
                    process.returncode is not None for process in processes
                ),
            }
    report["server_environment_unchanged"] = before_environment == dict(os.environ)
    report["success"] = bool(
        report["success"]
        and not report["cleanup_errors"]
        and report["server_environment_unchanged"]
    )
    return report


def run(
    root: Path, *, scripted: bool, executable: str, claude_home: str | None
) -> dict[str, Any]:
    if any(name.startswith("PLAN_") for name in os.environ):
        raise RuntimeError("Launch the manual driver with every PLAN_ variable removed")
    root = root.absolute()
    if scripted:
        expected = str(ROOT / "server/conversation/testing/claude/scripted_cli.ts")
        if executable != expected or claude_home is not None:
            raise RuntimeError(
                "The rehearsal permits only its exact fixture and synthetic homes"
            )
    elif not any(
        root.is_relative_to(ticket_root)
        for ticket_root in (
            Path("/home/vps/projects/panels/task-tmp/t_02cfxhf1"),
            Path("/home/vps/Deployments/Panels/current/data/files/tickets/t_02cfxhf1"),
        )
    ):
        raise RuntimeError("Real-provider state must stay inside this Ticket tree")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / ("scripted-attempt.json" if scripted else "real-attempt.json")
    # O_EXCL is the one-attempt latch. It precedes the first version probe or SDK child.
    with marker.open("x") as output:
        json.dump(
            {"scripted": scripted, "started_at": time.time(), "executable": executable},
            output,
        )
    environment = synthetic_environment(root)
    workspace = root / "workspace"
    workspace.mkdir(mode=0o700)
    database = root / "conversation.sqlite"
    if database.exists():
        raise RuntimeError("The manual proof requires a new scratch database")
    token = f"UNIT18_MEMORY_{secrets.token_hex(16)}"
    report: dict[str, Any] = {
        "scripted": scripted,
        "phases": [],
        "success": False,
        "billing_total": None,
        "cost_scope": (
            "Keep all three native totals separate. Seed/resume totals can overlap; "
            "no billing sum is inferred."
        ),
    }
    allowlist: list[dict[str, str]] = []
    base: dict[str, Any] = {
        "database": str(database),
        "workspace": str(workspace),
        "executable": executable,
        "scripted": scripted,
        "trace": str(root / "scripted-cli.jsonl"),
    }
    if claude_home is not None:
        base["claude_home"] = claude_home
    try:
        with patch.dict(os.environ, environment, clear=True):
            from planner.core.db import connect, create_schema

            connection = connect(str(database))
            try:
                create_schema(connection)
                connection.commit()
            finally:
                connection.close()
        fresh = run_typescript(
            {
                **base,
                "mode": "fresh",
                "conversation_id": "unit18-real-fresh",
                "prompt": FRESH_PROMPT,
            },
            environment,
            root,
        )
        report["phases"].append(fresh)
        if fresh.get("cursor_after"):
            allowlist.append(
                {
                    "creator": "typescript",
                    "conversation_id": "unit18-real-fresh",
                    "cursor": fresh["cursor_after"],
                }
            )
        (root / "created-session-allowlist.json").write_text(
            json.dumps(allowlist, indent=2)
        )
        if (
            not fresh.get("success")
            or fresh.get("reply", "").strip() != "UNIT18_FRESH_OK"
        ):
            raise RuntimeError("The fresh TS turn failed. No seed or resume ran.")
        with patch.dict(os.environ, environment, clear=True):
            seed = asyncio.run(
                asyncio.wait_for(
                    seed_python(
                        {**base, "conversation_id": "unit18-real-python"},
                        f"Remember this exact memory token for the next turn: {token}. "
                        "Reply exactly STORED. Do not use tools.",
                    ),
                    180,
                )
            )
        report["phases"].append(seed)
        if seed.get("cursor_after"):
            allowlist.append(
                {
                    "creator": "python",
                    "conversation_id": "unit18-real-python",
                    "cursor": seed["cursor_after"],
                }
            )
        (root / "created-session-allowlist.json").write_text(
            json.dumps(allowlist, indent=2)
        )
        if not seed.get("success"):
            raise RuntimeError("The Python seed failed. No resume ran.")
        cursor = seed["cursor_after"]
        if not cursor or cursor == fresh["cursor_after"]:
            raise RuntimeError(
                "The two new core sessions lack distinct captured cursors"
            )
        resume_spec = {
            **base,
            "mode": "resume",
            "conversation_id": "unit18-real-python",
            "prompt": RECALL_PROMPT,
            "expected_cursor": cursor,
        }
        assert token not in resume_spec["prompt"]
        resumed = run_typescript(resume_spec, environment, root)
        report["phases"].append(resumed)
        if not resumed.get("success") or resumed.get("reply", "").strip() != token:
            raise RuntimeError("The TS resume did not recall the Python seed token")
        if (
            resumed.get("cursor_before") != cursor
            or resumed.get("cursor_after") != cursor
        ):
            raise RuntimeError("The TS resume substituted the allowlisted core cursor")
        probes = [
            [item["arguments"] for item in phase.get("version_probes", [])]
            for phase in report["phases"]
        ]
        if probes != [[["--version"]], [["--version"], ["-v"]], [["--version"]]]:
            raise RuntimeError(
                "The manual proof did not use exactly its four normal version probes"
            )
        report["normal_version_probes"] = probes
        report["logical_sends"] = 3
        report["expected_token"] = token
        report["success"] = True
    except Exception as error:
        report["stop_reason"] = str(error)
        report["error_class"] = type(error).__name__
        report["pending"] = True
    finally:
        report["native_cost_values"] = [
            {"mode": phase.get("mode"), "usage": phase.get("usage")}
            for phase in report["phases"]
        ]
        (root / "part3-report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--scripted", action="store_true")
    parser.add_argument("--executable")
    parser.add_argument("--claude-home")
    arguments = parser.parse_args()
    if arguments.scripted:
        executable = str(ROOT / "server/conversation/testing/claude/scripted_cli.ts")
        if arguments.executable or arguments.claude_home:
            parser.error(
                "Scripted rehearsal selects only its fixture and synthetic provider homes"
            )
    else:
        if not arguments.executable or not Path(arguments.executable).is_absolute():
            parser.error(
                "Real mode requires --executable from the approved production selection"
            )
        if not arguments.claude_home or not Path(arguments.claude_home).is_absolute():
            parser.error(
                "Real mode requires the approved absolute child-only --claude-home"
            )
        executable = arguments.executable
    report = run(
        arguments.root,
        scripted=arguments.scripted,
        executable=executable,
        claude_home=arguments.claude_home,
    )
    print(
        json.dumps(
            {
                "success": report["success"],
                "report": str(arguments.root / "part3-report.json"),
                "stop_reason": report.get("stop_reason"),
            }
        )
    )
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())

"""Drive both real Claude SDK adapters behind both real conversation cores.

Only the owned Node evaluator and its scripted SDK CLI children start. No service
manager, real Claude executable, provider session, or HTTP/browser host is used.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from planner.conversation.api import delivery_fate_json
from planner.conversation.backends.claude_agent_sdk import (
    ClaudeAgentSdkBackendChildFactory,
    ClaudeAgentSdkChildLaunch,
)
from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
    PromptDeliveryMode,
    WorkspacePathPolicy,
)
from planner.conversation.events import AgentMessageDeltaFrame, UserInputAnswer
from planner.conversation.live_tail import ConversationLiveTail
from planner.conversation.message_content import text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.db import connect, create_schema

ROOT = Path(__file__).resolve().parents[3]
TABLES = (
    "conversations",
    "conversation_events",
    "conversation_lifecycle_facts",
    "conversation_view_state",
    "conversation_view_rows",
    "conversation_view_turns",
    "conversation_view_groups",
    "conversation_view_lookups",
)
ROW_ORDERS = {
    "conversation_lifecycle_facts": (
        "conversation_id,turn_number,kind,operation,identity"
    ),
    "conversation_view_lookups": (
        "conversation_id,lookup_kind,source_sequence,lookup_id"
    ),
}
RANDOM_ID = re.compile(
    r"(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}"
    r"-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])"
)


def environment(root: Path) -> dict[str, str]:
    home = root / "home"
    home.mkdir()
    runtime = root / "disconnected-runtime"
    runtime.mkdir(mode=0o700)
    return {
        "PATH": os.environ.get("PATH", "/bin:/usr/bin"),
        "LANG": "C.UTF-8",
        "HOME": str(home),
        "CODEX_HOME": str(home / "codex"),
        "CLAUDE_CONFIG_DIR": str(home / "claude"),
        "HERMES_HOME": str(home / "hermes"),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "XDG_DATA_HOME": str(home / "data"),
        "XDG_RUNTIME_DIR": str(runtime),
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/nonexistent-bus",
        "TMPDIR": str(root),
    }


def snapshot(path: str) -> dict[str, list[dict[str, Any]]]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        return {
            table: [
                dict(row)
                for row in connection.execute(
                    f"SELECT * FROM {table} ORDER BY {ROW_ORDERS.get(table, '1,2')}"
                )
            ]
            for table in TABLES
        }


async def python_exercise(spec: dict[str, str]) -> dict[str, Any]:
    store = ConversationStore(spec["database"], integer_now=lambda: 100)
    factory = ClaudeAgentSdkBackendChildFactory(
        ClaudeAgentSdkChildLaunch(
            claude_executable=Path(spec["executable"]),
            environment_overrides=(("UNIT18_TRACE", spec["trace"]),),
        )
    )
    live_tail = ConversationLiveTail()
    subscription = live_tail.subscribe("unit18-claude")
    plain_live_text: list[dict[str, str]] = []
    core = SqliteProcessConversationSystem(
        store=store,
        backend_child_factories={key: factory for key in ConversationBackendKey},
        message_files=ConversationMessageFiles(spec["database"]),
        unix_time_now=lambda: 100,
        live_tail=live_tail,
    )
    identity = "unit18-claude"
    checkpoints: list[dict[str, Any]] = []
    malformed_cancel_seconds = 0.0
    malformed_assistant_seconds = 0.0
    sender = 0

    async def send(
        scenario: str, mode: PromptDeliveryMode = PromptDeliveryMode.queue
    ) -> dict[str, Any]:
        nonlocal sender
        sender += 1
        return delivery_fate_json(
            await core.send(
                identity,
                text_message_content(f"[CASE:{scenario}]"),
                sender_label="Owner",
                sender_message_id=f"sender-{sender}",
                mode=mode,
            )
        )

    async def wait(condition: Any, description: str) -> None:
        deadline = time.monotonic() + 15
        while not await condition():
            if time.monotonic() > deadline:
                raise AssertionError(f"Timed out: {description}")
            await asyncio.sleep(0.005)
        await core.wait_until_quiescent()

    async def ended(count: int) -> None:
        async def complete() -> bool:
            rows = snapshot(spec["database"])["conversation_events"]
            return sum(
                row["kind"] == "turn_ended" for row in rows
            ) >= count and not await core.is_running(identity)

        await wait(complete, f"turn {count} ending")

    async def capture(name: str, fates: list[Any]) -> None:
        await core.wait_until_quiescent()
        checkpoints.append(
            {
                "name": name,
                "fates": fates,
                "rows": snapshot(spec["database"]),
                "running": await core.is_running(identity),
            }
        )

    try:
        await core.start_conversation(
            ConversationStartRequest(
                conversation_id=identity,
                backend_key=ConversationBackendKey.claude,
                model="haiku",
                reasoning_effort="low",
                workspace_folder=Path(spec["workspace"]),
                workspace_path_policy=WorkspacePathPolicy.absolute,
                role_materials=ConversationRoleMaterials(
                    "Synthetic owner", (("PLAN_ACTOR", "owner"),)
                ),
            )
        )
        plain = await send("plain")
        await ended(1)
        subscription.close()
        async for item in subscription:
            if isinstance(item, AgentMessageDeltaFrame):
                plain_live_text.append(
                    {"type": "agent_message_delta", "text_delta": item.text_delta}
                )
        await capture("plain", [plain])
        base = await send("steer-base")
        steer = await send("steer", PromptDeliveryMode.steer)
        await ended(2)
        await capture("steer", [base, steer])
        base = await send("queue-base")
        queued = await send("queued", PromptDeliveryMode.steer)
        await core.interrupt(identity)
        await ended(3)
        await capture("queued-and-cancelled", [base, queued])
        permission = await send("permission")
        await wait(lambda: core.has_pending_permission_ask(identity), "permission ask")
        ask = next(iter(await core.pending_permission_ask_ids(identity)))
        answer = await core.answer_permission_ask(identity, ask, "approve_once")
        await ended(4)
        await capture("permission", [permission, {"answer_fate": str(answer)}])
        question = await send("question")
        await wait(lambda: core.has_pending_user_input(identity), "question")
        request = next(iter(await core.pending_user_input_request_ids(identity)))
        answer = await core.answer_user_input(
            identity, request, (UserInputAnswer("Choose a color?", ("Blue",)),)
        )
        await ended(5)
        await capture("question", [question, {"answer_fate": str(answer)}])
        interruption = await send("interrupt-base")
        await core.interrupt(identity)
        await ended(6)
        await capture("interrupt", [interruption])
        malformed_started = time.perf_counter()
        malformed = await send("malformed-cancel-base")
        await core.interrupt(identity)
        await ended(7)
        await capture("malformed-cancel-receipt", [malformed])
        malformed_cancel_seconds = time.perf_counter() - malformed_started
        resume = await send("resume-must-fail")
        await capture("failed-resume", [resume])
        malformed_started = time.perf_counter()
        malformed_identity = "unit18-malformed"
        await core.start_conversation(
            ConversationStartRequest(
                conversation_id=malformed_identity,
                backend_key=ConversationBackendKey.claude,
                model="haiku",
                reasoning_effort="low",
                workspace_folder=Path(spec["workspace"]),
                workspace_path_policy=WorkspacePathPolicy.absolute,
                role_materials=ConversationRoleMaterials(
                    "Synthetic owner", (("PLAN_ACTOR", "owner"),)
                ),
            )
        )
        malformed_assistant = delivery_fate_json(
            await core.send(
                malformed_identity,
                text_message_content("[CASE:malformed-assistant]"),
                sender_label="Owner",
                sender_message_id="malformed-input",
            )
        )

        async def malformed_ended() -> bool:
            endings = [
                row
                for row in snapshot(spec["database"])["conversation_events"]
                if row["conversation_id"] == malformed_identity
                and row["kind"] == "turn_ended"
            ]
            return len(endings) == 1 and not await core.is_running(malformed_identity)

        await wait(malformed_ended, "malformed assistant fails its turn")
        await capture("malformed-assistant", [malformed_assistant])
        malformed_assistant_seconds = time.perf_counter() - malformed_started
    finally:
        subscription.close()
        await core.shutdown()
    return {
        "checkpoints": checkpoints,
        "plain_live_text": plain_live_text,
        "malformed_cancel_seconds": malformed_cancel_seconds,
        "malformed_assistant_seconds": malformed_assistant_seconds,
        "trace": read_trace(spec["trace"]),
    }


def read_trace(path: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def normalize(value: Any) -> Any:
    """Map known UUID identities, never arbitrary hex content or digest fields."""
    identities: dict[str, str] = {}
    control_sources: dict[tuple[str, int, str], str] = {}
    control_operations = {"steer", "cancel", "permission_answer", "user_input_answer"}
    identity_fields = {
        "vendor_session_cursor",
        "provider_identity",
        "identity",
        "uuid",
        "session",
        "session_id",
        "ask_id",
        "request_id",
        "answer_id",
        "held_prompt_id",
        "cancelled",
        "still_queued",
    }
    json_fields = {"payload", "identity_environment_variables", "composer_catalog"}

    def collect(item: Any, field: str = "") -> None:
        if isinstance(item, dict):
            if (
                field == "conversation_lifecycle_facts"
                and item.get("kind") == "attempt"
                and item.get("operation") in control_operations
            ):
                raw_identity = item["identity"]
                assert re.fullmatch(r"[0-9a-f]{32}", raw_identity)
                control_key = (
                    item["conversation_id"],
                    item["turn_number"],
                    item["operation"],
                )
                assert (
                    control_sources.setdefault(control_key, raw_identity)
                    == raw_identity
                )
                identities[raw_identity] = "control:" + ":".join(map(str, control_key))
            for key in sorted(item):
                raw = item[key]
                if key in json_fields and isinstance(raw, str):
                    raw = json.loads(raw)
                collect(raw, key)
        elif isinstance(item, list):
            for child in item:
                collect(child, field)
        elif isinstance(item, str) and field in identity_fields:
            for match in RANDOM_ID.finditer(item):
                identities.setdefault(match[0], f"generated-{len(identities) + 1}")

    collect(value)

    def walk(item: Any, field: str = "") -> Any:
        if isinstance(item, dict):
            result = {}
            for key in sorted(item):
                raw = item[key]
                if key in json_fields and isinstance(raw, str):
                    raw = json.loads(raw)
                result[key] = walk(raw, key)
            return result
        if isinstance(item, list):
            return [walk(child, field) for child in item]
        if isinstance(item, str):
            if "hash" in field or "digest" in field:
                return item
            for original, replacement in identities.items():
                item = item.replace(original, replacement)
            return item
        return item

    return walk(value)


def comparison(result: dict[str, Any], language: str) -> dict[str, Any]:
    kinds = {"prompt", "lifecycle", "interrupt", "tool-answer", "resume-refused"}
    native_observations = []
    for row in result["trace"]:
        if row["kind"] not in kinds:
            continue
        if row["kind"] == "tool-answer":
            response = dict(row["response"])
            if language == "typescript":
                assert response.pop("toolUseID") == row["tool"]
            else:
                assert "toolUseID" not in response
            row = {**row, "response": response}
        native_observations.append(row)
    return cast(
        dict[str, Any],
        normalize(
            {
                "checkpoints": result["checkpoints"],
                "plain_live_text": result["plain_live_text"],
                "native_observations": native_observations,
            }
        ),
    )


def launch_environments(
    python: dict[str, Any], typescript: dict[str, Any]
) -> dict[str, Any]:
    py = [row["environment"] for row in python["trace"] if row["kind"] == "launch"]
    ts = [row["environment"] for row in typescript["trace"] if row["kind"] == "launch"]
    python_shell = ROOT / "src/planner/conversation/tool_shell_startup.bash"
    typescript_shell = ROOT / "server/conversation/tool_shell_startup.bash"
    # Manager-approved exact source-location rule. Retain this drift assertion
    # throughout migration while the Python asset exists. No pattern/wildcard.
    assert python_shell.read_bytes() == typescript_shell.read_bytes()
    assert len(py) == len(ts) == 3
    from claude_agent_sdk._version import __version__ as python_sdk_version

    adjusted = []
    for left, right in zip(py, ts, strict=True):
        assert left["BASH_ENV"] == str(python_shell)
        assert right["BASH_ENV"] == str(typescript_shell)
        assert left["CLAUDE_CODE_ENTRYPOINT"] == "sdk-py"
        assert right["CLAUDE_CODE_ENTRYPOINT"] == "sdk-ts"
        assert left["CLAUDE_AGENT_SDK_VERSION"] == python_sdk_version
        assert right["CLAUDE_AGENT_SDK_VERSION"] == "0.3.316"
        assert "PLAN_TICKET_ID" not in left and "PLAN_TICKET_ID" not in right
        allowed = {"BASH_ENV", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_AGENT_SDK_VERSION"}
        left_rest = {key: value for key, value in left.items() if key not in allowed}
        right_rest = {key: value for key, value in right.items() if key not in allowed}
        assert left_rest == right_rest
        adjusted.append({"python": left, "typescript": right})
    return {
        "launches": adjusted,
        "shell_bytes_equal": True,
        "other_environment_values_equal": True,
    }


def test_unit18_adapter_real_core_parity(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Protect native admission, cancellation, asks, and real-core persisted outcomes."""
    started = time.perf_counter()
    selected_environment = environment(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executable = ROOT / "server/conversation/testing/claude/scripted_cli.ts"
    # The file is a checked-in executable fixture. No provider executable is selected.
    assert os.access(executable, os.X_OK)
    trace = tmp_path / "scripted-trace.jsonl"
    py_db, ts_db = tmp_path / "python.sqlite", tmp_path / "typescript.sqlite"
    with patch.dict(os.environ, selected_environment, clear=True):
        connection = connect(str(py_db))
        try:
            create_schema(connection)
            connection.commit()
            with sqlite3.connect(ts_db) as target:
                connection.backup(target)
        finally:
            connection.close()
        spec = {
            "database": str(py_db),
            "workspace": str(workspace),
            "executable": str(executable),
            "trace": str(trace),
        }
        python = asyncio.run(asyncio.wait_for(python_exercise(spec), 90))
    trace.unlink()
    spec["database"] = str(ts_db)
    input_path = tmp_path / "part2-input.json"
    input_path.write_text(json.dumps(spec))
    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {readFileSync} from 'node:fs'; import {pathToFileURL} from 'node:url'; "
            "const {run_part2_parity}=await import(pathToFileURL(process.argv[2]).href); "
            "process.stdout.write(JSON.stringify(await run_part2_parity("
            "JSON.parse(readFileSync(process.argv[1],'utf8')))));",
            str(input_path),
            str(ROOT / "server/conversation/testing/claude/part2_parity.ts"),
        ],
        cwd=ROOT,
        env=selected_environment,
        text=True,
        capture_output=True,
        timeout=90,
        check=True,
    )
    typescript = cast(dict[str, Any], json.loads(completed.stdout))
    expected = comparison(python, "python")
    actual = comparison(typescript, "typescript")
    report = {
        "python": python,
        "typescript": typescript,
        "compared_python": expected,
        "compared_typescript": actual,
        "seconds": time.perf_counter() - started,
        "extra_connected_case_seconds": {
            "python_malformed_cancel": python["malformed_cancel_seconds"],
            "typescript_malformed_cancel": typescript["malformed_cancel_seconds"],
            "python_malformed_assistant": python["malformed_assistant_seconds"],
            "typescript_malformed_assistant": typescript["malformed_assistant_seconds"],
        },
        "compared": [
            "seven scenarios through both real SDK adapters and cores",
            "ordered events",
            "lifecycle facts",
            "all five populated projection tables",
            "delivery fates",
            "native prompt/lifecycle/cancel/question/permission observations",
            "plain text delta through Python live-tail subscription and TS publish_frame seam",
        ],
        "ts_only": [
            "TS SDK option wiring and public process spawn hook; Python uses its native transport",
            "TS SDK adds toolUseID equal to the requested tool ID; Python omits it. "
            "All other answer fields compare exactly.",
        ],
        "identity_rule": (
            "Hyphenated UUIDs from explicit identity fields map bijectively; "
            "known references retain correlation. Hash/digest fields and "
            "bare hexadecimal content compare literally. Only lifecycle attempt "
            "control identities map by conversation, turn, and operation."
        ),
        "not_proven": [
            "real provider turn or resume",
            "systemd containment (part 1 owns policy/byte proof)",
            "browser or HTTP routes",
        ],
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / "unit18-adapter-real-core.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
    assert actual == expected
    environments = launch_environments(python, typescript)
    for result in (python, typescript):
        assert result["plain_live_text"] == [
            {"type": "agent_message_delta", "text_delta": "plain live text"}
        ]
        failed_resume = next(
            item for item in result["checkpoints"] if item["name"] == "failed-resume"
        )
        assert failed_resume["fates"] == [
            {"fate": "refused", "refusal_reason": "session_did_not_load"}
        ]
        assert sum(row["kind"] == "resume-refused" for row in result["trace"]) == 1
        assert not any(
            row["kind"] == "prompt" and row["scenario"] == "resume-must-fail"
            for row in result["trace"]
        )
        launches = [row["pid"] for row in result["trace"] if row["kind"] == "launch"]
        exits = [row["pid"] for row in result["trace"] if row["kind"] == "exit"]
        assert sorted(launches) == sorted(exits)
        queued = next(
            row["uuid"]
            for row in result["trace"]
            if row["kind"] == "prompt" and row["scenario"] == "queued"
        )
        assert not any(
            row["kind"] == "lifecycle"
            and row["uuid"] == queued
            and row["state"] == "started"
            for row in result["trace"]
        )
        assert any(
            row["kind"] == "interrupt"
            and row["cancel_queued"]
            and queued in row["cancelled"]
            and row["still_queued"] == []
            for row in result["trace"]
        )
        malformed = next(
            item
            for item in result["checkpoints"]
            if item["name"] == "malformed-cancel-receipt"
        )
        cancel_facts = [
            row
            for row in malformed["rows"]["conversation_lifecycle_facts"]
            if row["turn_number"] == 7 and row["operation"] == "cancel"
        ]
        assert len(cancel_facts) == 1 and cancel_facts[0]["status"] == "unknown"
        malformed_assistant = result["checkpoints"][-1]["rows"]
        conversation = next(
            row
            for row in malformed_assistant["conversations"]
            if row["conversation_id"] == "unit18-malformed"
        )
        assert (
            conversation["vendor_session_cursor"]
            != "ffffffff-ffff-4fff-8fff-ffffffffffff"
        )
        assert not any(
            row["conversation_id"] == "unit18-malformed"
            and row["kind"] == "agent_message"
            for row in malformed_assistant["conversation_events"]
        )
    if destination:
        (Path(destination) / "unit18-adapter-launch-environments.json").write_text(
            json.dumps(environments, indent=2) + "\n"
        )

"""One shared Codex executable behind both real cores, adapters and SQLite records.

This connected boundary proves native RPC-to-record behavior that the generic scripted
child cannot prove. No HTTP host, browser, service manager, or real provider starts.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from planner.conversation.api import delivery_fate_json
from planner.conversation import backend_state
from planner.conversation.backend_state import DurableCodexLoginRejection
from planner.conversation.backends.codex_app_server import adapter as codex_adapter
from planner.conversation.backends.codex_app_server.adapter import (
    CodexAppServerBackendChildFactory,
    CodexChildLaunch,
)
from planner.conversation.backends.codex_app_server.login import read_stored_codex_login
from planner.conversation.contracts import (
    ConversationBackendKey, ConversationRoleMaterials, ConversationStartRequest,
    PromptDeliveryMode, WorkspacePathPolicy,
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
    "conversations", "conversation_events", "conversation_lifecycle_facts",
    "conversation_view_state", "conversation_view_rows", "conversation_view_turns",
    "conversation_view_groups", "conversation_view_lookups", "codex_login_rejection",
)
ROW_ORDERS = {
    "conversation_lifecycle_facts": "conversation_id,turn_number,kind,operation,identity",
    "conversation_view_lookups": "conversation_id,lookup_kind,source_sequence,lookup_id",
}


def environment(root: Path) -> dict[str, str]:
    home = root / "home"
    home.mkdir()
    for name in ("codex", "claude", "hermes"):
        (home / name).mkdir()
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8",
        "HOME": str(home), "CODEX_HOME": str(home / "codex"),
        "CLAUDE_CONFIG_DIR": str(home / "claude"), "HERMES_HOME": str(home / "hermes"),
        "XDG_CONFIG_HOME": str(home / "config"), "XDG_CACHE_HOME": str(home / "cache"),
        "XDG_DATA_HOME": str(home / "data"), "TMPDIR": str(root),
    }


def write_login(home: str) -> None:
    (Path(home) / "auth.json").write_text(json.dumps({
        "tokens": {"account_id": "synthetic-account"}, "last_refresh": "old-generation",
    }))


def snapshot(path: str) -> dict[str, list[dict[str, Any]]]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        return {table: [dict(row) for row in connection.execute(
            f"SELECT * FROM {table} ORDER BY {ROW_ORDERS.get(table, '1,2')}"
        )] for table in TABLES}


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> FixedDatetime:
        return cls.fromtimestamp(100, tz or UTC)


@contextmanager
def owned_launch_boundary(executable: str) -> Any:
    original = asyncio.create_subprocess_exec

    async def guarded(*arguments: Any, **keywords: Any) -> Any:
        if list(arguments) not in ([executable, "--version"], [executable, "app-server"]):
            raise AssertionError(f"Unexpected child: {arguments}")
        return await original(*arguments, **keywords)

    with patch.object(asyncio, "create_subprocess_exec", guarded):
        yield


async def python_exercise(spec: dict[str, str]) -> dict[str, Any]:
    store = ConversationStore(spec["database"], integer_now=lambda: 100)
    rejection = DurableCodexLoginRejection(spec["database"])
    factory = CodexAppServerBackendChildFactory(
        CodexChildLaunch(argv=(spec["executable"], "app-server"), environment_overrides=(
            ("UNIT17_TRACE", spec["trace"]), ("UNIT17_CORE_PROOF", "1"),
        )), remembered_login_rejection=rejection,
    )
    identity = "unit17-codex"
    tail = ConversationLiveTail()
    subscription = tail.subscribe(identity)
    core = SqliteProcessConversationSystem(
        store=store, backend_child_factories={key: factory for key in ConversationBackendKey},
        message_files=ConversationMessageFiles(spec["database"]), unix_time_now=lambda: 100,
        live_tail=tail,
    )
    checkpoints: list[dict[str, Any]] = []
    live_text: list[dict[str, str]] = []
    sender = 0
    backlog_order: list[str] = []

    async def send(scenario: str, mode: PromptDeliveryMode = PromptDeliveryMode.queue,
                   native: bool = False) -> dict[str, Any]:
        nonlocal sender
        sender += 1
        return delivery_fate_json(await core.send(
            identity, text_message_content(scenario if native else f"[CASE:{scenario}]"),
            sender_label="Owner", sender_message_id=f"sender-{sender}", mode=mode,
        ))

    async def wait(condition: Any, description: str) -> None:
        deadline = time.monotonic() + 10
        while not await condition():
            if time.monotonic() > deadline:
                raise AssertionError(f"Timed out: {description}")
            await asyncio.sleep(0.005)
        await core.wait_until_quiescent()

    async def ended(count: int) -> None:
        async def complete() -> bool:
            return (sum(row["kind"] == "turn_ended" for row in
                        snapshot(spec["database"])["conversation_events"]) >= count
                    and not await core.is_running(identity))
        await wait(complete, f"turn {count} ending")

    async def capture(name: str, fates: list[Any]) -> None:
        await core.wait_until_quiescent()
        login = read_stored_codex_login()
        checkpoints.append({
            "name": name, "fates": fates, "rows": snapshot(spec["database"]),
            "running": await core.is_running(identity), "login": {
                "generation": asdict(login.generation) if login.generation else None,
                "rejected": rejection.applies_to(login.generation),
            },
        })

    original_notification = codex_adapter._CodexServerMessages.on_notification
    async def delayed_notification(handler: Any, method: str, notification: Any) -> None:
        if method == "skills/changed":
            backlog_order.append("handler-start")
            await asyncio.sleep(0.02)
            backlog_order.append("handler-end")
        await original_notification(handler, method, notification)
    notification_patch = patch.object(codex_adapter._CodexServerMessages, "on_notification", delayed_notification)
    notification_patch.start()
    try:
        await core.start_conversation(ConversationStartRequest(
            conversation_id=identity, backend_key=ConversationBackendKey.codex,
            model="gpt-6-luna", reasoning_effort="low", workspace_folder=Path(spec["workspace"]),
            workspace_path_policy=WorkspacePathPolicy.absolute,
            role_materials=ConversationRoleMaterials("Synthetic owner", (("PLAN_ACTOR", "owner"),)),
        ))
        async def catalog_read() -> bool:
            return "app/installed" in Path(spec["trace"]).read_text()
        await wait(catalog_read, "initial catalog reads")
        count = 0
        plain = await send("plain")
        count += 1
        await ended(count)
        await capture("plain", [plain])
        base = await send("steer-base")
        steer = await send("steer", PromptDeliveryMode.steer)
        count += 1
        await ended(count)
        await capture("steer", [base, steer])
        base = await send("queue-base")
        queued = await send("queued")
        held = await core.held_prompts(identity)
        assert len(held) == 1
        discarded = await core.discard_held_prompt(identity, held[0].held_prompt_id)
        await core.interrupt(identity)
        count += 1
        await ended(count)
        await capture("queued-and-cancelled", [base, queued, {"discarded": discarded}])
        permission = await send("permission")
        await wait(lambda: core.has_pending_permission_ask(identity), "permission ask")
        ask = next(iter(await core.pending_permission_ask_ids(identity)))
        answer = await core.answer_permission_ask(identity, ask, "approve_once")
        count += 1
        await ended(count)
        await capture("permission", [permission, {"answer_fate": str(answer)}])
        question = await send("question")
        await wait(lambda: core.has_pending_user_input(identity), "question")
        question_id = next(iter(await core.pending_user_input_request_ids(identity)))
        answer = await core.answer_user_input(identity, question_id, (UserInputAnswer("color", ("Blue",)),))
        count += 1
        await ended(count)
        await capture("question", [question, {"answer_fate": str(answer)}])
        interruption = await send("interrupt-base")
        await core.interrupt(identity)
        count += 1
        await ended(count)
        await capture("interrupt", [interruption])
        await core.kill(identity)
        resumed = await send("resume-success")
        count += 1
        await ended(count)
        await capture("successful-resume", [resumed])
        for scenario in ("auth-failure", "malformed", "auth-refresh"):
            fate = await send(scenario)
            count += 1
            await ended(count)
            await capture(scenario, [fate])
        for command in ("/compact", "/review", "/goal get"):
            fate = await send(command, native=True)
            count += 1
            await ended(count)
            await capture(command, [fate])
        uncertain = await send("rejected-after-output")
        backlog_order.append("receipt")
        await capture("rejected-after-output", [uncertain])
        await core.kill(identity)
        refused = await send("resume-must-fail")
        await capture("failed-resume", [refused])
    finally:
        subscription.close()
        async for frame in subscription:
            if isinstance(frame, AgentMessageDeltaFrame):
                live_text.append({"type": "agent_message_delta", "text_delta": frame.text_delta})
        await core.shutdown()
        notification_patch.stop()
    return {"checkpoints": checkpoints, "live_text": live_text, "backlog_order": backlog_order,
            "trace": read_trace(spec["trace"]), "children": read_trace(f"{spec['trace']}.children")}


def read_trace(path: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def normalize(value: Any) -> Any:
    """Map explicit generated identities bijectively, preserving content and hashes."""
    identities: dict[str, str] = {}
    control_keys: dict[tuple[Any, ...], str] = {}
    json_fields = {"payload", "identity_environment_variables", "composer_catalog"}
    identity_fields = {"identity", "ask_id", "request_id", "answer_id", "held_prompt_id"}

    def collect(item: Any, field: str = "") -> None:
        if isinstance(item, dict):
            if field == "conversation_lifecycle_facts" and item.get("kind") == "attempt" and item.get("operation") in {
                "steer", "cancel", "permission_answer", "user_input_answer",
            }:
                raw = item["identity"]
                assert re.fullmatch(r"[0-9a-f]{32}", raw)
                key = item["conversation_id"], item["turn_number"], item["operation"]
                assert control_keys.setdefault(key, raw) == raw
                identities[raw] = "control:" + ":".join(map(str, key))
            for key in sorted(item):
                raw = item[key]
                if key in json_fields and isinstance(raw, str):
                    raw = json.loads(raw)
                collect(raw, key)
        elif isinstance(item, list):
            for child in item:
                collect(child, field)
        elif isinstance(item, str) and field in identity_fields:
            for match in re.finditer(r"held_[0-9a-f]{32}|(?<![0-9a-f])[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?![0-9a-f])", item):
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
        if isinstance(item, str) and "hash" not in field and "digest" not in field:
            for original, replacement in identities.items():
                item = item.replace(original, replacement)
        return item
    return walk(value)


def test_unit17_adapter_real_core_parity(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """Protect RPC identity, certainty, interaction answers and persisted outcomes."""
    started = time.perf_counter()
    selected_environment = environment(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executable = tmp_path / "scripted_codex.ts"
    shutil.copy2(ROOT / "server/conversation/testing/codex/scripted_app_server.ts", executable)
    executable.chmod(0o700)
    trace = tmp_path / "adapter.jsonl"
    python_db, ts_db = tmp_path / "python.sqlite", tmp_path / "typescript.sqlite"
    with patch.dict(os.environ, selected_environment, clear=True):
        write_login(selected_environment["CODEX_HOME"])
        connection = connect(str(python_db))
        try:
            create_schema(connection)
            connection.execute("INSERT INTO conversation_runtime_epoch VALUES (1,?) ON CONFLICT(singleton) DO UPDATE SET epoch=excluded.epoch", ("unit17-runtime",))
            connection.commit()
            with sqlite3.connect(ts_db) as target:
                connection.backup(target)
        finally:
            connection.close()
        spec = {"database": str(python_db), "workspace": str(workspace),
                "executable": str(executable), "trace": str(trace)}
        with owned_launch_boundary(str(executable)), patch.object(backend_state, "datetime", FixedDatetime):
            python = asyncio.run(asyncio.wait_for(python_exercise(spec), timeout=90))
    for path in (trace, Path(f"{trace}.state.json"), Path(f"{trace}.children")):
        path.unlink()
    write_login(selected_environment["CODEX_HOME"])
    spec["database"] = str(ts_db)
    input_path = tmp_path / "adapter-input.json"
    input_path.write_text(json.dumps(spec))
    completed = subprocess.run([
        "node", "--input-type=module", "-e",
        "import {readFileSync} from 'node:fs'; import {pathToFileURL} from 'node:url'; "
        "const {run_adapter_parity}=await import(pathToFileURL(process.argv[2]).href); "
        "process.stdout.write(JSON.stringify(await run_adapter_parity("
        "JSON.parse(readFileSync(process.argv[1],'utf8')))));",
        str(input_path), str(ROOT / "server/conversation/testing/codex/adapter_parity.ts"),
    ], cwd=ROOT, env=selected_environment, text=True, capture_output=True, timeout=90, check=False)
    assert completed.returncode == 0, completed.stderr
    typescript = json.loads(completed.stdout)
    expected = normalize({key: python[key] for key in ("checkpoints", "live_text", "trace", "backlog_order")})
    actual = normalize({key: typescript[key] for key in ("checkpoints", "live_text", "trace", "backlog_order")})
    report = {"python": python, "typescript": typescript, "expected": expected, "actual": actual,
              "seconds": time.perf_counter() - started,
              "boundary": "shared JSONL process -> actual Codex adapter -> real SQLite core -> all five views",
              "not_proven": ["real provider", "browser/HTTP", "systemd execution", "provider bindings"],
              "identity_rule": "Only explicit random interaction/control/held identities map bijectively; content and hashes stay literal."}
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        output = Path(destination) / "unit-17-adapter-real-core.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n")
    assert actual == expected
    for result in (python, typescript):
        checkpoints = {item["name"]: item for item in result["checkpoints"]}
        assert checkpoints["plain"]["fates"] == [{"fate": "started"}]
        plain = checkpoints["plain"]["rows"]
        assert {"tool_call_started", "tool_call_finished", "plan_updated", "token_usage"} <= {row["kind"] for row in plain["conversation_events"]}
        assert all(plain[table] for table in TABLES if table.startswith("conversation_view_"))
        assert checkpoints["queued-and-cancelled"]["fates"][-1] == {"discarded": True}
        assert not any("[CASE:queued]" in json.dumps(frame) for frame in result["trace"])
        assert checkpoints["auth-failure"]["login"]["rejected"] is True
        assert checkpoints["malformed"]["login"]["rejected"] is False
        assert checkpoints["auth-refresh"]["login"] == {"generation": {"account_id": "synthetic-account", "last_refresh": "new-generation"}, "rejected": False}
        assert checkpoints["rejected-after-output"]["fates"] == [{"fate": "uncertain"}]
        assert result["backlog_order"] == ["handler-start", "handler-end", "receipt"]
        assert not any(frame["text_delta"] == "unadmitted output" for frame in result["live_text"])
        assert checkpoints["failed-resume"]["fates"] == [{"fate": "refused", "refusal_reason": "session_did_not_load"}]
        assert sum(frame.get("method") == "thread/start" for frame in result["trace"]) == 1
        assert sum(frame.get("method") == "thread/resume" for frame in result["trace"]) == 2
        assert {"type": "agent_message_delta", "text_delta": "codex-memory-token"} in result["live_text"]
        assert {"type": "agent_message_delta", "text_delta": "plain ☃ output"} in result["live_text"]
        launches = [row["pid"] for row in result["children"] if row["kind"] == "launch"]
        exits = [row["pid"] for row in result["children"] if row["kind"] == "exit"]
        assert sorted(launches) == sorted(exits)

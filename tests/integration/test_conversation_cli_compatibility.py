"""Opt-in qualification of a selected real CLI, without provider prompts.

Run each case with PANELS_QUALIFY_CODEX or PANELS_QUALIFY_CLAUDE set to an
absolute executable path. Run it separately for the minimum and newest releases.
These cases prove process startup, native control methods, and the wire values
Panels actually sends. Real model/steering exercises remain separate.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions, PermissionResultAllow, ToolPermissionContext
from jsonschema import Draft7Validator  # type: ignore[import-untyped]
from tests.support.conversation_codex_app_server_bench import _RecordingSink, _resolved_start

from planner.conversation.backends.claude_agent_sdk import claude_sdk_client
from planner.conversation.backends.codex_app_server import protocol
from planner.conversation.backends.codex_app_server.adapter import (
    CodexAppServerBackendChild,
    CodexChildLaunch,
    _CatalogInvocation,
    _wire,
)
from planner.conversation.backends.codex_app_server.client import (
    CodexAppServerClient,
    CodexRequestRejected,
)
from planner.conversation.backends.codex_app_server.login import RememberedCodexLoginRejection
from planner.conversation.backends.contracts import SessionLoadFailed, TurnToken
from planner.conversation.contracts import ComposerCatalogEntryKind, PromptDeliveryMode
from planner.conversation.message_content import MessageImage, text_message_content
from planner.conversation.message_files import ConversationMessageFiles


def _selected_cli(name: str) -> Path:
    value = os.environ.get(name)
    if value is None:
        pytest.skip(f"set {name} to an absolute CLI executable path")
    executable = Path(value)
    assert executable.is_absolute() and executable.is_file(), value
    return executable


def _private_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    for name in tuple(os.environ):
        if name.startswith(("ANTHROPIC_", "CLAUDE_", "OPENAI_", "CODEX_")) or name == "CLAUDECODE":
            monkeypatch.delenv(name)
    (home / "codex").mkdir()
    (home / "claude").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / "claude"))
    return home


def _version(executable: Path) -> str:
    return subprocess.run(
        [str(executable), "--version"], capture_output=True, text=True, check=True, timeout=15
    ).stdout.strip()


def test_selected_codex_accepts_panels_wire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _selected_cli("PANELS_QUALIFY_CODEX")
    home = _private_home(tmp_path, monkeypatch)
    schema_path = tmp_path / "schema"
    subprocess.run(
        [str(executable), "app-server", "generate-json-schema", "--out", str(schema_path)],
        capture_output=True, text=True, check=True, timeout=30,
    )
    dump = json.loads((schema_path / "codex_app_server_protocol.schemas.json").read_text())
    validator = Draft7Validator({
        "$ref": "#/definitions/ClientRequest", "definitions": dump["definitions"],
    })
    methods: set[str] = set()
    original = CodexAppServerClient.begin_request

    async def record_request(
        client: CodexAppServerClient, method: str, params: Mapping[str, Any] | None = None,
    ) -> asyncio.Future[dict[str, Any]]:
        # Validate concrete emitted data, not every variant in an upstream type.
        validator.validate({"id": 1, "method": method, "params": params})
        methods.add(method)
        return await original(client, method, params)

    monkeypatch.setattr(CodexAppServerClient, "begin_request", record_request)

    async def exercise() -> None:
        sink = _RecordingSink()
        start = _resolved_start(home)
        child = CodexAppServerBackendChild(
            launch=CodexChildLaunch(argv=(str(executable), "app-server")),
            resolved_start=start, event_sink=sink,
            message_files=ConversationMessageFiles(str(tmp_path / "planner.db")),
            remembered_login_rejection=RememberedCodexLoginRejection(),
        )
        try:
            await child.start(start, vendor_session_cursor=None)
            await child._catalog_coordinator.close()
            assert sink.vendor_session_cursor
            # An untouched thread has no persisted rollout. Check that resume
            # reaches that semantic refusal, rather than a method/parameter error.
            with pytest.raises(SessionLoadFailed, match="no rollout found"):
                await child._resume_thread(start, sink.vendor_session_cursor)
            skills = await child._read_skills(str(home))
            assert isinstance(skills.data, list)
            installed = await child._read_installed_apps(sink.vendor_session_cursor)
            assert isinstance(installed.apps, list)
            plugins = await child._read_plugins(str(home))
            assert isinstance(plugins.marketplaces, list)
            # The empty auth home has no connectors. The actual request must still
            # carry threadId; an auth refusal is different from a missing method.
            try:
                apps = await child._read_app_metadata([])
            except CodexRequestRejected as refused:
                assert refused.code not in {-32601, -32602}, str(refused)
                assert "auth" in str(refused).lower(), str(refused)
            else:
                assert apps.apps == []
            listed = protocol.ModelListResponse.model_validate(
                await child._client.request("model/list", {})
            )
            assert listed.data
            for number, command in enumerate((
                "/goal set CLI compatibility qualification", "/goal get", "/goal clear",
            ), 1):
                content = text_message_content(command)
                await child.write_prompt(
                    TurnToken("c", number), content, sender_label="Owner", sender_content=content,
                    mode=PromptDeliveryMode.queue, model_change=None, reasoning_effort_change=None,
                )
            assert sink.command_replies[0] == "Goal set: CLI compatibility qualification"
            assert "CLI compatibility qualification" in sink.command_replies[1]
            assert sink.command_replies[2] == "Goal cleared."
            # Validate ordinary turn values without a provider request. Semantic
            # execution and steering belong to the existing real-provider exercise.
            params = child._turn_start_parameters(
                thread_id=sink.vendor_session_cursor,
                content=text_message_content("qualification"), model=start.model,
            )
            validator.validate({"id": 1, "method": "turn/start", "params": _wire(params)})
            # Exercise each structured input Panels emits, without requiring the
            # CLI to support unrelated audio or URL variants from its full schema.
            for invocation in (
                _CatalogInvocation(ComposerCatalogEntryKind.skill, "qualification", "/skills/test"),
                _CatalogInvocation(ComposerCatalogEntryKind.app, "qualification", "app://test"),
            ):
                params = child._turn_start_parameters(
                    thread_id=sink.vendor_session_cursor,
                    content=text_message_content("qualification"), model=start.model,
                    invocation=invocation,
                )
                validator.validate({"id": 1, "method": "turn/start", "params": _wire(params)})
            kept = await child._message_files.keep("c", b"image fixture", media_type="image/png")
            params = child._turn_start_parameters(
                thread_id=sink.vendor_session_cursor,
                content=(MessageImage(kept.stored_file_id, "image/png"),), model=start.model,
            )
            validator.validate({"id": 1, "method": "turn/start", "params": _wire(params)})
            assert {
                "initialize", "thread/start", "thread/resume", "model/list", "skills/list",
                "app/installed", "app/read", "plugin/installed", "thread/goal/set",
                "thread/goal/get", "thread/goal/clear",
            } <= methods
        except Exception:
            print(child._client.standard_error_tail())
            raise
        finally:
            await child.stop()

    started = time.monotonic()
    asyncio.run(asyncio.wait_for(exercise(), 60))
    elapsed = time.monotonic() - started
    print(f"Qualified {_version(executable)} in {elapsed:.3f}s; no provider prompt")


def test_selected_claude_sdk_startup_and_queue_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _selected_cli("PANELS_QUALIFY_CLAUDE")
    home = _private_home(tmp_path, monkeypatch)

    async def exercise() -> None:
        async def allow(
            _tool: str, tool_input: dict[str, Any], _context: ToolPermissionContext,
        ) -> PermissionResultAllow:
            return PermissionResultAllow(updated_input=tool_input)

        client = claude_sdk_client(ClaudeAgentOptions(
            cli_path=str(executable), cwd=str(home),
            system_prompt={"type": "preset", "preset": "claude_code"},
            model="haiku", effort="high", permission_mode="default",
            session_id=str(uuid.uuid4()), include_partial_messages=True,
            max_buffer_size=4 * 1024 * 1024,
            can_use_tool=allow,
        ))

        async def drain() -> None:
            async for _ in client.receive_messages():
                pass

        reader: asyncio.Task[None] | None = None
        try:
            await client.connect()
            info = await client.get_server_info()
            assert info is not None
            assert isinstance(info.get("models"), list) and info["models"]
            assert isinstance(info.get("commands"), list)
            reader = asyncio.create_task(drain())
            # The production parser requires both arrays. A legacy empty control
            # response cannot masquerade as proof that the provider queue is empty.
            cancelled, still_queued = await client.interrupt_and_cancel_queued()
            assert cancelled == still_queued == frozenset()
        finally:
            if reader is not None:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            await client.disconnect()

    started = time.monotonic()
    asyncio.run(asyncio.wait_for(exercise(), 60))
    elapsed = time.monotonic() - started
    print(f"Qualified {_version(executable)} in {elapsed:.3f}s; no provider prompt")

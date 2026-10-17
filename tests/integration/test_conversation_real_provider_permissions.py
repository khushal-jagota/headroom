"""Opt-in real permission round trips, with no production-selectable ask posture.

Each case runs in its own pytest process when collecting durable release evidence.
The native mapping exists only in tests/support/real_permission_mapping.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from tests.support.real_permission_mapping import (
    assert_native_configuration,
    install_permission_mapping,
)

from planner.conversation.api import ConversationRuntime, build_conversation_runtime, router
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.production_backends import production_backend_child_factories
from planner.conversation.signals import read_conversation_signals
from planner.core.db import connect, create_schema

ROOT = Path(__file__).resolve().parents[2]
MODELS = {"codex": "gpt-5.6-luna", "claude": "haiku", "hermes": "openai-codex:gpt-6-astra"}
WAIT_SECONDS = 3.0


@pytest.mark.skipif(
    os.environ.get("PANELS_REAL_PERMISSION_TESTS") != "1",
    reason="set PANELS_REAL_PERMISSION_TESTS=1 for billable real-provider permission exercises",
)
@pytest.mark.parametrize("backend", list(ConversationBackendKey), ids=str)
@pytest.mark.parametrize("decision", ["allow", "deny"])
def test_real_permission_waits_for_explicit_panels_answer(
    backend: ConversationBackendKey, decision: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(asyncio.wait_for(_exercise(backend, decision, tmp_path, monkeypatch), 360))


async def _exercise(
    backend: ConversationBackendKey, decision: str, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = os.environ.get("PANELS_PERMISSION_EVIDENCE_ROOT")
    assert evidence and Path(evidence).is_absolute(), "an absolute evidence destination is required"
    evidence_root = Path(evidence)
    evidence_root.mkdir(parents=True, exist_ok=True)
    destination = evidence_root / f"{backend}-{decision}.json"
    import planner

    assert Path(planner.__file__).resolve().is_relative_to(ROOT)
    workspace = root / "workspace"
    workspace.mkdir()
    target = workspace / f"permission-{uuid.uuid4().hex}.txt"
    nonce = uuid.uuid4().hex
    _prepare_home(backend, root, monkeypatch)
    for key, value in {
        "PLAN_DB_PATH": root / "planner.db",
        "PLAN_LOGS_DIR": root / "logs",
        "PLAN_DISPATCHER_LOCK_PATH": root / "dispatcher.lock",
        "PLAN_SERVER_CONTROL_SOCKET": root / "server.sock",
    }.items():
        monkeypatch.setenv(key, str(value))
    observations: list[dict[str, Any]] = []
    install_permission_mapping(monkeypatch, backend, observations)
    receipts: dict[str, Any] = {
        "backend": str(backend),
        "decision": decision,
        "model": MODELS[str(backend)],
        "source_revision": _command(["git", "rev-parse", "HEAD"]),
        "source_status": _command(["git", "status", "--short"]),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "mapping_sha256": hashlib.sha256(
            (ROOT / "tests/support/real_permission_mapping.py").read_bytes()
        ).hexdigest(),
        "versions": _versions(backend),
        "effective_configuration": observations,
        "started_at": datetime.now(UTC).isoformat(),
        "passed": False,
    }

    def save() -> None:
        # Only these selected records are stored: never process environments, homes,
        # SDK initialization payloads, or arbitrary stderr that can contain credentials.
        destination.write_text(json.dumps(receipts, indent=2) + "\n")

    database = root / "planner.db"
    connection = connect(str(database), 5_000)
    try:
        create_schema(connection)
    finally:
        connection.close()
    conversation_id = f"permission-{backend}-{decision}-{uuid.uuid4().hex}"
    base = f"/api/conversation/conversations/{conversation_id}"
    try:
        async with _api(database) as (client, runtime):
            response = await client.post(
                "/api/conversation/conversations",
                json={
                    "conversation_id": conversation_id,
                    "backend_key": str(backend),
                    "model": MODELS[str(backend)],
                    "workspace_folder": str(workspace),
                    "access": "full",
                },
            )
            assert response.status_code == 201, response.text
            # Hermes file edits have a separate ACP ask, so ask for the native file-edit
            # tool. The other providers gate the shell command under their default policy.
            instruction = (
                f"Use your file editing tool (not shell) to create {target.name} "
                f"containing exactly {nonce} with no newline."
                if backend is ConversationBackendKey.hermes
                else f"Use your shell tool to run exactly: printf '%s' '{nonce}' > '{target.name}'."
            )
            response = await client.post(
                base + "/send",
                json={
                    "content": [
                        {
                            "piece": "text",
                            "text": instruction
                            + " Attempt this action exactly once. If permission is denied, stop; "
                            "do not retry or use any alternative tool. Do not ask in prose.",
                        }
                    ],
                    "sender_label": "permission-test",
                    "mode": "queue",
                    "sender_message_id": uuid.uuid4().hex,
                },
            )
            assert response.status_code == 200, response.text
            assert response.json() == {"fate": "started"}
            view = await _poll(
                client, base, lambda v: v["pending_permission_ask"] is not None, save, receipts
            )
            ask = view["pending_permission_ask"]
            assert target.name in (ask["title"] or "") + (ask["detail"] or ""), (
                "backend asked about an unrelated action",
                ask,
            )
            assert_native_configuration(backend, observations)
            receipts["ask"] = ask
            events = await _events(client, base)
            assert any(
                event["kind"] == "permission_asked"
                and event["payload"]["ask_id"] == ask["ask_id"]
                and event["payload"]["title"]
                for event in events
            )
            attention = await read_conversation_signals(
                runtime.system, runtime.store, {conversation_id}
            )
            assert attention[conversation_id].awaiting_answer is True
            receipts["awaiting_answer_before"] = attention[conversation_id].awaiting_answer
            # A bounded interval proves the request remains open without silently answering.
            before = asyncio.get_running_loop().time()
            samples = 0
            while asyncio.get_running_loop().time() - before < WAIT_SECONDS:
                waiting = (await client.get(base)).json()
                assert waiting["pending_permission_ask"]["ask_id"] == ask["ask_id"]
                assert not target.exists(), "action executed before an explicit answer"
                events = await _events(client, base)
                assert not any(event["kind"] == "permission_answered" for event in events)
                samples += 1
                await asyncio.sleep(0.1)
            receipts["wait"] = {
                "seconds": asyncio.get_running_loop().time() - before,
                "samples": samples,
                "file_absent": True,
                "unanswered": True,
            }
            kind = "allow_once" if decision == "allow" else "reject_once"
            options = [option for option in ask["options"] if option["option_kind"] == kind]
            assert len(options) == 1, ask
            answer = {"ask_id": ask["ask_id"], "option_id": options[0]["option_id"]}
            response = await client.post(base + "/permission-answers", json=answer)
            assert response.status_code == 200 and response.json() == {"landed": True}
            receipts["answer"] = answer
            await _poll(client, base, lambda v: not v["is_running"], save, receipts)
            events = await _events(client, base)
            answered = [event for event in events if event["kind"] == "permission_answered"]
            assert len(answered) == 1 and answered[0]["payload"] == answer
            assert len([event for event in events if event["kind"] == "permission_asked"]) == 1
            attention = await read_conversation_signals(
                runtime.system, runtime.store, {conversation_id}
            )
            assert attention[conversation_id].awaiting_answer is False
            receipts["awaiting_answer_after"] = attention[conversation_id].awaiting_answer
            if decision == "allow":
                assert target.read_text() == nonce
            else:
                assert not target.exists()
            endings = [event["payload"] for event in events if event["kind"] == "turn_ended"]
            assert len(endings) == 1 and endings[0]["ending"] == "completed", endings
            receipts["events"] = events
            receipts["file_outcome"] = {
                "exists": target.exists(),
                "content_matches": target.exists() and target.read_text() == nonce,
            }
            receipts["passed"] = True
    finally:
        receipts["finished_at"] = datetime.now(UTC).isoformat()
        save()


@asynccontextmanager
async def _api(database: Path) -> AsyncIterator[tuple[httpx.AsyncClient, ConversationRuntime]]:
    runtime = build_conversation_runtime(
        db_path=str(database),
        db_busy_timeout_ms=5_000,
        sse_heartbeat_ms=10_000,
        backend_child_factories=production_backend_child_factories(
            panels_server_url="http://127.0.0.1:1"
        ),
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/conversation")
    app.state.conversation = runtime
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://permission.test"
        ) as client:
            yield client, runtime
    finally:
        await runtime.shutdown()


async def _events(client: httpx.AsyncClient, base: str) -> list[dict[str, Any]]:
    response = await client.get(base + "/events")
    assert response.status_code == 200
    return list(response.json()["events"])


async def _poll(
    client: httpx.AsyncClient,
    base: str,
    predicate: Callable[[dict[str, Any]], bool],
    save: Callable[[], None],
    receipts: dict[str, Any],
) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + 150
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(base)
        assert response.status_code == 200
        view = response.json()
        receipts["last_view"] = view
        receipts["latest_events"] = await _events(client, base)
        save()
        if predicate(view):
            return dict(view)
        if any(event["kind"] == "turn_ended" for event in receipts["latest_events"]):
            # Events and view are independent reads. Completion can land between
            # them, so re-read the view before declaring the expected state absent.
            response = await client.get(base)
            assert response.status_code == 200
            final_view = response.json()
            receipts["last_view"] = final_view
            save()
            if predicate(final_view):
                return dict(final_view)
            raise AssertionError("turn ended before reaching expected permission state")
        await asyncio.sleep(0.1)
    raise AssertionError("real provider never reached expected permission state")


def _prepare_home(
    backend: ConversationBackendKey, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if backend is ConversationBackendKey.codex:
        return  # Existing shared home: copying a rotating grant can invalidate the real login.
    name = (
        "PANELS_REAL_HERMES_HOME_TEMPLATE"
        if backend is ConversationBackendKey.hermes
        else ("PANELS_REAL_CLAUDE_CONFIG_TEMPLATE")
    )
    source = Path(os.environ[name]).expanduser().resolve()
    assert source.is_dir()
    destination = root / f"{backend}-home"
    destination.mkdir()
    if backend is ConversationBackendKey.hermes:
        # Config is isolated, but rotating auth remains one shared canonical file.
        for filename in ("config.yaml", ".env"):
            if (source / filename).exists():
                shutil.copy2(source / filename, destination / filename)
        if (source / "auth.json").exists():
            (destination / "auth.json").symlink_to(source / "auth.json")
        monkeypatch.setenv("PLAN_HERMES_HOME", str(destination))
    else:
        for filename in (".credentials.json",):
            if (source / filename).exists():
                (destination / filename).symlink_to(source / filename)
        # Do not inherit permission grants/settings: default mode must actually ask.
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(destination))


def _versions(backend: ConversationBackendKey) -> dict[str, str]:
    if backend is ConversationBackendKey.codex:
        return {"cli": _command([shutil.which("codex") or "codex", "--version"])}
    if backend is ConversationBackendKey.claude:
        executable = os.environ.get(
            "PLAN_CLAUDE_EXECUTABLE",
            str(ROOT / "agent_backends/node_modules/@anthropic-ai/claude-code/bin/claude.exe"),
        )
        return {
            "cli": _command([executable, "--version"]),
            "sdk": importlib.metadata.version("claude-agent-sdk"),
        }
    executable = os.environ.get(
        "PLAN_HERMES_PYTHON", str(Path.home() / ".hermes/hermes-agent/venv/bin/python")
    )
    return {
        "python": _command([executable, "--version"]),
        "packages": _command(
            [
                executable,
                "-c",
                "import importlib.metadata as m,json; "
                "names=('hermes-agent','agent-client-protocol'); "
                "print(json.dumps({n:m.version(n) for n in names}))",
            ]
        ),
        "source_revision": _command(
            ["git", "-C", str(Path(executable).parents[2]), "rev-parse", "HEAD"]
        ),
    }


def _command(argv: list[str]) -> str:
    return subprocess.run(
        argv, cwd=ROOT, capture_output=True, text=True, check=True, timeout=30
    ).stdout.strip()

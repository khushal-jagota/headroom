"""Opt-in proof that real steering survives the full Panels conversation path.

The adapter exercises prove each vendor protocol in isolation.  This exercise starts the
production backend factory behind the real conversation system and HTTP API, then reads
only the same durable view and event payloads that a browser receives.  It is deliberately
opt-in because every case makes real model calls.
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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from planner.conversation.api import build_conversation_runtime, router
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.production_backends import production_backend_child_factories
from planner.core.db import connect, create_schema
from planner.environments.hermes_home import hermes_src_root

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REAL_SYSTEM_STEERING_ENVIRONMENT_NAME = "PANELS_REAL_SYSTEM_STEERING_TESTS"
EVIDENCE_ROOT_ENVIRONMENT_NAME = "PANELS_STEERING_EVIDENCE_ROOT"
HERMES_HOME_TEMPLATE_ENVIRONMENT_NAME = "PANELS_REAL_HERMES_HOME_TEMPLATE"
CODEX_HOME_TEMPLATE_ENVIRONMENT_NAME = "PANELS_REAL_CODEX_HOME_TEMPLATE"
CLAUDE_CONFIG_TEMPLATE_ENVIRONMENT_NAME = "PANELS_REAL_CLAUDE_CONFIG_TEMPLATE"

real_system_steering_only = pytest.mark.skipif(
    os.environ.get(REAL_SYSTEM_STEERING_ENVIRONMENT_NAME) != "1",
    reason=f"set {REAL_SYSTEM_STEERING_ENVIRONMENT_NAME}=1 to run real provider calls",
)


@dataclass(frozen=True, slots=True)
class _BackendCase:
    backend_key: ConversationBackendKey
    model: str


BACKEND_CASES = (
    _BackendCase(ConversationBackendKey.hermes, "openai-codex:gpt-6-astra"),
    _BackendCase(ConversationBackendKey.codex, "gpt-5.6-luna"),
    _BackendCase(ConversationBackendKey.claude, "haiku"),
)


@real_system_steering_only
@pytest.mark.parametrize("case", BACKEND_CASES, ids=lambda case: str(case.backend_key))
def test_real_provider_steer_reaches_model_system_api_stop_and_resume(
    case: _BackendCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One provider run proves model effect and the durable state a browser consumes."""

    asyncio.run(asyncio.wait_for(_exercise(case, tmp_path, monkeypatch), 900.0))


async def _exercise(
    case: _BackendCase,
    runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _required_evidence_root()
    planner_file = Path(_planner_file())
    assert planner_file.is_relative_to(REPOSITORY_ROOT), (
        f"planner resolved outside the proof worktree: {planner_file}"
    )
    _prepare_isolated_provider_home(case.backend_key, runtime_root, monkeypatch)
    database_path = runtime_root / "state" / "planner.db"
    workspace = runtime_root / "workspace"
    database_path.parent.mkdir(parents=True)
    workspace.mkdir(parents=True)
    temporary_root = runtime_root / "tmp"
    temporary_root.mkdir(parents=True)
    monkeypatch.setenv("TMPDIR", str(temporary_root))
    _create_database(database_path)

    conversation_id = f"real-system-steer-{case.backend_key}"
    model_nonce = f"MODEL-STEER-{uuid.uuid4()}"
    refused_nonce = f"REFUSED-STEER-{uuid.uuid4()}"
    forbidden_nonce = f"STOPPED-STEER-{uuid.uuid4()}"
    ordinary_nonce = f"AFTER-STOP-{uuid.uuid4()}"
    resume_nonce = f"AFTER-RESUME-{uuid.uuid4()}"
    forbidden_file = workspace / f"{forbidden_nonce}.txt"
    source_sha = _command_output(("git", "rev-parse", "HEAD"), cwd=REPOSITORY_ROOT)
    source_status = _command_output(("git", "status", "--short"), cwd=REPOSITORY_ROOT)
    assert source_status == "", f"real-provider proof requires a clean tree: {source_status}"
    receipts: dict[str, Any] = {
        "backend": str(case.backend_key),
        "model": case.model,
        "source_sha": source_sha,
        "source_status": source_status,
        "test_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "planner_file": str(planner_file),
        "provider_versions": _provider_versions(case.backend_key),
        "runtime_root": str(runtime_root),
        "started_at": datetime.now(UTC).isoformat(),
        "responses": [],
    }

    async with _api(database_path) as client:
        started = await client.post(
            "/api/conversation/conversations",
            json={
                "conversation_id": conversation_id,
                "backend_key": str(case.backend_key),
                "model": case.model,
                "workspace_folder": str(workspace),
                "access": "full",
            },
        )
        _keep_response(receipts, "start", started)
        assert started.status_code == 201, started.text

        first = await _send(
            client,
            conversation_id,
            "Run the shell command `sleep 6`. After it finishes, reply with exactly ORIGINAL-DONE.",
        )
        _keep_response(receipts, "first_prompt", first)
        assert first.json() == {"fate": "started"}
        first_tool_events = await _wait_for_event_count(
            client,
            conversation_id,
            "tool_call_started",
            1,
            receipts=receipts,
        )
        first_prompt_sequence = _prompt_sequence(
            first_tool_events,
            "Run the shell command `sleep 6`. After it finishes, reply with exactly ORIGINAL-DONE.",
        )
        first_tool = _open_tool_after(first_tool_events, first_prompt_sequence)
        receipts["first_active_tool"] = first_tool
        await _keep_and_assert_view(
            client,
            conversation_id,
            receipts,
            "active_before_model_steer",
            is_running=True,
        )

        steered = await _send(
            client,
            conversation_id,
            f"After the current command, reply with exactly {model_nonce} and nothing else.",
            mode="steer",
        )
        _keep_response(receipts, "model_steer", steered)
        assert steered.json() == {"fate": "injected"}
        first_events = await _wait_for_event_count(
            client,
            conversation_id,
            "turn_ended",
            1,
            timeout=240.0,
            receipts=receipts,
        )
        assert model_nonce in _agent_text(first_events)

        refused = await _send(
            client,
            conversation_id,
            refused_nonce,
            mode="steer",
        )
        _keep_response(receipts, "idle_steer_refused", refused)
        assert refused.json() == {
            "fate": "refused",
            "refusal_reason": "no_running_turn_to_steer_into",
        }
        refused_events = await _events(client, conversation_id)
        matching_refusals = [
            event
            for event in refused_events
            if event["kind"] == "prompt_delivery_refused"
            and event["payload"].get("text") == refused_nonce
        ]
        assert len(matching_refusals) == 1
        assert matching_refusals[0]["payload"]["mode"] == "steer"
        assert matching_refusals[0]["payload"]["refusal_reason"] == "no_running_turn_to_steer_into"
        assert not any(
            event["kind"] == "prompt" and event["payload"].get("text") == refused_nonce
            for event in refused_events
        )
        await _keep_and_assert_view(
            client,
            conversation_id,
            receipts,
            "idle_after_refused_steer",
            is_running=False,
        )

        stop_prompt_text = (
            "Run the shell command `sleep 6`. After it finishes, reply with exactly "
            "SECOND-ORIGINAL-DONE."
        )
        stop_prompt = await _send(
            client,
            conversation_id,
            stop_prompt_text,
        )
        _keep_response(receipts, "stop_prompt", stop_prompt)
        assert stop_prompt.json() == {"fate": "started"}
        stop_prompt_events = await _events(client, conversation_id)
        stop_prompt_sequence = _prompt_sequence(stop_prompt_events, stop_prompt_text)
        _, stop_tool = await _wait_for_open_tool_after(
            client,
            conversation_id,
            stop_prompt_sequence,
            receipts=receipts,
        )
        receipts["stop_active_tool"] = stop_tool
        await _keep_and_assert_view(
            client,
            conversation_id,
            receipts,
            "active_before_stop_steer",
            is_running=True,
        )

        stop_steer = await _send(
            client,
            conversation_id,
            (
                f"After the current command, create {forbidden_file.name} containing "
                f"{forbidden_nonce}, then reply with exactly {forbidden_nonce}."
            ),
            mode="steer",
        )
        _keep_response(receipts, "stop_steer", stop_steer)
        assert stop_steer.json() == {"fate": "injected"}
        stop_admission_events = await _events(client, conversation_id)
        assert _tool_is_open(stop_admission_events, stop_tool["payload"]["tool_call_id"])
        assert any(
            event["kind"] == "prompt"
            and event["payload"].get("mode") == "steer"
            and event["payload"].get("text", "").endswith(forbidden_nonce + ".")
            for event in stop_admission_events
        )
        interrupted = await client.post(
            f"/api/conversation/conversations/{conversation_id}/interrupt"
        )
        _keep_response(receipts, "interrupt", interrupted)
        assert interrupted.status_code == 204
        stopped_events = await _wait_for_event_count(
            client,
            conversation_id,
            "turn_ended",
            2,
            timeout=120.0,
            receipts=receipts,
        )
        endings = [event for event in stopped_events if event["kind"] == "turn_ended"]
        assert endings[-1]["payload"]["ending"] == "interrupted"
        await asyncio.sleep(7)
        bounded_stop_events = await _events(client, conversation_id)
        _assert_stopped_effect_absent(bounded_stop_events, forbidden_nonce, forbidden_file)

        ordinary = await _send(
            client,
            conversation_id,
            f"Reply with exactly {ordinary_nonce} and nothing else. Use no tools.",
        )
        _keep_response(receipts, "ordinary_after_stop", ordinary)
        assert ordinary.json() == {"fate": "started"}
        ordinary_events = await _wait_for_event_count(
            client,
            conversation_id,
            "turn_ended",
            3,
            timeout=240.0,
            receipts=receipts,
        )
        assert ordinary_nonce in _agent_text(ordinary_events)
        _assert_stopped_effect_absent(ordinary_events, forbidden_nonce, forbidden_file)
        await _keep_and_assert_view(
            client,
            conversation_id,
            receipts,
            "view_before_resume",
            is_running=False,
        )

    async with _api(database_path) as resumed_client:
        resumed = await _send(
            resumed_client,
            conversation_id,
            f"Reply with exactly {resume_nonce} and nothing else. Use no tools.",
        )
        _keep_response(receipts, "resumed_prompt", resumed)
        assert resumed.json() == {"fate": "started"}
        final_events = await _wait_for_event_count(
            resumed_client,
            conversation_id,
            "turn_ended",
            4,
            timeout=240.0,
            receipts=receipts,
        )
        assert resume_nonce in _agent_text(final_events)
        _assert_stopped_effect_absent(final_events, forbidden_nonce, forbidden_file)
        await _keep_and_assert_view(
            resumed_client,
            conversation_id,
            receipts,
            "final_view",
            is_running=False,
        )

    steer_prompts = [
        event
        for event in final_events
        if event["kind"] == "prompt" and event["payload"]["mode"] == "steer"
    ]
    ordinary_prompts = [
        event
        for event in final_events
        if event["kind"] == "prompt" and event["payload"]["mode"] != "steer"
    ]
    assert len(steer_prompts) == 2
    assert len(ordinary_prompts) == 4
    turn_endings = [event for event in final_events if event["kind"] == "turn_ended"]
    assert len(turn_endings) == 4
    assert [event["payload"]["ending"] for event in turn_endings] == [
        "completed",
        "interrupted",
        "completed",
        "completed",
    ]
    assert all(event["payload"]["error_summary"] is None for event in turn_endings)
    assert ordinary_prompts[0]["sequence"] < steer_prompts[0]["sequence"]
    assert steer_prompts[0]["sequence"] < turn_endings[0]["sequence"]
    assert ordinary_prompts[1]["sequence"] < steer_prompts[1]["sequence"]
    assert steer_prompts[1]["sequence"] < turn_endings[1]["sequence"]
    assert ordinary_prompts[2]["sequence"] < turn_endings[2]["sequence"]
    assert ordinary_prompts[3]["sequence"] < turn_endings[3]["sequence"]
    _assert_stopped_effect_absent(final_events, forbidden_nonce, forbidden_file)
    receipts["events"] = final_events
    receipts["assertions"] = {
        "active_steer_changed_model_output": True,
        "accepted_steers_are_durable": True,
        "idle_steer_refusal_is_durable_and_not_held": True,
        "one_panels_turn_ended_for_each_ordinary_prompt": True,
        "stop_interrupted_an_open_tool": True,
        "stopped_guidance_did_not_execute_later": True,
        "endings_are_exact_and_error_free": True,
        "ordinary_prompt_after_stop_completed": True,
        "resumed_prompt_completed": True,
        "final_api_view_is_idle": True,
    }
    receipts["completed_at"] = datetime.now(UTC).isoformat()
    _write_evidence(case.backend_key, receipts)
    print(json.dumps(receipts, indent=2))


@asynccontextmanager
async def _api(database_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    runtime = build_conversation_runtime(
        db_path=str(database_path),
        db_busy_timeout_ms=5_000,
        sse_heartbeat_ms=10_000,
        backend_child_factories=production_backend_child_factories(
            panels_server_url="http://127.0.0.1:8811"
        ),
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/conversation")
    app.state.conversation = runtime
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://panels.test"
        ) as client:
            yield client
    finally:
        await runtime.shutdown()


async def _send(
    client: httpx.AsyncClient,
    conversation_id: str,
    text: str,
    *,
    mode: str = "queue",
) -> httpx.Response:
    return await client.post(
        f"/api/conversation/conversations/{conversation_id}/send",
        json={
            "content": [{"piece": "text", "text": text}],
            "sender_label": "integration-proof",
            "mode": mode,
            "sender_message_id": str(uuid.uuid4()),
        },
    )


async def _events(client: httpx.AsyncClient, conversation_id: str) -> list[dict[str, Any]]:
    response = await client.get(f"/api/conversation/conversations/{conversation_id}/events")
    assert response.status_code == 200, response.text
    return list(response.json()["events"])


async def _wait_for_event_count(
    client: httpx.AsyncClient,
    conversation_id: str,
    kind: str,
    count: int,
    *,
    timeout: float = 180.0,
    receipts: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        events = await _events(client, conversation_id)
        if receipts is not None:
            receipts["latest_events"] = events
            _write_evidence_from_receipts(receipts)
        if sum(event["kind"] == kind for event in events) >= count:
            return events
        await asyncio.sleep(0.1)
    raise AssertionError(f"conversation never recorded {count} {kind} events")


async def _wait_for_open_tool_after(
    client: httpx.AsyncClient,
    conversation_id: str,
    prompt_sequence: int,
    *,
    timeout: float = 60.0,
    receipts: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        events = await _events(client, conversation_id)
        if receipts is not None:
            receipts["latest_events"] = events
            _write_evidence_from_receipts(receipts)
        try:
            return events, _open_tool_after(events, prompt_sequence)
        except AssertionError:
            await asyncio.sleep(0.1)
    raise AssertionError("the selected prompt never recorded an open tool call")


def _prompt_sequence(events: list[dict[str, Any]], text: str) -> int:
    matches = [
        event["sequence"]
        for event in events
        if event["kind"] == "prompt" and event["payload"].get("text") == text
    ]
    assert len(matches) == 1
    return int(matches[0])


def _open_tool_after(events: list[dict[str, Any]], prompt_sequence: int) -> dict[str, Any]:
    starts = [
        event
        for event in events
        if event["kind"] == "tool_call_started" and event["sequence"] > prompt_sequence
    ]
    assert starts
    for started in starts:
        if _tool_is_open(events, started["payload"]["tool_call_id"]):
            return started
    raise AssertionError("all tool calls after the selected prompt already finished")


def _tool_is_open(events: list[dict[str, Any]], tool_call_id: str) -> bool:
    return not any(
        event["kind"] == "tool_call_finished" and event["payload"]["tool_call_id"] == tool_call_id
        for event in events
    )


def _assert_stopped_effect_absent(
    events: list[dict[str, Any]], forbidden_nonce: str, forbidden_file: Path
) -> None:
    assert not forbidden_file.exists()
    assert forbidden_nonce not in _agent_text(events)
    assert not any(
        event["kind"] == "tool_call_started"
        and forbidden_nonce in str(event["payload"].get("detail", ""))
        for event in events
    )


async def _keep_and_assert_view(
    client: httpx.AsyncClient,
    conversation_id: str,
    receipts: dict[str, Any],
    name: str,
    *,
    is_running: bool,
) -> None:
    response = await client.get(f"/api/conversation/conversations/{conversation_id}")
    _keep_response(receipts, name, response)
    assert response.status_code == 200
    assert response.json()["is_running"] is is_running
    assert response.json()["held_prompts"] == []


def _agent_text(events: list[dict[str, Any]]) -> str:
    return " ".join(
        str(event["payload"].get("text", ""))
        for event in events
        if event["kind"] == "agent_message"
    )


def _prepare_isolated_provider_home(
    backend_key: ConversationBackendKey,
    runtime_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if backend_key is ConversationBackendKey.hermes:
        source = _required_template(HERMES_HOME_TEMPLATE_ENVIRONMENT_NAME)
        destination = runtime_root / "hermes-home"
        shutil.copytree(source, destination)
        monkeypatch.setenv("PLAN_HERMES_HOME", str(destination))
        return
    if backend_key is ConversationBackendKey.codex:
        source = _required_template(CODEX_HOME_TEMPLATE_ENVIRONMENT_NAME)
        destination = runtime_root / "codex-home"
        destination.mkdir()
        for name in ("auth.json", "config.toml"):
            shutil.copy2(source / name, destination / name)
        monkeypatch.setenv("CODEX_HOME", str(destination))
        return
    source = _required_template(CLAUDE_CONFIG_TEMPLATE_ENVIRONMENT_NAME)
    destination = runtime_root / "claude-config"
    destination.mkdir()
    for name in (".credentials.json", "settings.json"):
        candidate = source / name
        if candidate.exists():
            shutil.copy2(candidate, destination / name)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(destination))


def _required_template(environment_name: str) -> Path:
    value = os.environ.get(environment_name)
    if value is None:
        pytest.fail(f"{environment_name} is required for the selected real provider")
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        pytest.fail(f"{environment_name} does not name a directory: {path}")
    return path


def _create_database(database_path: Path) -> None:
    connection = connect(str(database_path), 5_000)
    try:
        create_schema(connection)
    finally:
        connection.close()


def _provider_versions(backend_key: ConversationBackendKey) -> dict[str, str]:
    if backend_key is ConversationBackendKey.hermes:
        executable = os.environ.get(
            "PLAN_HERMES_PYTHON",
            str(Path.home() / ".hermes/hermes-agent/venv/bin/python"),
        )
        source_root = hermes_src_root(executable)
        package_versions = json.loads(
            _command_output(
                (
                    executable,
                    "-c",
                    "import importlib.metadata as m, json; "
                    "print(json.dumps({name: m.version(name) for name in "
                    "('hermes-agent', 'agent-client-protocol')}))",
                )
            )
        )
        assert isinstance(package_versions, dict)
        return {
            "python": _command_output((executable, "--version")),
            "hermes_source": str(source_root),
            "hermes_source_sha": _command_output(("git", "rev-parse", "HEAD"), cwd=source_root),
            "hermes_agent": str(package_versions["hermes-agent"]),
            "agent_client_protocol": str(package_versions["agent-client-protocol"]),
        }
    if backend_key is ConversationBackendKey.codex:
        return {"codex_cli": _command_output((shutil.which("codex") or "codex", "--version"))}
    executable = os.environ.get(
        "PLAN_CLAUDE_EXECUTABLE",
        str(
            Path(__file__).resolve().parents[2]
            / "agent_backends/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
        ),
    )
    return {
        "claude_cli": _command_output((executable, "--version")),
        "claude_agent_sdk": importlib.metadata.version("claude-agent-sdk"),
    }


def _planner_file() -> str:
    import planner

    return str(Path(planner.__file__).resolve())


def _command_output(argv: tuple[str, ...], *, cwd: Path | None = None) -> str:
    return subprocess.run(
        argv,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()


def _keep_response(receipts: dict[str, Any], name: str, response: httpx.Response) -> None:
    body: object = None
    if response.content:
        body = response.json()
    receipts["responses"].append(
        {
            "name": name,
            "status_code": response.status_code,
            "body": body,
            "observed_at": datetime.now(UTC).isoformat(),
        }
    )
    receipts["last_checkpoint"] = name
    _write_evidence_from_receipts(receipts)
    print(json.dumps(receipts["responses"][-1], sort_keys=True))


def _write_evidence_from_receipts(receipts: dict[str, Any]) -> None:
    _write_evidence(ConversationBackendKey(str(receipts["backend"])), receipts)


def _write_evidence(backend_key: ConversationBackendKey, receipts: dict[str, Any]) -> None:
    destination = _required_evidence_root()
    (destination / f"{backend_key}-system-api.json").write_text(
        json.dumps(receipts, indent=2) + "\n", encoding="utf-8"
    )


def _required_evidence_root() -> Path:
    value = os.environ.get(EVIDENCE_ROOT_ENVIRONMENT_NAME)
    if value is None:
        pytest.fail(f"{EVIDENCE_ROOT_ENVIRONMENT_NAME} is required for durable provider receipts")
    destination = Path(value).expanduser()
    if not destination.is_absolute():
        pytest.fail(f"{EVIDENCE_ROOT_ENVIRONMENT_NAME} must be an absolute path")
    destination.mkdir(parents=True, exist_ok=True)
    return destination

"""Real-provider proofs through the production application composition boundary.

This host omits background Worker scheduling, but uses the actual conversation
factory, Signal producer, navigation installation, HTTP middleware and shutdown.
Authentication is a separately reviewed prerequisite, never installed by this host.
"""

from __future__ import annotations

import asyncio
import json
import os
import pwd
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Any

import httpx
import pytest

from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.publication.coordinator import SignalPublicationCoordinator
from planner.conversation.runtime_composition import ConversationRuntime
from planner.core.clock import RealClock
from planner.core.config import load_config
from planner.core.db import connect
from planner.core.server import create_app
from planner.navigation.contracts import SCHEMA_VERSION, SUPPORTED_SEMANTICS
from planner.navigation.delivery_authority import DeliveryUnavailable


def require_provider_auth(backend: ConversationBackendKey) -> dict[str, str]:
    """Validate the owner-authorized normal native profile without reading credentials.

    Native authentication, refresh locks and new session metadata stay in their normal
    profile. Panels data, workspaces and conversation identities remain fresh. This
    checks routing only; the real provider reports missing authentication/model access.
    """
    normal_home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    assert Path(os.environ["HOME"]).resolve() == normal_home, "normal native HOME is required"
    names = {
        ConversationBackendKey.codex: ("CODEX_HOME", ".codex"),
        ConversationBackendKey.claude: ("CLAUDE_CONFIG_DIR", ".claude"),
        ConversationBackendKey.hermes: ("HERMES_HOME", ".hermes"),
    }
    variable, directory = names[backend]
    expected = normal_home / directory
    assert expected.is_dir(), f"normal native profile is missing: {expected}"
    assert Path(os.environ[variable]).resolve() == expected.resolve(), variable
    if backend is ConversationBackendKey.hermes:
        assert Path(os.environ["PLAN_HERMES_HOME"]).resolve() == expected.resolve()
        interpreter = Path(os.environ["PLAN_HERMES_PYTHON"])
        assert interpreter == normal_home / ".hermes/hermes-agent/venv/bin/python"
        assert interpreter.is_file(), "installed Hermes interpreter is missing"
    return {
        "route": "normal canonical native profile",
        "home": str(normal_home),
        "profile_variable": variable,
        "profile": str(expected),
        "native_write_boundary": (
            "normal authentication refresh/locks, new provider session metadata "
            "and native caches/logs/temp"
        ),
        "panels_write_boundary": (
            "fresh synthetic Panels database, logs, workspaces and conversations"
        ),
        "existing_conversations": "not opened or reused",
    }


def observe_hermes_spawn(
    monkeypatch: pytest.MonkeyPatch, backend: ConversationBackendKey, receipts: dict[str, Any]
) -> None:
    """Observe actual subprocess environment membership without changing any argument."""
    if backend is not ConversationBackendKey.hermes:
        return
    original = asyncio.create_subprocess_exec
    interpreter = os.environ["PLAN_HERMES_PYTHON"]

    async def observed_spawn(program: Any, *arguments: Any, **options: Any) -> Any:
        if str(program) == interpreter:
            environment = options.get("env")
            assert isinstance(environment, dict)
            child_has_test_marker = "PYTEST_CURRENT_TEST" in environment
            script = Path(str(arguments[0])).name if arguments else ""
            is_acp = script == "hermes_acp_extension.py"
            boundary = {
                "hermes_model_catalog.py": "model catalogue before native subprocess spawn",
                "hermes_acp_extension.py": "ACP child after transport environment merge",
            }.get(script, "other configured Hermes interpreter subprocess")
            receipts.setdefault("native_spawn_observations", []).append(
                {
                    "boundary": boundary,
                    "native_guard_environment_preserved": True,
                    "parent_has_pytest_current_test": "PYTEST_CURRENT_TEST" in os.environ,
                    "child_has_pytest_current_test": child_has_test_marker,
                    "arguments_or_environment_modified": False,
                }
            )
            if is_acp:
                assert not child_has_test_marker, "native Hermes test seatbelt would be active"
        return await original(program, *arguments, **options)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", observed_spawn)


@asynccontextmanager
async def provider_application(
    database: Path, receipts: dict[str, Any]
) -> AsyncIterator[tuple[httpx.AsyncClient, ConversationRuntime]]:
    root = database.resolve().parent
    configured_root = os.environ.get("PANELS_PROVIDER_RUNTIME_ROOT")
    assert configured_root, "PANELS_PROVIDER_RUNTIME_ROOT must name the synthetic proof root"
    allowed_root = Path(configured_root)
    assert allowed_root.is_absolute() and allowed_root.is_dir(), allowed_root
    assert root.is_relative_to(allowed_root.resolve()) and root != allowed_root.resolve(), root
    config = load_config(
        str(root / "absent-config.yaml"),
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(database),
            "PLAN_LOGS_DIR": str(root / "logs"),
            "PLAN_BACKUP_DIR": str(root / "backups"),
            "PLAN_DISPATCHER_LOCK_PATH": str(root / "dispatcher.lock"),
            "PLAN_DISPATCH_ENABLED": "0",
            "PLAN_PORT": "1",
        },
    )
    app = create_app(config, RealClock(), lambda: connect(str(database), 5_000))
    lifetimes_before = len(receipts.get("application_lifetimes", []))
    try:
        async with app.router.lifespan_context(app):
            runtime = app.state.conversation
            assert isinstance(runtime, ConversationRuntime)
            assert isinstance(runtime.store.signal_coordinator, SignalPublicationCoordinator)
            assert app.state.conversation_system is runtime.system
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://provider-proof.test"
            ) as client:
                snapshot = await client.get("/api/navigation/snapshot")
                assert snapshot.status_code == 200, snapshot.text
                value = snapshot.json()
                assert value["mode"] == "current"
                assert value["snapshot"]["schema_version"] == SCHEMA_VERSION
                assert value["snapshot"]["semantics"] == SUPPORTED_SEMANTICS
                with closing(connect(str(database), 5_000)) as connection:
                    source = connection.execute(
                        "SELECT scope,runtime_epoch FROM signal_publication_meta WHERE singleton=1"
                    ).fetchone()
                    installed = connection.execute(
                        "SELECT scope,runtime_epoch,installation_id "
                        "FROM navigation_signal_installation WHERE singleton=1"
                    ).fetchone()
                    assert source is not None and installed is not None
                    assert tuple(source) == tuple(installed[:2])
                    assert installed[2]
                receipts.setdefault("application_lifetimes", []).append(
                    {
                        "signal_enabled": True,
                        "mode": value["mode"],
                        "schema_version": value["snapshot"]["schema_version"],
                        "scope": installed[0],
                        "runtime_epoch": installed[1],
                        "installation_id": installed[2],
                        "shutdown_invalidated_delivery": False,
                    }
                )
                yield client, runtime
    finally:
        if len(receipts.get("application_lifetimes", [])) > lifetimes_before:
            assert app.state.conversation is None
            try:
                after_shutdown, _ = await app.state.navigation_delivery.current_snapshot()
            except DeliveryUnavailable:
                pass
            else:
                assert after_shutdown["mode"] != "current", (
                    "navigation remained current after shutdown"
                )
            receipts["application_lifetimes"][-1]["shutdown_invalidated_delivery"] = True


async def assert_navigation_attention(
    client: httpx.AsyncClient,
    conversation_id: str,
    receipts: dict[str, Any],
    checkpoint: str,
    *,
    working: bool,
    awaiting_answer: bool,
) -> None:
    response = await client.get("/api/navigation/snapshot")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["mode"] == "current"
    rows = [
        row
        for row in payload["snapshot"]["records"]
        if row["kind"] == "conversation" and row["id"] == conversation_id
    ]
    assert len(rows) == 1, rows
    attention = rows[0]["value"]["attention"]["current"]
    assert attention is not None
    assert attention["signal"]["working"] is working, attention
    assert attention["awaiting_answer"] is awaiting_answer, attention
    assert attention["last_turn_failed"] is False, attention
    receipts.setdefault("navigation_checkpoints", []).append(
        {
            "checkpoint": checkpoint,
            "revision": payload["snapshot"]["revision"],
            "conversation_id": conversation_id,
            "attention": json.loads(json.dumps(attention)),
        }
    )

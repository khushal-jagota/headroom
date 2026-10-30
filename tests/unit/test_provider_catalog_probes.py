"""Provider metadata stays complete, visible, and exact without a turn."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from planner.conversation.backends import claude_model_catalog as claude
from planner.conversation.backends.codex_app_server import model_catalog as codex
from planner.conversation.backends.codex_app_server.client import (
    CodexAppServerError,
    CodexWireFailed,
)


def model(token: str, *, hidden: bool = False) -> dict[str, object]:
    return {
        "id": token, "model": token, "displayName": token, "description": "fixture",
        "hidden": hidden, "isDefault": False, "defaultReasoningEffort": "medium",
        "supportedReasoningEfforts": [{"reasoningEffort": "medium", "description": ""}],
    }


@pytest.mark.parametrize("ending", ["complete", "error", "repeated_cursor", "stalled_page"])
def test_codex_catalog_walks_pages_and_retains_partial_positive_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    monkeypatch.setattr(codex, "MODEL_CATALOG_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(codex, "MODEL_CATALOG_REPLY_CLEANUP_RESERVE_SECONDS", 0.1)
    requests: list[tuple[str, dict[str, Any]]] = []
    remaining_budgets: list[float] = []
    stopped: list[bool] = []
    scoped_environment = {"HOME": str(tmp_path), "PROVIDER_FIXTURE": "selected"}

    class Client:
        async def start(
            self, *, argv: tuple[str, ...], environment: Mapping[str, str],
            working_directory: Path,
        ) -> None:
            assert argv == ("/selected/codex", "app-server")
            assert environment == scoped_environment
            assert working_directory == tmp_path

        async def notify(self, method: str) -> None:
            assert method == "initialized"

        async def request(
            self, method: str, params: dict[str, Any], *, timeout_seconds: float
        ) -> dict[str, Any]:
            requests.append((method, params))
            remaining_budgets.append(timeout_seconds)
            if method == "initialize":
                return {}
            if method == "account/read":
                assert params == {"refreshToken": False}
                return {"account": {"type": "apiKey"}, "requiresOpenaiAuth": True}
            assert method == "model/list"
            if "cursor" not in params:
                return {"data": [model("gpt-6.1-terra"), model("hidden", hidden=True)],
                        "nextCursor": "second"}
            assert params["cursor"] == "second"
            if ending == "error":
                raise CodexAppServerError("second page offline")
            if ending == "stalled_page":
                try:
                    await asyncio.wait_for(asyncio.Event().wait(), timeout_seconds)
                except TimeoutError as error:
                    raise CodexWireFailed("model/list was not answered") from error
            return {"data": [model("gpt-6.1-fable")],
                    "nextCursor": "second" if ending == "repeated_cursor" else None}

        async def stop(self) -> None:
            stopped.append(True)

    monkeypatch.setattr(codex, "CodexAppServerClient", lambda **kwargs: Client())

    async def run() -> None:
        result = await codex.probe_codex_model_catalog(
            "/selected/codex", environment=scoped_environment, working_directory=tmp_path
        )
        assert result.models[0].model_id == "gpt-6.1-terra"
        assert result.models[1].model_id == "hidden" and result.models[1].hidden
        assert result.authenticated is True
        assert result.complete == (ending == "complete")
        assert result.visibility_limited
        assert len(requests) == 4
        assert stopped == [True]
        assert remaining_budgets == sorted(remaining_budgets, reverse=True)
        assert remaining_budgets[0] <= 0.1
        if ending not in ("error", "stalled_page"):
            assert result.models[2].model_id == "gpt-6.1-fable"

    asyncio.run(run())


def test_claude_probe_uses_only_destination_local_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from subprocess import CompletedProcess

    async def run_process(argv: list[str], **kwargs: Any) -> CompletedProcess[bytes]:
        assert argv == ["/selected/claude", "--version"]
        assert kwargs == {"env": {"PROVIDER_FIXTURE": "selected"}, "cwd": tmp_path}
        return CompletedProcess(argv, 0, b"2.1.280 (Claude Code)", b"")

    monkeypatch.setattr(claude.anyio, "run_process", run_process)
    result = asyncio.run(claude.probe_claude_model_catalog(
        "/selected/claude", environment={"PROVIDER_FIXTURE": "selected"},
        working_directory=tmp_path,
    ))
    rows = {row.model_id: row for row in result.models}
    assert "claude-opus-5-5" in rows
    assert "claude-sonnet-5-5" not in rows
    assert rows["claude-opus-4-7"].reasoning_effort_options == ("low", "medium", "high", "max")
    assert rows["claude-sonnet-4-6"].reasoning_effort_options == ("low", "medium", "high")
    assert rows["claude-haiku-4-5"].reasoning_effort_options == ()
    assert result.default_model_id is None
    assert set(result.reasoning_effort_options) == {"low", "medium", "high", "xhigh", "max"}


def test_claude_optional_version_failure_preserves_unbounded_suggestions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("fixture")

    monkeypatch.setattr(claude.anyio, "run_process", unavailable)
    result = asyncio.run(claude.probe_claude_model_catalog("/missing/claude"))
    assert {row.model_id for row in result.models} == {
        "claude-opus-4-6", "claude-opus-4-5", "claude-sonnet-5",
        "claude-sonnet-4-6", "claude-haiku-4-5",
    }
    assert claude.claude_launch_model("opus") == "opus"
    assert claude.claude_launch_model("opus[1m]") == "opus[1m]"
    assert claude.claude_launch_model("opus-4.6[1m]") == "claude-opus-4-6[1m]"
    assert claude.claude_launch_model("custom-exact-token") == "custom-exact-token"

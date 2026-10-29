"""Provider metadata stays complete, visible, and exact without a turn."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions

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
        assert all(row.model_id != "hidden" for row in result.models)
        assert result.complete == (ending == "complete")
        assert result.visibility_limited
        assert len(requests) == 3
        assert stopped == [True]
        assert remaining_budgets == sorted(remaining_budgets, reverse=True)
        assert remaining_budgets[0] <= 0.1
        if ending not in ("error", "stalled_page"):
            assert result.models[1].model_id == "gpt-6.1-fable"

    asyncio.run(run())


def test_claude_probe_keeps_unknown_effort_distinct_and_no_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options: list[ClaudeAgentOptions] = []
    disconnected: list[bool] = []

    class Client:
        def __init__(self, *, options: ClaudeAgentOptions, transport: Any) -> None:
            self.options = options
            transport.control_request = self.control_request

        async def __aenter__(self) -> Client:
            options.append(self.options)
            return self

        async def __aexit__(self, *args: object) -> None:
            disconnected.append(True)

        async def control_request(self, subtype: str) -> dict[str, object]:
            if subtype == "get_settings":
                return {"effective": {}, "sources": [],
                        "applied": {"model": "claude-opus-5", "effort": "high"}}
            assert subtype == "list_models"
            return {"models": [
                {"value": "opus", "resolvedModel": "claude-opus-5"},
                {"value": "claude-opus-4", "resolvedModel": "claude-opus-4",
                 "supportedEffortLevels": []},
                {"value": "bad-effort", "resolvedModel": "bad-effort",
                 "supportedEffortLevels": [42]},
                {"value": "hidden", "resolvedModel": "hidden", "hidden": True},
            ]}

    monkeypatch.setattr(claude, "ClaudeSDKClient", Client)

    async def run() -> None:
        result = await claude.probe_claude_model_catalog(
            "/selected/claude", environment={"PROVIDER_FIXTURE": "selected"},
            working_directory=tmp_path,
        )
        assert [entry.model_id for entry in result.models] == [
            "opus", "claude-opus-4", "bad-effort"
        ]
        assert [entry.reasoning_effort_options for entry in result.models] == [None, (), None]
        assert options[0].cli_path == "/selected/claude"
        assert options[0].cwd == tmp_path
        assert options[0].env == {"PROVIDER_FIXTURE": "selected"}
        assert disconnected == [True]

    asyncio.run(run())

"""Provider metadata stays complete, visible, and exact without a turn."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from planner.conversation.backends import claude_model_catalog as claude
from planner.conversation.backends.codex_app_server import model_catalog as codex
from planner.conversation.backends.codex_app_server.client import (
    CodexAppServerError,
)


def model(token: str, *, hidden: bool = False) -> dict[str, object]:
    return {
        "id": token,
        "model": token,
        "displayName": token,
        "description": "fixture",
        "hidden": hidden,
        "isDefault": False,
        "defaultReasoningEffort": "medium",
        "supportedReasoningEfforts": [{"reasoningEffort": "medium", "description": ""}],
    }


@pytest.mark.parametrize("ending", ["complete", "error", "repeated_cursor"])
def test_codex_catalog_walks_pages_and_retains_partial_positive_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    monkeypatch.setattr(codex, "MODEL_CATALOG_TIMEOUT_SECONDS", 0.2)
    requests: list[tuple[str, dict[str, Any]]] = []
    stopped: list[bool] = []

    class Client:
        async def start(
            self,
            *,
            argv: tuple[str, ...],
            environment: dict[str, str],
            working_directory: Path,
        ) -> None:
            assert argv == ("/selected/codex", "app-server")
            assert working_directory == Path.home()

        async def notify(self, method: str) -> None:
            assert method == "initialized"

        async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
            requests.append((method, params))
            if method == "initialize":
                return {}
            assert method == "model/list"
            if "cursor" not in params:
                return {
                    "data": [model("gpt-6.1-terra"), model("hidden", hidden=True)],
                    "nextCursor": "second",
                }
            assert params["cursor"] == "second"
            if ending == "error":
                raise CodexAppServerError("second page offline")
            return {
                "data": [model("gpt-6.1-fable")],
                "nextCursor": "second" if ending == "repeated_cursor" else None,
            }

        async def stop(self) -> None:
            stopped.append(True)

    monkeypatch.setattr(codex, "CodexAppServerClient", lambda **kwargs: Client())

    async def run() -> None:
        result = await codex.probe_codex_model_catalog("/selected/codex")
        assert result.models[0].model_id == "gpt-6.1-terra"
        assert all(entry.model_id != "hidden" for entry in result.models)
        assert result.complete == (ending == "complete")
        assert len(requests) == 3
        assert stopped == [True]
        if ending != "error":
            assert result.models[1].model_id == "gpt-6.1-fable"

    asyncio.run(run())


def test_claude_handshake_supplies_pinned_models_and_default_without_a_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Client:
        def __init__(self, *, options: Any) -> None:
            assert options.cli_path == "/scripted/claude"

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def get_server_info(self) -> dict[str, object]:
            return {
                "models": [
                    {"value": "default", "resolvedModel": "claude-opus-5-5"},
                    {
                        "value": "opus",
                        "resolvedModel": "claude-opus-5-5",
                        "displayName": "Opus 5.5",
                        "supportedEffortLevels": ["high", "max"],
                    },
                    {
                        "value": "fable",
                        "resolvedModel": "claude-fable-5-1",
                        "displayName": "Fable 5.1",
                        "supportedEffortLevels": ["high"],
                    },
                    {
                        "value": "claude-haiku-4-5-20251001",
                        "resolvedModel": "claude-haiku-4-5-20251001",
                        "supportedEffortLevels": [],
                    },
                ]
            }

    monkeypatch.setattr(claude, "ClaudeSDKClient", Client)
    result = asyncio.run(claude.probe_claude_model_catalog("/scripted/claude"))
    assert [entry.model_id for entry in result.models] == [
        "claude-opus-5-5",
        "claude-fable-5-1",
        "claude-haiku-4-5-20251001",
    ]
    assert result.default_model_id == "claude-opus-5-5"
    assert result.models[0].display_name == "Opus 5.5"
    assert result.models[0].reasoning_effort_options == ("high", "max")
    assert result.models[-1].reasoning_effort_options == ()


def test_claude_failed_handshake_reports_no_invented_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Client:
        def __init__(self, **kwargs: Any) -> None:
            raise ValueError("scripted failure")

    monkeypatch.setattr(claude, "ClaudeSDKClient", Client)
    with pytest.raises(claude.ClaudeModelCatalogUnavailable):
        asyncio.run(claude.probe_claude_model_catalog("/scripted/claude"))


def test_codex_timeout_stops_the_probe_child(monkeypatch: pytest.MonkeyPatch) -> None:
    stopped = []

    class Client:
        async def start(self, **kwargs: Any) -> None:
            pass

        async def request(self, *args: Any, **kwargs: Any) -> None:
            await asyncio.Event().wait()

        async def stop(self) -> None:
            stopped.append(True)

    monkeypatch.setattr(codex, "CodexAppServerClient", lambda **kwargs: Client())
    monkeypatch.setattr(codex, "MODEL_CATALOG_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(codex.CodexModelCatalogUnavailable):
        asyncio.run(codex.probe_codex_model_catalog("/scripted/codex"))
    assert stopped == [True]

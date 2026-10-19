"""Creation reports startup separately from the durable orchestrator save."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from click.testing import CliRunner, Result

from planner.cli.main import main


def _create(
    monkeypatch: pytest.MonkeyPatch,
    startup: dict[str, Any] | None,
    *,
    as_json: bool = False,
    command: tuple[str, ...] = ("orchestrator",),
    goal: str | None = None,
) -> tuple[Result, dict[str, Any]]:
    payload = {"id": "si_saved", "title": "A useful goal", "startup": startup}
    if goal is None and startup is not None:
        goal = "Do the work"

    def respond(method: str, url: str, **kwargs: Any) -> httpx.Response:
        assert method == "POST"
        assert url.endswith("/api/items")
        assert kwargs["json"]["goal"] == (goal or "")
        return httpx.Response(200, json=payload, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "request", respond)
    args = [*command, "create", "--title", "A useful goal", "--project-id", "p_test"]
    if goal is not None:
        args.extend(["--goal-file", "-"])
    if as_json:
        args.append("--json")
    return CliRunner().invoke(main, args, input=goal), payload


@pytest.mark.parametrize("fate", ["started", "queued", "injected"])
def test_create_reports_delivery_success(monkeypatch: pytest.MonkeyPatch, fate: str) -> None:
    result, _ = _create(
        monkeypatch,
        {"fate": fate, "conversation_id": "conv_saved", "queue_position": 2},
    )
    assert result.exit_code == 0, result.output
    assert f"Created orchestrator si_saved. Startup {fate}." in result.stdout
    if fate == "queued":
        assert "Queue position: 2." in result.stdout
    assert "Retry" not in result.stdout


@pytest.mark.parametrize("goal", [None, "", " \n\t"])
def test_create_without_a_goal_reports_idle(
    monkeypatch: pytest.MonkeyPatch, goal: str | None
) -> None:
    result, _ = _create(monkeypatch, None, goal=goal)
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "Created orchestrator si_saved. Idle (no goal)."


def test_refused_start_keeps_saved_address_and_recovery_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _ = _create(
        monkeypatch,
        {"fate": "refused", "conversation_id": None, "refusal_reason": "busy"},
    )
    assert result.exit_code == 0, result.output
    assert "Startup refused. busy" in result.stdout
    assert "The orchestrator is saved." in result.stdout
    assert "panels orchestrator show si_saved" in result.stdout
    assert 'panels send-message --sprint-item si_saved --message "<goal>"' in result.stdout
    assert "Startup started" not in result.stdout


@pytest.mark.parametrize("fate", ["uncertain", "failed"])
def test_unconfirmed_start_requires_inspection_before_resend(
    monkeypatch: pytest.MonkeyPatch, fate: str
) -> None:
    result, _ = _create(
        monkeypatch,
        {"fate": fate, "conversation_id": "conv_saved", "error": "Inspect before retry"},
    )
    assert result.exit_code == 0, result.output
    assert f"Startup {fate}. Inspect before retry" in result.stdout
    assert "The orchestrator is saved." in result.stdout
    assert "panels orchestrator show si_saved" in result.stdout
    assert "Check its conversation before any resend. Do not create it again." in result.stdout
    assert "send-message" not in result.stdout
    assert "Startup started" not in result.stdout


@pytest.mark.parametrize("command", [("orchestrator",), ("sprint", "item")])
@pytest.mark.parametrize("fate", [None, "started", "refused", "uncertain", "failed"])
def test_json_retains_complete_creation_response_for_both_aliases(
    monkeypatch: pytest.MonkeyPatch, command: tuple[str, ...], fate: str | None
) -> None:
    startup = {"fate": fate, "conversation_id": "conv_saved"} if fate else None
    result, payload = _create(monkeypatch, startup, as_json=True, command=command)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == payload
    assert result.stderr == ""

"""Packaging contract for the local CLI name, plus worker my-ticket surfacing."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from planner.cli import http
from planner.cli import main as cli_main


def test_panels_is_the_startup_console_script() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]

    assert scripts["panels"] == "planner.cli.main:main"
    assert "planner" not in scripts


@pytest.mark.parametrize(
    "command",
    [
        ["ticket", "show"],
        ["worker", "my-ticket"],
        ["sprint", "show"],
        ["sprint", "item", "show"],
        ["day", "show"],
        ["project", "show"],
    ],
)
def test_record_read_help_teaches_the_optional_part_list(command: list[str]) -> None:
    result = CliRunner().invoke(cli_main.main, [*command, "--help"])

    assert result.exit_code == 0, result.output
    assert "[PART_NAMES]" in result.output
    assert "selected comma-separated PART_NAMES" in result.output
    assert "Print machine-readable JSON" in result.output


def test_worker_my_ticket_human_line_surfaces_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    # t_tt05: the human line names the resolved worker specialist so the agent can
    # self-route with skill_view("<name>"). The server computes `worker` from the type.
    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "id": "t_demo",
            "worker_type": "coding",
            "stage": "needs_success",
            "ticket_status": "user",
            "priority": "P2",
            "title": "Demo",
            "worker": "panels-worker-coding",
            "fields": {"success": {"value": None, "user_note": None, "proposal": None}},
        }

    monkeypatch.setattr(http, "send", fake_send)
    runner = CliRunner()
    result = runner.invoke(
        cli_main.main,
        ["worker", "my-ticket"],
        env={"PLAN_TICKET_ID": "t_demo"},
    )

    assert result.exit_code == 0, result.output
    assert "worker: panels-worker-coding" in result.output


def test_worker_my_ticket_requests_worker_self_for_explicit_ticket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_paths: list[str] = []

    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        requested_paths.append(path)
        return {
            "id": "t_correct",
            "worker_type": "exploration",
            "stage": "needs_understanding",
            "ticket_status": "agent",
            "priority": "P1",
            "title": "Correct ticket",
            "worker": "panels-worker-exploration",
            "fields": {"understanding": {"value": None, "user_note": None, "proposal": None}},
        }

    monkeypatch.setattr(http, "send", fake_send)
    result = CliRunner().invoke(
        cli_main.main,
        ["worker", "my-ticket"],
        env={"PLAN_TICKET_ID": "t_correct"},
    )

    assert result.exit_code == 0, result.output
    assert requested_paths == ["/api/tickets/t_correct/worker-self"]
    assert "id: t_correct" in result.output
    assert "stage: needs_understanding" in result.output


def test_worker_request_user_help_is_a_no_payload_worker_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, Any]] = []

    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        return {"id": "t_help"}

    monkeypatch.setattr(http, "send", fake_send)
    result = CliRunner().invoke(
        cli_main.main,
        ["worker", "request-user-help"],
        env={"PLAN_TICKET_ID": "t_help"},
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        ("POST", "/api/tickets/t_help/request-user-help", {"as_json": False})
    ]
    assert "user help requested on t_help" in result.output

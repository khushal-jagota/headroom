"""Packaging contract for the local CLI name, plus worker my-ticket surfacing."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from planner.cli import main as cli_main


def test_panels_is_the_startup_console_script() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]

    assert scripts["panels"] == "planner.cli.main:main"
    assert "planner" not in scripts


def test_worker_my_ticket_human_line_surfaces_worker(monkeypatch) -> None:
    # t_tt05: the human line names the resolved worker specialist so the agent can
    # self-route with skill_view("<name>"). The server computes `worker` from the type.
    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "id": "t_demo",
            "stage": "needs_success",
            "priority": "P2",
            "title": "Demo",
            "worker": "panels-worker-coding",
        }

    monkeypatch.setattr(cli_main.http, "send", fake_send)
    runner = CliRunner()
    result = runner.invoke(
        cli_main.main,
        ["worker", "my-ticket"],
        env={"HERMES_SESSION_KEY": "sess_demo"},
    )

    assert result.exit_code == 0, result.output
    assert "worker: panels-worker-coding" in result.output


def test_worker_my_ticket_prefers_live_session_identity(monkeypatch) -> None:
    requested_paths: list[str] = []

    def fake_send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        requested_paths.append(path)
        return {
            "id": "t_correct",
            "stage": "needs_understanding",
            "priority": "P1",
            "title": "Correct ticket",
            "worker": "panels-worker-exploration",
        }

    monkeypatch.setattr(cli_main.http, "send", fake_send)
    result = CliRunner().invoke(
        cli_main.main,
        ["worker", "my-ticket"],
        env={
            "HERMES_UI_SESSION_ID": "live_correct",
            "HERMES_SESSION_KEY": "stale_other_ticket",
        },
    )

    assert result.exit_code == 0, result.output
    assert requested_paths == ["/api/tickets/by-live-session/live_correct"]
    assert "t_correct needs_understanding" in result.output

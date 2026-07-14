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

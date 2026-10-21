"""Calendar reads disappear while ordinary manager and Idea reads remain."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner
from fastapi.testclient import TestClient

from planner.cli.main import main
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect
from planner.core.server import create_app


def test_calendar_reads_and_commands_are_removed(tmp_path: Path) -> None:
    path = tmp_path / "calendar-retirement.db"
    config = load_config(path=None, env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(path)})
    clock = build_clock(config)
    with TestClient(create_app(config, clock, lambda: connect(str(path)))) as client:
        for route in (
            "/api/day/today",
            "/api/day/2026-08-03",
            "/api/day/2026-08-03/tickets",
            "/api/sprints",
            "/api/sprint/current",
            "/api/sprints/historical/tracking",
        ):
            assert client.get(route).status_code == 404
        assert client.get("/api/managers?detail=full").json() == {"managers": []}
        assert client.get("/api/ideas?detail=full").json() == {"ideas": []}

    runner = CliRunner()
    for arguments in (("day", "show", "today"), ("sprint", "list"), ("sprint", "show")):
        result = runner.invoke(main, arguments)
        assert result.exit_code != 0
        assert "is not a command" in result.output
    for arguments in (("manager", "--help"), ("sprint", "item", "--help")):
        result = runner.invoke(main, arguments)
        assert result.exit_code == 0, result.output
        assert "workspace" in result.output

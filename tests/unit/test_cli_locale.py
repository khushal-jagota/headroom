"""CLI locale inputs reach their canonical API writers."""

import json
from typing import Any

import pytest
from click.testing import CliRunner

from planner.cli import http
from planner.cli.main import main


@pytest.mark.parametrize(
    ("arguments", "method", "path", "expected"),
    [
        (
            ["ticket", "create", "--input-json", "-", "--locale", "macos"],
            "POST",
            "/api/tickets",
            {"title": "Work", "locale": "macos"},
        ),
        (
            ["ticket", "move", "t_one", "--locale", "macos"],
            "POST",
            "/api/tickets/t_one/move",
            {"locale": "macos"},
        ),
        (
            [
                "schedule",
                "create",
                "--title",
                "Work",
                "--worker-type",
                "coding",
                "--time",
                "14:30",
                "--locale",
                "macos",
            ],
            "POST",
            "/api/schedules",
            {"locale": "macos"},
        ),
        (
            ["schedule", "set", "schedule_one", "locale", "--value", "vps"],
            "PATCH",
            "/api/schedules/schedule_one",
            {"locale": "vps"},
        ),
        (
            ["project", "set", "p_one", "requires-macos", "--value", "true"],
            "PATCH",
            "/api/projects/p_one",
            {"requires_macos": True},
        ),
    ],
)
def test_locale_cli_inputs(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    method: str,
    path: str,
    expected: dict[str, Any],
) -> None:
    calls = []

    def send(actual_method: str, actual_path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((actual_method, actual_path, kwargs["json_body"]))
        return {
            "id": "created",
            "stage": "needs_brief",
            "supervision_receipt": "The Ticket first parks for review at Brief.",
            "local_time": "14:30",
            "cadence": "daily",
            "worker_type": "coding",
        }

    monkeypatch.setattr(http, "send", send)
    result = CliRunner().invoke(main, [*arguments, "--json"], input=json.dumps({"title": "Work"}))
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0][:2] == (method, path)
    assert expected.items() <= calls[0][2].items()

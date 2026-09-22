"""The Worker restart CLI reports the delivery result without overstating it."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from planner.cli.main import main as cli_main


def _uncertain_response(*_args: Any, **_kwargs: Any) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "ticket_id": "t_example",
            "killed_conversation_id": "conv_old",
            "killed_conversation_looked_running": False,
            "employee_configuration": {
                "employee_backend": "codex",
                "employee_launch_model": "gpt-5",
                "employee_launch_reasoning_effort": "medium",
            },
            "ticket_status": "errored",
            "conversation_id": "conv_new",
            "started": False,
            "delivery_fate": "uncertain",
            "not_started_because": None,
        },
        request=httpx.Request("POST", "http://test/api/tickets/t_example/restart-worker"),
    )


def test_human_restart_output_does_not_claim_success_for_uncertain_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "request", _uncertain_response)

    result = CliRunner().invoke(cli_main, ["ticket", "restart-worker", "t_example"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == (
        "t_example start delivery is uncertain. The Worker claim remains held."
    )
    assert "restarted" not in result.stdout


def test_json_restart_output_exposes_uncertain_delivery_fate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "request", _uncertain_response)

    result = CliRunner().invoke(
        cli_main,
        ["ticket", "restart-worker", "t_example", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["delivery_fate"] == "uncertain"

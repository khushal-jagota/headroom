from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner

from planner.cli import http
from planner.cli.main import main


@pytest.mark.parametrize(
    ("args", "path", "body", "output"),
    [
        (
            [
                "sprint",
                "item",
                "supervisor",
                "approve",
                "si_one",
                "t_one",
                "--ceiling",
                "done",
                "--at-cap",
                "propose",
            ],
            "/api/items/si_one/supervisor/tickets/t_one/approve",
            {"next_ceiling": "done", "at_cap": "propose"},
            "t_one proposal approved",
        ),
        (
            [
                "sprint",
                "item",
                "supervisor",
                "reject",
                "si_one",
                "t_one",
                "--message",
                "Keep the proof focused.",
            ],
            "/api/items/si_one/supervisor/tickets/t_one/reject",
            {"message": "Keep the proof focused."},
            "t_one proposal rejected",
        ),
    ],
)
def test_supervisor_review_commands_call_the_scoped_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    path: str,
    body: dict[str, Any],
    output: str,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_send(method: str, request_path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, request_path, kwargs))
        return {"id": "t_one"}

    monkeypatch.setattr(http, "send", fake_send)
    result = CliRunner().invoke(main, args)

    assert result.exit_code == 0, result.output
    assert calls == [("POST", path, {"as_json": False, "json_body": body})]
    assert output in result.output


def test_supervisor_reject_requires_one_guidance_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("no HTTP request expected")

    monkeypatch.setattr(http, "send", explode)
    result = CliRunner().invoke(
        main,
        ["sprint", "item", "supervisor", "reject", "si_one", "t_one"],
    )

    assert result.exit_code != 0
    assert "reject requires exactly one" in result.output


def test_supervisor_message_worker_names_the_current_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_send(method: str, request_path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, request_path, kwargs))
        return {"fate": "queued"}

    monkeypatch.setattr(http, "send", fake_send)
    result = CliRunner().invoke(
        main,
        [
            "sprint",
            "item",
            "supervisor",
            "message-worker",
            "si_one",
            "t_one",
            "--conversation-id",
            "conv_current",
            "--message",
            "Check the evidence.",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "POST",
            "/api/items/si_one/supervisor/tickets/t_one/message",
            {
                "as_json": False,
                "json_body": {
                    "conversation_id": "conv_current",
                    "message": "Check the evidence.",
                },
            },
        )
    ]


def test_supervisor_history_requests_one_bounded_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_send(method: str, request_path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, request_path, kwargs))
        return {"events": []}

    monkeypatch.setattr(http, "send", fake_send)
    result = CliRunner().invoke(
        main,
        [
            "sprint",
            "item",
            "supervisor",
            "history",
            "si_one",
            "t_one",
            "--limit",
            "12",
            "--before-sequence",
            "30",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "GET",
            "/api/items/si_one/supervisor/tickets/t_one/history",
            {
                "as_json": False,
                "params": {"limit": 12, "before_sequence": 30},
            },
        )
    ]


def test_supervisor_restart_worker_sends_the_whole_launch_configuration_or_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_send(method: str, request_path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, request_path, kwargs))
        return {
            "started": True,
            "not_started_because": None,
            "employee_configuration": {
                "employee_backend": "claude",
                "employee_launch_model": "opus",
                "employee_launch_reasoning_effort": None,
            },
        }

    monkeypatch.setattr(http, "send", fake_send)
    plain = CliRunner().invoke(
        main,
        ["sprint", "item", "supervisor", "restart-worker", "si_one", "t_one"],
    )
    configured = CliRunner().invoke(
        main,
        [
            "sprint",
            "item",
            "supervisor",
            "restart-worker",
            "si_one",
            "t_one",
            "--backend",
            "claude",
            "--model",
            "opus",
        ],
    )

    assert plain.exit_code == 0, plain.output
    assert configured.exit_code == 0, configured.output
    assert "t_one restarted on claude opus" in configured.output
    assert calls == [
        (
            "POST",
            "/api/items/si_one/supervisor/tickets/t_one/restart-worker",
            {"as_json": False, "json_body": {}},
        ),
        (
            "POST",
            "/api/items/si_one/supervisor/tickets/t_one/restart-worker",
            {
                "as_json": False,
                "json_body": {
                    "employee_backend": "claude",
                    "employee_launch_model": "opus",
                    "employee_launch_reasoning_effort": None,
                },
            },
        ),
    ]


def test_supervisor_restart_worker_refuses_half_a_launch_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model id belongs to the backend that named it, so neither travels alone."""

    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("no HTTP request expected")

    monkeypatch.setattr(http, "send", explode)
    result = CliRunner().invoke(
        main,
        [
            "sprint",
            "item",
            "supervisor",
            "restart-worker",
            "si_one",
            "t_one",
            "--backend",
            "claude",
        ],
    )

    assert result.exit_code != 0
    assert "needs --backend and --model together" in result.output

"""CLI contract for the top-level Send Message command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from planner.cli import http
from planner.cli.main import main


@pytest.mark.parametrize(
    ("selector", "recipient"),
    [
        (("--owner",), {"kind": "owner", "id": "owner"}),
        (("--chief",), {"kind": "chief", "id": "chief"}),
        (("--ticket", "t_one"), {"kind": "ticket", "id": "t_one"}),
        (("--sprint-item", "si_one"), {"kind": "sprint_item", "id": "si_one"}),
    ],
)
def test_each_selector_posts_one_general_send(
    monkeypatch: pytest.MonkeyPatch,
    selector: tuple[str, ...],
    recipient: dict[str, str],
) -> None:
    recorded: dict[str, Any] = {}

    def send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        recorded.update({"method": method, "path": path, **kwargs})
        return {
            "conversation_id": "c_one",
            "fate": "started",
        }

    monkeypatch.setattr(http, "send", send)
    result = CliRunner().invoke(main, ["send-message", *selector, "--message", "Hello", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["fate"] == "started"
    assert recorded == {
        "method": "POST",
        "path": "/api/messages/send",
        "as_json": True,
        "json_body": {"target": recipient, "message": "Hello", "mode": "steer"},
        "request_actor": "ordinary",
    }


@pytest.mark.parametrize("mode", ["queue", "steer", "send_now"])
def test_explicit_mode_is_sent_to_the_api(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    captured: dict[str, Any] = {}

    def send(_method: str, _path: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs["json_body"])
        return {"conversation_id": "c_one", "fate": "injected"}

    monkeypatch.setattr(http, "send", send)
    result = CliRunner().invoke(
        main,
        ["send-message", "--chief", "--message", "Hello", "--mode", mode, "--json"],
    )

    assert result.exit_code == 0, result.output
    assert captured["mode"] == mode


def test_employee_to_owner_reports_the_recorded_fate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def send(_method: str, _path: str, **_kwargs: Any) -> dict[str, Any]:
        return {
            "target": {"kind": "owner", "id": "owner"},
            "conversation_id": "c_sender",
            "fate": "recorded",
        }

    monkeypatch.setattr(http, "send", send)
    result = CliRunner().invoke(
        main,
        ["send-message", "--owner", "--message", "Done.", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "target": {"kind": "owner", "id": "owner"},
        "conversation_id": "c_sender",
        "fate": "recorded",
    }


@pytest.mark.parametrize("body_file", ["message.txt", "-"])
def test_body_file_and_stdin_preserve_message_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body_file: str,
) -> None:
    message_path = tmp_path / "message.txt"
    message_path.write_text("From a file.\n", encoding="utf-8")
    captured: dict[str, Any] = {}

    def send(_method: str, _path: str, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs["json_body"])
        return {"conversation_id": "c_one", "fate": "started"}

    monkeypatch.setattr(http, "send", send)
    selected_file = str(message_path) if body_file != "-" else "-"
    result = CliRunner().invoke(
        main,
        ["send-message", "--chief", "--body-file", selected_file, "--json"],
        input="From a file.\n" if selected_file == "-" else None,
    )

    assert result.exit_code == 0, result.output
    assert captured["message"] == "From a file.\n"


@pytest.mark.parametrize(
    "args",
    [
        ("--message", "Hello"),
        ("--chief", "--ticket", "t_one", "--message", "Hello"),
        ("--chief",),
        ("--chief", "--message", "Hello", "--body-file", "-"),
        ("--chief", "--message", "   "),
    ],
)
def test_cli_rejects_ambiguous_or_empty_input(args: tuple[str, ...]) -> None:
    result = CliRunner().invoke(main, ["send-message", *args, "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stderr)["error"]["code"] == "validation"


def test_cli_does_not_expose_an_arbitrary_agent_target() -> None:
    result = CliRunner().invoke(
        main,
        ["send-message", "--agent", "reviewer", "--message", "Hello"],
    )

    assert result.exit_code == 2
    assert "No such option '--agent'" in result.output


def test_uncertain_fate_is_preserved_in_json_and_normal_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {"conversation_id": "c_one", "fate": "uncertain"}
    monkeypatch.setattr(http, "send", lambda *_args, **_kwargs: response)

    json_result = CliRunner().invoke(
        main,
        [
            "send-message",
            "--chief",
            "--message",
            "Guide it",
            "--mode",
            "steer",
            "--json",
        ],
    )
    normal_result = CliRunner().invoke(
        main,
        ["send-message", "--chief", "--message", "Guide it", "--mode", "steer"],
    )

    assert json_result.exit_code == 0, json_result.output
    assert json.loads(json_result.stdout) == response
    assert normal_result.exit_code == 0, normal_result.output
    assert normal_result.stdout == "message uncertain\n"


def test_cli_rejects_a_mode_outside_the_public_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send = Mock()
    monkeypatch.setattr(http, "send", send)

    result = CliRunner().invoke(
        main,
        ["send-message", "--chief", "--message", "Hello", "--mode", "run_when_free"],
    )

    assert result.exit_code == 2
    assert "Invalid value for '--mode'" in result.output
    send.assert_not_called()

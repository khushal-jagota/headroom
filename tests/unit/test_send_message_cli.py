"""CLI contract for the top-level Send Message command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from planner.cli import http
from planner.cli.main import main


@pytest.mark.parametrize(
    ("selector", "target"),
    [
        (("--chief",), {"type": "chief"}),
        (("--ticket", "t_one"), {"type": "ticket", "id": "t_one"}),
        (("--sprint-item", "si_one"), {"type": "sprint_item", "id": "si_one"}),
        (("--agent", "reviewer"), {"type": "agent", "id": "reviewer"}),
    ],
)
def test_each_selector_posts_one_general_send(
    monkeypatch: pytest.MonkeyPatch,
    selector: tuple[str, ...],
    target: dict[str, str],
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
        "json_body": {"target": target, "message": "Hello"},
        "request_actor": "ordinary",
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

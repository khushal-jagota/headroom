"""CLI contract for the top-level Send Message command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from tests.unit.test_sprint_item_supervisors import _app, _create_item, _supervisor_headers

from planner.cli import http
from planner.cli.main import main
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem


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


def test_sprint_item_target_uses_existing_api_and_authenticated_supervisor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        sender = _create_item(client, "Sender")
        recipient = _create_item(client, "Recipient")
        requests: list[tuple[str, str, Any]] = []

        def send(method: str, path: str, *, as_json: bool, json_body: Any) -> Any:
            assert as_json
            requests.append((method, path, json_body))
            response = client.request(
                method, path, json=json_body, headers=_supervisor_headers(str(sender["id"]))
            )
            assert response.status_code == 200, response.text
            return response.json()

        monkeypatch.setattr(http, "send", send)
        result = CliRunner().invoke(
            main,
            [
                "send-message",
                "--sprint-item",
                str(recipient["id"]),
                "--message",
                "Coordinate",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        assert requests == [
            (
                "POST",
                "/api/messages/send",
                {
                    "target": {"kind": "sprint_item", "id": recipient["id"]},
                    "message": "Coordinate",
                    "mode": "steer",
                },
            )
        ]
        conversation_id = json.loads(result.output)["conversation_id"]
        system = app.state.conversation_system
        assert isinstance(system, InMemoryConversationSystem)
        assert system.backend_prompt_writes(conversation_id)[-1].text.endswith(
            f"Sprint Item {sender['id']}:\nCoordinate"
        )

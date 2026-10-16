"""Lifecycle commands carry bounded filters and fresh-record preconditions."""

from typing import Any

import pytest
from click.testing import CliRunner

from planner.cli import http
from planner.cli.main import main


@pytest.mark.parametrize("action", ["backlog", "resume", "retire"])
def test_lifecycle_command_passes_fresh_record_guard(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    requests: list[tuple[str, str, dict[str, Any]]] = []

    def send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        requests.append((method, path, kwargs))
        return {"id": "t_example"}

    monkeypatch.setattr(http, "send", send)
    arguments = ["ticket", action, "t_example", "--expected-record-sha256", "a" * 64, "--json"]
    if action == "retire":
        arguments += ["--reason", "Superseded"]
    result = CliRunner().invoke(main, arguments)
    assert result.exit_code == 0, result.output
    assert requests[0][0:2] == ("POST", f"/api/tickets/t_example/{action}")
    assert requests[0][2]["json_body"]["expected_record_sha256"] == "a" * 64
    if action == "retire":
        assert requests[0][2]["json_body"]["reason"] == "Superseded"

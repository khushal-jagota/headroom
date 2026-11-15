"""Queued CLI replies exit before ordinary handlers interpret a domain result."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from planner.cli import http
from planner.cli.main import main
from planner.executor.protocol import IpcOperation, IpcResponse, JsonObject, UnconfirmedCliResult


def test_worker_recap_queued_exits_three_before_domain_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PLAN_EXECUTOR_ROOT", str(tmp_path))
    monkeypatch.setenv("PLAN_TICKET_ID", "t_example")

    def queued(operation: IpcOperation, payload: JsonObject) -> IpcResponse:
        assert operation is IpcOperation.write
        assert payload == {
            "operation": "ticket.edit",
            "arguments": {"ticket_id": "t_example", "body": {"recap": "Keep this recap"}},
        }
        return IpcResponse(
            "request", None, None, UnconfirmedCliResult("request", "stable_submission", "queued")
        )

    monkeypatch.setattr("planner.executor.ipc_client.request", queued)
    result = CliRunner().invoke(main, ["worker", "recap", "--json"], input="Keep this recap")
    assert result.exit_code == 3
    assert json.loads(result.stdout) == {
        "request_id": "request",
        "submission_id": "stable_submission",
        "publication": "queued",
        "acceptance": "unconfirmed",
    }
    assert not result.stderr


@pytest.mark.parametrize(
    "origin",
    [
        "http://panels.example",
        "https://user@panels.example",
        "https://panels.example/api",
        "https://panels.example?secret=1",
    ],
)
def test_launcher_refuses_non_origin_urls(origin: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import Mock

    from planner.executor import mac_service as mac
    from planner.executor.cli import executor_group

    constructor = Mock()
    monkeypatch.setattr(mac, "MacService", constructor)
    result = CliRunner().invoke(
        executor_group,
        [
            "run",
            "--executor-id",
            "owner-mac",
            "--application-revision",
            "a" * 40,
            "--panels-server-url",
            origin,
        ],
    )
    assert result.exit_code != 0
    constructor.assert_not_called()


@pytest.mark.parametrize("command", ["prepare", "register", "show", "stop"])
def test_mac_preview_cli_refuses_before_any_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, command: str
) -> None:
    monkeypatch.setenv("PLAN_EXECUTOR_ROOT", str(tmp_path))

    def no_transport(*args: object, **kwargs: object) -> None:
        raise AssertionError("Mac preview commands must refuse before transport")

    monkeypatch.setattr(http, "send", no_transport)
    arguments = ["preview", command]
    if command == "register":
        arguments += ["--port", "3001"]
    result = CliRunner().invoke(main, arguments)
    assert result.exit_code == 1
    assert "previews are unavailable for Tickets running on the Mac" in result.output


def test_executor_send_back_carries_the_inspected_revision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The CLI must reach IPC without replacing the caller's inspected revision."""
    monkeypatch.setenv("PLAN_EXECUTOR_ROOT", str(tmp_path))

    def accepted(operation: IpcOperation, payload: JsonObject) -> IpcResponse:
        if operation is IpcOperation.read:
            assert payload == {
                "path": "/api/tickets", "params": {"detail": "full", "id": "t_target"},
            }
            return IpcResponse("read", {"pending_proposal": {"revision": 7}}, None)
        assert operation is IpcOperation.write
        assert payload == {
            "operation": "ticket.send_back",
            "arguments": {
                "ticket_id": "t_target",
                "body": {
                    "message": "Fix the missing evidence", "expected_proposal_revision": 7,
                    "decision_id": "t_target:7:reject",
                },
            },
        }
        return IpcResponse("request", {"id": "t_target"}, None)

    monkeypatch.setattr("planner.executor.ipc_client.request", accepted)
    result = CliRunner().invoke(
        main, ["ticket", "proposal", "t_target", "revise",
               "--body-file", "-"],
        input="Fix the missing evidence",
    )
    assert result.exit_code == 0, result.output

"""Queued CLI replies exit before ordinary handlers interpret a domain result."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from planner.cli import http
from planner.cli.main import main
from planner.executor.protocol import IpcOperation, IpcResponse, JsonObject, QueuedCliResult


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
        return IpcResponse("request", None, None, QueuedCliResult("stable_submission"))

    monkeypatch.setattr("planner.executor.ipc_client.request", queued)
    result = CliRunner().invoke(main, ["worker", "recap", "--json"], input="Keep this recap")
    assert result.exit_code == 3
    assert json.loads(result.stdout) == {
        "submission_id": "stable_submission",
        "disposition": "queued",
        "acceptance": "unconfirmed",
    }
    assert not result.stderr


def test_durable_authority_refusal_preserves_cli_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PLAN_EXECUTOR_ROOT", str(tmp_path))
    refusal: JsonObject = {
        "code": "agent_forbidden",
        "message": "another Ticket's record",
        "detail": {},
    }
    monkeypatch.setattr(
        "planner.executor.ipc_client.request",
        lambda operation, payload: IpcResponse("request", None, refusal),
    )
    with pytest.raises(SystemExit) as stopped:
        http.send("PATCH", "/api/tickets/t_other", as_json=True, json_body={"recap": "change"})
    assert stopped.value.code == 1


def test_text_read_uses_live_executor_ipc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PLAN_EXECUTOR_ROOT", str(tmp_path))

    def read(operation: IpcOperation, payload: JsonObject) -> IpcResponse:
        assert operation is IpcOperation.read
        assert payload["path"] == "/api/tickets/t_example/copy-text"
        return IpcResponse("request", {"text": "canonical VPS copy"}, None)

    monkeypatch.setattr("planner.executor.ipc_client.request", read)
    assert (
        http.send_text("GET", "/api/tickets/t_example/copy-text", as_json=False)
        == "canonical VPS copy"
    )


def test_explicit_launcher_passes_https_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import AsyncMock, Mock

    from planner.executor import mac_service as mac
    from planner.executor.cli import executor_group

    executor = Mock(run=AsyncMock())
    constructor = Mock(return_value=executor)
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
            "https://panels.example:9443",
        ],
    )
    assert result.exit_code == 0, result.output
    assert constructor.call_args.kwargs["executor_id"] == "owner-mac"
    assert constructor.call_args.kwargs["panels_server_url"] == "https://panels.example:9443"
    executor.run.assert_awaited_once()


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

"""The resident's explicit SSH inputs reach the executor without ambient defaults."""

from pathlib import Path
from unittest.mock import AsyncMock, Mock

from click.testing import CliRunner

from planner.executor.cli import executor_group
from planner.executor.contracts import ExecutorSshConfig


def test_explicit_launcher_passes_all_ssh_inputs(tmp_path: Path, monkeypatch) -> None:
    from planner.executor import mac

    identity, known_hosts = tmp_path / "identity", tmp_path / "known_hosts"
    identity.touch()
    known_hosts.touch()
    executor = Mock()
    executor.run = AsyncMock()
    constructor = Mock(return_value=executor)
    monkeypatch.setattr(mac, "MacExecutor", constructor)
    result = CliRunner().invoke(
        executor_group,
        [
            "run",
            "--ssh-host",
            "peer.example",
            "--ssh-port",
            "2222",
            "--ssh-user",
            "executor",
            "--ssh-identity-file",
            str(identity),
            "--ssh-known-hosts",
            str(known_hosts),
            "--application-revision",
            "a" * 40,
            "--panels-server-url",
            "https://panels.example",
            "--remote-command",
            "panels executor relay",
        ],
    )
    assert result.exit_code == 0, result.output
    assert constructor.call_args.kwargs["ssh_config"] == ExecutorSshConfig(
        "peer.example", 2222, "executor", identity, known_hosts
    )
    assert constructor.call_args.kwargs["remote_command"] == "panels executor relay"
    executor.run.assert_awaited_once()

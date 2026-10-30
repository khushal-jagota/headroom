from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from planner.environments.cli import environment
from planner.environments.executor_launch_contracts import (
    ExecutorLaunchConfigurationError,
    VpsExecutorLaunchConfiguration,
)
from planner.environments.vps_launch import (
    MAX_VPS_LAUNCH_CONFIGURATION_BYTES,
    read_vps_launch,
    restore_vps_launch,
    snapshot_vps_launch,
    write_vps_launch,
)


def test_explicit_remote_local_and_absence_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "data/executor-launch.json"
    assert read_vps_launch(path) is None
    remote = VpsExecutorLaunchConfiguration("remote", "owner-mac")
    write_vps_launch(path, remote)
    assert read_vps_launch(path) == remote
    assert path.stat().st_mode & 0o777 == 0o600
    prior = snapshot_vps_launch(path)
    write_vps_launch(path, VpsExecutorLaunchConfiguration("local"))
    assert read_vps_launch(path) == VpsExecutorLaunchConfiguration("local")
    restore_vps_launch(path, prior)
    assert path.read_bytes() == prior
    restore_vps_launch(path, None)
    assert read_vps_launch(path) is None


@pytest.mark.parametrize(
    "payload",
    [
        "{}",
        "[]",
        "{bad",
        '{"version":true,"mode":"local","socket":null}',
        '{"version":2,"mode":"local","socket":null}',
        '{"version":1,"mode":"remote","socket":"relative"}',
        '{"version":1,"mode":"local","socket":"/tmp/sock"}',
        '{"version":1,"mode":"remote","socket":12}',
        '{"version":1,"mode":[],"socket":null}',
        '{"version":1,"mode":"remote","socket":"/tmp/../other"}',
        " " * (MAX_VPS_LAUNCH_CONFIGURATION_BYTES + 1),
    ],
)
def test_malformed_configuration_refuses(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "executor-launch.json"
    path.write_text(payload)
    path.chmod(0o600)
    with pytest.raises(ExecutorLaunchConfigurationError):
        read_vps_launch(path)


def test_nonowner_access_or_symlink_refuses_read_and_write(tmp_path: Path) -> None:
    path = tmp_path / "executor-launch.json"
    write_vps_launch(path, VpsExecutorLaunchConfiguration("local"))
    path.chmod(0o644)
    with pytest.raises(ExecutorLaunchConfigurationError, match="owner-only"):
        read_vps_launch(path)
    with pytest.raises(ExecutorLaunchConfigurationError, match="owner-only"):
        write_vps_launch(path, VpsExecutorLaunchConfiguration("local"))
    path.chmod(0o600)
    alias = tmp_path / "link"
    alias.symlink_to(path)
    with pytest.raises(ExecutorLaunchConfigurationError, match="symbolic link"):
        read_vps_launch(alias)
    with pytest.raises(ExecutorLaunchConfigurationError, match="symbolic link"):
        write_vps_launch(alias, VpsExecutorLaunchConfiguration("local"))


def test_configuration_cli_requires_explicit_remote_identity(tmp_path: Path) -> None:
    path = tmp_path / "executor-launch.json"
    runner = CliRunner()
    remote = runner.invoke(
        environment, ["configure-vps-launch", "--path", str(path), "--mode", "remote"]
    )
    assert remote.exit_code != 0
    assert "executor ID" in remote.output
    assert not path.exists()
    local = runner.invoke(
        environment, ["configure-vps-launch", "--path", str(path), "--mode", "local"]
    )
    assert local.exit_code == 0, local.output
    assert json.loads(path.read_bytes()) == {"version": 2, "mode": "local", "executor_id": None}
    executor_id = "owner-mac"
    remote = runner.invoke(
        environment,
        [
            "configure-vps-launch",
            "--path",
            str(path),
            "--mode",
            "remote",
            "--executor-id",
            executor_id,
        ],
    )
    assert remote.exit_code == 0, remote.output
    assert read_vps_launch(path) == VpsExecutorLaunchConfiguration("remote", executor_id)


@pytest.mark.parametrize("mode", ["local", "remote"])
def test_version_one_refuses_without_rewriting_bytes(tmp_path: Path, mode: str) -> None:
    path = tmp_path / "executor-launch.json"
    raw = json.dumps(
        {"version": 1, "mode": mode, "socket": None if mode == "local" else "/tmp/old"}
    ).encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    with pytest.raises(ExecutorLaunchConfigurationError):
        snapshot_vps_launch(path)
    with pytest.raises(ExecutorLaunchConfigurationError):
        restore_vps_launch(path, raw)
    assert path.read_bytes() == raw

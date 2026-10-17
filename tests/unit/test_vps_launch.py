from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from planner.environments.resident.cli import main
from planner.environments.resident.contracts import ResidentError, VpsExecutorLaunchConfiguration
from planner.environments.resident.vps_launch import (
    MAX_VPS_LAUNCH_CONFIGURATION_BYTES,
    read_vps_launch,
    restore_vps_launch,
    snapshot_vps_launch,
    write_vps_launch,
)


def test_explicit_remote_local_and_absence_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "data/executor-launch.json"
    assert read_vps_launch(path) is None
    remote = VpsExecutorLaunchConfiguration("remote", tmp_path / "executor.sock")
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
    with pytest.raises(ResidentError):
        read_vps_launch(path)


def test_nonowner_access_or_symlink_refuses_read_and_write(tmp_path: Path) -> None:
    path = tmp_path / "executor-launch.json"
    write_vps_launch(path, VpsExecutorLaunchConfiguration("local"))
    path.chmod(0o644)
    with pytest.raises(ResidentError, match="owner-only"):
        read_vps_launch(path)
    with pytest.raises(ResidentError, match="owner-only"):
        write_vps_launch(path, VpsExecutorLaunchConfiguration("local"))
    path.chmod(0o600)
    alias = tmp_path / "link"
    alias.symlink_to(path)
    with pytest.raises(ResidentError, match="symbolic link"):
        read_vps_launch(alias)
    with pytest.raises(ResidentError, match="symbolic link"):
        write_vps_launch(alias, VpsExecutorLaunchConfiguration("local"))


def test_configuration_cli_requires_explicit_remote_socket(tmp_path: Path) -> None:
    path = tmp_path / "executor-launch.json"
    assert main(["configure-vps-launch", "--path", str(path), "--mode", "remote"]) == 2
    assert not path.exists()
    assert main(["configure-vps-launch", "--path", str(path), "--mode", "local"]) == 0
    assert json.loads(path.read_bytes()) == {"version": 1, "mode": "local", "socket": None}


def test_explicit_relay_socket_wins_over_absent_or_hostile_ambient(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from click.testing import CliRunner

    from planner.executor import cli

    requested = tmp_path / "live data/executor.sock"
    seen: list[str] = []

    class StopProbe(Exception):
        pass

    async def connection(path: str) -> None:
        seen.append(path)
        raise StopProbe("stopped before any connection or pipe")

    monkeypatch.setattr(asyncio, "open_unix_connection", connection)
    for env in (
        {},
        {"PLAN_DB_PATH": "/poison/planner.db", "PLAN_EXECUTOR_VPS_SOCKET": "/poison/sock"},
    ):
        monkeypatch.delenv("PLAN_DB_PATH", raising=False)
        monkeypatch.delenv("PLAN_EXECUTOR_VPS_SOCKET", raising=False)
        result = CliRunner().invoke(
            cli.executor_group, ["relay", "--socket-path", str(requested)], env=env
        )
        assert isinstance(result.exception, StopProbe)
    assert seen == [str(requested), str(requested)]

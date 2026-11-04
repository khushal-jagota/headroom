"""Prove the new containment boundary cannot silently fall back or lose OOM reasons."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from planner.conversation import worker_unit
from planner.conversation.worker_unit import WorkerUnit, WorkerUnitError


@pytest.fixture
def unit_setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> AsyncMock:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(worker_unit, "WORKER_TEMP_ROOT", str(tmp_path))
    monkeypatch.setattr(worker_unit, "_disk_directory", lambda path: None)
    control = AsyncMock(return_value=(0, "LoadState=loaded\nMemoryMax=25769803776\nCPUWeight=10"))
    monkeypatch.setattr(worker_unit, "_control", control)
    return control


def test_non_worker_does_not_contact_service_manager(unit_setup: AsyncMock, tmp_path: Path) -> None:
    async def scenario() -> None:
        assert await WorkerUnit.create({}, tmp_path) is None
        unit_setup.assert_not_awaited()
    asyncio.run(scenario())


def test_missing_slice_refuses_worker_instead_of_uncontained_fallback(
    unit_setup: AsyncMock, tmp_path: Path,
) -> None:
    async def scenario() -> None:
        unit_setup.return_value = (0, "LoadState=not-found\nMemoryMax=infinity\nCPUWeight=100")
        with pytest.raises(WorkerUnitError, match="lacks its resource limits"):
            await WorkerUnit.create({"PLAN_TICKET_ID": "test"}, tmp_path)
        assert not list(tmp_path.iterdir())
    asyncio.run(scenario())


def test_service_keeps_environment_private_and_temp_lifetime(
    unit_setup: AsyncMock, tmp_path: Path,
) -> None:
    async def scenario() -> None:
        secret = 'private $token "quoted" \\ slash\nsecond line'
        unit = await WorkerUnit.create({"PLAN_TICKET_ID": "test", "SECRET": secret}, tmp_path)
        assert unit is not None
        executable = tmp_path / "agent"
        executable.write_text("#!/bin/sh\n")
        executable.chmod(0o700)
        argv = unit.argv(("./agent", "argument $with %specifiers"))
        assert secret not in " ".join(argv)
        assert "--pipe" in argv and "--wait" in argv
        assert "--property=KillMode=control-group" in argv
        assert argv[-2:] == (str(executable), "argument $with %specifiers")
        assert unit.environment["TMPDIR"] == unit.environment["TMP"] == unit.environment["TEMP"]
        assert Path(unit.environment["TMPDIR"]).is_dir()
        assert unit.directory.parent == tmp_path
        assert unit.directory.stat().st_mode & 0o777 == 0o700
        environment_file = unit.directory / "environment"
        assert environment_file.stat().st_mode & 0o777 == 0o600
        expected = 'SECRET="private \\$token \\"quoted\\" \\\\ slash\nsecond line"'
        assert expected in environment_file.read_text()
        unit_setup.side_effect = [(0, "LoadState=not-found")]
        await unit.stop()
        assert not unit.directory.exists()
    asyncio.run(scenario())


def test_oom_result_survives_whole_unit_stop(unit_setup: AsyncMock, tmp_path: Path) -> None:
    async def scenario() -> None:
        unit = await WorkerUnit.create({"PLAN_TICKET_ID": "test"}, tmp_path)
        assert unit is not None
        unit_setup.reset_mock()
        unit_setup.side_effect = [
            (0, "LoadState=loaded\nResult=oom-kill"), (0, "LoadState=loaded"), (0, ""), (0, ""),
        ]
        reason = await unit.finish()
        assert reason == "Worker stopped because it exceeded its memory limit."
        assert [call.args[0] for call in unit_setup.await_args_list] == [
            "show", "show", "stop", "reset-failed",
        ]
        assert not unit.directory.exists()
        assert await unit.finish() == reason
        assert unit_setup.await_count == 4
    asyncio.run(scenario())


def test_stop_failure_retains_files_until_descendants_can_stop(
    unit_setup: AsyncMock, tmp_path: Path,
) -> None:
    async def scenario() -> None:
        unit = await WorkerUnit.create({"PLAN_TICKET_ID": "test"}, tmp_path)
        assert unit is not None
        unit_setup.side_effect = [(0, "LoadState=loaded"), (1, "manager unavailable")]
        with pytest.raises(WorkerUnitError, match="Cannot stop Worker unit"):
            await unit.stop()
        assert unit.directory.exists()
    asyncio.run(scenario())


def test_parent_slice_must_deprioritize_workers_against_app_slice(
    unit_setup: AsyncMock, tmp_path: Path,
) -> None:
    async def scenario() -> None:
        unit_setup.side_effect = [
            (0, "LoadState=loaded\nMemoryMax=25769803776\nCPUWeight=10"),
            (0, "LoadState=loaded\nCPUWeight=100"),
        ]
        with pytest.raises(WorkerUnitError, match="Install panels.slice"):
            await WorkerUnit.create({"PLAN_TICKET_ID": "test"}, tmp_path)
        assert not list(tmp_path.iterdir())
    asyncio.run(scenario())


def test_temp_root_rejects_ram_mount_even_below_disk_mount(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    mounts = (
        "1 0 8:1 / / rw - ext4 /dev/root rw\n"
        f"2 1 0:1 / {tmp_path} rw - tmpfs tmpfs rw\n"
    )
    monkeypatch.setattr(Path, "read_text", lambda path: mounts)
    with pytest.raises(WorkerUnitError, match="must use disk"):
        worker_unit._disk_directory(tmp_path / "worker-runs")

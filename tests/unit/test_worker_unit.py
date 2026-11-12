"""Prove the new containment boundary cannot silently fall back or leave a memory cap."""

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
    control = AsyncMock(return_value=(0, "LoadState=loaded\nCPUWeight=10"))
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
        unit_setup.return_value = (0, "LoadState=not-found\nCPUWeight=100")
        with pytest.raises(WorkerUnitError, match="lacks its lower CPU weight"):
            await WorkerUnit.create({"PLAN_TICKET_ID": "test", "TMPDIR": str(tmp_path)}, tmp_path)
        assert not list(tmp_path.iterdir())
    asyncio.run(scenario())


def test_service_keeps_environment_private_and_control_directory_lifetime(
    unit_setup: AsyncMock, tmp_path: Path,
) -> None:
    async def scenario() -> None:
        secret = 'private $token "quoted" \\ slash\nsecond line'
        environment = {
            "PLAN_TICKET_ID": "test", "SECRET": secret,
            "TMPDIR": str(tmp_path), "TMP": str(tmp_path), "TEMP": str(tmp_path),
        }
        unit = await WorkerUnit.create(environment, tmp_path)
        assert unit is not None
        executable = tmp_path / "agent"
        executable.write_text("#!/bin/sh\n")
        executable.chmod(0o700)
        argv = unit.argv(("./agent", "argument $with %specifiers"))
        assert secret not in " ".join(argv)
        assert "--pipe" in argv and "--wait" in argv
        assert "--property=KillMode=control-group" in argv
        # A process killed for memory ends alone, and no cap is set on the Worker.
        assert "--property=OOMPolicy=continue" in argv
        assert not any("MemoryMax" in item or "MemorySwapMax" in item for item in argv)
        assert argv[-2:] == (str(executable), "argument $with %specifiers")
        assert unit.environment == environment
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


def test_stop_failure_retains_files_until_descendants_can_stop(
    unit_setup: AsyncMock, tmp_path: Path,
) -> None:
    async def scenario() -> None:
        unit = await WorkerUnit.create(
            {"PLAN_TICKET_ID": "test", "TMPDIR": str(tmp_path)}, tmp_path,
        )
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
            (0, "LoadState=loaded\nCPUWeight=10"),
            (0, "LoadState=loaded\nCPUWeight=100"),
        ]
        with pytest.raises(WorkerUnitError, match="Install panels.slice"):
            await WorkerUnit.create({"PLAN_TICKET_ID": "test", "TMPDIR": str(tmp_path)}, tmp_path)
        assert not list(tmp_path.iterdir())
    asyncio.run(scenario())

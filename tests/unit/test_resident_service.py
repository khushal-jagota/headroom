"""Maintenance never kills replacement processes, including missed full restarts."""

from __future__ import annotations

import plistlib
import stat
import subprocess
from pathlib import Path

import pytest

from planner.environments.resident.contracts import (
    ExecutorLaunchOptions,
    ExecutorSourceClosure,
    PreparedResidentPackage,
    ResidentError,
    ResidentPackage,
    ResidentPaths,
    ServiceConfiguration,
)
from planner.environments.resident.control import OwnerExecutorControl
from planner.environments.resident.launch_agent import render_launch_agent
from planner.environments.resident.service import LaunchAgentService
from planner.executor.contracts import (
    ConnectionState,
    ExecutorHealth,
    ExecutorIdentity,
    Quiescence,
    QuiescenceReceipt,
    QuiesceRequest,
)

IDENTITY = ExecutorIdentity(1, "revision", 1, "first", 1)
REQUEST = QuiesceRequest(IDENTITY, 42, True)
RECEIPT = QuiescenceReceipt(IDENTITY, 42, Quiescence(True, 0, 0, 0, 0, True), True)


class Commands:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self, command: tuple[str, ...], timeout: float
    ) -> subprocess.CompletedProcess[str]:
        assert 0 < timeout <= 1
        assert command[:2] == ("launchctl", "print")
        self.calls.append(command)
        return subprocess.CompletedProcess(command, 0, self.outputs.pop(0), "")


def service(tmp_path: Path, outputs: list[str]) -> tuple[LaunchAgentService, Commands]:
    configuration = ServiceConfiguration(
        ResidentPaths(tmp_path, tmp_path / "deploy", tmp_path / "data"),
        501,
        launch=ExecutorLaunchOptions("panels-vps", "a" * 40, "http://127.0.0.1:8765"),
    )
    commands = Commands(outputs)
    return LaunchAgentService(
        configuration,
        OwnerExecutorControl(tmp_path / "missing", timeout_seconds=1),
        timeout_seconds=1,
        poll_seconds=0.001,
        run=commands,
    ), commands


def test_maintenance_waits_without_signalling(tmp_path: Path) -> None:
    adapter, commands = service(
        tmp_path, ["pid = 42\nruns = 3", "pid = 42\nruns = 3", "runs = 3\nlast exit code = 0"]
    )
    adapter.prepare_quiesce(REQUEST)
    adapter.stop(RECEIPT)
    assert len(commands.calls) == 3


@pytest.mark.parametrize(
    "observation",
    [
        "pid = 43\nruns = 4",
        "runs = 4\nlast exit code = 0",  # replacement started and exited before observation
        "pid = 42\nruns = 4",  # PID reused by replacement
        "runs = 3\nlast exit code = 1",  # abnormal exit cannot authorize cutover
        "runs = 3",  # no trustworthy exit status
    ],
)
def test_restart_races_refuse_without_killing(tmp_path: Path, observation: str) -> None:
    adapter, commands = service(tmp_path, ["pid = 42\nruns = 3", observation])
    adapter.prepare_quiesce(REQUEST)
    with pytest.raises(ResidentError):
        adapter.stop(RECEIPT)
    assert len(commands.calls) == 2


def test_receipt_cannot_be_reused_for_rollback(tmp_path: Path) -> None:
    adapter, commands = service(tmp_path, ["pid = 42\nruns = 3", "runs = 3\nlast exit code = 0"])
    adapter.prepare_quiesce(REQUEST)
    adapter.stop(RECEIPT)
    with pytest.raises(ResidentError, match="checkpoint"):
        adapter.stop(RECEIPT)
    assert len(commands.calls) == 2


def test_new_candidate_needs_its_own_checkpoint(tmp_path: Path) -> None:
    adapter, commands = service(tmp_path, ["pid = 42\nruns = 3"])
    adapter.prepare_quiesce(REQUEST)
    other = ExecutorIdentity(1, "candidate", 1, "candidate", 2)
    receipt = QuiescenceReceipt(other, 43, RECEIPT.quiescence, True)
    with pytest.raises(ResidentError, match="checkpoint"):
        adapter.stop(receipt)
    assert len(commands.calls) == 1


def test_checkpoint_refuses_already_replaced_process(tmp_path: Path) -> None:
    adapter, _ = service(tmp_path, ["pid = 43\nruns = 4"])
    with pytest.raises(ResidentError, match="before quiesce"):
        adapter.prepare_quiesce(REQUEST)


class ReadyControl(OwnerExecutorControl):
    def __init__(self, socket_path: Path) -> None:
        super().__init__(socket_path, timeout_seconds=1)
        self.waits: list[str] = []

    def wait_for_health(
        self,
        package: ResidentPackage,
        *,
        not_before_ms: int,
        timeout_seconds: float,
        poll_seconds: float,
    ) -> ExecutorHealth:
        assert 0 < timeout_seconds <= 1
        self.waits.append(package.application_revision)
        return ExecutorHealth(IDENTITY, 42, ConnectionState.ready, True, not_before_ms, None)


def prepared(adapter: LaunchAgentService) -> PreparedResidentPackage:
    return PreparedResidentPackage(
        ResidentPackage(
            "a" * 40,
            1,
            1,
            "Darwin",
            "arm64",
            "3.12",
            ExecutorSourceClosure(("entry.py",), "entry", "requirements.txt"),
        ),
        adapter.configuration.paths.app,
        adapter.configuration.paths.runtimes / "runtime",
    )


def loaded_job(adapter: LaunchAgentService) -> str:
    content = plistlib.loads(render_launch_agent(adapter.configuration))
    args = "\n".join(content["ProgramArguments"])
    environment = "\n".join(
        f"{key} => {value}" for key, value in content["EnvironmentVariables"].items()
    )
    return (
        f"runs = 3\nlast exit code = 0\narguments = {{\n{args}\n}}\n"
        f"environment = {{\n{environment}\n}}"
    )


def test_start_verifies_stable_loaded_contract_and_waits_for_revision(tmp_path: Path) -> None:
    adapter, _ = service(tmp_path, [])
    control = ReadyControl(tmp_path / "socket")
    adapter.control = control
    commands: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, loaded_job(adapter), "")

    adapter.run = run
    adapter.start(prepared(adapter))
    assert commands == [
        ("launchctl", "print", adapter.target),
        ("launchctl", "kickstart", adapter.target),
    ]
    assert control.waits == ["a" * 40]


def test_start_refuses_stale_loaded_arguments(tmp_path: Path) -> None:
    adapter, _ = service(tmp_path, [])
    calls: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(
            command, 0, loaded_job(adapter).replace("panels-vps", "old-vps"), ""
        )

    adapter.run = run
    with pytest.raises(ResidentError, match="arguments differ"):
        adapter.start(prepared(adapter))
    assert len(calls) == 1


def test_first_install_is_explicit_absence_only_and_writes_owner_plist(tmp_path: Path) -> None:
    adapter, _ = service(tmp_path, [])
    control = ReadyControl(tmp_path / "socket")
    adapter.control = control
    calls: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[1] == "print":
            return subprocess.CompletedProcess(command, 113, "", "Could not find service")
        return subprocess.CompletedProcess(command, 0, "", "")

    adapter.run = run
    adapter.install(prepared(adapter))
    plist = tmp_path / "Library/LaunchAgents/com.panels.executor.plist"
    assert plist.read_bytes() == render_launch_agent(adapter.configuration)
    assert stat.S_IMODE(plist.stat().st_mode) == 0o600
    assert calls[1] == ("launchctl", "bootstrap", "gui/501", str(plist))
    assert control.waits == ["a" * 40]


@pytest.mark.parametrize("returncode,stderr", [(1, "permission denied"), (0, "")])
def test_first_install_refuses_unknown_or_present_job(
    tmp_path: Path, returncode: int, stderr: str
) -> None:
    adapter, _ = service(tmp_path, [])
    calls: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], timeout: float) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, returncode, "", stderr)

    adapter.run = run
    with pytest.raises(ResidentError, match="positively absent"):
        adapter.install(prepared(adapter))
    assert len(calls) == 1
    assert not (tmp_path / "Library/LaunchAgents").exists()

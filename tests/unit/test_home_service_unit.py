"""Check the generated user unit with systemd's real directive parser."""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from planner.installation.contracts import HomeInstall
from planner.installation.home import linux_units


def test_generated_home_unit_passes_systemd_verify(
    tmp_path: Path,
) -> None:
    analyzer = shutil.which("systemd-analyze")
    if analyzer is None:
        pytest.skip("systemd-analyze is unavailable on this platform")
    root = tmp_path / "home with spaces and %n"
    executable = root / "app/bin/panels-launcher"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    backup_executable = executable.with_name("panels")
    backup_executable.write_text("#!/bin/sh\nexit 0\n")
    backup_executable.chmod(0o700)
    options = HomeInstall(root, "panels-ticket-unit-proof", 18767, Path("/usr/bin/python3"), True)
    units = []
    for name, text in linux_units(options).items():
        unit = tmp_path / name
        unit.write_text(text)
        units.append(str(unit))
    runtime = tmp_path / "run"
    runtime.mkdir(mode=0o700)
    started = time.perf_counter()
    result = subprocess.run(
        [analyzer, "--user", "verify", *units],
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "XDG_RUNTIME_DIR": str(runtime)},
    )
    print(f"systemd-analyze verify seconds={time.perf_counter() - started:.6f}")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
def test_failed_home_start_removes_only_this_calls_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    system: str,
) -> None:
    from types import SimpleNamespace
    from typing import Any

    from planner.installation import home, mac_home

    account = tmp_path / "account"
    account.mkdir()
    monkeypatch.setenv("HOME", str(account))
    monkeypatch.setattr("planner.installation.home.platform.system", lambda: system)
    options = HomeInstall(
        tmp_path / "new-home",
        "com.panels.proof",
        18767,
        Path(shutil.which("python3") or "/usr/bin/python3"),
        True,
    )
    agents = account / ("Library/LaunchAgents" if system == "Darwin" else ".config/systemd/user")
    agents.mkdir(parents=True)
    foreign = agents / ("foreign.plist" if system == "Darwin" else "foreign.service")
    foreign.write_text("pre-existing installation")
    jobs: dict[str, str] = {}
    active: set[str] = set()
    calls: list[list[str]] = []
    fail_at = "health"
    collision = True

    def run(
        arguments: list[str] | tuple[str, ...], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        args = list(arguments)
        calls.append(args)
        output, error, code = "", "", 0
        if args[0] == "loginctl":
            output = "yes\n"
        elif "launchctl" in args[0]:
            operation = args[1]
            if operation == "print":
                label = args[-1].rsplit("/", 1)[-1]
                if collision and label == options.service:
                    output = "path = /another/home.plist\n"
                elif label in jobs:
                    output = f"\tpath = {jobs[label]}\n"
                else:
                    code, error = 1, "Could not find service"
            elif operation == "bootstrap":
                path = Path(args[-1])
                jobs[path.stem] = str(path)
                if fail_at == "backup" and path.stem.endswith(".backup"):
                    raise subprocess.CalledProcessError(1, args, stderr="backup refused")
            elif operation == "bootout":
                jobs.pop(args[-1].rsplit("/", 1)[-1])
        elif args[0] == "systemctl":
            operation = args[2]
            if operation == "show":
                output = (
                    "loaded"
                    if collision and args[3] == options.service + ".service"
                    else "not-found"
                )
            elif operation == "enable":
                active.add(args[-1])
                if fail_at == "backup" and args[-1].endswith(".timer"):
                    raise subprocess.CalledProcessError(1, args, stderr="backup refused")
            elif operation == "stop":
                active.discard(args[-1])
            elif operation == "is-active":
                output = "active" if args[-1] in active else "inactive"
        return subprocess.CompletedProcess(args, code, output, error)

    def extract(archive: Path, app: Path, digest: str, revision: str) -> SimpleNamespace:
        (app / "bin").mkdir(parents=True)
        return SimpleNamespace(
            runtime_contract=SimpleNamespace(system="darwin", architecture="arm64", role="home")
        )

    def health(port: int, revision: str) -> str:
        if fail_at == "health":
            raise RuntimeError("home health refused")
        return "http://127.0.0.1:18767"

    monkeypatch.setattr("planner.installation.home.subprocess.run", run)
    monkeypatch.setattr(home, "extract_app_archive", extract)
    monkeypatch.setattr(mac_home, "extract_app_archive", extract)
    monkeypatch.setattr(home, "admit_runtime", lambda *_: None)
    monkeypatch.setattr("planner.environments.runtime_admission.admit_runtime", lambda *_: None)
    monkeypatch.setattr(home, "_wait_health", health)
    with pytest.raises(ValueError, match="already"):
        home.install_home(options, tmp_path / "archive", "a" * 64, "a" * 40)
    assert not options.root.exists()
    assert not any("stop" in call or "bootout" in call for call in calls)
    collision = False
    for failure in ("health", "backup"):
        fail_at = failure
        with pytest.raises((RuntimeError, ValueError), match="refused"):
            home.install_home(options, tmp_path / "archive", "a" * 64, "a" * 40)
        assert not options.root.exists()
        assert not jobs and not active
        assert list(agents.iterdir()) == [foreign]
    assert foreign.read_text() == "pre-existing installation"


def test_linux_linger_refusal_precedes_installation_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typing import Any

    from planner.installation import home

    monkeypatch.setattr("planner.installation.home.platform.system", lambda: "Linux")
    calls = []

    def run(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "no\n", "")

    monkeypatch.setattr("planner.installation.home.subprocess.run", run)
    options = HomeInstall(tmp_path / "home", "panels-proof", 18767, Path("/usr/bin/python3"), True)
    with pytest.raises(ValueError, match="loginctl enable-linger"):
        home.install_home(options, tmp_path / "archive", "a" * 64, "a" * 40)
    assert not options.root.exists()
    assert len(calls) == 1 and calls[0][0] == "loginctl"

"""Real systemd/kernel/supervisor proofs; no browser or live service participates.

These three connected cases cover boundaries that synthetic sender records cannot:
manager-originated shutdown, a foreign pattern signal, and systemd timeout escalation.
Every unit, command pattern, runtime path, and process belongs to a random fixture nonce.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from tests.support.server import BOOT_BUDGET_S, REPO_ROOT
from tests.support.server_fixtures import _free_port, _scrubbed_env


@dataclass(frozen=True)
class OwnedProcess:
    pid: int
    start_time: str
    parent: int
    command: tuple[str, ...]


def _identity(pid: int) -> OwnedProcess:
    directory = Path(f"/proc/{pid}")
    stat = (directory / "stat").read_text().rsplit(")", 1)[1].split()
    command = tuple((directory / "cmdline").read_bytes().rstrip(b"\0").decode().split("\0"))
    return OwnedProcess(pid, stat[19], int(stat[1]), command)


def _assert_owned(process: OwnedProcess) -> None:
    assert _identity(process.pid) == process, "fixture process identity changed; refuse signal"


def _systemctl(unit: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    # Unit names enter this helper only from FixtureUnit; no ambient service variable.
    assert re.fullmatch(r"panels-lifecycle-proof-[0-9a-f]{32}\.service", unit)
    return subprocess.run(
        ["systemctl", "--user", *arguments, unit],
        capture_output=True,
        text=True,
        timeout=15,
    )


def _properties(unit: str) -> dict[str, str]:
    result = _systemctl(unit, "show")
    assert result.returncode == 0, result.stderr
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def _ready(base: str, log: Path) -> None:
    deadline = time.monotonic() + BOOT_BUDGET_S
    while time.monotonic() < deadline:
        try:
            response = httpx.get(base + "/api/meta", timeout=0.5)
            if response.status_code == 200:
                assert response.json()["test_mode"] is True
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    raise AssertionError(f"fixture HTTP did not become ready: {log.read_text()}")


def _environment(root: Path, port: int, control_socket: Path) -> dict[str, str]:
    from planner.core.db import connect, create_schema

    root.mkdir()
    database = root / "planning.db"
    with closing(connect(str(database))) as connection:
        create_schema(connection)

    config = root / "config.yaml"
    config.write_text("{}\n")
    # env -i also removes all variables inherited from the user systemd manager.
    return {
        "PATH": os.defpath,
        "HOME": str(root / "home"),
        "LANG": "C.UTF-8",
        # The authenticated user bus proves the real manager's credentials.
        "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}",
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{os.getuid()}/bus",
        "PYTHONPATH": str(REPO_ROOT),
        "PLAN_TEST_MODE": "1",
        "PLAN_DISPATCH_ENABLED": "0",
        "PLAN_CONFIG_PATH": str(config),
        "PLAN_DB_PATH": str(database),
        "PLAN_PORT": str(port),
        "PLAN_LOGS_DIR": str(root / "logs"),
        "PLAN_BACKUP_DIR": str(root / "backups"),
        "PLAN_HERMES_HOME": str(root / "hermes-home"),
        "PLAN_DISPATCHER_LOCK_PATH": str(root / "dispatcher.lock"),
        "PLAN_SERVER_CONTROL_SOCKET": str(control_socket),
    }


@dataclass
class FixtureUnit:
    unit: str
    command: tuple[str, ...]
    process: OwnedProcess
    base: str
    log: Path
    control_socket: Path
    stop_record: Path

    def assert_owned(self) -> None:
        _assert_owned(self.process)
        assert _properties(self.unit)["MainPID"] == str(self.process.pid)

    def stop(self) -> subprocess.CompletedProcess[str]:
        self.assert_owned()
        return _systemctl(self.unit, "stop")


@pytest.fixture
def guarded_unit(tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[FixtureUnit]:
    if sys.platform != "linux" or any(
        shutil.which(binary) is None for binary in ("systemd-run", "systemctl", "pgrep", "pkill")
    ):
        pytest.skip("requires Linux, systemd user manager, pgrep, and pkill")
    manager = subprocess.run(
        ["systemctl", "--user", "show-environment"], capture_output=True, timeout=5
    )
    if manager.returncode:
        pytest.skip("requires an accessible real systemd user manager")
    nonce = uuid.uuid4().hex
    # The user manager can have a separate /tmp mount. Keep fixture payloads in
    # nonce-owned repository storage that both mount namespaces can access.
    runtime = REPO_ROOT / "data" / "lifecycle-proofs"
    runtime.mkdir(parents=True, exist_ok=True)
    directory = tempfile.TemporaryDirectory(prefix=nonce + "-", dir=runtime)
    tmp_path = Path(directory.name)
    # The user runtime directory is shared with the real manager and stays short
    # when pytest supplies a deeply nested private HOME. mkdtemp creates a private
    # mode-0700 nonce directory; cleanup removes only that owned directory.
    socket_root = Path(f"/run/user/{os.getuid()}")
    socket_metadata = socket_root.stat()
    assert socket_root.is_dir() and socket_metadata.st_uid == os.getuid()
    assert socket_metadata.st_mode & 0o077 == 0, "user runtime directory must be private"
    socket_directory = tempfile.TemporaryDirectory(prefix="panels-lifecycle-", dir=socket_root)
    unit = f"panels-lifecycle-proof-{nonce}.service"
    # The normal CLI stays the supervisor. Only its bootstrap command is test-specific.
    source = f"from planner.cli.main import main; main() # panels-lifecycle-proof-{nonce}"
    if getattr(request, "param", False):
        # A fixture-only stuck-cleanup fault. No production flags or code change.
        source = (
            "import time; from planner.server_lifecycle.signals import SupervisorSignals; "
            "SupervisorSignals.stop = lambda self: time.sleep(60); "
            + source
        )
    command = (sys.executable, "-c", source, "serve")
    # AF_UNIX's path limit prevents a socket under this long worktree path.
    control_socket = Path(socket_directory.name) / "control.sock"
    port = _free_port()
    env = _environment(tmp_path / "guarded", port, control_socket)
    log = tmp_path / "guarded.log"
    stop_record = tmp_path / "manager-stop.json"
    stop_source = (
        "import json,os,pathlib; "
        f"pathlib.Path({str(stop_record)!r}).write_text(json.dumps("
        "{key:os.environ.get(key) for key in ('SERVICE_RESULT','EXIT_CODE','EXIT_STATUS')}))"
    )
    stop_command = shlex.join([sys.executable, "-c", stop_source])
    started = time.monotonic()
    result = subprocess.run(
        [
            "systemd-run", "--user", f"--unit={unit}", "--quiet",
            "--property=Type=exec", "--property=Restart=no", "--property=KillMode=control-group",
            "--property=TimeoutStopSec=2s", "--property=SendSIGKILL=yes",
            "--property=FinalKillSignal=SIGKILL",
            f"--property=WorkingDirectory={REPO_ROOT}",
            f"--property=StandardOutput=append:{log}",
            f"--property=StandardError=append:{log}",
            f"--property=ExecStopPost={stop_command}",
            "/usr/bin/env", "-i", *(f"{key}={value}" for key, value in env.items()),
            *command,
        ],
        env=_scrubbed_env(), capture_output=True, text=True, timeout=10,
    )
    if result.returncode:
        _systemctl(unit, "stop")
        _systemctl(unit, "reset-failed")
        socket_directory.cleanup()
        directory.cleanup()
        pytest.fail(f"fixture unit start failed: {result.stderr}")
    fixture: FixtureUnit | None = None
    try:
        _ready(f"http://127.0.0.1:{port}", log)
        process = _identity(int(_properties(unit)["MainPID"]))
        assert process.command == command
        fixture = FixtureUnit(
            unit, command, process, f"http://127.0.0.1:{port}", log, control_socket, stop_record
        )
        fixture.assert_owned()
        yield fixture
    finally:
        # A fixture-only unit is the cleanup authority even when startup failed. Never
        # discover targets by a generic Panels command or by the production service name.
        properties = _properties(unit)
        if properties.get("ActiveState") not in {"inactive", "failed"}:
            if fixture is not None:
                fixture.assert_owned()
            _systemctl(unit, "stop")
        _systemctl(unit, "reset-failed")
        control_socket.unlink(missing_ok=True)
        print(log.read_text() if log.exists() else "(no fixture log)")
        socket_directory.cleanup()
        directory.cleanup()
        print(f"LIFECYCLE_SYSTEMD seconds={time.monotonic() - started:.3f}")


def test_manager_stop_cleans_supervisor_application_and_listener(guarded_unit: FixtureUnit) -> None:
    fixture = guarded_unit
    children = Path(f"/proc/{fixture.process.pid}/task/{fixture.process.pid}/children")
    application_pids = [int(value) for value in children.read_text().split()]
    assert len(application_pids) == 1
    result = fixture.stop()
    assert result.returncode == 0, result.stderr
    outcome = json.loads(fixture.stop_record.read_text())
    assert outcome == {"SERVICE_RESULT": "success", "EXIT_CODE": "exited", "EXIT_STATUS": "0"}
    assert all(not Path(f"/proc/{pid}").exists() for pid in application_pids)
    assert not Path(f"/proc/{fixture.process.pid}").exists()
    assert not fixture.control_socket.exists()
    with pytest.raises(httpx.HTTPError):
        httpx.get(fixture.base + "/api/meta", timeout=0.5)


def test_foreign_pattern_signal_is_visible_and_dogfood_still_stops(
    guarded_unit: FixtureUnit, tmp_path: Path
) -> None:
    fixture = guarded_unit
    port = _free_port()
    socket = Path(f"/tmp/panels-dogfood-proof-{uuid.uuid4().hex}.sock")
    env = _environment(tmp_path / "dogfood", port, socket)
    log = tmp_path / "dogfood.log"
    with log.open("wb") as output:
        dogfood = subprocess.Popen(
            fixture.command, cwd=REPO_ROOT, env=env,
            stdout=output, stderr=subprocess.STDOUT,
        )
    recorded = _identity(dogfood.pid)
    assert recorded.parent == os.getpid()
    assert recorded.command == fixture.command
    try:
        _ready(f"http://127.0.0.1:{port}", log)
        fixture.assert_owned()
        _assert_owned(recorded)
        # Fully anchored exact argv with one unpredictable nonce. Enumerate every
        # match and prove fixture ownership before the one and only pattern signal.
        pattern = "^" + re.escape(" ".join(fixture.command)) + "$"
        matches = subprocess.run(
            ["pgrep", "-f", "--", pattern], capture_output=True, text=True, check=True
        )
        assert {int(pid) for pid in matches.stdout.split()} == {fixture.process.pid, dogfood.pid}
        fixture.assert_owned()
        _assert_owned(recorded)
        sender = subprocess.Popen(["pkill", "-TERM", "-f", "--", pattern])
        assert sender.wait(timeout=5) == 0
        dogfood.wait(timeout=10)
        assert dogfood.returncode == 0
        assert not socket.exists()
        deadline = time.monotonic() + 5
        while "refused SIGTERM" not in fixture.log.read_text() and time.monotonic() < deadline:
            time.sleep(0.02)
        output_text = fixture.log.read_text()
        assert f"refused SIGTERM from sender PID {sender.pid}:" in output_text
        assert "SIGKILL" in output_text
        assert f"exact supervisor PID {fixture.process.pid}" in output_text
        fixture.assert_owned()
        assert httpx.get(fixture.base + "/api/meta", timeout=1).status_code == 200
    finally:
        if dogfood.poll() is None:
            # Never replace a failed identity assertion with a wider signal target.
            _assert_owned(recorded)
            dogfood.terminate()
            try:
                dogfood.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _assert_owned(recorded)
                dogfood.kill()
                dogfood.wait(timeout=5)
        socket.unlink(missing_ok=True)


@pytest.mark.parametrize("guarded_unit", [True], indirect=True)
def test_manager_timeout_kills_only_fixture_after_stuck_cleanup(
    guarded_unit: FixtureUnit,
) -> None:
    fixture = guarded_unit
    fixture.assert_owned()
    started = time.monotonic()
    result = fixture.stop()
    elapsed = time.monotonic() - started
    outcome = json.loads(fixture.stop_record.read_text())
    assert result.returncode == 0, result.stderr
    assert outcome == {"SERVICE_RESULT": "timeout", "EXIT_CODE": "killed", "EXIT_STATUS": "KILL"}
    assert 1.5 <= elapsed < 10, elapsed
    assert not Path(f"/proc/{fixture.process.pid}").exists()
    with pytest.raises(httpx.HTTPError):
        httpx.get(fixture.base + "/api/meta", timeout=0.5)
    print(f"LIFECYCLE_FORCED_STOP seconds={elapsed:.3f}")

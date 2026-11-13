"""The stdlib installer against a scratch root, a fake systemctl and a real health server.

It runs exactly as the server starts it: the host `python3 -I -S -B` on the copy that
`write_installer` puts in the data directory, with the `installer_command` arguments.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from planner.environments.update_install import installer_command, write_installer

FROM_SHA = "a" * 40
TO_SHA = "b" * 40
OLDER_SHA = "c" * 40
ATTEMPT_ID = "attempt-1"
HEALTH_TIMEOUT_SECONDS = 3.0

_HOST_PYTHON = shutil.which("python3", path="/usr/local/bin:/usr/bin:/bin")

# Serves the app_sha in `<root>/app/manifest.json` as read at start; 503 when the
# started app carries an `unhealthy` marker.
_HEALTH_SERVER = """
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
app = Path(sys.argv[2]) / "app"
sha = json.loads((app / "manifest.json").read_text())["app_sha"]
status = 503 if (app / "unhealthy").exists() else 200
class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"app_sha": sha, "ready": status == 200}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass
HTTPServer(("127.0.0.1", int(sys.argv[1])), Health).serve_forever()
"""

_SYSTEMCTL = """#!{python} -I
import os, signal, subprocess, sys, time
from pathlib import Path
state = Path(os.environ["FAKE_STATE"])
action = sys.argv[2]
with (state / "calls.log").open("a") as log:
    log.write(f"systemctl {{action}} {{sys.argv[3]}}\\n")
pidfile = state / "server.pid"
if action == "stop" and pidfile.exists():
    pid = int(pidfile.read_text())
    os.kill(pid, signal.SIGTERM)
    while Path(f"/proc/{{pid}}").exists():
        time.sleep(0.02)
    pidfile.unlink()
elif action == "start":
    server = subprocess.Popen(
        [sys.executable, "-I", str(state / "health_server.py"),
         os.environ["FAKE_PORT"], os.environ["FAKE_ROOT"]],
        start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    pidfile.write_text(str(server.pid))
"""

# Each app's restore command names its own SHA and whether the service was running.
_RESTORE = """#!/bin/sh
running=no
[ -e "$FAKE_STATE/server.pid" ] && running=yes
echo "restore {sha} running=$running $*" >> "$FAKE_STATE/calls.log"
cp "$4/database.sqlite" "$6"
"""

_CAPTURE = """#!/bin/sh
echo "capture {sha} $*" >> "$FAKE_STATE/calls.log"
"""


@dataclass(frozen=True)
class Scratch:
    root: Path
    state: Path
    port: int

    @property
    def record_path(self) -> Path:
        return self.root / "data/update-attempt.json"

    def record(self) -> dict[str, object]:
        value: dict[str, object] = json.loads(self.record_path.read_text())
        return value

    def calls(self) -> list[str]:
        log = self.state / "calls.log"
        return log.read_text().splitlines() if log.exists() else []

    def run_installer(self) -> subprocess.CompletedProcess[str]:
        command = installer_command(
            write_installer(self.root / "data"),
            current_root=self.root,
            attempt_id=ATTEMPT_ID,
            service="panels-live.service",
            health_url=f"http://127.0.0.1:{self.port}/api/health",
            health_timeout_seconds=HEALTH_TIMEOUT_SECONDS,
        )
        assert command[0] == _HOST_PYTHON
        return subprocess.run(
            command, capture_output=True, text=True, timeout=60,
            env={
                "PATH": f"{self.state / 'bin'}:/usr/bin:/bin",
                "HOME": str(self.root),
                "FAKE_STATE": str(self.state),
                "FAKE_PORT": str(self.port),
                "FAKE_ROOT": str(self.root),
            },
        )


def write_app(folder: Path, sha: str, *, unhealthy: bool = False) -> None:
    (folder / "bin").mkdir(parents=True)
    (folder / ".venv/bin").mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"app_sha": sha}))
    for path, script in ((folder / "bin/panels", _RESTORE),
                         (folder / ".venv/bin/python", _CAPTURE)):
        path.write_text(script.format(sha=sha))
        path.chmod(0o755)
    if unhealthy:
        (folder / "unhealthy").touch()


def write_attempt(scratch: Scratch, *, state: str = "installing", backup: str | None = None,
                  attempt_id: str = ATTEMPT_ID) -> None:
    scratch.record_path.write_text(json.dumps({
        "attempt_id": attempt_id, "from_sha": FROM_SHA, "to_sha": TO_SHA, "mode": "now",
        "confirm_activity": False, "state": state, "detail": None,
        "created_at": "2026-11-05T00:00:00+00:00", "updated_at": "2026-11-05T00:00:00+00:00",
        "deadline_at": "2026-11-05T00:15:00+00:00", "release": {"manifest": {"app_sha": TO_SHA}},
        "backup": backup, "steps": {}, "mac_attempt_id": "mac-1", "written_by_later_version": 7,
    }))


@pytest.fixture
def scratch(tmp_path: Path) -> Iterator[Scratch]:
    if _HOST_PYTHON is None:
        pytest.skip("the installer needs the host python3")
    root, state = tmp_path / "current", tmp_path / "fake"
    (state / "bin").mkdir(parents=True)
    (root / "data").mkdir(parents=True)
    (state / "health_server.py").write_text(_HEALTH_SERVER)
    systemctl = state / "bin/systemctl"
    systemctl.write_text(_SYSTEMCTL.format(python=_HOST_PYTHON))
    systemctl.chmod(0o755)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    write_app(root / "app", FROM_SHA)
    (root / "data/planner.db").write_text("database written by the new version")
    yield Scratch(root, state, port)
    pidfile = state / "server.pid"
    if pidfile.exists():
        try:
            os.kill(int(pidfile.read_text()), signal.SIGTERM)
        except ProcessLookupError:
            pass


def test_healthy_install_swaps_apps_keeps_one_previous_and_records_steps(
    scratch: Scratch,
) -> None:
    """Risk: a good install leaves the wrong app, stale rollback copies or an open attempt."""
    write_app(scratch.root / f".app-next-{TO_SHA}", TO_SHA)
    write_app(scratch.root / f".app-previous-{OLDER_SHA}", OLDER_SHA)
    # Bridge only: where the 5622d2a81 installer kept its previous app.
    write_app(scratch.root / "app-previous", OLDER_SHA)
    write_attempt(scratch, backup=str(scratch.root / "data/backups/snapshot"))

    barrier = scratch.root / "data/update-barrier.json"
    barrier.write_text(ATTEMPT_ID)
    result = scratch.run_installer()

    assert result.returncode == 0, result.stderr
    assert not barrier.exists()
    record = scratch.record()
    assert record["state"] == "done" and record["detail"] is None
    steps = record["steps"]
    assert isinstance(steps, dict)
    order = ["installer_started", "service_stopped", "interruptions_captured", "app_swapped",
             "service_started", "healthy", "done"]
    assert set(steps) == set(order)
    times = [datetime.fromisoformat(steps[name]) for name in order]
    assert times == sorted(times) and times[0].utcoffset() == timedelta(0)
    assert record["written_by_later_version"] == 7 and record["mac_attempt_id"] == "mac-1"
    stop, capture, start = scratch.calls()
    assert stop == "systemctl stop panels-live.service"
    # The stopped app captures interrupted turns; the last argument is a fresh capture id.
    assert capture.startswith(
        f"capture {FROM_SHA} -I -m planner.environments.interruption_capture "
        f"{scratch.root / 'data/planner.db'} {ATTEMPT_ID} "
    )
    assert start == "systemctl start panels-live.service"
    assert json.loads((scratch.root / "app/manifest.json").read_text())["app_sha"] == TO_SHA
    previous = scratch.root / f".app-previous-{FROM_SHA}"
    assert json.loads((previous / "manifest.json").read_text())["app_sha"] == FROM_SHA
    retained = sorted(path.name for path in scratch.root.iterdir() if path.name.startswith("."))
    assert retained == [f".app-previous-{FROM_SHA}"]
    assert not (scratch.root / "app-previous").exists()


@pytest.mark.parametrize("backup", [True, False], ids=["with-backup", "without-backup"])
def test_unhealthy_install_goes_back_and_restores_data_while_stopped(
    scratch: Scratch, backup: bool,
) -> None:
    """Risk: a failed install leaves the new app or its migrated data, or claims a restore."""
    write_app(scratch.root / f".app-next-{TO_SHA}", TO_SHA, unhealthy=True)
    snapshot = scratch.root / "data/backups/snapshot"
    snapshot.mkdir(parents=True)
    (snapshot / "database.sqlite").write_text("database before the update")
    write_attempt(scratch, backup=str(snapshot) if backup else None)

    barrier = scratch.root / "data/update-barrier.json"
    barrier.write_text(ATTEMPT_ID)
    result = scratch.run_installer()

    assert result.returncode == 1
    assert not barrier.exists()
    record = scratch.record()
    assert record["state"] == "failed"
    calls = [call for call in scratch.calls() if not call.startswith("capture")]
    restore = [
        f"restore {FROM_SHA} running=no environment restore --snapshot {snapshot} "
        f"--destination-db {scratch.root / 'data/planner.db'} --live-stopped"
    ]
    assert calls == [
        "systemctl stop panels-live.service", "systemctl start panels-live.service",
        "systemctl stop panels-live.service", *(restore if backup else []),
        "systemctl start panels-live.service",
    ]
    if backup:
        assert record["detail"] == (
            "The new version did not pass its health check, so Panels went back to the "
            "previous version and its data."
        )
        assert (scratch.root / "data/planner.db").read_text() == "database before the update"
        assert "rollback_database_restored" in record["steps"]  # type: ignore[operator]
    else:
        assert record["detail"] == (
            "The new version did not pass its health check, so Panels went back to the "
            "previous version."
        )
    assert "rollback_healthy" in record["steps"]  # type: ignore[operator]
    assert json.loads((scratch.root / "app/manifest.json").read_text())["app_sha"] == FROM_SHA
    staged = scratch.root / f".app-next-{TO_SHA}"
    assert json.loads((staged / "manifest.json").read_text())["app_sha"] == TO_SHA
    assert not (scratch.root / f".app-previous-{FROM_SHA}").exists()


@pytest.mark.parametrize("attempt", [
    {"state": "waiting"}, {"state": "failed"}, {"attempt_id": "another-attempt"},
])
def test_installer_refuses_an_attempt_that_is_not_this_installing_one(
    scratch: Scratch, attempt: dict[str, str],
) -> None:
    """Risk: a stale or repeated installer stops the service for an attempt it does not own."""
    write_app(scratch.root / f".app-next-{TO_SHA}", TO_SHA)
    write_attempt(scratch, **attempt)
    before = scratch.record_path.read_bytes()

    result = scratch.run_installer()

    assert result.returncode == 2
    assert scratch.calls() == []
    assert scratch.record_path.read_bytes() == before
    assert json.loads((scratch.root / "app/manifest.json").read_text())["app_sha"] == FROM_SHA


def test_failed_rollback_releases_receipt_cut(scratch: Scratch) -> None:
    """A terminal installer failure cannot leave the home permanently frozen."""
    write_app(scratch.root / f".app-next-{TO_SHA}", TO_SHA, unhealthy=True)
    (scratch.root / "app/unhealthy").touch()
    write_attempt(scratch)
    barrier = scratch.root / "data/update-barrier.json"
    barrier.write_text(ATTEMPT_ID)
    result = scratch.run_installer()
    assert result.returncode == 1
    assert not barrier.exists()
    assert "rollback_healthy" not in scratch.record()["steps"]


def test_trial_boot_holds_runtime_until_installer_releases_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only real lifespan order proves health does not admit producers during a trial."""
    import fcntl
    import threading
    import time
    from dataclasses import asdict

    from fastapi.testclient import TestClient
    from tests.support.update_apps import release

    from planner.core import path_observer
    from planner.core.clock import build_clock
    from planner.core.config import load_config
    from planner.core.db import connect
    from planner.core.server import create_app
    from planner.environments.update_attempts import atomic_write_json, utc_now
    from planner.environments.update_barrier import UpdateBarrier
    from planner.environments.update_contracts import UpdateAttempt
    from planner.executor.registry import VpsExecutorRegistry

    starts: list[bool] = []
    original = VpsExecutorRegistry.start

    async def record_start(self: VpsExecutorRegistry) -> None:
        starts.append(True)
        await original(self)

    monkeypatch.setattr(VpsExecutorRegistry, "start", record_start)
    database = tmp_path / "planner.db"
    barrier = UpdateBarrier(tmp_path / "update-barrier.json")
    barrier.freeze(ATTEMPT_ID)
    marker_observed = threading.Event()
    signature = path_observer.path_signature

    def observed_signature(path: Path):
        result = signature(path)
        if path == barrier.path:
            marker_observed.set()
        return result

    monkeypatch.setattr(path_observer, "path_signature", observed_signature)
    now = utc_now()
    record = UpdateAttempt(
        attempt_id=ATTEMPT_ID, from_sha=FROM_SHA, to_sha=TO_SHA,
        mode="idle", confirm_activity=False, state="installing",
        created_at=now, updated_at=now, release=release(),
    )
    attempt_path = tmp_path / "update-attempt.json"
    atomic_write_json(attempt_path, asdict(record))
    config = load_config(path=None, env={
        "PLAN_TEST_MODE": "1", "PLAN_APP_SHA": TO_SHA,
        "PLAN_DB_PATH": str(database), "PLAN_LOGS_DIR": str(tmp_path / "logs"),
        "PLAN_HERMES_HOME": str(tmp_path / "hermes"), "PLAN_DISPATCH_ENABLED": "0",
        "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "polling.lock"),
    })
    app = create_app(config, build_clock(config), lambda: connect(str(database)))
    with (tmp_path / "update-installer.lock").open("a+") as installer:
        fcntl.flock(installer.fileno(), fcntl.LOCK_EX)
        installer.seek(0)
        installer.truncate()
        # The new owner acquired the lock before it replaced the previous ID.
        installer.write("previous-attempt\n")
        installer.flush()
        with TestClient(app) as client:
            response = client.get("/api/health", params={"expected_sha": TO_SHA})
            assert response.status_code == 200
            assert app.state.conversation is not None
            assert client.get("/api/conversation/conversations/missing").status_code == 404
            assert not starts
            assert app.state.conversation_system.workspace_admission.closed
            assert marker_observed.wait(timeout=5)
            # An installer crash after unlink must not leave a healthy home frozen.
            # Deliberately omit its next attempt write to prove marker-only release.
            barrier.release()
            deadline = time.monotonic() + 5
            while not starts and time.monotonic() < deadline:
                time.sleep(0.01)
            assert starts == [True]
            assert not app.state.conversation_system.workspace_admission.closed

    barrier.freeze(ATTEMPT_ID)
    atomic_write_json(attempt_path, asdict(record))
    starts.clear()
    with (tmp_path / "update-installer.lock").open("a+") as installer:
        fcntl.flock(installer.fileno(), fcntl.LOCK_EX)
        installer.seek(0)
        installer.truncate()
        installer.write(ATTEMPT_ID + "\n")
        installer.flush()
        recovering = create_app(config, build_clock(config), lambda: connect(str(database)))
        with TestClient(recovering) as client:
            assert client.get("/api/health", params={"expected_sha": TO_SHA}).status_code == 200
            assert not starts
            # SIGKILL and reboot both release this kernel lock without a file change.
            fcntl.flock(installer.fileno(), fcntl.LOCK_UN)
            deadline = time.monotonic() + 5
            while not starts and time.monotonic() < deadline:
                time.sleep(0.01)
            assert starts == [True]
            assert not recovering.state.update_barrier.frozen

    # A crash before launch needs no trial, including a timed-out intake drain.
    # Waiting and preparing both prove that no installer can later restore data.
    for interrupted_state in ("waiting", "preparing", "installing", "failed", "unknown", None):
        barrier.freeze(ATTEMPT_ID)
        if interrupted_state is None:
            attempt_path.unlink()
        else:
            atomic_write_json(attempt_path, {**asdict(record), "state": interrupted_state})
        starts.clear()
        recovered = create_app(config, build_clock(config), lambda: connect(str(database)))
        with TestClient(recovered) as client:
            assert client.get("/api/health", params={"expected_sha": TO_SHA}).status_code == 200
            assert starts == [True]
            assert not recovered.state.update_barrier.frozen
            assert not recovered.state.conversation_system.workspace_admission.closed
            attempt = recovered.state.updates.attempts.read()
            if interrupted_state is not None:
                assert attempt is not None and attempt.state == "failed"
                if interrupted_state in {"waiting", "preparing"}:
                    assert attempt.detail == (
                        "Panels restarted before the update began, so nothing changed."
                    )
                elif interrupted_state == "unknown":
                    assert attempt.detail == "The previous update did not complete. Try again."

            if interrupted_state is None:
                # Reuse this already-open home. A later update must recover without
                # another lifespan or another call to the normal producer starters.
                saves: list[bool] = []

                def write_proof(target: list[bool]):
                    async def save() -> dict[str, bool]:
                        target.append(True)
                        return {"saved": True}
                    return save

                recovered.add_api_route(
                    "/__update-proof-write", write_proof(saves), methods=["POST"],
                )
                assert client.post("/__update-proof-write").status_code == 200
                running_barrier = recovered.state.update_barrier
                system = recovered.state.conversation_system
                live_lock_observed = threading.Event()
                active_probe = running_barrier.installer_active

                def record_active(
                    identity: str, probe=active_probe, observed=live_lock_observed,
                ) -> bool:
                    active = probe(identity)
                    if active:
                        observed.set()
                    return active

                monkeypatch.setattr(running_barrier, "installer_active", record_active)
                with (tmp_path / "update-installer.lock").open("a+") as installer:
                    fcntl.flock(installer.fileno(), fcntl.LOCK_EX)
                    atomic_write_json(attempt_path, asdict(record))
                    assert system.workspace_admission.claim(
                        lambda: None, idle=lambda: True, require_idle=False,
                    )
                    running_barrier.freeze(ATTEMPT_ID)
                    assert client.post("/__update-proof-write").status_code == 503
                    assert live_lock_observed.wait(timeout=5)
                    # Neither the marker nor the attempt changes when this kernel lock
                    # disappears. The shared liveness probe must notice the dead job.
                    fcntl.flock(installer.fileno(), fcntl.LOCK_UN)
                    deadline = time.monotonic() + 7
                    while (running_barrier.frozen or system.workspace_admission.closed
                           ) and time.monotonic() < deadline:
                        time.sleep(0.01)
                    assert not running_barrier.frozen
                    assert not system.workspace_admission.closed
                    assert not system.workspace_admission.starts_closed
                    ended = recovered.state.updates.attempts.read()
                    assert ended is not None and ended.state == "failed"
                    assert client.post("/__update-proof-write").status_code == 200
                    assert saves == [True, True]
                    assert starts == [True]


@pytest.mark.parametrize("owner", [ATTEMPT_ID, "previous-attempt"])
def test_busy_installer_preserves_trial_with_same_or_stale_identity(
    scratch: Scratch, owner: str,
) -> None:
    """Lock identity text cannot authorize a duplicate launch to release the trial."""
    import fcntl

    write_attempt(scratch)
    marker = scratch.root / "data/update-barrier.json"
    marker.write_text(ATTEMPT_ID)
    with (scratch.root / "data/update-installer.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        lock.write(owner + "\n")
        lock.flush()
        result = scratch.run_installer()
    assert result.returncode == 2
    assert not scratch.calls()
    assert scratch.record()["state"] == "installing"
    assert marker.exists()



def test_restore_child_retains_lock_then_times_out_after_parent_descriptor_closes(
    scratch: Scratch, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An orphan restore retains custody until its own deadline ends the writer."""
    import argparse
    import fcntl
    import signal
    import sys

    from planner.platform_runtime.linux import update_installer as installer

    previous = scratch.root / f".app-previous-{FROM_SHA}"
    (scratch.root / "app").rename(previous)
    write_app(scratch.root / "app", TO_SHA)
    write_attempt(scratch, backup="retained-snapshot")
    lock_path = scratch.root / "data/update-installer.lock"
    parent = lock_path.open("a+")
    fcntl.flock(parent.fileno(), fcntl.LOCK_EX)
    inherited = parent.fileno()
    installation = installer.Installation(
        scratch.root, ATTEMPT_ID, "service", lifetime_lock_fd=inherited,
    )
    monkeypatch.setattr(installation, "systemctl", lambda action: None)
    monkeypatch.setattr(installer, "healthy", lambda *args: True)
    monkeypatch.setattr(installer, "DATABASE_RESTORE_TIMEOUT_SECONDS", 1)
    (previous / "bin/panels").write_text(
        f"#!{sys.executable}\nimport os,time\nos.fstat({inherited})\n"
        "print('ready',flush=True)\ntime.sleep(30)\n"
    )
    observations: list[bool] = []

    def restore(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command[8:10] == ["environment", "restore"]
        assert kwargs["pass_fds"] == (inherited,)
        child = subprocess.Popen(
            command,
            pass_fds=kwargs["pass_fds"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert child.stdout is not None and child.stdout.readline().strip() == "ready"
            parent.close()  # SIGKILL closes the parent's descriptor in the same way.
            with lock_path.open("a+") as probe:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                observations.append(True)
            child.communicate(timeout=5)
            assert child.returncode == -signal.SIGALRM
            with lock_path.open("a+") as probe:
                fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            raise subprocess.CalledProcessError(child.returncode, command)
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
            if child.stdout is not None:
                child.stdout.close()

    monkeypatch.setattr(installer.subprocess, "run", restore)
    try:
        result = installer.roll_back(
            installation,
            argparse.Namespace(health_url="unused", health_timeout_seconds=1),
            scratch.record(), "new app failed", previous_moved=True, new_app_moved=True,
        )
        assert result == 1
        assert observations == [True]
        assert "rollback_healthy" not in scratch.record()["steps"]
        assert scratch.record()["state"] == "failed"
    finally:
        parent.close()

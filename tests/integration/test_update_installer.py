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
    write_attempt(scratch, backup=str(scratch.root / "data/backups/snapshot"))

    result = scratch.run_installer()

    assert result.returncode == 0, result.stderr
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

    result = scratch.run_installer()

    assert result.returncode == 1
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

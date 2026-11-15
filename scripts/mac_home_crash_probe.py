"""Prove launchd app custody on a separate native Mac test home.

Run with this checkout's .venv/bin/python. No provider, Tailscale, or live service
configuration is read or changed. Keep the generated folder as proof.
"""

from __future__ import annotations

import json
import os
import platform
import plistlib
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from planner.platform_runtime.darwin.worker_processes import DarwinWorkerProcesses
from planner.worker_supervision.contracts import WorkerProcess

SOURCE = Path(__file__).resolve().parents[1]
WAIT_SECONDS = 90


def command(*argv: str) -> str:
    return subprocess.check_output(argv, text=True, stderr=subprocess.STDOUT).strip()


def wait_for[T](check: Callable[[], T], seconds: int = WAIT_SECONDS) -> T:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        time.sleep(0.25)
    raise RuntimeError("The crash probe did not meet its condition within 90 seconds")


def process(pid: int) -> str | None:
    try:
        return command("/bin/ps", "-p", str(pid), "-o", "lstart=,ppid=,pgid=,command=") or None
    except subprocess.CalledProcessError:
        return None


def main() -> None:
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise SystemExit("Run this proof on an Apple Silicon Mac")
    if Path(sys.executable).parent != SOURCE / ".venv/bin":
        raise SystemExit("Run with this checkout's .venv/bin/python")
    token = uuid.uuid4().hex[:12]
    root = Path.home() / f".ph-{token}"
    if len(os.fsencode(root / "ctl-event-loop-tasks.sock")) >= 104:
        raise SystemExit("The home path is too long for this probe's Unix sockets")
    root.mkdir(mode=0o700)
    for name in ("home", "logs", "backups"):
        (root / name).mkdir()
    (root / "config.yaml").write_text("{}\n")
    revision = command("git", "-C", str(SOURCE), "rev-parse", "HEAD")
    if command("git", "-C", str(SOURCE), "status", "--porcelain"):
        raise SystemExit("Use a clean committed checkout for immutable proof")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    label = f"com.panels.crash-probe.{token}"
    target = f"gui/{os.getuid()}/{label}"
    environment = {
        "HOME": str(root / "home"),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "PLAN_CONFIG_PATH": str(root / "config.yaml"),
        "PLAN_DB_PATH": str(root / "planner.db"),
        "PLAN_LOGS_DIR": str(root / "logs"),
        "PLAN_BACKUP_DIR": str(root / "backups"),
        "PLAN_HERMES_HOME": str(root / "home/.hermes"),
        "PLAN_DISPATCHER_LOCK_PATH": str(root / "dispatcher.lock"),
        "PLAN_SERVER_CONTROL_SOCKET": str(root / "ctl.sock"),
        "PLAN_SERVER_LIFECYCLE_LEASE_PATH": str(root / "lifecycle.lock"),
        "PLAN_PORT": str(port),
        "PLAN_APP_SHA": revision,
        "PLAN_DISPATCH_ENABLED": "0",
        "PLAN_TEST_MODE": "1",
        "TMPDIR": str(root),
    }
    plist = root / "probe.plist"
    plist.write_bytes(
        plistlib.dumps(
            {
                "Label": label,
                "ProgramArguments": [sys.executable, "-m", "planner", "serve"],
                "WorkingDirectory": str(SOURCE),
                "EnvironmentVariables": environment,
                "LimitLoadToSessionType": "Aqua",
                "RunAtLoad": True,
                "KeepAlive": True,
                "AbandonProcessGroup": False,
                "ThrottleInterval": 2,
                "StandardOutPath": str(root / "stdout.log"),
                "StandardErrorPath": str(root / "stderr.log"),
            }
        )
    )
    report: dict[str, object] = {
        "revision": revision,
        "root": str(root),
        "job": target,
        "port": port,
    }
    print(f"Evidence: {root}", flush=True)

    def health() -> bool:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/health?expected_sha={revision}", timeout=2
        ) as response:
            return json.load(response).get("ready") is True

    def job_pid() -> int:
        status = command("/bin/launchctl", "print", target)
        match = re.search(r"^\s*pid = (\d+)\s*$", status, re.MULTILINE)
        return int(match[1]) if match else 0

    primitives = DarwinWorkerProcesses()
    owned: list[WorkerProcess] = []
    interventions: list[dict[str, object]] = []
    report["cleanup_interventions"] = interventions

    def wait_absent(identity: WorkerProcess, seconds: int = WAIT_SECONDS) -> None:
        wait_for(lambda: primitives.is_absent(identity, time.monotonic() + 5), seconds)

    loaded = False
    try:
        command("/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist))
        loaded = True
        wait_for(health)
        supervisor = wait_for(job_pid)
        app_pids = command(
            "/usr/sbin/lsof", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"
        ).splitlines()
        if len(set(app_pids)) != 1:
            raise RuntimeError(f"Expected one app listener, got {app_pids}")
        app = int(app_pids[0])
        app_identity = process(app)
        supervisor_identity = process(supervisor)
        if not app_identity or not supervisor_identity:
            raise RuntimeError("Could not capture process identities")
        if int(command("/bin/ps", "-p", str(app), "-o", "ppid=")) != supervisor:
            raise RuntimeError("The listener is not the test supervisor's child")
        app_group = command("/bin/ps", "-p", str(app), "-o", "pgid=")
        supervisor_group = command("/bin/ps", "-p", str(supervisor), "-o", "pgid=")
        if app_group != supervisor_group:
            raise RuntimeError("The app escaped the test launchd job process group")
        snapshot = primitives.snapshot(time.monotonic() + 5)
        old_app = snapshot[app]
        old_supervisor = snapshot[supervisor]
        owned.extend((old_app, old_supervisor))
        report["old_app_birth"] = asdict(old_app)
        report.update(
            supervisor=supervisor,
            app=app,
            app_identity=app_identity,
            supervisor_identity=supervisor_identity,
            process_group=app_group,
        )
        # Address only our random launchd job. Do not signal a PID that can be reused.
        if job_pid() != supervisor or process(supervisor) != supervisor_identity:
            raise RuntimeError("The test supervisor changed before the crash")
        command("/bin/launchctl", "kill", "SIGKILL", target)
        wait_for(lambda: primitives.is_absent(old_app, time.monotonic() + 5))
        new_supervisor = wait_for(lambda: pid if (pid := job_pid()) != supervisor else 0)
        wait_for(health)
        new_app = int(command("/usr/sbin/lsof", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"))
        if int(command("/bin/ps", "-p", str(new_app), "-o", "ppid=")) != new_supervisor:
            raise RuntimeError("Recovered health does not belong to the replacement supervisor")
        snapshot = primitives.snapshot(time.monotonic() + 5)
        owned.extend((snapshot[new_app], snapshot[new_supervisor]))
        report.update(
            result="passed", replacement_supervisor=new_supervisor, replacement_app=new_app
        )
    except BaseException as error:
        report.update(result="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        if loaded:
            try:
                command("/bin/launchctl", "bootout", target)
                report["job_removed"] = True
            except subprocess.SubprocessError as error:
                report["cleanup_error"] = str(error)
        try:
            for identity in owned:
                try:
                    wait_absent(identity, 30)
                except RuntimeError:
                    # Cleanup is intervention, never evidence that launchd passed.
                    interventions.append(asdict(identity))
                    primitives.signal(identity, signal.SIGKILL, time.monotonic() + 5)
                    wait_absent(identity)
            with socket.socket() as check_port:
                check_port.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                check_port.bind(("127.0.0.1", port))
            report["cleanup_verified"] = True
        except (OSError, RuntimeError) as error:
            report["cleanup_error"] = str(error)
        (root / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2), flush=True)
        if report.get("cleanup_error") or report.get("cleanup_interventions"):
            raise RuntimeError(
                "Probe cleanup needed intervention or remains unproved; read report.json"
            )


if __name__ == "__main__":
    main()

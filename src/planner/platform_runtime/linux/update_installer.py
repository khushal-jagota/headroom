"""Install one staged Panels release: stop, swap folders, start, check health, or go back.

The server copies this file to `<data>/update-installer.py` and runs it with the host's
`python3 -I -S -B` in its own transient user unit, so it outlives the service it stops.
It must stay standard-library only and run on Python 3.11 or newer: it runs outside
every app and virtual environment, and no planner code may load into it.

It never downloads and never builds. The server has already unpacked the release to
`<root>/.app-next-<to_sha>`, written the attempt as `installing` and taken any backup.
Each step's UTC time goes into the attempt's `steps`; that is the timing evidence.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, cast

STAGED_APP_PREFIX = ".app-next-"
PREVIOUS_APP_PREFIX = ".app-previous-"
SERVICE_COMMAND_TIMEOUT_SECONDS = 120.0
INTERRUPTION_CAPTURE_BUDGET_SECONDS = 2.0
DATABASE_RESTORE_TIMEOUT_SECONDS = 900.0
HEALTH_POLL_SECONDS = 0.5


class InstallFailed(Exception):
    """The new version is not serving; the reason opens the attempt's detail."""


# This native service code must remain in this file: the running copy cannot import
# anything from the application that it replaces.
class _BSDInfo(ctypes.Structure):
    _fields_: ClassVar[Any] = [
        (name, ctypes.c_uint32) for name in (
            "flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid", "rgid",
            "svuid", "svgid", "rfu",
        )
    ] + [("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)] + [
        (name, ctypes.c_uint32) for name in (
            "nfiles", "pgid", "pjobc", "e_tdev", "e_tpgid", "nice",
        )
    ] + [("start_sec", ctypes.c_uint64), ("start_usec", ctypes.c_uint64)]


def mac_process_identity(pid: int) -> tuple[int, int, int, int] | None:
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    info = _BSDInfo()
    ctypes.set_errno(0)
    length = library.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
    if length != ctypes.sizeof(info):
        if ctypes.get_errno() == errno.ESRCH:
            return None
        raise OSError(f"Mac process identity is unavailable for {pid}")
    return info.ppid, info.uid, info.start_sec, info.start_usec


def mac_processes(deadline: float | None = None) -> dict[int, tuple[int, int, int, int]]:
    """Complete account inventory: pid -> (parent, uid, birth seconds, microseconds)."""
    deadline = deadline or time.monotonic() + SERVICE_COMMAND_TIMEOUT_SECONDS
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    count = library.proc_listpids(4, os.getuid(), None, 0)
    if count <= 0:
        raise OSError("Mac process inventory is unavailable")
    pids = (ctypes.c_int * (count // ctypes.sizeof(ctypes.c_int) + 1024))()
    size = library.proc_listpids(4, os.getuid(), pids, ctypes.sizeof(pids))
    if size <= 0 or size >= ctypes.sizeof(pids):
        raise OSError("Mac process inventory is incomplete")
    result = {}
    for pid in pids[:size // ctypes.sizeof(ctypes.c_int)]:
        if pid <= 0:
            continue
        if time.monotonic() >= deadline:
            raise TimeoutError("Mac process inspection exceeded its deadline")
        info = mac_process_identity(pid)
        if info is not None:
            result[pid] = info
    return result


def external_python(path: Path, root: Path) -> Path:
    """Resolve the host interpreter outside every replaceable installation path."""
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as error:
        raise ValueError(
            "The Mac home needs an external Python 3.11 or newer for updates; "
            f"the interpreter is unavailable: {path}"
        ) from error
    if resolved.is_relative_to(root.resolve()) or not os.access(resolved, os.X_OK):
        raise ValueError("Installer Python must be executable outside the installation root")
    result = subprocess.run(
        [str(resolved), "-I", "-S", "-B", "-c",
         "import sys; raise SystemExit(sys.version_info < (3, 11))"],
        check=False, capture_output=True, timeout=10,
    )
    if result.returncode:
        raise ValueError("The Mac home needs an external Python 3.11 or newer for updates")
    return resolved


class MacService:
    """Control only the setup-bound home and backup in this graphical domain."""

    def __init__(self, root: Path, service: str) -> None:
        self.root = root.resolve(strict=True)
        path = root / "data/mac-home-installation.json"
        if path.is_symlink() or path.stat().st_uid != os.getuid():
            raise ValueError("Mac installation identity has a foreign owner or is a link")
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or set(value) != {
            "uid", "root", "label", "home_plist", "backup_plist", "installer_python",
        }:
            raise ValueError("Mac installation identity is invalid")
        label = value["label"]
        if (type(value["uid"]) is not int or value["uid"] != os.getuid()
                or value["root"] != str(self.root)
                or not isinstance(label, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", label) is None):
            raise ValueError("Mac installation identity differs from this account or root")
        self.domain = f"gui/{os.getuid()}"
        self.label = label
        if service != f"{self.domain}/{label}":
            raise ValueError("Mac service differs from this installation")
        self.plists = {}
        for suffix, key in (("", "home_plist"), (".backup", "backup_plist")):
            expected = self.root / "agents" / f"{label}{suffix}.plist"
            if value[key] != str(expected) or expected.is_symlink():
                raise ValueError("Mac service plist differs from this installation")
            self.plists[label + suffix] = expected
        self.python = external_python(Path(value["installer_python"]), self.root)
        self.stopped = False
        self.backup_was_loaded = False

    def plist(self, label: str) -> dict[str, Any]:
        path = self.plists[label]
        if path.stat().st_uid != os.getuid():
            raise ValueError("Mac service plist has a foreign owner")
        value = plistlib.loads(path.read_bytes())
        expected = [str(self.root / "app/bin/panels")]
        expected += ["serve"] if label == self.label else [
            "environment", "backup-current", "--source-db", str(self.root / "data/planner.db"),
            "--backup-dir", str(self.root / "data/backups"),
            "--current-app", str(self.root / "app"),
        ]
        environment = value.get("EnvironmentVariables", {})
        if (value.get("Label") != label or value.get("ProgramArguments") != expected
                or value.get("WorkingDirectory") != str(self.root)
                or environment.get("PLAN_DB_PATH") != str(self.root / "data/planner.db")
                or environment.get("PLAN_CONFIG_PATH") != str(self.root / "data/config.yaml")
                or "PLAN_APP_SHA" in environment):
            raise ValueError("Mac service plist does not bind the expected installation")
        return cast(dict[str, Any], value)

    def job(self, label: str, *, deadline: float | None = None) -> str | None:
        timeout = (SERVICE_COMMAND_TIMEOUT_SECONDS if deadline is None
                   else deadline - time.monotonic())
        if timeout <= 0:
            raise TimeoutError("Mac job inspection exceeded its deadline")
        result = subprocess.run(
            ["/bin/launchctl", "print", f"{self.domain}/{label}"],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode:
            # launchctl uses a specific missing-service diagnostic. Other failures
            # (including inaccessible login domains) are uncertainty, not absence.
            if "Could not find service" in result.stderr:
                return None
            raise OSError(f"Cannot inspect Mac job {label}: {result.stderr.strip()}")
        return result.stdout

    def authenticate(
        self, label: str, *, required: bool, deadline: float | None = None,
    ) -> tuple[int | None, str | None]:
        plist = self.plist(label)
        text = self.job(label, deadline=deadline)
        if text is None:
            if required:
                raise ValueError("The installed Mac home is not loaded")
            return None, None
        for key, expected in (
            ("path", str(self.plists[label])),
            ("program", str(self.root / "app/bin/panels")),
            ("working directory", str(self.root)),
        ):
            match = re.search(rf"^\s*{re.escape(key)} = (.+)$", text, re.MULTILINE)
            actual = match[1] if match is not None else None
            matches = actual == expected
            if key == "path" and actual is not None:
                matches = Path(actual).resolve() == Path(expected).resolve()
            if not matches:
                raise ValueError(f"Loaded Mac job has a different {key}")
        arguments = re.search(r"^\s*arguments = \{\n(.*?)^\s*\}",
                              text, re.MULTILINE | re.DOTALL)
        if arguments is None or [line.strip() for line in arguments[1].splitlines()
                                 if line.strip()] != plist["ProgramArguments"]:
            raise ValueError("Loaded Mac job has different arguments")
        environment = re.search(r"^\s*environment = \{\n(.*?)^\s*\}",
                                text, re.MULTILINE | re.DOTALL)
        if environment is None:
            raise ValueError("Loaded Mac job environment is unavailable")
        for key in ("PLAN_DB_PATH", "PLAN_CONFIG_PATH"):
            match = re.search(rf"^\s*{key} (?:=>|=) (.+)$", environment[1], re.MULTILINE)
            if match is None or match[1] != plist["EnvironmentVariables"][key]:
                raise ValueError(f"Loaded Mac job has a different {key}")
        match = re.search(r"^\s*pid = ([0-9]+)$", text, re.MULTILINE)
        pid = int(match[1]) if match else None
        if required and pid is None:
            raise ValueError("The installed Mac home has no running supervisor")
        return pid, text

    def validate_running_home(self, *, parent: int | None = None) -> None:
        pid, _ = self.authenticate(self.label, required=True)
        identity = mac_process_identity(pid) if pid is not None else None
        if identity is None or identity[1] != os.getuid() or (parent is not None and pid != parent):
            raise ValueError("Mac home supervisor identity differs from this process")

    def stop(self) -> None:
        deadline = time.monotonic() + SERVICE_COMMAND_TIMEOUT_SECONDS
        captured: dict[int, tuple[int, int, int, int]] = {}
        loaded = []
        inventory = mac_processes(deadline)
        for label in (self.label + ".backup", self.label):
            pid, text = self.authenticate(
                label, required=(label == self.label and not self.stopped), deadline=deadline,
            )
            if text is None:
                continue
            loaded.append(label)
            if label.endswith(".backup"):
                self.backup_was_loaded = True
            if pid is not None:
                if pid not in inventory or inventory[pid][1] != os.getuid():
                    raise ValueError("Loaded Mac job process identity is unavailable")
                descendants: dict[int, list[int]] = {}
                for child, info in inventory.items():
                    descendants.setdefault(info[0], []).append(child)
                selected: set[int] = set()
                pending = [pid]
                while pending:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Mac descendant inspection exceeded its deadline")
                    child = pending.pop()
                    if child not in selected:
                        selected.add(child)
                        pending.extend(descendants.get(child, []))
                captured.update({child: inventory[child] for child in selected})
        for label in loaded:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Mac job stop exceeded its deadline")
            subprocess.run(
                ["/bin/launchctl", "bootout", f"{self.domain}/{label}"],
                check=True, capture_output=True, timeout=remaining,
            )
        while True:
            jobs_gone = all(self.job(label, deadline=deadline) is None for label in loaded)
            processes_gone = True
            for pid, identity in captured.items():
                if time.monotonic() >= deadline:
                    raise TimeoutError("Mac process retirement inspection exceeded its deadline")
                current = mac_process_identity(pid)
                if current is not None and current[2:] == identity[2:]:
                    processes_gone = False
            if jobs_gone and processes_gone:
                self.stopped = True
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "Mac jobs or recorded processes did not retire before replacement"
                )
            time.sleep(HEALTH_POLL_SECONDS)

    def start(self) -> None:
        self.plist(self.label)
        if self.job(self.label) is not None:
            raise ValueError("Refuse to replace an already loaded Mac job")
        subprocess.run(
            ["/bin/launchctl", "bootstrap", self.domain, str(self.plists[self.label])],
            check=True, capture_output=True, timeout=SERVICE_COMMAND_TIMEOUT_SECONDS,
        )

    def resume_backup(self) -> None:
        label = self.label + ".backup"
        self.plist(label)
        if self.backup_was_loaded and self.job(label) is None:
            subprocess.run(
                ["/bin/launchctl", "bootstrap", self.domain, str(self.plists[label])],
                check=True, capture_output=True, timeout=SERVICE_COMMAND_TIMEOUT_SECONDS,
            )


class Installation:
    def __init__(self, root: Path, attempt_id: str, service: str) -> None:
        self.root = root
        self.attempt_id = attempt_id
        self.service = service
        self.record_path = root / "data/update-attempt.json"
        self.lock_path = root / "data/update-attempt.json.lock"
        self.mac: MacService | None = None

    @contextmanager
    def locked(self) -> Iterator[None]:
        with self.lock_path.open("a+") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def read(self) -> dict[str, object]:
        value = json.loads(self.record_path.read_text())
        if not isinstance(value, dict):
            raise ValueError("update attempt must be an object")
        return value

    def write(self, record: dict[str, object]) -> None:
        """Replace the record atomically; a reader sees the old or the new file."""
        encoded = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.record_path.name}.", suffix=".tmp", dir=self.record_path.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.record_path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        directory = os.open(self.record_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def record(self, step: str, *, state: str | None = None, detail: str | None = None) -> None:
        """Change only state, detail, steps and updated_at; keep every other key."""
        with self.locked():
            current = self.read()
            if current.get("attempt_id") != self.attempt_id:
                return
            now = datetime.now(UTC).isoformat()
            steps = current.get("steps")
            current["steps"] = {**(steps if isinstance(steps, dict) else {}), step: now}
            if state is not None:
                current["state"] = state
                current["detail"] = detail
            current["updated_at"] = now
            self.write(current)

    def release_barrier(self) -> None:
        """Only a healthy surviving home can accept output after the rollback cut."""
        (self.root / "data/update-barrier.json").unlink(missing_ok=True)
        directory = os.open(self.root / "data", os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def claim(self) -> dict[str, object] | None:
        """Read this attempt; only an `installing` attempt with this id may proceed."""
        with self.locked():
            current = self.read()
            if current.get("attempt_id") != self.attempt_id or current.get("state") != "installing":
                return None
        self.record("installer_started")
        return current

    def systemctl(self, action: str) -> None:
        if self.mac is not None:
            if action == "stop":
                self.mac.stop()
            else:
                self.mac.start()
            return
        subprocess.run(
            ["systemctl", "--user", action, self.service],
            check=True, stdin=subprocess.DEVNULL, timeout=SERVICE_COMMAND_TIMEOUT_SECONDS,
        )

    def capture_interruptions(self) -> None:
        """The stopped app records which turns the stop ended; fail open and bounded."""
        try:
            subprocess.run(
                [
                    str(self.root / "app/.venv/bin/python"), "-I", "-m",
                    "planner.environments.interruption_capture",
                    str((self.root / "data/planner.db").resolve()),
                    self.attempt_id, uuid.uuid4().hex,
                ],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=INTERRUPTION_CAPTURE_BUDGET_SECONDS,
            )
        except (OSError, subprocess.SubprocessError) as error:
            print(f"Interruption capture unavailable: {error}", file=sys.stderr)


def healthy(health_url: str, sha: str, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            with urllib.request.urlopen(f"{health_url}?expected_sha={sha}", timeout=5) as response:
                body = json.load(response)
            if response.status == 200 and isinstance(body, dict) and body.get("app_sha") == sha:
                return True
        except (OSError, ValueError):
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(HEALTH_POLL_SECONDS)


def installed_sha(app: Path) -> object:
    try:
        manifest = json.loads((app / "manifest.json").read_text())
    except (OSError, ValueError):
        return None
    return manifest.get("app_sha") if isinstance(manifest, dict) else None


def install(arguments: argparse.Namespace) -> int:
    root: Path = arguments.current_root
    installation = Installation(root, arguments.attempt_id, arguments.service)
    attempt = installation.claim()
    if attempt is None:
        print("This update attempt is not waiting to install; nothing was changed.",
              file=sys.stderr)
        return 2
    to_sha, from_sha = str(attempt["to_sha"]), str(attempt["from_sha"])
    app = root / "app"
    staged = root / f"{STAGED_APP_PREFIX}{to_sha}"
    previous = root / f"{PREVIOUS_APP_PREFIX}{from_sha}"
    if not staged.is_dir():
        installation.record("failed", state="failed", detail=(
            "The update could not start because the downloaded version is missing. "
            "The current version kept running."
        ))
        return 1
    if installed_sha(app) != from_sha:
        installation.record("failed", state="failed", detail=(
            "The update could not start because the installed version is not the one it "
            "expected. The current version kept running."
        ))
        return 1

    if sys.platform == "darwin":
        try:
            installation.mac = MacService(root, arguments.service)
            installation.mac.validate_running_home()
        except Exception as error:
            installation.record("failed", state="failed", detail=f"Mac identity refused: {error}")
            return 1

    # Clear a stale copy before the stop, so deleting 2.8 GB never adds downtime.
    if previous.exists():
        shutil.rmtree(previous)
    previous_moved = new_app_moved = False
    try:
        installation.systemctl("stop")
        installation.record("service_stopped")
        installation.capture_interruptions()
        installation.record("interruptions_captured")
        app.rename(previous)
        previous_moved = True
        staged.rename(app)
        new_app_moved = True
        installation.record("app_swapped")
        installation.systemctl("start")
        installation.record("service_started")
        if not healthy(arguments.health_url, to_sha, arguments.health_timeout_seconds):
            raise InstallFailed("The new version did not pass its health check")
    except Exception as error:
        reason = str(error) if isinstance(error, InstallFailed) else (
            f"The new version could not be installed ({type(error).__name__}: {error})"
        )
        return roll_back(
            installation, arguments, attempt, reason,
            previous_moved=previous_moved, new_app_moved=new_app_moved,
        )
    installation.release_barrier()
    installation.record("healthy")
    completion_detail = None
    if installation.mac is not None:
        try:
            installation.mac.resume_backup()
        except Exception as error:
            completion_detail = (
                f"The release passed health, but its backup job did not restart: {error}"
            )
            installation.record("backup_restart_failed", detail=completion_detail)
    installation.record("done", state="done", detail=completion_detail)
    for folder in root.glob(f"{PREVIOUS_APP_PREFIX}*"):
        if folder != previous:
            shutil.rmtree(folder, ignore_errors=True)
    # Bridge only: the 5622d2a81 installer kept its previous app here. The next release
    # deletes this line.
    shutil.rmtree(root / "app-previous", ignore_errors=True)
    return 0


def roll_back(
    installation: Installation,
    arguments: argparse.Namespace,
    attempt: dict[str, object],
    reason: str,
    *,
    previous_moved: bool,
    new_app_moved: bool,
) -> int:
    """Undo exactly the moves that happened; restore data if the new app may have run."""
    root = installation.root
    to_sha, from_sha = str(attempt["to_sha"]), str(attempt["from_sha"])
    backup = attempt.get("backup")
    app = root / "app"
    staged = root / f"{STAGED_APP_PREFIX}{to_sha}"
    previous = root / f"{PREVIOUS_APP_PREFIX}{from_sha}"
    restore_data = new_app_moved and isinstance(backup, str)
    try:
        if (installation.mac is not None and not previous_moved and not new_app_moved
                and not installation.mac.stopped):
            # A partial bootout can stop the backup while the old home still runs.
            # Never bootstrap another home over uncertain retirement.
            installation.mac.validate_running_home()
            if not healthy(arguments.health_url, from_sha, arguments.health_timeout_seconds):
                raise InstallFailed("the unchanged home did not pass health after its stop failed")
            installation.release_barrier()
            installation.mac.resume_backup()
            installation.record("failed", state="failed", detail=(
                f"{reason}. The previous app kept running. Its backup job was restored."
            ))
            return 1
        if previous_moved or new_app_moved:
            installation.systemctl("stop")
            installation.record("rollback_service_stopped")
        if new_app_moved:
            # Keep the new version staged, so trying again needs no download.
            app.rename(staged)
        if previous_moved:
            previous.rename(app)
        installation.record("rollback_app_restored")
        if restore_data:
            subprocess.run(
                [
                    str(app / "bin/panels"), "environment", "restore",
                    "--snapshot", str(backup),
                    "--destination-db", str(root / "data/planner.db"),
                    "--live-stopped",
                ],
                check=True, stdin=subprocess.DEVNULL, timeout=DATABASE_RESTORE_TIMEOUT_SECONDS,
            )
            installation.record("rollback_database_restored")
        installation.systemctl("start")
        installation.record("rollback_service_started")
        if not healthy(arguments.health_url, from_sha, arguments.health_timeout_seconds):
            raise InstallFailed("the previous version did not pass its health check either")
    except Exception as error:
        problem = str(error) if isinstance(error, InstallFailed) else (
            f"{type(error).__name__}: {error}"
        )
        installation.record("failed", state="failed", detail=(
            f"{reason}. Panels could not go back to the previous version: {problem}."
        ))
        return 1
    installation.release_barrier()
    installation.record("rollback_healthy")
    backup_detail = ""
    if installation.mac is not None:
        try:
            installation.mac.resume_backup()
        except Exception as error:
            backup_detail = f" Its backup job did not restart: {error}."
            installation.record("backup_restart_failed", detail=backup_detail)
    went_back = "the previous version and its data" if restore_data else "the previous version"
    installation.record(
        "failed", state="failed",
        detail=f"{reason}, so Panels went back to {went_back}.{backup_detail}"
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Install one staged Panels release.")
    parser.add_argument("--current-root", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--health-url", required=True)
    parser.add_argument("--health-timeout-seconds", type=float, required=True)
    arguments = parser.parse_args()
    try:
        return install(arguments)
    except Exception as error:
        # Never leave the attempt `installing` while the record can still be written.
        detail = f"The update stopped unexpectedly ({type(error).__name__}: {error})."
        try:
            Installation(arguments.current_root, arguments.attempt_id, arguments.service).record(
                "failed", state="failed", detail=detail
            )
        except Exception as record_error:
            print(f"Could not record the failed update: {record_error}", file=sys.stderr)
        print(detail, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

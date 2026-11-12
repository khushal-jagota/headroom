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
import fcntl
import json
import os
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

STAGED_APP_PREFIX = ".app-next-"
PREVIOUS_APP_PREFIX = ".app-previous-"
SERVICE_COMMAND_TIMEOUT_SECONDS = 120.0
INTERRUPTION_CAPTURE_BUDGET_SECONDS = 2.0
DATABASE_RESTORE_TIMEOUT_SECONDS = 900.0
HEALTH_POLL_SECONDS = 0.5


class InstallFailed(Exception):
    """The new version is not serving; the reason opens the attempt's detail."""


class Installation:
    def __init__(self, root: Path, attempt_id: str, service: str) -> None:
        self.root = root
        self.attempt_id = attempt_id
        self.service = service
        self.record_path = root / "data/update-attempt.json"
        self.lock_path = root / "data/update-attempt.json.lock"

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

    def claim(self) -> dict[str, object] | None:
        """Read this attempt; only an `installing` attempt with this id may proceed."""
        with self.locked():
            current = self.read()
            if current.get("attempt_id") != self.attempt_id or current.get("state") != "installing":
                return None
        self.record("installer_started")
        return current

    def systemctl(self, action: str) -> None:
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
    installation.record("healthy")
    installation.record("done", state="done")
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
    installation.record("rollback_healthy")
    went_back = "the previous version and its data" if restore_data else "the previous version"
    installation.record(
        "failed", state="failed", detail=f"{reason}, so Panels went back to {went_back}."
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

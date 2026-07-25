"""One-version database compatibility proof on disposable state."""

from __future__ import annotations

import os
import signal
import socket
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

from planner.environments.app import validate_app_manifest


class AppCompatibilityError(RuntimeError):
    """Raised when the current app cannot use a database upgraded by its successor."""


def prove_previous_app_compatibility(
    *,
    candidate_app: Path,
    current_app: Path,
    source_db: Path,
    timeout_seconds: float = 20.0,
) -> None:
    """Upgrade a copied database, then boot and health-check the current app against it."""
    if timeout_seconds <= 0:
        raise AppCompatibilityError("compatibility timeout must be positive")
    validate_app_manifest(candidate_app / "manifest.json", require_runtime=True)
    current = validate_app_manifest(current_app / "manifest.json", require_runtime=True)
    with tempfile.TemporaryDirectory(prefix="panels-app-compatibility-") as temporary:
        root = Path(temporary)
        disposable_db = root / "data" / "planning.db"
        disposable_db.parent.mkdir()
        _copy_database(source_db, disposable_db)
        environment = _probe_environment(root, disposable_db, current.app_sha)
        _upgrade_database(candidate_app, disposable_db, environment)
        port = _available_port()
        environment["PLAN_PORT"] = str(port)
        process = subprocess.Popen(
            [str(current_app / ".venv" / "bin" / "python"), "-m", "planner", "serve"],
            cwd=current_app,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        primary_error: BaseException | None = None
        try:
            _wait_for_health(process, port, current.app_sha, timeout_seconds)
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                _stop_process_group(process)
            except BaseException:
                if primary_error is None:
                    raise


def _copy_database(source: Path, destination: Path) -> None:
    try:
        with sqlite3.connect(source) as source_connection:
            with sqlite3.connect(destination) as destination_connection:
                source_connection.backup(destination_connection)
    except sqlite3.Error as exc:
        raise AppCompatibilityError("could not copy the live database for compatibility") from exc


def _probe_environment(root: Path, database: Path, current_sha: str) -> dict[str, str]:
    home = root / "home"
    logs = root / "logs"
    backups = root / "backups"
    hermes = root / "hermes"
    for path in (home, logs, backups, hermes):
        path.mkdir()
    return {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PLAN_TEST_MODE": "1",
        "PLAN_APP_SHA": current_sha,
        "PLAN_DB_PATH": str(database),
        "PLAN_LOGS_DIR": str(logs),
        "PLAN_BACKUP_DIR": str(backups),
        "PLAN_HERMES_HOME": str(hermes),
        "PLAN_DISPATCHER_LOCK_PATH": str(root / "dispatcher.lock"),
        "PLAN_SERVER_CONTROL_SOCKET": str(root / "server.sock"),
    }


def _upgrade_database(
    candidate_app: Path, database: Path, environment: dict[str, str]
) -> None:
    command = [
        str(candidate_app / ".venv" / "bin" / "python"),
        "-c",
        (
            "from planner.core.db import connect, create_schema; "
            f"connection = connect({str(database)!r}); "
            "create_schema(connection); connection.close()"
        ),
    ]
    try:
        subprocess.run(
            command,
            cwd=candidate_app,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AppCompatibilityError("candidate could not upgrade the disposable database") from exc


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_health(
    process: subprocess.Popen[str], port: int, current_sha: str, timeout_seconds: float
) -> None:
    deadline = time.monotonic() + timeout_seconds
    url = f"http://127.0.0.1:{port}/api/health"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = "" if process.stderr is None else process.stderr.read().strip()
            detail = f": {stderr[-500:]}" if stderr else ""
            raise AppCompatibilityError(f"previous app exited during compatibility proof{detail}")
        try:
            response = httpx.get(url, params={"expected_sha": current_sha}, timeout=0.5)
            if response.status_code == 200 and response.json().get("app_sha") == current_sha:
                return
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(0.05)
    raise AppCompatibilityError("previous app did not pass health on the upgraded database")


def _stop_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        process.wait(timeout=5)

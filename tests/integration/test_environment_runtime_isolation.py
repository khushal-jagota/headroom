"""Process integration for the persistent staging-environment boundary."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
from tests.support.server import PLAN_BIN
from tests.support.server_fixtures import _first_meta_answer

_STAGING_URL_RE = re.compile(r"staging (?P<url>http://127\.0\.0\.1:\d+)")
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_staging_runs_on_demand_with_dynamic_port_and_persistent_state(
    tmp_path: Path,
) -> None:
    digest = hashlib.sha1(str(tmp_path).encode("utf-8")).hexdigest()[:8]
    environment_root = Path(os.environ.get("TMPDIR", "/tmp")) / f"se-{os.getpid()}-{digest}"
    shutil.rmtree(environment_root, ignore_errors=True)
    repository = _REPOSITORY_ROOT
    log_path = tmp_path / "staging.log"

    try:
        manifest = _command_json(
            repository,
            "prepare",
            "--kind",
            "staging",
            "--environment-root",
            str(environment_root),
            "--repository-root",
            str(repository),
            "--json",
        )
        assert manifest["runtime_port_policy"] == "dynamic"
        assert "port" not in manifest
        assert "running" not in manifest

        first = _start_staging(repository, environment_root, log_path)
        first_url = _wait_for_staging_url(first, log_path)
        _wait_for_health(first, first_url, log_path)
        created = httpx.post(
            f"{first_url}/api/projects",
            json={"name": "Retained staging activity"},
            timeout=5.0,
        )
        assert created.status_code < 300, created.text
        project_id = created.json()["id"]
        _terminate(first)

        log_path.write_text("", encoding="utf-8")
        second = _start_staging(repository, environment_root, log_path)
        second_url = _wait_for_staging_url(second, log_path)
        _wait_for_health(second, second_url, log_path)
        read = f"{second_url}/api/projects?detail=full"
        projects = httpx.get(read, timeout=5.0).json()["projects"]
        assert project_id in {project["id"] for project in projects}
        _terminate(second)
        assert not Path(manifest["server_control_socket_path"]).exists()

        reset = _command_json(
            repository,
            "reset",
            "--kind",
            "staging",
            "--environment-root",
            str(environment_root),
            "--json",
        )
        assert reset["runtime_port_policy"] == "dynamic"
        with __import__("sqlite3").connect(reset["db_path"]) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM projects WHERE id = ?", (project_id,)
            ).fetchone() == (0,)
    finally:
        for candidate in (locals().get("first"), locals().get("second")):
            if isinstance(candidate, subprocess.Popen):
                _terminate(candidate)
        shutil.rmtree(environment_root, ignore_errors=True)


def _start_staging(
    repository: Path,
    environment_root: Path,
    log_path: Path,
) -> subprocess.Popen[bytes]:
    log = log_path.open("ab")
    try:
        return subprocess.Popen(
            [
                str(PLAN_BIN),
                "environment",
                "run",
                "--kind",
                "staging",
                "--environment-root",
                str(environment_root),
                "--repository-root",
                str(repository),
                "--test-mode",
            ],
            cwd=repository,
            env=_scrubbed_env(),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log.close()


def _wait_for_staging_url(proc: subprocess.Popen[bytes], log_path: Path) -> str:
    # The launcher prints its URL once it has bound the listener, before it starts the
    # server. Watch for that line until it appears or the launcher exits, with no budget.
    while True:
        exited = proc.poll() is not None
        match = _STAGING_URL_RE.search(log_path.read_text(encoding="utf-8"))
        if match is not None:
            return match.group("url")
        if exited:
            raise AssertionError(f"staging exited during launch\n{log_path.read_text()}")
        time.sleep(0.05)


def _wait_for_health(proc: subprocess.Popen[bytes], url: str, log_path: Path) -> None:
    # The launcher bound the listener before printing the URL and handed it to the server.
    _first_meta_answer(url, log_path, lambda: f"staging (rc={proc.poll()})")


def _terminate(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    os.killpg(proc.pid, signal.SIGTERM)
    proc.wait(timeout=10.0)


def _command_json(repository: Path, command: str, *args: str) -> dict[str, Any]:
    result = subprocess.run(
        [str(PLAN_BIN), "environment", command, *args],
        cwd=repository,
        env=_scrubbed_env(),
        capture_output=True,
        text=True,
        timeout=30.0,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    data: dict[str, Any] = json.loads(result.stdout)
    return data


def _scrubbed_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith("PLAN_")}

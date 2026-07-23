from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

import httpx
from conftest import BOOT_BUDGET_S, PLAN_BIN

_STAGING_URL_RE = re.compile(r"staging (?P<url>http://127\.0\.0\.1:\d+)")
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_staging_runs_on_demand_with_dynamic_port_and_persistent_state(
    tmp_path: Path,
) -> None:
    environment_root = Path("/tmp") / f"panels-staging-e2e-{os.getpid()}-{tmp_path.name}"
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
        projects = httpx.get(f"{second_url}/api/projects", timeout=5.0).json()["projects"]
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
    deadline = time.monotonic() + BOOT_BUDGET_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"staging exited during launch\n{log_path.read_text()}")
        match = _STAGING_URL_RE.search(log_path.read_text(encoding="utf-8"))
        if match is not None:
            return match.group("url")
        time.sleep(0.05)
    raise AssertionError(f"staging printed no URL\n{log_path.read_text()}")


def _wait_for_health(proc: subprocess.Popen[bytes], url: str, log_path: Path) -> None:
    deadline = time.monotonic() + BOOT_BUDGET_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"staging exited during boot\n{log_path.read_text()}")
        try:
            response = httpx.get(f"{url}/api/meta", timeout=0.5)
        except httpx.HTTPError:
            response = None
        if response is not None and response.status_code == 200:
            assert response.json()["test_mode"] is True
            return
        time.sleep(0.1)
    raise AssertionError(f"staging did not become healthy\n{log_path.read_text()}")


def _terminate(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    os.killpg(proc.pid, signal.SIGTERM)
    proc.wait(timeout=10.0)


def _command_json(repository: Path, command: str, *args: str) -> dict[str, object]:
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
    return json.loads(result.stdout)


def _scrubbed_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith("PLAN_")}

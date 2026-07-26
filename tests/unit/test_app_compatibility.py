from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from planner.environments.app import AppManifest
from planner.environments.app_compatibility import (
    AppCompatibilityError,
    _stop_process_group,
    prove_previous_app_compatibility,
)

SHA_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SHA_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def test_compatibility_upgrades_copy_then_boots_previous_app_with_isolated_state(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source_db = tmp_path / "planning.db"
    with sqlite3.connect(source_db) as connection:
        connection.execute("CREATE TABLE facts (value TEXT)")
        connection.execute("INSERT INTO facts VALUES ('live')")
    current = tmp_path / "current-app"
    candidate = tmp_path / "candidate-app"
    current.mkdir()
    candidate.mkdir()
    run_calls: list[tuple[list[str], dict[str, str]]] = []
    popen_calls: list[tuple[list[str], dict[str, str]]] = []

    def validate(path: Path, **_: object) -> AppManifest:
        sha = SHA_A if path.parent == current else SHA_B
        return AppManifest(sha, "a" * 64, "b" * 64)

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        environment = dict(kwargs["env"])
        run_calls.append((command, environment))
        database = Path(environment["PLAN_DB_PATH"])
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT value FROM facts").fetchone()[0] == "live"
            connection.execute("ALTER TABLE facts ADD COLUMN upgraded INTEGER")
        return subprocess.CompletedProcess(command, 0, "", "")

    class Process:
        pid = 123
        stderr = None

        def poll(self) -> None:
            return None

    def popen(command: list[str], **kwargs: Any) -> Process:
        popen_calls.append((command, dict(kwargs["env"])))
        return Process()

    monkeypatch.setattr(
        "planner.environments.app_compatibility.validate_app_manifest", validate
    )
    monkeypatch.setattr("planner.environments.app_compatibility.subprocess.run", run)
    monkeypatch.setattr("planner.environments.app_compatibility.subprocess.Popen", popen)
    monkeypatch.setattr("planner.environments.app_compatibility._wait_for_health", lambda *_: None)
    monkeypatch.setattr(
        "planner.environments.app_compatibility._stop_process_group", lambda *_: None
    )

    prove_previous_app_compatibility(
        candidate_app=candidate,
        current_app=current,
        source_db=source_db,
    )

    assert run_calls[0][0][0] == str(candidate / ".venv/bin/python")
    assert popen_calls[0][0] == [
        str(current / ".venv/bin/python"),
        "-m",
        "planner",
        "serve",
    ]
    environment = popen_calls[0][1]
    assert environment["PLAN_TEST_MODE"] == "1"
    assert environment["PLAN_APP_SHA"] == SHA_A
    assert environment["PLAN_APP_ROOT"] == str(current)
    assert environment["PLAN_DB_PATH"] != str(source_db)
    assert environment["PLAN_LOGS_DIR"].startswith(
        str(Path(environment["PLAN_DB_PATH"]).parents[1])
    )
    with sqlite3.connect(source_db) as connection:
        assert [row[1] for row in connection.execute("PRAGMA table_info(facts)")] == ["value"]


def test_probe_cleanup_accepts_process_exit_between_poll_and_signal(monkeypatch: Any) -> None:
    class Process:
        pid = 123

        def poll(self) -> None:
            return None

    monkeypatch.setattr(
        "planner.environments.app_compatibility.os.killpg",
        lambda *_: (_ for _ in ()).throw(ProcessLookupError()),
    )
    _stop_process_group(Process())  # type: ignore[arg-type]


def test_candidate_schema_upgrade_failure_rejects_compatibility_before_boot(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source_db = tmp_path / "planning.db"
    with sqlite3.connect(source_db) as connection:
        connection.execute("CREATE TABLE facts (value TEXT)")
    current = tmp_path / "current"
    candidate = tmp_path / "candidate"
    current.mkdir()
    candidate.mkdir()
    monkeypatch.setattr(
        "planner.environments.app_compatibility.validate_app_manifest",
        lambda path, **_: AppManifest(
            SHA_A if path.parent == current else SHA_B, "a" * 64, "b" * 64
        ),
    )
    monkeypatch.setattr(
        "planner.environments.app_compatibility.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, args[0], stderr="incompatible schema")
        ),
    )
    monkeypatch.setattr(
        "planner.environments.app_compatibility.subprocess.Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("previous app booted")),
    )
    with pytest.raises(AppCompatibilityError, match="candidate could not upgrade"):
        prove_previous_app_compatibility(
            candidate_app=candidate,
            current_app=current,
            source_db=source_db,
        )


def test_previous_app_health_failure_rejects_compatibility_and_stops_probe(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source_db = tmp_path / "planning.db"
    with sqlite3.connect(source_db) as connection:
        connection.execute("CREATE TABLE facts (value TEXT)")
    current = tmp_path / "current"
    candidate = tmp_path / "candidate"
    current.mkdir()
    candidate.mkdir()
    stopped: list[object] = []

    class Process:
        stderr = None

    process = Process()
    monkeypatch.setattr(
        "planner.environments.app_compatibility.validate_app_manifest",
        lambda path, **_: AppManifest(
            SHA_A if path.parent == current else SHA_B, "a" * 64, "b" * 64
        ),
    )
    monkeypatch.setattr(
        "planner.environments.app_compatibility._upgrade_database", lambda *_: None
    )
    monkeypatch.setattr(
        "planner.environments.app_compatibility.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr(
        "planner.environments.app_compatibility._wait_for_health",
        lambda *_: (_ for _ in ()).throw(
            AppCompatibilityError("previous app did not pass health")
        ),
    )
    monkeypatch.setattr(
        "planner.environments.app_compatibility._stop_process_group", stopped.append
    )
    with pytest.raises(AppCompatibilityError, match="did not pass health"):
        prove_previous_app_compatibility(
            candidate_app=candidate,
            current_app=current,
            source_db=source_db,
        )
    assert stopped == [process]


def test_real_previous_app_process_exit_rejects_compatibility(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source_db = tmp_path / "planning.db"
    with sqlite3.connect(source_db) as connection:
        connection.execute("CREATE TABLE facts (value TEXT)")
    candidate = tmp_path / "candidate"
    current = tmp_path / "current"
    candidate_python = candidate / ".venv" / "bin" / "python"
    current_python = current / ".venv" / "bin" / "python"
    candidate_python.parent.mkdir(parents=True)
    current_python.parent.mkdir(parents=True)
    candidate_python.write_text(
        f"#!/bin/sh\nexec {sys.executable!r} \"$@\"\n", encoding="utf-8"
    )
    candidate_python.chmod(0o755)
    current_python.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
    current_python.chmod(0o755)
    monkeypatch.setattr(
        "planner.environments.app_compatibility.validate_app_manifest",
        lambda path, **_: AppManifest(
            SHA_A if path.parent == current else SHA_B, "a" * 64, "b" * 64
        ),
    )
    with pytest.raises(AppCompatibilityError, match="previous app exited"):
        prove_previous_app_compatibility(
            candidate_app=candidate,
            current_app=current,
            source_db=source_db,
            timeout_seconds=2,
        )

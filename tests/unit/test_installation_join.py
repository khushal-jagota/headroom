"""Join must retain owner issuance, private argv, and accepted-lease completion.

Existing credential tests prove SQL authority, but do not exercise bootstrap output or
prove that a started service is insufficient for a successful join. No connected host.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from click.testing import CliRunner

from planner.installation import join
from planner.installation.join_cli import machine_join_command

REVISION = "a" * 40
CREDENTIAL = "x" * 43


def test_owner_join_command_keeps_secret_out_of_command(monkeypatch: pytest.MonkeyPatch) -> None:
    machine_id = str(uuid4())
    calls: list[tuple[str, str, object]] = []

    def send(method: str, path: str, **kwargs: Any) -> object:
        calls.append((method, path, kwargs.get("json_body")))
        if path == "/api/meta":
            return {"app_sha": REVISION}
        return {"machine_id": machine_id, "credential": CREDENTIAL, "issued_at_ms": 1}

    monkeypatch.setattr("planner.installation.join_cli.http.send", send)
    result = CliRunner().invoke(
        machine_join_command,
        [
            "Studio",
            "--home",
            "https://home.example",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls == [("GET", "/api/meta", None), ("POST", "/api/machines", {"name": "Studio"})]
    command = next(
        line for line in result.output.splitlines() if line.startswith("panels_join_script=")
    )
    assert machine_id in command and REVISION in command
    assert CREDENTIAL not in command
    assert result.output.count(CREDENTIAL) == 1
    linux = CliRunner().invoke(
        machine_join_command,
        ["Server", "--home", "https://home.example", "--platform", "linux"],
    )
    assert linux.exit_code == 0, linux.output
    assert calls[-1] == ("POST", "/api/machines", {"name": "Server", "platform": "linux"})
    assert "--platform linux" in linux.output


def test_join_refuses_existing_installation_before_native_actions(tmp_path: Path) -> None:
    marker = tmp_path / "journal"
    marker.write_text("retained")
    with pytest.raises(ValueError, match="already exists"):
        join.install_executor(
            archive=tmp_path / "unused",
            archive_sha256="b" * 64,
            archive_size=1,
            revision=REVISION,
            home_url="https://home.example",
            machine_id=str(uuid4()),
            credential=CREDENTIAL,
            root=tmp_path,
        )
    assert marker.read_text() == "retained"


def test_join_requires_fresh_matching_accepted_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal attempts
        attempts += 1
        assert kwargs["env"]["PLAN_EXECUTOR_ROOT"] == str(tmp_path)
        assert "PLAN_DB_PATH" not in kwargs["env"]
        status = {
            "identity": {
                "application_revision": "wrong" if attempts == 1 else REVISION,
                "protocol_version": 26,
            },
            "last_lease_failure": None,
            "going_to_sleep": False,
            "admission_stopped": attempts == 2,
            "lease_accepted_at_mac_ms": time.time_ns() // 1_000_000 + 1,
            "refusal": None,
        }
        return subprocess.CompletedProcess([], 0, json.dumps(status), "")

    monkeypatch.setenv("PLAN_DB_PATH", "/a/foreign/database")
    monkeypatch.setattr("planner.installation.join.subprocess.run", run)
    monkeypatch.setattr("planner.installation.join.time.sleep", lambda _: None)
    join.wait_for_lease(tmp_path / "python", tmp_path, REVISION)
    assert attempts == 3

    monkeypatch.setattr("planner.installation.join.time.monotonic", lambda: 0.0)
    with pytest.raises(ValueError, match="did not report an accepted lease"):
        join.wait_for_lease(tmp_path / "python", tmp_path, REVISION, timeout=0)

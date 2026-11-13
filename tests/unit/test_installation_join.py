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

from planner.executor.credential_file import write_credential
from planner.installation import join
from planner.installation.join_cli import machine_join_command
from planner.installation.join_linux import install_linux_executor

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
    assert "--platform" not in command
    assert result.output.count(CREDENTIAL) == 1


def test_join_refuses_existing_installation_before_native_actions(tmp_path: Path) -> None:
    marker = tmp_path / "journal"
    marker.write_text("retained")
    for installer in (join.install_executor, install_linux_executor):
        with pytest.raises(ValueError, match="already exists"):
            installer(
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


@pytest.mark.parametrize("system", ["Darwin", "Linux"])
def test_failed_join_cleans_only_its_installation_then_can_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    system: str,
) -> None:
    """Lease refusal must not leave a live credential/service or consume the first-install path."""
    import shutil
    from types import SimpleNamespace

    account = tmp_path / "account"
    account.mkdir()
    root = tmp_path / "executor"
    service_file = (
        account / "Library/LaunchAgents/com.panels.executor.plist"
        if system == "Darwin"
        else account / ".config/systemd/user/panels-executor.service"
    )
    loaded = False
    refuse_lease = True
    fail_stop = False
    foreign_job = False
    stop_calls = 0
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: account))
    monkeypatch.setattr("planner.installation.join.platform.system", lambda: system)
    monkeypatch.setattr(join, "check_mac_prerequisites", lambda: "-")

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal loaded, stop_calls
        if "bootstrap" in command or "enable" in command:
            loaded = True
        if "bootout" in command or "stop" in command:
            stop_calls += 1
            if fail_stop:
                return subprocess.CompletedProcess(command, 1, "", "stop refused")
            loaded = False
        if "print" in command:
            return subprocess.CompletedProcess(
                command,
                0 if loaded else 1,
                f"path = {tmp_path / 'foreign.plist' if foreign_job else service_file}"
                if loaded
                else "",
                "" if loaded else "Could not find service",
            )
        if "show" in command:
            value = (
                ("active" if loaded else "inactive")
                if "--property=ActiveState" in command
                else ("loaded" if loaded else "not-found")
            )
            return subprocess.CompletedProcess(command, 0, value, "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def extract(archive: Path, releases: Path, **kwargs: Any) -> Path:
        runtime = releases / "runtime"
        runtime.mkdir(parents=True)
        return runtime

    def build(home: Path, app: Path, revision: str, **kwargs: Any) -> None:
        app.mkdir()

    def install(configuration: Any, **kwargs: Any) -> Path:
        unit: Path = kwargs["unit_directory"] / "panels-executor.service"
        if unit.exists() or any(
            path.exists() and any(path.iterdir())
            for path in (configuration.installation, configuration.data)
        ):
            raise ValueError("This executor is already installed")
        grant: Path = kwargs["credential_file"]
        assert not grant.is_relative_to(configuration.data)
        assert not grant.is_relative_to(configuration.installation)
        assert grant.stat().st_mode & 0o777 == 0o600
        assert json.loads(grant.read_text())["credential"] == CREDENTIAL
        configuration.installation.mkdir(parents=True, exist_ok=True)
        configuration.data.mkdir(parents=True, exist_ok=True)
        write_credential(configuration.data / "machine-credential.json", CREDENTIAL)
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text("[Service]\n[Install]\n")
        return unit

    def lease(*args: Any, **kwargs: Any) -> None:
        nonlocal foreign_job
        if system == "Darwin" and fail_stop:
            foreign_job = True
        if refuse_lease:
            raise ValueError("lease refused")

    monkeypatch.setattr("planner.installation.join.subprocess.run", run)
    monkeypatch.setattr(join, "extract_runtime", extract)
    monkeypatch.setattr(join, "build_app", build)
    monkeypatch.setattr(join, "verify_app", lambda *args, **kwargs: "")
    monkeypatch.setattr(join, "wait_for_lease", lease)
    monkeypatch.setattr(
        "planner.installation.join_linux.importlib.import_module",
        lambda _: SimpleNamespace(
            LinuxExecutorInstallation=lambda **kwargs: SimpleNamespace(**kwargs),
            install=install,
        ),
    )
    arguments: dict[str, Any] = dict(
        archive=tmp_path / "archive",
        archive_sha256="b" * 64,
        archive_size=1,
        revision=REVISION,
        home_url="https://home.example",
        machine_id=str(uuid4()),
        credential=CREDENTIAL,
        root=root,
    )
    # Direct callers must refuse an existing file or loaded job before any install write.
    service_file.parent.mkdir(parents=True)
    service_file.write_text("preexisting")
    with pytest.raises(ValueError, match="service already exists"):
        join.install_executor(**arguments)
    assert service_file.read_text() == "preexisting" and not root.exists()
    service_file.unlink()
    loaded = True
    with pytest.raises(ValueError, match="service.*already"):
        join.install_executor(**arguments)
    assert loaded and not root.exists()
    loaded = False

    with pytest.raises(ValueError, match="lease refused"):
        join.install_executor(**arguments)
    assert not root.exists() and not service_file.exists() and not loaded

    # If the controller cannot stop the new service, its credential/data must remain in place.
    fail_stop = True
    prior_stops = stop_calls
    with pytest.raises(RuntimeError, match="Cleanup failed"):
        join.install_executor(**arguments)
    assert loaded and service_file.exists() and (root / "data/machine-credential.json").exists()
    if system == "Darwin":
        assert stop_calls == prior_stops  # A foreign job under the label must remain untouched.
    # Only the fake service is reset here; no host process or controller runs in this test.
    fail_stop = False
    foreign_job = False
    loaded = False
    service_file.unlink()
    shutil.rmtree(root)

    refuse_lease = False
    join.install_executor(**arguments)
    assert loaded and service_file.exists() and (root / "data/machine-credential.json").exists()

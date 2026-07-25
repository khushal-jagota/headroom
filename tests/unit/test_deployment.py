from __future__ import annotations

import json
import multiprocessing
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from planner.environments.app import digest_app_artifact, digest_app_source
from planner.environments.deployment import (
    DeploymentError,
    DeploymentResult,
    SubprocessServiceController,
    deploy_app,
)

SHA_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SHA_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


class FakeService:
    def __init__(self, events: list[str], *, fail_on: set[int] | None = None) -> None:
        self.events = events
        self.fail_on = fail_on or set()
        self.calls = 0

    def restart(self) -> None:
        self.calls += 1
        self.events.append("restart")
        if self.calls in self.fail_on:
            raise OSError("restart failed")


class FakeHealth:
    def __init__(self, events: list[str], healthy: set[str]) -> None:
        self.events = events
        self.healthy = healthy

    def wait_for_sha(self, sha: str, *, deadline: float) -> bool:
        self.events.append(f"health:{sha}")
        return sha in self.healthy


def test_app_deploy_proves_compatibility_then_backups_and_replaces_only_app(
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    _app(current / "app", SHA_A, "old")
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    data = current / "data"
    logs = current / "logs"
    data.mkdir()
    logs.mkdir()
    database = data / "planning.db"
    database.write_text("db", encoding="utf-8")
    log = logs / "panels.log"
    log.write_text("log", encoding="utf-8")
    events: list[str] = []

    result = deploy_app(
        candidate_app=candidate,
        current_root=current,
        source_db=database,
        prove_compatibility=lambda *_: events.append("compatibility"),
        backup=lambda revision: events.append(f"backup:{revision}"),
        service=FakeService(events),
        health=FakeHealth(events, {SHA_B}),
        now=lambda: 10.0,
    )

    assert result == DeploymentResult("succeeded", SHA_B, SHA_A, None)
    assert events == ["compatibility", f"backup:{SHA_A}", "restart", f"health:{SHA_B}"]
    assert (current / "app" / "new").is_file()
    assert not (current / "app" / "old").exists()
    assert database.read_text(encoding="utf-8") == "db"
    assert log.read_text(encoding="utf-8") == "log"
    assert candidate.is_dir()
    assert list(current.glob(".app-*")) == []


def test_candidate_health_failure_restores_and_proves_prior_app(tmp_path: Path) -> None:
    current = tmp_path / "current"
    _app(current / "app", SHA_A, "old")
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    database = _database(current)
    events: list[str] = []
    result = deploy_app(
        candidate_app=candidate,
        current_root=current,
        source_db=database,
        prove_compatibility=lambda *_: events.append("compatibility"),
        backup=lambda revision: events.append(f"backup:{revision}"),
        service=FakeService(events),
        health=FakeHealth(events, {SHA_A}),
    )
    assert result.status == "rolled_back"
    assert (current / "app" / "old").is_file()
    assert events == [
        "compatibility",
        f"backup:{SHA_A}",
        "restart",
        f"health:{SHA_B}",
        "restart",
        f"health:{SHA_A}",
    ]
    assert list(current.glob(".app-*")) == []


def test_recovery_failure_reports_every_existing_continuation_path(tmp_path: Path) -> None:
    current = tmp_path / "current"
    _app(current / "app", SHA_A, "old")
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    database = _database(current)
    with pytest.raises(DeploymentError) as raised:
        deploy_app(
            candidate_app=candidate,
            current_root=current,
            source_db=database,
            prove_compatibility=lambda *_: None,
            backup=lambda _: None,
            service=FakeService([], fail_on={2}),
            health=FakeHealth([], set()),
        )
    detail = str(raised.value)
    assert str(current / "app") in detail
    retained = list(current.glob(".app-failed-*"))
    assert len(retained) == 1
    assert str(retained[0]) in detail
    assert (current / "app" / "old").is_file()
    assert (retained[0] / "new").is_file()


def test_unhealthy_restored_app_reports_every_existing_continuation_path(
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    _app(current / "app", SHA_A, "old")
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    database = _database(current)
    with pytest.raises(DeploymentError) as raised:
        deploy_app(
            candidate_app=candidate,
            current_root=current,
            source_db=database,
            prove_compatibility=lambda *_: None,
            backup=lambda _: None,
            service=FakeService([]),
            health=FakeHealth([], set()),
        )
    detail = str(raised.value)
    assert "prior app did not become healthy" in detail
    assert str(current / "app") in detail
    retained = list(current.glob(".app-failed-*"))
    assert len(retained) == 1
    assert str(retained[0]) in detail
    assert (current / "app" / "old").is_file()
    assert (retained[0] / "new").is_file()


def test_compatibility_or_backup_failure_leaves_current_app_unchanged(tmp_path: Path) -> None:
    current = tmp_path / "current"
    _app(current / "app", SHA_A, "old")
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    database = _database(current)

    def incompatible(*_: Path) -> None:
        raise RuntimeError("incompatible")

    with pytest.raises(DeploymentError, match="compatibility"):
        deploy_app(
            candidate_app=candidate,
            current_root=current,
            source_db=database,
            prove_compatibility=incompatible,
            backup=lambda _: (_ for _ in ()).throw(AssertionError("backup called")),
            service=FakeService([]),
            health=FakeHealth([], set()),
        )
    assert (current / "app" / "old").is_file()

    with pytest.raises(DeploymentError, match="backup failed"):
        deploy_app(
            candidate_app=candidate,
            current_root=current,
            source_db=database,
            prove_compatibility=lambda *_: None,
            backup=lambda _: (_ for _ in ()).throw(OSError("unavailable")),
            service=FakeService([]),
            health=FakeHealth([], set()),
        )
    assert (current / "app" / "old").is_file()


def test_existing_state_without_current_app_fails_without_baseline_model(tmp_path: Path) -> None:
    current = tmp_path / "current"
    database = _database(current)
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    with pytest.raises(DeploymentError, match="persistent state exists"):
        deploy_app(
            candidate_app=candidate,
            current_root=current,
            source_db=database,
            prove_compatibility=lambda *_: None,
            backup=lambda _: None,
            service=FakeService([]),
            health=FakeHealth([], {SHA_B}),
        )
    assert not (current / "app").exists()


def test_symlinked_current_root_is_rejected(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    current = tmp_path / "current"
    current.symlink_to(actual, target_is_directory=True)
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    with pytest.raises(DeploymentError, match="current root must not be a symlink"):
        deploy_app(
            candidate_app=candidate,
            current_root=current,
            source_db=actual / "data/planning.db",
            prove_compatibility=lambda *_: None,
            backup=lambda _: None,
            service=FakeService([]),
            health=FakeHealth([], {SHA_B}),
        )
    assert not (actual / "app").exists()


def test_failed_first_install_removes_unhealthy_app_for_safe_retry(tmp_path: Path) -> None:
    current = tmp_path / "current"
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    result = deploy_app(
        candidate_app=candidate,
        current_root=current,
        source_db=current / "data/planning.db",
        prove_compatibility=lambda *_: None,
        backup=lambda _: None,
        service=FakeService([]),
        health=FakeHealth([], set()),
    )
    assert result.status == "initial_failed"
    assert not (current / "app").exists()
    assert candidate.exists()


def test_fresh_first_install_and_same_sha_are_bounded(tmp_path: Path) -> None:
    current = tmp_path / "current"
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    events: list[str] = []
    result = deploy_app(
        candidate_app=candidate,
        current_root=current,
        source_db=current / "data/planning.db",
        prove_compatibility=lambda *_: events.append("compatibility"),
        backup=lambda _: events.append("backup"),
        service=FakeService(events),
        health=FakeHealth(events, {SHA_B}),
    )
    assert result == DeploymentResult("succeeded", SHA_B, None, None)
    assert events == ["restart", f"health:{SHA_B}"]

    events.clear()
    result = deploy_app(
        candidate_app=candidate,
        current_root=current,
        source_db=current / "data/planning.db",
        prove_compatibility=lambda *_: events.append("compatibility"),
        backup=lambda _: events.append("backup"),
        service=FakeService(events),
        health=FakeHealth(events, {SHA_B}),
    )
    assert result.status == "unchanged"
    assert events == [f"health:{SHA_B}"]


def test_same_sha_is_not_unchanged_when_installed_app_is_unhealthy(tmp_path: Path) -> None:
    current = tmp_path / "current"
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    _app(current / "app", SHA_B, "new")
    database = _database(current)
    with pytest.raises(DeploymentError, match="did not pass health proof"):
        deploy_app(
            candidate_app=candidate,
            current_root=current,
            source_db=database,
            prove_compatibility=lambda *_: None,
            backup=lambda _: None,
            service=FakeService([]),
            health=FakeHealth([], set()),
        )


def test_invalid_candidate_does_not_run_any_external_action(tmp_path: Path) -> None:
    current = tmp_path / "current"
    _app(current / "app", SHA_A, "old")
    database = _database(current)
    events: list[str] = []
    with pytest.raises(DeploymentError, match="candidate app"):
        deploy_app(
            candidate_app=tmp_path / "missing",
            current_root=current,
            source_db=database,
            prove_compatibility=lambda *_: events.append("compatibility"),
            backup=lambda _: events.append("backup"),
            service=FakeService(events),
            health=FakeHealth(events, set()),
        )
    assert events == []


def test_candidate_copy_failure_leaves_current_app_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = tmp_path / "current"
    _app(current / "app", SHA_A, "old")
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    database = _database(current)
    monkeypatch.setattr(
        "planner.environments.deployment.shutil.copytree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("copy failed")),
    )
    with pytest.raises(OSError, match="copy failed"):
        deploy_app(
            candidate_app=candidate,
            current_root=current,
            source_db=database,
            prove_compatibility=lambda *_: None,
            backup=lambda _: None,
            service=FakeService([]),
            health=FakeHealth([], {SHA_B}),
        )
    assert (current / "app" / "old").is_file()
    assert list(current.glob(".app-*")) == []


def test_staged_candidate_validation_failure_precedes_old_app_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = tmp_path / "current"
    _app(current / "app", SHA_A, "old")
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    database = _database(current)
    from planner.environments import deployment

    real_validate_app = deployment._validate_app

    def reject_staged(
        app: Path, label: str, *, expected_sha: str | None = None
    ):
        if label == "staged candidate app":
            raise DeploymentError("staged candidate app is invalid: injected")
        return real_validate_app(app, label, expected_sha=expected_sha)

    monkeypatch.setattr("planner.environments.deployment._validate_app", reject_staged)
    with pytest.raises(DeploymentError, match="staged candidate app is invalid"):
        deploy_app(
            candidate_app=candidate,
            current_root=current,
            source_db=database,
            prove_compatibility=lambda *_: None,
            backup=lambda _: None,
            service=FakeService([]),
            health=FakeHealth([], {SHA_B}),
        )
    assert (current / "app" / "old").is_file()
    assert list(current.glob(".app-*")) == []


def test_old_app_move_failure_leaves_current_app_and_removes_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = tmp_path / "current"
    _app(current / "app", SHA_A, "old")
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    database = _database(current)
    real_replace = os.replace

    def fail_old_move(source: Path, destination: Path) -> None:
        if source == current / "app":
            raise OSError("old app move failed")
        real_replace(source, destination)

    monkeypatch.setattr("planner.environments.deployment.os.replace", fail_old_move)
    with pytest.raises(OSError, match="old app move failed"):
        deploy_app(
            candidate_app=candidate,
            current_root=current,
            source_db=database,
            prove_compatibility=lambda *_: None,
            backup=lambda _: None,
            service=FakeService([]),
            health=FakeHealth([], {SHA_B}),
        )
    assert (current / "app" / "old").is_file()
    assert list(current.glob(".app-*")) == []


def test_candidate_move_failure_restores_and_proves_prior_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = tmp_path / "current"
    _app(current / "app", SHA_A, "old")
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    database = _database(current)
    events: list[str] = []
    real_replace = os.replace

    def fail_candidate_move(source: Path, destination: Path) -> None:
        if source.name.startswith(".app-candidate-"):
            raise OSError("candidate move failed")
        real_replace(source, destination)

    monkeypatch.setattr("planner.environments.deployment.os.replace", fail_candidate_move)
    result = deploy_app(
        candidate_app=candidate,
        current_root=current,
        source_db=database,
        prove_compatibility=lambda *_: None,
        backup=lambda _: None,
        service=FakeService(events),
        health=FakeHealth(events, {SHA_A}),
    )
    assert result.status == "rolled_back"
    assert (current / "app" / "old").is_file()
    assert events == ["restart", f"health:{SHA_A}"]
    assert list(current.glob(".app-*")) == []


def test_fallback_cleanup_failure_retains_healthy_candidate_and_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = tmp_path / "current"
    _app(current / "app", SHA_A, "old")
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    database = _database(current)
    real_rmtree = shutil.rmtree

    def fail_fallback_cleanup(path: Path, *args: object, **kwargs: object) -> None:
        if path.name.startswith(".app-fallback-"):
            raise OSError("fallback cleanup failed")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("planner.environments.deployment.shutil.rmtree", fail_fallback_cleanup)
    with pytest.raises(OSError, match="fallback cleanup failed"):
        deploy_app(
            candidate_app=candidate,
            current_root=current,
            source_db=database,
            prove_compatibility=lambda *_: None,
            backup=lambda _: None,
            service=FakeService([]),
            health=FakeHealth([], {SHA_B}),
        )
    assert (current / "app" / "new").is_file()
    fallback = list(current.glob(".app-fallback-*"))
    assert len(fallback) == 1
    assert (fallback[0] / "old").is_file()


def test_deployment_uses_operator_owned_interprocess_lock(tmp_path: Path) -> None:
    lock = tmp_path / "deploy.lock"
    ready = tmp_path / "ready"
    candidate = _app(tmp_path / "candidate", SHA_B, "new")

    def hold_lock() -> None:
        from planner.environments.deployment import deployment_lock

        with deployment_lock(lock):
            ready.write_text("ready", encoding="utf-8")
            time.sleep(0.35)

    process = multiprocessing.get_context("fork").Process(target=hold_lock)
    process.start()
    while not ready.exists():
        time.sleep(0.01)
    started = time.monotonic()
    result = deploy_app(
        candidate_app=candidate,
        current_root=tmp_path / "current",
        source_db=tmp_path / "current/data/planning.db",
        prove_compatibility=lambda *_: None,
        backup=lambda _: None,
        service=FakeService([]),
        health=FakeHealth([], {SHA_B}),
        lock_path=lock,
    )
    assert result.status == "succeeded"
    assert time.monotonic() - started >= 0.25
    process.join()


def test_launchctl_restart_accepts_explicit_user_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    print_calls = 0

    def run(
        command: list[str], *, check: bool, shell: bool, **_: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal print_calls
        assert shell is False
        commands.append(command)
        if command[1] == "print":
            print_calls += 1
            stdout = "    pid = 123\n" if print_calls == 1 else "    state = waiting\n"
            return subprocess.CompletedProcess(command, 0, stdout, "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    SubprocessServiceController("launchctl", "gui/501/com.panels.live").restart()
    assert [command[1] for command in commands] == [
        "kill",
        "print",
        "print",
        "bootout",
        "bootstrap",
    ]


def _database(current: Path) -> Path:
    data = current / "data"
    data.mkdir(parents=True, exist_ok=True)
    database = data / "planning.db"
    database.write_text("db", encoding="utf-8")
    return database


def _app(root: Path, sha: str, marker: str) -> Path:
    root.mkdir(parents=True)
    (root / marker).write_text(marker, encoding="utf-8")
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    planner_file = root / "src" / "planner" / "__init__.py"
    planner_file.parent.mkdir(parents=True)
    planner_file.write_text("", encoding="utf-8")
    python.write_text(
        "#!/bin/sh\n"
        'root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)\n'
        'printf "%s\\n" "$root/src/planner/__init__.py"\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    launcher = root / "bin" / "panels-launcher"
    launcher.parent.mkdir()
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    (root / "web" / "dist").mkdir(parents=True)
    (root / "web" / "dist" / "index.html").write_text("ok", encoding="utf-8")
    (root / "agent_backends" / "node_modules").mkdir(parents=True)
    (root / "agent_backends" / "node_modules" / ".package-lock.json").write_text(
        "{}\n", encoding="utf-8"
    )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "format": "panels-app-v1",
                "app_sha": sha,
                "source_digest": digest_app_source(root),
                "artifact_digest": digest_app_artifact(root),
            }
        ),
        encoding="utf-8",
    )
    return root

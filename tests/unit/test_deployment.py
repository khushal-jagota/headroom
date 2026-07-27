from __future__ import annotations

import json
import multiprocessing
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from planner.environments.app import AppManifest, digest_app_artifact, digest_app_source
from planner.environments.deployment import (
    DeploymentError,
    DeploymentResult,
    SubprocessServiceController,
    deploy_app,
    run_current_app_backup,
)

SHA_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SHA_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


class FakeService:
    def __init__(
        self,
        events: list[str],
        *,
        fail_on: set[int] | None = None,
        fail_stop: bool = False,
    ) -> None:
        self.events = events
        self.fail_on = fail_on or set()
        self.fail_stop = fail_stop
        self.calls = 0

    def restart(self) -> None:
        self.calls += 1
        self.events.append("restart")
        if self.calls in self.fail_on:
            raise OSError("restart failed")

    def stop(self) -> None:
        self.events.append("stop")
        if self.fail_stop:
            raise OSError("stop failed")


class FakeHealth:
    def __init__(self, events: list[str], healthy: set[str]) -> None:
        self.events = events
        self.healthy = healthy

    def wait_for_sha(self, sha: str, *, deadline: float) -> bool:
        self.events.append(f"health:{sha}")
        return sha in self.healthy


def _recording_backup(events: list[str], snapshot: Path) -> Callable[[str], Path]:
    def backup(revision: str) -> Path:
        events.append(f"backup:{revision}")
        return snapshot

    return backup


def test_app_deploy_stops_then_backups_and_hard_cuts_over(
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    _app(current / "app", SHA_A, "old", include_cli=False)
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
    snapshot = tmp_path / "snapshot"

    result = deploy_app(
        candidate_app=candidate,
        current_root=current,
        source_db=database,
        backup=_recording_backup(events, snapshot),
        restore=lambda _: events.append("restore"),
        service=FakeService(events),
        health=FakeHealth(events, {SHA_B}),
        now=lambda: 10.0,
    )

    assert result == DeploymentResult("succeeded", SHA_B, SHA_A, None)
    assert events == ["stop", f"backup:{SHA_A}", "restart", f"health:{SHA_B}"]
    assert (current / "app" / "new").is_file()
    assert not (current / "app" / "old").exists()
    assert database.read_text(encoding="utf-8") == "db"
    assert log.read_text(encoding="utf-8") == "log"
    assert candidate.is_dir()
    assert list(current.glob(".app-*")) == []


def test_candidate_health_failure_restores_and_proves_prior_app(tmp_path: Path) -> None:
    current = tmp_path / "current"
    _app(current / "app", SHA_A, "old", include_cli=False)
    prior_artifact_digest = digest_app_artifact(current / "app")
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    database = _database(current)
    events: list[str] = []
    snapshot = tmp_path / "snapshot"
    result = deploy_app(
        candidate_app=candidate,
        current_root=current,
        source_db=database,
        backup=_recording_backup(events, snapshot),
        restore=lambda path: events.append(f"restore:{path.name}"),
        service=FakeService(events),
        health=FakeHealth(events, {SHA_A}),
    )
    assert result.status == "rolled_back"
    assert (current / "app" / "old").is_file()
    assert digest_app_artifact(current / "app") == prior_artifact_digest
    assert json.loads((current / "app" / "manifest.json").read_text(encoding="utf-8"))[
        "app_sha"
    ] == SHA_A
    assert events == [
        "stop",
        f"backup:{SHA_A}",
        "restart",
        f"health:{SHA_B}",
        "stop",
        "restore:snapshot",
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
            backup=lambda _: tmp_path / "snapshot",
            restore=lambda _: None,
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
            backup=lambda _: tmp_path / "snapshot",
            restore=lambda _: None,
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


def test_service_stop_failure_restarts_and_proves_current_app(tmp_path: Path) -> None:
    current = tmp_path / "current"
    _app(current / "app", SHA_A, "old")
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    database = _database(current)
    events: list[str] = []

    with pytest.raises(DeploymentError, match="service stop failed"):
        deploy_app(
            candidate_app=candidate,
            current_root=current,
            source_db=database,
            backup=lambda _: (_ for _ in ()).throw(AssertionError("backup called")),
            restore=lambda _: (_ for _ in ()).throw(AssertionError("restore called")),
            service=FakeService(events, fail_stop=True),
            health=FakeHealth(events, {SHA_A}),
        )

    assert (current / "app" / "old").is_file()
    assert events == ["stop", "restart", f"health:{SHA_A}"]


def test_backup_failure_restarts_current_app_unchanged(tmp_path: Path) -> None:
    current = tmp_path / "current"
    _app(current / "app", SHA_A, "old")
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    database = _database(current)
    events: list[str] = []

    with pytest.raises(DeploymentError, match="backup failed"):
        deploy_app(
            candidate_app=candidate,
            current_root=current,
            source_db=database,
            backup=lambda _: (_ for _ in ()).throw(OSError("unavailable")),
            restore=lambda _: (_ for _ in ()).throw(AssertionError("restore called")),
            service=FakeService(events),
            health=FakeHealth(events, {SHA_A}),
        )
    assert (current / "app" / "old").is_file()
    assert events == ["stop", "restart", f"health:{SHA_A}"]


def test_existing_state_without_current_app_fails_without_baseline_model(tmp_path: Path) -> None:
    current = tmp_path / "current"
    database = _database(current)
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    with pytest.raises(DeploymentError, match="persistent state exists"):
        deploy_app(
            candidate_app=candidate,
            current_root=current,
            source_db=database,
            backup=lambda _: tmp_path / "snapshot",
            restore=lambda _: None,
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
            backup=lambda _: tmp_path / "snapshot",
            restore=lambda _: None,
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
        backup=lambda _: tmp_path / "snapshot",
        restore=lambda _: None,
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
        backup=_recording_backup(events, tmp_path / "snapshot"),
        restore=lambda _: events.append("restore"),
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
        backup=_recording_backup(events, tmp_path / "snapshot"),
        restore=lambda _: events.append("restore"),
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
            backup=lambda _: tmp_path / "snapshot",
            restore=lambda _: None,
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
            backup=_recording_backup(events, tmp_path / "snapshot"),
            restore=lambda _: events.append("restore"),
            service=FakeService(events),
            health=FakeHealth(events, set()),
        )
    assert events == []


def test_tampered_current_app_does_not_run_any_external_action(tmp_path: Path) -> None:
    current = tmp_path / "current"
    _app(current / "app", SHA_A, "old", include_cli=False)
    (current / "app" / "old").write_text("tampered", encoding="utf-8")
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    database = _database(current)
    events: list[str] = []

    with pytest.raises(DeploymentError, match="current app"):
        deploy_app(
            candidate_app=candidate,
            current_root=current,
            source_db=database,
            backup=_recording_backup(events, tmp_path / "snapshot"),
            restore=lambda _: events.append("restore"),
            service=FakeService(events),
            health=FakeHealth(events, set()),
        )

    assert events == []


def test_redigested_incomplete_candidate_does_not_run_any_external_action(
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    _app(current / "app", SHA_A, "old")
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    (candidate / "bin" / "panels").unlink()
    _rewrite_artifact_digest(candidate)
    database = _database(current)
    events: list[str] = []

    with pytest.raises(DeploymentError, match="runtime file is missing: bin/panels"):
        deploy_app(
            candidate_app=candidate,
            current_root=current,
            source_db=database,
            backup=_recording_backup(events, tmp_path / "snapshot"),
            restore=lambda _: events.append("restore"),
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
            backup=lambda _: tmp_path / "snapshot",
            restore=lambda _: None,
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
        app: Path,
        label: str,
        *,
        expected_sha: str | None = None,
        require_runtime: bool = True,
    ) -> AppManifest:
        if label == "staged candidate app":
            raise DeploymentError("staged candidate app is invalid: injected")
        return real_validate_app(
            app,
            label,
            expected_sha=expected_sha,
            require_runtime=require_runtime,
        )

    monkeypatch.setattr("planner.environments.deployment._validate_app", reject_staged)
    with pytest.raises(DeploymentError, match="staged candidate app is invalid"):
        deploy_app(
            candidate_app=candidate,
            current_root=current,
            source_db=database,
            backup=lambda _: tmp_path / "snapshot",
            restore=lambda _: None,
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

    events: list[str] = []
    monkeypatch.setattr("planner.environments.deployment.os.replace", fail_old_move)
    with pytest.raises(OSError, match="old app move failed"):
        deploy_app(
            candidate_app=candidate,
            current_root=current,
            source_db=database,
            backup=lambda _: tmp_path / "snapshot",
            restore=lambda _: None,
            service=FakeService(events),
            health=FakeHealth(events, {SHA_A}),
        )
    assert events == ["stop", "restart", f"health:{SHA_A}"]
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
        backup=lambda _: tmp_path / "snapshot",
        restore=lambda _: None,
        service=FakeService(events),
        health=FakeHealth(events, {SHA_A}),
    )
    assert result.status == "rolled_back"
    assert (current / "app" / "old").is_file()
    assert events == ["stop", "stop", "restart", f"health:{SHA_A}"]
    assert list(current.glob(".app-*")) == []


def test_fallback_cleanup_failure_retains_healthy_candidate_and_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = tmp_path / "current"
    _app(current / "app", SHA_A, "old")
    candidate = _app(tmp_path / "candidate", SHA_B, "new")
    database = _database(current)
    real_rmtree = shutil.rmtree

    def fail_fallback_cleanup(path: Path, ignore_errors: bool = False) -> None:
        if path.name.startswith(".app-fallback-"):
            raise OSError("fallback cleanup failed")
        real_rmtree(path, ignore_errors=ignore_errors)

    monkeypatch.setattr("planner.environments.deployment.shutil.rmtree", fail_fallback_cleanup)
    with pytest.raises(OSError, match="fallback cleanup failed"):
        deploy_app(
            candidate_app=candidate,
            current_root=current,
            source_db=database,
            backup=lambda _: tmp_path / "snapshot",
            restore=lambda _: None,
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
        backup=lambda _: tmp_path / "snapshot",
        restore=lambda _: None,
        service=FakeService([]),
        health=FakeHealth([], {SHA_B}),
        lock_path=lock,
    )
    assert result.status == "succeeded"
    assert time.monotonic() - started >= 0.25
    process.join()


def test_launchctl_stop_boots_out_explicit_user_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def run(
        command: list[str], *, check: bool, shell: bool, **_: object
    ) -> subprocess.CompletedProcess[str]:
        assert check is True
        assert shell is False
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    SubprocessServiceController("launchctl", "gui/501/com.panels.live").stop()
    assert commands == [["/bin/launchctl", "bootout", "gui/501/com.panels.live"]]


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


def test_systemctl_restart_targets_the_callers_user_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def run(
        command: list[str], *, check: bool, shell: bool, **_: object
    ) -> subprocess.CompletedProcess[str]:
        assert check is True
        assert shell is False
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    controller = SubprocessServiceController("systemctl", "panels-live.service")
    controller.stop()
    controller.restart()
    assert commands == [
        ["systemctl", "--user", "stop", "panels-live.service"],
        ["systemctl", "--user", "restart", "panels-live.service"],
    ]


def test_predeploy_backup_runs_through_the_current_deployed_launcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = tmp_path / "current"
    launcher = current / "app" / "bin" / "panels-launcher"
    launcher.parent.mkdir(parents=True)
    source_db = current / "data" / "planner.db"
    backup_dir = current / "data" / "backups"
    commands: list[list[str]] = []
    environments: list[dict[str, str]] = []

    def run(
        command: list[str],
        *,
        check: bool,
        env: dict[str, str],
        shell: bool,
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        assert check is True
        assert shell is False
        commands.append(command)
        environments.append(env)
        return subprocess.CompletedProcess(command, 0, f"{backup_dir / 'snapshot-1'}\n", "")

    monkeypatch.setenv("HOME", str(tmp_path / "vps"))
    monkeypatch.setattr(subprocess, "run", run)
    snapshot = run_current_app_backup(current, source_db, backup_dir)
    assert snapshot == (backup_dir / "snapshot-1").resolve()
    assert commands == [
        [
            str(launcher),
            "environment",
            "backup-current",
            "--source-db",
            str(source_db),
            "--backup-dir",
            str(backup_dir),
            "--current-app",
            str(current / "app"),
        ]
    ]
    assert environments[0]["PLAN_HERMES_HOME"] == str(tmp_path / "vps" / ".hermes")


def _database(current: Path) -> Path:
    data = current / "data"
    data.mkdir(parents=True, exist_ok=True)
    database = data / "planning.db"
    database.write_text("db", encoding="utf-8")
    return database


def _app(
    root: Path,
    sha: str,
    marker: str,
    *,
    include_cli: bool = True,
) -> Path:
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
    if include_cli:
        cli = root / "bin" / "panels"
        cli.write_text("#!/bin/sh\n", encoding="utf-8")
        cli.chmod(0o755)
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


def _rewrite_artifact_digest(app: Path) -> None:
    manifest_path = app / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_digest"] = digest_app_artifact(app)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

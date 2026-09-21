from __future__ import annotations

import json
import multiprocessing
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from planner.environments.app import digest_app_artifact, digest_app_source
from planner.environments.deployment import (
    DeploymentError,
    DeploymentResult,
    deploy_app,
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
    lifecycle: list[tuple[str, dict[str, object]]] = []
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
        lifecycle_transition=lambda phase, **values: lifecycle.append((phase, values)),
    )

    assert result == DeploymentResult("succeeded", SHA_B, SHA_A, None)
    assert events == ["stop", f"backup:{SHA_A}", "restart", f"health:{SHA_B}"]
    assert lifecycle == [
        ("restarting", {"serving_sha": None, "detail": None, "code": None}),
        ("verifying", {"serving_sha": None, "detail": None, "code": None}),
        (
            "app_healthy",
            {"serving_sha": SHA_B, "detail": None, "code": None},
        ),
    ]
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
    lifecycle: list[tuple[str, dict[str, object]]] = []
    result = deploy_app(
        candidate_app=candidate,
        current_root=current,
        source_db=database,
        backup=_recording_backup(events, snapshot),
        restore=lambda path: events.append(f"restore:{path.name}"),
        service=FakeService(events),
        health=FakeHealth(events, {SHA_A}),
        lifecycle_transition=lambda phase, **values: lifecycle.append((phase, values)),
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
    assert [phase for phase, _ in lifecycle] == [
        "restarting",
        "verifying",
        "restarting",
        "verifying",
        "rolled_back",
    ]
    assert lifecycle[-1] == (
        "rolled_back",
        {
            "serving_sha": SHA_A,
            "detail": "The requested app failed health proof; the prior app was restored.",
            "code": "candidate_unhealthy",
        },
    )


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

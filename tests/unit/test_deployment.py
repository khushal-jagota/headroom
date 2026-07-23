from __future__ import annotations

import json
import multiprocessing
import subprocess
import time
from pathlib import Path

import pytest

from planner.environments.deployment import (
    DeploymentError,
    DeploymentResult,
    SubprocessServiceController,
    deploy_release,
)

SHA_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SHA_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _release(root: Path, sha: str, marker: str) -> Path:
    root.mkdir(parents=True)
    (root / marker).write_text(marker, encoding="utf-8")
    (root / ".venv" / "bin").mkdir(parents=True)
    python = root / ".venv" / "bin" / "python"
    planner_file = root / "src" / "planner" / "__init__.py"
    planner_file.parent.mkdir(parents=True)
    planner_file.write_text("", encoding="utf-8")
    python.write_text(
        f"#!/bin/sh\nprintf '%s\\n' '{planner_file}'\n",
        encoding="utf-8",
    )
    python.chmod(0o755)
    (root / "bin").mkdir()
    launcher = root / "bin" / "panels-launcher"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    (root / "web" / "dist").mkdir(parents=True)
    (root / "web" / "dist" / "index.html").write_text("ok", encoding="utf-8")
    (root / "agent_backends" / "node_modules").mkdir(parents=True)
    (root / "agent_backends" / "node_modules" / ".package-lock.json").write_text(
        "{}\n", encoding="utf-8"
    )
    from planner.environments.release import digest_release_artifact, digest_release_source

    (root / "manifest.json").write_text(
        json.dumps(
            {
                "format": "panels-release-v1",
                "release_sha": sha,
                "source_digest": digest_release_source(root),
                "artifact_digest": digest_release_artifact(root),
            }
        ),
        encoding="utf-8",
    )
    return root


class FakeService:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def restart(self) -> None:
        self.events.append("restart")


class FakeHealth:
    def __init__(self, events: list[str], healthy: set[str]) -> None:
        self.events = events
        self.healthy = healthy

    def wait_for_sha(self, sha: str, *, deadline: float) -> bool:
        self.events.append(f"health:{sha}")
        return sha in self.healthy


def test_launchctl_restart_accepts_an_explicit_user_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def run(command: list[str], *, check: bool, shell: bool) -> None:
        assert check is True
        assert shell is False
        commands.append(command)

    monkeypatch.setattr(subprocess, "run", run)
    SubprocessServiceController("launchctl", "gui/501/com.panels.live").restart()

    assert commands == [["/bin/launchctl", "kickstart", "-k", "gui/501/com.panels.live"]]


def test_deploy_backups_prior_manifest_before_switch_and_records_success(tmp_path: Path) -> None:
    release_root = tmp_path / "releases"
    release_root.mkdir()
    prior = _release(release_root / SHA_A, SHA_A, "old")
    candidate = _release(release_root / SHA_B, SHA_B, "new")
    current = tmp_path / "current"
    current.symlink_to(prior, target_is_directory=True)
    events: list[str] = []

    result = deploy_release(
        candidate=candidate,
        current_pointer=current,
        backup=lambda revision: events.append(f"backup:{revision}"),
        service=FakeService(events),
        health=FakeHealth(events, {SHA_B}),
        records_path=tmp_path / "records.jsonl",
        now=lambda: 10.0,
    )

    assert result == DeploymentResult("succeeded", SHA_B, SHA_A, None)
    assert events == [f"backup:{SHA_A}", "restart", f"health:{SHA_B}"]
    assert current.resolve() == candidate
    assert json.loads((tmp_path / "records.jsonl").read_text())["result"] == "succeeded"


def test_failed_candidate_validation_does_not_backup_or_switch(tmp_path: Path) -> None:
    current = tmp_path / "current"
    prior = _release(tmp_path / "prior", SHA_A, "old")
    current.symlink_to(prior, target_is_directory=True)
    events: list[str] = []
    with pytest.raises(DeploymentError, match="candidate"):
        deploy_release(
            candidate=tmp_path / "missing",
            current_pointer=current,
            backup=lambda revision: events.append(f"backup:{revision}"),
            service=FakeService(events),
            health=FakeHealth(events, {SHA_B}),
            records_path=tmp_path / "records.jsonl",
        )
    assert events == []
    assert current.resolve() == prior


def test_failed_candidate_health_rolls_back_code_and_proves_prior(tmp_path: Path) -> None:
    prior = _release(tmp_path / SHA_A, SHA_A, "old")
    candidate = _release(tmp_path / SHA_B, SHA_B, "new")
    current = tmp_path / "current"
    current.symlink_to(prior, target_is_directory=True)
    events: list[str] = []
    health = FakeHealth(events, {SHA_A})
    result = deploy_release(
        candidate=candidate,
        current_pointer=current,
        backup=lambda revision: events.append(f"backup:{revision}"),
        service=FakeService(events),
        health=health,
        records_path=tmp_path / "records.jsonl",
    )
    assert result.status == "rolled_back"
    assert result.requested_sha == SHA_B
    assert result.prior_sha == SHA_A
    assert current.resolve() == prior
    assert events == [f"backup:{SHA_A}", "restart", f"health:{SHA_B}", "restart", f"health:{SHA_A}"]


def test_same_sha_is_idempotent_without_backup_or_restart(tmp_path: Path) -> None:
    prior = _release(tmp_path / SHA_A, SHA_A, "old")
    current = tmp_path / "current"
    current.symlink_to(prior, target_is_directory=True)
    events: list[str] = []
    result = deploy_release(
        candidate=prior,
        current_pointer=current,
        backup=lambda revision: events.append(f"backup:{revision}"),
        service=FakeService(events),
        health=FakeHealth(events, {SHA_A}),
        records_path=tmp_path / "records.jsonl",
    )
    assert result.status == "unchanged"
    assert events == []


def test_same_sha_reuse_rejects_incomplete_release(tmp_path: Path) -> None:
    release = _runtime_release(tmp_path / SHA_A, SHA_A, "old")
    (release / ".venv" / "bin" / "python").unlink()
    from planner.environments.release import digest_release_artifact

    manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifact_digest"] = digest_release_artifact(release)
    (release / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    current = tmp_path / "current"
    current.symlink_to(release, target_is_directory=True)
    with pytest.raises(DeploymentError, match="runtime file is missing"):
        deploy_release(
            candidate=release,
            current_pointer=current,
            backup=lambda _: None,
            service=FakeService([]),
            health=FakeHealth([], {SHA_A}),
            records_path=tmp_path / "records.jsonl",
            release_root=tmp_path,
        )


def test_initial_deployment_skips_backup_and_removes_current_after_health_failure(
    tmp_path: Path,
) -> None:
    releases = tmp_path / "releases"
    candidate = _release(releases / SHA_B, SHA_B, "new")
    current = tmp_path / "current"
    events: list[str] = []
    result = deploy_release(
        candidate=candidate,
        current_pointer=current,
        backup=lambda revision: events.append(f"backup:{revision}"),
        service=FakeService(events),
        health=FakeHealth(events, set()),
        records_path=tmp_path / "records.jsonl",
        release_root=releases,
        health_timeout_seconds=0.001,
    )
    assert result.status == "initial_failed"
    assert "operator state path" in (result.detail or "")
    assert not current.exists()
    assert events[0] == "restart"
    assert json.loads((tmp_path / "records.jsonl").read_text())["result"] == "initial_failed"


def test_initial_deployment_backups_existing_database_before_switch(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    candidate = _runtime_release(releases / SHA_B, SHA_B, "new")
    source_db = tmp_path / "planner.db"
    source_db.write_text("existing", encoding="utf-8")
    events: list[str] = []
    result = deploy_release(
        candidate=candidate,
        current_pointer=tmp_path / "current",
        backup=lambda revision: events.append(f"backup:{revision}"),
        service=FakeService(events),
        health=FakeHealth(events, {SHA_B}),
        records_path=tmp_path / "records.jsonl",
        release_root=releases,
        source_db=source_db,
        baseline_sha=SHA_A,
    )
    assert result.status == "succeeded"
    assert events == [f"backup:{SHA_A}", "restart", f"health:{SHA_B}"]


def test_initial_existing_database_without_baseline_does_not_switch(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    candidate = _runtime_release(releases / SHA_B, SHA_B, "new")
    source_db = tmp_path / "planner.db"
    source_db.write_text("existing", encoding="utf-8")
    events: list[str] = []
    with pytest.raises(DeploymentError, match="baseline"):
        deploy_release(
            candidate=candidate,
            current_pointer=tmp_path / "current",
            backup=lambda revision: events.append(f"backup:{revision}"),
            service=FakeService(events),
            health=FakeHealth(events, {SHA_B}),
            records_path=tmp_path / "records.jsonl",
            release_root=releases,
            source_db=source_db,
        )
    assert events == []
    assert not (tmp_path / "current").exists()


def test_missing_initial_baseline_records_the_failed_attempt(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    candidate = _runtime_release(releases / SHA_B, SHA_B, "new")
    source_db = tmp_path / "planner.db"
    source_db.write_text("existing", encoding="utf-8")
    records = tmp_path / "records.jsonl"

    with pytest.raises(DeploymentError, match="baseline"):
        deploy_release(
            candidate=candidate,
            current_pointer=tmp_path / "current",
            backup=lambda _: None,
            service=FakeService([]),
            health=FakeHealth([], {SHA_B}),
            records_path=records,
            release_root=releases,
            source_db=source_db,
        )

    assert json.loads(records.read_text())["result"] == "initial_failed"


def test_initial_backup_failure_leaves_no_pointer_or_restart(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    candidate = _runtime_release(releases / SHA_B, SHA_B, "new")
    source_db = tmp_path / "planner.db"
    source_db.write_text("existing", encoding="utf-8")
    events: list[str] = []

    def fail_backup(_: str) -> None:
        events.append("backup")
        raise OSError("backup unavailable")

    result = deploy_release(
        candidate=candidate,
        current_pointer=tmp_path / "current",
        backup=fail_backup,
        service=FakeService(events),
        health=FakeHealth(events, {SHA_B}),
        records_path=tmp_path / "records.jsonl",
        release_root=releases,
        source_db=source_db,
        baseline_sha=SHA_A,
    )
    assert result.status == "initial_failed"
    assert events == ["backup"]
    assert not (tmp_path / "current").exists()


def _runtime_release(root: Path, sha: str, marker: str) -> Path:
    release = _release(root, sha, marker)
    from planner.environments.release import digest_release_artifact

    (release / "manifest.json").write_text(
        json.dumps(
            {
                "format": "panels-release-v1",
                "release_sha": sha,
                "source_digest": "a" * 64,
                "artifact_digest": digest_release_artifact(release),
            }
        ),
        encoding="utf-8",
    )
    return release


def test_deployment_rejects_candidate_outside_release_root(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    releases.mkdir()
    candidate = _release(tmp_path / SHA_B, SHA_B, "new")
    with pytest.raises(DeploymentError, match="release root"):
        deploy_release(
            candidate=candidate,
            current_pointer=tmp_path / "current",
            backup=lambda _: None,
            service=FakeService([]),
            health=FakeHealth([], {SHA_B}),
            records_path=tmp_path / "records.jsonl",
            release_root=releases,
        )


def test_pointer_switch_failure_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    releases = tmp_path / "releases"
    prior = _release(releases / SHA_A, SHA_A, "old")
    candidate = _release(releases / SHA_B, SHA_B, "new")
    current = tmp_path / "current"
    current.symlink_to(prior, target_is_directory=True)
    monkeypatch.setattr(
        "planner.environments.deployment._switch_pointer",
        lambda *_: (_ for _ in ()).throw(OSError("pointer denied")),
    )
    with pytest.raises(OSError, match="pointer denied"):
        deploy_release(
            candidate=candidate,
            current_pointer=current,
            backup=lambda _: None,
            service=FakeService([]),
            health=FakeHealth([], {SHA_B}),
            records_path=tmp_path / "records.jsonl",
            release_root=releases,
        )
    assert json.loads((tmp_path / "records.jsonl").read_text())["result"] == "failed"


def test_deployment_uses_operator_owned_interprocess_lock(tmp_path: Path) -> None:
    lock = tmp_path / "deploy.lock"
    ready = tmp_path / "ready"
    release = _release(tmp_path / SHA_B, SHA_B, "new")

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
    result = deploy_release(
        candidate=release,
        current_pointer=tmp_path / "current",
        backup=lambda _: None,
        service=FakeService([]),
        health=FakeHealth([], {SHA_B}),
        records_path=tmp_path / "records.jsonl",
        release_root=tmp_path,
        lock_path=lock,
    )
    assert result.status == "succeeded"
    assert time.monotonic() - started >= 0.25
    process.join()

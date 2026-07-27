from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from planner.environments import materialize
from planner.environments.contracts import EnvironmentManifest, EnvironmentValidationError
from planner.server_lifecycle.supervisor import PortScopedServerLifecycleLease


def test_staging_prepare_is_persistent_and_inspect_has_no_runtime_port(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "staging-repo")
    root = _environment_root(tmp_path)
    prepared = materialize.prepare_environment_instance(
        kind="staging",
        environment_root=root,
        repository_roots=(repository,),
        now=123,
    )
    marker = prepared.managed_files_root / "activity.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("retained", encoding="utf-8")

    inspected = materialize.inspect_environment_instance(
        kind="staging",
        environment_root=root,
        repository_roots=(),
    )
    payload = materialize.manifest_to_json_dict(inspected)

    assert marker.read_text(encoding="utf-8") == "retained"
    assert payload["runtime_port_policy"] == "dynamic"
    assert payload["bind_attempts"] == 10
    assert "port" not in payload
    assert "running" not in payload
    manifest_payload = json.loads((prepared.instance_root / "manifest.json").read_text())
    assert manifest_payload["runtime_port"] == {"bind_attempts": 10, "kind": "dynamic"}
    assert "port" not in manifest_payload


def test_staging_reset_replaces_fake_state_but_preserves_instance_contract(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "staging-repo")
    root = _environment_root(tmp_path)
    prepared = materialize.prepare_environment_instance(
        kind="staging",
        environment_root=root,
        repository_roots=(repository,),
        now=123,
    )
    marker = prepared.managed_files_root / "activity.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("discard", encoding="utf-8")
    reset = materialize.reset_environment_instance(
        kind="staging",
        environment_root=root,
        repository_roots=(),
        now=456,
    )

    assert reset.instance_root == prepared.instance_root
    assert reset.prepared_at == 456
    assert reset.db_path.is_file()
    assert not marker.exists()


def test_staging_reset_refuses_while_instance_lifecycle_is_owned(tmp_path: Path) -> None:
    repository = _repository(tmp_path, "staging-repo")
    root = _environment_root(tmp_path)
    prepared = materialize.prepare_environment_instance(
        kind="staging",
        environment_root=root,
        repository_roots=(repository,),
    )
    lease = PortScopedServerLifecycleLease(
        prepared.instance_root / "run" / "server-lifecycle.lock",
        0,
    )
    lease.acquire()
    try:
        with pytest.raises(EnvironmentValidationError, match="is running"):
            materialize.reset_environment_instance(
                kind="staging",
                environment_root=root,
                repository_roots=(),
            )
    finally:
        lease.release()


@pytest.mark.parametrize(
    "field",
    (
        "environment_root",
        "instance_root",
        "db_path",
        "managed_files_root",
        "logs_dir",
        "dispatcher_lock_path",
        "server_control_socket_path",
    ),
)
def test_inspect_rejects_tampered_manifest_paths(tmp_path: Path, field: str) -> None:
    repository = _repository(tmp_path, "staging-repo")
    root = _environment_root(tmp_path)
    prepared = materialize.prepare_environment_instance(
        kind="staging",
        environment_root=root,
        repository_roots=(repository,),
    )
    manifest_path = prepared.instance_root / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload[field] = str((tmp_path / "outside" / field).resolve())
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EnvironmentValidationError, match="manifest"):
        materialize.inspect_environment_instance(
            kind="staging",
            environment_root=root,
            repository_roots=(),
        )


@pytest.mark.parametrize(
    "legacy_field",
    ("expected_linux_account", "hermes_home", "runtime_user_home"),
)
def test_inspect_requires_legacy_manifests_to_be_reprepared(
    tmp_path: Path,
    legacy_field: str,
) -> None:
    repository = _repository(tmp_path, "staging-repo")
    root = _environment_root(tmp_path)
    prepared = materialize.prepare_environment_instance(
        kind="staging",
        environment_root=root,
        repository_roots=(repository,),
    )
    manifest_path = prepared.instance_root / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload[legacy_field] = "legacy"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EnvironmentValidationError, match="must be re-prepared"):
        materialize.inspect_environment_instance(
            kind="staging",
            environment_root=root,
            repository_roots=(),
        )


def test_reprepare_rejects_live_port_change(tmp_path: Path) -> None:
    root = _environment_root(tmp_path)
    port = _test_port(tmp_path)
    materialize.prepare_environment_instance(
        kind="live",
        environment_root=root,
        port=port,
        repository_roots=(),
    )
    with pytest.raises(EnvironmentValidationError, match="port"):
        materialize.prepare_environment_instance(
            kind="live",
            environment_root=root,
            port=port + 1,
            repository_roots=(),
        )


def test_live_prepare_records_contract_without_precreating_deployment_state(
    tmp_path: Path,
) -> None:
    root = _environment_root(tmp_path)

    prepared = materialize.prepare_environment_instance(
        kind="live",
        environment_root=root,
        repository_roots=(),
        now=123,
    )

    assert (root / "manifest.json").is_file()
    assert prepared.instance_root == root
    assert not (root / "current").exists()


def test_staging_remove_requires_stopped_instance_and_removes_only_its_root(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "staging-repo")
    root = _environment_root(tmp_path)
    prepared = materialize.prepare_environment_instance(
        kind="staging",
        environment_root=root,
        repository_roots=(repository,),
    )
    sibling = root / "keep.txt"
    sibling.write_text("keep", encoding="utf-8")
    lease = PortScopedServerLifecycleLease(
        prepared.instance_root / "run" / "server-lifecycle.lock", 0
    )
    lease.acquire()
    try:
        with pytest.raises(EnvironmentValidationError, match="is running"):
            materialize.remove_environment_instance(
                kind="staging", environment_root=root, repository_roots=()
            )
    finally:
        lease.release()
    materialize.remove_environment_instance(
        kind="staging", environment_root=root, repository_roots=()
    )
    assert not prepared.instance_root.exists()
    assert sibling.read_text(encoding="utf-8") == "keep"


def test_staging_operations_never_read_the_live_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_root = _environment_root(tmp_path) / "deployments"
    staging_root = _environment_root(tmp_path) / "coding"
    staging_repository = _repository(tmp_path, "staging-repo")
    materialize.prepare_environment_instance(
        kind="live",
        environment_root=live_root,
        port=_test_port(tmp_path),
        repository_roots=(),
    )
    materialize.prepare_environment_instance(
        kind="staging",
        environment_root=staging_root,
        repository_roots=(staging_repository,),
    )
    live_manifest = live_root / "manifest.json"
    real_read_manifest = materialize._read_manifest

    def guarded_read_manifest(
        path: Path, *, caller_environment_root: Path | None = None
    ) -> EnvironmentManifest:
        if path == live_manifest:
            raise AssertionError("live manifest was opened")
        return real_read_manifest(path, caller_environment_root=caller_environment_root)

    monkeypatch.setattr(materialize, "_read_manifest", guarded_read_manifest)
    materialize.inspect_environment_instance(
        kind="staging", environment_root=staging_root, repository_roots=()
    )
    materialize.reset_environment_instance(
        kind="staging", environment_root=staging_root, repository_roots=()
    )
    materialize.remove_environment_instance(
        kind="staging", environment_root=staging_root, repository_roots=()
    )


def test_reset_failure_keeps_previous_staging_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path, "staging-repo")
    root = _environment_root(tmp_path)
    prepared = materialize.prepare_environment_instance(
        kind="staging", environment_root=root, repository_roots=(repository,)
    )
    original = prepared.db_path.read_bytes()

    def fail_fixture(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected fixture failure")

    monkeypatch.setattr(materialize, "build_fake_environment_database", fail_fixture)
    with pytest.raises(RuntimeError, match="injected"):
        materialize.reset_environment_instance(
            kind="staging", environment_root=root, repository_roots=()
        )
    assert prepared.db_path.read_bytes() == original


def _repository(tmp_path: Path, name: str) -> Path:
    repository = tmp_path / name
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    shutil.copytree(
        Path(__file__).resolve().parents[2] / "src" / "planner" / "skills",
        repository / "src" / "planner" / "skills",
    )
    return repository.resolve()


def _environment_root(tmp_path: Path) -> Path:
    return Path("/tmp") / f"pe-{tmp_path.parent.name}-{tmp_path.name}"


def _test_port(tmp_path: Path) -> int:
    return 30_000 + sum(tmp_path.name.encode("utf-8")) % 20_000

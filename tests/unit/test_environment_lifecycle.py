from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from planner.environments import materialize
from planner.environments.contracts import EnvironmentManifest, EnvironmentValidationError
from planner.server_lifecycle.supervisor import PortScopedServerLifecycleLease


def test_staging_reset_refuses_while_instance_lifecycle_is_owned(
    tmp_path: Path, short_environment_root: Path
) -> None:
    repository = _repository(tmp_path, "staging-repo")
    root = short_environment_root
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


def test_staging_operations_never_read_the_live_manifest(
    tmp_path: Path,
    short_environment_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_root = short_environment_root / "deployments"
    staging_root = short_environment_root / "coding"
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
    short_environment_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path, "staging-repo")
    root = short_environment_root
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


@pytest.fixture
def short_environment_root() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="pe-", dir=Path("/tmp").resolve()) as root:
        yield Path(root)


def _test_port(tmp_path: Path) -> int:
    return 30_000 + sum(tmp_path.name.encode("utf-8")) % 20_000

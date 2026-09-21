from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from planner.environments import materialize
from planner.environments.contracts import EnvironmentManifest, EnvironmentValidationError
from planner.server_lifecycle.supervisor import PortScopedServerLifecycleLease


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
    """A short, already-resolved root for a real environment estate.

    Resolved because the code resolves an environment root before measuring the control
    socket inside it against the AF_UNIX path limit, and ``/tmp`` is a symlink on macOS —
    the eight characters that resolution adds are on their own enough to push the socket
    over. Short for the same reason: the whole of that path has to fit in 103 bytes, so
    the test's identity goes in as a digest rather than as its name.
    """
    digest = hashlib.sha1(str(tmp_path).encode("utf-8")).hexdigest()[:8]
    return Path("/tmp").resolve() / f"pe-{os.getpid()}-{digest}"


def _test_port(tmp_path: Path) -> int:
    return 30_000 + sum(tmp_path.name.encode("utf-8")) % 20_000

from __future__ import annotations

import hashlib
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from planner.environments import materialize
from planner.environments.contracts import (
    EnvironmentDefaults,
    EnvironmentPortRange,
    EnvironmentValidationError,
)
from planner.server_lifecycle.contracts import ServerLifecycleAlreadyOwnedError
from planner.server_lifecycle.control import resolve_server_lifecycle_lease_path
from planner.server_lifecycle.supervisor import PortScopedServerLifecycleLease


@pytest.mark.parametrize(
    ("field", "tampered_value"),
    [
        ("kind", "staging"),
        ("instance_id", "other-feature"),
        ("environment_root", "outside-root"),
        ("instance_root", "outside-root/previews/feature"),
        ("db_path", "outside-root/data/planner.db"),
        ("managed_files_root", "outside-root/data/files"),
        ("hermes_home", "outside-root/hermes-home"),
        ("logs_dir", "outside-root/logs"),
        ("dispatcher_lock_path", "outside-root/run/dispatcher.lock"),
        ("server_control_socket_path", "outside-root/run/server-control.sock"),
        ("expected_linux_account", "panels-live"),
        ("fixture_version", "tampered-fixture"),
        ("repository_roots", "outside-root/repository"),
    ],
)
def test_inspect_rejects_tampered_manifest_fields(
    tmp_path: Path,
    field: str,
    tampered_value: str,
) -> None:
    repository_root = _repository_root()
    environment_root = _short_environment_root(tmp_path)
    prepared = materialize.prepare_environment_instance(
        kind="preview",
        instance_id="feature",
        environment_root=environment_root,
        port=9120,
        repository_roots=(repository_root,),
    )
    outside_root = tmp_path / "outside"
    outside_repository = outside_root / "repository"
    outside_repository.mkdir(parents=True)
    (outside_repository / ".git").mkdir()
    replacement = str(tmp_path / tampered_value)
    payload = _manifest_payload(prepared.instance_root)
    payload[field] = [replacement] if field == "repository_roots" else replacement
    _write_manifest_payload(prepared.instance_root, payload)

    with pytest.raises(EnvironmentValidationError, match="manifest"):
        materialize.inspect_environment_instance(
            kind="preview",
            instance_id="feature",
            environment_root=environment_root,
            repository_roots=(),
        )


def test_run_rejects_tampered_manifest_before_exec(tmp_path: Path) -> None:
    repository_root = _repository_root()
    environment_root = _short_environment_root(tmp_path)
    prepared = materialize.prepare_environment_instance(
        kind="preview",
        instance_id="feature",
        environment_root=environment_root,
        port=9121,
        repository_roots=(repository_root,),
    )
    payload = _manifest_payload(prepared.instance_root)
    payload["db_path"] = str(tmp_path / "outside" / "planner.db")
    _write_manifest_payload(prepared.instance_root, payload)

    with pytest.raises(EnvironmentValidationError, match="manifest"):
        materialize.inspect_environment_instance(
            kind="preview",
            instance_id="feature",
            environment_root=environment_root,
            repository_roots=(),
        )


def test_remove_rejects_tampered_instance_root_and_never_deletes_that_path(
    tmp_path: Path,
) -> None:
    repository_root = _repository_root()
    environment_root = _short_environment_root(tmp_path)
    prepared = materialize.prepare_environment_instance(
        kind="preview",
        instance_id="feature",
        environment_root=environment_root,
        port=9122,
        repository_roots=(repository_root,),
    )
    outside_root = tmp_path / "outside-delete-target"
    outside_root.mkdir()
    sentinel = outside_root / "sentinel.txt"
    sentinel.write_text("do not delete", encoding="utf-8")
    payload = _manifest_payload(prepared.instance_root)
    payload["instance_root"] = str(outside_root)
    _write_manifest_payload(prepared.instance_root, payload)

    with pytest.raises(EnvironmentValidationError, match="manifest"):
        materialize.remove_environment_instance(
            kind="preview",
            instance_id="feature",
            environment_root=environment_root,
            repository_roots=(),
        )

    assert sentinel.read_text(encoding="utf-8") == "do not delete"
    assert prepared.instance_root.exists()


def test_reset_rejects_tampered_environment_root_before_materializing_there(
    tmp_path: Path,
) -> None:
    repository_root = _repository_root()
    environment_root = _short_environment_root(tmp_path)
    prepared = materialize.prepare_environment_instance(
        kind="preview",
        instance_id="feature",
        environment_root=environment_root,
        port=9123,
        repository_roots=(repository_root,),
    )
    outside_environment_root = tmp_path / "outside-envs"
    payload = _manifest_payload(prepared.instance_root)
    payload["environment_root"] = str(outside_environment_root)
    payload["instance_root"] = str(outside_environment_root / "previews" / "feature")
    payload["db_path"] = str(
        outside_environment_root / "previews" / "feature" / "data" / "planner.db"
    )
    payload["managed_files_root"] = str(
        outside_environment_root / "previews" / "feature" / "data" / "files"
    )
    payload["hermes_home"] = str(
        outside_environment_root / "previews" / "feature" / "hermes-home"
    )
    payload["logs_dir"] = str(outside_environment_root / "previews" / "feature" / "logs")
    payload["dispatcher_lock_path"] = str(
        outside_environment_root / "previews" / "feature" / "run" / "dispatcher.lock"
    )
    payload["server_control_socket_path"] = str(
        outside_environment_root / "previews" / "feature" / "run" / "server-control.sock"
    )
    _write_manifest_payload(prepared.instance_root, payload)

    with pytest.raises(EnvironmentValidationError, match="manifest"):
        materialize.reset_environment_instance(
            kind="preview",
            instance_id="feature",
            environment_root=environment_root,
            repository_roots=(),
        )

    assert not outside_environment_root.exists()
    assert prepared.instance_root.exists()


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("ANTHROPIC_API_KEY", "malformed"),
        ("ANTHROPIC_API_KEY=one\nANTHROPIC_API_KEY=two", "duplicate"),
        ("STRIPE_SECRET_KEY=secret", "unknown"),
        ("PLAN_DB_PATH=/tmp/poison.db", "forbidden"),
    ],
)
def test_prepare_rejects_existing_invalid_credential_file_before_lock_or_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
    message: str,
) -> None:
    repository_root = _repository_root()
    environment_root = _short_environment_root(tmp_path)
    credentials_env_file = tmp_path / "credentials.env"
    credentials_env_file.write_text(body, encoding="utf-8")

    def fail_if_lock_is_acquired(_environment_root: Path) -> object:
        raise AssertionError("credential validation must happen before registry lock")

    monkeypatch.setattr(
        materialize,
        "acquire_environment_registry_lock",
        fail_if_lock_is_acquired,
    )

    with pytest.raises(EnvironmentValidationError, match=message):
        materialize.prepare_environment_instance(
            kind="preview",
            instance_id="feature",
            environment_root=environment_root,
            port=9124,
            credentials_env_file=credentials_env_file,
            repository_roots=(repository_root,),
        )

    assert not (environment_root / "previews" / "feature").exists()


def test_exact_reprepare_revalidates_existing_credential_file_before_returning(
    tmp_path: Path,
) -> None:
    repository_root = _repository_root()
    environment_root = _short_environment_root(tmp_path)
    credentials_env_file = tmp_path / "credentials.env"
    credentials_env_file.write_text("ANTHROPIC_API_KEY=first-secret", encoding="utf-8")
    prepared = materialize.prepare_environment_instance(
        kind="preview",
        instance_id="feature",
        environment_root=environment_root,
        port=9125,
        credentials_env_file=credentials_env_file,
        repository_roots=(repository_root,),
    )
    original_manifest_payload = _manifest_payload(prepared.instance_root)
    original_database_hash = _sha256(prepared.db_path)
    credentials_env_file.write_text(
        "ANTHROPIC_API_KEY=one\nANTHROPIC_API_KEY=two",
        encoding="utf-8",
    )

    with pytest.raises(EnvironmentValidationError, match="duplicate"):
        materialize.prepare_environment_instance(
            kind="preview",
            instance_id="feature",
            environment_root=environment_root,
            port=9125,
            credentials_env_file=credentials_env_file,
            repository_roots=(repository_root,),
        )

    assert _manifest_payload(prepared.instance_root) == original_manifest_payload
    assert _sha256(prepared.db_path) == original_database_hash


def test_prepare_rejects_repository_root_shared_with_prepared_instance(
    tmp_path: Path,
) -> None:
    repository_root = _repository_root()
    environment_root = _short_environment_root(tmp_path)
    materialize.prepare_environment_instance(
        kind="staging",
        environment_root=environment_root,
        repository_roots=(repository_root,),
    )

    with pytest.raises(EnvironmentValidationError, match="repository_roots"):
        materialize.prepare_environment_instance(
            kind="preview",
            instance_id="feature",
            environment_root=environment_root,
            port=9127,
            repository_roots=(repository_root / ".." / repository_root.name,),
        )

    assert not (environment_root / "previews" / "feature").exists()


@pytest.mark.parametrize(
    ("kind", "instance_id", "credentials_relative_path"),
    [
        ("staging", None, "live/staging.env"),
        ("preview", "feature", "live/previews/feature.env"),
    ],
)
def test_prepare_rejects_nonproduction_credential_reference_under_live_before_live_exists(
    tmp_path: Path,
    kind: str,
    instance_id: str | None,
    credentials_relative_path: str,
) -> None:
    repository_root = _repository_root()
    environment_root = _short_environment_root(tmp_path)
    credentials_env_file = environment_root / credentials_relative_path

    with pytest.raises(EnvironmentValidationError, match="live"):
        materialize.prepare_environment_instance(
            kind=kind,  # type: ignore[arg-type]
            instance_id=instance_id,
            environment_root=environment_root,
            port=9126 if kind == "preview" else None,
            credentials_env_file=credentials_env_file,
            repository_roots=(repository_root,),
        )

    assert not (environment_root / "live").exists()
    assert not (environment_root / "staging").exists()
    assert not (environment_root / "previews" / "feature").exists()


def test_prepare_is_exactly_idempotent_for_existing_manifest(tmp_path: Path) -> None:
    repository_root = _repository_root()
    environment_root = _short_environment_root(tmp_path)

    first = materialize.prepare_environment_instance(
        kind="staging",
        environment_root=environment_root,
        repository_roots=(repository_root,),
        now=1_800_000_010,
    )
    first_manifest_payload = _manifest_payload(first.instance_root)
    first_database_hash = _sha256(first.db_path)

    second = materialize.prepare_environment_instance(
        kind="staging",
        environment_root=environment_root,
        repository_roots=(repository_root,),
        now=1_800_000_099,
    )

    assert second == first
    assert _manifest_payload(first.instance_root) == first_manifest_payload
    assert _sha256(first.db_path) == first_database_hash


def test_live_prepare_defaults_prepared_at_to_current_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = _repository_root()
    environment_root = _short_environment_root(tmp_path)
    monkeypatch.setattr(materialize.time, "time", lambda: 1_923_456_789.9)

    live = materialize.prepare_environment_instance(
        kind="live",
        environment_root=environment_root,
        repository_roots=(repository_root,),
    )

    assert live.prepared_at == 1_923_456_789
    assert live.prepared_at != 1_800_000_000


def test_mismatched_reprepare_fails_without_mutating_existing_instance(
    tmp_path: Path,
) -> None:
    repository_root = _repository_root()
    environment_root = _short_environment_root(tmp_path)
    env_file = tmp_path / "preview.env"
    env_file.write_text("ANTHROPIC_API_KEY=first-secret", encoding="utf-8")
    preview = materialize.prepare_environment_instance(
        kind="preview",
        instance_id="feature-one",
        environment_root=environment_root,
        port=9130,
        credentials_env_file=env_file,
        repository_roots=(repository_root,),
    )
    original_manifest_payload = _manifest_payload(preview.instance_root)
    original_database_hash = _sha256(preview.db_path)

    with pytest.raises(EnvironmentValidationError):
        materialize.prepare_environment_instance(
            kind="preview",
            instance_id="feature-one",
            environment_root=environment_root,
            port=9131,
            credentials_env_file=env_file,
            repository_roots=(repository_root,),
        )

    assert _manifest_payload(preview.instance_root) == original_manifest_payload
    assert _sha256(preview.db_path) == original_database_hash


def test_concurrent_preview_prepares_allocate_distinct_durable_ports(
    tmp_path: Path,
) -> None:
    environment_root = _short_environment_root(tmp_path)
    defaults = EnvironmentDefaults(preview_ports=EnvironmentPortRange(9140, 9145))
    repository_roots = {
        preview_id: _repository_root(tmp_path, f"{preview_id}-repo")
        for preview_id in ("one", "two", "three", "four")
    }

    def prepare_preview(preview_id: str) -> int:
        manifest = materialize.prepare_environment_instance(
            kind="preview",
            instance_id=preview_id,
            environment_root=environment_root,
            repository_roots=(repository_roots[preview_id],),
            defaults=defaults,
        )
        return manifest.port

    with ThreadPoolExecutor(max_workers=4) as executor:
        ports = tuple(executor.map(prepare_preview, ("one", "two", "three", "four")))

    assert len(set(ports)) == 4
    assert set(ports).issubset(set(range(9140, 9146)))
    for preview_id, port in zip(("one", "two", "three", "four"), ports, strict=True):
        inspected = materialize.inspect_environment_instance(
            kind="preview",
            instance_id=preview_id,
            environment_root=environment_root,
            repository_roots=(repository_roots[preview_id],),
        )
        assert inspected.port == port


def test_prepare_validates_existing_registry_manifests_before_writing(
    tmp_path: Path,
) -> None:
    live_repository_root = _repository_root(tmp_path, "live-repo")
    staging_repository_root = _repository_root(tmp_path, "staging-repo")
    preview_repository_root = _repository_root(tmp_path, "preview-repo")
    environment_root = _short_environment_root(tmp_path)
    live = materialize.prepare_environment_instance(
        kind="live",
        environment_root=environment_root,
        repository_roots=(live_repository_root,),
    )
    staging = materialize.prepare_environment_instance(
        kind="staging",
        environment_root=environment_root,
        repository_roots=(staging_repository_root,),
    )
    staging_manifest_path = staging.instance_root / "manifest.json"
    staging_payload = json.loads(staging_manifest_path.read_text(encoding="utf-8"))
    staging_payload["port"] = live.port
    staging_manifest_path.write_text(
        json.dumps(staging_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(EnvironmentValidationError):
        materialize.prepare_environment_instance(
            kind="preview",
            instance_id="feature",
            environment_root=environment_root,
            repository_roots=(preview_repository_root,),
            defaults=EnvironmentDefaults(preview_ports=EnvironmentPortRange(9150, 9151)),
        )

    assert not (environment_root / "previews" / "feature").exists()


@pytest.mark.parametrize("port", [-1, 0, 65536])
def test_inspect_rejects_manifest_ports_outside_tcp_boundaries(
    tmp_path: Path,
    port: int,
) -> None:
    repository_root = _repository_root()
    environment_root = _short_environment_root(tmp_path)
    prepared = materialize.prepare_environment_instance(
        kind="preview",
        instance_id="feature",
        environment_root=environment_root,
        port=9126,
        repository_roots=(repository_root,),
    )
    payload = _manifest_payload(prepared.instance_root)
    payload["port"] = port
    _write_manifest_payload(prepared.instance_root, payload)

    with pytest.raises(EnvironmentValidationError, match="port"):
        materialize.inspect_environment_instance(
            kind="preview",
            instance_id="feature",
            environment_root=environment_root,
            repository_roots=(),
        )


def test_reset_and_remove_fail_while_server_lifecycle_lease_is_held(
    tmp_path: Path,
) -> None:
    repository_root = _repository_root()
    environment_root = _short_environment_root(tmp_path)
    staging = materialize.prepare_environment_instance(
        kind="staging",
        environment_root=environment_root,
        repository_roots=(repository_root,),
    )
    lease = PortScopedServerLifecycleLease(
        resolve_server_lifecycle_lease_path(staging.port),
        staging.port,
    )
    lease.acquire()
    try:
        with pytest.raises(EnvironmentValidationError):
            materialize.reset_environment_instance(
                kind="staging",
                environment_root=environment_root,
                repository_roots=(repository_root,),
            )
        with pytest.raises(EnvironmentValidationError):
            materialize.remove_environment_instance(
                kind="staging",
                environment_root=environment_root,
                repository_roots=(repository_root,),
            )
    finally:
        lease.release()

    assert staging.instance_root.exists()


def test_reset_holds_lifecycle_lease_until_failed_fixture_cleanup_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = _repository_root()
    environment_root = _short_environment_root(tmp_path)
    staging = materialize.prepare_environment_instance(
        kind="staging",
        environment_root=environment_root,
        repository_roots=(repository_root,),
    )
    marker = staging.db_path.parent / "marker.txt"
    marker.write_text("old data", encoding="utf-8")
    observed_lease_block = False

    def fail_after_proving_lease_is_held(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal observed_lease_block
        probe = PortScopedServerLifecycleLease(
            resolve_server_lifecycle_lease_path(staging.port),
            staging.port,
        )
        with pytest.raises(ServerLifecycleAlreadyOwnedError):
            probe.acquire()
        observed_lease_block = True
        raise RuntimeError("fixture failed")

    monkeypatch.setattr(
        materialize,
        "build_fake_environment_database",
        fail_after_proving_lease_is_held,
    )

    with pytest.raises(RuntimeError, match="fixture failed"):
        materialize.reset_environment_instance(
            kind="staging",
            environment_root=environment_root,
            repository_roots=(repository_root,),
        )

    assert observed_lease_block is True
    assert marker.read_text(encoding="utf-8") == "old data"
    _assert_port_lease_is_free(staging.port)


def test_remove_refuses_live_and_removes_only_selected_instance(
    tmp_path: Path,
) -> None:
    live_repository_root = _repository_root(tmp_path, "live-repo")
    first_repository_root = _repository_root(tmp_path, "first-repo")
    second_repository_root = _repository_root(tmp_path, "second-repo")
    replacement_repository_root = _repository_root(tmp_path, "replacement-repo")
    environment_root = _short_environment_root(tmp_path)
    defaults = EnvironmentDefaults(preview_ports=EnvironmentPortRange(9160, 9161))
    live = materialize.prepare_environment_instance(
        kind="live",
        environment_root=environment_root,
        repository_roots=(live_repository_root,),
    )
    first = materialize.prepare_environment_instance(
        kind="preview",
        instance_id="first",
        environment_root=environment_root,
        repository_roots=(first_repository_root,),
        defaults=defaults,
    )
    second = materialize.prepare_environment_instance(
        kind="preview",
        instance_id="second",
        environment_root=environment_root,
        repository_roots=(second_repository_root,),
        defaults=defaults,
    )

    with pytest.raises(EnvironmentValidationError):
        materialize.remove_environment_instance(
            kind="live",
            environment_root=environment_root,
            repository_roots=(live_repository_root,),
        )

    removed = materialize.remove_environment_instance(
        kind="preview",
        instance_id="first",
        environment_root=environment_root,
        repository_roots=(first_repository_root,),
    )
    replacement = materialize.prepare_environment_instance(
        kind="preview",
        instance_id="third",
        environment_root=environment_root,
        repository_roots=(replacement_repository_root,),
        defaults=defaults,
    )

    assert removed.instance_root == first.instance_root
    assert not first.instance_root.exists()
    assert second.instance_root.exists()
    assert live.instance_root.exists()
    assert replacement.port == first.port


def test_remove_holds_lifecycle_lease_until_failed_cleanup_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = _repository_root()
    environment_root = _short_environment_root(tmp_path)
    preview = materialize.prepare_environment_instance(
        kind="preview",
        instance_id="feature",
        environment_root=environment_root,
        port=9170,
        repository_roots=(repository_root,),
    )
    observed_lease_block = False

    def fail_after_proving_lease_is_held(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal observed_lease_block
        if Path(path) == preview.instance_root:
            probe = PortScopedServerLifecycleLease(
                resolve_server_lifecycle_lease_path(preview.port),
                preview.port,
            )
            with pytest.raises(ServerLifecycleAlreadyOwnedError):
                probe.acquire()
            observed_lease_block = True
            raise RuntimeError("cleanup failed")
        return original_rmtree(path, *args, **kwargs)

    original_rmtree = materialize.shutil.rmtree
    monkeypatch.setattr(materialize.shutil, "rmtree", fail_after_proving_lease_is_held)

    with pytest.raises(RuntimeError, match="cleanup failed"):
        materialize.remove_environment_instance(
            kind="preview",
            instance_id="feature",
            environment_root=environment_root,
            repository_roots=(repository_root,),
        )

    assert observed_lease_block is True
    assert preview.instance_root.exists()
    _assert_port_lease_is_free(preview.port)


def _manifest_payload(instance_root: Path) -> dict[str, object]:
    return json.loads((instance_root / "manifest.json").read_text(encoding="utf-8"))


def _write_manifest_payload(instance_root: Path, payload: dict[str, object]) -> None:
    (instance_root / "manifest.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _repository_root(tmp_path: Path | None = None, name: str = "repo") -> Path:
    if tmp_path is None:
        return Path(__file__).resolve().parents[2]
    repository_root = tmp_path / name
    repository_root.mkdir()
    (repository_root / ".git").mkdir()
    return repository_root


def _short_environment_root(tmp_path: Path) -> Path:
    digest = hashlib.sha1(str(tmp_path).encode("utf-8")).hexdigest()[:8]
    environment_root = Path("/tmp") / f"pe-{os.getpid()}-{digest}"
    if environment_root.exists():
        shutil.rmtree(environment_root)
    return environment_root


def _assert_port_lease_is_free(port: int) -> None:
    lease = PortScopedServerLifecycleLease(resolve_server_lifecycle_lease_path(port), port)
    lease.acquire()
    lease.release()

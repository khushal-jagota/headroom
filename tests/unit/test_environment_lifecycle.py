from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
from pathlib import Path

import pytest

from planner.environments import materialize
from planner.environments.contracts import EnvironmentValidationError
from planner.server_lifecycle.control import resolve_server_lifecycle_lease_path
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


def test_staging_reset_replaces_fake_state_but_preserves_prepared_identity(
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
    identity_marker = prepared.runtime_user_home / ".codex" / "identity.json"
    identity_marker.parent.mkdir(parents=True)
    identity_marker.write_text("persistent", encoding="utf-8")

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
    assert identity_marker.read_text(encoding="utf-8") == "persistent"
    assert reset.runtime_user_home.stat().st_mode & 0o777 == 0o700


def test_staging_prepare_does_not_materialize_a_panels_managed_hermes_home(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "staging-repo")
    unrelated_ticket_checkout = tmp_path / "unrelated-ticket-checkout"
    unrelated_ticket_checkout.mkdir()
    prepared = materialize.prepare_environment_instance(
        kind="staging",
        environment_root=_environment_root(tmp_path),
        repository_roots=(repository,),
    )

    assert not prepared.hermes_home.exists()

    shutil.rmtree(unrelated_ticket_checkout)
    assert not prepared.hermes_home.exists()


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


def test_live_import_copies_committed_wal_files_settings_and_hermes(tmp_path: Path) -> None:
    repository = _repository(tmp_path, "live-repo")
    root = _environment_root(tmp_path)
    prepared = materialize.prepare_environment_instance(
        kind="live",
        environment_root=root,
        port=_test_port(tmp_path),
        repository_roots=(repository,),
        now=123,
    )
    prepared.db_path.parent.mkdir(parents=True, exist_ok=True)
    prepared.db_path.write_bytes(b"old-db")
    prepared.managed_files_root.mkdir(parents=True, exist_ok=True)
    (prepared.managed_files_root / "old.txt").write_text("old", encoding="utf-8")

    source_db, source_files, source_hermes, source_user_home, source_logs = _live_sources(tmp_path)
    connection = sqlite3.connect(source_db)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE imported (value TEXT NOT NULL)")
    connection.execute("INSERT INTO imported VALUES ('from-wal')")
    connection.commit()
    assert source_db.with_name(source_db.name + "-wal").exists()
    source_socket = source_user_home / ".codex" / "ipc.sock"
    source_link = source_user_home / ".codex" / "auth-link.json"
    source_link.symlink_to("auth.json")
    source_session_link = source_hermes / "sessions" / "current.json"
    source_session_link.symlink_to("session.json")
    imported_panels_skill = source_hermes / "skills" / "panels"
    imported_panels_skill.parent.mkdir()
    imported_panels_skill.symlink_to(
        source_user_home / "obsolete-checkout" / "src" / "planner" / "skills" / "panels"
    )
    ipc_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    ipc_listener.bind(str(source_socket))
    try:
        imported = materialize.import_live_environment_state(
            environment_root=root,
            source_db_path=source_db,
            source_managed_files_root=source_files,
            source_hermes_home=source_hermes,
            source_runtime_user_home=source_user_home,
            source_logs_root=source_logs,
        )
    finally:
        ipc_listener.close()
        connection.close()

    with sqlite3.connect(imported.db_path) as copied:
        assert copied.execute("SELECT value FROM imported").fetchone() == ("from-wal",)
    assert (imported.managed_files_root / "file.txt").read_text() == "managed"
    assert (imported.db_path.parent / "worker-settings" / "settings.json").read_text() == "{}"
    assert (imported.hermes_home / "sessions" / "session.json").read_text() == "session"
    assert (imported.hermes_home / "sessions" / "current.json").is_symlink()
    assert (imported.hermes_home / "sessions" / "current.json").read_text() == "session"
    assert (imported.hermes_home / "skills" / "panels").resolve() == (
        imported.db_path.parent / "skills" / "panels"
    ).resolve()
    assert (imported.runtime_user_home / ".codex" / "auth.json").read_text() == "codex"
    assert (imported.runtime_user_home / ".codex" / "config.toml").read_text() == "model='x'"
    assert (
        imported.runtime_user_home / ".codex" / "sessions" / "session.jsonl"
    ).read_text() == "codex-session"
    assert not (imported.runtime_user_home / ".codex" / "ipc.sock").exists()
    assert (imported.runtime_user_home / ".codex" / "auth-link.json").is_symlink()
    assert (imported.runtime_user_home / ".claude" / "session.json").read_text() == "claude"
    assert (imported.runtime_user_home / ".claude" / "settings.json").read_text() == "{}"
    assert (
        imported.runtime_user_home / ".claude" / "projects" / "conversation.jsonl"
    ).read_text() == "claude-session"
    assert (
        imported.logs_dir.parent / "archive" / "pre-cutover" / "panels.log"
    ).read_text() == "old log"
    assert imported.runtime_user_home.stat().st_mode & 0o777 == 0o700
    assert (imported.runtime_user_home / ".codex").stat().st_mode & 0o777 == 0o700
    assert (imported.runtime_user_home / ".codex" / "auth.json").stat().st_mode & 0o777 == 0o600
    assert (imported.runtime_user_home / ".codex" / "config.toml").stat().st_mode & 0o777 == 0o600
    assert (
        imported.runtime_user_home / ".claude" / "settings.json"
    ).stat().st_mode & 0o777 == 0o600
    assert not (imported.managed_files_root / "old.txt").exists()


def test_live_import_refuses_running_live(tmp_path: Path) -> None:
    repository = _repository(tmp_path, "live-repo")
    root = _environment_root(tmp_path)
    port = _test_port(tmp_path)
    materialize.prepare_environment_instance(
        kind="live",
        environment_root=root,
        port=port,
        repository_roots=(repository,),
    )
    source_db, source_files, source_hermes, source_user_home, source_logs = _live_sources(tmp_path)
    sqlite3.connect(source_db).close()
    lease = PortScopedServerLifecycleLease(resolve_server_lifecycle_lease_path(port), port)
    lease.acquire()
    try:
        with pytest.raises(EnvironmentValidationError, match="is running"):
            materialize.import_live_environment_state(
                environment_root=root,
                source_db_path=source_db,
                source_managed_files_root=source_files,
                source_hermes_home=source_hermes,
                source_runtime_user_home=source_user_home,
                source_logs_root=source_logs,
            )
    finally:
        lease.release()


def test_live_import_copy_failure_leaves_existing_state_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path, "live-repo")
    root = _environment_root(tmp_path)
    prepared = materialize.prepare_environment_instance(
        kind="live",
        environment_root=root,
        port=_test_port(tmp_path),
        repository_roots=(repository,),
    )
    prepared.db_path.parent.mkdir(parents=True, exist_ok=True)
    prepared.db_path.write_bytes(b"unchanged")
    prepared.managed_files_root.mkdir(parents=True, exist_ok=True)
    (prepared.managed_files_root / "old.txt").write_text("unchanged", encoding="utf-8")
    source_db, source_files, source_hermes, source_user_home, source_logs = _live_sources(tmp_path)
    sqlite3.connect(source_db).close()

    def fail_copytree(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected copy failure")

    monkeypatch.setattr(materialize.shutil, "copytree", fail_copytree)
    with pytest.raises(EnvironmentValidationError, match="injected copy failure"):
        materialize.import_live_environment_state(
            environment_root=root,
            source_db_path=source_db,
            source_managed_files_root=source_files,
            source_hermes_home=source_hermes,
            source_runtime_user_home=source_user_home,
            source_logs_root=source_logs,
        )

    assert prepared.db_path.read_bytes() == b"unchanged"
    assert (prepared.managed_files_root / "old.txt").read_text() == "unchanged"


def test_live_import_pointer_failure_keeps_the_complete_previous_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path, "live-repo")
    root = _environment_root(tmp_path)
    prepared = materialize.prepare_environment_instance(
        kind="live",
        environment_root=root,
        port=_test_port(tmp_path),
        repository_roots=(repository,),
    )
    prepared.db_path.write_bytes(b"old-db")
    prepared.hermes_home.mkdir(parents=True, exist_ok=True)
    (prepared.hermes_home / "old-session").write_text("old", encoding="utf-8")
    (prepared.runtime_user_home / "old-identity").write_text("old", encoding="utf-8")
    (prepared.logs_dir / "old.log").write_text("old", encoding="utf-8")
    old_generation = (prepared.instance_root / "current").resolve()
    source_db, source_files, source_hermes, source_user_home, source_logs = _live_sources(tmp_path)
    sqlite3.connect(source_db).close()
    real_replace = materialize.os.replace

    def fail_pointer_replace(source: object, destination: object) -> None:
        if Path(destination) == prepared.instance_root / "current":
            raise OSError("injected pointer failure")
        real_replace(source, destination)

    monkeypatch.setattr(materialize.os, "replace", fail_pointer_replace)
    with pytest.raises(EnvironmentValidationError, match="injected pointer failure"):
        materialize.import_live_environment_state(
            environment_root=root,
            source_db_path=source_db,
            source_managed_files_root=source_files,
            source_hermes_home=source_hermes,
            source_runtime_user_home=source_user_home,
            source_logs_root=source_logs,
        )

    assert (prepared.instance_root / "current").resolve() == old_generation
    assert prepared.db_path.read_bytes() == b"old-db"
    assert (prepared.hermes_home / "old-session").read_text() == "old"
    assert (prepared.runtime_user_home / "old-identity").read_text() == "old"
    assert (prepared.logs_dir / "old.log").read_text() == "old"


def test_live_import_commits_all_state_as_one_generation(tmp_path: Path) -> None:
    repository = _repository(tmp_path, "live-repo")
    root = _environment_root(tmp_path)
    materialize.prepare_environment_instance(
        kind="live",
        environment_root=root,
        port=_test_port(tmp_path),
        repository_roots=(repository,),
    )
    source_db, source_files, source_hermes, source_user_home, source_logs = _live_sources(tmp_path)
    sqlite3.connect(source_db).close()

    imported = materialize.import_live_environment_state(
        environment_root=root,
        source_db_path=source_db,
        source_managed_files_root=source_files,
        source_hermes_home=source_hermes,
        source_runtime_user_home=source_user_home,
        source_logs_root=source_logs,
    )

    current = imported.instance_root / "current"
    generation = current.resolve()
    assert current.is_symlink()
    for durable_path in (
        imported.db_path,
        imported.managed_files_root,
        imported.hermes_home,
        imported.runtime_user_home,
        imported.logs_dir,
    ):
        assert generation in durable_path.resolve().parents


def test_live_import_cleanup_failure_after_pointer_commit_is_nonfatal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path, "live-repo")
    root = _environment_root(tmp_path)
    prepared = materialize.prepare_environment_instance(
        kind="live",
        environment_root=root,
        port=_test_port(tmp_path),
        repository_roots=(repository,),
    )
    old_generation = (prepared.instance_root / "current").resolve()
    source_db, source_files, source_hermes, source_user_home, source_logs = _live_sources(
        tmp_path
    )
    sqlite3.connect(source_db).close()
    real_rmtree = materialize.shutil.rmtree

    def fail_old_generation_cleanup(path: object, *args: object, **kwargs: object) -> None:
        if Path(path) == old_generation:
            raise OSError("injected cleanup failure")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(materialize.shutil, "rmtree", fail_old_generation_cleanup)
    imported = materialize.import_live_environment_state(
        environment_root=root,
        source_db_path=source_db,
        source_managed_files_root=source_files,
        source_hermes_home=source_hermes,
        source_runtime_user_home=source_user_home,
        source_logs_root=source_logs,
    )

    assert (imported.instance_root / "current").resolve() != old_generation
    assert (imported.hermes_home / "sessions" / "session.json").read_text() == "session"
    assert old_generation.exists()


def test_live_import_rejects_sources_inside_live_root(tmp_path: Path) -> None:
    repository = _repository(tmp_path, "live-repo")
    root = _environment_root(tmp_path)
    prepared = materialize.prepare_environment_instance(
        kind="live",
        environment_root=root,
        port=_test_port(tmp_path),
        repository_roots=(repository,),
    )
    prepared.db_path.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(prepared.db_path).close()
    prepared.managed_files_root.mkdir(parents=True, exist_ok=True)
    prepared.hermes_home.mkdir(parents=True, exist_ok=True)
    (prepared.db_path.parent / "worker-settings").mkdir(exist_ok=True)
    (prepared.runtime_user_home / ".codex").mkdir()
    (prepared.runtime_user_home / ".claude").mkdir()

    with pytest.raises(EnvironmentValidationError, match="outside"):
        materialize.import_live_environment_state(
            environment_root=root,
            source_db_path=prepared.db_path,
            source_managed_files_root=prepared.managed_files_root,
            source_hermes_home=prepared.hermes_home,
            source_runtime_user_home=prepared.runtime_user_home,
            source_logs_root=prepared.logs_dir,
        )


@pytest.mark.parametrize(
    "field",
    (
        "environment_root",
        "instance_root",
        "db_path",
        "managed_files_root",
        "hermes_home",
        "runtime_user_home",
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


def test_reprepare_rejects_live_port_or_repository_change(tmp_path: Path) -> None:
    first_repository = _repository(tmp_path, "live-repo")
    second_repository = _repository(tmp_path, "other-repo")
    root = _environment_root(tmp_path)
    port = _test_port(tmp_path)
    materialize.prepare_environment_instance(
        kind="live",
        environment_root=root,
        port=port,
        repository_roots=(first_repository,),
    )
    with pytest.raises(EnvironmentValidationError, match="port"):
        materialize.prepare_environment_instance(
            kind="live",
            environment_root=root,
            port=port + 1,
            repository_roots=(first_repository,),
        )
    with pytest.raises(EnvironmentValidationError, match="repository"):
        materialize.prepare_environment_instance(
            kind="live",
            environment_root=root,
            port=port,
            repository_roots=(second_repository,),
        )


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
    root = _environment_root(tmp_path)
    live_repository = _repository(tmp_path, "live-repo")
    staging_repository = _repository(tmp_path, "staging-repo")
    materialize.prepare_environment_instance(
        kind="live",
        environment_root=root,
        port=_test_port(tmp_path),
        repository_roots=(live_repository,),
    )
    materialize.prepare_environment_instance(
        kind="staging",
        environment_root=root,
        repository_roots=(staging_repository,),
    )
    live_manifest = root / "live" / "manifest.json"
    real_read_manifest = materialize._read_manifest

    def guarded_read_manifest(path: Path, **kwargs: object) -> object:
        if path == live_manifest:
            raise AssertionError("live manifest was opened")
        return real_read_manifest(path, **kwargs)

    monkeypatch.setattr(materialize, "_read_manifest", guarded_read_manifest)
    materialize.inspect_environment_instance(
        kind="staging", environment_root=root, repository_roots=()
    )
    materialize.reset_environment_instance(
        kind="staging", environment_root=root, repository_roots=()
    )
    materialize.remove_environment_instance(
        kind="staging", environment_root=root, repository_roots=()
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


def test_sqlite_backup_handles_uri_metacharacters_in_source_path(tmp_path: Path) -> None:
    source = tmp_path / "source?#.db"
    destination = tmp_path / "destination.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample VALUES ('copied')")
    materialize._sqlite_backup(source, destination)
    with sqlite3.connect(destination) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone() == ("copied",)


def test_live_import_rejects_nested_source_directories(tmp_path: Path) -> None:
    repository = _repository(tmp_path, "live-repo")
    root = _environment_root(tmp_path)
    materialize.prepare_environment_instance(
        kind="live",
        environment_root=root,
        port=_test_port(tmp_path),
        repository_roots=(repository,),
    )
    source_db, source_files, _source_hermes, source_user_home, source_logs = _live_sources(
        tmp_path
    )
    sqlite3.connect(source_db).close()
    nested_hermes = source_files / "hermes"
    nested_hermes.mkdir()
    with pytest.raises(EnvironmentValidationError, match="overlapping paths"):
        materialize.import_live_environment_state(
            environment_root=root,
            source_db_path=source_db,
            source_managed_files_root=source_files,
            source_hermes_home=nested_hermes,
            source_runtime_user_home=source_user_home,
            source_logs_root=source_logs,
        )


def _live_sources(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    digest = hashlib.sha1(str(tmp_path).encode("utf-8")).hexdigest()[:8]
    source_user_home = Path("/tmp") / f"pe-source-{os.getpid()}-{digest}"
    shutil.rmtree(source_user_home, ignore_errors=True)
    source_runtime_root = source_user_home / ".hermes" / "planning-v2"
    source_data = source_runtime_root / "data"
    source_data.mkdir(parents=True)
    source_db = source_data / "planner.db"
    worker_settings = source_data / "worker-settings"
    worker_settings.mkdir()
    (worker_settings / "settings.json").write_text("{}", encoding="utf-8")
    source_files = source_data / "files"
    source_files.mkdir()
    (source_files / "file.txt").write_text("managed", encoding="utf-8")
    source_hermes = source_data / "hermes-home"
    (source_hermes / "sessions").mkdir(parents=True)
    (source_hermes / "sessions" / "session.json").write_text("session", encoding="utf-8")
    (source_user_home / ".codex").mkdir(parents=True)
    (source_user_home / ".codex" / "auth.json").write_text("codex", encoding="utf-8")
    (source_user_home / ".codex" / "config.toml").write_text("model='x'", encoding="utf-8")
    (source_user_home / ".codex" / "sessions").mkdir()
    (source_user_home / ".codex" / "sessions" / "session.jsonl").write_text(
        "codex-session", encoding="utf-8"
    )
    (source_user_home / ".claude").mkdir()
    (source_user_home / ".claude" / "session.json").write_text("claude", encoding="utf-8")
    (source_user_home / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    (source_user_home / ".claude" / "projects").mkdir()
    (source_user_home / ".claude" / "projects" / "conversation.jsonl").write_text(
        "claude-session", encoding="utf-8"
    )
    source_user_home.chmod(0o700)
    (source_user_home / ".codex").chmod(0o700)
    (source_user_home / ".claude").chmod(0o700)
    (source_user_home / ".codex" / "auth.json").chmod(0o600)
    (source_user_home / ".codex" / "config.toml").chmod(0o600)
    (source_user_home / ".claude" / "settings.json").chmod(0o600)
    source_logs = source_data / "logs"
    source_logs.mkdir()
    (source_logs / "panels.log").write_text("old log", encoding="utf-8")
    return source_db, source_files, source_hermes, source_user_home, source_logs


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

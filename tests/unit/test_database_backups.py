from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from planner.core.db import connect, create_schema
from planner.environments.backup import (
    _is_verified_snapshot,
    create_database_backup,
    restore_database_snapshot,
)
from planner.environments.cli import environment


@pytest.fixture(autouse=True)
def _default_skills_home(monkeypatch: pytest.MonkeyPatch) -> None:
    # Resolve the skills home from the database directory unless a test sets it.
    monkeypatch.delenv("PLAN_HERMES_HOME", raising=False)


def _seed_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
        connection.execute("INSERT INTO records VALUES ('canonical')")


def _seed_managed_tree(data_dir: Path, marker: str) -> None:
    """Create the managed-file roots beside a database, tagged with a marker."""
    ticket = data_dir / "files" / "tickets" / "t_1" / "artifacts"
    ticket.mkdir(parents=True)
    (ticket / "ui.html").write_text(f"<h1>{marker}</h1>")
    settings = data_dir / "worker-settings" / "coding"
    settings.mkdir(parents=True)
    (settings / "settings.json").write_text(f'{{"marker": "{marker}"}}')
    skills = data_dir / "hermes-home" / "skills" / "panels"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(f"# {marker}")


def test_backup_uses_online_snapshot_and_records_integrity_metadata(tmp_path: Path) -> None:
    source = tmp_path / "planning.db"
    backup_dir = tmp_path / "backups"
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
        connection.execute("INSERT INTO records VALUES ('committed in WAL')")
        connection.commit()

    snapshot = create_database_backup(
        source_db=source,
        backup_dir=backup_dir,
        deployed_revision="abc123",
    )

    assert snapshot.parent == backup_dir
    metadata = json.loads((snapshot / "metadata.json").read_text())
    assert metadata["deployed_revision"] == "abc123"
    assert metadata["verified"] is True
    with sqlite3.connect(snapshot / "database.sqlite") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT value FROM records").fetchone()[0] == "committed in WAL"


def test_failed_integrity_does_not_delete_existing_verified_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "planning.db"
    backup_dir = tmp_path / "backups"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
        connection.execute("INSERT INTO records VALUES ('old')")
    old_snapshot = create_database_backup(source, backup_dir, "old-revision")

    monkeypatch.setattr(
        "planner.environments.backup._verify_database",
        lambda _: (_ for _ in ()).throw(RuntimeError("corrupt")),
    )
    with pytest.raises(RuntimeError, match="corrupt"):
        create_database_backup(source, backup_dir, "new-revision")

    assert old_snapshot.exists()
    assert len(tuple(backup_dir.iterdir())) == 1


def test_backup_publish_failure_leaves_existing_snapshot_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "planning.db"
    backup_dir = tmp_path / "backups"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
        connection.execute("INSERT INTO records VALUES ('old')")
    old_snapshot = create_database_backup(source, backup_dir, "old-revision")

    real_replace = __import__("os").replace

    def fail_publish(source_path: str | Path, destination_path: str | Path) -> None:
        if Path(source_path).name.startswith(".backup-"):
            raise OSError("publish failed")
        real_replace(source_path, destination_path)

    monkeypatch.setattr("planner.environments.backup.os.replace", fail_publish)
    with pytest.raises(OSError, match="publish failed"):
        create_database_backup(source, backup_dir, "new-revision")

    assert old_snapshot.exists()
    assert sorted(path.name for path in backup_dir.glob("snapshot-*")) == [old_snapshot.name]


def test_retention_keeps_three_verified_snapshots(tmp_path: Path) -> None:
    source = tmp_path / "planning.db"
    backup_dir = tmp_path / "backups"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
    for revision in range(4):
        create_database_backup(source, backup_dir, str(revision))

    snapshots = sorted(backup_dir.glob("snapshot-*"))
    assert len(snapshots) == 3
    assert all((snapshot / "metadata.json").exists() for snapshot in snapshots)


def test_retention_keeps_newest_by_creation_metadata(tmp_path: Path) -> None:
    source = tmp_path / "planning.db"
    backup_dir = tmp_path / "backups"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
    snapshots = [create_database_backup(source, backup_dir, str(revision)) for revision in range(3)]
    # Insertion order deliberately differs from chronological order: the second
    # snapshot is the oldest by created_at, so it -- not the first -- is trimmed.
    created_ats = {
        0: "2030-01-01T00:00:00+00:00",
        1: "2020-01-01T00:00:00+00:00",
        2: "2025-01-01T00:00:00+00:00",
    }
    for revision, created_at in created_ats.items():
        metadata = json.loads((snapshots[revision] / "metadata.json").read_text())
        metadata["created_at"] = created_at
        (snapshots[revision] / "metadata.json").write_text(json.dumps(metadata))

    create_database_backup(source, backup_dir, "3")

    retained_revisions = {
        json.loads((snapshot / "metadata.json").read_text())["deployed_revision"]
        for snapshot in backup_dir.glob("snapshot-*")
    }
    assert retained_revisions == {"0", "2", "3"}


def test_retention_failure_leaves_existing_snapshots_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "planning.db"
    backup_dir = tmp_path / "backups"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
    for revision in range(7):
        create_database_backup(source, backup_dir, str(revision))
    existing = set(backup_dir.glob("snapshot-*"))

    def refuse_delete(path: Path, **_: object) -> None:
        if any(".retention-" in part for part in path.parts) or path in existing:
            raise OSError("retention storage failure")

    monkeypatch.setattr("planner.environments.backup.shutil.rmtree", refuse_delete)
    with pytest.raises(OSError, match="retention storage failure"):
        create_database_backup(source, backup_dir, "new")

    assert existing <= set(backup_dir.glob("snapshot-*"))


def test_retention_does_not_duplicate_an_old_snapshot_before_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "planning.db"
    backup_dir = tmp_path / "backups"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
    for revision in range(7):
        create_database_backup(source, backup_dir, str(revision))

    def fail_copy(*_: object, **__: object) -> None:
        raise AssertionError("retention must not duplicate a database snapshot")

    monkeypatch.setattr("planner.environments.backup.shutil.copytree", fail_copy)
    create_database_backup(source, backup_dir, "new")

    assert len(tuple(backup_dir.glob("snapshot-*"))) == 3


def test_restore_requires_stopped_live_and_removes_stale_sidecars(tmp_path: Path) -> None:
    source = tmp_path / "planning.db"
    backup_dir = tmp_path / "backups"
    restored = tmp_path / "live.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
        connection.execute("INSERT INTO records VALUES ('preserved')")
    snapshot = create_database_backup(source, backup_dir, "rev-1")
    restored.write_bytes(b"old")
    Path(str(restored) + "-wal").write_bytes(b"stale")
    Path(str(restored) + "-shm").write_bytes(b"stale")

    with pytest.raises(ValueError, match="stopped"):
        restore_database_snapshot(snapshot, restored, live_stopped=False)
    restore_database_snapshot(snapshot, restored, live_stopped=True)

    assert not Path(str(restored) + "-wal").exists()
    assert not Path(str(restored) + "-shm").exists()
    with sqlite3.connect(restored) as connection:
        assert connection.execute("SELECT value FROM records").fetchone()[0] == "preserved"


def test_restore_replacement_failure_preserves_database_and_sidecars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.db"
    backup_dir = tmp_path / "backups"
    destination = tmp_path / "live.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
        connection.execute("INSERT INTO records VALUES ('new')")
    snapshot = create_database_backup(source, backup_dir, "rev-1")
    destination.write_bytes(b"old database")
    wal = Path(str(destination) + "-wal")
    shm = Path(str(destination) + "-shm")
    wal.write_bytes(b"old wal")
    shm.write_bytes(b"old shm")

    real_replace = __import__("os").replace

    def fail_database_replacement(source_path: str, destination_path: str) -> None:
        if destination_path == destination:
            raise OSError("replace failed")
        real_replace(source_path, destination_path)

    monkeypatch.setattr("planner.environments.backup.os.replace", fail_database_replacement)
    with pytest.raises(OSError, match="replace failed"):
        restore_database_snapshot(snapshot, destination, live_stopped=True)

    assert destination.read_bytes() == b"old database"
    assert wal.read_bytes() == b"old wal"
    assert shm.read_bytes() == b"old shm"


def test_restore_rejects_corrupted_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "planning.db"
    backup_dir = tmp_path / "backups"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
    snapshot = create_database_backup(source, backup_dir, "rev-1")
    (snapshot / "database.sqlite").write_bytes(b"corrupt")

    with pytest.raises(ValueError, match="verified"):
        restore_database_snapshot(snapshot, tmp_path / "restored.db", live_stopped=True)


@pytest.mark.parametrize("metadata", ([], "valid JSON", 42, None))
def test_restore_ignores_non_object_metadata(tmp_path: Path, metadata: object) -> None:
    source = tmp_path / "planning.db"
    backup_dir = tmp_path / "backups"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
    snapshot = create_database_backup(source, backup_dir, "rev-1")
    (snapshot / "metadata.json").write_text(json.dumps(metadata))

    with pytest.raises(ValueError, match="verified"):
        restore_database_snapshot(snapshot, tmp_path / "restored.db", live_stopped=True)


def test_restored_old_schema_can_migrate_and_preserves_data(tmp_path: Path) -> None:
    source = tmp_path / "old.db"
    backup_dir = tmp_path / "backups"
    with connect(str(source)) as connection:
        create_schema(connection)
        connection.execute(
            "INSERT INTO tickets (id, title, worker_type, employee_backend, stage, ceiling, "
            "fields, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "t_backup_migration",
                "Preserve this Ticket",
                "coding",
                "hermes",
                "needs_kickoff",
                "needs_success",
                json.dumps({"kickoff": {"value": "canonical data"}}),
                1,
                1,
            ),
        )
        ticket_sql = str(
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tickets'"
            ).fetchone()[0]
        )
        old_ticket_sql = ticket_sql.replace("'needs_user',", "", 1)
        connection.execute("DROP TABLE ticket_conversation_projections")
        for index_name in (
            "idx_tickets_alias",
            "idx_tickets_stage",
            "idx_tickets_worker_type_stage",
            "idx_tickets_project_id",
        ):
            connection.execute(f"DROP INDEX IF EXISTS {index_name}")
        connection.execute("ALTER TABLE tickets RENAME TO tickets_current")
        connection.execute(old_ticket_sql)
        connection.execute("INSERT INTO tickets SELECT * FROM tickets_current")
        connection.execute("DROP TABLE tickets_current")
        connection.execute(
            "CREATE TABLE ticket_conversation_projections ("
            "ticket_id TEXT PRIMARY KEY REFERENCES tickets(id) ON DELETE CASCADE, "
            "latest_activity_state TEXT, "
            "has_completed_response_awaiting_user INTEGER NOT NULL DEFAULT 0 "
            "CHECK (has_completed_response_awaiting_user IN (0,1)), "
            "has_pending_permission INTEGER NOT NULL DEFAULT 0 "
            "CHECK (has_pending_permission IN (0,1)), updated_at INTEGER NOT NULL)"
        )
        connection.execute("PRAGMA user_version=34")
    snapshot = create_database_backup(source, backup_dir, "old-revision")
    restored = tmp_path / "restored.db"
    restore_database_snapshot(snapshot, restored, live_stopped=True)

    with connect(str(restored)) as connection:
        create_schema(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 36
        assert (
            connection.execute(
                "SELECT fields FROM tickets WHERE id = 't_backup_migration'"
            ).fetchone()[0]
            == json.dumps({"kickoff": {"value": "canonical data"}})
        )


def test_environment_commands_expose_backup_and_stopped_restore(tmp_path: Path) -> None:
    source = tmp_path / "planning.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
    backup_dir = tmp_path / "backups"
    runner = CliRunner()

    backup_result = runner.invoke(
        environment,
        [
            "backup",
            "--source-db",
            str(source),
            "--backup-dir",
            str(backup_dir),
            "--deployed-revision",
            "rev-1",
        ],
    )
    assert backup_result.exit_code == 0, backup_result.output
    snapshot = next(backup_dir.glob("snapshot-*"))
    restore_result = runner.invoke(
        environment,
        [
            "restore",
            "--snapshot",
            str(snapshot),
            "--destination-db",
            str(tmp_path / "restored.db"),
            "--live-stopped",
        ],
    )
    assert restore_result.exit_code == 0, restore_result.output


def test_backup_captures_and_verifies_the_managed_file_tree(tmp_path: Path) -> None:
    source = tmp_path / "data" / "planner.db"
    _seed_database(source)
    _seed_managed_tree(source.parent, marker="captured")
    backup_dir = tmp_path / "backups"

    snapshot = create_database_backup(source, backup_dir, "rev-1")

    metadata = json.loads((snapshot / "metadata.json").read_text())
    assert metadata["managed_files"]["roots"] == ["files", "skills", "worker-settings"]
    assert (
        snapshot / "files" / "files" / "tickets" / "t_1" / "artifacts" / "ui.html"
    ).read_text() == "<h1>captured</h1>"
    assert (
        snapshot / "files" / "worker-settings" / "coding" / "settings.json"
    ).read_text() == '{"marker": "captured"}'
    assert (snapshot / "files" / "skills" / "panels" / "SKILL.md").read_text() == "# captured"
    assert _is_verified_snapshot(snapshot)


def test_missing_skills_home_is_tolerated(tmp_path: Path) -> None:
    source = tmp_path / "data" / "planner.db"
    _seed_database(source)
    _seed_managed_tree(source.parent, marker="captured")
    shutil.rmtree(source.parent / "hermes-home")
    backup_dir = tmp_path / "backups"

    snapshot = create_database_backup(source, backup_dir, "rev-1")

    metadata = json.loads((snapshot / "metadata.json").read_text())
    assert metadata["managed_files"]["roots"] == ["files", "worker-settings"]
    assert not (snapshot / "files" / "skills").exists()
    assert _is_verified_snapshot(snapshot)


def test_backup_without_managed_roots_is_database_only(tmp_path: Path) -> None:
    source = tmp_path / "data" / "planner.db"
    _seed_database(source)
    backup_dir = tmp_path / "backups"

    snapshot = create_database_backup(source, backup_dir, "rev-1")

    metadata = json.loads((snapshot / "metadata.json").read_text())
    assert "managed_files" not in metadata
    assert not (snapshot / "files").exists()
    assert _is_verified_snapshot(snapshot)


def test_restore_brings_back_the_managed_file_tree(tmp_path: Path) -> None:
    source = tmp_path / "source" / "planner.db"
    _seed_database(source)
    _seed_managed_tree(source.parent, marker="snapshot")
    backup_dir = tmp_path / "backups"
    snapshot = create_database_backup(source, backup_dir, "rev-1")

    destination = tmp_path / "live" / "planner.db"
    _seed_database(destination)
    _seed_managed_tree(destination.parent, marker="stale")
    # A stale ticket file that only exists in the live tree must be gone afterwards.
    (destination.parent / "files" / "tickets" / "t_old").mkdir(parents=True)
    (destination.parent / "files" / "tickets" / "t_old" / "note.txt").write_text("stale")

    restore_database_snapshot(snapshot, destination, live_stopped=True)

    assert (
        destination.parent / "files" / "tickets" / "t_1" / "artifacts" / "ui.html"
    ).read_text() == "<h1>snapshot</h1>"
    assert (
        destination.parent / "worker-settings" / "coding" / "settings.json"
    ).read_text() == '{"marker": "snapshot"}'
    assert (destination.parent / "hermes-home" / "skills" / "panels" / "SKILL.md").read_text() == (
        "# snapshot"
    )
    assert not (destination.parent / "files" / "tickets" / "t_old").exists()
    with sqlite3.connect(destination) as connection:
        assert connection.execute("SELECT value FROM records").fetchone()[0] == "canonical"


def test_restore_rejects_tampered_managed_capture(tmp_path: Path) -> None:
    source = tmp_path / "data" / "planner.db"
    _seed_database(source)
    _seed_managed_tree(source.parent, marker="captured")
    backup_dir = tmp_path / "backups"
    snapshot = create_database_backup(source, backup_dir, "rev-1")

    tampered = snapshot / "files" / "files" / "tickets" / "t_1" / "artifacts" / "ui.html"
    tampered.write_text("<h1>tampered after capture</h1>")

    assert not _is_verified_snapshot(snapshot)
    with pytest.raises(ValueError, match="verified"):
        restore_database_snapshot(snapshot, tmp_path / "live" / "planner.db", live_stopped=True)


def test_restore_managed_replacement_failure_rolls_back_the_live_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source" / "planner.db"
    _seed_database(source)
    _seed_managed_tree(source.parent, marker="snapshot")
    backup_dir = tmp_path / "backups"
    snapshot = create_database_backup(source, backup_dir, "rev-1")

    destination = tmp_path / "live" / "planner.db"
    _seed_database(destination)
    _seed_managed_tree(destination.parent, marker="stale")

    real_replace = __import__("os").replace
    managed_targets = {"files", "worker-settings", "skills"}
    state = {"failed": False}

    def fail_managed_replace(source_path: str, destination_path: str) -> None:
        # Fail only the first forward swap of a managed root; the rollback swap
        # of that same target must be allowed through.
        if not state["failed"] and Path(destination_path).name in managed_targets:
            state["failed"] = True
            raise OSError("managed replace failed")
        real_replace(source_path, destination_path)

    monkeypatch.setattr("planner.environments.backup.os.replace", fail_managed_replace)
    with pytest.raises(OSError, match="managed replace failed"):
        restore_database_snapshot(snapshot, destination, live_stopped=True)

    # The live managed tree is rolled back to its pre-restore state.
    assert (
        destination.parent / "worker-settings" / "coding" / "settings.json"
    ).read_text() == '{"marker": "stale"}'
    assert (
        destination.parent / "files" / "tickets" / "t_1" / "artifacts" / "ui.html"
    ).read_text() == "<h1>stale</h1>"
    leftover = [p for p in (destination.parent).glob("*.restore-*")]
    assert leftover == []


def test_skills_home_resolves_from_plan_hermes_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "data" / "planner.db"
    _seed_database(source)
    hermes_home = tmp_path / "elsewhere" / "hermes-home"
    (hermes_home / "skills" / "panels").mkdir(parents=True)
    (hermes_home / "skills" / "panels" / "SKILL.md").write_text("# from PLAN_HERMES_HOME")
    monkeypatch.setenv("PLAN_HERMES_HOME", str(hermes_home))
    backup_dir = tmp_path / "backups"

    snapshot = create_database_backup(source, backup_dir, "rev-1")

    metadata = json.loads((snapshot / "metadata.json").read_text())
    assert "skills" in metadata["managed_files"]["roots"]
    assert (snapshot / "files" / "skills" / "panels" / "SKILL.md").read_text() == (
        "# from PLAN_HERMES_HOME"
    )


def test_symlinked_skills_are_captured_and_restored_as_symlinks(tmp_path: Path) -> None:
    source = tmp_path / "data" / "planner.db"
    _seed_database(source)
    packaged = tmp_path / "repo" / "panels"
    packaged.mkdir(parents=True)
    (packaged / "SKILL.md").write_text("# canonical")
    skills = source.parent / "hermes-home" / "skills"
    skills.mkdir(parents=True)
    (skills / "panels").symlink_to(packaged, target_is_directory=True)
    # A dangling symlink must not abort the backup (the database must still be protected).
    (skills / "broken").symlink_to(tmp_path / "missing", target_is_directory=True)
    backup_dir = tmp_path / "backups"

    snapshot = create_database_backup(source, backup_dir, "rev-1")

    assert _is_verified_snapshot(snapshot)
    assert (snapshot / "files" / "skills" / "panels").is_symlink()

    destination = tmp_path / "live" / "planner.db"
    _seed_database(destination)
    restore_database_snapshot(snapshot, destination, live_stopped=True)

    restored = destination.parent / "hermes-home" / "skills" / "panels"
    assert restored.is_symlink()
    assert (restored / "SKILL.md").read_text() == "# canonical"


def test_environment_commands_restore_the_managed_file_tree(tmp_path: Path) -> None:
    source = tmp_path / "source" / "planner.db"
    _seed_database(source)
    _seed_managed_tree(source.parent, marker="snapshot")
    backup_dir = tmp_path / "backups"
    runner = CliRunner()
    backup_result = runner.invoke(
        environment,
        ["backup", "--source-db", str(source), "--backup-dir", str(backup_dir),
         "--deployed-revision", "rev-1"],
    )
    assert backup_result.exit_code == 0, backup_result.output
    snapshot = next(backup_dir.glob("snapshot-*"))

    destination = tmp_path / "live" / "planner.db"
    _seed_database(destination)
    restore_result = runner.invoke(
        environment,
        ["restore", "--snapshot", str(snapshot), "--destination-db", str(destination),
         "--live-stopped"],
    )
    assert restore_result.exit_code == 0, restore_result.output
    assert (
        destination.parent / "files" / "tickets" / "t_1" / "artifacts" / "ui.html"
    ).read_text() == "<h1>snapshot</h1>"

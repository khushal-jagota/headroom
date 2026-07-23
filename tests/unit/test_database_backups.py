from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from planner.core.db import connect, create_schema
from planner.environments.backup import create_database_backup, restore_database_snapshot
from planner.environments.cli import environment


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


def test_retention_keeps_seven_verified_snapshots(tmp_path: Path) -> None:
    source = tmp_path / "planning.db"
    backup_dir = tmp_path / "backups"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
    for revision in range(8):
        create_database_backup(source, backup_dir, str(revision))

    snapshots = sorted(backup_dir.glob("snapshot-*"))
    assert len(snapshots) == 7
    assert all((snapshot / "metadata.json").exists() for snapshot in snapshots)


def test_retention_keeps_newest_seven_by_creation_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "planning.db"
    backup_dir = tmp_path / "backups"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
    snapshots = [create_database_backup(source, backup_dir, str(revision)) for revision in range(7)]
    for revision, snapshot in enumerate(snapshots):
        metadata = json.loads((snapshot / "metadata.json").read_text())
        metadata["created_at"] = (
            "2030-01-01T00:00:00+00:00"
            if revision == 0
            else f"202{revision - 1}-01-01T00:00:00+00:00"
        )
        (snapshot / "metadata.json").write_text(json.dumps(metadata))

    create_database_backup(source, backup_dir, "7")

    retained_revisions = {
        json.loads((snapshot / "metadata.json").read_text())["deployed_revision"]
        for snapshot in backup_dir.glob("snapshot-*")
    }
    assert retained_revisions == {"0", "2", "3", "4", "5", "6", "7"}


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

    assert len(tuple(backup_dir.glob("snapshot-*"))) == 7


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

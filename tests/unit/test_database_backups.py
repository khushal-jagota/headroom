from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from planner.environments.backup import (
    RETENTION_COUNT,
    _is_verified_snapshot,
    create_database_backup,
    restore_database_snapshot,
)


@pytest.fixture(autouse=True)
def _default_skills_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep ambient provider-home configuration isolated from managed backup roots.
    monkeypatch.setenv("HOME", str(tmp_path / "user-home"))
    for name in (
        "PLAN_HERMES_HOME",
        "HERMES_HOME",
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
    ):
        monkeypatch.setenv(name, str(tmp_path / "homes" / name.lower()))
    for name in (
        "PLAN_DB_PATH",
        "PLAN_LOGS_DIR",
        "PLAN_DISPATCHER_LOCK_PATH",
        "PLAN_SERVER_CONTROL_SOCKET",
        "PLAN_BACKUP_DIR",
    ):
        monkeypatch.setenv(name, str(tmp_path / "runtime" / name.lower()))


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
    skills = data_dir / "skills" / "panels"
    skills.mkdir(parents=True, exist_ok=True)
    (skills / "SKILL.md").write_text(f"# {marker}")


def _legacy_snapshot(source: Path, snapshot: Path) -> Path:
    """Build the pre-change v2 format independently of the capture writer."""
    snapshot.mkdir(parents=True)
    database = snapshot / "database.sqlite"
    with sqlite3.connect(source) as original, sqlite3.connect(database) as copied:
        original.backup(copied)
    manifest = {}
    for name in ("files", "worker-settings", "skills"):
        root = source.parent / name
        if not root.exists():
            continue
        target = snapshot / "files" / name
        shutil.copytree(root, target, symlinks=True)
        manifest[name] = {
            path.relative_to(target).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in target.rglob("*")
            if path.is_file() and not path.is_symlink()
        }
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    (snapshot / "files" / "manifest.json").write_bytes(manifest_bytes)
    (snapshot / "metadata.json").write_text(
        json.dumps(
            {
                "format": "panels-backup-v2",
                "created_at": "2026-10-01T00:00:00+00:00",
                "deployed_revision": "legacy-fixture",
                "sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
                "verified": True,
                "managed_files": {
                    "roots": sorted(manifest),
                    "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                },
            }
        )
    )
    return snapshot


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


def test_retention_keeps_the_configured_verified_snapshots(tmp_path: Path) -> None:
    source = tmp_path / "planning.db"
    backup_dir = tmp_path / "backups"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
    for revision in range(RETENTION_COUNT + 1):
        create_database_backup(source, backup_dir, str(revision))

    snapshots = sorted(backup_dir.glob("snapshot-*"))
    assert len(snapshots) == RETENTION_COUNT
    assert all((snapshot / "metadata.json").exists() for snapshot in snapshots)


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

    def fail_database_replacement(source_path: Path, destination_path: Path) -> None:
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


def test_restore_brings_back_the_managed_file_tree(tmp_path: Path) -> None:
    source = tmp_path / "source" / "planner.db"
    _seed_database(source)
    _seed_managed_tree(source.parent, marker="snapshot")
    backup_dir = tmp_path / "backups"
    snapshot = _legacy_snapshot(source, backup_dir / "snapshot-legacy")

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
    assert (destination.parent / "skills" / "panels" / "SKILL.md").read_text() == "# snapshot"
    assert not (destination.parent / "files" / "tickets" / "t_old").exists()
    with sqlite3.connect(destination) as connection:
        assert connection.execute("SELECT value FROM records").fetchone()[0] == "canonical"


def test_restore_managed_replacement_failure_rolls_back_the_live_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source" / "planner.db"
    _seed_database(source)
    _seed_managed_tree(source.parent, marker="snapshot")
    backup_dir = tmp_path / "backups"
    snapshot = _legacy_snapshot(source, backup_dir / "snapshot-legacy")

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


def test_symlinked_skills_are_captured_and_restored_as_symlinks(tmp_path: Path) -> None:
    source = tmp_path / "data" / "planner.db"
    _seed_database(source)
    packaged = tmp_path / "repo" / "panels"
    packaged.mkdir(parents=True)
    (packaged / "SKILL.md").write_text("# canonical")
    skills = source.parent / "skills"
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

    restored = destination.parent / "skills" / "panels"
    assert restored.is_symlink()
    assert (restored / "SKILL.md").read_text() == "# canonical"


def test_new_snapshot_excludes_artifacts_and_preserves_destination_files(tmp_path: Path) -> None:
    source = tmp_path / "source" / "planner.db"
    _seed_database(source)
    _seed_managed_tree(source.parent, marker="snapshot")
    artifact = Path("files/tickets/t_1/artifacts/ui.html")
    snapshot = create_database_backup(source, tmp_path / "backups", "new")

    metadata = json.loads((snapshot / "metadata.json").read_text())
    assert metadata["format"] == "panels-backup-v2"
    assert metadata["managed_files"]["roots"] == ["skills", "worker-settings"]
    assert not (snapshot / "files" / "files").exists()
    assert (source.parent / artifact).read_text() == "<h1>snapshot</h1>"
    assert _is_verified_snapshot(snapshot)

    destination = tmp_path / "destination" / "planner.db"
    _seed_database(destination)
    _seed_managed_tree(destination.parent, marker="destination")
    restore_database_snapshot(snapshot, destination, live_stopped=True)

    assert (destination.parent / artifact).read_text() == "<h1>destination</h1>"
    assert (destination.parent / "worker-settings/coding/settings.json").read_text() == (
        '{"marker": "snapshot"}'
    )
    assert (destination.parent / "skills/panels/SKILL.md").read_text() == "# snapshot"


@pytest.mark.parametrize("legacy", [False, True], ids=["new", "legacy"])
def test_restored_panels_starts_and_reads_and_writes_tickets(tmp_path: Path, legacy: bool) -> None:
    # This connected proof crosses the real schema, restore, lifespan and HTTP doors.
    # The source is synthetic. Every path and provider home belongs to tmp_path.
    from fastapi.testclient import TestClient

    from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
    from planner.core.clock import build_clock
    from planner.core.config import load_config
    from planner.core.contracts import OWNER_PRINCIPAL
    from planner.core.db import connect, create_schema
    from planner.core.server import create_app
    from planner.tickets import data as tickets_data

    source = tmp_path / "source" / "planner.db"
    source.parent.mkdir()
    with closing(connect(str(source))) as conn:
        create_schema(conn)
        ticket = tickets_data.create_ticket(
            conn,
            title="Recover this ticket",
            principal=OWNER_PRINCIPAL,
            now=1792231200,
            title_max_chars=200,
            worker_type="coding",
        )
    artifact_path = Path("files") / "tickets" / ticket.id / "artifacts" / "proof.txt"
    artifact = source.parent / artifact_path
    artifact.parent.mkdir(parents=True)
    artifact.write_text("legacy artifact content")
    snapshot = (
        _legacy_snapshot(source, tmp_path / "snapshot-legacy")
        if legacy
        else create_database_backup(source, tmp_path / "backups", "new")
    )
    destination = tmp_path / "restored" / "planner.db"
    restore_database_snapshot(snapshot, destination, live_stopped=True)
    if not legacy:
        assert not (destination.parent / "files").exists()

    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(destination),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatcher.lock"),
            "PLAN_BACKUP_DIR": str(tmp_path / "restored-backups"),
            "PLAN_FAKE_NOW": "2026-10-17T12:00:00+02:00",
        },
    )
    app = create_app(
        config,
        build_clock(config),
        lambda: connect(str(destination)),
        conversation_system_for_test=InMemoryConversationSystem(),
    )
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        response = client.get(f"/api/tickets?detail=full&id={ticket.id}")
        assert response.status_code == 200, response.text
        assert response.json()["title"] == "Recover this ticket"
        response = client.patch(
            f"/api/tickets/{ticket.id}", json={"title": "Recovered and editable"}
        )
        assert response.status_code == 200, response.text
        restored_artifact = client.get(f"/files/tickets/{ticket.id}/artifacts/proof.txt")
        assert restored_artifact.status_code == (200 if legacy else 404)
        if legacy:
            assert restored_artifact.text == "legacy artifact content"
        else:
            assert not (destination.parent / artifact_path).exists()
    with closing(connect(str(destination))) as conn:
        assert tickets_data.read_ticket(conn, ticket.id).title == "Recovered and editable"

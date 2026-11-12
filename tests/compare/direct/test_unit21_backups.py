"""Real SQLite/file boundaries for fresh TS bootstrap and backup-v2 compatibility.

Three connected cases reuse Python backup fixtures. No server or browser host.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from tests.compare.direct.unit21_schema_seed import ARTIFACT, build_seed
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment
from tests.unit.test_database_backups import _legacy_snapshot, _seed_database, _seed_managed_tree

from planner.environments import backup

ROOT = Path(__file__).resolve().parents[3]


def _node(root: Path, **spec: Any) -> dict[str, Any]:
    homes = create_synthetic_homes(root)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PLAN_") and key not in homes
    }
    env.update(homes)
    env.update(PLAN_TEST_MODE="1", PLAN_DB_PATH=str(root / "synthetic.sqlite"))
    request = root / "request.json"
    request.write_text(json.dumps({"root": str(root), **spec}))
    launcher = root / "probe.mjs"
    launcher.write_text(
        "import{readFileSync}from'node:fs';const{backup_probe}=await import(process.argv[2]);"
        "console.log(JSON.stringify(await backup_probe("
        "JSON.parse(readFileSync(process.argv[3],'utf8')))));"
    )
    result = subprocess.run(
        [
            shutil.which("node") or "node",
            str(launcher),
            (ROOT / "server/environments/backup/testing/compare.ts").as_uri(),
            str(request),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return payload


def _report(request: pytest.FixtureRequest, name: str, started: float, **proof: Any) -> None:
    root = request.config.getoption("compare_report_dir", default=None)
    if root:
        destination = Path(root)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / name).write_text(
            json.dumps({"seconds": time.perf_counter() - started, **proof}, indent=2) + "\n"
        )


def _tree(root: Path) -> dict[str, Any]:
    return {
        path.relative_to(root).as_posix(): {"link": os.readlink(path)}
        if path.is_symlink()
        else {"directory": True}
        if path.is_dir()
        else {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(root.rglob("*"))
    }


def _records(database: Path) -> list[str]:
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return [row[0] for row in connection.execute("SELECT value FROM records ORDER BY value")]


def test_unit21_fresh_schema_bootstrap_is_complete_unique_and_atomic(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """DDL-only fixtures missed required singleton rows. Prove the real bootstrap once."""
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    with synthetic_home_environment(homes):
        seed, inventory = build_seed(tmp_path / "python")
    assert seed == json.loads(ARTIFACT.read_text())
    actual = _node(tmp_path, action="fresh")
    assert "error" not in actual, actual
    first, second = actual["states"]
    with closing(sqlite3.connect(tmp_path / "python/source.sqlite")) as connection:
        connection.row_factory = sqlite3.Row
        expected_schema = [
            dict(row)
            for row in connection.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL "
                "AND name NOT LIKE 'sqlite_%' ORDER BY type,name"
            )
        ]
        assert first["projects"] == [
            dict(row) for row in connection.execute("SELECT * FROM projects ORDER BY id")
        ]
        assert first["launch"] == dict(
            connection.execute("SELECT * FROM manager_launch_defaults").fetchone()
        )
        assert first["chief"] == dict(connection.execute("SELECT * FROM chief_page").fetchone())
        assert first["preferences"] == [
            dict(row) for row in connection.execute("SELECT * FROM notification_preferences")
        ]
        expected_revision = connection.execute("SELECT revision FROM navigation_meta").fetchone()[0]
    for state in actual["states"]:
        assert state["schema"] == expected_schema
        assert state["inventory"] == inventory
        assert state["revision"] == {"version_num": seed["alembicRevision"]}
        assert state["navigation"]["schema_version"] == 7
        assert state["navigation"]["revision"] == expected_revision
        assert state["retention"] == [
            {"singleton": 1, "epoch": state["navigation"]["epoch"], "floor_revision": 0}
        ]
        assert state["attention"] == {"singleton": 1, "started_at": 42}
        assert state["web_push"]["created_at"] == 0
    for field in ("scope", "epoch"):
        assert first["navigation"][field] != second["navigation"][field]
    for field in ("private_key", "public_key"):
        assert first["web_push"][field] != second["web_push"][field]
    assert actual["existing_refused"] and actual["existing_unchanged"]
    assert actual["errors"]["throw"] == "synthetic bootstrap failure"
    assert "must be synchronous" in actual["errors"]["async"]
    assert actual["errors"]["throw_tables"] == actual["errors"]["async_tables"] == "0"
    assert "no longer active" in actual["errors"]["retained"]
    assert "active write transaction" in actual["errors"]["write_checkpoint"]
    assert "active read transaction" in actual["errors"]["read_checkpoint"]
    assert actual["checkpoint"] == {"busy": 0, "log": 0, "checkpointed": 0}
    _report(
        request,
        "unit-21-fresh-schema.json",
        started,
        result=actual,
        boundary=(
            "Generated final DDL, fresh TS bootstrap, atomic rollback and retained-capability "
            "fencing. No runtime publication is claimed."
        ),
    )


def test_unit21_backup_v2_cross_language_wal_roots_and_retention(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Native online images and root copies must survive both implementations."""
    started = time.perf_counter()
    source = tmp_path / "source/database.sqlite"
    _seed_database(source)
    _seed_managed_tree(source.parent, "canonical")
    (source.parent / "skills/dangling").symlink_to("missing")
    (source.parent / "skills/inner").symlink_to("panels", target_is_directory=True)
    # Root links are followed by copytree. Descendant links remain links.
    (source.parent / "worker-settings").rename(source.parent / "settings-target")
    (source.parent / "worker-settings").symlink_to("settings-target", target_is_directory=True)
    with closing(sqlite3.connect(source)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO records VALUES ('committed in WAL')")
        writer.commit()
        assert Path(str(source) + "-wal").stat().st_size > 0
        result = _node(tmp_path, action="backup", source=str(source))
        snapshot = Path(result["snapshot"])
        assert backup.is_verified_snapshot(snapshot)
        assert _records(snapshot / "database.sqlite") == ["canonical", "committed in WAL"]
        metadata = json.loads((snapshot / "metadata.json").read_text())
        assert re.fullmatch(
            r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{6})?\+00:00", metadata["created_at"]
        )
        assert metadata["managed_files"]["roots"] == ["skills", "worker-settings"]
        destination = tmp_path / "python-restore/database.sqlite"
        _seed_managed_tree(destination.parent, "old")
        backup.restore_database_snapshot(snapshot, destination, live_stopped=True)
        assert _records(destination) == ["canonical", "committed in WAL"]
        assert "old" in (destination.parent / "files/tickets/t_1/artifacts/ui.html").read_text()
        assert _tree(destination.parent / "skills") == _tree(source.parent / "skills")
        assert _tree(destination.parent / "worker-settings") == _tree(
            source.parent / "worker-settings"
        )
        python_snapshot = backup.create_database_backup(
            source, tmp_path / "python-backups", "python"
        )
        readonly = python_snapshot / "files/skills/panels/SKILL.md"
        readonly.chmod(0o444)
        typescript_destination = tmp_path / "typescript-restore/database.sqlite"
        _seed_managed_tree(typescript_destination.parent, "old")
        assert _node(
            tmp_path,
            action="restore",
            snapshot=str(python_snapshot),
            destination=str(typescript_destination),
        ) == {"restored": True}
        assert _records(typescript_destination) == _records(source)
        assert _tree(typescript_destination.parent / "skills") == _tree(source.parent / "skills")
        assert (
            typescript_destination.parent / "skills/panels/SKILL.md"
        ).stat().st_mode & 0o600 == 0o600
        legacy = _legacy_snapshot(source, tmp_path / "legacy")
        assert _node(
            tmp_path,
            action="restore",
            snapshot=str(legacy),
            destination=str(typescript_destination),
        ) == {"restored": True}
        assert _tree(typescript_destination.parent / "files") == _tree(source.parent / "files")
    bad = tmp_path / "backups/snapshot-invalid"
    bad.mkdir()
    for index in range(4):
        assert "snapshot" in _node(
            tmp_path, action="backup", source=str(source), revision=str(index)
        )
    verified = backup.verified_snapshots(tmp_path / "backups")
    assert len(verified) == 3 and bad.is_dir()
    before = sorted(path.name for path in (tmp_path / "backups").iterdir())
    assert _node(tmp_path, action="backup", source=str(source), fail_publication=True)["injected"]
    assert sorted(path.name for path in (tmp_path / "backups").iterdir()) == before
    # A former millisecond-Z timestamp sorts after a later Python microsecond value.
    # Mix the actual TS representation with Python snapshots inside one second.
    mixed = tmp_path / "mixed"
    mixed.mkdir()
    ts_snapshot = Path(_node(mixed, action="backup", source=str(source))["snapshot"])
    ts_metadata = json.loads((ts_snapshot / "metadata.json").read_text())
    second = ts_metadata["created_at"][:19]
    ts_metadata["created_at"] = second + ".123000+00:00"
    (ts_snapshot / "metadata.json").write_text(json.dumps(ts_metadata))
    for micros in (123001, 123002, 123003):
        py_snapshot = _legacy_snapshot(source, mixed / "backups" / f"snapshot-python-{micros}")
        py_metadata = json.loads((py_snapshot / "metadata.json").read_text())
        py_metadata["created_at"] = second + f".{micros:06d}+00:00"
        (py_snapshot / "metadata.json").write_text(json.dumps(py_metadata))
    assert _node(mixed, action="retention") == {"retained": True}
    assert not ts_snapshot.exists()
    assert len(backup.verified_snapshots(mixed / "backups")) == 3
    _report(
        request,
        "unit-21-backup-compatibility.json",
        started,
        verified=len(verified),
        boundary=(
            "Real WAL online backup, TS→Python and Python→TS restore, root/inner symlinks, "
            "legacy files, retention and unpublished failure cleanup."
        ),
    )


def test_unit21_restore_refusals_and_failed_swaps_preserve_owned_files(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Corruption precedes mutation. Sidecars and earlier roots survive failed swaps."""
    started = time.perf_counter()
    source = tmp_path / "source/database.sqlite"
    _seed_database(source)
    _seed_managed_tree(source.parent, "new")
    snapshot = backup.create_database_backup(source, tmp_path / "python-backups", "python")
    destination = tmp_path / "destination/database.sqlite"
    _seed_database(destination)
    _seed_managed_tree(destination.parent, "old")
    with sqlite3.connect(destination) as connection:
        connection.execute("UPDATE records SET value='old record'")
    sidecars = [Path(str(destination) + suffix) for suffix in ("-wal", "-shm")]
    for path in sidecars:
        path.write_bytes(path.name.encode())
    original = _tree(destination.parent)
    assert (
        "stopped"
        in _node(
            tmp_path,
            action="restore",
            snapshot=str(snapshot),
            destination=str(destination),
            live_stopped=False,
        )["error"]
    )
    assert _tree(destination.parent) == original
    corrupt = tmp_path / "corrupt"
    shutil.copytree(snapshot, corrupt, symlinks=True)
    (corrupt / "database.sqlite").write_bytes(b"corrupt")
    assert (
        "verified"
        in _node(tmp_path, action="restore", snapshot=str(corrupt), destination=str(destination))[
            "error"
        ]
    )
    assert _tree(destination.parent) == original
    metadata = json.loads((corrupt / "metadata.json").read_text())
    metadata["sha256"] = hashlib.sha256(b"corrupt").hexdigest()
    (corrupt / "metadata.json").write_text(json.dumps(metadata))
    assert "error" in _node(
        tmp_path, action="restore", snapshot=str(corrupt), destination=str(destination)
    )
    assert _tree(destination.parent) == original
    failure = _node(
        tmp_path,
        action="restore",
        snapshot=str(snapshot),
        destination=str(destination),
        fail_target=str(destination),
    )
    assert failure == {"error": "synthetic rename failure", "injected": True}
    assert _tree(destination.parent) == original
    # The second root fails after skills already swapped. Both managed roots roll back.
    second = destination.parent / "worker-settings"
    failure = _node(
        tmp_path,
        action="restore",
        snapshot=str(snapshot),
        destination=str(destination),
        fail_target=str(second),
    )
    assert failure == {"error": "synthetic rename failure", "injected": True}
    for name in ("files", "skills", "worker-settings"):
        assert _tree(destination.parent / name) == {
            key.removeprefix(name + "/"): value
            for key, value in original.items()
            if key.startswith(name + "/")
        }
    assert _records(destination) == ["canonical"]
    assert not any(path.exists() for path in sidecars)
    assert not any(".restore-" in path.name for path in destination.parent.iterdir())
    _report(
        request,
        "unit-21-restore-failures.json",
        started,
        boundary=(
            "Stopped assertion, checksum/integrity refusal, DB replacement/sidecar rollback, "
            "second-root rollback after DB replacement. No whole-restore transaction is claimed."
        ),
    )

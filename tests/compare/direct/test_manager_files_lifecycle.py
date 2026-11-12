"""Compare the real file lifecycle boundaries absent from ordinary HTTP input."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from tests.support.in_memory_conversation_system import InMemoryConversationSystem
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError
from planner.files.lifecycle import (
    purge_quarantined_files,
    quarantine_manager_files,
    restore_quarantined_files,
)
from planner.files.manager_files import list_artifact_entries, list_files
from planner.managers.service import delete_manager

ROOT = Path(__file__).resolve().parents[3]


def _tree(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = "symlink:" + os.readlink(path)
        elif path.is_file():
            result[relative] = path.read_bytes().hex()
    return result


def _python(db_path: Path) -> dict[str, Any]:
    root = db_path.parent / "files"
    managers = root / "sprint-items"
    owner = managers / "si_proof"
    outside = _tree(root / "outside")
    with sqlite3.connect(db_path) as conn:
        before = _tree(root)
        listed = list_files(conn, OWNER_PRINCIPAL, "si_proof", str(db_path))
        inventory = list_artifact_entries(conn, OWNER_PRINCIPAL, "si_proof", str(db_path))
        first = quarantine_manager_files(conn, "si_proof")
        assert first is not None
        quarantined = _tree(first.quarantine)
        absent_during_quarantine = not owner.exists()
        restore_quarantined_files(first)
        restored = _tree(root)
        purge_quarantined_files(quarantine_manager_files(conn, "si_proof"))
        purged = _tree(root)
        quarantine_empty = not list((root / ".sprint-item-quarantine").iterdir())

        managers.rename(managers.with_name("sprint-items-saved"))
        managers.symlink_to("absent")
        root_refusal = ""
        try:
            quarantine_manager_files(conn, "si_proof")
        except RuntimeError as error:
            root_refusal = str(error)
        root_inventory_refusal = ""
        try:
            list_artifact_entries(conn, OWNER_PRINCIPAL, "si_proof", str(db_path))
        except PlannerError as error:
            root_inventory_refusal = str(error)
        managers.unlink()
        managers.with_name("sprint-items-saved").rename(managers)

        owner.symlink_to("../outside")
        owner_listing = list_files(conn, OWNER_PRINCIPAL, "si_proof", str(db_path))
        owner_inventory = list_artifact_entries(
            conn, OWNER_PRINCIPAL, "si_proof", str(db_path)
        )
        link = quarantine_manager_files(conn, "si_proof")
        assert link is not None
        quarantined_link = link.quarantine.is_symlink()
        restore_quarantined_files(link)
        restored_link = os.readlink(owner)
        purge_quarantined_files(quarantine_manager_files(conn, "si_proof"))
        outside_unchanged = _tree(root / "outside") == outside
        owner.write_text("Owner file instead of directory")
        purge_quarantined_files(quarantine_manager_files(conn, "si_proof"))
        owner_file_purged = not owner.exists()
        missing = quarantine_manager_files(conn, "si_proof")
        restore_quarantined_files(missing)
        purge_quarantined_files(missing)
        return {
            "before": before,
            "listed": listed,
            "inventory": inventory,
            "quarantined": quarantined,
            "absent_during_quarantine": absent_during_quarantine,
            "restored": restored,
            "purged": purged,
            "quarantine_empty": quarantine_empty,
            "root_refusal": root_refusal,
            "root_inventory_refusal": root_inventory_refusal,
            "owner_listing": owner_listing,
            "owner_inventory": owner_inventory,
            "quarantined_link": quarantined_link,
            "restored_link": restored_link,
            "outside_unchanged": outside_unchanged,
            "owner_file_purged": owner_file_purged,
            "missing": missing,
            "final": _tree(root),
        }


async def _delete(db_path: Path) -> dict[str, Any]:
    with closing(connect(str(db_path))) as conn:

        def snapshot() -> dict[str, Any]:
            return {
                "files": _tree(db_path.parent / "files"),
                "managers": [
                    dict(row) for row in conn.execute("SELECT * FROM managers ORDER BY id")
                ],
                "agents": [
                    dict(row) for row in conn.execute("SELECT * FROM agents ORDER BY agent_key")
                ],
            }

        system = InMemoryConversationSystem()
        before = snapshot()
        failure = ""
        try:
            await delete_manager(system, conn, "si_delete", principal=OWNER_PRINCIPAL)
        except sqlite3.IntegrityError as error:
            failure = str(error)
        rollback = snapshot()
        conn.execute("DROP TRIGGER fail_manager_deletion")
        deleted = await delete_manager(system, conn, "si_delete", principal=OWNER_PRINCIPAL)
        return {
            "before": before,
            "failure": failure,
            "rollback": rollback,
            "deleted": {"manager_id": deleted.manager_id, "title": deleted.title},
            "after": snapshot(),
            "quarantine_empty": not list(
                (db_path.parent / "files" / ".sprint-item-quarantine").iterdir()
            ),
        }


def test_manager_files_quarantine_restore_purge_and_symlink_boundaries(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """HTTP cannot seed symlinks or observe the directory while it is quarantined."""
    started = time.perf_counter()
    seed = tmp_path / "seed"
    owner = seed / "files" / "sprint-items" / "si_proof"
    (owner / "artifacts" / "nested").mkdir(parents=True)
    (seed / "files" / "outside").mkdir()
    (seed / "files" / "outside" / "keep.bin").write_bytes(b"outside\x00\xff")
    (owner / "artifacts" / "index.html").write_text("<h1>Real artifact caf\u00e9</h1>")
    (owner / "artifacts" / "nested" / "data.bin").write_bytes(b"\x00\xff\xc3\xa9")
    (owner / "artifacts" / "skip.bin").symlink_to("../../../outside/keep.bin")
    (owner / "artifacts" / "dangling.bin").symlink_to("absent.bin")
    (owner / "artifacts" / "skip-directory").symlink_to("../../../outside")
    for path in owner.rglob("*"):
        if not path.is_symlink():
            os.utime(path, (100, 100))
    deletion_owner = seed / "files" / "sprint-items" / "si_delete"
    deletion_owner.mkdir()
    (deletion_owner / "report.bin").write_bytes(b"Preserve across rollback\x00\xff")
    homes = create_synthetic_homes(tmp_path)
    with synthetic_home_environment(homes), closing(connect(str(seed / "test.sqlite"))) as conn:
        create_schema(conn)
        conn.execute(
            "INSERT INTO managers(id,title,project_id,manager_backend,manager_model,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            ("si_delete", "Delete populated files", "project_other", "codex", "gpt-6.1-sol", 100, 100),
        )
        conn.execute("INSERT INTO agents VALUES ('manager_si_delete',NULL)")
        conn.execute(
            "CREATE TRIGGER fail_manager_deletion BEFORE DELETE ON managers "
            "WHEN old.id='si_delete' BEGIN SELECT RAISE(ABORT,'synthetic deletion failure'); END"
        )
    left, right = tmp_path / "python", tmp_path / "typescript"
    shutil.copytree(seed, left, symlinks=True)
    shutil.copytree(seed, right, symlinks=True)
    reference = _python(left / "test.sqlite")
    deletion = asyncio.run(_delete(left / "test.sqlite"))
    companion = ROOT / "server" / "files" / "testing" / "lifecycle.ts"
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "const m = await import(process.argv[1]); "
            "const helpers = await m.manager_files_lifecycle(process.argv[2]); "
            "const deletion = await m.manager_deletion(process.argv[2]); "
            "process.stdout.write(JSON.stringify({helpers,deletion}));",
            companion.as_uri(),
            str(right / "test.sqlite"),
        ],
        cwd=ROOT,
        env={key: value for key, value in os.environ.items() if not key.startswith("PLAN_")},
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    output = json.loads(result.stdout)
    candidate = output["helpers"]
    assert candidate == reference
    assert output["deletion"] == deletion
    assert deletion["failure"] == "synthetic deletion failure"
    assert deletion["before"] == deletion["rollback"]
    assert deletion["quarantine_empty"]
    assert deletion["after"]["managers"] == deletion["after"]["agents"] == []
    assert "sprint-items/si_delete/report.bin" not in deletion["after"]["files"]
    assert reference["before"] == reference["restored"]
    assert reference["absent_during_quarantine"] and reference["quarantine_empty"]
    assert reference["quarantined_link"] and reference["outside_unchanged"]
    assert reference["owner_file_purged"] and reference["missing"] is None
    assert reference["root_refusal"] == "managed Manager file root is a symlink"
    assert reference["root_inventory_refusal"] == "unsafe Manager file path"
    assert reference["owner_listing"] == reference["owner_inventory"] == []
    assert {row["path"] for row in reference["listed"]} == {
        "artifacts/index.html",
        "artifacts/nested/data.bin",
    }
    report = request.config.getoption("--compare-report-dir")
    if report:
        destination = Path(report)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "manager-files-lifecycle.json").write_text(
            json.dumps(
                {
                    "kind": "direct",
                    "case": "manager-files-lifecycle",
                    "equal": True,
                    "python": reference,
                    "typescript": candidate,
                    "deletion": deletion,
                    "seconds": time.perf_counter() - started,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

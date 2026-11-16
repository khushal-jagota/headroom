"""Actual stopped helpers share one legacy fixture and never change its database."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from tests.compare.direct.test_unit21_backups import (
    _report,
    _ticket,
    _ticket_payload,
    _tree,
)
from tests.compare.direct.test_unit21_updates import _environment
from tests.unit.test_database_backups import _seed_managed_tree
from tests.unit.test_interruption_capture import capture_database as capture_database

from planner.conversation.deploy_interruptions.files import (
    manifest_from_dict,
    manifest_root,
)
from planner.conversation.lifecycle_storage import acquire_runtime_ownership
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect, create_schema
from planner.environments import backup
from planner.tickets import data as tickets_data

ROOT = Path(__file__).resolve().parents[3]


def _database_state(database: Path) -> tuple[bytes, tuple[str, ...]]:
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        rows = tuple(connection.iterdump())
    return database.read_bytes(), rows


def _run(command: list[str], environment: dict[str, str]) -> tuple[str, float]:
    began = time.perf_counter()
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr
        assert stderr == ""
        return stdout, time.perf_counter() - began
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)


def _capture(
    command: list[str], environment: dict[str, str], database: Path, identity: str
) -> tuple[dict[str, Any], float]:
    before = _database_state(database)
    stdout, seconds = _run(command, environment)
    value = json.loads((manifest_root(database) / "paired" / f"{identity}.json").read_text())
    manifest = manifest_from_dict(value)
    assert json.loads(stdout) == {
        "state": manifest.state,
        "detail": (manifest.detail or "")[:1000],
    }
    assert _database_state(database) == before
    return value, seconds


def _private_environment(root: Path, node: str) -> dict[str, str]:
    supplied = _environment(root)
    environment = {
        key: value
        for key, value in supplied.items()
        if key.startswith("PLAN_")
        or key
        in {
            "HOME",
            "CODEX_HOME",
            "CLAUDE_CONFIG_DIR",
            "HERMES_HOME",
            "PYTEST_CURRENT_TEST",
            "VITEST",
        }
    }
    environment.update(
        TMPDIR=str(root),
        TMP=str(root),
        TEMP=str(root),
        LANG="C.UTF-8",
        PATH=str(Path(node).parent) + os.pathsep + os.defpath,
        PLAN_HERMES_HOME=environment["HERMES_HOME"],
    )
    return environment


def test_unit21_stopped_capture_is_readonly_and_exact(
    capture_database: Path,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """Legacy selection, native evidence and SQLite refusal need real readonly entries."""
    started = time.perf_counter()
    node = shutil.which("node")
    assert node is not None
    environment = _private_environment(tmp_path, node)
    environment["PLAN_DB_PATH"] = str(capture_database)
    python_script = (
        "import runpy,sys,time;time.time=lambda:1700000000;"
        "sys.argv=['planner.environments.interruption_capture',*sys.argv[1:]];"
        "runpy.run_module('planner.environments.interruption_capture',run_name='__main__')"
    )
    node_script = (
        "import{pathToFileURL}from'node:url';"
        "const[entry,...args]=process.argv.slice(1);Date.now=()=>1700000000000;"
        "process.argv=[process.execPath,entry,...args];await import(pathToFileURL(entry).href);"
    )
    entry = ROOT / "server/environments/updates/interruption_capture_main.ts"
    timings: dict[str, dict[str, float]] = {}

    def phase(identity: str) -> dict[str, Any]:
        arguments = [str(capture_database), "paired", identity]
        expected, python_seconds = _capture(
            [sys.executable, "-I", "-B", "-c", python_script, *arguments],
            environment,
            capture_database,
            identity,
        )
        actual, node_seconds = _capture(
            [node, "--input-type=module", "-e", node_script, str(entry), *arguments],
            environment,
            capture_database,
            identity,
        )
        if actual["state"] == "unavailable":
            assert isinstance(actual["detail"], str) and actual["detail"]
            assert isinstance(expected["detail"], str) and expected["detail"]
            assert {**actual, "detail": "<diagnostic>"} == {**expected, "detail": "<diagnostic>"}
        else:
            assert actual == expected
        timings[identity] = {
            "python_seconds": python_seconds,
            "node_seconds": node_seconds,
        }
        return actual

    current = phase("current")
    assert current["captured_at"] == 1700000000 and current["state"] == "captured"
    legacy, worker = current["turns"]
    assert (
        legacy["conversation_id"],
        legacy["owner_kind"],
        legacy["occurrence_id"],
        legacy["runtime_epoch"],
    ) == ("legacy", "chief", None, "source-epoch")
    assert (
        worker["conversation_id"],
        worker["occurrence_id"],
        worker["runtime_epoch"],
        worker["claim_revision"],
        worker["claim"],
    ) == ("worker", "nonce", "source-epoch", 7, "out")
    with closing(sqlite3.connect(capture_database)) as connection, connection:
        connection.execute("UPDATE tickets SET conversation_id='replacement'")
        connection.execute(
            "INSERT INTO conversation_history_catalog VALUES('worker','ticket','ticket')"
        )
    historical = phase("historical")["turns"][1]
    assert (
        historical["owner_kind"],
        historical["owner_id"],
        historical["display_name"],
        historical["claim_revision"],
        historical["claim"],
    ) == ("ticket", "ticket", "Ship the change", None, None)
    with closing(sqlite3.connect(capture_database)) as connection, connection:
        connection.execute("INSERT INTO mac_runs VALUES('worker','stopping')")
    assert [turn["conversation_id"] for turn in phase("stopping")["turns"]] == ["legacy"]
    with closing(sqlite3.connect(capture_database)) as connection, connection:
        connection.execute("UPDATE mac_runs SET stop_state='closed'")
    assert [turn["conversation_id"] for turn in phase("closed")["turns"]] == [
        "legacy",
        "worker",
    ]
    with acquire_runtime_ownership(str(capture_database)):
        owned = phase("owned")
    assert owned["state"] == "unavailable" and owned["turns"] == []
    assert owned["detail"] == (
        "Interruption capture failed: ConversationRuntimeAlreadyOwned: "
        + str(capture_database.resolve())
    )
    with acquire_runtime_ownership(str(capture_database)):
        pass
    # Fixture-only DDL creates one real SQLite diagnostic after the healthy phases.
    with closing(sqlite3.connect(capture_database)) as connection, connection:
        connection.execute("DROP TABLE conversation_lifecycle_facts")
    missing = phase("missing-table")
    assert missing["state"] == "unavailable" and missing["turns"] == []
    assert "no such table: conversation_lifecycle_facts" in missing["detail"]
    _report(
        request,
        "unit21-interruption-capture.json",
        started,
        phases=timings,
        helper_processes=2 * len(timings),
        clock="child-local fixed time.time/Date.now; actual fixed entries",
        readonly="exact database bytes and readonly row dump before/after every helper",
    )


def test_unit21_fixed_restore_entry_preserves_populated_snapshot(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The installed fixed entry must delegate restore before ordinary Ticket edits work."""
    started = time.perf_counter()
    node = shutil.which("node")
    assert node is not None
    environment = _private_environment(tmp_path, node)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    source = tmp_path / "source/database.sqlite"
    source.parent.mkdir()
    now = 1792231200
    with closing(connect(str(source))) as connection:
        create_schema(connection)
        ticket = tickets_data.create_ticket(
            connection,
            title="Recover through the fixed entry",
            brief="Saved content",
            principal=OWNER_PRINCIPAL,
            now=now,
            title_max_chars=200,
            worker_type="coding",
        )
    _seed_managed_tree(source.parent, "saved")
    snapshot = backup.create_database_backup(source, tmp_path / "snapshots", "python")
    (snapshot / "files/skills/panels/SKILL.md").chmod(0o444)
    destination = tmp_path / "restored/database.sqlite"
    _seed_managed_tree(destination.parent, "old")
    entry = ROOT / "server/environments/updates/restore_main.ts"
    stdout, restore_seconds = _run([node, str(entry), str(snapshot), str(destination)], environment)
    assert stdout == ""
    expected = _ticket_payload(ticket)
    assert _ticket(destination, ticket.id) == expected
    for root in ("skills", "worker-settings"):
        assert _tree(destination.parent / root) == _tree(source.parent / root)
    assert (destination.parent / "skills/panels/SKILL.md").stat().st_mode & 0o600 == 0o600
    assert "old" in (destination.parent / "files/tickets/t_1/artifacts/ui.html").read_text()
    script = (
        "const{backup_probe}=await import(process.argv[1]);"
        "console.log(JSON.stringify(await backup_probe(JSON.parse(process.argv[2]))));"
    )
    stdout, ticket_seconds = _run(
        [
            node,
            "--input-type=module",
            "-e",
            script,
            (ROOT / "server/environments/backup/testing/compare.ts").as_uri(),
            json.dumps(
                {
                    "action": "ticket",
                    "root": str(tmp_path),
                    "destination": str(destination),
                    "ticket": {
                        "id": ticket.id,
                        "title": "Recovered and editable",
                        "now": now + 1,
                    },
                }
            ),
        ],
        environment,
    )
    result = json.loads(stdout)
    assert result["before"] == expected
    assert result["after"]["title"] == "Recovered and editable"
    assert _ticket(destination, ticket.id) == result["after"]
    _report(
        request,
        "unit21-fixed-restore-entry.json",
        started,
        restore_seconds=restore_seconds,
        ticket_seconds=ticket_seconds,
        helper_processes=2,
        boundary=(
            "Actual fixed restore entry, then existing Ticket probe, with exact child cleanup. "
            "No nested helper or second restore."
        ),
    )

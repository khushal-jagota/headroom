"""Measure production record-runtime startup on a large synthetic event history.

Existing view fixtures prove replay values. They do not measure the event-loop
stall from the one-writer rebuild. This explicit measurement reuses one server
fixture and has no performance threshold. Default CI discovery excludes this file.

Run explicitly:
  scripts/dev pytest tests/compare/direct/unit21_rebuild_measurement.py -s
The report distinguishes initialization from view readiness.
"""

from __future__ import annotations

import ast
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.support.server_fixtures import _start_server, _stop_server

ROOT = Path(__file__).resolve().parents[3]
CONVERSATIONS = 20
TURNS_PER_CONVERSATION = 2_000
EVENTS_PER_TURN = 5


def _seed(database: Path) -> None:
    # Reuse the existing TS Ticket fixture's current-schema literal extraction.
    # No Planner import, migration execution, skill publication, or provider access.
    source = ROOT / "src/planner/core/migrations/versions/current_schema.py"
    statements: list[str] = []
    for node in ast.parse(source.read_text()).body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "BASELINE_STATEMENTS":
                assert node.value is not None
                statements.extend(ast.literal_eval(node.value))
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "BASELINE_STATEMENTS":
                assert isinstance(node.op, ast.Add)
                statements.extend(ast.literal_eval(node.value))
    assert statements, "current-schema declarations absent"
    with sqlite3.connect(database) as conn:
        for statement in statements:
            conn.execute(statement)
        for conversation in range(CONVERSATIONS):
            identity = f"synthetic-{conversation:03d}"
            conn.execute(
                "INSERT INTO conversations "
                "(conversation_id,backend_key,model,workspace_folder,access,created_at,"
                "latest_sequence) "
                "VALUES (?,'hermes','synthetic-model',?,'full',1800000000,?)",
                (
                    identity,
                    str(database.parent / "workspace"),
                    TURNS_PER_CONVERSATION * EVENTS_PER_TURN,
                ),
            )
            events = []
            for turn in range(TURNS_PER_CONVERSATION):
                tool = f"synthetic-tool-{turn}"
                payloads: list[tuple[str, dict[str, Any]]] = [
                    (
                        "prompt",
                        {
                            "sender_label": "owner",
                            "mode": "queue",
                            "sender": {"kind": "owner", "id": "owner"},
                            "content": [{"piece": "text", "text": f"Synthetic work {turn}"}],
                        },
                    ),
                    (
                        "tool_call_started",
                        {
                            "tool_call_id": tool,
                            "title": "Read synthetic file",
                            "tool_kind": "read",
                            "detail": "synthetic/input.txt",
                        },
                    ),
                    (
                        "tool_call_finished",
                        {
                            "tool_call_id": tool,
                            "tool_call_status": "completed",
                            "detail": "Synthetic file contents " + "x" * 128,
                        },
                    ),
                    (
                        "message_to_owner",
                        {
                            "content": [{"piece": "text", "text": f"Synthetic result {turn}"}],
                            "sender": {"kind": "ticket", "id": "synthetic-worker"},
                            "recipient": {"kind": "owner", "id": "owner"},
                            "sender_label": "worker",
                        },
                    ),
                    ("turn_ended", {"ending": "completed", "error_summary": None}),
                ]
                for offset, (kind, payload) in enumerate(payloads, 1):
                    sequence = turn * EVENTS_PER_TURN + offset
                    events.append(
                        (identity, sequence, kind, json.dumps(payload), 1_800_000_000 + sequence)
                    )
            conn.executemany("INSERT INTO conversation_events VALUES (?,?,?,?,?)", events)


def _environment(root: Path, database: Path, config: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PLAN_")
        and key not in {"HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "HERMES_HOME"}
    }
    for variable, name in {
        "HOME": "home",
        "CODEX_HOME": "codex-home",
        "CLAUDE_CONFIG_DIR": "claude-home",
        "HERMES_HOME": "hermes-home",
        "PLAN_HERMES_HOME": "plan-hermes-home",
        "PLAN_LOGS_DIR": "logs",
        "PLAN_BACKUP_DIR": "backups",
        "PLAN_WORKSPACE_ROOT": "workspace",
        "PLAN_TICKET_SCRATCH_ROOT": "scratch",
        "PLAN_APP_ROOT": "app",
    }.items():
        directory = root / name
        directory.mkdir(exist_ok=True)
        env[variable] = str(directory)
    env.update(
        {
            "PLAN_TEST_MODE": "1",
            "PLAN_DISPATCH_ENABLED": "false",
            "PLAN_PORT": "0",
            "PLAN_DB_PATH": str(database),
            "PLAN_CONFIG_PATH": str(config),
            "PLAN_DISPATCHER_LOCK_PATH": str(root / "dispatcher.lock"),
            "PLAN_SERVER_CONTROL_SOCKET": str(root / "control.sock"),
            "PLAN_CREDENTIAL_KEY_PATH": str(root / "private-key"),
            "PLAN_TICKET_SCRATCH_DATABASE": str(database),
        }
    )
    return env


def _server_startup(root: Path, source: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    root.mkdir()
    config = root / "config.yaml"
    config.write_text("{}\n")
    env = _environment(root, root / "planning.db", config)
    monkeypatch.setenv("PANELS_COMPARE_TS_ENTRY", "server/main.ts")
    seed_seconds = 0.0

    def copy_source(target: Path) -> None:
        nonlocal seed_seconds
        started = time.perf_counter()
        with sqlite3.connect(target) as conn:
            conn.execute("ATTACH DATABASE ? AS synthetic", (str(source),))
            conn.execute("INSERT INTO conversations SELECT * FROM synthetic.conversations")
            conn.execute(
                "INSERT INTO conversation_events SELECT * FROM synthetic.conversation_events"
            )
        seed_seconds = time.perf_counter() - started

    started = time.perf_counter()
    handle = _start_server(
        root,
        seed_db=copy_source,
        server_module="tests.compare.ts_launcher",
        trusted_ingress_env={key: value for key, value in env.items() if key.startswith("PLAN_")},
    )
    ready_seconds = time.perf_counter() - started
    try:
        response = httpx.get(f"{handle.base}/api/health", timeout=10)
        assert response.status_code == 200
        assert response.json()["ready"] is True
        with sqlite3.connect(handle.db_path) as conn:
            events = conn.execute("SELECT COUNT(*) FROM conversation_events").fetchone()[0]
        assert events == CONVERSATIONS * TURNS_PER_CONVERSATION * EVENTS_PER_TURN
        return {
            "fixture_bootstrap_to_meta_and_root_seconds": ready_seconds,
            "source_copy_seconds_within_fixture": seed_seconds,
            "health": response.json(),
            "source_events": events,
            "scope": (
                "Existing fixture, TS normal main.ts, inherited listener, test mode. "
                "Includes schema bootstrap and source copy."
            ),
        }
    finally:
        stop_started = time.perf_counter()
        _stop_server(handle)
        print(f"UNIT21_STARTUP_SERVER_STOP seconds={time.perf_counter() - stop_started:.3f}")


def test_large_record_rebuild_reports_initialization_and_event_loop_cost(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = time.perf_counter()
    database = tmp_path / "synthetic.sqlite"
    config = tmp_path / "config.yaml"
    config.write_text("{}\n")
    env = _environment(tmp_path, database, config)
    _seed(database)
    before_bytes = database.stat().st_size
    input_path = tmp_path / "request.json"
    input_path.write_text(
        json.dumps(
            {
                "root": str(tmp_path),
                "database_path": str(database),
                "config_path": str(config),
            }
        )
    )
    companion = ROOT / "server/conversation/record/testing/rebuild_measurement.ts"
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {readFileSync} from 'node:fs'; "
            "const {measure_record_rebuild}=await import(process.argv[1]); "
            "const input=JSON.parse(readFileSync(process.argv[2],'utf8')); "
            "const result=await measure_record_rebuild(input); "
            "process.stdout.write(JSON.stringify(result));",
            companion.as_uri(),
            str(input_path),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=930,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    interim = request.config.getoption("--compare-report-dir")
    if interim:
        target = Path(interim)
        target.mkdir(parents=True, exist_ok=True)
        (target / "unit-21-record-rebuild-probe.json").write_text(
            json.dumps(actual, indent=2, sort_keys=True) + "\n"
        )
    turns = CONVERSATIONS * TURNS_PER_CONVERSATION
    assert actual["source"] == {
        "conversations": CONVERSATIONS,
        "events": turns * EVENTS_PER_TURN,
        "event_kinds": {
            kind: turns
            for kind in (
                "prompt",
                "tool_call_started",
                "tool_call_finished",
                "message_to_owner",
                "turn_ended",
            )
        },
    }
    assert actual["projection"] == {
        "state": CONVERSATIONS,
        "rows": turns * 4,
        "turns": turns,
        "groups": turns,
        "lookups": turns,
    }
    assert actual["incomplete_conversations"] == 0
    assert actual["event_loop_delay_ms"]["samples"] > 0
    assert actual["heartbeat"]["samples"] > 0
    server_startup = _server_startup(tmp_path / "startup", database, monkeypatch)
    report = {
        "kind": "measurement",
        "case": "unit-21-record-rebuild",
        "complete": True,
        "measurement": actual,
        "normal_test_mode_server_startup": server_startup,
        "database_before_bytes": before_bytes,
        "database_after_bytes": database.stat().st_size,
        "seconds_including_seed": time.perf_counter() - started,
        "scope": (
            "Production record runtime initialization and rebuild, not whole-server HTTP readiness."
        ),
        "limits": [
            "Synthetic completed turns only. No live data, production provider, "
            "or concurrent external writer.",
            "The 10ms readiness poll adds read work on the measured event loop.",
            "All-views-current includes preceding production pacing. "
            "Close interrupts the final pacing wait.",
            "This reports timings without a performance acceptance threshold or optimization.",
        ],
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        target = Path(destination)
        target.mkdir(parents=True, exist_ok=True)
        (target / "unit-21-record-rebuild.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n"
        )
    print(json.dumps(report, sort_keys=True))

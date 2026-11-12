"""Explicit Python baseline for the Unit 21 large conversation rebuild note.

Run explicitly, not through default CI discovery. Reuse the exact 200k-event
fixture and the production Python second-connection rebuilder without optimization.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import platform
import sqlite3
import subprocess
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from tests.compare.direct.unit21_rebuild_measurement import ROOT, _environment, _seed

import planner.conversation.views.rebuild as rebuild_module
from planner.conversation.views.contracts import VIEW_PROJECTION_VERSION
from planner.conversation.views.rebuild import (
    out_of_date_conversation_ids,
    rebuild_out_of_date_views,
)
from planner.conversation.workspace_admission import WorkspaceAdmission
from planner.core.db import connect


def event_source_digest(database: Path) -> str:
    digest = hashlib.sha256()
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        for row in connection.execute(
            "SELECT conversation_id,sequence,kind,payload,created_at "
            "FROM conversation_events ORDER BY conversation_id,sequence"
        ):
            digest.update(json.dumps(tuple(row), separators=(",", ":")).encode())
            digest.update(b"\n")
    return digest.hexdigest()


def copy_database(source: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(destination)
    with (
        sqlite3.connect(f"file:{source}?mode=ro", uri=True) as incoming,
        sqlite3.connect(destination) as outgoing,
    ):
        incoming.backup(outgoing)


async def python_baseline(database: Path) -> dict[str, Any]:
    admission = WorkspaceAdmission()
    original_rebuild_one = rebuild_module.rebuild_one_conversation_view
    conversation_samples: list[dict[str, Any]] = []

    def observed_rebuild_one(db_path: str, busy_timeout_ms: int, conversation_id: str) -> None:
        started = time.perf_counter()
        print(f"PYTHON_REBUILD_BEGIN {conversation_id}", flush=True)
        try:
            original_rebuild_one(db_path, busy_timeout_ms, conversation_id)
        except BaseException as error:
            print(
                f"PYTHON_REBUILD_FAILED {conversation_id} {type(error).__name__}: {error}",
                flush=True,
            )
            raise
        finally:
            elapsed = time.perf_counter() - started
            conversation_samples.append({"conversation_id": conversation_id, "seconds": elapsed})
            print(f"PYTHON_REBUILD_END {conversation_id} seconds={elapsed:.6f}", flush=True)

    rebuild_module.rebuild_one_conversation_view = observed_rebuild_one
    before = out_of_date_conversation_ids(str(database), 5000)
    assert len(before) == 20
    heartbeat_samples = 0
    heartbeat_gap = 0.0
    heartbeat_stop = asyncio.Event()

    async def heartbeat() -> None:
        nonlocal heartbeat_gap, heartbeat_samples
        previous = time.perf_counter()
        while not heartbeat_stop.is_set():
            await asyncio.sleep(0.005)
            current = time.perf_counter()
            heartbeat_gap = max(heartbeat_gap, current - previous)
            heartbeat_samples += 1
            previous = current

    # Keep the application writer connection open while the real rebuilder opens
    # its private second connection in a worker thread, exactly as production does.
    with closing(connect(str(database), 5000)) as application_connection:
        started = time.perf_counter()
        cpu_started = time.process_time()
        beat = asyncio.create_task(heartbeat())
        rebuild = asyncio.create_task(rebuild_out_of_date_views(str(database), 5000, admission))
        try:
            async with asyncio.timeout(1800):
                while True:
                    incomplete = application_connection.execute(
                        "SELECT COUNT(*) FROM conversations c "
                        "LEFT JOIN conversation_view_state v "
                        "ON v.conversation_id=c.conversation_id "
                        "WHERE c.latest_sequence>0 AND (v.conversation_id IS NULL "
                        "OR v.projection_version!=? OR v.through_sequence!=c.latest_sequence)",
                        (VIEW_PROJECTION_VERSION,),
                    ).fetchone()[0]
                    if incomplete == 0:
                        all_current = time.perf_counter()
                        break
                    if rebuild.done():
                        await rebuild
                        raise AssertionError(f"Python rebuild ended with {incomplete} stale views")
                    await asyncio.sleep(0.010)
                # Production includes one final pacing wait after the last view.
                # Report it separately rather than compare it with TS close(),
                # which interrupts that final wait in the original measurement.
                await rebuild
            finished = time.perf_counter()
            cpu_seconds = time.process_time() - cpu_started
        finally:
            rebuild_module.rebuild_one_conversation_view = original_rebuild_one
            heartbeat_stop.set()
            await beat
            if not rebuild.done():
                rebuild.cancel()
                await asyncio.gather(rebuild, return_exceptions=True)
        counts = {
            table: application_connection.execute(
                f"SELECT COUNT(*) FROM conversation_view_{table}"
            ).fetchone()[0]
            for table in ("state", "rows", "turns", "groups", "lookups")
        }
        source = {
            "conversations": application_connection.execute(
                "SELECT COUNT(*) FROM conversations"
            ).fetchone()[0],
            "events": application_connection.execute(
                "SELECT COUNT(*) FROM conversation_events"
            ).fetchone()[0],
        }
        assert counts == {
            "state": 20,
            "rows": 160_000,
            "turns": 40_000,
            "groups": 40_000,
            "lookups": 40_000,
        }
        assert source == {"conversations": 20, "events": 200_000}
        assert out_of_date_conversation_ids(str(database), 5000) == []
        return {
            "python_version": platform.python_version(),
            "sqlite_version": sqlite3.sqlite_version,
            "projection_version": VIEW_PROJECTION_VERSION,
            "conversation_rebuilds": conversation_samples,
            "all_views_current_ms": (all_current - started) * 1000,
            "production_task_finished_ms": (finished - started) * 1000,
            "final_pacing_ms": (finished - all_current) * 1000,
            "process_cpu_ms": cpu_seconds * 1000,
            "heartbeat": {
                "interval_ms": 5,
                "samples": heartbeat_samples,
                "max_gap_ms": heartbeat_gap * 1000,
            },
            "source": source,
            "projection": counts,
            "incomplete_conversations": 0,
            "source_digest": event_source_digest(database),
            "scope": (
                "Production rebuild_out_of_date_views, real connect() second connection "
                "on a worker thread, application writer held open."
            ),
            "limits": [
                "Synthetic completed turns only. "
                "No concurrent domain writers or production provider.",
                "The 10ms completion poll uses the held application connection and adds read work.",
                "Python connect() keeps its production commit participants. "
                "The prior standalone TS record probe omits global commit steps.",
                "All-current excludes the final pacing wait, while task-finished includes it.",
            ],
        }


def test_python_large_rebuild_baseline(request: pytest.FixtureRequest, tmp_path: Path) -> None:
    report_path = request.config.getoption("--compare-report-dir")
    root = Path(report_path).resolve() if report_path else tmp_path
    root.mkdir(parents=True, exist_ok=True)
    data = root / "data"
    data.mkdir(exist_ok=True)
    source = data / "source.sqlite"
    if not source.exists():
        _seed(source)
    digest = event_source_digest(source)
    prepared = data / "prepared-source.sqlite"
    copy_database(source, prepared)
    epoch = str(uuid.uuid4())
    with sqlite3.connect(prepared) as connection:
        connection.execute("INSERT INTO chief_page(singleton) VALUES(1)")
        connection.execute(
            "INSERT INTO navigation_meta VALUES(1,?,7,?,0)", (str(uuid.uuid4()), epoch)
        )
    assert event_source_digest(prepared) == digest
    source = prepared
    database = data / "python.sqlite"
    copy_database(source, database)
    report = asyncio.run(python_baseline(database))
    assert report["source_digest"] == digest
    result = {
        "kind": "measurement",
        "case": "unit-21-python-rebuild-baseline",
        "complete": True,
        "source_digest": digest,
        "setup_prerequisites": [
            "INSERT INTO chief_page(singleton) VALUES(1)",
            "INSERT INTO navigation_meta VALUES(1,?,7,?,0) with fresh UUID scope and epoch",
        ],
        "measurement": report,
    }
    (root / "unit-21-python-rebuild-baseline.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(result, sort_keys=True))


def test_typescript_profile_after_python_baseline(
    request: pytest.FixtureRequest, tmp_path: Path
) -> None:
    report_path = request.config.getoption("--compare-report-dir")
    root = Path(report_path).resolve() if report_path else tmp_path
    baseline = json.loads((root / "unit-21-python-rebuild-baseline.json").read_text())
    assert baseline["complete"] is True
    source = root / "data/prepared-source.sqlite"
    python_current = root / "data/python.sqlite"
    assert event_source_digest(source) == baseline["source_digest"]
    companion = ROOT / "server/conversation/record/testing/rebuild_profile.ts"
    for mode in ("reuse-check", "navigation-startup", "record-rebuild"):
        run_root = root / mode
        run_root.mkdir()
        database = run_root / "synthetic.sqlite"
        copy_database(source if mode == "record-rebuild" else python_current, database)
        assert event_source_digest(database) == baseline["source_digest"]
        config = run_root / "config.yaml"
        config.write_text("{}\n")
        input_path = run_root / "request.json"
        input_path.write_text(
            json.dumps(
                {
                    "root": str(run_root),
                    "database_path": str(database),
                    "config_path": str(config),
                    "mode": mode,
                }
            )
        )
        env = _environment(run_root, database, config)
        started = time.perf_counter()
        output = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                "import {readFileSync} from 'node:fs'; "
                "const {profile_record_rebuild}=await import(process.argv[1]); "
                "const input=JSON.parse(readFileSync(process.argv[2],'utf8')); "
                "process.stdout.write(JSON.stringify(await profile_record_rebuild(input)));",
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
        assert output.returncode == 0, output.stderr
        actual = json.loads(output.stdout)
        after_source_digest = event_source_digest(database)
        assert after_source_digest == baseline["source_digest"]
        result = {
            "kind": "measurement",
            "case": f"unit-21-{mode}",
            "complete": True,
            "source_digest": baseline["source_digest"],
            "measurement": actual,
            "process_seconds": time.perf_counter() - started,
            "source_digest_after": after_source_digest,
        }
        destination = root / f"unit-21-{mode}.json"
        destination.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        assert actual["projection_current"] is True
        assert actual["replayed_events"] == (200_000 if mode == "record-rebuild" else 0)
        if mode != "record-rebuild":
            assert actual["projection_rows_digest_before"] == actual["projection_rows_digest_after"]
        print(json.dumps(result, sort_keys=True), flush=True)


def test_stale_view_route_answers(request: pytest.FixtureRequest, tmp_path: Path) -> None:
    """Call production Python route functions with a read-only synthetic runtime."""
    from types import SimpleNamespace
    from typing import cast

    from fastapi import HTTPException

    from planner.conversation.runtime_composition import ConversationRuntime
    from planner.conversation.views import api as views_api

    report_path = request.config.getoption("--compare-report-dir")
    root = Path(report_path).resolve() if report_path else tmp_path
    database = root / "data/prepared-source.sqlite"
    assert database.exists()
    started = time.perf_counter()
    answers: dict[str, Any] = {}

    async def probe(connection: sqlite3.Connection) -> None:
        async def read(operation: Any) -> Any:
            return operation(connection)

        async def stopped(_conversation_id: str) -> bool:
            return False

        async def no_ids(_conversation_id: str) -> tuple[str, ...]:
            return ()

        runtime = cast(
            ConversationRuntime,
            SimpleNamespace(
                reads=SimpleNamespace(run=read),
                system=SimpleNamespace(
                    is_running=stopped,
                    pending_permission_ask_ids=no_ids,
                    pending_user_input_request_ids=no_ids,
                ),
            ),
        )
        identity = "synthetic-000"
        reference = json.dumps(
            {
                "body_id": "row:1",
                "version": "1:5",
                "byte_count": 0,
                "media_type": "application/json",
            }
        )
        calls: dict[str, Callable[[], Awaitable[Any]]] = {
            "snapshot": lambda: views_api.conversation_view(identity, runtime, "full", True),
            "pages": lambda: views_api.conversation_view_page(
                identity, runtime, "full", None, None, "older"
            ),
            "receipts": lambda: views_api.conversation_view_receipts(identity, runtime, "[]"),
            "tail": lambda: views_api.conversation_view_tail(
                identity, runtime, "full", True, None, VIEW_PROJECTION_VERSION, "[]"
            ),
            "body": lambda: views_api.conversation_view_body(identity, runtime, reference, 0),
            "body_content": lambda: views_api.conversation_view_body_content(
                identity, runtime, reference
            ),
        }
        for name, call in calls.items():
            try:
                value = await call()
                answers[name] = {
                    "status": 200,
                    "items": len(value["items"]) if name == "pages" else None,
                }
            except HTTPException as error:
                answers[name] = {"status": error.status_code, "detail": error.detail}

    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        assert connection.execute("SELECT COUNT(*) FROM conversation_view_state").fetchone()[0] == 0
        asyncio.run(probe(connection))
    assert (
        answers["snapshot"]
        == answers["receipts"]
        == answers["tail"]
        == {
            "status": 409,
            "detail": "Conversation view is upgrading",
        }
    )
    assert answers["pages"] == {"status": 200, "items": 0}
    assert (
        answers["body"]
        == answers["body_content"]
        == {
            "status": 422,
            "detail": "View row is absent",
        }
    )
    report = {
        "case": "unit-21-stale-view-route-answers",
        "source_events": 200_000,
        "missing_view_rows": 20,
        "answers": answers,
        "seconds": time.perf_counter() - started,
        "scope": (
            "Production Python route functions, read-only synthetic database, "
            "stopped runtime facts; no HTTP host or provider."
        ),
    }
    (root / "unit-21-stale-view-route-answers.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(report, sort_keys=True))

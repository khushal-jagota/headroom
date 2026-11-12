"""Compare a real failed terminal save and its transient wait on both cores."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.compare.direct.test_unit18_adapter import ROOT, environment, normalize, snapshot
from tests.compare.scripted.scripted_child import ScriptedChild

from planner.conversation.backends.contracts import BackendEventSink
from planner.conversation.contracts import ConversationBackendKey, ConversationStartRequest
from planner.conversation.live_tail import ConversationLiveTail
from planner.conversation.message_content import text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.db import connect, create_schema
from planner.stalls.registry import ProcessWaitRegistry


async def python_wait(path: Path) -> dict[str, Any]:
    armed = False
    attempts = 0
    registry = ProcessWaitRegistry(lambda: 100000)

    def participant(*_args: Any, **_kwargs: Any) -> None:
        nonlocal attempts
        if armed:
            attempts += 1
            if attempts <= 2:
                raise RuntimeError("unit15b save fault")

    store = ConversationStore(
        str(path), integer_now=lambda: 100, transaction_participant=participant
    )
    children: list[ScriptedChild] = []

    def factory(*, event_sink: BackendEventSink, **_kwargs: Any) -> ScriptedChild:
        child = ScriptedChild(event_sink)
        children.append(child)
        return child

    core = SqliteProcessConversationSystem(
        store=store,
        message_files=ConversationMessageFiles(str(path)),
        backend_child_factories={key: factory for key in ConversationBackendKey},
        live_tail=ConversationLiveTail(),
        unix_time_now=lambda: 100,
    )
    checkpoints: list[dict[str, Any]] = []

    def capture(name: str) -> None:
        checkpoints.append(
            {
                "name": name,
                "waits": [asdict(wait) for wait in registry.snapshot()],
                "rows": snapshot(str(path)),
            }
        )

    with patch("planner.conversation.storage.WAITS", registry):
        try:
            await core.start_conversation(
                ConversationStartRequest(
                    conversation_id="wait-proof", model="a-model", workspace_folder=Path("/tmp")
                )
            )
            await core.send(
                "wait-proof",
                text_message_content("work"),
                sender_label="Owner",
                sender_message_id="wait-work",
            )
            armed = True
            finish = asyncio.create_task(children[0].finish())
            for failure in [1, 2]:
                async with asyncio.timeout(5):
                    while (
                        not registry.snapshot()
                        or registry.snapshot()[0].detail != f"Stuck at write {failure}"
                    ):
                        await asyncio.sleep(0.002)
                capture(f"failure-{failure}")
            await finish
            await core.wait_until_quiescent()
            capture("saved")
            return {"checkpoints": checkpoints, "attempts": attempts}
        finally:
            await core.shutdown()
            for child in children:
                await child.terminate()


def test_unit15b_save_wait(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    selected = environment(tmp_path)
    py_db, ts_db = tmp_path / "python.sqlite", tmp_path / "typescript.sqlite"
    with patch.dict(os.environ, selected, clear=True):
        connection = connect(str(py_db))
        try:
            create_schema(connection)
            connection.execute(
                "INSERT INTO conversation_runtime_epoch VALUES (1, 'unit15b-runtime')"
            )
            connection.commit()
            with sqlite3.connect(ts_db) as target:
                connection.backup(target)
        finally:
            connection.close()
        python = asyncio.run(asyncio.wait_for(python_wait(py_db), 15))
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {run_wait} from './server/conversation/testing/unit15b_wait.ts'; "
            "console.log(JSON.stringify(await run_wait(process.argv[1])));",
            str(ts_db),
        ],
        cwd=ROOT,
        env=selected,
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    typescript = json.loads(result.stdout)
    report = {"python": python, "typescript": typescript, "seconds": time.perf_counter() - started}
    output = Path(request.config.getoption("--compare-report-dir") or tmp_path / "report")
    output.mkdir(parents=True, exist_ok=True)
    (output / "unit15b-wait.json").write_text(json.dumps(report, indent=2))
    assert normalize(typescript) == normalize(python)
    assert python["attempts"] == 3
    for index in [0, 1]:
        checkpoint = python["checkpoints"][index]
        assert checkpoint["waits"] == [
            {
                "kind": "vps_save_retrying",
                "wait_key": "wait-proof",
                "conversation_id": "wait-proof",
                "detail": f"Stuck at write {index + 1}",
                "started_at_ms": 100000,
            }
        ]
        assert not any(
            row["kind"] == "turn_ended" for row in checkpoint["rows"]["conversation_events"]
        )
    final = python["checkpoints"][-1]
    assert final["waits"] == []
    assert sum(row["kind"] == "turn_ended" for row in final["rows"]["conversation_events"]) == 1

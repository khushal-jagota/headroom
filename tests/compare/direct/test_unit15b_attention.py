"""Prove automatic prompt provenance through real core attention participants.

The prior summary proof used prewritten events. This case feeds two scripted
turns through the real core, including its held queue, before it reads a summary.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from tests.compare.direct.test_unit18_adapter import environment, normalize, snapshot
from tests.compare.scripted.scripted_child import ScriptedChild

from planner.attention.actions import read_summary
from planner.attention.contracts import SummaryRequest
from planner.conversation.api import delivery_fate_json
from planner.conversation.contracts import ConversationBackendKey, ConversationStartRequest
from planner.conversation.message_content import text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect, create_schema
from planner.tickets.contracts import TITLE_MAX_CHARS
from planner.tickets.data import create_ticket

ROOT = Path(__file__).resolve().parents[3]
START = int(datetime(2026, 11, 2, tzinfo=ZoneInfo("Europe/Berlin")).timestamp())


def attention_rows(path: str) -> dict[str, Any]:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return {
            table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1,2")]
            for table in ("attention_history_metadata",)
        }


async def exercise(database: str, ticket: str, workspace: str) -> dict[str, Any]:
    now = START - 86400
    store = ConversationStore(database, integer_now=lambda: now)
    children: list[ScriptedChild] = []

    def factory(*, event_sink: Any, **_: Any) -> ScriptedChild:
        child = ScriptedChild(event_sink)
        children.append(child)
        return child

    core = SqliteProcessConversationSystem(
        store=store,
        backend_child_factories={key: factory for key in ConversationBackendKey},
        message_files=ConversationMessageFiles(database),
        unix_time_now=lambda: now,
        monotonic_now=lambda: float(now),
    )
    try:
        await core.start_conversation(
            ConversationStartRequest(
                conversation_id="attention-system",
                model="model",
                workspace_folder=Path(workspace),
            )
        )
        conn = connect(database)
        try:
            conn.execute(
                "UPDATE tickets SET conversation_id='attention-system' WHERE id=?", (ticket,)
            )
            conn.commit()
        finally:
            conn.close()
        now = START + 100
        fates = []
        for text in ("Run the next worker step", "A queued worker step"):
            fates.append(
                delivery_fate_json(
                    await core.send(
                        "attention-system",
                        text_message_content(text),
                        sender_label="Panels loop",
                    )
                )
            )
        for _ in range(2):
            await children[0].finish()
            await core.wait_until_quiescent()
        conn = connect(database)
        try:
            summary = read_summary(
                conn, SummaryRequest(date(2026, 11, 2), date(2026, 11, 2)), now=START + 86401
            )
        finally:
            conn.close()
        return {
            "fates": fates,
            "native_text": (await children[0].inspect())["delivered_text"],
            "rows": snapshot(database),
            "attention": attention_rows(database),
            "summary": summary,
        }
    finally:
        await core.shutdown()
        for child in children:
            await child.terminate()


def test_unit15b_agent_only_attention(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    selected = environment(tmp_path)
    py_db, ts_db = tmp_path / "python.sqlite", tmp_path / "typescript.sqlite"
    with patch.dict(os.environ, selected, clear=True):
        conn = connect(str(py_db))
        try:
            create_schema(conn)
            ticket = create_ticket(
                conn,
                title="Agent-only attention",
                principal=OWNER_PRINCIPAL,
                now=START - 86400,
                title_max_chars=TITLE_MAX_CHARS,
                worker_type="coding",
            )
            conn.execute("UPDATE attention_history_metadata SET started_at=?", (START - 86400,))
            conn.commit()
            with sqlite3.connect(ts_db) as target:
                conn.backup(target)
        finally:
            conn.close()
        python = asyncio.run(exercise(str(py_db), ticket.id, str(tmp_path)))
    spec = {"database": str(ts_db), "ticket": ticket.id, "workspace": str(tmp_path), "start": START}
    input_path = tmp_path / "attention-input.json"
    input_path.write_text(json.dumps(spec))
    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {readFileSync} from 'node:fs'; import {pathToFileURL} from 'node:url'; "
            "const {exercise}=await import(pathToFileURL(process.argv[2]).href); "
            "process.stdout.write(JSON.stringify(await exercise("
            "JSON.parse(readFileSync(process.argv[1],'utf8')))));",
            str(input_path),
            str(ROOT / "server/conversation/testing/unit15b_attention.ts"),
        ],
        cwd=ROOT,
        env=selected,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    typescript = json.loads(completed.stdout)
    expected, actual = normalize(python), normalize(typescript)
    report = {
        "python": python,
        "typescript": typescript,
        "compared_python": expected,
        "compared_typescript": actual,
        "seconds": time.perf_counter() - started,
        "source_test": "test_system_prompt_provenance_keeps_agent_only_attention_share_usable",
        "boundary": (
            "Two real-core scripted turns, queued delivery, actual area participants "
            "and populated attention summary"
        ),
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        output = Path(destination)
        output.mkdir(parents=True, exist_ok=True)
        (output / "unit15b-attention.json").write_text(json.dumps(report, indent=2) + "\n")
    assert actual == expected
    for result in (python, typescript):
        assert result["fates"] == [
            {"fate": "started"},
            {"fate": "queued", "queue_position": 1},
        ]
        assert result["native_text"] == [
            "Run the next worker step",
            "A queued worker step",
        ]
        prompts = [
            json.loads(row["payload"])
            for row in result["rows"]["conversation_events"]
            if row["kind"] == "prompt"
        ]
        assert len(prompts) == 2
        assert all(
            prompt["system_generated"] is True and prompt.get("sender") is None
            for prompt in prompts
        )
        figures = result["summary"]["totals"]["current"]
        assert figures["owner_messages"]["value"] == 0
        assert figures["owner_messages"]["coverage_status"] == "complete"
        assert figures["active_ticket_count"]["value"] == 1
        assert figures["without_owner_involvement_share"]["value"] == 1
        assert result["attention"]["attention_history_metadata"]
        assert all(
            result["rows"][table]
            for table in (
                "conversation_lifecycle_facts",
                "conversation_view_rows",
                "conversation_view_turns",
            )
        )

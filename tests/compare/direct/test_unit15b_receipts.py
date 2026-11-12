"""Fresh Python/TS receipt comparison through the shared scripted child.

The distinct risks are uncertain review acknowledgement and concurrent attachment
identity replay. Existing ordinary steer/text deduplication lacks addressed read
bounds and attachment materialization. No HTTP host or provider is added.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from tests.compare.direct.test_unit18_adapter import environment, normalize, snapshot
from tests.compare.scripted.scripted_child import ScriptedChild

from planner.conversation.api import SentFilePiece, conversation_message_content, delivery_fate_json
from planner.conversation.backends.contracts import BackendEventSink
from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationStartRequest,
    PromptDeliveryFate,
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.live_tail import ConversationLiveTail
from planner.conversation.message_content import MessageContent, MessageFile, text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect, create_schema

ROOT = Path(__file__).resolve().parents[3]


async def python_receipts(path: Path) -> dict[str, Any]:
    store = ConversationStore(str(path), integer_now=lambda: 100)
    files = ConversationMessageFiles(str(path))
    children: dict[str, ScriptedChild] = {}

    def factory(
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> ScriptedChild:
        child = ScriptedChild(event_sink)
        children[resolved_start.conversation_id] = child
        return child

    core = SqliteProcessConversationSystem(
        store=store,
        backend_child_factories={k: factory for k in ConversationBackendKey},
        message_files=files,
        live_tail=ConversationLiveTail(),
        unix_time_now=lambda: 100,
        monotonic_now=lambda: 100,
    )
    worker = Principal(PrincipalKind.ticket, "worker")
    checkpoints: list[dict[str, Any]] = []

    async def capture(name: str, identity: str, fates: list[Any]) -> None:
        checkpoints.append(
            {
                "name": name,
                "fates": fates,
                "rows": snapshot(str(path)),
                "native": [await children[identity].inspect()],
            }
        )

    async def addressed(identity: str, text: str) -> int:
        await core.record_message_to_owner(
            identity,
            text_message_content(text),
            sender_label="Worker",
            sender=worker,
            recipient=OWNER_PRINCIPAL,
        )
        record = await store.read_conversation(identity)
        assert record is not None
        return record.latest_sequence

    try:
        identity = "uncertain"
        await core.start_conversation(
            ConversationStartRequest(
                conversation_id=identity, model="model", workspace_folder=Path("/tmp")
            )
        )
        await core.send(identity, text_message_content("busy"), sender_label="system")
        displayed = await addressed(identity, "displayed")
        await children[identity].configure(
            "steer", {"result": {"fate": "uncertain", "composed_content_delivered": True}}
        )
        fate = await core.send(
            identity,
            text_message_content("reply"),
            sender_label="owner",
            sender=OWNER_PRINCIPAL,
            recipient=worker,
            sender_message_id="uncertain-review",
            mode=PromptDeliveryMode.steer,
            owner_read_through_sequence=displayed,
        )
        await capture("uncertain", identity, [delivery_fate_json(fate)])
        identity = "attachment"
        await core.start_conversation(
            ConversationStartRequest(
                conversation_id=identity, model="model", workspace_folder=Path("/tmp")
            )
        )
        bound = await addressed(identity, "displayed")
        body = SentFilePiece(
            piece="file",
            data="cmVwbHkgYXR0YWNobWVudA==",
            media_type="text/plain",
            file_name="reply.txt",
        )

        async def content() -> MessageContent:
            return await conversation_message_content(
                files, identity, [body], sender_message_id="attachment-review"
            )

        async def attempt() -> PromptDeliveryFate:
            return await core.send(
                identity,
                await content(),
                sender_label="owner",
                sender=OWNER_PRINCIPAL,
                recipient=worker,
                sender_message_id="attachment-review",
                owner_read_through_sequence=bound,
            )

        fates = await asyncio.gather(attempt(), attempt())
        await capture("attachment-concurrent", identity, [delivery_fate_json(f) for f in fates])
        bound = await addressed(identity, "arrived after the lost response")
        retry = await attempt()
        await capture("attachment-retry", identity, [delivery_fate_json(retry)])
        piece = (await content())[0]
        assert isinstance(piece, MessageFile)
        attachment_bytes = (await files.read(identity, piece.stored_file_id)).hex()
        body.data = "ZGlmZmVyZW50IHJlcGx5IGF0dGFjaG1lbnQ="
        conflict = None
        try:
            await attempt()
        except ValueError as error:
            conflict = str(error)
        await capture("attachment-conflict", identity, [])
        return {
            "checkpoints": checkpoints,
            "attachment_bytes": attachment_bytes,
            "stored_file_id": piece.stored_file_id,
            "conflict": conflict,
        }
    finally:
        await core.shutdown()
        await asyncio.gather(*(child.terminate() for child in children.values()))


def test_unit15b_uncertain_and_attachment_receipts(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    selected = environment(tmp_path)
    py_dir, ts_dir = tmp_path / "python", tmp_path / "typescript"
    py_dir.mkdir()
    ts_dir.mkdir()
    py_db, ts_db = py_dir / "test.sqlite", ts_dir / "test.sqlite"
    with patch.dict(os.environ, selected, clear=True):
        conn = connect(str(py_db))
        try:
            create_schema(conn)
            conn.commit()
            with sqlite3.connect(ts_db) as target:
                conn.backup(target)
        finally:
            conn.close()
        python = asyncio.run(asyncio.wait_for(python_receipts(py_db), 30))
    spec = tmp_path / "receipts-input.json"
    spec.write_text(json.dumps({"database": str(ts_db)}))
    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {readFileSync} from 'node:fs'; import {pathToFileURL} from 'node:url'; "
            "const {run_receipts}=await import(pathToFileURL(process.argv[2]).href); "
            "process.stdout.write(JSON.stringify(await "
            "run_receipts(JSON.parse(readFileSync(process.argv[1],'utf8')))));",
            str(spec),
            str(ROOT / "server/conversation/testing/unit15b_receipts.ts"),
        ],
        cwd=ROOT,
        env=selected,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    typescript = cast(dict[str, Any], json.loads(completed.stdout))
    expected, actual = normalize(python), normalize(typescript)
    report = {"python": expected, "typescript": actual, "seconds": time.perf_counter() - started}
    directory = request.config.getoption("--compare-report-dir")
    if directory:
        output = Path(directory)
        output.mkdir(parents=True, exist_ok=True)
        (output / "unit-15b.receipts-direct.json").write_text(json.dumps(report, indent=2) + "\n")
    assert expected == actual
    assert all(f["fate"] == "started" for f in expected["checkpoints"][1]["fates"])
    assert expected["checkpoints"][2]["fates"][0]["fate"] == "started"
    assert expected["attachment_bytes"] == b"reply attachment".hex()
    assert "different message" in expected["conflict"]
    points = {p["name"]: p for p in expected["checkpoints"]}
    assert points["uncertain"]["fates"][0]["fate"] == "uncertain"
    assert points["uncertain"]["rows"]["conversations"][0]["owner_read_through_sequence"] == 0
    for name in ["attachment-concurrent", "attachment-retry", "attachment-conflict"]:
        point = points[name]
        record = next(
            r for r in point["rows"]["conversations"] if r["conversation_id"] == "attachment"
        )
        assert record["owner_read_through_sequence"] == 1
        assert len([c for c in point["native"][0]["calls"] if c["command"] == "write_prompt"]) == 1

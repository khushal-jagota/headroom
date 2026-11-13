"""One populated exchange proves ordered batches, file negotiation and receipt retry custody."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from tests.compare.direct.test_unit22_mac_http import CHILD, encoded, typed
from tests.compare.direct.test_unit22_mac_spool import _receipt, _rows
from tests.support.synthetic_homes import create_synthetic_homes

from planner.executor.mac_outbox import MacOutbox, OutboxEntry
from planner.executor.protocol import EnvelopeKind

ROOT = Path(__file__).resolve().parents[3]


def test_unit22_mac_outbox_sender(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reference, candidate = tmp_path / "reference", tmp_path / "candidate"
    artifact = reference / "artifacts/tickets/ticket/file.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("immutable snapshot")
    box = MacOutbox(reference / "spool")
    child: Any = CHILD
    event: Any = dict(
        child=child,
        turn=None,
        method="agent_message_completed",
        arguments={"content": [{"text": "complete"}]},
    )
    for identifier in ("a1", "a2"):
        box.add(OutboxEntry("conversation", EnvelopeKind.backend_event, event, identifier))
    box.add(
        OutboxEntry(
            "conversation",
            EnvelopeKind.cli_write,
            dict(child=child, turn=None, operation="worker.recap", arguments={"text": "saved"}),
            "a-write",
        ),
        ticket_id="ticket",
    )
    box.add(OutboxEntry("conversation", EnvelopeKind.child_stopped, dict(child=child), "a-stop"))
    other = {**event, "child": {**CHILD, "conversation_id": "other", "child_id": "other-child"}}
    box.add(OutboxEntry("other", EnvelopeKind.backend_event, other, "b1"))
    box.close()
    shutil.copytree(reference, candidate)
    box = MacOutbox(reference / "spool")
    calls: dict[str, list[str]] = {}
    replies: dict[str, list[str | None]] = {}
    uploaded: list[list[str]] = []
    confirmed: list[list[Any]] = []

    async def exchange() -> None:
        drained = asyncio.Event()

        def confirmation(receipt: Any) -> None:
            row = box.lookup(receipt.submission_id)
            confirmed.append([receipt.submission_id, row is None or row.receipt is not None])
            if box.pending_count() == 0:
                drained.set()

        monkeypatch.setattr(box, "_confirmed", confirmation)

        async def post_items(conversation_id: str, batch: Any) -> Any:
            calls.setdefault(conversation_id, []).append(encoded("ItemBatch", batch))
            responses = replies.setdefault(conversation_id, [])
            if conversation_id == "other" and not responses:
                responses.append(None)
                raise OSError("offline")
            items = list(batch.items)
            missing = next(
                (i for i, item in enumerate(items) if item.envelope.file_dependencies), None
            )
            if uploaded:
                missing = None
            accepted = items if missing is None else items[:missing]
            receipts = [_receipt(box, item.envelope.submission_id, True) for item in accepted]
            stop = (
                None
                if missing is None
                else dict(
                    sequence=items[missing].envelope.sequence,
                    reason="missing_files",
                    message="upload",
                    missing_files=items[missing].envelope.file_dependencies,
                )
            )
            reply = typed("ItemBatchReply", dict(receipts=receipts, stop=stop))
            responses.append(encoded("ItemBatchReply", reply))
            return reply

        async def upload(request: Any, path: Path) -> None:
            uploaded.append(
                [
                    encoded("FileUploadRequest", request),
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                ]
            )

        task = asyncio.create_task(
            box.send_pending(
                cast(Any, SimpleNamespace(post_items=post_items)),
                cast(Any, SimpleNamespace(upload=upload)),
            )
        )
        try:
            await asyncio.wait_for(drained.wait(), 10)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(exchange())
    finally:
        box.close()
    spec = tmp_path / "sender.json"
    spec.write_text(json.dumps(dict(root=str(candidate), replies=replies)))
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("PLAN_")
    }
    environment.update(create_synthetic_homes(tmp_path))
    environment.update(PLAN_TEST_MODE="1", PLAN_SERVER_URL="http://127.0.0.1:9")
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import{readFileSync}from'node:fs'; const{deliver}=await import(process.argv[1]); "
            "const input=JSON.parse(readFileSync(process.argv[2],'utf8')); "
            "console.log(JSON.stringify(await deliver(input)));",
            (ROOT / "server/executor/mac/testing/outbox_sender_parity.ts").as_uri(),
            str(spec),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == dict(
        calls=calls, uploaded=uploaded, confirmed=sorted(confirmed)
    )
    assert _rows(candidate) == _rows(reference)

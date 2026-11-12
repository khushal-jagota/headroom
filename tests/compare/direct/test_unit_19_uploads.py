"""Chunk transfer, failure rows, bytes, and expiry match Python without a host.

Existing adoption coverage starts from a ready row. This direct case proves the
intake state machine before that boundary, including repeated chunk bytes and an
interrupted transfer retry. Files are at most sixty bytes and media is recorded.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import time
from collections.abc import AsyncIterator
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.conversation.video_uploads import config
from planner.conversation.video_uploads import service as source
from planner.conversation.video_uploads.contracts import (
    ReserveVideoUpload,
    UploadDestination,
    VideoUploadError,
)
from planner.core.db import connect, create_schema

ROOT = Path(__file__).resolve().parents[3]
LIMITS = {
    "max_video_bytes": 64,
    "video_budget_bytes": 100,
    "free_space_floor_bytes": 100,
    "upload_ttl_seconds": 10,
    "transfer_chunk_bytes": 4,
    "progress_record_bytes": 8,
}
DESTINATION = UploadDestination(kind="chief", id="chief", expected_conversation_id=None)


def operations() -> list[dict[str, Any]]:
    return [
        {"op": "reserve", "name": "ready", "filename": "clip.mp4", "byte_count": 8},
        {"op": "transfer", "name": "ready", "chunks": ["61626364", "61626364"]},
        {"op": "status", "name": "ready"},
        {"op": "reserve", "name": "interrupted", "filename": "clip.mp4", "byte_count": 8},
        {"op": "transfer", "name": "interrupted", "chunks": ["61626364"], "interrupted": True},
        {"op": "transfer", "name": "interrupted", "chunks": ["61626364", "61626364"]},
        {"op": "reserve", "name": "short", "filename": "clip.mp4", "byte_count": 8},
        {"op": "transfer", "name": "short", "chunks": ["61626364"]},
        {"op": "reserve", "name": "rejected", "filename": "rejected.mp4", "byte_count": 4},
        {"op": "transfer", "name": "rejected", "chunks": ["61626364"]},
        {"op": "reserve", "name": "oversized", "filename": "clip.mp4", "byte_count": 4},
        {"op": "transfer", "name": "oversized", "chunks": ["6162636461626364"]},
        {"op": "reserve", "name": "large", "filename": "clip.mp4", "byte_count": 60},
        {"op": "reserve", "name": "quota", "filename": "clip.mp4", "byte_count": 40},
        {"op": "cancel", "name": "large"},
        {"op": "reserve", "name": "too-big", "filename": "clip.mp4", "byte_count": 65},
        {"op": "reserve", "name": "filename", "filename": "../clip.mp4", "byte_count": 4},
        {"op": "free", "name": "low-space", "value": 105},
        {"op": "reserve", "name": "low-space", "filename": "clip.mp4", "byte_count": 8},
        {"op": "reserve", "name": "disk-filled", "filename": "clip.mp4", "byte_count": 4},
        {
            "op": "transfer",
            "name": "disk-filled",
            "chunks": ["6162", "6364"],
            "free_after_chunk": 101,
        },
        {"op": "free", "name": "restore-space", "value": 1000},
        {"op": "reserve", "name": "cancelled", "filename": "clip.mp4", "byte_count": 4},
        {"op": "transfer", "name": "cancelled", "chunks": []},
        {"op": "cancel", "name": "cancelled"},
        {"op": "transfer", "name": "cancelled", "chunks": ["61626364"]},
        {"op": "reserve", "name": "pinned", "filename": "clip.mp4", "byte_count": 4},
        {"op": "transfer", "name": "pinned", "chunks": ["61626364"]},
        {"op": "admit", "name": "pinned"},
        {"op": "cancel", "name": "pinned"},
        {"op": "now", "name": "expire", "value": 1010},
        {"op": "cleanup", "name": "expire"},
        {"op": "status", "name": "ready"},
        {"op": "status", "name": "pinned"},
    ]


async def python_answers(path: Path, steps: list[dict[str, Any]]) -> dict[str, Any]:
    now = 1000
    free = 1000
    requires = 0
    references: dict[str, str] = {}
    names: dict[str, str] = {}
    probe_calls: list[dict[str, Any]] = []
    progress_calls: list[dict[str, Any]] = []
    chunk_observations: list[dict[str, Any]] = []
    service = source.VideoUploadService(path)
    original_record_progress = service._record_progress

    def record_progress(upload_id: str, received: int) -> None:
        progress_calls.append({"upload": names[upload_id], "received_bytes": received})
        original_record_progress(upload_id, received)

    def require_probe() -> None:
        nonlocal requires
        requires += 1

    async def probe(path: Path, filename: str) -> str:
        probe_calls.append(
            {"upload": names[path.name], "filename": filename, "bytes": path.read_bytes().hex()}
        )
        if filename == "rejected.mp4":
            raise VideoUploadError("Synthetic media rejection.")
        return "video/mp4"

    def bytes_at(path: Path | None) -> str | None:
        return path.read_bytes().hex() if path is not None and path.exists() else None

    def snapshot() -> dict[str, Any]:
        with closing(connect(str(path))) as conn:
            rows = [
                dict(row)
                for row in conn.execute("SELECT * FROM conversation_video_uploads ORDER BY rowid")
            ]
        files: dict[str, Any] = {}
        for row in rows:
            target = (
                None
                if row["conversation_id"] is None
                else path.parent
                / "files"
                / "conversations"
                / row["conversation_id"]
                / row["stored_file_id"]
            )
            files[names[row["upload_id"]]] = {
                "temporary": bytes_at(service.root / row["upload_id"]),
                "adopted": bytes_at(target),
                "metadata": bytes_at(
                    None if target is None else target.with_name(target.name + ".media-type")
                ),
            }
        return {
            "rows": [
                {
                    **row,
                    "upload_id": names[row["upload_id"]],
                    "stored_file_id": "stored:" + names[row["upload_id"]],
                }
                for row in rows
            ],
            "files": files,
        }

    answers: list[dict[str, Any]] = []
    with (
        patch.object(source, "require_probe", require_probe),
        patch.object(source, "probe_video", probe),
        patch.object(service, "_record_progress", record_progress),
        patch("planner.conversation.video_uploads.service.time.time", lambda: now),
        patch(
            "planner.conversation.video_uploads.service.shutil.disk_usage",
            lambda _: SimpleNamespace(free=free),
        ),
        patch.multiple(
            config,
            MAX_VIDEO_BYTES=64,
            VIDEO_BUDGET_BYTES=100,
            FREE_SPACE_FLOOR_BYTES=100,
            UPLOAD_TTL_SECONDS=10,
            TRANSFER_CHUNK_BYTES=4,
            PROGRESS_RECORD_BYTES=8,
        ),
    ):
        for step in steps:
            answer: dict[str, Any] | None = None
            try:
                name, op = step["name"], step["op"]
                upload_id = references.get(name, "")
                if op == "reserve":
                    result = service.reserve(
                        ReserveVideoUpload(
                            destination=DESTINATION,
                            filename=step["filename"],
                            byte_count=step["byte_count"],
                        )
                    )
                    references[name] = result.upload_id
                    names[result.upload_id] = name
                    answer = {**result.model_dump(), "upload_id": name}
                elif op == "transfer":

                    async def chunks(step: dict[str, Any] = step) -> AsyncIterator[bytes]:
                        nonlocal free
                        for chunk in step.get("chunks", []):
                            yield bytes.fromhex(chunk)
                            chunk_observations.append(
                                {
                                    "upload": step["name"],
                                    "received_bytes": service.status(
                                        references[step["name"]]
                                    ).received_bytes,
                                }
                            )
                            if "free_after_chunk" in step:
                                free = step["free_after_chunk"]
                        if step.get("interrupted"):
                            raise VideoUploadError("Synthetic interrupted stream.")

                    result = await service.transfer(upload_id, chunks())
                    answer = {**result.model_dump(), "upload_id": name}
                elif op == "cancel":
                    service.cancel(upload_id)
                elif op == "status":
                    answer = {**service.status(upload_id).model_dump(), "upload_id": name}
                elif op == "free":
                    free = step["value"]
                elif op == "now":
                    now = step["value"]
                elif op == "cleanup":
                    service.recover_and_cleanup()
                elif op == "admit":
                    checked = service.preflight(upload_id, DESTINATION, "sender:" + name)
                    answer = {
                        **asdict(checked),
                        "upload_id": name,
                        "destination": DESTINATION.model_dump(),
                    }
                answers.append(
                    {"name": name, "op": op, "status": 200, "answer": answer, **snapshot()}
                )
            except VideoUploadError as error:
                answers.append(
                    {
                        "name": name,
                        "op": op,
                        "status": error.status_code,
                        "answer": {"detail": str(error)},
                        **snapshot(),
                    }
                )
    return {
        "answers": answers,
        "probe_calls": probe_calls,
        "progress_calls": progress_calls,
        "chunk_observations": chunk_observations,
        "requires": requires,
    }


def test_unit19_upload_intake_rows_and_bytes(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    seed = tmp_path / "seed.sqlite"
    with synthetic_home_environment(homes), closing(connect(str(seed))) as conn:
        create_schema(conn)
    paths = [tmp_path / side / "data.sqlite" for side in ("python", "typescript")]
    for path in paths:
        path.parent.mkdir()
        with closing(sqlite3.connect(seed)) as src, closing(sqlite3.connect(path)) as dst:
            src.backup(dst)
    steps = operations()
    spec = {
        "db_path": str(paths[1]),
        "now": 1000,
        "free": 1000,
        "limits": LIMITS,
        "operations": steps,
    }
    with synthetic_home_environment(homes):
        expected = asyncio.run(python_answers(paths[0], steps))
        actual = json.loads(
            subprocess.check_output(
                [
                    "node",
                    "--input-type=module",
                    "-e",
                    "import {readFileSync} from 'node:fs'; "
                    "import {pathToFileURL} from 'node:url'; "
                    "const {run}=await import(pathToFileURL(process.argv[1]).href); "
                    "const spec=JSON.parse(readFileSync(0,'utf8')); "
                    "process.stdout.write(JSON.stringify(await run(spec)));",
                    str(ROOT / "server/conversation/video_uploads/testing/direct.ts"),
                ],
                cwd=ROOT,
                input=json.dumps(spec),
                text=True,
            )
        )
    report = {
        "kind": "direct",
        "case": "unit19-upload-intake",
        "equal": actual == expected,
        "python": expected,
        "typescript": actual,
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        folder = Path(destination)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "unit19-upload-intake.json").write_text(json.dumps(report, indent=2) + "\n")
    assert expected["answers"][1]["files"]["ready"]["temporary"] == "6162636461626364"
    assert expected["answers"][5]["status"] == 409
    assert expected["answers"][4]["rows"][1]["received_bytes"] == 0
    assert expected["progress_calls"][:2] == [
        {"upload": "ready", "received_bytes": 8},
        {"upload": "ready", "received_bytes": 8},
    ]
    assert expected["chunk_observations"][:2] == [
        {"upload": "ready", "received_bytes": 0},
        {"upload": "ready", "received_bytes": 8},
    ]
    disk_filled = next(
        answer
        for answer in expected["answers"]
        if answer["name"] == "disk-filled" and answer["op"] == "transfer"
    )
    assert disk_filled["status"] == 507
    assert disk_filled["answer"] == {"detail": "Video upload stopped: disk space is low."}
    assert (
        next(row for row in disk_filled["rows"] if row["upload_id"] == "disk-filled")["error"]
        == disk_filled["answer"]["detail"]
    )
    empty_body = next(
        answer
        for answer in expected["answers"]
        if answer["name"] == "cancelled" and answer["op"] == "transfer" and answer["status"] == 400
    )
    assert empty_body["answer"] == {
        "detail": "Video transfer was interrupted. Retry the attachment."
    }
    assert expected["answers"][-1]["answer"]["state"] == "admitted"
    assert actual == expected

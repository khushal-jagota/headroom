"""Fresh Python parity for the Codex JSONL wire, with one shared scripted child.

The new connected boundary is process stdio to each real client. No real provider,
HTTP host, database, service manager, or browser starts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from planner.conversation.backends.codex_app_server.client import (
    CodexAppServerClient,
    CodexRequestRejected,
    CodexWireFailed,
)

ROOT = Path(__file__).resolve().parents[3]


def _environment(root: Path, trace: Path) -> dict[str, str]:
    home = root / "home"
    home.mkdir(exist_ok=True)
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "HOME": str(home),
        "PLAN_SERVER_URL": "http://127.0.0.1:0",
        "CODEX_HOME": str(home / "codex"),
        "CLAUDE_CONFIG_DIR": str(home / "claude"),
        "HERMES_HOME": str(home / "hermes"),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_DATA_HOME": str(home / "data"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "TMPDIR": str(root),
        "UNIT17_TRACE": str(trace),
    }


def _warning(message: str) -> str:
    if "item/agentMessage/delta" in message:
        return "malformed notification"
    if "item/fileChange/requestApproval" in message:
        return "malformed request"
    if "JSON" in message:
        return "invalid JSON"
    return message


async def _python(spec: dict[str, Any]) -> dict[str, Any]:
    notifications: list[dict[str, Any]] = []
    ordering: list[str] = []
    requests: list[dict[str, Any]] = []
    ended = asyncio.Event()
    child_ended = 0

    class Handler:
        async def on_notification(self, method: str, notification: BaseModel) -> None:
            if getattr(notification, "delta", None) == "slow":
                ordering.append("handler-start")
                await asyncio.sleep(0.02)
                ordering.append("handler-end")
            notifications.append(
                {"method": method, "params": notification.model_dump()}
            )

        async def on_server_request(
            self, method: str, request_id: Any, params: BaseModel
        ) -> None:
            requests.append(
                {"method": method, "id": request_id, "params": params.model_dump()}
            )
            await client.respond(request_id, {"decision": "decline"})

        async def on_child_ended(self) -> None:
            nonlocal child_ended
            child_ended += 1
            ended.set()

    client = CodexAppServerClient(handler=Handler(), description="unit17-wire")
    try:
        await client.start(
            argv=(spec["executable"], "app-server"),
            environment=spec["environment"],
            working_directory=Path(spec["working_directory"]),
        )
        initialize = await client.request(
            "initialize",
            {
                "clientInfo": {"name": "unit17-proof", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        )
        await client.notify("initialized")
        first = await client.begin_request("wire/first", {"input": "first"})
        second = await client.begin_request("wire/second", {"input": "second"})
        replies = await asyncio.gather(
            client.finish_request("wire/first", first),
            client.finish_request("wire/second", second),
        )
        exchange = await client.request("wire/exchange", {})
        await client.request("wire/slow-handler", {})
        ordering.append("reply")
        usage = await client.request("account/rateLimits/read", {})
        rejection: dict[str, Any] | None = None
        try:
            await client.request("wire/reject", {})
        except CodexRequestRejected as error:
            rejection = {
                "method": error.method,
                "code": error.code,
                "message": str(error),
                "data": error.data,
                "request_id": error.request_id,
            }
        exit_failure: dict[str, Any] | None = None
        try:
            await client.request("wire/exit", {})
        except CodexWireFailed as error:
            exit_failure = {
                "message": str(error),
                "request_may_have_been_written": error.request_may_have_been_written,
            }
        await asyncio.wait_for(ended.wait(), timeout=5)
        return {
            "initialize": initialize,
            "replies": replies,
            "exchange": exchange,
            "usage": usage,
            "rejection": rejection,
            "exit_failure": exit_failure,
            "notifications": notifications,
            "ordering": ordering,
            "requests": requests,
            "child_ended": child_ended,
            "stderr": client.standard_error_tail(),
            "is_alive": client.is_alive,
            "trace": [
                json.loads(line)
                for line in Path(spec["trace"]).read_text().splitlines()
            ],
        }
    finally:
        await client.stop()


def test_unit17_wire(
    tmp_path: Path, request: pytest.FixtureRequest, caplog: pytest.LogCaptureFixture
) -> None:
    """Prove correlation, typed dispatch and death with one reused executable per side."""
    started = time.perf_counter()
    executable = tmp_path / "scripted_codex.ts"
    shutil.copy2(
        ROOT / "server/conversation/testing/codex/scripted_app_server.ts", executable
    )
    executable.chmod(0o700)
    trace = tmp_path / "wire.jsonl"
    environment = _environment(tmp_path, trace)
    spec = {
        "executable": str(executable),
        "environment": environment,
        "working_directory": str(tmp_path),
        "trace": str(trace),
    }
    with (
        patch.dict(os.environ, environment, clear=True),
        caplog.at_level(
            logging.WARNING,
            logger="planner.conversation.backends.codex_app_server",
        ),
    ):
        reference = asyncio.run(asyncio.wait_for(_python(spec), timeout=15))
    reference["warnings"] = [
        _warning(record.getMessage())
        for record in caplog.records
        if record.name == "planner.conversation.backends.codex_app_server"
    ]
    trace.unlink()
    input_path = tmp_path / "wire-input.json"
    input_path.write_text(json.dumps(spec))
    run = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {readFileSync} from 'node:fs'; "
            "import {pathToFileURL} from 'node:url'; "
            "const {run_wire_parity}=await import(pathToFileURL(process.argv[2]).href); "
            "process.stdout.write(JSON.stringify(await run_wire_parity("
            "JSON.parse(readFileSync(process.argv[1],'utf8')))));",
            str(input_path),
            str(ROOT / "server/conversation/testing/codex/wire_parity.ts"),
        ],
        env=environment,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    candidate = json.loads(run.stdout)
    assert candidate == reference
    assert reference["ordering"] == ["handler-start", "handler-end", "reply"]
    assert [reply["label"] for reply in candidate["replies"]] == ["first", "second"]
    assert len(candidate["notifications"]) == 2 and len(candidate["requests"]) == 1
    assert candidate["ordering"] == ["handler-start", "handler-end", "reply"]
    assert candidate["warnings"] == [
        "invalid JSON",
        "malformed notification",
        "malformed request",
    ]
    assert candidate["child_ended"] == 1 and not candidate["is_alive"]
    assert candidate["exit_failure"]["request_may_have_been_written"] is True
    directory = request.config.getoption("--compare-report-dir")
    if directory:
        report = Path(directory) / "unit-17-wire.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(
                {
                    "python": reference,
                    "typescript": candidate,
                    "elapsed_seconds": time.perf_counter() - started,
                    "differences": [],
                },
                indent=2,
            )
            + "\n"
        )

"""The lease wire preserves backlog, restore cursor, update offers and replacement refusal."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from tests.compare.direct.test_unit22_mac_http import CHILD, IDENTITY, encoded, typed
from tests.support.synthetic_homes import create_synthetic_homes

from planner.executor import mac_presence
from planner.executor.mac_outbox import OutboxBacklog
from planner.executor.protocol import WaitingConversation

ROOT = Path(__file__).resolve().parents[3]


def test_unit22_mac_leases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    update = dict(schema_version=1, target=None, prefetch=None)
    refusal = dict(
        peer_identity=IDENTITY,
        server_identity=IDENTITY,
        reason="replaced",
        refused_at_ms=1700000000000,
    )
    replies = [
        typed(
            "LeaseReply",
            dict(
                accepted_at_vps_ms=1700000000000,
                away_after_seconds=25,
                update=update,
                mailbox_high_sequence=50,
            ),
        ),
        typed(
            "LeaseRefused",
            dict(error={"code": "executor_protocol_unsupported"}, refusal=None, update=update),
        ),
        typed(
            "LeaseRefused",
            dict(error={"code": "executor_incarnation_not_current"}, refusal=refusal, update=None),
        ),
    ]
    calls: list[str] = []
    offers: list[str] = []
    lowered: list[list[int]] = []
    cursor, replaced = 100, False
    presence = mac_presence.MacPresence(typed("ExecutorIdentity", IDENTITY))
    working = typed(
        "WorkingTurn",
        dict(
            child=CHILD, turn=dict(conversation_id="conversation", turn_number=3), tool_running=True
        ),
    )
    runner = SimpleNamespace(admission_stopped=False, working_turns=lambda: (working,))
    backlog = OutboxBacklog(
        7,
        11,
        tuple(WaitingConversation(f"conversation-{index}", index + 1) for index in range(64)),
        True,
    )

    async def lease(request: Any) -> Any:
        nonlocal cursor
        calls.append(encoded("LeaseRequest", request))
        cursor += 1
        return replies[len(calls) - 1]

    def lower(high: int, *, cursor_at_lease: int) -> bool:
        lowered.append([high, cursor_at_lease])
        return True

    def replacement() -> None:
        nonlocal replaced
        replaced = True

    loop = mac_presence.MacLeaseLoop(
        client=cast(Any, SimpleNamespace(lease=lease)),
        presence=presence,
        outbox=cast(Any, SimpleNamespace(lease_backlog=lambda: backlog)),
        runner=cast(Any, runner),
        journal=cast(Any, SimpleNamespace(poll_cursor=lambda: cursor, lower_poll_cursor=lower)),
        updates=cast(
            Any,
            SimpleNamespace(offer=lambda value: offers.append(encoded("MacUpdateCheck", value))),
        ),
        agent_processes=None,
        replaced=replacement,
    )

    async def exchange() -> list[bool]:
        return [await loop.lease_once() for _ in range(3)]

    with monkeypatch.context() as patch:
        patch.setattr(mac_presence, "now_ms", lambda: 1700000000000)
        patch.setattr(os, "getpid", lambda: 7)
        expected = dict(
            calls=calls,
            offers=offers,
            lowered=lowered,
            keep_running=asyncio.run(exchange()),
            replaced=replaced,
        )
        status = json.loads(
            encoded("MacLocalStatus", presence.status(admission_stopped=runner.admission_stopped))
        )
    expected.update(status=status, failed=status.pop("last_lease_failure") is not None)
    spec = tmp_path / "leases.json"
    payload = dict(
        identity=encoded("ExecutorIdentity", presence.identity),
        working=encoded("WorkingTurn", working),
        replies=[
            encoded("LeaseReply" if index == 0 else "LeaseRefused", reply)
            for index, reply in enumerate(replies)
        ],
    )
    spec.write_text(json.dumps(payload))
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PLAN_")}
    environment.update(create_synthetic_homes(tmp_path))
    environment.update(PLAN_TEST_MODE="1", PLAN_SERVER_URL="http://127.0.0.1:9")
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import{readFileSync}from'node:fs'; const{leases}=await import(process.argv[1]); "
            "const input=JSON.parse(readFileSync(process.argv[2],'utf8')); "
            "console.log(JSON.stringify(await leases(input)));",
            (ROOT / "server/executor/mac/testing/leases_parity.ts").as_uri(),
            str(spec),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected

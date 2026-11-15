"""One populated exchange proves catalog policy above the existing mailbox proof."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import time
from contextlib import closing, suppress
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from tests.compare.direct.test_unit22_files import IDENTITY, ROOT
from tests.support.executor_presence import lease_request, make_current
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.conversation.contracts import ConversationBackendKey as Backend
from planner.conversation.snapshot import BackendModel, BackendSnapshot
from planner.core.db import connect, create_schema
from planner.executor import protocol as wire
from planner.executor.catalog_relay import VpsCatalogRelay
from planner.executor.vps_mailbox import SqliteCommandMailbox
from planner.executor.vps_presence import MacPresenceService
from planner.machines.contracts import ORIGINAL_MAC_MACHINE_ID


def test_catalog_machine_storage(tmp_path: Path) -> None:
    """Storage ownership uses the existing peer without protocol admission or a host."""
    homes = create_synthetic_homes(tmp_path)
    path, candidate = tmp_path / "source.sqlite", tmp_path / "candidate.sqlite"
    other = "44444444-4444-4444-8444-444444444444"
    snapshots = [BackendSnapshot(
        Backend.codex, True, None, str(index), None, (BackendModel(name),),
        (), name, None, None, (), True,
    ) for index, name in enumerate(("original", "named"))]

    async def reference() -> dict[str, Any]:
        values = []
        for machine_id, snapshot in zip((ORIGINAL_MAC_MACHINE_ID, other), snapshots, strict=True):
            mailbox = SqliteCommandMailbox(str(path), machine_id=machine_id)
            presence = MacPresenceService(str(path), mailbox, machine_id=machine_id)
            relay = VpsCatalogRelay(cast(Any, SimpleNamespace(
                db_path=str(path), machine_id=machine_id, owns_conversation=lambda _: False,
            )), presence, mailbox)
            try:
                values.append(wire.encode_value(relay.retained(Backend.codex)))
                relay._keep(Backend.codex, snapshot)
                values.append(wire.encode_value(relay.retained(Backend.codex)))
            finally:
                await relay.close()
                await presence.close()
                await mailbox.close()
        with closing(connect(str(path))) as conn:
            rows = [dict(row) for row in conn.execute(
                "SELECT * FROM machine_backend_snapshots ORDER BY machine_id,backend_key"
            )]
        return dict(values=values, rows=[
            {**row, "snapshot_json": json.loads(row["snapshot_json"])} for row in rows
        ])

    with synthetic_home_environment(homes), closing(connect(str(path))) as conn:
        create_schema(conn)
        conn.execute("INSERT INTO machines VALUES (?,'Other','laptop','blue',0)", (other,))
        conn.commit()
        with sqlite3.connect(candidate) as target:
            conn.backup(target)
        expected = asyncio.run(reference())
    run = subprocess.run(
        ["node", str(ROOT / "server/executor/testing/catalog.ts")],
        input=json.dumps(dict(scope_only=True, path=str(candidate), other_machine=other,
                             snapshots=[wire.as_object(value) for value in snapshots])),
        cwd=ROOT, env={**homes, "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                       "PLAN_SERVER_URL": "http://127.0.0.1:1"},
        text=True, capture_output=True, timeout=15, check=False,
    )
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == expected
    assert expected["values"][0] is expected["values"][2] is None
    assert len(expected["rows"]) == 2


@pytest.mark.xfail(
    run=False,
    reason=(
        "expected-open P2: #916 staging protocol24 source identity cannot pass TS22 "
        "catalogue mailbox admission"
    ),
)
def test_catalog_retention_refresh_and_owned_cancellation(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    began, now = time.perf_counter(), 1760000000000
    homes = create_synthetic_homes(tmp_path)
    path, candidate = tmp_path / "source.sqlite", tmp_path / "candidate.sqlite"
    with synthetic_home_environment(homes), closing(connect(str(path))) as conn:
        create_schema(conn)
        make_current(str(path), IDENTITY, now)
    with sqlite3.connect(path) as source, sqlite3.connect(candidate) as target:
        source.backup(target)
    complete = BackendSnapshot(
        Backend.codex,
        True,
        None,
        "1",
        None,
        (BackendModel("fixture", version=(123456789,)),),
        ("low",),
        "fixture",
        "low",
        None,
        (),
        True,
    )
    snapshots = [
        complete,
        replace(complete, version="2", available_models=(), models_complete=False),
        replace(complete, backend_key=Backend.claude),
        replace(complete, version="3"),
    ]
    spec = dict(
        path=str(candidate),
        now=now,
        identity=wire.as_object(IDENTITY),
        lease=wire.as_object(lease_request(IDENTITY)),
        snapshots=[wire.as_object(value) for value in snapshots],
    )

    async def reference() -> dict[str, Any]:
        mailbox = SqliteCommandMailbox(str(path), clock=lambda: now)
        presence = MacPresenceService(str(path), mailbox, clock=lambda: now)
        runs = cast(
            Any,
            SimpleNamespace(
                db_path=str(path),
                machine_id=ORIGINAL_MAC_MACHINE_ID,
                owns_conversation=lambda _: False,
            ),
        )
        relay = VpsCatalogRelay(runs, presence, mailbox)
        cursor = 0
        flags: list[bool] = []

        async def reply(snapshot: Any, caller: Any = None) -> None:
            nonlocal cursor
            command = (await mailbox.poll(IDENTITY, cursor, 1)).commands[-1]
            cursor = command.command_sequence
            request = command.model_list_request
            assert request is not None
            flags.append(request.refresh)
            owned = relay._tasks[Backend.codex][1]
            if caller is not None:
                caller.cancel()
                with suppress(asyncio.CancelledError):
                    await caller
            await mailbox.record_results(
                IDENTITY,
                wire.CommandOutcomeReport(
                    (wire.CommandResult(command.command_id, model_list=wire.as_object(snapshot)),)
                ),
            )
            await owned

        try:
            cold = asyncio.create_task(relay.snapshot(Backend.codex))
            await reply(snapshots[0])
            first = await cold
            cached = await relay.snapshot(Backend.codex)
            refreshed = asyncio.create_task(relay.snapshot(Backend.codex, refresh=True))
            await reply(snapshots[1])
            await reply(snapshots[2])
            fallback = await refreshed
            caller = asyncio.create_task(relay.snapshot(Backend.codex, refresh=True))
            await reply(snapshots[3], caller)
            await relay.close()
            await presence.accept_lease(lease_request(IDENTITY, going_to_sleep=True))
            relay = VpsCatalogRelay(runs, presence, mailbox)
            offline = await relay.snapshot(Backend.codex)
            with closing(connect(str(path))) as conn:
                rows = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM machine_backend_snapshots ORDER BY machine_id,backend_key"
                    )
                ]
            for row in rows:
                row["snapshot_json"] = json.loads(row["snapshot_json"])
            return dict(
                values=[wire.as_object(value) for value in (first, cached, fallback, offline)],
                flags=flags,
                cancelled=caller.cancelled(),
                rows=rows,
            )
        finally:
            await relay.close()
            await presence.close()
            await mailbox.close()

    with synthetic_home_environment(homes):
        expected = asyncio.run(asyncio.wait_for(reference(), 10))
    env = {
        **homes,
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": str(tmp_path),
        "PLAN_TEST_MODE": "1",
        "PLAN_SERVER_URL": "http://127.0.0.1:0",
    }
    run = subprocess.run(
        ["node", str(ROOT / "server/executor/testing/catalog.ts")],
        input=json.dumps(spec),
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    actual = json.loads(run.stdout)
    assert actual.pop("sealed") == "ServerLifecycleError"
    assert actual.pop("aborted_waiting_rows") == [{"state": "withdrawn"}] * 4
    assert actual.pop("after_abort_poll") == 0
    assert actual == expected
    assert actual["flags"] == [False, False, True, True]
    assert actual["values"][-1]["available_models"][0]["version"] == [123456789]
    report = request.config.getoption("--compare-report-dir", default=None)
    if report:
        destination = Path(report)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "unit22-catalog.json").write_text(
            json.dumps(
                dict(seconds=time.perf_counter() - began, expected=expected, actual=actual),
                indent=2,
            )
        )

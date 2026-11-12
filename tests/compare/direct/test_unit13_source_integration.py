"""Producer-to-durable-prompt and collection-to-Goal proof on synthetic database copies.

The existing minimal durable conversation fixture is the backend boundary. This
adds no host or native provider and does not claim real-core callback admission.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import subprocess
import time
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from tests.support.measure_sources import NOW, configured_source
from tests.support.synthetic_homes import (
    create_synthetic_homes,
    synthetic_home_environment,
)
from tests.unit.test_manager_wakes import (
    _DurableStartedConversationSystem,
    _item_and_ticket,
)

from planner.conversation.contracts import ConversationSystem
from planner.core.clock import TestClock as FakeClock
from planner.core.config import load_config
from planner.core.contracts import (
    CHIEF_PRINCIPAL,
    OWNER_PRINCIPAL,
    Principal,
    PrincipalKind,
)
from planner.core.db import connect, create_schema
from planner.goals import actions as goals
from planner.manager_wakes import data as wakes
from planner.manager_wakes.runtime import deliver_batch
from planner.manager_wakes.contracts import WakeBatch
from planner.measure_sources import actions, run_actions
from planner.measure_sources.contracts import (
    FailureCode,
    RunFailure,
    RunMetrics,
    RunMode,
    RunResult,
)
from planner.measure_sources.registry import build_participants
from planner.measure_sources.schedule_loop import SourceScheduleLoop
from planner.measures.contracts import CandidateObservation

ROOT = Path(__file__).resolve().parents[3]
TABLES = {
    "measure_sources": "id",
    "measure_source_runs": "id",
    "measure_observations": "measure_id,date",
    "goals": "id",
    "measure_source_failure_episodes": "source_id,episode",
    "manager_wakes": "id",
    "manager_wake_batches": "id",
    "manager_wake_batch_members": "batch_id,wake_id",
}


def normalized(value: object) -> object:
    """Only minted conversation and sender identities vary across the two executions."""
    text = json.dumps(value)
    maps: dict[str, str] = {}

    def token(match: re.Match[str]) -> str:
        return maps.setdefault(match[0], f"<delivery-id-{len(maps)}>")

    return cast(object, json.loads(
        re.sub(r"manager_delivery_wake_[0-9a-f]+|conv_[0-9a-f]{32}", token, text)
    ))


def test_unit13_source_integration(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    seed = tmp_path / "seed.sqlite"
    with synthetic_home_environment(homes), closing(connect(str(seed))) as conn:
        create_schema(conn)
        clock = FakeClock(datetime.fromtimestamp(NOW, UTC))
        manager_id, _ = _item_and_ticket(conn, clock)
        source, measure = configured_source(
            conn,
            responsible=Principal(PrincipalKind.manager, manager_id),
            enabled=False,
        )
        goal = goals.create_goal(
            conn,
            {
                "project_id": "project_vylo",
                "title": "Collected milestone",
                "kind": "quantitative",
                "completion_rule": {
                    "actual": {
                        "op": "reference",
                        "measure_id": measure.id,
                        "day_offset": 0,
                    },
                    "target": {"op": "constant", "value": 10},
                    "direction": "atleast",
                    "first_date": "2026-10-27",
                },
                "primary_chart": {
                    "type": "chart",
                    "id": "progress",
                    "kind": "number",
                    "placement": "primary",
                    "measure_id": measure.id,
                },
                "page": {
                    "kind": "headroom.page",
                    "version": 1,
                    "title": "Collected evidence",
                    "summary": "",
                    "blocks": [],
                },
            },
            principal=OWNER_PRINCIPAL,
            now=NOW,
        )
        script = source.active_script
        assert script is not None
        runs = [
            run_actions.request_run(
                conn,
                source.id,
                mode=RunMode.collection,
                history=None,
                expected_script_revision=script.revision,
                principal=OWNER_PRINCIPAL,
                now=NOW,
            ).id
            for _ in range(7)
        ]
        target = tmp_path / "typescript.sqlite"
        with sqlite3.connect(target) as other:
            conn.backup(other)
        config = load_config(
            str(tmp_path / "missing.yaml"),
            env={
                "PLAN_DB_PATH": str(seed),
                "PLAN_TEST_MODE": "1",
                "PLAN_CREDENTIAL_KEY_PATH": str(tmp_path / "key"),
            },
        )
        loop = SourceScheduleLoop(
            str(seed), clock, participants=build_participants(config, clock)
        )
        system = _DurableStartedConversationSystem(seed)
        metrics = RunMetrics(0, 0, 0, 1, 0)
        snapshots: list[dict[str, object]] = []

        def snapshot(phase: str, outcome: object = None) -> None:
            snapshots.append(
                {
                    "phase": phase,
                    "outcome": outcome,
                    "tables": {
                        table: [
                            dict(row)
                            for row in conn.execute(
                                f"SELECT * FROM {table} ORDER BY {order}"
                            )
                        ]
                        for table, order in TABLES.items()
                    },
                    "prompts": [
                        json.loads(row[0])
                        for row in conn.execute(
                            "SELECT payload FROM conversation_events WHERE kind='prompt' ORDER "
                            "BY created_at,rowid"
                        )
                    ],
                }
            )

        def complete(index: int, value: float | None = None) -> None:
            run_actions.claim_run(conn, runs[index], now=NOW)
            result = (
                RunResult(
                    (), RunFailure(FailureCode.http_error, http_status=401), metrics
                )
                if value is None
                else RunResult(
                    (CandidateObservation(measure.id, "2026-10-27", value),),
                    None,
                    metrics,
                )
            )
            run_actions.complete_run(conn, runs[index], result, now=NOW)
            loop.poll_once()

        def claim() -> WakeBatch | None:
            return wakes.claim_next_batch(
                conn, process_token="unit13-source", now=NOW + 1
            )

        def offer(batch: WakeBatch, conversation: str) -> None:
            wakes.record_batch_offering(
                conn,
                batch.id,
                conversation_id=conversation,
                process_token="unit13-source",
                now=NOW + 2,
            )

        async def exercise() -> None:
            loop.poll_once()
            snapshot("initial")
            complete(0)
            batch = claim()
            assert batch is not None
            snapshot("producer-and-claim", asdict(batch))
            sent = await deliver_batch(
                batch,
                connect_database=lambda: connect(str(seed)),
                conversation_system=cast(ConversationSystem, system),
                process_token="unit13-source",
                now=lambda: NOW + 2,
            )
            snapshot("durable-prompt-and-delivered-receipt", sent)
            complete(1)
            snapshot("same-episode-deduplicates", claim())
            complete(2, 12.0)
            snapshot("collection-achieves-goal")
            complete(3)
            closed = claim()
            assert closed is not None
            offer(closed, "unit13-not-offered-closure")
            complete(4, 3.0)
            with conn:
                admitted = wakes.admit_notice_batch(
                    conn,
                    conversation_id="unit13-not-offered-closure",
                    sender_message_id=closed.sender_message_id,
                    now=NOW + 3,
                )
            snapshot("episode-closure-discards-and-correction-reopens-goal", admitted)
            complete(5)
            transferred = claim()
            assert transferred is not None
            offer(transferred, "unit13-not-offered-transfer")
            actions.set_responsible(
                conn,
                source.id,
                CHIEF_PRINCIPAL,
                expected_responsibility_revision=1,
                principal=OWNER_PRINCIPAL,
                now=NOW + 3,
            )
            with conn:
                admitted = wakes.admit_notice_batch(
                    conn,
                    conversation_id="unit13-not-offered-transfer",
                    sender_message_id=transferred.sender_message_id,
                    now=NOW + 4,
                )
            snapshot("responsibility-transfer-discards-old-batch", admitted)
            chief = claim()
            assert chief is not None
            snapshot("replacement-recipient-claim", asdict(chief))
            sent = await deliver_batch(
                chief,
                connect_database=lambda: connect(str(seed)),
                conversation_system=cast(ConversationSystem, system),
                process_token="unit13-source",
                now=lambda: NOW + 5,
            )
            snapshot("replacement-recipient-durable-prompt", sent)
            complete(6, 15.0)
            snapshot("later-collection-restores-goal")

        asyncio.run(exercise())
        expected = normalized(snapshots)
    spec = tmp_path / "input.json"
    spec.write_text(
        json.dumps(
            {
                "db_path": str(target),
                "now": NOW,
                "source_id": source.id,
                "measure_id": measure.id,
                "goal_id": goal.id,
                "runs": runs,
            }
        )
    )
    actual = json.loads(
        subprocess.check_output(
            [
                "node",
                "--input-type=module",
                "-e",
                (
                    "import {readFileSync} from 'node:fs'; import {pathToFileURL} from "
                    "'node:url'; const {run}=await "
                    "import(pathToFileURL(process.argv[2]).href); "
                    "process.stdout.write(JSON.stringify(await "
                    "run(JSON.parse(readFileSync(process.argv[1],'utf8')))));"
                ),
                str(spec),
                str(ROOT / "server/measure_sources/testing/integration.ts"),
            ],
            cwd=ROOT,
            text=True,
        )
    )
    actual = normalized(actual)
    report = {
        "kind": "direct",
        "case": "unit13-source-integration",
        "equal": actual == expected,
        "python": expected,
        "typescript": actual,
        "seconds": time.perf_counter() - started,
        "boundary": (
            "Canonical producer, batch admission, minimal durable conversation backend, "
            "and shared source clock. No native real-core callback claim."
        ),
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        folder = Path(destination)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "unit13-source-integration.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
    assert actual == expected

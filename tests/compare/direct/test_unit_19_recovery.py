"""Compare recovery evidence and receipts on identical synthetic source rows.

Core tests without deployment participants cannot prove these source transitions.
This reuses the direct-check host boundary and starts no server or provider.
The separate real-core comparison must still prove actual prompt delivery.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from tests.support.synthetic_homes import (
    create_synthetic_homes,
    synthetic_home_environment,
)
from tests.support.ticket_workflows import preset_workflow_json

from planner.conversation.deploy_interruptions import data
from planner.conversation.deploy_interruptions.contracts import (
    CapturedTurn,
    CaptureManifest,
)
from planner.conversation.lifecycle import (
    LifecycleFact,
    LifecycleKind,
    LifecycleOperation,
    LifecycleStatus,
)
from planner.core.db import connect, create_schema

ROOT = Path(__file__).resolve().parents[3]


def test_unit19_recovery_receipts(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    manifest = CaptureManifest(
        "deploy",
        "capture",
        99,
        "captured",
        None,
        (
            CapturedTurn(
                "c",
                1,
                None,
                "old-epoch",
                "running",
                "Synthetic work",
                "ticket",
                "t",
                7,
                "out",
            ),
        ),
    )
    homes = create_synthetic_homes(tmp_path)
    seed = tmp_path / "seed.sqlite"
    with synthetic_home_environment(homes), closing(connect(str(seed))) as conn:
        create_schema(conn)
        conn.execute(
            "INSERT INTO "
            "conversations(conversation_id,backend_key,workspace_folder,access,created_at) "
            "VALUES ('c','codex','/synthetic','full',0)"
        )
        conn.execute(
            "INSERT INTO conversation_lifecycle_facts VALUES "
            "('c','turn','1',1,'turn','running',NULL)"
        )
        conn.execute("INSERT INTO conversation_runtime_epoch VALUES (1,'old-epoch')")
        conn.execute(
            "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
            "field_values,created_at,updated_at,conversation_id,worker_step_claim,"
            "worker_step_claim_revision,workflow_json) VALUES "
            "('t','Work','coding','codex','needs_implementation','needs_implementation',"
            "'{}',0,0,'c','out',7,?)",
            (preset_workflow_json(conn),),
        )
        conn.commit()
    expected = []
    cases = []
    for status in ("started", "refused", "suppressed", "unknown", "restart"):
        paths = [
            tmp_path / f"{status}-{side}.sqlite" for side in ("python", "typescript")
        ]
        for path in paths:
            with (
                closing(sqlite3.connect(seed)) as source,
                closing(sqlite3.connect(path)) as target,
            ):
                source.backup(target)
        with synthetic_home_environment(homes), closing(connect(str(paths[0]))) as conn:
            data.reconcile(conn, (manifest,), 100)
            selected = data.select_recoveries(conn)
            notice = data.bind_delivery(
                conn,
                "c",
                2,
                selected["c"],
                {ref.interruption_id: "sender" for ref in selected["c"]},
            )
            assert data.admit_recovery(conn, "c", selected["c"], 100) is None
            if status == "restart":
                data.reconcile(conn, (manifest,), 101)
            else:
                data.settle_delivery(
                    conn,
                    "c",
                    (
                        LifecycleFact(
                            LifecycleKind.attempt,
                            "prompt:2",
                            2,
                            LifecycleOperation.prompt,
                            LifecycleStatus(status),
                            None,
                        ),
                    ),
                    now=101,
                )
            expected.append(
                {
                    "selected": {
                        key: [asdict(ref) for ref in refs]
                        for key, refs in selected.items()
                    },
                    "notice": notice,
                    "accounts": data.read_summary(conn),
                    "deliveries": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT * FROM deploy_interruption_deliveries ORDER BY "
                            "interruption_id,conversation_id,turn_number"
                        )
                    ],
                    "eligible": {
                        key: [asdict(ref) for ref in refs]
                        for key, refs in data.select_recoveries(conn).items()
                    },
                    "epoch_rotated": conn.execute(
                        "SELECT epoch FROM conversation_runtime_epoch"
                    ).fetchone()[0]
                    != "old-epoch",
                    "claims": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT "
                            "id,worker_step_claim,worker_step_claim_revision FROM "
                            "tickets ORDER BY id"
                        )
                    ],
                }
            )
        cases.append({"path": str(paths[1]), "status": status})
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"manifest": asdict(manifest), "cases": cases}))
    with synthetic_home_environment(homes):
        actual = json.loads(
            subprocess.check_output(
                [
                    "node",
                    "--input-type=module",
                    "-e",
                    "import {readFileSync} from 'node:fs'; import {pathToFileURL} "
                    "from 'node:url'; const {run}=await "
                    "import(pathToFileURL(process.argv[2]).href); "
                    "process.stdout.write(JSON.stringify(run(JSON.parse(readFileSync(process.argv[1],'utf8')))));",
                    str(spec),
                    str(
                        ROOT
                        / "server/conversation/deploy_interruptions/testing/direct.ts"
                    ),
                ],
                cwd=ROOT,
                text=True,
            )
        )
    report = {
        "kind": "direct",
        "case": "unit19-recovery-receipts",
        "equal": actual == expected,
        "python": expected,
        "typescript": actual,
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / "unit19-recovery-receipts.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
    assert actual == expected


def test_unit19_startup_manifest_and_lost_task_prompts(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Only real hosts prove that imported evidence reaches the scripted child's wire.

    Reuse the existing comparison host and its process fixture. Start the two
    hosts serially on copies of one tiny synthetic seed. Stop each exact owned
    process immediately after its observations. Record the measured cost.
    """
    import httpx
    from tests.support.server_fixtures import _start_server, _stop_server

    from planner.conversation.deploy_interruptions.files import write_manifest

    started = time.perf_counter()
    seed = tmp_path / "startup-seed.sqlite"
    home_storage = tmp_path / "seed-homes"
    home_storage.mkdir()
    homes = create_synthetic_homes(home_storage)
    with synthetic_home_environment(homes), closing(connect(str(seed))) as conn:
        create_schema(conn)
        for identity in ("recover", "background"):
            conn.execute(
                "INSERT INTO "
                "conversations(conversation_id,backend_key,model,"
                "workspace_folder,access,created_at) "
                "VALUES (?,'codex','model','/tmp','full',0)",
                (identity,),
            )
        conn.execute(
            "INSERT INTO conversation_lifecycle_facts VALUES "
            "('recover','turn','1',1,'turn','running',NULL)"
        )
        conn.execute(
            "INSERT INTO conversation_turn_occurrences VALUES "
            "('recover',1,'old-occurrence','old-epoch')"
        )
        conn.execute("INSERT INTO conversation_runtime_epoch VALUES (1,'old-epoch')")
        conn.execute(
            "INSERT INTO conversation_background_tasks VALUES "
            "('background','old-task','Synthetic background work')"
        )
        conn.commit()
    manifest = CaptureManifest(
        "deploy",
        "capture",
        99,
        "captured",
        None,
        (
            CapturedTurn(
                "recover",
                1,
                "old-occurrence",
                "old-epoch",
                "running",
                "Synthetic work",
                "unknown",
                None,
                None,
                None,
            ),
        ),
    )

    def seed_copy(target: Path) -> None:
        with (
            closing(sqlite3.connect(seed)) as source,
            closing(sqlite3.connect(target)) as destination,
        ):
            source.backup(destination)
        write_manifest(target, manifest)

    observations: list[dict[str, Any]] = []
    for side, module in (
        ("python", "tests.compare.host"),
        ("typescript", "tests.compare.ts_launcher"),
    ):
        server = _start_server(tmp_path / side, seed_db=seed_copy, server_module=module)
        try:
            wire = {}
            with httpx.Client(timeout=10) as client:
                for identity in ("recover", "background"):
                    deadline = time.monotonic() + 10
                    while True:
                        response = client.get(
                            server.base + f"/api/test/signals/{identity}/inspect"
                        )
                        assert response.status_code == 200, response.text
                        body = response.json()
                        children = body["backend"]["children"]
                        if children and children[-1]["delivered_text"]:
                            wire[identity] = children[-1]["delivered_text"]
                            break
                        assert time.monotonic() < deadline, body
                        time.sleep(0.05)
            with closing(
                sqlite3.connect(f"file:{server.db_path}?mode=ro", uri=True)
            ) as conn:
                conn.row_factory = sqlite3.Row
                tables = {
                    table: [
                        dict(row)
                        for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")
                    ]
                    for table in (
                        "deploy_interruption_accounts",
                        "deploy_interruptions",
                        "deploy_interruption_deliveries",
                        "conversation_lifecycle_facts",
                        "conversation_background_tasks",
                    )
                }
                events = [
                    {
                        "conversation_id": row["conversation_id"],
                        "sequence": row["sequence"],
                        "kind": row["kind"],
                        "payload": json.loads(row["payload"]),
                    }
                    for row in conn.execute(
                        "SELECT * FROM conversation_events ORDER BY conversation_id,sequence"
                    )
                ]
                observations.append({"wire": wire, "tables": tables, "events": events})
        finally:
            _stop_server(server)
    assert (
        "Panels deployment interruption context"
        in observations[0]["wire"]["recover"][0]
    )
    assert "Synthetic background work" in observations[0]["wire"]["background"][0]
    report = {
        "kind": "direct-connected",
        "case": "unit19-startup-recovery",
        "equal": observations[0] == observations[1],
        "python": observations[0],
        "typescript": observations[1],
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / "unit19-startup-recovery.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
    assert observations[0] == observations[1]

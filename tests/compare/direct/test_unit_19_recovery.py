"""Compare recovery evidence and receipts on identical synthetic source rows.

Core tests without deployment participants cannot prove these source transitions.
This reuses the direct-check host boundary and starts no server or provider.
The separate real-core comparison must still prove actual prompt delivery.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import time
from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
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


def _seed_recovery(seed: Path, homes: Any) -> None:
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


def _recovery_manifest() -> CaptureManifest:
    return CaptureManifest(
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


def test_unit19_recovery_receipts(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    manifest = _recovery_manifest()
    homes = create_synthetic_homes(tmp_path)
    seed = tmp_path / "seed.sqlite"
    _seed_recovery(seed, homes)
    expected = []
    cases = []
    for status in ("started", "refused", "suppressed", "unknown", "restart"):
        paths = [tmp_path / f"{status}-{side}.sqlite" for side in ("python", "typescript")]
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
                        key: [asdict(ref) for ref in refs] for key, refs in selected.items()
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
                    str(ROOT / "server/conversation/deploy_interruptions/testing/direct.ts"),
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
        (path / "unit19-recovery-receipts.json").write_text(json.dumps(report, indent=2) + "\n")
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
                        response = client.get(server.base + f"/api/test/signals/{identity}/inspect")
                        assert response.status_code == 200, response.text
                        body = response.json()
                        children = body["backend"]["children"]
                        if children and children[-1]["delivered_text"]:
                            wire[identity] = children[-1]["delivered_text"]
                            break
                        assert time.monotonic() < deadline, body
                        time.sleep(0.05)
            with closing(sqlite3.connect(f"file:{server.db_path}?mode=ro", uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                tables = {
                    table: [
                        dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")
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
    assert "Panels deployment interruption context" in observations[0]["wire"]["recover"][0]
    assert "Synthetic background work" in observations[0]["wire"]["background"][0]
    # Only the random suffix of this admission identity differs between processes.
    # Preserve every field and validate the conversation-bound identity before mapping it.
    import copy
    import re

    canonical = copy.deepcopy(observations)
    for observation in canonical:
        identities: dict[str, str] = {}
        for event in observation["events"]:
            sender_id = event["payload"].get("sender_message_id")
            if not isinstance(sender_id, str) or not sender_id.startswith("lost-background:"):
                continue
            prefix = f"lost-background:{event['conversation_id']}:"
            assert sender_id.startswith(prefix), sender_id
            assert re.fullmatch(r"[0-9a-f]{32}", sender_id[len(prefix):]), sender_id
            identities.setdefault(sender_id, f"{prefix}notice-{len(identities) + 1}")
            event["payload"]["sender_message_id"] = identities[sender_id]
    report = {
        "kind": "direct-connected",
        "case": "unit19-startup-recovery",
        "equal": canonical[0] == canonical[1],
        "python": observations[0],
        "typescript": observations[1],
        "canonical": {"python": canonical[0], "typescript": canonical[1]},
        "identity_rule": (
            "bijective UUID suffix tokens only for event sender_message_id "
            "lost-background:<same conversation>:<32 lowercase hex>"
        ),
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / "unit19-startup-recovery.json").write_text(json.dumps(report, indent=2) + "\n")
    assert canonical[0] == canonical[1]


@pytest.mark.parametrize("scenario", ["closure", "canceled-first", "paced", "already-running"])
def test_unit19_recovery_refused_during_update_keeps_pending_rows(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    scenario: str,
) -> None:
    """The pending loop releases admission and keeps populated durable recovery evidence."""
    from planner.conversation.contracts import (
        PromptDeliveryRefusalReason,
        PromptDeliveryRefused,
        PromptDeliveryUncertain,
    )
    from planner.conversation.deploy_interruptions.coordinator import recover_conversations
    from planner.conversation.start_admission import AutomaticStartAdmission
    from planner.conversation.start_admission.contracts import StartRequest
    from planner.conversation.system import SqliteProcessConversationSystem
    from planner.conversation.workspace_admission import WorkspaceAdmission
    from planner.machines.contracts import HOME_MACHINE_ID

    canceled_first = scenario == "canceled-first"
    paced = scenario == "paced"
    already_running = scenario == "already-running"
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    paths = [tmp_path / f"closure-{side}.sqlite" for side in ("python", "typescript")]
    _seed_recovery(paths[0], homes)
    if canceled_first or paced:
        with closing(sqlite3.connect(paths[0])) as conn:
            conn.execute(
                "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,"
                "created_at) VALUES ('c2','codex','/synthetic','full',0)"
            )
            conn.execute(
                "INSERT INTO conversation_lifecycle_facts VALUES "
                "('c2','turn','1',1,'turn','running',NULL)"
            )
            conn.execute(
                "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
                "field_values,created_at,updated_at,conversation_id,worker_step_claim,"
                "worker_step_claim_revision,workflow_json) SELECT 't2','Other work',worker_type,"
                "employee_backend,stage,ceiling,field_values,created_at,updated_at,'c2',"
                "worker_step_claim,worker_step_claim_revision,workflow_json "
                "FROM tickets WHERE id='t'"
            )
            if paced:
                conn.execute("DELETE FROM tickets WHERE id='t2'")
            conn.commit()
    with closing(sqlite3.connect(paths[0])) as source, closing(sqlite3.connect(paths[1])) as target:
        source.backup(target)
    manifest = _recovery_manifest()
    if canceled_first or paced:
        manifest = replace(
            manifest,
            deployment_id="sort-proof" if paced else manifest.deployment_id,
            turns=(
                *manifest.turns,
                replace(
                    manifest.turns[0],
                    conversation_id="c2",
                    owner_id=None if paced else "t2",
                    owner_kind="unknown" if paced else "ticket",
                    claim_revision=None if paced else 7,
                    claim=None if paced else "out",
                    display_name="Other work",
                ),
            ),
        )

    async def reference() -> dict[str, Any]:
        admission = WorkspaceAdmission()
        now = [0.0]
        starts = AutomaticStartAdmission(clock=lambda: now[0])
        if paced:
            starts.try_start(StartRequest("prior", "worker", HOME_MACHINE_ID))
            starts.finish("prior")
        trace = []
        for name in ("try_start", "finish", "finish_unbound", "uncertain"):
            native = getattr(starts, name)

            def observed(argument: Any, _name: str = name, _native: Any = native) -> Any:
                result = _native(argument)
                trace.append(
                    [
                        _name,
                        argument if isinstance(argument, str) else argument.identity,
                        None if isinstance(argument, str) else argument.work_class,
                        result,
                    ]
                )
                if _name == "try_start":
                    now[0] += 1
                return result

            setattr(starts, name, observed)
        offers = 0
        offered_with_admission = []
        offered_ids = []
        with closing(connect(str(paths[0]))) as conn:
            data.reconcile(conn, (manifest,), 100)
            conn.commit()

            async def select() -> Any:
                return data.select_recoveries(conn)

            async def is_running(_identity: str) -> bool:
                return already_running

            async def send(conversation_id: str, *_args: Any, **_options: Any) -> Any:
                nonlocal offers
                offers += 1
                offered_ids.append(conversation_id)
                offered_with_admission.append(admission.active)
                if paced:
                    return (
                        PromptDeliveryUncertain()
                        if conversation_id == "c2"
                        else PromptDeliveryRefused(PromptDeliveryRefusalReason.no_such_conversation)
                    )
                if canceled_first:
                    if offers == 1:
                        raise asyncio.CancelledError("one recovery canceled")
                    return
                admission.close_starts()
                raise RuntimeError("Workspace admission is closed for an owner update.")

            request_factory: Any = SqliteProcessConversationSystem.automatic_start_request

            async def automatic_start_request(identity: str, conversation_id: str) -> Any:
                return await request_factory(
                    SimpleNamespace(_store=SimpleNamespace(_db_path=str(paths[0]))),
                    identity,
                    conversation_id,
                )

            system = SimpleNamespace(
                workspace_admission=admission,
                is_running=is_running,
                send=send,
                start_admission=starts,
                automatic_start_request=automatic_start_request,
            )
            store: Any = SimpleNamespace(select_deployment_recoveries=select)

            def snapshot() -> dict[str, Any]:
                return {
                    "offers": offers,
                    "trace": trace,
                    "snapshots": [asdict(entry) for entry in starts.snapshots()],
                    "offered_with_admission": offered_with_admission,
                    "offered_ids": offered_ids,
                    "active": admission.active,
                    "starts_closed": admission.starts_closed,
                    "pending": {
                        key: [asdict(ref) for ref in refs]
                        for key, refs in data.select_recoveries(conn).items()
                    },
                    "rows": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT * FROM deploy_interruptions ORDER BY interruption_id"
                        )
                    ],
                    "deliveries": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT * FROM deploy_interruption_deliveries ORDER BY interruption_id"
                        )
                    ],
                    "claims": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT id,worker_step_claim,worker_step_claim_revision "
                            "FROM tickets ORDER BY id"
                        )
                    ],
                }

            await recover_conversations(system, store, startup=True)
            after_refusal = snapshot()
            if not canceled_first and not paced and not already_running:
                await recover_conversations(system, store, startup=True)
            return {"after_refusal": after_refusal, "after_denied_pass": snapshot()}

    with synthetic_home_environment(homes):
        expected = asyncio.run(reference())
        spec = tmp_path / "closure.json"
        spec.write_text(
            json.dumps(
                {
                    "path": str(paths[1]),
                    "manifest": asdict(manifest),
                    "canceled_first": canceled_first,
                    "paced": paced,
                    "already_running": already_running,
                }
            )
        )
        actual = json.loads(
            subprocess.check_output(
                [
                    "node",
                    "--input-type=module",
                    "-e",
                    "import{readFileSync}from'node:fs';import{pathToFileURL}from'node:url';"
                    "const{run_recovery_closure}=await import(pathToFileURL(process.argv[2]).href);"
                    "process.stdout.write(JSON.stringify(await run_recovery_closure("
                    "JSON.parse(readFileSync(process.argv[1],'utf8')))));",
                    str(spec),
                    str(ROOT / "server/conversation/deploy_interruptions/testing/direct.ts"),
                ],
                cwd=ROOT,
                text=True,
            )
        )
    first = expected["after_refusal"]
    assert first["offers"] == (0 if already_running else 2 if canceled_first or paced else 1)
    assert first["offered_with_admission"] == [True] * first["offers"]
    assert first["active"] is False
    if canceled_first or paced:
        assert set(first["offered_ids"]) == {"c", "c2"}
    if paced:
        assert list(first["pending"]) == ["c", "c2"]
        assert first["offered_ids"][0] == "c2"
        assert [row[3] for row in first["trace"] if row[0] == "try_start"] == [
            False,
            False,
            True,
            True,
        ]
        assert {row[0] for row in first["trace"]} >= {"uncertain", "finish"}
    elif already_running or canceled_first:
        assert any(row[0] == "finish_unbound" for row in first["trace"])
    assert len(expected["after_denied_pass"]["pending"]["c"]) == 1
    assert len(expected["after_denied_pass"]["rows"]) == (2 if canceled_first or paced else 1)
    assert expected["after_denied_pass"]["deliveries"] == []
    report = {
        "kind": "direct",
        "case": "unit19-recovery-" + scenario,
        "equal": actual == expected,
        "python": expected,
        "typescript": actual,
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        output = Path(destination)
        output.mkdir(parents=True, exist_ok=True)
        (output / ("unit19-recovery-" + scenario + ".json")).write_text(
            json.dumps(report, indent=2) + "\n"
        )
    assert actual == expected

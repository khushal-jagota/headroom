"""Regenerate Python answers and compare actual TS functions on SQLite copies.

Run after every staging merge from the Ticket worktree:
  scripts/dev pytest tests/compare/direct --compare-report-dir /tmp/worker-direct-proof
Requires that worktree's isolated Python and server dependencies. No saved oracle.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sqlite3
import subprocess
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.support.in_memory_conversation_system import InMemoryConversationSystem
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.conversation.contracts import ConversationStartRequest, PromptDeliveryFate
from planner.conversation.message_content import text_message_content
from planner.core.clock import TestClock as RuntimeTestClock
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.executor.configuration import MAC_AWAY_AFTER_SECONDS
from planner.machines.contracts import ORIGINAL_MAC_MACHINE_ID
from planner.managers import data as managers
from planner.runtime import conversation_start as start
from planner.runtime import worker_step_readiness as readiness
from planner.runtime.logic.conversation_start_resolution import ConversationStartValues
from planner.runtime.workspaces import register_workspace_root
from planner.tickets import data as tickets
from planner.tickets.contracts import Ticket

ROOT = Path(__file__).resolve().parents[3]

NOW = 1783159200
WALL = NOW * 1000
TABLES = (
    "tickets",
    "ticket_paired_stage_openers",
    "ticket_conversations",
    "agents",
    "conversations",
)


def plain(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {
            field.name: plain(getattr(value, field.name)) for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    return value


def rows(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    return {
        table: [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY 1,2")]
        for table in TABLES
    }


def new_ticket(conn: sqlite3.Connection, worker_type: str = "coding") -> Ticket:
    return tickets.create_ticket(
        conn,
        title="Parity Ticket",
        worker_type=worker_type,
        brief="Agreed brief",
        principal=OWNER_PRINCIPAL,
        now=NOW,
        title_max_chars=200,
        stated_ceiling="needs_what_changes" if worker_type == "coding" else "needs_stages",
        stated_holder=OWNER_PRINCIPAL,
    )


def compare(
    conn: sqlite3.Connection, root: Path, spec: dict[str, Any], python: Callable[[], dict[str, Any]]
) -> dict[str, Any]:
    ts_path = root / "typescript.sqlite"
    with sqlite3.connect(ts_path) as target:
        conn.backup(target)
    spec.update(db_path=str(ts_path), workspace=str(root), now=NOW, wall=WALL)
    input_path = root / "input.json"
    input_path.write_text(json.dumps(spec, default=str))
    expected = python()
    actual = json.loads(
        subprocess.check_output(
            [
                "node",
                "--input-type=module",
                "-e",
                "import {readFileSync} from 'node:fs'; "
                "import {pathToFileURL} from 'node:url'; "
                "const {run_worker_runtime_parity}=await import("
                "pathToFileURL(process.argv[2]).href); "
                "const result=await run_worker_runtime_parity("
                "JSON.parse(readFileSync(process.argv[1],'utf8'))); "
                "process.stdout.write(JSON.stringify(result));",
                str(input_path),
                str(ROOT / "server/tests/worker_runtime_parity.ts"),
            ],
            cwd=ROOT,
            text=True,
        )
    )
    if actual != expected:
        (root / "difference.json").write_text(
            json.dumps({"python": expected, "typescript": actual}, indent=2, default=str)
        )

        def differences(a: Any, b: Any, path: str = "") -> list[tuple[str, Any, Any]]:
            if type(a) is not type(b):
                return [(path, a, b)]
            if isinstance(a, dict):
                return [
                    item
                    for key in a.keys() | b.keys()
                    for item in differences(a.get(key), b.get(key), path + "." + key)
                ]
            if isinstance(a, list):
                return (
                    [(path + ".length", len(a), len(b))]
                    if len(a) != len(b)
                    else [
                        item
                        for i, (x, y) in enumerate(zip(a, b, strict=True))
                        for item in differences(x, y, path + f".{i}")
                    ]
                )
            return [] if a == b else [(path, a, b)]

        raise AssertionError(json.dumps(differences(expected, actual)[:8], indent=2, default=str))
    return expected


def check_decisions(conn: sqlite3.Connection, root: Path) -> dict[str, Any]:
    from planner.conversation.logic.conversation_start_resolution import (
        resolve_conversation_start_request,
    )
    from planner.conversation.storage import ConversationStore
    from planner.core.contracts import Principal, PrincipalKind
    from planner.tickets import decision_messages

    ticket = new_ticket(conn)
    asyncio.run(
        ConversationStore(str(root / "python.sqlite"), integer_now=lambda: NOW).create_conversation(
            resolve_conversation_start_request(
                ConversationStartRequest(
                    conversation_id="decision-parity-conversation",
                    model="gpt-6.1-sol",
                    workspace_folder=root,
                )
            )
        )
    )
    # Canonical decision writes require an existing session for attributed guidance.
    ticket = tickets.write_ticket_conversation_start(
        conn,
        ticket.id,
        conversation_id="decision-parity-conversation",
        backend="codex",
        model=ticket.employee_launch_model,
        reasoning_effort=ticket.employee_launch_reasoning_effort,
        now=NOW,
    )
    ticket = tickets.file_current_proposal(
        conn,
        ticket.id,
        body="Decision parity scope",
        principal=Principal(PrincipalKind.ticket, ticket.id),
        now=NOW,
    )

    def python() -> dict[str, Any]:
        tickets.reject_proposal(
            conn,
            ticket.id,
            message="Keep the attributed note.",
            principal=OWNER_PRINCIPAL,
            now=NOW,
            expected_proposal_revision=ticket.pending_proposal_revision,
            decision_id="parity-decision",
        )
        tickets.reject_proposal(
            conn,
            ticket.id,
            message="Keep the attributed note.",
            principal=OWNER_PRINCIPAL,
            now=NOW,
            expected_proposal_revision=ticket.pending_proposal_revision,
            decision_id="parity-decision",
        )
        revised = tickets.file_current_proposal(
            conn,
            ticket.id,
            body="Reviewed scope",
            principal=Principal(PrincipalKind.ticket, ticket.id),
            now=NOW,
        )
        tickets.accept_proposal(
            conn,
            ticket.id,
            field="what_changes",
            principal=OWNER_PRINCIPAL,
            now=NOW,
            next_ceiling="needs_implementation",
            next_holder=OWNER_PRINCIPAL,
            expected_proposal_revision=revised.pending_proposal_revision,
            message="Approuvé — café et 日本語.",
            decision_id="parity-approval",
        )
        conn.execute(
            "UPDATE ticket_decision_messages SET delivery_status='uncertain' WHERE ticket_id=?",
            (ticket.id,),
        )
        decision_messages.rearm_after_restart(conn, ticket.id)
        conn.commit()
        return {
            "rows": [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM ticket_decision_messages ORDER BY decision_id"
                )
            ],
            "pending": decision_messages.pending_ticket_ids(conn),
            "undelivered": decision_messages.has_undelivered(conn, ticket.id),
        }

    return compare(conn, root, {"mode": "decisions", "ticket_id": ticket.id}, python)


def presence(conn: sqlite3.Connection, state: str) -> None:
    identity = dict(
        protocol_version=PROTOCOL_VERSION,
        spool_format_version=SPOOL_FORMAT_VERSION,
        application_revision="synthetic",
        executor_incarnation="synthetic",
        connection_generation=0,
    )
    if state == "incompatible":
        identity["protocol_version"] = 0
    lease = WALL if state != "expired" else WALL - int(MAC_AWAY_AFTER_SECONDS * 1000)
    sleep = WALL if state == "asleep" else None
    conn.execute(
        """INSERT INTO mac_executor_presence
            (machine_id,executor_incarnation,identity_json,last_contact_at_ms,
             last_lease_at_ms,continuous_since_ms,going_to_sleep_at_ms)
            VALUES (?,?,?,?,?,?,?)""",
        (ORIGINAL_MAC_MACHINE_ID, "synthetic", json.dumps(identity), WALL, lease, WALL, sleep),
    )
    if state == "refused":
        refusal = dict(
            reason="Mac needs update",
            peer_identity=identity,
            server_identity=dict(
                protocol_version=PROTOCOL_VERSION,
                spool_format_version=SPOOL_FORMAT_VERSION,
                application_revision="server",
                executor_incarnation="server",
            ),
            refused_at_ms=WALL,
        )
        conn.execute(
            "UPDATE mac_executor_presence SET refusal_json=? WHERE machine_id=?",
            (json.dumps(refusal), ORIGINAL_MAC_MACHINE_ID),
        )


def check_readiness(conn: sqlite3.Connection, root: Path, outcome: str) -> dict[str, Any]:
    ticket = new_ticket(conn, "new_worker" if outcome == "user_opened" else "coding")
    fieldless = None
    if outcome in ("present", "expired", "asleep", "incompatible", "absent", "refused"):
        conn.execute(
            "UPDATE tickets SET locale='macos',machine_id=? WHERE id=?",
            (ORIGINAL_MAC_MACHINE_ID, ticket.id),
        )
        if outcome != "absent":
            presence(conn, outcome)
    elif outcome == "backlogged":
        conn.execute("UPDATE tickets SET is_backlogged=1 WHERE id=?", (ticket.id,))
    elif outcome == "terminal":
        conn.execute("UPDATE tickets SET stage='done' WHERE id=?", (ticket.id,))
    elif outcome == "out":
        tickets.claim_ticket_for_worker_step(
            conn, ticket.id, readiness_check=readiness.is_ready_for_worker_step, now=NOW
        )
    elif outcome == "user_opened":
        conn.execute(
            "INSERT INTO ticket_paired_stage_openers VALUES (?,?,?)", (ticket.id, ticket.stage, NOW)
        )
    elif outcome == "disabled":
        conn.execute(
            "INSERT INTO backend_model_enablement VALUES (?,?,0)",
            (ticket.employee_backend, ticket.employee_launch_model),
        )
    elif outcome == "fieldless":
        workflow = dataclasses.replace(
            ticket.workflow,
            stages=tuple(
                dataclasses.replace(stage, gating_field=None) if stage.id == ticket.stage else stage
                for stage in ticket.workflow.stages
            ),
        )
        fieldless = dataclasses.replace(ticket, workflow=workflow)
        payload = json.loads(
            conn.execute("SELECT workflow_json FROM tickets WHERE id=?", (ticket.id,)).fetchone()[0]
        )
        for stage in payload["stages"]:
            if stage["id"] == ticket.stage:
                stage["gating_field"] = None
        # Deliberately invalid synthetic row. Production commit projection rejects it.
        with sqlite3.connect(root / "python.sqlite") as raw:
            raw.execute(
                "UPDATE tickets SET workflow_json=? WHERE id=?", (json.dumps(payload), ticket.id)
            )

    def python() -> dict[str, Any]:
        with patch("planner.runtime.worker_step_readiness.now_ms", return_value=WALL):
            current = fieldless or tickets.read_ticket(conn, ticket.id)
            reason = readiness.worker_step_blocker(
                conn, current, worker_type_definition=current.workflow
            )
            error = None
            try:
                claimed = (
                    tickets.claim_ticket_for_worker_step(
                        conn, ticket.id, readiness_check=readiness.is_ready_for_worker_step, now=NOW
                    )
                    is not None
                )
            except PlannerError as caught:
                claimed = False
                error = {"code": caught.code.value, "message": str(caught), "detail": caught.detail}
            return {
                "ready": reason is None,
                "reason": reason,
                "claimed": claimed,
                "error": error,
                "rows": rows(conn),
            }

    spec: dict[str, Any] = {"mode": "readiness", "ticket_id": ticket.id}
    if fieldless is not None:
        spec["fieldless_snapshot"] = plain(fieldless)
        spec["fieldless_snapshot"]["ticket_status"] = fieldless.ticket_status.value
    result = compare(conn, root, spec, python)
    return {key: result[key] for key in ("ready", "reason", "claimed", "error", "rows")}


async def python_abort(
    conn: sqlite3.Connection,
    root: Path,
    spec: dict[str, Any],
    values: ConversationStartValues | None,
) -> dict[str, Any]:
    entered = asyncio.Event()
    killed = []

    class Hung(InMemoryConversationSystem):
        async def send(self, *args: Any, **kwargs: Any) -> PromptDeliveryFate:
            entered.set()
            return await asyncio.Future[PromptDeliveryFate]()

        async def kill(self, conversation_id: str) -> None:
            killed.append(conversation_id)
            await super().kill(conversation_id)

    system = Hung()
    if spec["existing"]:
        await system.start_conversation(
            ConversationStartRequest(
                conversation_id="parity_conversation", model="gpt-6.1-sol", workspace_folder=root
            )
        )
    conversation_id = "parity_conversation" if spec["existing"] else None
    if spec["kind"] == "ticket":
        operation = start.send_to_ticket_conversation(
            system,
            conn,
            spec["ticket_id"],
            text_message_content("parity opener"),
            now=NOW,
            conversation_id=conversation_id,
            created_conversation_id="parity_conversation",
            sender_label="test",
        )
    else:
        operation = start.send_to_agent_conversation(
            system,
            conn,
            spec["agent_key"],
            text_message_content("parity opener"),
            values,
            required_manager_id=spec.get("manager_id"),
            conversation_id=conversation_id,
            created_conversation_id="parity_conversation",
            sender_label="test",
        )
    task = asyncio.create_task(operation)
    await entered.wait()
    task.cancel()
    aborted = False
    try:
        await task
    except asyncio.CancelledError:
        aborted = True
    return {"aborted": aborted, "killed": killed, "rows": rows(conn)}


def check_abort(conn: sqlite3.Connection, root: Path, kind: str, existing: bool) -> dict[str, Any]:
    spec: dict[str, Any] = {"mode": "abort", "kind": kind, "existing": existing, "ticket_id": ""}
    values: ConversationStartValues | None = None
    if kind == "ticket":
        ticket = new_ticket(conn)
        spec["ticket_id"] = ticket.id
        tickets.claim_ticket_for_worker_step(
            conn, ticket.id, readiness_check=readiness.is_ready_for_worker_step, now=NOW
        )
        if existing:
            system = InMemoryConversationSystem()
            asyncio.run(
                start.start_ticket_conversation(
                    system,
                    conn,
                    ticket,
                    start.worker_resolve(conn, ticket),
                    conversation_id="parity_conversation",
                    now=NOW,
                )
            )
    else:
        if kind == "manager":
            manager = managers.create_manager(
                conn,
                title="Parity Manager",
                project_id="project_other",
                clock=RuntimeTestClock(datetime.fromtimestamp(NOW, UTC)),
            )
            spec.update(agent_key="manager_" + manager.id, manager_id=manager.id)
            values = start.manager_resolve(conn, manager)
        else:
            spec["agent_key"] = "chief"
            values = start.agent_resolve(conn)
        if existing:
            asyncio.run(
                start.start_agent_conversation(
                    InMemoryConversationSystem(),
                    conn,
                    spec["agent_key"],
                    values,
                    conversation_id="parity_conversation",
                    required_manager_id=spec.get("manager_id"),
                )
            )
        spec["values"] = plain(values)
    conn.commit()
    result = compare(conn, root, spec, lambda: asyncio.run(python_abort(conn, root, spec, values)))
    return {"aborted": result["aborted"], "killed": result["killed"], "rows": result["rows"]}


def run_worker_runtime_parity(storage: Path) -> dict[str, Any]:
    started = time.monotonic()
    results = []
    cases = [
        ("readiness", name, False)
        for name in (
            "ready",
            "backlogged",
            "absent",
            "incompatible",
            "refused",
            "present",
            "expired",
            "asleep",
            "terminal",
            "out",
            "user_opened",
            "disabled",
            "fieldless",
        )
    ]
    cases += [
        ("abort", kind, existing)
        for kind in ("ticket", "chief", "manager")
        for existing in (False, True)
    ]
    with synthetic_home_environment(create_synthetic_homes(storage)):
        for mode, name, existing in cases:
            root = storage / (mode + "-" + name + ("-existing" if existing else ""))
            root.mkdir()
            conn = connect(str(root / "python.sqlite"))
            try:
                create_schema(conn)
                with register_workspace_root(str(root / "python.sqlite"), root):
                    result = (
                        check_readiness(conn, root, name)
                        if mode == "readiness"
                        else check_abort(conn, root, name, existing)
                    )
                results.append({"mode": mode, "case": name, "existing": existing, "answer": result})
            finally:
                conn.close()
    return {"match": True, "seconds": round(time.monotonic() - started, 3), "cases": results}


@pytest.mark.xfail(
    run=False,
    reason="expected-open P3 (#916): port named-machine Worker readiness and protocol-24 claims",
)
def test_worker_runtime_parity(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    report = run_worker_runtime_parity(tmp_path)
    directory = request.config.getoption("--compare-report-dir")
    if directory:
        destination = Path(directory)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "unit-09.direct.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {"match": report["match"], "seconds": report["seconds"], "cases": len(report["cases"])}
        )
    )


def test_worker_decisions_parity(tmp_path: Path) -> None:
    with synthetic_home_environment(create_synthetic_homes(tmp_path)):
        conn = connect(str(tmp_path / "python.sqlite"))
        try:
            create_schema(conn)
            check_decisions(conn, tmp_path)
        finally:
            conn.close()


def test_automatic_start_admission_populated_lifecycle(tmp_path: Path) -> None:
    """Gap: pure engine tests cannot prove fresh staging admission rows agree.

    Reuse the SQLite schema and synthetic homes. One Node evaluator has no server,
    adapter, or provider. It replaces the isolated TS restore/remote-retirement case.
    """
    from tests.compare.direct.test_unit18_adapter import environment

    from planner.conversation.start_admission import AutomaticStartAdmission
    from planner.conversation.start_admission.contracts import StartRequest
    from planner.execution_identity import ChildReference, TurnToken
    from planner.machines.contracts import HOME_MACHINE_ID

    began = time.monotonic()
    python_path = tmp_path / "python.sqlite"
    ts_path = tmp_path / "typescript.sqlite"
    node_root = tmp_path / "node"
    node_root.mkdir()
    with synthetic_home_environment(create_synthetic_homes(tmp_path)):
        conn = connect(str(python_path))
        try:
            create_schema(conn)
            new_ticket(conn)
            conn.execute(
                "INSERT INTO automatic_start_bindings "
                "(identity,work_class,destination,conversation_id,turn_number) VALUES (?,?,?,?,?)",
                ("retained", "worker", HOME_MACHINE_ID, "untouched", 99),
            )
            conn.commit()
            with sqlite3.connect(ts_path) as target:
                conn.backup(target)
            now = 0

            def make() -> AutomaticStartAdmission:
                admission = AutomaticStartAdmission(clock=lambda: now)
                admission.configure_destinations(lambda _: True)
                return admission

            checkpoints: list[dict[str, Any]] = []

            def capture(admission: AutomaticStartAdmission) -> None:
                checkpoints.append(
                    {
                        "snapshots": sorted(
                            plain(admission.snapshots()), key=lambda row: row["request"]["identity"]
                        ),
                        "rows": [
                            dict(row)
                            for row in conn.execute(
                                "SELECT * FROM automatic_start_bindings ORDER BY identity"
                            )
                        ],
                    }
                )

            home = TurnToken("admission-home", 1)
            away = TurnToken("admission-away", 3)
            child = ChildReference("executor", "current", away.conversation_id)
            first = make()
            grants = [
                first.try_start(StartRequest("h1", "worker", HOME_MACHINE_ID)),
                first.try_start(StartRequest("h2", "worker", HOME_MACHINE_ID)),
                first.try_start(StartRequest("away", "worker", ORIGINAL_MAC_MACHINE_ID)),
            ]
            now = 1
            grants.append(first.try_start(StartRequest("h2", "worker", HOME_MACHINE_ID)))
            now = 2
            grants.append(first.try_start(StartRequest("h3", "worker", HOME_MACHINE_ID)))
            now = 3
            grants.extend(
                (
                    first.try_start(StartRequest("h4", "worker", HOME_MACHINE_ID)),
                    first.try_start(StartRequest("chief", "supervisor", HOME_MACHINE_ID)),
                )
            )
            first.bind(("h1",), home)
            first.bind(("away",), away)
            first.persist_bindings(str(python_path), home)
            first.persist_bindings(str(python_path), away)
            first.bind_child(str(python_path), away, child)
            first.persist_bindings(str(python_path), dataclasses.replace(away))
            capture(first)
            restored = make()
            restored.restore_bindings(conn, dataclasses.replace(home))
            restored.restore_bindings(conn, dataclasses.replace(away))
            capture(restored)
            grants.extend(
                (
                    restored.allow_dispatch(dataclasses.replace(home)),
                    restored.allow_dispatch(dataclasses.replace(away)),
                )
            )
            restored.bind_source(dataclasses.replace(away), object())
            restored.failed_attempt(str(python_path), dataclasses.replace(away))
            restored.accept(str(python_path), dataclasses.replace(home, turn_number=2))
            restored.retire_remote(
                str(python_path), dataclasses.replace(child, child_id="previous")
            )
            capture(restored)
            restored.accept(str(python_path), dataclasses.replace(home))
            restored.retire_remote(str(python_path), dataclasses.replace(child))
            capture(restored)
            expected = {"grants": grants, "checkpoints": checkpoints}
            actual = json.loads(
                subprocess.check_output(
                    [
                        "node", "--input-type=module", "-e",
                        "import {pathToFileURL} from 'node:url'; "
                        "const {run}=await import(pathToFileURL(process.argv[2]).href); "
                        "process.stdout.write(JSON.stringify(run(process.argv[1])));",
                        str(ts_path), str(ROOT / "server/conversation/testing/start_admission.ts"),
                    ],
                    cwd=ROOT, text=True, env=environment(node_root),
                )
            )
            assert actual == expected
            assert grants == [True, False, True, True, True, False, True, True, True]
            assert len(checkpoints[0]["rows"]) == 3
            assert [row["identity"] for row in checkpoints[-1]["rows"]] == ["retained"]
            assert checkpoints[2]["snapshots"][0]["state"] == "uncertain"
        finally:
            conn.close()
    print(f"populated admission lifecycle proof: {time.monotonic() - began:.3f}s")


def test_automatic_start_core_steer_receipts(tmp_path: Path) -> None:
    """Engine parity cannot prove that core steering binds before wire or releases after save.

    Reuse the existing scripted child and its receipt barrier. No provider or host starts.
    """
    import os

    from tests.compare.direct.test_unit18_adapter import environment
    from tests.compare.scripted.scripted_registry import ScriptedBackendRegistry
    from tests.unit.test_conversation_system import _link_worker_ticket

    from planner.conversation.api import delivery_fate_json
    from planner.conversation.contracts import (
        ConversationBackendKey,
        PromptDeliveryMode,
    )
    from planner.conversation.live_tail import ConversationLiveTail
    from planner.conversation.logic.conversation_start_resolution import (
        resolve_conversation_start_request,
    )
    from planner.conversation.message_files import ConversationMessageFiles
    from planner.conversation.storage import ConversationStore
    from planner.conversation.system import SqliteProcessConversationSystem

    async def exercise(path: Path, outcome: str) -> dict[str, Any]:
        now, available = 0, False
        saving = asyncio.Event()
        registry = ScriptedBackendRegistry()
        store = ConversationStore(str(path), integer_now=lambda: 100)
        core = SqliteProcessConversationSystem(
            store=store,
            backend_child_factories={key: registry.make_child for key in ConversationBackendKey},
            message_files=ConversationMessageFiles(str(path)),
            live_tail=ConversationLiveTail(),
            unix_time_now=lambda: 100,
            monotonic_now=lambda: now,
        )
        checkpoints: list[dict[str, Any]] = []
        grants, fates = [], []

        async def capture(name: str) -> None:
            with sqlite3.connect(path) as conn:
                conn.row_factory = sqlite3.Row
                binding_rows = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM automatic_start_bindings ORDER BY identity"
                    )
                ]
                receipts = [
                    {**dict(row), "payload": json.loads(row["payload"])}
                    for row in conn.execute(
                        "SELECT sequence,kind,payload FROM conversation_events "
                        "WHERE conversation_id='c' AND kind IN "
                        "('prompt','prompt_delivery_uncertain','prompt_delivery_refused') "
                        "ORDER BY sequence"
                    )
                ]
            checkpoints.append(
                {
                    "name": name,
                    "snapshots": sorted(
                        plain(core.start_admission.snapshots()),
                        key=lambda row: row["request"]["identity"],
                    ),
                    "rows": binding_rows,
                    "receipts": receipts,
                    "delivered": (await registry.current["c"].inspect())["delivered_text"],
                }
            )

        async def send(identity: str, mode: PromptDeliveryMode) -> PromptDeliveryFate:
            return await core.send(
                "c",
                text_message_content(identity),
                sender_label="loop",
                sender_message_id=identity,
                mode=mode,
            )

        original = store.append_events

        async def append(*args: Any, **kwargs: Any) -> Any:
            if (
                any(
                    getattr(payload, "sender_message_id", None) == "followup" for payload in args[1]
                )
                and not available
            ):
                saving.set()
                raise RuntimeError("receipt save held")
            return await original(*args, **kwargs)

        store.append_events = append  # type: ignore[method-assign]
        try:
            grants.append(
                core.start_admission.try_start(await core.automatic_start_request("initial", "c"))
            )
            fates.append(delivery_fate_json(await send("initial", PromptDeliveryMode.queue)))
            await capture("initial")
            now = 1
            grants.append(
                core.start_admission.try_start(await core.automatic_start_request("followup", "c"))
            )
            child = registry.current["c"]
            await child.configure(
                "steer",
                {
                    "barrier": "steer-receipt",
                    "result": {"fate": outcome, "composed_content_delivered": True},
                },
            )
            steering = asyncio.create_task(send("followup", PromptDeliveryMode.steer))
            await child.wait_for_barrier("steer-receipt")
            await capture("wire-pending")
            await child.release("steer-receipt")
            await saving.wait()
            await capture("save-pending")
            available = True
            fates.append(delivery_fate_json(await steering))
            await capture("settled")
            return {"grants": grants, "fates": fates, "checkpoints": checkpoints}
        finally:
            available = True
            await core.shutdown()
            await registry.close()

    began = time.monotonic()
    for outcome in ("accepted", "uncertain"):
        root = tmp_path / outcome
        root.mkdir()
        selected = environment(root)
        py_path, ts_path = root / "python.sqlite", root / "typescript.sqlite"
        with patch.dict(os.environ, selected, clear=True):
            conn = connect(str(py_path))
            create_schema(conn)
            conn.commit()
            conn.close()
            store = ConversationStore(str(py_path), integer_now=lambda: 100)
            asyncio.run(
                store.create_conversation(
                    resolve_conversation_start_request(
                        ConversationStartRequest(
                            conversation_id="c",
                            model="model",
                            workspace_folder=Path("/tmp"),
                        )
                    )
                )
            )
            _link_worker_ticket(py_path, "c")
            with sqlite3.connect(py_path) as source, sqlite3.connect(ts_path) as target:
                source.backup(target)
            expected = asyncio.run(asyncio.wait_for(exercise(py_path, outcome), 30))
        completed = subprocess.run(
            [
                "node",
                "--input-type=module",
                "-e",
                "import {run} from './server/conversation/testing/start_admission_core.ts';"
                "process.stdout.write(JSON.stringify(await run(process.argv[1],process.argv[2])));",
                str(ts_path),
                outcome,
            ],
            cwd=ROOT,
            env=selected,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr
        actual = json.loads(completed.stdout)
        (root / "comparison.json").write_text(
            json.dumps({"python": expected, "typescript": actual}, indent=2)
        )
        assert actual == expected
        assert expected["grants"] == [True, True]
        assert [len(point["rows"]) for point in expected["checkpoints"]] == [
            1,
            2,
            2,
            0 if outcome == "accepted" else 2,
        ]
        assert len(expected["checkpoints"][-1]["receipts"]) == 2
    print(f"core steer receipt parity: {time.monotonic() - began:.3f}s")

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


def test_machine_admission_and_refusal_envelopes(tmp_path: Path) -> None:
    """One destination's presence must not admit or block another destination.

    Reuse the populated Ticket fixture and Node evaluator without a host. Runtime
    protocol activation remains deferred: each side uses its supported identity,
    and both complete fixture identities remain explicit in the comparison report.
    This proves machine admission, not a same-wire protocol-26 exchange.
    """
    from planner.machines.contracts import HOME_MACHINE_ID
    from planner.tickets import locale_configuration
    from planner.tickets.worker_restart import require_restartable

    began = time.monotonic()
    machine_a = "44444444-4444-4444-8444-444444444444"
    machine_b = "55555555-5555-4555-8555-555555555555"
    source_identity = {
        "protocol_version": PROTOCOL_VERSION,
        "spool_format_version": SPOOL_FORMAT_VERSION,
        "application_revision": "machine-admission-fixture",
        "executor_incarnation": "77777777-7777-4777-8777-777777777777",
        "connection_generation": 0,
    }
    # The runtime deviation remains recorded until the whole executor port is ready.
    candidate_identity = {**source_identity, "protocol_version": 22}
    with synthetic_home_environment(create_synthetic_homes(tmp_path)):
        conn = connect(str(tmp_path / "python.sqlite"))
        try:
            create_schema(conn)
            for machine_id, name in ((machine_a, "Travel Linux"), (machine_b, "Office")):
                conn.execute(
                    "INSERT INTO machines VALUES (?,?,'server','cyan',0)", (machine_id, name)
                )
            machine_tickets = {}
            for name, machine_id in (
                ("home", HOME_MACHINE_ID), ("original", ORIGINAL_MAC_MACHINE_ID),
                ("a", machine_a), ("b", machine_b),
            ):
                ticket = new_ticket(conn)
                conn.execute(
                    "UPDATE tickets SET machine_id=?,locale=? WHERE id=?",
                    (machine_id, "vps" if machine_id == HOME_MACHINE_ID else "macos", ticket.id),
                )
                machine_tickets[name] = ticket.id
            conn.execute(
                "INSERT INTO mac_executor_presence(machine_id,executor_incarnation,identity_json,"
                "last_contact_at_ms,last_lease_at_ms,continuous_since_ms) VALUES(?,?,?,?,?,?)",
                (machine_a, source_identity["executor_incarnation"], json.dumps(source_identity),
                 WALL, WALL, WALL),
            )
            conn.commit()

            async def exercise() -> dict[str, Any]:
                system = InMemoryConversationSystem()

                async def capture() -> dict[str, Any]:
                    result = {}
                    for name, ticket_id in machine_tickets.items():
                        current = tickets.read_ticket(conn, ticket_id)
                        admitted = locale_configuration.can_execute_machine(
                            conn, current.machine_id
                        )
                        restart_error: dict[str, Any] | None = None
                        send_error: dict[str, Any] | None = None
                        try:
                            with patch("planner.tickets.worker_restart.now_ms", return_value=WALL):
                                require_restartable(conn, OWNER_PRINCIPAL, ticket_id)
                        except PlannerError as error:
                            restart_error = error.to_payload()
                        if not admitted:
                            try:
                                await start.send_to_ticket_conversation(
                                    system, conn, ticket_id,
                                    text_message_content("Refused machine opener"),
                                    now=NOW, conversation_id=None, sender_label="test",
                                )
                                raise AssertionError("A refused destination started a conversation")
                            except PlannerError as error:
                                send_error = error.to_payload()
                        with patch(
                            "planner.runtime.worker_step_readiness.now_ms", return_value=WALL
                        ):
                            blocker = readiness.worker_step_blocker(
                                conn, current, worker_type_definition=current.workflow
                            )
                        result[name] = {
                            "admitted": admitted,
                            "reason": locale_configuration.machine_unavailable_reason(
                                conn, current.machine_id
                            ),
                            "readiness": blocker, "restart": restart_error, "send": send_error,
                        }
                    return result

                phases = [await capture()]
                conn.execute(
                    "UPDATE mac_executor_presence SET last_lease_at_ms=? WHERE machine_id=?",
                    (WALL - 60000, machine_a),
                )
                other_identity = {
                    **source_identity,
                    "executor_incarnation": "88888888-8888-4888-8888-888888888888",
                }
                conn.execute(
                    "INSERT INTO mac_executor_presence"
                    "(machine_id,executor_incarnation,identity_json,last_contact_at_ms,"
                    "last_lease_at_ms,continuous_since_ms) VALUES(?,?,?,?,?,?)",
                    (machine_b, other_identity["executor_incarnation"], json.dumps(other_identity),
                     WALL, WALL, WALL),
                )
                conn.execute(
                    "UPDATE machines SET name=? WHERE machine_id=?",
                    ("Café $&", ORIGINAL_MAC_MACHINE_ID),
                )
                conn.commit()
                phases.append(await capture())
                unknown = None
                try:
                    locale_configuration.machine_unavailable_reason(conn, "unknown")
                except PlannerError as error:
                    unknown = error.to_payload()
                return {
                    "fixture": {
                        "source_identity": source_identity,
                        "candidate_identity": candidate_identity,
                    },
                    "phases": phases, "unknown": unknown, "rows": rows(conn),
                }

            answer = compare(conn, tmp_path, {
                "mode": "machine_admission", "ticket_id": "", "machine_tickets": machine_tickets,
                "named_machines": [machine_a, machine_b], "source_identity": source_identity,
                "candidate_identity": candidate_identity,
            }, lambda: asyncio.run(exercise()))
            assert answer["phases"][0]["a"]["admitted"]
            assert answer["phases"][0]["a"]["readiness"] is None
            assert not answer["phases"][0]["b"]["admitted"]
            assert answer["phases"][1]["a"]["admitted"]
            assert answer["phases"][1]["a"]["readiness"] == "Waiting for Travel Linux"
            assert answer["phases"][1]["b"]["readiness"] is None
            assert answer["phases"][1]["original"]["send"]["error"]["detail"] == {
                "ticket_id": machine_tickets["original"],
                "locale": "macos", "machine_locale": "vps",
            }
        finally:
            conn.close()
    print(f"machine admission and refusal proof: {time.monotonic() - began:.3f}s")


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


def test_worker_journal_exact_python_roundtrip(tmp_path: Path) -> None:
    """Stored journal bytes must match fresh Python, including exact kernel integers.

    This uses one Node evaluator and private synthetic files. It grants no process
    or cgroup authority and starts no server or provider.
    """
    from tests.compare.direct.test_unit18_adapter import environment

    from planner.worker_supervision import records

    began = time.monotonic()
    directory = tmp_path / "journals"
    directory.mkdir(mode=0o700)
    path = directory / ("a" * 32 + ".json")
    owner = dict(pid=17, birth="100:200", parent=1, group=17, session=17, zombie=False)
    root = dict(pid=42, birth="300:400", parent=17, group=42, session=42, zombie=False)
    record = dict(
        version=1,
        ticket_id="synthetic-ticket",
        host="synthetic-host",
        boot="synthetic-boot",
        owner=owner,
        root=root,
        processes=[root],
        state="running",
        launch_phase="running",
        isolated=True,
        cgroup_joined=True,
        cgroup=dict(
            path=str(tmp_path / "synthetic-cgroup"),
            inode=2**64 - 1,
            root_device=2**53 + 1,
            root_inode=2**63 + 1,
        ),
        reason="Review café — 日本語 🚀",
    )
    records.write(path, record)
    expected = path.read_bytes()
    assert expected == (json.dumps(record, sort_keys=True, ensure_ascii=True) + "\n").encode()
    node_root = tmp_path / "node"
    node_root.mkdir()
    completed = subprocess.run(
        [
            "node", "--input-type=module", "-e",
            "import {read,write} from './server/worker_supervision/records.ts';"
            "const path=process.argv[1]; const record=await read(path);"
            "if(!['inode','root_device','root_inode'].every("
            "key=>typeof record.cgroup[key]==='bigint'))"
            "throw new Error('Cgroup identity lost exact integer type');"
            "await write(path,record);",
            str(path),
        ],
        cwd=ROOT, env=environment(node_root), text=True, capture_output=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert path.read_bytes() == expected
    assert records.read(path) == record
    print(f"exact journal Python roundtrip: {time.monotonic() - began:.3f}s")


def test_local_child_custody_populated_transitions(tmp_path: Path) -> None:
    """A stale generation or inspection must not retire a replacement source.

    This reuses the direct SQLite evaluator. Synthetic inspection answers exercise
    recorded custody only, without process, provider, cgroup, or service authority.
    Existing launch comparisons do not prove persisted occurrence and binding fences.
    """
    import os

    from tests.compare.direct.test_unit18_adapter import environment

    from planner.conversation import child_custody
    from planner.conversation.local_child_contracts import LocalChildIdentity, LocalHostBootIdentity
    from planner.conversation.logic.conversation_start_resolution import (
        resolve_conversation_start_request,
    )
    from planner.conversation.storage import ConversationStore
    from planner.worker_supervision import records

    began = time.monotonic()
    selected = environment(tmp_path)
    py_path, ts_path = tmp_path / "python.sqlite", tmp_path / "typescript.sqlite"
    directory = tmp_path / "journals"
    directory.mkdir(mode=0o700)
    journal = directory / ("b" * 32 + ".json")
    records.write(
        journal,
        dict(
            version=1,
            ticket_id="synthetic",
            conversation_id="pending",
            processes=[],
            state="starting",
        ),
    )

    async def prepare() -> None:
        store = ConversationStore(str(py_path), integer_now=lambda: 100)
        for identity in (
            "started",
            "pending",
            "quiet",
            "startup-blocked",
            "startup-proven",
            "startup-changed",
            "startup-mismatch",
            "startup-legacy",
        ):
            await store.create_conversation(
                resolve_conversation_start_request(
                    ConversationStartRequest(
                        conversation_id=identity, model="model", workspace_folder=tmp_path
                    )
                )
            )
            if identity != "quiet":
                await store.reserve_turn(identity)
        with connect(str(py_path)) as conn:
            conn.execute("UPDATE conversation_runtime_epoch SET epoch='epoch'")
            for identity in (
                "started",
                "pending",
                "startup-blocked",
                "startup-proven",
                "startup-changed",
                "startup-mismatch",
                "startup-legacy",
            ):
                conn.execute(
                    "UPDATE conversation_turn_occurrences SET occurrence_id=?,"
                    "runtime_epoch='epoch' WHERE conversation_id=?",
                    (identity + "-occurrence", identity),
                )
                conn.execute(
                    "INSERT INTO automatic_start_bindings "
                    "(identity,work_class,destination,conversation_id,turn_number) "
                    "VALUES (?,'worker','home',?,1)",
                    (identity, identity),
                )
            conn.commit()

    async def exercise() -> dict[str, Any]:
        store = ConversationStore(str(py_path), integer_now=lambda: 100)
        identity = LocalChildIdentity(42, "birth", "boot", None, None)
        points: list[dict[str, Any]] = []
        absent, change_during_inspection, startup = False, False, False

        def inspected(row: sqlite3.Row) -> bool:
            if startup and row["conversation_id"] == "startup-blocked":
                return False
            if change_during_inspection or (
                startup and row["conversation_id"] == "startup-changed"
            ):
                with connect(str(py_path)) as conn:
                    conn.execute(
                        "UPDATE conversation_child_custody SET generation='replacement' "
                        "WHERE conversation_id=?",
                        (row["conversation_id"],),
                    )
                    conn.commit()
            return row["state"] == "retired" or absent

        def capture(label: str, result: Any = None) -> None:
            with connect(str(py_path)) as conn:
                points.append(
                    dict(
                        label=label,
                        result=plain(result),
                        custody=[
                            dict(row)
                            for row in conn.execute(
                                "SELECT * FROM conversation_child_custody ORDER BY conversation_id"
                            )
                        ],
                        facts=[
                            dict(row)
                            for row in conn.execute(
                                "SELECT * FROM conversation_lifecycle_facts "
                                "ORDER BY conversation_id,kind,identity"
                            )
                        ],
                        bindings=[
                            dict(row)
                            for row in conn.execute(
                                "SELECT * FROM automatic_start_bindings "
                                "ORDER BY conversation_id,identity"
                            )
                        ],
                        settled=plain(store.settled_recorded_turn("pending")),
                    )
                )

        with (
            patch.object(
                child_custody,
                "host_boot_identity",
                return_value=LocalHostBootIdentity("host", "boot"),
            ),
            patch.object(child_custody, "retirement_row_proven", side_effect=inspected),
            patch.object(child_custody, "stop_recorded_child", side_effect=lambda *_: absent),
            patch(
                "planner.worker_supervision.inspect_record",
                side_effect=lambda *_: type("Proof", (), {"complete": absent})(),
            ),
            patch(
                "planner.worker_supervision.stop_record",
                side_effect=lambda *_: type("Proof", (), {"complete": absent})(),
            ),
        ):
            await store.record_child_custody("started", "old")
            await store.record_child_custody("started", "new")
            refused = False
            try:
                await store.record_child_custody("started", "old", identity)
            except Exception:
                refused = True
            capture("stale-start", refused)
            await store.record_child_custody("started", "new", identity)
            capture("started", await store.check_local_child_started("started", "new", 1))
            with connect(str(py_path)) as conn:
                conn.execute(
                    "UPDATE conversation_child_custody SET occurrence_id='wrong' "
                    "WHERE conversation_id='started'"
                )
                conn.commit()
            capture("wrong-occurrence", await store.check_local_child_started("started", "new", 1))
            with connect(str(py_path)) as conn:
                conn.execute(
                    "UPDATE conversation_child_custody SET occurrence_id='started-occurrence' "
                    "WHERE conversation_id='started'"
                )
                conn.commit()
            await store.record_child_custody("pending", "pending-generation")
            wrong_owner = False
            try:
                await store.record_child_supervision("started", "new", str(journal))
            except Exception:
                wrong_owner = True
            capture("wrong-journal-owner", wrong_owner)
            await store.record_child_supervision("pending", "pending-generation", str(journal))
            capture(
                "pending-unknown", await store.settle_recorded_source("pending", inspect_only=True)
            )
            absent = True
            capture(
                "pending-undelivered",
                await store.settle_recorded_source("pending", inspect_only=True),
            )
            try:
                await store.record_child_custody("quiet", "quiet-generation", identity)
            except Exception:
                pass
            await store.record_child_custody("quiet", "quiet-generation")
            await store.record_child_custody("quiet", "quiet-generation", identity)
            change_during_inspection = True
            capture(
                "changed-during-inspection",
                await store.retire_idle_recorded_source("quiet", inspect_only=True),
            )
            change_during_inspection = False
            capture(
                "quiet-retired", await store.retire_idle_recorded_source("quiet", inspect_only=True)
            )
            capture(
                "started-retired", await store.settle_recorded_source("started", inspect_only=True)
            )
            capture("retirement-proof", await store.recorded_source_retirement_proven("started"))
            with connect(str(py_path)) as conn:
                started_facts = [
                    {
                        key: row[key]
                        for key in (
                            "kind",
                            "identity",
                            "turn_number",
                            "operation",
                            "status",
                            "provider_identity",
                        )
                    }
                    for row in conn.execute(
                        "SELECT * FROM conversation_lifecycle_facts "
                        "WHERE conversation_id='started' ORDER BY kind,identity"
                    )
                ]
            for name in (
                "startup-blocked",
                "startup-proven",
                "startup-changed",
                "startup-mismatch",
            ):
                await store.record_child_custody(name, name)
                await store.record_child_custody(name, name, identity)
            with connect(str(py_path)) as conn:
                conn.execute(
                    "UPDATE conversation_child_custody SET occurrence_id='wrong' "
                    "WHERE conversation_id='startup-mismatch'"
                )
                conn.execute(
                    "DELETE FROM conversation_child_custody WHERE conversation_id='startup-legacy'"
                )
                conn.commit()
            startup = True
            restored = ConversationStore(str(py_path), integer_now=lambda: 100)
            conn = connect(str(py_path))
            try:
                restored._reconcile_runtime(conn, {})
                restart = dict(
                    blocked=sorted(restored.blocked_local_conversations),
                    facts=[
                        dict(row)
                        for row in conn.execute(
                            "SELECT * FROM conversation_lifecycle_facts "
                            "ORDER BY conversation_id,kind,identity"
                        )
                    ],
                    custody=[
                        dict(row)
                        for row in conn.execute(
                            "SELECT * FROM conversation_child_custody ORDER BY conversation_id"
                        )
                    ],
                    interruptions=[
                        dict(row)
                        for row in conn.execute(
                            "SELECT * FROM deploy_interruptions ORDER BY interruption_id"
                        )
                    ],
                    epoch_changed=conn.execute(
                        "SELECT epoch FROM conversation_runtime_epoch WHERE singleton=1"
                    ).fetchone()[0]
                    != "epoch",
                )
            finally:
                conn.close()
        return dict(points=points, started_facts=started_facts, restart=restart)

    with patch.dict(os.environ, selected, clear=True):
        conn = connect(str(py_path))
        create_schema(conn)
        conn.execute("INSERT INTO conversation_runtime_epoch VALUES (1,'epoch')")
        conn.commit()
        conn.close()
        asyncio.run(prepare())
        with sqlite3.connect(py_path) as source, sqlite3.connect(ts_path) as target:
            source.backup(target)
        expected = asyncio.run(exercise())
    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {run} from './server/conversation/testing/custody_parity.ts';"
            "process.stdout.write(JSON.stringify(await run(process.argv[1],process.argv[2])));",
            str(ts_path),
            str(journal),
        ],
        cwd=ROOT,
        env=selected,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    actual = json.loads(completed.stdout)
    (tmp_path / "comparison.json").write_text(
        json.dumps(dict(python=expected, typescript=actual), indent=2)
    )
    assert actual == expected
    assert [point["result"] for point in expected["points"]] == [
        True,
        True,
        False,
        True,
        None,
        "undelivered",
        False,
        True,
        "retired",
        True,
    ]
    print(f"local child custody populated parity: {time.monotonic() - began:.3f}s")

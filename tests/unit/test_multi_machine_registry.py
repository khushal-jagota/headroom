"""Protect destination admission and startup custody absent from single-executor coverage."""

from __future__ import annotations

import asyncio
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from planner.conversation.backends.contracts import RetainedBackendRun
from planner.conversation.start_admission import AutomaticStartAdmission
from planner.conversation.start_admission.contracts import StartRequest
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.execution_identity import ChildReference
from planner.executor.registry import VpsExecutorRegistry
from planner.machines.contracts import HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID
from planner.worker_supervision.contracts import CleanupResult

EXTRA = "11111111-1111-1111-1111-111111111111"


def test_machine_capacity_and_pacing_do_not_block_other_destinations() -> None:
    now = [0.0]
    gate = AutomaticStartAdmission(clock=lambda: now[0])
    gate.configure_destinations(lambda destination: True)
    for number in range(4):
        now[0] = float(number)
        assert gate.try_start(StartRequest(f"a{number}", "worker", ORIGINAL_MAC_MACHINE_ID))
    assert not gate.try_start(StartRequest("a-full", "worker", ORIGINAL_MAC_MACHINE_ID))
    assert gate.try_start(StartRequest("b", "worker", EXTRA))
    assert gate.try_start(StartRequest("home", "worker", HOME_MACHINE_ID))
    assert not gate.try_start(StartRequest("b-next", "worker", EXTRA))
    # A wake keeps each destination's FIFO, without inheriting another's age or capacity.
    gate.configure_destinations(lambda destination: destination != EXTRA)
    assert not gate.try_start(StartRequest("b-wake", "worker", EXTRA))
    now[0] += 1
    gate.configure_destinations(lambda destination: True)
    assert not gate.try_start(StartRequest("b-wake", "worker", EXTRA))
    assert gate.try_start(StartRequest("b-next", "worker", EXTRA))


def test_server_loads_both_retained_owners_before_adoption_and_dispatches_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reuse the existing TestClient lifespan host: no browser, backend, or network calls.

    Risk: an empty registry at adoption loses remote custody; a second endpoint
    overwrites the global cleanup callback. Existing startup coverage has one executor.
    """
    database = str(tmp_path / "home.db")
    with closing(connect(database)) as conn:
        create_schema(conn)
        conn.execute("INSERT INTO machines VALUES (?, 'Extra','desktop','cyan',0)", (EXTRA,))
        conn.execute(
            "INSERT INTO machine_executor_credentials(machine_id,credential_sha256) "
            "VALUES (?, 'verifier')",
            (EXTRA,),
        )
        for number, machine in enumerate((ORIGINAL_MAC_MACHINE_ID, EXTRA)):
            incarnation = f"00000000-0000-0000-0000-{number:012d}"
            conn.execute(
                "INSERT INTO executor_incarnations"
                "(executor_incarnation,first_leased_at_ms,machine_id) "
                "VALUES (?,1,?)",
                (incarnation, machine),
            )
            conn.execute(
                "INSERT INTO mac_executor_presence(machine_id,executor_incarnation) VALUES (?,?)",
                (machine, incarnation),
            )
            conn.execute(
                "INSERT INTO executor_child_assignments VALUES (?,?,?,'ticket',?,1,NULL)",
                (incarnation, f"child{number}", f"conversation{number}", f"ticket{number}"),
            )
            conn.execute(
                "INSERT INTO "
                "mac_runs(conversation_id,executor_incarnation,child_id,principal_kind,"
                "principal_id,turn_number,delivery_uncertain,stop_state,machine_id) VALUES "
                "(?,?,?,'ticket',?,1,0,'open',?)",
                (
                    f"conversation{number}",
                    incarnation,
                    f"child{number}",
                    f"ticket{number}",
                    machine,
                ),
            )
    from planner.server_lifecycle import runtime as lifecycle_runtime

    evidence_reads: list[tuple[str, ...]] = []
    read_evidence = lifecycle_runtime.read_retained_run_evidence

    def off_loop_evidence(path: str, ids: tuple[str, ...], timeout: int):
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        evidence_reads.append(ids)
        return read_evidence(path, ids, timeout)

    monkeypatch.setattr(lifecycle_runtime, "read_retained_run_evidence", off_loop_evidence)
    seen: list[set[str]] = []
    retained = VpsExecutorRegistry.retained_runs

    def inventory(registry: VpsExecutorRegistry) -> tuple[RetainedBackendRun, ...]:
        runs = retained(registry)
        seen.append({run.reference.conversation_id for run in runs})
        return runs

    monkeypatch.setattr(VpsExecutorRegistry, "retained_runs", inventory)
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": database,
            "PLAN_EXECUTOR_ID": "original",
            "PLAN_DISPATCH_ENABLED": "0",
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_HERMES_HOME": str(tmp_path / "hermes"),
        },
    )
    app = create_app(config, build_clock(config), lambda: connect(database))
    with TestClient(app) as client:
        assert seen and all(ids == {"conversation0", "conversation1"} for ids in seen)
        assert len(evidence_reads) == 1
        assert set(evidence_reads[0]) == seen[0]
        registry = app.state.executor_registry
        first = registry.get(ORIGINAL_MAC_MACHINE_ID)
        second = registry.get(EXTRA)
        monkeypatch.setattr(
            first, "health_payload", lambda: {"presence": "present", "identity": None},
        )
        monkeypatch.setattr(
            second, "health_payload", lambda: {"presence": "asleep", "identity": None},
        )
        assert client.get("/api/executor/health").json()["presence"] == "present"
        assert client.get(f"/api/executor/health?machine_id={EXTRA}").json()["presence"] == "asleep"
        first.runs.recheck_cleanup = AsyncMock(return_value=CleanupResult(True, "first"))
        second.runs.recheck_cleanup = AsyncMock(return_value=CleanupResult(True, "second"))
        callback = app.state.conversation.system._worker_cleanup_recheck
        for number in (0, 1):
            source = ChildReference(
                f"00000000-0000-0000-0000-{number:012d}", f"child{number}", f"conversation{number}"
            )
            assert asyncio.run(callback(source, f"ticket{number}")).complete
        first.runs.recheck_cleanup.assert_awaited_once()
        second.runs.recheck_cleanup.assert_awaited_once()
        denied = asyncio.run(
            callback(
                ChildReference(
                    "00000000-0000-0000-0000-000000000001",
                    "child1",
                    "conversation1",
                ),
                "ticket0",
            )
        )
        assert not denied.complete
        second.runs.recheck_cleanup.assert_awaited_once()

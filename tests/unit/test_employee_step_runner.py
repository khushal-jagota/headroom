from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

import pytest
from tests.support.acp_e2e_server import build_scripted_employee_runtime_definitions

from planner.conversation.composition import (
    ConversationComposition,
    ConversationTestOptions,
)
from planner.conversation.contracts import ConversationSessionBinding
from planner.core.clock import RealClock
from planner.core.db import connect, create_schema
from planner.days import data as days_data
from planner.days.logic import dates
from planner.runtime.automatic_employee_step_eligibility_wake import (
    NoOpAutomaticEmployeeStepEligibilityWake,
)
from planner.runtime.employee_step_repository import SqliteEmployeeStepRepository
from planner.runtime.employee_step_runner import EmployeeStepRunner
from planner.runtime.step_gateway import (
    EmployeeStepGatewayBusy,
    EmployeeStepRunResult,
)
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    NO_FURTHER,
    AtCap,
    EmployeeLaunchConfiguration,
    StageOwnershipMode,
    TicketStatus,
)

BOUNDARY_HOUR = 5


@dataclass(frozen=True)
class _Status:
    available: bool = True


class _Gateway:
    def __init__(self, result: EmployeeStepRunResult | None = None) -> None:
        self.result = result or EmployeeStepRunResult("complete", "session-1", None)
        self.calls: list[tuple[str | None, str, str, bool]] = []
        self.interrupts: list[tuple[str, str]] = []
        self.busy = False

    def run_ticket_step(
        self,
        employee_session_id: str | None,
        entity_id: str,
        prompt_text: str,
        on_employee_session_id=None,
        *,
        require_existing_session: bool = False,
    ) -> EmployeeStepRunResult:
        self.calls.append(
            (employee_session_id, entity_id, prompt_text, require_existing_session)
        )
        if self.busy:
            raise EmployeeStepGatewayBusy(employee_session_id or "session-1")
        if on_employee_session_id is not None:
            on_employee_session_id(self.result.employee_session_id or "session-1")
        return self.result

    def interrupt(
        self,
        employee_session_id: str,
        entity_id: str,
        *,
        deadline: float | None = None,
    ) -> None:
        del deadline
        self.interrupts.append((employee_session_id, entity_id))

    def status(self) -> _Status:
        return _Status()


class _ConversationLoopThread:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.started = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        assert self.started.wait(1)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.started.set()
        self.loop.run_forever()

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=1)
        self.loop.close()


def _read_json_lines(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _eligible_ticket(tmp_path: Path) -> tuple[str, str]:
    db_path = str(tmp_path / "runner.db")
    conn = connect(db_path)
    create_schema(conn)
    ticket = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title="Run",
        actor="human",
        now=1,
        title_max_chars=200,
    )
    tickets_data.accept_proposal(
        conn,
        ticket.id,
        field="kickoff",
        actor="human",
        now=2,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )
    today = dates.resolve_day_id("today", RealClock().now(), BOUNDARY_HOUR)
    days_data.add_day_ticket(conn, today, ticket.id, 3)
    conn.close()
    return db_path, ticket.id


def _runner(db_path: str, gateway: _Gateway) -> EmployeeStepRunner:
    return EmployeeStepRunner(
        db_path,
        RealClock(),
        gateway=gateway,
        automatic_employee_step_eligibility_wake=(
            NoOpAutomaticEmployeeStepEligibilityWake()
        ),
        boundary_hour=BOUNDARY_HOUR,
    )


def test_automatic_step_uses_acp_and_stores_no_transcript(tmp_path: Path) -> None:
    db_path, ticket_id = _eligible_ticket(tmp_path)
    gateway = _Gateway()
    runner = _runner(db_path, gateway)
    runner.try_run_automatic_step(ticket_id)
    assert runner.wait_idle(timeout=2)

    conn = connect(db_path)
    rows = conn.execute("SELECT * FROM employee_step_runs").fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "complete"
    assert rows[0]["employee_session_id"] == "session-1"
    assert set(rows[0].keys()) == {
        "employee_step_id",
        "ticket_id",
        "status",
        "employee_session_id",
        "error",
        "started_at",
        "updated_at",
        "completed_at",
    }
    assert not any(
        name in {
            "chat_turns",
            "chat_messages",
            "chat_turn_activity_entries",
        }
        for name, in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    )
    assert len(gateway.calls) == 1
    assert gateway.calls[0][1] == ticket_id


def test_first_automatic_prompt_uses_selected_model_then_reasoning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, ticket_id = _eligible_ticket(tmp_path)
    config_audit_path = tmp_path / "automatic-config-audit.jsonl"
    monkeypatch.setenv("ACP_TEST_CONFIG_AUDIT_PATH", str(config_audit_path))
    conn = connect(db_path)
    conn.execute(
        "UPDATE tickets SET employee_backend = 'probe-backend', "
        "employee_launch_model = 'probe-alt', "
        "employee_launch_reasoning_effort = 'probe-low' WHERE id = ?",
        (ticket_id,),
    )
    conn.close()

    loop_thread = _ConversationLoopThread()
    composition: ConversationComposition | None = None
    runner: EmployeeStepRunner | None = None
    binding_observations: list[ConversationSessionBinding] = []
    try:

        async def build_composition() -> ConversationComposition:
            built = ConversationComposition.build(
                db_path=db_path,
                busy_timeout_ms=5000,
                clock=RealClock(),
                repository_root=tmp_path,
                employee_workspace_root=tmp_path,
                loop=asyncio.get_running_loop(),
                test_options=ConversationTestOptions(
                    employee_runtime_definitions=(
                        build_scripted_employee_runtime_definitions()
                    )
                ),
            )
            original_initial_binding = (
                built.registry._compare_and_swap_initial_binding  # noqa: SLF001
            )
            assert original_initial_binding is not None

            async def observe_initial_binding(
                candidate: ConversationSessionBinding,
                configuration: EmployeeLaunchConfiguration,
            ) -> ConversationSessionBinding:
                audit = _read_json_lines(config_audit_path)
                assert [
                    (event["event"], event.get("configId"), event.get("value"))
                    for event in audit
                ] == [
                    ("set_config_option", "scripted-model", "probe-alt"),
                    ("set_config_option", "scripted-reasoning", "probe-low"),
                ]
                assert configuration == EmployeeLaunchConfiguration(
                    employee_backend="probe-backend",
                    employee_launch_model="probe-alt",
                    employee_launch_reasoning_effort="probe-low",
                )
                binding_observations.append(candidate)
                return await original_initial_binding(candidate, configuration)

            built.registry._compare_and_swap_initial_binding = (  # noqa: SLF001
                observe_initial_binding
            )
            return built

        composition = asyncio.run_coroutine_threadsafe(
            build_composition(), loop_thread.loop
        ).result(timeout=5)
        runner = EmployeeStepRunner(
            db_path,
            RealClock(),
            gateway=composition.step_gateway,
            automatic_employee_step_eligibility_wake=(
                NoOpAutomaticEmployeeStepEligibilityWake()
            ),
            boundary_hour=BOUNDARY_HOUR,
        )

        runner.try_run_automatic_step(ticket_id)
        assert runner.wait_idle(timeout=8)

        audit = _read_json_lines(config_audit_path)
        assert [event["event"] for event in audit] == [
            "set_config_option",
            "set_config_option",
            "prompt",
        ]
        assert audit[-1]["model"] == "probe-alt"
        assert audit[-1]["reasoning"] == "probe-low"
        assert len({event["processId"] for event in audit}) == 1
        assert len(binding_observations) == 1
        assert audit[-1]["sessionId"] == binding_observations[0].acp_session_id

        conn = connect(db_path)
        binding_rows = conn.execute(
            "SELECT acp_session_id, backend_key, binding_generation "
            "FROM conversation_session_bindings WHERE employee_id = ?",
            (ticket_id,),
        ).fetchall()
        step = conn.execute(
            "SELECT status, employee_session_id FROM employee_step_runs "
            "WHERE ticket_id = ?",
            (ticket_id,),
        ).fetchone()
        ticket = tickets_data.read_ticket(conn, ticket_id)
        conn.close()
        assert [tuple(row) for row in binding_rows] == [
            (binding_observations[0].acp_session_id, "probe-backend", 1)
        ]
        assert tuple(step) == ("complete", binding_observations[0].acp_session_id)
        assert ticket.employee_session_id == binding_observations[0].acp_session_id
        assert ticket.employee_launch_model == "probe-alt"
        assert ticket.employee_launch_reasoning_effort == "probe-low"
    finally:
        if runner is not None:
            runner.stop(deadline=monotonic() + 2)
        if composition is not None:
            asyncio.run_coroutine_threadsafe(
                composition.shutdown(loop_thread.loop.time() + 5),
                loop_thread.loop,
            ).result(timeout=6)
        loop_thread.close()


def test_busy_step_interrupts_record_and_releases_ticket(tmp_path: Path) -> None:
    db_path, ticket_id = _eligible_ticket(tmp_path)
    gateway = _Gateway()
    gateway.busy = True
    runner = _runner(db_path, gateway)
    runner.try_run_automatic_step(ticket_id)
    assert runner.wait_idle(timeout=2)
    conn = connect(db_path)
    assert conn.execute("SELECT status FROM employee_step_runs").fetchone()[0] == "interrupted"
    assert tickets_data.read_ticket(conn, ticket_id).ticket_status is TicketStatus.empty


@pytest.mark.parametrize(
    ("result", "expected_step_status", "expected_ticket_status", "expected_backend_error"),
    [
        (EmployeeStepRunResult("interrupted", "session-1", None), "interrupted", "empty", None),
        (
            EmployeeStepRunResult(
                "errored",
                "session-1",
                "Prompt cancellation timed out",
                failure_provenance="conversation",
            ),
            "errored",
            "empty",
            None,
        ),
        (
            EmployeeStepRunResult(
                "errored",
                "session-1",
                "Provider process exited",
                failure_provenance="backend",
            ),
            "errored",
            "errored",
            "Provider process exited",
        ),
    ],
)
def test_only_confirmed_backend_failure_marks_ticket_errored(
    tmp_path: Path,
    result: EmployeeStepRunResult,
    expected_step_status: str,
    expected_ticket_status: str,
    expected_backend_error: str | None,
) -> None:
    db_path, ticket_id = _eligible_ticket(tmp_path)
    runner = _runner(db_path, _Gateway(result))
    runner.try_run_automatic_step(ticket_id)
    assert runner.wait_idle(timeout=2)

    conn = connect(db_path)
    step = conn.execute(
        "SELECT status, error FROM employee_step_runs WHERE ticket_id = ?", (ticket_id,)
    ).fetchone()
    ticket = tickets_data.read_ticket(conn, ticket_id)
    conn.close()
    assert step["status"] == expected_step_status
    assert ticket.ticket_status.value == expected_ticket_status
    assert ticket.backend_error == expected_backend_error


def test_unclassified_gateway_exception_keeps_ticket_non_error(tmp_path: Path) -> None:
    class CrashingGateway(_Gateway):
        def run_ticket_step(self, *args, **kwargs) -> EmployeeStepRunResult:
            del args, kwargs
            raise RuntimeError("conversation projection failed")

    db_path, ticket_id = _eligible_ticket(tmp_path)
    runner = _runner(db_path, CrashingGateway())
    runner.try_run_automatic_step(ticket_id)
    assert runner.wait_idle(timeout=2)

    conn = connect(db_path)
    step = conn.execute(
        "SELECT status, error FROM employee_step_runs WHERE ticket_id = ?", (ticket_id,)
    ).fetchone()
    ticket = tickets_data.read_ticket(conn, ticket_id)
    conn.close()
    assert tuple(step) == (
        "errored",
        "employee step crashed: conversation projection failed",
    )
    assert ticket.ticket_status is TicketStatus.empty
    assert ticket.backend_error is None


def test_uncertain_complete_settlement_keeps_ticket_non_error(tmp_path: Path) -> None:
    db_path, ticket_id = _eligible_ticket(tmp_path)

    class ConcurrentSettlementGateway(_Gateway):
        def run_ticket_step(self, *args, **kwargs) -> EmployeeStepRunResult:
            result = super().run_ticket_step(*args, **kwargs)
            conn = connect(db_path)
            conn.execute(
                "UPDATE employee_step_runs SET status = 'interrupted', error = 'concurrent stop' "
                "WHERE ticket_id = ? AND status = 'running'",
                (ticket_id,),
            )
            conn.close()
            return result

    runner = _runner(db_path, ConcurrentSettlementGateway())
    runner.try_run_automatic_step(ticket_id)
    assert runner.wait_idle(timeout=2)

    conn = connect(db_path)
    ticket = tickets_data.read_ticket(conn, ticket_id)
    conn.close()
    assert ticket.ticket_status is TicketStatus.empty
    assert ticket.backend_error is None


def test_restart_missing_session_records_failure_and_releases_to_user_control(
    tmp_path: Path,
) -> None:
    db_path, ticket_id = _eligible_ticket(tmp_path)
    conn = connect(db_path)
    ticket = tickets_data.read_ticket(conn, ticket_id)
    tickets_data.set_stage_ownership(
        conn,
        ticket_id,
        stage=ticket.stage,
        ownership_mode=StageOwnershipMode.user,
        now=4,
    )
    conn.execute(
        "UPDATE tickets SET ticket_status = 'agent_running_step', "
        "employee_session_id = NULL WHERE id = ?",
        (ticket_id,),
    )
    running = SqliteEmployeeStepRepository().start(conn, ticket_id, now=10)
    conn.close()
    gateway = _Gateway()

    runner = _runner(db_path, gateway)
    runner.recover_running_step(ticket_id)
    assert runner.wait_idle(timeout=2)

    conn = connect(db_path)
    recorded = SqliteEmployeeStepRepository().require(conn, running.employee_step_id)
    released = tickets_data.read_ticket(conn, ticket_id)
    conn.close()
    assert recorded.status == "errored"
    assert recorded.error == "restart recovery has no existing Employee session"
    assert released.ticket_status is TicketStatus.user_takeover
    assert released.backend_error is None
    assert gateway.calls == []


def test_revision_missing_session_releases_to_paired_control_without_replay(
    tmp_path: Path,
) -> None:
    db_path, ticket_id = _eligible_ticket(tmp_path)
    conn = connect(db_path)
    ticket = tickets_data.read_ticket(conn, ticket_id)
    tickets_data.set_stage_ownership(
        conn,
        ticket_id,
        stage=ticket.stage,
        ownership_mode=StageOwnershipMode.paired,
        now=4,
    )
    conn.execute(
        "UPDATE tickets SET ticket_status = 'agent_running_step', "
        "employee_session_id = NULL WHERE id = ?",
        (ticket_id,),
    )
    conn.close()
    gateway = _Gateway()

    runner = _runner(db_path, gateway)
    handoff = runner.reserve_revision(ticket_id, "Please revise it")
    handoff.release()
    assert runner.wait_idle(timeout=2)

    conn = connect(db_path)
    released = tickets_data.read_ticket(conn, ticket_id)
    run_count = conn.execute(
        "SELECT COUNT(*) FROM employee_step_runs WHERE ticket_id = ?", (ticket_id,)
    ).fetchone()[0]
    conn.close()
    assert run_count == 0
    assert released.ticket_status is TicketStatus.paired_work
    assert released.backend_error is None
    assert gateway.calls == []


def test_restart_session_mismatch_releases_to_paired_control_without_replay(
    tmp_path: Path,
) -> None:
    db_path, ticket_id = _eligible_ticket(tmp_path)
    conn = connect(db_path)
    ticket = tickets_data.read_ticket(conn, ticket_id)
    tickets_data.set_stage_ownership(
        conn,
        ticket_id,
        stage=ticket.stage,
        ownership_mode=StageOwnershipMode.paired,
        now=4,
    )
    conn.execute(
        "UPDATE tickets SET ticket_status = 'agent_running_step', "
        "employee_session_id = 'session-current' WHERE id = ?",
        (ticket_id,),
    )
    stale = SqliteEmployeeStepRepository().start(
        conn, ticket_id, now=10, employee_session_id="session-stale"
    )
    conn.close()
    gateway = _Gateway()

    runner = _runner(db_path, gateway)
    runner.recover_running_step(ticket_id)
    assert runner.wait_idle(timeout=2)

    conn = connect(db_path)
    preserved = SqliteEmployeeStepRepository().require(conn, stale.employee_step_id)
    released = tickets_data.read_ticket(conn, ticket_id)
    conn.close()
    assert preserved.status == "running"
    assert preserved.employee_session_id == "session-stale"
    assert released.ticket_status is TicketStatus.paired_work
    assert released.backend_error is None
    assert gateway.calls == []


def test_active_employee_step_collision_releases_to_user_control_without_replay(
    tmp_path: Path,
) -> None:
    db_path, ticket_id = _eligible_ticket(tmp_path)
    conn = connect(db_path)
    ticket = tickets_data.read_ticket(conn, ticket_id)
    tickets_data.set_stage_ownership(
        conn,
        ticket_id,
        stage=ticket.stage,
        ownership_mode=StageOwnershipMode.user,
        now=4,
    )
    conn.execute(
        "UPDATE tickets SET ticket_status = 'agent_running_step', "
        "employee_session_id = 'session-1' WHERE id = ?",
        (ticket_id,),
    )
    active = SqliteEmployeeStepRepository().start(
        conn, ticket_id, now=10, employee_session_id="session-1"
    )
    conn.close()
    gateway = _Gateway()

    runner = _runner(db_path, gateway)
    handoff = runner.reserve_revision(ticket_id, "Please revise it")
    handoff.release()
    assert runner.wait_idle(timeout=2)

    conn = connect(db_path)
    preserved = SqliteEmployeeStepRepository().require(conn, active.employee_step_id)
    released = tickets_data.read_ticket(conn, ticket_id)
    conn.close()
    assert preserved.status == "running"
    assert released.ticket_status is TicketStatus.user_takeover
    assert released.backend_error is None
    assert gateway.calls == []


def test_restart_replaces_exact_running_record_and_reuses_session(tmp_path: Path) -> None:
    db_path, ticket_id = _eligible_ticket(tmp_path)
    conn = connect(db_path)
    conn.execute(
        "UPDATE tickets SET ticket_status = 'agent_running_step', "
        "employee_session_id = 'session-1' WHERE id = ?",
        (ticket_id,),
    )
    original = SqliteEmployeeStepRepository().start(
        conn, ticket_id, now=10, employee_session_id="session-1"
    )
    conn.close()
    gateway = _Gateway()
    runner = _runner(db_path, gateway)
    runner.recover_running_step(ticket_id)
    assert runner.wait_idle(timeout=2)
    conn = connect(db_path)
    rows = conn.execute(
        "SELECT employee_step_id, status FROM employee_step_runs ORDER BY started_at"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        (original.employee_step_id, "interrupted"),
        (rows[1]["employee_step_id"], "complete"),
    ]
    assert gateway.calls[0][0] == "session-1"
    assert gateway.calls[0][3] is True


def test_controlled_stop_preserves_exact_running_record_for_restart_recovery(
    tmp_path: Path,
) -> None:
    db_path, ticket_id = _eligible_ticket(tmp_path)

    class BlockingShutdownGateway(_Gateway):
        def __init__(self) -> None:
            super().__init__()
            self.started = threading.Event()
            self.released = threading.Event()

        def run_ticket_step(
            self,
            employee_session_id: str | None,
            entity_id: str,
            prompt_text: str,
            on_employee_session_id=None,
            *,
            require_existing_session: bool = False,
        ) -> EmployeeStepRunResult:
            del employee_session_id, prompt_text, require_existing_session
            if on_employee_session_id is not None:
                on_employee_session_id("session-1")
            self.started.set()
            assert self.released.wait(2)
            return EmployeeStepRunResult("interrupted", "session-1", None)

        def interrupt(
            self,
            employee_session_id: str,
            entity_id: str,
            *,
            deadline: float | None = None,
        ) -> None:
            del deadline
            self.interrupts.append((employee_session_id, entity_id))
            self.released.set()

    shutdown_gateway = BlockingShutdownGateway()
    runner = _runner(db_path, shutdown_gateway)
    runner.try_run_automatic_step(ticket_id)
    assert shutdown_gateway.started.wait(2)

    runner.stop(deadline=monotonic() + 2)

    assert shutdown_gateway.interrupts == [("session-1", ticket_id)]
    conn = connect(db_path)
    original = SqliteEmployeeStepRepository().read_running(conn, ticket_id)
    stopped_ticket = tickets_data.read_ticket(conn, ticket_id)
    conn.close()
    assert original is not None
    assert original.employee_session_id == "session-1"
    assert stopped_ticket.ticket_status is TicketStatus.agent_running_step
    assert stopped_ticket.employee_session_id == "session-1"

    recovery_gateway = _Gateway()
    recovery_runner = _runner(db_path, recovery_gateway)
    recovery_runner.recover_running_step(ticket_id)
    assert recovery_runner.wait_idle(timeout=2)

    conn = connect(db_path)
    rows = conn.execute(
        "SELECT employee_step_id, status, employee_session_id "
        "FROM employee_step_runs WHERE ticket_id = ?",
        (ticket_id,),
    ).fetchall()
    conn.close()
    by_id = {str(row["employee_step_id"]): row for row in rows}
    assert len(by_id) == 2
    assert by_id[original.employee_step_id]["status"] == "interrupted"
    replacement = next(row for run_id, row in by_id.items() if run_id != original.employee_step_id)
    assert tuple(replacement)[1:] == ("complete", "session-1")
    assert recovery_gateway.calls[0][0] == "session-1"
    assert recovery_gateway.calls[0][3] is True

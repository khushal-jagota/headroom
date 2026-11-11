"""Admission proves launch pacing and destination isolation without live agents."""

from planner.conversation.start_admission import AutomaticStartAdmission
from planner.conversation.start_admission.contracts import StartRequest
from planner.execution_identity import TurnToken


def test_pacing_capacity_and_exact_acceptance_do_not_limit_running_turns():
    # Existing per-Ticket claim tests do not prove a global launch boundary.
    now = [0.0]
    admission = AutomaticStartAdmission(clock=lambda: now[0])
    for number in range(20):
        request = StartRequest(f"worker_{number}", "worker", "home")
        assert admission.try_start(request)
        token = TurnToken(f"conversation_{number}", 1)
        admission.bind((request.identity,), token)
        admission.observe(TurnToken(token.conversation_id, 2))
        assert len(admission.snapshots()) == 1
        admission.observe(token)
        # No turn-end proof is needed; twenty turns can remain active.
        assert not admission.try_start(StartRequest("too-soon", "worker", "home"))
        admission.finish("too-soon")
        now[0] += 1.0
    assert now[0] == 20.0


def test_supervisor_capacity_and_priority_and_canceled_pending_work():
    now = [0.0]
    admission = AutomaticStartAdmission(clock=lambda: now[0])
    for number in range(3):
        assert admission.try_start(StartRequest(f"worker_{number}", "worker", "home"))
        now[0] += 1
    assert not admission.try_start(StartRequest("fourth_worker", "worker", "home"))
    assert admission.try_start(StartRequest("chief", "supervisor", "home"))
    now[0] += 1
    assert not admission.try_start(
        StartRequest("manager_delivery_wake_pending", "supervisor", "home")
    )
    admission.finish("worker_0")
    assert not admission.try_start(StartRequest("fourth_worker", "worker", "home"))
    admission.retain_deferred("manager_delivery_wake_", set())
    assert admission.try_start(StartRequest("fourth_worker", "worker", "home"))
    assert admission.poll_delay(60) == 1


def test_recovery_superseded_by_running_turn_drops_its_pending_priority(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from planner.conversation.contracts import DeploymentRecoveryReference
    from planner.conversation.deploy_interruptions.coordinator import recover_conversations

    now = [0.0]
    admission = AutomaticStartAdmission(clock=lambda: now[0])
    assert admission.try_start(StartRequest("existing", "worker", "home"))
    system = SimpleNamespace(
        start_admission=admission,
        automatic_start_request=AsyncMock(
            side_effect=lambda identity, _conversation: StartRequest(identity, "supervisor", "home")
        ),
        is_running=AsyncMock(side_effect=[False, True]),
        send=AsyncMock(),
    )
    store = SimpleNamespace(
        select_deployment_recoveries=AsyncMock(
            return_value={"c": (DeploymentRecoveryReference("interruption"),)}
        )
    )

    async def advance(_delay):
        now[0] += 1

    monkeypatch.setattr(
        "planner.conversation.deploy_interruptions.coordinator.asyncio.sleep", advance
    )
    asyncio.run(recover_conversations(system, store))
    assert not any(s.request.identity == "recovery" for s in admission.snapshots())
    assert admission.try_start(StartRequest("next-worker", "worker", "home"))
    system.send.assert_not_called()


def test_prebind_recovery_send_failure_releases_start_slot():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from planner.conversation.contracts import DeploymentRecoveryReference
    from planner.conversation.deploy_interruptions.coordinator import recover_conversations

    admission = AutomaticStartAdmission()
    system = SimpleNamespace(
        start_admission=admission,
        automatic_start_request=AsyncMock(
            side_effect=lambda identity, _conversation: StartRequest(identity, "supervisor", "home")
        ),
        is_running=AsyncMock(return_value=False),
        send=AsyncMock(side_effect=RuntimeError("before bind")),
    )
    store = SimpleNamespace(
        select_deployment_recoveries=AsyncMock(
            return_value={"c": (DeploymentRecoveryReference("interruption"),)}
        )
    )
    asyncio.run(recover_conversations(system, store))
    assert admission.snapshots() == ()

from __future__ import annotations

import logging

import pytest

from planner.runtime.automatic_employee_step_eligibility_wake import (
    LoopAutomaticEmployeeStepEligibilityWake,
    NoOpAutomaticEmployeeStepEligibilityWake,
)


def test_loop_eligibility_wake_invokes_delivery_once_without_a_ticket_key() -> None:
    deliveries = 0

    def deliver() -> None:
        nonlocal deliveries
        deliveries += 1

    eligibility_wake = LoopAutomaticEmployeeStepEligibilityWake(deliver)

    eligibility_wake.wake()

    assert deliveries == 1
    with pytest.raises(TypeError):
        eligibility_wake.wake("t_not_part_of_the_contract")  # type: ignore[call-arg]


def test_loop_eligibility_wake_logs_delivery_failure_without_raising(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail() -> None:
        raise RuntimeError("wake failed")

    with caplog.at_level(logging.ERROR):
        LoopAutomaticEmployeeStepEligibilityWake(fail).wake()

    assert "automatic employee-step eligibility wake delivery failed" in caplog.text
    assert "wake failed" in caplog.text


def test_noop_eligibility_wake_returns_normally() -> None:
    eligibility_wake = NoOpAutomaticEmployeeStepEligibilityWake()
    eligibility_wake.wake()
    with pytest.raises(TypeError):
        eligibility_wake.wake("t_not_part_of_the_contract")  # type: ignore[call-arg]

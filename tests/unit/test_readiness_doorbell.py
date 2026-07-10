from __future__ import annotations

import logging

import pytest

from planner.runtime.readiness_doorbell import (
    LoopReadinessDoorbell,
    NoOpReadinessDoorbell,
)


def test_loop_doorbell_invokes_delivery_once_without_a_ticket_key() -> None:
    deliveries = 0

    def deliver() -> None:
        nonlocal deliveries
        deliveries += 1

    doorbell = LoopReadinessDoorbell(deliver)

    doorbell.ring()

    assert deliveries == 1
    with pytest.raises(TypeError):
        doorbell.ring("t_not_part_of_the_contract")  # type: ignore[call-arg]


def test_loop_doorbell_logs_delivery_failure_without_raising(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail() -> None:
        raise RuntimeError("wake failed")

    with caplog.at_level(logging.ERROR):
        LoopReadinessDoorbell(fail).ring()

    assert "readiness doorbell delivery failed" in caplog.text
    assert "wake failed" in caplog.text


def test_noop_doorbell_returns_normally() -> None:
    doorbell = NoOpReadinessDoorbell()
    doorbell.ring()
    with pytest.raises(TypeError):
        doorbell.ring("t_not_part_of_the_contract")  # type: ignore[call-arg]

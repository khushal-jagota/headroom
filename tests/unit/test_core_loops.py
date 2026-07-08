from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from planner.core import loops
from planner.core.clock import TestClock
from planner.core.config import load_config
from planner.minds.shared_gateway import SharedGateway


def test_start_background_loops_skips_system_a_when_dispatch_disabled(
    tmp_path: Path,
    fake_clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "dispatcher.lock"
    config = load_config(
        path=None,
        env={
            "PLAN_DISPATCH_ENABLED": "0",
            "PLAN_DB_PATH": str(tmp_path / "planning.db"),
            "PLAN_DISPATCHER_LOCK_PATH": str(lock_path),
        },
    )

    def fail_if_called(path: str) -> bool:
        raise AssertionError(f"lock should not be acquired when dispatch is disabled: {path}")

    monkeypatch.setattr(loops, "ensure_machine_lock", fail_if_called)
    handle = loops.start_background_loops(
        config,
        fake_clock,
        shared_gateway=cast(SharedGateway, object()),
    )
    try:
        assert handle.system_a is None
        assert not lock_path.exists()
    finally:
        asyncio.run(handle.stop())

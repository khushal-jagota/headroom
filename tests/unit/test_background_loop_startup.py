"""Legacy background admission cleans partial starts; publication preparation raises."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from planner.conversation.contracts import ConversationSystem
from planner.core import loops
from planner.core.clock import build_clock
from planner.core.config import load_config


@pytest.mark.parametrize("fail_during_reconciliation", [False, True])
def test_legacy_failure_returns_inert_owner_and_releases_every_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_during_reconciliation: bool,
) -> None:
    events: list[str] = []

    class Candidate:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.name = "candidate"

        def reconcile_imported_state(self) -> None:
            events.append("reconcile")
            if fail_during_reconciliation:
                raise RuntimeError("synthetic reconciliation failure")

        def start(self, _seconds: int) -> None:
            events.append("start")
            if events.count("start") == 3:
                raise RuntimeError("synthetic partial admission failure")

        def stop(self, *, deadline: float | None = None) -> None:
            events.append("stop")

        def wake(self) -> None:
            raise AssertionError("failed startup cannot subscribe to change wakes")

    for candidate in (
        "ScheduledTicketLoop",
        "WorkerStepReadinessLoop",
        "NotificationLoop",
        "ManagerWakeLoop",
    ):
        monkeypatch.setattr(loops, candidate, Candidate)
    cfg = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(tmp_path / "synthetic.db"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "polling.lock"),
        },
    )

    async def run() -> None:
        if fail_during_reconciliation:
            # The server calls this strict preparation seam before rotating its
            # publication epoch; only the legacy wrapper absorbs this failure.
            with pytest.raises(RuntimeError, match="synthetic reconciliation failure"):
                loops.prepare_background_loops(
                    cfg,
                    build_clock(cfg),
                    conversation_system=cast(ConversationSystem, object()),
                    asyncio_loop=asyncio.get_running_loop(),
                )
            assert loops._active is None
            events.clear()
        running = loops.start_background_loops(
            cfg,
            build_clock(cfg),
            conversation_system=cast(ConversationSystem, object()),
            asyncio_loop=asyncio.get_running_loop(),
        )
        assert loops._active is running
        assert running.worker_step_readiness_loop is None
        assert running.scheduled_ticket_loop is None
        assert running.notification_loop is None
        assert running.manager_wake_loop is None
        assert events.count("stop") == 4
        assert events.count("start") == (0 if fail_during_reconciliation else 3)
        subprocess.run(
            [
                sys.executable,
                "-c",
                "import fcntl,sys; f=open(sys.argv[1], 'a'); "
                "fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)",
                cfg.dispatcher_lock_path,
            ],
            check=True,
        )
        with pytest.raises(RuntimeError, match="already running"):
            loops.start_background_loops(
                cfg,
                build_clock(cfg),
                conversation_system=cast(ConversationSystem, object()),
                asyncio_loop=asyncio.get_running_loop(),
            )
        await running.stop()
        assert loops._active is None

    asyncio.run(run())

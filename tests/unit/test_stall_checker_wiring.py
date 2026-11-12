"""The server builds the stall checker with the Mac's stall sources, and runs it for its life.

Risk: the checker is built with the wrong database, registry or clock, never sees the
executor's sources (so no Mac stall ever alarms), is never started, or is left running
at shutdown. Gap: the checker itself (Part E's stalls/checker.py) is tested on its own;
nothing proves core.server wires it. Layer: unit, the real create_app lifespan in test
mode through TestClient, with a recording factory in place of the checker. Setup reused:
the configuration of test_executor_mode.py, with an executor ID so the endpoint exists.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from fastapi.testclient import TestClient

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect
from planner.core.server import create_app
from planner.executor.clock import now_ms
from planner.executor.contracts import StallSource
from planner.executor.vps_presence import PresenceStallSource
from planner.executor.vps_transport import VpsExecutor
from planner.stalls.contracts import WaitRegistry
from planner.stalls.registry import WAITS


@dataclass
class _RecordedChecker:
    db_path: str
    registry: WaitRegistry
    sources: Callable[[], tuple[StallSource, ...]]
    clock: Callable[[], int]
    events: list[str] = field(default_factory=list)

    def start(self) -> None:
        self.events.append("start")

    async def close(self) -> None:
        self.events.append("close")


def test_the_server_runs_one_stall_checker_over_the_executor_sources(tmp_path: Path) -> None:
    configuration = tmp_path / "config.yaml"
    configuration.write_text("executor_id: owner-mac\n")
    config = load_config(
        str(configuration),
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(tmp_path / "planning.db"),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_HERMES_HOME": str(tmp_path / "hermes"),
            "PLAN_DISPATCH_ENABLED": "0",
        },
    )
    # A fresh database: the endpoint is built before the lifespan migrates it, so
    # nothing may read executor rows until startup has run.
    built: list[_RecordedChecker] = []

    def factory(
        db_path: str,
        registry: WaitRegistry,
        sources: Callable[[], tuple[StallSource, ...]],
        clock: Callable[[], int],
    ) -> _RecordedChecker:
        built.append(_RecordedChecker(db_path, registry, sources, clock))
        return built[-1]

    app = create_app(
        config,
        build_clock(config),
        lambda: connect(config.db_path),
        stall_checker_factory_for_test=factory,
    )
    with TestClient(app):
        (checker,) = built
        assert checker.events == ["start"]
        assert checker.db_path == config.db_path
        assert checker.registry is WAITS
        endpoint = app.state.executor_endpoint
        assert isinstance(endpoint, VpsExecutor)
        sources = checker.sources()
        assert [type(source) for source in sources] == [
            type(source) for source in endpoint.stall_sources()
        ]
        # This endpoint's own memory, not a copy built elsewhere.
        (presence_source,) = [s for s in sources if isinstance(s, PresenceStallSource)]
        assert presence_source._presence is endpoint.presence
        before = now_ms()
        assert before <= checker.clock() <= now_ms()
    assert checker.events == ["start", "close"]

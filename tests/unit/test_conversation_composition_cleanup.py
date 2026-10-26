"""Composition failures must not strand a successfully constructed runtime owner.

These injected constructors prove only retained cleanup seams, not domain lease
adoption, producer policy, bootstrap migration order, or assembled startup.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from planner.core import server
from planner.core.config import load_config


class CompositionFailure(RuntimeError):
    pass








def test_idle_managed_connection_refreshes_functions_across_runtime_generations(
    tmp_path: Path,
) -> None:
    """Admission must replace absent and stale callbacks before protected source SQL."""
    from planner.conversation.runtime_composition import SignalRuntimeComposition
    from planner.core.db import connect
    from planner.navigation.delivery import create_delivery_runtime
    from planner.navigation.signal_runtime import SignalNavigationBinding

    path = str(tmp_path / "retained-connection.db")
    idle = connect(path)
    epochs: list[str] = []
    try:
        for identity in ("first", "second"):
            composition = SignalRuntimeComposition(path, 100)
            composition.bootstrap()
            composition.coordinator.bind_runtime(lambda _identity: None)
            composition.initialize()
            binding = SignalNavigationBinding(
                composition.coordinator, create_delivery_runtime(path)
            )
            try:
                binding.attach()
                composition.coordinator.admit_runtime()
                idle.execute("BEGIN IMMEDIATE")
                with pytest.raises(RuntimeError, match="active transaction"):
                    composition.close()
                assert not composition._closed
                assert composition.ownership is not None and not composition.ownership.closed
                idle.execute(
                    "INSERT INTO conversations"
                    "(conversation_id,backend_key,workspace_folder,access,created_at) "
                    "VALUES(?,'codex','/synthetic','full',0)",
                    (identity,),
                )
                idle.commit()
                publication = idle.execute(
                    "SELECT runtime_epoch FROM navigation_signal_publications "
                    "WHERE conversation_id=?",
                    (identity,),
                ).fetchone()
                assert publication is not None
                assert publication[0] == composition.coordinator.runtime_epoch
                epochs.append(str(publication[0]))
            finally:
                binding.close()
                composition.close()
        assert epochs[0] != epochs[1]
    finally:
        idle.close()


def test_composed_lifetime_invalidates_before_failed_dependent_stop_and_drains_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed HTTP cleanup callback must not bypass the actual runtime owner."""
    from planner.conversation.lifecycle_storage import acquire_runtime_ownership
    from planner.core.clock import build_clock
    from planner.core.db import connect

    path = str(tmp_path / "callback-failure.db")
    config = load_config(
        None,
        env={
            "PLAN_DB_PATH": path,
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_HERMES_HOME": str(tmp_path / "hermes"),
            "PLAN_TEST_MODE": "1",
        },
    )
    app = server.create_app(config, build_clock(config), lambda: connect(path))
    events: list[str] = []

    async def exercise() -> None:
        context = app.router.lifespan_context(app)
        await context.__aenter__()
        runtime = app.state.conversation
        source = runtime.store.signal_coordinator
        assert source is not None
        original_shutdown = runtime.system.shutdown
        original_close = app.state.navigation_delivery.close

        async def failed_dependent_stop() -> None:
            assert source.signal_availability().state == "unavailable"
            assert not runtime.system._runtime_ownership.closed
            assert source._participant is not None
            events.append("dependent stop")
            await original_close()
            raise CompositionFailure("dependent stop failed")

        async def observe_shutdown() -> None:
            events.append("system drain")
            assert source._participant is not None
            await original_shutdown()

        monkeypatch.setattr(app.state.navigation_delivery, "close", failed_dependent_stop)
        monkeypatch.setattr(runtime.system, "shutdown", observe_shutdown)
        with pytest.raises(CompositionFailure, match="dependent stop failed"):
            await context.__aexit__(None, None, None)
        assert events == ["dependent stop", "system drain"]
        assert runtime.system._runtime_ownership.closed
        assert source._participant is None
        assert app.state.conversation is None
        successor = acquire_runtime_ownership(path)
        successor.close()

    asyncio.run(exercise())

"""Composition failures must not strand a successfully constructed runtime owner.

These injected constructors prove only retained cleanup seams, not domain lease
adoption, producer policy, bootstrap migration order, or assembled startup.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from planner.conversation import api
from planner.core import server
from planner.core.clock import Clock
from planner.core.config import load_config


class CompositionFailure(RuntimeError):
    pass


@pytest.mark.parametrize("failure", ["system", "later_component", None])
def test_runtime_cleanup_tracks_successful_system_transfer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str | None
) -> None:
    events: list[str] = []
    borrowed = (tmp_path / "synthetic-owner.lock").open("w+b")

    class System:
        def __init__(self, **kwargs: Any) -> None:
            if failure == "system":
                raise CompositionFailure("system construction")
            events.append("constructed")

        async def shutdown(self) -> None:
            events.append("system shutdown")
            borrowed.close()

    class SignIn:
        def __init__(self, **kwargs: Any) -> None:
            if failure == "later_component":
                raise CompositionFailure("later component")

        async def shutdown(self) -> None:
            events.append("sign-in shutdown")

    monkeypatch.setattr(api, "SqliteProcessConversationSystem", System)
    monkeypatch.setattr(api, "CodexSignInService", SignIn)
    monkeypatch.setattr(
        api,
        "ConversationLiveTail",
        lambda: SimpleNamespace(close_all_subscriptions=lambda: events.append("tail closed")),
    )
    for name in (
        "ConversationStore",
        "ConversationMessageFiles",
        "BackendSnapshotService",
        "BackendStateStore",
        "ModelResolutionService",
        "ModelResolutionStore",
    ):
        monkeypatch.setattr(api, name, lambda *args, **kwargs: object())
    monkeypatch.setattr(api, "production_backend_usage_service", lambda: object())

    async def compose() -> None:
        async with AsyncExitStack() as cleanup:
            api.build_conversation_runtime(
                db_path=str(tmp_path / "synthetic.db"),
                db_busy_timeout_ms=100,
                sse_heartbeat_ms=100,
                backend_child_factories={},
                cleanup=cleanup,
            )
            events.append("runtime complete")

    try:
        if failure is None:
            asyncio.run(compose())
            assert events == [
                "constructed",
                "runtime complete",
                "tail closed",
                "system shutdown",
                "sign-in shutdown",
            ]
            assert borrowed.closed
        else:
            with pytest.raises(CompositionFailure):
                asyncio.run(compose())
            if failure == "system":
                assert events == []
                assert not borrowed.closed  # Constructor failure transfers no ownership.
            else:
                assert events == ["constructed", "system shutdown"]
                assert borrowed.closed
    finally:
        borrowed.close()


def test_application_stack_drains_failure_before_runtime_return(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_clock: Clock
) -> None:
    events: list[str] = []

    async def shutdown() -> None:
        events.append("drained")

    def fail_after_transfer(*, cleanup: AsyncExitStack, **kwargs: Any) -> None:
        cleanup.push_async_callback(shutdown)
        raise CompositionFailure("composition after transfer")

    monkeypatch.setattr(server, "build_conversation_runtime", fail_after_transfer)
    monkeypatch.setattr(server, "DurableCodexLoginRejection", lambda *args, **kwargs: object())
    config = load_config(
        None,
        env={
            "PLAN_DB_PATH": str(tmp_path / "runtime" / "synthetic.db"),
            "PLAN_LOGS_DIR": str(tmp_path / "runtime" / "logs"),
            "PLAN_TEST_MODE": "1",
        },
    )
    app = server.create_app(config, fake_clock, lambda: pytest.fail("No SQL is admitted"))

    async def enter() -> None:
        async with app.router.lifespan_context(app):
            pytest.fail("Failed composition cannot enter service")

    with pytest.raises(CompositionFailure):
        asyncio.run(enter())
    assert events == ["drained"]


@pytest.mark.parametrize("after_transfer", [False, True])
def test_actual_bootstrap_owner_cleanup_before_and_after_system_transfer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, after_transfer: bool
) -> None:
    """The actual borrowed lease must survive only until its assigned cleanup owner."""
    from planner.conversation.contracts import ConversationBackendKey
    from planner.conversation.lifecycle_storage import acquire_runtime_ownership
    from planner.conversation.runtime_composition import SignalRuntimeComposition

    path = str(tmp_path / "actual-owner.db")
    composition = SignalRuntimeComposition(path, 100)
    composition.bootstrap()
    borrowed = composition.ownership
    assert borrowed is not None

    def no_child(**kwargs: Any) -> Any:
        pytest.fail("No provider child belongs in composition cleanup")

    if after_transfer:

        def fail_sign_in(**kwargs: Any) -> Any:
            raise CompositionFailure("after actual ownership transfer")

        monkeypatch.setattr(api, "CodexSignInService", fail_sign_in)

    async def compose() -> None:
        async with AsyncExitStack() as cleanup:
            cleanup.callback(composition.close)
            api.build_conversation_runtime(
                db_path=path,
                db_busy_timeout_ms=100,
                sse_heartbeat_ms=100,
                backend_child_factories=(
                    {key: no_child for key in ConversationBackendKey} if after_transfer else {}
                ),
                signal_composition=composition,
                cleanup=cleanup,
            )
            pytest.fail("Composition failure must not return a runtime")

    error = CompositionFailure if after_transfer else ValueError
    with pytest.raises(error):
        asyncio.run(compose())
    assert borrowed.closed
    # This acquires the real lock without an unguarded source reconciliation.
    successor = acquire_runtime_ownership(path)
    successor.close()


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

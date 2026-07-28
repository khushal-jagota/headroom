"""Shared fixtures for the unit suite. Owned by the orchestrator (glue);
ticket agents use these read-only and add their own fixtures locally.
"""

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection
from typing import Never

import pytest

from planner.conversation.backends.contracts import BackendEventSink
from planner.conversation.contracts import ConversationBackendKey, ResolvedConversationStart
from planner.conversation.message_files import ConversationMessageFiles
from planner.core.clock import TestClock
from planner.core.config import Config, load_config
from planner.core.db import connect, create_schema
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION
from planner.worker_types.configuration import (
    ConfiguredWorkerRuntimeDefinitions,
    configured_worker_type_registry,
    install_worker_runtime_definitions_for_test,
    restore_worker_runtime_definitions_for_test,
)
from planner.worker_types.registry import WorkerTypeRegistry


@pytest.fixture
def cfg() -> Config:
    """Default configuration, isolated from process env."""
    return load_config(path=None, env={})


@pytest.fixture
def tmp_db(tmp_path: Path) -> Iterator[Connection]:
    """A fresh schema on a temp SQLite file."""
    conn = connect(str(tmp_path / "planning-test.db"))
    create_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def fake_clock() -> TestClock:
    """Mutable clock pinned to a mid-day baseline; tests move it as needed."""
    return TestClock(datetime(2026, 7, 4, 12, 0, 0).astimezone())


@pytest.fixture
def planning_worker_registry() -> Iterator[None]:
    """Install coding-shaped planning types only for authorization boundary tests."""
    current = configured_worker_type_registry()
    production = tuple(
        current.require(worker_type) for worker_type in current.registered_worker_types()
    )
    planning = tuple(
        replace(
            CODING_WORKER_TYPE_DEFINITION,
            worker_type=worker_type,
            label=worker_type,
        )
        for worker_type in (
            "planning-day",
            "planning-midday-check",
            "planning-sprint",
        )
    )
    registry = WorkerTypeRegistry(
        production + planning,
        known_skills=frozenset(
            definition.worker_profile.specialist_skill
            for definition in production + planning
        )
        | {"panels-worker"},
        known_toolset_profiles=frozenset({"default"}),
    )
    previous = install_worker_runtime_definitions_for_test(
        ConfiguredWorkerRuntimeDefinitions(registry)
    )
    try:
        yield
    finally:
        restore_worker_runtime_definitions_for_test(previous)


class RealBackendChildInAUnitTest(AssertionError):
    """A unit test was one step away from starting a real agent."""


def _refuse_to_make_a_real_backend_child(
    backend_key: ConversationBackendKey,
) -> object:
    def make_child(
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> Never:
        del event_sink
        raise RealBackendChildInAUnitTest(
            f"This test composed the real {backend_key} backend and asked it for a child "
            f"for conversation {resolved_start.conversation_id}, which is one step from "
            f"starting {backend_key} on this machine.\n"
            f"\n"
            f"An application built in test mode runs the real conversation system, the "
            f"same one production runs. A test that drives workers wants one it can hold "
            f"still, so pass it in:\n"
            f"\n"
            f"    create_app(\n"
            f"        config,\n"
            f"        clock,\n"
            f"        conn_factory,\n"
            f"        conversation_system_for_test=InMemoryConversationSystem(),\n"
            f"    )\n"
            f"\n"
            f"from planner.conversation.in_memory_conversation_system. If this test "
            f"genuinely means to reach a vendor CLI, it belongs with the opt-in exercises "
            f"that are gated behind a PANELS_REAL_* variable, not in the ordinary suite."
        )

    return make_child


@pytest.fixture(autouse=True)
def _no_real_backend_children(monkeypatch: pytest.MonkeyPatch) -> None:
    """The unit suite cannot make a real backend child, rather than merely not doing so.

    Two tests once reached real agents while passing green, which costs time and real API
    calls and proves less than it appears to. An audit of that expires the moment somebody
    writes the next test; this does not.

    It replaces the factories the application composes, so it catches exactly the mistake
    it is for: a test that builds an app and drives a conversation through it without
    saying which conversation system it wants. The opt-in exercises that mean to reach a
    vendor CLI are untouched, because they build their adapters directly and never go
    through the application's composition at all.
    """
    monkeypatch.setattr(
        "planner.core.server.production_backend_child_factories",
        lambda **_composed_against_this_machine: {
            key: _refuse_to_make_a_real_backend_child(key) for key in ConversationBackendKey
        },
    )

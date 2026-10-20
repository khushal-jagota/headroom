"""What answering one backend card costs, counted in connections rather than timed.

The backend card request measures 118-149 ms warm, every call, and the reason was not
where anyone looked. The probe behind it is kept in the process and costs half a
millisecond; what costs the 126 ms is that answering about one backend opens a fresh
database connection per model, three times over, and this schema charges 2.39 ms for the
first statement on a fresh connection against 0.022 ms for the second, because the
connection has to load 80 tables before it can plan anything.

Nothing in the repository notices that, and nothing would: every correctness test passes
whether one answer opens one connection or eighty, so the cost grew without a failing
test. Timing it would not help either, because load on this machine moved between 0.8 and
5.1 while these numbers were being taken. So this counts the connections.

The ceilings are today's cost, not a target. They come down when the three loops below
read sets instead of rows.

How to read a failure here is written once, in the module docstring of
tests/unit/test_conversation_view_budget.py. The short of it: a failure is a question, not
a number to adjust, and a ceiling that rises without the arithmetic written beside it stops
being a gate.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from tests.unit.test_current_model_resolution import Catalogue, setup

from planner.conversation.api import _snapshot_json
from planner.conversation.backend_state import BackendStateStore
from planner.conversation.contracts import ConversationBackendKey as Backend
from planner.conversation.snapshot import BackendModel

DOORS = (
    "planner.conversation.model_resolution.data",
    "planner.conversation.backend_state",
)
"""The modules that open a connection to answer about a backend."""


def catalogue_of(models: int) -> Catalogue:
    """A backend offering `models` models across two families, as codex really does."""
    names = [f"gpt-6.{index}-{'sol' if index % 2 else 'astra'}" for index in range(models)]
    return Catalogue(
        tuple(BackendModel(name, reasoning_effort_options=("medium",)) for name in names)
    )


class Connections:
    """Counts connections opened, and statements run on them, inside its block.

    It patches and restores the doors itself rather than taking monkeypatch, because two
    of these are used in one test: a patch that outlived its counter would charge the
    second measurement's setup to the first.
    """

    def __init__(self) -> None:
        self.opened = 0
        self.statements = 0
        self._restore: list[tuple[Any, str, Any]] = []

    def __enter__(self) -> Connections:
        import importlib

        from planner.core.db import connect

        def open_one(*args: Any, **kwargs: Any) -> sqlite3.Connection:
            conn = connect(*args, **kwargs)
            self.opened += 1
            conn.set_trace_callback(self._charge)
            return conn

        for name in DOORS:
            module = importlib.import_module(name)
            self._restore.append((module, "connect", module.connect))
            module.connect = open_one  # type: ignore[attr-defined]
        return self

    def _charge(self, _statement: str) -> None:
        self.statements += 1

    def __exit__(self, *_: object) -> None:
        for module, attribute, original in self._restore:
            setattr(module, attribute, original)


def answer(tmp_path: Path, models: int) -> Connections:
    """Answer one backend card the way the endpoint does, and report what it cost."""
    catalogue = catalogue_of(models)
    service, path = setup(tmp_path, catalogue)
    state = BackendStateStore(str(path))
    snapshot = asyncio.run(
        catalogue.snapshot(Backend.codex, refresh=True, discover_update_advisory=False)
    )
    # The first answer is the one that populates the durable resolutions, so the cost
    # being counted is the second: a warm card, which is every card after boot.
    _snapshot_json(service.decorate_snapshot(snapshot), state)

    with Connections() as counted:
        payload = _snapshot_json(service.decorate_snapshot(snapshot), state)
    assert len(payload["available_models"]) == models
    return counted


def test_one_warm_backend_card_costs_what_it_costs_today(tmp_path: Path) -> None:
    cost = answer(tmp_path, 26)
    # Today: 26 models cost 5 connections and 10 statements. Before the three per-model
    # loops became set reads it was 81 connections and 87 statements for the same 26, and
    # production measured 78 connections and 492 statements.
    #
    # The statement count is the one to read carefully: it counts statements traced on a
    # connection, and a connection cannot be traced until it exists, so the three PRAGMAs
    # every one of them runs while opening are not in it. That is 15 more here, and was
    # 243 before, which is most of the difference from production's 492.
    #
    # The five are five different doors, not five rounds of the same question: the stored
    # evidence, the enablement set, the write that adopts a changed catalogue, the usage
    # snapshot, and the enablement set again while serialising. Collapsing the last one
    # would mean handing a connection through a service interface that other callers use
    # for other reasons, which is a larger change than the remaining 2 ms is worth.
    assert cost.opened <= 8, f"a warm backend card now opens {cost.opened} connections"
    assert cost.statements <= 15, f"a warm backend card now runs {cost.statements} statements"


def test_describing_a_backend_costs_the_same_whatever_its_model_count(tmp_path: Path) -> None:
    """The defect this found, now stated as the property that must hold.

    One backend is one row of state and one catalogue, so answering about it should cost
    what one backend costs. It used to add three connections per model: one to adopt the
    catalogue, one to read that model's catalogue row, and one to ask whether it was
    enabled. Measured then: 27 connections for 8 models and 75 for 24, a slope of exactly
    three. Measured now: 5 and 5, a slope of zero.

    A per-model loop is easy to reintroduce and invisible to every other test, which is
    how three of them accumulated, so the gate is the slope rather than a total.
    """
    few = answer(tmp_path / "few", 8)
    many = answer(tmp_path / "many", 24)
    per_model = (many.opened - few.opened) / (24 - 8)
    assert per_model == 0, (
        f"each extra model now adds {per_model:.2f} connections to a backend card: "
        f"{few.opened} connections for 8 models, {many.opened} for 24"
    )
    statements = (many.statements - few.statements) / (24 - 8)
    assert statements == 0, (
        f"each extra model now adds {statements:.2f} statements to a backend card: "
        f"{few.statements} for 8 models, {many.statements} for 24"
    )

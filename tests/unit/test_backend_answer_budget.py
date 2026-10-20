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
    # Today: 26 models cost 81 connections and 87 statements. Production measures 78
    # connections for its 26, the difference being models whose family is unrecognised.
    #
    # The statement count is the one to read carefully: it counts statements traced on a
    # connection, and a connection cannot be traced until it exists, so the three PRAGMAs
    # every one of them runs while opening are not in it. At this connection count those
    # are another 243 statements, which is most of the 492 production measures.
    assert cost.opened <= 85, f"a warm backend card now opens {cost.opened} connections"
    assert cost.statements <= 100, f"a warm backend card now runs {cost.statements} statements"


def test_answering_about_one_backend_opens_a_connection_per_model(tmp_path: Path) -> None:
    """The defect the measurement found, stated as the property that is wrong.

    One backend is one row of state and one catalogue. Answering about it should cost
    what one backend costs, so adding a model to the backend should not add a connection
    to the answer. Today it adds three: one to adopt the catalogue, one to read the
    model's own catalogue row, and one to ask whether it is enabled.

    The ceiling is a slope, because that is what is wrong here. It goes to zero when the
    three loops read sets.
    """
    few = answer(tmp_path / "few", 8)
    many = answer(tmp_path / "many", 24)
    # Today: 27 connections for 8 models, 75 for 24. Three per model exactly, which is
    # the three loops named above and nothing else.
    per_model = (many.opened - few.opened) / (24 - 8)
    assert per_model <= 3.5, (
        f"each extra model now adds {per_model:.2f} connections to a backend card: "
        f"{few.opened} connections for 8 models, {many.opened} for 24"
    )

"""Current-sprint response semantics at the planning-day boundary."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from sqlite3 import Connection
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from planner.core.clock import TestClock as MutableClock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.sprints.data import create_sprint


def _make_app(tmp_path: Path) -> tuple[TestClient, MutableClock]:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    clock = MutableClock(
        datetime(2026, 8, 10, 4, 59, tzinfo=ZoneInfo("Europe/Berlin"))
    )
    create_sprint(
        boot,
        name="Previous week",
        date_start="2026-08-03",
        date_end="2026-08-09",
        clock=clock,
    )
    create_sprint(
        boot,
        name="Next week",
        date_start="2026-08-10",
        date_end="2026-08-16",
        clock=clock,
    )
    boot.close()
    config = load_config(
        path=None,
        env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)},
    )

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return TestClient(create_app(config, clock, conn_factory)), clock


def test_current_sprint_response_changes_planning_date_at_0500(tmp_path: Path) -> None:
    client, clock = _make_app(tmp_path)

    with client:
        before_boundary = client.get("/api/sprint/current")
        clock.set(datetime(2026, 8, 10, 5, 0, tzinfo=ZoneInfo("Europe/Berlin")))
        at_boundary = client.get("/api/sprint/current")

    assert before_boundary.status_code == 200
    assert before_boundary.json()["planning_date"] == "2026-08-09"
    assert before_boundary.json()["sprint"]["name"] == "Previous week"
    assert at_boundary.status_code == 200
    assert at_boundary.json()["planning_date"] == "2026-08-10"
    assert at_boundary.json()["sprint"]["name"] == "Next week"

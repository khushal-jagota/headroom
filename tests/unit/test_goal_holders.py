"""Holder moves retain authored records; workspace versions cover their goal bodies."""

from dataclasses import asdict
from sqlite3 import Connection
from types import SimpleNamespace
from typing import cast

import pytest
from starlette.requests import Request
from tests.unit.test_goal_pages import create, page
from tests.unit.test_manual_measures import manual_measure, replacement

from planner.core.clock import TestClock as Clock
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.errors import PlannerError
from planner.goals import actions
from planner.managers.contracts import ManagerWorkspaceDocument
from planner.managers.data import _require_manager_can_delete, create_manager, read_manager
from planner.managers.pages import page_readings, read_page, write_page
from planner.managers.resource_versions import ManagerDocumentResources
from planner.measures import manual


def test_promotion_preserves_goal_record_and_owner_control(
    tmp_db: Connection, fake_clock: Clock
) -> None:
    goal = create(tmp_db)
    manager_id, moved = actions.make_manager(
        tmp_db, goal.id, expected_revision=goal.revision,
        principal=CHIEF_PRINCIPAL, clock=fake_clock,
    )
    changed = {"holder_kind", "holder_id", "revision", "updated_at"}
    assert {key: value for key, value in asdict(moved).items() if key not in changed} == {
        key: value for key, value in asdict(goal).items() if key not in changed
    }
    manager = read_manager(tmp_db, manager_id).item
    assert (manager.title, manager.project_id) == (goal.title, goal.project_id)
    with pytest.raises(PlannerError, match="holds goals"):
        _require_manager_can_delete(tmp_db, manager_id)


def test_manager_page_survives_goal_cardinality_and_rejects_stale_write(
    tmp_db: Connection, fake_clock: Clock
) -> None:
    manager = create_manager(tmp_db, title="Guide", project_id="project_vylo", clock=fake_clock)
    actor = Principal(PrincipalKind.manager, manager.id)
    saved = write_page(
        tmp_db, manager.id, {"page": page(), "expected_revision": 0}, principal=actor
    )
    create(tmp_db, holder_kind="manager", holder_id=manager.id)
    second = create(tmp_db, holder_kind="manager", holder_id=manager.id)
    actions.update_goal(
        tmp_db, second.id,
        {"expected_revision": second.revision, "holder_kind": "chief", "holder_id": None},
        principal=OWNER_PRINCIPAL, now=fake_clock.now_unix(),
    )
    with pytest.raises(PlannerError, match="page changed"):
        write_page(tmp_db, manager.id, {"page": None, "expected_revision": 0}, principal=actor)
    assert read_page(tmp_db, manager.id) == saved


def test_holder_page_writer_validates_bound_measure(
    tmp_db: Connection, fake_clock: Clock
) -> None:
    measure = manual_measure(tmp_db)
    day = fake_clock.now().date().isoformat()
    manual.write_value(
        tmp_db, measure.id, replacement("page-read", day, 7, 0),
        principal=OWNER_PRINCIPAL, now=fake_clock.now_unix(),
    )
    document = {**page(), "blocks": [
        {"id": "chart", "type": "chart", "kind": "line", "measure_id": measure.id}
    ]}
    write_page(
        tmp_db, None, {"page": document, "expected_revision": 0}, principal=CHIEF_PRINCIPAL
    )
    result = page_readings(tmp_db, None, fake_clock.now())
    assert (
        next(value for value in result["values"] if value["measure_id"] == measure.id)["value"] == 7
    )
    document["blocks"][0]["measure_id"] = "missing-measure"
    with pytest.raises(PlannerError):
        write_page(
            tmp_db, None, {"page": document, "expected_revision": 1}, principal=CHIEF_PRINCIPAL
        )
    assert read_page(tmp_db, None)["page"]["blocks"][0]["measure_id"] == measure.id


def test_manager_workspace_goals_share_snapshot_and_resource_revision(
    tmp_db: Connection, fake_clock: Clock
) -> None:
    manager = create_manager(tmp_db, title="Held", project_id="project_vylo", clock=fake_clock)
    goal = create(tmp_db, holder_kind="manager", holder_id=manager.id)
    request = Request({
        "type": "http", "headers": [],
        "app": SimpleNamespace(state=SimpleNamespace(clock=fake_clock)),
    })

    def read_workspace() -> tuple[str, ManagerWorkspaceDocument]:
        snapshot = ManagerDocumentResources().capture(
            tmp_db, request, f"/api/managers/{manager.id}/workspace-content"
        )
        assert snapshot is not None
        return snapshot.revision, cast(ManagerWorkspaceDocument, snapshot.read())

    before, document = read_workspace()
    assert document["goals"][0]["page"] == goal.page
    edited = actions.update_goal(
        tmp_db, goal.id, {"expected_revision": goal.revision, "why": "Accepted rationale"},
        principal=OWNER_PRINCIPAL, now=fake_clock.now_unix(),
    )
    after_edit, document = read_workspace()
    assert after_edit != before and document["goals"][0]["why"] == "Accepted rationale"
    actions.update_goal(
        tmp_db, goal.id,
        {"expected_revision": edited.revision, "holder_kind": "chief", "holder_id": None},
        principal=OWNER_PRINCIPAL, now=fake_clock.now_unix(),
    )
    after_move, document = read_workspace()
    assert after_move != after_edit and document["goals"] == []

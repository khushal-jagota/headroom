"""t_tt04a Seam 5 — per-type "in progress by stage" + its permanent goldens.

``derive_sprint_item_status`` no longer holds a hardcoded coding mid-stage set; it
consumes a precomputed ``stage_in_progress`` boolean per child, computed at the
caller (``read_item``) from the child's OWN type via ``_child_stage_in_progress``:
non-terminal AND strictly past the type's default ceiling (its first worker stage).

Two PERMANENT goldens pin that the derivation cannot drift:
- ``_CODING_STATE_IN_PROGRESS`` — the exact coding bucket map (reproducing the old
  ``_IN_PROGRESS_STATES`` = {needs_approach, needs_plan, needs_implementation,
  needs_closeout}, with done/dropped/kickoff/success NOT in progress);
- the ``derive_sprint_item_status`` verdict for a single-child item at each coding
  stage.

A probe child in a mid-stage classifies correctly, and the roll-up flows through the
real ``read_item`` door. This module carries its OWN local ``probe_registry`` fixture
(Codex F3); probe children go in through the real create_ticket door.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from sqlite3 import Connection

import pytest
from tests.support.probe import (
    FIELD_ALPHA,
    NEEDS_BETA,
    install_probe_registry,
    uninstall_probe_registry,
)

from planner.core.clock import TestClock
from planner.sprints.contracts import ItemStatus
from planner.sprints.data import _child_stage_in_progress, create_item, read_item
from planner.sprints.logic import SprintItemChildStatus, derive_sprint_item_status
from planner.ticket_types.contracts import WorkflowDefinition
from planner.tickets.contracts import AtCap, FieldName
from planner.tickets.data import accept_proposal, create_ticket, file_proposal

# PERMANENT GOLDEN 1 — coding's "in progress by stage" map, every stage + the two
# terminals. Reproduces the retired _IN_PROGRESS_STATES exactly; done/dropped are
# False WITHOUT raising (dropped is outside the linear order — the F1 short-circuit).
_CODING_STATE_IN_PROGRESS: dict[str, bool] = {
    "needs_kickoff": False,
    "needs_success": False,
    "needs_approach": True,
    "needs_plan": True,
    "needs_implementation": True,
    "needs_closeout": True,
    "done": False,
    "dropped": False,
}


@pytest.fixture
def probe_registry() -> Iterator[WorkflowDefinition]:
    definition = install_probe_registry()
    try:
        yield definition
    finally:
        uninstall_probe_registry()


def _child(stage: str, *, worker_type: str = "coding") -> SprintItemChildStatus:
    return SprintItemChildStatus(
        stage=stage,
        ticket_status="empty",
        blocked=False,
        stage_in_progress=_child_stage_in_progress(worker_type, stage),
    )


def test_stage_in_progress_coding_buckets_golden() -> None:
    # The child-level primitive matches the permanent golden for every coding stage,
    # and done/dropped do not raise.
    for stage, expected in _CODING_STATE_IN_PROGRESS.items():
        assert _child_stage_in_progress("coding", stage) is expected


def test_derive_sprint_item_status_coding_buckets_golden() -> None:
    # A single-child item at each coding stage derives the pinned ItemStatus. The four
    # mid-states roll up in_progress; kickoff/success roll up todo (no other signal);
    # a lone done child rolls up done; a lone dropped child rolls up todo.
    expected_status = {
        "needs_kickoff": ItemStatus.todo,
        "needs_success": ItemStatus.todo,
        "needs_approach": ItemStatus.in_progress,
        "needs_plan": ItemStatus.in_progress,
        "needs_implementation": ItemStatus.in_progress,
        "needs_closeout": ItemStatus.in_progress,
        "done": ItemStatus.done,
        "dropped": ItemStatus.todo,
    }
    for stage, status in expected_status.items():
        assert derive_sprint_item_status(directly_blocked=False, children=[_child(stage)]) is status


def test_probe_midstage_rolls_up(probe_registry: WorkflowDefinition) -> None:
    # needs_beta is strictly past probe's default ceiling (needs_alpha) -> in progress;
    # needs_alpha (== the ceiling) and needs_kickoff are not.
    assert _child_stage_in_progress("probe", NEEDS_BETA) is True
    assert _child_stage_in_progress("probe", "needs_alpha") is False
    assert _child_stage_in_progress("probe", "needs_kickoff") is False

    assert (
        derive_sprint_item_status(
            directly_blocked=False, children=[_child(NEEDS_BETA, worker_type="probe")]
        )
        is ItemStatus.in_progress
    )
    assert (
        derive_sprint_item_status(
            directly_blocked=False, children=[_child("needs_alpha", worker_type="probe")]
        )
        is ItemStatus.todo
    )


def test_rollup_probe_child_via_read_item(
    tmp_db: Connection, probe_registry: WorkflowDefinition
) -> None:
    clock = TestClock(datetime(2026, 7, 4, 12, 0, 0).astimezone())
    item = create_item(tmp_db, title="Item", project_id="project_vylo", clock=clock)

    probe = create_ticket(
        tmp_db,
        title="Probe child",
        actor="human",
        now=1,
        title_max_chars=200,
        worker_type="probe",
        sprint_item_id=item.id,
    )
    # Drive the probe child to needs_beta (a mid-stage past its default ceiling).
    accept_proposal(
        tmp_db,
        probe.id,
        field=FieldName.kickoff,
        actor="human",
        now=2,
        next_ceiling=NEEDS_BETA,
        at_cap=AtCap.propose,
    )
    file_proposal(tmp_db, probe.id, field=FIELD_ALPHA, body="alpha", actor="agent", now=3)

    assert read_item(tmp_db, item.id).status is ItemStatus.in_progress


def test_rollup_coding_child_via_read_item_unchanged(
    tmp_db: Connection,
) -> None:
    # Coding rollups are unchanged: a mid-stage coding child (needs_approach) rolls up
    # in_progress through the real read_item door.
    clock = TestClock(datetime(2026, 7, 4, 12, 0, 0).astimezone())
    item = create_item(tmp_db, title="Item", project_id="project_vylo", clock=clock)
    child = create_ticket(
        tmp_db,
        worker_type="coding",
        title="Coding child",
        actor="human",
        now=1,
        title_max_chars=200,
        sprint_item_id=item.id,
    )
    # Advance the coding child to needs_approach.
    accept_proposal(
        tmp_db,
        child.id,
        field=FieldName.kickoff,
        actor="human",
        now=2,
        next_ceiling="needs_approach",
        at_cap=AtCap.propose,
    )
    file_proposal(tmp_db, child.id, field=FieldName.success, body="s", actor="agent", now=3)

    assert read_item(tmp_db, item.id).status is ItemStatus.in_progress

"""Pin the complete manifest for the ``planning-day`` Worker type."""

from __future__ import annotations

from planner.worker_types.planning_day import PLANNING_DAY_WORKER_TYPE_DEFINITION


def test_planning_day_worker_type_manifest() -> None:
    definition = PLANNING_DAY_WORKER_TYPE_DEFINITION

    assert definition.stage_ids() == (
        "needs_review",
        "needs_direction",
        "needs_day_changes",
        "needs_closeout",
        "done",
    )
    assert definition.field_ids() == (
        "review",
        "direction",
        "day_changes",
        "closeout",
    )
    assert {
        stage: definition.advance_target(stage)
        for stage in definition.stage_ids()
        if definition.advance_target(stage) is not None
    } == {
        "needs_review": "needs_direction",
        "needs_direction": "needs_day_changes",
        "needs_day_changes": "needs_closeout",
        "needs_closeout": "done",
    }
    assert definition.ceiling_range() == definition.stage_ids()

    profile = definition.worker_profile
    assert profile.default_backend == "claude"
    assert profile.default_model == "opus[1m]"
    assert profile.default_reasoning_effort == "medium"

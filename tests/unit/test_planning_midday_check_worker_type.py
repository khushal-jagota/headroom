from __future__ import annotations

from pathlib import Path

import planner.worker_types as worker_types
from planner.environments.hermes_home import PLANNER_SKILL_NAMES
from planner.worker_settings.service import read_worker_settings
from planner.worker_types import configuration
from planner.worker_types.configuration import PRODUCTION_WORKER_TYPE_REGISTRY
from planner.worker_types.contracts import WorkerTypeManifest

PLANNING_MIDDAY_CHECK_MANIFEST: WorkerTypeManifest = {
    "worker_type": "planning-midday-check",
    "label": "Planning Midday Check",
    "stages": [
        {
            "id": "needs_kickoff",
            "label": "Kickoff",
            "gating_field": "kickoff",
            "is_terminal": False,
            "default_ownership_mode": "worker",
        },
        {
            "id": "needs_action",
            "label": "Action",
            "gating_field": "action",
            "is_terminal": False,
            "default_ownership_mode": "worker",
        },
        {
            "id": "needs_closeout",
            "label": "Closeout",
            "gating_field": "closeout",
            "is_terminal": False,
            "default_ownership_mode": "worker",
        },
        {
            "id": "done",
            "label": "Done",
            "gating_field": None,
            "is_terminal": True,
            "default_ownership_mode": None,
        },
    ],
    "dropped": {
        "id": "dropped",
        "label": "Dropped",
        "gating_field": None,
        "is_terminal": True,
        "default_ownership_mode": None,
    },
    "advance": {
        "needs_kickoff": "needs_action",
        "needs_action": "needs_closeout",
        "needs_closeout": "done",
    },
    "fields": [
        {"id": "kickoff", "label": "Kickoff"},
        {"id": "action", "label": "Action"},
        {"id": "closeout", "label": "Closeout"},
    ],
    "ceiling_range": [
        "needs_kickoff",
        "needs_action",
        "needs_closeout",
        "done",
    ],
    "default_ceiling": "needs_kickoff",
    "worker_profile_id": "panels-worker-planning-midday-check",
    "default_backend": "codex",
    "default_model": "gpt-5.6-terra",
    "default_reasoning_effort": "medium",
}


def test_production_registry_carries_complete_planning_midday_check_manifest() -> None:
    assert (
        PRODUCTION_WORKER_TYPE_REGISTRY.manifest("planning-midday-check")
        == PLANNING_MIDDAY_CHECK_MANIFEST
    )


def test_planning_midday_check_definition_carries_complete_execution_contract() -> None:
    definition = PRODUCTION_WORKER_TYPE_REGISTRY.require("planning-midday-check")

    assert definition.stage_ids() == tuple(
        stage["id"] for stage in PLANNING_MIDDAY_CHECK_MANIFEST["stages"]
    )
    assert definition.field_ids() == tuple(
        field["id"] for field in PLANNING_MIDDAY_CHECK_MANIFEST["fields"]
    )
    assert tuple(
        stage.default_ownership_mode.value if stage.default_ownership_mode else None
        for stage in definition.stages
    ) == ("worker", "worker", "worker", None)
    assert definition.worker_profile.specialist_skill == "panels-worker-planning-midday-check"
    assert definition.worker_profile.toolset_profile == "default"
    assert definition.supports_prefix_reconciliation is True


def test_planning_midday_check_specialist_is_known_public_and_provisioned() -> None:
    assert "panels-worker-planning-midday-check" in configuration._KNOWN_SKILLS
    assert "panels-worker-planning-midday-check" in PLANNER_SKILL_NAMES
    assert (
        worker_types.PLANNING_MIDDAY_CHECK_WORKER_TYPE_DEFINITION
        is PRODUCTION_WORKER_TYPE_REGISTRY.require("planning-midday-check")
    )


def test_planning_midday_check_bootstrap_uses_approved_runtime_defaults(
    tmp_path: Path,
) -> None:
    settings = read_worker_settings(
        tmp_path,
        PRODUCTION_WORKER_TYPE_REGISTRY,
        "planning-midday-check",
    )

    assert settings.launch_defaults.employee_backend == "codex"
    assert settings.launch_defaults.employee_launch_model == "gpt-5.6-terra"
    assert settings.launch_defaults.employee_launch_reasoning_effort == "medium"
    assert settings.stage_ownership_defaults == {
        "needs_kickoff": "worker",
        "needs_action": "worker",
        "needs_closeout": "worker",
    }


def test_planning_midday_check_is_announced_at_both_agent_front_doors() -> None:
    root = Path(__file__).resolve().parents[2]
    panels = (root / "src/planner/skills/panels/SKILL.md").read_text(encoding="utf-8")
    chief = (root / "src/planner/skills/panels-chief-of-staff/SKILL.md").read_text(encoding="utf-8")
    worker = (root / "src/planner/skills/panels-worker/SKILL.md").read_text(encoding="utf-8")

    assert "`planning-midday-check` (checking execution against the" in panels
    assert "`panels-worker-planning-midday-check` — planning-midday-check tickets" in worker
    assert "panels-ticket-creation" in chief


def test_planning_midday_check_skill_preserves_approved_judgments() -> None:
    root = Path(__file__).resolve().parents[2]
    skill = (root / "src/planner/skills/panels-worker-planning-midday-check/SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "execution intervention, not a productivity scorecard" in skill
    assert "`panels worker request-user-help`" in skill
    assert "Make no changes before explicit Release and approval" in skill
    assert "then write `midday_reconciliation`" in skill

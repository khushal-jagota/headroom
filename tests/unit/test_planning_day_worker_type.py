from __future__ import annotations

from pathlib import Path

import planner.worker_types as worker_types
from planner.environments.hermes_home import PLANNER_SKILL_NAMES
from planner.worker_settings.service import read_worker_settings
from planner.worker_types import configuration
from planner.worker_types.configuration import PRODUCTION_WORKER_TYPE_REGISTRY
from planner.worker_types.contracts import WorkerTypeManifest

PLANNING_DAY_MANIFEST: WorkerTypeManifest = {
    "worker_type": "planning-day",
    "label": "Planning Day",
    "stages": [
        {
            "id": "needs_kickoff",
            "label": "Kickoff",
            "gating_field": "kickoff",
            "is_terminal": False,
            "default_ownership_mode": "worker",
        },
        {
            "id": "needs_gather",
            "label": "Gather",
            "gating_field": "gather",
            "is_terminal": False,
            "default_ownership_mode": "worker",
        },
        {
            "id": "needs_planning",
            "label": "Planning",
            "gating_field": "planning",
            "is_terminal": False,
            "default_ownership_mode": "paired",
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
        "needs_kickoff": "needs_gather",
        "needs_gather": "needs_planning",
        "needs_planning": "needs_closeout",
        "needs_closeout": "done",
    },
    "fields": [
        {"id": "kickoff", "label": "Kickoff"},
        {"id": "gather", "label": "Gather"},
        {"id": "planning", "label": "Planning"},
        {"id": "closeout", "label": "Closeout"},
    ],
    "ceiling_range": [
        "needs_kickoff",
        "needs_gather",
        "needs_planning",
        "needs_closeout",
        "done",
    ],
    "default_ceiling": "needs_kickoff",
    "worker_profile_id": "panels-worker-planning-day",
    "default_backend": "claude",
    "default_model": "opus[1m]",
    "default_reasoning_effort": "medium",
}


def test_production_registry_carries_complete_planning_day_manifest() -> None:
    assert PRODUCTION_WORKER_TYPE_REGISTRY.registered_worker_types() == (
        "coding",
        "new_worker",
        "exploration",
        "initiative_planning",
        "product_design",
        "planning-day",
    )
    assert PRODUCTION_WORKER_TYPE_REGISTRY.manifest("planning-day") == PLANNING_DAY_MANIFEST


def test_planning_day_definition_carries_complete_execution_contract() -> None:
    definition = PRODUCTION_WORKER_TYPE_REGISTRY.require("planning-day")

    assert definition.stage_ids() == tuple(
        stage["id"] for stage in PLANNING_DAY_MANIFEST["stages"]
    )
    assert definition.field_ids() == tuple(
        field["id"] for field in PLANNING_DAY_MANIFEST["fields"]
    )
    assert tuple(
        stage.default_ownership_mode.value if stage.default_ownership_mode else None
        for stage in definition.stages
    ) == ("worker", "worker", "paired", "worker", None)
    assert definition.worker_profile.specialist_skill == "panels-worker-planning-day"
    assert definition.worker_profile.toolset_profile == "default"
    assert definition.supports_prefix_reconciliation is True


def test_planning_day_specialist_is_known_public_and_provisioned() -> None:
    assert "panels-worker-planning-day" in configuration._KNOWN_SKILLS
    assert "panels-worker-planning-day" in PLANNER_SKILL_NAMES
    assert (
        worker_types.PLANNING_DAY_WORKER_TYPE_DEFINITION
        is PRODUCTION_WORKER_TYPE_REGISTRY.require("planning-day")
    )


def test_planning_day_managed_settings_bootstrap_uses_approved_runtime_defaults(
    tmp_path: Path,
) -> None:
    settings = read_worker_settings(
        tmp_path,
        PRODUCTION_WORKER_TYPE_REGISTRY,
        "planning-day",
    )

    assert settings.launch_defaults.employee_backend == "claude"
    assert settings.launch_defaults.employee_launch_model == "opus[1m]"
    assert settings.launch_defaults.employee_launch_reasoning_effort == "medium"
    assert settings.stage_ownership_defaults == {
        "needs_kickoff": "worker",
        "needs_gather": "worker",
        "needs_planning": "paired",
        "needs_closeout": "worker",
    }


def test_planning_day_is_announced_at_both_agent_front_doors() -> None:
    root = Path(__file__).resolve().parents[2]
    chief = (root / "src/planner/skills/panels-chief-of-staff/SKILL.md").read_text(
        encoding="utf-8"
    )
    worker = (root / "src/planner/skills/panels-worker/SKILL.md").read_text(encoding="utf-8")

    assert "`panels-worker-planning-day` — planning-day tickets" in worker
    assert "Use a **`planning-day` ticket**" in chief


def test_planning_day_skill_preserves_the_approved_planning_judgments() -> None:
    root = Path(__file__).resolve().parents[2]
    skill = (
        root / "src/planner/skills/panels-worker-planning-day/SKILL.md"
    ).read_text(encoding="utf-8")

    assert "one evidence-backed best guess of today's focus" in skill
    assert "without automatic carryover" in skill
    assert "create or reshape agreed Tickets" in skill
    assert "no backfill, guilt, streak, or rollover ceremony" in skill

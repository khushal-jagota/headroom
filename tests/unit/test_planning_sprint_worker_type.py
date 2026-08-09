from __future__ import annotations

from pathlib import Path

import planner.worker_types as worker_types
from planner.environments.hermes_home import PLANNER_SKILL_NAMES
from planner.worker_settings.service import read_worker_settings
from planner.worker_types import configuration
from planner.worker_types.configuration import PRODUCTION_WORKER_TYPE_REGISTRY
from planner.worker_types.contracts import WorkerTypeManifest

PLANNING_SPRINT_MANIFEST: WorkerTypeManifest = {
    "worker_type": "planning-sprint",
    "label": "Planning Sprint",
    "stages": [
        {
            "id": "needs_kickoff",
            "label": "Kickoff",
            "gating_field": "kickoff",
            "is_terminal": False,
            "default_ownership_mode": "worker",
        },
        {
            "id": "needs_review",
            "label": "Review",
            "gating_field": "review",
            "is_terminal": False,
            "default_ownership_mode": "worker",
        },
        {
            "id": "needs_next_sprint",
            "label": "Next Sprint",
            "gating_field": "next_sprint",
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
        "needs_kickoff": "needs_review",
        "needs_review": "needs_next_sprint",
        "needs_next_sprint": "needs_closeout",
        "needs_closeout": "done",
    },
    "fields": [
        {"id": "kickoff", "label": "Kickoff"},
        {"id": "review", "label": "Review"},
        {"id": "next_sprint", "label": "Next Sprint"},
        {"id": "closeout", "label": "Closeout"},
    ],
    "ceiling_range": [
        "needs_kickoff",
        "needs_review",
        "needs_next_sprint",
        "needs_closeout",
        "done",
    ],
    "default_ceiling": "needs_kickoff",
    "worker_profile_id": "panels-worker-planning-sprint",
    "default_backend": "claude",
    "default_model": "opus[1m]",
    "default_reasoning_effort": "medium",
}


def test_production_registry_carries_complete_planning_sprint_manifest() -> None:
    assert PRODUCTION_WORKER_TYPE_REGISTRY.registered_worker_types() == (
        "coding",
        "general",
        "debugging",
        "new_worker",
        "exploration",
        "initiative_planning",
        "product_design",
        "planning-day",
        "planning-midday-check",
        "planning-sprint",
        "personal",
    )
    assert PRODUCTION_WORKER_TYPE_REGISTRY.manifest("planning-sprint") == PLANNING_SPRINT_MANIFEST


def test_planning_sprint_definition_carries_complete_execution_contract() -> None:
    definition = PRODUCTION_WORKER_TYPE_REGISTRY.require("planning-sprint")

    assert definition.stage_ids() == tuple(
        stage["id"] for stage in PLANNING_SPRINT_MANIFEST["stages"]
    )
    assert definition.field_ids() == tuple(
        field["id"] for field in PLANNING_SPRINT_MANIFEST["fields"]
    )
    assert tuple(
        stage.default_ownership_mode.value if stage.default_ownership_mode else None
        for stage in definition.stages
    ) == ("worker", "worker", "worker", "worker", None)
    assert definition.worker_profile.specialist_skill == "panels-worker-planning-sprint"
    assert definition.worker_profile.toolset_profile == "default"
    assert definition.supports_prefix_reconciliation is True


def test_planning_sprint_specialist_is_known_public_and_provisioned() -> None:
    assert "panels-worker-planning-sprint" in configuration._KNOWN_SKILLS
    assert "panels-worker-planning-sprint" in PLANNER_SKILL_NAMES
    assert (
        worker_types.PLANNING_SPRINT_WORKER_TYPE_DEFINITION
        is PRODUCTION_WORKER_TYPE_REGISTRY.require("planning-sprint")
    )


def test_planning_sprint_managed_settings_bootstrap_uses_approved_runtime_defaults(
    tmp_path: Path,
) -> None:
    settings = read_worker_settings(
        tmp_path,
        PRODUCTION_WORKER_TYPE_REGISTRY,
        "planning-sprint",
    )

    assert settings.launch_defaults.employee_backend == "claude"
    assert settings.launch_defaults.employee_launch_model == "opus[1m]"
    assert settings.launch_defaults.employee_launch_reasoning_effort == "medium"
    assert settings.stage_ownership_defaults == {
        "needs_kickoff": "worker",
        "needs_review": "worker",
        "needs_next_sprint": "worker",
        "needs_closeout": "worker",
    }


def test_planning_sprint_is_announced_at_agent_front_doors() -> None:
    root = Path(__file__).resolve().parents[2]
    panels = (root / "src/planner/skills/panels/SKILL.md").read_text(encoding="utf-8")
    chief = (root / "src/planner/skills/panels-chief-of-staff/SKILL.md").read_text(encoding="utf-8")
    worker = (root / "src/planner/skills/panels-worker/SKILL.md").read_text(encoding="utf-8")
    assert "`planning-sprint` (reviewing one sprint" in panels
    assert "A **sprint** is a fixed seven-day period" in panels
    assert "Checkpoint Ticket prompts reflection at 17:00 on sprint day four" in panels
    assert "panels-ticket-creation" in chief
    assert "`panels-worker-planning-sprint` — planning-sprint tickets" in worker
    assert "panels ticket create --worker-type <planning-worker-type>" in chief
    assert "panels-sprint-planning" not in PLANNER_SKILL_NAMES
    assert "panels-rollover" not in PLANNER_SKILL_NAMES
    assert not (root / "src/planner/skills/panels-sprint-planning/SKILL.md").exists()
    assert not (root / "src/planner/skills/panels-rollover/SKILL.md").exists()


def test_planning_sprint_skill_preserves_the_approved_judgments() -> None:
    root = Path(__file__).resolve().parents[2]
    skill = (root / "src/planner/skills/panels-worker-planning-sprint/SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "facts from Panels, worker judgment, and user" in skill
    assert "request-user-help" in skill
    assert "explicitly Releases" in skill
    assert "review before planning" in skill
    assert "first sprint" in skill
    assert "perform no review" in skill
    assert "Closeout is the only canonical-write phase" in skill
    assert "day-four Checkpoint, in-sprint reconciliation" in skill
    assert "exactly seven inclusive dates" in skill
    assert "Existing historical sprint ranges remain unchanged" in skill

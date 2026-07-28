from __future__ import annotations

from pathlib import Path

import planner.worker_types as worker_types
from planner.environments.hermes_home import PLANNER_SKILL_NAMES
from planner.worker_settings.service import read_worker_settings
from planner.worker_types import configuration
from planner.worker_types.configuration import PRODUCTION_WORKER_TYPE_REGISTRY
from planner.worker_types.contracts import WorkerTypeManifest

PRODUCT_DESIGN_MANIFEST: WorkerTypeManifest = {
    "worker_type": "product_design",
    "label": "Product Design",
    "stages": [
        {
            "id": "needs_kickoff",
            "label": "Kickoff",
            "gating_field": "kickoff",
            "is_terminal": False,
            "default_ownership_mode": "worker",
        },
        {
            "id": "needs_direction",
            "label": "Direction",
            "gating_field": "direction",
            "is_terminal": False,
            "default_ownership_mode": "worker",
        },
        {
            "id": "needs_wireframe",
            "label": "Wireframe",
            "gating_field": "wireframe",
            "is_terminal": False,
            "default_ownership_mode": "paired",
        },
        {
            "id": "needs_design",
            "label": "Design",
            "gating_field": "design",
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
        "needs_kickoff": "needs_direction",
        "needs_direction": "needs_wireframe",
        "needs_wireframe": "needs_design",
        "needs_design": "needs_closeout",
        "needs_closeout": "done",
    },
    "fields": [
        {"id": "kickoff", "label": "Kickoff"},
        {"id": "direction", "label": "Direction"},
        {"id": "wireframe", "label": "Wireframe"},
        {"id": "design", "label": "Design"},
        {"id": "closeout", "label": "Closeout"},
    ],
    "ceiling_range": [
        "needs_kickoff",
        "needs_direction",
        "needs_wireframe",
        "needs_design",
        "needs_closeout",
        "done",
    ],
    "default_ceiling": "needs_kickoff",
    "worker_profile_id": "panels-worker-product-design",
    "default_backend": "claude",
    "default_model": "opus[1m]",
    "default_reasoning_effort": "high",
}


def test_production_registry_carries_complete_product_design_manifest() -> None:
    assert PRODUCTION_WORKER_TYPE_REGISTRY.registered_worker_types() == (
        "coding",
        "new_worker",
        "exploration",
        "initiative_planning",
        "product_design",
    )
    assert PRODUCTION_WORKER_TYPE_REGISTRY.manifest("product_design") == PRODUCT_DESIGN_MANIFEST


def test_product_design_definition_carries_complete_execution_contract() -> None:
    definition = PRODUCTION_WORKER_TYPE_REGISTRY.require("product_design")

    assert definition.stage_ids() == tuple(
        stage["id"] for stage in PRODUCT_DESIGN_MANIFEST["stages"]
    )
    assert definition.field_ids() == tuple(
        field["id"] for field in PRODUCT_DESIGN_MANIFEST["fields"]
    )
    assert tuple(
        stage.default_ownership_mode.value if stage.default_ownership_mode else None
        for stage in definition.stages
    ) == ("worker", "worker", "paired", "paired", "worker", None)
    assert definition.worker_profile.specialist_skill == "panels-worker-product-design"
    assert definition.worker_profile.toolset_profile == "default"
    assert definition.supports_prefix_reconciliation is True


def test_product_design_specialist_is_known_public_and_provisioned() -> None:
    assert "panels-worker-product-design" in configuration._KNOWN_SKILLS
    assert "panels-worker-product-design" in PLANNER_SKILL_NAMES
    assert (
        worker_types.PRODUCT_DESIGN_WORKER_TYPE_DEFINITION
        is PRODUCTION_WORKER_TYPE_REGISTRY.require("product_design")
    )


def test_product_design_managed_settings_bootstrap_uses_approved_runtime_defaults(
    tmp_path: Path,
) -> None:
    settings = read_worker_settings(
        tmp_path,
        PRODUCTION_WORKER_TYPE_REGISTRY,
        "product_design",
    )

    assert settings.launch_defaults.employee_backend == "claude"
    assert settings.launch_defaults.employee_launch_model == "opus[1m]"
    assert settings.launch_defaults.employee_launch_reasoning_effort == "high"
    assert settings.stage_ownership_defaults == {
        "needs_kickoff": "worker",
        "needs_direction": "worker",
        "needs_wireframe": "paired",
        "needs_design": "paired",
        "needs_closeout": "worker",
    }


def test_product_design_is_announced_at_both_agent_front_doors() -> None:
    root = Path(__file__).resolve().parents[2]
    chief = (root / "src/planner/skills/panels-chief-of-staff/SKILL.md").read_text(
        encoding="utf-8"
    )
    worker = (root / "src/planner/skills/panels-worker/SKILL.md").read_text(encoding="utf-8")

    assert "`panels-worker-product-design` — product_design tickets" in worker
    assert "Use a **`product_design` ticket**" in chief


def test_product_design_skill_preserves_the_approved_handoff_boundary() -> None:
    root = Path(__file__).resolve().parents[2]
    skill = (
        root / "src/planner/skills/panels-worker-product-design/SKILL.md"
    ).read_text(encoding="utf-8")

    assert "adopt its established patterns, tokens, and" in skill
    assert "coding owns implementation and all later feedback" in skill

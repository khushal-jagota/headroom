from __future__ import annotations

from pathlib import Path

import planner.worker_types as worker_types
from planner.environments.hermes_home import PLANNER_SKILL_NAMES
from planner.worker_types import configuration
from planner.worker_types.configuration import PRODUCTION_WORKER_TYPE_REGISTRY
from planner.worker_types.contracts import WorkerTypeManifest

INITIATIVE_PLANNING_MANIFEST: WorkerTypeManifest = {
    "worker_type": "initiative_planning",
    "label": "Initiative Planning",
    "stages": [
        {
            "id": "needs_kickoff",
            "label": "Kickoff",
            "gating_field": "kickoff",
            "is_terminal": False,
            "default_ownership_mode": "worker",
        },
        {
            "id": "needs_rough_shape",
            "label": "Rough Shape",
            "gating_field": "rough_shape",
            "is_terminal": False,
            "default_ownership_mode": "worker",
        },
        {
            "id": "needs_question_tree",
            "label": "Question Tree",
            "gating_field": "question_tree",
            "is_terminal": False,
            "default_ownership_mode": "worker",
        },
        {
            "id": "needs_question_answers",
            "label": "Question Answers",
            "gating_field": "question_answers",
            "is_terminal": False,
            "default_ownership_mode": "paired",
        },
        {
            "id": "needs_ticket_outlines",
            "label": "Ticket Outlines",
            "gating_field": "ticket_outlines",
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
        "needs_kickoff": "needs_rough_shape",
        "needs_rough_shape": "needs_question_tree",
        "needs_question_tree": "needs_question_answers",
        "needs_question_answers": "needs_ticket_outlines",
        "needs_ticket_outlines": "needs_closeout",
        "needs_closeout": "done",
    },
    "fields": [
        {"id": "kickoff", "label": "Kickoff"},
        {"id": "rough_shape", "label": "Rough Shape"},
        {"id": "question_tree", "label": "Question Tree"},
        {"id": "question_answers", "label": "Question Answers"},
        {"id": "ticket_outlines", "label": "Ticket Outlines"},
        {"id": "closeout", "label": "Closeout"},
    ],
    "ceiling_range": [
        "needs_kickoff",
        "needs_rough_shape",
        "needs_question_tree",
        "needs_question_answers",
        "needs_ticket_outlines",
        "needs_closeout",
        "done",
    ],
    "default_ceiling": "needs_kickoff",
    "worker_profile_id": "panels-worker-initiative-planning",
    "default_backend": "codex",
    "default_model": "gpt-5.6-sol",
    "default_reasoning_effort": "medium",
}


def test_production_registry_carries_complete_initiative_planning_manifest() -> None:
    assert PRODUCTION_WORKER_TYPE_REGISTRY.registered_worker_types() == (
        "coding",
        "new_worker",
        "exploration",
        "initiative_planning",
        "product_design",
        "planning-day",
    )
    assert (
        PRODUCTION_WORKER_TYPE_REGISTRY.manifest("initiative_planning")
        == INITIATIVE_PLANNING_MANIFEST
    )


def test_initiative_planning_definition_carries_complete_execution_contract() -> None:
    definition = PRODUCTION_WORKER_TYPE_REGISTRY.require("initiative_planning")

    assert definition.stage_ids() == tuple(
        stage["id"] for stage in INITIATIVE_PLANNING_MANIFEST["stages"]
    )
    assert definition.field_ids() == tuple(
        field["id"] for field in INITIATIVE_PLANNING_MANIFEST["fields"]
    )
    assert tuple(
        stage.default_ownership_mode.value if stage.default_ownership_mode else None
        for stage in definition.stages
    ) == (
        "worker",
        "worker",
        "worker",
        "paired",
        "worker",
        "worker",
        None,
    )
    assert definition.worker_profile.specialist_skill == "panels-worker-initiative-planning"
    assert definition.worker_profile.toolset_profile == "default"
    assert definition.supports_prefix_reconciliation is True


def test_initiative_planning_specialist_is_known_public_and_provisioned() -> None:
    assert "panels-worker-initiative-planning" in configuration._KNOWN_SKILLS
    assert "panels-worker-initiative-planning" in PLANNER_SKILL_NAMES
    assert (
        worker_types.INITIATIVE_PLANNING_WORKER_TYPE_DEFINITION
        is PRODUCTION_WORKER_TYPE_REGISTRY.require("initiative_planning")
    )


def test_initiative_planning_is_announced_at_both_agent_front_doors() -> None:
    root = Path(__file__).resolve().parents[2]
    shared = (root / "src/planner/skills/panels/SKILL.md").read_text(encoding="utf-8")
    worker = (root / "src/planner/skills/panels-worker/SKILL.md").read_text(encoding="utf-8")

    assert "`panels-worker-initiative-planning` — initiative_planning tickets" in worker
    assert (
        "`initiative_planning` (working out the shared top-level how for a confirmed "
        "direction before creating its downstream Tickets)"
    ) in " ".join(shared.split())


def test_live_worker_type_docs_include_initiative_planning() -> None:
    root = Path(__file__).resolve().parents[2]
    docs = (root / "docs/worker-types.md").read_text(encoding="utf-8")

    assert "Six Worker types ship today:" in docs
    assert "- **`initiative_planning`**" in docs
    assert "`src/planner/worker_types/initiative_planning.py`" in docs
    assert "- `panels-worker-initiative-planning` guides `initiative_planning` Tickets." in docs

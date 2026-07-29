from __future__ import annotations

from pathlib import Path

import planner.worker_types as worker_types
from planner.worker_types import configuration
from planner.worker_types.configuration import PRODUCTION_WORKER_TYPE_REGISTRY
from planner.worker_types.contracts import WorkerTypeManifest

EXPLORATION_MANIFEST: WorkerTypeManifest = {
    "worker_type": "exploration",
    "label": "Exploration",
    "stages": [
        {
            "id": "needs_kickoff",
            "label": "Kickoff",
            "gating_field": "kickoff",
            "is_terminal": False,
            "default_ownership_mode": "worker",
        },
        {
            "id": "needs_understanding",
            "label": "Understanding",
            "gating_field": "understanding",
            "is_terminal": False,
            "default_ownership_mode": "paired",
        },
        {
            "id": "needs_research_plan",
            "label": "Research Plan",
            "gating_field": "research_plan",
            "is_terminal": False,
            "default_ownership_mode": "worker",
        },
        {
            "id": "needs_research",
            "label": "Research",
            "gating_field": "research",
            "is_terminal": False,
            "default_ownership_mode": "worker",
        },
        {
            "id": "needs_answer",
            "label": "Answer",
            "gating_field": "answer",
            "is_terminal": False,
            "default_ownership_mode": "paired",
        },
        {
            "id": "needs_follow_up",
            "label": "Follow-up",
            "gating_field": "follow_up",
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
        "needs_kickoff": "needs_understanding",
        "needs_understanding": "needs_research_plan",
        "needs_research_plan": "needs_research",
        "needs_research": "needs_answer",
        "needs_answer": "needs_follow_up",
        "needs_follow_up": "needs_closeout",
        "needs_closeout": "done",
    },
    "fields": [
        {"id": "kickoff", "label": "Kickoff"},
        {"id": "understanding", "label": "Understanding"},
        {"id": "research_plan", "label": "Research Plan"},
        {"id": "research", "label": "Research"},
        {"id": "answer", "label": "Answer"},
        {"id": "follow_up", "label": "Follow-up"},
        {"id": "closeout", "label": "Closeout"},
    ],
    "ceiling_range": [
        "needs_kickoff",
        "needs_understanding",
        "needs_research_plan",
        "needs_research",
        "needs_answer",
        "needs_follow_up",
        "needs_closeout",
        "done",
    ],
    "default_ceiling": "needs_kickoff",
    "worker_profile_id": "panels-worker-exploration",
    "default_backend": "codex",
    "default_model": "gpt-5.6-sol",
    "default_reasoning_effort": "medium",
}


def test_production_registry_carries_complete_exploration_manifest() -> None:
    assert PRODUCTION_WORKER_TYPE_REGISTRY.registered_worker_types() == (
        "coding",
        "debugging",
        "new_worker",
        "exploration",
        "initiative_planning",
        "product_design",
        "planning-day",
        "planning-midday-check",
        "planning-sprint",
    )
    assert PRODUCTION_WORKER_TYPE_REGISTRY.manifest("exploration") == EXPLORATION_MANIFEST


def test_exploration_definition_carries_complete_execution_contract() -> None:
    definition = PRODUCTION_WORKER_TYPE_REGISTRY.require("exploration")

    assert definition.stage_ids() == tuple(stage["id"] for stage in EXPLORATION_MANIFEST["stages"])
    assert definition.field_ids() == tuple(field["id"] for field in EXPLORATION_MANIFEST["fields"])
    assert tuple(
        stage.default_ownership_mode.value if stage.default_ownership_mode else None
        for stage in definition.stages
    ) == (
        "worker",
        "paired",
        "worker",
        "worker",
        "paired",
        "worker",
        "worker",
        None,
    )
    assert "needs_corpus" not in definition.stage_ids()
    assert "corpus" not in definition.field_ids()
    assert definition.worker_profile.specialist_skill == "panels-worker-exploration"
    assert definition.worker_profile.toolset_profile == "default"
    assert definition.supports_prefix_reconciliation is True


def test_exploration_specialist_is_known_and_definition_is_public() -> None:
    assert "panels-worker-exploration" in configuration._KNOWN_SKILLS
    assert (
        worker_types.EXPLORATION_WORKER_TYPE_DEFINITION
        is PRODUCTION_WORKER_TYPE_REGISTRY.require("exploration")
    )


def test_exploration_is_announced_at_both_agent_front_doors() -> None:
    root = Path(__file__).resolve().parents[2]
    shared = (root / "src/planner/skills/panels/SKILL.md").read_text(encoding="utf-8")
    worker = (root / "src/planner/skills/panels-worker/SKILL.md").read_text(encoding="utf-8")

    assert "`panels-worker-exploration` — exploration tickets." in worker
    assert (
        "`exploration` (a worker for exploring something undefined and making it clearer)"
    ) in " ".join(shared.split())


def test_live_worker_type_docs_include_shipped_exploration_paths_and_guidance() -> None:
    root = Path(__file__).resolve().parents[2]
    docs = (root / "docs/worker-types.md").read_text(encoding="utf-8")

    assert "Eight Worker types ship today:" in docs
    assert "- **`exploration`**" in docs
    assert "`src/planner/worker_types/exploration.py`" in docs
    assert (
        "contains `coding`, `new_worker`, `exploration`, `initiative_planning`, "
        "`product_design`, `planning-day`, `planning-midday-check`, and `planning-sprint`"
    ) in " ".join(docs.split())
    assert "- `panels-worker-exploration` guides `exploration` Tickets." in docs

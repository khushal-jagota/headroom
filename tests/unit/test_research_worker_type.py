from __future__ import annotations

from pathlib import Path

import planner.worker_types as worker_types
from planner.worker_types import configuration
from planner.worker_types.configuration import PRODUCTION_WORKER_TYPE_REGISTRY
from planner.worker_types.contracts import WorkerTypeManifest

RESEARCH_MANIFEST: WorkerTypeManifest = {
    "worker_type": "research",
    "label": "Research",
    "stages": [
        {
            "id": "needs_kickoff",
            "label": "Kickoff",
            "gating_field": "kickoff",
            "is_terminal": False,
            "default_ownership_mode": "worker",
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
        "needs_kickoff": "needs_research_plan",
        "needs_research_plan": "needs_research",
        "needs_research": "needs_closeout",
        "needs_closeout": "done",
    },
    "fields": [
        {"id": "kickoff", "label": "Kickoff"},
        {"id": "research_plan", "label": "Research Plan"},
        {"id": "research", "label": "Research"},
        {"id": "closeout", "label": "Closeout"},
    ],
    "ceiling_range": [
        "needs_kickoff",
        "needs_research_plan",
        "needs_research",
        "needs_closeout",
        "done",
    ],
    "default_ceiling": "needs_kickoff",
    "worker_profile_id": "panels-worker-research",
    "default_backend": "claude",
    "default_model": "opus[1m]",
    "default_reasoning_effort": "high",
}


def test_production_registry_carries_complete_research_manifest() -> None:
    assert "research" in PRODUCTION_WORKER_TYPE_REGISTRY.registered_worker_types()
    assert PRODUCTION_WORKER_TYPE_REGISTRY.manifest("research") == RESEARCH_MANIFEST


def test_research_definition_carries_complete_execution_contract() -> None:
    definition = PRODUCTION_WORKER_TYPE_REGISTRY.require("research")

    assert definition.stage_ids() == tuple(stage["id"] for stage in RESEARCH_MANIFEST["stages"])
    assert definition.field_ids() == tuple(field["id"] for field in RESEARCH_MANIFEST["fields"])
    # The question arrives already framed, so research runs unattended: no paired Stage.
    assert all(
        stage.default_ownership_mode.value == "worker"
        for stage in definition.stages
        if stage.default_ownership_mode is not None
    )
    # It does not frame the question or decide the answer; that is `exploration`.
    assert "needs_understanding" not in definition.stage_ids()
    assert "needs_answer" not in definition.stage_ids()
    assert definition.worker_profile.specialist_skill == "panels-worker-research"
    assert definition.worker_profile.toolset_profile == "default"
    assert definition.supports_prefix_reconciliation is True


def test_research_specialist_is_known_and_definition_is_public() -> None:
    assert "panels-worker-research" in configuration._KNOWN_SKILLS
    assert worker_types.RESEARCH_WORKER_TYPE_DEFINITION is (
        PRODUCTION_WORKER_TYPE_REGISTRY.require("research")
    )


def test_research_is_announced_at_both_agent_front_doors() -> None:
    root = Path(__file__).resolve().parents[2]
    shared = (root / "src/planner/skills/panels/SKILL.md").read_text(encoding="utf-8")
    worker = (root / "src/planner/skills/panels-worker/SKILL.md").read_text(encoding="utf-8")
    chief = (root / "src/planner/skills/panels-chief-of-staff/SKILL.md").read_text(encoding="utf-8")

    assert "- `panels-worker-research` — research tickets" in worker
    assert "`research` (answering an already-framed question with sourced evidence" in " ".join(
        shared.split()
    )
    assert "Use the `research` Worker type" in chief


def test_research_skill_ships_and_is_provisioned() -> None:
    root = Path(__file__).resolve().parents[2]
    from planner.environments.hermes_home import PLANNER_SKILL_NAMES

    assert "panels-worker-research" in PLANNER_SKILL_NAMES
    assert (root / "src/planner/skills/panels-worker-research/SKILL.md").is_file()


def test_live_worker_type_docs_include_shipped_research_paths_and_guidance() -> None:
    root = Path(__file__).resolve().parents[2]
    docs = (root / "docs/worker-types.md").read_text(encoding="utf-8")

    assert "- **`research`**" in docs
    assert "`src/planner/worker_types/research.py`" in docs
    assert "- `panels-worker-research` guides `research` Tickets." in docs

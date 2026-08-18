from __future__ import annotations

from pathlib import Path

import planner.worker_types as worker_types
from planner.worker_types import configuration
from planner.worker_types.configuration import PRODUCTION_WORKER_TYPE_REGISTRY
from planner.worker_types.contracts import WorkerTypeManifest

AMEND_WORKER_MANIFEST: WorkerTypeManifest = {
    "worker_type": "amend_worker",
    "label": "Amend Worker",
    "stages": [
        {
            "id": "needs_kickoff",
            "label": "Kickoff",
            "gating_field": "kickoff",
            "is_terminal": False,
            "default_ownership_mode": "worker",
        },
        {
            "id": "needs_amendment",
            "label": "Amendment",
            "gating_field": "amendment",
            "is_terminal": False,
            "default_ownership_mode": "paired",
        },
        {
            "id": "needs_drafting",
            "label": "Drafting",
            "gating_field": "drafting",
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
        "needs_kickoff": "needs_amendment",
        "needs_amendment": "needs_drafting",
        "needs_drafting": "needs_closeout",
        "needs_closeout": "done",
    },
    "fields": [
        {"id": "kickoff", "label": "Kickoff"},
        {"id": "amendment", "label": "Amendment"},
        {"id": "drafting", "label": "Drafting"},
        {"id": "closeout", "label": "Closeout"},
    ],
    "ceiling_range": [
        "needs_kickoff",
        "needs_amendment",
        "needs_drafting",
        "needs_closeout",
        "done",
    ],
    "default_ceiling": "needs_kickoff",
    "worker_profile_id": "panels-worker-amend-worker",
    "default_backend": "codex",
    "default_model": "gpt-5.6-sol",
    "default_reasoning_effort": "medium",
}


def test_production_registry_carries_complete_amend_worker_manifest() -> None:
    assert "amend_worker" in PRODUCTION_WORKER_TYPE_REGISTRY.registered_worker_types()
    assert PRODUCTION_WORKER_TYPE_REGISTRY.manifest("amend_worker") == AMEND_WORKER_MANIFEST


def test_amend_worker_definition_carries_complete_execution_contract() -> None:
    definition = PRODUCTION_WORKER_TYPE_REGISTRY.require("amend_worker")

    assert definition.stage_ids() == tuple(
        stage["id"] for stage in AMEND_WORKER_MANIFEST["stages"]
    )
    assert definition.field_ids() == tuple(
        field["id"] for field in AMEND_WORKER_MANIFEST["fields"]
    )
    # Amendment is the only Stage with a decision in it: what changes, and what that does
    # to the live Tickets of the Worker being amended. Everything after it is execution.
    paired = tuple(
        stage.id
        for stage in definition.stages
        if stage.default_ownership_mode is not None
        and stage.default_ownership_mode.value == "paired"
    )
    assert paired == ("needs_amendment",)
    # It changes a Worker that already exists; designing one from nothing is `new_worker`.
    assert "needs_understanding" not in definition.stage_ids()
    assert "needs_stages" not in definition.stage_ids()
    assert definition.worker_profile.specialist_skill == "panels-worker-amend-worker"
    assert definition.worker_profile.toolset_profile == "default"
    assert definition.supports_prefix_reconciliation is True


def test_amend_worker_specialist_is_known_and_definition_is_public() -> None:
    assert "panels-worker-amend-worker" in configuration._KNOWN_SKILLS
    assert worker_types.AMEND_WORKER_TYPE_DEFINITION is (
        PRODUCTION_WORKER_TYPE_REGISTRY.require("amend_worker")
    )


def test_amend_worker_is_announced_at_both_agent_front_doors() -> None:
    root = Path(__file__).resolve().parents[2]
    shared = (root / "src/planner/skills/panels/SKILL.md").read_text(encoding="utf-8")
    worker = (root / "src/planner/skills/panels-worker/SKILL.md").read_text(encoding="utf-8")
    chief = (root / "src/planner/skills/panels-chief-of-staff/SKILL.md").read_text(encoding="utf-8")

    assert "- `panels-worker-amend-worker` — amend_worker tickets" in worker
    assert "`amend_worker` (changing an existing Worker type" in " ".join(shared.split())
    assert "Use the `amend_worker` Worker type" in chief


def test_amend_worker_skill_ships_and_is_provisioned() -> None:
    root = Path(__file__).resolve().parents[2]
    from planner.environments.hermes_home import PLANNER_SKILL_NAMES

    assert "panels-worker-amend-worker" in PLANNER_SKILL_NAMES
    assert (root / "src/planner/skills/panels-worker-amend-worker/SKILL.md").is_file()


def test_live_worker_type_docs_include_shipped_amend_worker_paths_and_guidance() -> None:
    root = Path(__file__).resolve().parents[2]
    docs = (root / "docs/worker-types.md").read_text(encoding="utf-8")

    assert "- **`amend_worker`**" in docs
    assert "src/planner/worker_types/amend_worker.py" in docs

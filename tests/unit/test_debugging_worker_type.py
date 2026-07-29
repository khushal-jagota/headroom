from __future__ import annotations

from pathlib import Path

import planner.worker_types as worker_types
from planner.environments.hermes_home import PLANNER_SKILL_NAMES
from planner.worker_settings.service import read_worker_settings
from planner.worker_types import configuration
from planner.worker_types.configuration import PRODUCTION_WORKER_TYPE_REGISTRY


def test_debugging_definition_carries_the_approved_execution_contract() -> None:
    definition = PRODUCTION_WORKER_TYPE_REGISTRY.require("debugging")

    assert definition.stage_ids() == (
        "needs_kickoff",
        "needs_problem_understanding",
        "needs_structural_diagnosis",
        "needs_solution",
        "needs_closeout",
        "done",
    )
    assert definition.field_ids() == (
        "kickoff",
        "problem_understanding",
        "structural_diagnosis",
        "solution",
        "closeout",
    )
    assert tuple(
        stage.default_ownership_mode.value if stage.default_ownership_mode else None
        for stage in definition.stages
    ) == ("worker", "worker", "worker", "worker", "worker", None)
    assert definition.supports_prefix_reconciliation is True


def test_debugging_specialist_is_known_public_and_provisioned() -> None:
    assert "panels-worker-debugging" in configuration._KNOWN_SKILLS
    assert "panels-worker-debugging" in PLANNER_SKILL_NAMES
    assert (
        worker_types.DEBUGGING_WORKER_TYPE_DEFINITION
        is PRODUCTION_WORKER_TYPE_REGISTRY.require("debugging")
    )


def test_debugging_managed_settings_use_the_approved_runtime_defaults(
    tmp_path: Path,
) -> None:
    settings = read_worker_settings(
        tmp_path,
        PRODUCTION_WORKER_TYPE_REGISTRY,
        "debugging",
    )

    assert settings.launch_defaults.employee_backend == "codex"
    assert settings.launch_defaults.employee_launch_model == "gpt-5.6-sol"
    assert settings.launch_defaults.employee_launch_reasoning_effort == "high"


def test_debugging_is_announced_at_both_agent_front_doors() -> None:
    root = Path(__file__).resolve().parents[2]
    panels = (root / "src/planner/skills/panels/SKILL.md").read_text(encoding="utf-8")
    chief = (root / "src/planner/skills/panels-chief-of-staff/SKILL.md").read_text(
        encoding="utf-8"
    )
    worker = (root / "src/planner/skills/panels-worker/SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "`debugging` (understanding a reported bug" in panels
    assert "`panels-worker-debugging` — debugging tickets" in worker
    assert "Use a **`debugging` ticket**" in chief


def test_debugging_is_current_in_worker_type_docs() -> None:
    root = Path(__file__).resolve().parents[2]
    docs = (root / "docs/worker-types.md").read_text(encoding="utf-8")

    assert "Nine Worker types ship today:" in docs
    assert "- **`debugging`**" in docs
    assert "`src/planner/worker_types/debugging.py`" in docs
    assert "`panels-worker-debugging` guides `debugging` Tickets." in docs


def test_problem_understanding_stays_separate_from_diagnosis() -> None:
    root = Path(__file__).resolve().parents[2]
    skill = (root / "src/planner/skills/panels-worker-debugging/SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "Propose the problem in your own words" in skill
    assert "do not diagnose\nthe cause or suggest a fix yet" in skill
    assert "An actual cause is expected" in skill
    assert "Do not\nimplement the fix" in skill

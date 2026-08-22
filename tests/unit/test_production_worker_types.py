from __future__ import annotations

from pathlib import Path

from planner.environments.hermes_home import PLANNER_SKILL_NAMES
from planner.worker_types.configuration import PRODUCTION_WORKER_TYPE_REGISTRY


def test_production_worker_types_carry_their_distinct_workflow_and_runtime_contracts() -> None:
    expected = {
        "coding": (
            (
                "needs_kickoff",
                "needs_success",
                "needs_approach",
                "needs_plan",
                "needs_implementation",
                "needs_closeout",
                "done",
            ),
            ("worker", "worker", "worker", "worker", "worker", "worker", None),
            ("panels-worker-coding", "codex", "gpt-5.6-sol", "medium"),
        ),
        "general": (
            ("needs_kickoff", "needs_execution", "needs_closeout", "done"),
            ("worker", "worker", "worker", None),
            ("panels-worker-general", "codex", "gpt-5.6-sol", "medium"),
        ),
        "debugging": (
            (
                "needs_kickoff",
                "needs_problem_understanding",
                "needs_structural_diagnosis",
                "needs_solution",
                "needs_closeout",
                "done",
            ),
            ("worker", "worker", "worker", "worker", "worker", None),
            ("panels-worker-debugging", "codex", "gpt-5.6-sol", "high"),
        ),
        "new_worker": (
            (
                "needs_kickoff",
                "needs_understanding",
                "needs_stages",
                "needs_thinking",
                "needs_runtime_defaults",
                "needs_drafting",
                "needs_closeout",
                "done",
            ),
            ("worker", "paired", "worker", "worker", "paired", "worker", "worker", None),
            ("panels-worker-new-worker", "codex", "gpt-5.6-sol", "medium"),
        ),
        "amend_worker": (
            ("needs_kickoff", "needs_amendment", "needs_drafting", "needs_closeout", "done"),
            ("worker", "paired", "worker", "worker", None),
            ("panels-worker-amend-worker", "codex", "gpt-5.6-sol", "medium"),
        ),
        "exploration": (
            (
                "needs_kickoff",
                "needs_understanding",
                "needs_research_plan",
                "needs_research",
                "needs_answer",
                "needs_follow_up",
                "needs_closeout",
                "done",
            ),
            ("worker", "paired", "worker", "worker", "paired", "worker", "worker", None),
            ("panels-worker-exploration", "codex", "gpt-5.6-sol", "medium"),
        ),
        "initiative_planning": (
            (
                "needs_kickoff",
                "needs_rough_shape",
                "needs_question_tree",
                "needs_question_answers",
                "needs_ticket_outlines",
                "needs_closeout",
                "done",
            ),
            ("worker", "worker", "worker", "paired", "worker", "worker", None),
            ("panels-worker-initiative-planning", "codex", "gpt-5.6-sol", "medium"),
        ),
        "product_design": (
            (
                "needs_kickoff",
                "needs_direction",
                "needs_wireframe",
                "needs_design",
                "needs_closeout",
                "done",
            ),
            ("worker", "worker", "paired", "paired", "worker", None),
            ("panels-worker-product-design", "claude", "opus[1m]", "high"),
        ),
        "planning-day": (
            ("needs_kickoff", "needs_gather", "needs_planning", "needs_closeout", "done"),
            ("worker", "worker", "paired", "worker", None),
            ("panels-worker-planning-day", "claude", "opus[1m]", "medium"),
        ),
        "planning-midday-check": (
            ("needs_kickoff", "needs_action", "needs_closeout", "done"),
            ("worker", "worker", "worker", None),
            ("panels-worker-planning-midday-check", "codex", "gpt-5.6-terra", "medium"),
        ),
        "planning-sprint": (
            ("needs_kickoff", "needs_review", "needs_next_sprint", "needs_closeout", "done"),
            ("worker", "worker", "worker", "worker", None),
            ("panels-worker-planning-sprint", "claude", "opus[1m]", "medium"),
        ),
        "personal": (
            ("needs_kickoff", "needs_outcome", "needs_closeout", "done"),
            ("user", "user", "worker", None),
            ("panels-worker-personal-task", "codex", "gpt-5.6-luna", "medium"),
        ),
        "research": (
            ("needs_kickoff", "needs_research_plan", "needs_research", "needs_closeout", "done"),
            ("worker", "worker", "worker", "worker", None),
            ("panels-worker-research", "claude", "opus[1m]", "high"),
        ),
    }

    assert set(PRODUCTION_WORKER_TYPE_REGISTRY.registered_worker_types()) == set(expected)
    for worker_type, contract in expected.items():
        definition = PRODUCTION_WORKER_TYPE_REGISTRY.require(worker_type)
        profile = definition.worker_profile
        actual = (
            definition.stage_ids(),
            tuple(
                stage.default_ownership_mode.value
                if stage.default_ownership_mode is not None
                else None
                for stage in definition.stages
            ),
            (
                profile.specialist_skill,
                profile.default_backend,
                profile.default_model,
                profile.default_reasoning_effort,
            ),
        )
        assert actual == contract, worker_type
        expected_fields = tuple(
            stage_id.removeprefix("needs_") for stage_id in contract[0][:-1]
        )
        assert definition.field_ids() == expected_fields, worker_type
        assert tuple(stage.gating_field for stage in definition.stages) == (
            *expected_fields,
            None,
        ), worker_type
        assert definition.supports_prefix_reconciliation is True, worker_type
        skill_path = (
            Path(__file__).resolve().parents[2]
            / "src/planner/skills"
            / profile.specialist_skill
            / "SKILL.md"
        )
        assert skill_path.is_file(), worker_type
        assert profile.specialist_skill in PLANNER_SKILL_NAMES, worker_type

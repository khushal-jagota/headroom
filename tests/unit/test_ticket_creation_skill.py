from __future__ import annotations

from pathlib import Path

import yaml

from planner.environments.hermes_home import (
    PLANNER_SKILL_NAMES,
    provision_planner_home_skills,
)
from planner.skill_sources import (
    RETIRED_PANELS_SKILL_NAMES,
    ensure_managed_panels_skills,
    panels_skill_root,
    provision_native_backend_skills,
)

CREATOR_WRAPPERS = {
    "panels-chief-of-staff": "faithful intake",
    "panels-worker": "current approved step",
    "panels-worker-exploration": "approved answer and follow-up",
    "panels-worker-initiative-planning": "approved outlines",
    "panels-worker-planning-day": "Day agreement",
    "panels-worker-product-design": "Worker type as `coding`",
}


def _read_skill(name: str) -> str:
    return (panels_skill_root() / name / "SKILL.md").read_text(encoding="utf-8")


def test_ticket_creation_skill_is_active_and_core_routing_is_mandatory() -> None:
    skill = _read_skill("panels-ticket-creation")
    _opening, front_matter, body = skill.split("---", 2)

    assert yaml.safe_load(front_matter) == {
        "name": "panels-ticket-creation",
        "description": "The shared Panels model for creating a coherent Ticket.",
    }
    assert "# Creating a Ticket" in body
    assert "panels-ticket-creation" in PLANNER_SKILL_NAMES
    assert "panels-ticket-creation" not in RETIRED_PANELS_SKILL_NAMES
    assert "panels-ticket-management" in RETIRED_PANELS_SKILL_NAMES

    panels = _read_skill("panels")
    assert "Before creating any Ticket, load and follow **`panels-ticket-creation`**." in panels
    assert "**`panels-ticket-creation`** — the shared model" in panels


def test_creation_model_covers_the_system_without_becoming_a_command_recipe() -> None:
    skill = _read_skill("panels-ticket-creation")
    compact_skill = " ".join(skill.split())

    for marker in (
        "A Sprint Item is an optional broader-outcome classification",
        "Choose the Worker type whose lifecycle fits the job",
        "use its registered specialist guidance",
        "light, faithful Kickoff",
        "Today and sprint placement answer different questions",
        "With no explicit Sprint choice",
        "Without a current Sprint, the Ticket stays in backlog",
        "Explicit backlog placement",
        "view-only Other group",
        "Personal Project",
        "Keep an `initiative_planning` Ticket with its initiative",
        "Priority is strategic importance, not urgency",
        "**P0:** rare",
        "**P1:** strongly and directly contributes",
        "**P2:** contributes indirectly",
        "**P3:** background work",
        "Sprint Item → Project order",
        "A real calendar constraint belongs in the deadline",
        "A genuine prerequisite belongs in the Ticket's blockers",
        "read the Ticket back as a whole",
    ):
        assert marker in compact_skill
    assert "panels ticket create" not in skill


def test_every_ticket_creator_delegates_and_retains_its_authority() -> None:
    for skill_name, authority_marker in CREATOR_WRAPPERS.items():
        skill = _read_skill(skill_name)
        assert "panels-ticket-creation" in skill
        assert authority_marker in skill


def test_fresh_managed_hermes_and_native_homes_expose_creation_skill(
    tmp_path: Path,
) -> None:
    managed = ensure_managed_panels_skills(tmp_path / "data")
    assert (managed / "panels-ticket-creation" / "SKILL.md").is_file()

    provision_planner_home_skills(
        tmp_path / "hermes",
        configured_database_parent=tmp_path / "data",
    )
    hermes_skill = tmp_path / "hermes" / "skills" / "panels-ticket-creation"
    assert hermes_skill.is_symlink()
    assert hermes_skill.resolve() == (managed / "panels-ticket-creation").resolve()

    provision_native_backend_skills(tmp_path / "native", tmp_path / "data")
    native_skill = tmp_path / "native" / "skills" / "panels-ticket-creation"
    assert native_skill.is_dir()
    assert native_skill.resolve() == (managed / "panels-ticket-creation").resolve()

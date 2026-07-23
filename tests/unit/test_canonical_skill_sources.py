import concurrent.futures
import shutil
from pathlib import Path

import pytest

from planner.conversation.hermes_backend_configuration import provision_planner_home_skills
from planner.core.contracts import PlannerError
from planner.worker_settings import service
from planner.worker_types.configuration import configured_worker_type_registry


@pytest.fixture
def canonical_skills_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    source = service.panels_skill_root()
    target = tmp_path / "canonical-skills"
    shutil.copytree(source, target)
    monkeypatch.setattr(service, "panels_skill_root", lambda: target)
    return target


def test_worker_skill_edit_writes_versioned_canonical_file(
    tmp_path: Path, canonical_skills_root: Path
) -> None:
    registry = configured_worker_type_registry()
    source = canonical_skills_root / "panels-worker-coding" / "SKILL.md"
    saved = service.save_specialist_skill(
        tmp_path,
        registry,
        "coding",
        {"description": "canonical test", "markdown_body": "# canonical\n"},
    )
    assert saved.specialist_skill.description == "canonical test"
    assert source.read_text(encoding="utf-8").startswith('---\nname: "panels-worker-coding"')
    assert not (service.managed_worker_settings_root(tmp_path) / "coding" / "SKILL.md").exists()


def test_hermes_skill_is_symlink_to_canonical_source(tmp_path: Path) -> None:
    provision_planner_home_skills(tmp_path / "home")
    source = Path(__file__).parents[2] / "src" / "planner" / "skills" / "panels-worker-coding"
    target = tmp_path / "home" / "skills" / "panels-worker-coding"
    assert target.is_symlink()
    assert target.resolve() == source.resolve()


def test_chief_skill_uses_same_canonical_source(
    tmp_path: Path, canonical_skills_root: Path
) -> None:
    registry = configured_worker_type_registry()
    saved = service.save_chief_skill(
        tmp_path,
        registry,
        {"description": "chief canonical test", "body": "# chief\n"},
    )
    assert saved.skill.description == "chief canonical test"
    assert saved.skill.name == "panels-chief-of-staff"


def test_every_skill_in_home_can_be_edited_and_is_catalogued(
    canonical_skills_root: Path,
) -> None:
    home = service.read_skills_home()
    assert len(home.skills) >= 3
    target = next(skill for skill in home.skills if skill.name == "panels")
    saved = service.save_skill(
        target.name, {"description": "edited", "markdown_body": target.markdown_body}
    )
    assert saved.description == "edited"
    assert 'description: "edited"' in (
        canonical_skills_root / "panels" / "SKILL.md"
    ).read_text(encoding="utf-8")


def test_skill_edit_rejects_path_traversal(canonical_skills_root: Path) -> None:
    with pytest.raises(PlannerError):
        service.save_skill("..", {"description": "bad"})


def test_concurrent_skill_field_edits_preserve_both_fields(canonical_skills_root: Path) -> None:
    current = next(skill for skill in service.read_skills_home().skills if skill.name == "panels")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(service.save_skill, "panels", {"description": "parallel"}),
            executor.submit(
                service.save_skill,
                "panels",
                {"markdown_body": current.markdown_body + "\nparallel\n"},
            ),
        ]
        [future.result() for future in futures]
    final = next(skill for skill in service.read_skills_home().skills if skill.name == "panels")
    assert final.description == "parallel"
    assert final.markdown_body.endswith("parallel\n")

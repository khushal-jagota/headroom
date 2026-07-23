from pathlib import Path

from planner.conversation.hermes_backend_configuration import provision_planner_home_skills
from planner.worker_settings import service
from planner.worker_types.configuration import configured_worker_type_registry


def test_worker_skill_edit_writes_versioned_canonical_file(tmp_path: Path) -> None:
    registry = configured_worker_type_registry()
    source = Path(__file__).parents[2] / "src" / "planner" / "skills" / "panels-worker-coding" / "SKILL.md"
    before = source.read_text(encoding="utf-8")
    try:
        saved = service.save_specialist_skill(
            tmp_path,
            registry,
            "coding",
            {"description": "canonical test", "markdown_body": "# canonical\n"},
        )
        assert saved.specialist_skill.description == "canonical test"
        assert source.read_text(encoding="utf-8").startswith('---\nname: "panels-worker-coding"')
        assert not (service.managed_worker_settings_root(tmp_path) / "coding" / "SKILL.md").exists()
    finally:
        source.write_text(before, encoding="utf-8")


def test_hermes_skill_is_symlink_to_canonical_source(tmp_path: Path) -> None:
    provision_planner_home_skills(tmp_path / "home")
    source = Path(__file__).parents[2] / "src" / "planner" / "skills" / "panels-worker-coding"
    target = tmp_path / "home" / "skills" / "panels-worker-coding"
    assert target.is_symlink()
    assert target.resolve() == source.resolve()


def test_chief_skill_uses_same_canonical_source(tmp_path: Path) -> None:
    registry = configured_worker_type_registry()
    source = Path(__file__).parents[2] / "src" / "planner" / "skills" / "panels-chief-of-staff" / "SKILL.md"
    before = source.read_text(encoding="utf-8")
    try:
        saved = service.save_chief_skill(
            tmp_path,
            registry,
            {"description": "chief canonical test", "body": "# chief\n"},
        )
        assert saved.skill.description == "chief canonical test"
        assert saved.skill.name == "panels-chief-of-staff"
    finally:
        source.write_text(before, encoding="utf-8")

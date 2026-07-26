import concurrent.futures
import shutil
from pathlib import Path

import pytest

from planner.core.contracts import PlannerError
from planner.environments.hermes_home import provision_planner_home_skills
from planner.skill_sources import ensure_managed_panels_skills, provision_native_backend_skills
from planner.worker_settings import service
from planner.worker_types.configuration import configured_worker_type_registry


@pytest.fixture
def canonical_skills_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    source = service.panels_skill_root()
    target = tmp_path / "canonical-skills"
    shutil.copytree(source, target)
    monkeypatch.setattr(service, "panels_skill_root", lambda: target)
    return target


def test_worker_skill_edit_writes_managed_canonical_file(
    tmp_path: Path, canonical_skills_root: Path
) -> None:
    registry = configured_worker_type_registry()
    source = tmp_path / "skills" / "panels-worker-coding" / "SKILL.md"
    saved = service.save_specialist_skill(
        tmp_path,
        registry,
        "coding",
        {"description": "canonical test", "markdown_body": "# canonical\n"},
    )
    assert saved.specialist_skill.description == "canonical test"
    assert source.read_text(encoding="utf-8").startswith('---\nname: "panels-worker-coding"')
    assert not (service.managed_worker_settings_root(tmp_path) / "coding" / "SKILL.md").exists()


def test_hermes_skill_is_symlink_to_managed_source(tmp_path: Path) -> None:
    provision_planner_home_skills(tmp_path / "home", configured_database_parent=tmp_path)
    source = tmp_path / "skills" / "panels-worker-coding"
    target = tmp_path / "home" / "skills" / "panels-worker-coding"
    assert target.is_symlink()
    assert target.resolve() == source.resolve()


def test_seed_prefers_legacy_live_edit_and_never_clobbers_managed_file(tmp_path: Path) -> None:
    legacy = tmp_path / "worker-settings" / "coding" / "SKILL.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        '---\nname: "panels-worker-coding"\ndescription: "legacy edit"\n---\n# legacy\n',
        encoding="utf-8",
    )
    root = ensure_managed_panels_skills(tmp_path)
    managed = root / "panels-worker-coding" / "SKILL.md"
    assert 'description: "legacy edit"' in managed.read_text(encoding="utf-8")
    managed.write_text(
        '---\nname: "panels-worker-coding"\ndescription: "managed edit"\n---\n# managed\n',
        encoding="utf-8",
    )
    ensure_managed_panels_skills(tmp_path)
    assert 'description: "managed edit"' in managed.read_text(encoding="utf-8")


def test_native_skills_directory_preserves_custom_entries_and_replaces_panels_collision(
    tmp_path: Path,
) -> None:
    managed = ensure_managed_panels_skills(tmp_path)
    native_skills = tmp_path / "provider" / "skills"
    (native_skills / "custom").mkdir(parents=True)
    (native_skills / "custom" / "SKILL.md").write_text("custom", encoding="utf-8")
    (native_skills / "panels-worker-coding").mkdir()
    (native_skills / "panels-worker-coding" / "SKILL.md").write_text("stale", encoding="utf-8")

    provision_native_backend_skills(tmp_path / "provider", tmp_path)

    assert (native_skills / "custom" / "SKILL.md").read_text(encoding="utf-8") == "custom"
    assert (native_skills / "panels-worker-coding").resolve() == (
        managed / "panels-worker-coding"
    ).resolve()


def test_native_skills_root_symlink_merges_custom_entries_and_replaces_panels_collision(
    tmp_path: Path,
) -> None:
    managed = ensure_managed_panels_skills(tmp_path)
    legacy = tmp_path / "legacy-skills"
    (legacy / "custom").mkdir(parents=True)
    (legacy / "custom" / "SKILL.md").write_text("custom", encoding="utf-8")
    (legacy / "panels-worker-coding").mkdir()
    (legacy / "panels-worker-coding" / "SKILL.md").write_text("stale", encoding="utf-8")
    native_home = tmp_path / "provider"
    native_home.mkdir()
    (native_home / "skills").symlink_to(legacy, target_is_directory=True)

    provision_native_backend_skills(native_home, tmp_path)

    assert not (native_home / "skills").is_symlink()
    assert (native_home / "skills" / "custom").resolve() == (legacy / "custom").resolve()
    assert (native_home / "skills" / "panels-worker-coding").resolve() == (
        managed / "panels-worker-coding"
    ).resolve()


def test_chief_skill_uses_same_canonical_source(
    tmp_path: Path, canonical_skills_root: Path
) -> None:
    saved = service.save_chief_skill(
        tmp_path,
        {"description": "chief canonical test", "body": "# chief\n"},
    )
    assert saved.skill.description == "chief canonical test"
    assert saved.skill.name == "panels-chief-of-staff"


def test_every_skill_in_home_can_be_edited_and_is_catalogued(
    canonical_skills_root: Path,
) -> None:
    home = service.read_skills_home(canonical_skills_root.parent)
    assert len(home.skills) >= 3
    target = next(skill for skill in home.skills if skill.name == "panels")
    saved = service.save_skill(
        canonical_skills_root.parent,
        target.name,
        {"description": "edited", "markdown_body": target.markdown_body},
    )
    assert saved.description == "edited"
    assert 'description: "edited"' in (
        canonical_skills_root.parent / "skills" / "panels" / "SKILL.md"
    ).read_text(encoding="utf-8")


def test_skill_edit_rejects_path_traversal(canonical_skills_root: Path) -> None:
    with pytest.raises(PlannerError):
        service.save_skill(canonical_skills_root.parent, "..", {"description": "bad"})


def test_concurrent_skill_field_edits_preserve_both_fields(canonical_skills_root: Path) -> None:
    current = next(
        skill
        for skill in service.read_skills_home(canonical_skills_root.parent).skills
        if skill.name == "panels"
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                service.save_skill,
                canonical_skills_root.parent,
                "panels",
                {"description": "parallel"},
            ),
            executor.submit(
                service.save_skill,
                canonical_skills_root.parent,
                "panels",
                {"markdown_body": current.markdown_body + "\nparallel\n"},
            ),
        ]
        [future.result() for future in futures]
    final = next(
        skill
        for skill in service.read_skills_home(canonical_skills_root.parent).skills
        if skill.name == "panels"
    )
    assert final.description == "parallel"
    assert final.markdown_body.endswith("parallel\n")

import concurrent.futures
import shutil
from pathlib import Path

import pytest

from planner.core.contracts import PlannerError
from planner.environments.hermes_home import provision_planner_home_skills
from planner.skill_sources import (
    RETIRED_PANELS_SKILL_NAMES,
    ensure_managed_panels_skills,
    panels_skill_root,
    provision_native_backend_skills,
)
from planner.worker_settings import service
from planner.worker_types.configuration import configured_worker_type_registry


@pytest.fixture
def canonical_skills_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    source = panels_skill_root()
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


def test_supervisor_edit_reaches_every_backend_home_from_one_managed_source(
    tmp_path: Path,
) -> None:
    managed = ensure_managed_panels_skills(tmp_path)
    homes = {
        "hermes": tmp_path / "hermes-home",
        "codex": tmp_path / "codex-home",
        "claude": tmp_path / "claude-home",
    }
    provision_planner_home_skills(
        homes["hermes"], configured_database_parent=tmp_path
    )
    provision_native_backend_skills(homes["codex"], tmp_path)
    provision_native_backend_skills(homes["claude"], tmp_path)

    service.save_skill(
        tmp_path,
        "panels-sprint-item-supervisor",
        {
            "description": "Future conversations read this revision",
            "markdown_body": "# Supervisor\n\nUse canonical context.\n",
        },
    )

    canonical = managed / "panels-sprint-item-supervisor"
    for home in homes.values():
        exposed = home / "skills" / "panels-sprint-item-supervisor"
        assert exposed.resolve() == canonical.resolve()
        assert "Future conversations read this revision" in (
            exposed / "SKILL.md"
        ).read_text(encoding="utf-8")


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
    for retired_skill_name in RETIRED_PANELS_SKILL_NAMES:
        (native_skills / retired_skill_name).write_text("retired", encoding="utf-8")

    provision_native_backend_skills(tmp_path / "provider", tmp_path)
    provision_native_backend_skills(tmp_path / "provider", tmp_path)

    assert (native_skills / "custom" / "SKILL.md").read_text(encoding="utf-8") == "custom"
    for retired_skill_name in RETIRED_PANELS_SKILL_NAMES:
        assert not (native_skills / retired_skill_name).exists()
    assert (native_skills / "panels-worker-coding").resolve() == (
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

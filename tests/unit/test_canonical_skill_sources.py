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


def test_hermes_skill_is_symlink_to_managed_source(tmp_path: Path) -> None:
    provision_planner_home_skills(tmp_path / "home", configured_database_parent=tmp_path)
    source = tmp_path / "skills" / "panels-worker-coding"
    target = tmp_path / "home" / "skills" / "panels-worker-coding"
    assert target.is_symlink()
    assert target.resolve() == source.resolve()
    supervisor_source = tmp_path / "skills" / "panels-sprint-item-supervisor"
    supervisor_target = tmp_path / "home" / "skills" / "panels-sprint-item-supervisor"
    assert supervisor_target.is_symlink()
    assert supervisor_target.resolve() == supervisor_source.resolve()


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


def test_supervisor_skill_preserves_the_operating_contract() -> None:
    guidance = (
        panels_skill_root() / "panels-sprint-item-supervisor" / "SKILL.md"
    ).read_text(encoding="utf-8")
    normalized = " ".join(guidance.split())

    for required in (
        "The Sprint Item body is the shared brief.",
        "Routine progress needs no response.",
        "If a required action or message fails, do not acknowledge the obligation.",
        "Supervise only current child Tickets.",
        "Approve only when the current record proves the accepted outcome.",
        "Ask the user before destructive, irreversible, security-sensitive, or",
        "`awaiting_user_review` and `needs_user` as user-owned escalation states",
        "The readiness system owns Worker starts.",
        "Re-read canonical context after a restart",
    ):
        assert required in normalized


def test_debugging_worker_runtime_app_boundary_is_packaged_for_new_homes(tmp_path: Path) -> None:
    source = panels_skill_root() / "panels-worker-debugging" / "SKILL.md"
    guidance = source.read_text(encoding="utf-8")

    assert "/home/vps/Deployments/Panels/current/app" in guidance
    assert (
        "Never use it as the current working directory, a test root, or a source tree."
        in guidance
    )
    assert "logs or service state" in guidance
    assert "assigned checkout or an isolated deployed-revision copy" in guidance
    assert "suitable checkout when one is not available" in guidance

    managed = ensure_managed_panels_skills(tmp_path / "data")
    assert (managed / "panels-worker-debugging" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == guidance


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


def test_fresh_managed_skills_never_seed_retired_packaged_skill(tmp_path: Path) -> None:
    packaged = tmp_path / "packaged"
    for retired_skill_name in RETIRED_PANELS_SKILL_NAMES:
        retired = packaged / retired_skill_name
        retired.mkdir(parents=True, exist_ok=True)
        (retired / "SKILL.md").write_text("retired", encoding="utf-8")
    active = packaged / "panels"
    active.mkdir()
    (active / "SKILL.md").write_text("active", encoding="utf-8")

    root = ensure_managed_panels_skills(
        tmp_path / "data", packaged_skill_root=packaged
    )

    assert RETIRED_PANELS_SKILL_NAMES == frozenset(
        {
            "panels-rollover",
            "panels-sprint-planning",
            "panels-ticket-management",
        }
    )
    for retired_skill_name in RETIRED_PANELS_SKILL_NAMES:
        assert not (root / retired_skill_name).exists()
    assert (root / "panels" / "SKILL.md").read_text(encoding="utf-8") == "active"


@pytest.mark.parametrize("retired_path_kind", ("file", "directory", "symlink"))
def test_managed_skills_remove_retired_path_and_preserve_other_entries(
    tmp_path: Path, retired_path_kind: str
) -> None:
    managed = tmp_path / "skills"
    managed.mkdir()
    custom = managed / "custom"
    custom.mkdir()
    (custom / "SKILL.md").write_text("custom", encoding="utf-8")
    retired_paths = [managed / name for name in RETIRED_PANELS_SKILL_NAMES]
    for retired in retired_paths:
        if retired_path_kind == "file":
            retired.write_text("retired", encoding="utf-8")
        elif retired_path_kind == "directory":
            retired.mkdir()
            (retired / "SKILL.md").write_text("retired", encoding="utf-8")
        else:
            retired.symlink_to(tmp_path / f"missing-{retired.name}")

    ensure_managed_panels_skills(tmp_path)
    ensure_managed_panels_skills(tmp_path)

    for retired in retired_paths:
        assert not retired.exists()
        assert not retired.is_symlink()
    assert (custom / "SKILL.md").read_text(encoding="utf-8") == "custom"


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


def test_native_skills_root_symlink_merges_custom_entries_and_replaces_panels_collision(
    tmp_path: Path,
) -> None:
    managed = ensure_managed_panels_skills(tmp_path)
    legacy = tmp_path / "legacy-skills"
    (legacy / "custom").mkdir(parents=True)
    (legacy / "custom" / "SKILL.md").write_text("custom", encoding="utf-8")
    (legacy / "panels-worker-coding").mkdir()
    (legacy / "panels-worker-coding" / "SKILL.md").write_text("stale", encoding="utf-8")
    for retired_skill_name in RETIRED_PANELS_SKILL_NAMES:
        retired = legacy / retired_skill_name
        retired.mkdir()
        (retired / "SKILL.md").write_text("retired", encoding="utf-8")
    native_home = tmp_path / "provider"
    native_home.mkdir()
    (native_home / "skills").symlink_to(legacy, target_is_directory=True)

    provision_native_backend_skills(native_home, tmp_path)

    assert not (native_home / "skills").is_symlink()
    assert (native_home / "skills" / "custom").resolve() == (legacy / "custom").resolve()
    for retired_skill_name in RETIRED_PANELS_SKILL_NAMES:
        assert not (native_home / "skills" / retired_skill_name).exists()
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

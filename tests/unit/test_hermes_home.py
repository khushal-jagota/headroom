from __future__ import annotations

from pathlib import Path

from planner.environments.hermes_home import (
    PLANNER_SKILL_NAMES,
    hermes_src_root,
    provision_planner_home_skills,
    resolve_hermes_python,
    resolve_planner_home,
)
from planner.skill_sources import panels_skill_root


def test_resolve_hermes_python_precedence() -> None:
    assert (
        resolve_hermes_python(None, env={})
        == Path("~/.hermes/hermes-agent/venv/bin/python").expanduser()
    )
    assert resolve_hermes_python("/opt/py", env={"PLAN_HERMES_PYTHON": "/env/py"}) == Path(
        "/opt/py"
    )
    assert (
        resolve_hermes_python(None, env={"PLAN_HERMES_PYTHON": "~/envpy"})
        == Path("~/envpy").expanduser()
    )


def test_hermes_src_root() -> None:
    assert hermes_src_root(Path("/x/hermes-agent/venv/bin/python")) == Path("/x/hermes-agent")
    assert hermes_src_root(Path("/python")) == Path("/")


def test_resolve_planner_home_precedence() -> None:
    assert resolve_planner_home(None, env={}) == Path("~/.hermes").expanduser()
    assert resolve_planner_home(None, env={}, default="/database/home") == Path("/database/home")
    assert resolve_planner_home(
        None, env={"PLAN_HERMES_HOME": "/env"}, default="/database/home"
    ) == Path("/env")
    assert resolve_planner_home("/explicit", env={"PLAN_HERMES_HOME": "/env"}) == Path("/explicit")


def test_provision_planner_home_skills_symlinks_packaged_skills_idempotently(
    tmp_path: Path,
) -> None:
    names = (
        "panels",
        "panels-worker",
        "panels-chief-of-staff",
        "panels-update-chief-of-staff",
    )
    assert panels_skill_root() == Path(__file__).resolve().parents[2] / "src/planner/skills"
    provision_planner_home_skills(tmp_path / "home", names, configured_database_parent=tmp_path)
    provision_planner_home_skills(tmp_path / "home", names, configured_database_parent=tmp_path)
    for name in names:
        target = tmp_path / "home" / "skills" / name
        assert target.is_symlink()
        assert (target / "SKILL.md").is_file()


def test_provision_planner_home_skills_removes_retired_exposure_and_preserves_custom(
    tmp_path: Path,
) -> None:
    target_root = tmp_path / "home" / "skills"
    target_root.mkdir(parents=True)
    custom = target_root / "custom"
    custom.mkdir()
    (custom / "SKILL.md").write_text("custom", encoding="utf-8")
    retired = target_root / "panels-ticket-management"
    retired.mkdir()
    (retired / "SKILL.md").write_text("retired", encoding="utf-8")

    provision_planner_home_skills(
        tmp_path / "home",
        ("panels", "panels-ticket-management"),
        configured_database_parent=tmp_path,
    )
    provision_planner_home_skills(
        tmp_path / "home",
        ("panels", "panels-ticket-management"),
        configured_database_parent=tmp_path,
    )

    assert not retired.exists()
    assert (custom / "SKILL.md").read_text(encoding="utf-8") == "custom"
    assert (target_root / "panels").is_symlink()


def test_panels_owns_shared_system_and_communication_guidance() -> None:
    root = panels_skill_root()
    shared = (root / "panels" / "SKILL.md").read_text(encoding="utf-8")
    worker = (root / "panels-worker" / "SKILL.md").read_text(encoding="utf-8")
    chief = (root / "panels-chief-of-staff" / "SKILL.md").read_text(encoding="utf-8")

    assert "panels" in PLANNER_SKILL_NAMES
    assert "## Communication" in shared
    assert "Inspect the relevant source, docs, or workspace state before advising." in shared
    assert "## The system" not in worker
    assert "## The system" not in chief
    assert "Ground before you opine." not in worker
    assert "Keep your responses practical and grounded in the actual workspace." not in chief

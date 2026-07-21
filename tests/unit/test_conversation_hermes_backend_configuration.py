from __future__ import annotations

from pathlib import Path

from planner.conversation.hermes_backend_configuration import (
    hermes_src_root,
    provision_planner_home_skills,
    resolve_hermes_python,
    resolve_planner_home,
)


def test_resolve_hermes_python_precedence() -> None:
    assert resolve_hermes_python(None, env={}) == Path(
        "~/.hermes/hermes-agent/venv/bin/python"
    ).expanduser()
    assert resolve_hermes_python(
        "/opt/py", env={"PLAN_HERMES_PYTHON": "/env/py"}
    ) == Path("/opt/py")
    assert resolve_hermes_python(
        None, env={"PLAN_HERMES_PYTHON": "~/envpy"}
    ) == Path("~/envpy").expanduser()


def test_hermes_src_root() -> None:
    assert hermes_src_root(Path("/x/hermes-agent/venv/bin/python")) == Path(
        "/x/hermes-agent"
    )
    assert hermes_src_root(Path("/python")) == Path("/")


def test_resolve_planner_home_precedence() -> None:
    assert resolve_planner_home(None, env={}) == Path("data/hermes-home")
    assert resolve_planner_home(None, env={}, default="/database/home") == Path(
        "/database/home"
    )
    assert resolve_planner_home(
        None, env={"PLAN_HERMES_HOME": "/env"}, default="/database/home"
    ) == Path("/env")
    assert resolve_planner_home(
        "/explicit", env={"PLAN_HERMES_HOME": "/env"}
    ) == Path("/explicit")


def test_provision_planner_home_skills_symlinks_repo_skills_idempotently(
    tmp_path: Path,
) -> None:
    names = ("panels", "panels-worker", "panels-chief-of-staff")
    provision_planner_home_skills(tmp_path, names)
    provision_planner_home_skills(tmp_path, names)
    for name in names:
        target = tmp_path / "skills" / name
        assert target.is_symlink()
        assert (target / "SKILL.md").is_file()

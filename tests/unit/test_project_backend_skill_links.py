from __future__ import annotations

import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _skill_names(root: Path) -> set[str]:
    return {skill.parent.name for skill in root.glob("*/SKILL.md") if skill.is_file()}


def test_canonical_panels_skills_are_packaged_and_exposed_to_native_projects() -> None:
    canonical_skills = REPOSITORY_ROOT / "src/planner/skills"
    expected_names = _skill_names(canonical_skills)
    package_data = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["setuptools"]["package-data"]["planner"]

    assert expected_names
    for source in canonical_skills.rglob("*"):
        if source.is_file() and not source.name.startswith("."):
            package_path = source.relative_to(canonical_skills.parent)
            assert any(package_path.match(pattern) for pattern in package_data), package_path
    for native_skills in (
        REPOSITORY_ROOT / ".agents/skills",
        REPOSITORY_ROOT / ".claude/skills",
    ):
        assert native_skills.is_symlink()
        assert native_skills.resolve(strict=True) == canonical_skills.resolve(strict=True)
        assert _skill_names(native_skills) == expected_names

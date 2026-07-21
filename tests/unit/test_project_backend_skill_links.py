from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _skill_names(root: Path) -> set[str]:
    return {
        skill.parent.name
        for skill in root.glob("*/SKILL.md")
        if skill.is_file()
    }


def test_native_project_skill_roots_expose_every_canonical_panels_skill() -> None:
    canonical_skills = REPOSITORY_ROOT / "skills"
    expected_names = _skill_names(canonical_skills)

    assert expected_names
    for native_skills in (
        REPOSITORY_ROOT / ".agents/skills",
        REPOSITORY_ROOT / ".claude/skills",
    ):
        assert native_skills.is_symlink()
        assert native_skills.resolve(strict=True) == canonical_skills.resolve(strict=True)
        assert _skill_names(native_skills) == expected_names

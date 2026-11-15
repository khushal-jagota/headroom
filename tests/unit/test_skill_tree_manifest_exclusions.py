"""Codex-owned files can change without hiding changes to user skills."""

from pathlib import Path

from tests.support.skill_tree_protection.inventory import (
    capture_launch_inventory,
    compare_manifests,
    snapshot,
)


def test_codex_system_rewrites_leave_other_skill_changes_visible(tmp_path: Path) -> None:
    account = tmp_path / "account"
    home = tmp_path / "home"
    configured = tmp_path / "configured"
    alias = tmp_path / "codex-home"
    configured.mkdir()
    alias.symlink_to(configured, target_is_directory=True)
    codex_roots = (account / ".codex/skills", home / ".codex/skills", configured / "skills")
    for root in codex_roots:
        (root / ".system/openai-docs/references").mkdir(parents=True)
        (root / ".system/.codex-system-skills.marker").write_text("old")
        (root / ".system/openai-docs/references/model-migration.md").write_text("old")
    claude_system = home / ".claude/skills/.system/SKILL.md"
    claude_system.parent.mkdir(parents=True)
    claude_system.write_text("old")
    panels_skill = tmp_path / "data/skills/panels/SKILL.md"
    panels_skill.parent.mkdir(parents=True)
    panels_skill.write_text("old")
    user_skill = codex_roots[1] / "user/SKILL.md"
    user_skill.parent.mkdir()
    user_skill.write_text("old")
    parent_link = codex_roots[1] / "parent-link"
    parent_link.symlink_to(".system/../user", target_is_directory=True)
    removed = codex_roots[1] / "removed"
    removed.write_text("old")
    external = tmp_path / "external"
    external.mkdir()
    external_skill = external / "SKILL.md"
    external_skill.write_text("old")
    linked = codex_roots[1] / "linked"
    linked.symlink_to(external, target_is_directory=True)
    inventory, baseline = capture_launch_inventory(
        {"HOME": str(home), "CODEX_HOME": str(alias)}, tmp_path, account,
    )
    assert any(
        entry.path == str(codex_roots[1] / ".system/../user")
        for entry in baseline.entries
    )
    for root in codex_roots:
        (root / ".system/.codex-system-skills.marker").write_text("rewritten")
        (root / ".system/openai-docs/references/model-migration.md").unlink()
        (root / ".system/new").mkdir()
    assert not compare_manifests(baseline, snapshot(inventory))

    for path in (claude_system, panels_skill, user_skill, external_skill):
        path.write_text("changed")
    removed.unlink()
    added = codex_roots[1] / "added"
    added.mkdir()
    linked.unlink()
    linked.symlink_to(tmp_path / "new-target", target_is_directory=True)
    differences = {
        item.path: item.change for item in compare_manifests(baseline, snapshot(inventory))
    }
    assert differences == {
        str(claude_system): "changed",
        str(panels_skill): "changed",
        str(user_skill): "changed",
        str(external_skill): "changed",
        str(removed): "removed",
        str(added): "added",
        str(linked): "changed",
    }


def test_system_link_does_not_exempt_external_target_of_user_skill(tmp_path: Path) -> None:
    skills = tmp_path / ".codex/skills"
    skills.mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    skill = external / "SKILL.md"
    skill.write_text("old")
    (skills / ".system").symlink_to(external, target_is_directory=True)
    (skills / "user").symlink_to(external, target_is_directory=True)
    inventory, baseline = capture_launch_inventory({"HOME": str(tmp_path)}, tmp_path, tmp_path)
    skill.write_text("changed")
    assert {item.path for item in compare_manifests(baseline, snapshot(inventory))} == {str(skill)}

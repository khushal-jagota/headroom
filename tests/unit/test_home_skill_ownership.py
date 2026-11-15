"""A second home must not claim or erase the account's existing Panels skills."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from planner.environments.cli import environment
from planner.skill_sources import (
    account_skill_provisioning_conflict,
    provision_account_skills,
)


def test_account_skill_ownership(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = tmp_path / "first" / "data"
    second = tmp_path / "second" / "data"
    for root in (first, second):
        for name in ("panels", "panels-worker"):
            folder = root / "skills" / name
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(f"{root}: {name}")
    homes = tuple(tmp_path / name for name in ("hermes", "codex", "claude"))
    assert account_skill_provisioning_conflict(first, homes) is None
    assert not any(home.exists() for home in homes)  # Setup preflight is read-only.
    assert provision_account_skills(first, homes) is None
    hidden = "panels-worker-personal-task"
    (first / "skills" / hidden).mkdir()
    owned_hidden = homes[0] / "skills" / hidden
    owned_hidden.symlink_to(first / "skills" / hidden)
    links = [home / "skills" / name for home in homes for name in ("panels", "panels-worker")]
    before = {link: (link.lstat().st_ino, link.readlink()) for link in links}
    assert provision_account_skills(first, homes) is None
    assert {link: (link.lstat().st_ino, link.readlink()) for link in links} == before
    assert not owned_hidden.is_symlink()
    for link in links:
        assert link.resolve() == first / "skills" / link.name

    unknown = homes[0] / "skills" / "user-skill"
    unknown.mkdir()
    (unknown / "note").write_text("keep")
    foreign_hidden = homes[0] / "skills" / "panels-rollover"
    foreign_hidden.symlink_to(tmp_path / "foreign" / "panels-rollover")
    unused = tmp_path / "a-unused-home"
    warning = provision_account_skills(second, (unused, *homes))
    assert warning is not None and "one instance per OS account" in warning
    assert account_skill_provisioning_conflict(second, homes) == warning
    assert account_skill_provisioning_conflict(second, tuple(reversed(homes))) == warning
    assert str(first / "skills") in warning
    assert not unused.exists()  # All providers are checked before any provider write.
    cli = CliRunner().invoke(
        environment,
        [
            "provision-skills",
            "--database-parent",
            str(second),
            "--hermes-home",
            str(homes[0]),
            "--codex-home",
            str(homes[1]),
            "--claude-home",
            str(homes[2]),
        ],
    )
    assert cli.exit_code == 0
    assert "one instance per OS account" in cli.output
    assert "provisioned Panels skills" not in cli.output
    assert {link: (link.lstat().st_ino, link.readlink()) for link in links} == before
    assert foreign_hidden.readlink() == tmp_path / "foreign" / "panels-rollover"
    assert (unknown / "note").read_text() == "keep"
    warning = provision_account_skills(first, homes)
    assert warning is not None and str(foreign_hidden) in warning
    assert foreign_hidden.is_symlink()

    legacy = tmp_path / "legacy-home"
    legacy.mkdir()
    (legacy / "skills").symlink_to(first / "skills", target_is_directory=True)
    warning = provision_account_skills(second, (legacy,))
    assert warning is not None and str(first / "skills") in warning
    assert (legacy / "skills").readlink() == first / "skills"
    assert provision_account_skills(first, (legacy,)) is None
    assert not (legacy / "skills").is_symlink()
    assert (legacy / "skills" / "panels").resolve() == first / "skills" / "panels"
    assert not (legacy / "skills" / hidden).exists()

    unowned = tmp_path / "unowned-home"
    (unowned / "skills" / "panels").mkdir(parents=True)
    marker = unowned / "skills" / "panels" / "SKILL.md"
    marker.write_text("user-owned")
    warning = provision_account_skills(first, (unowned,))
    assert warning is not None and "unowned entry" in warning
    assert marker.read_text() == "user-owned"

    looping_home = tmp_path / "looping-home"
    looping_home.mkdir()
    loop = looping_home / "skills"
    loop.symlink_to("skills", target_is_directory=True)
    original_resolve = Path.resolve

    def python312_resolve(path: Path, strict: bool = False) -> Path:
        # Python 3.12 raises for non-strict symlink loops. Python 3.13 does not.
        if path == loop:
            raise RuntimeError(f"Symlink loop from {path}")
        return original_resolve(path, strict=strict)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "resolve", python312_resolve)
        warning = account_skill_provisioning_conflict(first, (looping_home,))
        assert warning is not None and str(loop) in warning
        warning = provision_account_skills(first, (looping_home,))
        assert warning is not None and "one instance per OS account" in warning
    assert loop.readlink() == Path("skills")

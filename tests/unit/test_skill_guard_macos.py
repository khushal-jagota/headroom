"""The macOS descriptor lookup preserves refusal for protected skills."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.support.skill_tree_protection.audit import AuditGuard
from tests.support.skill_tree_protection.contracts import GuardRefusal, ProtectionInventory


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS F_GETPATH descriptor proof")
def test_macos_directory_descriptor_resolves_private_and_protected_paths(tmp_path: Path) -> None:
    skills, private = tmp_path / "skills", tmp_path / "private"
    skills.mkdir()
    private.mkdir()
    (skills / "SKILL.md").write_text("sentinel")
    (private / "scratch").write_text("removable")
    guard = AuditGuard(ProtectionInventory((str(skills),), (str(skills),)))
    protected_fd, private_fd = os.open(skills, os.O_RDONLY), os.open(private, os.O_RDONLY)
    try:
        with pytest.raises(GuardRefusal, match="protected launch skill path"):
            guard._audit("os.remove", ("SKILL.md", protected_fd))
        assert (skills / "SKILL.md").read_text() == "sentinel"
        guard._audit("os.remove", ("scratch", private_fd))
        os.unlink("scratch", dir_fd=private_fd)
        assert not (private / "scratch").exists()
        with pytest.raises(GuardRefusal, match="descriptor base is unavailable"):
            guard._audit("os.remove", ("unknown", 999999))
    finally:
        os.close(protected_fd)
        os.close(private_fd)


def test_open_wrapper_retains_directory_base_and_refuses_unobserved_calls(tmp_path: Path) -> None:
    script = r'''
import os
import sys
from pathlib import Path
from tests.support.skill_tree_protection.audit import AuditGuard
from tests.support.skill_tree_protection.contracts import GuardRefusal, ProtectionInventory
root = Path(sys.argv[1])
skills = root / "skills"
skills.mkdir()
(skills / "SKILL.md").write_text("sentinel")
private = root / "private"
private.mkdir()
native_open = os.open
guard = AuditGuard(ProtectionInventory((str(skills),), (str(skills),)))
guard.install()
assert os.open in os.supports_dir_fd
protected_fd = os.open(skills, os.O_RDONLY)
private_fd = os.open(private, os.O_RDONLY)
try:
    os.close(os.open("allowed", os.O_WRONLY | os.O_CREAT, dir_fd=private_fd))
    assert (private / "allowed").exists()
    for operation in (
        lambda: os.open("SKILL.md", os.O_WRONLY | os.O_TRUNC, dir_fd=protected_fd),
        lambda: os.open("../skills/SKILL.md", os.O_WRONLY, dir_fd=private_fd),
        lambda: os.open("unknown", os.O_WRONLY | os.O_CREAT, dir_fd=999999),
        lambda: native_open("unobserved", os.O_WRONLY | os.O_CREAT, dir_fd=private_fd),
    ):
        try:
            operation()
        except GuardRefusal:
            pass
        else:
            raise AssertionError("unproved write escaped the guard")
    assert len(guard.receipts) == 4
    assert (skills / "SKILL.md").read_text() == "sentinel"
    assert not (private / "unobserved").exists()
finally:
    os.close(protected_fd)
    os.close(private_fd)
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr

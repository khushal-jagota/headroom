"""Protect source-home custody from crossed paths and shell splitting."""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest
from scripts.mac_home_setup import write_home


def test_service_and_backup_keep_one_record_with_space_paths(tmp_path: Path) -> None:
    # No existing test covers generated launchd jobs. A wrong backup source or
    # shell-split interpreter could silently omit the running home's record.
    source = tmp_path / "source with spaces"
    interpreter = source / ".venv/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    root = tmp_path / "state with spaces"
    write_home(root, source, "com.panels.test", 9876, "a" * 40)
    home = plistlib.loads((root / "agents/com.panels.test.plist").read_bytes())
    backup = plistlib.loads((root / "agents/com.panels.test.backup.plist").read_bytes())
    environment = home["EnvironmentVariables"]
    assert home["ProgramArguments"] == [str(interpreter), "-m", "planner", "serve"]
    assert backup["EnvironmentVariables"] == environment
    arguments = backup["ProgramArguments"]
    assert arguments[0] == str(interpreter)
    assert arguments[arguments.index("--source-db") + 1] == environment["PLAN_DB_PATH"]
    assert arguments[arguments.index("--backup-dir") + 1] == environment["PLAN_BACKUP_DIR"]
    assert arguments[arguments.index("--deployed-revision") + 1] == environment["PLAN_APP_SHA"]
    assert environment["PLAN_WORKSPACE_ROOT"] == str(root / "workspaces")
    assert environment["CODEX_HOME"] == str(root / "providers/codex")
    assert environment["CLAUDE_CONFIG_DIR"] == str(root / "providers/claude")
    assert environment["HOME"] == str(Path.home())
    assert home["AbandonProcessGroup"] is False


def test_existing_root_is_not_reconfigured(tmp_path: Path) -> None:
    # Regeneration must not repoint an existing home's service and backup paths.
    source = tmp_path / "source"
    interpreter = source / ".venv/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    root = tmp_path / "state"
    write_home(root, source, "com.panels.test", 9876, "a" * 40)
    plist = root / "agents/com.panels.test.plist"
    original = plist.read_bytes()
    with pytest.raises(FileExistsError):
        write_home(root, source, "com.panels.test", 9877, "b" * 40)
    assert plist.read_bytes() == original

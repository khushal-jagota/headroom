"""Protect source-home custody from crossed paths and shell splitting."""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest
from scripts.mac_home_setup import write_home

from planner.core.config import load_config


def test_service_and_backup_keep_one_record_with_space_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # No existing test covers generated launchd jobs. A wrong backup source or
    # shell-split interpreter could silently omit the running home's record.
    source = tmp_path / "source with spaces"
    interpreter = source / ".venv/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    root = tmp_path / "state with spaces"
    codex_home = tmp_path / "existing codex"
    hermes_home = tmp_path / "existing hermes"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("PLAN_HERMES_HOME", str(hermes_home))
    warning = (
        "Only one Panels instance can own account skills per OS account. Use a separate OS account."
    )
    monkeypatch.setattr(
        "scripts.mac_home_setup.account_skill_provisioning_conflict",
        lambda *args, **kwargs: warning,
    )
    write_home(root, source, "com.panels.test", 9876, "a" * 40)
    assert f"Warning: {warning}" in capsys.readouterr().out
    config_path = str(root / "data/config.yaml")
    assert load_config(config_path, env={}).provision_account_skills is True
    assert (
        load_config(
            config_path, env={"PLAN_PROVISION_ACCOUNT_SKILLS": "0"}
        ).provision_account_skills
        is False
    )
    assert load_config(str(tmp_path / "absent.yaml"), env={}).provision_account_skills is None
    home = plistlib.loads((root / "agents/com.panels.test.plist").read_bytes())
    backup = plistlib.loads((root / "agents/com.panels.test.backup.plist").read_bytes())
    environment = home["EnvironmentVariables"]
    assert home["ProgramArguments"] == [str(interpreter), "-m", "planner", "serve"]
    assert backup["EnvironmentVariables"] == environment
    for suffix in ("maintenance", "stall-watchdog"):
        scheduled = plistlib.loads((root / f"agents/com.panels.test.{suffix}.plist").read_bytes())
        assert scheduled["ProgramArguments"][:3] == [str(interpreter), "-m", "planner"]
        assert scheduled["WorkingDirectory"] == str(source)
        assert scheduled["EnvironmentVariables"] == environment
    arguments = backup["ProgramArguments"]
    assert arguments[0] == str(interpreter)
    assert arguments[arguments.index("--source-db") + 1] == environment["PLAN_DB_PATH"]
    assert arguments[arguments.index("--backup-dir") + 1] == environment["PLAN_BACKUP_DIR"]
    assert arguments[arguments.index("--deployed-revision") + 1] == environment["PLAN_APP_SHA"]
    assert environment["PLAN_WORKSPACE_ROOT"] == str(root / "workspaces")
    assert environment["CODEX_HOME"] == str(codex_home)
    assert environment["CLAUDE_CONFIG_DIR"] == str((Path.home() / ".claude").resolve())
    assert environment["PLAN_HERMES_HOME"] == str(hermes_home)
    assert environment["HOME"] == str(Path.home())
    assert not (root / "providers").exists()
    assert not codex_home.exists()
    assert not hermes_home.exists()
    assert home["AbandonProcessGroup"] is False
    # A disposable test can explicitly keep all provider state away from the
    # existing account. This protection must not become the install default.
    isolated_root = tmp_path / "isolated state"
    write_home(
        isolated_root,
        source,
        "com.panels.isolated",
        9877,
        "a" * 40,
        isolated_providers=True,
    )
    isolated = plistlib.loads((isolated_root / "agents/com.panels.isolated.plist").read_bytes())[
        "EnvironmentVariables"
    ]
    assert (
        load_config(str(isolated_root / "data/config.yaml"), env={}).provision_account_skills
        is False
    )
    assert capsys.readouterr().out == ""
    for variable, folder in (
        ("CODEX_HOME", "codex"),
        ("CLAUDE_CONFIG_DIR", "claude"),
        ("PLAN_HERMES_HOME", "hermes"),
    ):
        provider = isolated_root / "providers" / folder
        assert isolated[variable] == str(provider)
        assert list(provider.iterdir()) == []


def test_existing_root_is_not_reconfigured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Regeneration must not repoint an existing home's service and backup paths.
    source = tmp_path / "source"
    interpreter = source / ".venv/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    root = tmp_path / "state"
    monkeypatch.setattr(
        "scripts.mac_home_setup.account_skill_provisioning_conflict", lambda *args, **kwargs: None
    )
    write_home(root, source, "com.panels.test", 9876, "a" * 40)
    plist = root / "agents/com.panels.test.plist"
    original = plist.read_bytes()
    with pytest.raises(FileExistsError):
        write_home(root, source, "com.panels.test", 9877, "b" * 40)
    assert plist.read_bytes() == original

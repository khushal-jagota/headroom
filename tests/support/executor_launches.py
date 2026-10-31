"""Scripted provider launch configuration."""

from dataclasses import replace
from pathlib import Path

import pytest

from planner.conversation.production_backends import (
    ProductionBackendLaunches,
    production_backend_launches,
)


def launch_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProductionBackendLaunches:
    executable = tmp_path / "selected-cli"
    executable.write_text("fixture, never executed")
    executable.chmod(0o700)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "provider-home"))
    launches = production_backend_launches(panels_server_url="http://fixture.invalid")
    return replace(
        launches,
        codex=replace(
            launches.codex,
            argv=(str(executable), "app-server"),
            environment_overrides=(("PROVIDER_FIXTURE", "selected"),),
        ),
        claude=replace(
            launches.claude,
            claude_executable=executable,
            environment_overrides=(("PROVIDER_FIXTURE", "selected"),),
        ),
    )

"""Build checks use current isolated CLIs without changing the owner's installation."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from development_safety import launcher
from development_safety.contracts import SafetyRefusal
from scripts import deployment_codex


def test_backend_install_refreshes_current_cli_and_refuses_lockfile_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The storage scanner has separate proof. This test covers the install policy
    # that otherwise leaves an existing installation stale despite a `latest` manifest.
    monkeypatch.setattr(launcher, "node_safety", lambda *_: tmp_path)
    monkeypatch.setattr(launcher, "check_paths", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(shutil, "which", lambda name, **_: f"/usr/bin/{name}")
    command = launcher.npm_command(tmp_path, ["--prefix", "agent_backends", "install"], {})
    assert command == [
        "/usr/bin/npm", "--prefix", str(tmp_path), "install", "--package-lock=false",
        "--save=false", "@anthropic-ai/claude-code@latest", "@openai/codex@latest",
    ]
    with pytest.raises(SafetyRefusal, match="Backend CLIs have no lockfile"):
        launcher.npm_command(tmp_path, ["--prefix", "agent_backends", "ci"], {})


def test_build_accepts_resolved_version_and_refuses_an_external_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "agent_backends/node_modules/.bin/codex"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\necho 'codex-cli 9.4.0'\n")
    executable.chmod(0o755)
    assert deployment_codex.setup(tmp_path) == executable.parent
    monkeypatch.setattr(shutil, "which", lambda _: str(executable))
    deployment_codex.verify(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda _: "/owner/.local/bin/codex")
    with pytest.raises(RuntimeError, match="requested source"):
        deployment_codex.verify(tmp_path)

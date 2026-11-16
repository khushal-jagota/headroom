"""Build checks use current isolated CLIs without changing the owner's installation."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import dev_launcher as launcher
import pytest
from scripts import deployment_codex


def test_backend_install_refreshes_current_cli_and_refuses_lockfile_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This test covers the install policy
    # that otherwise leaves an existing installation stale despite a `latest` manifest.
    monkeypatch.setattr(shutil, "which", lambda name, **_: f"/usr/bin/{name}")
    command = launcher.npm_command(tmp_path, ["--prefix", "agent_backends", "install"], {})
    assert command == [
        "/usr/bin/npm", "--prefix", str(tmp_path / "agent_backends"), "install",
        "--package-lock=false",
        "--save=false", "@anthropic-ai/claude-code@latest", "@openai/codex@latest",
    ]
    with pytest.raises(ValueError, match="Backend CLIs have no lockfile"):
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


def test_dev_npm_forwards_arguments_working_directory_and_exit_status(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "npm-call.json"
    npm = tmp_path / "npm"
    npm.write_text(
        "#!/usr/bin/python3\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        f"Path({str(output)!r}).write_text(json.dumps([os.getcwd(), sys.argv[1:]]))\n"
        "raise SystemExit(17)\n"
    )
    npm.chmod(0o755)
    result = subprocess.run(
        [str(root / "scripts/dev"), "npm", "--prefix", "web",
         "run", "build", "--", "--mode", "test"],
        cwd=tmp_path,
        env={"PATH": str(tmp_path), "HOME": str(tmp_path)},
        check=False,
    )
    assert result.returncode == 17
    assert json.loads(output.read_text()) == [
        str(root), ["--prefix", str(root / "web"), "run", "build", "--", "--mode", "test"],
    ]

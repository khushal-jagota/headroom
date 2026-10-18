"""The deploy gate supplies its pinned tool without relying on the runner's PATH."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.deployment_codex import required_version

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "deployment_codex.py"
SOURCE_ROOT = SCRIPT.parents[1]


def fake_codex(path: Path, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{version}'\n")
    path.chmod(0o755)


def fake_environment(tmp_path: Path) -> dict[str, str]:
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    return {**os.environ, "PATH": str(binary_dir), "HOME": str(tmp_path / "home")}


def fake_npm(tmp_path: Path, version: str) -> None:
    npm = tmp_path / "bin" / "npm"
    npm.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(tmp_path / 'npm-args.json')!r})"
        ".write_text(json.dumps(sys.argv[1:]))\n"
        "prefix = pathlib.Path(sys.argv[sys.argv.index('--prefix') + 1])\n"
        "codex = prefix / 'node_modules/.bin/codex'\n"
        "codex.parent.mkdir(parents=True, exist_ok=True)\n"
        f"codex.write_text({f'#!/bin/sh\nprintf \'%s\\n\' \'{version}\'\n'!r})\n"
        "codex.chmod(0o755)\n"
        "print('npm installation output')\n"
    )
    npm.chmod(0o755)


def run_helper(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--source-root", str(SOURCE_ROOT)], env=env,
        capture_output=True, text=True, check=False,
    )


@pytest.mark.parametrize("ambient", [None, "codex-cli 0.1.0"])
def test_setup_provisions_exact_pin_for_missing_or_wrong_ambient(
    tmp_path: Path, ambient: str | None,
) -> None:
    env = fake_environment(tmp_path)
    required = required_version(SOURCE_ROOT)
    if ambient is not None:
        fake_codex(tmp_path / "bin" / "codex", ambient)
    fake_npm(tmp_path, required)
    tools = tmp_path / "job tools"
    result = run_helper(env, "setup", "--tools-dir", str(tools))
    assert result.returncode == 0, result.stderr
    chosen = tools / "node_modules" / ".bin"
    assert result.stdout == f"{chosen}\n"
    assert "npm installation output" in result.stderr
    assert f"actual={ambient or 'absent'!r}" in result.stderr
    assert json.loads((tmp_path / "npm-args.json").read_text()) == [
        "install", "--prefix", str(tools), "--no-audit", "--no-fund",
        f"@openai/codex@{required.removeprefix('codex-cli ')}",
    ]
    verified = run_helper({**env, "PATH": f"{chosen}:{env['PATH']}"}, "verify")
    assert verified.returncode == 0, verified.stderr
    assert f"required={required!r} actual={required!r}" in verified.stderr
    assert f"resolved={str(chosen / 'codex')!r}" in verified.stderr


@pytest.mark.parametrize("use_local", [False, True])
def test_setup_reuses_exact_installed_pin_without_npm(
    tmp_path: Path, use_local: bool,
) -> None:
    env = fake_environment(tmp_path)
    chosen = tmp_path / "bin"
    if use_local:
        fake_codex(chosen / "codex", "codex-cli 0.1.0")
        chosen = Path(env["HOME"]) / ".local" / "bin"
    fake_codex(chosen / "codex", required_version(SOURCE_ROOT))
    tools = tmp_path / "tools"
    result = run_helper(env, "setup", "--tools-dir", str(tools))
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{chosen}\n"
    assert not tools.exists()


@pytest.mark.parametrize("ambient", [None, "codex-cli 0.1.0"])
def test_verify_refuses_missing_or_wrong_binary_without_provisioning(
    tmp_path: Path, ambient: str | None,
) -> None:
    env = fake_environment(tmp_path)
    if ambient is not None:
        fake_codex(tmp_path / "bin" / "codex", ambient)
    result = run_helper(env, "verify")
    assert result.returncode != 0
    assert "PATH does not resolve the requested source's pinned Codex CLI" in result.stderr
    assert f"actual={ambient or 'absent'!r}" in result.stderr
    assert not result.stdout


def test_setup_refuses_incorrect_provisioned_binary(tmp_path: Path) -> None:
    env = fake_environment(tmp_path)
    fake_npm(tmp_path, "codex-cli 0.1.0")
    result = run_helper(env, "setup", "--tools-dir", str(tmp_path / "tools"))
    assert result.returncode != 0
    assert "Provisioned Codex CLI does not match" in result.stderr
    assert not result.stdout


def test_control_helper_reads_pin_from_separate_requested_source(tmp_path: Path) -> None:
    env = fake_environment(tmp_path)
    control_helper = tmp_path / "control" / "scripts" / "deployment_codex.py"
    control_helper.parent.mkdir(parents=True)
    control_helper.write_text(SCRIPT.read_text())
    requested_source = tmp_path / "older-source"
    package = requested_source / "src"
    # This older source supplies its pin but does not contain the new control helper.
    for name in ("planner", "conversation", "backends", "codex_app_server"):
        package = package / name
        package.mkdir(parents=True)
        (package / "__init__.py").touch()
    requested_version = "codex-cli 0.111.222"
    (package / "generate_bindings.py").write_text(
        f"PINNED_CODEX_CLI_VERSION = {requested_version!r}\n"
    )
    fake_codex(tmp_path / "bin" / "codex", requested_version)
    env["PYTHONPATH"] = str(requested_source / "src")
    result = subprocess.run(
        [sys.executable, str(control_helper), "verify", "--source-root", str(requested_source)],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert f"required={requested_version!r} actual={requested_version!r}" in result.stderr
    assert not (requested_source / "scripts" / "deployment_codex.py").exists()

    # A Python environment bound to another source must still fail closed.
    wrong_source = subprocess.run(
        [sys.executable, str(control_helper), "verify", "--source-root", str(SOURCE_ROOT)],
        env=env, capture_output=True, text=True, check=False,
    )
    assert wrong_source.returncode != 0
    assert "Codex pin import does not belong to the requested source" in wrong_source.stderr

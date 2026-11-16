"""Launch checkout development tools and install checkout dependencies."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent
PACKAGES = ("web", "agent_backends", "server")


def npm_command(tree: Path, args: Sequence[str], env: Mapping[str, str]) -> list[str]:
    executable = shutil.which("npm", path=env.get("PATH", os.defpath))
    if not executable:
        raise ValueError("npm is absent from PATH.")
    arguments = list(args)
    if len(arguments) >= 3 and arguments[:2] == ["--prefix", "agent_backends"]:
        operation = arguments[2]
        if operation == "ci":
            raise ValueError("Backend CLIs have no lockfile. Use install.")
        if operation == "install":
            # Backend CLIs track current releases, without a repository lockfile.
            arguments[3:] = [
                "--package-lock=false", "--save=false",
                "@anthropic-ai/claude-code@latest", "@openai/codex@latest",
            ]
    if len(arguments) >= 2 and arguments[0] == "--prefix":
        arguments[1] = str(tree / arguments[1])
    return [executable, *arguments]


def python_command(tree: Path, args: Sequence[str]) -> list[str]:
    command, *tail = args
    name = Path(command).name
    if name in {"python", "python3"}:
        return [str(tree / ".venv/bin/python"), "-I", *tail]
    return [str(tree / ".venv/bin" / name), *tail]


def setup(tree: Path, env: Mapping[str, str]) -> int:
    """Install fresh dependencies in this checkout."""
    destinations = [tree / ".venv", *(tree / item / "node_modules" for item in PACKAGES)]
    for destination in destinations:
        if destination.exists() or destination.is_symlink():
            raise ValueError(f"Setup requires fresh dependencies: {destination}")
    registry_keys = {
        "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_TRUSTED_HOST", "PIP_CERT",
        "PIP_CLIENT_CERT", "PIP_TIMEOUT", "PIP_RETRIES", "PIP_PROXY",
    }
    install_environment = {
        key: value for key, value in env.items()
        if not key.upper().startswith("PIP_") or key in registry_keys
    }
    install_environment["PIP_CONFIG_FILE"] = os.devnull
    bootstrap = shutil.which("python3", path=env.get("PATH", os.defpath))
    if not bootstrap:
        raise ValueError("python3 is absent from PATH.")
    subprocess.run(
        [bootstrap, "-I", "-S", "-B", "-m", "venv", str(tree / ".venv")],
        cwd=tree, env=install_environment, check=True,
    )
    python = str(tree / ".venv/bin/python")
    for arguments in (
        ["-I", "-m", "pip", "--no-cache-dir", "install", "-r", str(tree / "requirements.txt")],
        ["-I", "-m", "pip", "--no-cache-dir", "install", "--editable", str(tree),
         "--config-settings", "editable_mode=compat"],
    ):
        subprocess.run([python, *arguments], cwd=tree, env=install_environment, check=True)
    for package in PACKAGES:
        subprocess.run(
            npm_command(
                tree, ["--prefix", package, "install" if package == "agent_backends" else "ci"],
                env,
            ),
            cwd=tree, env=install_environment, check=True,
        )
    return 0


def main(args: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if args is None else args)
    environment = os.environ.copy()
    try:
        if not arguments:
            raise ValueError("Use setup, npm, pytest, mypy, or python -m pytest|mypy.")
        if arguments == ["setup"]:
            return setup(TREE, environment)
        command = (
            npm_command(TREE, arguments[1:], environment)
            if arguments[0] == "npm"
            else python_command(TREE, arguments)
        )
        is_pytest = arguments[0] != "npm" and (
            Path(arguments[0]).name == "pytest"
            or (len(arguments) > 2 and arguments[1:3] == ["-m", "pytest"])
        )
        if is_pytest:
            sys.path.insert(0, str(TREE / "scripts"))
            from test_runtime import RuntimeIsolation

            with RuntimeIsolation(environment) as runtime:
                return subprocess.run(command, cwd=TREE, env=runtime.environment).returncode
        os.chdir(TREE)
        os.execvpe(command[0], command, environment)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"Development command failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

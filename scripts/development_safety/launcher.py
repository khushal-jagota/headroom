"""Guard supported development entry points before target tool startup."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NoReturn

from development_safety.contracts import Diagnostic, SafetyRefusal
from development_safety.scanner import check_paths, check_python, check_python_lookup, check_tree

TREE = Path(__file__).resolve().parents[2]
PACKAGES = ("web", "agent_backends")
# These do not redirect executable or dependency lookup. Unknown options fail closed.
NPM_CONFIG = {
    "registry",
    "loglevel",
    "progress",
    "audit",
    "fund",
    "color",
    "fetch-retries",
    "fetch-timeout",
    "strict-ssl",
    "cafile",
    "always-auth",
}


def refuse(path: Path, signal: str, detail: str) -> NoReturn:
    raise SafetyRefusal(
        Diagnostic("cannot establish dependency safety", path, None, None, signal, detail)
    )


def node_environment(tree: Path, package: Path, env: Mapping[str, str]) -> None:
    for name in ("NODE_PATH", "NODE_OPTIONS"):
        if env.get(name):
            refuse(
                package,
                "unsupported lookup redirect",
                f"{name} changes executable or module lookup.",
            )
    selected_npm = shutil.which("npm", path=env.get("PATH", os.defpath))
    if selected_npm and len(Path(selected_npm).resolve().parents) < 5:
        refuse(
            Path(selected_npm),
            "npm configuration location",
            "Cannot establish npm installation prefix from executable location.",
        )
    npm_prefix = Path(selected_npm).resolve().parents[4] if selected_npm else Path("/usr")
    expected_paths = {
        "prefix": package,
        "local-prefix": package,
        "global-prefix": package,
        "userconfig": Path.home() / ".npmrc",
        "globalconfig": package / "etc/npmrc",
        "cache": Path.home() / ".npm",
        "init-module": Path.home() / ".npm-init.js",
    }
    for name in env:
        if name.lower().startswith("npm_config_"):
            key = name[len("npm_config_") :].lower().replace("_", "-")
            if key in expected_paths:
                allowed = {expected_paths[key].absolute()}
                if key in {"prefix", "global-prefix"}:
                    allowed.add(npm_prefix)
                if key == "globalconfig":
                    allowed.add(npm_prefix / "etc/npmrc")
                if Path(env[name]).absolute() not in allowed:
                    refuse(package, "unsupported npm configuration", f"Unexpected {name} path.")
                check_tree(Path(env[name]), environment=env)
                continue
            if key == "node-gyp":
                npm = shutil.which("npm", path=env.get("PATH", os.defpath))
                if not npm:
                    refuse(package, "executable lookup", "npm is absent from PATH.")
                expected = Path(npm).resolve().parents[1] / "node_modules/node-gyp/bin/node-gyp.js"
                if Path(env[name]) != expected:
                    refuse(
                        package, "unsupported npm configuration", "Unexpected npm node-gyp path."
                    )
                check_paths([expected], environment=env)
                continue
            if key in {"user-agent", "npm-version", "noproxy"}:
                continue
            if key not in NPM_CONFIG and not key.endswith(":_authtoken"):
                refuse(
                    package,
                    "unsupported npm configuration",
                    f"Unsupported npm environment key: {name}",
                )
    # npm reads user, global, and project configuration before dependency resolution.
    configs = {
        Path.home() / ".npmrc",
        Path("/etc/npmrc"),
        Path("/usr/etc/npmrc"),
        Path("/usr/local/etc/npmrc"),
        tree / ".npmrc",
        package / ".npmrc",
        package / "etc/npmrc",
        npm_prefix / "etc/npmrc",
        *((Path(selected_npm).resolve().parents[1] / "npmrc",) if selected_npm else ()),
    }
    check_paths(list(configs), environment=env)
    for config in configs:
        if not config.exists():
            continue
        try:
            lines = config.read_text().splitlines()
        except (OSError, UnicodeError) as exc:
            refuse(config, "npm configuration read", str(exc))
        for line in lines:
            line = line.strip()
            if not line or line.startswith(("#", ";")):
                continue
            key, separator, _ = line.partition("=")
            key = key.strip().lower()
            if not separator or (key not in NPM_CONFIG and not key.endswith(":_authtoken")):
                refuse(config, "unsupported npm configuration", f"Unsupported config key: {key}")


def node_safety(tree: Path, package: str, env: Mapping[str, str]) -> Path:
    if package not in PACKAGES:
        refuse(tree, "npm command grammar", "Prefix must be web or agent_backends.")
    destination = tree / package
    node_environment(tree, destination, env)
    check_tree(tree, environment=env)
    check_tree(destination, environment=env)
    lookups = [base / "node_modules" for base in (destination, *destination.parents)]
    node = shutil.which("node", path=env.get("PATH", os.defpath))
    if not node:
        refuse(destination, "executable lookup", "node is absent from PATH.")
    check_paths([*lookups, Path(node)], environment=env)
    return destination


def npm_command(tree: Path, args: Sequence[str], env: Mapping[str, str]) -> list[str]:
    if len(args) < 3 or args[0] != "--prefix":
        refuse(
            tree,
            "npm command grammar",
            "Use npm --prefix web|agent_backends ci|install|run SCRIPT.",
        )
    package, operation, *tail = args[1:]
    destination = node_safety(tree, package, env)
    if operation in {"ci", "install"}:
        if tail:
            refuse(
                destination,
                "npm command grammar",
                "Install and ci accept no extra options or packages.",
            )
    elif operation == "run" and len(tail) >= 1:
        try:
            scripts = json.loads((destination / "package.json").read_text()).get("scripts", {})
        except (OSError, ValueError) as exc:
            refuse(destination, "npm script lookup", str(exc))
        if tail[0] not in scripts or (len(tail) > 1 and tail[1] != "--"):
            refuse(
                destination,
                "npm command grammar",
                "Use a registered script followed by optional -- arguments.",
            )
    else:
        refuse(
            destination,
            "npm command grammar",
            "Only ci, install, and registered scripts are supported.",
        )
    executable = shutil.which("npm", path=env.get("PATH", os.defpath))
    if not executable:
        refuse(destination, "executable lookup", "npm is absent from PATH.")
    node = shutil.which("node", path=env.get("PATH", os.defpath))
    if not node:
        refuse(destination, "executable lookup", "node is absent from PATH.")
    check_paths([Path(executable), Path(node)], environment=env)
    return [str(executable), "--prefix", str(destination), operation, *tail]


def python_command(tree: Path, args: Sequence[str], env: Mapping[str, str]) -> list[str]:
    venv = tree / ".venv"
    check_python(venv, tree, environment=env)
    command, *tail = args
    name = Path(command).name
    if command not in {name, f".venv/bin/{name}", str(venv / "bin" / name)}:
        refuse(tree, "Python command grammar", "Use this worktree's Python, pytest, or mypy.")
    if name in {"python", "python3"}:
        if len(tail) < 2 or tail[0] != "-m" or tail[1] not in {"pytest", "mypy"}:
            refuse(venv, "Python command grammar", "Python supports only -m pytest and -m mypy.")
        return [str(venv / "bin/python"), "-I", *tail]
    if name not in {"pytest", "mypy"}:
        refuse(venv, "Python command grammar", "Only pytest and mypy are supported.")
    from tree_environment import read_launcher_interpreter

    console = venv / "bin" / name
    interpreter = read_launcher_interpreter(console)
    if interpreter is None:
        refuse(
            console,
            "console interpreter evidence",
            "Cannot establish console launcher interpreter.",
        )
    check_paths([console, interpreter], environment=env)
    if interpreter.parent != (venv / "bin").resolve():
        refuse(
            console,
            "console interpreter identity",
            f"Console launcher names {interpreter}, outside this worktree virtualenv.",
        )
    return [str(console), *tail]


def setup(tree: Path, env: Mapping[str, str]) -> int:
    """Create fresh local dependencies; never remove or reuse a suspect destination."""
    check_python_lookup(tree, environment=env)
    destinations = [tree / ".venv", *(tree / item / "node_modules" for item in PACKAGES)]
    check_tree(tree, environment=env)
    check_paths(destinations, environment=env)
    for destination in destinations:
        if destination.exists() or destination.is_symlink():
            refuse(
                destination,
                "setup requires fresh dependencies",
                "Use a fresh worktree. Preserve existing dependencies "
                "for deliberate link or mount repair.",
            )
    for package in PACKAGES:
        node_safety(tree, package, env)
    # pip's configuration loader explicitly treats os.devnull as no configuration.
    # Destination switches never reach pip, including ensurepip during venv creation.
    registry_keys = {
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
        "PIP_TRUSTED_HOST",
        "PIP_CERT",
        "PIP_CLIENT_CERT",
        "PIP_TIMEOUT",
        "PIP_RETRIES",
        "PIP_PROXY",
    }
    install_environment = {
        key: value
        for key, value in env.items()
        if not key.upper().startswith("PIP_") or key in registry_keys
    }
    install_environment["PIP_CONFIG_FILE"] = os.devnull
    selected_python = shutil.which("python3", path=env.get("PATH", os.defpath))
    if not selected_python:
        refuse(tree, "executable lookup", "python3 is absent from PATH.")
    bootstrap = Path(selected_python)
    check_paths([bootstrap], environment=env)
    subprocess.run(
        [str(bootstrap), "-I", "-S", "-B", "-m", "venv", str(tree / ".venv")],
        cwd=tree,
        env=install_environment,
        check=True,
    )
    check_python(tree / ".venv", tree, environment=env)
    python = str(tree / ".venv/bin/python")
    for arguments in (
        ["-I", "-m", "pip", "--no-cache-dir", "install", "-r", str(tree / "requirements.txt")],
        [
            "-I",
            "-m",
            "pip",
            "--no-cache-dir",
            "install",
            "--editable",
            str(tree),
            "--config-settings",
            "editable_mode=compat",
        ],
    ):
        check_python(tree / ".venv", tree, environment=env)
        subprocess.run([python, *arguments], cwd=tree, env=install_environment, check=True)
    for package in PACKAGES:
        subprocess.run(
            npm_command(tree, ["--prefix", package, "ci"], env),
            cwd=tree,
            env=install_environment,
            check=True,
        )
    return 0


def main(args: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if args is None else args)
    environment = os.environ.copy()
    try:
        if not arguments:
            refuse(
                TREE, "command grammar", "Use setup, npm, pytest, mypy, or python -m pytest|mypy."
            )
        if arguments == ["setup"]:
            return setup(TREE, environment)
        if arguments[0] == "check-node":
            if len(arguments) != 2:
                refuse(TREE, "command grammar", "check-node requires one package name.")
            node_safety(TREE, arguments[1], environment)
            return 0
        command = (
            npm_command(TREE, arguments[1:], environment)
            if arguments[0] == "npm"
            else python_command(TREE, arguments, environment)
        )
        os.chdir(TREE)
        os.execvpe(command[0], command, environment)
    except SafetyRefusal as exc:
        print(f"Command: {' '.join(arguments)}\n{exc}", file=sys.stderr)
        return 2
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Development command failed: {exc}", file=sys.stderr)
        return 2
    return 0

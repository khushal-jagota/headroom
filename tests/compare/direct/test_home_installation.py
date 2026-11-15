"""Populated first-home orchestration against fresh Python without native service actions.

Reuse the existing Python home installer and cheap archive/service dependencies.
The Node route intentionally omits Python bytecode flags and Python PATH entries.
Actual plist conversion and system service custody remain hosted native acceptance.
"""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tests.support.synthetic_homes import create_synthetic_homes

from planner.installation import home, mac_home
from planner.installation.contracts import HomeInstall

ROOT = Path(__file__).resolve().parents[3]


def _seed(base: Path, system: str) -> Path:
    agents = (
        base
        / "account"
        / ("Library/LaunchAgents" if system == "darwin" else ".config/systemd/user")
    )
    agents.mkdir(parents=True)
    (agents / ("foreign.plist" if system == "darwin" else "foreign.service")).write_text(
        "pre-existing installation"
    )
    skills = base / "configured-codex/skills"
    skills.mkdir(parents=True)
    (skills / "panels").symlink_to(base / "foreign-owner/skills/panels")
    provider = base / "provider/bin"
    provider.mkdir(parents=True)
    for name in ("node", "codex", "claude"):
        executable = provider / name
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o700)
    return agents


def _python(base: Path, system: str, scenario: str, port: int, monkeypatch: Any) -> Any:
    root, account, service = base / "home", base / "account", "com.panels.proof"
    agents = _seed(base, system)
    effects: list[list[str]] = []
    active: set[str] = set()
    jobs: dict[str, str] = {}
    native_node_directory = str(Path(shutil.which("node") or "/usr/bin/node").resolve().parent)

    def normalized_path(value: str) -> str:
        return ":".join(
            entry
            for entry in value.split(":")
            if entry != str(root / "app/.venv/bin")
            and entry != native_node_directory
        )

    def command(arguments: list[str] | tuple[str, ...], **kwargs: Any) -> Any:
        argv = list(arguments)
        output, error, code = "", "", 0
        if argv[0] == "loginctl":
            output = "yes\n"
        elif argv[0] == "systemctl":
            operation = argv[2]
            name = argv[3] if len(argv) > 3 else ""
            if operation == "show":
                output = (
                    "loaded"
                    if scenario == "loaded-conflict" and name == service + ".service"
                    else "not-found"
                )
            elif operation == "is-active":
                output = "active" if name in active else "inactive"
            elif operation != "show-environment":
                effects.append(argv)
                if operation == "enable":
                    active.add(argv[-1])
                    if scenario == "auxiliary-failure" and argv[-1].endswith(".timer"):
                        code = 1
                elif operation == "stop" and scenario != "stop-unconfirmed":
                    active.discard(name)
        elif argv[0] == "/bin/launchctl":
            operation, label = argv[1], argv[-1].rsplit("/", 1)[-1]
            if operation == "print":
                if scenario == "loaded-conflict" and label == service:
                    output = "path = /another/home.plist\n"
                elif label in jobs:
                    output = f"path = {jobs[label]}\n"
                else:
                    code, error = 1, "Could not find service"
            else:
                effects.append(argv)
                if operation == "bootstrap":
                    path = Path(argv[-1])
                    jobs[path.stem] = str(path)
                    if scenario == "auxiliary-failure" and path.stem.endswith(".backup"):
                        code = 1
                elif operation == "bootout" and scenario != "stop-unconfirmed":
                    jobs.pop(label)
        elif argv[0] != str(Path(sys.executable).resolve()):
            raise AssertionError(f"unadmitted reference command {argv}")
        if code and kwargs.get("check"):
            raise subprocess.CalledProcessError(code, argv, stderr="scripted command refusal")
        return subprocess.CompletedProcess(argv, code, output, error)

    def extract(_archive: Any, destination: Path, _digest: str, _revision: str) -> Any:
        (destination / "bin").mkdir(parents=True)
        (destination / "bin/panels").write_text("sealed application")
        (destination / "src/planner/skills/panels").mkdir(parents=True)
        (destination / "src/planner/skills/panels/SKILL.md").write_text("Panels")
        return SimpleNamespace(
            runtime_contract=SimpleNamespace(
                version=2, system="darwin", architecture="arm64", role="home"
            )
        )

    def health(_port: int, _revision: str) -> str:
        effects.append(["health"])
        if scenario in {"health-failure", "stop-unconfirmed"}:
            raise RuntimeError("home health refused")
        return f"http://127.0.0.1:{port}"

    with monkeypatch.context() as patch:
        patch.setenv("HOME", str(account))
        patch.setenv("PATH", str(base / "provider/bin") + ":/build-icu/bin:/usr/bin:/bin")
        for name, folder in (
            ("CODEX_HOME", "configured-codex"),
            ("CLAUDE_CONFIG_DIR", "configured-claude"),
            ("PLAN_HERMES_HOME", "configured-hermes"),
        ):
            patch.setenv(name, str(base / folder))
        patch.setenv("PLAN_CLAUDE_EXECUTABLE", "/custom/claude")
        patch.setattr(
            "planner.installation.home.platform.system",
            lambda: "Darwin" if system == "darwin" else "Linux",
        )
        patch.setattr("planner.installation.home.subprocess.run", command)
        patch.setattr(home, "extract_app_archive", extract)
        patch.setattr(mac_home, "extract_app_archive", extract)
        patch.setattr(home, "admit_runtime", lambda *_: None)
        patch.setattr("planner.environments.runtime_admission.admit_runtime", lambda *_: None)
        patch.setattr(home, "_wait_health", health)
        outcome = "ok"
        try:
            home.install_home(
                HomeInstall(root, service, port, Path(sys.executable), scenario != "configured"),
                base / "archive",
                "a" * 64,
                "b" * 40,
            )
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            outcome = "refused"
    config = json.loads((root / "data/config.yaml").read_text()) if root.exists() else None
    generated: Any = None
    if config is not None:
        if system == "darwin":
            generated = {}
            for path in sorted((root / "agents").iterdir()):
                job = plistlib.loads(path.read_bytes())
                job["EnvironmentVariables"] = {
                    key: normalized_path(value) if key == "PATH" else value
                    for key, value in job["EnvironmentVariables"].items()
                    if key != "PYTHONDONTWRITEBYTECODE"
                }
                generated[path.name] = job
        else:
            generated = {
                path.name: "\n".join(
                    sorted(
                        "Environment="
                        + json.dumps(
                            "PATH="
                            + normalized_path(
                                json.loads(line[len("Environment=") :])[len("PATH=") :]
                            )
                        )
                        if line.startswith('Environment="PATH=')
                        else line
                        for line in path.read_text().split("\n")
                        if not line.startswith('Environment="PYTHONDONTWRITEBYTECODE=')
                    )
                )
                for path in sorted(agents.iterdir())
                if path.name != "foreign.service"
            }
    identity = None
    identity_path = root / "data/mac-home-installation.json"
    if identity_path.exists():
        legacy = json.loads(identity_path.read_text())
        assert set(legacy) == {
            "uid", "root", "label", "home_plist", "backup_plist", "installer_python"
        }
        assert isinstance(legacy["installer_python"], str)
        identity = {
            key: legacy[key] for key in ("uid", "root", "label", "home_plist", "backup_plist")
        }
    result = dict(
        outcome=outcome,
        root_exists=root.exists(),
        config=config,
        application=(root / "app/bin/panels").read_text() if root.exists() else None,
        generated=generated,
        identity=identity,
        owned_files=sorted(
            path.name for path in agents.iterdir() if not path.name.startswith("foreign.")
        ),
        active=sorted(jobs if system == "darwin" else active),
        effects=effects,
        foreign=(
            agents / ("foreign.plist" if system == "darwin" else "foreign.service")
        ).read_text(),
        foreign_skill=os.readlink(base / "configured-codex/skills/panels"),
    )
    return json.loads(json.dumps(result).replace(str(base), "<root>"))


def test_first_home_installation_parity(tmp_path: Path, monkeypatch: Any, request: Any) -> None:
    started = time.perf_counter()
    cases, expected = [], []
    for system in ("linux", "darwin"):
        for scenario in (
            "success",
            "configured",
            "health-failure",
            "auxiliary-failure",
            "stop-unconfirmed",
            "loaded-conflict",
        ):
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            name = system + "-" + scenario
            expected.append(_python(tmp_path / "py" / name, system, scenario, port, monkeypatch))
            base = tmp_path / "ts" / name
            _seed(base, system)
            cases.append(dict(root=str(base), system=system, scenario=scenario, port=port))
    node = shutil.which("node")
    assert node
    environment = {
        "PATH": str(Path(node).parent) + os.pathsep + os.defpath,
        **create_synthetic_homes(tmp_path),
    }
    result = subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            "import {home_install_comparison} from './server/installation/testing/compare.ts';"
            "const result=await home_install_comparison(JSON.parse(process.argv[1]));"
            "console.log(JSON.stringify(result));",
            json.dumps(cases),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual == expected
    elapsed = time.perf_counter() - started
    request.node.user_properties.append(("elapsed_seconds", round(elapsed, 3)))
    report = request.config.getoption("--compare-report-dir")
    if report:
        destination = Path(report)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "first-home-installation.json").write_text(
            json.dumps(
                dict(
                    equal=True,
                    elapsed_seconds=elapsed,
                    observations=expected,
                    native_boundary=(
                        "Archive admission, service control, health, and plutil are recording "
                        "dependencies. No real service or candidate executes."
                    ),
                    intentional_difference=(
                        "Node releases omit Python bytecode flags and .venv/bin PATH entries. "
                        "Each host uses its selected canonical Node directory. Only that directory "
                        "is normalized from both platform PATH lists. Remaining ordered entries, "
                        "including Homebrew before custom provider directories, compare exactly."
                        " Native identity writes exact version-2 fields with canonical external "
                        "installer_node. Python writes its exact legacy six-key identity. "
                        "Both records compare every common binding."
                        " Python initializes its fresh database on first server startup. "
                        "Native installation initializes before service activation. "
                        "Only that initialization timing is projected from service effects."
                    ),
                ),
                indent=2,
            )
        )

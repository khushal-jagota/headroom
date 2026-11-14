"""Native Mac home runtime admission and executable proof."""

from __future__ import annotations

import platform
import shutil
from pathlib import Path
from re import fullmatch

from planner.environments.app import AppValidationError
from planner.platform_runtime.contracts import RuntimeRequirements
from planner.platform_runtime.darwin.runtime_contracts import DarwinRuntimeContract
from planner.platform_runtime.targets import MAC_HOME


class DarwinRuntime:
    target = MAC_HOME

    def native_probe(self) -> str:
        from .runtime_probe import NATIVE_PROBE

        return NATIVE_PROBE

    def parse_contract(self, value: object) -> DarwinRuntimeContract:
        return DarwinRuntimeContract.from_dict(value)

    def complete_private_runtime(self, app_root: Path) -> DarwinRuntimeContract:
        from planner.environments.runtime_contracts import runtime_bytes_digest

        inventory = app_root / "runtime/inventory.json"
        from planner.environments.mac_runtime_configuration import MAC_PYTHON_VERSION

        return DarwinRuntimeContract(
            MAC_PYTHON_VERSION, runtime_bytes_digest(inventory.read_bytes())
        )

    def verify_host(self, contract: RuntimeRequirements, *, state: Path) -> None:
        from planner.environments.runtime_admission import _run_probe

        if not isinstance(contract, DarwinRuntimeContract):
            raise AppValidationError("runtime requires a darwin arm64 home contract")
        if platform.system().lower() != "darwin" or platform.machine() != "arm64":
            raise AppValidationError("runtime requires macOS arm64")
        try:
            if tuple(map(int, platform.mac_ver()[0].split("."))) < tuple(
                map(int, contract.minimum_os.split("."))
            ):
                raise ValueError("Mac is older than the runtime minimum OS")
        except ValueError as exc:
            raise AppValidationError(str(exc)) from exc
        executable = shutil.which("node")
        if executable is None:
            raise AppValidationError("runtime host prerequisite is missing: Node 22 or newer")
        node = _run_probe([str(Path(executable).resolve()), "--version"], state=state)
        if (
            (match := fullmatch(r"v(\d+)\.\d+\.\d+", node.strip())) is None
            or int(match[1]) < contract.node_major
        ):
            raise AppValidationError(f"runtime requires Node {contract.node_major} or newer")

    def native_tool_commands(
        self, root: Path, contract: RuntimeRequirements, *, node: str = "node"
    ) -> list[list[str]]:
        modules = root / "agent_backends/node_modules"
        executable = shutil.which("node")
        if executable is None:
            raise AppValidationError("runtime host prerequisite is missing: Node 22 or newer")
        return [
            [
                str(Path(executable).resolve()),
                str(modules / "@openai/codex/bin/codex.js"),
                "--version",
            ],
            [str(modules / "@anthropic-ai/claude-code/bin/claude.exe"), "--version"],
        ]

"""Linux native host admission, tool selection, and runtime packaging."""

from __future__ import annotations

import ctypes
import platform
import shutil
from collections.abc import Callable
from pathlib import Path
from re import fullmatch

from planner.environments.app import AppValidationError
from planner.platform_runtime.contracts import RuntimeRequirements
from planner.platform_runtime.linux.runtime_contracts import (
    AnyRuntimeContract,
    NodeRuntimeContract,
    RuntimeContract,
    RuntimeContractError,
    parse_optional_runtime_contract,
)
from planner.platform_runtime.targets import LINUX_HOME


def verify_runtime_host(
    contract: AnyRuntimeContract, *, state: Path, run_probe: Callable[..., str]
) -> None:
    if platform.system().lower() != contract.system or platform.machine() != contract.architecture:
        raise AppValidationError("runtime requires Linux x86_64")
    try:
        release = platform.freedesktop_os_release()
        supported = next(
            item
            for item in contract.platforms
            if (item.distribution, item.version) == (release.get("ID"), release.get("VERSION_ID"))
        )
        libc, version = platform.libc_ver()
        if libc != "glibc" or tuple(map(int, version.split("."))) < tuple(
            map(int, supported.minimum_glibc.split("."))
        ):
            raise ValueError("unsupported glibc version")
        feature_sets = [
            set(line.split(":", 1)[1].split())
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("flags")
        ]
        if not feature_sets or any(
            not set(contract.cpu_features) <= features for features in feature_sets
        ):
            raise ValueError("required CPU features are unavailable")
    except (OSError, ValueError, StopIteration) as exc:
        raise AppValidationError(f"unsupported runtime host: {exc}") from exc
    for library in contract.external_libraries:
        try:
            ctypes.CDLL(library)
        except OSError as exc:
            raise AppValidationError(f"runtime host library is missing: {library}") from exc
    for executable in contract.external_executables:
        if shutil.which(executable, path="/usr/local/bin:/usr/bin:/bin") is None:
            raise AppValidationError(f"runtime host prerequisite is missing: {executable}")
    node = run_probe(["/usr/bin/env", "node", "--version"], state=state)
    if node.split(".")[0] != f"v{contract.node_major}":
        raise AppValidationError(f"runtime requires Node {contract.node_major}")
    if isinstance(contract, NodeRuntimeContract):
        match = fullmatch(r"v(\d+)\.(\d+)\.(\d+)", node.strip())
        if match is None:
            raise AppValidationError(f"unreadable Node version: {node[:40]}")
        found = tuple(int(part) for part in match.groups())
        minimum = tuple(int(part) for part in contract.minimum_node_version.split("."))
        if found < minimum:
            raise AppValidationError(
                f"runtime requires Node {contract.minimum_node_version} or a later Node "
                f"{contract.node_major}"
            )


def native_tool_commands(
    root: Path, contract: RuntimeRequirements, *, node: str = "node"
) -> list[list[str]]:
    """Offline startup probes for selected Linux packages, never agent sessions."""
    modules = root / "agent_backends/node_modules"
    site = root / ".venv/lib/python3.12/site-packages"
    vendor = modules / "@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl"
    commands = [
        [node, str(modules / "@anthropic-ai/claude-code/cli-wrapper.cjs"), "--version"],
        [node, str(modules / "@openai/codex/bin/codex.js"), "--version"],
    ]
    runtime_binaries = (
        (root / "server/node_modules/@anthropic-ai/claude-agent-sdk-linux-x64/claude",)
        if isinstance(contract, NodeRuntimeContract)
        else (site / "claude_agent_sdk/_bundled/claude", site / "playwright/driver/node")
    )
    for path in (
        modules / "@anthropic-ai/claude-code-linux-x64/claude",
        modules / "@anthropic-ai/claude-code/bin/claude.exe",
        *runtime_binaries,
        vendor / "bin/codex",
        vendor / "codex-resources/bwrap",
        vendor / "codex-path/rg",
    ):
        commands.append([str(path), "--version"])
    commands.append([str(vendor / "bin/codex-code-mode-host"), "--help"])
    return commands



class LinuxRuntime:
    target = LINUX_HOME

    def complete_private_runtime(self, app_root: Path) -> RuntimeContract:
        from planner.platform_runtime.linux.runtime_packaging import complete_private_runtime

        return complete_private_runtime(app_root)

    def parse_contract(self, value: object) -> AnyRuntimeContract:
        contract = parse_optional_runtime_contract(value)
        if contract is None:
            raise RuntimeContractError("runtime contract must be an object")
        return contract

    def verify_host(self, contract: RuntimeRequirements, *, state: Path) -> None:
        from planner.environments.runtime_admission import _run_probe

        if not isinstance(contract, (RuntimeContract, NodeRuntimeContract)):
            raise RuntimeContractError("unsupported runtime operating system or architecture")
        verify_runtime_host(contract, state=state, run_probe=_run_probe)

    def native_tool_commands(
        self, root: Path, contract: RuntimeRequirements, *, node: str = "node"
    ) -> list[list[str]]:
        return native_tool_commands(root, contract, node=node)

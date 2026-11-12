"""Linux native host admission, tool selection, and runtime packaging."""

from __future__ import annotations

import ctypes
import platform
import shutil
from collections.abc import Callable
from pathlib import Path

from planner.environments.app import AppValidationError
from planner.environments.runtime_contracts import RuntimeContract
from planner.platform_runtime.targets import LINUX_HOME


def verify_runtime_host(
    contract: RuntimeContract, *, state: Path, run_probe: Callable[..., str]
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


def native_tool_commands(root: Path, contract: RuntimeContract) -> list[list[str]]:
    """Offline startup probes for selected Linux packages, never agent sessions."""
    modules = root / "agent_backends/node_modules"
    site = root / ".venv/lib/python3.12/site-packages"
    vendor = modules / "@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl"
    commands = [
        ["node", str(modules / "@anthropic-ai/claude-code/cli-wrapper.cjs"), "--version"],
        ["node", str(modules / "@openai/codex/bin/codex.js"), "--version"],
    ]
    for path in (
        modules / "@anthropic-ai/claude-code-linux-x64/claude",
        modules / "@anthropic-ai/claude-code/bin/claude.exe",
        site / "claude_agent_sdk/_bundled/claude",
        site / "playwright/driver/node",
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

    def verify_host(self, contract: RuntimeContract, *, state: Path) -> None:
        from planner.environments.runtime_admission import _run_probe

        verify_runtime_host(contract, state=state, run_probe=_run_probe)

    def native_tool_commands(self, root: Path, contract: RuntimeContract) -> list[list[str]]:
        return native_tool_commands(root, contract)

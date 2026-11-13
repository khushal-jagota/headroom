"""One bounded runtime admission before any deployment service action."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from planner.environments.app import AppManifest, AppValidationError
from planner.platform_runtime.contracts import RuntimeRequirements
from planner.platform_runtime.linux.runtime_probe import NATIVE_PROBE as NATIVE_PROBE
from planner.platform_runtime.selection import select_home_runtime
from planner.platform_runtime.targets import RuntimeTarget

PROBE_TIMEOUT_SECONDS = 45
ADMISSION_TIMEOUT_SECONDS = 120


def probe_environment(state: Path) -> dict[str, str]:
    """Allowlist instead of inheriting credentials, homes, or loader overrides."""
    host_path = "/usr/local/bin:/usr/bin:/bin"
    if sys.platform == "darwin":
        node = shutil.which("node")
        node_parent = str(Path(node).resolve().parent) if node is not None else "/opt/homebrew/bin"
        host_path = f"{node_parent}:/opt/homebrew/bin:{host_path}"
    return {
        "PATH": host_path,
        "HOME": str(state),
        "TMPDIR": str(state),
        "XDG_CONFIG_HOME": str(state / "config"),
        "XDG_CACHE_HOME": str(state / "cache"),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PLAN_DISPATCH_ENABLED": "false",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        "CODEX_HOME": str(state / "codex"),
        "CLAUDE_CONFIG_DIR": str(state / "claude"),
    }


def _run_probe(command: list[str], *, state: Path, timeout: float = PROBE_TIMEOUT_SECONDS) -> str:
    if timeout <= 0:
        raise AppValidationError("runtime admission exceeded its time budget")
    try:
        with tempfile.TemporaryFile(mode="w+") as output:
            with subprocess.Popen(
                command,
                cwd=state,
                env=probe_environment(state),
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                text=True,
                start_new_session=True,
            ) as process:
                try:
                    code = process.wait(timeout=min(PROBE_TIMEOUT_SECONDS, timeout))
                except BaseException:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise
            output.seek(0, os.SEEK_END)
            output.seek(max(0, output.tell() - 2000))
            detail = output.read()
            if code:
                raise AppValidationError(f"runtime probe failed: {detail}")
            return detail.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise AppValidationError(f"runtime probe failed: {exc}") from exc


def verify_runtime_host(contract: RuntimeRequirements, *, state: Path) -> None:
    runtime = select_home_runtime(RuntimeTarget(contract.system, contract.architecture, "home"))
    runtime.verify_host(contract, state=state)


def native_tool_commands(root: Path, contract: RuntimeRequirements) -> list[list[str]]:
    target = RuntimeTarget(contract.system, contract.architecture, "home")
    return select_home_runtime(target).native_tool_commands(root, contract)


def admit_runtime(root: Path, manifest: AppManifest) -> None:
    """Reject legacy candidates, verify the host, then execute the private runtime."""
    contract = manifest.runtime_contract
    if contract is None:
        raise AppValidationError("candidate lacks a runtime contract; republish the app")
    runtime = select_home_runtime(RuntimeTarget(contract.system, contract.architecture, "home"))
    deadline = time.monotonic() + ADMISSION_TIMEOUT_SECONDS
    with tempfile.TemporaryDirectory(prefix="panels-runtime-probe-") as temporary:
        state = Path(temporary)
        verify_runtime_host(contract, state=state)
        _run_probe(
            [
                str(root.resolve() / ".venv/bin/python"),
                "-I",
                "-B",
                "-c",
                runtime.native_probe(),
                str(root.resolve()),
                contract.python_version,
                json.dumps(contract.external_libraries),
            ],
            state=state,
            timeout=deadline - time.monotonic(),
        )
        for command in native_tool_commands(root.resolve(), contract):
            _run_probe(command, state=state, timeout=deadline - time.monotonic())

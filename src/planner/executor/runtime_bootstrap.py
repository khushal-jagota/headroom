"""Permanent, standard-library-only supervisor behind the fixed signed launcher.

This module and its Python interpreter remain in the original installation.
Versioned runtimes are children, never replacements for this supervisor.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from planner.executor.update_configuration import (
    MAC_RUNTIME_CHILD_ENV,
    MAC_RUNTIME_HANDOFF_ENV,
    MAC_RUNTIME_MANIFEST_NAME,
    MAC_RUNTIME_READY_ENV,
    MAC_RUNTIME_READY_TIMEOUT_SECONDS,
    MAC_RUNTIME_UPDATE_EXIT_CODE,
    MAC_UPDATE_POLL_SECONDS,
    MAC_UPDATE_TERMINATE_SECONDS,
)


def atomic_record(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}")
    try:
        with temporary.open("x") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def read_record(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    if not isinstance(value, dict):
        raise ValueError("runtime state must be an object")
    return value


def update_root() -> Path:
    executor = os.environ.get("PLAN_EXECUTOR_ROOT")
    root = Path(executor).expanduser() if executor else (
        Path.home() / "Library/Application Support/Panels/executor"
    )
    return root.resolve() / "updates"


def _runtime_revision(runtime: Path, expected: str) -> None:
    value = read_record(runtime / MAC_RUNTIME_MANIFEST_NAME)
    if value is None or (
        value.get("schema_version") != 1
        or value.get("application_revision") != expected
        or value.get("platform") != "darwin"
        or value.get("architecture") != "arm64"
        or re.fullmatch(r"[0-9a-f]{40}", expected) is None
    ):
        raise ValueError("selected runtime differs from the approved release")


def _arguments(arguments: list[str], revision: str) -> list[str]:
    result = list(arguments)
    for index, value in enumerate(result):
        if value == "--application-revision":
            result[index + 1] = revision
            break
        if value.startswith("--application-revision="):
            result[index] = "--application-revision=" + revision
            break
    else:
        raise ValueError("executor runtime needs its explicit application revision")
    return result


def _launch(
    arguments: list[str],
    runtime: str | None,
    revision: str,
    *,
    ready: Path | None = None,
    handoff: Path | None = None,
) -> subprocess.Popen[bytes]:
    environment = dict(os.environ)
    environment[MAC_RUNTIME_CHILD_ENV] = "1"
    environment.pop(MAC_RUNTIME_READY_ENV, None)
    environment.pop(MAC_RUNTIME_HANDOFF_ENV, None)
    python = sys.executable
    if runtime is not None:
        root = Path(runtime)
        _runtime_revision(root, revision)
        python = str(root / "python/bin/python3")
        environment["PATH"] = str(root / "python/bin") + os.pathsep + environment.get("PATH", "")
        environment["PLAN_APP_ROOT"] = str(root)
        environment["PLAN_CLAUDE_EXECUTABLE"] = str(
            root / "agent_backends/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
        )
    if ready is not None:
        environment[MAC_RUNTIME_READY_ENV] = str(ready)
    if handoff is not None:
        environment[MAC_RUNTIME_HANDOFF_ENV] = str(handoff)
    return subprocess.Popen(
        [python, "-I", "-B", "-m", "planner.executor.entrypoint", *_arguments(arguments, revision)],
        env=environment,
        start_new_session=True,
    )


def _signal_child(process: subprocess.Popen[bytes], signum: int) -> None:
    if process.poll() is None:
        try:
            # This process group belongs only to the child launched above.
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass


def _stop_child(process: subprocess.Popen[bytes]) -> None:
    _signal_child(process, signal.SIGTERM)
    try:
        process.wait(timeout=MAC_UPDATE_TERMINATE_SECONDS)
    except subprocess.TimeoutExpired:
        _signal_child(process, signal.SIGKILL)
        process.wait()


def supervise(arguments: list[str]) -> int:
    root = update_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    baseline_revision = next(
        value.partition("=")[2] if "=" in value else arguments[index + 1]
        for index, value in enumerate(arguments)
        if value == "--application-revision" or value.startswith("--application-revision=")
    )
    selected = read_record(root / "selection.json")
    pending = read_record(root / "pending.json")
    custody = read_record(root / "handoff.json")
    handoff: Path | None = (
        root / "handoff.json" if custody and custody.get("replay_pending") is True else None
    )
    if pending is not None:
        # The permanent supervisor did not confirm this attempt before it ended.
        if selected is None or selected.get("request_id") != pending["request_id"]:
            atomic_record(root / "failure.json", {
                "request_id": pending["request_id"], "phase": "reverted",
                "detail": "The runtime supervisor restarted before confirmation.",
            })
            handoff = root / "handoff.json"
        (root / "pending.json").unlink()
    runtime = str(selected["runtime_path"]) if selected else None
    revision = str(selected["application_revision"]) if selected else baseline_revision
    process = _launch(arguments, runtime, revision, handoff=handoff)
    stopping = False

    def forward(signum: int, frame: object) -> None:
        nonlocal stopping
        stopping = True
        _signal_child(process, signum)

    previous_signals = {
        signum: signal.signal(signum, forward) for signum in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        while True:
            returncode = process.wait()
            if stopping or returncode != MAC_RUNTIME_UPDATE_EXIT_CODE:
                return returncode if returncode >= 0 else 128 - returncode
            pending = read_record(root / "pending.json")
            if pending is None:
                raise ValueError("runtime requested a swap without a durable selection")
            ready = root / "ready" / f"{uuid4().hex}.json"
            launch_failed = False
            try:
                process = _launch(
                    arguments,
                    str(pending["runtime_path"]),
                    str(pending["application_revision"]),
                    ready=ready,
                    handoff=root / "handoff.json",
                )
            except (OSError, ValueError):
                launch_failed = True
            deadline = time.monotonic() + MAC_RUNTIME_READY_TIMEOUT_SECONDS
            confirmed = False
            while not launch_failed and not stopping and time.monotonic() < deadline:
                try:
                    marker = read_record(ready)
                except (OSError, ValueError):
                    marker = None
                if marker is not None and (
                    marker.get("request_id") == pending["request_id"]
                    and marker.get("application_revision") == pending["application_revision"]
                ):
                    confirmed = True
                    break
                if process.poll() is not None:
                    break
                time.sleep(min(MAC_UPDATE_POLL_SECONDS, max(0, deadline - time.monotonic())))
            ready.unlink(missing_ok=True)
            if confirmed:
                atomic_record(root / "selection.json", pending)
                selected = pending
                (root / "pending.json").unlink()
                continue
            _stop_child(process)
            atomic_record(root / "failure.json", {
                "request_id": pending["request_id"], "phase": "reverted",
                "detail": "The new runtime did not confirm its VPS connection before the deadline.",
            })
            (root / "pending.json").unlink()
            if stopping:
                return 128 + signal.SIGTERM
            # Restore the last confirmed runtime. Its data directory and journal
            # never moved, and generation allocation continues the planned handoff.
            process = _launch(
                arguments,
                str(selected["runtime_path"]) if selected else None,
                str(selected["application_revision"]) if selected else baseline_revision,
                handoff=root / "handoff.json",
            )
    finally:
        for signum, previous in previous_signals.items():
            signal.signal(signum, previous)
        if process.poll() is None:
            _stop_child(process)


def main(arguments: list[str]) -> None:
    raise SystemExit(supervise(arguments))

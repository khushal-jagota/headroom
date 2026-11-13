"""Shared synthetic starts, process custody and evidence for the Mac direct comparisons."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]


def start_inputs(root: Path) -> list[dict[str, Any]]:
    values = []
    for backend, policy, workspace in (
        ("codex", "executor_default", "/vps/workspace"),
        ("claude", "home_relative", "/vps/project"),
        ("hermes", "absolute", str(root / "absolute")),
    ):
        identity = [
            ["PLAN_ACTOR", "worker"],
            ["PLAN_TICKET_ID", "ticket-" + backend],
            ["KEPT_IDENTITY", "retained"],
            *[
                [key, "/vps/" + key]
                for key in (
                    "HOME",
                    "USERPROFILE",
                    "CODEX_HOME",
                    "HERMES_HOME",
                    "CLAUDE_CONFIG_DIR",
                    "PLAN_EXECUTOR_ROOT",
                    "PLAN_EXECUTOR_CHILD_ID",
                    "PLAN_EXECUTOR_CAPABILITY",
                    "PLAN_TICKET_FILES_DIR",
                    "PLAN_NATIVE_SKILLS_DIR",
                )
            ],
        ]
        values.append(
            dict(
                resolved_start=dict(
                    conversation_id="conversation-" + backend,
                    backend_key=backend,
                    model=backend + "-model",
                    reasoning_effort="high",
                    access="full",
                    workspace_folder=workspace,
                    workspace_path_policy=policy,
                    role_materials=dict(
                        role_text="Canonical Worker role", identity_environment_variables=identity
                    ),
                ),
                canonical_skill_snapshot=dict(sources=[], required_skill_names=[]),
                vendor_session_cursor="resume-" + backend,
                workspace_home_relative="project" if policy != "absolute" else None,
            )
        )
    return values


def run_node(entry: str, root: Path, homes: Mapping[str, str], *, timeout: int) -> dict[str, Any]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PLAN_") and key not in homes
    }
    environment.update(homes)
    environment.update(PLAN_TEST_MODE="1", PLAN_SERVER_URL="http://127.0.0.1:9")
    process = subprocess.Popen(
        ["node", entry, str(root)],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.communicate(timeout=5)
        raise
    assert process.returncode == 0, stdout + stderr
    result = json.loads(stdout)
    assert isinstance(result, dict), "Node comparison did not produce an object"
    return result


def report_direct(
    request: pytest.FixtureRequest,
    name: str,
    observed: dict[str, Any],
    started: float,
    boundary: str,
    **evidence: Any,
) -> None:
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(
            json.dumps(
                dict(
                    kind="direct",
                    equal=True,
                    **{key: value for key, value in observed.items() if key != "elapsed_seconds"},
                    node_elapsed_seconds=observed["elapsed_seconds"],
                    elapsed_seconds=time.perf_counter() - started,
                    boundary=boundary,
                    **evidence,
                ),
                indent=2,
            )
        )

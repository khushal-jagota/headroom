"""Cold process boundaries must work without pytest warming conversation imports."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


def cold_python(program: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-B", "-c", program],
        env={
            key: value
            for key, value in os.environ.items()
            if not key.startswith("PLAN_") and key != "PYTHONPATH"
        },
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize(
    "module",
    [
        "planner.executor.protocol",
        "planner.executor.mac_outbox",
        "planner.conversation.model_resolution.contracts",
        "planner.executor.mac_runner",
        "planner.executor.mac_service",
        "planner.executor.vps_transport",
        "planner.executor.vps_agent_writes",
        "planner.executor.cli",
        "planner.cli.main",
    ],
)
def test_ordinary_entrypoint_imports_in_its_own_cold_process(module: str) -> None:
    result = cold_python(f"import importlib; importlib.import_module({module!r})")
    assert result.returncode == 0, result.stderr


def test_cold_catalog_context_retains_runtime_executor_identity_validation() -> None:
    result = cold_python("""
from pathlib import Path
from pydantic import TypeAdapter, ValidationError
from planner.conversation.model_resolution.contracts import (
    CatalogExpectedContext, CatalogWorkspaceBinding,
)
from planner.conversation.contracts import WorkspacePathPolicy
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ExecutorIdentity

adapter = TypeAdapter(CatalogExpectedContext)
identity = ExecutorIdentity(PROTOCOL_VERSION, "fixture-revision", SPOOL_FORMAT_VERSION, "native", 1)
workspace = CatalogWorkspaceBinding(Path("/workspace"), WorkspacePathPolicy.native_absolute, None)
context = adapter.validate_python({"executor_identity": identity, "workspace": workspace})
assert type(context) is CatalogExpectedContext
assert context.executor_identity is identity
wire_identity = {
    "protocol_version": PROTOCOL_VERSION,
    "application_revision": "fixture-revision",
    "spool_format_version": SPOOL_FORMAT_VERSION,
    "executor_incarnation": "native",
    "connection_generation": 1,
}
wire_workspace = {
    "workspace_folder": "/workspace",
    "workspace_path_policy": WorkspacePathPolicy.native_absolute.value,
    "workspace_home_relative": None,
}
context = adapter.validate_python({"executor_identity": wire_identity, "workspace": wire_workspace})
assert type(context.executor_identity) is ExecutorIdentity
assert context.executor_identity == identity
assert context.workspace == workspace
wire_identity["connection_generation"] = "malformed"
try:
    adapter.validate_python({"executor_identity": wire_identity, "workspace": wire_workspace})
except ValidationError as error:
    locations = [item["loc"] for item in error.errors()]
    assert ("executor_identity", "connection_generation") in locations
else:
    raise AssertionError("malformed identity passed runtime validation")
""")
    assert result.returncode == 0, result.stderr

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
        "planner.executor.runtime",
        "planner.executor.mac",
        "planner.executor.vps",
        "planner.executor.native_messages",
        "planner.executor.cli",
        "planner.cli.main",
    ],
)
def test_ordinary_entrypoint_imports_in_its_own_cold_process(module: str) -> None:
    result = cold_python(f"import importlib; importlib.import_module({module!r})")
    assert result.returncode == 0, result.stderr


def test_public_conversation_exports_keep_canonical_objects_after_cold_contract_import() -> None:
    result = cold_python("""
import sys
import planner.conversation as conversation
from planner.conversation import contracts
from planner.conversation.logic.conversation_start_resolution import (
    resolve_conversation_start_request,
)

assert "planner.conversation.in_memory_conversation_system" not in sys.modules
assert conversation.resolve_conversation_start_request is resolve_conversation_start_request
try:
    conversation.unknown_export
except AttributeError as error:
    assert "unknown_export" in str(error)
else:
    raise AssertionError("unknown export did not raise AttributeError")
assert "planner.conversation.in_memory_conversation_system" not in sys.modules

implementation_names = {
    "InMemoryBackendPromptWrite",
    "InMemoryConversationObservation",
    "InMemoryConversationObservationKind",
    "InMemoryConversationSystem",
    "InMemoryConversationTurnEnding",
    "TurnCannotEndWhilePermissionAskIsPending",
    "TurnCannotEndWhileUserInputIsPending",
}
# First public access must resolve the implementation; later accesses keep identity.
first = conversation.InMemoryConversationSystem
from planner.conversation import in_memory_conversation_system as implementation
assert first is implementation.InMemoryConversationSystem
assert implementation_names <= set(conversation.__all__)
for name in conversation.__all__:
    if name == "resolve_conversation_start_request":
        expected = resolve_conversation_start_request
    else:
        expected = getattr(implementation if name in implementation_names else contracts, name)
    assert getattr(conversation, name) is expected
    assert getattr(conversation, name) is expected
""")
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

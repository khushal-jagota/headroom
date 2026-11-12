"""An executor archive must never satisfy a home runtime request."""

import json
from pathlib import Path

import pytest
from tests.support.update_apps import SHA_B, build_app

from planner.environments.app import AppValidationError, validate_app_manifest
from planner.platform_runtime.selection import select_home_runtime
from planner.platform_runtime.targets import LINUX_HOME, RuntimeTarget


def test_executor_artifact_cannot_select_home_runtime() -> None:
    # Keep OS and architecture equal so only the role distinguishes the payloads.
    executor = RuntimeTarget(LINUX_HOME.system, LINUX_HOME.architecture, "executor")
    with pytest.raises(ValueError, match="does not match"):
        select_home_runtime(executor)


def test_contract_dispatch_refuses_an_unimplemented_os(tmp_path: Path) -> None:
    # The Linux schema must not interpret a foreign OS as a Linux payload.
    from planner.environments.runtime_contracts import parse_optional_runtime_contract
    from planner.platform_runtime.contracts import UnsupportedPlatformError

    with pytest.raises(UnsupportedPlatformError, match="not implemented for darwin"):
        parse_optional_runtime_contract({"system": "darwin"})

    root = build_app(tmp_path / "app", SHA_B, "new")
    path = root / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["runtime_contract"]["system"] = "darwin"
    path.write_text(json.dumps(manifest))
    with pytest.raises(AppValidationError, match="not implemented for darwin"):
        validate_app_manifest(path)

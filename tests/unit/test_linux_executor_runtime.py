"""Distinct risk: a home and executor accept each other's Linux archive role."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.unit.test_app import SHA, _rewrite_digest, _runtime_app

from planner.environments.app import AppValidationError, validate_app_manifest
from planner.environments.linux_executor_runtime import read_runtime_revision
from planner.platform_runtime.targets import LINUX_EXECUTOR


def test_linux_home_and_executor_artifacts_cannot_exchange_roles(tmp_path: Path) -> None:
    app = _runtime_app(tmp_path / "app")
    with pytest.raises(AppValidationError, match="lacks its target"):
        validate_app_manifest(app / "manifest.json", expected_target=LINUX_EXECUTOR)
    marker = app / "mac-runtime.json"
    marker.write_text(
        json.dumps(
            dict(
                schema_version=1,
                application_revision=SHA,
                platform="linux",
                architecture="x86_64",
                role="executor",
            )
        )
    )
    _rewrite_digest(app)
    with pytest.raises(AppValidationError, match="cannot replace a home"):
        validate_app_manifest(app / "manifest.json")
    assert (
        validate_app_manifest(
            app / "manifest.json",
            expected_target=LINUX_EXECUTOR,
        ).app_sha
        == SHA
    )
    assert read_runtime_revision(app, SHA) == SHA
    marker.write_text(marker.read_text().replace('"executor"', '"home"'))
    with pytest.raises(ValueError, match="does not match"):
        read_runtime_revision(app, SHA)

"""Opt-in native homes retain selected paths without a provider process."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from test_runtime import RuntimeIsolation, RuntimeRefusal, environment_for
from tests.support.skill_tree_protection.audit import AuditGuard
from tests.support.skill_tree_protection.contracts import (
    Manifest,
    ProtectionInventory,
    TraversalMetrics,
)
from tests.support.skill_tree_protection.pytest_plugin import SkillTreeProtectionPlugin


@pytest.mark.parametrize(("marked", "opt_in"), [
    (False, "PANELS_REAL_HERMES_TESTS"), (True, None),
    (True, "PANELS_REAL_HERMES_TESTS"), (True, "PANELS_REAL_PERMISSION_TESTS"),
])
def test_native_hermes_paths_require_both_marker_and_explicit_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, marked: bool, opt_in: str | None,
) -> None:
    native = tmp_path / "native/.hermes"
    interpreter = tmp_path / "native/hermes-python"
    original = {
        **os.environ,
        "PLAN_HERMES_HOME": str(native),
        "PLAN_HERMES_PYTHON": str(interpreter),
    }
    for flag in (
        "PANELS_REAL_HERMES_TESTS", "PANELS_REAL_PERMISSION_TESTS",
        "PANELS_REAL_SYSTEM_STEERING_TESTS",
    ):
        original.pop(flag, None)
    if opt_in is not None:
        original[opt_in] = "1"
    original.pop("PANELS_TEST_ORIGINAL_PATHS", None)
    launch = environment_for(tmp_path / "launcher", original)
    with RuntimeIsolation(launch) as runtime:
        monkeypatch.setattr(AuditGuard, "install", lambda _: None)
        plugin = SkillTreeProtectionPlugin(
            ProtectionInventory((), ()), Manifest((), TraversalMetrics(0, 0, 0, 0)),
            launch_environment=runtime.environment,
        )
        item = cast(pytest.Item, SimpleNamespace(
            nodeid="fake native provider boundary",
            get_closest_marker=lambda _: object() if marked else None,
        ))
        monkeypatch.setattr(os, "environ", dict(runtime.environment))
        protocol = cast(Generator[None, None, None], plugin.pytest_runtest_protocol(item))
        try:
            next(protocol)
            if marked and opt_in:
                assert os.environ["PLAN_HERMES_HOME"] == str(native)
                assert os.environ["PLAN_HERMES_PYTHON"] == str(interpreter)
            else:
                assert os.environ["PLAN_HERMES_HOME"] != str(native)
                assert "PLAN_HERMES_PYTHON" not in os.environ
            with pytest.raises(StopIteration):
                next(protocol)
            assert os.environ["PLAN_HERMES_HOME"] == runtime.environment["PLAN_HERMES_HOME"]
            assert "PLAN_HERMES_PYTHON" not in os.environ
        finally:
            protocol.close()
            plugin._homes_storage.cleanup()


def test_opt_in_native_home_still_refuses_deployment_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployment = tmp_path / "Deployments/Panels/current"
    deployment.mkdir(parents=True)
    alias = tmp_path / "native-home"
    alias.symlink_to(deployment)
    monkeypatch.setattr(AuditGuard, "install", lambda _: None)
    plugin = SkillTreeProtectionPlugin(
        ProtectionInventory((), ()), Manifest((), TraversalMetrics(0, 0, 0, 0)),
        launch_environment={"PLAN_HERMES_HOME": str(alias), "PANELS_REAL_HERMES_TESTS": "1"},
    )
    item = cast(pytest.Item, SimpleNamespace(
        nodeid="fake deployment alias",
        get_closest_marker=lambda _: object(),
    ))
    try:
        with pytest.raises(RuntimeRefusal, match="deployment path"):
            next(plugin.pytest_runtest_protocol(item))
    finally:
        plugin._homes_storage.cleanup()

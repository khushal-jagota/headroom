"""CLI contract gap: preview performs no access, and an explicit check cleans its probe.

Reuse the existing CLI companion. No server, provider, new process host or consent API.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from click.testing import CliRunner
from tests.compare.direct.mac_executor_fixture import report_direct, run_node
from tests.support.synthetic_homes import create_synthetic_homes

from planner.cli.main import main


def test_mac_permissions_cli_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    home = Path(homes["HOME"])
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(workspace, target_is_directory=True)
    regular = tmp_path / "regular"
    regular.write_text("must not read this file")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PLAN_")}
    environment.update(homes)
    environment.update(
        PLAN_EXECUTOR_ROOT=str(tmp_path / "state"),
        PLAN_TEST_MODE="1",
        PLAN_SERVER_URL="http://127.0.0.1:9",
    )
    scenarios = [
        dict(
            argv=[
                "executor",
                "permissions",
                "--workspace",
                str(home / "Desktop/missing"),
                "--output",
                str(home / "Documents/missing"),
            ]
        ),
        dict(
            argv=[
                "executor",
                "permissions",
                "--workspace",
                str(linked),
                "--output",
                str(workspace),
                "--check",
            ]
        ),
        dict(
            argv=[
                "executor",
                "permissions",
                "--workspace",
                str(regular),
                "--output",
                str(workspace),
                "--check",
            ]
        ),
    ]
    expected = []
    with monkeypatch.context() as patch:
        for key in tuple(os.environ):
            if key.startswith("PLAN_"):
                patch.delenv(key)
        for key, value in environment.items():
            patch.setenv(key, value)
        for scenario in scenarios:
            result = CliRunner().invoke(main, scenario["argv"], prog_name="panels")
            expected.append(
                dict(exit_code=result.exit_code, stdout=result.stdout, stderr=result.stderr)
            )
    source = tmp_path / "permissions.json"
    source.write_text(json.dumps(dict(environment=environment, scenarios=scenarios)))
    observed = run_node("server/executor/mac/testing/cli_parity.ts", source, homes, timeout=15)
    assert observed["results"] == expected
    assert [value["exit_code"] for value in expected] == [0, 0, 1]
    assert list(workspace.iterdir()) == []
    assert regular.read_text() == "must not read this file"
    assert not (home / "Desktop/missing").exists()
    assert not (home / "Documents/missing").exists()
    assert not (tmp_path / "state").exists()
    report_direct(
        request,
        "unit22-mac-permissions.json",
        observed,
        started,
        "fresh Python CLI bytes/exits; lexical preview, redirected check, directory refusal",
        cases=len(scenarios),
    )

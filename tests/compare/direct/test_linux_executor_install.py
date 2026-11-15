"""The native Linux unit preserves Python policy with explicit Node layout changes."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from tests.compare.direct.unit20_cli import ROOT, write_report

from planner.environments.linux_executor import LinuxExecutorInstallation, _quoted, render_service
from planner.executor.configuration import default_executor_root


def test_linux_executor_service_contract_matches_python(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    began = time.perf_counter()
    home, root, node = tmp_path / "home", tmp_path / "runtime % $ space", "/usr/bin/node"
    runtime = root / "releases/one"
    configuration = LinuxExecutorInstallation(
        root, tmp_path / "data", "linux-test", "https://home.example", "a" * 40
    )
    with patch("pathlib.Path.home", return_value=home):
        expected = render_service(configuration, runtime)
    # The native projection changes Node layout and HOME and removes inherited loader overrides.
    prefix = (
        _quoted(str(runtime / ".venv/bin/python").replace("$", "$$"))
        + ' "-I" "-B" "-m" "planner.executor.entrypoint"'
    )
    replacement = (
        _quoted(node)
        + " "
        + _quoted(str(runtime / "server/executor/mac/entrypoint.ts").replace("$", "$$"))
    )
    expected = expected.replace(prefix, replacement)
    old_path = ":".join(
        [
            str(runtime / ".venv/bin"),
            str(runtime / "agent_backends/node_modules/.bin"),
            str(home / ".local/bin"),
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
        ]
    )
    new_path = ":".join(
        [
            str(runtime / "bin"),
            str(runtime / "agent_backends/node_modules/.bin"),
            str(Path(node).parent),
            str(home / ".local/bin"),
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
        ]
    )
    expected = expected.replace(_quoted("PATH=" + old_path), _quoted("PATH=" + new_path))
    expected = expected.replace(
        "UMask=0077\n",
        "UMask=0077\nUnsetEnvironment=NODE_OPTIONS NODE_PATH LD_PRELOAD LD_LIBRARY_PATH\n"
        "Environment=" + _quoted("HOME=" + str(home)) + "\n",
    )
    payload = dict(
        installation=str(root),
        data=str(configuration.data),
        executor_id=configuration.executor_id,
        server_url=configuration.server_url,
        revision=configuration.revision,
        home=str(home),
        node_executable=node,
    )
    xdg = tmp_path / "xdg"
    with (
        patch.dict(os.environ, {"XDG_DATA_HOME": str(xdg)}),
        patch("planner.executor.configuration.sys.platform", "linux"),
    ):
        source_root = default_executor_root()
    expected_data = source_root.parent / "executor-typescript/data"
    script = """
import {render_service} from './server/environments/linux_executor/units.ts';
import {default_linux_executor_root} from './server/environments/linux_executor/logic/paths.ts';
const [configuration,runtime,xdg]=JSON.parse(process.argv[1]);
console.log(JSON.stringify({unit:render_service(configuration,runtime),
  root:default_linux_executor_root(configuration.home,xdg)}));
"""
    actual = json.loads(
        subprocess.check_output(
            [
                "node",
                "--input-type=module",
                "-e",
                script,
                json.dumps([payload, str(runtime), str(xdg)]),
            ],
            cwd=ROOT,
            text=True,
        )
    )
    report = dict(
        equal=actual == dict(unit=expected, root=str(expected_data)),
        python=dict(unit=expected, root=str(expected_data)),
        typescript=actual,
        seconds=time.perf_counter() - began,
    )
    write_report(request, "linux-executor-service", report)
    assert report["equal"], report
